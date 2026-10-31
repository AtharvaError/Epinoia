"""Tells open dashboards that something changed. Carries no data: clients refetch through
the normal permission-checked routes, so a socket can never leak a record.
"""
import asyncio
from collections.abc import Callable

from fastapi import WebSocket
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.models import AuditLog


class Hub:
    # ponytail: in-process, so one API worker only. Move to Postgres LISTEN/NOTIFY or Redis for several.
    def __init__(self) -> None:
        self.sockets: dict[int, set[WebSocket]] = {}
        self.loop: asyncio.AbstractEventLoop | None = None
        self.alert: Callable[[int, str], None] = lambda _festival_id, _text: None  # The Telegram bot takes this over while it runs.

    def add(self, festival_id: int, socket: WebSocket) -> None:
        self.sockets.setdefault(festival_id, set()).add(socket)

    def remove(self, festival_id: int, socket: WebSocket) -> None:
        self.sockets.get(festival_id, set()).discard(socket)

    async def _send(self, festival_id: int) -> None:
        for socket in list(self.sockets.get(festival_id, ())):
            try:
                await socket.send_json({"type": "changed"})
            except (RuntimeError, ConnectionError):
                self.remove(festival_id, socket)

    def changed(self, festival_id: int) -> None:
        """Safe to call from the worker threads that run sync routes."""
        if self.loop and self.sockets.get(festival_id):
            asyncio.run_coroutine_threadsafe(self._send(festival_id), self.loop)


hub = Hub()


@event.listens_for(Session, "after_flush")
def remember_changes(session: Session, _context: object) -> None:
    for item in session.new:
        if isinstance(item, AuditLog) and item.festival_id:
            session.info.setdefault("changed_festivals", set()).add(item.festival_id)


@event.listens_for(Session, "after_commit")
def announce_changes(session: Session) -> None:
    for festival_id in session.info.pop("changed_festivals", ()):
        hub.changed(festival_id)
    for festival_id, text in session.info.pop("team_alerts", ()):
        hub.alert(festival_id, text)


@event.listens_for(Session, "after_rollback")
def forget_changes(session: Session) -> None:
    session.info.pop("changed_festivals", None)
    session.info.pop("team_alerts", None)

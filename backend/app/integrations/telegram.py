"""Two-way Telegram bot. Long-polls getUpdates (no public webhook needed) and answers
with the same permission-checked, read-only assistant the web UI uses.

The bot answers as one configured FestOS user (TELEGRAM_AS_EMAIL): its replies carry
exactly that user's permissions and festival, and it can never write. Questions are not
logged, because people may type names or medical details into them; the audit log records
only that a question was asked and which tools ran — mirroring the web assistant.
"""
import asyncio
import logging
from typing import Any

import httpx
from sqlalchemy import func, select

from app.assistant.agent import answer
from app.assistant.tools import Context, tools_for
from app.auth.deps import permissions_of
from app.config import settings
from app.database import SessionLocal
from app.models import Festival, User
from app.services.audit import record

logger = logging.getLogger("festos.telegram")

API = "https://api.telegram.org/bot{token}/{method}"
POLL_TIMEOUT = 30  # Telegram holds the request open this long when idle.
MAX_REPLY = 4000  # Telegram caps a message at 4096 characters.
HELP = ("FestOS Command bot. I answer from live festival data and can't change anything.\n\n"
        "Commands:\n/status — festival overview and alerts\n/incidents — open safety incidents\n/help — this message\n\n"
        "Or just ask, e.g. \"how many participants still need a bed?\" or \"which events clash?\"")
COMMANDS = {"status": "Give me a festival status overview with the current alerts.",
            "incidents": "List the open safety incidents."}


class Bot:
    def __init__(self) -> None:
        self.token = settings.telegram_bot_token
        self.allowed = {int(c) for c in settings.telegram_allowed_chat_ids.split(",") if c.strip().lstrip("-").isdigit()}
        self._offset = 0

    @property
    def enabled(self) -> bool:
        return bool(self.token and settings.telegram_as_email)

    async def _call(self, client: httpx.AsyncClient, method: str, **params: Any) -> Any:
        response = await client.post(API.format(token=self.token, method=method), json=params)
        response.raise_for_status()
        return response.json().get("result")

    def _reply_for(self, text: str) -> str:
        """Runs in a worker thread: owns its own DB session, resolves the bot's user fresh each time."""
        with SessionLocal() as db:
            user = db.scalar(select(User).where(func.lower(User.email) == settings.telegram_as_email.lower(), User.is_active.is_(True)))
            if user is None:
                return "This bot is not configured correctly: its FestOS account was not found."
            festival = db.get(Festival, user.festival_id) if user.festival_id else db.scalars(select(Festival).order_by(Festival.id)).first()
            if festival is None:
                return "No festival is set up yet."

            command = text[1:].split("@")[0].split()[0].lower() if text.startswith("/") else None
            if command in ("start", "help"):
                return HELP
            if command is not None and command not in COMMANDS:
                return f"I don't know /{command}. Try /help."
            question = COMMANDS[command] if command else text

            result = answer(Context(db, festival, permissions_of(user)), question, [])
            # The question stays out of the log; only that one was asked and which tools ran.
            record(db, user, festival.id, "telegram.query", "assistant", user.id,
                   f"{user.name} asked via Telegram", after={"mode": result["mode"], "tools": result["tools"]})
            db.commit()
            note = f"\n\n({result['note']})" if result.get("note") else ""
            return (result["answer"] + note)[:MAX_REPLY]

    async def _handle(self, client: httpx.AsyncClient, message: dict[str, Any]) -> None:
        chat_id = message["chat"]["id"]
        text = (message.get("text") or "").strip()
        if not text:
            return
        if self.allowed and chat_id not in self.allowed:
            logger.warning("Ignoring Telegram message from chat %s (not in allow list)", chat_id)
            await self._call(client, "sendMessage", chat_id=chat_id, text="This bot is restricted. Ask an organizer to add your chat.")
            return
        reply = await asyncio.to_thread(self._reply_for, text)  # answer() is sync and hits the DB, so keep polling free.
        await self._call(client, "sendMessage", chat_id=chat_id, text=reply)

    async def run(self) -> None:
        if not self.allowed:
            logger.warning("TELEGRAM_ALLOWED_CHAT_IDS is empty: the bot will answer anyone who messages it.")
        logger.info("Telegram bot started, answering as %s", settings.telegram_as_email)
        async with httpx.AsyncClient(timeout=POLL_TIMEOUT + 10) as client:
            while True:
                try:
                    updates = await self._call(client, "getUpdates", offset=self._offset, timeout=POLL_TIMEOUT) or []
                    for update in updates:
                        self._offset = update["update_id"] + 1
                        message = update.get("message") or update.get("edited_message")
                        if message:
                            await self._handle(client, message)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 — the poll loop must survive any single failure.
                    logger.exception("Telegram poll failed; retrying shortly")
                    await asyncio.sleep(5)


bot = Bot()

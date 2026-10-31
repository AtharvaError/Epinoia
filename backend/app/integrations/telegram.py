"""Two-way Telegram bot. Long-polls getUpdates (no public webhook needed) and answers
with the same permission-checked, read-only assistant the web UI uses.

The bot answers as one configured FestOS user (TELEGRAM_AS_EMAIL): its replies carry
exactly that user's permissions and festival, and it can never write. Only chats on
TELEGRAM_ALLOWED_CHAT_IDS are answered, and those same chats receive the team alerts.
Questions are not logged, because people may type names or medical details into them;
the audit log records only that a question was asked and which tools ran — mirroring
the web assistant.
"""
import asyncio
import logging
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.assistant.agent import answer
from app.assistant.tools import Context
from app.auth.deps import permissions_of
from app.config import settings
from app.database import SessionLocal
from app.models import Festival, User
from app.services.audit import record

logger = logging.getLogger("festos.telegram")
logging.getLogger("httpx").setLevel(logging.WARNING)  # At INFO it logs full URLs, and ours contain the bot token.

API = "https://api.telegram.org/bot{token}/{method}"
POLL_TIMEOUT = 30  # Telegram holds the request open this long when idle.
RETRY_SECONDS = 5
SEND_ATTEMPTS = 3
MAX_REPLY = 4000  # Telegram caps a message at 4096 characters.
COMMANDS = {
    "status": ("Festival overview and alerts", "Give me a festival status overview with the current alerts."),
    "incidents": ("Open safety incidents", "List the open safety incidents."),
    "beds": ("Who still needs a bed", "How many participants still need a bed?"),
    "food": ("Vendors and dietary gaps", "What is the food status?"),
    "transport": ("Delayed trips", "Which buses are delayed?"),
}
HELP = ("FestOS Command bot. I answer from live festival data and can't change anything.\n\nCommands:\n"
        + "".join(f"/{name} — {about.lower()}\n" for name, (about, _) in COMMANDS.items()) + "/help — this message\n\n"
        "Or just ask, e.g. \"which events clash?\" or \"can we serve Jain food tomorrow?\" In a group, mention me in the question.")


class TelegramError(Exception):
    """Telegram refused a call. Carries its reason, never the URL, because the URL holds the token."""


class Bot:
    def __init__(self) -> None:
        self.token = settings.telegram_bot_token
        self.allowed = {int(c) for c in settings.telegram_allowed_chat_ids.split(",") if c.strip().lstrip("-").isdigit()}
        self._offset = 0
        self._client: httpx.AsyncClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.token and settings.telegram_as_email)

    async def _call(self, client: httpx.AsyncClient, method: str, **params: Any) -> Any:
        response = await client.post(API.format(token=self.token, method=method), json=params)
        body = response.json()
        if not body.get("ok"):
            raise TelegramError(f"{method}: {response.status_code} {body.get('description', '')}")
        return body.get("result")

    async def _send(self, client: httpx.AsyncClient, chat_id: int, text: str) -> None:
        for attempt in range(1, SEND_ATTEMPTS + 1):
            try:
                await self._call(client, "sendMessage", chat_id=chat_id, text=text)
                return
            except (httpx.HTTPError, ValueError, TelegramError) as error:
                logger.warning("Telegram send failed, attempt %s of %s: %s", attempt, SEND_ATTEMPTS, error if isinstance(error, TelegramError) else type(error).__name__)
                await asyncio.sleep(attempt)

    def _identity(self, db: Session) -> tuple[User | None, Festival | None]:
        """Resolved fresh each time, so a deactivated account stops working at once."""
        user = db.scalar(select(User).where(func.lower(User.email) == settings.telegram_as_email.lower(), User.is_active.is_(True)))
        if user is None:
            return None, None
        return user, db.get(Festival, user.festival_id) if user.festival_id else db.scalars(select(Festival).order_by(Festival.id)).first()

    def _reply_for(self, text: str) -> str:
        """Runs in a worker thread: owns its own DB session."""
        with SessionLocal() as db:
            user, festival = self._identity(db)
            if user is None:
                return "This bot is not configured correctly: its FestOS account was not found."
            if festival is None:
                return "No festival is set up yet."

            command = (text[1:].split() or [""])[0].split("@")[0].lower() if text.startswith("/") else None
            if command in ("start", "help") or text.lower().strip("!. ") in ("hi", "hello", "hey"):
                return HELP
            if command is not None and command not in COMMANDS:
                return f"I don't know /{command}. Try /help."
            question = COMMANDS[command][1] if command else text

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
        if chat_id not in self.allowed:
            logger.warning("Ignoring Telegram message from chat %s (not in allow list)", chat_id)
            await self._send(client, chat_id, f"This bot is private. To get access, give an organizer this chat id: {chat_id}")
            return
        try:
            reply = await asyncio.to_thread(self._reply_for, text)  # answer() is sync and hits the DB, so keep polling free.
        except Exception:  # noqa: BLE001 — one bad question must still get an answer, and must not stop the bot.
            logger.exception("Telegram reply failed")
            reply = "Sorry, I could not answer that just now. Try again, or use the portal."
        await self._send(client, chat_id, reply)

    def _festival_id(self) -> int | None:
        with SessionLocal() as db:
            festival = self._identity(db)[1]
            return festival.id if festival else None

    async def _alert(self, festival_id: int, text: str) -> None:
        if self._client and festival_id == await asyncio.to_thread(self._festival_id):
            await asyncio.gather(*(self._send(self._client, chat_id, f"FestOS alert: {text}") for chat_id in self.allowed))

    def alert(self, festival_id: int, text: str) -> None:
        """Safe to call from the worker threads that run sync routes."""
        # ponytail: held in memory, so an alert is lost if the server stops mid-send. The portal stays the record; add an outbox table if alerts must survive restarts.
        if self._loop:
            asyncio.run_coroutine_threadsafe(self._alert(festival_id, text), self._loop)

    async def run(self) -> None:
        if not self.allowed:
            logger.warning("TELEGRAM_ALLOWED_CHAT_IDS is empty: the bot refuses everyone until a chat is added.")
        logger.info("Telegram bot started, answering as %s", settings.telegram_as_email)
        self._loop = asyncio.get_running_loop()
        menu_set = False
        async with httpx.AsyncClient(timeout=POLL_TIMEOUT + 10) as client:
            self._client = client
            while True:
                try:
                    if not menu_set:
                        await self._call(client, "setMyCommands", commands=[{"command": name, "description": about} for name, (about, _) in COMMANDS.items()])
                        menu_set = True
                    updates = await self._call(client, "getUpdates", offset=self._offset, timeout=POLL_TIMEOUT) or []
                    for update in updates:
                        self._offset = update["update_id"] + 1
                        message = update.get("message") or update.get("edited_message")
                        if message:
                            await self._handle(client, message)
                except asyncio.CancelledError:
                    raise
                except TelegramError as error:  # A 409 here means another process is polling with this token.
                    logger.warning("Telegram poll failed: %s", error)
                    await asyncio.sleep(RETRY_SECONDS)
                except httpx.HTTPError as error:
                    logger.warning("Telegram unreachable: %s", type(error).__name__)
                    await asyncio.sleep(RETRY_SECONDS)
                except Exception:  # noqa: BLE001 — the poll loop must survive any single failure.
                    logger.exception("Telegram poll failed; retrying shortly")
                    await asyncio.sleep(RETRY_SECONDS)


bot = Bot()

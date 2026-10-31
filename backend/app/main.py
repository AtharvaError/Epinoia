import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api import accommodation, analytics, assistant, auth, communication, events, festivals, finance, incidents, participants, simulations, transport, users, vendors, venues, volunteers
from app.auth.deps import COOKIE, user_from_token
from app.config import settings
from app.database import SessionLocal
from app.integrations.telegram import bot
from app.models import Festival
from app.notifications.live import hub

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("festos")
access = logging.getLogger("festos.access")

SLOW_REQUEST_MS = 1000
SECURITY_HEADERS = {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
                    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'"}
DOCS_PATHS = ("/api/docs", "/api/openapi.json")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    hub.loop = asyncio.get_running_loop()
    telegram = asyncio.create_task(bot.run()) if bot.enabled else None
    if telegram:
        hub.alert = bot.alert
    try:
        yield
    finally:
        if telegram:
            telegram.cancel()
            try:
                await telegram
            except asyncio.CancelledError:
                pass


production = settings.environment == "production"
app = FastAPI(title="FestOS Command API", docs_url=None if production else DOCS_PATHS[0], openapi_url=None if production else DOCS_PATHS[1], lifespan=lifespan)
for module in (auth, festivals, users, participants, events, venues, accommodation, transport, vendors, volunteers, simulations, finance, communication, incidents, analytics, assistant):
    app.include_router(module.router)


@app.middleware("http")
async def observe(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Tags each request so a user's error can be matched to the logs. Paths only: query strings can hold names."""
    request_id = uuid.uuid4().hex[:12]
    request.state.request_id = request_id
    started = time.perf_counter()
    response = await call_next(request)
    elapsed = (time.perf_counter() - started) * 1000
    level = logging.WARNING if elapsed > SLOW_REQUEST_MS or response.status_code >= 500 else logging.INFO
    access.log(level, "%s %s %s %.0fms id=%s", request.method, request.url.path, response.status_code, elapsed, request_id)
    response.headers["X-Request-ID"] = request_id
    response.headers["Cache-Control"] = "no-store"
    if not request.url.path.startswith(DOCS_PATHS):
        response.headers.update(SECURITY_HEADERS)
    return response


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "unknown")
    logger.exception("Unhandled error id=%s on %s %s", request_id, request.method, request.url.path)
    return JSONResponse({"detail": f"Something went wrong on the server. Reference: {request_id}"}, status_code=500)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/ready")
def ready() -> JSONResponse:
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.exception("Readiness check failed")
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return JSONResponse({"status": "ready"})


@app.websocket("/api/ws/{festival_id}")
async def live_updates(socket: WebSocket, festival_id: int) -> None:
    with SessionLocal() as db:
        user = user_from_token(db, socket.cookies.get(COOKIE))
        allowed = bool(user and db.get(Festival, festival_id) and user.festival_id in (None, festival_id))
    if not allowed:
        await socket.close(code=4401)
        return
    await socket.accept()
    hub.add(festival_id, socket)
    try:
        while True:
            await socket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.remove(festival_id, socket)

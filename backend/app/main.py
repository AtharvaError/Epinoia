import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from app.api import accommodation, analytics, assistant, auth, communication, events, festivals, finance, incidents, participants, simulations, transport, venues, volunteers
from app.auth.deps import COOKIE
from app.auth.verifier import verify_token
from app.database import SessionLocal
from app.models import Festival, User
from app.notifications.live import hub

logger = logging.getLogger("festos")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    hub.loop = asyncio.get_running_loop()
    yield


app = FastAPI(title="FestOS Command API", docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan)
for module in (auth, festivals, participants, events, venues, accommodation, transport, volunteers, simulations, finance, communication, incidents, analytics, assistant):
    app.include_router(module.router)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"detail": "Something went wrong on the server"}, status_code=500)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.websocket("/api/ws/{festival_id}")
async def live_updates(socket: WebSocket, festival_id: int) -> None:
    token = socket.cookies.get(COOKIE)
    user_id = verify_token(token) if token else None
    with SessionLocal() as db:
        user = db.get(User, user_id) if user_id else None
        allowed = bool(user and user.is_active and db.get(Festival, festival_id) and user.festival_id in (None, festival_id))
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

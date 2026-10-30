import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import auth, festivals

logger = logging.getLogger("festos")

app = FastAPI(title="FestOS Command API", docs_url="/api/docs", openapi_url="/api/openapi.json")
app.include_router(auth.router)
app.include_router(festivals.router)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"detail": "Something went wrong on the server"}, status_code=500)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

import time
from collections import defaultdict, deque
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.assistant.agent import answer
from app.assistant.tools import Context, tools_for
from app.auth.deps import current_user, festival_access, permissions_of
from app.config import settings
from app.database import get_db
from app.models import Festival, User
from app.services.audit import record

router = APIRouter(prefix="/api/festivals/{festival_id}/assistant", tags=["assistant"])

MAX_QUESTIONS, WINDOW_SECONDS = 20, 60
# ponytail: per-process limiter, resets on restart. Move to Redis if we run several workers.
recent: dict[int, deque[float]] = defaultdict(deque)


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(max_length=4000)


class Question(BaseModel):
    question: str = Field(min_length=2, max_length=500)
    history: list[Turn] = Field(default_factory=list, max_length=10)


@router.get("")
def status(festival: Festival = Depends(festival_access), user: User = Depends(current_user)):
    return {"mode": "claude" if settings.anthropic_api_key else "offline", "topics": [t.label for t in tools_for(permissions_of(user))]}


@router.post("")
def ask(body: Question, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    window = recent[user.id]
    now = time.monotonic()
    while window and now - window[0] > WINDOW_SECONDS:
        window.popleft()
    if len(window) >= MAX_QUESTIONS:
        raise HTTPException(429, "Too many questions. Wait a minute and try again.")
    window.append(now)
    result = answer(Context(db, festival, permissions_of(user)), body.question, [t.model_dump() for t in body.history])
    # The question stays out of the log because people may type names or medical details into it.
    record(db, user, festival.id, "assistant.query", "assistant", user.id, f"{user.name} asked the assistant", after={"mode": result["mode"], "tools": result["tools"]})
    db.commit()
    return result

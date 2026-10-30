import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.deps import COOKIE, current_user, permissions_of
from app.auth.verifier import check_password, issue_token
from app.config import settings
from app.database import get_db
from app.models import AuditLog, User

router = APIRouter(prefix="/api/auth", tags=["auth"])

MAX_FAILURES, WINDOW_SECONDS = 5, 300
# ponytail: per-process limiter, resets on restart. Move to Redis if we run several workers.
failures: dict[str, deque[float]] = defaultdict(deque)


class Login(BaseModel):
    email: str = Field(min_length=3, max_length=160)
    password: str = Field(min_length=1, max_length=128)


def user_payload(user: User) -> dict:
    return {
        "id": user.id, "name": user.name, "email": user.email, "festival_id": user.festival_id,
        "role": {"key": user.role.key, "name": user.role.name},
        "permissions": sorted(permissions_of(user)),
    }


@router.post("/login")
def login(body: Login, request: Request, response: Response, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    key = f"{request.client.host if request.client else 'unknown'}:{email}"
    recent = failures[key]
    now = time.monotonic()
    while recent and now - recent[0] > WINDOW_SECONDS:
        recent.popleft()
    if len(recent) >= MAX_FAILURES:
        raise HTTPException(429, "Too many attempts. Try again in a few minutes.")

    user = db.scalar(select(User).where(func.lower(User.email) == email))
    if not user or not user.is_active or not check_password(body.password, user.password_hash):
        recent.append(now)
        raise HTTPException(401, "Email or password is incorrect")

    failures.pop(key, None)
    db.add(AuditLog(festival_id=user.festival_id, actor_id=user.id, actor_label=user.name, action="auth.login",
                    entity="user", entity_id=str(user.id), summary=f"{user.name} signed in"))
    db.commit()
    response.set_cookie(COOKIE, issue_token(user.id), httponly=True, samesite="lax", secure=settings.cookie_secure,
                        max_age=settings.session_hours * 3600, path="/")
    return user_payload(user)


@router.post("/logout", status_code=204)
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")


@router.get("/me")
def me(user: User = Depends(current_user)):
    return user_payload(user)

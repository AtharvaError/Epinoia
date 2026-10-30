import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.deps import COOKIE, current_user, permissions_of, user_from_token
from app.auth.verifier import check_password, hash_password, issue_token
from app.config import settings
from app.database import get_db
from app.models import AuditLog, User
from app.services.audit import record

router = APIRouter(prefix="/api/auth", tags=["auth"])

MAX_FAILURES, MAX_FAILURES_PER_ADDRESS, WINDOW_SECONDS, MAX_TRACKED = 5, 30, 300, 10000
# ponytail: per-process limiter, resets on restart. Move to Redis if we run several workers.
failures: dict[str, deque[float]] = defaultdict(deque)


class Login(BaseModel):
    email: str = Field(min_length=3, max_length=160)
    password: str = Field(min_length=1, max_length=72)


class ProfileIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    phone: str | None = Field(default=None, max_length=30)


class PasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=72)
    new_password: str = Field(min_length=10, max_length=72)


def user_payload(user: User) -> dict:
    return {
        "id": user.id, "name": user.name, "email": user.email, "phone": user.phone, "festival_id": user.festival_id,
        "role": {"key": user.role.key, "name": user.role.name},
        "permissions": sorted(permissions_of(user)),
    }


def recent_failures(key: str, now: float) -> deque[float]:
    recent = failures[key]
    while recent and now - recent[0] > WINDOW_SECONDS:
        recent.popleft()
    return recent


def start_session(response: Response, user: User) -> None:
    response.set_cookie(COOKIE, issue_token(user.id, user.token_version), httponly=True, samesite="lax", secure=settings.cookie_secure,
                        max_age=settings.session_hours * 3600, path="/")


@router.post("/login")
def login(body: Login, request: Request, response: Response, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    address = request.client.host if request.client else "unknown"
    now = time.monotonic()
    if len(failures) > MAX_TRACKED:
        failures.clear()
    by_account, by_address = recent_failures(f"{address}:{email}", now), recent_failures(f"address:{address}", now)
    if len(by_account) >= MAX_FAILURES or len(by_address) >= MAX_FAILURES_PER_ADDRESS:
        raise HTTPException(429, "Too many attempts. Try again in a few minutes.")

    user = db.scalar(select(User).where(func.lower(User.email) == email))
    valid = check_password(body.password, user.password_hash if user else None)
    if not user or not user.is_active or not valid:
        by_account.append(now)
        by_address.append(now)
        raise HTTPException(401, "Email or password is incorrect")

    failures.pop(f"{address}:{email}", None)
    db.add(AuditLog(festival_id=user.festival_id, actor_id=user.id, actor_label=user.name, action="auth.login",
                    entity="user", entity_id=str(user.id), summary=f"{user.name} signed in"))
    db.commit()
    start_session(response, user)
    return user_payload(user)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    """Revokes the session on the server too, so a copied token stops working."""
    user = user_from_token(db, request.cookies.get(COOKIE))
    if user:
        user.token_version += 1
        db.commit()
    response.delete_cookie(COOKIE, path="/")


@router.get("/me")
def me(user: User = Depends(current_user)):
    return user_payload(user)


@router.patch("/me")
def update_profile(body: ProfileIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    target = db.merge(user)
    target.name, target.phone = body.name, body.phone
    db.commit()
    return user_payload(target)


@router.post("/password", status_code=204)
def change_password(body: PasswordIn, response: Response, user: User = Depends(current_user), db: Session = Depends(get_db)):
    target = db.merge(user)
    if not check_password(body.current_password, target.password_hash):
        raise HTTPException(403, "The current password is incorrect")
    target.password_hash = hash_password(body.new_password)
    target.token_version += 1  # Other sessions end; this one continues with a fresh token.
    if target.festival_id:
        record(db, target, target.festival_id, "auth.password", "user", target.id, f"{target.name} changed their password")
    db.commit()
    start_session(response, target)

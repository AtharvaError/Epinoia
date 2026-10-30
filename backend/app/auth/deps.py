from collections.abc import Callable

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth.verifier import verify_token
from app.database import get_db
from app.models import Festival, User

COOKIE = "festos_session"


def user_from_token(db: Session, token: str | None) -> User | None:
    claims = verify_token(token) if token else None
    if not claims:
        return None
    user = db.get(User, claims[0])
    return user if user and user.is_active and user.token_version == claims[1] else None


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = user_from_token(db, request.cookies.get(COOKIE))
    if not user:
        raise HTTPException(401, "Not signed in")
    return user


def permissions_of(user: User) -> set[str]:
    return {p.key for p in user.role.permissions}


def require(permission: str) -> Callable[[User], User]:
    def check(user: User = Depends(current_user)) -> User:
        if permission not in permissions_of(user):
            raise HTTPException(403, f"Your role does not include {permission}")
        return user
    return check


def festival_access(festival_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Festival:
    festival = db.get(Festival, festival_id)
    if not festival:
        raise HTTPException(404, "Festival not found")
    if user.festival_id not in (None, festival_id):
        raise HTTPException(403, "You do not belong to this festival")
    return festival

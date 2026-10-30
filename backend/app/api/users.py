from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.common import like, paged
from app.auth.deps import current_user, festival_access, permissions_of, require
from app.auth.verifier import hash_password
from app.database import get_db
from app.models import Festival, Role, User
from app.services.audit import record

router = APIRouter(prefix="/api/festivals/{festival_id}/users", tags=["users"])


class UserIn(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=160)
    name: str = Field(min_length=2, max_length=120)
    role_key: str
    password: str = Field(min_length=10, max_length=72)


class UserChange(BaseModel):
    role_key: str | None = None
    is_active: bool | None = None


def row(u: User) -> dict[str, Any]:
    return {"id": u.id, "email": u.email, "name": u.name, "role": {"key": u.role.key, "name": u.role.name}, "is_active": u.is_active, "is_simulated": u.is_simulated}


def role_for(db: Session, key: str, actor: User) -> Role:
    role = db.scalar(select(Role).where(Role.key == key))
    if not role:
        raise HTTPException(422, "Choose a valid role")
    if key == "super_admin" and "festivals.manage" not in permissions_of(actor):
        raise HTTPException(403, "Only a Super Admin can grant that role")
    return role


@router.get("", dependencies=[Depends(require("settings.manage"))])
def list_users(page: int = Query(1, ge=1), q: str | None = Query(None, max_length=80), festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    query = select(User).where(User.festival_id == festival.id)
    if q:
        query = query.where(or_(User.name.ilike(like(q), escape="\\"), User.email.ilike(like(q), escape="\\")))
    return paged(db, query.order_by(User.name), page, row)


@router.get("/roles", dependencies=[Depends(require("settings.manage"))])
def list_roles(festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    allowed = permissions_of(user)
    return [{"key": r.key, "name": r.name} for r in db.scalars(select(Role).order_by(Role.id)) if r.key != "super_admin" or "festivals.manage" in allowed]


@router.post("", status_code=201, dependencies=[Depends(require("settings.manage"))])
def create_user(body: UserIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    created = User(email=body.email.lower(), name=body.name, password_hash=hash_password(body.password), role_id=role_for(db, body.role_key, user).id, festival_id=festival.id)
    db.add(created)
    try:
        db.flush()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(409, "That email is already in use") from error
    record(db, user, festival.id, "user.create", "user", created.id, f"{user.name} added user {created.name}")
    db.commit()
    return {"id": created.id}


@router.patch("/{user_id}", dependencies=[Depends(require("settings.manage"))])
def change_user(user_id: int, body: UserChange, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if not target or target.festival_id != festival.id:
        raise HTTPException(404, "User not found")
    if target.id == user.id:
        raise HTTPException(409, "You cannot change your own role or access")
    if body.role_key:
        target.role_id = role_for(db, body.role_key, user).id
    if body.is_active is not None:
        target.is_active = body.is_active
    target.token_version += 1  # New rights take effect now, not when the session expires.
    record(db, user, festival.id, "user.update", "user", target.id, f"{user.name} changed access for {target.name}")
    db.commit()
    db.refresh(target)
    return row(target)

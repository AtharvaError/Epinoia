from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.common import by_code, paged
from app.auth.deps import current_user, festival_access, require
from app.auth.permissions import ROLES
from app.database import get_db
from app.models import Announcement, Department, Event, Festival, Notification, Role, User
from app.services.audit import record

router = APIRouter(prefix="/api/festivals/{festival_id}", tags=["communication"])


class AnnouncementIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    body: str = Field(min_length=3, max_length=4000)
    audience_type: str = Field(default="festival", pattern="^(festival|role|department|event)$")
    audience_ref: str | None = Field(default=None, max_length=60)


def recipients(db: Session, festival_id: int, body: AnnouncementIn) -> list[User]:
    query = select(User).where(User.festival_id == festival_id, User.is_active.is_(True))
    if body.audience_type == "role":
        if body.audience_ref not in ROLES:
            raise HTTPException(422, "Choose a role")
        query = query.join(Role, Role.id == User.role_id).where(Role.key == body.audience_ref)
    elif body.audience_type == "department":
        department = db.scalar(select(Department.id).where(Department.festival_id == festival_id, Department.name == body.audience_ref))
        if not department:
            raise HTTPException(422, "Choose a department")
        query = query.where(User.department_id == department)
    elif body.audience_type == "event":
        by_code(db, Event, festival_id, body.audience_ref or "")
        query = query.join(Role, Role.id == User.role_id).where(Role.key.in_(("event_coordinator", "festival_director")))
    return list(db.scalars(query))


@router.get("/announcements/options", dependencies=[Depends(require("communication.read"))])
def options(festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    return {"roles": [{"key": k, "name": name} for k, (name, _) in ROLES.items()],
            "departments": list(db.scalars(select(Department.name).where(Department.festival_id == festival.id).order_by(Department.name))),
            "events": [{"code": e.code, "name": e.name} for e in db.scalars(select(Event).where(Event.festival_id == festival.id, Event.deleted_at.is_(None)).order_by(Event.name))]}


@router.get("/announcements", dependencies=[Depends(require("communication.read"))])
def list_announcements(page: int = Query(1, ge=1), festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    names = dict(db.execute(select(User.id, User.name)).all())
    query = select(Announcement).where(Announcement.festival_id == festival.id).order_by(Announcement.id.desc())
    link = lambda a: f"/communication?announcement={a.id}"  # noqa: E731

    def row(a: Announcement) -> dict[str, Any]:
        reached, read, acknowledged = db.execute(select(func.count(), func.count(Notification.read_at), func.count(Notification.acknowledged_at))
                                                 .where(Notification.festival_id == festival.id, Notification.link == link(a))).one()
        return {"id": a.id, "title": a.title, "body": a.body, "audience_type": a.audience_type, "audience_ref": a.audience_ref, "created_at": a.created_at, "created_by": names.get(a.created_by_id),
                "recipients": reached, "read": read, "acknowledged": acknowledged, "is_simulated": a.is_simulated}
    return paged(db, query, page, row)


@router.post("/announcements", status_code=201, dependencies=[Depends(require("communication.write"))])
def create_announcement(body: AnnouncementIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    people = recipients(db, festival.id, body)
    announcement = Announcement(festival_id=festival.id, created_by_id=user.id, **body.model_dump())
    db.add(announcement)
    db.flush()
    link = f"/communication?announcement={announcement.id}"
    db.add_all(Notification(festival_id=festival.id, user_id=u.id, title=body.title, body=body.body, link=link) for u in people if u.id != user.id)
    record(db, user, festival.id, "announcement.create", "announcement", announcement.id, f"{user.name} sent an announcement: {body.title}")
    db.commit()
    return {"id": announcement.id, "recipients": sum(u.id != user.id for u in people)}


@router.post("/notifications/{notification_id}/acknowledge", status_code=204)
def acknowledge(notification_id: int, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    note = db.get(Notification, notification_id)
    if not note or note.user_id != user.id or note.festival_id != festival.id:
        raise HTTPException(404, "Notification not found")
    note.read_at = note.read_at or func.now()
    note.acknowledged_at = note.acknowledged_at or func.now()
    db.commit()

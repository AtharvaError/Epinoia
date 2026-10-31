import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.analytics.dashboard import dashboard, festival_phase
from app.auth.deps import current_user, festival_access, permissions_of, require
from app.database import get_db
from app.models import Department, EquipmentRequest, Event, Festival, Notification, Participant, Resource, User
from app.services.audit import record
from app.services.pack_import import DEPARTMENTS

router = APIRouter(prefix="/api/festivals", tags=["festivals"])


def today(festival: Festival):
    return datetime.now(ZoneInfo(festival.timezone)).date()


@router.get("")
def list_festivals(user: User = Depends(current_user), db: Session = Depends(get_db)):
    query = select(Festival).order_by(Festival.start_date.desc())
    if user.festival_id is not None:
        query = query.where(Festival.id == user.festival_id)
    return [{"id": f.id, "name": f.name, "start_date": f.start_date, "end_date": f.end_date,
             "phase": festival_phase(f, today(f))} for f in db.scalars(query)]


class FestivalIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    start_date: date
    end_date: date
    status: str = Field(default="planning", pattern="^(planning|active|completed)$")

    @model_validator(mode="after")
    def dates_make_sense(self):
        if self.end_date < self.start_date:
            raise ValueError("The end date must not be before the start date")
        return self


@router.post("", status_code=201, dependencies=[Depends(require("festivals.manage"))])
def create_festival(body: FestivalIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    slug = re.sub(r"[^a-z0-9]+", "-", body.name.lower()).strip("-") or "festival"
    if db.scalar(select(Festival.id).where(Festival.slug == slug)):
        raise HTTPException(409, "A festival with that name already exists")
    festival = Festival(slug=slug, **body.model_dump())
    db.add(festival)
    db.flush()
    db.add_all(Department(festival_id=festival.id, name=name) for name in DEPARTMENTS)
    record(db, user, festival.id, "festival.create", "festival", festival.id, f"{user.name} created {festival.name}")
    db.commit()
    return {"id": festival.id, "name": festival.name}


@router.patch("/{festival_id}", dependencies=[Depends(require("settings.manage"))])
def update_festival(body: FestivalIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    for field, value in body.model_dump().items():
        setattr(festival, field, value)
    record(db, user, festival.id, "festival.update", "festival", festival.id, f"{user.name} updated festival details")
    db.commit()
    return {"id": festival.id}


@router.get("/{festival_id}/dashboard", dependencies=[Depends(require("dashboard.read"))])
def get_dashboard(festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    return dashboard(db, festival, today(festival))


@router.get("/{festival_id}/search")
def search(q: str = Query(min_length=2, max_length=80), festival: Festival = Depends(festival_access),
           user: User = Depends(current_user), db: Session = Depends(get_db)):
    permissions = permissions_of(user)
    like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    results = []
    if "participants.read" in permissions:
        people = db.scalars(select(Participant).where(
            Participant.festival_id == festival.id, Participant.deleted_at.is_(None),
            or_(Participant.code.ilike(like, escape="\\"), Participant.full_name.ilike(like, escape="\\"),
                Participant.college.ilike(like, escape="\\"))).order_by(Participant.code).limit(6))
        results += [{"type": "Participant", "code": p.code, "title": p.full_name, "subtitle": p.college} for p in people]
    if "events.read" in permissions:
        events = db.scalars(select(Event).where(
            Event.festival_id == festival.id, Event.deleted_at.is_(None),
            or_(Event.code.ilike(like, escape="\\"), Event.name.ilike(like, escape="\\"))).order_by(Event.code).limit(6))
        results += [{"type": "Event", "code": e.code, "title": e.name, "subtitle": e.status.title()} for e in events]
    if "equipment.read" in permissions:
        eq_items = sorted(set(db.scalars(select(Resource.name).where(
            Resource.festival_id == festival.id, Resource.kind == "logistics",
            or_(Resource.name.ilike(like, escape="\\"), Resource.code.ilike(like, escape="\\"))))))[:4]
        results += [{"type": "Equipment", "code": item_name, "title": item_name, "subtitle": "Inventory Control"} for item_name in eq_items]
        eq_reqs = db.scalars(select(EquipmentRequest).where(
            EquipmentRequest.festival_id == festival.id,
            or_(EquipmentRequest.code.ilike(like, escape="\\"), EquipmentRequest.item.ilike(like, escape="\\"),
                EquipmentRequest.destination.ilike(like, escape="\\"))).order_by(EquipmentRequest.id.desc()).limit(4))
        results += [{"type": "EquipmentRequest", "code": r.code, "title": f"{r.code} · {r.item}", "subtitle": f"{r.destination} ({r.status})"} for r in eq_reqs]
    return results


@router.get("/{festival_id}/notifications")
def notifications(festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    mine = (Notification.festival_id == festival.id, Notification.user_id == user.id)
    rows = db.scalars(select(Notification).where(*mine).order_by(Notification.id.desc()).limit(20))
    unread = db.scalar(select(func.count()).select_from(Notification).where(*mine, Notification.read_at.is_(None)))
    return {"unread": unread, "items": [{"id": n.id, "title": n.title, "body": n.body, "link": n.link, "read": n.read_at is not None,
                                         "acknowledged": n.acknowledged_at is not None, "created_at": n.created_at} for n in rows]}


@router.post("/{festival_id}/notifications/{notification_id}/read", status_code=204)
def mark_read(notification_id: int, festival: Festival = Depends(festival_access), user: User = Depends(current_user),
              db: Session = Depends(get_db)):
    note = db.get(Notification, notification_id)
    if not note or note.user_id != user.id or note.festival_id != festival.id:
        raise HTTPException(404, "Notification not found")
    note.read_at = note.read_at or func.now()
    db.commit()

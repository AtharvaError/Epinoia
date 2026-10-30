from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.api.common import by_code, like, paged
from app.api.participants import DeleteReason
from app.auth.deps import current_user, festival_access, require
from app.database import get_db
from app.models import Event, Festival, Registration, Resource, ResourceAllocation, Role, User, Venue, Volunteer, VolunteerShift
from app.services.audit import record
from app.services.events import all_conflicts, outside_venue_hours, venue_conflicts

router = APIRouter(prefix="/api/festivals/{festival_id}/events", tags=["events"])
STATUS = "^(scheduled|live|completed|postponed|cancelled)$"


class EventIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    participation_type: str = Field(pattern="^(solo|team)$")
    category: str | None = Field(default=None, max_length=60)
    description: str | None = Field(default=None, max_length=2000)
    start_at: datetime | None = None
    end_at: datetime | None = None
    venue_code: str | None = None
    coordinator_id: int | None = None
    judges: list[str] = Field(default_factory=list, max_length=20)
    dependencies: list[str] = Field(default_factory=list, max_length=20)
    volunteers_needed: int = Field(default=0, ge=0, le=500)
    duration_minutes: int = Field(default=60, ge=5, le=1440)
    status: str = Field(default="scheduled", pattern=STATUS)
    override_reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def times_make_sense(self):
        if bool(self.start_at) != bool(self.end_at):
            raise ValueError("Set both a start and an end time, or neither")
        if self.start_at and self.end_at <= self.start_at:
            raise ValueError("End time must be after the start time")
        return self


def load(db: Session, festival_id: int) -> dict[int, dict[str, int]]:
    registered = dict(db.execute(select(Registration.event_id, func.count()).where(Registration.festival_id == festival_id).group_by(Registration.event_id)).all())
    staffed = dict(db.execute(select(VolunteerShift.event_id, func.count()).where(VolunteerShift.festival_id == festival_id, VolunteerShift.status != "cancelled").group_by(VolunteerShift.event_id)).all())
    return {"registered": registered, "staffed": staffed}


def serialize(e: Event, counts: dict[str, dict[int, int]]) -> dict[str, Any]:
    return {
        "code": e.code, "name": e.name, "category": e.category, "participation_type": e.participation_type, "description": e.description,
        "start_at": e.start_at, "end_at": e.end_at, "status": e.status, "schedule_source": e.schedule_source, "rounds": e.rounds,
        "duration_minutes": e.duration_minutes, "venue_requirements": e.venue_requirements, "equipment_needs": e.equipment_needs,
        "volunteers_needed": e.volunteers_needed, "judges": e.judges, "dependencies": e.dependencies, "coordinator_id": e.coordinator_id,
        "venue": e.venue and {"code": e.venue.code, "name": e.venue.name, "capacity": e.venue.capacity},
        "registered": counts["registered"].get(e.id, 0), "staffed": counts["staffed"].get(e.id, 0),
    }


@router.get("", dependencies=[Depends(require("events.read"))])
def list_events(page: int = Query(1, ge=1), q: str | None = Query(None, max_length=80), status: str | None = None, venue: str | None = None,
                day: date | None = None, festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    query = select(Event).options(joinedload(Event.venue)).where(Event.festival_id == festival.id, Event.deleted_at.is_(None))
    if q:
        query = query.where(or_(Event.code.ilike(like(q), escape="\\"), Event.name.ilike(like(q), escape="\\")))
    if status:
        query = query.where(Event.status == status)
    if venue:
        query = query.join(Venue, Venue.id == Event.venue_id).where(Venue.code == venue)
    if day:
        query = query.where(func.date(func.timezone(festival.timezone, Event.start_at)) == day)
    counts = load(db, festival.id)
    return paged(db, query.order_by(Event.start_at.nulls_last(), Event.code), page, lambda e: serialize(e, counts))


@router.get("/conflicts", dependencies=[Depends(require("events.read"))])
def conflicts(festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    return all_conflicts(db, festival.id)


@router.get("/schedule", dependencies=[Depends(require("events.read"))])
def schedule(day: date, festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    counts = load(db, festival.id)
    events = db.scalars(select(Event).options(joinedload(Event.venue)).where(
        Event.festival_id == festival.id, Event.deleted_at.is_(None), func.date(func.timezone(festival.timezone, Event.start_at)) == day).order_by(Event.start_at))
    venues = db.scalars(select(Venue).where(Venue.festival_id == festival.id).order_by(Venue.code))
    return {"day": day, "venues": [{"code": v.code, "name": v.name, "capacity": v.capacity} for v in venues],
            "events": [serialize(e, counts) for e in events]}


@router.get("/options", dependencies=[Depends(require("events.read"))])
def options(festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    coordinators = db.scalars(select(User).join(Role, Role.id == User.role_id).where(User.festival_id == festival.id, Role.key == "event_coordinator").order_by(User.name))
    venues = db.scalars(select(Venue).where(Venue.festival_id == festival.id).order_by(Venue.name))
    return {"coordinators": [{"id": u.id, "name": u.name} for u in coordinators],
            "venues": [{"code": v.code, "name": v.name, "capacity": v.capacity} for v in venues]}


@router.get("/{code}", dependencies=[Depends(require("events.read"))])
def get_event(code: str, festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    e = by_code(db, Event, festival.id, code)
    row = serialize(e, load(db, festival.id))
    row["clashing_venue_events"] = [] if not (e.venue_id and e.start_at) else [
        {"code": c.code, "name": c.name} for c in venue_conflicts(db, festival.id, e.venue_id, e.start_at, e.end_at, e.id)]
    row["shifts"] = [{"volunteer": v.name, "code": v.code, "task": s.task, "attendance": s.attendance} for s, v in db.execute(
        select(VolunteerShift, Volunteer).join(Volunteer, Volunteer.id == VolunteerShift.volunteer_id).where(VolunteerShift.event_id == e.id, VolunteerShift.status != "cancelled").order_by(Volunteer.name))]
    row["resources"] = [{"code": r.code, "name": r.name, "quantity": a.quantity} for a, r in db.execute(
        select(ResourceAllocation, Resource).join(Resource, Resource.id == ResourceAllocation.resource_id).where(ResourceAllocation.event_id == e.id))]
    return row


def check_schedule(db: Session, festival: Festival, body: EventIn, venue: Venue | None, event: Event | None) -> list[str]:
    """Return problems a user must knowingly override. Raises when there is no reason given."""
    problems: list[str] = []
    if venue and body.start_at:
        clashes = venue_conflicts(db, festival.id, venue.id, body.start_at, body.end_at, event.id if event else None)
        problems += [f"{venue.name} is already booked for {c.name}" for c in clashes]
        if outside_venue_hours(venue, body.start_at, body.end_at):
            problems.append(f"That time is outside {venue.name}'s hours")
        if venue.maintenance_status != "operational":
            problems.append(f"{venue.name} is marked {venue.maintenance_status}")
    if problems and not (body.override_reason and len(body.override_reason.strip()) >= 5):
        raise HTTPException(409, {"message": "This change has conflicts", "conflicts": problems})
    return problems


def apply(e: Event, body: EventIn, venue: Venue | None) -> None:
    moved = (e.start_at, e.end_at, e.venue_id) != (body.start_at, body.end_at, venue.id if venue else None)
    for field in ("name", "participation_type", "category", "description", "start_at", "end_at", "coordinator_id", "judges",
                  "dependencies", "volunteers_needed", "duration_minutes", "status"):
        setattr(e, field, getattr(body, field))
    e.venue_id = venue.id if venue else None
    if moved and body.start_at:
        e.schedule_source = "manual"


@router.post("", status_code=201, dependencies=[Depends(require("events.write"))])
def create_event(body: EventIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    venue = by_code(db, Venue, festival.id, body.venue_code) if body.venue_code else None
    problems = check_schedule(db, festival, body, venue, None)
    codes = db.scalars(select(Event.code).where(Event.festival_id == festival.id))
    e = Event(festival_id=festival.id, code=f"E{max((int(c[1:]) for c in codes if c[1:].isdigit()), default=0) + 1:02d}")
    apply(e, body, venue)
    db.add(e)
    db.flush()
    record(db, user, festival.id, "event.create", "event", e.code, f"{user.name} created {e.name}", reason=body.override_reason if problems else None)
    db.commit()
    return {"code": e.code, "overridden": problems}


@router.patch("/{code}", dependencies=[Depends(require("events.write"))])
def update_event(code: str, body: EventIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    e = by_code(db, Event, festival.id, code)
    venue = by_code(db, Venue, festival.id, body.venue_code) if body.venue_code else None
    problems = check_schedule(db, festival, body, venue, e)
    before = {"start_at": str(e.start_at), "end_at": str(e.end_at), "venue_id": e.venue_id, "status": e.status}
    moved = (e.start_at, e.end_at, e.venue_id) != (body.start_at, body.end_at, venue.id if venue else None)
    apply(e, body, venue)
    action = "event.reschedule" if moved else "event.update"
    summary = f"{user.name} rescheduled {e.name}" if moved else f"{user.name} updated {e.name}"
    record(db, user, festival.id, action, "event", code, summary, before=before,
           after={"start_at": str(e.start_at), "end_at": str(e.end_at), "venue_id": e.venue_id, "status": e.status},
           reason=body.override_reason if problems else None)
    db.commit()
    return {"code": e.code, "overridden": problems}


@router.delete("/{code}", status_code=204, dependencies=[Depends(require("events.write"))])
def delete_event(code: str, body: DeleteReason, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    e = by_code(db, Event, festival.id, code)
    e.deleted_at = func.now()
    record(db, user, festival.id, "event.delete", "event", code, f"{user.name} deleted {e.name}", reason=body.reason)
    db.commit()

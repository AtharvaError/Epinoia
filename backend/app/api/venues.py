from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.common import by_code, like, ordered, paged
from app.auth.deps import current_user, festival_access, require
from app.database import get_db
from app.models import Event, Festival, Registration, Resource, ResourceAllocation, User, Venue, Volunteer, VolunteerShift
from app.services.equipment import bookings, free_units
from app.services.audit import record

router = APIRouter(prefix="/api/festivals/{festival_id}", tags=["venues"])


class VenueIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    location: str | None = Field(default=None, max_length=160)
    capacity: int = Field(ge=0, le=100000)
    indoor_outdoor: str | None = Field(default=None, pattern="^(indoor|outdoor)$")
    maintenance_status: str = Field(default="operational", pattern="^(operational|maintenance|closed)$")


class ResourceIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    kind: str = Field(default="logistics", pattern="^(logistics|merch)$")
    size: str | None = Field(default=None, max_length=10)
    quantity: int = Field(ge=0, le=1000000)
    location: str | None = Field(default=None, max_length=120)
    condition: str | None = Field(default=None, max_length=30)


class AllocationIn(BaseModel):
    event_code: str
    quantity: int = Field(gt=0, le=1000000)


def venue_row(v: Venue, events: list[dict[str, Any]]) -> dict[str, Any]:
    return {"code": v.code, "name": v.name, "location": v.location, "capacity": v.capacity, "indoor_outdoor": v.indoor_outdoor,
            "maintenance_status": v.maintenance_status, "availability": v.availability, "events": events,
            "over_capacity": sum(e["registered"] > v.capacity for e in events)}


@router.get("/venues", dependencies=[Depends(require("venues.read"))])
def list_venues(festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    assigned: dict[int, list[dict[str, Any]]] = {}
    rows = db.execute(select(Event, func.count(Registration.id)).outerjoin(Registration, Registration.event_id == Event.id)
                      .where(Event.festival_id == festival.id, Event.deleted_at.is_(None), Event.venue_id.is_not(None))
                      .group_by(Event.id).order_by(Event.start_at))
    for e, registered in rows:
        assigned.setdefault(e.venue_id, []).append({"code": e.code, "name": e.name, "start_at": e.start_at, "end_at": e.end_at, "registered": registered})
    return [venue_row(v, assigned.get(v.id, [])) for v in db.scalars(select(Venue).where(Venue.festival_id == festival.id).order_by(Venue.code))]


@router.get("/venues/{code}/staff", dependencies=[Depends(require("venues.read"))])
def venue_staff(code: str, festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    v = by_code(db, Venue, festival.id, code)
    return [{"code": row.code, "name": row.name} for row in db.execute(
        select(Volunteer.code, Volunteer.name).join(VolunteerShift, VolunteerShift.volunteer_id == Volunteer.id)
        .join(Event, Event.id == VolunteerShift.event_id).where(Event.venue_id == v.id).distinct().order_by(Volunteer.name))]


def next_venue_code(db: Session, festival_id: int) -> str:
    codes = db.scalars(select(Venue.code).where(Venue.festival_id == festival_id))
    return f"V{max((int(c[1:]) for c in codes if c[1:].isdigit()), default=0) + 1:02d}"


@router.post("/venues", status_code=201, dependencies=[Depends(require("venues.write"))])
def create_venue(body: VenueIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    v = Venue(festival_id=festival.id, code=next_venue_code(db, festival.id), **body.model_dump())
    db.add(v)
    db.flush()
    record(db, user, festival.id, "venue.create", "venue", v.code, f"{user.name} added venue {v.name}")
    db.commit()
    return {"code": v.code}


@router.patch("/venues/{code}", dependencies=[Depends(require("venues.write"))])
def update_venue(code: str, body: VenueIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    v = by_code(db, Venue, festival.id, code)
    before = {"capacity": v.capacity, "maintenance_status": v.maintenance_status}
    for field, value in body.model_dump().items():
        setattr(v, field, value)
    record(db, user, festival.id, "venue.update", "venue", code, f"{user.name} updated venue {v.name}", before=before,
           after={"capacity": v.capacity, "maintenance_status": v.maintenance_status})
    db.commit()
    return {"code": v.code}


@router.delete("/venues/{code}", status_code=204, dependencies=[Depends(require("venues.write"))])
def delete_venue(code: str, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    v = by_code(db, Venue, festival.id, code)
    assigned = db.scalar(select(func.count()).select_from(Event).where(Event.venue_id == v.id, Event.deleted_at.is_(None)))
    if assigned:
        raise HTTPException(409, f"{v.name} still has {assigned} events assigned. Move them first.")
    db.delete(v)
    record(db, user, festival.id, "venue.delete", "venue", code, f"{user.name} deleted venue {v.name}")
    db.commit()


def resource_row(r: Resource) -> dict[str, Any]:
    usable = r.kind == "merch" or (r.condition or "").lower() == "good"
    return {"code": r.code, "kind": r.kind, "name": r.name, "size": r.size, "quantity": r.quantity, "reserved_quantity": r.reserved_quantity,
            "available": max(0, r.quantity - r.reserved_quantity) if usable else 0, "location": r.location, "condition": r.condition}


@router.get("/resources", dependencies=[Depends(require("venues.read"))])
def list_resources(page: int = Query(1, ge=1), q: str | None = Query(None, max_length=80), kind: str | None = None, condition: str | None = None,
                   location: str | None = None, sort: str | None = None, festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    query = select(Resource).where(Resource.festival_id == festival.id)
    if q:
        query = query.where(or_(Resource.name.ilike(like(q), escape="\\"), Resource.code.ilike(like(q), escape="\\")))
    if kind:
        query = query.where(Resource.kind == kind)
    if condition:
        query = query.where(Resource.condition == condition)
    if location:
        query = query.where(Resource.location == location)
    columns = {"code": Resource.code, "item": Resource.name, "location": Resource.location, "total": Resource.quantity, "reserved": Resource.reserved_quantity,
               "available": Resource.quantity - Resource.reserved_quantity, "condition": Resource.condition}
    return paged(db, ordered(query, sort, columns, Resource.kind, Resource.name, Resource.code), page, resource_row)


@router.post("/resources", status_code=201, dependencies=[Depends(require("venues.write"))])
def create_resource(body: ResourceIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    count = db.scalar(select(func.count()).select_from(Resource).where(Resource.festival_id == festival.id)) or 0
    r = Resource(festival_id=festival.id, code=f"R{count + 1:03d}", **body.model_dump())
    db.add(r)
    db.flush()
    record(db, user, festival.id, "resource.create", "resource", r.code, f"{user.name} added {r.quantity} {r.name}")
    db.commit()
    return resource_row(r)


@router.patch("/resources/{code}", dependencies=[Depends(require("venues.write"))])
def update_resource(code: str, body: ResourceIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    r = by_code(db, Resource, festival.id, code)
    if body.quantity < r.reserved_quantity:
        raise HTTPException(409, f"{r.reserved_quantity} are reserved, so quantity cannot drop to {body.quantity}")
    for field, value in body.model_dump().items():
        setattr(r, field, value)
    record(db, user, festival.id, "resource.update", "resource", code, f"{user.name} updated {r.name} ({code})")
    db.commit()
    return resource_row(r)


@router.post("/resources/{code}/allocate", status_code=201, dependencies=[Depends(require("venues.write"))])
def allocate_resource(code: str, body: AllocationIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    r = db.scalar(select(Resource).where(Resource.festival_id == festival.id, Resource.code == code).with_for_update())
    if not r:
        raise HTTPException(404, f"Resource {code} not found")
    event = by_code(db, Event, festival.id, body.event_code)
    available = resource_row(r)["available"]
    if r.kind == "logistics" and event.start_at:
        available = min(available, free_units(r.id, r.quantity, event.start_at, event.end_at, bookings(db, festival.id), skip_event=event.id))
    if body.quantity > available:
        raise HTTPException(409, f"Only {available} {r.name} available. Condition is {r.condition or 'unknown'}.")
    existing = db.scalar(select(ResourceAllocation).where(ResourceAllocation.resource_id == r.id, ResourceAllocation.event_id == event.id))
    if existing:
        existing.quantity += body.quantity
    else:
        db.add(ResourceAllocation(festival_id=festival.id, resource_id=r.id, event_id=event.id, quantity=body.quantity))
    r.reserved_quantity += body.quantity
    record(db, user, festival.id, "resource.allocate", "resource", code, f"{user.name} allocated {body.quantity} {r.name} to {event.name}")
    db.commit()
    return resource_row(r)

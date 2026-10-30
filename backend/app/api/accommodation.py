import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.api.common import by_code, like, paged
from app.auth.deps import current_user, festival_access, permissions_of, require
from app.database import get_db
from app.models import AccommodationAllocation, Festival, Hostel, Participant, Room, User
from app.services.allocator import Beds, Person, housing_group, plan_allocation, suits_access, summarize
from app.services.audit import record
from app.services.housing import (
    ACTIVE, contingent_codes, external_commitment, hospitality_budget, occupied_counts, open_rooms, to_person, unallocated_query,
)

router = APIRouter(prefix="/api/festivals/{festival_id}/accommodation", tags=["accommodation"])


class HostelIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    kind: str = Field(default="on_campus", pattern="^(on_campus|external)$")
    cost_per_bed_night: Decimal | None = Field(default=None, ge=0)
    distance_km: Decimal | None = Field(default=None, ge=0)
    transport_available: bool | None = None
    status: str = Field(default="available", pattern="^(available|closed)$")


class RoomIn(BaseModel):
    hostel_code: str
    code: str = Field(min_length=2, max_length=30)
    floor: int | None = Field(default=None, ge=0, le=50)
    gender_policy: str | None = Field(default=None, pattern="^(boys|girls)$")
    capacity: int = Field(ge=0, le=1000)
    accessible: bool | None = None
    status: str = Field(default="available", pattern="^(available|cleaning|closed)$")


class AllocateIn(BaseModel):
    participant_code: str
    room_code: str
    override_reason: str | None = Field(default=None, max_length=500)


class PlanIn(BaseModel):
    budget_limit: Decimal | None = Field(default=None, ge=0)


class ApplyIn(PlanIn):
    state_hash: str
    reason: str | None = Field(default=None, max_length=500)


@router.get("/summary", dependencies=[Depends(require("accommodation.read"))])
def summary(festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    occupied = occupied_counts(db, festival.id)
    hostels: dict[int, dict[str, Any]] = {}
    over = []
    rows = db.execute(select(Room, Hostel).join(Hostel, Hostel.id == Room.hostel_id).where(Room.festival_id == festival.id).order_by(Hostel.kind.desc(), Hostel.code))
    for room, hostel in rows:
        h = hostels.setdefault(hostel.id, {"code": hostel.code, "name": hostel.name, "kind": hostel.kind, "status": hostel.status, "capacity": 0, "occupied": 0,
                                           "cost_per_bed_night": hostel.cost_per_bed_night, "distance_km": hostel.distance_km})
        used = occupied.get(room.id, 0)
        h["capacity"] += room.capacity if room.status == "available" and hostel.status == "available" else 0
        h["occupied"] += used
        if used > room.capacity:
            over.append({"room": room.code, "capacity": room.capacity, "occupied": used})
    return {"hostels": list(hostels.values()), "over_capacity": over,
            "unallocated": db.scalar(select(func.count()).select_from(unallocated_query(festival.id).subquery())),
            "budget": hospitality_budget(db, festival.id)}


@router.post("/hostels", status_code=201, dependencies=[Depends(require("accommodation.write"))])
def create_hostel(body: HostelIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    count = db.scalar(select(func.count()).select_from(Hostel).where(Hostel.festival_id == festival.id)) or 0
    hostel = Hostel(festival_id=festival.id, code=f"H{count + 1:02d}", **body.model_dump())
    db.add(hostel)
    db.flush()
    record(db, user, festival.id, "hostel.create", "hostel", hostel.code, f"{user.name} added {hostel.name}")
    db.commit()
    return {"code": hostel.code}


@router.post("/rooms", status_code=201, dependencies=[Depends(require("accommodation.write"))])
def create_room(body: RoomIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    hostel = by_code(db, Hostel, festival.id, body.hostel_code)
    if db.scalar(select(Room.id).where(Room.festival_id == festival.id, Room.code == body.code)):
        raise HTTPException(409, f"Room {body.code} already exists")
    room = Room(festival_id=festival.id, hostel_id=hostel.id, **body.model_dump(exclude={"hostel_code"}))
    db.add(room)
    record(db, user, festival.id, "room.create", "room", body.code, f"{user.name} added room {body.code} to {hostel.name}")
    db.commit()
    return {"code": room.code}


@router.get("/rooms", dependencies=[Depends(require("accommodation.read"))])
def list_rooms(page: int = Query(1, ge=1), hostel: str | None = None, q: str | None = Query(None, max_length=40), gender: str | None = None,
               accessible: bool | None = None, free_only: bool = False, festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    occupied = occupied_counts(db, festival.id)
    query = select(Room).join(Hostel, Hostel.id == Room.hostel_id).where(Room.festival_id == festival.id)
    if hostel:
        query = query.where(Hostel.code == hostel)
    if q:
        query = query.where(Room.code.ilike(like(q), escape="\\"))
    if gender:
        query = query.where(Room.gender_policy == gender)
    if accessible is not None:
        query = query.where(Room.accessible.is_(accessible))
    if free_only:
        counts = select(AccommodationAllocation.room_id, func.count().label("n")).where(ACTIVE).group_by(AccommodationAllocation.room_id).subquery()
        query = query.outerjoin(counts, counts.c.room_id == Room.id).where(func.coalesce(counts.c.n, 0) < Room.capacity)
    names = dict(db.execute(select(Hostel.id, Hostel.name).where(Hostel.festival_id == festival.id)).all())

    def row(r: Room) -> dict[str, Any]:
        used = occupied.get(r.id, 0)
        return {"code": r.code, "hostel": names[r.hostel_id], "floor": r.floor, "gender_policy": r.gender_policy, "capacity": r.capacity,
                "occupied": used, "available": max(0, r.capacity - used), "accessible": r.accessible, "status": r.status}
    return paged(db, query.order_by(Room.code), page, row)


@router.get("/allocations", dependencies=[Depends(require("accommodation.read"))])
def list_allocations(page: int = Query(1, ge=1), q: str | None = Query(None, max_length=80), room: str | None = None,
                     festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    query = (select(AccommodationAllocation).join(Participant, Participant.id == AccommodationAllocation.participant_id)
             .join(Room, Room.id == AccommodationAllocation.room_id).where(AccommodationAllocation.festival_id == festival.id, ACTIVE))
    if q:
        query = query.where(or_(Participant.code.ilike(like(q), escape="\\"), Participant.full_name.ilike(like(q), escape="\\")))
    if room:
        query = query.where(Room.code == room)
    people = {}
    rooms = {}

    def row(a: AccommodationAllocation) -> dict[str, Any]:
        p = people.setdefault(a.participant_id, db.get(Participant, a.participant_id))
        r = rooms.setdefault(a.room_id, db.get(Room, a.room_id))
        return {"id": a.id, "participant": p.code, "name": p.full_name, "room": r.code, "status": a.status, "check_in_at": a.check_in_at,
                "check_out_at": a.check_out_at, "relaxed_constraints": a.relaxed_constraints, "override_reason": a.override_reason}
    return paged(db, query.order_by(Participant.code), page, row)


@router.get("/unallocated", dependencies=[Depends(require("accommodation.read"))])
def unallocated(page: int = Query(1, ge=1), festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    sensitive = "participants.read_sensitive" in permissions_of(user)
    return paged(db, unallocated_query(festival.id).order_by(Participant.code), page, lambda p: {
        "code": p.code, "name": p.full_name, "college": p.college, "gender": p.gender_as_given if sensitive else None,
        "accessibility": p.accessibility_needs if sensitive else None, "restricted": not sensitive})


def state_hash(people: list[Person], rooms: list[Beds]) -> str:
    """Changes whenever a person or a free bed changes, so a stale preview cannot be applied."""
    parts = sorted(f"{p.code}:{p.gender}:{p.accessibility}" for p in people) + sorted(f"{r.code}:{r.free}" for r in rooms)
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def build_plan(db: Session, festival: Festival, limit: Decimal | None):
    contingents = contingent_codes(db, festival.id)
    people = [to_person(p, contingents) for p in db.scalars(unallocated_query(festival.id).order_by(Participant.code))]
    rooms = open_rooms(db, festival.id)
    # The limit covers all external stays, so only what is left of it can be spent here.
    remaining = None if limit is None else max(Decimal(0), limit - external_commitment(db, festival.id))
    return people, rooms, plan_allocation(people, rooms, remaining), state_hash(people, rooms)


def effective_limit(db: Session, festival: Festival, requested: Decimal | None) -> Decimal | None:
    return requested if requested is not None else hospitality_budget(db, festival.id)


@router.post("/plan", dependencies=[Depends(require("accommodation.read"))])
def preview_plan(body: PlanIn, festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    limit = effective_limit(db, festival, body.budget_limit)
    people, _, plan, hash_value = build_plan(db, festival, limit)
    approved = hospitality_budget(db, festival.id)
    return {"summary": summarize(plan, len(people)), "state_hash": hash_value, "budget_limit": limit, "approved_budget": approved,
            "already_committed": external_commitment(db, festival.id),
            "over_approved_budget": bool(approved is not None and limit is not None and limit > approved)}


@router.post("/plan/apply", dependencies=[Depends(require("accommodation.write"))])
def apply_plan(body: ApplyIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    approved = hospitality_budget(db, festival.id)
    limit = effective_limit(db, festival, body.budget_limit)
    over = approved is not None and limit is not None and limit > approved
    if over:
        if "finance.approve" not in permissions_of(user):
            raise HTTPException(403, "Exceeding the approved budget needs someone who can approve finance")
        if not (body.reason and len(body.reason.strip()) >= 5):
            raise HTTPException(422, "Give a reason for exceeding the approved budget")
    people, _, plan, hash_value = build_plan(db, festival, limit)
    if hash_value != body.state_hash:
        raise HTTPException(409, "Beds or participants changed since this preview. Run the plan again.")
    db.add_all(AccommodationAllocation(festival_id=festival.id, participant_id=a["participant_id"], room_id=a["room_id"],
                                       relaxed_constraints=a["relaxed"], override_reason=body.reason if over else None) for a in plan.assignments)
    result = summarize(plan, len(people))
    record(db, user, festival.id, "accommodation.plan", "accommodation", "plan",
           f"{user.name} allocated {result['placed']:,} beds; {result['held']:,} held for review", after=result, reason=body.reason if over else None)
    db.commit()
    return result


def room_problems(p: Participant, room: Room, hostel: Hostel, used: int) -> list[str]:
    problems = []
    if room.status != "available" or hostel.status != "available":
        problems.append(f"{room.code} is not available")
    if used >= room.capacity:
        problems.append(f"{room.code} is full")
    group = housing_group(p.gender_as_given)
    if room.gender_policy and group is None:
        problems.append("Gender is blank, and this room is gendered")
    elif room.gender_policy and group != room.gender_policy:
        problems.append(f"{room.code} is a {room.gender_policy} room")
    need = p.accessibility_needs
    bed = Beds(room.id, room.code, hostel.name, hostel.kind, room.floor, room.gender_policy, room.accessible, 0)
    if need and need != "None" and not suits_access(Person(p.id, p.code, "", None, need, None, None, None), bed):
        problems.append(f"Room does not meet: {need}")
    return problems


@router.post("/allocate", status_code=201, dependencies=[Depends(require("accommodation.write"))])
def allocate(body: AllocateIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = by_code(db, Participant, festival.id, body.participant_code)
    room = db.scalar(select(Room).where(Room.festival_id == festival.id, Room.code == body.room_code).with_for_update())
    if not room:
        raise HTTPException(404, f"Room {body.room_code} not found")
    if db.scalar(select(AccommodationAllocation.id).where(AccommodationAllocation.participant_id == p.id, ACTIVE)):
        raise HTTPException(409, f"{p.code} already has a room. Cancel it first.")
    used = db.scalar(select(func.count()).select_from(AccommodationAllocation).where(AccommodationAllocation.room_id == room.id, ACTIVE)) or 0
    problems = room_problems(p, room, db.get(Hostel, room.hostel_id), used)
    if any("is full" in x or "not available" in x for x in problems):
        raise HTTPException(409, {"message": "This room cannot take anyone", "conflicts": problems})
    if problems and not (body.override_reason and len(body.override_reason.strip()) >= 5):
        raise HTTPException(409, {"message": "This allocation breaks a hard rule. Give a reason to override.", "conflicts": problems})
    db.add(AccommodationAllocation(festival_id=festival.id, participant_id=p.id, room_id=room.id, relaxed_constraints=problems,
                                   override_reason=body.override_reason if problems else None))
    # Room and participant codes only: the reason for an override may be sensitive, so it stays on the allocation.
    record(db, user, festival.id, "accommodation.allocate", "allocation", p.code, f"{user.name} allocated {p.code} to {room.code}",
           reason=body.override_reason if problems else None)
    db.commit()
    return {"overridden": problems}


@router.get("/suggest/{participant_code}", dependencies=[Depends(require("accommodation.read"))])
def suggest(participant_code: str, festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    p = by_code(db, Participant, festival.id, participant_code)
    occupied = occupied_counts(db, festival.id)
    rows = db.execute(select(Room, Hostel).join(Hostel, Hostel.id == Room.hostel_id).where(Room.festival_id == festival.id).order_by(
        case((Hostel.kind == "on_campus", 0), else_=1), Hostel.cost_per_bed_night.nulls_first(), Room.code))
    rooms = [{"code": r.code, "hostel": h.name, "available": r.capacity - occupied.get(r.id, 0)} for r, h in rows
             if not room_problems(p, r, h, occupied.get(r.id, 0))]
    return rooms[:8]


def own_allocation(db: Session, festival_id: int, allocation_id: int) -> AccommodationAllocation:
    allocation = db.get(AccommodationAllocation, allocation_id)
    if not allocation or allocation.festival_id != festival_id or allocation.status == "cancelled":
        raise HTTPException(404, "Allocation not found")
    return allocation


@router.post("/allocations/{allocation_id}/check-in", dependencies=[Depends(require("accommodation.write"))])
def room_check_in(allocation_id: int, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = own_allocation(db, festival.id, allocation_id)
    if a.check_in_at:
        raise HTTPException(409, "Already checked in")
    a.check_in_at, a.status = datetime.now(timezone.utc), "checked_in"
    record(db, user, festival.id, "accommodation.check_in", "allocation", allocation_id, f"{user.name} recorded a room check-in")
    db.commit()


@router.post("/allocations/{allocation_id}/check-out", dependencies=[Depends(require("accommodation.write"))])
def room_check_out(allocation_id: int, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = own_allocation(db, festival.id, allocation_id)
    if not a.check_in_at:
        raise HTTPException(409, "Not checked in yet")
    if a.check_out_at:
        raise HTTPException(409, "Already checked out")
    a.check_out_at, a.status = datetime.now(timezone.utc), "checked_out"
    record(db, user, festival.id, "accommodation.check_out", "allocation", allocation_id, f"{user.name} recorded a room check-out")
    db.commit()


@router.delete("/allocations/{allocation_id}", status_code=204, dependencies=[Depends(require("accommodation.write"))])
def cancel_allocation(allocation_id: int, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = own_allocation(db, festival.id, allocation_id)
    a.status = "cancelled"
    record(db, user, festival.id, "accommodation.cancel", "allocation", allocation_id, f"{user.name} cancelled a room allocation")
    db.commit()

"""Bed and budget queries shared by the accommodation API and the Ripple Simulator."""
from collections.abc import Collection
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AccommodationAllocation, AuditLog, Budget, Contingent, Department, Festival, Hostel, Participant, Room,
)
from app.services.allocator import Beds, Person, nights, plan_allocation

ACTIVE = AccommodationAllocation.status != "cancelled"


def occupied_counts(db: Session, festival_id: int) -> dict[int, int]:
    return dict(db.execute(select(AccommodationAllocation.room_id, func.count()).where(
        AccommodationAllocation.festival_id == festival_id, ACTIVE).group_by(AccommodationAllocation.room_id)).all())


def unallocated_query(festival_id: int):
    allocated = select(AccommodationAllocation.participant_id).where(AccommodationAllocation.festival_id == festival_id, ACTIVE)
    return select(Participant).where(Participant.festival_id == festival_id, Participant.deleted_at.is_(None),
                                     Participant.accommodation_required.is_(True), Participant.id.not_in(allocated))


def hospitality_budget(db: Session, festival_id: int) -> Decimal | None:
    return db.scalar(select(Budget.approved_amount).join(Department, Department.id == Budget.department_id)
                     .where(Budget.festival_id == festival_id, Department.name == "Hospitality"))


def contingent_codes(db: Session, festival_id: int) -> dict[int, str]:
    return dict(db.execute(select(Contingent.id, Contingent.code).where(Contingent.festival_id == festival_id)).all())


def to_person(p: Participant, contingents: dict[int, str]) -> Person:
    return Person(p.id, p.code, contingents.get(p.contingent_id, "none"), p.gender_as_given, p.accessibility_needs, p.age, p.arrival_at, p.departure_at)


def open_rooms(db: Session, festival_id: int, exclude_hostel_id: int | None = None) -> list[Beds]:
    occupied = occupied_counts(db, festival_id)
    query = select(Room, Hostel).join(Hostel, Hostel.id == Room.hostel_id).where(
        Room.festival_id == festival_id, Room.status == "available", Hostel.status == "available")
    if exclude_hostel_id:
        query = query.where(Hostel.id != exclude_hostel_id)
    return [Beds(r.id, r.code, h.name, h.kind, r.floor, r.gender_policy, r.accessible, max(0, r.capacity - occupied.get(r.id, 0)),
                 h.cost_per_bed_night, h.transport_available, h.distance_km) for r, h in db.execute(query)]


def external_commitment(db: Session, festival_id: int, exclude: Collection[int] = ()) -> Decimal:
    """What active external stays already cost, optionally leaving out some participants."""
    rows = db.execute(
        select(Participant, Hostel.cost_per_bed_night)
        .join(AccommodationAllocation, AccommodationAllocation.participant_id == Participant.id)
        .join(Room, Room.id == AccommodationAllocation.room_id).join(Hostel, Hostel.id == Room.hostel_id)
        .where(AccommodationAllocation.festival_id == festival_id, ACTIVE, Hostel.kind == "external"))
    total = Decimal(0)
    for p, cost in rows:
        if p.id not in exclude:
            total += (cost or Decimal(0)) * (nights(to_person(p, {})) or 0)
    return total


def apply_sample_plan(db: Session, festival: Festival) -> None:
    """Seed the sample allocation plan the brief asks for, within the approved budget."""
    contingents = contingent_codes(db, festival.id)
    people = [to_person(p, contingents) for p in db.scalars(unallocated_query(festival.id).order_by(Participant.code))]
    plan = plan_allocation(people, open_rooms(db, festival.id), hospitality_budget(db, festival.id))
    db.add_all(AccommodationAllocation(festival_id=festival.id, participant_id=a["participant_id"], room_id=a["room_id"],
                                       relaxed_constraints=a["relaxed"]) for a in plan.assignments)
    db.add(AuditLog(festival_id=festival.id, actor_label="Planner", action="accommodation.plan", entity="accommodation", entity_id="plan",
                    summary=f"Generated the sample plan: {len(plan.assignments):,} beds allocated, {len(plan.held):,} held for review"))

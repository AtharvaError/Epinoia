"""Who holds which equipment and when. A lot can serve two events that do not overlap."""
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, Event, Resource, ResourceAllocation
from app.services.schedule_plan import TURNAROUND


@dataclass
class Booking:
    allocation_id: int | None
    resource_id: int
    event_id: int
    quantity: int
    start: datetime
    end: datetime


def equipment_needs(event: Event) -> list[tuple[str, int]]:
    needs = []
    for part in (event.equipment_needs or "").split(";"):
        match = re.fullmatch(r"(?:(\d+)\s+)?(.+?)s?", part.strip())
        if match and "no special" not in part:
            needs.append((match[2], int(match[1] or 1)))
    return needs


def is_usable(lot: Resource) -> bool:
    return lot.kind == "logistics" and (lot.condition or "").lower() == "good"


def bookings(db: Session, festival_id: int) -> list[Booking]:
    rows = db.execute(select(ResourceAllocation, Event).join(Event, Event.id == ResourceAllocation.event_id).where(
        ResourceAllocation.festival_id == festival_id, Event.deleted_at.is_(None), Event.status != "cancelled", Event.start_at.is_not(None)))
    return [Booking(a.id, a.resource_id, e.id, a.quantity, e.start_at, e.end_at) for a, e in rows]


def free_units(resource_id: int, quantity: int, start: datetime, end: datetime, booked: list[Booking], skip_event: int | None = None) -> int:
    """Units left for this window, allowing turnaround to move kit between events."""
    # ponytail: adds every overlapping booking, so it can under-count when those bookings do not overlap each other.
    used = sum(b.quantity for b in booked if b.resource_id == resource_id and b.event_id != skip_event and b.start - TURNAROUND < end and start < b.end + TURNAROUND)
    return quantity - used


def apply_inventory_plan(db: Session, festival_id: int) -> None:
    """Seed the sample inventory plan the brief asks for: good stock assigned to each event's stated needs."""
    lots = list(db.scalars(select(Resource).where(Resource.festival_id == festival_id, Resource.kind == "logistics").order_by(Resource.code)))
    events = db.scalars(select(Event).where(Event.festival_id == festival_id, Event.deleted_at.is_(None), Event.start_at.is_not(None)).order_by(Event.start_at, Event.code)).all()
    booked: list[Booking] = []
    placed = 0
    for event in events:
        for item, needed in equipment_needs(event):
            # Kit already at the venue first, then the fullest lot.
            options = [lot for lot in lots if lot.name == item and is_usable(lot)]
            options.sort(key=lambda lot: (lot.location != (event.venue.name if event.venue else None), -free_units(lot.id, lot.quantity, event.start_at, event.end_at, booked)))
            for lot in options:
                take = min(needed, free_units(lot.id, lot.quantity, event.start_at, event.end_at, booked))
                if take <= 0:
                    continue
                db.add(ResourceAllocation(festival_id=festival_id, resource_id=lot.id, event_id=event.id, quantity=take))
                booked.append(Booking(None, lot.id, event.id, take, event.start_at, event.end_at))
                needed -= take
                placed += take
                if not needed:
                    break
    db.add(AuditLog(festival_id=festival_id, actor_label="Planner", action="inventory.plan", entity="inventory", entity_id="plan",
                    summary=f"Generated the sample inventory plan: {placed:,} units assigned to events"))


def equipment_gaps(db: Session, festival_id: int) -> list[dict[str, Any]]:
    """Events holding less working equipment than they say they need."""
    lots = {r.id: r for r in db.scalars(select(Resource).where(Resource.festival_id == festival_id, Resource.kind == "logistics"))}
    held: dict[tuple[int, str], int] = {}
    for b in bookings(db, festival_id):
        lot = lots.get(b.resource_id)
        if lot and is_usable(lot):
            held[(b.event_id, lot.name)] = held.get((b.event_id, lot.name), 0) + b.quantity
    gaps = []
    for event in db.scalars(select(Event).where(Event.festival_id == festival_id, Event.deleted_at.is_(None), Event.status != "cancelled").order_by(Event.start_at)):
        for item, needed in equipment_needs(event):
            have = held.get((event.id, item), 0)
            if have < needed:
                gaps.append({"code": event.code, "name": event.name, "item": item, "needed": needed, "allocated": have})
    return gaps


def double_bookings(db: Session, festival_id: int) -> list[dict[str, Any]]:
    """Working lots promised to more events at once than they have units."""
    lots = {r.id: r for r in db.scalars(select(Resource).where(Resource.festival_id == festival_id, Resource.kind == "logistics")) if is_usable(r)}
    booked = bookings(db, festival_id)
    names = dict(db.execute(select(Event.id, Event.name).where(Event.festival_id == festival_id)).all())
    found = []
    for b in booked:
        lot = lots.get(b.resource_id)
        if lot and free_units(lot.id, lot.quantity, b.start, b.end, booked, skip_event=b.event_id) < b.quantity:
            found.append({"code": lot.code, "item": lot.name, "location": lot.location, "event": names.get(b.event_id), "quantity": b.quantity})
    return found

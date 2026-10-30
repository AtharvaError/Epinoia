"""Schedule conflict checks shared by events, participants and volunteers."""
from datetime import datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import Event, Registration, Venue


def venue_conflicts(db: Session, festival_id: int, venue_id: int, start: datetime, end: datetime,
                    exclude_event_id: int | None = None) -> list[Event]:
    query = select(Event).where(Event.festival_id == festival_id, Event.venue_id == venue_id, Event.deleted_at.is_(None),
                                Event.status != "cancelled", Event.start_at < end, Event.end_at > start)
    if exclude_event_id:
        query = query.where(Event.id != exclude_event_id)
    return list(db.scalars(query.order_by(Event.start_at)))


def outside_venue_hours(venue: Venue, start: datetime, end: datetime) -> bool:
    return not any(datetime.fromisoformat(w["start"]) <= start and end <= datetime.fromisoformat(w["end"]) for w in venue.availability)


def participant_clashes(db: Session, participant_id: int, event: Event) -> list[Event]:
    """Events this participant is already in that overlap the given event."""
    if not event.start_at or not event.end_at:
        return []
    query = (select(Event).join(Registration, Registration.event_id == Event.id)
             .where(Registration.participant_id == participant_id, Event.id != event.id, Event.deleted_at.is_(None),
                    Event.start_at < event.end_at, Event.end_at > event.start_at))
    return list(db.scalars(query))


def all_conflicts(db: Session, festival_id: int) -> dict[str, list[dict[str, Any]]]:
    venue = db.execute(text("""
        SELECT a.code AS first_code, a.name AS first_name, b.code AS second_code, b.name AS second_name, v.name AS venue
        FROM events a JOIN events b ON a.venue_id=b.venue_id AND a.id<b.id JOIN venues v ON v.id=a.venue_id
        WHERE a.festival_id=:f AND a.deleted_at IS NULL AND b.deleted_at IS NULL
          AND a.start_at<b.end_at AND b.start_at<a.end_at"""), {"f": festival_id}).mappings().all()
    people = db.execute(text("""
        SELECT a.code AS first_code, a.name AS first_name, b.code AS second_code, b.name AS second_name, count(*) AS people
        FROM registrations r1 JOIN registrations r2 ON r2.participant_id=r1.participant_id AND r1.event_id<r2.event_id
        JOIN events a ON a.id=r1.event_id JOIN events b ON b.id=r2.event_id
        WHERE r1.festival_id=:f AND a.deleted_at IS NULL AND b.deleted_at IS NULL
          AND a.start_at<b.end_at AND b.start_at<a.end_at
        GROUP BY a.id, b.id ORDER BY people DESC"""), {"f": festival_id}).mappings().all()
    capacity = db.execute(text("""
        SELECT e.code, e.name, v.name AS venue, v.capacity, count(r.id) AS registered
        FROM events e JOIN venues v ON v.id=e.venue_id JOIN registrations r ON r.event_id=e.id
        WHERE e.festival_id=:f AND e.deleted_at IS NULL GROUP BY e.id, v.id
        HAVING count(r.id) > v.capacity ORDER BY count(r.id) - v.capacity DESC"""), {"f": festival_id}).mappings().all()
    return {"venue": [dict(r) for r in venue], "participants": [dict(r) for r in people], "capacity": [dict(r) for r in capacity]}

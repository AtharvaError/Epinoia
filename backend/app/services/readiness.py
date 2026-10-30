"""Per-participant readiness.

A participant is a bundle of demands that can contradict each other: several events,
a fixed arrival/departure window, a bed, accessibility and a team. Each conflict is
already detected in the module that owns it; this collects them into one answer —
is this person actually ready, and if not, why?

Read-only. No new rules: every check mirrors one that already exists elsewhere
(see analytics/dashboard.py and services/events.py).
"""
from typing import Any

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.models import AccommodationAllocation, Event, Participant, Registration, Room


def _has_access_need(value: str | None) -> bool:
    return bool(value) and value != "None"


# --- Aggregate clause: reused by the Command Center count, the list filter and row flags ---

def _presence_gap():
    """Registered for an event that starts before arrival or ends after departure."""
    return exists(select(Registration.id).join(Event, Event.id == Registration.event_id).where(
        Registration.participant_id == Participant.id, Event.deleted_at.is_(None),
        Event.start_at.isnot(None), Event.end_at.isnot(None),
        Participant.arrival_at.isnot(None), Participant.departure_at.isnot(None),
        Participant.departure_at >= Participant.arrival_at,
        or_(Participant.arrival_at > Event.start_at, Participant.departure_at < Event.end_at)))


def _event_clash():
    """Two registered events overlap in time."""
    r1, r2, a, b = aliased(Registration), aliased(Registration), aliased(Event), aliased(Event)
    return exists(select(r1.id)
                  .join(r2, and_(r2.participant_id == r1.participant_id, r1.event_id < r2.event_id))
                  .join(a, a.id == r1.event_id).join(b, b.id == r2.event_id)
                  .where(r1.participant_id == Participant.id, a.deleted_at.is_(None), b.deleted_at.is_(None),
                         a.start_at < b.end_at, b.start_at < a.end_at))


def _active_allocation():
    return select(AccommodationAllocation.id).where(
        AccommodationAllocation.participant_id == Participant.id, AccommodationAllocation.status != "cancelled")


def _needs_bed():
    return and_(Participant.accommodation_required.is_(True), ~exists(_active_allocation()))


def _access_unmet():
    """Has an accessibility need but the allocated room is not an accessible one."""
    return and_(Participant.accessibility_needs.isnot(None), Participant.accessibility_needs != "None",
                exists(select(AccommodationAllocation.id).join(Room, Room.id == AccommodationAllocation.room_id).where(
                    AccommodationAllocation.participant_id == Participant.id,
                    AccommodationAllocation.status != "cancelled", Room.accessible.isnot(True))))


def _team_unresolved():
    return exists(select(Registration.id).where(
        Registration.participant_id == Participant.id,
        Registration.team_code_raw.isnot(None), Registration.team_id.is_(None)))


def unready_clause():
    """A participant is not ready if any single check fails."""
    return or_(_presence_gap(), _event_clash(), _needs_bed(), _access_unmet(), _team_unresolved())


def unready_count(db: Session, festival_id: int) -> int:
    return db.scalar(select(func.count()).select_from(Participant).where(
        Participant.festival_id == festival_id, Participant.deleted_at.is_(None), unready_clause())) or 0


def unready_among(db: Session, festival_id: int, codes: list[str]) -> set[str]:
    """Which of these codes are not ready — one query, so a list page can flag rows cheaply."""
    if not codes:
        return set()
    return set(db.scalars(select(Participant.code).where(
        Participant.festival_id == festival_id, Participant.deleted_at.is_(None),
        Participant.code.in_(codes), unready_clause())))


# --- Per-participant detail: the reasons, for the profile ---

def _issue(key: str, severity: str, title: str, detail: str) -> dict[str, Any]:
    return {"key": key, "severity": severity, "title": title, "detail": detail}


def participant_readiness(db: Session, p: Participant) -> list[dict[str, Any]]:
    """The conflicts holding this person up, most severe first. Empty means ready."""
    issues: list[dict[str, Any]] = []
    events = list(db.scalars(select(Event).join(Registration, Registration.event_id == Event.id)
                             .where(Registration.participant_id == p.id, Event.deleted_at.is_(None)).order_by(Event.start_at)))
    timed = [e for e in events if e.start_at and e.end_at]

    if p.arrival_at and p.departure_at and p.departure_at >= p.arrival_at:
        gap = [e for e in timed if p.arrival_at > e.start_at or p.departure_at < e.end_at]
        if gap:
            issues.append(_issue("presence_gap", "critical",
                                 f"{len(gap)} event{'s' if len(gap) > 1 else ''} fall outside their stay",
                                 "They arrive after it starts or leave before it ends: "
                                 + ", ".join(f"{e.name} ({e.code})" for e in gap[:4])))

    clashed = sorted({e.code: e.name for i, a in enumerate(timed) for b in timed[i + 1:]
                      if a.start_at < b.end_at and b.start_at < a.end_at for e in (a, b)}.items())
    if clashed:
        issues.append(_issue("event_clash", "high", f"{len(clashed)} events overlap in time",
                             "Registered for events that run at the same time: "
                             + ", ".join(f"{name} ({code})" for code, name in clashed[:4])))

    allocation = db.execute(select(AccommodationAllocation, Room).join(Room, Room.id == AccommodationAllocation.room_id)
                            .where(AccommodationAllocation.participant_id == p.id,
                                   AccommodationAllocation.status != "cancelled")).first()
    if p.accommodation_required and not allocation:
        detail = "Marked as needing accommodation but no room is assigned."
        if _has_access_need(p.accessibility_needs):
            detail += " They also have an accessibility need, so an accessible bed must be found."
        issues.append(_issue("needs_bed", "high", "Needs a bed allocated", detail))
    elif allocation and _has_access_need(p.accessibility_needs) and not allocation[1].accessible:
        issues.append(_issue("access_unmet", "critical", "Allocated room is not accessible",
                             f"Has an accessibility need but is placed in {allocation[1].code}, which is not an accessible room."))

    unresolved = sorted(set(db.scalars(select(Registration.team_code_raw).where(
        Registration.participant_id == p.id, Registration.team_code_raw.isnot(None), Registration.team_id.is_(None)))))
    if unresolved:
        issues.append(_issue("team_unresolved", "medium",
                             f"{len(unresolved)} team registration{'s' if len(unresolved) > 1 else ''} unresolved",
                             "Entered a team event but the team is not linked yet: " + ", ".join(unresolved[:6])))

    order = {"critical": 0, "high": 1, "medium": 2}
    return sorted(issues, key=lambda i: order[i["severity"]])

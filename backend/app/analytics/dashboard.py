"""Command Center metrics. Everything is computed from database rows at request time."""
from datetime import date, datetime
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import Event, Festival, Vendor, Venue
from app.services.equipment import equipment_gaps
from app.services.events import outside_venue_hours, venue_conflicts
from app.services.food import coverage, diet_counts
from app.services.pack_import import IST
from app.services.readiness import unready_count
from app.services.schedule_plan import SUITABLE_VENUES, TURNAROUND


def _one(db: Session, sql: str, **params: Any) -> Any:
    return db.execute(text(sql), params).scalar_one()


def _rows(db: Session, sql: str, **params: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in db.execute(text(sql), params).mappings()]


def _kpi(key: str, label: str, value: Any, source: str, total: Any = None, note: str | None = None) -> dict[str, Any]:
    return {"key": key, "label": label, "value": value, "total": total, "source": source, "note": note}


def festival_phase(festival: Festival, today: date) -> str:
    if today < festival.start_date:
        return "Upcoming"
    return "Live" if today <= festival.end_date else "Completed"


BEDS = "SELECT coalesce(sum(r.capacity),0) FROM rooms r JOIN hostels h ON h.id=r.hostel_id WHERE r.festival_id=:f AND r.status='available' AND h.status='available'"
OCCUPIED = "SELECT count(*) FROM accommodation_allocations WHERE festival_id=:f AND status<>'cancelled'"
UNALLOCATED = """
    SELECT count(*) FROM participants p
    WHERE p.festival_id=:f AND p.deleted_at IS NULL AND p.accommodation_required IS TRUE
      AND NOT EXISTS (SELECT 1 FROM accommodation_allocations a WHERE a.participant_id=p.id AND a.status<>'cancelled')"""
EVENT_LOAD = """
    SELECT e.id, e.code, e.name, e.status, e.volunteers_needed, v.code AS venue_code, v.name AS venue, v.capacity,
           (SELECT count(*) FROM registrations r WHERE r.event_id=e.id) AS registered,
           (SELECT count(*) FROM volunteer_shifts s WHERE s.event_id=e.id AND s.status<>'cancelled') AS staffed
    FROM events e LEFT JOIN venues v ON v.id=e.venue_id
    WHERE e.festival_id=:f AND e.deleted_at IS NULL"""
CLASHES = """
    SELECT count(DISTINCT r1.participant_id) FROM registrations r1
    JOIN registrations r2 ON r2.participant_id=r1.participant_id AND r1.event_id<r2.event_id
    JOIN events a ON a.id=r1.event_id JOIN events b ON b.id=r2.event_id
    WHERE r1.festival_id=:f AND a.deleted_at IS NULL AND b.deleted_at IS NULL
      AND a.start_at<b.end_at AND b.start_at<a.end_at"""
OFF_CAMPUS = """
    SELECT count(*) FROM registrations r
    JOIN participants p ON p.id=r.participant_id JOIN events e ON e.id=r.event_id
    WHERE r.festival_id=:f AND e.deleted_at IS NULL AND p.deleted_at IS NULL
      AND p.departure_at >= p.arrival_at AND (p.arrival_at > e.start_at OR p.departure_at < e.end_at)"""
VENUE_CLASHES = """
    SELECT count(*) FROM events a JOIN events b ON a.venue_id=b.venue_id AND a.id<b.id
    WHERE a.festival_id=:f AND a.deleted_at IS NULL AND b.deleted_at IS NULL
      AND a.start_at<b.end_at AND b.start_at<a.end_at"""
WORST_VENUE_CLASH = """
    SELECT b.code, b.name FROM events a JOIN events b ON a.venue_id=b.venue_id AND a.id<b.id
    WHERE a.festival_id=:f AND a.deleted_at IS NULL AND b.deleted_at IS NULL
      AND a.start_at<b.end_at AND b.start_at<a.end_at ORDER BY b.code LIMIT 1"""
WORST_OFF_CAMPUS = """
    SELECT e.code, e.name, count(*) AS away, v.code AS venue_code, e.start_at, e.end_at
    FROM registrations r JOIN participants p ON p.id=r.participant_id JOIN events e ON e.id=r.event_id JOIN venues v ON v.id=e.venue_id
    WHERE r.festival_id=:f AND e.deleted_at IS NULL AND p.deleted_at IS NULL
      AND p.departure_at >= p.arrival_at AND (p.arrival_at > e.start_at OR p.departure_at < e.end_at)
    GROUP BY e.id, v.code ORDER BY away DESC, e.code LIMIT 1"""


def kpis(db: Session, f: int, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pending = _one(db, "SELECT (SELECT count(*) FROM expenses WHERE festival_id=:f AND deleted_at IS NULL AND approval_status='pending')"
                       " + (SELECT count(*) FROM change_approvals WHERE festival_id=:f AND status='pending')", f=f)
    budget = _one(db, "SELECT coalesce(sum(approved_amount),0) FROM budgets WHERE festival_id=:f", f=f)
    spent = _one(db, "SELECT coalesce(sum(actual_amount),0) FROM expenses WHERE festival_id=:f AND deleted_at IS NULL AND approval_status='approved'", f=f)
    return [
        _kpi("participants", "Total participants", _one(db, "SELECT count(*) FROM participants WHERE festival_id=:f AND deleted_at IS NULL", f=f), "pack"),
        _kpi("teams", "Registered teams", _one(db, "SELECT count(*) FROM teams WHERE festival_id=:f", f=f), "pack",
             note="Pack team IDs are unreliable; most have one member"),
        _kpi("events", "Active events", sum(e["status"] != "cancelled" for e in events), "pack"),
        _kpi("venues", "Available venues", _one(db, "SELECT count(*) FROM venues WHERE festival_id=:f AND maintenance_status='operational'", f=f), "pack",
             total=_one(db, "SELECT count(*) FROM venues WHERE festival_id=:f", f=f)),
        _kpi("beds", "Beds occupied", _one(db, OCCUPIED, f=f), "pack", total=_one(db, BEDS, f=f)),
        _kpi("volunteers", "Active volunteers", _one(db, "SELECT count(*) FROM volunteers WHERE festival_id=:f AND status='active'", f=f), "simulated"),
        _kpi("budget", "Festival budget", budget, "simulated"),
        _kpi("spent", "Expenses incurred", spent, "simulated", total=budget),
        _kpi("approvals", "Pending approvals", pending, "simulated"),
        _kpi("incidents", "Open incidents", _one(db, "SELECT count(*) FROM incidents WHERE festival_id=:f AND status NOT IN ('resolved','closed')", f=f), "simulated"),
    ]


FIXES_TRIED = 3


def link(label: str, path: str, permission: str, **params: Any) -> dict[str, str]:
    """What to do about an alert. The page reads the query string and fills itself in."""
    return {"label": label, "to": f"{path}?{urlencode(params)}" if params else path, "permission": permission}


def simulate(label: str, scenario: str, **change: Any) -> dict[str, str]:
    return link(label, "/ripple", "simulation.run", scenario=scenario, **{k: v for k, v in change.items() if v is not None})


def to_form(moment: datetime) -> str:
    return moment.astimezone(IST).strftime("%Y-%m-%dT%H:%M")


def roomier_venue(db: Session, f: int, code: str, registered: int, free: bool = True) -> Venue | None:
    """The smallest suitable venue that seats everyone, and is free if asked. Ripple judges it fully."""
    event = db.scalar(select(Event).where(Event.festival_id == f, Event.code == code))
    if not event or not event.start_at:
        return None
    suitable = SUITABLE_VENUES.get(event.venue_requirements or "")
    venues = db.scalars(select(Venue).where(Venue.festival_id == f, Venue.maintenance_status == "operational", Venue.capacity >= registered,
                                            Venue.id != event.venue_id).order_by(Venue.capacity))
    for venue in venues:
        if suitable is not None and venue.name not in suitable:
            continue
        if not free or not (outside_venue_hours(venue, event.start_at, event.end_at)
                            or venue_conflicts(db, f, venue.id, event.start_at - TURNAROUND, event.end_at + TURNAROUND, event.id)):
            return venue
    return None


def alerts(db: Session, f: int, festival: Festival, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def add(severity: str, module: str, title: str, detail: str, source: str, action: dict[str, str] | None = None) -> None:
        found.append({"severity": severity, "module": module, "title": title, "detail": detail, "source": source, "action": action})

    pending = _rows(db, "SELECT simulation_id FROM change_approvals WHERE festival_id=:f AND status='pending' ORDER BY id", f=f)
    if pending:
        add("high", "Ripple", f"{len(pending)} {'change waits' if len(pending) == 1 else 'changes wait'} for approval", "Each was simulated and needs a decision before it is applied.", "generated",
            link("Review the change", "/ripple", "simulation.run", simulation=pending[0]["simulation_id"]))

    urgent = _one(db, "SELECT count(*) FROM incidents WHERE festival_id=:f AND status NOT IN ('resolved','closed') AND priority IN ('critical','high')", f=f)
    if urgent:
        add("critical", "Safety", f"{urgent} high-priority {'incident is' if urgent == 1 else 'incidents are'} open", "They need a response team and a named owner.", "simulated", link("Open incidents", "/safety", "incidents.read"))

    over = [e for e in events if e["capacity"] is not None and e["registered"] > e["capacity"]]
    if over:
        by_gap = sorted(over, key=lambda e: e["capacity"] - e["registered"])
        worst = by_gap[0]
        # ponytail: checks only the worst few to bound the queries here; Ripple can try any event.
        fix = next(((e, v) for e in by_gap[:FIXES_TRIED] if (v := roomier_venue(db, f, e["code"], e["registered"]))), None)
        if fix:
            detail = f" {fix[0]['name']} can move to {fix[1].name}, which seats {fix[1].capacity} and is free."
        else:
            fix = next(((e, v) for e in by_gap if (v := roomier_venue(db, f, e["code"], e["registered"], free=False))), (worst, None))
            detail = f" {fix[1].name} seats all of {fix[0]['name']} but is booked then, so simulate the clash." if fix[1] else " No suitable venue seats everyone."
        add("critical", "Venues", f"{len(over)} events exceed venue capacity",
            f"Worst is {worst['name']}: {worst['registered']} registered, {worst['capacity']} seats at {worst['venue']}.{detail}", "generated",
            simulate(f"Simulate moving {fix[0]['name']}", "venue_change", event_code=fix[0]["code"], venue_code=fix[1].code if fix[1] else None))

    clashes = _one(db, CLASHES, f=f)
    if clashes:
        add("high", "Events", f"{clashes} participants have overlapping events", "They are registered for events that run at the same time.", "generated",
            link("See the clashes", "/events", "events.read", tab="conflicts"))

    off_campus = _one(db, OFF_CAMPUS, f=f)
    if off_campus:
        worst_stay = _rows(db, WORST_OFF_CAMPUS, f=f)
        action = None
        if worst_stay:
            e = worst_stay[0]
            action = simulate(f"Try a new time for {e['name']}", "venue_change", event_code=e["code"], venue_code=e["venue_code"], start_at=to_form(e["start_at"]), end_at=to_form(e["end_at"]))
        add("critical", "Events", f"{off_campus:,} registrations fall outside the participant's stay",
            "They arrive after the event starts or leave before it ends, so they would miss all or part of it."
            + (f" {worst_stay[0]['name']} loses the most, {worst_stay[0]['away']:,}." if worst_stay else ""), "generated", action)

    double_booked = _one(db, VENUE_CLASHES, f=f)
    if double_booked:
        clash = _rows(db, WORST_VENUE_CLASH, f=f)[0]
        add("critical", "Venues", f"{double_booked} venue double-bookings", "Two events share a venue at the same time.", "generated",
            simulate(f"Move {clash['name']}", "venue_change", event_code=clash["code"]))

    unallocated, beds = _one(db, UNALLOCATED, f=f), _one(db, BEDS, f=f)
    occupied = _one(db, OCCUPIED, f=f)
    if unallocated:
        free = beds - occupied
        detail = f"{free:,} beds are free, so {unallocated - free:,} people have no bed even if every bed is used." if unallocated > free else f"{free:,} beds are free."
        add("critical" if unallocated > free else "high", "Accommodation", f"{unallocated:,} participants need a bed allocated", detail, "pack",
            link("Open the room map", "/accommodation", "accommodation.read"))

    accessible = _one(db, "SELECT coalesce(sum(capacity),0) FROM rooms WHERE festival_id=:f AND accessible IS TRUE", f=f)
    needing = _one(db, "SELECT count(*) FROM participants WHERE festival_id=:f AND deleted_at IS NULL AND accommodation_required IS TRUE AND accessibility_needs IS NOT NULL AND accessibility_needs<>'None'", f=f)
    if needing > accessible:
        add("critical", "Accommodation", f"{needing} accessibility needs, {accessible} accessible beds",
            "Accessibility is a hard constraint. Some needs may be met by ground-floor rooms, which needs a human decision.", "pack",
            link("See who needs a bed", "/accommodation", "accommodation.read", tab="unallocated"))

    expired = _one(db, "SELECT count(*) FROM vendors WHERE festival_id=:f AND hygiene_certificate_expiry < :d", f=f, d=festival.start_date)
    expiring = _one(db, "SELECT count(*) FROM vendors WHERE festival_id=:f AND hygiene_certificate_expiry BETWEEN :d AND :e", f=f, d=festival.start_date, e=festival.end_date)
    if expired or expiring:
        add("critical" if expired else "high", "Vendors", f"{expired + expiring} vendor hygiene certificates need action",
            f"{expired} already expired, {expiring} expire during the festival.", "pack", link("Open vendors", "/vendors", "dashboard.read"))

    vendors = list(db.scalars(select(Vendor).where(Vendor.festival_id == f)))
    uncovered = [c for c in coverage(vendors, diet_counts(db, f), festival) if c["people"] and not c["vendors"]]
    if uncovered:
        add("critical", "Vendors", f"{sum(c['people'] for c in uncovered):,} participants have no vendor for their diet",
            f"No compliant vendor lists: {', '.join(c['diet'] for c in uncovered)}.", "pack", link("See dietary coverage", "/vendors", "dashboard.read"))

    gaps = equipment_gaps(db, f)
    if gaps:
        add("high", "Venues", f"{len({g['code'] for g in gaps})} events are short of equipment", f"Worst shortage: {gaps[0]['item']} for {gaps[0]['name']}.", "pack",
            link("See equipment gaps", "/events", "events.read", tab="conflicts"))

    unready = unready_count(db, f)
    if unready:
        add("high", "Participants", f"{unready:,} participants are not ready",
            "Each has a conflict across their events, stay, bed or team that needs resolving before the festival.", "generated",
            link("Review who is not ready", "/participants", "participants.read", unready=1))

    duplicates = _one(db, "SELECT count(*) FROM data_issues WHERE festival_id=:f AND status='open' AND issue='possible_duplicate'", f=f)
    if duplicates:
        add("medium", "Participants", f"{duplicates} records look like duplicates", "Review each pair, then merge them or mark them as different people.", "pack",
            link("Review duplicates", "/participants", "participants.read_sensitive", duplicates=1))

    short = [e for e in events if e["staffed"] < e["volunteers_needed"]]
    if short:
        gap = sum(e["volunteers_needed"] - e["staffed"] for e in short)
        add("medium", "Volunteers", f"{len(short)} events are understaffed", f"{gap} volunteer shifts are unfilled.", "simulated",
            link("Find replacements", "/volunteers", "volunteers.read", tab="understaffed"))

    awaiting = _one(db, "SELECT count(*) FROM expenses WHERE festival_id=:f AND deleted_at IS NULL AND approval_status='pending'", f=f)
    if awaiting:
        add("medium", "Finance", f"{awaiting} expenses wait for approval", "Spending stays uncommitted until a decision is made.", "simulated",
            link("Review expenses", "/finance", "finance.read", tab="expenses", approval="pending"))

    delayed = _rows(db, """SELECT r.code, r.name, count(t.id) AS riders FROM routes r LEFT JOIN transport_assignments t ON t.route_id=r.id
                           WHERE r.festival_id=:f AND r.status='delayed' GROUP BY r.id""", f=f)
    for route in delayed:
        add("high", "Transport", f"{route['name']} is delayed", f"{route['riders']} passengers assigned.", "simulated",
            simulate("Simulate 30 more minutes", "bus_delay", route_code=route["code"], minutes=30))

    unknown = _one(db, "SELECT count(*) FROM data_issues WHERE festival_id=:f AND status='open' AND issue IN ('missing_gender','missing_dietary')", f=f)
    if unknown:
        add("medium", "Participants", f"{unknown} records have unknown gender or dietary needs", "Blanks are treated as unknown and need a person to confirm.", "pack",
            link("Review records", "/participants", "participants.read", flagged=1))

    order = {"critical": 0, "high": 1, "medium": 2}
    return sorted(found, key=lambda a: order[a["severity"]])


def charts(db: Session, f: int, events: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "arrivals": _rows(db, """
            SELECT to_char(date_trunc('day', arrival_at AT TIME ZONE 'Asia/Kolkata')
                           + floor(extract(hour FROM arrival_at AT TIME ZONE 'Asia/Kolkata') / 6) * interval '6 hours',
                           'DD Mon HH24:MI') AS label,
                   min(arrival_at) AS sort_key, count(*) AS count
            FROM participants WHERE festival_id=:f AND deleted_at IS NULL AND arrival_at IS NOT NULL
            GROUP BY 1 ORDER BY 2""", f=f),
        "event_participation": sorted(
            ({"code": e["code"], "name": e["name"], "registered": e["registered"], "capacity": e["capacity"]} for e in events),
            key=lambda e: -e["registered"]),
        "budget": [{k: float(v) if k != "department" else v for k, v in r.items()} for r in _rows(db, """
            SELECT d.name AS department, b.approved_amount AS approved,
                   coalesce((SELECT sum(x.actual_amount) FROM expenses x WHERE x.department_id=d.id AND x.deleted_at IS NULL AND x.approval_status='approved'),0) AS spent
            FROM budgets b JOIN departments d ON d.id=b.department_id WHERE b.festival_id=:f ORDER BY approved DESC""", f=f)],
        "occupancy": _rows(db, """
            SELECT h.name, h.kind, coalesce(sum(r.capacity),0) AS capacity,
                   count(a.id) AS occupied
            FROM hostels h JOIN rooms r ON r.hostel_id=h.id
            LEFT JOIN accommodation_allocations a ON a.room_id=r.id AND a.status<>'cancelled'
            WHERE h.festival_id=:f GROUP BY h.id ORDER BY h.kind DESC, h.code""", f=f),
        "volunteers": _rows(db, """
            SELECT d.name AS department, count(DISTINCT v.id) AS volunteers, count(s.id) AS shifts
            FROM volunteers v JOIN departments d ON d.id=v.department_id
            LEFT JOIN volunteer_shifts s ON s.volunteer_id=v.id
            WHERE v.festival_id=:f GROUP BY d.id ORDER BY volunteers DESC""", f=f),
        "event_status": _rows(db, "SELECT status, count(*) AS count FROM events WHERE festival_id=:f AND deleted_at IS NULL GROUP BY status ORDER BY count DESC", f=f),
        "tasks": _rows(db, """
            SELECT d.name AS department, count(*) AS total, count(*) FILTER (WHERE t.status='done') AS done,
                   count(*) FILTER (WHERE t.status='in_progress') AS in_progress
            FROM tasks t JOIN departments d ON d.id=t.department_id
            WHERE t.festival_id=:f GROUP BY d.id ORDER BY d.name""", f=f),
    }


def feed(db: Session, f: int) -> list[dict[str, Any]]:
    return _rows(db, """SELECT id, action, actor_label, summary, created_at FROM audit_logs
                        WHERE festival_id=:f AND action NOT IN ('auth.login', 'assistant.query') ORDER BY id DESC LIMIT 12""", f=f)


def dashboard(db: Session, festival: Festival, today: date) -> dict[str, Any]:
    f = festival.id
    events = _rows(db, EVENT_LOAD, f=f)
    return {
        "festival": {"id": f, "name": festival.name, "start_date": festival.start_date, "end_date": festival.end_date,
                     "phase": festival_phase(festival, today)},
        "kpis": kpis(db, f, events),
        "alerts": alerts(db, f, festival, events),
        "charts": charts(db, f, events),
        "feed": feed(db, f),
    }

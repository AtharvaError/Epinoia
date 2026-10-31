"""Read-only tools for the assistant. Every tool names its permission, and none can write.

Results never include participant names, contact details or accessibility details.
"""
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.analytics.dashboard import dashboard
from app.api.common import like
from app.api.transport import affected_events
from app.api.vendors import build_intelligence, day_label
from app.models import AccommodationAllocation, Contingent, Department, Event, Festival, Incident, Participant, Registration, Resource, Route, Task, Venue, Volunteer, VolunteerShift
from app.services.events import all_conflicts
from app.services.finance import department_lines
from app.services.housing import hospitality_budget, occupied_counts, open_rooms, unallocated_query
from app.services.pack_import import IST  # noqa: F401  (re-exported for the agent)
from app.simulation.engine import build_report
from app.simulation.scenarios import SCENARIOS, VenueChange


@dataclass
class Context:
    db: Session
    festival: Festival
    permissions: set[str]


@dataclass(frozen=True)
class Tool:
    name: str
    label: str
    description: str
    permission: str
    properties: dict[str, Any]
    run: Callable[[Context, dict[str, Any]], Any]

    def definition(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "strict": True,
                "input_schema": {"type": "object", "properties": self.properties, "required": list(self.properties), "additionalProperties": False}}


def festival_overview(ctx: Context, _: dict[str, Any]) -> dict[str, Any]:
    data = dashboard(ctx.db, ctx.festival, date.today())
    return {"phase": data["festival"]["phase"], "figures": {k["label"]: k["value"] for k in data["kpis"]},
            "alerts": [{"severity": a["severity"], "title": a["title"], "source": a["source"]} for a in data["alerts"]]}


def accommodation_status(ctx: Context, _: dict[str, Any]) -> dict[str, Any]:
    fid = ctx.festival.id
    waiting = ctx.db.scalar(select(func.count()).select_from(unallocated_query(fid).subquery())) or 0
    return {"participants_without_a_bed": waiting, "free_beds": sum(r.free for r in open_rooms(ctx.db, fid)), "beds_occupied": sum(occupied_counts(ctx.db, fid).values()),
            "hospitality_budget": hospitality_budget(ctx.db, fid)}


def event_conflicts(ctx: Context, _: dict[str, Any]) -> dict[str, Any]:
    data = all_conflicts(ctx.db, ctx.festival.id)
    return {"venue_double_bookings": data["venue"], "events_over_capacity_count": len(data["capacity"]), "worst_over_capacity": data["capacity"][:8],
            "clashing_event_pairs_count": len(data["participants"]), "worst_participant_clashes": data["participants"][:8]}


def find_event(ctx: Context, args: dict[str, Any]) -> list[dict[str, Any]]:
    term = like(args["query"])
    events = ctx.db.scalars(select(Event).where(Event.festival_id == ctx.festival.id, Event.deleted_at.is_(None), or_(Event.name.ilike(term, escape="\\"), Event.code.ilike(term, escape="\\"))).limit(5))
    return [{"code": e.code, "name": e.name, "status": e.status, "starts": e.start_at, "ends": e.end_at, "venue": e.venue.name if e.venue else None,
             "venue_capacity": e.venue.capacity if e.venue else None, "registered": ctx.db.scalar(select(func.count()).select_from(Registration).where(Registration.event_id == e.id))} for e in events]


def available_volunteers(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    day = date.fromisoformat(args["day"])
    fid = ctx.festival.id
    busy = select(VolunteerShift.volunteer_id).where(VolunteerShift.festival_id == fid, VolunteerShift.status != "cancelled", func.date(func.timezone(ctx.festival.timezone, VolunteerShift.start_at)) == day)
    rows = ctx.db.execute(select(Department.name, func.count()).join(Volunteer, Volunteer.department_id == Department.id)
                          .where(Volunteer.festival_id == fid, Volunteer.status == "active", Volunteer.id.not_in(busy)).group_by(Department.name).order_by(func.count().desc())).all()
    return {"day": day, "with_no_shift_that_day": sum(n for _, n in rows), "by_department": {name: n for name, n in rows}}


def transport_risks(ctx: Context, args: dict[str, Any]) -> list[dict[str, Any]]:
    query = select(Route).where(Route.festival_id == ctx.festival.id)
    query = query.where(Route.code == args["route_code"]) if args.get("route_code") else query.where(Route.status == "delayed")
    return [{"trip": r.code, "name": r.name, "status": r.status, "delay_minutes": r.delay_minutes, "expected_arrival": r.expected_arrival_at,
             "events_at_risk": affected_events(ctx.db, ctx.festival.id, r, r.expected_arrival_at)} for r in ctx.db.scalars(query) if r.expected_arrival_at]


def budget_status(ctx: Context, _: dict[str, Any]) -> dict[str, Any]:
    lines = department_lines(ctx.db, ctx.festival.id)
    return {"approved": sum(x["approved"] for x in lines), "committed": sum(x["committed"] for x in lines), "pending_approval": sum(x["pending"] for x in lines),
            "remaining": sum(x["remaining"] for x in lines), "by_department": lines, "note": "Budgets and expenses are simulated demo data."}


def pending_tasks(ctx: Context, _: dict[str, Any]) -> list[dict[str, Any]]:
    rows = ctx.db.execute(select(Department.name, func.count(), func.count().filter(Task.status == "in_progress")).join(Task, Task.department_id == Department.id)
                          .where(Task.festival_id == ctx.festival.id, Task.status != "done").group_by(Department.name).order_by(func.count().desc())).all()
    return [{"department": name, "pending": pending, "in_progress": active} for name, pending, active in rows]


def open_incidents(ctx: Context, _: dict[str, Any]) -> list[dict[str, Any]]:
    rows = ctx.db.scalars(select(Incident).where(Incident.festival_id == ctx.festival.id, Incident.status.not_in(("resolved", "closed"))).order_by(Incident.reported_at.desc()))
    return [{"code": i.code, "category": i.category, "location": i.location, "priority": i.priority, "status": i.status, "assigned_team": i.assigned_team} for i in rows]


def food_status(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    data = build_intelligence(ctx.db, ctx.festival)
    names = {v["code"]: v["name"] for v in data["vendors"]}
    wanted = (args.get("diet") or "").lower()
    diets = [{"diet": r["label"], "people": r["participant_count"],
              "days": [{"day": day_label(d["day"]), "state": d["state"], "vendors": [names[c] for c in d["clear"] + d["boundary"]]} for d in r["days"]]}
             for r in data["dietary_intelligence"]["coverage_matrix"] if not wanted or wanted == r["key"]]
    return {"overview": {k: data["overview"][k] for k in ("total_vendors", "confirmed_active_count", "unconfirmed_count", "interrupted_count", "certificates_expired", "dietary_coverage_gaps")},
            "diets": diets, "fix_first": [{"action": f["action"], "why": f["why"]} for f in data["fix_first"][:3]]}


def venue_status(ctx: Context, _: dict[str, Any]) -> dict[str, Any]:
    fid = ctx.festival.id
    venues = ctx.db.scalars(select(Venue).where(Venue.festival_id == fid, Venue.maintenance_status != "operational"))
    stock = ctx.db.scalars(select(Resource).where(Resource.festival_id == fid, Resource.kind == "logistics", or_(Resource.condition != "good", Resource.condition.is_(None), Resource.reserved_quantity >= Resource.quantity)))
    return {"venues_not_operational": [{"name": v.name, "status": v.maintenance_status} for v in venues],
            "equipment_needing_attention": [{"name": r.name, "condition": r.condition or "unknown", "quantity": r.quantity, "reserved": r.reserved_quantity, "location": r.location} for r in stock]}


def arrival_status(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    """Counts only: contingent leaders, escorts and individual participants stay in the portal."""
    query = (select(Contingent.college, Contingent.strength_declared, func.count(Participant.id), func.count(AccommodationAllocation.check_in_at))
             .outerjoin(Participant, and_(Participant.contingent_id == Contingent.id, Participant.deleted_at.is_(None)))
             .outerjoin(AccommodationAllocation, and_(AccommodationAllocation.participant_id == Participant.id, AccommodationAllocation.status != "cancelled"))
             .where(Contingent.festival_id == ctx.festival.id).group_by(Contingent.id).order_by(Contingent.college))
    rows = ctx.db.execute(query).all()
    term = (args.get("query") or "").lower()
    return {"contingents": len(rows), "participants": sum(r[2] for r in rows), "checked_in_to_rooms": sum(r[3] for r in rows), "searched_for": term or None,
            "matches": [{"college": c, "declared": declared, "registered": people, "checked_in_to_rooms": arrived} for c, declared, people, arrived in rows if term and term in c.lower()][:5]}


def predict_venue_move(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    """Runs the simulator's read-only calculation. Stores nothing and changes nothing."""
    event = ctx.db.scalar(select(Event).where(Event.festival_id == ctx.festival.id, Event.deleted_at.is_(None), Event.name.ilike(like(args["event"]), escape="\\")))
    venue = ctx.db.scalar(select(Venue).where(Venue.festival_id == ctx.festival.id, Venue.name.ilike(like(args["venue"]), escape="\\")))
    if not event or not venue:
        return {"error": f"Could not find {'that event' if not event else 'that venue'}."}
    report = build_report("venue_change", SCENARIOS["venue_change"].simulate(ctx.db, ctx.festival, VenueChange(event_code=event.code, venue_code=venue.code)))
    return {"prediction": report["headline"], "before_after": report["before_after"],
            "impacts": [{k: i[k] for k in ("severity", "certainty", "explanation")} for i in report["impacts"][:6]],
            "to_apply": "Run this in the Ripple Simulator, where an approver can review and apply it."}


NOTHING: dict[str, Any] = {}
NULLABLE_TEXT = {"type": ["string", "null"]}
TOOLS = (
    Tool("festival_overview", "Festival overview", "Current headline figures and the priority alerts.", "dashboard.read", NOTHING, festival_overview),
    Tool("accommodation_status", "Accommodation", "How many participants have no bed, free beds, and occupancy.", "accommodation.read", NOTHING, accommodation_status),
    Tool("event_conflicts", "Event conflicts", "Venue double-bookings, events over capacity, and participant clashes.", "events.read", NOTHING, event_conflicts),
    Tool("find_event", "Event lookup", "Look up an event by name or code.", "events.read", {"query": {"type": "string", "description": "Event name or code"}}, find_event),
    Tool("available_volunteers", "Volunteer availability", "Active volunteers with no shift on a day.", "volunteers.read", {"day": {"type": "string", "description": "Date as YYYY-MM-DD"}}, available_volunteers),
    Tool("transport_risks", "Transport", "Delayed trips and the events their passengers may miss. Pass a trip code, or null for all delayed trips.", "transport.read", {"route_code": NULLABLE_TEXT}, transport_risks),
    Tool("budget_status", "Budget", "Approved, committed, pending and remaining budget by department.", "finance.read", NOTHING, budget_status),
    Tool("pending_tasks", "Pending tasks", "Unfinished tasks by department.", "volunteers.read", NOTHING, pending_tasks),
    Tool("open_incidents", "Incidents", "Incidents that are not resolved yet.", "incidents.read", NOTHING, open_incidents),
    Tool("food_status", "Food and vendors", "Vendor availability, certificates, and which diets are covered each day. Pass a diet key, or null for all.", "vendors.read",
         {"diet": {"type": ["string", "null"], "enum": ["nut-free", "jain", "vegan", "gluten-free", "vegetarian", "non-vegetarian", None]}}, food_status),
    Tool("venue_status", "Venues and equipment", "Venues under maintenance or closed, and equipment that is damaged or fully reserved.", "venues.read", NOTHING, venue_status),
    Tool("arrival_status", "Arrivals", "Contingent and participant counts, and how many have checked in to rooms. Pass a college name to look one up, or null for totals.", "participants.read", {"query": NULLABLE_TEXT}, arrival_status),
    Tool("predict_venue_move", "Venue move prediction", "Predict what moving an event to another venue would affect. Changes nothing.", "simulation.run",
         {"event": {"type": "string", "description": "Event name"}, "venue": {"type": "string", "description": "Venue name"}}, predict_venue_move),
)
BY_NAME = {t.name: t for t in TOOLS}


def tools_for(permissions: set[str]) -> list[Tool]:
    return [t for t in TOOLS if t.permission in permissions]


def execute(ctx: Context, name: str, args: dict[str, Any]) -> Any:
    """Permissions are checked again here, so a tool outside the role's list still cannot run."""
    tool = BY_NAME.get(name)
    if not tool:
        return {"error": f"Unknown tool {name}"}
    if tool.permission not in ctx.permissions:
        return {"error": "Your role does not have access to this information."}
    try:
        return tool.run(ctx, args)
    except (ValueError, KeyError, TypeError) as error:
        return {"error": f"That request could not be read: {error}"}
    except HTTPException as error:
        return {"error": str(error.detail)}

"""The five scenarios. Each reads current state and returns an Outcome, writing nothing.

An Outcome's plan holds exactly what apply() will do. Apply functions change production
rows and only ever run inside the approval transaction in engine.py.
"""
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from math import ceil
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.common import by_code
from app.api.transport import affected_events, rider_counts
from app.api.volunteers import EVENT_SKILLS, event_staffing, shift_counts
from app.models import (
    AccommodationAllocation, Budget, Department, Event, Expense, Festival, Hostel, Participant, Registration, Resource, Room, Route,
    Sponsor, TransportAssignment, User, Vehicle, Venue, Volunteer, VolunteerShift,
)
from app.services.allocator import plan_allocation
from app.services.events import outside_venue_hours, venue_conflicts
from app.services.housing import (
    ACTIVE, contingent_codes, external_commitment, hospitality_budget, occupied_counts, open_rooms, to_person, unallocated_query,
)
from app.services.pack_import import IST
from app.services.schedule_plan import SUITABLE_VENUES, TURNAROUND
from app.simulation.graph import Hop, Impact, Rules

PEOPLE_PER_HELPER = 40
TIGHT_GAP = timedelta(minutes=30)
AVERAGE_SPEED_KMH = 25
EARLY_HOUR, LATE_HOUR = 9, 21


class SimulationError(HTTPException):
    def __init__(self, message: str):
        super().__init__(422, message)


@dataclass
class Outcome:
    title: str
    headline: str
    seed_module: str
    seed_records: set[Any]
    seed_impacts: list[Impact]
    rules: Rules
    current_state: list[dict[str, Any]]
    before_after: list[dict[str, Any]]
    actions: list[dict[str, Any]] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    resources: list[str] = field(default_factory=list)
    scheduling: list[str] = field(default_factory=list)
    budget_impact: float = 0.0
    needs_reason: str | None = None
    plan: dict[str, Any] = field(default_factory=dict)


def inr(value: Decimal | float | int) -> str:
    digits = f"{abs(int(round(value)))}"
    head, tail = digits[:-3], digits[-3:]
    groups = []
    while head:
        groups.insert(0, head[-2:])
        head = head[:-2]
    grouped = ",".join([*groups, tail]) if groups else tail
    return f"{'-' if value < 0 else ''}₹{grouped}"


def ist(moment: datetime) -> str:
    return moment.astimezone(IST).strftime("%d %b, %H:%M")


def stat(label: str, value: Any) -> dict[str, Any]:
    return {"label": label, "value": value}


def change_row(metric: str, before: Any, after: Any, certainty: str = "fact") -> dict[str, Any]:
    return {"metric": metric, "before": before, "after": after, "certainty": certainty}


def action(title: str, detail: str, module: str, suggest: dict[str, Any] | None = None) -> dict[str, Any]:
    """suggest holds change fields the interface can fill in to try this action in a new simulation."""
    return {"title": title, "detail": detail, "module": module, "suggest": suggest}


def plural(count: int, word: str) -> str:
    return f"{count:,} {word}{'' if count == 1 else 's'}"


# --- Scenario A: a hostel becomes unavailable ---------------------------------------------

class HostelChange(BaseModel):
    hostel_code: str
    extra_budget: Decimal = Field(default=Decimal(0), ge=0, le=100_000_000)


def hostel_closure(db: Session, festival: Festival, change: HostelChange) -> Outcome:
    fid = festival.id
    hostel = by_code(db, Hostel, fid, change.hostel_code)
    if hostel.status != "available":
        raise SimulationError(f"{hostel.name} is already {hostel.status}")
    room_ids = select(Room.id).where(Room.hostel_id == hostel.id)
    stays = db.execute(select(AccommodationAllocation, Participant).join(Participant, Participant.id == AccommodationAllocation.participant_id)
                       .where(AccommodationAllocation.room_id.in_(room_ids), ACTIVE).order_by(Participant.code)).all()
    contingents = contingent_codes(db, fid)
    people = [to_person(p, contingents) for _, p in stays]
    displaced = {p.id for p in people}

    capacity = db.scalar(select(func.coalesce(func.sum(Room.capacity), 0)).where(Room.hostel_id == hostel.id, Room.status == "available"))
    others_cost = external_commitment(db, fid, exclude=displaced)
    old_cost = external_commitment(db, fid) - others_cost
    budget = hospitality_budget(db, fid)
    allowance = None if budget is None else max(Decimal(0), budget + change.extra_budget - others_cost)
    free = open_rooms(db, fid, exclude_hostel_id=hostel.id)
    plan = plan_allocation(people, free, allowance)
    full_cost = plan_allocation(people, free, None).external_cost
    needed_extra = max(Decimal(0), others_cost + full_cost - (budget or Decimal(0)))

    held_reasons = Counter(h["reason"].split(":")[-1].strip() for h in plan.held)
    access_held = sum(1 for h in plan.held if "ccessible" in h["reason"])
    moved_external = [a for a in plan.assignments if a["kind"] == "external"]
    minors_out = sum("minor_off_campus" in a["relaxed"] for a in plan.assignments)
    extra_cost = plan.external_cost - old_cost
    waiting = db.scalar(select(func.count()).select_from(unallocated_query(fid).subquery())) or 0
    free_beds = sum(r.free for r in free)

    def to_people(_: set[Any]) -> Hop:
        if not people:
            return Hop()
        impacts = [Impact("participants", "critical" if plan.held else "high", "fact", f"{plural(len(people), 'participant')} lose their beds in {hostel.name}.",
                          f"Move {len(plan.assignments):,} to the rooms in this plan.", hostel.code)]
        if plan.held:
            detail = "; ".join(f"{n:,}: {r}" for r, n in held_reasons.most_common())
            impacts.append(Impact("participants", "critical", "fact", f"{plural(len(plan.held), 'participant')} cannot be rehoused under the rules. {detail}.",
                                  "Add budget or arrange these people individually." if needed_extra > change.extra_budget else "Arrange these people individually."))
        if access_held:
            impacts.append(Impact("participants", "critical", "fact", f"{access_held:,} of them have accessibility needs that no remaining room meets.",
                                  "Assign a named coordinator to arrange each of these personally."))
        if minors_out:
            impacts.append(Impact("participants", "high", "fact", f"{plural(minors_out, 'participant')} under 18 would move off campus.",
                                  "Confirm supervision with their escorts before moving them."))
        return Hop({p.code for p in people}, impacts)

    def to_finance(_: set[Any]) -> Hop:
        if not extra_cost and not change.extra_budget and not needed_extra:
            return Hop()
        impacts = []
        if extra_cost:
            impacts.append(Impact("finance", "high" if extra_cost > 0 else "low", "fact", f"External stay cost changes by {inr(extra_cost)} at listed bed rates.", record_ref="Hospitality"))
        if needed_extra > change.extra_budget:
            impacts.append(Impact("finance", "critical", "fact", f"Rehousing everyone the rules allow needs {inr(needed_extra)} more than the approved budget.",
                                  f"Add {inr(needed_extra)} to the External budget field and run the simulation again.", "Hospitality"))
        return Hop({"Hospitality"}, impacts)

    def to_transport(_: set[Any]) -> Hop:
        by_property = Counter(a["hostel"] for a in moved_external)
        if not by_property:
            return Hop()
        routes = db.execute(select(Route.pickup, Route.code).where(Route.festival_id == fid, Route.pickup.in_(by_property))).all()
        served = defaultdict(list)
        for pickup, code in routes:
            served[pickup].append(code)
        impacts = []
        for name, count in by_property.most_common():
            if served[name]:
                impacts.append(Impact("transportation", "medium", "fact", f"{plural(count, 'participant')} move to {name}, served by {plural(len(served[name]), 'trip')}.",
                                      f"Check seats on {', '.join(sorted(served[name]))}.", name))
            else:
                impacts.append(Impact("transportation", "high", "fact", f"{plural(count, 'participant')} move to {name}, and no trip serves it.",
                                      f"Add a shuttle from {name} to campus.", name))
        return Hop(set(by_property), impacts)

    def to_events(_: set[Any]) -> Hop:
        if not moved_external:
            return Hop()
        rows = db.execute(select(Event.code, Event.name, Event.start_at, Event.end_at, func.count()).join(Registration, Registration.event_id == Event.id)
                          .where(Registration.participant_id.in_([a["participant_id"] for a in moved_external]), Event.deleted_at.is_(None), Event.start_at.is_not(None))
                          .group_by(Event.id).order_by(Event.start_at)).all()
        risky = [r for r in rows if r.start_at.astimezone(IST).hour < EARLY_HOUR or r.end_at.astimezone(IST).hour >= LATE_HOUR]
        impacts = [Impact("events", "medium", "estimate", f"{plural(count, 'moved participant')} are registered for {name} at {ist(start)}, an early or late slot for someone staying off campus.",
                          "Add an early or late shuttle for this slot.", code) for code, name, start, _, count in risky[:6]]
        return Hop({r[0] for r in risky}, impacts)

    def to_volunteers(_: set[Any]) -> Hop:
        if not people:
            return Hop()
        helpers = ceil(len(people) / PEOPLE_PER_HELPER)
        team = sorted(db.scalars(select(Volunteer.code).join(Department, Department.id == Volunteer.department_id)
                                 .where(Volunteer.festival_id == fid, Volunteer.status == "active", Department.name == "Hospitality")))
        short = helpers > len(team)
        return Hop(set(team[:helpers]), [Impact("volunteers", "high" if short else "low", "estimate", f"Moving {len(people):,} people needs about {plural(helpers, 'helper')}. Hospitality has {len(team)} active.",
                                                "Borrow volunteers from another department." if short else "Brief Hospitality volunteers on the move.")])

    rules: Rules = {("accommodation", "participants"): to_people, ("accommodation", "finance"): to_finance, ("participants", "transportation"): to_transport,
                    ("participants", "events"): to_events, ("participants", "volunteers"): to_volunteers}

    actions = []
    if plan.assignments:
        actions.append(action(f"Move {len(plan.assignments):,} participants", f"{sum(a['kind'] == 'on_campus' for a in plan.assignments):,} on campus and {len(moved_external):,} to external stays.", "accommodation"))
    if needed_extra > change.extra_budget:
        actions.append(action(f"Add {inr(needed_extra)} to the external budget", "This places everyone who is held only because of budget.", "finance", {"extra_budget": float(needed_extra)}))
    if plan.held:
        actions.append(action(f"Resolve {len(plan.held):,} held participants", "They stay on the unallocated list with their reason until someone decides.", "participants"))
    if moved_external:
        actions.append(action("Review shuttles", "Confirm a trip serves every property people are moving to.", "transportation"))

    return Outcome(
        title=f"{hostel.name} becomes unavailable",
        headline=f"{plural(len(people), 'participant')} lose beds. {len(plan.assignments):,} can be rehoused and {len(plan.held):,} cannot.",
        seed_module="accommodation", seed_records={hostel.code},
        seed_impacts=[Impact("accommodation", "critical", "fact", f"{hostel.name} closes, removing {plural(capacity, 'bed')}.", record_ref=hostel.code)],
        rules=rules,
        current_state=[stat("Beds in this hostel", capacity), stat("Occupied", len(people)), stat("Free beds elsewhere", free_beds),
                       stat("Already waiting for a bed", waiting), stat("Hospitality budget", inr(budget or 0)), stat("External stays committed", inr(others_cost + old_cost))],
        before_after=[change_row("People without a bed", waiting, waiting + len(plan.held)), change_row("Free beds", free_beds + capacity - len(people), free_beds - len(plan.assignments)),
                      change_row("External stay cost", inr(others_cost + old_cost), inr(others_cost + plan.external_cost)),
                      change_row("Hospitality budget", inr(budget or 0), inr((budget or 0) + change.extra_budget))],
        actions=actions,
        assumptions=["Costs use listed bed rates and each person's stay nights.", f"About {PEOPLE_PER_HELPER} people to each moving helper.",
                     f"Slots before {EARLY_HOUR}:00 or ending after {LATE_HOUR}:00 count as hard for people staying off campus."],
        resources=[f"{plural(ceil(len(people) / PEOPLE_PER_HELPER), 'moving helper')}"] if people else [],
        scheduling=[], budget_impact=float(extra_cost),
        needs_reason="People would be left without a bed." if plan.held else ("This adds to the approved budget." if change.extra_budget else None),
        plan={"hostel_id": hostel.id, "cancel": sorted(a.id for a, _ in stays), "extra_budget": str(change.extra_budget),
              "moves": [{"participant_id": a["participant_id"], "room_id": a["room_id"], "relaxed": a["relaxed"]} for a in plan.assignments]},
    )


def apply_hostel_closure(db: Session, festival: Festival, plan: dict[str, Any]) -> str:
    hostel = db.get(Hostel, plan["hostel_id"])
    hostel.status = "closed"
    for allocation in db.scalars(select(AccommodationAllocation).where(AccommodationAllocation.id.in_(plan["cancel"]))):
        allocation.status = "cancelled"
    db.flush()  # The one-active-allocation index needs the cancellations in place first.
    db.add_all(AccommodationAllocation(festival_id=festival.id, participant_id=m["participant_id"], room_id=m["room_id"], relaxed_constraints=m["relaxed"]) for m in plan["moves"])
    extra = Decimal(plan["extra_budget"])
    if extra:
        budget = db.scalar(select(Budget).join(Department, Department.id == Budget.department_id).where(Budget.festival_id == festival.id, Department.name == "Hospitality"))
        budget.approved_amount += extra
    return f"{hostel.name} closed; {len(plan['moves']):,} moved"


# --- Scenario B: an event changes venue ----------------------------------------------------

class VenueChange(BaseModel):
    event_code: str
    venue_code: str
    start_at: datetime | None = None
    end_at: datetime | None = None

    @model_validator(mode="after")
    def times_make_sense(self):
        if bool(self.start_at) != bool(self.end_at):
            raise ValueError("Give both a new start and a new end, or neither")
        if self.start_at and self.end_at <= self.start_at:
            raise ValueError("End must be after start")
        return self


def equipment_needs(event: Event) -> list[tuple[str, int]]:
    needs = []
    for part in (event.equipment_needs or "").split(";"):
        match = re.fullmatch(r"(?:(\d+)\s+)?(.+?)s?", part.strip())
        if match and "no special" not in part:
            needs.append((match[2], int(match[1] or 1)))
    return needs


def usable_stock(db: Session, festival_id: int, item: str) -> tuple[int, int]:
    """Good, unreserved quantity of an item, and its total in any condition."""
    rows = db.execute(select(Resource.quantity, Resource.reserved_quantity).where(Resource.festival_id == festival_id, Resource.kind == "logistics", Resource.name == item, Resource.condition == "good")).all()
    total = db.scalar(select(func.coalesce(func.sum(Resource.quantity), 0)).where(Resource.festival_id == festival_id, Resource.kind == "logistics", Resource.name == item)) or 0
    return sum(q - r for q, r in rows), total


def people_missing(rows: list[Any], start: datetime, end: datetime) -> tuple[set[int], set[int], set[int]]:
    """Registrants with a clash, a stay that misses the event, and a tight transfer."""
    clash, away, tight = set(), set(), set()
    for pid, arrival, departure, other_start, other_end in rows:
        if arrival and departure and departure >= arrival and (arrival > start or departure < end):
            away.add(pid)
        if other_start and other_start < end and other_end > start:
            clash.add(pid)
        elif other_start and (timedelta(0) <= start - other_end < TIGHT_GAP or timedelta(0) <= other_start - end < TIGHT_GAP):
            tight.add(pid)
    return clash, away, tight


def venue_change(db: Session, festival: Festival, change: VenueChange) -> Outcome:
    fid = festival.id
    event = by_code(db, Event, fid, change.event_code)
    target = by_code(db, Venue, fid, change.venue_code)
    start, end = change.start_at or event.start_at, change.end_at or event.end_at
    if not start or not end:
        raise SimulationError(f"{event.name} has no schedule yet. Give a new start and end.")
    moved_time = (start, end) != (event.start_at, event.end_at)
    if target.id == event.venue_id and not moved_time:
        raise SimulationError(f"{event.name} is already at {target.name} at that time")

    old = db.get(Venue, event.venue_id) if event.venue_id else None
    registered = db.scalar(select(func.count()).select_from(Registration).where(Registration.event_id == event.id)) or 0
    seats_before = old.capacity if old else 0
    conflicts = venue_conflicts(db, fid, target.id, start - TURNAROUND, end + TURNAROUND, event.id)
    outside = outside_venue_hours(target, start, end)
    suitable = target.name in SUITABLE_VENUES.get(event.venue_requirements or "", {target.name})
    other_event = Event.__table__.alias("other")
    rows = db.execute(
        select(Participant.id, Participant.arrival_at, Participant.departure_at, other_event.c.start_at, other_event.c.end_at)
        .select_from(Registration).join(Participant, Participant.id == Registration.participant_id)
        .outerjoin(Registration.__table__.alias("other_reg"), text("other_reg.participant_id = registrations.participant_id AND other_reg.event_id <> registrations.event_id"))
        .outerjoin(other_event, text("other.id = other_reg.event_id AND other.deleted_at IS NULL"))
        .where(Registration.event_id == event.id)).all()
    clash_before, away_before, _ = people_missing(rows, event.start_at, event.end_at) if event.start_at else (set(), set(), set())
    clash_after, away_after, tight = people_missing(rows, start, end)
    registrants = {r[0] for r in rows}

    shifts = list(db.scalars(select(VolunteerShift).where(VolunteerShift.event_id == event.id, VolunteerShift.status != "cancelled")))
    delta = start - event.start_at if event.start_at else timedelta(0)
    volunteer_clashes = []
    if moved_time:
        for shift in shifts:
            overlap = db.scalar(select(func.count()).select_from(VolunteerShift).where(
                VolunteerShift.volunteer_id == shift.volunteer_id, VolunteerShift.id != shift.id, VolunteerShift.status != "cancelled",
                VolunteerShift.start_at < shift.end_at + delta, VolunteerShift.end_at > shift.start_at + delta))
            if overlap:
                volunteer_clashes.append(shift.volunteer_id)

    def to_venue(_: set[Any]) -> Hop:
        impacts = []
        short = registered - target.capacity
        if short > 0:
            impacts.append(Impact("venues", "critical", "fact", f"{target.name} seats {target.capacity}, and {registered} are registered: {short} short.", "Split the event into sessions or choose a larger venue.", target.code))
        else:
            impacts.append(Impact("venues", "low", "fact", f"{target.name} seats all {registered} registrants, with {-short} to spare.", record_ref=target.code))
        if outside:
            impacts.append(Impact("venues", "critical", "fact", f"That time is outside {target.name}'s available hours.", "Choose a time inside the venue's hours.", target.code))
        if target.maintenance_status != "operational":
            impacts.append(Impact("venues", "critical", "fact", f"{target.name} is marked {target.maintenance_status}.", "Clear the maintenance status first.", target.code))
        if not suitable:
            impacts.append(Impact("venues", "high", "estimate", f"{event.name} needs {event.venue_requirements}, which {target.name} may not provide.", "Confirm the facilities on site.", target.code))
        for item, needed in equipment_needs(event):
            usable, total = usable_stock(db, fid, item)
            if usable < needed:
                impacts.append(Impact("venues", "high", "fact", f"{event.name} needs {needed} {item}, and {usable} of {total} in stock are usable.", f"Repair or hire {item} stock.", item))
        impacts.append(Impact("venues", "low", "estimate", "The data has no distances between venues, so moving kit and people cannot be timed exactly.", "Allow setup time when briefing the team."))
        return Hop({target.code}, impacts)

    def to_other_events(_: set[Any]) -> Hop:
        impacts = [Impact("events", "critical", "fact", f"{c.name} is booked at {target.name} from {ist(c.start_at)} to {ist(c.end_at)}, including 30 minutes of turnaround.",
                          f"Move {c.name} or choose another time.", c.code) for c in conflicts]
        return Hop({c.code for c in conflicts}, impacts)

    def to_participants(_: set[Any]) -> Hop:
        impacts = [Impact("participants", "low", "fact", f"{plural(len(registrants), 'registrant')} must be told about this change.", "Send an announcement when this is applied.", event.code)]
        new_clashes, new_away = clash_after - clash_before, away_after - away_before
        if new_clashes:
            impacts.append(Impact("participants", "high", "fact", f"{plural(len(new_clashes), 'participant')} gain a clash with another event.", "Choose a time with fewer clashes.", event.code))
        if new_away:
            impacts.append(Impact("participants", "high", "fact", f"{plural(len(new_away), 'participant')} would no longer be on campus for the whole event.", "Choose a time inside more people's stays.", event.code))
        if tight:
            impacts.append(Impact("participants", "medium", "estimate", f"{plural(len(tight), 'participant')} have under 30 minutes between this and another event.", "Tell them which venue comes next.", event.code))
        return Hop(registrants, impacts)

    def to_volunteers(_: set[Any]) -> Hop:
        if not shifts:
            return Hop()
        impacts = [Impact("volunteers", "low", "fact", f"{plural(len(shifts), 'volunteer')} must be briefed on the new {'time and venue' if moved_time else 'venue'}.", record_ref=event.code)]
        if volunteer_clashes:
            impacts.append(Impact("volunteers", "high", "fact", f"{plural(len(volunteer_clashes), 'volunteer')} already have another shift at the new time.", "Use Find replacements for this event.", event.code))
        return Hop({s.volunteer_id for s in shifts}, impacts)

    alternatives = []
    if conflicts or registered > target.capacity:
        for v in db.scalars(select(Venue).where(Venue.festival_id == fid, Venue.id != target.id, Venue.id != event.venue_id, Venue.maintenance_status == "operational").order_by(Venue.capacity.desc())):
            fits = v.name in SUITABLE_VENUES.get(event.venue_requirements or "", {v.name})
            if fits and not outside_venue_hours(v, start, end) and not venue_conflicts(db, fid, v.id, start - TURNAROUND, end + TURNAROUND, event.id):
                alternatives.append(v)

    actions = [action(f"Try {v.name}", f"Free at this time and seats {v.capacity}.", "venues", {"venue_code": v.code}) for v in alternatives[:3]]
    actions.append(action("Brief everyone involved", f"{plural(len(registrants), 'registrant')} and {plural(len(shifts), 'volunteer')} need the new details.", "participants"))
    problems = [p for p, on in (("the venue is double-booked", conflicts), ("the time is outside venue hours", outside), (f"the venue is {target.maintenance_status}", target.maintenance_status != "operational")) if on]
    return Outcome(
        title=f"Move {event.name} to {target.name}",
        headline=f"{plural(len(registrants), 'registrant')} and {plural(len(shifts), 'volunteer')} are affected. {plural(len(conflicts), 'booking conflict')}.",
        seed_module="events", seed_records={event.code},
        seed_impacts=[Impact("events", "medium", "fact", f"{event.name} moves from {old.name if old else 'no venue'} to {target.name}" + (f", now starting {ist(start)}." if moved_time else "."), record_ref=event.code)],
        rules={("events", "venues"): to_venue, ("venues", "events"): to_other_events, ("events", "participants"): to_participants, ("events", "volunteers"): to_volunteers},
        current_state=[stat("Current venue", old.name if old else "None"), stat("Seats", seats_before), stat("Registered", registered),
                       stat("Starts", ist(event.start_at) if event.start_at else "Not set"), stat("Volunteers", len(shifts)), stat("Needs", event.venue_requirements or "Not stated")],
        before_after=[change_row("Venue", old.name if old else "None", target.name), change_row("Seats short", max(0, registered - seats_before), max(0, registered - target.capacity)),
                      change_row("Starts", ist(event.start_at) if event.start_at else "Not set", ist(start)), change_row("Registrants with a clash", len(clash_before), len(clash_after)),
                      change_row("Registrants outside their stay", len(away_before), len(away_after))],
        actions=actions,
        assumptions=["30 minutes of turnaround between events at a venue.", "Venue suitability is inferred from the assumptions log.", "A gap under 30 minutes between events is treated as tight."],
        resources=[f"{need} {item}" for item, need in equipment_needs(event)],
        scheduling=[f"{event.name} would start {ist(start)}"] if moved_time else [],
        needs_reason=f"Applying this despite: {', '.join(problems)}." if problems else None,
        plan={"event_id": event.id, "venue_id": target.id, "start_at": start.isoformat(), "end_at": end.isoformat(), "delta_seconds": delta.total_seconds()},
    )


def apply_venue_change(db: Session, festival: Festival, plan: dict[str, Any]) -> str:
    event = db.get(Event, plan["event_id"])
    event.venue_id = plan["venue_id"]
    event.start_at, event.end_at = datetime.fromisoformat(plan["start_at"]), datetime.fromisoformat(plan["end_at"])
    event.schedule_source = "manual"
    delta = timedelta(seconds=plan["delta_seconds"])
    if delta:
        for shift in db.scalars(select(VolunteerShift).where(VolunteerShift.event_id == event.id, VolunteerShift.status != "cancelled")):
            shift.start_at += delta
            shift.end_at += delta
    return f"{event.name} moved"


# --- Scenario C: a bus is delayed ----------------------------------------------------------

class DelayChange(BaseModel):
    route_code: str
    minutes: int = Field(ge=1, le=1440)


def on_shift(db: Session, festival_id: int, department: str, moment: datetime) -> int:
    local = moment.astimezone(IST)
    now = local.hour * 60 + local.minute
    count = 0
    for shift in db.scalars(select(User.shift).join(Department, Department.id == User.department_id).where(User.festival_id == festival_id, Department.name == department, User.shift.is_not(None))):
        start_text, end_text = shift.split("-")
        start, end = (int(t[:2]) * 60 + int(t[3:]) for t in (start_text, end_text))
        count += start <= now < end if start < end else (now >= start or now < end)
    return count


def bus_delay(db: Session, festival: Festival, change: DelayChange) -> Outcome:
    fid = festival.id
    route = by_code(db, Route, fid, change.route_code)
    if not route.expected_arrival_at:
        raise SimulationError(f"{route.code} has no arrival time")
    new_arrival = route.expected_arrival_at + timedelta(minutes=change.minutes)
    riders = list(db.scalars(select(Participant).join(TransportAssignment, TransportAssignment.participant_id == Participant.id).where(TransportAssignment.route_id == route.id).order_by(Participant.code)))
    vehicle = db.get(Vehicle, route.vehicle_id) if route.vehicle_id else None
    next_trip = None
    if vehicle:
        next_trip = db.scalar(select(Route).where(Route.vehicle_id == vehicle.id, Route.id != route.id, Route.departure_at >= route.departure_at).order_by(Route.departure_at).limit(1))
    knock_on = next_trip is not None and next_trip.departure_at < new_arrival

    before = {e["code"]: e for e in affected_events(db, fid, route, route.expected_arrival_at)}
    after = {e["code"]: e for e in affected_events(db, fid, route, new_arrival)}
    newly = [e for code, e in after.items() if code not in before]

    def to_people(_: set[Any]) -> Hop:
        if not riders:
            return Hop(impacts=[Impact("participants", "low", "fact", "No passengers are assigned to this trip.", record_ref=route.code)])
        return Hop({p.code for p in riders}, [Impact("participants", "high", "fact", f"{plural(len(riders), 'passenger')} reach campus at {ist(new_arrival)} instead of {ist(route.expected_arrival_at)}.", "Notify passengers of the new time.", route.code)])

    def to_events(_: set[Any]) -> Hop:
        impacts = [Impact("events", "high", "estimate", f"{plural(e['passengers'], 'passenger')} may miss the start of {e['name']} at {ist(e['start_at'])}.", "Ask the coordinator to hold registration for them.", e["code"]) for e in newly]
        return Hop({e["code"] for e in newly}, impacts)

    def to_accommodation(_: set[Any]) -> Hop:
        housed = db.scalar(select(func.count()).select_from(AccommodationAllocation).where(AccommodationAllocation.participant_id.in_([p.id for p in riders]), ACTIVE)) or 0
        if not housed:
            return Hop()
        hour = new_arrival.astimezone(IST).hour
        late = hour >= 22 or hour < 6
        staff = on_shift(db, fid, "Hospitality", new_arrival)
        severity = "high" if late and staff == 0 else "medium" if late else "low"
        return Hop({"check-in"}, [Impact("accommodation", severity, "fact", f"{plural(housed, 'passenger')} with rooms check in around {ist(new_arrival)}. {plural(staff, 'Hospitality team member')} on shift then.", "Keep a check-in desk open." if late else None, route.code)])

    def to_desk(_: set[Any]) -> Hop:
        staff = on_shift(db, fid, "Registration", new_arrival)
        severity = "high" if riders and staff == 0 else "low"
        return Hop({"registration desk"}, [Impact("volunteers", severity, "fact", f"{plural(staff, 'Registration Desk member')} on shift at the new arrival time.", "Move a volunteer to the desk." if severity == "high" else None, route.code)])

    seats = rider_counts(db, fid)
    alternatives = []
    if vehicle or riders:
        for v in db.scalars(select(Vehicle).where(Vehicle.festival_id == fid, Vehicle.status == "available", Vehicle.id != route.vehicle_id).order_by(Vehicle.capacity.desc(), Vehicle.code)):
            busy = db.scalar(select(func.count()).select_from(Route).where(Route.vehicle_id == v.id, Route.departure_at < new_arrival, Route.expected_arrival_at > route.departure_at))
            if not busy:
                alternatives.append(v)
    later = [r for r in db.scalars(select(Route).where(Route.festival_id == fid, Route.pickup == route.pickup, Route.departure_at > route.departure_at).order_by(Route.departure_at))
             if r.vehicle_id and seats.get(r.id, 0) < db.get(Vehicle, r.vehicle_id).capacity]

    actions = [action(f"Send {v.code} instead", f"{v.vehicle_type} with {v.capacity} seats, free at this time.", "transportation") for v in alternatives[:2]]
    actions += [action(f"Use {r.code}", f"Leaves {r.pickup} at {ist(r.departure_at)} with spare seats.", "transportation") for r in later[:1]]
    if knock_on:
        actions.append(action(f"Reassign {next_trip.code}", f"{vehicle.code} will not be back in time for it.", "transportation"))
    actions.append(action("Tell passengers and coordinators", "Everyone affected is notified when this is applied.", "participants"))
    seed = [Impact("transportation", "high", "fact", f"{route.name} ({route.code}) arrives {change.minutes} minutes later than now expected.", record_ref=route.code)]
    if knock_on:
        seed.append(Impact("transportation", "high", "fact", f"{vehicle.code} is also due to run {next_trip.code} at {ist(next_trip.departure_at)}, before it gets back.", f"Reassign {next_trip.code} to another vehicle.", next_trip.code))
    return Outcome(
        title=f"{route.name} ({route.code}) delayed by {change.minutes} minutes",
        headline=f"{plural(len(riders), 'passenger')} arrive late, and {plural(len(newly), 'more event')} become at risk.",
        seed_module="transportation", seed_records={route.code}, seed_impacts=seed,
        rules={("transportation", "participants"): to_people, ("participants", "events"): to_events, ("participants", "accommodation"): to_accommodation, ("participants", "volunteers"): to_desk},
        current_state=[stat("Expected arrival", ist(route.expected_arrival_at)), stat("Passengers", len(riders)), stat("Vehicle", vehicle.code if vehicle else "None"),
                       stat("Delay so far", f"{route.delay_minutes} minutes"), stat("Events already at risk", len(before)), stat("From", route.pickup)],
        before_after=[change_row("Expected arrival", ist(route.expected_arrival_at), ist(new_arrival)), change_row("Total delay", f"{route.delay_minutes} minutes", f"{route.delay_minutes + change.minutes} minutes"),
                      change_row("Events at risk", len(before), len(after), "estimate")],
        actions=actions,
        assumptions=["Passengers need 30 minutes from drop-off to reach an event.", "Trips and vehicles are simulated: the pack has no transport data.", "Staff on shift come from the core team shift list."],
        scheduling=[f"Arrival moves to {ist(new_arrival)}"], plan={"route_id": route.id, "minutes": change.minutes},
    )


def apply_bus_delay(db: Session, festival: Festival, plan: dict[str, Any]) -> str:
    route = db.get(Route, plan["route_id"])
    route.expected_arrival_at += timedelta(minutes=plan["minutes"])
    route.delay_minutes += plan["minutes"]
    route.status = "delayed"
    return f"{route.name} delayed"


# --- Scenario D: volunteers become unavailable ---------------------------------------------

class VolunteerChange(BaseModel):
    volunteer_codes: list[str] = Field(min_length=1, max_length=50)


def volunteers_unavailable(db: Session, festival: Festival, change: VolunteerChange) -> Outcome:
    fid = festival.id
    leaving = [by_code(db, Volunteer, fid, code) for code in sorted(set(change.volunteer_codes))]
    inactive = [v.name for v in leaving if v.status != "active"]
    if inactive:
        raise SimulationError(f"Already unavailable: {', '.join(inactive)}")
    gone = {v.id for v in leaving}
    shifts = list(db.scalars(select(VolunteerShift).where(VolunteerShift.volunteer_id.in_(gone), VolunteerShift.status != "cancelled").order_by(VolunteerShift.start_at, VolunteerShift.id)))
    events = {e.id: e for e in db.scalars(select(Event).where(Event.id.in_([s.event_id for s in shifts if s.event_id])))}
    staffing = {e["code"]: e for e in event_staffing(db, fid)}

    pool = [v for v in db.scalars(select(Volunteer).where(Volunteer.festival_id == fid, Volunteer.status == "active", Volunteer.id.not_in(gone)).order_by(Volunteer.code))]
    busy: dict[int, list[tuple[datetime, datetime]]] = defaultdict(list)
    for s in db.scalars(select(VolunteerShift).where(VolunteerShift.festival_id == fid, VolunteerShift.status != "cancelled")):
        busy[s.volunteer_id].append((s.start_at, s.end_at))
    load = shift_counts(db, fid)
    before_load = [load.get(v.id, 0) for v in pool]

    assignments, unfilled = [], []
    for shift in shifts:
        event = events.get(shift.event_id)
        wanted = EVENT_SKILLS.get(event.venue_requirements or "", set()) if event else set()
        free = [v for v in pool if all(shift.end_at <= s or e <= shift.start_at for s, e in busy[v.id])]
        if not free:
            unfilled.append(shift)
            continue
        pick = min(free, key=lambda v: (-len(wanted & set(v.skills)), load.get(v.id, 0), v.code))
        busy[pick.id].append((shift.start_at, shift.end_at))
        load[pick.id] = load.get(pick.id, 0) + 1
        assignments.append({"shift_id": shift.id, "volunteer_id": pick.id, "volunteer": pick.name, "code": pick.code})
    after_load = [load.get(v.id, 0) for v in pool]
    by_id = {s.id: s for s in shifts}
    short_events = Counter(events[s.event_id].code for s in unfilled if s.event_id in events)

    def to_events(_: set[Any]) -> Hop:
        impacts = []
        for event in sorted(events.values(), key=lambda e: e.start_at or datetime.max.replace(tzinfo=IST)):
            lost = sum(s.event_id == event.id for s in shifts)
            covered = sum(events.get(by_id[a["shift_id"]].event_id) is event for a in assignments)
            row = staffing.get(event.code)
            if row:
                final = row["staffed"] - lost + covered
                severity = "high" if covered < lost else "low"
                impacts.append(Impact("events", severity, "fact", f"{event.name} loses {plural(lost, 'shift')} and {covered} can be covered, leaving {final} staffed of {row['needed']} needed.", record_ref=event.code))
        return Hop({e.code for e in events.values()}, impacts)

    def to_replacements(_: set[Any]) -> Hop:
        impacts = []
        if assignments:
            impacts.append(Impact("volunteers", "medium", "fact", f"{plural(len(assignments), 'shift')} can be covered by volunteers who are free at that time.", "Assign the replacements in this plan."))
        if unfilled:
            impacts.append(Impact("volunteers", "critical", "fact", f"{plural(len(unfilled), 'shift')} cannot be covered: nobody is free.", "Recruit more volunteers or lower the requirement."))
        return Hop({a["code"] for a in assignments}, impacts)

    def to_participants(_: set[Any]) -> Hop:
        if not short_events:
            return Hop()
        codes = list(short_events)
        count = db.scalar(select(func.count(func.distinct(Registration.participant_id))).join(Event, Event.id == Registration.event_id).where(Event.code.in_(codes), Event.festival_id == fid)) or 0
        return Hop({f"event:{c}" for c in codes}, [Impact("participants", "medium", "estimate", f"{plural(count, 'registrant')} are in {plural(len(codes), 'event')} that would stay short of volunteers.", "Prioritise safety and registration roles.")])

    actions = []
    if assignments:
        actions.append(action(f"Assign {plural(len(assignments), 'replacement')}", "; ".join(f"{a['volunteer']} ({a['code']})" for a in assignments[:4]) + (" and others." if len(assignments) > 4 else "."), "volunteers"))
    if unfilled:
        actions.append(action(f"Find cover for {plural(len(unfilled), 'shift')}", "Nobody is free. Move someone from a lower-priority role.", "volunteers"))
    average = lambda values: round(sum(values) / len(values), 1) if values else 0  # noqa: E731
    return Outcome(
        title=f"{plural(len(leaving), 'volunteer')} become unavailable",
        headline=f"{plural(len(shifts), 'shift')} open up. {len(assignments)} can be covered and {len(unfilled)} cannot.",
        seed_module="volunteers", seed_records={v.code for v in leaving},
        seed_impacts=[Impact("volunteers", "high" if shifts else "low", "fact", f"{', '.join(v.name for v in leaving[:4])}{' and others' if len(leaving) > 4 else ''} become unavailable, leaving {plural(len(shifts), 'shift')} open.")],
        rules={("volunteers", "events"): to_events, ("events", "volunteers"): to_replacements, ("events", "participants"): to_participants},
        current_state=[stat("Volunteers affected", len(leaving)), stat("Their shifts", len(shifts)), stat("Events involved", len(events)), stat("Other active volunteers", len(pool))],
        before_after=[change_row("Open shifts", 0, len(unfilled)), change_row("Most shifts on one person", max(before_load, default=0), max(after_load, default=0)), change_row("Average shifts per person", average(before_load), average(after_load)),
                      change_row("Active volunteers", len(pool) + len(leaving), len(pool))],
        actions=actions,
        assumptions=["Skills matching comes from each event's venue needs.", "Shifts and most volunteers are simulated."],
        scheduling=[f"{plural(len(unfilled), 'shift')} left uncovered"] if unfilled else [],
        needs_reason="Some shifts would be left uncovered." if unfilled else None,
        plan={"volunteers": sorted(gone), "cancel": [s.id for s in shifts], "assign": assignments},
    )


def apply_volunteers_unavailable(db: Session, festival: Festival, plan: dict[str, Any]) -> str:
    for v in db.scalars(select(Volunteer).where(Volunteer.id.in_(plan["volunteers"]))):
        v.status = "unavailable"
    shifts = {s.id: s for s in db.scalars(select(VolunteerShift).where(VolunteerShift.id.in_(plan["cancel"])))}
    for shift in shifts.values():
        shift.status = "cancelled"
    for a in plan["assign"]:
        old = shifts[a["shift_id"]]
        db.add(VolunteerShift(festival_id=festival.id, volunteer_id=a["volunteer_id"], event_id=old.event_id, start_at=old.start_at, end_at=old.end_at, task=old.task))
    return f"{len(plan['volunteers'])} volunteers marked unavailable; {len(plan['assign'])} replacements assigned"


# --- Scenario E: the budget is reduced -----------------------------------------------------

class BudgetChange(BaseModel):
    department: str | None = None
    amount: Decimal = Field(gt=0, le=1_000_000_000)


BUDGET_MODULES = {"Hospitality": "accommodation", "Transport": "transportation", "Logistics": "venues", "Events": "events", "Volunteers": "volunteers"}


def budget_reduction(db: Session, festival: Festival, change: BudgetChange) -> Outcome:
    fid = festival.id
    rows = db.execute(select(Budget, Department).join(Department, Department.id == Budget.department_id).where(Budget.festival_id == fid).order_by(Department.name)).all()
    scope = [(b, d) for b, d in rows if change.department in (None, d.name)]
    if not scope:
        raise SimulationError(f"No budget found for {change.department}")
    total = sum(b.approved_amount for b, _ in scope)
    if change.amount > total:
        raise SimulationError(f"{inr(change.amount)} is more than the {inr(total)} approved")

    cuts: dict[int, Decimal] = {}
    left = change.amount
    for i, (b, _) in enumerate(scope):
        cut = left if i == len(scope) - 1 else (change.amount * b.approved_amount / total).quantize(Decimal("1"))
        cuts[b.id] = cut
        left -= cut

    expenses = list(db.scalars(select(Expense).where(Expense.festival_id == fid, Expense.deleted_at.is_(None), Expense.approval_status != "rejected", Expense.department_id.in_([d.id for _, d in scope])).order_by(Expense.id)))
    stays = external_commitment(db, fid)
    lines = []
    for b, d in scope:
        spend = [e for e in expenses if e.department_id == d.id]
        committed = sum((e.actual_amount or e.estimated_amount) for e in spend if e.approval_status == "approved")
        committed += stays if d.name == "Hospitality" else 0
        pending = sum(e.estimated_amount for e in spend if e.approval_status == "pending")
        new_budget = b.approved_amount - cuts[b.id]
        lines.append({"department": d.name, "before": b.approved_amount, "after": new_budget, "committed": committed, "pending": pending,
                      "deficit": max(Decimal(0), committed + pending - new_budget), "spend": spend})
    deficit = sum(line["deficit"] for line in lines)
    over_committed = [line for line in lines if line["committed"] > line["after"]]
    outstanding = sum(s.committed_amount - s.received_amount for s in db.scalars(select(Sponsor).where(Sponsor.festival_id == fid)))
    pending_total = sum(line["pending"] for line in lines)

    def stay_saving() -> tuple[Decimal, int]:
        occupied = occupied_counts(db, fid)
        rows = db.execute(select(Hostel, func.sum(Room.capacity), func.min(Room.id)).join(Room, Room.hostel_id == Hostel.id).where(Hostel.festival_id == fid, Hostel.kind == "external", Hostel.status == "available").group_by(Hostel.id).order_by(Hostel.cost_per_bed_night)).all()
        beds = [{"cost": h.cost_per_bed_night or Decimal(0), "free": capacity - occupied.get(room, 0), "used": occupied.get(room, 0)} for h, capacity, room in rows]
        saving, moved = Decimal(0), 0
        for dear in reversed(beds):
            for cheap in beds:
                if cheap["cost"] >= dear["cost"]:
                    break
                count = min(dear["used"], cheap["free"])
                saving += count * (dear["cost"] - cheap["cost"])
                dear["used"] -= count
                cheap["free"] -= count
                moved += count
        return saving, moved

    def for_module(module: str):
        def rule(_: set[Any]) -> Hop:
            impacts, records = [], set()
            for line in lines:
                if BUDGET_MODULES.get(line["department"]) != module or not line["deficit"]:
                    continue
                review = [e for e in line["spend"] if e.approval_status == "pending" or e.payment_status != "paid"]
                records.update(e.id for e in review)
                records.add(line["department"])
                impacts.extend(Impact(module, "high" if e.approval_status == "approved" else "medium", "fact", f"{e.description}: {inr(e.estimated_amount)}, {e.approval_status}, needs review.", "Reduce, defer or reject this.", str(e.id)) for e in review[:5])
                if module == "accommodation":
                    saving, moved = stay_saving()
                    if saving:
                        impacts.append(Impact(module, "medium", "estimate", f"Moving {plural(moved, 'guest')} to cheaper external stays could save up to {inr(saving)} a night.", "Review before rooms are confirmed."))
            return Hop(records, impacts)
        return rule

    seed = [Impact("finance", "critical" if over_committed else "high" if deficit else "low", "fact", f"{line['department']} budget falls from {inr(line['before'])} to {inr(line['after'])}, against {inr(line['committed'])} committed and {inr(line['pending'])} pending.",
                   record_ref=line["department"]) for line in lines if line["deficit"] or len(lines) == 1]
    if not seed:
        seed = [Impact("finance", "low", "fact", f"The budget falls by {inr(change.amount)}, and commitments still fit.")]
    actions = []
    if pending_total:
        actions.append(action("Review pending expenses", f"{inr(pending_total)} is waiting for approval and could be deferred.", "finance"))
    if outstanding:
        actions.append(action("Chase sponsor payments", f"{inr(outstanding)} is committed by sponsors but not yet received. These sponsors are simulated.", "finance"))
    if not deficit:
        actions.append(action("No corrective action needed", "Commitments still fit inside the reduced budget.", "finance"))
    label = change.department or "the festival"
    return Outcome(
        title=f"Reduce {label} budget by {inr(change.amount)}",
        headline=f"Budget shortfall of {inr(deficit)} against commitments." if deficit else "Commitments still fit inside the reduced budget.",
        seed_module="finance", seed_records={line["department"] for line in lines}, seed_impacts=seed,
        rules={("finance", module): for_module(module) for module in set(BUDGET_MODULES.values())},
        current_state=[stat("Approved budget", inr(total)), stat("Committed", inr(sum(line["committed"] for line in lines))), stat("Pending approval", inr(pending_total)), stat("Sponsor money outstanding", inr(outstanding))],
        before_after=[change_row("Approved budget", inr(total), inr(total - change.amount)), change_row("Shortfall", inr(sum(max(Decimal(0), line["committed"] + line["pending"] - line["before"]) for line in lines)), inr(deficit))],
        actions=actions,
        assumptions=["All budgets, expenses and sponsors are simulated.", "External stays count against Hospitality. Savings assume guests can move between properties.", "A festival-wide cut is shared in proportion to each budget."],
        budget_impact=-float(change.amount),
        needs_reason="The budget would fall below what is already committed." if over_committed else None,
        plan={"cuts": {str(k): str(v) for k, v in cuts.items()}},
    )


def apply_budget_reduction(db: Session, festival: Festival, plan: dict[str, Any]) -> str:
    for budget_id, cut in plan["cuts"].items():
        db.get(Budget, int(budget_id)).approved_amount -= Decimal(cut)
    return "Budget reduced"


@dataclass(frozen=True)
class Scenario:
    label: str
    model: type[BaseModel]
    simulate: Any
    apply: Any


SCENARIOS = {
    "hostel_closure": Scenario("Hostel becomes unavailable", HostelChange, hostel_closure, apply_hostel_closure),
    "venue_change": Scenario("Event changes venue", VenueChange, venue_change, apply_venue_change),
    "bus_delay": Scenario("Bus is delayed", DelayChange, bus_delay, apply_bus_delay),
    "volunteers_unavailable": Scenario("Volunteers become unavailable", VolunteerChange, volunteers_unavailable, apply_volunteers_unavailable),
    "budget_reduction": Scenario("Budget is reduced", BudgetChange, budget_reduction, apply_budget_reduction),
}

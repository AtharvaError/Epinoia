import csv
import hashlib
import io
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.api.common import by_code, like, ordered, paged
from app.auth.deps import current_user, festival_access, permissions_of, require
from app.database import get_db
from app.models import (
    AccommodationAllocation, Announcement, AuditLog, Budget, Contingent, DataIssue, Department, Festival, Hostel, Participant, Room, User,
)
from app.services.allocator import MINOR_AGE, SEVERITY, Beds, Person, housing_group, nights, plan_allocation, suits_access, summarize
from app.services.audit import record
from app.services.housing import (
    ACTIVE, contingent_codes, external_commitment, hospitality_budget, occupied_counts, open_rooms, to_person, unallocated_query,
)

router = APIRouter(prefix="/api/festivals/{festival_id}/accommodation", tags=["accommodation"])

OUTREACH_QUESTION = (
    "Which housing would you be comfortable in? "
    "Boys' block / Girls' block / Private or gender-neutral room / I'd like to talk to an organizer."
)


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
    gender_policy: str | None = Field(default=None, pattern="^(boys|girls|gender_neutral)$")
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


class BudgetOverrideIn(BaseModel):
    amount: Decimal = Field(gt=0, le=100000000)
    reason: str = Field(min_length=5, max_length=500)


class HousingOutreachIn(BaseModel):
    scope: str = Field(default="blank_only", pattern="^(blank_only|all_unmapped|codes)$")
    participant_codes: list[str] = Field(default_factory=list)


class HousingPreferenceIn(BaseModel):
    participant_codes: list[str] = Field(min_length=1, max_length=1000)
    housing_preference: str = Field(pattern="^(boys_block|girls_block|gender_neutral|talk_to_organizer|clear)$")
    note: str | None = Field(default=None, max_length=300)


class GenderNeutralRoomsIn(BaseModel):
    room_codes: list[str] = Field(min_length=1, max_length=100)
    confirmed_by: str = Field(min_length=2, max_length=120)
    reason: str = Field(min_length=5, max_length=500)


class CallSheetIn(BaseModel):
    step_free_entry: bool | None = None
    ground_floor_rooms: int | None = Field(default=None, ge=0, le=1000)
    accessible_bathrooms: int | None = Field(default=None, ge=0, le=1000)
    has_lift: bool | None = None
    confirmed_by: str = Field(min_length=2, max_length=120)
    reset_unknown: bool = False


class RoomAccessFeaturesIn(BaseModel):
    accessible: bool | None = None
    near_washroom: bool | None = None
    visual_assistance: bool | None = None
    confirmed_by: str = Field(min_length=2, max_length=120)


class FixStayDatesIn(BaseModel):
    participant_code: str
    arrival_at: datetime
    departure_at: datetime
    reason: str = Field(min_length=5, max_length=500)


class MinorPolicyIn(BaseModel):
    policy: str = Field(pattern="^(require_escort_confirmation|on_campus_only|escort_confirmed_contingent)$")
    reason: str = Field(min_length=5, max_length=500)


class ConfirmEscortIn(BaseModel):
    participant_codes: list[str] = Field(default_factory=list)
    contingent_code: str | None = None
    escort_name: str = Field(min_length=2, max_length=120)
    note: str | None = Field(default=None, max_length=240)
    reason: str = Field(min_length=5, max_length=500)


def _cause_key(raw_reason: str) -> tuple[str, str]:
    base = raw_reason.split(":")[-1].strip() if ":" in raw_reason else raw_reason
    if base == "External budget limit reached":
        return "budget", base
    if "Gender is blank" in base:
        return "gender_blank", base
    if "Accessible rooms are gendered" in base:
        return "access_gendered", base
    if "No suitable accessible bed is left" in base:
        return "access_exhausted", base
    if "Stay dates are invalid" in base:
        return "invalid_dates", base
    if "talk to an organizer" in base:
        return "talk_to_organizer", base
    if "under 18" in base:
        return "minors_policy", base
    return "other", base


def _redact_reason(raw_reason: str, sensitive: bool) -> str:
    if sensitive:
        return raw_reason
    key, base = _cause_key(raw_reason)
    if key in ("gender_blank", "access_gendered", "access_exhausted", "talk_to_organizer"):
        return "Personal housing or accessibility arrangement needed (restricted detail)"
    return base


def _primary_action_for(cause_key: str) -> dict[str, str]:
    return {
        "budget": {"key": "request_budget", "label": "Request extra budget"},
        "gender_blank": {"key": "housing_outreach", "label": "Send private housing inquiry"},
        "access_gendered": {"key": "access_arrangement", "label": "Accessibility & neutral room setup"},
        "access_exhausted": {"key": "accessibility_call_sheet", "label": "Record external accessibility call sheet"},
        "invalid_dates": {"key": "fix_stay_dates", "label": "Fix stay dates inline"},
        "talk_to_organizer": {"key": "housing_outreach", "label": "Record organizer housing arrangement"},
        "minors_policy": {"key": "confirm_escort", "label": "Confirm escort / guardian"},
    }.get(cause_key, {"key": "review", "label": "Review people"})


def state_hash(people: list[Person], rooms: list[Beds]) -> str:
    """Changes whenever a person or a free bed changes, so a stale preview cannot be applied."""
    parts = (
        sorted(f"{p.code}:{p.gender}:{p.accessibility}:{p.housing_preference}:{p.arrival}:{p.departure}:{p.minor_supervision_status}" for p in people)
        + sorted(f"{r.code}:{r.free}:{r.gender}:{r.accessible}:{r.access_status}:{r.ground_floor_beds}:{r.accessible_bathroom_beds}" for r in rooms)
    )
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def build_plan(db: Session, festival: Festival, limit: Decimal | None):
    contingents = contingent_codes(db, festival.id)
    people = [to_person(p, contingents) for p in db.scalars(unallocated_query(festival.id).order_by(Participant.code))]
    rooms = open_rooms(db, festival.id)
    # The limit covers all external stays, so only what is left of it can be spent here.
    remaining = None if limit is None else max(Decimal(0), limit - external_commitment(db, festival.id))
    return people, rooms, plan_allocation(people, rooms, remaining, festival.minor_housing_policy), state_hash(people, rooms)


def effective_limit(db: Session, festival: Festival, requested: Decimal | None) -> Decimal | None:
    return requested if requested is not None else hospitality_budget(db, festival.id)


@router.get("/summary", dependencies=[Depends(require("accommodation.read"))])
def summary(festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    sensitive = "participants.read_sensitive" in permissions_of(user)
    occupied = occupied_counts(db, festival.id)

    # Check-in status per room for ready vs occupied vs reserved breakdown
    checked_in_by_room = dict(
        db.execute(
            select(AccommodationAllocation.room_id, func.count())
            .where(AccommodationAllocation.festival_id == festival.id, AccommodationAllocation.status == "checked_in")
            .group_by(AccommodationAllocation.room_id)
        ).all()
    )

    hostels: dict[int, dict[str, Any]] = {}
    over = []
    all_hostels = list(db.scalars(select(Hostel).where(Hostel.festival_id == festival.id).order_by(Hostel.kind.desc(), Hostel.code)))
    for hostel in all_hostels:
        hostels[hostel.id] = {
            "code": hostel.code,
            "name": hostel.name,
            "kind": hostel.kind,
            "status": hostel.status,
            "capacity": 0,
            "total_capacity": 0,
            "occupied": 0,
            "ready": 0,
            "checked_in": 0,
            "reserved": 0,
            "cleaning": 0,
            "maintenance": 0,
            "cost_per_bed_night": hostel.cost_per_bed_night,
            "distance_km": hostel.distance_km,
            "transport_available": hostel.transport_available,
            "accessibility_status": hostel.accessibility_status or "unknown",
            "step_free_entry": hostel.step_free_entry,
            "ground_floor_rooms": hostel.ground_floor_rooms,
            "accessible_bathrooms": hostel.accessible_bathrooms,
            "has_lift": hostel.has_lift,
            "accessibility_confirmed_by": hostel.accessibility_confirmed_by,
            "accessibility_confirmed_at": hostel.accessibility_confirmed_at,
        }

    bed_status = {
        "on_campus": {
            "total_capacity": 0, "ready": 0, "occupied": 0, "reserved": 0, "cleaning": 0, "maintenance": 0,
            "rooms_total": 0, "accessible_rooms": 0, "accessible_beds": 0, "accessible_free_beds": 0,
            "ground_floor_non_access_beds": 0, "near_washroom_confirmed_beds": 0, "visual_confirmed_beds": 0,
            "gender_neutral_rooms": 0, "gender_neutral_beds": 0, "gender_neutral_free_beds": 0, "gender_neutral_status": "UNKNOWN",
        },
        "external": {
            "total_capacity": 0, "ready": 0, "occupied": 0, "reserved": 0, "cleaning": 0, "maintenance": 0,
            "properties_total": sum(1 for h in all_hostels if h.kind == "external"),
            "declared_accessible_properties": sum(1 for h in all_hostels if h.kind == "external" and h.accessibility_status == "declared"),
            "confirmed_none_properties": sum(1 for h in all_hostels if h.kind == "external" and h.accessibility_status == "confirmed_none"),
            "unknown_accessible_properties": sum(1 for h in all_hostels if h.kind == "external" and (h.accessibility_status or "unknown") == "unknown"),
            "declared_accessible_beds": 0,
        },
    }

    room_rows = list(db.execute(select(Room, Hostel).join(Hostel, Hostel.id == Room.hostel_id).where(Room.festival_id == festival.id).order_by(Hostel.kind.desc(), Hostel.code, Room.code)))
    gender_neutral_rooms_list = []
    for room, hostel in room_rows:
        h = hostels[hostel.id]
        used = occupied.get(room.id, 0)
        chk = checked_in_by_room.get(room.id, 0)
        res = max(0, used - chk)
        avail_open = room.status == "available" and hostel.status == "available"
        free_beds = max(0, room.capacity - used) if avail_open else 0
        cleaning_beds = max(0, room.capacity - used) if (hostel.status == "available" and room.status == "cleaning") else 0
        maint_beds = max(0, room.capacity - used) if (hostel.status != "available" or room.status == "closed") else 0

        h["total_capacity"] += room.capacity
        h["capacity"] += room.capacity if avail_open else 0
        h["occupied"] += used
        h["ready"] += free_beds
        h["checked_in"] += chk
        h["reserved"] += res
        h["cleaning"] += cleaning_beds
        h["maintenance"] += maint_beds
        if used > room.capacity:
            over.append({"room": room.code, "capacity": room.capacity, "occupied": used})

        bucket = bed_status["on_campus" if hostel.kind == "on_campus" else "external"]
        bucket["total_capacity"] += room.capacity
        bucket["ready"] += free_beds
        bucket["occupied"] += chk
        bucket["reserved"] += res
        bucket["cleaning"] += cleaning_beds
        bucket["maintenance"] += maint_beds

        if hostel.kind == "on_campus":
            bucket["rooms_total"] += 1
            if room.accessible:
                bucket["accessible_rooms"] += 1
                bucket["accessible_beds"] += room.capacity
                bucket["accessible_free_beds"] += free_beds
            elif room.floor == 0:
                bucket["ground_floor_non_access_beds"] += room.capacity
            if room.near_washroom:
                bucket["near_washroom_confirmed_beds"] += room.capacity
            if room.visual_assistance:
                bucket["visual_confirmed_beds"] += room.capacity
            if room.gender_policy == "gender_neutral":
                bucket["gender_neutral_rooms"] += 1
                bucket["gender_neutral_beds"] += room.capacity
                bucket["gender_neutral_free_beds"] += free_beds
                gender_neutral_rooms_list.append({
                    "code": room.code, "hostel": hostel.name, "floor": room.floor,
                    "capacity": room.capacity, "occupied": used, "free": free_beds,
                    "accessible": room.accessible, "confirmed_by": room.policy_confirmed_by,
                })
        else:
            if hostel.accessibility_status == "declared":
                declared_cap = max(hostel.ground_floor_rooms or 0, hostel.accessible_bathrooms or 0)
                if declared_cap == 0 and hostel.step_free_entry and hostel.has_lift:
                    declared_cap = room.capacity
                bucket["declared_accessible_beds"] += min(room.capacity, declared_cap)

    if bed_status["on_campus"]["gender_neutral_rooms"] > 0:
        bed_status["on_campus"]["gender_neutral_status"] = "DECLARED"

    # Load all unallocated participants and contingents
    contingent_map: dict[int, Contingent] = {
        c.id: c for c in db.scalars(select(Contingent).where(Contingent.festival_id == festival.id))
    }
    contingent_code_map = {cid: c.code for cid, c in contingent_map.items()}
    unalloc_participants = list(db.scalars(unallocated_query(festival.id).order_by(Participant.code)))
    unalloc_by_code = {p.code: p for p in unalloc_participants}
    unalloc_count = len(unalloc_participants)

    # Arrival window counts among unallocated
    already_here = 0
    next_6h = 0
    next_12h = 0
    next_24h = 0
    for p in unalloc_participants:
        if not p.arrival_at:
            continue
        delta_h = (p.arrival_at - now).total_seconds() / 3600.0
        if delta_h <= 0:
            already_here += 1
        else:
            if delta_h <= 6:
                next_6h += 1
            if delta_h <= 12:
                next_12h += 1
            if delta_h <= 24:
                next_24h += 1

    # Run real allocation engine with current remaining budget
    approved = hospitality_budget(db, festival.id)
    committed = external_commitment(db, festival.id)
    remaining_budget = max(Decimal(0), (approved or Decimal(0)) - committed) if approved is not None else None
    people_objs = [to_person(p, contingent_code_map) for p in unalloc_participants]
    rooms_objs = open_rooms(db, festival.id)
    live_plan = plan_allocation(people_objs, rooms_objs, remaining_budget, festival.minor_housing_policy)

    # Compute exact extra budget needed for budget-held people using the real engine
    budget_held_codes = {h["code"] for h in live_plan.held if h["reason"] == "External budget limit reached"}
    budget_held_count = len(budget_held_codes)
    uncapped_plan = plan_allocation(people_objs, rooms_objs, None, festival.minor_housing_policy)
    placed_in_uncapped = [a for a in uncapped_plan.assignments if a["code"] in budget_held_codes]
    placeable_in_open_beds_count = len(placed_in_uncapped)
    cheapest_mix_open_beds_cost = uncapped_plan.external_cost
    extra_needed_open_beds = max(Decimal(0), cheapest_mix_open_beds_cost - (remaining_budget or Decimal(0)))

    # If budget_held_count > placeable_in_open_beds_count (some hit "No beds left" once the 380 open beds fill),
    # run the real engine with overflow capacity at the cheapest compliant external hostel rate to get exact total for all N.
    ext_rooms_sorted = sorted((r for r in rooms_objs if r.kind == "external" and r.transport is not False and r.cost is not None), key=lambda r: (r.cost, r.code))
    cheapest_ext_rate = ext_rooms_sorted[0].cost if ext_rooms_sorted else Decimal(0)
    if budget_held_count > placeable_in_open_beds_count and ext_rooms_sorted:
        placed_codes_uncapped = {a["code"] for a in uncapped_plan.assignments}
        overflow_people = [p for p in people_objs if p.code in budget_held_codes and p.code not in placed_codes_uncapped]
        overflow_room = Beds(
            ext_rooms_sorted[0].id, ext_rooms_sorted[0].code, ext_rooms_sorted[0].hostel, "external",
            None, None, None, len(overflow_people), cheapest_ext_rate, True, ext_rooms_sorted[0].distance,
        )
        overflow_plan = plan_allocation(overflow_people, [overflow_room], None, festival.minor_housing_policy)
        cheapest_mix_all_cost = cheapest_mix_open_beds_cost + overflow_plan.external_cost
    else:
        cheapest_mix_all_cost = cheapest_mix_open_beds_cost
    extra_needed_all = max(Decimal(0), cheapest_mix_all_cost - (remaining_budget or Decimal(0)))

    # Budget overrides from audit log
    override_logs = list(
        db.scalars(
            select(AuditLog)
            .where(AuditLog.festival_id == festival.id, AuditLog.action.in_(("accommodation.budget_override", "accommodation.plan")))
            .order_by(AuditLog.id.desc())
            .limit(15)
        )
    )
    budget_overrides = []
    for log in override_logs:
        if log.action == "accommodation.budget_override" or (log.action == "accommodation.plan" and log.reason):
            budget_overrides.append({
                "id": log.id,
                "actor": log.actor_label,
                "action": log.action,
                "summary": log.summary,
                "reason": log.reason,
                "added_amount": (log.after or {}).get("added_amount"),
                "approved_amount": (log.after or {}).get("approved_amount"),
                "created_at": log.created_at,
            })

    budget_row = db.scalar(select(Budget).join(Department, Department.id == Budget.department_id).where(Budget.festival_id == festival.id, Department.name == "Hospitality"))
    budget_source = (
        "Hospitality department budget (Finance seed) + authorized overrides"
        if any(o["action"] == "accommodation.budget_override" for o in budget_overrides)
        else "Hospitality department budget (Finance table, simulated seed)"
    )

    # Pipeline counts (reconciling)
    requested_total = db.scalar(
        select(func.count()).select_from(Participant).where(
            Participant.festival_id == festival.id, Participant.deleted_at.is_(None), Participant.accommodation_required.is_(True)
        )
    ) or 0
    active_alloc_rows = list(
        db.execute(
            select(AccommodationAllocation, Participant, Room, Hostel)
            .join(Participant, Participant.id == AccommodationAllocation.participant_id)
            .join(Room, Room.id == AccommodationAllocation.room_id)
            .join(Hostel, Hostel.id == Room.hostel_id)
            .where(AccommodationAllocation.festival_id == festival.id, ACTIVE)
        )
    )
    total_active_allocations = len(active_alloc_rows)
    checked_in_total = sum(1 for a, _, _, _ in active_alloc_rows if a.status == "checked_in")
    allocated_awaiting_checkin = total_active_allocations - checked_in_total

    # Build "Needs a decision" work queue grouped by CAUSE and sorted by URGENCY (earliest arrival still without a bed)
    cause_groups: dict[str, dict[str, Any]] = {}
    for h_item in live_plan.held:
        p = unalloc_by_code.get(h_item["code"])
        if not p:
            continue
        ckey, base_reason = _cause_key(h_item["reason"])
        display_reason = _redact_reason(base_reason, sensitive)
        grp = cause_groups.get(ckey)
        if not grp:
            grp = {
                "key": ckey,
                "cause": display_reason,
                "raw_cause": base_reason,
                "count": 0,
                "already_here_count": 0,
                "next_12h_count": 0,
                "earliest_arrival": None,
                "contingents_counter": Counter(),
                "sub_breakdown_counter": Counter(),
                "people": [],
                "primary_action": _primary_action_for(ckey),
            }
            cause_groups[ckey] = grp
        grp["count"] += 1
        if p.arrival_at:
            delta_h = (p.arrival_at - now).total_seconds() / 3600.0
            if delta_h <= 0:
                grp["already_here_count"] += 1
            elif delta_h <= 12:
                grp["next_12h_count"] += 1
            if grp["earliest_arrival"] is None or p.arrival_at < grp["earliest_arrival"]:
                grp["earliest_arrival"] = p.arrival_at
        c_code = contingent_code_map.get(p.contingent_id, "none")
        grp["contingents_counter"][c_code] += 1
        if ":" in h_item["reason"] and sensitive:
            prefix = h_item["reason"].split(":")[0].strip()
            grp["sub_breakdown_counter"][prefix] += 1
        if len(grp["people"]) < 60:
            grp["people"].append({
                "code": p.code,
                "name": p.full_name,
                "contingent": c_code,
                "college": p.college,
                "arrival_at": p.arrival_at,
                "departure_at": p.departure_at,
                "age": p.age,
                "is_minor": bool(p.age is not None and p.age < MINOR_AGE),
                "minor_supervision_status": p.minor_supervision_status,
                "gender": p.gender_as_given if sensitive else None,
                "housing_preference": p.housing_preference if sensitive else None,
                "housing_outreach_sent_at": p.housing_outreach_sent_at,
                "accessibility": p.accessibility_needs if sensitive else None,
                "restricted": not sensitive,
                "reason": _redact_reason(h_item["reason"], sensitive),
            })

    # Under-18s summary & queue item if any minors are unconfirmed off-campus or unallocated
    minors_on_campus = 0
    minors_ext_unconfirmed = 0
    minors_ext_confirmed = 0
    minors_needing_confirmation = []
    for a, p, r, h in active_alloc_rows:
        if p.age is not None and p.age < MINOR_AGE:
            if h.kind == "on_campus":
                minors_on_campus += 1
            else:
                if p.minor_supervision_status == "confirmed":
                    minors_ext_confirmed += 1
                else:
                    minors_ext_unconfirmed += 1
                    c_code = contingent_code_map.get(p.contingent_id, "none")
                    cont = contingent_map.get(p.contingent_id)
                    minors_needing_confirmation.append({
                        "code": p.code,
                        "name": p.full_name,
                        "age": p.age,
                        "contingent": c_code,
                        "college": p.college,
                        "leader": cont.leader if cont else None,
                        "escort": cont.escort if cont else None,
                        "placement": f"{r.code} ({h.name}, external)",
                        "arrival_at": p.arrival_at,
                        "minor_supervision_status": p.minor_supervision_status or "needs_confirmation",
                        "minor_supervision_note": p.minor_supervision_note,
                    })

    unalloc_minors = [p for p in unalloc_participants if p.age is not None and p.age < MINOR_AGE]
    unconfirmed_unalloc_minors = [p for p in unalloc_minors if p.minor_supervision_status != "confirmed"]
    for p in unconfirmed_unalloc_minors[:40]:
        c_code = contingent_code_map.get(p.contingent_id, "none")
        cont = contingent_map.get(p.contingent_id)
        minors_needing_confirmation.append({
            "code": p.code,
            "name": p.full_name,
            "age": p.age,
            "contingent": c_code,
            "college": p.college,
            "leader": cont.leader if cont else None,
            "escort": cont.escort if cont else None,
            "placement": "Unallocated (waiting for bed)",
            "arrival_at": p.arrival_at,
            "minor_supervision_status": p.minor_supervision_status or "needs_confirmation",
            "minor_supervision_note": p.minor_supervision_note,
        })

    if minors_ext_unconfirmed > 0 or (not festival.minor_housing_policy and unconfirmed_unalloc_minors):
        minor_dates = [p.arrival_at for p in unconfirmed_unalloc_minors if p.arrival_at] + [
            p.arrival_at for _, p, _, h in active_alloc_rows if p.age is not None and p.age < MINOR_AGE and h.kind == "external" and p.minor_supervision_status != "confirmed" and p.arrival_at
        ]
        minor_conts = Counter(
            [contingent_code_map.get(p.contingent_id, "none") for p in unconfirmed_unalloc_minors]
            + [contingent_code_map.get(p.contingent_id, "none") for _, p, _, h in active_alloc_rows if p.age is not None and p.age < MINOR_AGE and h.kind == "external" and p.minor_supervision_status != "confirmed"]
        )
        cause_groups["minors_confirmation"] = {
            "key": "minors_confirmation",
            "cause": f"Under-18 escort/guardian confirmation needed ({minors_ext_unconfirmed} placed off campus, {len(unconfirmed_unalloc_minors)} waiting)",
            "raw_cause": "Under-18 escort/guardian confirmation needed",
            "count": minors_ext_unconfirmed + len(unconfirmed_unalloc_minors),
            "already_here_count": sum(1 for d in minor_dates if (d - now).total_seconds() <= 0),
            "next_12h_count": sum(1 for d in minor_dates if 0 < (d - now).total_seconds() <= 43200),
            "earliest_arrival": min(minor_dates) if minor_dates else None,
            "contingents_counter": minor_conts,
            "sub_breakdown_counter": Counter({"Off-campus unconfirmed": minors_ext_unconfirmed, "Unallocated under-18": len(unconfirmed_unalloc_minors)}),
            "people": [
                {
                    "code": m["code"], "name": m["name"], "contingent": m["contingent"], "college": m["college"],
                    "arrival_at": m["arrival_at"], "departure_at": None, "age": m["age"], "is_minor": True,
                    "minor_supervision_status": m["minor_supervision_status"], "gender": None, "housing_preference": None,
                    "housing_outreach_sent_at": None, "accessibility": None, "restricted": not sensitive,
                    "reason": f"Age {m['age']} · {m['placement']} · Escort: {m['escort'] or 'not listed'}",
                }
                for m in minors_needing_confirmation[:50]
            ],
            "primary_action": {"key": "confirm_escort", "label": "Confirm escort / policy"},
        }

    # Format and sort decision queue by earliest_arrival ascending (urgency), then by highest already_here_count as tiebreaker
    far_future = datetime(2099, 1, 1, tzinfo=timezone.utc)
    decision_queue = []
    for grp in sorted(
        cause_groups.values(),
        key=lambda g: (
            g["earliest_arrival"] or far_future,
            -g["already_here_count"],
            g["key"],
        ),
    ):
        cont_list = [
            {"code": code, "count": cnt}
            for code, cnt in grp["contingents_counter"].most_common()
            if code != "none"
        ]
        sub_list = [{"label": k, "count": v} for k, v in grp["sub_breakdown_counter"].most_common()]
        decision_queue.append({
            "key": grp["key"],
            "cause": grp["cause"],
            "raw_cause": grp["raw_cause"],
            "count": grp["count"],
            "already_here_count": grp["already_here_count"],
            "next_12h_count": grp["next_12h_count"],
            "earliest_arrival": grp["earliest_arrival"],
            "contingents_count": len(cont_list),
            "contingents": cont_list,
            "sub_breakdown": sub_list,
            "primary_action": grp["primary_action"],
            "people": grp["people"],
        })

    # Housing preference & gender summary across all accommodation requesters
    all_requesters = list(
        db.scalars(
            select(Participant).where(
                Participant.festival_id == festival.id,
                Participant.deleted_at.is_(None),
                Participant.accommodation_required.is_(True),
            )
        )
    )
    req_blank = sum(1 for p in all_requesters if not p.gender_as_given)
    req_pnts = sum(1 for p in all_requesters if p.gender_as_given == "Prefer not to say")
    req_nb = sum(1 for p in all_requesters if p.gender_as_given == "Non-binary")
    unalloc_blank = sum(1 for p in unalloc_participants if not p.gender_as_given and not p.housing_preference)
    unalloc_unmapped = sum(
        1 for p in unalloc_participants
        if (not p.gender_as_given or p.gender_as_given in ("Prefer not to say", "Non-binary")) and not p.housing_preference
    )
    pref_counts = Counter(p.housing_preference for p in all_requesters if p.housing_preference)
    outreach_sent_count = sum(1 for p in all_requesters if p.housing_outreach_sent_at is not None)

    # Accessibility need-vs-feature summary
    need_types = ["Wheelchair access", "Ground floor / no stairs", "Near washroom", "Visual assistance"]
    alloc_pids = {a.participant_id for a, _, _, _ in active_alloc_rows}
    by_need = []
    for need in need_types:
        reqs = [p for p in all_requesters if p.accessibility_needs == need]
        alloc_n = sum(1 for p in reqs if p.id in alloc_pids)
        unalloc_n = len(reqs) - alloc_n
        if need == "Wheelchair access":
            on_campus_match = bed_status["on_campus"]["accessible_beds"]
            on_campus_free = bed_status["on_campus"]["accessible_free_beds"]
            feature_note = "Accessible room (step-free + accessible washroom)"
        elif need == "Ground floor / no stairs":
            on_campus_match = bed_status["on_campus"]["accessible_beds"] + bed_status["on_campus"]["ground_floor_non_access_beds"]
            on_campus_free = sum(
                max(0, r.capacity - occupied.get(r.id, 0))
                for r, h in room_rows
                if h.kind == "on_campus" and r.status == "available" and h.status == "available" and (r.accessible or r.floor == 0)
            )
            feature_note = "Accessible room OR on-campus Ground Floor (floor 0)"
        elif need == "Near washroom":
            on_campus_match = bed_status["on_campus"]["accessible_beds"] + bed_status["on_campus"]["near_washroom_confirmed_beds"]
            on_campus_free = bed_status["on_campus"]["accessible_free_beds"]
            feature_note = "Accessible room OR confirmed near-washroom room (non-accessible rooms UNKNOWN in pack)"
        else:
            on_campus_match = bed_status["on_campus"]["accessible_beds"] + bed_status["on_campus"]["visual_confirmed_beds"]
            on_campus_free = bed_status["on_campus"]["accessible_free_beds"]
            feature_note = "Accessible room OR confirmed visual-assistance room (non-accessible rooms UNKNOWN in pack)"

        ext_declared = sum(
            1 for h in all_hostels
            if h.kind == "external" and h.accessibility_status == "declared"
        )
        by_need.append({
            "need": need,
            "priority_rank": SEVERITY.get(need, 9) + 1,
            "total_requesters": len(reqs),
            "allocated": alloc_n,
            "unallocated": unalloc_n,
            "on_campus_matching_beds": on_campus_match,
            "on_campus_free_matching_beds": on_campus_free,
            "feature_rule": feature_note,
            "external_status": f"{ext_declared} properties DECLARED" if ext_declared else "UNKNOWN (0 of 6 external properties confirmed)",
        })

    # Stay-date errors (both unallocated and allocated so all pack errors are visible and fixable)
    date_errors = []
    for p in all_requesters:
        if not p.arrival_at or not p.departure_at or p.departure_at < p.arrival_at:
            date_errors.append({
                "code": p.code,
                "name": p.full_name,
                "contingent": contingent_code_map.get(p.contingent_id, "none"),
                "college": p.college,
                "arrival_at": p.arrival_at,
                "departure_at": p.departure_at,
                "is_allocated": p.id in alloc_pids,
            })

    total_minors_pack = db.scalar(
        select(func.count()).select_from(Participant).where(
            Participant.festival_id == festival.id, Participant.deleted_at.is_(None), Participant.age < MINOR_AGE
        )
    ) or 0

    return {
        "as_of": now.isoformat(),
        "hostels": list(hostels.values()),
        "over_capacity": over,
        "unallocated": unalloc_count,
        "budget": approved,
        "pipeline": {
            "requested_total": requested_total,
            "need_bed": unalloc_count,
            "blocked": len(live_plan.held),
            "ready_to_place": len(live_plan.assignments),
            "allocated": allocated_awaiting_checkin,
            "checked_in": checked_in_total,
            "total_active_allocations": total_active_allocations,
        },
        "arrivals": {
            "already_here": already_here,
            "next_6h": next_6h,
            "next_12h": next_12h,
            "next_24h": next_24h,
            "within_12h_total": already_here + next_12h,
        },
        "bed_status": bed_status,
        "budget_gauge": {
            "approved": float(approved or 0),
            "committed": float(committed),
            "remaining": float(remaining_budget or 0),
            "source": budget_source,
            "is_simulated_base": bool(budget_row.is_simulated) if budget_row else True,
            "budget_held_count": budget_held_count,
            "placeable_in_open_beds_count": placeable_in_open_beds_count,
            "cheapest_mix_for_open_beds_cost": float(cheapest_mix_open_beds_cost),
            "extra_needed_for_open_beds": float(extra_needed_open_beds),
            "overflow_beyond_open_beds_count": max(0, budget_held_count - placeable_in_open_beds_count),
            "cheapest_mix_all_budget_held_cost": float(cheapest_mix_all_cost),
            "extra_needed_all_budget_held": float(extra_needed_all),
            "cheapest_external_rate": float(cheapest_ext_rate or 0),
            "overrides": budget_overrides,
        },
        "decision_queue": decision_queue,
        "housing_summary": {
            "requesters_blank_gender": req_blank,
            "requesters_prefer_not_to_say": req_pnts,
            "requesters_non_binary": req_nb,
            "requesters_unmapped_total": req_blank + req_pnts + req_nb,
            "unallocated_blank_gender": unalloc_blank,
            "unallocated_unmapped_total": unalloc_unmapped,
            "outreach_sent_count": outreach_sent_count,
            "preferences_recorded": dict(pref_counts),
            "gender_neutral_rooms": gender_neutral_rooms_list,
            "outreach_question": OUTREACH_QUESTION,
        },
        "accessibility_summary": {
            "total_requesters_with_need": sum(x["total_requesters"] for x in by_need),
            "by_need": by_need,
            "external_call_sheets": [h for h in hostels.values() if h["kind"] == "external"],
        },
        "minors_summary": {
            "policy": festival.minor_housing_policy,
            "policy_set_by": festival.minor_policy_set_by,
            "total_minors_in_pack": total_minors_pack,
            "minors_requiring_accommodation": sum(1 for p in all_requesters if p.age is not None and p.age < MINOR_AGE),
            "minors_on_campus": minors_on_campus,
            "minors_external_unconfirmed": minors_ext_unconfirmed,
            "minors_external_confirmed": minors_ext_confirmed,
            "minors_unallocated": len(unalloc_minors),
            "people_needing_confirmation": minors_needing_confirmation[:80],
        },
        "date_errors": date_errors,
    }


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
    if room.gender_policy == "gender_neutral":
        room.policy_confirmed_by = user.name
    db.add(room)
    record(db, user, festival.id, "room.create", "room", body.code, f"{user.name} added room {body.code} to {hostel.name}")
    db.commit()
    return {"code": room.code}


def _build_room_map(festival: Festival, db: Session) -> list[dict[str, Any]]:
    occupied = occupied_counts(db, festival.id)
    hostels: dict[int, dict[str, Any]] = {}
    for hostel in db.scalars(select(Hostel).where(Hostel.festival_id == festival.id).order_by(Hostel.kind.desc(), Hostel.code)):
        hostels[hostel.id] = {
            "code": hostel.code,
            "name": hostel.name,
            "kind": hostel.kind,
            "status": hostel.status,
            "cost_per_bed_night": hostel.cost_per_bed_night,
            "distance_km": hostel.distance_km,
            "transport_available": hostel.transport_available,
            "accessibility_status": hostel.accessibility_status or "unknown",
            "step_free_entry": hostel.step_free_entry,
            "ground_floor_rooms": hostel.ground_floor_rooms,
            "accessible_bathrooms": hostel.accessible_bathrooms,
            "has_lift": hostel.has_lift,
            "accessibility_confirmed_by": hostel.accessibility_confirmed_by,
            "rooms": [],
        }
    rows = db.execute(
        select(Room, Hostel)
        .join(Hostel, Hostel.id == Room.hostel_id)
        .where(Room.festival_id == festival.id)
        .order_by(Hostel.kind.desc(), Hostel.code, Room.floor.nulls_last(), Room.code)
    )
    for room, hostel in rows:
        h = hostels[hostel.id]
        h["rooms"].append({
            "code": room.code,
            "floor": room.floor,
            "capacity": room.capacity,
            "occupied": occupied.get(room.id, 0),
            "gender_policy": room.gender_policy,
            "policy_confirmed_by": room.policy_confirmed_by,
            "accessible": room.accessible,
            "near_washroom": room.near_washroom,
            "visual_assistance": room.visual_assistance,
            "status": room.status,
        })
    return list(hostels.values())


@router.get("/room-map", dependencies=[Depends(require("accommodation.read"))])
def room_map(festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    return _build_room_map(festival, db)


@router.get("/room_map", dependencies=[Depends(require("accommodation.read"))])
def room_map_alias(festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    return _build_room_map(festival, db)


@router.get("/rooms/{room_code}", dependencies=[Depends(require("accommodation.read"))])
def room_detail(room_code: str, festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    room = by_code(db, Room, festival.id, room_code)
    hostel = db.get(Hostel, room.hostel_id)
    contingents = contingent_codes(db, festival.id)
    rows = db.execute(
        select(AccommodationAllocation, Participant)
        .join(Participant, Participant.id == AccommodationAllocation.participant_id)
        .where(AccommodationAllocation.festival_id == festival.id, AccommodationAllocation.room_id == room.id, ACTIVE)
        .order_by(Participant.code)
    ).all()
    occupants = [
        {
            "allocation_id": a.id,
            "participant": p.code,
            "name": p.full_name,
            "college": p.college,
            "contingent": contingents.get(p.contingent_id, "none"),
            "status": a.status,
            "check_in_at": a.check_in_at,
            "check_out_at": a.check_out_at,
            "is_minor": bool(p.age is not None and p.age < MINOR_AGE),
        }
        for a, p in rows
    ]
    return {
        "code": room.code,
        "hostel": hostel.name if hostel else "",
        "hostel_code": hostel.code if hostel else "",
        "kind": hostel.kind if hostel else "on_campus",
        "floor": room.floor,
        "gender_policy": room.gender_policy,
        "policy_confirmed_by": room.policy_confirmed_by,
        "capacity": room.capacity,
        "occupied": len(occupants),
        "available": max(0, room.capacity - len(occupants)),
        "accessible": room.accessible,
        "near_washroom": room.near_washroom,
        "visual_assistance": room.visual_assistance,
        "status": room.status,
        "accessibility_status": hostel.accessibility_status if hostel else "unknown",
        "occupants": occupants,
    }


@router.get("/rooms", dependencies=[Depends(require("accommodation.read"))])
def list_rooms(page: int = Query(1, ge=1), hostel: str | None = None, q: str | None = Query(None, max_length=40), gender: str | None = None,
               accessible: bool | None = None, free_only: bool = False, sort: str | None = None, festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
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
    hostel_map = {h.id: h for h in db.scalars(select(Hostel).where(Hostel.festival_id == festival.id))}

    def row(r: Room) -> dict[str, Any]:
        used = occupied.get(r.id, 0)
        h = hostel_map[r.hostel_id]
        return {
            "code": r.code, "hostel": h.name, "hostel_code": h.code, "kind": h.kind, "floor": r.floor,
            "gender_policy": r.gender_policy, "policy_confirmed_by": r.policy_confirmed_by,
            "capacity": r.capacity, "occupied": used, "available": max(0, r.capacity - used),
            "accessible": r.accessible, "near_washroom": r.near_washroom, "visual_assistance": r.visual_assistance,
            "accessibility_status": h.accessibility_status or "unknown", "status": r.status,
        }
    used = select(func.count()).select_from(AccommodationAllocation).where(AccommodationAllocation.room_id == Room.id, ACTIVE).scalar_subquery()
    columns = {"code": Room.code, "hostel": Hostel.name, "floor": Room.floor, "policy": Room.gender_policy, "accessible": Room.accessible,
               "occupied": used, "free": Room.capacity - used}
    return paged(db, ordered(query, sort, columns, Room.code), page, row)


@router.get("/allocations", dependencies=[Depends(require("accommodation.read"))])
def list_allocations(
    page: int = Query(1, ge=1),
    q: str | None = Query(None, max_length=80),
    room: str | None = None,
    status: str | None = None,
    contingent: str | None = None,
    under_18: bool = False,
    sort: str | None = None,
    festival: Festival = Depends(festival_access),
    db: Session = Depends(get_db),
):
    query = (
        select(AccommodationAllocation)
        .join(Participant, Participant.id == AccommodationAllocation.participant_id)
        .join(Room, Room.id == AccommodationAllocation.room_id)
        .where(AccommodationAllocation.festival_id == festival.id, ACTIVE)
    )
    if q:
        query = query.where(or_(Participant.code.ilike(like(q), escape="\\"), Participant.full_name.ilike(like(q), escape="\\"), Participant.college.ilike(like(q), escape="\\")))
    if room:
        query = query.where(Room.code == room)
    if status:
        query = query.where(AccommodationAllocation.status == status)
    if contingent:
        query = query.join(Contingent, Contingent.id == Participant.contingent_id).where(Contingent.code == contingent)
    if under_18:
        query = query.where(Participant.age < MINOR_AGE)

    contingents = contingent_codes(db, festival.id)
    hostels = {h.id: h for h in db.scalars(select(Hostel).where(Hostel.festival_id == festival.id))}
    people: dict[int, Participant] = {}
    rooms: dict[int, Room] = {}

    def row(a: AccommodationAllocation) -> dict[str, Any]:
        p = people.setdefault(a.participant_id, db.get(Participant, a.participant_id))
        r = rooms.setdefault(a.room_id, db.get(Room, a.room_id))
        h = hostels.get(r.hostel_id)
        return {
            "id": a.id, "participant": p.code, "name": p.full_name,
            "contingent": contingents.get(p.contingent_id, "none"), "college": p.college,
            "age": p.age, "is_minor": bool(p.age is not None and p.age < MINOR_AGE),
            "minor_supervision_status": p.minor_supervision_status,
            "room": r.code, "hostel": h.name if h else "", "kind": h.kind if h else "on_campus",
            "status": a.status, "check_in_at": a.check_in_at, "check_out_at": a.check_out_at,
            "relaxed_constraints": a.relaxed_constraints, "override_reason": a.override_reason,
        }
    columns = {"name": Participant.full_name, "room": Room.code, "status": AccommodationAllocation.status}
    return paged(db, ordered(query, sort, columns, Participant.code), page, row)


@router.get("/unallocated", dependencies=[Depends(require("accommodation.read"))])
def unallocated(
    page: int = Query(1, ge=1),
    q: str | None = Query(None, max_length=80),
    arrival_window: str | None = None,
    cause: str | None = None,
    contingent: str | None = None,
    under_18: bool = False,
    sort: str | None = None,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    sensitive = "participants.read_sensitive" in permissions_of(user)
    contingents = contingent_codes(db, festival.id)

    # Compute held reasons from live engine so each row in "Still need a bed" shows why they are waiting
    _, _, live_plan, _ = build_plan(db, festival, None if cause == "proposed" else effective_limit(db, festival, None))
    reason_by_code = {h["code"]: h["reason"] for h in live_plan.held}

    query = unallocated_query(festival.id)
    if q:
        query = query.where(or_(Participant.code.ilike(like(q), escape="\\"), Participant.full_name.ilike(like(q), escape="\\"), Participant.college.ilike(like(q), escape="\\")))
    if contingent:
        query = query.join(Contingent, Contingent.id == Participant.contingent_id).where(Contingent.code == contingent)
    if under_18:
        query = query.where(Participant.age < MINOR_AGE)
    if arrival_window == "already_here":
        query = query.where(Participant.arrival_at.is_not(None), Participant.arrival_at <= now)
    elif arrival_window == "6h":
        query = query.where(Participant.arrival_at.is_not(None), Participant.arrival_at > now, Participant.arrival_at <= now + timedelta(hours=6))
    elif arrival_window == "12h":
        query = query.where(Participant.arrival_at.is_not(None), Participant.arrival_at > now, Participant.arrival_at <= now + timedelta(hours=12))
    elif arrival_window == "24h":
        query = query.where(Participant.arrival_at.is_not(None), Participant.arrival_at > now, Participant.arrival_at <= now + timedelta(hours=24))
    elif arrival_window == "within_6h":
        query = query.where(Participant.arrival_at.is_not(None), Participant.arrival_at <= now + timedelta(hours=6))
    elif arrival_window == "within_12h":
        query = query.where(Participant.arrival_at.is_not(None), Participant.arrival_at <= now + timedelta(hours=12))
    elif arrival_window == "within_24h":
        query = query.where(Participant.arrival_at.is_not(None), Participant.arrival_at <= now + timedelta(hours=24))

    if cause:
        matching_codes = [code for code, r in reason_by_code.items() if _cause_key(r)[0] == cause]
        query = query.where(Participant.code.in_(matching_codes if matching_codes else ["__none__"]))

    columns = {"code": Participant.code, "name": Participant.full_name, "college": Participant.college, "arrival": Participant.arrival_at}
    return paged(db, ordered(query, sort, columns, Participant.arrival_at.asc().nulls_last()), page, lambda p: {
        "code": p.code,
        "name": p.full_name,
        "college": p.college,
        "contingent": contingents.get(p.contingent_id, "none"),
        "arrival_at": p.arrival_at,
        "departure_at": p.departure_at,
        "age": p.age,
        "is_minor": bool(p.age is not None and p.age < MINOR_AGE),
        "minor_supervision_status": p.minor_supervision_status,
        "gender": p.gender_as_given if sensitive else None,
        "housing_preference": p.housing_preference if sensitive else None,
        "housing_outreach_sent_at": p.housing_outreach_sent_at,
        "accessibility": p.accessibility_needs if sensitive else None,
        "held_reason": _redact_reason(reason_by_code.get(p.code, "Ready to place in next preview"), sensitive),
        "restricted": not sensitive,
    })


@router.post("/plan", dependencies=[Depends(require("accommodation.read"))])
def preview_plan(body: PlanIn, festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    limit = effective_limit(db, festival, body.budget_limit)
    people, _, plan, hash_value = build_plan(db, festival, limit)
    approved = hospitality_budget(db, festival.id)
    names = dict(db.execute(select(Participant.code, Participant.full_name).where(Participant.festival_id == festival.id)).all())
    contingents = contingent_codes(db, festival.id)
    cont_by_code = {
        code: contingents.get(cid, "none")
        for code, cid in db.execute(select(Participant.code, Participant.contingent_id).where(Participant.festival_id == festival.id)).all()
    }
    assignments_preview = [
        {
            "code": a["code"],
            "name": names.get(a["code"], a["code"]),
            "contingent": cont_by_code.get(a["code"], "none"),
            "room": a["room"],
            "hostel": a["hostel"],
            "kind": a["kind"],
            "relaxed": a["relaxed"],
        }
        for a in plan.assignments[:250]
    ]
    return {
        "summary": summarize(plan, len(people)),
        "state_hash": hash_value,
        "budget_limit": limit,
        "approved_budget": approved,
        "already_committed": external_commitment(db, festival.id),
        "over_approved_budget": bool(approved is not None and limit is not None and limit > approved),
        "assignments_preview": assignments_preview,
    }


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
    has_overrides = over or any(a["relaxed"] for a in plan.assignments)
    db.add_all(
        AccommodationAllocation(
            festival_id=festival.id,
            participant_id=a["participant_id"],
            room_id=a["room_id"],
            relaxed_constraints=a["relaxed"],
            override_reason=body.reason if (over or (body.reason and a["relaxed"])) else None,
        )
        for a in plan.assignments
    )
    result = summarize(plan, len(people))
    record(
        db, user, festival.id, "accommodation.plan", "accommodation", "plan",
        f"{user.name} allocated {result['placed']:,} beds; {result['held']:,} held for review",
        after=result, reason=body.reason if has_overrides else None,
    )
    db.commit()
    return result


@router.post("/budget-override", dependencies=[Depends(require("accommodation.write"))])
def request_extra_budget(body: BudgetOverrideIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    dept = db.scalar(select(Department).where(Department.festival_id == festival.id, Department.name == "Hospitality"))
    if not dept:
        dept = Department(festival_id=festival.id, name="Hospitality")
        db.add(dept)
        db.flush()
    budget = db.scalar(select(Budget).where(Budget.festival_id == festival.id, Budget.department_id == dept.id).with_for_update())
    before_amt = budget.approved_amount if budget else Decimal(0)
    if not budget:
        budget = Budget(festival_id=festival.id, department_id=dept.id, approved_amount=body.amount, is_simulated=False)
        db.add(budget)
    else:
        budget.approved_amount = before_amt + body.amount
    after_amt = budget.approved_amount
    record(
        db, user, festival.id, "accommodation.budget_override", "budget", "Hospitality",
        f"{user.name} added ₹{body.amount:,.0f} extra Hospitality budget (₹{before_amt:,.0f} → ₹{after_amt:,.0f})",
        before={"approved_amount": float(before_amt)},
        after={"approved_amount": float(after_amt), "added_amount": float(body.amount)},
        reason=body.reason.strip(),
    )
    db.commit()
    return {"approved_budget": float(after_amt), "added_amount": float(body.amount), "reason": body.reason.strip()}


@router.post("/housing-outreach", dependencies=[Depends(require("accommodation.write"))])
def send_housing_outreach(body: HousingOutreachIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    sensitive = "participants.read_sensitive" in permissions_of(user)
    query = select(Participant).where(
        Participant.festival_id == festival.id,
        Participant.deleted_at.is_(None),
        Participant.accommodation_required.is_(True),
    )
    if body.scope == "codes":
        if not body.participant_codes:
            raise HTTPException(422, "Select at least one participant")
        query = query.where(Participant.code.in_(body.participant_codes))
    elif body.scope == "blank_only":
        query = query.where(Participant.gender_as_given.is_(None), Participant.housing_preference.is_(None))
    else:
        query = query.where(
            or_(Participant.gender_as_given.is_(None), Participant.gender_as_given.in_(("Prefer not to say", "Non-binary"))),
            Participant.housing_preference.is_(None),
        )
    targets = list(db.scalars(query.order_by(Participant.code)))
    for p in targets:
        p.housing_outreach_sent_at = now

    # Log to Communication module as an internal Hospitality operational dispatch (never routed via contingent leaders)
    announcement = Announcement(
        festival_id=festival.id,
        created_by_id=user.id,
        title=f"Direct private housing inquiry queued ({len(targets):,} participants)",
        body=(
            f"1-to-1 private outreach prepared for {len(targets):,} participants ({body.scope}). "
            f"Question sent directly to participant contact (never via contingent leaders): \"{OUTREACH_QUESTION}\""
        ),
        audience_type="department",
        audience_ref="Hospitality",
        is_simulated=False,
    )
    db.add(announcement)
    record(
        db, user, festival.id, "accommodation.housing_outreach", "accommodation", "housing_outreach",
        f"{user.name} sent private housing preference outreach to {len(targets):,} participants (direct, bypassing contingent leaders)",
        after={"count": len(targets), "scope": body.scope},
    )
    db.commit()

    drafts = [
        {
            "code": p.code,
            "name": p.full_name,
            "email": p.email if sensitive else "Restricted",
            "phone": p.phone if sensitive else "Restricted",
            "message": f"Hi {p.full_name}, for your Aarohan stay: {OUTREACH_QUESTION}",
            "sent_at": now,
        }
        for p in targets[:30]
    ]
    return {
        "count": len(targets),
        "scope": body.scope,
        "question": OUTREACH_QUESTION,
        "privacy_guarantee": "Sent directly to individual participant contacts. Contingent leaders receive only aggregate unresolved counts.",
        "drafts_sample": drafts,
    }


@router.get("/housing-outreach.csv", dependencies=[Depends(require("accommodation.read"))])
def download_housing_outreach_csv(
    scope: str = Query("blank_only", pattern="^(blank_only|all_unmapped)$"),
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    sensitive = "participants.read_sensitive" in permissions_of(user)
    query = select(Participant).where(
        Participant.festival_id == festival.id,
        Participant.deleted_at.is_(None),
        Participant.accommodation_required.is_(True),
        Participant.housing_preference.is_(None),
    )
    if scope == "blank_only":
        query = query.where(Participant.gender_as_given.is_(None))
    else:
        query = query.where(or_(Participant.gender_as_given.is_(None), Participant.gender_as_given.in_(("Prefer not to say", "Non-binary"))))

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["participant_code", "full_name", "direct_email", "direct_phone", "private_question", "options", "routing_policy"])
    for p in db.scalars(query.order_by(Participant.code)):
        writer.writerow([
            p.code,
            p.full_name,
            (p.email or "") if sensitive else "RESTRICTED",
            (p.phone or "") if sensitive else "RESTRICTED",
            OUTREACH_QUESTION,
            "boys_block | girls_block | gender_neutral | talk_to_organizer",
            "DIRECT_TO_PARTICIPANT_ONLY_NEVER_VIA_CONTINGENT_LEADER",
        ])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="housing_private_outreach_{scope}.csv"'},
    )


@router.post("/housing-preference", dependencies=[Depends(require("accommodation.write"))])
def record_housing_preference(body: HousingPreferenceIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = list(db.scalars(select(Participant).where(Participant.festival_id == festival.id, Participant.code.in_(body.participant_codes))))
    if not rows:
        raise HTTPException(404, "No matching participants found")
    new_val = None if body.housing_preference == "clear" else body.housing_preference
    for p in rows:
        # NEVER overwrite p.gender_as_given!
        p.housing_preference = new_val
    entity_id = rows[0].code if len(rows) == 1 else f"batch({len(rows)})"
    record(
        db, user, festival.id, "accommodation.housing_preference", "participant", entity_id,
        f"{user.name} recorded housing_preference='{body.housing_preference}' for {len(rows):,} participant(s) (gender_as_given preserved)",
        after={"codes": [p.code for p in rows[:25]], "count": len(rows), "housing_preference": new_val},
        reason=body.note,
    )
    db.commit()
    return {"updated": len(rows), "housing_preference": new_val}


@router.post("/gender-neutral-rooms", dependencies=[Depends(require("accommodation.write"))])
def register_gender_neutral_rooms(body: GenderNeutralRoomsIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    rooms = list(db.scalars(select(Room).where(Room.festival_id == festival.id, Room.code.in_(body.room_codes))))
    if not rooms:
        raise HTTPException(404, "No matching rooms found")
    occupied = occupied_counts(db, festival.id)
    for r in rooms:
        r.gender_policy = "gender_neutral"
        r.policy_confirmed_by = body.confirmed_by.strip()
    total_beds = sum(r.capacity for r in rooms)
    free_beds = sum(max(0, r.capacity - occupied.get(r.id, 0)) for r in rooms)
    record(
        db, user, festival.id, "room.gender_neutral_register", "room", ",".join(r.code for r in rooms[:5]),
        f"{user.name} registered {len(rooms)} gender-neutral/private room(s) ({total_beds} beds, confirmed by {body.confirmed_by.strip()})",
        after={"rooms": [r.code for r in rooms], "confirmed_by": body.confirmed_by.strip(), "total_beds": total_beds, "free_beds": free_beds},
        reason=body.reason.strip(),
    )
    db.commit()
    return {"registered_rooms": [r.code for r in rooms], "total_beds": total_beds, "free_beds": free_beds}


@router.post("/hostels/{hostel_code}/accessibility-call-sheet", dependencies=[Depends(require("accommodation.write"))])
def update_accessibility_call_sheet(
    hostel_code: str,
    body: CallSheetIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    hostel = by_code(db, Hostel, festival.id, hostel_code)
    before = {
        "accessibility_status": hostel.accessibility_status,
        "step_free_entry": hostel.step_free_entry,
        "ground_floor_rooms": hostel.ground_floor_rooms,
        "accessible_bathrooms": hostel.accessible_bathrooms,
        "has_lift": hostel.has_lift,
    }
    if body.reset_unknown or (
        body.step_free_entry is None and body.ground_floor_rooms is None and body.accessible_bathrooms is None and body.has_lift is None
    ):
        hostel.accessibility_status = "unknown"
        hostel.step_free_entry = None
        hostel.ground_floor_rooms = None
        hostel.accessible_bathrooms = None
        hostel.has_lift = None
        hostel.accessibility_confirmed_by = None
        hostel.accessibility_confirmed_at = None
    else:
        hostel.step_free_entry = body.step_free_entry
        hostel.ground_floor_rooms = body.ground_floor_rooms or 0
        hostel.accessible_bathrooms = body.accessible_bathrooms or 0
        hostel.has_lift = body.has_lift
        hostel.accessibility_confirmed_by = body.confirmed_by.strip()
        hostel.accessibility_confirmed_at = datetime.now(timezone.utc)
        has_any = bool(
            body.step_free_entry
            or (body.ground_floor_rooms and body.ground_floor_rooms > 0)
            or (body.accessible_bathrooms and body.accessible_bathrooms > 0)
            or body.has_lift
        )
        hostel.accessibility_status = "declared" if has_any else "confirmed_none"

    after = {
        "accessibility_status": hostel.accessibility_status,
        "step_free_entry": hostel.step_free_entry,
        "ground_floor_rooms": hostel.ground_floor_rooms,
        "accessible_bathrooms": hostel.accessible_bathrooms,
        "has_lift": hostel.has_lift,
        "confirmed_by": hostel.accessibility_confirmed_by,
    }
    record(
        db, user, festival.id, "hostel.accessibility_call_sheet", "hostel", hostel.code,
        f"{user.name} recorded accessibility call sheet for {hostel.name}: {hostel.accessibility_status.upper()}",
        before=before, after=after,
    )
    db.commit()
    return after


@router.post("/rooms/{room_code}/accessibility-features", dependencies=[Depends(require("accommodation.write"))])
def update_room_accessibility_features(
    room_code: str,
    body: RoomAccessFeaturesIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    room = by_code(db, Room, festival.id, room_code)
    before = {"accessible": room.accessible, "near_washroom": room.near_washroom, "visual_assistance": room.visual_assistance}
    if body.accessible is not None:
        room.accessible = body.accessible
    if body.near_washroom is not None:
        room.near_washroom = body.near_washroom
    if body.visual_assistance is not None:
        room.visual_assistance = body.visual_assistance
    room.policy_confirmed_by = body.confirmed_by.strip()
    after = {"accessible": room.accessible, "near_washroom": room.near_washroom, "visual_assistance": room.visual_assistance, "confirmed_by": room.policy_confirmed_by}
    record(
        db, user, festival.id, "room.accessibility_features", "room", room.code,
        f"{user.name} updated accessibility features for room {room.code}",
        before=before, after=after,
    )
    db.commit()
    return after


@router.post("/fix-stay-dates", dependencies=[Depends(require("accommodation.write"))])
def fix_stay_dates(body: FixStayDatesIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    if body.departure_at <= body.arrival_at:
        raise HTTPException(422, "Departure must be after arrival")
    p = by_code(db, Participant, festival.id, body.participant_code)
    before = {
        "arrival_at": p.arrival_at.isoformat() if p.arrival_at else None,
        "departure_at": p.departure_at.isoformat() if p.departure_at else None,
    }
    p.arrival_at = body.arrival_at
    p.departure_at = body.departure_at
    after = {
        "arrival_at": p.arrival_at.isoformat(),
        "departure_at": p.departure_at.isoformat(),
    }
    for issue in db.scalars(
        select(DataIssue).where(
            DataIssue.festival_id == festival.id,
            DataIssue.entity_code == p.code,
            DataIssue.issue == "departure_before_arrival",
        )
    ):
        issue.status = "resolved"
    record(
        db, user, festival.id, "participant.fix_stay_dates", "participant", p.code,
        f"{user.name} corrected stay dates for {p.code} ({p.full_name})",
        before=before, after=after, reason=body.reason.strip(),
    )
    db.commit()
    return {"code": p.code, "arrival_at": p.arrival_at, "departure_at": p.departure_at}


@router.post("/minor-policy", dependencies=[Depends(require("accommodation.write"))])
def set_minor_policy(body: MinorPolicyIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    before = {"minor_housing_policy": festival.minor_housing_policy, "minor_policy_set_by": festival.minor_policy_set_by}
    festival.minor_housing_policy = body.policy
    festival.minor_policy_set_by = user.name
    after = {"minor_housing_policy": festival.minor_housing_policy, "minor_policy_set_by": festival.minor_policy_set_by}
    record(
        db, user, festival.id, "accommodation.minor_policy", "festival", festival.slug,
        f"{user.name} set minor housing policy to '{body.policy}'",
        before=before, after=after, reason=body.reason.strip(),
    )
    db.commit()
    return after


@router.post("/minors/confirm-escort", dependencies=[Depends(require("accommodation.write"))])
def confirm_minor_escort(body: ConfirmEscortIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    query = select(Participant).where(Participant.festival_id == festival.id, Participant.deleted_at.is_(None), Participant.age < MINOR_AGE)
    if body.participant_codes:
        query = query.where(Participant.code.in_(body.participant_codes))
    elif body.contingent_code:
        cont = by_code(db, Contingent, festival.id, body.contingent_code)
        query = query.where(Participant.contingent_id == cont.id)
    else:
        raise HTTPException(422, "Provide participant_codes or contingent_code")
    minors = list(db.scalars(query))
    if not minors:
        raise HTTPException(404, "No matching under-18 participants found")
    note_text = f"Escort: {body.escort_name.strip()}" + (f" — {body.note.strip()}" if body.note else "")
    for p in minors:
        p.minor_supervision_status = "confirmed"
        p.minor_supervision_note = note_text[:240]
    target_id = body.contingent_code or (minors[0].code if len(minors) == 1 else f"batch({len(minors)})")
    record(
        db, user, festival.id, "accommodation.minor_escort_confirm", "participant", target_id,
        f"{user.name} confirmed escort/guardian arrangement ({body.escort_name.strip()}) for {len(minors)} under-18 participant(s)",
        after={"count": len(minors), "escort_name": body.escort_name.strip(), "codes": [p.code for p in minors[:25]]},
        reason=body.reason.strip(),
    )
    db.commit()
    return {"confirmed": len(minors), "escort_name": body.escort_name.strip()}


@router.get("/contingents", dependencies=[Depends(require("accommodation.read"))])
def list_contingents_housing(festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    contingents = list(db.scalars(select(Contingent).where(Contingent.festival_id == festival.id).order_by(Contingent.code)))
    participants = list(
        db.scalars(select(Participant).where(Participant.festival_id == festival.id, Participant.deleted_at.is_(None)))
    )
    alloc_rows = dict(
        db.execute(
            select(AccommodationAllocation.participant_id, Hostel.name)
            .join(Room, Room.id == AccommodationAllocation.room_id)
            .join(Hostel, Hostel.id == Room.hostel_id)
            .where(AccommodationAllocation.festival_id == festival.id, ACTIVE)
        ).all()
    )
    checked_in_pids = set(
        db.scalars(
            select(AccommodationAllocation.participant_id).where(
                AccommodationAllocation.festival_id == festival.id, AccommodationAllocation.status == "checked_in"
            )
        )
    )
    by_cont: dict[int, list[Participant]] = defaultdict(list)
    for p in participants:
        if p.contingent_id:
            by_cont[p.contingent_id].append(p)

    out = []
    for c in contingents:
        members = by_cont.get(c.id, [])
        req = [p for p in members if p.accommodation_required]
        placed = [p for p in req if p.id in alloc_rows]
        waiting = [p for p in req if p.id not in alloc_rows]
        locs = Counter(alloc_rows[p.id] for p in placed)
        unresolved_housing = sum(1 for p in waiting if not p.gender_as_given and not p.housing_preference)
        out.append({
            "code": c.code,
            "college": c.college,
            "leader": c.leader,
            "escort": c.escort,
            "strength_declared": c.strength_declared,
            "registered": len(members),
            "requested": len(req),
            "placed": len(placed),
            "checked_in": sum(1 for p in placed if p.id in checked_in_pids),
            "waiting": len(waiting),
            "minors": sum(1 for p in req if p.age is not None and p.age < MINOR_AGE),
            "unresolved_housing_count": unresolved_housing,
            "locations": [{"hostel": h, "count": n} for h, n in locs.most_common()],
            "is_split": len(locs) > 1,
        })
    return out


@router.get("/contingents/{contingent_code}", dependencies=[Depends(require("accommodation.read"))])
def contingent_housing_detail(
    contingent_code: str,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    sensitive = "participants.read_sensitive" in permissions_of(user)
    c = by_code(db, Contingent, festival.id, contingent_code)
    members = list(
        db.scalars(
            select(Participant)
            .where(Participant.festival_id == festival.id, Participant.deleted_at.is_(None), Participant.contingent_id == c.id)
            .order_by(Participant.code)
        )
    )
    allocs = {
        a.participant_id: (a, r, h)
        for a, r, h in db.execute(
            select(AccommodationAllocation, Room, Hostel)
            .join(Room, Room.id == AccommodationAllocation.room_id)
            .join(Hostel, Hostel.id == Room.hostel_id)
            .where(AccommodationAllocation.festival_id == festival.id, ACTIVE)
        ).all()
    }
    _, _, live_plan, _ = build_plan(db, festival, effective_limit(db, festival, None))
    reason_by_code = {h["code"]: h["reason"] for h in live_plan.held}

    placed_members = []
    waiting_members = []
    locations = Counter()
    waiting_causes = Counter()
    unresolved_housing_count = 0

    for p in members:
        if not p.accommodation_required:
            continue
        if p.id in allocs:
            a, r, h = allocs[p.id]
            locations[h.name] += 1
            placed_members.append({
                "code": p.code,
                "name": p.full_name,
                "age": p.age,
                "is_minor": bool(p.age is not None and p.age < MINOR_AGE),
                "minor_supervision_status": p.minor_supervision_status,
                "room": r.code,
                "hostel": h.name,
                "kind": h.kind,
                "floor": r.floor,
                "status": a.status,
                "check_in_at": a.check_in_at,
                "arrival_at": p.arrival_at,
                "departure_at": p.departure_at,
            })
        else:
            if not p.gender_as_given and not p.housing_preference:
                unresolved_housing_count += 1
            raw_r = reason_by_code.get(p.code, "Ready to place in next preview")
            disp_r = _redact_reason(raw_r, sensitive)
            waiting_causes[disp_r] += 1
            waiting_members.append({
                "code": p.code,
                "name": p.full_name,
                "age": p.age,
                "is_minor": bool(p.age is not None and p.age < MINOR_AGE),
                "minor_supervision_status": p.minor_supervision_status,
                "arrival_at": p.arrival_at,
                "departure_at": p.departure_at,
                "reason": disp_r,
                "gender": p.gender_as_given if sensitive else None,
                "housing_preference": p.housing_preference if sensitive else None,
                "accessibility": p.accessibility_needs if sensitive else None,
                "restricted": not sensitive,
            })

    # Check if leader or escort matches a member in the contingent
    leader_match = next((m for m in placed_members if c.leader and m["name"].lower() == c.leader.lower()), None)
    escort_match = next((m for m in placed_members if c.escort and m["name"].lower() == c.escort.lower()), None)
    primary_hostel = locations.most_common(1)[0][0] if locations else None

    leader_sleep = (
        f"{leader_match['room']} ({leader_match['hostel']})"
        if leader_match
        else (
            f"{c.leader} — not registered as a participant bed row; primary contingent block: {primary_hostel or 'none placed yet'}"
            if c.leader
            else "No leader listed in contingent pack"
        )
    )
    escort_sleep = (
        f"{escort_match['room']} ({escort_match['hostel']})"
        if escort_match
        else (
            f"{c.escort} — faculty/staff escort (no separate participant bed row in pack; contingent housed at: {', '.join(f'{h} ({n})' for h, n in locations.most_common()) or 'none placed yet'})"
            if c.escort
            else "No escort listed in contingent pack"
        )
    )

    return {
        "code": c.code,
        "college": c.college,
        "leader": c.leader,
        "escort": c.escort,
        "preferences": c.preferences,
        "strength_declared": c.strength_declared,
        "leader_sleep": leader_sleep,
        "escort_sleep": escort_sleep,
        "counts": {
            "registered": len(members),
            "requested": len(placed_members) + len(waiting_members),
            "placed": len(placed_members),
            "checked_in": sum(1 for m in placed_members if m["status"] == "checked_in"),
            "waiting": len(waiting_members),
            "minors_total": sum(1 for p in members if p.accommodation_required and p.age is not None and p.age < MINOR_AGE),
            "minors_unconfirmed": sum(
                1 for p in members
                if p.accommodation_required and p.age is not None and p.age < MINOR_AGE and p.minor_supervision_status != "confirmed"
            ),
            "unresolved_housing_count": unresolved_housing_count,
        },
        "locations": [{"hostel": h, "count": n} for h, n in locations.most_common()],
        "waiting_reasons": [{"reason": r, "count": n} for r, n in waiting_causes.most_common()],
        "placed_members": placed_members,
        "waiting_members": waiting_members,
    }


def room_problems(p: Participant, room: Room, hostel: Hostel, used: int, minor_policy: str | None = None) -> list[str]:
    problems = []
    if room.status != "available" or hostel.status != "available":
        problems.append(f"{room.code} is not available")
    if used >= room.capacity:
        problems.append(f"{room.code} is full")
    group = housing_group(p.gender_as_given, p.housing_preference)
    if group == "talk_to_organizer":
        problems.append("Participant asked to talk to an organizer before housing")
    elif room.gender_policy and group is None:
        problems.append("Gender is blank, and this room is gendered")
    elif room.gender_policy and room.gender_policy != "gender_neutral" and group != room.gender_policy:
        problems.append(f"{room.code} is a {room.gender_policy} room")
    need = p.accessibility_needs
    bed = Beds(
        room.id, room.code, hostel.name, hostel.kind, room.floor, room.gender_policy, room.accessible, 0,
        hostel.cost_per_bed_night, hostel.transport_available, hostel.distance_km,
        hostel.accessibility_status, hostel.step_free_entry, hostel.ground_floor_rooms or 0,
        hostel.accessible_bathrooms or 0, hostel.has_lift, room.near_washroom, room.visual_assistance,
    )
    if need and need != "None" and not suits_access(Person(p.id, p.code, "", None, need, None, None, None), bed):
        problems.append(f"Room does not meet: {need}")
    if hostel.kind == "external" and p.age is not None and p.age < MINOR_AGE and p.minor_supervision_status != "confirmed" and minor_policy == "on_campus_only":
        problems.append("Participant is under 18 and escort arrangement is not confirmed for off-campus stay")
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
    problems = room_problems(p, room, db.get(Hostel, room.hostel_id), used, festival.minor_housing_policy)
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
             if not room_problems(p, r, h, occupied.get(r.id, 0), festival.minor_housing_policy)]
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

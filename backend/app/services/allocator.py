"""Accommodation allocator. Pure logic: takes people and beds, returns a defensible plan.

Hard rules are never traded for a better score. Someone who cannot be placed safely is
held with a stated reason, and a person decides. See ASSUMPTIONS #10 to #14.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from math import ceil
from typing import Any

SEVERITY = {"Wheelchair access": 0, "Visual assistance": 1, "Ground floor / no stairs": 2, "Near washroom": 3}
MINOR_AGE = 18


@dataclass
class Person:
    id: int
    code: str
    contingent: str
    gender: str | None
    accessibility: str | None
    age: int | None
    arrival: datetime | None
    departure: datetime | None
    housing_preference: str | None = None
    minor_supervision_status: str | None = None


@dataclass
class Beds:
    id: int
    code: str
    hostel: str
    kind: str
    floor: int | None
    gender: str | None
    accessible: bool | None
    free: int
    cost: Decimal | None = None
    transport: bool | None = None
    distance: Decimal | None = None
    access_status: str | None = None
    step_free: bool | None = None
    ground_floor_beds: int = 0
    accessible_bathroom_beds: int = 0
    has_lift: bool | None = None
    near_washroom: bool | None = None
    visual_assistance: bool | None = None


@dataclass
class Plan:
    assignments: list[dict[str, Any]] = field(default_factory=list)
    held: list[dict[str, str]] = field(default_factory=list)
    splits: dict[str, int] = field(default_factory=dict)
    external_cost: Decimal = Decimal(0)


def housing_group(gender: str | None, housing_preference: str | None = None) -> str | None:
    """boys, girls, inclusive, talk_to_organizer, or None when unknown. Unknown is never guessed."""
    if housing_preference:
        mapped = {
            "boys_block": "boys",
            "girls_block": "girls",
            "gender_neutral": "inclusive",
            "talk_to_organizer": "talk_to_organizer",
        }.get(housing_preference)
        if mapped:
            return mapped
    return {"Male": "boys", "Female": "girls", "Non-binary": "inclusive", "Prefer not to say": "inclusive"}.get(gender or "")


def nights(p: Person) -> int | None:
    if not p.arrival or not p.departure or p.departure < p.arrival:
        return None
    return max(1, ceil((p.departure - p.arrival).total_seconds() / 86400))


def needs_access(p: Person) -> bool:
    return bool(p.accessibility) and p.accessibility != "None"


def suits_access(p: Person, room: Beds) -> bool:
    """Only verified facilities count. External pools have no stated accessibility until confirmed on the call sheet."""
    if room.accessible:
        return True
    need = p.accessibility
    if need == "Ground floor / no stairs":
        if room.kind == "on_campus" and room.floor == 0:
            return True
        if room.kind == "external" and room.access_status == "declared" and room.step_free and (room.ground_floor_beds > 0 or bool(room.has_lift)):
            return True
    elif need == "Wheelchair access":
        if room.kind == "external" and room.access_status == "declared" and room.step_free and (room.ground_floor_beds > 0 or bool(room.has_lift)) and room.accessible_bathroom_beds > 0:
            return True
    elif need == "Near washroom":
        if room.near_washroom:
            return True
        if room.kind == "external" and room.access_status == "declared" and room.accessible_bathroom_beds > 0:
            return True
    elif need == "Visual assistance":
        if room.visual_assistance:
            return True
        if room.kind == "external" and room.access_status == "declared" and room.step_free and (room.ground_floor_beds > 0 or bool(room.has_lift)):
            return True
    return False


def allowed_gender(group: str, room: Beds) -> bool:
    if group == "inclusive":
        return room.gender in (None, "gender_neutral")
    return room.gender in (None, group)


def _declared_access_cap(room: Beds) -> int:
    if room.kind != "external" or room.access_status != "declared":
        return 0
    explicit = max(room.ground_floor_beds or 0, room.accessible_bathroom_beds or 0)
    if explicit > 0:
        return min(room.free, explicit)
    if room.step_free and room.has_lift:
        return room.free
    return 0


def plan_allocation(people: list[Person], rooms: list[Beds], budget: Decimal | None, minor_policy: str | None = None) -> Plan:
    plan = Plan()
    remaining = {r.id: r.free for r in rooms}
    ext_access_remaining = {r.id: _declared_access_cap(r) for r in rooms if r.kind == "external"}
    on_campus = sorted((r for r in rooms if r.kind == "on_campus"), key=lambda r: r.code)
    external = sorted((r for r in rooms if r.kind == "external" and r.transport is not False), key=lambda r: (r.cost or 0, r.code))
    spent = Decimal(0)
    placed_in: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)

    def place(p: Person, room: Beds, group: str, relaxed: list[str]) -> None:
        remaining[room.id] -= 1
        placed_in[(p.contingent, group)][room.hostel] += 1
        plan.assignments.append({"participant_id": p.id, "code": p.code, "room_id": room.id, "room": room.code, "hostel": room.hostel, "kind": room.kind, "relaxed": relaxed})

    def try_external(p: Person, group: str, require_access: bool = False) -> str | None:
        """Place in the cheapest external bed that fits. Returns why not, or None when placed."""
        nonlocal spent
        stay = nights(p)
        if stay is None:
            return "Stay dates are invalid, so cost and check-in cannot be worked out"
        if minor_policy == "on_campus_only" and p.age is not None and p.age < MINOR_AGE and p.minor_supervision_status != "confirmed":
            return "Participant under 18 needs escort confirmation before off-campus placement"
        hit_budget = False
        for room in external:
            if remaining[room.id] <= 0:
                continue
            if require_access:
                if ext_access_remaining.get(room.id, 0) <= 0 or not suits_access(p, room):
                    continue
            cost = (room.cost or Decimal(0)) * stay
            if budget is not None and spent + cost > budget:
                hit_budget = True
                continue
            spent += cost
            if require_access:
                ext_access_remaining[room.id] -= 1
            relaxed = ["room_set_by_property"]
            if p.age is not None and p.age < MINOR_AGE and p.minor_supervision_status != "confirmed":
                relaxed.append("minor_off_campus")
            place(p, room, group, relaxed)
            return None
        return "External budget limit reached" if hit_budget else "No beds left"

    def hold(p: Person, reason: str) -> None:
        plan.held.append({"code": p.code, "reason": reason})

    usable: list[tuple[Person, str]] = []
    for p in people:
        group = housing_group(p.gender, p.housing_preference)
        if group == "talk_to_organizer":
            hold(p, "Requested to talk to an organizer about housing arrangement")
        elif group is None:
            hold(p, "Gender is blank. It must be confirmed before housing, not assumed")
        else:
            usable.append((p, group))

    # Scarcest resource first: accessible beds go to the most severe need (Wheelchair access first).
    access = sorted((x for x in usable if needs_access(x[0])), key=lambda x: (SEVERITY.get(x[0].accessibility or "", 9), x[0].code))
    for p, group in access:
        room = next((r for r in on_campus if remaining[r.id] > 0 and suits_access(p, r) and allowed_gender(group, r)), None)
        if room:
            place(p, room, group, [])
            continue
        # If any external property has declared matching accessible features on its call sheet, try placing there.
        matching_ext = next((r for r in external if ext_access_remaining.get(r.id, 0) > 0 and suits_access(p, r)), None)
        if matching_ext:
            ext_reason = try_external(p, group, require_access=True)
            if ext_reason is None:
                continue
            if ext_reason == "Stay dates are invalid, so cost and check-in cannot be worked out":
                hold(p, ext_reason)
                continue
            if ext_reason == "External budget limit reached":
                ext_access_remaining[matching_ext.id] -= 1
                hold(p, ext_reason)
                continue
        if group == "inclusive":
            hold(p, f"{p.accessibility}: Accessible rooms are gendered, so this needs a personal arrangement")
        else:
            hold(p, f"{p.accessibility}: No suitable accessible bed is left")

    # Keep each contingent's group together, largest groups first so they still fit in one block.
    groups: dict[tuple[str, str], list[Person]] = defaultdict(list)
    for p, group in usable:
        if not needs_access(p):
            groups[(p.contingent, group)].append(p)

    for (contingent, group), members in sorted(groups.items(), key=lambda g: (-len(g[1]), g[0])):
        # Minors go first so they get the on-campus beds.
        members.sort(key=lambda p: (p.age is None or p.age >= MINOR_AGE, p.code))
        for p in members:
            room = None
            if group != "inclusive":
                candidates = [r for r in on_campus if remaining[r.id] > 0 and allowed_gender(group, r)]
            else:
                candidates = [r for r in on_campus if remaining[r.id] > 0 and r.gender == "gender_neutral"]
            if candidates:
                used = placed_in[(contingent, group)]
                # Stay in a block the group already uses; rooms are in order, so people fill rooms together.
                room = next((r for r in candidates if r.hostel in used), candidates[0])
            if room:
                place(p, room, group, [])
                continue
            reason = try_external(p, group)
            if reason:
                hold(p, reason)

    plan.external_cost = spent
    plan.splits = {f"{c} ({g})": len(h) for (c, g), h in placed_in.items() if len(h) > 1}
    return plan


def summarize(plan: Plan, people: int) -> dict[str, Any]:
    held = Counter(h["reason"].split(":")[-1].strip() if ":" in h["reason"] else h["reason"] for h in plan.held)
    relaxed = Counter(r for a in plan.assignments for r in a["relaxed"])
    return {
        "people": people, "placed": len(plan.assignments), "held": len(plan.held),
        "on_campus": sum(a["kind"] == "on_campus" for a in plan.assignments),
        "external": sum(a["kind"] == "external" for a in plan.assignments),
        "external_cost": float(plan.external_cost), "contingents_split": len(plan.splits),
        "held_reasons": [{"reason": r, "count": n} for r, n in held.most_common()],
        "relaxed": [{"constraint": r, "count": n} for r, n in relaxed.most_common()],
        "splits": [{"group": g, "locations": n} for g, n in sorted(plan.splits.items(), key=lambda s: -s[1])[:12]],
    }

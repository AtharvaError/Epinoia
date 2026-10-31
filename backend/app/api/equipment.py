"""Inventory Control (refactor of Transportation): Events -> campus Logistics inventory.

Responsibility split:
- Events & Schedule: owns event records and raw equipment_needs text from events.csv.
- Venues & Resources: owns venue records and raw venue_requirements text from venues.csv.
- Inventory Control: owns the 18-item logistics catalogue, location balances, alias mappings,
  equipment requests, allocation, transfers, dispatch, receipt, returns, inspection, repair,
  failure handling, and the append-only movement ledger.
"""
import csv
import io
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.common import by_code, csv_safe
from app.auth.deps import current_user, festival_access, permissions_of, require
from app.database import get_db
from app.models import (
    EquipmentAlias,
    EquipmentAllocation,
    EquipmentRequest,
    EquipmentRequestLine,
    EquipmentTransfer,
    Event,
    Festival,
    InventoryLocation,
    MovementLedger,
    Resource,
    ResourceAllocation,
    User,
    Venue,
)
from app.services.audit import record

router = APIRouter(prefix="/api/festivals/{festival_id}/equipment", tags=["equipment"])

LIFECYCLE = ["REQUESTED", "APPROVED", "DISPATCHED", "IN_TRANSIT", "RECEIVED", "PARTIALLY_RECEIVED", "COMPLETED"]
STAGE_PERMISSION = {
    "APPROVED": "equipment.approve",
    "DISPATCHED": "equipment.approve",
    "IN_TRANSIT": "equipment.approve",
    "RECEIVED": "equipment.receive",
    "PARTIALLY_RECEIVED": "equipment.receive",
    "COMPLETED": "equipment.receive",
}
READINESS_ORDER = [
    "unavailable",
    "requires_inspection",
    "not_requested",
    "awaiting_approval",
    "pending_transfer",
    "partially_satisfied",
    "satisfied",
]

VENUE_EXACT_ALIASES = {"projector": "projector", "tables": "table"}
VENUE_AMBIGUOUS_HINTS = {
    "audio": "Venue requires audio — add specific items (e.g. mic, speaker, mixer) if not already installed.",
    "power": "Venue requires power — add extension cords or power strips if not already installed.",
    "stage": "Venue requires stage setup — add barricades, podium, or stage lighting if needed.",
    "lights": "Venue requires lights — add stage lighting if not already installed.",
    "seating": "Venue requires seating — add chairs or tables if not already installed.",
}
VENUE_NON_EQUIPMENT = {"open ground", "quiet room"}


def now() -> datetime:
    return datetime.now(timezone.utc)


def cond(r: Resource) -> str:
    return (r.condition or "").strip().lower()


def logistics_items(db: Session, fid: int) -> list[str]:
    return sorted(set(db.scalars(select(Resource.name).where(Resource.festival_id == fid, Resource.kind == "logistics"))))


def alias_map(db: Session, fid: int) -> dict[str, EquipmentAlias]:
    rows = db.scalars(select(EquipmentAlias).where(EquipmentAlias.festival_id == fid))
    return {a.token.strip().lower(): a for a in rows}


def log_movement(
    db: Session,
    fid: int,
    user: User | None,
    *,
    action: str,
    item: str,
    quantity: int,
    resource_code: str | None = None,
    from_location: str | None = None,
    to_location: str | None = None,
    from_condition: str | None = None,
    to_condition: str | None = None,
    event_code: str | None = None,
    request_code: str | None = None,
    transfer_id: int | None = None,
    status: str = "completed",
    reason: str | None = None,
    idempotency_key: str | None = None,
) -> MovementLedger:
    entry = MovementLedger(
        festival_id=fid,
        action=action,
        item=item,
        resource_code=resource_code,
        qty=quantity,
        from_location=from_location,
        to_location=to_location,
        from_condition=from_condition,
        to_condition=to_condition,
        event_code=event_code,
        request_code=request_code,
        transfer_id=transfer_id,
        status=status,
        actor_id=user.id if user else None,
        actor_name=user.name if user else "System",
        reason=reason,
        idempotency_key=idempotency_key,
    )
    db.add(entry)
    return entry


def is_declared_none(text: str | None) -> bool:
    return bool(text and text.strip().lower() in ("no special equipment", "none"))


def parse_needs(text: str | None, items: list[str], aliases: dict[str, EquipmentAlias] | None = None) -> list[dict[str, Any]]:
    """Reads events.csv equipment_needs. Quantities only when the text states them; never invented."""
    if not text or not text.strip() or is_declared_none(text):
        return []
    known = {i.lower(): i for i in items}
    alias_lookup = aliases or {}
    out = []
    for part in text.split(";"):
        raw = part.strip()
        if not raw:
            continue
        m = re.match(r"^(\d+)\s+(.+)$", raw.lower())
        qty, token = (int(m[1]), m[2].strip()) if m else (None, raw.lower().strip())
        alias_row = alias_lookup.get(token)
        if alias_row and alias_row.target_item and alias_row.target_item.lower() in known:
            match = known[alias_row.target_item.lower()]
            alias_verified = alias_row.verified
            matched_via = f"alias:{alias_row.token}->{alias_row.target_item}"
        else:
            match = known.get(token) or (known.get(token[:-1]) if token.endswith("s") else None)
            alias_verified = match is not None
            matched_via = "exact" if match == known.get(token) else ("plural_rule" if match else None)
        out.append({
            "raw": raw,
            "token": token,
            "item": match,
            "quantity": qty,
            "matched": match is not None,
            "alias_verified": alias_verified,
            "matched_via": matched_via,
            "quantity_source": "from events.csv" if qty is not None else "needs organizer confirmation",
        })
    return out


def parse_venue_requirements(text: str | None, items: list[str]) -> dict[str, list[dict[str, Any]]]:
    """Classifies venue_requirements into exact suggestions, organizer hints, and ignored non-equipment."""
    suggestions: list[dict[str, Any]] = []
    hints: list[dict[str, Any]] = []
    ignored: list[dict[str, Any]] = []
    if not text or not text.strip():
        return {"suggestions": suggestions, "hints": hints, "ignored": ignored}
    known = {i.lower(): i for i in items}
    for part in re.split(r"[;,]", text):
        raw = part.strip()
        if not raw:
            continue
        low = raw.lower()
        if low in VENUE_NON_EQUIPMENT:
            ignored.append({"token": raw, "reason": "Venue characteristic, not movable equipment"})
        elif low in VENUE_EXACT_ALIASES and VENUE_EXACT_ALIASES[low] in known:
            target = known[VENUE_EXACT_ALIASES[low]]
            suggestions.append({
                "token": raw,
                "item": target,
                "quantity": None,
                "note": f"Suggested from venue requirements ('{raw}'). Enter quantity if not already installed.",
            })
        elif low in VENUE_AMBIGUOUS_HINTS:
            hints.append({"token": raw, "hint": VENUE_AMBIGUOUS_HINTS[low]})
        elif low in known:
            suggestions.append({
                "token": raw,
                "item": known[low],
                "quantity": None,
                "note": f"Suggested from venue requirements ('{raw}'). Enter quantity if needed.",
            })
        else:
            hints.append({"token": raw, "hint": f"Venue mentions '{raw}' — add specific equipment items if needed."})
    return {"suggestions": suggestions, "hints": hints, "ignored": ignored}


def location_lookup(db: Session, fid: int) -> dict[str, InventoryLocation]:
    return {loc.name: loc for loc in db.scalars(select(InventoryLocation).where(InventoryLocation.festival_id == fid))}


def availability(
    db: Session,
    fid: int,
    item: str,
    required: int,
    destination: str | None,
    exclude_request: int | None = None,
) -> dict[str, Any]:
    rows = list(
        db.scalars(
            select(Resource)
            .where(
                Resource.festival_id == fid,
                Resource.kind == "logistics",
                func.lower(Resource.name) == item.lower(),
            )
            .order_by(Resource.code)
        )
    )
    loc_map = location_lookup(db, fid)
    pending_reqs = list(
        db.scalars(
            select(EquipmentRequestLine)
            .join(EquipmentRequest, EquipmentRequest.id == EquipmentRequestLine.request_id)
            .where(
                EquipmentRequestLine.festival_id == fid,
                func.lower(EquipmentRequestLine.item) == item.lower(),
                EquipmentRequest.status == "submitted",
            )
        )
    )
    if exclude_request:
        pending_reqs = [r for r in pending_reqs if r.request_id != exclude_request]
    pending_demand_total = sum(r.confirmed_qty for r in pending_reqs)

    lines = []
    for r in rows:
        c = cond(r)
        good = c == "good"
        loc_info = loc_map.get(r.location or "")
        pos_unverified = bool(loc_info.position_unverified) if loc_info else (r.location == "Van 1")
        pos_note = loc_info.position_note if loc_info else None
        deployable = max(0, r.quantity - r.reserved_quantity) if good else 0
        lines.append({
            "code": r.code,
            "location": r.location,
            "condition": r.condition,
            "quantity": r.quantity,
            "on_hand": r.quantity,
            "committed": r.reserved_quantity if good else 0,
            "available": deployable,
            "deployable": deployable,
            "in_transit": r.in_transit_quantity or 0,
            "pending_requests": 0,
            "under_repair": r.quantity if c == "under repair" else 0,
            "needs_check": r.quantity if c == "needs check" else 0,
            "position_unverified": pos_unverified,
            "position_note": pos_note,
        })

    good_available = sum(l["available"] for l in lines)
    in_transit_total = sum(l["in_transit"] for l in lines)
    plan, left = [], max(0, required)
    candidates = sorted(
        [l for l in lines if l["available"] > 0],
        key=lambda l: (
            l["location"] != destination,
            l["position_unverified"],
            -l["available"],
            l["code"],
        ),
    )
    for l in candidates:
        if left <= 0:
            break
        take = min(left, l["available"])
        plan.append({
            "resource_code": l["code"],
            "from_location": l["location"],
            "quantity": take,
            "transfer_needed": l["location"] != destination,
            "is_in_place": l["location"] == destination,
            "position_unverified": l["position_unverified"],
            "position_note": l["position_note"],
        })
        left -= take

    shortage = max(0, required - good_available)
    needs_check = sum(l["needs_check"] for l in lines)
    under_repair = sum(l["under_repair"] for l in lines)
    uses_van1 = any(p["position_unverified"] for p in plan)
    return {
        "item": item,
        "required": required,
        "destination": destination,
        "good_total": sum(l["quantity"] for l in lines if (l["condition"] or "").lower() == "good"),
        "good_available": good_available,
        "deployable": good_available,
        "committed_elsewhere": sum(l["committed"] for l in lines),
        "in_transit": in_transit_total,
        "pending_requests_elsewhere": pending_demand_total,
        "under_repair": under_repair,
        "needs_check": needs_check,
        "shortage": shortage,
        "feasibility": "full" if shortage == 0 else ("partial" if good_available > 0 else "none"),
        "inspection_could_cover": shortage > 0 and needs_check >= shortage,
        "repair_could_cover": shortage > 0 and (needs_check + under_repair) >= shortage,
        "uses_unverified_location": uses_van1,
        "locations": lines,
        "plan": plan,
    }


def transfer_row(t: EquipmentTransfer, resources: dict[int, Resource]) -> dict[str, Any]:
    r = resources.get(t.resource_id)
    return {
        "id": t.id,
        "request_id": t.request_id,
        "request_line_id": t.request_line_id,
        "allocation_id": t.allocation_id,
        "kind": t.kind,
        "parent_id": t.parent_id,
        "resource_code": r.code if r else None,
        "item": r.name if r else None,
        "from_location": t.from_location,
        "to_location": t.to_location,
        "status": t.status,
        "quantity_planned": t.quantity_planned,
        "quantity_approved": t.quantity_approved,
        "quantity_dispatched": t.quantity_dispatched,
        "quantity_received": t.quantity_received,
        "quantity_accepted": t.quantity_accepted if t.quantity_accepted is not None else t.quantity_received,
        "quantity_damaged": t.quantity_damaged,
        "quantity_missing": t.quantity_missing,
        "quantity_good_returned": t.quantity_good_returned,
        "quantity_needs_check_returned": t.quantity_needs_check_returned,
        "reserved_quantity": t.reserved_quantity,
        "verified": t.verified,
        "stages": t.stages or {},
        "discrepancy_note": t.discrepancy_note,
        "discrepancy_status": t.discrepancy_status,
        "discrepancy_resolved_by": t.discrepancy_resolved_by,
        "discrepancy_resolved_at": t.discrepancy_resolved_at,
        "discrepancy_resolution_note": t.discrepancy_resolution_note,
        "condition_note": t.condition_note,
    }


def allocation_row(a: EquipmentAllocation, resources: dict[int, Resource]) -> dict[str, Any]:
    r = resources.get(a.resource_id)
    return {
        "id": a.id,
        "request_id": a.request_id,
        "request_line_id": a.request_line_id,
        "resource_id": a.resource_id,
        "resource_code": r.code if r else None,
        "item": a.item,
        "source_location": a.source_location,
        "destination": a.destination,
        "quantity_allocated": a.qty,
        "is_in_place": a.is_in_place,
        "used_needs_check": a.needs_check_exception,
        "exception_by": a.exception_by,
        "exception_reason": a.exception_reason,
        "status": a.status,
        "commitment_scope": a.commitment_scope,
    }


def readiness(req: EquipmentRequest, transfers: list[EquipmentTransfer], allocations: list[EquipmentAllocation] | None = None) -> str:
    if req.status == "rejected":
        return "unavailable"
    allocs = allocations or []
    delivery = [t for t in transfers if t.kind == "delivery" and t.status != "CANCELLED"]
    if req.status == "submitted":
        snap = req.availability_snapshot or {}
        if snap.get("good_available", 0) == 0:
            return "requires_inspection" if snap.get("needs_check", 0) > 0 else "unavailable"
        return "awaiting_approval"

    in_place_qty = sum(a.qty for a in allocs if a.is_in_place and a.status not in ("released",))
    strictly_verified_qty = in_place_qty + sum(
        (t.quantity_accepted if t.quantity_accepted is not None else (t.quantity_received or 0))
        for t in delivery
        if t.status in ("COMPLETED", "PARTIALLY_RECEIVED") and (t.verified or t.status == "PARTIALLY_RECEIVED")
    )
    total_required = sum(ln.confirmed_qty for ln in req.lines) if req.lines else req.quantity
    if strictly_verified_qty >= total_required and total_required > 0:
        return "satisfied"
    if any(t.status not in ("COMPLETED", "PARTIALLY_RECEIVED") for t in delivery):
        return "pending_transfer"
    if strictly_verified_qty > 0:
        return "partially_satisfied"
    return "unavailable"


def request_rows(db: Session, reqs: list[EquipmentRequest]) -> list[dict[str, Any]]:
    if not reqs:
        return []
    ids = [r.id for r in reqs]
    transfers = list(db.scalars(select(EquipmentTransfer).where(EquipmentTransfer.request_id.in_(ids)).order_by(EquipmentTransfer.id)))
    allocs = list(db.scalars(select(EquipmentAllocation).where(EquipmentAllocation.request_id.in_(ids)).order_by(EquipmentAllocation.id)))
    lines = list(db.scalars(select(EquipmentRequestLine).where(EquipmentRequestLine.request_id.in_(ids)).order_by(EquipmentRequestLine.id)))
    res_ids = {t.resource_id for t in transfers} | {a.resource_id for a in allocs} | {0}
    resources = {r.id: r for r in db.scalars(select(Resource).where(Resource.id.in_(res_ids)))}
    events = {e.id: e for e in db.scalars(select(Event).where(Event.id.in_({r.event_id for r in reqs})))}
    out = []
    for q in reqs:
        ts = [t for t in transfers if t.request_id == q.id]
        als = [a for a in allocs if a.request_id == q.id]
        lns = [ln for ln in lines if ln.request_id == q.id]
        e = events.get(q.event_id)
        in_place_delivered = sum(a.qty for a in als if a.is_in_place and a.status not in ("released",))
        transfer_delivered = sum(
            (t.quantity_accepted if t.quantity_accepted is not None else (t.quantity_received or 0))
            for t in ts
            if t.kind == "delivery" and t.status in ("COMPLETED", "PARTIALLY_RECEIVED") and (t.verified or t.status == "PARTIALLY_RECEIVED")
        )
        delivered = in_place_delivered + transfer_delivered
        returns = [t for t in ts if t.kind == "return"]
        approved_qty = in_place_delivered + sum(
            t.quantity_approved or 0 for t in ts if t.kind == "delivery" and t.status != "CANCELLED"
        )
        total_req_qty = sum(ln.confirmed_qty for ln in lns) if lns else q.quantity

        fresh_lines = []
        any_short = False
        for ln in lns:
            snap = availability(db, q.festival_id, ln.item, ln.confirmed_qty, q.destination, exclude_request=q.id)
            if snap.get("shortage", 0) > 0 and q.status in ("submitted", "partially_approved"):
                any_short = True
            line_allocs = [allocation_row(a, resources) for a in als if a.request_line_id == ln.id]
            line_transfers = [transfer_row(t, resources) for t in ts if t.request_line_id == ln.id and t.status != "CANCELLED"]
            line_delivered = sum(a["quantity_allocated"] for a in line_allocs if a["is_in_place"]) + sum(
                (t["quantity_accepted"] or 0) for t in line_transfers if t["kind"] == "delivery" and t["status"] in ("COMPLETED", "PARTIALLY_RECEIVED")
            )
            fresh_lines.append({
                "id": ln.id,
                "item": ln.item,
                "raw_token": ln.raw_token,
                "quantity": ln.confirmed_qty,
                "confirmed_qty": ln.confirmed_qty,
                "quantity_approved": ln.approved_qty,
                "quantity_delivered": line_delivered,
                "shortage_qty": ln.shortage_qty,
                "quantity_source": ln.qty_source,
                "shortage_decision": ln.shortage_decision,
                "shortage_reason": ln.shortage_decision_note,
                "availability": snap,
                "allocations": line_allocs,
                "transfers": line_transfers,
            })

        top_snap = (
            fresh_lines[0]["availability"]
            if len(fresh_lines) == 1
            else (
                availability(db, q.festival_id, q.item, q.quantity, q.destination, exclude_request=q.id)
                if q.status == "submitted"
                else q.availability_snapshot
            )
        )
        all_returned = bool(als) and all(a.status == "returned" for a in als)
        return_status = (
            "completed"
            if (all_returned or (returns and all(t.status == "COMPLETED" for t in returns)))
            else ("in_progress" if returns else None)
        )
        out.append({
            "id": q.id,
            "code": q.code,
            "event": {
                "code": e.code,
                "name": e.name,
                "venue": e.venue.name if e and e.venue else None,
                "start_at": e.start_at,
                "end_at": e.end_at,
                "status": e.status,
                "completed": event_done(e) if e else False,
            } if e else None,
            "item": q.item if len(fresh_lines) <= 1 else f"{len(fresh_lines)} items ({', '.join(ln['item'] for ln in fresh_lines)})",
            "primary_item": q.item,
            "quantity": total_req_qty,
            "destination": q.destination,
            "destination_verified": q.destination_verified,
            "needed_by": q.needed_by,
            "priority": q.priority,
            "notes": q.notes,
            "source": q.source,
            "source_text": q.source_text,
            "status": q.status,
            "has_shortage": any_short or q.has_shortage,
            "availability": top_snap,
            "lines": fresh_lines,
            "allocations": [allocation_row(a, resources) for a in als],
            "requested_by": q.requested_by_name,
            "created_at": q.created_at,
            "decided_by": q.decided_by_name,
            "decided_at": q.decided_at,
            "decision_reason": q.decision_reason,
            "readiness": readiness(q, ts, als),
            "verified_received": delivered,
            "in_place_quantity": in_place_delivered,
            "approved_quantity": approved_qty,
            "return_status": return_status,
            "transfers": [transfer_row(t, resources) for t in ts if t.status != "CANCELLED"],
        })
    return out


def event_done(e: Event) -> bool:
    return e.status == "completed" or bool(e.end_at and e.end_at <= now())


def row_for(db: Session, fid: int, template: Resource, location: str, condition: str) -> Resource:
    """The aggregate line for this item at this location and condition; created empty if none exists yet."""
    existing = db.scalar(
        select(Resource)
        .where(
            Resource.festival_id == fid,
            Resource.kind == "logistics",
            Resource.name == template.name,
            Resource.location == location,
            func.lower(Resource.condition) == condition.lower(),
        )
        .with_for_update()
    )
    if existing:
        return existing
    slug = re.sub(r"[^A-Z0-9]", "", location.upper())[:12]
    base_code = template.code.split("-")[0]
    code = f"{base_code}-{condition.replace(' ', '')[:6].upper()}-{slug}"[:40]
    r = Resource(
        festival_id=fid,
        code=code,
        kind="logistics",
        name=template.name,
        quantity=0,
        reserved_quantity=0,
        in_transit_quantity=0,
        version=1,
        location=location,
        condition=condition,
    )
    db.add(r)
    db.flush()
    return r


def adjust_allocation(db: Session, fid: int, resource_id: int, event_id: int | None, delta: int) -> None:
    if not event_id:
        return
    a = db.scalar(select(ResourceAllocation).where(ResourceAllocation.resource_id == resource_id, ResourceAllocation.event_id == event_id))
    if a:
        a.quantity += delta
        if a.quantity <= 0:
            db.delete(a)
    elif delta > 0:
        db.add(ResourceAllocation(festival_id=fid, resource_id=resource_id, event_id=event_id, quantity=delta))


def stage(t: EquipmentTransfer, status: str, user: User, quantity: int | None = None, note: str | None = None) -> None:
    t.status = status
    t.stages = {**(t.stages or {}), status: {"at": now().isoformat(), "by": user.name, "quantity": quantity, "note": note}}


def recompute_open_request_shortages(db: Session, fid: int, item: str) -> list[dict[str, Any]]:
    """Refreshes availability snapshots on open requests for `item` and returns newly unblocked requests."""
    open_reqs = list(
        db.scalars(
            select(EquipmentRequest).where(
                EquipmentRequest.festival_id == fid,
                EquipmentRequest.status.in_(("submitted", "partially_approved")),
            )
        )
    )
    unblocked = []
    for req in open_reqs:
        lns = list(db.scalars(select(EquipmentRequestLine).where(EquipmentRequestLine.request_id == req.id)))
        matched_line = False
        still_short = False
        for ln in lns:
            if ln.item.lower() == item.lower():
                matched_line = True
                old_short = ln.shortage_qty
                fresh = availability(db, fid, ln.item, ln.confirmed_qty, req.destination, exclude_request=req.id)
                ln.shortage_qty = fresh["shortage"]
                if fresh["shortage"] > 0:
                    still_short = True
                elif old_short > 0:
                    unblocked.append({"request_code": req.code, "item": ln.item, "quantity": ln.confirmed_qty, "destination": req.destination})
            else:
                if ln.shortage_qty > 0:
                    still_short = True
        if matched_line:
            req.has_shortage = still_short
            if req.item.lower() == item.lower():
                req.availability_snapshot = availability(db, fid, req.item, req.quantity, req.destination, exclude_request=req.id)
    return unblocked


# ---------- Summary, Options, Locations, Aliases ----------

@router.get("/summary", dependencies=[Depends(require("equipment.read"))])
def equipment_summary(festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    fid = festival.id
    resources = list(db.scalars(select(Resource).where(Resource.festival_id == fid, Resource.kind == "logistics")))
    on_hand_total = sum(r.quantity for r in resources)
    in_transit_total = sum(r.in_transit_quantity or 0 for r in resources)
    good_rows = [r for r in resources if cond(r) == "good"]
    deployable_now = sum(max(0, r.quantity - r.reserved_quantity) for r in good_rows)
    committed_total = sum(r.reserved_quantity for r in good_rows)
    needs_inspection = sum(r.quantity for r in resources if cond(r) == "needs check")
    under_repair = sum(r.quantity for r in resources if cond(r) == "under repair")

    reqs = list(db.scalars(select(EquipmentRequest).where(EquipmentRequest.festival_id == fid).order_by(EquipmentRequest.id)))
    req_rows = request_rows(db, reqs)
    transfers = list(db.scalars(select(EquipmentTransfer).where(EquipmentTransfer.festival_id == fid, EquipmentTransfer.status != "CANCELLED")))
    ledger_count = db.scalar(select(func.count()).select_from(MovementLedger).where(MovementLedger.festival_id == fid)) or 0

    awaiting_approval = sum(1 for r in req_rows if r["status"] == "submitted")
    lines_short = sum(
        1
        for r in req_rows
        if r["status"] in ("submitted", "partially_approved")
        for ln in (r.get("lines") or [{"availability": r.get("availability")}])
        if (ln.get("availability") or {}).get("shortage", 0) > 0
    )
    open_discrepancies = sum(1 for t in transfers if t.discrepancy_status == "open")

    all_events = list(db.scalars(select(Event).where(Event.festival_id == fid, Event.deleted_at.is_(None)).order_by(Event.code)))
    items_cat = logistics_items(db, fid)
    aliases = alias_map(db, fid)
    reqs_by_event: dict[str, list[dict[str, Any]]] = {}
    for r in req_rows:
        if r["event"]:
            reqs_by_event.setdefault(r["event"]["code"], []).append(r)

    declared_events = 0
    declared_none_events = 0
    quantities_confirmed = 0
    approved_events = 0
    allocated_events = 0
    delivered_events = 0
    returned_events = 0
    event_states = []

    for e in all_events:
        if is_declared_none(e.equipment_needs):
            declared_none_events += 1
            event_states.append({"code": e.code, "name": e.name, "stage": "declared_none", "needs": e.equipment_needs})
            continue
        parsed = parse_needs(e.equipment_needs, items_cat, aliases)
        if not parsed:
            event_states.append({"code": e.code, "name": e.name, "stage": "declared_none", "needs": e.equipment_needs})
            continue
        declared_events += 1
        ev_reqs = [r for r in reqs_by_event.get(e.code, []) if r["status"] != "rejected"]
        requested_items = {
            ln["item"].lower()
            for r in ev_reqs
            for ln in (r.get("lines") or [{"item": r["primary_item"]}])
            if ln.get("item")
        }
        all_items_requested = all(n["item"] and n["item"].lower() in requested_items for n in parsed)
        stage_name = "declared"
        if ev_reqs and all_items_requested:
            quantities_confirmed += 1
            stage_name = "quantities_confirmed"
            if all(r["status"] in ("approved", "partially_approved", "returning", "closed") for r in ev_reqs):
                approved_events += 1
                stage_name = "approved"
                if any(r["approved_quantity"] > 0 for r in ev_reqs):
                    allocated_events += 1
                    stage_name = "allocated"
                if all(r["readiness"] == "satisfied" or r["status"] == "closed" for r in ev_reqs):
                    delivered_events += 1
                    stage_name = "delivered"
                if all(r["status"] == "closed" for r in ev_reqs):
                    returned_events += 1
                    stage_name = "returned"
        event_states.append({
            "code": e.code,
            "name": e.name,
            "venue": e.venue.name if e.venue else None,
            "stage": stage_name,
            "needs": e.equipment_needs,
            "unconfirmed_tokens": [n["raw"] for n in parsed if n["quantity"] is None and (not n["item"] or n["item"].lower() not in requested_items)],
        })

    locs = list(db.scalars(select(InventoryLocation).where(InventoryLocation.festival_id == fid).order_by(InventoryLocation.id)))
    alias_rows = list(db.scalars(select(EquipmentAlias).where(EquipmentAlias.festival_id == fid).order_by(EquipmentAlias.token)))

    return {
        "kpis": {
            "total_units": on_hand_total + in_transit_total,
            "on_hand": on_hand_total,
            "good_on_hand": sum(r.quantity for r in good_rows),
            "committed": committed_total,
            "deployable_now": deployable_now,
            "in_transit": in_transit_total,
            "needs_inspection": needs_inspection,
            "under_repair": under_repair,
        },
        "attention": {
            "awaiting_approval": awaiting_approval,
            "lines_short": lines_short,
            "units_need_inspection": needs_inspection,
            "open_discrepancies": open_discrepancies,
        },
        "tab_counts": {
            "requests": len(req_rows),
            "stock": len(items_cat),
            "transfers": sum(1 for t in transfers if t.status != "COMPLETED"),
            "ledger": ledger_count,
        },
        "funnel": {
            "declared": declared_events,
            "declared_none": declared_none_events,
            "quantities_confirmed": quantities_confirmed,
            "approved": approved_events,
            "allocated": allocated_events,
            "delivered": delivered_events,
            "returned": returned_events,
            "events": event_states,
        },
        "locations": [
            {
                "id": loc.id,
                "name": loc.name,
                "kind": loc.kind,
                "venue_id": loc.venue_id,
                "venue_code": loc.venue_code,
                "matched_by_name": loc.matched_by_name,
                "venue_confirmed": loc.venue_confirmed,
                "venue_confirmed_by": loc.venue_confirmed_by,
                "venue_confirmed_at": loc.venue_confirmed_at,
                "position_unverified": loc.position_unverified,
                "position_note": loc.position_note,
            }
            for loc in locs
        ],
        "aliases": [
            {
                "id": a.id,
                "token": a.token,
                "target_item": a.target_item,
                "verified": a.verified,
                "verified_by": a.verified_by,
                "verified_at": a.verified_at,
                "source": a.source,
            }
            for a in alias_rows
        ],
    }


@router.get("/options", dependencies=[Depends(require("equipment.read"))])
def options(festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    locations = sorted({l for l in db.scalars(select(Resource.location).where(Resource.festival_id == festival.id, Resource.kind == "logistics")) if l})
    venues = list(db.scalars(select(Venue.name).where(Venue.festival_id == festival.id).order_by(Venue.name)))
    locs = list(db.scalars(select(InventoryLocation).where(InventoryLocation.festival_id == festival.id).order_by(InventoryLocation.name)))
    return {
        "items": logistics_items(db, festival.id),
        "inventory_locations": locations,
        "venues": venues,
        "unified_locations": [
            {
                "id": l.id,
                "name": l.name,
                "kind": l.kind,
                "venue_code": l.venue_code,
                "matched_by_name": l.matched_by_name,
                "venue_confirmed": l.venue_confirmed,
                "position_unverified": l.position_unverified,
                "position_note": l.position_note,
            }
            for l in locs
        ],
        "priorities": ["low", "normal", "high", "critical"],
        "lifecycle": LIFECYCLE,
    }


class LocationPatchIn(BaseModel):
    venue_confirmed: bool | None = None
    position_unverified: bool | None = None
    position_note: str | None = Field(default=None, max_length=240)


@router.patch("/locations/{location_id}", dependencies=[Depends(require("equipment.approve"))])
def update_location(
    location_id: int,
    body: LocationPatchIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    loc = db.scalar(select(InventoryLocation).where(InventoryLocation.id == location_id, InventoryLocation.festival_id == festival.id))
    if not loc:
        raise HTTPException(404, "Location not found")
    if body.venue_confirmed is not None:
        loc.venue_confirmed = body.venue_confirmed
        loc.venue_confirmed_by = user.name if body.venue_confirmed else None
        loc.venue_confirmed_at = now() if body.venue_confirmed else None
    if body.position_unverified is not None:
        loc.position_unverified = body.position_unverified
    if body.position_note is not None:
        loc.position_note = body.position_note.strip() or None
        loc.position_updated_by = user.name
        loc.position_updated_at = now()
    record(db, user, festival.id, "equipment.location.update", "inventory_location", loc.name, f"{user.name} updated location {loc.name}")
    db.commit()
    return {
        "id": loc.id,
        "name": loc.name,
        "kind": loc.kind,
        "venue_code": loc.venue_code,
        "matched_by_name": loc.matched_by_name,
        "venue_confirmed": loc.venue_confirmed,
        "venue_confirmed_by": loc.venue_confirmed_by,
        "venue_confirmed_at": loc.venue_confirmed_at,
        "position_unverified": loc.position_unverified,
        "position_note": loc.position_note,
    }


class AliasUpsertIn(BaseModel):
    token: str = Field(min_length=1, max_length=80)
    target_item: str = Field(min_length=1, max_length=80)
    verified: bool = True


@router.put("/aliases", dependencies=[Depends(require("equipment.approve"))])
def upsert_alias(
    body: AliasUpsertIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    items = {i.lower(): i for i in logistics_items(db, festival.id)}
    target = items.get(body.target_item.strip().lower())
    if not target:
        raise HTTPException(422, f"'{body.target_item}' is not in the 18-item logistics catalogue")
    tok = body.token.strip().lower()
    row = db.scalar(select(EquipmentAlias).where(EquipmentAlias.festival_id == festival.id, func.lower(EquipmentAlias.token) == tok))
    if row:
        row.target_item = target
        row.verified = body.verified
        row.verified_by = user.name if body.verified else None
        row.verified_at = now() if body.verified else None
    else:
        row = EquipmentAlias(
            festival_id=festival.id,
            token=tok,
            target_item=target,
            verified=body.verified,
            verified_by=user.name if body.verified else None,
            verified_at=now() if body.verified else None,
            source="organizer",
        )
        db.add(row)
    record(db, user, festival.id, "equipment.alias.upsert", "equipment_alias", tok, f"{user.name} mapped '{tok}' -> '{target}'")
    db.commit()
    return {
        "id": row.id,
        "token": row.token,
        "target_item": row.target_item,
        "verified": row.verified,
        "verified_by": row.verified_by,
        "verified_at": row.verified_at,
        "source": row.source,
    }


@router.get("/availability", dependencies=[Depends(require("equipment.read"))])
def check_availability(
    item: str = Query(min_length=2, max_length=60),
    quantity: int = Query(1, ge=1, le=100000),
    destination: str | None = None,
    festival: Festival = Depends(festival_access),
    db: Session = Depends(get_db),
):
    return availability(db, festival.id, item, quantity, destination)


@router.get("/events/{code}", dependencies=[Depends(require("equipment.read"))])
def event_equipment(code: str, festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    e = by_code(db, Event, festival.id, code)
    items = logistics_items(db, festival.id)
    aliases = alias_map(db, festival.id)
    declared_none = is_declared_none(e.equipment_needs)
    needs = parse_needs(e.equipment_needs, items, aliases)
    venue_name = e.venue.name if e.venue else None
    venue_reqs_raw = e.venue_requirements
    venue_classified = parse_venue_requirements(venue_reqs_raw, items)

    loc_map = location_lookup(db, festival.id)
    dest_loc = loc_map.get(venue_name or "")

    reqs = request_rows(db, list(db.scalars(select(EquipmentRequest).where(EquipmentRequest.event_id == e.id).order_by(EquipmentRequest.id))))
    requested_items = {
        ln["item"].lower()
        for r in reqs
        if r["status"] != "rejected"
        for ln in (r.get("lines") or [{"item": r["primary_item"]}])
        if ln.get("item")
    }
    statuses = [r["readiness"] for r in reqs if r["status"] != "rejected"]
    for n in needs:
        n["requested"] = bool(n["item"] and n["item"].lower() in requested_items)
        if n["item"]:
            n["preview"] = availability(db, festival.id, n["item"], n["quantity"] or 0, venue_name)
        if not n["requested"]:
            statuses.append("not_requested")
    for s in venue_classified["suggestions"]:
        s["already_in_needs"] = any(n["item"] == s["item"] for n in needs)
        s["preview"] = availability(db, festival.id, s["item"], 0, venue_name)

    overall = min(statuses, key=READINESS_ORDER.index) if statuses else ("no_equipment_needed" if (declared_none or not needs) else "not_requested")
    return {
        "event": {
            "code": e.code,
            "name": e.name,
            "category": e.category,
            "venue": venue_name,
            "venue_code": e.venue.code if e.venue else None,
            "venue_requirements": venue_reqs_raw,
            "destination_matched_by_name": bool(dest_loc and dest_loc.matched_by_name),
            "destination_venue_confirmed": bool(dest_loc and dest_loc.venue_confirmed),
            "destination_location_id": dest_loc.id if dest_loc else None,
            "start_at": e.start_at,
            "end_at": e.end_at,
            "status": e.status,
            "completed": event_done(e),
        },
        "declared_none": declared_none,
        "equipment_needs_text": e.equipment_needs,
        "parsed_needs": needs,
        "venue_suggestions": venue_classified["suggestions"],
        "venue_hints": venue_classified["hints"],
        "venue_ignored": venue_classified["ignored"],
        "requests": reqs,
        "readiness": overall,
    }


@router.get("/requests", dependencies=[Depends(require("equipment.read"))])
def list_requests(
    status: str | None = None,
    mine: bool = False,
    event: str | None = None,
    item: str | None = None,
    destination: str | None = None,
    priority: str | None = None,
    shortage_only: bool = False,
    funnel_stage: str | None = None,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    q = select(EquipmentRequest).where(EquipmentRequest.festival_id == festival.id)
    if status:
        q = q.where(EquipmentRequest.status == status)
    if mine:
        q = q.where(EquipmentRequest.requested_by_name == user.name)
    if event:
        q = q.where(EquipmentRequest.event_id == by_code(db, Event, festival.id, event).id)
    if destination:
        q = q.where(func.lower(EquipmentRequest.destination) == destination.strip().lower())
    if priority:
        q = q.where(EquipmentRequest.priority == priority)
    reqs = list(db.scalars(q))
    prio_rank = {"critical": 0, "high": 1, "normal": 2, "low": 3}
    reqs.sort(key=lambda r: (r.status not in ("submitted",), prio_rank.get(r.priority, 9), r.needed_by))
    rows = request_rows(db, reqs)
    if item:
        item_low = item.strip().lower()
        rows = [
            r for r in rows
            if r["primary_item"].lower() == item_low or any(ln["item"].lower() == item_low for ln in r.get("lines", []))
        ]
    if shortage_only:
        rows = [
            r for r in rows
            if r["has_shortage"] or any((ln.get("availability") or {}).get("shortage", 0) > 0 for ln in r.get("lines", []))
        ]
    if funnel_stage:
        if funnel_stage == "quantities_confirmed":
            rows = [r for r in rows if r["status"] != "rejected"]
        elif funnel_stage == "approved":
            rows = [r for r in rows if r["status"] in ("approved", "partially_approved", "returning", "closed")]
        elif funnel_stage == "allocated":
            rows = [r for r in rows if r["approved_quantity"] > 0]
        elif funnel_stage == "delivered":
            rows = [r for r in rows if r["readiness"] == "satisfied" or r["status"] == "closed"]
        elif funnel_stage == "returned":
            rows = [r for r in rows if r["status"] == "closed"]

    all_open_transfers = list(
        db.scalars(
            select(EquipmentTransfer)
            .where(
                EquipmentTransfer.festival_id == festival.id,
                EquipmentTransfer.status.notin_(("COMPLETED", "CANCELLED")),
            )
            .order_by(EquipmentTransfer.id.desc())
        )
    )
    res_map = {
        r.id: r
        for r in db.scalars(
            select(Resource).where(Resource.id.in_({t.resource_id for t in all_open_transfers} or {0}))
        )
    }
    req_by_id = {r["id"]: r for r in rows}
    open_transfers = []
    for t in all_open_transfers:
        parent_req = req_by_id.get(t.request_id)
        open_transfers.append({
            **transfer_row(t, res_map),
            "request_code": parent_req["code"] if parent_req else None,
            "event": parent_req["event"] if parent_req else None,
        })
    return {
        "items": rows,
        "open_transfers": open_transfers,
        "counts": {
            s: sum(1 for r in rows if r["status"] == s)
            for s in ("submitted", "approved", "partially_approved", "rejected", "returning", "closed")
        },
    }


# ---------- Request, approve, reject ----------

class RequestLineIn(BaseModel):
    item: str = Field(min_length=2, max_length=60)
    quantity: int = Field(ge=1, le=100000)
    source_text: str | None = Field(default=None, max_length=160)
    quantity_source: str | None = Field(default=None, max_length=40)
    notes: str | None = Field(default=None, max_length=500)


class RequestIn(BaseModel):
    event_code: str = Field(min_length=1, max_length=20)
    item: str | None = Field(default=None, max_length=60)
    quantity: int | None = Field(default=None, ge=1, le=100000)
    lines: list[RequestLineIn] | None = None
    destination: str = Field(min_length=2, max_length=120)
    needed_by: datetime
    priority: str = Field(default="normal", pattern="^(low|normal|high|critical)$")
    notes: str | None = Field(default=None, max_length=1000)
    source_text: str | None = Field(default=None, max_length=160)


def next_code(db: Session, fid: int) -> str:
    codes = db.scalars(select(EquipmentRequest.code).where(EquipmentRequest.festival_id == fid))
    return f"EQR{max((int(c[3:]) for c in codes if c[3:].isdigit()), default=0) + 1:03d}"


@router.post("/requests", status_code=201, dependencies=[Depends(require("equipment.request"))])
def create_request(
    body: RequestIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    e = by_code(db, Event, festival.id, body.event_code)
    items_map = {i.lower(): i for i in logistics_items(db, festival.id)}
    aliases = alias_map(db, festival.id)

    raw_lines: list[RequestLineIn] = []
    if body.lines:
        raw_lines = body.lines
    elif body.item and body.quantity is not None:
        raw_lines = [
            RequestLineIn(
                item=body.item,
                quantity=body.quantity,
                source_text=body.source_text,
                notes=body.notes,
            )
        ]
    else:
        raise HTTPException(422, "Enter at least one equipment item and a positive quantity")

    if body.needed_by.tzinfo is None:
        raise HTTPException(422, "Delivery time must include a timezone")

    dest = body.destination.strip()
    loc_map = location_lookup(db, festival.id)
    dest_loc = loc_map.get(dest)
    dest_verified = bool(dest_loc and (dest_loc.venue_confirmed or not dest_loc.matched_by_name))

    parsed_event_needs = parse_needs(e.equipment_needs, list(items_map.values()), aliases)
    csv_items = {n["item"]: n for n in parsed_event_needs if n["item"]}

    validated_lines: list[dict[str, Any]] = []
    for rl in raw_lines:
        raw_name = rl.item.strip().lower()
        canonical = items_map.get(raw_name)
        if not canonical and raw_name in aliases and aliases[raw_name].target_item:
            canonical = items_map.get(aliases[raw_name].target_item.lower())
        if not canonical:
            raise HTTPException(
                422,
                f"'{rl.item}' is not in campus logistics inventory. Known items: {', '.join(items_map.values())}",
            )
        snap = availability(db, festival.id, canonical, rl.quantity, dest)
        csv_match = csv_items.get(canonical)
        qty_source = rl.quantity_source or (
            "events_csv" if (csv_match and csv_match["quantity"] == rl.quantity) else "organizer"
        )
        validated_lines.append({
            "item": canonical,
            "quantity": rl.quantity,
            "raw_token": rl.source_text or (csv_match["raw"] if csv_match else canonical),
            "qty_source": qty_source,
            "notes": rl.notes,
            "snap": snap,
            "from_csv": csv_match is not None,
        })

    first = validated_lines[0]
    total_qty = sum(l["quantity"] for l in validated_lines)
    any_shortage = any(l["snap"]["shortage"] > 0 for l in validated_lines)
    from_csv_any = any(l["from_csv"] for l in validated_lines)

    req = EquipmentRequest(
        festival_id=festival.id,
        code=next_code(db, festival.id),
        event_id=e.id,
        item=first["item"],
        quantity=total_qty,
        destination=dest,
        destination_verified=dest_verified,
        needed_by=body.needed_by,
        priority=body.priority,
        notes=body.notes,
        source="event_csv" if from_csv_any else "manual",
        source_text=body.source_text or first["raw_token"],
        availability_snapshot=first["snap"],
        has_shortage=any_shortage,
        requested_by_name=user.name,
    )
    db.add(req)
    db.flush()

    for vl in validated_lines:
        ln = EquipmentRequestLine(
            festival_id=festival.id,
            request_id=req.id,
            raw_token=vl["raw_token"],
            item=vl["item"],
            matched=True,
            confirmed_qty=vl["quantity"],
            qty_source=vl["qty_source"],
            confirmed_by=user.name,
            approved_qty=0,
            shortage_qty=vl["snap"]["shortage"],
        )
        db.add(ln)
        db.flush()
        by_code_res = {
            r.code: r
            for r in db.scalars(
                select(Resource).where(
                    Resource.festival_id == festival.id,
                    Resource.code.in_([p["resource_code"] for p in vl["snap"]["plan"]] or [""]),
                )
            )
        }
        for p_line in vl["snap"]["plan"]:
            res = by_code_res.get(p_line["resource_code"])
            if res:
                t = EquipmentTransfer(
                    festival_id=festival.id,
                    request_id=req.id,
                    request_line_id=ln.id,
                    resource_id=res.id,
                    kind="delivery",
                    from_location=p_line["from_location"],
                    to_location=req.destination,
                    quantity_planned=p_line["quantity"],
                )
                stage(t, "REQUESTED", user, p_line["quantity"], "Proposed by FestOS from actual inventory")
                db.add(t)
        log_movement(
            db,
            festival.id,
            user,
            action="request_submitted",
            item=vl["item"],
            quantity=vl["quantity"],
            to_location=dest,
            event_code=e.code,
            request_code=req.code,
            status="submitted",
            reason=vl["notes"] or body.notes,
        )

    record(
        db,
        user,
        festival.id,
        "equipment.request",
        "equipment_request",
        req.code,
        f"{user.name} submitted equipment request {req.code} for {e.name} at {req.destination}",
        after={"lines": [{"item": l["item"], "quantity": l["quantity"], "shortage": l["snap"]["shortage"]} for l in validated_lines]},
    )
    db.commit()

    warnings = []
    if e.start_at and body.needed_by > e.start_at:
        warnings.append("Delivery time is after the event starts.")
    for vl in validated_lines:
        if vl["snap"]["shortage"]:
            warnings.append(
                f"{vl['item']}: short by {vl['snap']['shortage']} (only {vl['snap']['good_available']} good units free across campus)."
            )
        if vl["snap"].get("uses_unverified_location"):
            warnings.append(
                f"{vl['item']}: candidate plan includes Van 1 (mobile location, position unverified)."
            )
    return {**request_rows(db, [req])[0], "warnings": warnings}


class ApproveIn(BaseModel):
    lines: list[dict[str, Any]] | None = None
    reason: str | None = Field(default=None, max_length=400)
    shortage_decision: str | None = Field(default=None, pattern="^(inspect|repair|transfer|source_externally|accept_reduced)?$")
    allow_needs_check: bool = False
    force_transfer_in_place: bool = False


@router.post("/requests/{code}/approve", dependencies=[Depends(require("equipment.approve"))])
def approve_request(
    code: str,
    body: ApproveIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    req = db.scalar(
        select(EquipmentRequest)
        .where(EquipmentRequest.festival_id == festival.id, EquipmentRequest.code == code)
        .with_for_update()
    )
    if not req:
        raise HTTPException(404, f"Request {code} not found")
    if req.status != "submitted":
        raise HTTPException(409, f"Request {code} is already {req.status}")

    req_lines = list(
        db.scalars(
            select(EquipmentRequestLine)
            .where(EquipmentRequestLine.request_id == req.id)
            .order_by(EquipmentRequestLine.id)
            .with_for_update()
        )
    )
    if not req_lines:
        ln = EquipmentRequestLine(
            festival_id=festival.id,
            request_id=req.id,
            raw_token=req.source_text,
            item=req.item,
            matched=True,
            confirmed_qty=req.quantity,
            qty_source="events_csv" if req.source == "event_csv" else "organizer",
            confirmed_by=req.requested_by_name,
            approved_qty=0,
            shortage_qty=0,
        )
        db.add(ln)
        db.flush()
        req_lines = [ln]

    desired_allocations: list[dict[str, Any]] = []
    if body.lines is not None:
        for entry in body.lines:
            qty = int(entry.get("quantity") or 0)
            if qty <= 0:
                continue
            desired_allocations.append({
                "resource_code": str(entry.get("resource_code")),
                "quantity": qty,
                "request_line_id": entry.get("request_line_id"),
                "allow_needs_check": bool(entry.get("allow_needs_check") or body.allow_needs_check),
                "exception_reason": entry.get("exception_reason") or body.reason,
            })
    else:
        for ln in req_lines:
            fresh = availability(db, festival.id, ln.item, ln.confirmed_qty, req.destination, exclude_request=req.id)
            for p in fresh["plan"]:
                desired_allocations.append({
                    "resource_code": p["resource_code"],
                    "quantity": p["quantity"],
                    "request_line_id": ln.id,
                    "allow_needs_check": body.allow_needs_check,
                    "exception_reason": body.reason,
                })

    if not desired_allocations:
        raise HTTPException(409, f"No good stock is free on campus for {req.code}. Inspect 'needs check' stock or reject the request.")

    target_codes = {d["resource_code"] for d in desired_allocations}
    locked_resources = {
        r.code: r
        for r in db.scalars(
            select(Resource)
            .where(Resource.festival_id == festival.id, Resource.code.in_(target_codes))
            .order_by(Resource.id)
            .with_for_update()
        )
    }

    line_by_id = {ln.id: ln for ln in req_lines}
    line_by_item = {ln.item.lower(): ln for ln in req_lines}

    planned: list[tuple[EquipmentRequestLine, Resource, int, bool, str | None]] = []
    totals_by_line: dict[int, int] = {ln.id: 0 for ln in req_lines}

    for d in desired_allocations:
        r = locked_resources.get(d["resource_code"])
        if not r:
            raise HTTPException(422, f"{d['resource_code']} is not a valid inventory line")
        ln = line_by_id.get(d["request_line_id"]) if d["request_line_id"] else line_by_item.get(r.name.lower())
        if not ln or r.name.lower() != ln.item.lower():
            raise HTTPException(422, f"{d['resource_code']} does not match requested item")
        c = cond(r)
        allow_nc = d["allow_needs_check"]
        if c != "good":
            if c == "needs check" and allow_nc:
                if not (d["exception_reason"] and len(str(d["exception_reason"]).strip()) >= 3):
                    raise HTTPException(422, "An explicit reason is required to allocate 'needs check' stock.")
            else:
                raise HTTPException(409, f"{r.code} is '{r.condition}'. Items under repair or needing a check cannot be allocated.")
        free = r.quantity - r.reserved_quantity
        if d["quantity"] > free:
            raise HTTPException(409, f"Only {free} {r.name} free at {r.location} ({r.code}); the rest is already committed.")
        totals_by_line[ln.id] = totals_by_line.get(ln.id, 0) + d["quantity"]
        if totals_by_line[ln.id] > ln.confirmed_qty:
            raise HTTPException(422, f"Allocation {totals_by_line[ln.id]} exceeds requested {ln.confirmed_qty} for {ln.item}")
        planned.append((ln, r, d["quantity"], c == "needs check", d["exception_reason"]))

    total_allocated = sum(totals_by_line.values())
    total_requested = sum(ln.confirmed_qty for ln in req_lines)
    if total_allocated == 0:
        raise HTTPException(409, "No units could be allocated. Inspect 'needs check' stock or reject the request.")
    if total_allocated < total_requested and not (body.reason and len(body.reason.strip()) >= 3):
        raise HTTPException(422, f"Allocating {total_allocated} of {total_requested}. A reason is required for a partial allocation.")

    for t in db.scalars(select(EquipmentTransfer).where(EquipmentTransfer.request_id == req.id, EquipmentTransfer.status == "REQUESTED")):
        t.status = "CANCELLED"

    e = db.get(Event, req.event_id)
    loc_map = location_lookup(db, festival.id)
    warnings = []

    for ln, r, qty, used_nc, exc_reason in planned:
        if used_nc:
            r.quantity -= qty
            r.version = (r.version or 1) + 1
            good_r = row_for(db, festival.id, r, r.location or "Unknown", "good")
            good_r.quantity += qty
            good_r.reserved_quantity += qty
            good_r.version = (good_r.version or 1) + 1
            target_r = good_r
        else:
            r.reserved_quantity += qty
            r.version = (r.version or 1) + 1
            target_r = r

        in_place = (target_r.location == req.destination) and not body.force_transfer_in_place
        src_loc_info = loc_map.get(target_r.location or "")
        if src_loc_info and src_loc_info.position_unverified:
            warnings.append(f"Allocating {qty} {ln.item} from {target_r.location} (mobile location, position unverified).")

        alloc = EquipmentAllocation(
            festival_id=festival.id,
            request_id=req.id,
            request_line_id=ln.id,
            resource_id=target_r.id,
            item=ln.item,
            source_location=target_r.location or "Unknown",
            destination=req.destination,
            qty=qty,
            status="received" if in_place else "allocated",
            is_in_place=in_place,
            needs_check_exception=used_nc,
            exception_by=user.name if used_nc else None,
            exception_reason=exc_reason if used_nc else None,
            commitment_scope="held for entire fest",
        )
        db.add(alloc)
        db.flush()

        adjust_allocation(db, festival.id, target_r.id, req.event_id, qty)

        if in_place:
            log_movement(
                db,
                festival.id,
                user,
                action="allocate_in_place",
                item=ln.item,
                quantity=qty,
                resource_code=target_r.code,
                from_location=target_r.location,
                to_location=req.destination,
                from_condition="Good",
                to_condition="Committed (In Place)",
                event_code=e.code if e else None,
                request_code=req.code,
                status="allocated_in_place",
                reason=body.reason or "Stock already at destination; allocated in place with no transfer",
            )
        else:
            t = EquipmentTransfer(
                festival_id=festival.id,
                request_id=req.id,
                request_line_id=ln.id,
                allocation_id=alloc.id,
                resource_id=target_r.id,
                kind="delivery",
                from_location=target_r.location or "Unknown",
                to_location=req.destination,
                quantity_planned=qty,
                quantity_approved=qty,
                reserved_quantity=qty,
                stages={
                    "REQUESTED": {
                        "at": req.created_at.isoformat() if req.created_at else None,
                        "by": req.requested_by_name,
                        "quantity": qty,
                        "note": None,
                    }
                },
            )
            stage(t, "APPROVED", user, qty, body.reason)
            db.add(t)
            db.flush()
            log_movement(
                db,
                festival.id,
                user,
                action="allocate_transfer",
                item=ln.item,
                quantity=qty,
                resource_code=target_r.code,
                from_location=target_r.location,
                to_location=req.destination,
                from_condition="Good",
                to_condition="Committed",
                event_code=e.code if e else None,
                request_code=req.code,
                transfer_id=t.id,
                status="approved",
                reason=body.reason,
            )

    for ln in req_lines:
        alloc_for_ln = totals_by_line.get(ln.id, 0)
        ln.approved_qty = alloc_for_ln
        ln.shortage_qty = max(0, ln.confirmed_qty - alloc_for_ln)
        if alloc_for_ln < ln.confirmed_qty:
            ln.shortage_decision = body.shortage_decision or "accept_reduced"
            ln.shortage_decision_qty = ln.shortage_qty
            ln.shortage_decision_note = body.reason
            ln.shortage_decided_by = user.name
            ln.shortage_decided_at = now()

    req.status = "approved" if total_allocated == total_requested else "partially_approved"
    req.has_shortage = total_allocated < total_requested
    req.decided_by_name, req.decided_at, req.decision_reason = user.name, now(), body.reason
    record(
        db,
        user,
        festival.id,
        "equipment.approve",
        "equipment_request",
        req.code,
        f"{user.name} approved {total_allocated} of {total_requested} units for {req.code}",
        reason=body.reason,
    )
    db.commit()
    return {**request_rows(db, [req])[0], "warnings": warnings}


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=400)


@router.post("/requests/{code}/reject", dependencies=[Depends(require("equipment.approve"))])
def reject_request(
    code: str,
    body: ReasonIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    req = by_code(db, EquipmentRequest, festival.id, code)
    if req.status != "submitted":
        raise HTTPException(409, f"Request {code} is already {req.status}")
    for t in db.scalars(select(EquipmentTransfer).where(EquipmentTransfer.request_id == req.id, EquipmentTransfer.status == "REQUESTED")):
        t.status = "CANCELLED"
    req.status, req.decided_by_name, req.decided_at, req.decision_reason = "rejected", user.name, now(), body.reason
    log_movement(
        db,
        festival.id,
        user,
        action="request_rejected",
        item=req.item,
        quantity=req.quantity,
        to_location=req.destination,
        request_code=req.code,
        status="rejected",
        reason=body.reason,
    )
    record(db, user, festival.id, "equipment.reject", "equipment_request", req.code, f"{user.name} rejected {req.code}", reason=body.reason)
    db.commit()
    return request_rows(db, [req])[0]


# ---------- Standalone Transfer Creation & Transfer Lifecycle ----------

class StandaloneTransferIn(BaseModel):
    resource_code: str = Field(min_length=2, max_length=40)
    to_location: str = Field(min_length=2, max_length=120)
    quantity: int = Field(ge=1, le=100000)
    note: str | None = Field(default=None, max_length=500)


@router.post("/transfers", status_code=201, dependencies=[Depends(require("equipment.approve"))])
def create_standalone_transfer(
    body: StandaloneTransferIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    r = db.scalar(
        select(Resource)
        .where(Resource.festival_id == festival.id, Resource.code == body.resource_code)
        .with_for_update()
    )
    if not r or r.kind != "logistics":
        raise HTTPException(404, f"Resource {body.resource_code} not found")
    if cond(r) != "good":
        raise HTTPException(409, f"{r.code} is '{r.condition}'. Only Good stock can be transferred.")
    to_loc = body.to_location.strip()
    if r.location == to_loc:
        raise HTTPException(422, "Destination must be different from source location")
    free = r.quantity - r.reserved_quantity
    if body.quantity > free:
        raise HTTPException(409, f"Only {free} {r.name} free at {r.location}")
    r.reserved_quantity += body.quantity
    r.version = (r.version or 1) + 1
    t = EquipmentTransfer(
        festival_id=festival.id,
        request_id=None,
        resource_id=r.id,
        kind="manual",
        from_location=r.location or "Unknown",
        to_location=to_loc,
        quantity_planned=body.quantity,
        quantity_approved=body.quantity,
        reserved_quantity=body.quantity,
    )
    stage(t, "APPROVED", user, body.quantity, body.note or "Standalone campus transfer")
    db.add(t)
    db.flush()
    log_movement(
        db,
        festival.id,
        user,
        action="standalone_transfer_created",
        item=r.name,
        quantity=body.quantity,
        resource_code=r.code,
        from_location=t.from_location,
        to_location=to_loc,
        transfer_id=t.id,
        status="approved",
        reason=body.note,
    )
    record(db, user, festival.id, "equipment.transfer.create", "equipment_transfer", t.id, f"{user.name} created transfer of {body.quantity} {r.name} ({t.from_location} -> {to_loc})")
    db.commit()
    return transfer_row(t, {r.id: r})


class AdvanceIn(BaseModel):
    status: str = Field(pattern="^(APPROVED|DISPATCHED|IN_TRANSIT|RECEIVED|COMPLETED)$")
    quantity: int | None = Field(default=None, ge=0, le=100000)
    accepted: int | None = Field(default=None, ge=0, le=100000)
    damaged: int | None = Field(default=None, ge=0, le=100000)
    missing: int | None = Field(default=None, ge=0, le=100000)
    good_returned: int | None = Field(default=None, ge=0, le=100000)
    verified: bool = False
    note: str | None = Field(default=None, max_length=600)
    idempotency_key: str | None = Field(default=None, max_length=64)


@router.post("/transfers/{transfer_id}/advance", dependencies=[Depends(require("equipment.read"))])
def advance_transfer(
    transfer_id: int,
    body: AdvanceIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    t = db.scalar(
        select(EquipmentTransfer)
        .where(EquipmentTransfer.id == transfer_id, EquipmentTransfer.festival_id == festival.id)
        .with_for_update()
    )
    if not t:
        raise HTTPException(404, "Transfer not found")
    if STAGE_PERMISSION[body.status] not in permissions_of(user):
        raise HTTPException(403, f"Moving a transfer to {body.status} needs {STAGE_PERMISSION[body.status]}")

    req = db.get(EquipmentRequest, t.request_id) if t.request_id else None
    e = db.get(Event, req.event_id) if req else None

    if body.idempotency_key:
        if body.status == "DISPATCHED" and t.dispatch_idempotency_key == body.idempotency_key:
            return request_rows(db, [req])[0] if req else transfer_row(t, {t.resource_id: db.get(Resource, t.resource_id)})
        if body.status == "RECEIVED" and t.receive_idempotency_key == body.idempotency_key:
            return request_rows(db, [req])[0] if req else transfer_row(t, {t.resource_id: db.get(Resource, t.resource_id)})
        if body.status == "COMPLETED" and t.return_idempotency_key == body.idempotency_key:
            return request_rows(db, [req])[0] if req else transfer_row(t, {t.resource_id: db.get(Resource, t.resource_id)})

    valid_next = {
        "REQUESTED": {"APPROVED"},
        "APPROVED": {"DISPATCHED"},
        "DISPATCHED": {"IN_TRANSIT", "RECEIVED"},
        "IN_TRANSIT": {"RECEIVED"},
        "RECEIVED": {"COMPLETED"},
        "PARTIALLY_RECEIVED": {"COMPLETED"},
    }
    if body.status not in valid_next.get(t.status, set()):
        std_idx = LIFECYCLE.index(t.status) if t.status in LIFECYCLE else -1
        nxt = LIFECYCLE[std_idx + 1] if 0 <= std_idx < len(LIFECYCLE) - 1 else "none"
        raise HTTPException(409, f"Transfer is {t.status}; the next step is {nxt}")
    if t.kind == "delivery" and body.status == "APPROVED":
        raise HTTPException(409, "Deliveries are approved through the request's allocation approval")

    r = db.scalar(select(Resource).where(Resource.id == t.resource_id).with_for_update())
    qty = body.quantity

    if body.status == "APPROVED":  # return transfers only
        t.quantity_approved = t.quantity_planned
        stage(t, "APPROVED", user, t.quantity_planned, body.note)

    elif body.status == "DISPATCHED":
        limit = t.quantity_approved or 0
        qty = limit if qty is None else qty
        if qty > limit or qty <= 0:
            raise HTTPException(422, f"Dispatch between 1 and {limit}")
        if t.kind in ("delivery", "manual"):
            if qty < limit:
                diff = limit - qty
                r.reserved_quantity = max(0, r.reserved_quantity - diff)
                t.reserved_quantity = max(0, t.reserved_quantity - diff)
                if req:
                    adjust_allocation(db, festival.id, r.id, req.event_id, -diff)
            r.quantity = max(0, r.quantity - qty)
            r.reserved_quantity = max(0, r.reserved_quantity - qty)
            r.in_transit_quantity = (r.in_transit_quantity or 0) + qty
            r.version = (r.version or 1) + 1
            if t.allocation_id:
                alloc = db.get(EquipmentAllocation, t.allocation_id)
                if alloc:
                    alloc.status = "in_transit"
            log_movement(
                db,
                festival.id,
                user,
                action="dispatch",
                item=r.name,
                quantity=qty,
                resource_code=r.code,
                from_location=t.from_location,
                to_location=t.to_location,
                from_condition="Good (On Hand)",
                to_condition="In Transit",
                event_code=e.code if e else None,
                request_code=req.code if req else None,
                transfer_id=t.id,
                status="dispatched",
                reason=body.note,
                idempotency_key=body.idempotency_key,
            )
        t.quantity_dispatched = qty
        t.dispatch_idempotency_key = body.idempotency_key
        stage(t, "DISPATCHED", user, qty, body.note)

    elif body.status == "IN_TRANSIT":
        stage(t, "IN_TRANSIT", user, t.quantity_dispatched, body.note)

    elif body.status == "RECEIVED":
        dispatched = t.quantity_dispatched or 0
        if body.accepted is not None:
            accepted_qty = body.accepted
            damaged_qty = body.damaged or 0
            missing_qty = body.missing if body.missing is not None else max(0, dispatched - accepted_qty - damaged_qty)
            qty = accepted_qty
        else:
            if qty is None:
                raise HTTPException(422, "Count and enter the quantity physically received")
            damaged_qty = body.damaged or 0
            accepted_qty = qty
            missing_qty = max(0, dispatched - accepted_qty - damaged_qty)

        if accepted_qty + damaged_qty + missing_qty > dispatched or accepted_qty > dispatched:
            raise HTTPException(422, f"Received counts ({accepted_qty} accepted, {damaged_qty} damaged, {missing_qty} missing) exceed the {dispatched} dispatched")

        t.quantity_received = accepted_qty
        t.quantity_accepted = accepted_qty
        t.quantity_damaged = damaged_qty
        t.quantity_missing = missing_qty
        t.receive_idempotency_key = body.idempotency_key

        if t.kind in ("delivery", "manual"):
            r.in_transit_quantity = max(0, (r.in_transit_quantity or 0) - dispatched)
            r.version = (r.version or 1) + 1
            if accepted_qty > 0:
                dest_good = row_for(db, festival.id, r, t.to_location, "good")
                dest_good.quantity += accepted_qty
                if t.kind == "delivery":
                    dest_good.reserved_quantity += accepted_qty
                    t.reserved_quantity = accepted_qty
                else:
                    t.reserved_quantity = 0
                dest_good.version = (dest_good.version or 1) + 1
            else:
                t.reserved_quantity = 0

            if damaged_qty > 0:
                dest_repair = row_for(db, festival.id, r, t.to_location, "under repair")
                dest_repair.quantity += damaged_qty
                dest_repair.version = (dest_repair.version or 1) + 1

            if missing_qty > 0 or damaged_qty > 0:
                parts = [f"Dispatched {dispatched}, accepted {accepted_qty}"]
                if damaged_qty > 0:
                    parts.append(f"{damaged_qty} damaged -> Under repair at {t.to_location}")
                if missing_qty > 0:
                    parts.append(f"{missing_qty} missing (open discrepancy)")
                    t.discrepancy_status = "open"
                if body.note:
                    parts.append(body.note)
                t.discrepancy_note = "; ".join(parts)

            if t.allocation_id:
                alloc = db.get(EquipmentAllocation, t.allocation_id)
                if alloc:
                    alloc.status = "partially_received" if (missing_qty > 0 or damaged_qty > 0) else "received"

            log_movement(
                db,
                festival.id,
                user,
                action="receive_partial" if (missing_qty > 0 or damaged_qty > 0) else "receive",
                item=r.name,
                quantity=accepted_qty,
                resource_code=r.code,
                from_location=t.from_location,
                to_location=t.to_location,
                from_condition="In Transit",
                to_condition="Good (Destination)",
                event_code=e.code if e else None,
                request_code=req.code if req else None,
                transfer_id=t.id,
                status="partially_received" if (missing_qty > 0 or damaged_qty > 0) else "received",
                reason=t.discrepancy_note or body.note,
                idempotency_key=body.idempotency_key,
            )

        target_stage = "PARTIALLY_RECEIVED" if (missing_qty > 0 or damaged_qty > 0) else "RECEIVED"
        stage(t, target_stage, user, accepted_qty, body.note)
        if body.accepted is not None and (missing_qty > 0 or damaged_qty > 0):
            t.status = "PARTIALLY_RECEIVED"
            if body.verified:
                t.verified = True
        else:
            t.status = "RECEIVED"

    elif body.status == "COMPLETED":
        if not body.verified:
            raise HTTPException(422, "Confirm the received equipment was physically checked before completing")
        t.verified = True
        t.return_idempotency_key = body.idempotency_key
        if t.kind == "return":
            complete_return(
                db,
                festival.id,
                t,
                r,
                req,
                damaged=body.damaged or 0,
                good_returned=body.good_returned,
                note=body.note,
                user=user,
            )
        stage(t, "COMPLETED", user, t.quantity_received, body.note)

    record(
        db,
        user,
        festival.id,
        f"equipment.transfer.{t.status.lower()}",
        "equipment_transfer",
        t.id,
        f"{user.name} marked {t.kind} of {r.name} ({t.from_location} -> {t.to_location}) {t.status}",
        reason=body.note,
        after={"quantity": qty, "status": t.status},
    )
    db.flush()
    if req:
        returns = list(db.scalars(select(EquipmentTransfer).where(EquipmentTransfer.request_id == req.id, EquipmentTransfer.kind == "return")))
        allocs = list(db.scalars(select(EquipmentAllocation).where(EquipmentAllocation.request_id == req.id)))
        if returns and all(x.status == "COMPLETED" for x in returns) and all(a.status == "returned" for a in allocs if a.is_in_place):
            req.status = "closed"
    db.commit()
    return request_rows(db, [req])[0] if req else transfer_row(t, {r.id: r})


def complete_return(
    db: Session,
    fid: int,
    t: EquipmentTransfer,
    r: Resource,
    req: EquipmentRequest | None,
    damaged: int,
    good_returned: int | None,
    note: str | None,
    user: User,
) -> None:
    returned = t.quantity_received or 0
    if damaged > returned:
        raise HTTPException(422, f"Damaged {damaged} cannot exceed the {returned} returned")
    good_back = good_returned if good_returned is not None else 0
    if good_back + damaged > returned:
        raise HTTPException(422, f"Good ({good_back}) + damaged ({damaged}) cannot exceed returned ({returned})")
    needs_check_back = returned - good_back - damaged

    parent = db.get(EquipmentTransfer, t.parent_id) if t.parent_id else None
    held = parent.reserved_quantity if parent else t.quantity_planned
    missing = max(0, held - returned)

    event_loc = t.from_location
    event_good = row_for(db, fid, r, event_loc, "good")
    event_good.reserved_quantity = max(0, event_good.reserved_quantity - held)
    event_good.quantity = max(0, event_good.quantity - held)
    event_good.version = (event_good.version or 1) + 1

    if parent:
        parent.reserved_quantity = 0
        if parent.allocation_id:
            alloc = db.get(EquipmentAllocation, parent.allocation_id)
            if alloc:
                alloc.status = "returned"
    if req:
        adjust_allocation(db, fid, r.id, req.event_id, -held)

    if good_back > 0:
        dest_good = row_for(db, fid, r, t.to_location, "good")
        dest_good.quantity += good_back
        dest_good.version = (dest_good.version or 1) + 1
    if needs_check_back > 0:
        dest_nc = row_for(db, fid, r, t.to_location, "needs check")
        dest_nc.quantity += needs_check_back
        dest_nc.version = (dest_nc.version or 1) + 1
    if damaged > 0:
        dest_rep = row_for(db, fid, r, t.to_location, "under repair")
        dest_rep.quantity += damaged
        dest_rep.version = (dest_rep.version or 1) + 1

    t.quantity_damaged = damaged
    t.quantity_good_returned = good_back
    t.quantity_needs_check_returned = needs_check_back
    t.quantity_missing = missing
    if missing > 0:
        t.discrepancy_status = "open"
    t.condition_note = note
    parts = [f"{returned} of {held} returned"]
    if needs_check_back:
        parts.append(f"{needs_check_back} -> Needs check at {t.to_location}")
    if good_back:
        parts.append(f"{good_back} inspected Good at {t.to_location}")
    if damaged:
        parts.append(f"{damaged} damaged -> Under repair at {t.to_location}")
    if missing:
        parts.append(f"{missing} missing")
    t.discrepancy_note = "; ".join(parts)

    e = db.get(Event, req.event_id) if req else None
    log_movement(
        db,
        fid,
        user,
        action="return_completed",
        item=r.name,
        quantity=returned,
        resource_code=r.code,
        from_location=t.from_location,
        to_location=t.to_location,
        from_condition="Committed (Event)",
        to_condition="Needs check" if needs_check_back and not good_back else "Returned",
        event_code=e.code if e else None,
        request_code=req.code if req else None,
        transfer_id=t.id,
        status="returned",
        reason=note,
    )
    record(
        db,
        user,
        fid,
        "equipment.return.inspected",
        "resource",
        r.code,
        f"{user.name} completed return of {r.name}: {'; '.join(parts)}",
        after={"returned": returned, "good": good_back, "needs_check": needs_check_back, "damaged": damaged, "missing": missing},
        reason=note,
    )


class ReturnIn(BaseModel):
    destination: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=400)
    complete_immediately: bool = False
    returned_quantity: int | None = Field(default=None, ge=0, le=100000)
    good_returned: int | None = Field(default=None, ge=0, le=100000)
    damaged: int = Field(default=0, ge=0, le=100000)


@router.post("/requests/{code}/return", status_code=201, dependencies=[Depends(require("equipment.receive"))])
def create_return(
    code: str,
    body: ReturnIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    req = by_code(db, EquipmentRequest, festival.id, code)
    e = db.get(Event, req.event_id)
    if not event_done(e):
        raise HTTPException(409, f"{e.name} has not finished yet. Returns start after the event ends.")

    deliveries = list(
        db.scalars(
            select(EquipmentTransfer).where(
                EquipmentTransfer.request_id == req.id,
                EquipmentTransfer.kind == "delivery",
                EquipmentTransfer.status != "CANCELLED",
            )
        )
    )
    in_place_allocs = list(
        db.scalars(
            select(EquipmentAllocation).where(
                EquipmentAllocation.request_id == req.id,
                EquipmentAllocation.is_in_place.is_(True),
                EquipmentAllocation.status != "returned",
            )
        )
    )
    existing_returns = {
        t.parent_id
        for t in db.scalars(select(EquipmentTransfer).where(EquipmentTransfer.request_id == req.id, EquipmentTransfer.kind == "return"))
    }
    created = 0

    for alloc in in_place_allocs:
        r = db.scalar(select(Resource).where(Resource.id == alloc.resource_id).with_for_update())
        held = alloc.qty
        if held <= 0 or not r:
            continue
        ret_qty = held if body.returned_quantity is None else min(held, body.returned_quantity)
        dmg_qty = min(ret_qty, body.damaged or 0)
        good_qty = min(ret_qty - dmg_qty, body.good_returned or 0)
        nc_qty = ret_qty - good_qty - dmg_qty

        r.reserved_quantity = max(0, r.reserved_quantity - held)
        r.quantity = max(0, r.quantity - held)
        r.version = (r.version or 1) + 1
        to_loc = (body.destination or alloc.source_location).strip()
        if good_qty > 0:
            row_for(db, festival.id, r, to_loc, "good").quantity += good_qty
        if nc_qty > 0:
            row_for(db, festival.id, r, to_loc, "needs check").quantity += nc_qty
        if dmg_qty > 0:
            row_for(db, festival.id, r, to_loc, "under repair").quantity += dmg_qty
        alloc.status = "returned"
        adjust_allocation(db, festival.id, r.id, req.event_id, -held)
        log_movement(
            db,
            festival.id,
            user,
            action="return_completed",
            item=r.name,
            quantity=ret_qty,
            resource_code=r.code,
            from_location=req.destination,
            to_location=to_loc,
            from_condition="Committed (In Place)",
            to_condition="Needs check" if nc_qty and not good_qty else "Returned",
            event_code=e.code if e else None,
            request_code=req.code,
            status="returned",
            reason=body.note,
        )
        created += 1

    for d in deliveries:
        if d.id in existing_returns or d.reserved_quantity <= 0:
            continue
        planned_qty = d.quantity_accepted if d.quantity_accepted is not None else (d.quantity_received if d.quantity_received is not None else d.reserved_quantity)
        t = EquipmentTransfer(
            festival_id=festival.id,
            request_id=req.id,
            request_line_id=d.request_line_id,
            allocation_id=d.allocation_id,
            parent_id=d.id,
            resource_id=d.resource_id,
            kind="return",
            from_location=req.destination,
            to_location=(body.destination or d.from_location).strip(),
            quantity_planned=planned_qty,
        )
        stage(t, "REQUESTED", user, t.quantity_planned, body.note)
        db.add(t)
        db.flush()
        if body.complete_immediately:
            r = db.scalar(select(Resource).where(Resource.id == d.resource_id).with_for_update())
            ret_qty = planned_qty if body.returned_quantity is None else body.returned_quantity
            t.quantity_approved = planned_qty
            t.quantity_dispatched = ret_qty
            t.quantity_received = ret_qty
            t.verified = True
            complete_return(
                db,
                festival.id,
                t,
                r,
                req,
                damaged=body.damaged or 0,
                good_returned=body.good_returned,
                note=body.note,
                user=user,
            )
            stage(t, "COMPLETED", user, ret_qty, body.note)
        created += 1

    if not created:
        raise HTTPException(409, "Nothing is out for this request, or returns are already open")

    all_returns = list(db.scalars(select(EquipmentTransfer).where(EquipmentTransfer.request_id == req.id, EquipmentTransfer.kind == "return")))
    all_allocs = list(db.scalars(select(EquipmentAllocation).where(EquipmentAllocation.request_id == req.id)))
    if all(x.status == "COMPLETED" for x in all_returns) and all(a.status == "returned" for a in all_allocs if a.is_in_place):
        req.status = "closed"
    else:
        req.status = "returning"

    record(
        db,
        user,
        festival.id,
        "equipment.return.request",
        "equipment_request",
        req.code,
        f"{user.name} recorded return for {req.code} ({e.name})",
        reason=body.note,
    )
    db.commit()
    return request_rows(db, [req])[0]


class DiscrepancyResolveIn(BaseModel):
    resolution: str = Field(pattern="^(found|lost)$")
    location: str | None = Field(default=None, max_length=120)
    note: str = Field(min_length=3, max_length=400)


@router.post("/transfers/{transfer_id}/resolve-discrepancy", dependencies=[Depends(require("equipment.approve"))])
def resolve_discrepancy(
    transfer_id: int,
    body: DiscrepancyResolveIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    t = db.scalar(
        select(EquipmentTransfer)
        .where(EquipmentTransfer.id == transfer_id, EquipmentTransfer.festival_id == festival.id)
        .with_for_update()
    )
    if not t:
        raise HTTPException(404, "Transfer not found")
    if t.discrepancy_status != "open":
        raise HTTPException(409, "Transfer does not have an open discrepancy")
    r = db.scalar(select(Resource).where(Resource.id == t.resource_id).with_for_update())
    missing_qty = t.quantity_missing or 0
    if body.resolution == "found" and missing_qty > 0:
        loc = (body.location or t.to_location or r.location or "Unknown").strip()
        dest_nc = row_for(db, festival.id, r, loc, "needs check")
        dest_nc.quantity += missing_qty
        dest_nc.version = (dest_nc.version or 1) + 1
    t.discrepancy_status = f"resolved_{body.resolution}"
    t.discrepancy_resolved_by = user.name
    t.discrepancy_resolved_at = now()
    t.discrepancy_resolution_note = body.note
    log_movement(
        db,
        festival.id,
        user,
        action=f"discrepancy_{body.resolution}",
        item=r.name if r else "Unknown",
        quantity=missing_qty,
        resource_code=r.code if r else None,
        from_location=t.from_location,
        to_location=body.location or t.to_location,
        transfer_id=t.id,
        status=t.discrepancy_status,
        reason=body.note,
    )
    record(
        db,
        user,
        festival.id,
        "equipment.discrepancy.resolve",
        "equipment_transfer",
        t.id,
        f"{user.name} resolved {missing_qty}-unit discrepancy on transfer #{t.id} as {body.resolution}",
        reason=body.note,
    )
    db.commit()
    return transfer_row(t, {r.id: r} if r else {})


# ---------- Campus Stock, Inspection, Repair, Mid-Fest Failure, External Receipt ----------

@router.get("/stock", dependencies=[Depends(require("equipment.read"))])
def list_campus_stock(
    q: str | None = None,
    location: str | None = None,
    condition: str | None = None,
    shortage_only: bool = False,
    festival: Festival = Depends(festival_access),
    db: Session = Depends(get_db),
):
    fid = festival.id
    rows = list(
        db.scalars(
            select(Resource)
            .where(Resource.festival_id == fid, Resource.kind == "logistics")
            .order_by(Resource.name, Resource.location, Resource.code)
        )
    )
    loc_map = location_lookup(db, fid)
    aliases = alias_map(db, fid)
    items_cat = logistics_items(db, fid)

    all_events = list(db.scalars(select(Event).where(Event.festival_id == fid, Event.deleted_at.is_(None))))
    demanding_events: dict[str, set[str]] = {i: set() for i in items_cat}
    unconfirmed_demand: dict[str, int] = {i: 0 for i in items_cat}
    for e in all_events:
        for n in parse_needs(e.equipment_needs, items_cat, aliases):
            if n["item"]:
                demanding_events[n["item"]].add(e.code)
                if n["quantity"] is None:
                    unconfirmed_demand[n["item"]] += 1

    open_lines = list(
        db.scalars(
            select(EquipmentRequestLine)
            .join(EquipmentRequest, EquipmentRequest.id == EquipmentRequestLine.request_id)
            .where(
                EquipmentRequestLine.festival_id == fid,
                EquipmentRequest.status.in_(("submitted", "partially_approved")),
            )
        )
    )
    shortage_by_item: dict[str, int] = {i: 0 for i in items_cat}
    for ln in open_lines:
        snap = availability(db, fid, ln.item, ln.confirmed_qty, None)
        shortage_by_item[ln.item] = max(shortage_by_item.get(ln.item, 0), snap["shortage"])

    by_item: dict[str, list[Resource]] = {}
    for r in rows:
        if q and q.strip().lower() not in r.name.lower() and q.strip().lower() not in (r.location or "").lower():
            continue
        by_item.setdefault(r.name, []).append(r)

    result = []
    for item_name, item_rows in sorted(by_item.items()):
        if location and not any((r.location or "").lower() == location.strip().lower() for r in item_rows):
            continue
        if condition and not any(cond(r) == condition.strip().lower() and r.quantity > 0 for r in item_rows):
            continue

        loc_buckets: dict[str, dict[str, Any]] = {}
        for r in item_rows:
            loc_name = r.location or "Unknown"
            loc_info = loc_map.get(loc_name)
            b = loc_buckets.setdefault(
                loc_name,
                {
                    "location": loc_name,
                    "position_unverified": bool(loc_info.position_unverified) if loc_info else (loc_name == "Van 1"),
                    "position_note": loc_info.position_note if loc_info else None,
                    "good": 0,
                    "committed": 0,
                    "deployable": 0,
                    "in_transit": 0,
                    "needs_check": 0,
                    "under_repair": 0,
                    "total": 0,
                    "codes": {},
                },
            )
            c = cond(r)
            b["in_transit"] += r.in_transit_quantity or 0
            if c == "good":
                b["good"] += r.quantity
                b["committed"] += r.reserved_quantity
                b["deployable"] += max(0, r.quantity - r.reserved_quantity)
                b["codes"]["good"] = r.code
            elif c == "needs check":
                b["needs_check"] += r.quantity
                b["codes"]["needs_check"] = r.code
            elif c == "under repair":
                b["under_repair"] += r.quantity
                b["codes"]["under_repair"] = r.code
            b["total"] += r.quantity + (r.in_transit_quantity or 0)

        good_sum = sum(b["good"] for b in loc_buckets.values())
        committed_sum = sum(b["committed"] for b in loc_buckets.values())
        deployable_sum = sum(b["deployable"] for b in loc_buckets.values())
        in_transit_sum = sum(b["in_transit"] for b in loc_buckets.values())
        nc_sum = sum(b["needs_check"] for b in loc_buckets.values())
        rep_sum = sum(b["under_repair"] for b in loc_buckets.values())
        tot_sum = good_sum + nc_sum + rep_sum + in_transit_sum
        short_val = shortage_by_item.get(item_name, 0)

        if shortage_only and short_val <= 0 and deployable_sum > 0:
            continue

        result.append({
            "item": item_name,
            "total": tot_sum,
            "good": good_sum,
            "committed": committed_sum,
            "deployable": deployable_sum,
            "in_transit": in_transit_sum,
            "needs_check": nc_sum,
            "under_repair": rep_sum,
            "demanding_events_count": len(demanding_events.get(item_name, set())),
            "unconfirmed_events_count": unconfirmed_demand.get(item_name, 0),
            "shortage": short_val,
            "locations": list(loc_buckets.values()),
        })
    return {"items": result}


@router.get("/stock/{item}", dependencies=[Depends(require("equipment.read"))])
def item_detail(item: str, festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    fid = festival.id
    rows = list(
        db.scalars(
            select(Resource)
            .where(Resource.festival_id == fid, Resource.kind == "logistics", func.lower(Resource.name) == item.strip().lower())
            .order_by(Resource.location, Resource.code)
        )
    )
    if not rows:
        raise HTTPException(404, f"Item '{item}' not found")
    canonical = rows[0].name
    items_cat = logistics_items(db, fid)
    aliases = alias_map(db, fid)

    all_events = list(db.scalars(select(Event).where(Event.festival_id == fid, Event.deleted_at.is_(None)).order_by(Event.start_at, Event.code)))
    reqs = request_rows(db, list(db.scalars(select(EquipmentRequest).where(EquipmentRequest.festival_id == fid))))
    reqs_by_event: dict[str, list[dict[str, Any]]] = {}
    for r in reqs:
        if r["event"]:
            reqs_by_event.setdefault(r["event"]["code"], []).append(r)

    event_demands = []
    for e in all_events:
        parsed = [n for n in parse_needs(e.equipment_needs, items_cat, aliases) if n["item"] == canonical]
        ev_reqs = [
            (r, ln)
            for r in reqs_by_event.get(e.code, [])
            for ln in (r.get("lines") or [])
            if ln["item"] == canonical
        ]
        if not parsed and not ev_reqs:
            continue
        p_first = parsed[0] if parsed else None
        r_first, ln_first = ev_reqs[0] if ev_reqs else (None, None)
        event_demands.append({
            "event_code": e.code,
            "event_name": e.name,
            "venue": e.venue.name if e.venue else None,
            "start_at": e.start_at,
            "end_at": e.end_at,
            "declared_raw": p_first["raw"] if p_first else None,
            "parsed_quantity": p_first["quantity"] if p_first else None,
            "quantity_confirmed": ln_first["quantity"] if ln_first else (p_first["quantity"] if p_first else None),
            "is_confirmed": ln_first is not None,
            "request_code": r_first["code"] if r_first else None,
            "request_status": r_first["status"] if r_first else "not_requested",
            "allocated_quantity": ln_first["quantity_approved"] if ln_first else 0,
            "delivered_quantity": ln_first["quantity_delivered"] if ln_first else 0,
            "readiness": r_first["readiness"] if r_first else "not_requested",
        })

    history = list(
        db.scalars(
            select(MovementLedger)
            .where(MovementLedger.festival_id == fid, func.lower(MovementLedger.item) == canonical.lower())
            .order_by(MovementLedger.id.desc())
            .limit(40)
        )
    )
    return {
        "item": canonical,
        "rows": [
            {
                "id": r.id,
                "code": r.code,
                "location": r.location,
                "condition": r.condition,
                "quantity": r.quantity,
                "reserved_quantity": r.reserved_quantity,
                "in_transit_quantity": r.in_transit_quantity or 0,
                "deployable": max(0, r.quantity - r.reserved_quantity) if cond(r) == "good" else 0,
            }
            for r in rows
        ],
        "events": event_demands,
        "history": [ledger_row(h) for h in history],
    }


class InspectIn(BaseModel):
    passed: int = Field(ge=0, le=100000)
    failed: int = Field(ge=0, le=100000)
    note: str = Field(min_length=3, max_length=400)


@router.post("/resources/{code}/inspect", dependencies=[Depends(require("equipment.approve"))])
def inspect_resource(
    code: str,
    body: InspectIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    r = db.scalar(select(Resource).where(Resource.festival_id == festival.id, Resource.code == code).with_for_update())
    if not r or r.kind != "logistics":
        raise HTTPException(404, f"Inventory line {code} not found")
    if cond(r) != "needs check":
        raise HTTPException(409, f"{code} is '{r.condition}', not 'needs check'")
    if body.passed + body.failed == 0 or body.passed + body.failed > r.quantity:
        raise HTTPException(422, f"Inspect between 1 and {r.quantity} units")
    before = r.quantity
    r.quantity -= body.passed + body.failed
    r.version = (r.version or 1) + 1
    if body.passed:
        good_r = row_for(db, festival.id, r, r.location or "Unknown", "good")
        good_r.quantity += body.passed
        good_r.version = (good_r.version or 1) + 1
    if body.failed:
        rep_r = row_for(db, festival.id, r, r.location or "Unknown", "under repair")
        rep_r.quantity += body.failed
        rep_r.version = (rep_r.version or 1) + 1

    log_movement(
        db,
        festival.id,
        user,
        action="inspect",
        item=r.name,
        quantity=body.passed + body.failed,
        resource_code=r.code,
        from_location=r.location,
        to_location=r.location,
        from_condition="Needs check",
        to_condition=f"Good ({body.passed}), Repair ({body.failed})",
        status="inspected",
        reason=body.note,
    )
    unblocked = recompute_open_request_shortages(db, festival.id, r.name)
    record(
        db,
        user,
        festival.id,
        "equipment.inspect",
        "resource",
        code,
        f"{user.name} inspected {body.passed + body.failed} {r.name} at {r.location}: {body.passed} good, {body.failed} to repair",
        before={"needs_check": before},
        after={"needs_check": r.quantity, "passed": body.passed, "failed": body.failed},
        reason=body.note,
    )
    db.commit()
    return {
        "code": code,
        "item": r.name,
        "location": r.location,
        "needs_check_remaining": r.quantity,
        "passed": body.passed,
        "failed": body.failed,
        "unblocked_requests": unblocked,
    }


class RepairIn(BaseModel):
    repaired_to_good: int = Field(ge=0, le=100000)
    repaired_to_needs_check: int = Field(default=0, ge=0, le=100000)
    note: str = Field(min_length=3, max_length=400)


@router.post("/resources/{code}/repair", dependencies=[Depends(require("equipment.approve"))])
def repair_resource(
    code: str,
    body: RepairIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    r = db.scalar(select(Resource).where(Resource.festival_id == festival.id, Resource.code == code).with_for_update())
    if not r or r.kind != "logistics":
        raise HTTPException(404, f"Inventory line {code} not found")
    if cond(r) != "under repair":
        raise HTTPException(409, f"{code} is '{r.condition}', not 'under repair'")
    total = body.repaired_to_good + body.repaired_to_needs_check
    if total <= 0 or total > r.quantity:
        raise HTTPException(422, f"Repair between 1 and {r.quantity} units")
    r.quantity -= total
    r.version = (r.version or 1) + 1
    if body.repaired_to_good:
        good_r = row_for(db, festival.id, r, r.location or "Unknown", "good")
        good_r.quantity += body.repaired_to_good
        good_r.version = (good_r.version or 1) + 1
    if body.repaired_to_needs_check:
        nc_r = row_for(db, festival.id, r, r.location or "Unknown", "needs check")
        nc_r.quantity += body.repaired_to_needs_check
        nc_r.version = (nc_r.version or 1) + 1

    log_movement(
        db,
        festival.id,
        user,
        action="repair",
        item=r.name,
        quantity=total,
        resource_code=r.code,
        from_location=r.location,
        to_location=r.location,
        from_condition="Under repair",
        to_condition=f"Good ({body.repaired_to_good}), Needs check ({body.repaired_to_needs_check})",
        status="repaired",
        reason=body.note,
    )
    unblocked = recompute_open_request_shortages(db, festival.id, r.name)
    record(
        db,
        user,
        festival.id,
        "equipment.repair",
        "resource",
        code,
        f"{user.name} marked {total} {r.name} repaired at {r.location}: {body.repaired_to_good} good, {body.repaired_to_needs_check} needs check",
        reason=body.note,
    )
    db.commit()
    return {
        "code": code,
        "item": r.name,
        "location": r.location,
        "under_repair_remaining": r.quantity,
        "repaired_to_good": body.repaired_to_good,
        "repaired_to_needs_check": body.repaired_to_needs_check,
        "unblocked_requests": unblocked,
    }


class FailureIn(BaseModel):
    quantity: int = Field(ge=1, le=100000)
    event_code: str | None = Field(default=None, max_length=20)
    note: str = Field(min_length=3, max_length=400)


@router.post("/resources/{code}/report-failure", dependencies=[Depends(require("equipment.approve"))])
def report_failure(
    code: str,
    body: FailureIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    r = db.scalar(select(Resource).where(Resource.festival_id == festival.id, Resource.code == code).with_for_update())
    if not r or r.kind != "logistics":
        raise HTTPException(404, f"Inventory line {code} not found")
    if cond(r) == "under repair":
        raise HTTPException(409, f"{code} is already 'under repair'")
    if body.quantity > r.quantity:
        raise HTTPException(422, f"Cannot fail {body.quantity} units; only {r.quantity} on hand at {r.location}")

    r.quantity -= body.quantity
    r.version = (r.version or 1) + 1
    rep_r = row_for(db, festival.id, r, r.location or "Unknown", "under repair")
    rep_r.quantity += body.quantity
    rep_r.version = (rep_r.version or 1) + 1

    affected_events = []
    if r.reserved_quantity > r.quantity or body.event_code:
        committed_cut = max(0, r.reserved_quantity - r.quantity)
        if committed_cut > 0:
            r.reserved_quantity = r.quantity

        alloc_q = select(EquipmentAllocation).where(
            EquipmentAllocation.festival_id == festival.id,
            EquipmentAllocation.resource_id == r.id,
            EquipmentAllocation.status.in_(("allocated", "received", "partially_received")),
        )
        active_allocs = list(db.scalars(alloc_q))
        cut_left = committed_cut if committed_cut > 0 else (body.quantity if body.event_code else 0)
        for alloc in active_allocs:
            req = db.get(EquipmentRequest, alloc.request_id)
            ev = db.get(Event, req.event_id) if req else None
            if body.event_code and ev and ev.code != body.event_code:
                continue
            if cut_left <= 0 and not body.event_code:
                break
            reduce_by = min(alloc.qty, cut_left or body.quantity)
            if reduce_by >= alloc.qty:
                alloc.status = "released"
            else:
                alloc.qty -= reduce_by
            cut_left = max(0, cut_left - reduce_by)
            if req:
                req.has_shortage = True
                if req.status == "approved":
                    req.status = "partially_approved"
                if alloc.request_line_id:
                    ln = db.get(EquipmentRequestLine, alloc.request_line_id)
                    if ln:
                        ln.approved_qty = max(0, ln.approved_qty - reduce_by)
                        ln.shortage_qty += reduce_by
                affected_events.append({
                    "request_code": req.code,
                    "event_code": ev.code if ev else None,
                    "event_name": ev.name if ev else None,
                    "item": r.name,
                    "shortfall_created": reduce_by,
                })

    recompute_open_request_shortages(db, festival.id, r.name)
    log_movement(
        db,
        festival.id,
        user,
        action="report_failure",
        item=r.name,
        quantity=body.quantity,
        resource_code=r.code,
        from_location=r.location,
        to_location=r.location,
        from_condition=r.condition,
        to_condition="Under repair",
        event_code=body.event_code,
        status="failed",
        reason=body.note,
    )
    record(
        db,
        user,
        festival.id,
        "equipment.failure",
        "resource",
        code,
        f"{user.name} reported failure of {body.quantity} {r.name} at {r.location}",
        reason=body.note,
        after={"affected_events": affected_events},
    )
    db.commit()
    return {
        "code": code,
        "item": r.name,
        "location": r.location,
        "failed_quantity": body.quantity,
        "remaining_good": r.quantity,
        "affected_events": affected_events,
    }


class ExternalReceiptIn(BaseModel):
    item: str = Field(min_length=2, max_length=60)
    location: str = Field(min_length=2, max_length=120)
    condition: str = Field(default="good", pattern="^(good|needs check|under repair)$")
    quantity: int = Field(ge=1, le=100000)
    note: str = Field(min_length=3, max_length=400)


@router.post("/resources/external-receipt", status_code=201, dependencies=[Depends(require("equipment.approve"))])
def external_receipt(
    body: ExternalReceiptIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    template = db.scalar(
        select(Resource).where(
            Resource.festival_id == festival.id,
            Resource.kind == "logistics",
            func.lower(Resource.name) == body.item.strip().lower(),
        )
    )
    if not template:
        raise HTTPException(422, f"'{body.item}' is not in the campus logistics catalogue")
    target = row_for(db, festival.id, template, body.location.strip(), body.condition)
    target.quantity += body.quantity
    target.version = (target.version or 1) + 1
    unblocked = recompute_open_request_shortages(db, festival.id, template.name)
    log_movement(
        db,
        festival.id,
        user,
        action="external_receipt",
        item=template.name,
        quantity=body.quantity,
        resource_code=target.code,
        from_location="External / Rental",
        to_location=target.location,
        from_condition="External",
        to_condition=target.condition,
        status="received",
        reason=body.note,
    )
    record(
        db,
        user,
        festival.id,
        "equipment.external_receipt",
        "resource",
        target.code,
        f"{user.name} received {body.quantity} external {template.name} at {target.location}",
        reason=body.note,
    )
    db.commit()
    return {
        "code": target.code,
        "item": target.name,
        "location": target.location,
        "condition": target.condition,
        "quantity": target.quantity,
        "unblocked_requests": unblocked,
    }


# ---------- Movement History Ledger ----------

def ledger_row(m: MovementLedger) -> dict[str, Any]:
    return {
        "id": m.id,
        "action": m.action,
        "item": m.item,
        "quantity": m.qty,
        "resource_code": m.resource_code,
        "from_location": m.from_location,
        "to_location": m.to_location,
        "from_condition": m.from_condition,
        "to_condition": m.to_condition,
        "event_code": m.event_code,
        "request_code": m.request_code,
        "transfer_id": m.transfer_id,
        "status": m.status,
        "actor_name": m.actor_name,
        "reason": m.reason,
        "created_at": m.created_at,
    }


def filtered_ledger_query(
    fid: int,
    item: str | None = None,
    location: str | None = None,
    event_code: str | None = None,
    action: str | None = None,
    actor: str | None = None,
    q: str | None = None,
):
    query = select(MovementLedger).where(MovementLedger.festival_id == fid)
    if item:
        query = query.where(func.lower(MovementLedger.item) == item.strip().lower())
    if location:
        loc = location.strip().lower()
        query = query.where(
            or_(
                func.lower(MovementLedger.from_location) == loc,
                func.lower(MovementLedger.to_location) == loc,
            )
        )
    if event_code:
        query = query.where(func.lower(MovementLedger.event_code) == event_code.strip().lower())
    if action:
        query = query.where(MovementLedger.action == action.strip())
    if actor:
        query = query.where(MovementLedger.actor_name.ilike(f"%{actor.strip()}%"))
    if q:
        term = f"%{q.strip()}%"
        query = query.where(
            or_(
                MovementLedger.item.ilike(term),
                MovementLedger.request_code.ilike(term),
                MovementLedger.event_code.ilike(term),
                MovementLedger.from_location.ilike(term),
                MovementLedger.to_location.ilike(term),
                MovementLedger.reason.ilike(term),
            )
        )
    return query.order_by(MovementLedger.id.desc())


@router.get("/ledger", dependencies=[Depends(require("equipment.read"))])
def list_ledger(
    item: str | None = None,
    location: str | None = None,
    event_code: str | None = None,
    action: str | None = None,
    actor: str | None = None,
    q: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
    festival: Festival = Depends(festival_access),
    db: Session = Depends(get_db),
):
    rows = list(db.scalars(filtered_ledger_query(festival.id, item, location, event_code, action, actor, q).limit(limit)))
    return {"items": [ledger_row(m) for m in rows]}


@router.get("/ledger/export.csv", dependencies=[Depends(require("equipment.read"))])
def export_ledger_csv(
    item: str | None = None,
    location: str | None = None,
    event_code: str | None = None,
    action: str | None = None,
    actor: str | None = None,
    q: str | None = None,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    rows = list(db.scalars(filtered_ledger_query(festival.id, item, location, event_code, action, actor, q)))
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([
        "id", "created_at", "action", "item", "quantity", "resource_code",
        "from_location", "to_location", "from_condition", "to_condition",
        "event_code", "request_code", "transfer_id", "status", "actor_name", "reason",
    ])
    for m in rows:
        writer.writerow([
            csv_safe(m.id),
            csv_safe(m.created_at.isoformat() if m.created_at else ""),
            csv_safe(m.action),
            csv_safe(m.item),
            csv_safe(m.qty),
            csv_safe(m.resource_code),
            csv_safe(m.from_location),
            csv_safe(m.to_location),
            csv_safe(m.from_condition),
            csv_safe(m.to_condition),
            csv_safe(m.event_code),
            csv_safe(m.request_code),
            csv_safe(m.transfer_id),
            csv_safe(m.status),
            csv_safe(m.actor_name),
            csv_safe(m.reason),
        ])
    record(db, user, festival.id, "equipment.ledger.export", "movement_ledger", "report", f"{user.name} exported {len(rows)} movement ledger rows")
    db.commit()
    return StreamingResponse(
        iter([out.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="inventory_movement_ledger.csv"'},
    )

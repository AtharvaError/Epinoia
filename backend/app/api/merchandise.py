"""Merchandise & Kit Distribution module for FestOS.

Covers:
1. Stock and demand truth from inventory_merch.csv (10 rows), participants.csv (2,834 after excluding P_DUP01/P_DUP02),
   core_team.csv (48 members), and contingents.csv (38 contingents, 76 leaders/escorts).
2. Confirmable assumptions (A1-A4), entitlement rules, kit templates, and staff/leader quotas.
3. Deterministic Distribution Plan Engine (P1 Arrival order, P2 Equal wave share, P3 Equal contingent share),
   policy comparison, plan approval (plan_reserved separate from csv_reserved), stale detection, and re-plan diff.
4. Wave Prep & Tray Packing sheets (printable A4 per contingent/wave + CSV export).
5. Express Issue Desk & On-Spot Handover (atomic kit handover, QR/ID/phone/name lookup with strict privacy,
   duplicate blocking for P_DUP01/P_DUP02, DB unique constraint against double-issue, size substitution with
   recorded consent, 60-second Undo, exchange/void, wrong-size claim, contingent queue mode, paper reconciliation).
6. Owed list sorted by departure time with Communication prefill & CSV export, plus append-only MerchLedger.
"""
import csv
import io
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.common import by_code, csv_safe
from app.auth.deps import current_user, festival_access, permissions_of, require
from app.database import get_db
from app.models import (
    Contingent,
    DataIssue,
    Event,
    Festival,
    MerchConfig,
    MerchIncoming,
    MerchIssue,
    MerchItem,
    MerchLedger,
    MerchOwed,
    Participant,
    Registration,
    User,
)
from app.services.audit import record

router = APIRouter(prefix="/api/festivals/{festival_id}/merchandise", tags=["merchandise"])

SIZES = ["S", "M", "L", "XL", "XXL"]
ADJACENT_SIZES = {
    "S": ["M"],
    "M": ["L", "S"],
    "L": ["XL", "M"],
    "XL": ["XXL", "L"],
    "XXL": ["XL"],
}
UP_PREFERRED_SIZES = {
    "S": ["M"],
    "M": ["L"],
    "L": ["XL"],
    "XL": ["XXL"],
    "XXL": ["XL"],
}


def now() -> datetime:
    return datetime.now(timezone.utc)


def get_or_create_config(db: Session, fid: int) -> MerchConfig:
    cfg = db.scalar(select(MerchConfig).where(MerchConfig.festival_id == fid))
    if not cfg:
        from app.services.pack_import import ensure_pack_extensions
        ensure_pack_extensions(db, fid)
        cfg = db.scalar(select(MerchConfig).where(MerchConfig.festival_id == fid))
    return cfg


def log_merch(
    db: Session,
    fid: int,
    user: User | None,
    *,
    action: str,
    item: str,
    size: str = "NA",
    qty_delta: int = 0,
    merch_item_id: int | None = None,
    participant_code: str | None = None,
    participant_name: str | None = None,
    desk: str | None = None,
    status: str = "completed",
    is_exception: bool = False,
    reason: str | None = None,
) -> MerchLedger:
    entry = MerchLedger(
        festival_id=fid,
        merch_item_id=merch_item_id,
        action=action,
        item=item,
        size=size,
        qty_delta=qty_delta,
        participant_code=participant_code,
        participant_name=participant_name,
        desk=desk,
        actor_id=user.id if user else None,
        actor_name=user.name if user else "System",
        status=status,
        reason=reason,
        is_exception=is_exception,
    )
    db.add(entry)
    return entry


def mark_plan_stale_if_needed(db: Session, fid: int, reason: str) -> None:
    cfg = db.scalar(select(MerchConfig).where(MerchConfig.festival_id == fid))
    if cfg and cfg.approved_plan:
        cfg.plan_stale = True
        reasons = list(cfg.plan_stale_reasons or [])
        if reason not in reasons:
            reasons.append(reason)
        cfg.plan_stale_reasons = reasons


def open_duplicate_codes(db: Session, fid: int) -> dict[str, str]:
    """Returns probable duplicate codes (P_DUP01, P_DUP02) that still have an open possible_duplicate DataIssue."""
    issues = list(
        db.scalars(
            select(DataIssue).where(
                DataIssue.festival_id == fid,
                DataIssue.entity_type == "participant",
                DataIssue.issue == "possible_duplicate",
                DataIssue.status == "open",
                DataIssue.entity_code.like("P_DUP%"),
            )
        )
    )
    return {i.entity_code: i.detail or "Probable duplicate record" for i in issues}


def has_invalid_dates(p: Participant) -> bool:
    return bool(p.arrival_at and p.departure_at and p.departure_at < p.arrival_at)


def eligible_participants(db: Session, fid: int) -> tuple[list[Participant], dict[str, str], dict[int, Contingent]]:
    dup_map = open_duplicate_codes(db, fid)
    contingents = {c.id: c for c in db.scalars(select(Contingent).where(Contingent.festival_id == fid))}
    all_p = list(
        db.scalars(
            select(Participant)
            .where(Participant.festival_id == fid, Participant.deleted_at.is_(None))
            .order_by(Participant.code)
        )
    )
    eligible = [p for p in all_p if p.code not in dup_map and p.registration_status != "cancelled"]
    return eligible, dup_map, contingents


def staff_and_leaders(db: Session, fid: int) -> dict[str, Any]:
    from app.models import Department
    depts = {d.id: d.name for d in db.scalars(select(Department).where(Department.festival_id == fid))}
    staff = list(
        db.scalars(
            select(User)
            .where(User.festival_id == fid, User.member_code.is_not(None), User.is_active.is_(True))
            .order_by(User.member_code)
        )
    )
    contingents = list(db.scalars(select(Contingent).where(Contingent.festival_id == fid).order_by(Contingent.code)))
    leaders_escorts = []
    for c in contingents:
        if c.leader:
            leaders_escorts.append({
                "code": f"{c.code}-LEADER",
                "name": c.leader,
                "role": "Contingent Leader",
                "contingent_code": c.code,
                "college": c.college,
            })
        if c.escort:
            leaders_escorts.append({
                "code": f"{c.code}-ESCORT",
                "name": c.escort,
                "role": "Contingent Escort",
                "contingent_code": c.code,
                "college": c.college,
            })
    return {
        "core_team_count": len(staff),
        "core_team": [{"code": u.member_code, "name": u.name, "department": depts.get(u.department_id)} for u in staff],
        "leaders_escorts_count": len(leaders_escorts),
        "leaders_escorts": leaders_escorts,
    }


def wave_key(p: Participant) -> str:
    if has_invalid_dates(p) or not p.arrival_at:
        return "UNSCHEDULED"
    return p.arrival_at.isoformat()


def largest_remainder_quotas(pool: int, demands: dict[str, int]) -> dict[str, int]:
    """Apportions `pool` across buckets proportionally to `demands` using largest-remainder method."""
    total_demand = sum(demands.values())
    if total_demand <= 0 or pool <= 0:
        return {k: 0 for k in demands}
    if pool >= total_demand:
        return dict(demands)
    exact = {k: (pool * d) / total_demand for k, d in demands.items()}
    floored = {k: min(demands[k], int(exact[k])) for k in demands}
    rem = pool - sum(floored.values())
    order = sorted(
        demands.keys(),
        key=lambda k: (-(exact[k] - floored[k]), -demands[k], k),
    )
    for k in order:
        if rem <= 0:
            break
        if floored[k] < demands[k]:
            floored[k] += 1
            rem -= 1
    return floored


def compute_distribution_plan(
    db: Session,
    fid: int,
    policy: str = "P1",
    substitution_override: dict[str, Any] | None = None,
    staff_quota_override: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Deterministically computes a full participant kit & T-shirt distribution plan under P1, P2, or P3."""
    cfg = get_or_create_config(db, fid)
    eligible, dup_map, contingents = eligible_participants(db, fid)
    merch_rows = list(db.scalars(select(MerchItem).where(MerchItem.festival_id == fid).order_by(MerchItem.id)))
    items_by_key = {(m.item, m.size): m for m in merch_rows}

    # Existing active issues
    active_issues = list(
        db.scalars(
            select(MerchIssue).where(
                MerchIssue.festival_id == fid,
                MerchIssue.status == "issued",
            )
        )
    )
    issued_by_person_item: dict[tuple[int, str], MerchIssue] = {
        (iss.participant_id, iss.item): iss for iss in active_issues
    }

    sub_rule = substitution_override or cfg.substitution_rule or {"max_steps": 1, "direction": "up_preferred"}
    max_steps = int(sub_rule.get("max_steps", 1))
    direction = sub_rule.get("direction", "up_preferred")
    staff_quota = staff_quota_override if staff_quota_override is not None else (cfg.staff_leader_quota or {})

    pools: dict[tuple[str, str], int] = {}
    for (item_name, sz), m in items_by_key.items():
        q_key = f"{item_name}:{sz}" if sz != "NA" else item_name
        staff_hold = int(staff_quota.get(q_key, 0) or 0)
        pools[(item_name, sz)] = max(0, m.on_hand - m.csv_reserved - staff_hold)

    def p_sort_key(p: Participant):
        unsched = has_invalid_dates(p) or (p.arrival_at is None)
        arr_ts = p.arrival_at.timestamp() if (p.arrival_at and not unsched) else float("inf")
        return (1 if unsched else 0, arr_ts, p.code)

    sorted_p = sorted(eligible, key=p_sort_key)
    valid_waves = sorted({wave_key(p) for p in sorted_p if wave_key(p) != "UNSCHEDULED"})
    wave_index = {w: idx + 1 for idx, w in enumerate(valid_waves)}

    by_size: dict[str, list[Participant]] = {s: [] for s in SIZES}
    for p in sorted_p:
        sz = (p.tshirt_size or "").strip().upper()
        if sz in by_size:
            by_size[sz].append(p)

    tshirt_alloc: dict[str, dict[str, Any]] = {}
    tshirt_reserved_by_size: dict[str, int] = {s: 0 for s in SIZES}
    remaining_tshirt_pool: dict[str, int] = {s: pools.get(("T-shirt", s), 0) for s in SIZES}

    for sz in SIZES:
        group = by_size[sz]
        already_issued = [p for p in group if (p.id, "T-shirt") in issued_by_person_item]
        need_plan = [p for p in group if (p.id, "T-shirt") not in issued_by_person_item]
        for p in already_issued:
            iss = issued_by_person_item[(p.id, "T-shirt")]
            tshirt_alloc[p.code] = {
                "status": "ISSUED",
                "allocated_size": iss.issued_size,
                "declared_size": sz,
                "explanation": f"Already issued size {iss.issued_size} at desk",
            }

        pool_sz = remaining_tshirt_pool[sz]
        if policy == "P1":
            for idx, p in enumerate(need_plan, 1):
                wk = wave_key(p)
                w_label = f"Wave {wave_index[wk]}" if wk in wave_index else "Unscheduled (invalid dates)"
                if remaining_tshirt_pool[sz] > 0:
                    remaining_tshirt_pool[sz] -= 1
                    tshirt_reserved_by_size[sz] += 1
                    tshirt_alloc[p.code] = {
                        "status": "EXACT",
                        "allocated_size": sz,
                        "declared_size": sz,
                        "explanation": f"Exact {sz} reserved ({w_label}, #{idx} of {len(need_plan)} {sz} in arrival order; {pool_sz} {sz} issuable)",
                    }
                else:
                    tshirt_alloc[p.code] = {
                        "status": "SHORT",
                        "allocated_size": None,
                        "declared_size": sz,
                        "explanation": f"Short {sz} ({w_label}, #{idx} of {len(need_plan)} {sz} in arrival order; {pool_sz} {sz} exhausted)",
                    }

        elif policy == "P2":
            by_wave: dict[str, list[Participant]] = defaultdict(list)
            for p in need_plan:
                by_wave[wave_key(p)].append(p)
            demands_w = {wk: len(plist) for wk, plist in by_wave.items()}
            quotas_w = largest_remainder_quotas(pool_sz, demands_w)
            for wk, plist in by_wave.items():
                q_w = quotas_w.get(wk, 0)
                w_label = f"Wave {wave_index[wk]}" if wk in wave_index else "Unscheduled"
                for idx, p in enumerate(plist, 1):
                    if idx <= q_w and remaining_tshirt_pool[sz] > 0:
                        remaining_tshirt_pool[sz] -= 1
                        tshirt_reserved_by_size[sz] += 1
                        tshirt_alloc[p.code] = {
                            "status": "EXACT",
                            "allocated_size": sz,
                            "declared_size": sz,
                            "explanation": f"Exact {sz} reserved ({w_label} equal-share quota {q_w}/{len(plist)}, #{idx} in wave)",
                        }
                    else:
                        tshirt_alloc[p.code] = {
                            "status": "SHORT",
                            "allocated_size": None,
                            "declared_size": sz,
                            "explanation": f"Short {sz} ({w_label} equal-share quota {q_w}/{len(plist)} reached, #{idx} in wave)",
                        }

        elif policy == "P3":
            by_cont: dict[str, list[Participant]] = defaultdict(list)
            for p in need_plan:
                c_code = contingents[p.contingent_id].code if p.contingent_id in contingents else "UNKNOWN"
                by_cont[c_code].append(p)
            demands_c = {ck: len(plist) for ck, plist in by_cont.items()}
            quotas_c = largest_remainder_quotas(pool_sz, demands_c)
            for ck, plist in by_cont.items():
                q_c = quotas_c.get(ck, 0)
                for idx, p in enumerate(plist, 1):
                    if idx <= q_c and remaining_tshirt_pool[sz] > 0:
                        remaining_tshirt_pool[sz] -= 1
                        tshirt_reserved_by_size[sz] += 1
                        tshirt_alloc[p.code] = {
                            "status": "EXACT",
                            "allocated_size": sz,
                            "declared_size": sz,
                            "explanation": f"Exact {sz} reserved ({ck} equal-share quota {q_c}/{len(plist)}, #{idx} in contingent)",
                        }
                    else:
                        tshirt_alloc[p.code] = {
                            "status": "SHORT",
                            "allocated_size": None,
                            "declared_size": sz,
                            "explanation": f"Short {sz} ({ck} equal-share quota {q_c}/{len(plist)} reached, #{idx} in contingent)",
                        }

    # Propose substitutions from surplus sizes (XL has +115 surplus)
    substitute_proposed_count = 0
    if max_steps >= 1 and direction != "none":
        short_people = [p for p in sorted_p if tshirt_alloc.get(p.code, {}).get("status") == "SHORT"]
        for p in short_people:
            decl = tshirt_alloc[p.code]["declared_size"]
            candidates = UP_PREFERRED_SIZES.get(decl, []) if direction == "up_preferred" else ADJACENT_SIZES.get(decl, [])
            assigned_sub = None
            for cand_sz in candidates:
                if remaining_tshirt_pool.get(cand_sz, 0) > 0:
                    assigned_sub = cand_sz
                    remaining_tshirt_pool[cand_sz] -= 1
                    tshirt_reserved_by_size[cand_sz] += 1
                    break
            if assigned_sub:
                substitute_proposed_count += 1
                tshirt_alloc[p.code] = {
                    "status": "SUBSTITUTE_PROPOSED",
                    "allocated_size": assigned_sub,
                    "declared_size": decl,
                    "explanation": f"Owed {decl}; {assigned_sub} substitute proposed (consent required at desk) — {tshirt_alloc[p.code]['explanation']}",
                }
            else:
                tshirt_alloc[p.code]["status"] = "OWED"
                tshirt_alloc[p.code]["explanation"] = f"Owed {decl} (no adjacent surplus size available) — {tshirt_alloc[p.code]['explanation']}"
    else:
        for p in sorted_p:
            if tshirt_alloc.get(p.code, {}).get("status") == "SHORT":
                decl = tshirt_alloc[p.code]["declared_size"]
                tshirt_alloc[p.code]["status"] = "OWED"
                tshirt_alloc[p.code]["explanation"] = f"Owed {decl} — {tshirt_alloc[p.code]['explanation']}"

    kit_items = [k["item"] for k in (cfg.kit_templates or {}).get("participant", []) if k.get("item") != "T-shirt"]
    other_alloc: dict[str, dict[str, str]] = {p.code: {} for p in sorted_p}
    other_reserved: dict[str, int] = {item_name: 0 for item_name in kit_items}

    for item_name in kit_items:
        pool_item = pools.get((item_name, "NA"), 0)
        need_item = [p for p in sorted_p if (p.id, item_name) not in issued_by_person_item]
        for p in sorted_p:
            if (p.id, item_name) in issued_by_person_item:
                other_alloc[p.code][item_name] = "ISSUED"
        if policy == "P1":
            rem_pool = pool_item
            for p in need_item:
                if rem_pool > 0:
                    rem_pool -= 1
                    other_reserved[item_name] += 1
                    other_alloc[p.code][item_name] = "RESERVED"
                else:
                    other_alloc[p.code][item_name] = "OWED"
        elif policy == "P2":
            by_w: dict[str, list[Participant]] = defaultdict(list)
            for p in need_item:
                by_w[wave_key(p)].append(p)
            qw = largest_remainder_quotas(pool_item, {wk: len(pl) for wk, pl in by_w.items()})
            for wk, pl in by_w.items():
                q_val = qw.get(wk, 0)
                for idx, p in enumerate(pl, 1):
                    if idx <= q_val:
                        other_reserved[item_name] += 1
                        other_alloc[p.code][item_name] = "RESERVED"
                    else:
                        other_alloc[p.code][item_name] = "OWED"
        elif policy == "P3":
            by_c: dict[str, list[Participant]] = defaultdict(list)
            for p in need_item:
                ck = contingents[p.contingent_id].code if p.contingent_id in contingents else "UNKNOWN"
                by_c[ck].append(p)
            qc = largest_remainder_quotas(pool_item, {ck: len(pl) for ck, pl in by_c.items()})
            for ck, pl in by_c.items():
                q_val = qc.get(ck, 0)
                for idx, p in enumerate(pl, 1):
                    if idx <= q_val:
                        other_reserved[item_name] += 1
                        other_alloc[p.code][item_name] = "RESERVED"
                    else:
                        other_alloc[p.code][item_name] = "OWED"

    wave_summary: list[dict[str, Any]] = []
    for wk in valid_waves + ["UNSCHEDULED"]:
        w_people = [p for p in sorted_p if wave_key(p) == wk]
        if not w_people:
            continue
        exact_cnt = sum(1 for p in w_people if tshirt_alloc[p.code]["status"] in ("EXACT", "ISSUED"))
        sub_cnt = sum(1 for p in w_people if tshirt_alloc[p.code]["status"] == "SUBSTITUTE_PROPOSED")
        owed_cnt = sum(1 for p in w_people if tshirt_alloc[p.code]["status"] == "OWED")
        id_cnt = sum(1 for p in w_people if other_alloc[p.code].get("ID card") in ("RESERVED", "ISSUED"))
        lan_cnt = sum(1 for p in w_people if other_alloc[p.code].get("lanyard") in ("RESERVED", "ISSUED"))
        full_kits = sum(
            1
            for p in w_people
            if tshirt_alloc[p.code]["status"] in ("EXACT", "ISSUED")
            and all(other_alloc[p.code].get(ki) in ("RESERVED", "ISSUED") for ki in kit_items)
        )
        size_breakdown = {
            sz: {
                "needed": sum(1 for p in w_people if (p.tshirt_size or "").upper() == sz),
                "exact": sum(1 for p in w_people if (p.tshirt_size or "").upper() == sz and tshirt_alloc[p.code]["status"] in ("EXACT", "ISSUED")),
                "sub": sum(1 for p in w_people if (p.tshirt_size or "").upper() == sz and tshirt_alloc[p.code]["status"] == "SUBSTITUTE_PROPOSED"),
                "owed": sum(1 for p in w_people if (p.tshirt_size or "").upper() == sz and tshirt_alloc[p.code]["status"] == "OWED"),
            }
            for sz in SIZES
        }
        wave_summary.append({
            "wave_key": wk,
            "wave_number": wave_index.get(wk),
            "label": f"Wave {wave_index[wk]}" if wk in wave_index else "Unscheduled (departure < arrival)",
            "arrival_at": wk if wk != "UNSCHEDULED" else None,
            "headcount": len(w_people),
            "exact_tshirts": exact_cnt,
            "substitute_proposed": sub_cnt,
            "owed_tshirts": owed_cnt,
            "id_cards_ready": id_cnt,
            "lanyards_ready": lan_cnt,
            "full_kits_ready": full_kits,
            "partial_kits": len(w_people) - full_kits,
            "coverage_pct": round((exact_cnt / len(w_people)) * 100, 1) if w_people else 0.0,
            "sizes": size_breakdown,
        })

    contingent_summary: list[dict[str, Any]] = []
    for c in sorted(contingents.values(), key=lambda x: x.code):
        c_people = [p for p in sorted_p if p.contingent_id == c.id]
        if not c_people:
            continue
        exact_cnt = sum(1 for p in c_people if tshirt_alloc[p.code]["status"] in ("EXACT", "ISSUED"))
        sub_cnt = sum(1 for p in c_people if tshirt_alloc[p.code]["status"] == "SUBSTITUTE_PROPOSED")
        owed_cnt = sum(1 for p in c_people if tshirt_alloc[p.code]["status"] == "OWED")
        id_cnt = sum(1 for p in c_people if other_alloc[p.code].get("ID card") in ("RESERVED", "ISSUED"))
        lan_cnt = sum(1 for p in c_people if other_alloc[p.code].get("lanyard") in ("RESERVED", "ISSUED"))
        full_kits = sum(
            1
            for p in c_people
            if tshirt_alloc[p.code]["status"] in ("EXACT", "ISSUED")
            and all(other_alloc[p.code].get(ki) in ("RESERVED", "ISSUED") for ki in kit_items)
        )
        contingent_summary.append({
            "contingent_code": c.code,
            "college": c.college,
            "leader": c.leader,
            "escort": c.escort,
            "headcount": len(c_people),
            "exact_tshirts": exact_cnt,
            "substitute_proposed": sub_cnt,
            "owed_tshirts": owed_cnt,
            "id_cards_ready": id_cnt,
            "lanyards_ready": lan_cnt,
            "full_kits_ready": full_kits,
            "partial_kits": len(c_people) - full_kits,
            "coverage_pct": round((exact_cnt / len(c_people)) * 100, 1) if c_people else 0.0,
        })

    exact_total = sum(1 for v in tshirt_alloc.values() if v["status"] in ("EXACT", "ISSUED"))
    sub_total = sum(1 for v in tshirt_alloc.values() if v["status"] == "SUBSTITUTE_PROPOSED")
    owed_total = sum(1 for v in tshirt_alloc.values() if v["status"] == "OWED")
    full_kits_total = sum(w["full_kits_ready"] for w in wave_summary)

    allocations_map = {
        p.code: {
            "tshirt": tshirt_alloc[p.code],
            "other_items": other_alloc[p.code],
            "wave_key": wave_key(p),
            "wave_number": wave_index.get(wave_key(p)),
            "contingent_code": contingents[p.contingent_id].code if p.contingent_id in contingents else None,
        }
        for p in sorted_p
    }

    return {
        "policy": policy,
        "entitled_participants": len(sorted_p),
        "excluded_duplicates": list(dup_map.keys()),
        "unscheduled_participants": [p.code for p in sorted_p if wave_key(p) == "UNSCHEDULED"],
        "totals": {
            "exact_tshirts": exact_total,
            "substitute_proposed": sub_total,
            "owed_tshirts": owed_total,
            "full_kits_ready": full_kits_total,
            "partial_kits": len(sorted_p) - full_kits_total,
            "id_cards_reserved": other_reserved.get("ID card", 0),
            "lanyards_reserved": other_reserved.get("lanyard", 0),
        },
        "plan_reserved_by_item": {
            **{f"T-shirt:{sz}": tshirt_reserved_by_size[sz] for sz in SIZES},
            **{f"{ki}:NA": other_reserved.get(ki, 0) for ki in kit_items},
        },
        "waves": wave_summary,
        "contingents": contingent_summary,
        "allocations": allocations_map,
    }


# ---------- Summary & Stock/Demand View ----------

@router.get("/summary", dependencies=[Depends(require("merch.read"))])
def merch_summary(festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    fid = festival.id
    cfg = get_or_create_config(db, fid)
    eligible, dup_map, contingents = eligible_participants(db, fid)
    merch_rows = list(db.scalars(select(MerchItem).where(MerchItem.festival_id == fid).order_by(MerchItem.id)))
    active_issues = list(db.scalars(select(MerchIssue).where(MerchIssue.festival_id == fid, MerchIssue.status == "issued")))
    open_owed = list(db.scalars(select(MerchOwed).where(MerchOwed.festival_id == fid, MerchOwed.status == "owed")))
    incoming_rows = list(db.scalars(select(MerchIncoming).where(MerchIncoming.festival_id == fid).order_by(MerchIncoming.id.desc())))
    ledger_count = db.scalar(select(func.count()).select_from(MerchLedger).where(MerchLedger.festival_id == fid)) or 0
    exceptions_count = db.scalar(select(func.count()).select_from(MerchLedger).where(MerchLedger.festival_id == fid, MerchLedger.is_exception.is_(True))) or 0

    issued_counts: dict[int, int] = defaultdict(int)
    for iss in active_issues:
        issued_counts[iss.merch_item_id] += iss.qty

    owed_counts: dict[tuple[str, str], int] = defaultdict(int)
    for ow in open_owed:
        owed_counts[(ow.item, ow.requested_size)] += 1

    size_demand: dict[str, int] = {s: 0 for s in SIZES}
    checked_in_size_demand: dict[str, int] = {s: 0 for s in SIZES}
    checked_in_total = 0
    for p in eligible:
        sz = (p.tshirt_size or "").strip().upper()
        if sz in size_demand:
            size_demand[sz] += 1
        if p.checkin_status == "checked_in":
            checked_in_total += 1
            if sz in checked_in_size_demand:
                checked_in_size_demand[sz] += 1

    items_out = []
    size_curve = []
    what_to_order = []

    for m in merch_rows:
        iss_qty = issued_counts.get(m.id, 0)
        issuable_now = max(0, m.on_hand - m.csv_reserved)
        unallocated_now = max(0, m.on_hand - m.csv_reserved - m.plan_reserved)
        is_tshirt = m.item.lower() == "t-shirt"
        if is_tshirt:
            needed = size_demand.get(m.size, 0) if m.entitlement_rule == "every_participant" else (
                checked_in_size_demand.get(m.size, 0) if m.entitlement_rule == "checked_in_only" else 0
            )
        else:
            if m.entitlement_rule == "every_participant":
                needed = len(eligible)
            elif m.entitlement_rule == "checked_in_only":
                needed = checked_in_total
            else:
                needed = len(eligible) if m.item.lower() in ("id card", "lanyard", "tote") else None

        remaining_need = max(0, (needed or 0) - iss_qty) if needed is not None else None
        gap = (issuable_now - remaining_need) if remaining_need is not None else None
        coverage_pct = round((min(issuable_now, remaining_need) / remaining_need) * 100, 1) if (remaining_need and remaining_need > 0) else (100.0 if remaining_need == 0 else None)

        row_dict = {
            "id": m.id,
            "code": m.code,
            "item": m.item,
            "size": m.size,
            "on_hand": m.on_hand,
            "csv_reserved": m.csv_reserved,
            "csv_reserved_note": m.csv_reserved_note or "Reserved (purpose not recorded)",
            "plan_reserved": m.plan_reserved,
            "issuable": issuable_now,
            "unallocated": unallocated_now,
            "issued": iss_qty,
            "owed": owed_counts.get((m.item, m.size), 0),
            "needed": remaining_need,
            "raw_participant_demand": size_demand.get(m.size, 0) if is_tshirt else len(eligible),
            "gap": gap,
            "coverage_pct": coverage_pct,
            "entitlement_rule": m.entitlement_rule,
            "entitlement_confirmed": m.entitlement_confirmed,
            "entitlement_confirmed_by": m.entitlement_confirmed_by,
            "entitlement_confirmed_at": m.entitlement_confirmed_at,
            "max_per_person": m.max_per_person,
        }
        items_out.append(row_dict)
        if is_tshirt and m.size in SIZES:
            size_curve.append(row_dict)
        if gap is not None and gap < 0:
            what_to_order.append({
                "item": m.item,
                "size": m.size,
                "issuable": issuable_now,
                "needed": remaining_need,
                "shortfall": abs(gap),
            })

    tshirt_rows = [r for r in items_out if r["item"].lower() == "t-shirt"]
    tshirt_on_hand = sum(r["on_hand"] for r in tshirt_rows)
    tshirt_csv_res = sum(r["csv_reserved"] for r in tshirt_rows)
    tshirt_issuable = sum(r["issuable"] for r in tshirt_rows)
    tshirt_issued = sum(r["issued"] for r in tshirt_rows)
    tshirt_needed = sum(r["needed"] or 0 for r in tshirt_rows)
    tshirt_shortage = sum(abs(r["gap"]) for r in tshirt_rows if (r["gap"] or 0) < 0)
    tshirt_surplus = sum(r["gap"] for r in tshirt_rows if (r["gap"] or 0) > 0)
    other_issuable = sum(r["issuable"] for r in items_out if r["item"].lower() != "t-shirt")
    total_reserved_all = sum(r["csv_reserved"] for r in items_out)

    plan_preview = cfg.approved_plan or compute_distribution_plan(db, fid, cfg.active_policy or "P1")
    staff_info = staff_and_leaders(db, fid)

    return {
        "kpis": {
            "tshirts_issuable": tshirt_issuable,
            "tshirts_on_hand": tshirt_on_hand,
            "tshirts_csv_reserved": tshirt_csv_res,
            "tshirts_needed": tshirt_needed,
            "tshirts_net_gap": tshirt_issuable - tshirt_needed,
            "tshirts_shortage_total": tshirt_shortage,
            "tshirts_surplus_xl": tshirt_surplus,
            "other_items_issuable": other_issuable,
            "total_csv_reserved": total_reserved_all,
            "issued_total": sum(r["issued"] for r in items_out),
            "tshirts_issued": tshirt_issued,
            "owed_total": len(open_owed),
            "full_kits_possible_now": plan_preview["totals"]["full_kits_ready"],
            "partial_kits_count": plan_preview["totals"]["partial_kits"],
            "unscheduled_count": len(plan_preview["unscheduled_participants"]),
        },
        "participants_summary": {
            "raw_total": len(eligible) + len(dup_map),
            "entitled_total": len(eligible),
            "checked_in_total": checked_in_total,
            "excluded_duplicates": [{"code": k, "detail": v} for k, v in dup_map.items()],
            "unscheduled_codes": plan_preview["unscheduled_participants"],
        },
        "staff_and_leaders": staff_info,
        "config": {
            "assumptions_confirmed": cfg.assumptions_confirmed or {},
            "kit_templates": cfg.kit_templates or {},
            "kit_confirmed": cfg.kit_confirmed,
            "kit_confirmed_by": cfg.kit_confirmed_by,
            "kit_confirmed_at": cfg.kit_confirmed_at,
            "staff_leader_quota": cfg.staff_leader_quota,
            "staff_quota_set_by": cfg.staff_quota_set_by,
            "staff_quota_set_at": cfg.staff_quota_set_at,
            "substitution_rule": cfg.substitution_rule or {"max_steps": 1, "direction": "up_preferred"},
            "active_policy": cfg.active_policy,
            "has_approved_plan": cfg.approved_plan is not None,
            "approved_by": cfg.plan_approved_by,
            "approved_at": cfg.plan_approved_at,
            "plan_stale": cfg.plan_stale,
            "plan_stale_reasons": cfg.plan_stale_reasons or [],
        },
        "size_curve": size_curve,
        "items": items_out,
        "what_to_order": what_to_order,
        "incoming": [
            {
                "id": inc.id,
                "item": inc.item,
                "size": inc.size,
                "quantity_expected": inc.expected_qty,
                "quantity_received": inc.qty_received,
                "quantity_short": inc.discrepancy_qty,
                "expected_date": inc.expected_date.isoformat() if inc.expected_date else None,
                "supplier_note": inc.note,
                "status": inc.status,
                "created_by": inc.created_by,
                "received_by": inc.received_by,
                "received_at": inc.received_at,
            }
            for inc in incoming_rows
        ],
        "tab_counts": {
            "issue_desk": sum(r["issued"] for r in items_out),
            "distribution": len(plan_preview.get("waves", [])),
            "stock": len(items_out),
            "owed": len(open_owed),
            "ledger": ledger_count,
            "exceptions": exceptions_count,
        },
    }


# ---------- Settings: Assumptions, Entitlement, Reserve Release, Stock Adjustment, Incoming ----------

class AssumptionConfirmIn(BaseModel):
    key: str = Field(pattern="^(A1|A2|A3|A4)$")
    confirmed: bool = True


@router.post("/assumptions/confirm", dependencies=[Depends(require("merch.manage"))])
def confirm_assumption(
    body: AssumptionConfirmIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    cfg = get_or_create_config(db, festival.id)
    assumptions = dict(cfg.assumptions_confirmed or {})
    entry = dict(assumptions.get(body.key, {}))
    entry["confirmed"] = body.confirmed
    entry["by"] = user.name if body.confirmed else None
    entry["at"] = now().isoformat() if body.confirmed else None
    assumptions[body.key] = entry
    cfg.assumptions_confirmed = assumptions
    record(db, user, festival.id, "merch.assumption.confirm", "merch_config", body.key, f"{user.name} confirmed assumption {body.key}")
    db.commit()
    return {"assumptions_confirmed": cfg.assumptions_confirmed}


class ItemSettingsIn(BaseModel):
    entitlement_rule: str | None = Field(default=None, pattern="^(every_participant|checked_in_only|manual_only|event_winners)$")
    entitlement_confirmed: bool | None = None
    csv_reserved_note: str | None = Field(default=None, max_length=240)
    max_per_person: int | None = Field(default=None, ge=1, le=50)


@router.patch("/items/{item_id}", dependencies=[Depends(require("merch.manage"))])
def update_item_settings(
    item_id: int,
    body: ItemSettingsIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    m = db.scalar(select(MerchItem).where(MerchItem.id == item_id, MerchItem.festival_id == festival.id))
    if not m:
        raise HTTPException(404, "Merchandise item not found")
    if body.entitlement_rule is not None:
        m.entitlement_rule = body.entitlement_rule
    if body.entitlement_confirmed is not None:
        m.entitlement_confirmed = body.entitlement_confirmed
        m.entitlement_confirmed_by = user.name if body.entitlement_confirmed else None
        m.entitlement_confirmed_at = now() if body.entitlement_confirmed else None
    if body.csv_reserved_note is not None:
        m.csv_reserved_note = body.csv_reserved_note.strip() or None
        m.csv_reserved_annotated_by = user.name
        m.csv_reserved_annotated_at = now()
    if body.max_per_person is not None:
        m.max_per_person = body.max_per_person
    log_merch(
        db,
        festival.id,
        user,
        action="item_settings_updated",
        merch_item_id=m.id,
        item=m.item,
        size=m.size,
        qty_delta=0,
        reason=f"Entitlement rule: {m.entitlement_rule}",
    )
    record(db, user, festival.id, "merch.item.update", "merch_item", m.code, f"{user.name} updated settings for {m.code}")
    db.commit()
    return {"id": m.id, "code": m.code, "entitlement_rule": m.entitlement_rule, "entitlement_confirmed": m.entitlement_confirmed}


class ReleaseReserveIn(BaseModel):
    quantity: int = Field(ge=1, le=100000)
    reason: str = Field(min_length=3, max_length=400)


@router.post("/items/{item_id}/release-reserve", dependencies=[Depends(require("merch.manage"))])
def release_csv_reserve(
    item_id: int,
    body: ReleaseReserveIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    m = db.scalar(select(MerchItem).where(MerchItem.id == item_id, MerchItem.festival_id == festival.id).with_for_update())
    if not m:
        raise HTTPException(404, "Merchandise item not found")
    if body.quantity > m.csv_reserved:
        raise HTTPException(422, f"Cannot release {body.quantity}; only {m.csv_reserved} units are in csv_reserved")
    m.csv_reserved -= body.quantity
    m.version = (m.version or 1) + 1
    mark_plan_stale_if_needed(db, festival.id, f"Released {body.quantity} csv_reserved on {m.item} ({m.size})")
    log_merch(
        db,
        festival.id,
        user,
        action="reserve_released",
        merch_item_id=m.id,
        item=m.item,
        size=m.size,
        qty_delta=body.quantity,
        reason=body.reason,
    )
    record(db, user, festival.id, "merch.reserve.release", "merch_item", m.code, f"{user.name} released {body.quantity} reserved {m.code}", reason=body.reason)
    db.commit()
    return {"id": m.id, "code": m.code, "csv_reserved": m.csv_reserved, "issuable": max(0, m.on_hand - m.csv_reserved)}


class StockAdjustIn(BaseModel):
    delta: int = Field(ge=-10000, le=10000)
    reason: str = Field(min_length=3, max_length=400)


@router.post("/items/{item_id}/adjust", dependencies=[Depends(require("merch.manage"))])
def adjust_stock(
    item_id: int,
    body: StockAdjustIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if body.delta == 0:
        raise HTTPException(422, "Adjustment delta cannot be 0")
    m = db.scalar(select(MerchItem).where(MerchItem.id == item_id, MerchItem.festival_id == festival.id).with_for_update())
    if not m:
        raise HTTPException(404, "Merchandise item not found")
    if m.on_hand + body.delta < m.csv_reserved:
        raise HTTPException(409, f"Adjustment would drop on_hand ({m.on_hand + body.delta}) below csv_reserved ({m.csv_reserved})")
    m.on_hand += body.delta
    m.version = (m.version or 1) + 1
    mark_plan_stale_if_needed(db, festival.id, f"Stock adjusted by {body.delta:+d} on {m.item} ({m.size})")
    log_merch(
        db,
        festival.id,
        user,
        action="stock_adjusted",
        merch_item_id=m.id,
        item=m.item,
        size=m.size,
        qty_delta=body.delta,
        is_exception=True,
        reason=body.reason,
    )
    record(db, user, festival.id, "merch.stock.adjust", "merch_item", m.code, f"{user.name} adjusted {m.code} by {body.delta:+d}", reason=body.reason)
    db.commit()
    return {"id": m.id, "code": m.code, "on_hand": m.on_hand, "issuable": max(0, m.on_hand - m.csv_reserved)}


class IncomingCreateIn(BaseModel):
    item: str = Field(min_length=2, max_length=60)
    size: str = Field(default="NA", max_length=16)
    quantity_expected: int = Field(ge=1, le=100000)
    expected_date: str | None = Field(default=None, max_length=40)
    supplier_note: str | None = Field(default=None, max_length=400)


@router.post("/incoming", status_code=201, dependencies=[Depends(require("merch.manage"))])
def create_incoming(
    body: IncomingCreateIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    m = db.scalar(
        select(MerchItem).where(
            MerchItem.festival_id == festival.id,
            func.lower(MerchItem.item) == body.item.strip().lower(),
            func.lower(MerchItem.size) == body.size.strip().lower(),
        )
    )
    if not m:
        raise HTTPException(404, f"Merchandise item {body.item} ({body.size}) not found")
    exp_date = None
    if body.expected_date:
        try:
            exp_date = datetime.strptime(body.expected_date.split("T")[0], "%Y-%m-%d").date()
        except ValueError:
            pass
    inc = MerchIncoming(
        festival_id=festival.id,
        merch_item_id=m.id,
        item=m.item,
        size=m.size,
        expected_qty=body.quantity_expected,
        expected_date=exp_date,
        note=body.supplier_note,
        status="expected",
        created_by=user.name,
    )
    db.add(inc)
    db.flush()
    log_merch(
        db,
        festival.id,
        user,
        action="incoming_recorded",
        merch_item_id=m.id,
        item=m.item,
        size=m.size,
        qty_delta=body.quantity_expected,
        reason=body.supplier_note,
    )
    db.commit()
    return {"id": inc.id, "item": inc.item, "size": inc.size, "quantity_expected": inc.expected_qty, "status": inc.status}


class IncomingReceiveIn(BaseModel):
    quantity_received: int = Field(ge=1, le=100000)
    note: str | None = Field(default=None, max_length=400)


@router.post("/incoming/{incoming_id}/receive", dependencies=[Depends(require("merch.manage"))])
def receive_incoming(
    incoming_id: int,
    body: IncomingReceiveIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    inc = db.scalar(select(MerchIncoming).where(MerchIncoming.id == incoming_id, MerchIncoming.festival_id == festival.id).with_for_update())
    if not inc:
        raise HTTPException(404, "Incoming delivery not found")
    if inc.status in ("received", "partially_received"):
        raise HTTPException(409, f"Delivery #{inc.id} was already received")
    m = db.scalar(select(MerchItem).where(MerchItem.id == inc.merch_item_id).with_for_update())
    m.on_hand += body.quantity_received
    m.version = (m.version or 1) + 1
    short_qty = max(0, inc.expected_qty - body.quantity_received)
    inc.qty_received = body.quantity_received
    inc.discrepancy_qty = short_qty
    inc.status = "partially_received" if short_qty > 0 else "received"
    inc.received_by = user.name
    inc.received_at = now()
    inc.discrepancy_note = body.note

    mark_plan_stale_if_needed(db, festival.id, f"Received {body.quantity_received} {m.item} ({m.size}) from incoming delivery #{inc.id}")
    log_merch(
        db,
        festival.id,
        user,
        action="incoming_received_partial" if short_qty > 0 else "incoming_received",
        merch_item_id=m.id,
        item=m.item,
        size=m.size,
        qty_delta=body.quantity_received,
        is_exception=short_qty > 0,
        reason=body.note or (f"Expected {inc.expected_qty}, received {body.quantity_received}, short {short_qty}" if short_qty > 0 else "Full receipt"),
    )
    record(
        db,
        user,
        festival.id,
        "merch.incoming.receive",
        "merch_incoming",
        inc.id,
        f"{user.name} received {body.quantity_received} of {inc.expected_qty} {m.item} ({m.size})",
        reason=body.note,
    )
    db.commit()
    return {
        "id": inc.id,
        "item": inc.item,
        "size": inc.size,
        "quantity_expected": inc.expected_qty,
        "quantity_received": inc.qty_received,
        "quantity_short": inc.discrepancy_qty,
        "status": inc.status,
        "new_on_hand": m.on_hand,
        "new_issuable": max(0, m.on_hand - m.csv_reserved),
    }


# ---------- Distribution Plan, Kit Setup, Policy Comparison, Approval, Re-plan, Wave Prep ----------

class KitSetupIn(BaseModel):
    kit_templates: dict[str, list[dict[str, Any]]]
    staff_leader_quota: dict[str, int] | None = None
    substitution_rule: dict[str, Any] | None = None
    confirm_kit: bool = True


@router.post("/distribution/setup", dependencies=[Depends(require("merch.manage"))])
def update_distribution_setup(
    body: KitSetupIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    cfg = get_or_create_config(db, festival.id)
    cfg.kit_templates = body.kit_templates
    if body.confirm_kit:
        cfg.kit_confirmed = True
        cfg.kit_confirmed_by = user.name
        cfg.kit_confirmed_at = now()
    if body.staff_leader_quota is not None:
        cfg.staff_leader_quota = body.staff_leader_quota
        cfg.staff_quota_set_by = user.name
        cfg.staff_quota_set_at = now()
    if body.substitution_rule is not None:
        cfg.substitution_rule = body.substitution_rule
    mark_plan_stale_if_needed(db, festival.id, "Kit templates or quota settings updated")
    log_merch(
        db,
        festival.id,
        user,
        action="kit_setup_confirmed",
        item="Kit Templates",
        size="NA",
        qty_delta=0,
        reason="Confirmed kit templates and distribution rules",
    )
    record(db, user, festival.id, "merch.kit.setup", "merch_config", festival.id, f"{user.name} confirmed kit setup")
    db.commit()
    return {
        "kit_confirmed": cfg.kit_confirmed,
        "kit_confirmed_by": cfg.kit_confirmed_by,
        "kit_confirmed_at": cfg.kit_confirmed_at,
        "kit_templates": cfg.kit_templates,
        "staff_leader_quota": cfg.staff_leader_quota,
        "substitution_rule": cfg.substitution_rule,
    }


@router.get("/distribution/preview", dependencies=[Depends(require("merch.read"))])
def preview_distribution(
    policy: str = Query("P1", pattern="^(P1|P2|P3)$"),
    max_steps: int | None = Query(None, ge=0, le=2),
    direction: str | None = Query(None, pattern="^(up_preferred|adjacent|none)$"),
    include_compare: bool = True,
    festival: Festival = Depends(festival_access),
    db: Session = Depends(get_db),
):
    cfg = get_or_create_config(db, festival.id)
    sub_override = None
    if max_steps is not None or direction is not None:
        base_sub = dict(cfg.substitution_rule or {"max_steps": 1, "direction": "up_preferred"})
        if max_steps is not None:
            base_sub["max_steps"] = max_steps
        if direction is not None:
            base_sub["direction"] = direction
        sub_override = base_sub

    plan = compute_distribution_plan(db, festival.id, policy=policy, substitution_override=sub_override)
    comparison = None
    if include_compare:
        p1 = plan if policy == "P1" else compute_distribution_plan(db, festival.id, policy="P1", substitution_override=sub_override)
        p2 = plan if policy == "P2" else compute_distribution_plan(db, festival.id, policy="P2", substitution_override=sub_override)
        p3 = plan if policy == "P3" else compute_distribution_plan(db, festival.id, policy="P3", substitution_override=sub_override)
        comparison = {
            key: {
                "policy": pl["policy"],
                "totals": pl["totals"],
                "waves": [
                    {
                        "label": w["label"],
                        "headcount": w["headcount"],
                        "exact_tshirts": w["exact_tshirts"],
                        "coverage_pct": w["coverage_pct"],
                        "full_kits_ready": w["full_kits_ready"],
                    }
                    for w in pl["waves"]
                ],
                "min_wave_coverage_pct": min((w["coverage_pct"] for w in pl["waves"] if w["wave_key"] != "UNSCHEDULED"), default=0.0),
                "max_wave_coverage_pct": max((w["coverage_pct"] for w in pl["waves"] if w["wave_key"] != "UNSCHEDULED"), default=0.0),
                "min_contingent_coverage_pct": min((c["coverage_pct"] for c in pl["contingents"]), default=0.0),
                "max_contingent_coverage_pct": max((c["coverage_pct"] for c in pl["contingents"]), default=0.0),
            }
            for key, pl in [("P1", p1), ("P2", p2), ("P3", p3)]
        }

    return {
        "policy": plan["policy"],
        "entitled_participants": plan["entitled_participants"],
        "excluded_duplicates": plan["excluded_duplicates"],
        "unscheduled_participants": plan["unscheduled_participants"],
        "totals": plan["totals"],
        "plan_reserved_by_item": plan["plan_reserved_by_item"],
        "waves": plan["waves"],
        "contingents": plan["contingents"],
        "comparison": comparison,
        "tray_prep_status": cfg.tray_prep_status or {},
    }


class PlanApproveIn(BaseModel):
    policy: str = Field(default="P1", pattern="^(P1|P2|P3)$")
    confirm_kit_if_needed: bool = False


@router.post("/distribution/approve", dependencies=[Depends(require("merch.manage"))])
def approve_distribution_plan(
    body: PlanApproveIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    cfg = get_or_create_config(db, festival.id)
    if not cfg.kit_confirmed:
        if body.confirm_kit_if_needed:
            cfg.kit_confirmed = True
            cfg.kit_confirmed_by = user.name
            cfg.kit_confirmed_at = now()
        else:
            raise HTTPException(409, "Confirm the kit templates for participants, core team, and leaders/escorts before approving a distribution plan.")

    plan = compute_distribution_plan(db, festival.id, policy=body.policy)
    merch_rows = list(db.scalars(select(MerchItem).where(MerchItem.festival_id == festival.id).order_by(MerchItem.id).with_for_update()))
    for m in merch_rows:
        key = f"{m.item}:{m.size}"
        m.plan_reserved = int(plan["plan_reserved_by_item"].get(key, 0))
        m.version = (m.version or 1) + 1

    cfg.active_policy = body.policy
    cfg.approved_plan = plan
    cfg.plan_approved_by = user.name
    cfg.plan_approved_at = now()
    cfg.plan_stale = False
    cfg.plan_stale_reasons = []

    log_merch(
        db,
        festival.id,
        user,
        action="plan_approved",
        item="Distribution Plan",
        size=body.policy,
        qty_delta=plan["totals"]["exact_tshirts"],
        reason=f"Approved policy {body.policy}: {plan['totals']['exact_tshirts']} exact T-shirts, {plan['totals']['full_kits_ready']} full kits",
    )
    record(
        db,
        user,
        festival.id,
        "merch.plan.approve",
        "merch_config",
        body.policy,
        f"{user.name} approved merchandise distribution plan ({body.policy})",
    )
    db.commit()
    return {
        "policy": body.policy,
        "approved_by": cfg.plan_approved_by,
        "approved_at": cfg.plan_approved_at,
        "totals": plan["totals"],
        "plan_reserved_by_item": plan["plan_reserved_by_item"],
    }


class ReplanIn(BaseModel):
    policy: str | None = Field(default=None, pattern="^(P1|P2|P3)$")
    apply: bool = False


@router.post("/distribution/replan", dependencies=[Depends(require("merch.manage"))])
def replan_distribution(
    body: ReplanIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    cfg = get_or_create_config(db, festival.id)
    old_plan = cfg.approved_plan or {}
    old_allocs = old_plan.get("allocations", {})
    pol = body.policy or cfg.active_policy or "P1"
    new_plan = compute_distribution_plan(db, festival.id, policy=pol)
    new_allocs = new_plan.get("allocations", {})

    upgraded_to_exact = []
    downgraded = []
    preserved_issued = 0
    for code, n_al in new_allocs.items():
        n_st = n_al["tshirt"]["status"]
        if n_st == "ISSUED":
            preserved_issued += 1
            continue
        o_al = old_allocs.get(code)
        o_st = o_al["tshirt"]["status"] if o_al else None
        if o_st in ("OWED", "SUBSTITUTE_PROPOSED", None) and n_st == "EXACT":
            upgraded_to_exact.append({"code": code, "size": n_al["tshirt"]["allocated_size"], "from": o_st or "UNPLANNED"})
        elif o_st == "EXACT" and n_st in ("OWED", "SUBSTITUTE_PROPOSED"):
            downgraded.append({"code": code, "size": n_al["tshirt"]["declared_size"], "to": n_st})

    diff = {
        "policy": pol,
        "preserved_issued_count": preserved_issued,
        "upgraded_to_exact_count": len(upgraded_to_exact),
        "upgraded_sample": upgraded_to_exact[:30],
        "downgraded_count": len(downgraded),
        "old_totals": old_plan.get("totals"),
        "new_totals": new_plan["totals"],
        "stale_reasons_cleared": cfg.plan_stale_reasons or [],
        "applied": body.apply,
    }

    if body.apply:
        merch_rows = list(db.scalars(select(MerchItem).where(MerchItem.festival_id == festival.id).order_by(MerchItem.id).with_for_update()))
        for m in merch_rows:
            key = f"{m.item}:{m.size}"
            m.plan_reserved = int(new_plan["plan_reserved_by_item"].get(key, 0))
            m.version = (m.version or 1) + 1
        cfg.active_policy = pol
        cfg.approved_plan = new_plan
        cfg.plan_approved_by = user.name
        cfg.plan_approved_at = now()
        cfg.plan_stale = False
        cfg.plan_stale_reasons = []
        log_merch(
            db,
            festival.id,
            user,
            action="plan_replanned",
            item="Distribution Plan",
            size=pol,
            qty_delta=len(upgraded_to_exact),
            reason=f"Re-planned ({pol}): {len(upgraded_to_exact)} upgraded to exact, {preserved_issued} issued preserved",
        )
        record(db, user, festival.id, "merch.plan.replan", "merch_config", pol, f"{user.name} applied re-plan ({pol})")
        db.commit()

    return diff


class TrayStatusIn(BaseModel):
    tray_key: str = Field(min_length=1, max_length=80)
    status: str = Field(pattern="^(unpacked|packed|at_desk)$")


@router.patch("/distribution/trays", dependencies=[Depends(require("merch.issue"))])
def update_tray_status(
    body: TrayStatusIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    cfg = get_or_create_config(db, festival.id)
    trays = dict(cfg.tray_prep_status or {})
    trays[body.tray_key] = body.status
    cfg.tray_prep_status = trays
    db.commit()
    return {"tray_key": body.tray_key, "status": body.status}


@router.get("/distribution/wave-sheet", dependencies=[Depends(require("merch.read"))])
def wave_packing_sheet(
    wave_key_filter: str | None = None,
    contingent_code: str | None = None,
    festival: Festival = Depends(festival_access),
    db: Session = Depends(get_db),
):
    cfg = get_or_create_config(db, festival.id)
    plan = cfg.approved_plan or compute_distribution_plan(db, festival.id, cfg.active_policy or "P1")
    allocs = plan.get("allocations", {})
    eligible, _, contingents = eligible_participants(db, festival.id)

    trays: dict[tuple[str, str], dict[str, Any]] = {}
    for p in eligible:
        wk = wave_key(p)
        c = contingents.get(p.contingent_id)
        ck = c.code if c else "UNKNOWN"
        if wave_key_filter and wk != wave_key_filter:
            continue
        if contingent_code and ck.lower() != contingent_code.strip().lower():
            continue
        al = allocs.get(p.code, {})
        t_al = al.get("tshirt", {"status": "OWED", "allocated_size": None, "declared_size": p.tshirt_size})
        o_al = al.get("other_items", {})
        tray_id = f"{wk}|{ck}"
        t_bucket = trays.setdefault(
            (wk, ck),
            {
                "tray_key": tray_id,
                "wave_key": wk,
                "wave_number": al.get("wave_number"),
                "contingent_code": ck,
                "college": c.college if c else "Unknown",
                "leader": c.leader if c else None,
                "escort": c.escort if c else None,
                "prep_status": (cfg.tray_prep_status or {}).get(tray_id, "unpacked"),
                "headcount": 0,
                "sizes_to_pack": {s: 0 for s in SIZES},
                "substitute_xl_to_pack": 0,
                "owed_count": 0,
                "id_cards_to_pack": 0,
                "lanyards_to_pack": 0,
                "members": [],
            },
        )
        t_bucket["headcount"] += 1
        st = t_al.get("status")
        alloc_sz = t_al.get("allocated_size")
        if st in ("EXACT", "ISSUED") and alloc_sz in SIZES:
            t_bucket["sizes_to_pack"][alloc_sz] += 1
        elif st == "SUBSTITUTE_PROPOSED" and alloc_sz in SIZES:
            t_bucket["substitute_xl_to_pack"] += 1
        else:
            t_bucket["owed_count"] += 1
        if o_al.get("ID card") in ("RESERVED", "ISSUED"):
            t_bucket["id_cards_to_pack"] += 1
        if o_al.get("lanyard") in ("RESERVED", "ISSUED"):
            t_bucket["lanyards_to_pack"] += 1
        t_bucket["members"].append({
            "code": p.code,
            "full_name": p.full_name,
            "declared_size": p.tshirt_size,
            "tshirt_status": st,
            "allocated_size": alloc_sz,
            "id_card": o_al.get("ID card", "OWED"),
            "lanyard": o_al.get("lanyard", "OWED"),
        })

    sorted_trays = sorted(trays.values(), key=lambda x: (x["wave_number"] or 999, x["contingent_code"]))
    return {"policy": plan["policy"], "trays": sorted_trays}


# ---------- Issue Desk: Lookup, Contingent Queue, Express Kit Handover, Single Issue, Undo, Void/Exchange, Size Change ----------

def mask_phone_last4(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = "".join(ch for ch in phone if ch.isdigit())
    return digits[-4:] if len(digits) >= 4 else digits


def build_person_desk_card(
    db: Session,
    fid: int,
    p: Participant,
    dup_map: dict[str, str],
    contingents: dict[int, Contingent],
    cfg: MerchConfig,
) -> dict[str, Any]:
    c = contingents.get(p.contingent_id)
    issues = list(
        db.scalars(
            select(MerchIssue)
            .where(MerchIssue.festival_id == fid, MerchIssue.participant_id == p.id)
            .order_by(MerchIssue.id.desc())
        )
    )
    owed = list(
        db.scalars(
            select(MerchOwed)
            .where(MerchOwed.festival_id == fid, MerchOwed.participant_id == p.id, MerchOwed.status == "owed")
            .order_by(MerchOwed.id.desc())
        )
    )
    events = [
        {"code": e.code, "name": e.name}
        for _, e in db.execute(
            select(Registration, Event)
            .join(Event, Event.id == Registration.event_id)
            .where(Registration.participant_id == p.id, Event.deleted_at.is_(None))
            .order_by(Event.code)
        )
    ]

    plan = cfg.approved_plan or compute_distribution_plan(db, fid, cfg.active_policy or "P1")
    plan_entry = (plan.get("allocations") or {}).get(p.code)

    now_ts = now()
    return {
        "recipient_type": "participant",
        "code": p.code,
        "participant_id": p.id,
        "full_name": p.full_name,
        "college": p.college,
        "contingent_code": c.code if c else None,
        "tshirt_size": p.tshirt_size,
        "phone_last4": mask_phone_last4(p.phone),
        "checkin_status": p.checkin_status,
        "registration_status": p.registration_status,
        "arrival_at": p.arrival_at,
        "departure_at": p.departure_at,
        "invalid_dates": has_invalid_dates(p),
        "blocked_duplicate": p.code in dup_map,
        "duplicate_detail": dup_map.get(p.code),
        "duplicate_resolve_link": f"/participants?q={p.code}" if p.code in dup_map else None,
        "plan_allocation": plan_entry,
        "events": events,
        "issued_items": [
            {
                "id": iss.id,
                "item": iss.item,
                "size": iss.issued_size,
                "declared_size": iss.requested_size,
                "quantity": iss.qty,
                "status": iss.status,
                "is_substitute": iss.is_substitution,
                "consent_recorded": iss.consent_recorded,
                "is_override": iss.override_reason is not None,
                "event_code": iss.event_code,
                "issued_by_id": iss.issued_by_id,
                "issued_by_name": iss.issued_by,
                "created_at": iss.issued_at,
                "can_undo": iss.status == "issued" and bool(iss.issued_at and (now_ts - iss.issued_at).total_seconds() <= 60),
            }
            for iss in issues
        ],
        "owed_items": [
            {
                "id": ow.id,
                "item": ow.item,
                "size": ow.requested_size,
                "quantity": 1,
                "status": ow.status,
                "reason": ow.reason,
                "created_at": None,
            }
            for ow in owed
        ],
    }


@router.get("/lookup", dependencies=[Depends(require("merch.read"))])
def desk_lookup(
    q: str = Query(min_length=1, max_length=80),
    festival: Festival = Depends(festival_access),
    db: Session = Depends(get_db),
):
    fid = festival.id
    term = q.strip()
    cfg = get_or_create_config(db, fid)
    dup_map = open_duplicate_codes(db, fid)
    contingents = {c.id: c for c in db.scalars(select(Contingent).where(Contingent.festival_id == fid))}

    exact_p = db.scalar(
        select(Participant).where(
            Participant.festival_id == fid,
            Participant.deleted_at.is_(None),
            or_(
                func.lower(Participant.code) == term.lower(),
                Participant.qr_token == term,
            ),
        )
    )
    if exact_p:
        return {"results": [build_person_desk_card(db, fid, exact_p, dup_map, contingents, cfg)]}

    like_term = f"%{term}%"
    people = list(
        db.scalars(
            select(Participant)
            .where(
                Participant.festival_id == fid,
                Participant.deleted_at.is_(None),
                or_(
                    Participant.code.ilike(like_term),
                    Participant.full_name.ilike(like_term),
                    Participant.phone.ilike(like_term),
                ),
            )
            .order_by(Participant.code)
            .limit(12)
        )
    )
    results = [build_person_desk_card(db, fid, p, dup_map, contingents, cfg) for p in people]

    staff_matches = list(
        db.scalars(
            select(User)
            .where(
                User.festival_id == fid,
                User.member_code.is_not(None),
                or_(User.member_code.ilike(like_term), User.name.ilike(like_term)),
            )
            .order_by(User.member_code)
            .limit(5)
        )
    )
    for u in staff_matches:
        results.append({
            "recipient_type": "core_team",
            "code": u.member_code,
            "full_name": u.name,
            "college": f"Core Team · {u.department.name if u.department else 'Staff'}",
            "contingent_code": None,
            "tshirt_size": None,
            "phone_last4": None,
            "checkin_status": "checked_in",
            "registration_status": "confirmed",
            "arrival_at": None,
            "departure_at": None,
            "invalid_dates": False,
            "blocked_duplicate": False,
            "duplicate_detail": None,
            "duplicate_resolve_link": None,
            "plan_allocation": None,
            "events": [],
            "issued_items": [],
            "owed_items": [],
        })

    return {"results": results}


@router.get("/contingent-queue/{contingent_code}", dependencies=[Depends(require("merch.read"))])
def contingent_queue(
    contingent_code: str,
    festival: Festival = Depends(festival_access),
    db: Session = Depends(get_db),
):
    fid = festival.id
    c = by_code(db, Contingent, fid, contingent_code)
    cfg = get_or_create_config(db, fid)
    dup_map = open_duplicate_codes(db, fid)
    contingents = {c.id: c}
    members = list(
        db.scalars(
            select(Participant)
            .where(Participant.festival_id == fid, Participant.contingent_id == c.id, Participant.deleted_at.is_(None))
            .order_by(Participant.code)
        )
    )
    cards = [build_person_desk_card(db, fid, p, dup_map, contingents, cfg) for p in members]
    complete_cnt = sum(
        1
        for card in cards
        if any(i["item"] == "T-shirt" and i["status"] == "issued" for i in card["issued_items"])
        and any(i["item"] == "ID card" and i["status"] == "issued" for i in card["issued_items"])
        and any(i["item"] == "lanyard" and i["status"] == "issued" for i in card["issued_items"])
    )
    return {
        "contingent": {
            "code": c.code,
            "college": c.college,
            "leader": c.leader,
            "escort": c.escort,
            "total_members": len(cards),
            "kits_completed": complete_cnt,
        },
        "members": cards,
    }


class HandoverKitIn(BaseModel):
    recipient_type: str = Field(default="participant", pattern="^(participant|core_team|leader_escort)$")
    recipient_code: str = Field(min_length=2, max_length=40)
    tshirt_size_override: str | None = Field(default=None, max_length=16)
    accept_substitute: bool = False
    consent_recorded: bool = False
    include_tote: bool = False
    idempotency_key: str | None = Field(default=None, max_length=64)


@router.post("/handover-kit", status_code=201, dependencies=[Depends(require("merch.issue"))])
def handover_kit(
    body: HandoverKitIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    fid = festival.id
    dup_map = open_duplicate_codes(db, fid)
    code = body.recipient_code.strip()

    if code in dup_map:
        raise HTTPException(
            409,
            f"{code} is flagged as a probable duplicate ({dup_map[code]}). Resolve in Participants (/participants?q={code}) before issuing merchandise.",
        )

    if body.idempotency_key:
        existing_idem = db.scalar(select(MerchIssue).where(MerchIssue.festival_id == fid, MerchIssue.idempotency_key == body.idempotency_key))
        if existing_idem:
            return {"status": "idempotent_replay", "recipient_code": code}

    p = db.scalar(select(Participant).where(Participant.festival_id == fid, Participant.code == code, Participant.deleted_at.is_(None)))
    if not p:
        raise HTTPException(404, f"Participant {code} not found")
    if p.registration_status == "cancelled":
        raise HTTPException(409, f"{p.code} has a cancelled registration")

    recipient_name = p.full_name
    declared_size = (p.tshirt_size or "L").strip().upper()

    cfg = get_or_create_config(db, fid)
    plan = cfg.approved_plan or compute_distribution_plan(db, fid, cfg.active_policy or "P1")
    p_alloc = (plan.get("allocations") or {}).get(code, {})
    t_plan = p_alloc.get("tshirt", {})

    merch_rows = list(db.scalars(select(MerchItem).where(MerchItem.festival_id == fid).order_by(MerchItem.id).with_for_update()))
    by_item_size = {(m.item, m.size): m for m in merch_rows}

    already_issued_items = {
        iss.item
        for iss in db.scalars(
            select(MerchIssue).where(
                MerchIssue.festival_id == fid,
                MerchIssue.participant_id == p.id,
                MerchIssue.status == "issued",
            )
        )
    }

    issued_now = []
    owed_now = []

    if "T-shirt" not in already_issued_items:
        target_sz = (body.tshirt_size_override or declared_size).strip().upper()
        is_sub = False
        if not body.tshirt_size_override:
            if t_plan.get("status") == "SUBSTITUTE_PROPOSED" and body.accept_substitute:
                if not body.consent_recorded:
                    raise HTTPException(422, "Record participant consent before issuing a substitute T-shirt size.")
                target_sz = t_plan.get("allocated_size") or target_sz
                is_sub = target_sz != declared_size
        elif declared_size and body.tshirt_size_override.strip().upper() != declared_size:
            if not body.consent_recorded:
                raise HTTPException(422, "Record participant consent before issuing a substitute T-shirt size.")
            is_sub = True

        t_item = by_item_size.get(("T-shirt", target_sz))
        issuable_t = max(0, t_item.on_hand - t_item.csv_reserved) if t_item else 0
        can_issue_t = issuable_t > 0 and (
            not cfg.approved_plan
            or t_plan.get("status") in ("EXACT", "SUBSTITUTE_PROPOSED")
            or max(0, t_item.on_hand - t_item.csv_reserved - t_item.plan_reserved) > 0
        )
        if t_item and can_issue_t and (t_plan.get("status") != "SUBSTITUTE_PROPOSED" or body.accept_substitute or target_sz == declared_size and max(0, t_item.on_hand - t_item.csv_reserved - t_item.plan_reserved) > 0):
            t_item.on_hand -= 1
            if t_item.plan_reserved > 0 and t_plan.get("allocated_size") == target_sz:
                t_item.plan_reserved = max(0, t_item.plan_reserved - 1)
            t_item.version = (t_item.version or 1) + 1
            iss = MerchIssue(
                festival_id=fid,
                participant_id=p.id,
                merch_item_id=t_item.id,
                item="T-shirt",
                requested_size=declared_size,
                issued_size=target_sz,
                is_substitution=is_sub,
                consent_recorded=body.consent_recorded if is_sub else False,
                qty=1,
                desk="Main Desk",
                issued_by=user.name,
                issued_by_id=user.id,
                verification_method="id_lookup",
                status="issued",
                idempotency_key=f"{body.idempotency_key}:T-shirt" if body.idempotency_key else None,
            )
            db.add(iss)
            db.flush()
            for ow in db.scalars(select(MerchOwed).where(MerchOwed.festival_id == fid, MerchOwed.participant_id == p.id, MerchOwed.item == "T-shirt", MerchOwed.status == "owed")):
                ow.status = "collected"
            log_merch(
                db,
                fid,
                user,
                action="issue_substitute" if is_sub else "issue",
                merch_item_id=t_item.id,
                item="T-shirt",
                size=target_sz,
                qty_delta=1,
                participant_code=code,
                participant_name=recipient_name,
                desk="Main Desk",
                is_exception=is_sub,
                reason=f"Substituted {declared_size} -> {target_sz} with participant consent" if is_sub else "Kit handover",
            )
            issued_now.append({"item": "T-shirt", "size": target_sz, "is_substitute": is_sub})
        else:
            existing_ow = db.scalar(
                select(MerchOwed).where(
                    MerchOwed.festival_id == fid,
                    MerchOwed.participant_id == p.id,
                    MerchOwed.item == "T-shirt",
                    MerchOwed.status == "owed",
                )
            )
            if not existing_ow:
                ow = MerchOwed(
                    festival_id=fid,
                    participant_id=p.id,
                    item="T-shirt",
                    requested_size=declared_size,
                    proposed_substitute_size=t_plan.get("allocated_size") if is_sub else None,
                    status="owed",
                    reason="consent_declined" if (t_plan.get("status") == "SUBSTITUTE_PROPOSED" and not body.accept_substitute) else "size_short",
                    note="Kit handover shortage",
                    created_by=user.name,
                )
                db.add(ow)
                db.flush()
                log_merch(
                    db,
                    fid,
                    user,
                    action="owed_recorded",
                    item="T-shirt",
                    size=declared_size,
                    qty_delta=1,
                    participant_code=code,
                    participant_name=recipient_name,
                    desk="Main Desk",
                    is_exception=True,
                    reason=ow.reason,
                )
            owed_now.append({"item": "T-shirt", "size": declared_size})

    other_kit_items = ["ID card", "lanyard"] + (["tote"] if body.include_tote else [])
    for item_name in other_kit_items:
        if item_name in already_issued_items:
            continue
        m_item = by_item_size.get((item_name, "NA"))
        issuable_m = max(0, m_item.on_hand - m_item.csv_reserved) if m_item else 0
        if m_item and issuable_m > 0:
            m_item.on_hand -= 1
            if m_item.plan_reserved > 0:
                m_item.plan_reserved = max(0, m_item.plan_reserved - 1)
            m_item.version = (m_item.version or 1) + 1
            iss = MerchIssue(
                festival_id=fid,
                participant_id=p.id,
                merch_item_id=m_item.id,
                item=item_name,
                requested_size="NA",
                issued_size="NA",
                is_substitution=False,
                consent_recorded=False,
                qty=1,
                desk="Main Desk",
                issued_by=user.name,
                issued_by_id=user.id,
                verification_method="id_lookup",
                status="issued",
                idempotency_key=f"{body.idempotency_key}:{item_name}" if body.idempotency_key else None,
            )
            db.add(iss)
            db.flush()
            for ow in db.scalars(select(MerchOwed).where(MerchOwed.festival_id == fid, MerchOwed.participant_id == p.id, MerchOwed.item == item_name, MerchOwed.status == "owed")):
                ow.status = "collected"
            log_merch(
                db,
                fid,
                user,
                action="issue",
                merch_item_id=m_item.id,
                item=item_name,
                size="NA",
                qty_delta=1,
                participant_code=code,
                participant_name=recipient_name,
                desk="Main Desk",
                reason="Kit handover",
            )
            issued_now.append({"item": item_name, "size": "NA", "is_substitute": False})
        else:
            existing_ow = db.scalar(
                select(MerchOwed).where(
                    MerchOwed.festival_id == fid,
                    MerchOwed.participant_id == p.id,
                    MerchOwed.item == item_name,
                    MerchOwed.status == "owed",
                )
            )
            if not existing_ow:
                ow = MerchOwed(
                    festival_id=fid,
                    participant_id=p.id,
                    item=item_name,
                    requested_size="NA",
                    status="owed",
                    reason="no_stock",
                    note="Kit handover shortage",
                    created_by=user.name,
                )
                db.add(ow)
                db.flush()
                log_merch(
                    db,
                    fid,
                    user,
                    action="owed_recorded",
                    item=item_name,
                    size="NA",
                    qty_delta=1,
                    participant_code=code,
                    participant_name=recipient_name,
                    desk="Main Desk",
                    is_exception=True,
                    reason=ow.reason,
                )
            owed_now.append({"item": item_name, "size": "NA"})

    if not issued_now and not owed_now:
        raise HTTPException(409, f"{recipient_name} ({code}) has already received their complete kit.")

    record(
        db,
        user,
        fid,
        "merch.kit.handover",
        "participant",
        code,
        f"{user.name} handed over kit to {recipient_name} ({code}): {len(issued_now)} issued, {len(owed_now)} owed",
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, f"Duplicate issue blocked for {code}") from exc

    return {
        "recipient_code": code,
        "recipient_name": recipient_name,
        "issued": issued_now,
        "owed": owed_now,
    }


class SingleIssueIn(BaseModel):
    recipient_code: str = Field(min_length=2, max_length=40)
    item: str = Field(min_length=2, max_length=60)
    size: str = Field(default="NA", max_length=16)
    quantity: int = Field(default=1, ge=1, le=50)
    consent_recorded: bool = False
    event_code: str | None = Field(default=None, max_length=20)
    position: str | None = Field(default=None, max_length=40)
    override: bool = False
    override_approver: str | None = Field(default=None, max_length=120)
    override_reason: str | None = Field(default=None, max_length=400)
    verification_method: str = Field(default="id_lookup", max_length=30)
    idempotency_key: str | None = Field(default=None, max_length=64)


@router.post("/issue", status_code=201, dependencies=[Depends(require("merch.issue"))])
def issue_single_item(
    body: SingleIssueIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    fid = festival.id
    code = body.recipient_code.strip()
    dup_map = open_duplicate_codes(db, fid)
    if code in dup_map:
        raise HTTPException(409, f"{code} is flagged as a probable duplicate ({dup_map[code]}). Resolve in Participants before issuing.")

    if body.idempotency_key:
        existing_idem = db.scalar(select(MerchIssue).where(MerchIssue.festival_id == fid, MerchIssue.idempotency_key == body.idempotency_key))
        if existing_idem:
            return {"id": existing_idem.id, "status": existing_idem.status, "idempotent_replay": True}

    p = db.scalar(select(Participant).where(Participant.festival_id == fid, Participant.code == code, Participant.deleted_at.is_(None)))
    if not p:
        raise HTTPException(404, f"Participant {code} not found")
    if p.registration_status == "cancelled":
        raise HTTPException(409, f"{p.code} has a cancelled registration")

    m = db.scalar(
        select(MerchItem)
        .where(
            MerchItem.festival_id == fid,
            func.lower(MerchItem.item) == body.item.strip().lower(),
            func.lower(MerchItem.size) == body.size.strip().lower(),
        )
        .with_for_update()
    )
    if not m:
        raise HTTPException(404, f"Merchandise item {body.item} ({body.size}) not found")

    declared_size = (p.tshirt_size or "NA").strip().upper() if m.item.lower() == "t-shirt" else "NA"

    if m.item.lower() == "winner medal" and not body.event_code:
        raise HTTPException(422, "Select the event won when issuing a winner medal")

    existing_active = list(
        db.scalars(
            select(MerchIssue).where(
                MerchIssue.festival_id == fid,
                MerchIssue.participant_id == p.id,
                func.lower(MerchIssue.item) == m.item.lower(),
                MerchIssue.status == "issued",
            )
        )
    )
    if existing_active and m.item.lower() != "winner medal":
        if not body.override:
            first = existing_active[0]
            raise HTTPException(
                409,
                f"Already issued {m.item} ({first.issued_size}) to {p.full_name} ({code}) at {first.issued_at.isoformat() if first.issued_at else 'earlier'} by {first.issued_by}.",
            )
        if "merch.override" not in permissions_of(user):
            raise HTTPException(403, "Duplicate issue override requires merch.override permission")
        if not (body.override_reason and len(body.override_reason.strip()) >= 3):
            raise HTTPException(422, "Provide an override reason for issuing a duplicate item")

    is_sub = m.item.lower() == "t-shirt" and m.size.upper() != declared_size
    if is_sub and not body.consent_recorded:
        raise HTTPException(422, f"Participant declared size {declared_size}. Record participant consent to substitute {m.size}.")

    issuable_now = max(0, m.on_hand - m.csv_reserved)
    if body.quantity > issuable_now:
        raise HTTPException(
            409,
            f"Only {issuable_now} issuable {m.item} ({m.size}) remaining ({m.on_hand} on hand, {m.csv_reserved} reserved). Add to Owed list or release reserve.",
        )

    m.on_hand -= body.quantity
    if m.plan_reserved > 0:
        m.plan_reserved = max(0, m.plan_reserved - body.quantity)
    m.version = (m.version or 1) + 1

    iss = MerchIssue(
        festival_id=fid,
        participant_id=p.id,
        merch_item_id=m.id,
        item=m.item,
        requested_size=declared_size,
        issued_size=m.size,
        is_substitution=is_sub,
        consent_recorded=body.consent_recorded if is_sub else False,
        qty=body.quantity,
        desk="Main Desk",
        issued_by=user.name,
        issued_by_id=user.id,
        verification_method=body.verification_method,
        status="issued",
        event_code=body.event_code,
        position=body.position,
        override_reason=body.override_reason if body.override else None,
        override_approved_by=body.override_approver or (user.name if body.override else None),
        idempotency_key=body.idempotency_key,
    )
    db.add(iss)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, f"Already issued {m.item} to {code}") from exc

    for ow in db.scalars(
        select(MerchOwed).where(
            MerchOwed.festival_id == fid,
            MerchOwed.participant_id == p.id,
            func.lower(MerchOwed.item) == m.item.lower(),
            MerchOwed.status == "owed",
        )
    ):
        ow.status = "collected"

    log_merch(
        db,
        fid,
        user,
        action="issue_override" if body.override else ("issue_substitute" if is_sub else "issue"),
        merch_item_id=m.id,
        item=m.item,
        size=m.size,
        qty_delta=body.quantity,
        participant_code=code,
        participant_name=p.full_name,
        desk="Main Desk",
        is_exception=body.override or is_sub,
        reason=body.override_reason or (f"Substitute {declared_size} -> {m.size} (consent recorded)" if is_sub else (f"Event {body.event_code}" if body.event_code else None)),
    )
    record(
        db,
        user,
        fid,
        "merch.issue",
        "merch_issue",
        iss.id,
        f"{user.name} issued {body.quantity} {m.item} ({m.size}) to {p.full_name} ({code})",
        reason=body.override_reason,
    )
    db.commit()
    return {
        "id": iss.id,
        "item": iss.item,
        "size": iss.issued_size,
        "quantity": iss.qty,
        "recipient_code": code,
        "status": iss.status,
        "can_undo": True,
    }


@router.post("/issues/{issue_id}/undo", dependencies=[Depends(require("merch.issue"))])
def undo_issue(
    issue_id: int,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    iss = db.scalar(select(MerchIssue).where(MerchIssue.id == issue_id, MerchIssue.festival_id == festival.id).with_for_update())
    if not iss:
        raise HTTPException(404, "Issue record not found")
    if iss.status != "issued":
        raise HTTPException(409, f"Issue record is already {iss.status}")
    if iss.issued_by_id != user.id and "merch.override" not in permissions_of(user):
        raise HTTPException(403, "Only the user who issued this item (or an override approver) can undo it")
    if iss.issued_at and (now() - iss.issued_at) > timedelta(seconds=60) and "merch.override" not in permissions_of(user):
        raise HTTPException(409, "The 60-second undo window has expired. Use Void or Exchange instead.")

    m = db.scalar(select(MerchItem).where(MerchItem.id == iss.merch_item_id).with_for_update())
    m.on_hand += iss.qty
    m.version = (m.version or 1) + 1
    iss.status = "voided"
    iss.voided_by = user.name
    iss.voided_at = now()
    iss.void_reason = "Undone within 60s window"

    p = db.get(Participant, iss.participant_id)
    log_merch(
        db,
        festival.id,
        user,
        action="issue_undone",
        merch_item_id=m.id,
        item=iss.item,
        size=iss.issued_size,
        qty_delta=-iss.qty,
        participant_code=p.code if p else None,
        participant_name=p.full_name if p else None,
        is_exception=True,
        reason="60s Undo",
    )
    record(db, user, festival.id, "merch.issue.undo", "merch_issue", iss.id, f"{user.name} undid issue #{iss.id} ({iss.item} {iss.issued_size} for {p.code if p else 'participant'})")
    db.commit()
    return {"id": iss.id, "status": iss.status, "restored_on_hand": m.on_hand}


class VoidExchangeIn(BaseModel):
    return_to_stock: bool = True
    new_size: str | None = Field(default=None, max_length=16)
    consent_recorded: bool = True
    reason: str = Field(min_length=3, max_length=400)


@router.post("/issues/{issue_id}/exchange", dependencies=[Depends(require("merch.issue"))])
def void_or_exchange_issue(
    issue_id: int,
    body: VoidExchangeIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    iss = db.scalar(select(MerchIssue).where(MerchIssue.id == issue_id, MerchIssue.festival_id == festival.id).with_for_update())
    if not iss:
        raise HTTPException(404, "Issue record not found")
    if iss.status != "issued":
        raise HTTPException(409, f"Issue record is already {iss.status}")

    old_m = db.scalar(select(MerchItem).where(MerchItem.id == iss.merch_item_id).with_for_update())
    if body.return_to_stock:
        old_m.on_hand += iss.qty
        old_m.version = (old_m.version or 1) + 1

    iss.status = "voided"
    iss.voided_by = user.name
    iss.voided_at = now()
    iss.void_reason = body.reason
    db.flush()

    p = db.get(Participant, iss.participant_id)
    new_iss = None
    if body.new_size:
        new_m = db.scalar(
            select(MerchItem)
            .where(
                MerchItem.festival_id == festival.id,
                func.lower(MerchItem.item) == iss.item.lower(),
                func.lower(MerchItem.size) == body.new_size.strip().lower(),
            )
            .with_for_update()
        )
        if not new_m:
            raise HTTPException(404, f"Item {iss.item} ({body.new_size}) not found")
        if max(0, new_m.on_hand - new_m.csv_reserved) < iss.qty:
            raise HTTPException(409, f"Not enough issuable {iss.item} ({new_m.size}) for exchange")
        new_m.on_hand -= iss.qty
        new_m.version = (new_m.version or 1) + 1
        is_sub = new_m.size.upper() != (iss.requested_size or new_m.size).upper()
        new_iss = MerchIssue(
            festival_id=festival.id,
            participant_id=iss.participant_id,
            merch_item_id=new_m.id,
            item=new_m.item,
            requested_size=iss.requested_size,
            issued_size=new_m.size,
            is_substitution=is_sub,
            consent_recorded=body.consent_recorded,
            qty=iss.qty,
            desk=iss.desk,
            issued_by=user.name,
            issued_by_id=user.id,
            verification_method="exchange",
            status="issued",
        )
        db.add(new_iss)
        db.flush()

    log_merch(
        db,
        festival.id,
        user,
        action="exchange" if body.new_size else "void",
        merch_item_id=old_m.id,
        item=iss.item,
        size=f"{iss.issued_size}->{body.new_size}" if body.new_size else iss.issued_size,
        qty_delta=iss.qty,
        participant_code=p.code if p else None,
        participant_name=p.full_name if p else None,
        is_exception=True,
        reason=f"{body.reason} (returned to stock: {body.return_to_stock})",
    )
    record(
        db,
        user,
        festival.id,
        "merch.issue.exchange",
        "merch_issue",
        iss.id,
        f"{user.name} {'exchanged' if body.new_size else 'voided'} issue #{iss.id} for {p.code if p else 'participant'}",
        reason=body.reason,
    )
    db.commit()
    return {
        "voided_issue_id": iss.id,
        "status": iss.status,
        "new_issue_id": new_iss.id if new_iss else None,
        "new_size": new_iss.issued_size if new_iss else None,
    }


class SizeChangeIn(BaseModel):
    new_size: str = Field(pattern="^(S|M|L|XL|XXL)$")
    reason: str = Field(min_length=3, max_length=400)


@router.post("/participants/{code}/size-change", dependencies=[Depends(require("merch.issue"))])
def change_participant_size(
    code: str,
    body: SizeChangeIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    p = by_code(db, Participant, festival.id, code)
    old_size = p.tshirt_size
    if old_size == body.new_size:
        raise HTTPException(422, f"{p.code} already has size {body.new_size}")
    p.tshirt_size = body.new_size
    mark_plan_stale_if_needed(db, festival.id, f"Participant {p.code} changed T-shirt size from {old_size} to {body.new_size}")
    m = db.scalar(select(MerchItem).where(MerchItem.festival_id == festival.id, MerchItem.item == "T-shirt", MerchItem.size == body.new_size))
    log_merch(
        db,
        festival.id,
        user,
        action="size_changed",
        merch_item_id=m.id if m else None,
        item="T-shirt",
        size=f"{old_size}->{body.new_size}",
        qty_delta=0,
        participant_code=p.code,
        participant_name=p.full_name,
        is_exception=True,
        reason=body.reason,
    )
    record(db, user, festival.id, "merch.participant.size_change", "participant", p.code, f"{user.name} updated {p.code} T-shirt size {old_size} -> {body.new_size}", reason=body.reason)
    db.commit()
    return {
        "code": p.code,
        "old_size": old_size,
        "new_size": p.tshirt_size,
        "new_size_issuable": max(0, m.on_hand - m.csv_reserved) if m else 0,
    }


class ResolveDuplicateIn(BaseModel):
    action: str = Field(pattern="^(keep_separate|confirm_duplicate_delete)$")
    reason: str = Field(min_length=3, max_length=400)


@router.post("/duplicates/{code}/resolve", dependencies=[Depends(require("merch.manage"))])
def resolve_probable_duplicate(
    code: str,
    body: ResolveDuplicateIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    fid = festival.id
    p = by_code(db, Participant, fid, code)
    issues = list(
        db.scalars(
            select(DataIssue).where(
                DataIssue.festival_id == fid,
                DataIssue.entity_type == "participant",
                DataIssue.entity_code == p.code,
                DataIssue.issue == "possible_duplicate",
                DataIssue.status == "open",
            )
        )
    )
    for i in issues:
        i.status = "resolved"
    if body.action == "confirm_duplicate_delete":
        p.deleted_at = func.now()
    mark_plan_stale_if_needed(db, fid, f"Duplicate flag on {p.code} resolved ({body.action})")
    log_merch(
        db,
        fid,
        user,
        action=f"duplicate_{body.action}",
        item="Participant Entitlement",
        size=p.tshirt_size or "NA",
        qty_delta=1,
        participant_code=p.code,
        participant_name=p.full_name,
        is_exception=True,
        reason=body.reason,
    )
    record(db, user, fid, "merch.duplicate.resolve", "participant", p.code, f"{user.name} resolved duplicate {p.code} ({body.action})", reason=body.reason)
    db.commit()
    return {"code": p.code, "action": body.action, "status": "resolved"}


# ---------- Offline Paper Reconciliation ----------

class PaperRowIn(BaseModel):
    recipient_code: str = Field(min_length=2, max_length=40)
    item: str = Field(default="T-shirt", max_length=60)
    size: str = Field(default="NA", max_length=16)
    paper_ref: str | None = Field(default=None, max_length=120)


class PaperReconcileIn(BaseModel):
    rows: list[PaperRowIn]


@router.post("/reconcile-paper", dependencies=[Depends(require("merch.issue"))])
def reconcile_paper_tallies(
    body: PaperReconcileIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    fid = festival.id
    dup_map = open_duplicate_codes(db, fid)
    committed = []
    conflicts = []

    for idx, r in enumerate(body.rows, 1):
        code = r.recipient_code.strip()
        if code in dup_map:
            conflicts.append({"row": idx, "recipient_code": code, "reason": f"Probable duplicate ({dup_map[code]})"})
            continue
        p = db.scalar(select(Participant).where(Participant.festival_id == fid, Participant.code == code, Participant.deleted_at.is_(None)))
        if not p:
            conflicts.append({"row": idx, "recipient_code": code, "reason": "Participant not found"})
            continue
        sz = r.size if r.item.lower() != "t-shirt" else (r.size if r.size in SIZES else (p.tshirt_size or "L"))
        m = db.scalar(
            select(MerchItem)
            .where(MerchItem.festival_id == fid, func.lower(MerchItem.item) == r.item.strip().lower(), func.lower(MerchItem.size) == sz.lower())
            .with_for_update()
        )
        if not m:
            conflicts.append({"row": idx, "recipient_code": code, "reason": f"Item {r.item} ({sz}) not found"})
            continue
        existing = db.scalar(
            select(MerchIssue).where(
                MerchIssue.festival_id == fid,
                MerchIssue.participant_id == p.id,
                func.lower(MerchIssue.item) == m.item.lower(),
                MerchIssue.status == "issued",
            )
        )
        if existing and m.item.lower() != "winner medal":
            conflicts.append({"row": idx, "recipient_code": code, "reason": f"Already issued {m.item} ({existing.issued_size})"})
            continue
        if max(0, m.on_hand - m.csv_reserved) < 1:
            conflicts.append({"row": idx, "recipient_code": code, "reason": f"Insufficient issuable stock for {m.item} ({m.size})"})
            continue

        m.on_hand -= 1
        if m.plan_reserved > 0:
            m.plan_reserved = max(0, m.plan_reserved - 1)
        m.version = (m.version or 1) + 1
        iss = MerchIssue(
            festival_id=fid,
            participant_id=p.id,
            merch_item_id=m.id,
            item=m.item,
            requested_size=p.tshirt_size or "NA",
            issued_size=m.size,
            is_substitution=(m.item.lower() == "t-shirt" and m.size != p.tshirt_size),
            consent_recorded=True,
            qty=1,
            desk="Paper Reconciled",
            issued_by=user.name,
            issued_by_id=user.id,
            verification_method="paper_reconciled",
            status="issued",
        )
        db.add(iss)
        db.flush()
        log_merch(
            db,
            fid,
            user,
            action="paper_reconciled",
            merch_item_id=m.id,
            item=m.item,
            size=m.size,
            qty_delta=1,
            participant_code=p.code,
            participant_name=p.full_name,
            reason=f"Paper tally ref: {r.paper_ref or 'sheet'}",
        )
        committed.append({"row": idx, "recipient_code": p.code, "item": m.item, "size": m.size})

    db.commit()
    return {"committed": committed, "conflicts": conflicts}


# ---------- Owed List & Ledger & Exports ----------

class OwedCreateIn(BaseModel):
    recipient_code: str = Field(min_length=2, max_length=40)
    item: str = Field(default="T-shirt", max_length=60)
    size: str = Field(default="NA", max_length=16)
    reason: str = Field(default="size_short", max_length=40)
    note: str | None = Field(default=None, max_length=400)


@router.post("/owed", status_code=201, dependencies=[Depends(require("merch.issue"))])
def add_to_owed_list(
    body: OwedCreateIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    fid = festival.id
    code = body.recipient_code.strip()
    p = by_code(db, Participant, fid, code)
    sz = body.size
    if body.item.lower() == "t-shirt" and sz == "NA":
        sz = p.tshirt_size or "L"

    existing = db.scalar(
        select(MerchOwed).where(
            MerchOwed.festival_id == fid,
            MerchOwed.participant_id == p.id,
            func.lower(MerchOwed.item) == body.item.strip().lower(),
            MerchOwed.status == "owed",
        )
    )
    if existing:
        return {"id": existing.id, "recipient_code": code, "item": existing.item, "size": existing.requested_size, "status": existing.status}

    ow = MerchOwed(
        festival_id=fid,
        participant_id=p.id,
        item=body.item,
        requested_size=sz,
        status="owed",
        reason=body.reason,
        note=body.note,
        created_by=user.name,
    )
    db.add(ow)
    db.flush()
    log_merch(
        db,
        fid,
        user,
        action="owed_recorded",
        item=ow.item,
        size=ow.requested_size,
        qty_delta=1,
        participant_code=code,
        participant_name=p.full_name,
        is_exception=True,
        reason=body.note or body.reason,
    )
    db.commit()
    return {"id": ow.id, "recipient_code": code, "item": ow.item, "size": ow.requested_size, "status": ow.status}


@router.get("/owed", dependencies=[Depends(require("merch.read"))])
def list_owed(
    status: str = Query("owed", pattern="^(owed|collected|ALL)$"),
    size: str | None = None,
    contingent: str | None = None,
    festival: Festival = Depends(festival_access),
    db: Session = Depends(get_db),
):
    fid = festival.id
    q = select(MerchOwed).where(MerchOwed.festival_id == fid)
    if status != "ALL":
        q = q.where(MerchOwed.status == status)
    if size:
        q = q.where(func.lower(MerchOwed.requested_size) == size.strip().lower())
    rows = list(db.scalars(q))

    p_ids = {r.participant_id for r in rows}
    people = {p.id: p for p in db.scalars(select(Participant).where(Participant.id.in_(p_ids or {0})))}
    contingents = {c.id: c for c in db.scalars(select(Contingent).where(Contingent.festival_id == fid))}

    merch_stock = {
        (m.item.lower(), m.size.upper()): max(0, m.on_hand - m.csv_reserved)
        for m in db.scalars(select(MerchItem).where(MerchItem.festival_id == fid))
    }

    out = []
    for r in rows:
        p = people.get(r.participant_id)
        if not p:
            continue
        c = contingents.get(p.contingent_id)
        if contingent and c and c.code.lower() != contingent.strip().lower():
            continue
        out.append({
            "id": r.id,
            "recipient_code": p.code,
            "recipient_name": p.full_name,
            "contingent_code": c.code if c else None,
            "college": p.college,
            "phone_last4": mask_phone_last4(p.phone),
            "departure_at": p.departure_at,
            "item": r.item,
            "size": r.requested_size,
            "quantity": 1,
            "status": r.status,
            "stock_available_now": merch_stock.get((r.item.lower(), r.requested_size.upper()), 0) > 0,
            "reason": r.reason,
            "note": r.note,
            "created_by": r.created_by,
            "notify_link": f"/communication?audience=participants&subject=Merchandise+Ready:+{r.item}+({r.requested_size})&body=Hi+{p.full_name}+({p.code}),+your+{r.item}+(size+{r.requested_size})+is+ready+for+pickup+at+the+Merchandise+Desk.",
        })

    out.sort(key=lambda x: (x["departure_at"] is None, x["departure_at"] or datetime.max.replace(tzinfo=timezone.utc), x["recipient_code"]))
    return {"items": out}


@router.get("/owed/export.csv", dependencies=[Depends(require("merch.read"))])
def export_owed_csv(
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    rows = list(db.scalars(select(MerchOwed).where(MerchOwed.festival_id == festival.id).order_by(MerchOwed.id)))
    p_ids = {r.participant_id for r in rows}
    people = {p.id: p for p in db.scalars(select(Participant).where(Participant.id.in_(p_ids or {0})))}
    contingents = {c.id: c for c in db.scalars(select(Contingent).where(Contingent.festival_id == festival.id))}

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["id", "recipient_code", "recipient_name", "contingent_code", "college", "item", "size", "quantity", "status", "departure_at", "reason"])
    for r in rows:
        p = people.get(r.participant_id)
        if not p:
            continue
        c = contingents.get(p.contingent_id)
        writer.writerow([
            csv_safe(r.id),
            csv_safe(p.code),
            csv_safe(p.full_name),
            csv_safe(c.code if c else ""),
            csv_safe(p.college),
            csv_safe(r.item),
            csv_safe(r.requested_size),
            1,
            csv_safe(r.status),
            csv_safe(p.departure_at.isoformat() if p.departure_at else ""),
            csv_safe(r.reason),
        ])
    record(db, user, festival.id, "merch.owed.export", "merch_owed", "report", f"{user.name} exported {len(rows)} owed rows")
    db.commit()
    return StreamingResponse(
        iter([out.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="merchandise_owed_list.csv"'},
    )


@router.get("/packing-list.csv", dependencies=[Depends(require("merch.read"))])
def export_packing_list_csv(
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    cfg = get_or_create_config(db, festival.id)
    plan = cfg.approved_plan or compute_distribution_plan(db, festival.id, cfg.active_policy or "P1")
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([
        "contingent_code", "college", "leader", "escort", "headcount",
        "exact_tshirts", "substitute_proposed", "owed_tshirts",
        "id_cards_ready", "lanyards_ready", "full_kits_ready", "coverage_pct",
    ])
    for c in plan["contingents"]:
        writer.writerow([
            csv_safe(c["contingent_code"]),
            csv_safe(c["college"]),
            csv_safe(c["leader"]),
            csv_safe(c["escort"]),
            csv_safe(c["headcount"]),
            csv_safe(c["exact_tshirts"]),
            csv_safe(c["substitute_proposed"]),
            csv_safe(c["owed_tshirts"]),
            csv_safe(c["id_cards_ready"]),
            csv_safe(c["lanyards_ready"]),
            csv_safe(c["full_kits_ready"]),
            csv_safe(c["coverage_pct"]),
        ])
    record(db, user, festival.id, "merch.packing_list.export", "merch_config", "report", f"{user.name} exported contingent packing list")
    db.commit()
    return StreamingResponse(
        iter([out.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="merchandise_contingent_packing_list.csv"'},
    )


def merch_ledger_query(
    fid: int,
    item: str | None = None,
    size: str | None = None,
    action: str | None = None,
    actor: str | None = None,
    exceptions_only: bool = False,
    q: str | None = None,
):
    query = select(MerchLedger).where(MerchLedger.festival_id == fid)
    if item:
        query = query.where(func.lower(MerchLedger.item) == item.strip().lower())
    if size:
        query = query.where(func.lower(MerchLedger.size) == size.strip().lower())
    if action:
        query = query.where(MerchLedger.action == action.strip())
    if actor:
        query = query.where(MerchLedger.actor_name.ilike(f"%{actor.strip()}%"))
    if exceptions_only:
        query = query.where(MerchLedger.is_exception.is_(True))
    if q:
        term = f"%{q.strip()}%"
        query = query.where(
            or_(
                MerchLedger.participant_code.ilike(term),
                MerchLedger.participant_name.ilike(term),
                MerchLedger.item.ilike(term),
                MerchLedger.reason.ilike(term),
            )
        )
    return query.order_by(MerchLedger.id.desc())


@router.get("/ledger", dependencies=[Depends(require("merch.read"))])
def list_merch_ledger(
    item: str | None = None,
    size: str | None = None,
    action: str | None = None,
    actor: str | None = None,
    exceptions_only: bool = False,
    q: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
    festival: Festival = Depends(festival_access),
    db: Session = Depends(get_db),
):
    rows = list(db.scalars(merch_ledger_query(festival.id, item, size, action, actor, exceptions_only, q).limit(limit)))
    return {
        "items": [
            {
                "id": m.id,
                "action": m.action,
                "item": m.item,
                "size": m.size,
                "quantity": m.qty_delta,
                "participant_code": m.participant_code,
                "participant_name": m.participant_name,
                "desk": m.desk,
                "is_exception": m.is_exception,
                "actor_name": m.actor_name,
                "status": m.status,
                "reason": m.reason,
                "created_at": m.created_at,
            }
            for m in rows
        ]
    }


@router.get("/ledger/export.csv", dependencies=[Depends(require("merch.read"))])
def export_merch_ledger_csv(
    item: str | None = None,
    size: str | None = None,
    action: str | None = None,
    actor: str | None = None,
    exceptions_only: bool = False,
    q: str | None = None,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    rows = list(db.scalars(merch_ledger_query(festival.id, item, size, action, actor, exceptions_only, q)))
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([
        "id", "created_at", "action", "item", "size", "quantity",
        "participant_code", "participant_name", "desk",
        "is_exception", "actor_name", "status", "reason",
    ])
    for m in rows:
        writer.writerow([
            csv_safe(m.id),
            csv_safe(m.created_at.isoformat() if m.created_at else ""),
            csv_safe(m.action),
            csv_safe(m.item),
            csv_safe(m.size),
            csv_safe(m.qty_delta),
            csv_safe(m.participant_code),
            csv_safe(m.participant_name),
            csv_safe(m.desk),
            csv_safe("yes" if m.is_exception else "no"),
            csv_safe(m.actor_name),
            csv_safe(m.status),
            csv_safe(m.reason),
        ])
    record(db, user, festival.id, "merch.ledger.export", "merch_ledger", "report", f"{user.name} exported {len(rows)} merch ledger rows")
    db.commit()
    return StreamingResponse(
        iter([out.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="merchandise_ledger.csv"'},
    )

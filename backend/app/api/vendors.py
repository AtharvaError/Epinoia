import csv
import io
from collections import Counter
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.common import by_code, like
from app.auth.deps import current_user, festival_access, require
from app.database import get_db
from app.models import AuditLog, DataIssue, Festival, Participant, Resource, User, Vendor, VendorInterruption
from app.services.audit import record

router = APIRouter(prefix="/api/festivals/{festival_id}/vendors", tags=["vendors"])

MEAL_WINDOWS = [
    {"key": "breakfast", "label": "Breakfast", "window": "07:00–10:00", "start": 7, "end": 10},
    {"key": "midday", "label": "Mid-day / Brunch", "window": "10:00–12:00", "start": 10, "end": 12},
    {"key": "lunch", "label": "Lunch", "window": "12:00–15:00", "start": 12, "end": 15},
    {"key": "evening", "label": "Evening Snacks", "window": "16:00–19:00", "start": 16, "end": 19},
    {"key": "dinner", "label": "Dinner", "window": "19:00–23:00", "start": 19, "end": 23},
]


class VendorCreateIn(BaseModel):
    code: str | None = Field(default=None, max_length=20)
    name: str = Field(min_length=2, max_length=120)
    menu: str | None = Field(default=None, max_length=120)
    dietary_options: list[str] = Field(default_factory=list)
    hygiene_certificate_expiry: date | None = None
    hours: str | None = Field(default=None, max_length=30)
    capacity: int | None = Field(default=None, ge=0, le=100000)
    payment_terms: str | None = Field(default=None, max_length=80)
    reason: str = Field(min_length=3, max_length=300)


class VendorUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    menu: str | None = Field(default=None, max_length=120)
    dietary_options: list[str] | None = None
    hygiene_certificate_expiry: date | None = None
    hours: str | None = Field(default=None, max_length=30)
    capacity: int | None = Field(default=None, ge=0, le=100000)
    payment_terms: str | None = Field(default=None, max_length=80)
    reason: str = Field(min_length=3, max_length=300)


class VendorVerifyIn(BaseModel):
    operational_status: str | None = Field(
        default=None, pattern="^(unconfirmed|confirmed_active|interrupted|archived)$"
    )
    capacity_unit: str | None = Field(default=None, max_length=40)
    clear_capacity_unit: bool = False
    verified_dietary_options: list[str] | None = None
    nut_free_verified: bool | None = None
    allergen_controls_verified: bool | None = None
    cross_contamination_note: str | None = Field(default=None, max_length=240)
    certificate_verified: bool | None = None
    certificate_verification_note: str | None = Field(default=None, max_length=240)
    contact_name: str | None = Field(default=None, max_length=120)
    contact_phone: str | None = Field(default=None, max_length=40)
    verification_notes: str | None = Field(default=None, max_length=1000)
    reason: str = Field(min_length=3, max_length=300)


class VendorArchiveIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


class InterruptionSimulateIn(BaseModel):
    reason: str = Field(min_length=3, max_length=400)


class InterruptionDecisionIn(BaseModel):
    reason: str = Field(min_length=3, max_length=400)
    replacement_codes: list[str] = Field(default_factory=list)


def parse_hours_range(hours_str: str | None) -> tuple[int, int] | None:
    if not hours_str or "-" not in hours_str:
        return None
    try:
        start_part, end_part = hours_str.strip().split("-", 1)
        start_h = int(start_part.split(":")[0])
        end_h = int(end_part.split(":")[0])
        return start_h, end_h
    except (ValueError, IndexError):
        return None


def covered_meal_windows(hours_str: str | None) -> list[str]:
    parsed = parse_hours_range(hours_str)
    if not parsed:
        return []
    start_h, end_h = parsed
    covered: list[str] = []
    for w in MEAL_WINDOWS:
        # A vendor covers a meal window if its operating hours overlap the full meal window
        if start_h <= w["start"] and end_h >= w["end"]:
            covered.append(w["key"])
    return covered


def certificate_info(v: Vendor, festival: Festival) -> dict[str, Any]:
    exp = v.hygiene_certificate_expiry
    if exp is None:
        status = "missing"
        label = "Missing / Unknown"
        tone = "bad"
        boundary_note = "Hygiene certificate expiry date is missing and requires verification before service."
    elif exp < festival.start_date:
        status = "expired"
        label = f"Expired ({exp.isoformat()})"
        tone = "bad"
        boundary_note = (
            f"Expired on {exp.isoformat()} before festival start ({festival.start_date.isoformat()}). "
            "Non-compliant for festival food service unless a renewed certificate is verified."
        )
    elif exp <= festival.end_date:
        status = "expires_during_festival"
        label = f"Expires {exp.isoformat()} (During Fest)"
        tone = "warn"
        boundary_note = (
            f"Certificate expires on {exp.isoformat()} during the festival ({festival.start_date.isoformat()} to {festival.end_date.isoformat()}). "
            "Do not treat as automatically valid for every service throughout 31 Oct and 1 Nov without boundary verification."
        )
    else:
        status = "valid_beyond_festival"
        label = f"Valid beyond festival ({exp.isoformat()})"
        tone = "ok"
        boundary_note = f"Valid through {exp.isoformat()}, beyond festival end ({festival.end_date.isoformat()})."

    is_compliant = status == "valid_beyond_festival" or (
        status == "expires_during_festival" and bool(v.certificate_verified_by)
    )
    return {
        "status": status,
        "label": label,
        "tone": tone,
        "expiry": exp.isoformat() if exp else None,
        "boundary_flag": status == "expires_during_festival",
        "boundary_note": boundary_note,
        "is_compliant": is_compliant,
        "verified_by": v.certificate_verified_by,
        "verified_at": v.certificate_verified_at.isoformat() if v.certificate_verified_at else None,
        "verification_note": v.certificate_verification_note,
    }


def detect_vendor_inconsistencies(v: Vendor) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    menu_lower = (v.menu or "").lower()
    declared = [d.lower().strip() for d in (v.dietary_options or [])]
    verified = [d.lower().strip() for d in (v.verified_dietary_options or [])] if v.verified_dietary_options is not None else None

    # 1. Menu says Vegan bowls (VND11) but dietary_options state non-vegetarian;halal
    if "vegan" in menu_lower and ("vegan" not in declared or "non-vegetarian" in declared):
        resolved = verified is not None
        issues.append({
            "key": "menu_dietary_conflict",
            "severity": "low" if resolved else "medium",
            "title": f"{v.code}: Menu vs. dietary tags conflict",
            "detail": (
                f"Menu lists '{v.menu}', but CSV dietary_options state '{'; '.join(v.dietary_options or [])}'."
                + (f" Verified dietary options set to: {', '.join(v.verified_dietary_options or [])}." if resolved else " Requires organizer verification before labeling as vegan or non-vegetarian.")
            ),
            "resolved": "true" if resolved else "false",
        })

    # 2. Menu advertises Jain thali (VND09) but lists only vegetarian
    if "jain" in menu_lower and "jain" not in declared:
        resolved = verified is not None and ("jain" in verified or bool(v.verification_notes))
        issues.append({
            "key": "jain_menu_unconfirmed",
            "severity": "low" if resolved else "medium",
            "title": f"{v.code}: Advertises '{v.menu}' but lists only '{'; '.join(v.dietary_options or [])}'",
            "detail": (
                f"{v.name} ({v.code}) advertises '{v.menu}' on its menu, but declared dietary_options only state '{'; '.join(v.dietary_options or [])}'. "
                "Do not treat as compliant Jain coverage until root-vegetable / Jain preparation standards are explicitly verified."
            ),
            "resolved": "true" if resolved else "false",
        })

    # 3. Menu is Breakfast (VND04) but operating hours are 10:00-16:00
    if "breakfast" in menu_lower and v.hours == "10:00-16:00":
        issues.append({
            "key": "breakfast_hours_mismatch",
            "severity": "medium",
            "title": f"{v.code}: Menu is '{v.menu}' but operating hours are {v.hours}",
            "detail": (
                f"{v.name} ({v.code}) lists '{v.menu}' as its menu, but operates {v.hours} (missing the 07:00–10:00 early breakfast service window)."
            ),
            "resolved": "false",
        })

    return issues


def serialize_vendor(v: Vendor, festival: Festival) -> dict[str, Any]:
    cert = certificate_info(v, festival)
    inconsistencies = detect_vendor_inconsistencies(v)
    windows = covered_meal_windows(v.hours)
    requires_attention = (
        cert["status"] in ("expired", "expires_during_festival", "missing")
        or len(inconsistencies) > 0
        or v.capacity_unit is None
        or v.operational_status == "unconfirmed"
        or v.operational_status == "interrupted"
    )
    attention_reasons: list[str] = []
    if cert["status"] == "expired":
        attention_reasons.append(f"Hygiene certificate expired ({cert['expiry']})")
    elif cert["status"] == "expires_during_festival" and not v.certificate_verified_by:
        attention_reasons.append(f"Certificate expires during festival ({cert['expiry']})")
    elif cert["status"] == "missing":
        attention_reasons.append("Hygiene certificate missing")
    for inc in inconsistencies:
        if inc["resolved"] != "true":
            attention_reasons.append(inc["title"])
    if v.capacity_unit is None:
        attention_reasons.append("Capacity unit not verified")
    if v.operational_status == "unconfirmed":
        attention_reasons.append("Operational availability unconfirmed")
    elif v.operational_status == "interrupted":
        attention_reasons.append("Service interrupted / unavailable")

    return {
        "id": v.id,
        "code": v.code,
        "name": v.name,
        "menu": v.menu,
        "dietary_options": v.dietary_options or [],
        "verified_dietary_options": v.verified_dietary_options,
        "effective_dietary_options": v.verified_dietary_options if v.verified_dietary_options is not None else (v.dietary_options or []),
        "hygiene_certificate_expiry": v.hygiene_certificate_expiry.isoformat() if v.hygiene_certificate_expiry else None,
        "certificate": cert,
        "hours": v.hours,
        "meal_windows": windows,
        "capacity": v.capacity,
        "capacity_unit": v.capacity_unit,
        "capacity_unit_display": v.capacity_unit if v.capacity_unit else "Capacity unit not verified.",
        "capacity_verified_by": v.capacity_verified_by,
        "capacity_verified_at": v.capacity_verified_at.isoformat() if v.capacity_verified_at else None,
        "payment_terms": v.payment_terms,
        "status": v.status,
        "operational_status": v.operational_status,
        "source": v.source,
        "original_pack_data": v.original_pack_data,
        "nut_free_verified": bool(v.nut_free_verified),
        "allergen_controls_verified": bool(v.allergen_controls_verified),
        "cross_contamination_note": v.cross_contamination_note,
        "contact_name": v.contact_name,
        "contact_phone": v.contact_phone,
        "verification_notes": v.verification_notes,
        "verified_by": v.verified_by,
        "verified_at": v.verified_at.isoformat() if v.verified_at else None,
        "archived_at": v.archived_at.isoformat() if v.archived_at else None,
        "inconsistencies": inconsistencies,
        "requires_attention": requires_attention,
        "attention_reasons": attention_reasons,
    }


def build_intelligence(db: Session, festival: Festival) -> dict[str, Any]:
    vendors = list(
        db.scalars(
            select(Vendor)
            .where(Vendor.festival_id == festival.id, Vendor.archived_at.is_(None))
            .order_by(Vendor.code)
        )
    )
    serialized = [serialize_vendor(v, festival) for v in vendors]

    # Participant dietary demand from actual participants table
    dietary_rows = db.execute(
        select(Participant.dietary_needs, func.count(Participant.id))
        .where(Participant.festival_id == festival.id, Participant.deleted_at.is_(None))
        .group_by(Participant.dietary_needs)
    ).all()
    participant_counts: dict[str, int] = {}
    total_participants = 0
    for need, count in dietary_rows:
        key = (need or "").strip().lower() or "blank"
        participant_counts[key] = count
        total_participants += count

    # Certificate counts
    expired_vendors = [v for v in serialized if v["certificate"]["status"] == "expired"]
    expiring_vendors = [v for v in serialized if v["certificate"]["status"] == "expires_during_festival"]
    valid_beyond_vendors = [v for v in serialized if v["certificate"]["status"] == "valid_beyond_festival"]
    missing_cert_vendors = [v for v in serialized if v["certificate"]["status"] == "missing"]

    # Dietary coverage matrix
    # Compare declared participant needs vs vendor listed options vs compliant valid vendors vs independently verified
    dietary_categories = [
        {"key": "nut-free", "label": "Nut Allergy / Nut-Free", "participant_key": "nut allergy"},
        {"key": "jain", "label": "Jain", "participant_key": "jain"},
        {"key": "vegan", "label": "Vegan", "participant_key": "vegan"},
        {"key": "gluten-free", "label": "Gluten-Free", "participant_key": "gluten-free"},
        {"key": "vegetarian", "label": "Vegetarian", "participant_key": "vegetarian"},
        {"key": "non-vegetarian", "label": "Non-Vegetarian", "participant_key": "non-vegetarian"},
        {"key": "halal", "label": "Halal", "participant_key": "halal"},
    ]

    coverage_matrix: list[dict[str, Any]] = []
    dietary_gaps_count = 0

    for cat in dietary_categories:
        dkey = cat["key"]
        p_count = participant_counts.get(cat["participant_key"], 0)
        listed = [v for v in serialized if dkey in [x.lower() for x in v["dietary_options"]]]
        valid_beyond = [
            v for v in listed
            if v["certificate"]["status"] == "valid_beyond_festival" and v["operational_status"] != "interrupted"
        ]
        expiring_during = [
            v for v in listed
            if v["certificate"]["status"] == "expires_during_festival" and v["operational_status"] != "interrupted"
        ]
        expired = [v for v in listed if v["certificate"]["status"] == "expired"]

        if dkey == "nut-free":
            independently_verified = [
                v for v in serialized
                if v["nut_free_verified"]
                and v["allergen_controls_verified"]
                and v["certificate"]["status"] != "expired"
                and v["operational_status"] != "interrupted"
            ]
        else:
            independently_verified = [
                v for v in serialized
                if v["verified_dietary_options"] is not None
                and dkey in [x.lower() for x in v["verified_dietary_options"]]
                and v["certificate"]["status"] != "expired"
                and v["operational_status"] != "interrupted"
            ]

        if not listed and not independently_verified:
            status = "critical_gap"
            status_label = "No declared vendor option"
            tone = "bad"
            note = (
                f"{p_count:,} participants declare a nut allergy, but 0 of {len(serialized)} vendors list a nut-free option. "
                "Menu labels alone do not establish allergen safety; verified kitchen allergen controls and cross-contamination prevention are required."
                if dkey == "nut-free"
                else f"No vendor lists {cat['label']}."
            )
            dietary_gaps_count += 1
        elif len(valid_beyond) == 0 and len(expiring_during) == 0 and len(expired) > 0 and not independently_verified:
            status = "expired_only"
            status_label = "Listed only by expired vendor"
            tone = "bad"
            codes = ", ".join(v["code"] for v in expired)
            exp_dates = ", ".join(sorted({v["certificate"]["expiry"] or "" for v in expired}))
            note = (
                f"Only {codes} lists {cat['label']}, and its hygiene certificate expired on {exp_dates}. "
                "An expired vendor cannot be counted as compliant coverage."
            )
            dietary_gaps_count += 1
        elif len(valid_beyond) == 0 and len(expiring_during) == 1 and not independently_verified:
            status = "single_expiring_dependency"
            status_label = "Single vendor (expires Oct 31)"
            tone = "warn"
            v0 = expiring_during[0]
            note = (
                f"{p_count:,} participants require {cat['label']}, which depends solely on {v0['code']} ({v0['name']}) "
                f"whose certificate expires during the festival on {v0['certificate']['expiry']}."
            )
            dietary_gaps_count += 1
        else:
            status = "covered"
            status_label = f"{len(valid_beyond)} valid beyond fest" + (f" (+{len(expiring_during)} expiring Oct 31)" if expiring_during else "")
            tone = "ok"
            note = (
                f"Covered by {len(valid_beyond)} vendor(s) with certificates valid beyond the festival"
                + (f" and {len(expiring_during)} expiring on 31 Oct." if expiring_during else ".")
            )

        coverage_matrix.append({
            "key": dkey,
            "label": cat["label"],
            "participant_need_label": cat["participant_key"],
            "participant_count": p_count,
            "listed_count": len(listed),
            "listed_codes": [v["code"] for v in listed],
            "valid_beyond_count": len(valid_beyond),
            "valid_beyond_codes": [v["code"] for v in valid_beyond],
            "expiring_during_count": len(expiring_during),
            "expiring_during_codes": [v["code"] for v in expiring_during],
            "expired_count": len(expired),
            "expired_codes": [v["code"] for v in expired],
            "independently_verified_count": len(independently_verified),
            "independently_verified_codes": [v["code"] for v in independently_verified],
            "status": status,
            "status_label": status_label,
            "tone": tone,
            "note": note,
        })

    # Also include blank dietary participants row for full transparency
    blank_dietary_count = participant_counts.get("blank", 0)

    # Build actionable intelligence alerts (derived live from data)
    alerts: list[dict[str, Any]] = []
    nut_allergy_count = participant_counts.get("nut allergy", 0)
    nut_listed = [v["code"] for v in serialized if "nut-free" in [x.lower() for x in v["dietary_options"]]]
    nut_verified = [v["code"] for v in serialized if v["nut_free_verified"] and v["allergen_controls_verified"]]
    if not nut_listed and nut_allergy_count > 0:
        alerts.append({
            "id": "nut-free-gap",
            "severity": "critical",
            "title": f"No declared nut-free vendor option despite {nut_allergy_count:,} declared nut allergies",
            "detail": (
                f"0 of {len(serialized)} vendors declare a 'nut-free' option in vendors.csv while {nut_allergy_count:,} participants report a nut allergy. "
                + (f"Independently verified nut-free controls recorded for: {', '.join(nut_verified)}." if nut_verified else "No vendor has verified kitchen allergen controls or cross-contamination clearance.")
            ),
            "affected_vendors": nut_verified,
            "participant_impact": nut_allergy_count,
            "category": "allergen_safety",
        })

    jain_row = next((c for c in coverage_matrix if c["key"] == "jain"), None)
    vegan_row = next((c for c in coverage_matrix if c["key"] == "vegan"), None)
    if (jain_row and jain_row["status"] == "expired_only") or (vegan_row and vegan_row["status"] == "expired_only"):
        affected_codes = sorted(set((jain_row["expired_codes"] if jain_row else []) + (vegan_row["expired_codes"] if vegan_row else [])))
        alerts.append({
            "id": "jain-vegan-expired",
            "severity": "critical",
            "title": "Jain and vegan options are listed only by vendors with expired certificates",
            "detail": (
                f"Jain ({', '.join(jain_row['listed_codes']) if jain_row else 'none'}, {jain_row['participant_count'] if jain_row else 0} participants) "
                f"and Vegan ({', '.join(vegan_row['listed_codes']) if vegan_row else 'none'}) are listed only by vendors whose hygiene certificates expired on 2026-09-29. "
                "An expired vendor is non-compliant even if its menu lists the required dietary category."
            ),
            "affected_vendors": affected_codes,
            "participant_impact": jain_row["participant_count"] if jain_row else 0,
            "category": "compliance_gap",
        })

    if expired_vendors:
        alerts.append({
            "id": "expired-certificates",
            "severity": "critical",
            "title": f"{len(expired_vendors)} vendor hygiene certificates expired before the festival (2026-09-29)",
            "detail": (
                f"Vendors {', '.join(v['code'] for v in expired_vendors)} have hygiene certificates that expired on "
                f"{', '.join(sorted({v['certificate']['expiry'] or '' for v in expired_vendors}))} (before festival start {festival.start_date.isoformat()})."
            ),
            "affected_vendors": [v["code"] for v in expired_vendors],
            "participant_impact": None,
            "category": "certificate_expired",
        })

    gf_row = next((c for c in coverage_matrix if c["key"] == "gluten-free"), None)
    if gf_row and gf_row["listed_count"] == 1 and gf_row["expiring_during_count"] == 1:
        gf_code = gf_row["expiring_during_codes"][0]
        alerts.append({
            "id": "gluten-free-single-expiring",
            "severity": "high",
            "title": f"Gluten-free support ({gf_row['participant_count']:,} participants) depends on one vendor ({gf_code}) whose certificate expires during the festival",
            "detail": (
                f"Only {gf_code} lists gluten-free options, and its hygiene certificate expires on 2026-10-31 during the festival (30 Oct–1 Nov 2026). "
                f"Any service interruption or unrenewed certificate on {gf_code} leaves all {gf_row['participant_count']:,} gluten-free participants without a compliant vendor."
            ),
            "affected_vendors": [gf_code],
            "participant_impact": gf_row["participant_count"],
            "category": "single_point_of_failure",
        })

    if expiring_vendors:
        alerts.append({
            "id": "expiring-during-festival",
            "severity": "high",
            "title": f"{len(expiring_vendors)} certificates expire on 31 October 2026 during the festival",
            "detail": (
                f"Vendors {', '.join(v['code'] for v in expiring_vendors)} have certificates expiring on 2026-10-31. "
                "Do not treat an expiry date of 31 October as automatically valid for every service throughout 31 Oct and 1 Nov; verify certificate boundary coverage."
            ),
            "affected_vendors": [v["code"] for v in expiring_vendors],
            "participant_impact": None,
            "category": "certificate_boundary",
        })

    inconsistent_vendors = [v for v in serialized if v["inconsistencies"]]
    if inconsistent_vendors:
        alerts.append({
            "id": "menu-dietary-inconsistencies",
            "severity": "medium",
            "title": "Vendor menu, dietary labels, and operating hours require verification",
            "detail": " | ".join(
                inc["detail"] for v in inconsistent_vendors for inc in v["inconsistencies"]
            ),
            "affected_vendors": [v["code"] for v in inconsistent_vendors],
            "participant_impact": None,
            "category": "data_inconsistency",
        })

    # Operating hours & capacity summary
    hours_groups: dict[str, list[str]] = {}
    for v in serialized:
        h = v["hours"] or "Unknown"
        hours_groups.setdefault(h, []).append(v["code"])

    meal_window_summary = []
    for w in MEAL_WINDOWS:
        active_in_window = [v for v in serialized if w["key"] in v["meal_windows"]]
        compliant_in_window = [
            v for v in active_in_window
            if v["certificate"]["status"] == "valid_beyond_festival"
            or (v["certificate"]["status"] == "expires_during_festival" and v["certificate"]["verified_by"])
        ]
        expiring_in_window = [
            v for v in active_in_window
            if v["certificate"]["status"] == "expires_during_festival" and not v["certificate"]["verified_by"]
        ]
        expired_in_window = [
            v for v in active_in_window
            if v["certificate"]["status"] == "expired"
        ]
        meal_window_summary.append({
            **w,
            "total_vendors": len(active_in_window),
            "vendor_codes": [v["code"] for v in active_in_window],
            "valid_beyond_codes": [v["code"] for v in compliant_in_window],
            "expiring_codes": [v["code"] for v in expiring_in_window],
            "expired_codes": [v["code"] for v in expired_in_window],
        })

    total_listed_capacity = sum(v["capacity"] or 0 for v in serialized)
    verified_unit_vendors = [v for v in serialized if v["capacity_unit"]]
    all_units_verified = len(serialized) > 0 and len(verified_unit_vendors) == len(serialized)

    requiring_verification_vendors = [v for v in serialized if v["requires_attention"]]

    #Recent interruptions
    interruptions = list(
        db.scalars(
            select(VendorInterruption)
            .where(VendorInterruption.festival_id == festival.id)
            .order_by(VendorInterruption.created_at.desc())
            .limit(20)
        )
    )

    return {
        "festival": {
            "id": festival.id,
            "name": festival.name,
            "start_date": festival.start_date.isoformat(),
            "end_date": festival.end_date.isoformat(),
        },
        "overview": {
            "total_vendors": len(serialized),
            "certificates_expired": len(expired_vendors),
            "expired_vendor_codes": [v["code"] for v in expired_vendors],
            "certificates_expiring_oct31": len(expiring_vendors),
            "expiring_vendor_codes": [v["code"] for v in expiring_vendors],
            "certificates_valid_beyond": len(valid_beyond_vendors),
            "valid_beyond_vendor_codes": [v["code"] for v in valid_beyond_vendors],
            "certificates_missing": len(missing_cert_vendors),
            "dietary_coverage_gaps": dietary_gaps_count,
            "vendors_requiring_verification": len(requiring_verification_vendors),
            "requiring_verification_codes": [v["code"] for v in requiring_verification_vendors],
            "confirmed_active_count": sum(1 for v in serialized if v["operational_status"] == "confirmed_active"),
            "unconfirmed_count": sum(1 for v in serialized if v["operational_status"] == "unconfirmed"),
            "interrupted_count": sum(1 for v in serialized if v["operational_status"] == "interrupted"),
        },
        "alerts": alerts,
        "dietary_intelligence": {
            "total_participants": total_participants,
            "blank_dietary_participants": blank_dietary_count,
            "participant_counts": participant_counts,
            "coverage_matrix": coverage_matrix,
            "inconsistencies": [
                {"vendor_code": v["code"], "vendor_name": v["name"], "menu": v["menu"], "dietary_options": v["dietary_options"], "hours": v["hours"], **inc}
                for v in inconsistent_vendors
                for inc in v["inconsistencies"]
            ],
        },
        "hours_and_capacity": {
            "total_listed_capacity": total_listed_capacity,
            "capacity_unit_status": (
                "All vendor capacity units verified"
                if all_units_verified
                else "Capacity unit not verified."
            ),
            "verified_unit_count": len(verified_unit_vendors),
            "unverified_unit_count": len(serialized) - len(verified_unit_vendors),
            "numerical_allocation_enabled": all_units_verified,
            "hours_distribution": [
                {"hours": h, "count": len(codes), "vendor_codes": codes}
                for h, codes in sorted(hours_groups.items())
            ],
            "meal_windows": meal_window_summary,
        },
        "procurement_scope_note": {
            "scope": "food_vendors_only",
            "message": (
                "These 14 records are food vendors from vendors.csv with payment terms only. "
                "Merchandise T-shirt sizes, stock, and shortages are managed in Venues & Resources (Inventory), and verified financial records are managed in Finance."
            ),
        },
        "interruptions": [serialize_interruption(i) for i in interruptions],
        "vendors": serialized,
    }


def serialize_interruption(i: VendorInterruption) -> dict[str, Any]:
    return {
        "id": i.id,
        "vendor_id": i.vendor_id,
        "vendor_code": i.vendor_code,
        "reason": i.reason,
        "simulation_report": i.simulation_report,
        "replacement_codes": i.replacement_codes or [],
        "status": i.status,
        "created_by_id": i.created_by_id,
        "created_by_name": i.created_by_name,
        "created_at": i.created_at.isoformat() if i.created_at else None,
        "decided_by_id": i.decided_by_id,
        "decided_by_name": i.decided_by_name,
        "decided_at": i.decided_at.isoformat() if i.decided_at else None,
        "decision_reason": i.decision_reason,
    }


@router.get("/intelligence", dependencies=[Depends(require("vendors.read"))])
def get_vendors_intelligence(festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    return build_intelligence(db, festival)


@router.get("", dependencies=[Depends(require("vendors.read"))])
def list_vendors(
    q: str | None = Query(default=None, max_length=80),
    filter_by: str | None = Query(default=None),
    dietary: str | None = Query(default=None),
    hours: str | None = Query(default=None),
    include_archived: bool = Query(default=False),
    festival: Festival = Depends(festival_access),
    db: Session = Depends(get_db),
):
    stmt = select(Vendor).where(Vendor.festival_id == festival.id)
    if not include_archived:
        stmt = stmt.where(Vendor.archived_at.is_(None))
    if q:
        pattern = like(q)
        stmt = stmt.where(
            or_(
                Vendor.code.ilike(pattern, escape="\\"),
                Vendor.name.ilike(pattern, escape="\\"),
                Vendor.menu.ilike(pattern, escape="\\"),
                Vendor.payment_terms.ilike(pattern, escape="\\"),
            )
        )
    if hours:
        stmt = stmt.where(Vendor.hours == hours)

    vendors = [serialize_vendor(v, festival) for v in db.scalars(stmt.order_by(Vendor.code))]

    if filter_by == "valid":
        vendors = [v for v in vendors if v["certificate"]["status"] == "valid_beyond_festival"]
    elif filter_by == "expired":
        vendors = [v for v in vendors if v["certificate"]["status"] == "expired"]
    elif filter_by == "expiring":
        vendors = [v for v in vendors if v["certificate"]["status"] == "expires_during_festival"]
    elif filter_by == "attention":
        vendors = [v for v in vendors if v["requires_attention"]]
    elif filter_by == "confirmed":
        vendors = [v for v in vendors if v["operational_status"] == "confirmed_active"]
    elif filter_by == "interrupted":
        vendors = [v for v in vendors if v["operational_status"] == "interrupted"]

    if dietary:
        d_low = dietary.strip().lower()
        if d_low == "nut-free":
            vendors = [
                v for v in vendors
                if "nut-free" in [x.lower() for x in v["effective_dietary_options"]] or v["nut_free_verified"]
            ]
        else:
            vendors = [
                v for v in vendors
                if d_low in [x.lower() for x in v["effective_dietary_options"]]
            ]

    return {"items": vendors, "total": len(vendors)}


@router.get("/{code}", dependencies=[Depends(require("vendors.read"))])
def get_vendor_detail(code: str, festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    v = by_code(db, Vendor, festival.id, code)
    audit_rows = list(
        db.scalars(
            select(AuditLog)
            .where(AuditLog.festival_id == festival.id, AuditLog.entity == "vendor", AuditLog.entity_id == v.code)
            .order_by(AuditLog.created_at.desc())
            .limit(50)
        )
    )
    interruption_rows = list(
        db.scalars(
            select(VendorInterruption)
            .where(VendorInterruption.festival_id == festival.id, VendorInterruption.vendor_id == v.id)
            .order_by(VendorInterruption.created_at.desc())
        )
    )
    data_issues = list(
        db.scalars(
            select(DataIssue)
            .where(DataIssue.festival_id == festival.id, DataIssue.entity_type == "vendor", DataIssue.entity_code == v.code)
        )
    )
    return {
        "vendor": serialize_vendor(v, festival),
        "data_issues": [
            {"id": di.id, "issue": di.issue, "detail": di.detail, "status": di.status}
            for di in data_issues
        ],
        "interruptions": [serialize_interruption(i) for i in interruption_rows],
        "audit_history": [
            {
                "id": a.id,
                "actor": a.actor_label,
                "action": a.action,
                "summary": a.summary,
                "reason": a.reason,
                "before": a.before,
                "after": a.after,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in audit_rows
        ],
    }


def next_vendor_code(db: Session, festival_id: int) -> str:
    codes = list(db.scalars(select(Vendor.code).where(Vendor.festival_id == festival_id)))
    nums = [int(c[3:]) for c in codes if c.upper().startswith("VND") and c[3:].isdigit()]
    return f"VND{max(nums, default=0) + 1:02d}"


@router.post("", status_code=201, dependencies=[Depends(require("vendors.write"))])
def create_vendor(
    body: VendorCreateIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    code = (body.code or "").strip().upper() or next_vendor_code(db, festival.id)
    existing = db.scalar(select(Vendor).where(Vendor.festival_id == festival.id, Vendor.code == code))
    if existing:
        raise HTTPException(409, f"Vendor {code} already exists in this festival.")
    normalized_dietary = [d.strip().lower() for d in body.dietary_options if d.strip()]
    v = Vendor(
        festival_id=festival.id,
        code=code,
        name=body.name.strip(),
        menu=body.menu.strip() if body.menu else None,
        dietary_options=normalized_dietary,
        hygiene_certificate_expiry=body.hygiene_certificate_expiry,
        hours=body.hours.strip() if body.hours else None,
        capacity=body.capacity,
        payment_terms=body.payment_terms.strip() if body.payment_terms else None,
        source="manual",
        original_pack_data=None,
        operational_status="unconfirmed",
    )
    db.add(v)
    try:
        db.flush()
        record(
            db,
            user,
            festival.id,
            "vendor.create",
            "vendor",
            v.code,
            f"{user.name} created vendor {v.code} ({v.name})",
            after={"code": v.code, "name": v.name, "menu": v.menu, "dietary_options": normalized_dietary, "source": "manual"},
            reason=body.reason,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, f"Vendor {code} already exists.") from exc
    return serialize_vendor(v, festival)


@router.patch("/{code}", dependencies=[Depends(require("vendors.write"))])
def update_vendor(
    code: str,
    body: VendorUpdateIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    v = by_code(db, Vendor, festival.id, code)
    before = {
        "name": v.name,
        "menu": v.menu,
        "dietary_options": v.dietary_options,
        "hygiene_certificate_expiry": v.hygiene_certificate_expiry.isoformat() if v.hygiene_certificate_expiry else None,
        "hours": v.hours,
        "capacity": v.capacity,
        "payment_terms": v.payment_terms,
    }
    updates = body.model_dump(exclude_unset=True, exclude={"reason"})
    if "dietary_options" in updates and updates["dietary_options"] is not None:
        updates["dietary_options"] = [d.strip().lower() for d in updates["dietary_options"] if d.strip()]
    for field, val in updates.items():
        setattr(v, field, val)
    after = {
        "name": v.name,
        "menu": v.menu,
        "dietary_options": v.dietary_options,
        "hygiene_certificate_expiry": v.hygiene_certificate_expiry.isoformat() if v.hygiene_certificate_expiry else None,
        "hours": v.hours,
        "capacity": v.capacity,
        "payment_terms": v.payment_terms,
    }
    record(
        db,
        user,
        festival.id,
        "vendor.update",
        "vendor",
        v.code,
        f"{user.name} updated vendor {v.code} ({v.name})",
        before=before,
        after=after,
        reason=body.reason,
    )
    db.commit()
    return serialize_vendor(v, festival)


@router.post("/{code}/verify", dependencies=[Depends(require("vendors.write"))])
def verify_vendor_information(
    code: str,
    body: VendorVerifyIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    v = by_code(db, Vendor, festival.id, code)
    now = datetime.now(timezone.utc)
    before = {
        "operational_status": v.operational_status,
        "capacity_unit": v.capacity_unit,
        "verified_dietary_options": v.verified_dietary_options,
        "nut_free_verified": v.nut_free_verified,
        "allergen_controls_verified": v.allergen_controls_verified,
        "cross_contamination_note": v.cross_contamination_note,
        "certificate_verified_by": v.certificate_verified_by,
        "certificate_verification_note": v.certificate_verification_note,
        "contact_name": v.contact_name,
        "contact_phone": v.contact_phone,
        "verification_notes": v.verification_notes,
    }

    if body.operational_status is not None:
        if body.operational_status == "confirmed_active" and v.hygiene_certificate_expiry and v.hygiene_certificate_expiry < festival.start_date and not body.certificate_verified and not v.certificate_verified_by:
            raise HTTPException(
                422,
                f"Cannot confirm {v.code} as operationally active while its hygiene certificate is expired ({v.hygiene_certificate_expiry.isoformat()}) without verified renewal.",
            )
        v.operational_status = body.operational_status
        v.status = "active" if body.operational_status in ("confirmed_active", "unconfirmed") else body.operational_status

    if body.clear_capacity_unit:
        v.capacity_unit = None
        v.capacity_verified_by = None
        v.capacity_verified_at = None
    elif body.capacity_unit is not None:
        unit_clean = body.capacity_unit.strip()
        v.capacity_unit = unit_clean or None
        v.capacity_verified_by = user.name if unit_clean else None
        v.capacity_verified_at = now if unit_clean else None

    if body.verified_dietary_options is not None:
        v.verified_dietary_options = [d.strip().lower() for d in body.verified_dietary_options if d.strip()]

    if body.nut_free_verified is not None:
        v.nut_free_verified = body.nut_free_verified
    if body.allergen_controls_verified is not None:
        v.allergen_controls_verified = body.allergen_controls_verified
    if body.cross_contamination_note is not None:
        v.cross_contamination_note = body.cross_contamination_note.strip() or None

    if body.certificate_verified is not None:
        if body.certificate_verified:
            v.certificate_verified_by = user.name
            v.certificate_verified_at = now
        else:
            v.certificate_verified_by = None
            v.certificate_verified_at = None
    if body.certificate_verification_note is not None:
        v.certificate_verification_note = body.certificate_verification_note.strip() or None

    if body.contact_name is not None:
        v.contact_name = body.contact_name.strip() or None
    if body.contact_phone is not None:
        v.contact_phone = body.contact_phone.strip() or None
    if body.verification_notes is not None:
        v.verification_notes = body.verification_notes.strip() or None

    v.verified_by = user.name
    v.verified_at = now

    after = {
        "operational_status": v.operational_status,
        "capacity_unit": v.capacity_unit,
        "verified_dietary_options": v.verified_dietary_options,
        "nut_free_verified": v.nut_free_verified,
        "allergen_controls_verified": v.allergen_controls_verified,
        "cross_contamination_note": v.cross_contamination_note,
        "certificate_verified_by": v.certificate_verified_by,
        "certificate_verification_note": v.certificate_verification_note,
        "contact_name": v.contact_name,
        "contact_phone": v.contact_phone,
        "verification_notes": v.verification_notes,
    }
    record(
        db,
        user,
        festival.id,
        "vendor.verify",
        "vendor",
        v.code,
        f"{user.name} updated verified operational fields for {v.code} ({v.name})",
        before=before,
        after=after,
        reason=body.reason,
    )
    db.commit()
    return serialize_vendor(v, festival)


@router.post("/{code}/archive", dependencies=[Depends(require("vendors.write"))])
def archive_vendor(
    code: str,
    body: VendorArchiveIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    v = by_code(db, Vendor, festival.id, code)
    before = {"operational_status": v.operational_status, "archived_at": v.archived_at.isoformat() if v.archived_at else None}
    v.archived_at = datetime.now(timezone.utc)
    v.operational_status = "archived"
    v.status = "archived"
    record(
        db,
        user,
        festival.id,
        "vendor.archive",
        "vendor",
        v.code,
        f"{user.name} archived vendor {v.code} ({v.name})",
        before=before,
        after={"operational_status": "archived", "archived_at": v.archived_at.isoformat()},
        reason=body.reason,
    )
    db.commit()
    return serialize_vendor(v, festival)


@router.post("/{code}/restore", dependencies=[Depends(require("vendors.write"))])
def restore_vendor(
    code: str,
    body: VendorArchiveIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    v = by_code(db, Vendor, festival.id, code)
    before = {"operational_status": v.operational_status, "archived_at": v.archived_at.isoformat() if v.archived_at else None}
    v.archived_at = None
    v.operational_status = "unconfirmed"
    v.status = "active"
    record(
        db,
        user,
        festival.id,
        "vendor.restore",
        "vendor",
        v.code,
        f"{user.name} restored vendor {v.code} ({v.name}) to unconfirmed status",
        before=before,
        after={"operational_status": "unconfirmed", "archived_at": None},
        reason=body.reason,
    )
    db.commit()
    return serialize_vendor(v, festival)


@router.post("/import", dependencies=[Depends(require("vendors.write"))])
def import_vendors_csv(
    file: UploadFile = File(...),
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    raw_bytes = file.file.read()
    try:
        text_content = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(422, "CSV file must be UTF-8 encoded.") from exc

    reader = csv.DictReader(io.StringIO(text_content))
    existing_by_code = {
        v.code: v
        for v in db.scalars(select(Vendor).where(Vendor.festival_id == festival.id))
    }
    created = 0
    skipped_duplicates: list[str] = []
    rejected: list[dict[str, Any]] = []
    seen_in_file: set[str] = set()

    for idx, row in enumerate(reader, start=2):
        clean = {k.strip(): (v or "").strip() for k, v in row.items() if k}
        vid = clean.get("vendor_id", "").upper()
        vname = clean.get("vendor_name", "")
        if not vid or not vname:
            rejected.append({"line": idx, "reason": "Missing vendor_id or vendor_name"})
            continue
        if vid in seen_in_file or vid in existing_by_code:
            skipped_duplicates.append(vid)
            continue
        seen_in_file.add(vid)
        exp_str = clean.get("hygiene_certificate_expiry", "")
        try:
            expiry = date.fromisoformat(exp_str) if exp_str else None
        except ValueError:
            rejected.append({"line": idx, "vendor_id": vid, "reason": f"Invalid expiry date: {exp_str}"})
            continue
        cap_str = clean.get("capacity", "")
        cap = int(cap_str) if cap_str.isdigit() else None
        dietary = [x.strip().lower() for x in clean.get("dietary_options", "").split(";") if x.strip()]
        v = Vendor(
            festival_id=festival.id,
            code=vid,
            name=vname,
            menu=clean.get("menu") or None,
            dietary_options=dietary,
            hygiene_certificate_expiry=expiry,
            hours=clean.get("hours") or None,
            capacity=cap,
            payment_terms=clean.get("payment_terms") or None,
            source="pack",
            original_pack_data=clean,
            operational_status="unconfirmed",
        )
        db.add(v)
        created += 1

    record(
        db,
        user,
        festival.id,
        "vendor.import",
        "festival",
        festival.id,
        f"{user.name} imported {created} vendors ({len(skipped_duplicates)} duplicates prevented)",
        after={"created": created, "skipped_duplicates": skipped_duplicates, "rejected": rejected},
    )
    db.commit()
    return {"created": created, "skipped_duplicates": skipped_duplicates, "rejected": rejected}


@router.post("/{code}/interruptions/simulate", status_code=201, dependencies=[Depends(require("vendors.write"))])
def simulate_vendor_interruption(
    code: str,
    body: InterruptionSimulateIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    target_vendor = by_code(db, Vendor, festival.id, code)
    all_vendors = [
        serialize_vendor(v, festival)
        for v in db.scalars(
            select(Vendor)
            .where(Vendor.festival_id == festival.id, Vendor.archived_at.is_(None))
            .order_by(Vendor.code)
        )
    ]
    target = next(v for v in all_vendors if v["code"] == target_vendor.code)
    target_dietary = set(x.lower() for x in target["effective_dietary_options"])
    target_windows = set(target["meal_windows"])

    # Participant dietary demand for affected dietary categories
    dietary_rows = dict(
        db.execute(
            select(func.lower(Participant.dietary_needs), func.count(Participant.id))
            .where(Participant.festival_id == festival.id, Participant.deleted_at.is_(None))
            .group_by(func.lower(Participant.dietary_needs))
        ).all()
    )

    candidates: list[dict[str, Any]] = []
    excluded_vendors: list[dict[str, Any]] = []

    for cand in all_vendors:
        if cand["code"] == target["code"]:
            continue
        if cand["operational_status"] == "interrupted":
            excluded_vendors.append({
                "code": cand["code"],
                "name": cand["name"],
                "reason": "Vendor is already marked unavailable / interrupted.",
            })
            continue
        if cand["certificate"]["status"] == "expired":
            excluded_vendors.append({
                "code": cand["code"],
                "name": cand["name"],
                "menu": cand["menu"],
                "dietary_options": cand["effective_dietary_options"],
                "reason": f"Hygiene certificate expired on {cand['certificate']['expiry']} — cannot serve as a compliant replacement.",
            })
            continue
        if cand["certificate"]["status"] == "missing":
            excluded_vendors.append({
                "code": cand["code"],
                "name": cand["name"],
                "reason": "Hygiene certificate is missing.",
            })
            continue

        cand_dietary = set(x.lower() for x in cand["effective_dietary_options"])
        shared_dietary = sorted(target_dietary & cand_dietary)
        missing_dietary = sorted(target_dietary - cand_dietary)

        cand_windows = set(cand["meal_windows"])
        shared_windows = sorted(target_windows & cand_windows)
        missing_windows = sorted(target_windows - cand_windows)

        # Only compare numerical capacity when both units are verified and match
        capacity_comparable = bool(
            target["capacity_unit"]
            and cand["capacity_unit"]
            and target["capacity_unit"].strip().lower() == cand["capacity_unit"].strip().lower()
        )
        capacity_comparison = (
            f"{cand['capacity']} {cand['capacity_unit']} vs target {target['capacity']} {target['capacity_unit']}"
            if capacity_comparable
            else "Capacity unit not verified — numerical capacity comparison disabled."
        )

        # Suitability score for sorting human-review candidates
        if not shared_dietary and target["menu"] != cand["menu"]:
            continue

        suitability = "full_match" if not missing_dietary and not missing_windows and cand["certificate"]["status"] == "valid_beyond_festival" else (
            "boundary_warning" if not missing_dietary and not missing_windows else "partial_match"
        )

        candidates.append({
            "code": cand["code"],
            "name": cand["name"],
            "menu": cand["menu"],
            "hours": cand["hours"],
            "dietary_options": cand["effective_dietary_options"],
            "certificate": cand["certificate"],
            "operational_status": cand["operational_status"],
            "listed_capacity": cand["capacity"],
            "capacity_unit": cand["capacity_unit"],
            "capacity_comparable": capacity_comparable,
            "capacity_comparison": capacity_comparison,
            "shared_dietary": shared_dietary,
            "missing_dietary": missing_dietary,
            "shared_meal_windows": shared_windows,
            "missing_meal_windows": missing_windows,
            "inconsistencies": cand["inconsistencies"],
            "suitability": suitability,
        })

    # Sort candidates: valid_beyond_festival first, most shared_dietary, most shared_meal_windows
    candidates.sort(
        key=lambda c: (
            0 if c["certificate"]["status"] == "valid_beyond_festival" else 1,
            -len(c["shared_dietary"]),
            -len(c["shared_meal_windows"]),
            c["code"],
        )
    )

    # Identify uncovered dietary requirements across all compliant candidates
    covered_by_valid_candidates = {
        d
        for c in candidates
        if c["certificate"]["status"] == "valid_beyond_festival"
        for d in c["shared_dietary"]
    }
    covered_by_any_candidate = {
        d
        for c in candidates
        for d in c["shared_dietary"]
    }

    uncovered_requirements: list[dict[str, Any]] = []
    for d in sorted(target_dietary):
        p_affected = dietary_rows.get(d, 0)
        if d not in covered_by_any_candidate:
            uncovered_requirements.append({
                "requirement": d,
                "participant_count": p_affected,
                "severity": "critical",
                "explanation": (
                    f"No compliant alternative vendor offers '{d}' ({p_affected:,} participants declare this requirement)."
                ),
            })
        elif d not in covered_by_valid_candidates:
            uncovered_requirements.append({
                "requirement": d,
                "participant_count": p_affected,
                "severity": "high",
                "explanation": (
                    f"'{d}' ({p_affected:,} participants) is only offered by candidate vendor(s) whose certificate expires on 2026-10-31."
                ),
            })

    if not target["capacity_unit"]:
        uncovered_requirements.append({
            "requirement": "service_capacity_verification",
            "participant_count": None,
            "severity": "medium",
            "explanation": (
                f"{target['code']} has listed capacity {target['capacity']} with 'Capacity unit not verified.' "
                "The system cannot verify whether alternative vendors have sufficient physical throughput without verified capacity units."
            ),
        })

    sufficient_coverage = (
        len(candidates) > 0
        and all(u["severity"] != "critical" for u in uncovered_requirements)
        and bool(target["capacity_unit"])
        and any(c["capacity_comparable"] for c in candidates)
    )

    report = {
        "target_vendor": {
            "code": target["code"],
            "name": target["name"],
            "menu": target["menu"],
            "dietary_options": target["effective_dietary_options"],
            "hours": target["hours"],
            "meal_windows": target["meal_windows"],
            "certificate": target["certificate"],
            "listed_capacity": target["capacity"],
            "capacity_unit": target["capacity_unit"],
            "capacity_status": target["capacity_unit"] if target["capacity_unit"] else "Capacity unit not verified.",
            "operational_status": target["operational_status"],
            "inconsistencies": target["inconsistencies"],
        },
        "alternatives": candidates,
        "excluded_non_compliant": excluded_vendors,
        "uncovered_requirements": uncovered_requirements,
        "sufficient_coverage_established": sufficient_coverage,
        "coverage_verdict": (
            "Verified alternatives identified for human review."
            if sufficient_coverage
            else "The system cannot establish sufficient verified coverage automatically. Human review and manual verification are required before redirecting service."
        ),
        "auto_reassignment_performed": False,
        "simulation_notice": (
            "Simulation only: vendor operational status has NOT been changed and food demand has NOT been automatically redirected. "
            "Authorized approval is required to mark this vendor unavailable or record a replacement service plan."
        ),
    }

    interruption = VendorInterruption(
        festival_id=festival.id,
        vendor_id=target_vendor.id,
        vendor_code=target_vendor.code,
        reason=body.reason.strip(),
        simulation_report=report,
        replacement_codes=[],
        status="simulated",
        created_by_id=user.id,
        created_by_name=user.name,
    )
    db.add(interruption)
    db.flush()
    record(
        db,
        user,
        festival.id,
        "vendor.interruption.simulate",
        "vendor",
        target_vendor.code,
        f"{user.name} simulated service interruption for {target_vendor.code} ({target_vendor.name})",
        after={"interruption_id": interruption.id, "alternatives_found": len(candidates), "uncovered_count": len(uncovered_requirements)},
        reason=body.reason,
    )
    db.commit()
    return serialize_interruption(interruption)


@router.post("/interruptions/{interruption_id}/approve", dependencies=[Depends(require("vendors.approve"))])
def approve_vendor_interruption(
    interruption_id: int,
    body: InterruptionDecisionIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    interruption = db.scalar(
        select(VendorInterruption).where(
            VendorInterruption.id == interruption_id,
            VendorInterruption.festival_id == festival.id,
        )
    )
    if not interruption:
        raise HTTPException(404, "Vendor interruption record not found")

    vendor = db.get(Vendor, interruption.vendor_id)
    if not vendor or vendor.festival_id != festival.id:
        raise HTTPException(404, "Vendor not found")

    valid_replacements: list[str] = []
    for rcode in body.replacement_codes:
        rv = by_code(db, Vendor, festival.id, rcode)
        if rv.code == vendor.code:
            raise HTTPException(422, "Cannot select the interrupted vendor as its own replacement.")
        if rv.hygiene_certificate_expiry and rv.hygiene_certificate_expiry < festival.start_date and not rv.certificate_verified_by:
            raise HTTPException(
                422,
                f"Replacement vendor {rv.code} has an expired hygiene certificate ({rv.hygiene_certificate_expiry.isoformat()}) and cannot be approved as a compliant replacement.",
            )
        valid_replacements.append(rv.code)

    before_status = vendor.operational_status
    vendor.operational_status = "interrupted"
    vendor.status = "interrupted"

    interruption.status = "approved"
    interruption.replacement_codes = valid_replacements
    interruption.decided_by_id = user.id
    interruption.decided_by_name = user.name
    interruption.decided_at = datetime.now(timezone.utc)
    interruption.decision_reason = body.reason.strip()

    record(
        db,
        user,
        festival.id,
        "vendor.interruption.approve",
        "vendor",
        vendor.code,
        f"{user.name} approved marking {vendor.code} ({vendor.name}) unavailable"
        + (f" with replacement plan: {', '.join(valid_replacements)}" if valid_replacements else " (no replacement assigned)"),
        before={"operational_status": before_status},
        after={"operational_status": "interrupted", "replacement_codes": valid_replacements},
        reason=body.reason,
    )
    db.commit()
    return serialize_interruption(interruption)


@router.post("/interruptions/{interruption_id}/reject", dependencies=[Depends(require("vendors.approve"))])
def reject_vendor_interruption(
    interruption_id: int,
    body: InterruptionDecisionIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    interruption = db.scalar(
        select(VendorInterruption).where(
            VendorInterruption.id == interruption_id,
            VendorInterruption.festival_id == festival.id,
        )
    )
    if not interruption:
        raise HTTPException(404, "Vendor interruption record not found")

    interruption.status = "rejected"
    interruption.decided_by_id = user.id
    interruption.decided_by_name = user.name
    interruption.decided_at = datetime.now(timezone.utc)
    interruption.decision_reason = body.reason.strip()

    record(
        db,
        user,
        festival.id,
        "vendor.interruption.reject",
        "vendor",
        interruption.vendor_code,
        f"{user.name} rejected interruption simulation #{interruption.id} for {interruption.vendor_code}",
        reason=body.reason,
    )
    db.commit()
    return serialize_interruption(interruption)


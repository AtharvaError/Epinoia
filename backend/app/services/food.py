"""Dietary coverage. The pack lists vendor diets and capacity, not meal times or who eats where."""
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Festival, Participant, Vendor

DIET_TAGS = {"Vegetarian": "vegetarian", "Non-vegetarian": "non-vegetarian", "Halal": "halal", "Gluten-free": "gluten-free", "Jain": "jain", "Nut allergy": "nut-free"}
SETTLEMENT = {
    "minimum guarantee": "Check what the guarantee still owes for meals that were not served.",
    "50% advance; balance actuals": "Recover any unused advance, then pay the balance on actuals.",
    "per item sold": "Nothing further is owed for meals that were not served.",
}


def compliance(vendor: Vendor, festival: Festival) -> str:
    expiry = vendor.hygiene_certificate_expiry
    if expiry is None:
        return "missing"
    if expiry < festival.start_date:
        return "expired"
    return "expires_during_festival" if expiry <= festival.end_date else "valid"


def can_serve(vendor: Vendor, festival: Festival) -> bool:
    return vendor.status == "active" and compliance(vendor, festival) in ("valid", "expires_during_festival")


def diet_counts(db: Session, festival_id: int) -> dict[str | None, int]:
    return dict(db.execute(select(Participant.dietary_needs, func.count()).where(Participant.festival_id == festival_id, Participant.deleted_at.is_(None),
                                                                                 Participant.registration_status != "cancelled").group_by(Participant.dietary_needs)).all())


def coverage(vendors: list[Vendor], counts: dict[str | None, int], festival: Festival) -> list[dict[str, Any]]:
    """For each declared diet: the compliant vendors that list it, and the seats they offer at one sitting."""
    rows = []
    for diet, tag in DIET_TAGS.items():
        serving = [v for v in vendors if tag in v.dietary_options and can_serve(v, festival)]
        people, seats = counts.get(diet, 0), sum(v.capacity or 0 for v in serving)
        rows.append({"diet": diet, "people": people, "vendors": [v.name for v in serving], "seats": seats, "gap": max(0, people - seats)})
    return rows

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import festival_access, require
from app.database import get_db
from app.models import Festival, Vendor
from app.services.food import SETTLEMENT, can_serve, compliance, coverage, diet_counts

router = APIRouter(prefix="/api/festivals/{festival_id}/vendors", tags=["vendors"])


@router.get("", dependencies=[Depends(require("dashboard.read"))])
def list_vendors(festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    vendors = list(db.scalars(select(Vendor).where(Vendor.festival_id == festival.id).order_by(Vendor.code)))
    counts = diet_counts(db, festival.id)
    return {
        "vendors": [{"code": v.code, "name": v.name, "menu": v.menu, "dietary_options": v.dietary_options, "hours": v.hours, "capacity": v.capacity, "status": v.status,
                     "hygiene_certificate_expiry": v.hygiene_certificate_expiry, "compliance": compliance(v, festival), "can_serve": can_serve(v, festival),
                     "payment_terms": v.payment_terms, "settlement": SETTLEMENT.get(v.payment_terms or "")} for v in vendors],
        "coverage": coverage(vendors, counts, festival),
        "unknown_diet": counts.get(None, 0),
        "people": sum(counts.values()),
        "seats": sum(v.capacity or 0 for v in vendors if can_serve(v, festival)),
    }

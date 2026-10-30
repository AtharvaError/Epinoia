from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.analytics.reports import REPORTS, Filters, overview, to_csv, to_pdf
from app.auth.deps import current_user, festival_access, permissions_of, require
from app.database import get_db
from app.models import Festival, User
from app.services.audit import record

router = APIRouter(prefix="/api/festivals/{festival_id}", tags=["analytics"])


def filters(day: date | None = None, event: str | None = None, department: str | None = None, venue: str | None = None, status: str | None = None) -> Filters:
    return Filters(day, event, department, venue, status)


@router.get("/analytics", dependencies=[Depends(require("analytics.read"))])
def analytics(x: Filters = Depends(filters), festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    permissions = permissions_of(user)
    return {"sections": overview(db, festival.id, x, permissions),
            "reports": [{"key": k, "title": r.title} for k, r in REPORTS.items() if r.permission in permissions]}


@router.get("/reports/{name}.{kind}", dependencies=[Depends(require("analytics.read"))])
def download(name: str, kind: str, x: Filters = Depends(filters), festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    report = REPORTS.get(name)
    if not report or kind not in ("csv", "pdf"):
        raise HTTPException(404, "Report not found")
    if report.permission not in permissions_of(user):
        raise HTTPException(403, f"Your role does not include {report.permission}")
    data = report.load(db, festival.id, x)
    record(db, user, festival.id, "report.export", "report", name, f"{user.name} exported {report.title} as {kind.upper()}")
    db.commit()
    if kind == "csv":
        body, media = to_csv(report, data).encode(), "text/csv"
    else:
        body, media = to_pdf(report, festival, data, x), "application/pdf"
    return Response(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}.{kind}"'})

"""Incident records and workflow. The system tracks and notifies; people make every emergency decision."""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.common import by_code, paged
from app.auth.deps import current_user, festival_access, require
from app.database import get_db
from app.models import AuditLog, Festival, Incident, User
from app.notifications import notify_roles
from app.services.audit import record

router = APIRouter(prefix="/api/festivals/{festival_id}/incidents", tags=["incidents"])

NEXT_STATUS = {"reported": {"acknowledged"}, "acknowledged": {"assigned"}, "assigned": {"in_progress"}, "in_progress": {"resolved"}, "resolved": {"closed", "in_progress"}, "closed": set()}
PRIORITIES = ("low", "medium", "high", "critical")
RESPONDERS = ("safety_coordinator", "festival_director")


class IncidentIn(BaseModel):
    category: str = Field(min_length=2, max_length=60)
    location: str = Field(min_length=2, max_length=120)
    description: str = Field(min_length=5, max_length=2000)
    priority: str = Field(default="medium", pattern="^(low|medium|high|critical)$")


class StatusIn(BaseModel):
    status: str
    assigned_team: str | None = Field(default=None, max_length=120)
    resolution_notes: str | None = Field(default=None, max_length=2000)


class EscalateIn(BaseModel):
    reason: str = Field(min_length=5, max_length=500)


def row(i: Incident) -> dict[str, Any]:
    return {"code": i.code, "category": i.category, "location": i.location, "description": i.description, "priority": i.priority, "reported_by": i.reported_by, "assigned_team": i.assigned_team,
            "reported_at": i.reported_at, "status": i.status, "resolution_notes": i.resolution_notes, "is_simulated": i.is_simulated, "next_statuses": sorted(NEXT_STATUS[i.status])}


@router.get("", dependencies=[Depends(require("incidents.read"))])
def list_incidents(page: int = Query(1, ge=1), status: str | None = None, priority: str | None = None, open_only: bool = False, festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    query = select(Incident).where(Incident.festival_id == festival.id)
    if status:
        query = query.where(Incident.status == status)
    if priority:
        query = query.where(Incident.priority == priority)
    if open_only:
        query = query.where(Incident.status.not_in(("resolved", "closed")))
    return paged(db, query.order_by(func.array_position(list(reversed(PRIORITIES)), Incident.priority), Incident.reported_at.desc()), page, row)


@router.get("/summary", dependencies=[Depends(require("incidents.read"))])
def summary(festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    rows = db.execute(select(Incident.status, Incident.priority, func.count()).where(Incident.festival_id == festival.id).group_by(Incident.status, Incident.priority)).all()
    return {"open": sum(n for s, _, n in rows if s not in ("resolved", "closed")), "total": sum(n for *_, n in rows),
            "urgent": sum(n for s, p, n in rows if s not in ("resolved", "closed") and p in ("high", "critical")),
            "unassigned": sum(n for s, _, n in rows if s in ("reported", "acknowledged"))}


@router.post("", status_code=201, dependencies=[Depends(require("incidents.report"))])
def report_incident(body: IncidentIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    count = db.scalar(select(func.count()).select_from(Incident).where(Incident.festival_id == festival.id)) or 0
    incident = Incident(festival_id=festival.id, code=f"INC{count + 1:03d}", reported_by=user.name, **body.model_dump())
    db.add(incident)
    db.flush()
    record(db, user, festival.id, "incident.report", "incident", incident.code, f"{user.name} reported a {incident.priority} {incident.category} incident at {incident.location}")
    if body.priority in ("high", "critical"):
        notify_roles(db, festival.id, RESPONDERS, f"{body.priority.title()} incident: {body.category}", f"{body.location}: {body.description[:160]}", f"/safety?open={incident.code}", exclude_user=user.id)
    db.commit()
    return row(incident)


@router.get("/{code}", dependencies=[Depends(require("incidents.read"))])
def get_incident(code: str, festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    incident = by_code(db, Incident, festival.id, code)
    result = row(incident)
    result["history"] = [{"at": a.created_at, "by": a.actor_label, "summary": a.summary, "reason": a.reason} for a in db.scalars(
        select(AuditLog).where(AuditLog.festival_id == festival.id, AuditLog.entity == "incident", AuditLog.entity_id == code).order_by(AuditLog.id))]
    return result


@router.post("/{code}/status", dependencies=[Depends(require("incidents.write"))])
def change_status(code: str, body: StatusIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    incident = by_code(db, Incident, festival.id, code)
    if body.status not in NEXT_STATUS[incident.status]:
        raise HTTPException(409, f"An incident that is {incident.status} cannot move to {body.status}")
    if body.status == "assigned" and not (body.assigned_team or incident.assigned_team):
        raise HTTPException(422, "Name the response team")
    if body.status == "resolved" and len((body.resolution_notes or "").strip()) < 5:
        raise HTTPException(422, "Add resolution notes")
    before = incident.status
    incident.status = body.status
    incident.assigned_team = body.assigned_team or incident.assigned_team
    incident.resolution_notes = body.resolution_notes or incident.resolution_notes
    record(db, user, festival.id, "incident.status", "incident", code, f"{user.name} moved {code} from {before} to {body.status}", reason=body.resolution_notes)
    db.commit()
    return row(incident)


@router.post("/{code}/escalate", dependencies=[Depends(require("incidents.write"))])
def escalate(code: str, body: EscalateIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    incident = by_code(db, Incident, festival.id, code)
    if incident.status in ("resolved", "closed"):
        raise HTTPException(409, "This incident is already resolved")
    index = PRIORITIES.index(incident.priority)
    if index == len(PRIORITIES) - 1:
        raise HTTPException(409, "This incident is already critical")
    incident.priority = PRIORITIES[index + 1]
    record(db, user, festival.id, "incident.escalate", "incident", code, f"{user.name} escalated {code} to {incident.priority}", reason=body.reason)
    notify_roles(db, festival.id, RESPONDERS, f"Escalated to {incident.priority}: {incident.category}", f"{incident.location}: {body.reason}", f"/safety?open={code}", exclude_user=user.id)
    db.commit()
    return row(incident)

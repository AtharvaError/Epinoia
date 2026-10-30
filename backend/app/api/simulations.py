from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.common import like, paged
from app.auth.deps import current_user, festival_access, permissions_of, require
from app.database import get_db
from app.models import (
    AuditLog, Budget, ChangeApproval, Department, Event, Festival, Hostel, Registration, Room, Route, Simulation, TransportAssignment, User, Vehicle, Venue, Volunteer, VolunteerShift,
)
from app.services.housing import ACTIVE, AccommodationAllocation
from app.simulation import engine
from app.simulation.scenarios import SCENARIOS

router = APIRouter(prefix="/api/festivals/{festival_id}", tags=["ripple simulator"])


class PreviewIn(BaseModel):
    scenario: str
    change: dict[str, Any]


class ReasonIn(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)


class RequiredReason(BaseModel):
    reason: str = Field(min_length=5, max_length=1000)


def row(s: Simulation, names: dict[int, str]) -> dict[str, Any]:
    summary = s.report.get("summary", {})
    return {"id": s.id, "scenario": s.scenario, "label": SCENARIOS[s.scenario].label, "title": s.report.get("title"), "headline": s.report.get("headline"),
            "status": s.status, "created_at": s.created_at, "created_by": names.get(s.created_by_id), "critical": summary.get("critical", 0), "modules": summary.get("modules", 0)}


def user_names(db: Session) -> dict[int, str]:
    return dict(db.execute(select(User.id, User.name)).all())


@router.get("/simulations/options", dependencies=[Depends(require("simulation.run"))])
def options(festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    f = festival.id
    occupied = dict(db.execute(select(Room.hostel_id, func.count()).join(AccommodationAllocation, AccommodationAllocation.room_id == Room.id).where(Room.festival_id == f, ACTIVE).group_by(Room.hostel_id)).all())
    capacity = dict(db.execute(select(Room.hostel_id, func.sum(Room.capacity)).where(Room.festival_id == f).group_by(Room.hostel_id)).all())
    registered = dict(db.execute(select(Registration.event_id, func.count()).where(Registration.festival_id == f).group_by(Registration.event_id)).all())
    riders = dict(db.execute(select(TransportAssignment.route_id, func.count()).where(TransportAssignment.festival_id == f).group_by(TransportAssignment.route_id)).all())
    shifts = dict(db.execute(select(VolunteerShift.volunteer_id, func.count()).where(VolunteerShift.festival_id == f, VolunteerShift.status != "cancelled").group_by(VolunteerShift.volunteer_id)).all())
    vehicles = dict(db.execute(select(Vehicle.id, Vehicle.code).where(Vehicle.festival_id == f)).all())
    venues = db.scalars(select(Venue).where(Venue.festival_id == f).order_by(Venue.name)).all()
    venue_names = {v.id: v.name for v in venues}
    return {
        "scenarios": [{"key": k, "label": s.label} for k, s in SCENARIOS.items()],
        "hostels": [{"code": h.code, "name": h.name, "kind": h.kind, "capacity": capacity.get(h.id, 0), "occupied": occupied.get(h.id, 0)}
                    for h in db.scalars(select(Hostel).where(Hostel.festival_id == f, Hostel.status == "available").order_by(Hostel.kind.desc(), Hostel.code))],
        "events": [{"code": e.code, "name": e.name, "start_at": e.start_at, "end_at": e.end_at, "venue": venue_names.get(e.venue_id), "registered": registered.get(e.id, 0)}
                   for e in db.scalars(select(Event).where(Event.festival_id == f, Event.deleted_at.is_(None)).order_by(Event.name))],
        "venues": [{"code": v.code, "name": v.name, "capacity": v.capacity} for v in venues],
        "routes": [{"code": r.code, "name": r.name, "expected_arrival_at": r.expected_arrival_at, "passengers": riders.get(r.id, 0), "vehicle": vehicles.get(r.vehicle_id), "status": r.status}
                   for r in db.scalars(select(Route).where(Route.festival_id == f).order_by(Route.departure_at))],
        "volunteers": [{"code": v.code, "name": v.name, "shifts": shifts.get(v.id, 0)} for v in db.scalars(select(Volunteer).where(Volunteer.festival_id == f, Volunteer.status == "active").order_by(Volunteer.code))],
        "budgets": [{"department": d.name, "approved": b.approved_amount} for b, d in db.execute(select(Budget, Department).join(Department, Department.id == Budget.department_id).where(Budget.festival_id == f).order_by(Department.name))],
    }


def detail(db: Session, festival: Festival, simulation: Simulation, user: User, just_computed: bool = False) -> dict[str, Any]:
    result = row(simulation, user_names(db))
    report = {k: v for k, v in simulation.report.items() if k != "plan"}
    still_open = simulation.status in engine.OPEN_STATUSES
    result.update(report=report, proposed_change=simulation.proposed_change,
                  current=(just_computed or engine.is_current(db, festival, simulation)) if still_open else None,
                  can_approve="simulation.approve" in permissions_of(user))
    approval = db.scalar(select(ChangeApproval).where(ChangeApproval.simulation_id == simulation.id).order_by(ChangeApproval.id.desc()).limit(1))
    result["approval"] = approval and {"status": approval.status, "reason": approval.reason, "decided_at": approval.decided_at, "decided_by": user_names(db).get(approval.decided_by_id)}
    return result


@router.post("/simulations", status_code=201, dependencies=[Depends(require("simulation.run"))])
def run_simulation(body: PreviewIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    return detail(db, festival, engine.preview(db, festival, user, body.scenario, body.change), user, just_computed=True)


@router.get("/simulations", dependencies=[Depends(require("simulation.run"))])
def history(page: int = Query(1, ge=1), status: str | None = None, festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    query = select(Simulation).where(Simulation.festival_id == festival.id)
    if status:
        query = query.where(Simulation.status == status)
    names = user_names(db)
    return paged(db, query.order_by(Simulation.id.desc()), page, lambda s: row(s, names))


@router.get("/simulations/{simulation_id}", dependencies=[Depends(require("simulation.run"))])
def get_simulation(simulation_id: int, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    simulation = db.get(Simulation, simulation_id)
    if not simulation or simulation.festival_id != festival.id:
        raise HTTPException(404, "Simulation not found")
    return detail(db, festival, simulation, user)


@router.post("/simulations/{simulation_id}/submit", status_code=204, dependencies=[Depends(require("simulation.run"))])
def submit(simulation_id: int, body: ReasonIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    engine.submit(db, festival, user, simulation_id, body.reason)


@router.post("/simulations/{simulation_id}/approve", dependencies=[Depends(require("simulation.approve"))])
def approve(simulation_id: int, body: ReasonIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user)):
    return engine.approve(festival.id, user, simulation_id, body.reason)


@router.post("/simulations/{simulation_id}/reject", status_code=204, dependencies=[Depends(require("simulation.approve"))])
def reject(simulation_id: int, body: RequiredReason, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    engine.reject(db, festival, user, simulation_id, body.reason)


@router.post("/simulations/{simulation_id}/cancel", status_code=204, dependencies=[Depends(require("simulation.run"))])
def cancel(simulation_id: int, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    engine.cancel(db, festival, user, simulation_id)


@router.post("/simulations/{simulation_id}/recalculate", status_code=201, dependencies=[Depends(require("simulation.run"))])
def recalculate(simulation_id: int, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    return detail(db, festival, engine.recalculate(db, festival, user, simulation_id), user, just_computed=True)


@router.get("/audit", dependencies=[Depends(require("audit.read"))])
def audit_log(page: int = Query(1, ge=1), q: str | None = Query(None, max_length=80), entity: str | None = None, festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    query = select(AuditLog).where(AuditLog.festival_id == festival.id)
    if q:
        query = query.where(or_(AuditLog.summary.ilike(like(q), escape="\\"), AuditLog.actor_label.ilike(like(q), escape="\\")))
    if entity:
        query = query.where(AuditLog.entity == entity)
    return paged(db, query.order_by(AuditLog.id.desc()), page, lambda a: {"id": a.id, "created_at": a.created_at, "actor": a.actor_label, "action": a.action, "entity": a.entity,
                                                                          "entity_id": a.entity_id, "summary": a.summary, "reason": a.reason})

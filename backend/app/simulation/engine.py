"""Two-phase workflow. Preview reads and reports. Commit revalidates, then applies in one transaction.

The state hash covers the whole computed report, so any change to the data that would alter
the outcome invalidates the preview, and changes that would not, do not.
"""
import hashlib
import json
from typing import Any

from fastapi import HTTPException
from psycopg.errors import SerializationFailure
from pydantic import BaseModel, ValidationError
from sqlalchemy import func
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import ChangeApproval, Festival, Simulation, SimulationImpact, User
from app.notifications import alert_team, notify_roles, roles_for_modules
from app.services.audit import record
from app.simulation.graph import graph_payload, traverse
from app.simulation.scenarios import SCENARIOS, Outcome

OPEN_STATUSES = ("previewed", "pending_approval")
APPROVER_ROLES = ("festival_director", "super_admin")
MIN_REASON = 5


def parse_change(scenario: str, change: dict[str, Any]) -> BaseModel:
    if scenario not in SCENARIOS:
        raise HTTPException(422, f"Unknown scenario {scenario}")
    try:
        return SCENARIOS[scenario].model.model_validate(change)
    except ValidationError as error:
        first = error.errors()[0]
        field = ".".join(str(p) for p in first["loc"])
        raise HTTPException(422, f"{field + ': ' if field else ''}{first['msg']}") from error


def build_report(scenario: str, outcome: Outcome) -> dict[str, Any]:
    walk = traverse(outcome.seed_module, outcome.seed_records, outcome.seed_impacts, outcome.rules)
    direct = sum(len(records) for module, records in walk.records.items() if walk.depth[module] <= 1)
    indirect = sum(len(records) for module, records in walk.records.items() if walk.depth[module] > 1)
    critical = sum(i.severity == "critical" for i in walk.impacts)
    return {
        "scenario": scenario, "title": outcome.title, "headline": outcome.headline, "seed_module": outcome.seed_module,
        "current_state": outcome.current_state, "before_after": outcome.before_after, "actions": outcome.actions, "assumptions": outcome.assumptions,
        "impacts": [i.as_dict() for i in walk.impacts], "graph": graph_payload(walk),
        "summary": {"direct_records": direct, "indirect_records": indirect, "modules": len(walk.depth), "critical": critical,
                    "conflicts": sum(i.severity in ("critical", "high") for i in walk.impacts),
                    "facts": sum(i.certainty == "fact" for i in walk.impacts), "estimates": sum(i.certainty == "estimate" for i in walk.impacts),
                    "budget_impact": round(outcome.budget_impact, 2), "resources": outcome.resources, "scheduling": outcome.scheduling},
        "needs_reason": outcome.needs_reason, "plan": outcome.plan,
    }


def fingerprint(report: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(report, sort_keys=True, default=str).encode()).hexdigest()


def compute(db: Session, festival: Festival, scenario: str, change: dict[str, Any]) -> tuple[dict[str, Any], str]:
    model = parse_change(scenario, change)
    report = build_report(scenario, SCENARIOS[scenario].simulate(db, festival, model))
    return report, fingerprint(report)


def preview(db: Session, festival: Festival, user: User, scenario: str, change: dict[str, Any]) -> Simulation:
    report, state_hash = compute(db, festival, scenario, change)
    stored = parse_change(scenario, change).model_dump(mode="json")
    simulation = Simulation(festival_id=festival.id, scenario=scenario, proposed_change=stored, state_hash=state_hash, report=report, created_by_id=user.id)
    db.add(simulation)
    db.flush()
    db.add_all(SimulationImpact(festival_id=festival.id, simulation_id=simulation.id, module=i["module"], severity=i["severity"], is_direct=i["is_direct"], certainty=i["certainty"],
                                record_ref=i["record_ref"], explanation=i["explanation"], recommended_action=i["recommended_action"]) for i in report["impacts"])
    record(db, user, festival.id, "simulation.preview", "simulation", simulation.id, f"{user.name} simulated: {report['title']}")
    db.commit()
    return simulation


def load_open(db: Session, festival: Festival, simulation_id: int, lock: bool = False) -> Simulation:
    simulation = db.get(Simulation, simulation_id, with_for_update=lock)
    if not simulation or simulation.festival_id != festival.id:
        raise HTTPException(404, "Simulation not found")
    if simulation.status not in OPEN_STATUSES:
        raise HTTPException(409, f"This simulation is already {simulation.status.replace('_', ' ')}")
    return simulation


def is_current(db: Session, festival: Festival, simulation: Simulation) -> bool:
    try:
        return compute(db, festival, simulation.scenario, simulation.proposed_change)[1] == simulation.state_hash
    except HTTPException:
        return False  # The scenario can no longer run, so the preview cannot stand.


def submit(db: Session, festival: Festival, user: User, simulation_id: int, note: str | None) -> None:
    simulation = load_open(db, festival, simulation_id, lock=True)
    if simulation.status == "pending_approval":
        raise HTTPException(409, "This is already waiting for approval")
    simulation.status = "pending_approval"
    db.add(ChangeApproval(festival_id=festival.id, simulation_id=simulation.id, reason=note))
    title = simulation.report["title"]
    record(db, user, festival.id, "simulation.submit", "simulation", simulation.id, f"{user.name} requested approval: {title}", reason=note)
    notify_roles(db, festival.id, APPROVER_ROLES, "Approval requested", f"{user.name} requested approval: {title}", f"/ripple?simulation={simulation.id}", exclude_user=user.id)
    alert_team(db, festival.id, f"Approval needed in Ripple: {title}. {simulation.report['headline']}")
    db.commit()


def _decide(db: Session, simulation: Simulation, status: str, user_id: int, reason: str | None) -> None:
    approval = db.query(ChangeApproval).filter_by(simulation_id=simulation.id, status="pending").one_or_none()
    if approval is None:
        approval = ChangeApproval(festival_id=simulation.festival_id, simulation_id=simulation.id)
        db.add(approval)
    approval.status, approval.decided_by_id, approval.decided_at, approval.reason = status, user_id, func.now(), reason


def approve(festival_id: int, user: User, simulation_id: int, reason: str | None) -> dict[str, Any]:
    """Serializable, so a change racing this commit makes one of the two fail rather than both land."""
    try:
        with SessionLocal() as tx:
            tx.connection(execution_options={"isolation_level": "SERIALIZABLE"})
            festival = tx.get(Festival, festival_id)
            simulation = load_open(tx, festival, simulation_id, lock=True)
            report, state_hash = compute(tx, festival, simulation.scenario, simulation.proposed_change)
            if state_hash != simulation.state_hash:
                simulation.status = "stale"
                record(tx, user, festival_id, "simulation.stale", "simulation", simulation.id, f"Approval stopped: data changed since the simulation of {report['title']}")
                tx.commit()
                raise HTTPException(409, "The data changed after this simulation ran, so it was not applied. Recalculate and review it again.")
            if report["needs_reason"] and len((reason or "").strip()) < MIN_REASON:
                raise HTTPException(422, f"A reason is required. {report['needs_reason']}")
            result = SCENARIOS[simulation.scenario].apply(tx, festival, report["plan"])
            simulation.status = "applied"
            _decide(tx, simulation, "approved", user.id, reason)
            record(tx, user, festival_id, f"change.{simulation.scenario}", "simulation", simulation.id, f"{user.name} applied: {report['title']}. {result}.",
                   before={"state": report["before_after"]}, after={"summary": report["summary"]}, reason=reason)
            modules = [n["id"] for n in report["graph"]["nodes"] if n["depth"] is not None]
            sent = notify_roles(tx, festival_id, roles_for_modules(modules), report["title"], report["headline"], f"/ripple?simulation={simulation.id}", exclude_user=user.id)
            alert_team(tx, festival_id, f"Change applied: {report['title']}. {report['headline']}")
            tx.commit()
            return {"status": "applied", "result": result, "notified": sent}
    except OperationalError as error:
        if isinstance(error.orig, SerializationFailure):
            raise HTTPException(409, "Another change landed at the same moment. Recalculate and review it again.") from error
        raise


def reject(db: Session, festival: Festival, user: User, simulation_id: int, reason: str) -> None:
    simulation = load_open(db, festival, simulation_id, lock=True)
    simulation.status = "rejected"
    _decide(db, simulation, "rejected", user.id, reason)
    record(db, user, festival.id, "simulation.reject", "simulation", simulation.id, f"{user.name} rejected: {simulation.report['title']}", reason=reason)
    db.commit()


def cancel(db: Session, festival: Festival, user: User, simulation_id: int) -> None:
    simulation = load_open(db, festival, simulation_id, lock=True)
    simulation.status = "cancelled"
    db.query(ChangeApproval).filter_by(simulation_id=simulation.id, status="pending").update({"status": "cancelled"})
    record(db, user, festival.id, "simulation.cancel", "simulation", simulation.id, f"{user.name} cancelled the simulation: {simulation.report['title']}")
    db.commit()


def recalculate(db: Session, festival: Festival, user: User, simulation_id: int) -> Simulation:
    old = db.get(Simulation, simulation_id)
    if not old or old.festival_id != festival.id:
        raise HTTPException(404, "Simulation not found")
    if old.status == "applied":
        raise HTTPException(409, "This change has already been applied")
    if old.status in OPEN_STATUSES:
        old.status = "superseded"
        db.query(ChangeApproval).filter_by(simulation_id=old.id, status="pending").update({"status": "cancelled"})
    return preview(db, festival, user, old.scenario, old.proposed_change)

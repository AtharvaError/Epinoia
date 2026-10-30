"""Budget figures shared by the finance API, reports and the assistant."""
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Budget, Department, Expense
from app.services.housing import external_commitment


def department_lines(db: Session, festival_id: int) -> list[dict[str, Any]]:
    """Approved against committed, per department. External stays count against Hospitality."""
    budgets = {b.department_id: b.approved_amount for b in db.scalars(select(Budget).where(Budget.festival_id == festival_id))}
    expenses = list(db.scalars(select(Expense).where(Expense.festival_id == festival_id, Expense.deleted_at.is_(None))))
    stays = external_commitment(db, festival_id)
    lines = []
    for d in db.scalars(select(Department).where(Department.festival_id == festival_id).order_by(Department.name)):
        mine = [e for e in expenses if e.department_id == d.id]
        approved_spend = [e for e in mine if e.approval_status == "approved"]
        committed = sum(((e.actual_amount or e.estimated_amount) for e in approved_spend), Decimal(0))
        external = stays if d.name == "Hospitality" else Decimal(0)
        approved = budgets.get(d.id, Decimal(0))
        lines.append({
            "department": d.name, "approved": approved, "committed": committed + external, "external_stays": external,
            "actual": sum((e.actual_amount or Decimal(0) for e in approved_spend), Decimal(0)),
            "estimated": sum((e.estimated_amount for e in mine if e.approval_status != "rejected"), Decimal(0)),
            "pending": sum((e.estimated_amount for e in mine if e.approval_status == "pending"), Decimal(0)),
            "remaining": approved - committed - external,
        })
    return lines

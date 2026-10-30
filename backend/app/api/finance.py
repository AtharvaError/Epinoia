from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.common import paged
from app.api.participants import DeleteReason
from app.auth.deps import current_user, festival_access, require
from app.database import get_db
from app.models import Budget, Department, Expense, Festival, Sponsor, User
from app.services.audit import record
from app.services.finance import department_lines

router = APIRouter(prefix="/api/festivals/{festival_id}/finance", tags=["finance"])


class ExpenseIn(BaseModel):
    department: str
    category: str = Field(min_length=2, max_length=60)
    description: str = Field(min_length=2, max_length=200)
    estimated_amount: Decimal = Field(ge=0, le=100_000_000)
    actual_amount: Decimal | None = Field(default=None, ge=0, le=100_000_000)


class DecisionIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class PaymentIn(BaseModel):
    actual_amount: Decimal = Field(ge=0, le=100_000_000)


class BudgetIn(BaseModel):
    approved_amount: Decimal = Field(ge=0, le=1_000_000_000)
    reason: str = Field(min_length=5, max_length=500)


class SponsorIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    tier: str | None = Field(default=None, max_length=30)
    committed_amount: Decimal = Field(ge=0, le=1_000_000_000)
    received_amount: Decimal = Field(default=Decimal(0), ge=0, le=1_000_000_000)


def department(db: Session, festival_id: int, name: str) -> Department:
    found = db.scalar(select(Department).where(Department.festival_id == festival_id, Department.name == name))
    if not found:
        raise HTTPException(404, f"Department {name} not found")
    return found


def own_expense(db: Session, festival_id: int, expense_id: int) -> Expense:
    expense = db.get(Expense, expense_id)
    if not expense or expense.festival_id != festival_id or expense.deleted_at:
        raise HTTPException(404, "Expense not found")
    return expense


def expense_row(e: Expense, names: dict[int, str]) -> dict[str, Any]:
    return {"id": e.id, "department": names.get(e.department_id), "category": e.category, "description": e.description, "estimated_amount": e.estimated_amount,
            "actual_amount": e.actual_amount, "approval_status": e.approval_status, "payment_status": e.payment_status, "is_simulated": e.is_simulated}


def department_names(db: Session, festival_id: int) -> dict[int, str]:
    return dict(db.execute(select(Department.id, Department.name).where(Department.festival_id == festival_id)).all())


@router.get("/summary", dependencies=[Depends(require("finance.read"))])
def summary(festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    lines = department_lines(db, festival.id)
    sponsors = list(db.scalars(select(Sponsor).where(Sponsor.festival_id == festival.id)))
    totals = {key: sum((line[key] for line in lines), Decimal(0)) for key in ("approved", "committed", "actual", "pending", "remaining")}
    return {"lines": lines, "totals": totals, "sponsors_committed": sum((s.committed_amount for s in sponsors), Decimal(0)),
            "sponsors_received": sum((s.received_amount for s in sponsors), Decimal(0))}


@router.put("/budgets/{name}", dependencies=[Depends(require("finance.approve"))])
def set_budget(name: str, body: BudgetIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    d = department(db, festival.id, name)
    budget = db.scalar(select(Budget).where(Budget.festival_id == festival.id, Budget.department_id == d.id))
    before = budget.approved_amount if budget else Decimal(0)
    if budget:
        budget.approved_amount = body.approved_amount
    else:
        db.add(Budget(festival_id=festival.id, department_id=d.id, approved_amount=body.approved_amount))
    record(db, user, festival.id, "budget.update", "budget", name, f"{user.name} set the {name} budget to {body.approved_amount}", before={"approved": str(before)}, after={"approved": str(body.approved_amount)}, reason=body.reason)
    db.commit()
    return {"department": name, "approved_amount": body.approved_amount}


@router.get("/expenses", dependencies=[Depends(require("finance.read"))])
def list_expenses(page: int = Query(1, ge=1), department_name: str | None = Query(None, alias="department"), approval: str | None = None, payment: str | None = None,
                  festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    query = select(Expense).where(Expense.festival_id == festival.id, Expense.deleted_at.is_(None))
    if department_name:
        query = query.where(Expense.department_id == department(db, festival.id, department_name).id)
    if approval:
        query = query.where(Expense.approval_status == approval)
    if payment:
        query = query.where(Expense.payment_status == payment)
    names = department_names(db, festival.id)
    return paged(db, query.order_by(Expense.id.desc()), page, lambda e: expense_row(e, names))


@router.post("/expenses", status_code=201, dependencies=[Depends(require("finance.write"))])
def create_expense(body: ExpenseIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    d = department(db, festival.id, body.department)
    expense = Expense(festival_id=festival.id, department_id=d.id, **body.model_dump(exclude={"department"}))
    db.add(expense)
    db.flush()
    record(db, user, festival.id, "expense.create", "expense", expense.id, f"{user.name} added an expense: {expense.description}")
    db.commit()
    return {"id": expense.id}


@router.post("/expenses/{expense_id}/approve", dependencies=[Depends(require("finance.approve"))])
def approve_expense(expense_id: int, body: DecisionIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    expense = own_expense(db, festival.id, expense_id)
    if expense.approval_status != "pending":
        raise HTTPException(409, f"This expense is already {expense.approval_status}")
    names = department_names(db, festival.id)
    line = next(x for x in department_lines(db, festival.id) if x["department"] == names[expense.department_id])
    over = line["committed"] + expense.estimated_amount - line["approved"]
    if over > 0 and len((body.reason or "").strip()) < 5:
        raise HTTPException(409, f"This puts {line['department']} over budget by {over:,.0f}. Give a reason to approve it anyway.")
    expense.approval_status = "approved"
    record(db, user, festival.id, "expense.approve", "expense", expense_id, f"{user.name} approved: {expense.description}", reason=body.reason)
    db.commit()
    return {"over_budget_by": max(over, Decimal(0))}


@router.post("/expenses/{expense_id}/reject", status_code=204, dependencies=[Depends(require("finance.approve"))])
def reject_expense(expense_id: int, body: DeleteReason, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    expense = own_expense(db, festival.id, expense_id)
    if expense.approval_status != "pending":
        raise HTTPException(409, f"This expense is already {expense.approval_status}")
    expense.approval_status = "rejected"
    record(db, user, festival.id, "expense.reject", "expense", expense_id, f"{user.name} rejected: {expense.description}", reason=body.reason)
    db.commit()


@router.post("/expenses/{expense_id}/pay", dependencies=[Depends(require("finance.write"))])
def pay_expense(expense_id: int, body: PaymentIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    expense = own_expense(db, festival.id, expense_id)
    if expense.approval_status != "approved":
        raise HTTPException(409, "Only approved expenses can be paid")
    if expense.payment_status == "paid":
        raise HTTPException(409, "This expense is already paid")
    expense.actual_amount, expense.payment_status = body.actual_amount, "paid"
    record(db, user, festival.id, "expense.pay", "expense", expense_id, f"{user.name} recorded payment for: {expense.description}")
    db.commit()
    return {"id": expense_id}


@router.delete("/expenses/{expense_id}", status_code=204, dependencies=[Depends(require("finance.write"))])
def delete_expense(expense_id: int, body: DeleteReason, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    expense = own_expense(db, festival.id, expense_id)
    if expense.payment_status == "paid":
        raise HTTPException(409, "A paid expense cannot be deleted")
    expense.deleted_at = func.now()
    record(db, user, festival.id, "expense.delete", "expense", expense_id, f"{user.name} deleted: {expense.description}", reason=body.reason)
    db.commit()


@router.get("/sponsors", dependencies=[Depends(require("finance.read"))])
def list_sponsors(festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    return [{"id": s.id, "name": s.name, "tier": s.tier, "committed_amount": s.committed_amount, "received_amount": s.received_amount, "outstanding": s.committed_amount - s.received_amount, "is_simulated": s.is_simulated}
            for s in db.scalars(select(Sponsor).where(Sponsor.festival_id == festival.id).order_by(Sponsor.committed_amount.desc()))]


@router.post("/sponsors", status_code=201, dependencies=[Depends(require("finance.write"))])
def create_sponsor(body: SponsorIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    sponsor = Sponsor(festival_id=festival.id, **body.model_dump())
    db.add(sponsor)
    record(db, user, festival.id, "sponsor.create", "sponsor", body.name, f"{user.name} added sponsor {body.name}")
    db.commit()
    return {"name": body.name}


@router.patch("/sponsors/{sponsor_id}", dependencies=[Depends(require("finance.write"))])
def update_sponsor(sponsor_id: int, body: SponsorIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    sponsor = db.get(Sponsor, sponsor_id)
    if not sponsor or sponsor.festival_id != festival.id:
        raise HTTPException(404, "Sponsor not found")
    for field, value in body.model_dump().items():
        setattr(sponsor, field, value)
    record(db, user, festival.id, "sponsor.update", "sponsor", sponsor_id, f"{user.name} updated sponsor {sponsor.name}")
    db.commit()
    return {"id": sponsor_id}

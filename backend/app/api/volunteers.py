from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.common import by_code, like, ordered, paged
from app.auth.deps import current_user, festival_access, require
from app.database import get_db
from app.models import Department, Event, Festival, Task, User, Volunteer, VolunteerShift
from app.services.audit import record

router = APIRouter(prefix="/api/festivals/{festival_id}", tags=["volunteers"])
SHIFT_PADDING = timedelta(minutes=30)
EVENT_SKILLS = {  # Which skills suit an event, from its pack requirements.
    "stage; lights": {"stage", "AV"}, "audio; seating": {"AV", "crowd control"}, "projector; seating": {"AV"},
    "open ground": {"crowd control", "logistics"}, "power; tables": {"logistics"}, "quiet room": {"registration"},
}


class VolunteerIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    department: str | None = None
    phone: str | None = Field(default=None, max_length=30)
    skills: list[str] = Field(default_factory=list, max_length=20)
    status: str = Field(default="active", pattern="^(active|unavailable|inactive)$")


class ShiftIn(BaseModel):
    event_code: str | None = None
    start_at: datetime
    end_at: datetime
    task: str | None = Field(default=None, max_length=160)


class ShiftUpdate(BaseModel):
    attendance: str | None = Field(default=None, pattern="^(unknown|present|absent)$")
    status: str | None = Field(default=None, pattern="^(assigned|in_progress|done|cancelled)$")


class TaskIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    department: str
    event_code: str | None = None
    volunteer_code: str | None = None
    status: str = Field(default="todo", pattern="^(todo|in_progress|done)$")
    due_at: datetime | None = None


def department_id(db: Session, festival_id: int, name: str | None) -> int | None:
    if not name:
        return None
    found = db.scalar(select(Department.id).where(Department.festival_id == festival_id, Department.name == name))
    if not found:
        raise HTTPException(404, f"Department {name} not found")
    return found


def departments(db: Session, festival_id: int) -> dict[int, str]:
    return dict(db.execute(select(Department.id, Department.name).where(Department.festival_id == festival_id)).all())


def shift_counts(db: Session, festival_id: int) -> dict[int, int]:
    return dict(db.execute(select(VolunteerShift.volunteer_id, func.count()).where(VolunteerShift.festival_id == festival_id, VolunteerShift.status != "cancelled").group_by(VolunteerShift.volunteer_id)).all())


def row(v: Volunteer, names: dict[int, str], shifts: dict[int, int]) -> dict[str, Any]:
    return {"code": v.code, "name": v.name, "department": names.get(v.department_id), "skills": v.skills, "status": v.status,
            "shifts": shifts.get(v.id, 0), "is_simulated": v.is_simulated}


@router.get("/volunteers", dependencies=[Depends(require("volunteers.read"))])
def list_volunteers(page: int = Query(1, ge=1), q: str | None = Query(None, max_length=80), department: str | None = None, status: str | None = None, sort: str | None = None,
                    festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    query = select(Volunteer).where(Volunteer.festival_id == festival.id)
    if q:
        query = query.where(or_(Volunteer.name.ilike(like(q), escape="\\"), Volunteer.code.ilike(like(q), escape="\\")))
    if department:
        query = query.join(Department, Department.id == Volunteer.department_id).where(Department.name == department)
    if status:
        query = query.where(Volunteer.status == status)
    names, shifts = departments(db, festival.id), shift_counts(db, festival.id)
    columns = {"code": Volunteer.code, "name": Volunteer.name, "status": Volunteer.status,
               "department": select(Department.name).where(Department.id == Volunteer.department_id).correlate(Volunteer).scalar_subquery(),
               "shifts": select(func.count()).where(VolunteerShift.volunteer_id == Volunteer.id, VolunteerShift.status != "cancelled").scalar_subquery()}
    return paged(db, ordered(query, sort, columns, Volunteer.code), page, lambda v: row(v, names, shifts))


def event_staffing(db: Session, festival_id: int) -> list[dict[str, Any]]:
    staffed = dict(db.execute(select(VolunteerShift.event_id, func.count()).where(VolunteerShift.festival_id == festival_id, VolunteerShift.status != "cancelled").group_by(VolunteerShift.event_id)).all())
    events = db.scalars(select(Event).where(Event.festival_id == festival_id, Event.deleted_at.is_(None), Event.status != "cancelled").order_by(Event.start_at))
    return [{"code": e.code, "name": e.name, "start_at": e.start_at, "needed": e.volunteers_needed, "staffed": staffed.get(e.id, 0),
             "short": e.volunteers_needed - staffed.get(e.id, 0)} for e in events]


@router.get("/volunteers/understaffed", dependencies=[Depends(require("volunteers.read"))])
def understaffed(festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    return sorted((e for e in event_staffing(db, festival.id) if e["short"] > 0), key=lambda e: -e["short"])


def has_overlap(db: Session, volunteer_id: int, start: datetime, end: datetime) -> VolunteerShift | None:
    return db.scalar(select(VolunteerShift).where(VolunteerShift.volunteer_id == volunteer_id, VolunteerShift.status != "cancelled",
                                                  VolunteerShift.start_at < end, VolunteerShift.end_at > start).limit(1))


@router.get("/volunteers/replacements", dependencies=[Depends(require("volunteers.read"))])
def replacements(event: str, festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    e = by_code(db, Event, festival.id, event)
    if not e.start_at:
        raise HTTPException(409, f"{e.name} has no scheduled time")
    start, end = e.start_at - SHIFT_PADDING, e.end_at + SHIFT_PADDING
    busy = select(VolunteerShift.volunteer_id).where(VolunteerShift.status != "cancelled", VolunteerShift.start_at < end, VolunteerShift.end_at > start)
    free = db.scalars(select(Volunteer).where(Volunteer.festival_id == festival.id, Volunteer.status == "active", Volunteer.id.not_in(busy)))
    wanted = EVENT_SKILLS.get(e.venue_requirements or "", set())
    names, shifts = departments(db, festival.id), shift_counts(db, festival.id)
    ranked = sorted(free, key=lambda v: (-len(wanted & set(v.skills)), shifts.get(v.id, 0), v.code))
    return [{**row(v, names, shifts), "matching_skills": sorted(wanted & set(v.skills))} for v in ranked[:10]]


def next_code(db: Session, festival_id: int) -> str:
    codes = db.scalars(select(Volunteer.code).where(Volunteer.festival_id == festival_id, Volunteer.code.like("VOL%")))
    return f"VOL{max((int(c[3:]) for c in codes if c[3:].isdigit()), default=0) + 1:03d}"


@router.post("/volunteers", status_code=201, dependencies=[Depends(require("volunteers.write"))])
def create_volunteer(body: VolunteerIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    v = Volunteer(festival_id=festival.id, code=next_code(db, festival.id), department_id=department_id(db, festival.id, body.department), **body.model_dump(exclude={"department"}))
    db.add(v)
    record(db, user, festival.id, "volunteer.create", "volunteer", v.code, f"{user.name} added volunteer {v.name}")
    db.commit()
    return {"code": v.code}


@router.get("/volunteers/{code}", dependencies=[Depends(require("volunteers.read"))])
def get_volunteer(code: str, festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    v = by_code(db, Volunteer, festival.id, code)
    result = row(v, departments(db, festival.id), shift_counts(db, festival.id))
    result["shift_list"] = [{"id": s.id, "event": e.name if e else None, "start_at": s.start_at, "end_at": s.end_at, "task": s.task, "attendance": s.attendance, "status": s.status}
                            for s, e in db.execute(select(VolunteerShift, Event).outerjoin(Event, Event.id == VolunteerShift.event_id)
                                                   .where(VolunteerShift.volunteer_id == v.id).order_by(VolunteerShift.start_at))]
    return result


@router.patch("/volunteers/{code}", dependencies=[Depends(require("volunteers.write"))])
def update_volunteer(code: str, body: VolunteerIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    v = by_code(db, Volunteer, festival.id, code)
    v.name, v.phone, v.skills, v.status = body.name, body.phone, body.skills, body.status
    v.department_id = department_id(db, festival.id, body.department)
    record(db, user, festival.id, "volunteer.update", "volunteer", code, f"{user.name} updated volunteer {v.name}")
    db.commit()
    return {"code": code}


@router.post("/volunteers/{code}/shifts", status_code=201, dependencies=[Depends(require("volunteers.write"))])
def add_shift(code: str, body: ShiftIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    v = db.scalar(select(Volunteer).where(Volunteer.festival_id == festival.id, Volunteer.code == code).with_for_update())
    if not v:
        raise HTTPException(404, f"Volunteer {code} not found")
    if v.status != "active":
        raise HTTPException(409, f"{v.name} is {v.status}")
    if body.end_at <= body.start_at:
        raise HTTPException(422, "Shift must end after it starts")
    clash = has_overlap(db, v.id, body.start_at, body.end_at)
    if clash:
        raise HTTPException(409, f"{v.name} already has a shift then: {clash.task or 'unnamed shift'}")
    event = by_code(db, Event, festival.id, body.event_code) if body.event_code else None
    db.add(VolunteerShift(festival_id=festival.id, volunteer_id=v.id, event_id=event.id if event else None, start_at=body.start_at, end_at=body.end_at, task=body.task))
    record(db, user, festival.id, "volunteer.assign", "volunteer", code, f"{user.name} assigned {v.name}" + (f" to {event.name}" if event else " to a shift"))
    db.commit()
    return {"code": code}


def own_shift(db: Session, festival_id: int, shift_id: int) -> VolunteerShift:
    shift = db.get(VolunteerShift, shift_id)
    if not shift or shift.festival_id != festival_id:
        raise HTTPException(404, "Shift not found")
    return shift


@router.patch("/shifts/{shift_id}", dependencies=[Depends(require("volunteers.write"))])
def update_shift(shift_id: int, body: ShiftUpdate, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    shift = own_shift(db, festival.id, shift_id)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(shift, field, value)
    record(db, user, festival.id, "shift.update", "shift", shift_id, f"{user.name} updated a volunteer shift")
    db.commit()


@router.get("/tasks", dependencies=[Depends(require("volunteers.read"))])
def list_tasks(page: int = Query(1, ge=1), department: str | None = None, status: str | None = None, festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    query = select(Task).where(Task.festival_id == festival.id)
    if department:
        query = query.join(Department, Department.id == Task.department_id).where(Department.name == department)
    if status:
        query = query.where(Task.status == status)
    names = departments(db, festival.id)
    return paged(db, query.order_by(Task.status, Task.id), page, lambda t: {"id": t.id, "title": t.title, "department": names.get(t.department_id), "status": t.status, "due_at": t.due_at, "is_simulated": t.is_simulated})


@router.post("/tasks", status_code=201, dependencies=[Depends(require("volunteers.write"))])
def create_task(body: TaskIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    try:
        task = Task(festival_id=festival.id, title=body.title, department_id=department_id(db, festival.id, body.department), status=body.status, due_at=body.due_at,
                    event_id=by_code(db, Event, festival.id, body.event_code).id if body.event_code else None,
                    assignee_volunteer_id=by_code(db, Volunteer, festival.id, body.volunteer_code).id if body.volunteer_code else None)
        db.add(task)
        db.flush()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(409, "That task could not be saved") from error
    record(db, user, festival.id, "task.create", "task", task.id, f"{user.name} added task: {task.title}")
    db.commit()
    return {"id": task.id}


@router.patch("/tasks/{task_id}", dependencies=[Depends(require("volunteers.write"))])
def update_task(task_id: int, status: str = Query(pattern="^(todo|in_progress|done)$"), festival: Festival = Depends(festival_access),
                user: User = Depends(current_user), db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if not task or task.festival_id != festival.id:
        raise HTTPException(404, "Task not found")
    task.status = status
    record(db, user, festival.id, "task.update", "task", task_id, f"{user.name} moved a task to {status}")
    db.commit()

import csv
import io
import secrets
from datetime import datetime
from typing import Any

import openpyxl
import segno
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.common import by_code, csv_safe, like, paged
from app.auth.deps import current_user, festival_access, permissions_of, require
from app.database import get_db
from app.models import (
    AccommodationAllocation, Contingent, DataIssue, Event, Festival, Hostel, Participant, Registration, Room, Route,
    TransportAssignment, User,
)
from app.services.audit import record
from app.services.events import participant_clashes

router = APIRouter(prefix="/api/festivals/{festival_id}", tags=["participants"])

SENSITIVE = ("phone", "email", "gender_as_given", "accessibility_needs", "dietary_needs")
IMPORT_LIMIT_BYTES = 5_000_000


class ParticipantIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    college: str = Field(min_length=2, max_length=160)
    contingent_code: str | None = None
    email: str | None = Field(default=None, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=160)
    phone: str | None = Field(default=None, max_length=30)
    department: str | None = Field(default=None, max_length=120)
    gender_as_given: str | None = Field(default=None, max_length=40)
    age: int | None = Field(default=None, ge=10, le=100)
    accessibility_needs: str | None = Field(default=None, max_length=120)
    dietary_needs: str | None = Field(default=None, max_length=80)
    arrival_at: datetime | None = None
    departure_at: datetime | None = None
    accommodation_required: bool | None = None
    transport_required: bool | None = None
    tshirt_size: str | None = Field(default=None, max_length=10)
    registration_status: str = Field(default="registered", pattern="^(registered|confirmed|cancelled)$")


class RegistrationIn(BaseModel):
    event_code: str


class PassCheck(BaseModel):
    token: str = Field(min_length=8, max_length=64)


class DeleteReason(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


def serialize(p: Participant, sensitive: bool, event_count: int | None = None) -> dict[str, Any]:
    row = {
        "code": p.code, "full_name": p.full_name, "college": p.college, "department": p.department, "age": p.age,
        "contingent_id": p.contingent_id, "arrival_at": p.arrival_at, "departure_at": p.departure_at,
        "accommodation_required": p.accommodation_required, "transport_required": p.transport_required,
        "tshirt_size": p.tshirt_size, "registration_status": p.registration_status, "checkin_status": p.checkin_status,
        "has_pass": p.qr_token is not None, "restricted": not sensitive,
    }
    for field in SENSITIVE:
        row[field] = getattr(p, field) if sensitive else None
    if event_count is not None:
        row["event_count"] = event_count
    return row


def filtered(festival_id: int, q: str | None, college: str | None, contingent: str | None, accommodation: str | None,
             checkin: str | None, status: str | None, event: str | None, flagged: bool):
    query = select(Participant).where(Participant.festival_id == festival_id, Participant.deleted_at.is_(None))
    if q:
        term = like(q)
        query = query.where(or_(Participant.code.ilike(term, escape="\\"), Participant.full_name.ilike(term, escape="\\"),
                                Participant.college.ilike(term, escape="\\")))
    if college:
        query = query.where(Participant.college == college)
    if contingent:
        query = query.join(Contingent, Contingent.id == Participant.contingent_id).where(Contingent.code == contingent)
    if accommodation in ("yes", "no"):
        query = query.where(Participant.accommodation_required.is_(accommodation == "yes"))
    if accommodation == "unknown":
        query = query.where(Participant.accommodation_required.is_(None))
    if checkin:
        query = query.where(Participant.checkin_status == checkin)
    if status:
        query = query.where(Participant.registration_status == status)
    if event:
        query = query.where(Participant.id.in_(
            select(Registration.participant_id).join(Event, Event.id == Registration.event_id)
            .where(Event.festival_id == festival_id, Event.code == event)))
    if flagged:
        query = query.where(Participant.code.in_(
            select(DataIssue.entity_code).where(DataIssue.festival_id == festival_id, DataIssue.entity_type == "participant",
                                                DataIssue.status == "open")))
    return query.order_by(Participant.code)


@router.get("/participants", dependencies=[Depends(require("participants.read"))])
def list_participants(page: int = Query(1, ge=1), q: str | None = Query(None, max_length=80), college: str | None = None,
                      contingent: str | None = None, accommodation: str | None = None, checkin: str | None = None,
                      status: str | None = None, event: str | None = None, flagged: bool = False,
                      festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    sensitive = "participants.read_sensitive" in permissions_of(user)
    query = filtered(festival.id, q, college, contingent, accommodation, checkin, status, event, flagged)
    result = paged(db, query, page, lambda p: serialize(p, sensitive))
    codes = [row["code"] for row in result["items"]]
    counts = dict(db.execute(select(Participant.code, func.count(Registration.id))
                             .join(Registration, Registration.participant_id == Participant.id)
                             .where(Participant.festival_id == festival.id, Participant.code.in_(codes))
                             .group_by(Participant.code)).all())
    for row in result["items"]:
        row["event_count"] = counts.get(row["code"], 0)
    return result


@router.get("/participants/filters", dependencies=[Depends(require("participants.read"))])
def filter_options(festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    colleges = db.scalars(select(Contingent).where(Contingent.festival_id == festival.id).order_by(Contingent.college))
    return {"contingents": [{"code": c.code, "college": c.college} for c in colleges],
            "events": [{"code": e.code, "name": e.name} for e in db.scalars(
                select(Event).where(Event.festival_id == festival.id, Event.deleted_at.is_(None)).order_by(Event.name))]}


EXPORT_COLUMNS = ["code", "full_name", "college", "department", "age", "registration_status", "checkin_status",
                  "accommodation_required", "transport_required", "arrival_at", "departure_at", "tshirt_size"]


@router.get("/participants/export.csv", dependencies=[Depends(require("participants.read"))])
def export_participants(q: str | None = None, college: str | None = None, contingent: str | None = None,
                        accommodation: str | None = None, checkin: str | None = None, status: str | None = None,
                        event: str | None = None, flagged: bool = False, festival: Festival = Depends(festival_access),
                        user: User = Depends(current_user), db: Session = Depends(get_db)):
    sensitive = "participants.read_sensitive" in permissions_of(user)
    columns = EXPORT_COLUMNS + (list(SENSITIVE) if sensitive else [])
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(columns)
    rows = db.scalars(filtered(festival.id, q, college, contingent, accommodation, checkin, status, event, flagged))
    count = 0
    for p in rows:
        writer.writerow([csv_safe(getattr(p, c)) for c in columns])
        count += 1
    record(db, user, festival.id, "participant.export", "participant", "report",
           f"{user.name} exported {count:,} participants" + (" with contact details" if sensitive else ""))
    db.commit()
    return StreamingResponse(iter([out.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="participants.csv"'})


def next_code(db: Session, festival_id: int) -> str:
    codes = db.scalars(select(Participant.code).where(Participant.festival_id == festival_id, Participant.code.like("P%")))
    return f"P{max((int(c[1:]) for c in codes if c[1:].isdigit()), default=0) + 1:04d}"


def find_duplicate(db: Session, festival_id: int, phone: str | None, email: str | None, exclude: int | None = None) -> Participant | None:
    matches = []
    if phone:
        matches.append(Participant.phone == phone)
    if email:
        matches.append(func.lower(Participant.email) == email.lower())
    if not matches:
        return None
    query = select(Participant).where(Participant.festival_id == festival_id, Participant.deleted_at.is_(None), or_(*matches))
    if exclude:
        query = query.where(Participant.id != exclude)
    return db.scalar(query.limit(1))


def contingent_id(db: Session, festival_id: int, code: str | None) -> int | None:
    return by_code(db, Contingent, festival_id, code).id if code else None


@router.post("/participants", status_code=201, dependencies=[Depends(require("participants.write"))])
def create_participant(body: ParticipantIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user),
                       db: Session = Depends(get_db)):
    duplicate = find_duplicate(db, festival.id, body.phone, body.email)
    if duplicate:
        raise HTTPException(409, f"{duplicate.code} already uses this phone or email")
    data = body.model_dump(exclude={"contingent_code"})
    p = Participant(festival_id=festival.id, code=next_code(db, festival.id), contingent_id=contingent_id(db, festival.id, body.contingent_code), **data)
    db.add(p)
    db.flush()
    record(db, user, festival.id, "participant.create", "participant", p.code, f"{user.name} added participant {p.full_name} ({p.code})")
    db.commit()
    return serialize(p, "participants.read_sensitive" in permissions_of(user))


@router.post("/participants/import", dependencies=[Depends(require("participants.write"))])
def import_participants(file: UploadFile = File(...), festival: Festival = Depends(festival_access),
                        user: User = Depends(current_user), db: Session = Depends(get_db)):
    raw = file.file.read(IMPORT_LIMIT_BYTES + 1)
    if len(raw) > IMPORT_LIMIT_BYTES:
        raise HTTPException(413, "File is larger than 5 MB")
    rows = read_table(file.filename or "", raw)
    created, rejected = 0, []
    code_number = int(next_code(db, festival.id)[1:])
    for number, row in enumerate(rows, 2):
        try:
            body = ParticipantIn(**{k: v for k, v in row.items() if k in ParticipantIn.model_fields and v not in ("", None)})
        except ValueError as error:
            first = error.errors()[0] if hasattr(error, "errors") else {"loc": ["row"], "msg": str(error)}
            rejected.append({"row": number, "reason": f"{first['loc'][0]}: {first['msg']}"})
            continue
        duplicate = find_duplicate(db, festival.id, body.phone, body.email)
        if duplicate:
            rejected.append({"row": number, "reason": f"Duplicate of {duplicate.code}"})
            continue
        try:
            contingent = contingent_id(db, festival.id, body.contingent_code)
        except HTTPException as error:
            rejected.append({"row": number, "reason": error.detail})
            continue
        db.add(Participant(festival_id=festival.id, code=f"P{code_number:04d}", contingent_id=contingent, **body.model_dump(exclude={"contingent_code"})))
        db.flush()
        code_number += 1
        created += 1
    record(db, user, festival.id, "participant.import", "participant", file.filename,
           f"{user.name} imported {created:,} participants from {file.filename}; {len(rejected)} rows rejected")
    db.commit()
    return {"created": created, "rejected": rejected}


def read_table(filename: str, raw: bytes) -> list[dict[str, Any]]:
    try:
        if filename.lower().endswith(".xlsx"):
            sheet = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True).active
            values = list(sheet.iter_rows(values_only=True))
            headers = [str(h).strip() for h in values[0]]
            return [dict(zip(headers, (str(v).strip() if v is not None else "" for v in row), strict=False)) for row in values[1:]]
        if filename.lower().endswith(".csv"):
            return [{(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))]
    except (ValueError, IndexError, UnicodeDecodeError, OSError) as error:
        raise HTTPException(400, f"Could not read that file: {error}") from error
    raise HTTPException(400, "Upload a .csv or .xlsx file")


@router.post("/passes/verify", dependencies=[Depends(require("participants.checkin"))])
def verify_pass(body: PassCheck, festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    p = db.scalar(select(Participant).where(Participant.festival_id == festival.id, Participant.qr_token == body.token, Participant.deleted_at.is_(None)))
    if not p:
        return {"valid": False}
    return {"valid": True, "code": p.code, "full_name": p.full_name, "college": p.college, "checkin_status": p.checkin_status,
            "registration_status": p.registration_status}


@router.post("/passes/check-in", dependencies=[Depends(require("participants.checkin"))])
def check_in(body: PassCheck, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = db.scalar(select(Participant).where(Participant.festival_id == festival.id, Participant.qr_token == body.token,
                                            Participant.deleted_at.is_(None)).with_for_update())
    if not p:
        raise HTTPException(404, "This pass is not valid")
    if p.registration_status == "cancelled":
        raise HTTPException(409, f"{p.code} has a cancelled registration")
    if p.checkin_status == "checked_in":
        raise HTTPException(409, f"{p.full_name} ({p.code}) is already checked in")
    p.checkin_status = "checked_in"
    record(db, user, festival.id, "participant.checkin", "participant", p.code, f"{p.full_name} ({p.code}) checked in")
    db.commit()
    return {"code": p.code, "full_name": p.full_name, "checkin_status": p.checkin_status}


@router.get("/participants/{code}", dependencies=[Depends(require("participants.read"))])
def get_participant(code: str, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = by_code(db, Participant, festival.id, code)
    sensitive = "participants.read_sensitive" in permissions_of(user)
    row = serialize(p, sensitive)
    row["events"] = [
        {"code": e.code, "name": e.name, "start_at": e.start_at, "end_at": e.end_at, "venue": e.venue.name if e.venue else None,
         "participation_type": e.participation_type, "team_code": r.team_code_raw,
         "clashes": [c.code for c in participant_clashes(db, p.id, e)],
         "off_campus": bool(p.arrival_at and p.departure_at and p.departure_at >= p.arrival_at and e.start_at and
                            (p.arrival_at > e.start_at or p.departure_at < e.end_at))}
        for r, e in db.execute(select(Registration, Event).join(Event, Event.id == Registration.event_id)
                               .where(Registration.participant_id == p.id, Event.deleted_at.is_(None)).order_by(Event.start_at))]
    contingent = db.get(Contingent, p.contingent_id) if p.contingent_id else None
    row["contingent"] = contingent and {"code": contingent.code, "college": contingent.college, "leader": contingent.leader}
    stay = db.execute(select(AccommodationAllocation, Room, Hostel).join(Room, Room.id == AccommodationAllocation.room_id)
                      .join(Hostel, Hostel.id == Room.hostel_id)
                      .where(AccommodationAllocation.participant_id == p.id, AccommodationAllocation.status != "cancelled")).first()
    if "accommodation.read" in permissions_of(user) or sensitive:
        row["accommodation"] = stay and {"room": stay[1].code, "hostel": stay[2].name, "status": stay[0].status}
    else:
        row["accommodation"] = None
    row["transport"] = [{"code": r.code, "name": r.name, "departure_at": r.departure_at, "status": r.status} for r in db.scalars(
        select(Route).join(TransportAssignment, TransportAssignment.route_id == Route.id).where(TransportAssignment.participant_id == p.id))]
    row["issues"] = [{"issue": i.issue, "detail": i.detail} for i in db.scalars(
        select(DataIssue).where(DataIssue.festival_id == festival.id, DataIssue.entity_type == "participant", DataIssue.entity_code == code, DataIssue.status == "open"))]
    return row


@router.patch("/participants/{code}", dependencies=[Depends(require("participants.write"))])
def update_participant(code: str, body: ParticipantIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user),
                       db: Session = Depends(get_db)):
    p = by_code(db, Participant, festival.id, code)
    duplicate = find_duplicate(db, festival.id, body.phone, body.email, exclude=p.id)
    if duplicate:
        raise HTTPException(409, f"{duplicate.code} already uses this phone or email")
    changes = body.model_dump(exclude={"contingent_code"})
    changes["contingent_id"] = contingent_id(db, festival.id, body.contingent_code)
    changed = [k for k, v in changes.items() if getattr(p, k) != v]
    for field in changed:
        setattr(p, field, changes[field])
    # Sensitive values stay out of the audit log: it records that they changed, not what they were.
    record(db, user, festival.id, "participant.update", "participant", code, f"{user.name} updated {p.full_name} ({code})", after={"changed": changed})
    db.commit()
    return serialize(p, "participants.read_sensitive" in permissions_of(user))


@router.delete("/participants/{code}", status_code=204, dependencies=[Depends(require("participants.write"))])
def delete_participant(code: str, body: DeleteReason, festival: Festival = Depends(festival_access), user: User = Depends(current_user),
                       db: Session = Depends(get_db)):
    p = by_code(db, Participant, festival.id, code)
    p.deleted_at = func.now()
    for allocation in db.scalars(select(AccommodationAllocation).where(AccommodationAllocation.participant_id == p.id, AccommodationAllocation.status != "cancelled")):
        allocation.status = "cancelled"
    record(db, user, festival.id, "participant.delete", "participant", code, f"{user.name} removed {p.full_name} ({code})", reason=body.reason)
    db.commit()


@router.post("/participants/{code}/registrations", status_code=201, dependencies=[Depends(require("participants.write"))])
def register(code: str, body: RegistrationIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = by_code(db, Participant, festival.id, code)
    event = by_code(db, Event, festival.id, body.event_code)
    db.add(Registration(festival_id=festival.id, participant_id=p.id, event_id=event.id))
    try:
        db.flush()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(409, f"{p.full_name} is already registered for {event.name}") from error
    clashes = participant_clashes(db, p.id, event)
    record(db, user, festival.id, "registration.create", "registration", f"{code}:{event.code}", f"{user.name} registered {p.full_name} for {event.name}")
    db.commit()
    return {"warnings": [f"Overlaps with {c.name}" for c in clashes]}


@router.delete("/participants/{code}/registrations/{event_code}", status_code=204, dependencies=[Depends(require("participants.write"))])
def unregister(code: str, event_code: str, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = by_code(db, Participant, festival.id, code)
    event = by_code(db, Event, festival.id, event_code)
    registration = db.scalar(select(Registration).where(Registration.participant_id == p.id, Registration.event_id == event.id))
    if not registration:
        raise HTTPException(404, "Registration not found")
    db.delete(registration)
    record(db, user, festival.id, "registration.delete", "registration", f"{code}:{event_code}", f"{user.name} removed {p.full_name} from {event.name}")
    db.commit()


@router.get("/participants/{code}/pass.svg", dependencies=[Depends(require("participants.read"))])
def pass_image(code: str, festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    p = by_code(db, Participant, festival.id, code)
    if not p.qr_token:
        p.qr_token = secrets.token_urlsafe(24)
        db.commit()
    out = io.BytesIO()
    segno.make(p.qr_token, error="m").save(out, kind="svg", scale=6, border=2, dark="#071230", light="#fcf6ea")
    return Response(out.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "private, no-store"})

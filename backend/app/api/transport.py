from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.common import by_code, ordered, paged
from app.auth.deps import current_user, festival_access, require
from app.database import get_db
from app.models import Event, Festival, Participant, Registration, Route, TransportAssignment, User, Vehicle
from app.services.audit import record

router = APIRouter(prefix="/api/festivals/{festival_id}/transport", tags=["transport"])
ROUTE_STATUS = "^(scheduled|in_transit|delayed|arrived|cancelled)$"
ARRIVAL_BUFFER = timedelta(minutes=30)  # Time from drop-off to reaching a venue. See ASSUMPTIONS #15.


class VehicleIn(BaseModel):
    code: str = Field(min_length=2, max_length=20)
    vehicle_type: str = Field(min_length=2, max_length=40)
    capacity: int = Field(ge=1, le=200)
    status: str = Field(default="available", pattern="^(available|maintenance|unavailable)$")


class RouteIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    pickup: str = Field(min_length=2, max_length=120)
    destination: str = Field(min_length=2, max_length=120)
    vehicle_code: str | None = None
    departure_at: datetime
    expected_arrival_at: datetime
    status: str = Field(default="scheduled", pattern=ROUTE_STATUS)


class DelayIn(BaseModel):
    minutes: int = Field(ge=1, le=1440)
    reason: str = Field(min_length=3, max_length=300)


class PassengerIn(BaseModel):
    participant_code: str


def rider_counts(db: Session, festival_id: int) -> dict[int, int]:
    return dict(db.execute(select(TransportAssignment.route_id, func.count()).where(TransportAssignment.festival_id == festival_id).group_by(TransportAssignment.route_id)).all())


def route_row(r: Route, riders: dict[int, int], vehicles: dict[int, Vehicle]) -> dict[str, Any]:
    v = vehicles.get(r.vehicle_id)
    return {"code": r.code, "name": r.name, "pickup": r.pickup, "destination": r.destination, "departure_at": r.departure_at,
            "expected_arrival_at": r.expected_arrival_at, "status": r.status, "delay_minutes": r.delay_minutes, "is_simulated": r.is_simulated,
            "vehicle": v and {"code": v.code, "vehicle_type": v.vehicle_type, "capacity": v.capacity}, "passengers": riders.get(r.id, 0),
            "points": None if r.pickup_lat is None or r.destination_lat is None else [[r.pickup_lat, r.pickup_lng], [r.destination_lat, r.destination_lng]]}


def vehicle_map(db: Session, festival_id: int) -> dict[int, Vehicle]:
    return {v.id: v for v in db.scalars(select(Vehicle).where(Vehicle.festival_id == festival_id))}


@router.get("/vehicles", dependencies=[Depends(require("transport.read"))])
def list_vehicles(festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    return [{"code": v.code, "vehicle_type": v.vehicle_type, "capacity": v.capacity, "status": v.status, "is_simulated": v.is_simulated}
            for v in sorted(vehicle_map(db, festival.id).values(), key=lambda v: v.code)]


@router.post("/vehicles", status_code=201, dependencies=[Depends(require("transport.write"))])
def create_vehicle(body: VehicleIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.add(Vehicle(festival_id=festival.id, **body.model_dump()))
    try:
        record(db, user, festival.id, "vehicle.create", "vehicle", body.code, f"{user.name} added vehicle {body.code}")
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(409, f"Vehicle {body.code} already exists") from error
    return {"code": body.code}


@router.patch("/vehicles/{code}", dependencies=[Depends(require("transport.write"))])
def update_vehicle(code: str, body: VehicleIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    v = by_code(db, Vehicle, festival.id, code)
    v.vehicle_type, v.capacity, v.status = body.vehicle_type, body.capacity, body.status
    record(db, user, festival.id, "vehicle.update", "vehicle", code, f"{user.name} updated vehicle {code}")
    db.commit()
    return {"code": code}


@router.get("/routes", dependencies=[Depends(require("transport.read"))])
def list_routes(page: int = Query(1, ge=1), status: str | None = None, sort: str | None = None, festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    query = select(Route).where(Route.festival_id == festival.id)
    if status:
        query = query.where(Route.status == status)
    riders, vehicles = rider_counts(db, festival.id), vehicle_map(db, festival.id)
    columns = {"code": Route.code, "name": Route.name, "departs": Route.departure_at, "arrives": Route.expected_arrival_at, "status": Route.status}
    return paged(db, ordered(query, sort, columns, Route.departure_at.nulls_last(), Route.code), page, lambda r: route_row(r, riders, vehicles))


def next_route_code(db: Session, festival_id: int) -> str:
    codes = db.scalars(select(Route.code).where(Route.festival_id == festival_id))
    return f"TRIP{max((int(c[4:]) for c in codes if c[4:].isdigit()), default=0) + 1:02d}"


def validated_vehicle(db: Session, festival_id: int, body: RouteIn) -> Vehicle | None:
    if body.expected_arrival_at <= body.departure_at:
        raise HTTPException(422, "Arrival must be after departure")
    return by_code(db, Vehicle, festival_id, body.vehicle_code) if body.vehicle_code else None


@router.post("/routes", status_code=201, dependencies=[Depends(require("transport.write"))])
def create_route(body: RouteIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    vehicle = validated_vehicle(db, festival.id, body)
    r = Route(festival_id=festival.id, code=next_route_code(db, festival.id), vehicle_id=vehicle.id if vehicle else None, **body.model_dump(exclude={"vehicle_code"}))
    db.add(r)
    record(db, user, festival.id, "route.create", "route", r.code, f"{user.name} added {r.name}")
    db.commit()
    return {"code": r.code}


@router.patch("/routes/{code}", dependencies=[Depends(require("transport.write"))])
def update_route(code: str, body: RouteIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    r = by_code(db, Route, festival.id, code)
    vehicle = validated_vehicle(db, festival.id, body)
    riders = rider_counts(db, festival.id).get(r.id, 0)
    if vehicle and riders > vehicle.capacity:
        raise HTTPException(409, f"{riders} passengers will not fit in {vehicle.code}, which seats {vehicle.capacity}")
    for field, value in body.model_dump(exclude={"vehicle_code"}).items():
        setattr(r, field, value)
    r.vehicle_id = vehicle.id if vehicle else None
    record(db, user, festival.id, "route.update", "route", code, f"{user.name} updated {r.name}")
    db.commit()
    return {"code": code}


def affected_events(db: Session, festival_id: int, route: Route, arrival: datetime) -> list[dict[str, Any]]:
    """Registered events that start before these passengers can reach them."""
    rows = db.execute(
        select(Event.code, Event.name, Event.start_at, func.count(Registration.id))
        .join(Registration, Registration.event_id == Event.id)
        .join(TransportAssignment, TransportAssignment.participant_id == Registration.participant_id)
        .where(TransportAssignment.route_id == route.id, Event.festival_id == festival_id, Event.deleted_at.is_(None),
               Event.start_at < arrival + ARRIVAL_BUFFER, Event.end_at > arrival)
        .group_by(Event.id).order_by(Event.start_at))
    return [{"code": c, "name": n, "start_at": s, "passengers": count} for c, n, s, count in rows]


@router.get("/routes/{code}", dependencies=[Depends(require("transport.read"))])
def get_route(code: str, festival: Festival = Depends(festival_access), db: Session = Depends(get_db)):
    r = by_code(db, Route, festival.id, code)
    row = route_row(r, rider_counts(db, festival.id), vehicle_map(db, festival.id))
    row["riders"] = [{"code": c, "name": n, "college": g} for c, n, g in db.execute(
        select(Participant.code, Participant.full_name, Participant.college).join(TransportAssignment, TransportAssignment.participant_id == Participant.id)
        .where(TransportAssignment.route_id == r.id).order_by(Participant.code))]
    row["affected_events"] = affected_events(db, festival.id, r, r.expected_arrival_at) if r.expected_arrival_at else []
    return row


@router.post("/routes/{code}/delay", dependencies=[Depends(require("transport.write"))])
def report_delay(code: str, body: DelayIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    r = by_code(db, Route, festival.id, code)
    if not r.expected_arrival_at:
        raise HTTPException(409, "This route has no arrival time to delay")
    r.expected_arrival_at += timedelta(minutes=body.minutes)
    r.delay_minutes += body.minutes
    r.status = "delayed"
    record(db, user, festival.id, "route.delay", "route", code, f"{r.name} delayed by {body.minutes} minutes", reason=body.reason)
    db.commit()
    return {"expected_arrival_at": r.expected_arrival_at, "affected_events": affected_events(db, festival.id, r, r.expected_arrival_at)}


@router.post("/routes/{code}/passengers", status_code=201, dependencies=[Depends(require("transport.write"))])
def add_passenger(code: str, body: PassengerIn, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    r = db.scalar(select(Route).where(Route.festival_id == festival.id, Route.code == code).with_for_update())
    if not r:
        raise HTTPException(404, f"Route {code} not found")
    p = by_code(db, Participant, festival.id, body.participant_code)
    vehicle = db.get(Vehicle, r.vehicle_id) if r.vehicle_id else None
    riders = db.scalar(select(func.count()).select_from(TransportAssignment).where(TransportAssignment.route_id == r.id)) or 0
    if vehicle and riders >= vehicle.capacity:
        raise HTTPException(409, f"{vehicle.code} is full with {riders} passengers")
    db.add(TransportAssignment(festival_id=festival.id, route_id=r.id, participant_id=p.id))
    try:
        record(db, user, festival.id, "route.assign", "route", code, f"{user.name} assigned {p.code} to {r.name}")
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(409, f"{p.code} is already on this route") from error
    return {"passengers": riders + 1}


@router.delete("/routes/{code}/passengers/{participant_code}", status_code=204, dependencies=[Depends(require("transport.write"))])
def remove_passenger(code: str, participant_code: str, festival: Festival = Depends(festival_access), user: User = Depends(current_user), db: Session = Depends(get_db)):
    r = by_code(db, Route, festival.id, code)
    p = by_code(db, Participant, festival.id, participant_code)
    assignment = db.scalar(select(TransportAssignment).where(TransportAssignment.route_id == r.id, TransportAssignment.participant_id == p.id))
    if not assignment:
        raise HTTPException(404, "Passenger not on this route")
    db.delete(assignment)
    record(db, user, festival.id, "route.unassign", "route", code, f"{user.name} removed {p.code} from {r.name}")
    db.commit()

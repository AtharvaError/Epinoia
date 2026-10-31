"""All tables. Pack blanks stay NULL: a missing value never gets a default meaning."""
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Column, Date, DateTime, Float, ForeignKey, Index, Integer,
    Numeric, String, Table, Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

from app.database import Base

Money = Numeric(12, 2)


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class FestivalScoped(Timestamped):
    @declared_attr
    def festival_id(cls) -> Mapped[int]:
        return mapped_column(ForeignKey("festivals.id", ondelete="CASCADE"), index=True)


class Simulated:
    """True when the row was invented for the demo because the pack has no such data."""
    is_simulated: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))


class SoftDeleted:
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


role_permissions = Table(
    "role_permissions", Base.metadata,
    Column("role_id", ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column("permission_id", ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
)


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    permissions: Mapped[list["Permission"]] = relationship(secondary=role_permissions)


class Permission(Base):
    __tablename__ = "permissions"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(60), unique=True)


class Festival(Timestamped, Base):
    __tablename__ = "festivals"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    timezone: Mapped[str] = mapped_column(String(40), server_default="Asia/Kolkata")
    status: Mapped[str] = mapped_column(String(20), server_default="planning")
    minor_housing_policy: Mapped[str | None] = mapped_column(String(40))
    minor_policy_set_by: Mapped[str | None] = mapped_column(String(120))
    campus_latitude: Mapped[float] = mapped_column(Float, server_default="22.5838")
    campus_longitude: Mapped[float] = mapped_column(Float, server_default="75.7656")
    campus_name: Mapped[str] = mapped_column(String(120), server_default="IIM Indore")
    __table_args__ = (CheckConstraint("end_date >= start_date", name="dates_ordered"),)


class Department(FestivalScoped, Base):
    __tablename__ = "departments"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    __table_args__ = (UniqueConstraint("festival_id", "name"),)


class User(Timestamped, Simulated, Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(160), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(100))  # NULL for external sign-in
    name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(30))
    member_code: Mapped[str | None] = mapped_column(String(20))
    shift: Mapped[str | None] = mapped_column(String(20))
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), index=True)
    festival_id: Mapped[int | None] = mapped_column(ForeignKey("festivals.id"), index=True)  # NULL = all festivals
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    token_version: Mapped[int] = mapped_column(Integer, server_default="0")  # Raising it ends every session.
    role: Mapped[Role] = relationship(lazy="joined")


class Contingent(FestivalScoped, Base):
    __tablename__ = "contingents"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20))
    college: Mapped[str] = mapped_column(String(160))
    leader: Mapped[str | None] = mapped_column(String(120))
    escort: Mapped[str | None] = mapped_column(String(120))
    strength_declared: Mapped[int | None] = mapped_column(Integer)
    preferences: Mapped[str | None] = mapped_column(String(120))
    __table_args__ = (UniqueConstraint("festival_id", "code"),)


class Participant(FestivalScoped, SoftDeleted, Base):
    __tablename__ = "participants"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20))
    full_name: Mapped[str] = mapped_column(String(120), index=True)
    email: Mapped[str | None] = mapped_column(String(160))
    phone: Mapped[str | None] = mapped_column(String(30))
    college: Mapped[str] = mapped_column(String(160), index=True)
    department: Mapped[str | None] = mapped_column(String(120))
    contingent_id: Mapped[int | None] = mapped_column(ForeignKey("contingents.id"), index=True)
    gender_as_given: Mapped[str | None] = mapped_column(String(40))
    age: Mapped[int | None] = mapped_column(Integer)
    accessibility_needs: Mapped[str | None] = mapped_column(String(120))
    dietary_needs: Mapped[str | None] = mapped_column(String(80))
    arrival_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    departure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accommodation_required: Mapped[bool | None] = mapped_column(Boolean)
    transport_required: Mapped[bool | None] = mapped_column(Boolean)
    tshirt_size: Mapped[str | None] = mapped_column(String(10))
    registration_status: Mapped[str] = mapped_column(String(20), server_default="registered")
    checkin_status: Mapped[str] = mapped_column(String(20), server_default="not_arrived")
    qr_token: Mapped[str | None] = mapped_column(String(64), unique=True)
    housing_preference: Mapped[str | None] = mapped_column(String(40))
    housing_outreach_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    minor_supervision_status: Mapped[str | None] = mapped_column(String(30))
    minor_supervision_note: Mapped[str | None] = mapped_column(String(240))
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    telegram_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    __table_args__ = (UniqueConstraint("festival_id", "code"),)


class Venue(FestivalScoped, Base):
    __tablename__ = "venues"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(120))
    location: Mapped[str | None] = mapped_column(String(160))
    capacity: Mapped[int] = mapped_column(Integer)
    indoor_outdoor: Mapped[str | None] = mapped_column(String(20))
    availability: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    maintenance_status: Mapped[str] = mapped_column(String(20), server_default="operational")
    __table_args__ = (UniqueConstraint("festival_id", "code"), CheckConstraint("capacity >= 0", name="capacity_valid"))


class Event(FestivalScoped, SoftDeleted, Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str | None] = mapped_column(String(60))
    participation_type: Mapped[str] = mapped_column(String(10))
    description: Mapped[str | None] = mapped_column(Text)
    rounds: Mapped[list[str]] = mapped_column(JSONB, server_default="[]")
    duration_minutes: Mapped[int] = mapped_column(Integer)
    venue_requirements: Mapped[str | None] = mapped_column(String(120))
    equipment_needs: Mapped[str | None] = mapped_column(String(120))
    volunteers_needed: Mapped[int] = mapped_column(Integer, server_default="0")
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    schedule_source: Mapped[str | None] = mapped_column(String(20))  # generated | manual
    venue_id: Mapped[int | None] = mapped_column(ForeignKey("venues.id"), index=True)
    coordinator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    judges: Mapped[list[str]] = mapped_column(JSONB, server_default="[]")
    dependencies: Mapped[list[str]] = mapped_column(JSONB, server_default="[]")
    status: Mapped[str] = mapped_column(String(20), server_default="scheduled")
    venue: Mapped[Venue | None] = relationship()
    __table_args__ = (
        UniqueConstraint("festival_id", "code"),
        CheckConstraint("end_at IS NULL OR start_at IS NULL OR end_at > start_at", name="times_ordered"),
    )


class Team(FestivalScoped, Base):
    __tablename__ = "teams"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30))
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    contingent_id: Mapped[int | None] = mapped_column(ForeignKey("contingents.id"))
    __table_args__ = (UniqueConstraint("festival_id", "code"),)


class Registration(FestivalScoped, Base):
    __tablename__ = "registrations"
    id: Mapped[int] = mapped_column(primary_key=True)
    participant_id: Mapped[int] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), index=True)
    team_code_raw: Mapped[str | None] = mapped_column(String(30))  # as given, see ASSUMPTIONS #4
    attendance: Mapped[str] = mapped_column(String(20), server_default="unknown")
    __table_args__ = (UniqueConstraint("participant_id", "event_id"),)


class Resource(FestivalScoped, Simulated, Base):
    __tablename__ = "resources"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(20))  # logistics | merch
    name: Mapped[str] = mapped_column(String(120))
    size: Mapped[str | None] = mapped_column(String(10))
    quantity: Mapped[int] = mapped_column(Integer)
    reserved_quantity: Mapped[int] = mapped_column(Integer, server_default="0")
    location: Mapped[str | None] = mapped_column(String(120))
    condition: Mapped[str | None] = mapped_column(String(30))
    __table_args__ = (
        UniqueConstraint("festival_id", "code"),
        CheckConstraint("quantity >= 0 AND reserved_quantity >= 0", name="quantities_valid"),
    )


class ResourceAllocation(FestivalScoped, Base):
    __tablename__ = "resource_allocations"
    id: Mapped[int] = mapped_column(primary_key=True)
    resource_id: Mapped[int] = mapped_column(ForeignKey("resources.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    __table_args__ = (UniqueConstraint("resource_id", "event_id"), CheckConstraint("quantity > 0", name="quantity_valid"))


class Hostel(FestivalScoped, Base):
    __tablename__ = "hostels"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20))  # on_campus | external
    cost_per_bed_night: Mapped[Decimal | None] = mapped_column(Money)
    distance_km: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    transport_available: Mapped[bool | None] = mapped_column(Boolean)
    accessibility_status: Mapped[str] = mapped_column(String(20), server_default="unknown")
    step_free_entry: Mapped[bool | None] = mapped_column(Boolean)
    ground_floor_rooms: Mapped[int | None] = mapped_column(Integer)
    accessible_bathrooms: Mapped[int | None] = mapped_column(Integer)
    has_lift: Mapped[bool | None] = mapped_column(Boolean)
    accessibility_confirmed_by: Mapped[str | None] = mapped_column(String(120))
    accessibility_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), server_default="available")
    __table_args__ = (UniqueConstraint("festival_id", "code"),)


class Room(FestivalScoped, Base):
    __tablename__ = "rooms"
    id: Mapped[int] = mapped_column(primary_key=True)
    hostel_id: Mapped[int] = mapped_column(ForeignKey("hostels.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(30))
    floor: Mapped[int | None] = mapped_column(Integer)
    gender_policy: Mapped[str | None] = mapped_column(String(20))  # boys | girls | NULL = not stated
    capacity: Mapped[int] = mapped_column(Integer)
    accessible: Mapped[bool | None] = mapped_column(Boolean)
    policy_confirmed_by: Mapped[str | None] = mapped_column(String(120))
    near_washroom: Mapped[bool | None] = mapped_column(Boolean)
    visual_assistance: Mapped[bool | None] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String(20), server_default="available")
    __table_args__ = (UniqueConstraint("festival_id", "code"), CheckConstraint("capacity >= 0", name="capacity_valid"))


class AccommodationAllocation(FestivalScoped, Base):
    __tablename__ = "accommodation_allocations"
    id: Mapped[int] = mapped_column(primary_key=True)
    participant_id: Mapped[int] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"), index=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), server_default="allocated")
    check_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    check_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    relaxed_constraints: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    override_reason: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        Index("uq_one_active_allocation", "participant_id", unique=True, postgresql_where=text("status <> 'cancelled'")),
    )


class Vehicle(FestivalScoped, Simulated, Base):
    __tablename__ = "vehicles"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20))
    vehicle_type: Mapped[str] = mapped_column(String(40))
    capacity: Mapped[int] = mapped_column(Integer)
    coordinator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(20), server_default="available")
    __table_args__ = (UniqueConstraint("festival_id", "code"),)


class Route(FestivalScoped, Simulated, Base):
    """One scheduled trip of a vehicle."""
    __tablename__ = "routes"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(120))
    pickup: Mapped[str] = mapped_column(String(120))
    destination: Mapped[str] = mapped_column(String(120))
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey("vehicles.id"), index=True)
    departure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expected_arrival_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), server_default="scheduled")
    delay_minutes: Mapped[int] = mapped_column(Integer, server_default="0")
    # Approximate stop locations for the route map. NULL when a stop has no known location. Never live GPS.
    pickup_lat: Mapped[float | None] = mapped_column(Float)
    pickup_lng: Mapped[float | None] = mapped_column(Float)
    destination_lat: Mapped[float | None] = mapped_column(Float)
    destination_lng: Mapped[float | None] = mapped_column(Float)
    __table_args__ = (UniqueConstraint("festival_id", "code"),)


class TransportAssignment(FestivalScoped, Simulated, Base):
    __tablename__ = "transport_assignments"
    id: Mapped[int] = mapped_column(primary_key=True)
    route_id: Mapped[int] = mapped_column(ForeignKey("routes.id", ondelete="CASCADE"), index=True)
    participant_id: Mapped[int] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"), index=True)
    __table_args__ = (UniqueConstraint("route_id", "participant_id"),)


class Volunteer(FestivalScoped, Simulated, Base):
    __tablename__ = "volunteers"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(30))
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), index=True)
    skills: Mapped[list[str]] = mapped_column(JSONB, server_default="[]")
    availability: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    status: Mapped[str] = mapped_column(String(20), server_default="active")
    __table_args__ = (UniqueConstraint("festival_id", "code"),)


class VolunteerShift(FestivalScoped, Simulated, Base):
    __tablename__ = "volunteer_shifts"
    id: Mapped[int] = mapped_column(primary_key=True)
    volunteer_id: Mapped[int] = mapped_column(ForeignKey("volunteers.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id"), index=True)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    task: Mapped[str | None] = mapped_column(String(160))
    attendance: Mapped[str] = mapped_column(String(20), server_default="unknown")
    status: Mapped[str] = mapped_column(String(20), server_default="assigned")
    __table_args__ = (CheckConstraint("end_at > start_at", name="times_ordered"),)


class Task(FestivalScoped, Simulated, Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id"))
    assignee_volunteer_id: Mapped[int | None] = mapped_column(ForeignKey("volunteers.id"))
    status: Mapped[str] = mapped_column(String(20), server_default="todo")
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Budget(FestivalScoped, Simulated, Base):
    __tablename__ = "budgets"
    id: Mapped[int] = mapped_column(primary_key=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"))
    approved_amount: Mapped[Decimal] = mapped_column(Money)
    __table_args__ = (UniqueConstraint("festival_id", "department_id"), CheckConstraint("approved_amount >= 0", name="amount_valid"))


class Expense(FestivalScoped, Simulated, SoftDeleted, Base):
    __tablename__ = "expenses"
    id: Mapped[int] = mapped_column(primary_key=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), index=True)
    category: Mapped[str] = mapped_column(String(60))
    description: Mapped[str] = mapped_column(String(200))
    estimated_amount: Mapped[Decimal] = mapped_column(Money)
    actual_amount: Mapped[Decimal | None] = mapped_column(Money)
    approval_status: Mapped[str] = mapped_column(String(20), server_default="pending")
    payment_status: Mapped[str] = mapped_column(String(20), server_default="unpaid")


class Sponsor(FestivalScoped, Simulated, Base):
    __tablename__ = "sponsors"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    tier: Mapped[str | None] = mapped_column(String(30))
    committed_amount: Mapped[Decimal] = mapped_column(Money)
    received_amount: Mapped[Decimal] = mapped_column(Money, server_default="0")


class Vendor(FestivalScoped, Base):
    __tablename__ = "vendors"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(120))
    menu: Mapped[str | None] = mapped_column(String(120))
    dietary_options: Mapped[list[str]] = mapped_column(JSONB, server_default="[]")
    hygiene_certificate_expiry: Mapped[date | None] = mapped_column(Date)
    hours: Mapped[str | None] = mapped_column(String(30))
    capacity: Mapped[int | None] = mapped_column(Integer)
    payment_terms: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), server_default="active")
    # Provenance & manually verified operational fields (never overwrite original CSV data)
    source: Mapped[str] = mapped_column(String(20), server_default="pack")
    original_pack_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    operational_status: Mapped[str] = mapped_column(String(24), server_default="unconfirmed")
    capacity_unit: Mapped[str | None] = mapped_column(String(40))
    capacity_verified_by: Mapped[str | None] = mapped_column(String(120))
    capacity_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_dietary_options: Mapped[list[str] | None] = mapped_column(JSONB)
    nut_free_verified: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    allergen_controls_verified: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    cross_contamination_note: Mapped[str | None] = mapped_column(String(240))
    certificate_verified_by: Mapped[str | None] = mapped_column(String(120))
    certificate_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    certificate_verification_note: Mapped[str | None] = mapped_column(String(240))
    contact_name: Mapped[str | None] = mapped_column(String(120))
    contact_phone: Mapped[str | None] = mapped_column(String(40))
    verification_notes: Mapped[str | None] = mapped_column(Text)
    verified_by: Mapped[str | None] = mapped_column(String(120))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("festival_id", "code"),)


class VendorInterruption(FestivalScoped, Base):
    __tablename__ = "vendor_interruptions"
    id: Mapped[int] = mapped_column(primary_key=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id", ondelete="CASCADE"), index=True)
    vendor_code: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(Text)
    simulation_report: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    replacement_codes: Mapped[list[str]] = mapped_column(JSONB, server_default="[]")
    status: Mapped[str] = mapped_column(String(24), server_default="simulated")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_by_name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decided_by_name: Mapped[str | None] = mapped_column(String(120))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str | None] = mapped_column(Text)


class Announcement(FestivalScoped, Simulated, Base):
    __tablename__ = "announcements"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    audience_type: Mapped[str] = mapped_column(String(20), server_default="festival")
    audience_ref: Mapped[str | None] = mapped_column(String(60))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class Notification(FestivalScoped, Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str | None] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(String(200))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Incident(FestivalScoped, Simulated, Base):
    __tablename__ = "incidents"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20))
    category: Mapped[str] = mapped_column(String(60))
    location: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(10))
    reported_by: Mapped[str] = mapped_column(String(120))
    assigned_team: Mapped[str | None] = mapped_column(String(120))
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    status: Mapped[str] = mapped_column(String(20), server_default="reported")
    resolution_notes: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint("festival_id", "code"),
        CheckConstraint(
            "status IN ('reported','acknowledged','assigned','in_progress','resolved','closed')", name="status_valid"
        ),
    )


class Simulation(FestivalScoped, Base):
    __tablename__ = "simulations"
    id: Mapped[int] = mapped_column(primary_key=True)
    scenario: Mapped[str] = mapped_column(String(40))
    proposed_change: Mapped[dict[str, Any]] = mapped_column(JSONB)
    state_hash: Mapped[str] = mapped_column(String(64))
    report: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    status: Mapped[str] = mapped_column(String(20), server_default="previewed")
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))


class SimulationImpact(FestivalScoped, Base):
    __tablename__ = "simulation_impacts"
    id: Mapped[int] = mapped_column(primary_key=True)
    simulation_id: Mapped[int] = mapped_column(ForeignKey("simulations.id", ondelete="CASCADE"), index=True)
    module: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(10))
    is_direct: Mapped[bool] = mapped_column(Boolean)
    certainty: Mapped[str] = mapped_column(String(10))  # fact | estimate
    record_ref: Mapped[str | None] = mapped_column(String(60))
    explanation: Mapped[str] = mapped_column(Text)
    recommended_action: Mapped[str | None] = mapped_column(Text)


class ChangeApproval(FestivalScoped, Base):
    __tablename__ = "change_approvals"
    id: Mapped[int] = mapped_column(primary_key=True)
    simulation_id: Mapped[int] = mapped_column(ForeignKey("simulations.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), server_default="pending")
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(Text)


class DataIssue(FestivalScoped, Base):
    __tablename__ = "data_issues"
    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(30))
    entity_code: Mapped[str] = mapped_column(String(40))
    issue: Mapped[str] = mapped_column(String(50), index=True)
    detail: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), server_default="open")


class AuditLog(Base):
    """Append-only: a database trigger rejects UPDATE and DELETE."""
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    festival_id: Mapped[int | None] = mapped_column(ForeignKey("festivals.id"), index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    actor_label: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(60))
    entity: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[str | None] = mapped_column(String(60))
    summary: Mapped[str] = mapped_column(Text)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class ExternalHotel(FestivalScoped, Base):
    __tablename__ = "external_hotels"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    address: Mapped[str | None] = mapped_column(String(300))
    area: Mapped[str | None] = mapped_column(String(120), index=True)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    phone: Mapped[str | None] = mapped_column(String(40))
    alternate_phone: Mapped[str | None] = mapped_column(String(40))
    price_min: Mapped[int | None] = mapped_column(Integer)
    price_max: Mapped[int | None] = mapped_column(Integer)
    rating: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(Text)
    google_maps_link: Mapped[str | None] = mapped_column(String(500))
    is_verified: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    verified_by: Mapped[str | None] = mapped_column(String(120))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class StaySuggestionLog(FestivalScoped, Base):
    __tablename__ = "stay_suggestion_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    sent_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    sent_by_name: Mapped[str] = mapped_column(String(120))
    participant_id: Mapped[int] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"), index=True)
    participant_name: Mapped[str] = mapped_column(String(120))
    participant_code: Mapped[str] = mapped_column(String(20))
    hotel_ids: Mapped[list[int]] = mapped_column(JSONB, server_default="[]")
    hotel_names: Mapped[list[str]] = mapped_column(JSONB, server_default="[]")
    message_text: Mapped[str] = mapped_column(Text)
    delivery_channel: Mapped[str] = mapped_column(String(30))  # telegram | clipboard
    delivery_status: Mapped[str] = mapped_column(String(30))   # delivered | failed | copied
    error_message: Mapped[str | None] = mapped_column(Text)

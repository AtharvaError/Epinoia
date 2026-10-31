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
    in_transit_quantity: Mapped[int] = mapped_column(Integer, server_default="0")
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    location: Mapped[str | None] = mapped_column(String(120))
    condition: Mapped[str | None] = mapped_column(String(30))
    __table_args__ = (
        UniqueConstraint("festival_id", "code"),
        CheckConstraint("quantity >= 0 AND reserved_quantity >= 0 AND in_transit_quantity >= 0", name="quantities_valid"),
    )


class ResourceAllocation(FestivalScoped, Base):
    __tablename__ = "resource_allocations"
    id: Mapped[int] = mapped_column(primary_key=True)
    resource_id: Mapped[int] = mapped_column(ForeignKey("resources.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    __table_args__ = (UniqueConstraint("resource_id", "event_id"), CheckConstraint("quantity > 0", name="quantity_valid"))


class InventoryLocation(FestivalScoped, Base):
    """Unified location registry for the 5 campus stock locations and 9 venues."""
    __tablename__ = "inventory_locations"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(24), server_default="stock_only")  # stock_only | venue_and_stock | venue_only
    venue_id: Mapped[int | None] = mapped_column(ForeignKey("venues.id", ondelete="SET NULL"), index=True)
    venue_code: Mapped[str | None] = mapped_column(String(20))
    matched_by_name: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    venue_confirmed: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    venue_confirmed_by: Mapped[str | None] = mapped_column(String(120))
    venue_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    position_unverified: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    position_note: Mapped[str | None] = mapped_column(String(240))
    position_updated_by: Mapped[str | None] = mapped_column(String(120))
    position_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("festival_id", "name"),)


class EquipmentAlias(FestivalScoped, Base):
    """Plain-data editable alias table mapping event tokens to catalogue items."""
    __tablename__ = "equipment_aliases"
    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(80))
    target_item: Mapped[str | None] = mapped_column(String(80))
    verified: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    verified_by: Mapped[str | None] = mapped_column(String(120))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(20), server_default="pack")  # pack | organizer
    __table_args__ = (UniqueConstraint("festival_id", "token"),)


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


class EquipmentRequest(FestivalScoped, Base):
    """An event's confirmed equipment need. Prefilled from events.csv equipment_needs; quantities confirmed by a person."""
    __tablename__ = "equipment_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20))
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    item: Mapped[str] = mapped_column(String(60))
    quantity: Mapped[int] = mapped_column(Integer)
    destination: Mapped[str] = mapped_column(String(120))
    destination_verified: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    needed_by: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    priority: Mapped[str] = mapped_column(String(10), server_default="normal")
    notes: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20), server_default="event_csv")  # event_csv | manual
    source_text: Mapped[str | None] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(24), server_default="submitted")
    has_shortage: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    availability_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    requested_by_name: Mapped[str] = mapped_column(String(120))
    decided_by_name: Mapped[str | None] = mapped_column(String(120))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str | None] = mapped_column(Text)
    lines: Mapped[list["EquipmentRequestLine"]] = relationship(cascade="all, delete-orphan")
    __table_args__ = (UniqueConstraint("festival_id", "code"), CheckConstraint("quantity > 0", name="quantity_valid"))


class EquipmentRequestLine(FestivalScoped, Base):
    """One category line inside an EquipmentRequest."""
    __tablename__ = "equipment_request_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("equipment_requests.id", ondelete="CASCADE"), index=True)
    raw_token: Mapped[str | None] = mapped_column(String(120))
    item: Mapped[str] = mapped_column(String(60))
    matched: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    confirmed_qty: Mapped[int] = mapped_column(Integer)
    qty_source: Mapped[str] = mapped_column(String(30), server_default="organizer")  # events_csv | organizer | venue_suggestion
    confirmed_by: Mapped[str | None] = mapped_column(String(120))
    approved_qty: Mapped[int] = mapped_column(Integer, server_default="0")
    shortage_qty: Mapped[int] = mapped_column(Integer, server_default="0")
    shortage_decision: Mapped[str | None] = mapped_column(String(40))  # inspect | repair | transfer | source_externally | accept_reduced
    shortage_decision_qty: Mapped[int | None] = mapped_column(Integer)
    shortage_decision_note: Mapped[str | None] = mapped_column(Text)
    shortage_decided_by: Mapped[str | None] = mapped_column(String(120))
    shortage_decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("confirmed_qty > 0 AND approved_qty >= 0 AND shortage_qty >= 0", name="req_line_qty_valid"),)


class EquipmentAllocation(FestivalScoped, Base):
    """Approved commitment of Good stock from a source location for a request line."""
    __tablename__ = "equipment_allocations"
    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("equipment_requests.id", ondelete="CASCADE"), index=True)
    request_line_id: Mapped[int | None] = mapped_column(ForeignKey("equipment_request_lines.id", ondelete="CASCADE"), index=True)
    resource_id: Mapped[int] = mapped_column(ForeignKey("resources.id"), index=True)
    item: Mapped[str] = mapped_column(String(60))
    source_location: Mapped[str] = mapped_column(String(120))
    destination: Mapped[str] = mapped_column(String(120))
    qty: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), server_default="allocated")  # allocated | dispatched | in_transit | received | partially_received | returned | released
    is_in_place: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    needs_check_exception: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    exception_by: Mapped[str | None] = mapped_column(String(120))
    exception_reason: Mapped[str | None] = mapped_column(Text)
    commitment_scope: Mapped[str] = mapped_column(String(40), server_default="held for entire fest")
    __table_args__ = (CheckConstraint("qty > 0", name="alloc_qty_valid"),)


class EquipmentTransfer(FestivalScoped, Base):
    """Quantity-based movement of one aggregate inventory line. Lifecycle REQUESTED to COMPLETED."""
    __tablename__ = "equipment_transfers"
    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int | None] = mapped_column(ForeignKey("equipment_requests.id", ondelete="CASCADE"), index=True)
    request_line_id: Mapped[int | None] = mapped_column(ForeignKey("equipment_request_lines.id", ondelete="SET NULL"), index=True)
    allocation_id: Mapped[int | None] = mapped_column(ForeignKey("equipment_allocations.id", ondelete="SET NULL"), index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("equipment_transfers.id"))
    resource_id: Mapped[int] = mapped_column(ForeignKey("resources.id"), index=True)
    kind: Mapped[str] = mapped_column(String(10), server_default="delivery")  # delivery | return | manual
    from_location: Mapped[str] = mapped_column(String(120))
    to_location: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(24), server_default="REQUESTED")
    quantity_planned: Mapped[int] = mapped_column(Integer)
    quantity_approved: Mapped[int | None] = mapped_column(Integer)
    quantity_dispatched: Mapped[int | None] = mapped_column(Integer)
    quantity_received: Mapped[int | None] = mapped_column(Integer)
    quantity_accepted: Mapped[int | None] = mapped_column(Integer)
    quantity_damaged: Mapped[int | None] = mapped_column(Integer)
    quantity_missing: Mapped[int | None] = mapped_column(Integer, server_default="0")
    quantity_good_returned: Mapped[int | None] = mapped_column(Integer)
    quantity_needs_check_returned: Mapped[int | None] = mapped_column(Integer)
    reserved_quantity: Mapped[int] = mapped_column(Integer, server_default="0")
    verified: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    stages: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")  # status -> {at, by, quantity, note}
    discrepancy_note: Mapped[str | None] = mapped_column(Text)
    discrepancy_status: Mapped[str | None] = mapped_column(String(24))  # open | resolved_found | resolved_lost
    discrepancy_resolved_by: Mapped[str | None] = mapped_column(String(120))
    discrepancy_resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discrepancy_resolution_note: Mapped[str | None] = mapped_column(Text)
    condition_note: Mapped[str | None] = mapped_column(Text)
    dispatch_idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True)
    receive_idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True)
    return_idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True)


class MovementLedger(Base):
    """Append-only equipment movement & condition ledger: DB trigger rejects UPDATE and DELETE."""
    __tablename__ = "movement_ledger"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    festival_id: Mapped[int] = mapped_column(ForeignKey("festivals.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(40), index=True)
    item: Mapped[str] = mapped_column(String(60), index=True)
    resource_code: Mapped[str | None] = mapped_column(String(40))
    qty: Mapped[int] = mapped_column(Integer)
    from_location: Mapped[str | None] = mapped_column(String(120))
    to_location: Mapped[str | None] = mapped_column(String(120))
    from_condition: Mapped[str | None] = mapped_column(String(30))
    to_condition: Mapped[str | None] = mapped_column(String(30))
    event_code: Mapped[str | None] = mapped_column(String(20), index=True)
    request_code: Mapped[str | None] = mapped_column(String(20), index=True)
    transfer_id: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    actor_name: Mapped[str] = mapped_column(String(120))
    reason: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class MerchItem(FestivalScoped, Base):
    """One merchandise SKU + size row from inventory_merch.csv."""
    __tablename__ = "merch_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40))
    item: Mapped[str] = mapped_column(String(80))
    size: Mapped[str] = mapped_column(String(16))  # S | M | L | XL | XXL | NA
    on_hand: Mapped[int] = mapped_column(Integer)
    csv_reserved: Mapped[int] = mapped_column(Integer, server_default="0")
    csv_reserved_note: Mapped[str | None] = mapped_column(String(240))
    csv_reserved_annotated_by: Mapped[str | None] = mapped_column(String(120))
    csv_reserved_annotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    plan_reserved: Mapped[int] = mapped_column(Integer, server_default="0")
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    entitlement_rule: Mapped[str] = mapped_column(String(40), server_default="manual_only")  # every_participant | participants_staff_leaders | manual_only
    entitlement_confirmed: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    entitlement_confirmed_by: Mapped[str | None] = mapped_column(String(120))
    entitlement_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    max_per_person: Mapped[int] = mapped_column(Integer, server_default="1")
    __table_args__ = (
        UniqueConstraint("festival_id", "item", "size", name="uq_merch_item_size"),
        UniqueConstraint("festival_id", "code", name="uq_merch_item_code"),
        CheckConstraint("on_hand >= 0 AND csv_reserved >= 0 AND plan_reserved >= 0 AND on_hand >= csv_reserved + plan_reserved", name="merch_quantities_valid"),
    )


class MerchConfig(FestivalScoped, Base):
    """Festival-wide settings for Merchandise assumptions, kit template, staff quota, and active distribution plan."""
    __tablename__ = "merch_configs"
    id: Mapped[int] = mapped_column(primary_key=True)
    assumptions_confirmed: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    kit_templates: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    kit_confirmed: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    kit_confirmed_by: Mapped[str | None] = mapped_column(String(120))
    kit_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    staff_leader_quota: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    staff_quota_set_by: Mapped[str | None] = mapped_column(String(120))
    staff_quota_set_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    substitution_rule: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default='{"max_steps": 1, "direction": "up_preferred"}')
    active_policy: Mapped[str | None] = mapped_column(String(20))  # P1 | P2 | P3 | None
    approved_plan: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    plan_approved_by: Mapped[str | None] = mapped_column(String(120))
    plan_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    plan_stale: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    plan_stale_reasons: Mapped[list[str]] = mapped_column(JSONB, server_default="[]")
    tray_prep_status: Mapped[dict[str, str]] = mapped_column(JSONB, server_default="{}")
    __table_args__ = (UniqueConstraint("festival_id"),)


class MerchIssue(FestivalScoped, Base):
    """One merchandise issue to a participant (or voided record)."""
    __tablename__ = "merch_issues"
    id: Mapped[int] = mapped_column(primary_key=True)
    participant_id: Mapped[int] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"), index=True)
    merch_item_id: Mapped[int] = mapped_column(ForeignKey("merch_items.id"), index=True)
    item: Mapped[str] = mapped_column(String(80), index=True)
    requested_size: Mapped[str] = mapped_column(String(16))
    issued_size: Mapped[str] = mapped_column(String(16))
    is_substitution: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    consent_recorded: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    qty: Mapped[int] = mapped_column(Integer, server_default="1")
    desk: Mapped[str | None] = mapped_column(String(60))
    issued_by: Mapped[str] = mapped_column(String(120))
    issued_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    verification_method: Mapped[str] = mapped_column(String(30))  # id_lookup | qr_scan | phone_lookup | name_select | paper_reconciled
    status: Mapped[str] = mapped_column(String(20), server_default="issued")  # issued | voided
    voided_by: Mapped[str | None] = mapped_column(String(120))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    void_reason: Mapped[str | None] = mapped_column(Text)
    event_code: Mapped[str | None] = mapped_column(String(20))
    position: Mapped[str | None] = mapped_column(String(40))
    badge_category: Mapped[str | None] = mapped_column(String(60))
    override_reason: Mapped[str | None] = mapped_column(Text)
    override_approved_by: Mapped[str | None] = mapped_column(String(120))
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True)
    __table_args__ = (
        CheckConstraint("qty > 0", name="merch_issue_qty_valid"),
        Index("uq_merch_issue_once_per_person", "participant_id", "item", unique=True,
              postgresql_where=text("status = 'issued' AND item <> 'winner medal' AND override_reason IS NULL")),
        Index("uq_merch_issue_medal", "participant_id", "item", "event_code", "position", unique=True,
              postgresql_where=text("status = 'issued' AND item = 'winner medal' AND override_reason IS NULL")),
    )


class MerchOwed(FestivalScoped, Base):
    """Owed item record when size is short, out of stock, or substitution consent was declined."""
    __tablename__ = "merch_owed"
    id: Mapped[int] = mapped_column(primary_key=True)
    participant_id: Mapped[int] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"), index=True)
    item: Mapped[str] = mapped_column(String(80), index=True)
    requested_size: Mapped[str] = mapped_column(String(16))
    proposed_substitute_size: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(24), server_default="owed")  # owed | substitute_offered | collected
    reason: Mapped[str] = mapped_column(String(40), server_default="size_short")  # size_short | no_stock | consent_declined
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(120))
    __table_args__ = (
        Index("uq_merch_owed_active", "festival_id", "participant_id", "item", unique=True,
              postgresql_where=text("status <> 'collected'")),
    )


class MerchIncoming(FestivalScoped, Base):
    """Expected incoming merchandise stock delivery."""
    __tablename__ = "merch_incoming"
    id: Mapped[int] = mapped_column(primary_key=True)
    merch_item_id: Mapped[int] = mapped_column(ForeignKey("merch_items.id", ondelete="CASCADE"), index=True)
    item: Mapped[str] = mapped_column(String(80))
    size: Mapped[str] = mapped_column(String(16))
    expected_qty: Mapped[int] = mapped_column(Integer)
    expected_date: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), server_default="expected")  # expected | received | partially_received | cancelled
    qty_received: Mapped[int] = mapped_column(Integer, server_default="0")
    discrepancy_qty: Mapped[int] = mapped_column(Integer, server_default="0")
    discrepancy_note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(120))
    received_by: Mapped[str | None] = mapped_column(String(120))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("expected_qty > 0 AND qty_received >= 0", name="merch_incoming_qty_valid"),)


class MerchLedger(Base):
    """Append-only merchandise ledger: DB trigger rejects UPDATE and DELETE."""
    __tablename__ = "merch_ledger"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    festival_id: Mapped[int] = mapped_column(ForeignKey("festivals.id", ondelete="CASCADE"), index=True)
    merch_item_id: Mapped[int | None] = mapped_column(ForeignKey("merch_items.id", ondelete="SET NULL"), index=True)
    action: Mapped[str] = mapped_column(String(40), index=True)
    item: Mapped[str] = mapped_column(String(80), index=True)
    size: Mapped[str] = mapped_column(String(16))
    qty_delta: Mapped[int] = mapped_column(Integer)
    participant_code: Mapped[str | None] = mapped_column(String(20), index=True)
    participant_name: Mapped[str | None] = mapped_column(String(120))
    desk: Mapped[str | None] = mapped_column(String(60))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    actor_name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(24))
    reason: Mapped[str | None] = mapped_column(Text)
    is_exception: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


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
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


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

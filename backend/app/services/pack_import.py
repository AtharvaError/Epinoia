"""Loads the data pack into PostgreSQL. Validates and flags; never drops or merges."""
import csv
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.verifier import hash_password
from app.config import ROOT, settings
from app.models import (
    AuditLog, Contingent, DataIssue, Department, EquipmentAlias, Event, Festival, Hostel,
    InventoryLocation, MerchConfig, MerchItem, Participant, Registration, Resource,
    Role, Room, Team, User, Vendor, Venue, Volunteer,
)
from app.services.schedule_plan import PlanEvent, PlanVenue, build_schedule, event_length

PACK = ROOT / "festos_dataset_pack"
IST = timezone(timedelta(hours=5, minutes=30))
DEPARTMENTS = ["Direction", "Events", "Hospitality", "Logistics", "Food & Vendors", "Merchandise", "Registration",
               "Transport", "Volunteers", "Finance", "Safety"]
ROLE_MAP = {  # pack role -> (app role, department). See ASSUMPTIONS #8.
    "Festival Director": ("festival_director", "Direction"),
    "Events Team": ("event_coordinator", "Events"),
    "Hospitality": ("accommodation_head", "Hospitality"),
    "Logistics Head": ("transport_head", "Logistics"),
    "Food Coordinator": ("volunteer", "Food & Vendors"),
    "Merchandise Desk": ("volunteer", "Merchandise"),
    "Registration Desk": ("volunteer", "Registration"),
    "Volunteer": ("volunteer", "Volunteers"),
}
SIMULATED_USERS = [
    ("admin@festos.test", "Demo Super Admin", "super_admin", None),
    ("finance@festos.test", "Demo Finance Head", "finance_head", "Finance"),
    ("safety@festos.test", "Demo Safety Coordinator", "safety_coordinator", "Safety"),
    ("volunteers@festos.test", "Demo Volunteer Head", "volunteer_head", "Volunteers"),
    ("participant@festos.test", "Demo Participant", "participant", None),
]

DEFAULT_ALIASES = [
    ("barricades", "barricade"),
    ("tables", "table"),
    ("projector", "projector"),
    ("extension cords", "extension cord"),
    ("camera", "camera"),
    ("tripod", "tripod"),
    ("mics", "mic"),
    ("speaker", "speaker"),
]

DEFAULT_KIT_TEMPLATES = {
    "participant": [
        {"item": "T-shirt", "qty": 1, "size": "declared"},
        {"item": "ID card", "qty": 1, "size": "NA"},
        {"item": "lanyard", "qty": 1, "size": "NA"},
    ],
    "core_team": [
        {"item": "T-shirt", "qty": 1, "size": "capture_at_issue"},
        {"item": "ID card", "qty": 1, "size": "NA"},
        {"item": "lanyard", "qty": 1, "size": "NA"},
    ],
    "leader_escort": [
        {"item": "ID card", "qty": 1, "size": "NA"},
        {"item": "lanyard", "qty": 1, "size": "NA"},
    ],
}


def read(name: str) -> list[dict[str, str]]:
    with Path(PACK, name).open(newline="", encoding="utf-8") as fh:
        return [{k: v.strip() for k, v in row.items()} for row in csv.DictReader(fh)]


def blank(value: str) -> str | None:
    return value or None


def timestamp(value: str) -> datetime | None:
    return datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=IST) if value else None


def parse_windows(value: str) -> list[dict[str, str]]:
    windows = []
    for part in value.split(";"):
        day, hours = part.strip().split(" ")
        start, end = hours.split("-")
        windows.append({"start": f"{day}T{start}:00+05:30", "end": f"{day}T{end}:00+05:30"})
    return windows


def ensure_pack_extensions(db: Session, fid: int) -> None:
    """Idempotently ensures inventory locations, aliases, merch items, merch config, and P_DUP flags exist."""
    venues_by_name = {v.name: v for v in db.scalars(select(Venue).where(Venue.festival_id == fid))}
    stock_locs = sorted({r["location"] for r in read("inventory_logistics.csv") if r.get("location")})
    existing_locs = {loc.name: loc for loc in db.scalars(select(InventoryLocation).where(InventoryLocation.festival_id == fid))}

    for loc_name in stock_locs:
        if loc_name in existing_locs:
            continue
        v = venues_by_name.get(loc_name)
        db.add(InventoryLocation(
            festival_id=fid,
            name=loc_name,
            kind="venue_and_stock" if v else "stock_only",
            venue_id=v.id if v else None,
            venue_code=v.code if v else None,
            matched_by_name=v is not None,
            venue_confirmed=False,
            position_unverified=(loc_name == "Van 1"),
            position_note="Currently near Gate 2 loading bay (unverified)" if loc_name == "Van 1" else None,
        ))
    for v_name, v in venues_by_name.items():
        if v_name not in existing_locs and v_name not in stock_locs:
            db.add(InventoryLocation(
                festival_id=fid,
                name=v_name,
                kind="venue_only",
                venue_id=v.id,
                venue_code=v.code,
                matched_by_name=False,
                venue_confirmed=True,
                position_unverified=False,
            ))

    existing_aliases = {a.token for a in db.scalars(select(EquipmentAlias).where(EquipmentAlias.festival_id == fid))}
    for token, target in DEFAULT_ALIASES:
        if token not in existing_aliases:
            db.add(EquipmentAlias(
                festival_id=fid,
                token=token,
                target_item=target,
                verified=True,
                source="pack",
            ))

    existing_merch = {(m.item, m.size): m for m in db.scalars(select(MerchItem).where(MerchItem.festival_id == fid))}
    for r in read("inventory_merch.csv"):
        item_name = r["item"]
        size_val = r["size"] or "NA"
        if (item_name, size_val) in existing_merch:
            continue
        is_tshirt = item_name.lower() == "t-shirt"
        db.add(MerchItem(
            festival_id=fid,
            code=f"{item_name}-{size_val}",
            item=item_name,
            size=size_val,
            on_hand=int(r["quantity"]),
            csv_reserved=int(r["reserved_quantity"]),
            plan_reserved=0,
            entitlement_rule="every_participant" if is_tshirt else "manual_only",
            entitlement_confirmed=False,
            max_per_person=10 if item_name.lower() == "winner medal" else 1,
        ))

    existing_cfg = db.scalar(select(MerchConfig).where(MerchConfig.festival_id == fid))
    if not existing_cfg:
        db.add(MerchConfig(
            festival_id=fid,
            assumptions_confirmed={
                "A1": {"confirmed": False, "by": None, "at": None, "text": "quantity is total on hand INCLUDING reserved, so issuable = quantity minus reserved."},
                "A2": {"confirmed": False, "by": None, "at": None, "text": "Every non-duplicate participant is entitled to one T-shirt in their declared size."},
                "A3": {"confirmed": False, "by": None, "at": None, "text": "One unit per participant per item, except winner medals."},
                "A4": {"confirmed": False, "by": None, "at": None, "text": "Merch stock is one pool: inventory_merch.csv has no location column."},
            },
            kit_templates=DEFAULT_KIT_TEMPLATES,
            kit_confirmed=False,
            staff_leader_quota=None,
            substitution_rule={"max_steps": 1, "direction": "up_preferred"},
            active_policy=None,
            approved_plan=None,
            plan_stale=False,
            plan_stale_reasons=[],
            tray_prep_status={},
        ))

    # Ensure P_DUP01 and P_DUP02 have probable_duplicate DataIssues
    dup_issues = {
        i.entity_code
        for i in db.scalars(
            select(DataIssue).where(
                DataIssue.festival_id == fid,
                DataIssue.entity_type == "participant",
                DataIssue.issue == "possible_duplicate",
            )
        )
    }
    if "P_DUP01" not in dup_issues and db.scalar(select(Participant.id).where(Participant.festival_id == fid, Participant.code == "P_DUP01")):
        db.add(DataIssue(
            festival_id=fid,
            entity_type="participant",
            entity_code="P_DUP01",
            issue="possible_duplicate",
            detail="Probable duplicate of P0021 (Tanvi Das, IIM Kozhikode, age 25, size M, same arrival)",
            status="open",
        ))
    if "P_DUP02" not in dup_issues and db.scalar(select(Participant.id).where(Participant.festival_id == fid, Participant.code == "P_DUP02")):
        db.add(DataIssue(
            festival_id=fid,
            entity_type="participant",
            entity_code="P_DUP02",
            issue="possible_duplicate",
            detail="Same name, contingent, age, size and arrival as P0119",
            status="open",
        ))
    db.flush()


def load_pack(db: Session) -> Festival:
    existing_fest = db.scalar(select(Festival).where(Festival.slug == "aarohan-2026"))
    if existing_fest:
        ensure_pack_extensions(db, existing_fest.id)
        return existing_fest

    festival = Festival(slug="aarohan-2026", name="Aarohan 2026", start_date=date(2026, 10, 30),
                        end_date=date(2026, 11, 1))
    db.add(festival)
    db.flush()
    fid = festival.id
    issues: list[DataIssue] = []

    def flag(entity_type: str, code: str, issue: str, detail: str = "") -> None:
        issues.append(DataIssue(festival_id=fid, entity_type=entity_type, entity_code=code, issue=issue, detail=detail))

    departments = {name: Department(festival_id=fid, name=name) for name in DEPARTMENTS}
    db.add_all(departments.values())
    db.flush()

    _load_users(db, fid, departments)

    contingents = {}
    for r in read("contingents.csv"):
        contingents[r["contingent_id"]] = Contingent(
            festival_id=fid, code=r["contingent_id"], college=r["college"], leader=blank(r["leader"]),
            escort=blank(r["escort"]), preferences=blank(r["preferences"]),
            strength_declared=int(r["strength_declared"]) if r["strength_declared"].isdigit() else None)
    db.add_all(contingents.values())
    db.flush()

    participants = {}
    seen_people = defaultdict(list)
    for r in read("participants.csv"):
        code = r["participant_id"]
        arrival, departure = timestamp(r["arrival_datetime"]), timestamp(r["departure_datetime"])
        participants[code] = Participant(
            festival_id=fid, code=code, full_name=r["name"], phone=blank(r["contact"]), college=r["college"],
            contingent_id=contingents[r["contingent_id"]].id, gender_as_given=blank(r["gender_as_given"]),
            age=int(r["age"]) if r["age"].isdigit() else None, accessibility_needs=blank(r["accessibility_needs"]),
            dietary_needs=blank(r["dietary_needs"]), arrival_at=arrival, departure_at=departure,
            accommodation_required={"yes": True, "no": False}.get(r["accommodation_requested"].lower()),
            tshirt_size=blank(r["tshirt_size"]))
        if not r["gender_as_given"]:
            flag("participant", code, "missing_gender", "Blank is not a gender")
        if not r["dietary_needs"]:
            flag("participant", code, "missing_dietary", "Blank is not dietary clearance")
        if arrival and departure and departure < arrival:
            flag("participant", code, "departure_before_arrival", f"{r['arrival_datetime']} to {r['departure_datetime']}")
        if not re.fullmatch(r"\+\d{2}-\d{10}", r["contact"]):
            flag("participant", code, "phone_format", "Phone number is not in the usual format")
        seen_people[(r["name"].lower(), r["contingent_id"], r["age"], r["tshirt_size"], r["arrival_datetime"])].append(code)
    for key, codes in seen_people.items():
        if len(codes) > 1:
            for code in codes:
                flag("participant", code, "possible_duplicate", f"Same name, contingent, age, size and arrival as {', '.join(c for c in codes if c != code)}")
    db.add_all(participants.values())
    db.flush()

    actual = Counter(r["contingent_id"] for r in read("participants.csv"))
    for code, c in contingents.items():
        if c.strength_declared is not None and c.strength_declared != actual[code]:
            flag("contingent", code, "strength_mismatch", f"Declared {c.strength_declared}, found {actual[code]}")

    venue_rows = read("venues.csv")
    venues = {r["venue_id"]: Venue(festival_id=fid, code=r["venue_id"], name=r["venue_name"], capacity=int(r["capacity"]),
                                   indoor_outdoor=blank(r["indoor_outdoor"]), availability=parse_windows(r["availability_windows"]))
              for r in venue_rows}
    db.add_all(venues.values())
    db.flush()

    event_rows = read("events.csv")
    registration_rows = read("registrations.csv")
    by_event: dict[str, set[str]] = defaultdict(set)
    for r in registration_rows:
        by_event[r["event_id"]].add(r["participant_id"])

    events = {}
    for r in event_rows:
        events[r["event_id"]] = Event(
            festival_id=fid, code=r["event_id"], name=r["event_name"], participation_type=r["type"],
            rounds=r["rounds"].split("|"), duration_minutes=int(r["duration_minutes"]),
            venue_requirements=blank(r["venue_requirements"]), equipment_needs=blank(r["equipment_needs"]),
            volunteers_needed=int(r["volunteers_needed"]))

    def stays_for(codes: set[str]) -> tuple[tuple[datetime, datetime], ...]:
        people = (participants[c] for c in codes)
        return tuple((p.arrival_at, p.departure_at) for p in people
                     if p.arrival_at and p.departure_at and p.departure_at >= p.arrival_at)

    schedule = build_schedule(
        [PlanEvent(e.code, e.venue_requirements or "", event_length(len(e.rounds), e.duration_minutes),
                   frozenset(by_event[e.code]), stays_for(by_event[e.code])) for e in events.values()],
        [PlanVenue(v.code, v.name, v.capacity, tuple((datetime.fromisoformat(w["start"]), datetime.fromisoformat(w["end"]))
                                                     for w in v.availability)) for v in venues.values()],
    )
    for slot in schedule:
        event = events[slot.event.code]
        event.start_at, event.end_at, event.schedule_source = slot.start, slot.end, "generated"
        event.venue_id = venues[slot.venue.code].id
    db.add_all(events.values())
    db.flush()

    teams = {}
    for r in registration_rows:
        code = r["team_id"]
        if code and code not in teams:
            teams[code] = Team(festival_id=fid, code=code, event_id=events[r["event_id"]].id,
                               contingent_id=participants[r["participant_id"]].contingent_id)
    db.add_all(teams.values())
    db.flush()

    registrations = []
    for i, r in enumerate(registration_rows, 2):
        event = events[r["event_id"]]
        team = teams.get(r["team_id"])
        registrations.append(Registration(festival_id=fid, participant_id=participants[r["participant_id"]].id,
                                          event_id=event.id, team_id=team.id if team else None,
                                          team_code_raw=blank(r["team_id"])))
        if r["team_id"] and event.participation_type == "solo":
            flag("registration", f"line {i}", "team_on_solo_event", f"{r['participant_id']} in {r['event_id']} has team {r['team_id']}")
        if not r["team_id"] and event.participation_type == "team":
            flag("registration", f"line {i}", "team_event_without_team", f"{r['participant_id']} in {r['event_id']}")
    db.add_all(registrations)

    _load_accommodation(db, fid)

    seen_vendors: set[str] = set()
    for r in read("vendors.csv"):
        vid = r["vendor_id"]
        if vid in seen_vendors:
            continue
        seen_vendors.add(vid)
        expiry = date.fromisoformat(r["hygiene_certificate_expiry"]) if r["hygiene_certificate_expiry"] else None
        db.add(Vendor(festival_id=fid, code=vid, name=r["vendor_name"], menu=blank(r["menu"]),
                      dietary_options=[x.strip() for x in r["dietary_options"].split(";") if x.strip()],
                      hygiene_certificate_expiry=expiry, hours=blank(r["hours"]),
                      capacity=int(r["capacity"]) if r["capacity"].isdigit() else None,
                      payment_terms=blank(r["payment_terms"]),
                      source="pack", original_pack_data=dict(r), operational_status="unconfirmed"))
        if expiry is None:
            flag("vendor", vid, "hygiene_cert_missing")
        elif expiry < festival.start_date:
            flag("vendor", vid, "hygiene_cert_expired", r["hygiene_certificate_expiry"])
        elif expiry <= festival.end_date:
            flag("vendor", vid, "hygiene_cert_expires_during_fest", r["hygiene_certificate_expiry"])

    for r in read("inventory_logistics.csv"):
        db.add(Resource(festival_id=fid, code=r["item_id"], kind="logistics", name=r["item"], quantity=int(r["quantity"]),
                        location=blank(r["location"]), condition=blank(r["condition"])))
        if r["condition"].lower() != "good":
            flag("resource", r["item_id"], "not_deployable", r["condition"])
    for r in read("inventory_merch.csv"):
        db.add(Resource(festival_id=fid, code=f"{r['item']}-{r['size']}", kind="merch", name=r["item"], size=blank(r["size"]),
                        quantity=int(r["quantity"]), reserved_quantity=int(r["reserved_quantity"])))

    db.add_all(issues)
    db.flush()
    ensure_pack_extensions(db, fid)
    counts = Counter(i.issue for i in issues)
    summaries = [
        ("import.participants", f"Imported {len(participants):,} participants from the data pack"),
        ("import.registrations", f"Imported {len(registrations):,} event registrations"),
        ("schedule.generated", f"Generated a schedule for {len(events)} events across {len(venues)} venues"),
        ("import.flags", f"Flagged {len(issues):,} data issues for review, {counts['team_on_solo_event'] + counts['team_event_without_team']:,} of them team data"),
    ]
    db.add_all(AuditLog(festival_id=fid, actor_label="Data import", action=a, entity="festival", entity_id=str(fid), summary=s)
               for a, s in summaries)
    return festival


def _load_users(db: Session, fid: int, departments: dict[str, Department]) -> None:
    roles = {r.key: r for r in db.query(Role).all()}
    hashed = hash_password(settings.demo_password)
    for r in read("core_team.csv"):
        role_key, department = ROLE_MAP[r["role"]]
        db.add(User(email=f"{r['member_id'].lower()}@festos.test", password_hash=hashed, name=r["name"], phone=blank(r["phone"]),
                    member_code=r["member_id"], shift=blank(r["shift"]), role_id=roles[role_key].id, festival_id=fid,
                    department_id=departments[department].id))
        if r["role"] == "Volunteer":
            db.add(Volunteer(festival_id=fid, code=r["member_id"], name=r["name"], phone=blank(r["phone"]),
                             department_id=departments[department].id))
    for email, name, role_key, department in SIMULATED_USERS:
        db.add(User(email=email, password_hash=hashed, name=name, role_id=roles[role_key].id, is_simulated=True,
                    festival_id=None if role_key == "super_admin" else fid,
                    department_id=departments[department].id if department else None))
    db.flush()


def _load_accommodation(db: Session, fid: int) -> None:
    room_rows = read("hostels.csv")
    blocks = {code: Hostel(festival_id=fid, code=code, name=f"Block {code}", kind="on_campus")
              for code in sorted({r["block"] for r in room_rows})}
    external = read("external_stays.csv")
    properties = {r["property_id"]: Hostel(
        festival_id=fid, code=r["property_id"], name=r["property_name"], kind="external",
        cost_per_bed_night=r["cost_per_bed_per_night"] or None, distance_km=r["distance_km"] or None,
        transport_available={"yes": True, "no": False}.get(r["transport_availability"].lower())) for r in external}
    db.add_all([*blocks.values(), *properties.values()])
    db.flush()
    for r in room_rows:
        db.add(Room(festival_id=fid, hostel_id=blocks[r["block"]].id, code=r["room_id"], floor=int(r["floor"]),
                    gender_policy=blank(r["gender"]), capacity=int(r["capacity"]),
                    accessible={"yes": True, "no": False}.get(r["accessibility"].lower()), status=r["status"] or "unknown"))
    for r in external:
        db.add(Room(festival_id=fid, hostel_id=properties[r["property_id"]].id, code=f"{r['property_id']}-POOL", capacity=int(r["beds"])))

"""Demo-only records for areas the pack does not cover. Every row has is_simulated=True."""
import random
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Announcement, AuditLog, Budget, Department, Event, Expense, Incident, Participant, Route, Sponsor, Task,
    TransportAssignment, Vehicle, Volunteer, VolunteerShift,
)
from app.services.pack_import import IST

VOLUNTEER_COUNT = 120
BUDGETS = {"Direction": 200000, "Events": 650000, "Hospitality": 900000, "Logistics": 350000, "Food & Vendors": 500000,
           "Merchandise": 300000, "Registration": 80000, "Transport": 400000, "Volunteers": 120000, "Finance": 40000,
           "Safety": 160000}
EXPENSES = [
    ("Events", "Production", "Stage and lighting hire"), ("Events", "Prizes", "Competition prizes"),
    ("Events", "Equipment", "Audio equipment rental"), ("Hospitality", "Lodging", "External stay advance"),
    ("Hospitality", "Housekeeping", "Hostel cleaning contract"), ("Logistics", "Equipment", "Barricades and tables"),
    ("Logistics", "Power", "Generator hire"), ("Food & Vendors", "Catering", "Vendor minimum guarantees"),
    ("Food & Vendors", "Compliance", "Water testing"), ("Merchandise", "Merchandise", "T-shirt printing"),
    ("Registration", "Printing", "Passes and lanyards"), ("Transport", "Fleet", "Bus hire"),
    ("Transport", "Fleet", "Fuel"), ("Volunteers", "Welfare", "Volunteer meals"),
    ("Safety", "Medical", "First aid post"), ("Safety", "Security", "Night security cover"),
    ("Direction", "Administration", "Permissions and licences"), ("Finance", "Administration", "Payment charges"),
]
SPONSORS = [("Simulated Title Sponsor", "Title", 800000, 400000), ("Simulated Beverage Partner", "Gold", 350000, 350000),
            ("Simulated Apparel Partner", "Gold", 300000, 150000), ("Simulated Fintech Partner", "Silver", 150000, 0),
            ("Simulated Food Partner", "Silver", 120000, 60000)]
INCIDENTS = [
    ("Medical", "Sports Complex", "Participant reported dizziness during warm-up.", "high", "in_progress", "Medical Team"),
    ("Power", "LT-2", "Projector circuit tripped twice.", "medium", "assigned", "Logistics"),
    ("Crowd", "Amphitheatre", "Queue building at the east gate.", "medium", "acknowledged", None),
    ("Lost property", "Registration Desk", "Lost ID card reported.", "low", "reported", None),
    ("Food safety", "Food Court", "Complaint about a dish served at the wrong temperature.", "high", "reported", None),
    ("Facilities", "Block B2", "Water supply interrupted on the first floor.", "medium", "resolved", "Hospitality"),
]
CAMPUS = (22.6203, 75.7992)
ROUTES = [  # Stops with no known location have no coordinates, so the map omits them.
    ("Rail shuttle", "Indore Junction", "Campus Main Gate", 30, (22.7176, 75.8682)),
    ("Airport shuttle", "Indore Airport", "Campus Main Gate", 45, (22.7218, 75.8011)),
    ("Campus Guest House loop", "Campus Guest House", "Campus Main Gate", 10, None),
    ("Central Residency loop", "Central Residency", "Campus Main Gate", 25, None),
]
TASKS = ["Confirm vendor roster", "Print signage", "Brief shift leads", "Check equipment list", "Update contact sheet",
         "Walk the venue", "Reconcile desk stock", "Publish duty chart"]
FIRST = ["Asha", "Dev", "Isha", "Kabir", "Lata", "Mihir", "Neha", "Omkar", "Pooja", "Rohan", "Sana", "Tarun"]
LAST = ["Agarwal", "Bhatt", "Chauhan", "Desai", "Kulkarni", "Pandey", "Shukla", "Yadav"]


def load_simulated(db: Session, festival_id: int) -> None:
    rng = random.Random(2026)
    fid = festival_id
    departments = {d.name: d for d in db.scalars(select(Department).where(Department.festival_id == fid))}
    events = list(db.scalars(select(Event).where(Event.festival_id == fid).order_by(Event.start_at)))

    for name, amount in BUDGETS.items():
        db.add(Budget(festival_id=fid, department_id=departments[name].id, approved_amount=amount, is_simulated=True))
    for department, category, description in EXPENSES:
        estimate = rng.randrange(20, 240) * 1000
        state = rng.choices(["approved", "pending", "rejected"], [6, 3, 1])[0]
        paid = state == "approved" and rng.random() < 0.6
        db.add(Expense(festival_id=fid, department_id=departments[department].id, category=category, description=description,
                       estimated_amount=estimate, actual_amount=round(estimate * rng.uniform(0.85, 1.2), -2) if paid else None,
                       approval_status=state, payment_status="paid" if paid else "unpaid", is_simulated=True))
    for name, tier, committed, received in SPONSORS:
        db.add(Sponsor(festival_id=fid, name=name, tier=tier, committed_amount=committed, received_amount=received, is_simulated=True))

    volunteer_departments = ["Events", "Hospitality", "Logistics", "Registration", "Food & Vendors", "Safety", "Transport"]
    skills = ["crowd control", "AV", "first aid", "registration", "stage", "logistics", "hospitality"]
    volunteers = [Volunteer(festival_id=fid, code=f"VOL{i:03d}", name=f"{rng.choice(FIRST)} {rng.choice(LAST)}",
                            department_id=departments[rng.choice(volunteer_departments)].id,
                            skills=rng.sample(skills, 2), is_simulated=True) for i in range(1, VOLUNTEER_COUNT + 1)]
    db.add_all(volunteers)
    db.flush()

    busy: dict[int, list[tuple[datetime, datetime]]] = {v.id: [] for v in volunteers}
    for event in events:
        wanted = round(event.volunteers_needed * rng.uniform(0.6, 1.0))
        start, end = event.start_at - timedelta(minutes=30), event.end_at + timedelta(minutes=30)
        free = [v for v in volunteers if all(end <= s or e <= start for s, e in busy[v.id])]
        for v in rng.sample(free, min(wanted, len(free))):
            busy[v.id].append((start, end))
            db.add(VolunteerShift(festival_id=fid, volunteer_id=v.id, event_id=event.id, start_at=start, end_at=end,
                                  task=f"{event.name} duty", is_simulated=True))

    for i, (department, d) in enumerate(departments.items()):
        for title in rng.sample(TASKS, 5):
            db.add(Task(festival_id=fid, department_id=d.id, title=title, is_simulated=True,
                        status=rng.choices(["todo", "in_progress", "done"], [3, 3, 4])[0]))

    vehicles = [Vehicle(festival_id=fid, code=f"BUS{i}", vehicle_type="Bus", capacity=40, is_simulated=True) for i in range(1, 5)]
    vehicles.append(Vehicle(festival_id=fid, code="VAN1", vehicle_type="Accessible van", capacity=8, is_simulated=True))
    db.add_all(vehicles)
    db.flush()

    participants = list(db.scalars(select(Participant).where(Participant.festival_id == fid, Participant.arrival_at.is_not(None))
                                   .order_by(Participant.arrival_at, Participant.code)))
    first_day = datetime(2026, 10, 29, 8, 0, tzinfo=IST)
    trip = 0
    busiest: tuple[int, Route | None] = (0, None)
    for hours in range(0, 60, 6):
        name, pickup, destination, minutes, stop = ROUTES[trip % len(ROUTES)]
        vehicle = vehicles[trip % len(vehicles)]
        departure = first_day + timedelta(hours=hours)
        arrival = departure + timedelta(minutes=minutes)
        trip += 1
        route = Route(festival_id=fid, code=f"TRIP{trip:02d}", name=name, pickup=pickup, destination=destination,
                      vehicle_id=vehicle.id, departure_at=departure, expected_arrival_at=arrival, is_simulated=True,
                      pickup_lat=stop[0] if stop else None, pickup_lng=stop[1] if stop else None,
                      destination_lat=CAMPUS[0] if stop else None, destination_lng=CAMPUS[1] if stop else None)
        db.add(route)
        db.flush()
        riders = [p for p in participants if timedelta(0) <= p.arrival_at - departure < timedelta(hours=6)][:vehicle.capacity]
        for p in riders:
            db.add(TransportAssignment(festival_id=fid, route_id=route.id, participant_id=p.id, is_simulated=True))
        if len(riders) > busiest[0]:
            busiest = (len(riders), route)
    if busiest[1]:
        busiest[1].status = "delayed"
        busiest[1].delay_minutes = 40
        busiest[1].expected_arrival_at += timedelta(minutes=40)

    for i, (category, location, description, priority, status, team) in enumerate(INCIDENTS, 1):
        db.add(Incident(festival_id=fid, code=f"INC{i:03d}", category=category, location=location, description=description,
                        priority=priority, status=status, assigned_team=team, reported_by="Simulated reporter", is_simulated=True))
    db.add(Announcement(festival_id=fid, title="Simulated: briefing at 07:30", body="All shift leads meet at the Registration Desk.", is_simulated=True))
    db.add(AuditLog(festival_id=fid, actor_label="Demo data", action="import.simulated", entity="festival", entity_id=str(fid),
                    summary="Loaded simulated records for transport, volunteers, finance, tasks and incidents"))

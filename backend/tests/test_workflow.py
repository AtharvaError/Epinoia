"""The fourteen-step workflow from the product brief, run from an empty festival through the API."""
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.auth import failures
from app.database import SessionLocal
from app.main import app
from tests.helpers import client_for

DIRECTOR_PASSWORD = "director-password-1"
CSV_ROWS = "full_name,college,gender_as_given,age,accommodation_required,arrival_at,departure_at\n" + "".join(
    f"Guest {i},Workflow College,{'Female' if i % 2 else 'Male'},20,true,2027-02-01T08:00:00+05:30,2027-02-03T18:00:00+05:30\n" for i in range(1, 13))


def scalar(sql, **params):
    with SessionLocal() as db:
        return db.execute(text(sql), params).scalar_one()


def test_complete_operations_workflow():
    admin = client_for("admin@festos.test")

    # 1. An administrator creates a festival.
    created = admin.post("/api/festivals", json={"name": "Workflow Fest", "start_date": "2027-02-01", "end_date": "2027-02-03"})
    assert created.status_code == 201
    fid = created.json()["id"]
    base = f"/api/festivals/{fid}"
    empty = admin.get(f"{base}/dashboard")
    assert empty.status_code == 200, "A brand new festival must not crash the dashboard"

    director = admin.post(f"{base}/users", json={"email": "wf.director@festos.test", "name": "Workflow Director", "role_key": "festival_director", "password": DIRECTOR_PASSWORD})
    assert director.status_code == 201
    head = admin.post(f"{base}/users", json={"email": "wf.head@festos.test", "name": "Workflow Head", "role_key": "accommodation_head", "password": DIRECTOR_PASSWORD})
    assert head.status_code == 201

    # 2. Participants and operational data are imported.
    imported = admin.post(f"{base}/participants/import", files={"file": ("people.csv", CSV_ROWS)}).json()
    assert imported == {"created": 12, "rejected": []}

    # 3. Events, venues, hostels and volunteers are configured.
    venue = admin.post(f"{base}/venues", json={"name": "Small Hall", "capacity": 5}).json()["code"]
    assert admin.post(f"{base}/venues", json={"name": "Big Hall", "capacity": 50}).status_code == 201
    event = {"name": "Opening Talk", "participation_type": "solo", "start_at": "2027-02-01T10:00:00+05:30", "end_at": "2027-02-01T11:00:00+05:30", "venue_code": venue, "volunteers_needed": 1}
    event_code = admin.post(f"{base}/events", json=event).json()["code"]
    people = [p["code"] for p in admin.get(f"{base}/participants").json()["items"]]
    for code in people:
        assert admin.post(f"{base}/participants/{code}/registrations", json={"event_code": event_code}).status_code == 201
    hostel = admin.post(f"{base}/accommodation/hostels", json={"name": "North Block"}).json()["code"]
    annex = admin.post(f"{base}/accommodation/hostels", json={"name": "Annex", "kind": "external", "cost_per_bed_night": 500, "distance_km": 2, "transport_available": True}).json()["code"]
    for code, policy in (("N1", "boys"), ("N2", "girls")):
        assert admin.post(f"{base}/accommodation/rooms", json={"hostel_code": hostel, "code": code, "floor": 1, "gender_policy": policy, "capacity": 6, "accessible": False}).status_code == 201
    assert admin.post(f"{base}/accommodation/rooms", json={"hostel_code": annex, "code": "A-POOL", "capacity": 20}).status_code == 201
    assert admin.post(f"{base}/volunteers", json={"name": "Helper One", "department": "Events", "skills": ["AV"]}).status_code == 201
    assert admin.put(f"{base}/finance/budgets/Hospitality", json={"approved_amount": 0, "reason": "Starting budget"}).status_code == 200

    # 4. The dashboard calculates live statistics.
    kpis = {k["key"]: k for k in admin.get(f"{base}/dashboard").json()["kpis"]}
    assert kpis["participants"]["value"] == 12 and kpis["events"]["value"] == 1 and kpis["venues"]["value"] == 2 and kpis["beds"]["total"] == 32

    # 5. The system identifies a capacity issue.
    alerts = [a["title"] for a in admin.get(f"{base}/dashboard").json()["alerts"]]
    assert any("exceed venue capacity" in a for a in alerts)
    plan = admin.post(f"{base}/accommodation/plan", json={}).json()
    assert plan["summary"]["placed"] == 12
    assert admin.post(f"{base}/accommodation/plan/apply", json={"state_hash": plan["state_hash"]}).status_code == 200

    # 6 to 9. The organizer proposes a change; the simulator works out the consequences.
    head_client = login("wf.head@festos.test")
    fix = head_client.post(f"{base}/simulations", json={"scenario": "hostel_closure", "change": {"hostel_code": hostel, "extra_budget": 20000}})
    assert fix.status_code == 201, fix.text
    simulation = fix.json()
    report = simulation["report"]
    depth = {n["id"]: n["depth"] for n in report["graph"]["nodes"]}
    assert depth["accommodation"] == 0 and depth["participants"] == 1
    assert any(e["active"] for e in report["graph"]["edges"])
    assert report["actions"] and report["before_after"]
    assert scalar("SELECT status FROM hostels WHERE festival_id=:f AND code=:c", f=fid, c=hostel) == "available", "Nothing changes before approval"

    # 10. An authorized person reviews and approves it.
    assert head_client.post(f"{base}/simulations/{simulation['id']}/approve", json={}).status_code == 403
    assert head_client.post(f"{base}/simulations/{simulation['id']}/submit", json={"reason": "Water supply failed"}).status_code == 204
    director_client = login("wf.director@festos.test")
    notes = director_client.get(f"{base}/notifications").json()
    assert any(n["title"] == "Approval requested" for n in notes["items"])
    approved = director_client.post(f"{base}/simulations/{simulation['id']}/approve", json={"reason": "Confirmed by the warden"})

    # 11. The backend commits it safely.
    assert approved.status_code == 200, approved.text

    # 12. Connected modules update.
    assert scalar("SELECT status FROM hostels WHERE festival_id=:f AND code=:c", f=fid, c=hostel) == "closed"
    assert scalar("""SELECT count(*) FROM accommodation_allocations a JOIN rooms r ON r.id=a.room_id JOIN hostels h ON h.id=r.hostel_id
                     WHERE a.festival_id=:f AND a.status<>'cancelled' AND h.code=:c""", f=fid, c=annex) == 12
    after = {k["key"]: k for k in admin.get(f"{base}/dashboard").json()["kpis"]}
    assert after["beds"]["total"] == 20 and after["beds"]["value"] == 12
    finance = admin.get(f"{base}/finance/summary").json()
    assert float(next(x for x in finance["lines"] if x["department"] == "Hospitality")["approved"]) == 20000

    # 13. Relevant people are notified.
    head_notes = head_client.get(f"{base}/notifications").json()
    assert head_notes["unread"] >= 1 and any("North Block" in n["title"] for n in head_notes["items"])

    # 14. The whole operation is on the audit log, with its reason.
    audit = director_client.get(f"{base}/audit", params={"q": "applied"}).json()["items"]
    assert audit and audit[0]["reason"] == "Confirmed by the warden"


def login(email):
    failures.clear()
    client = TestClient(app)
    assert client.post("/api/auth/login", json={"email": email, "password": DIRECTOR_PASSWORD}).status_code == 200
    return client

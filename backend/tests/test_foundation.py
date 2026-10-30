"""Runs against the seeded database. Expected numbers are recomputed from the CSVs, not copied from the app."""
import csv
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.api.auth import failures
from app.auth.permissions import ROLES
from app.config import ROOT, settings
from app.database import SessionLocal
from app.main import app


def pack(name):
    with open(ROOT / "festos_dataset_pack" / name, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def client_for(email=None):
    client = TestClient(app)
    if email:
        assert client.post("/api/auth/login", json={"email": email, "password": settings.demo_password}).status_code == 200
    return client


@pytest.fixture(autouse=True)
def clean_limiter():
    failures.clear()


@pytest.fixture(scope="module")
def admin():
    return client_for("admin@festos.test")


@pytest.fixture(scope="module")
def fest(admin):
    return admin.get("/api/festivals").json()[0]["id"]


@pytest.fixture(scope="module")
def data(admin, fest):
    response = admin.get(f"/api/festivals/{fest}/dashboard")
    assert response.status_code == 200
    return response.json()


def kpi(data, key):
    return next(k for k in data["kpis"] if k["key"] == key)


def test_api_requires_login():
    client = client_for()
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/festivals").status_code == 401


def test_wrong_password_is_rejected_then_rate_limited():
    client = client_for()
    body = {"email": "admin@festos.test", "password": "wrong"}
    assert [client.post("/api/auth/login", json=body).status_code for _ in range(6)] == [401] * 5 + [429]


def test_forged_token_is_rejected():
    client = client_for()
    client.cookies.set("festos_session", "not-a-real-token")
    assert client.get("/api/auth/me").status_code == 401


def test_logout_ends_session():
    client = client_for("admin@festos.test")
    client.post("/api/auth/logout")
    client.cookies.clear()
    assert client.get("/api/auth/me").status_code == 401


def test_every_role_is_seeded_with_its_exact_permissions():
    with SessionLocal() as db:
        rows = db.execute(text("""SELECT r.key, p.key FROM roles r JOIN role_permissions rp ON rp.role_id=r.id
                                  JOIN permissions p ON p.id=rp.permission_id""")).all()
    seeded = {}
    for role, permission in rows:
        seeded.setdefault(role, set()).add(permission)
    assert seeded == {key: granted for key, (_, granted) in ROLES.items()}


def test_volunteer_cannot_read_dashboard(fest):
    assert client_for("st03@festos.test").get("/api/auth/me").json()["role"]["key"] == "event_coordinator"
    volunteer = next(r["member_id"] for r in pack("core_team.csv") if r["role"] == "Volunteer")
    client = client_for(f"{volunteer.lower()}@festos.test")
    assert client.get(f"/api/festivals/{fest}/dashboard").status_code == 403


def test_user_cannot_reach_another_festival():
    client = client_for("st03@festos.test")
    with SessionLocal() as db:
        other = db.execute(text("INSERT INTO festivals (slug,name,start_date,end_date) VALUES ('other-test','Other','2027-01-01','2027-01-02') RETURNING id")).scalar_one()
        db.commit()
    try:
        assert client.get(f"/api/festivals/{other}/dashboard").status_code == 403
    finally:
        with SessionLocal() as db:
            db.execute(text("DELETE FROM festivals WHERE id=:i"), {"i": other})
            db.commit()


def test_search_never_returns_contact_details(admin, fest):
    rows = pack("participants.csv")
    results = admin.get(f"/api/festivals/{fest}/search", params={"q": rows[0]["name"]}).json()
    assert results
    assert rows[0]["contact"] not in str(results)


def test_search_treats_wildcards_literally(admin, fest):
    assert admin.get(f"/api/festivals/{fest}/search", params={"q": "%%"}).json() == []


def test_audit_log_is_append_only():
    with SessionLocal() as db:
        with pytest.raises(DBAPIError, match="append-only"):
            db.execute(text("UPDATE audit_logs SET summary='tampered'"))
        db.rollback()
        with pytest.raises(DBAPIError, match="append-only"):
            db.execute(text("DELETE FROM audit_logs"))
        db.rollback()


def test_blanks_stay_null():
    blank_gender = sum(1 for r in pack("participants.csv") if not r["gender_as_given"].strip())
    with SessionLocal() as db:
        assert db.execute(text("SELECT count(*) FROM participants WHERE gender_as_given IS NULL")).scalar_one() == blank_gender
        assert db.execute(text("SELECT count(*) FROM participants WHERE email IS NOT NULL OR transport_required IS NOT NULL")).scalar_one() == 0


def test_kpis_match_the_pack(data):
    rooms, stays = pack("hostels.csv"), pack("external_stays.csv")
    assert kpi(data, "participants")["value"] == len(pack("participants.csv"))
    assert kpi(data, "events")["value"] == len(pack("events.csv"))
    assert kpi(data, "venues")["value"] == len(pack("venues.csv"))
    assert kpi(data, "beds")["total"] == sum(int(r["capacity"]) for r in rooms) + sum(int(r["beds"]) for r in stays)
    assert kpi(data, "teams")["value"] == len({r["team_id"] for r in pack("registrations.csv") if r["team_id"]})


def test_kpis_follow_the_database(admin, fest, data):
    before = kpi(data, "incidents")["value"]
    with SessionLocal() as db:
        db.execute(text("""INSERT INTO incidents (festival_id,code,category,location,description,priority,reported_by,is_simulated)
                           VALUES (:f,'TEST1','Test','Test','Test','low','Test',true)"""), {"f": fest})
        db.commit()
    try:
        after = admin.get(f"/api/festivals/{fest}/dashboard").json()
        assert kpi(after, "incidents")["value"] == before + 1
    finally:
        with SessionLocal() as db:
            db.execute(text("DELETE FROM incidents WHERE code='TEST1'"))
            db.commit()


def test_unallocated_alert_matches_pack(data):
    wanted = sum(1 for r in pack("participants.csv") if r["accommodation_requested"] == "yes")
    alert = next(a for a in data["alerts"] if a["module"] == "Accommodation" and "bed allocated" in a["title"])
    assert f"{wanted:,}" in alert["title"]


def test_schedule_has_no_double_booked_venue_and_fits_venue_hours():
    with SessionLocal() as db:
        overlaps = db.execute(text("""SELECT count(*) FROM events a JOIN events b ON a.venue_id=b.venue_id AND a.id<b.id
                                      WHERE a.start_at<b.end_at + interval '30 min' AND b.start_at<a.end_at + interval '30 min'""")).scalar_one()
        outside = db.execute(text("""SELECT count(*) FROM events e WHERE e.start_at IS NULL OR
            NOT EXISTS (SELECT 1 FROM venues v, jsonb_array_elements(v.availability) w
                        WHERE v.id=e.venue_id AND e.start_at >= (w->>'start')::timestamptz AND e.end_at <= (w->>'end')::timestamptz)""")).scalar_one()
    assert overlaps == 0
    assert outside == 0


def test_planner_avoids_times_when_registrants_are_away():
    from datetime import datetime, timedelta

    from app.services.pack_import import IST
    from app.services.schedule_plan import PlanEvent, PlanVenue, build_schedule

    day1, day2 = datetime(2026, 10, 30, 8, tzinfo=IST), datetime(2026, 10, 31, 8, tzinfo=IST)
    hall = PlanVenue("V", "LT-1", 150, ((day1, day1 + timedelta(hours=14)), (day2, day2 + timedelta(hours=14))))
    arrives_day2 = tuple((day2, day2 + timedelta(days=1)) for _ in range(10))
    event = PlanEvent("E", "audio; seating", timedelta(hours=2), frozenset(str(i) for i in range(10)), arrives_day2)
    assert build_schedule([event], [hall])[0].start == day2


def test_off_campus_alert_matches_an_independent_count(data):
    with SessionLocal() as db:
        expected = db.execute(text("""SELECT count(*) FROM registrations r JOIN participants p ON p.id=r.participant_id
            JOIN events e ON e.id=r.event_id WHERE p.departure_at >= p.arrival_at
            AND NOT (p.arrival_at <= e.start_at AND p.departure_at >= e.end_at)""")).scalar_one()
    assert expected > 0
    assert any(a["title"].startswith(f"{expected:,} registrations fall outside") for a in data["alerts"])


def test_simulated_rows_are_labelled():
    with SessionLocal() as db:
        for table in ("vehicles", "routes", "budgets", "expenses", "sponsors", "incidents", "tasks", "volunteer_shifts"):
            assert db.execute(text(f"SELECT count(*) FROM {table} WHERE is_simulated IS NOT TRUE")).scalar_one() == 0


def test_phase_uses_festival_dates(data):
    from types import SimpleNamespace

    from app.analytics.dashboard import festival_phase
    festival = SimpleNamespace(start_date=date(2026, 10, 30), end_date=date(2026, 11, 1))
    assert [festival_phase(festival, d) for d in (date(2026, 10, 29), date(2026, 10, 30), date(2026, 11, 2))] == ["Upcoming", "Live", "Completed"]

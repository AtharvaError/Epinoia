from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.database import SessionLocal
from tests.helpers import client_for, festival_id

IST = timezone(timedelta(hours=5, minutes=30))


@pytest.fixture(scope="module")
def admin():
    return client_for("admin@festos.test")


@pytest.fixture(scope="module")
def base(admin):
    return f"/api/festivals/{festival_id(admin)}"


def scheduled(admin, base, code):
    return admin.get(f"{base}/events/{code}").json()


def event_body(event, **changes):
    body = {k: event[k] for k in ("name", "participation_type", "category", "description", "start_at", "end_at", "coordinator_id", "judges",
                                  "dependencies", "volunteers_needed", "duration_minutes", "status")}
    body["venue_code"] = event["venue"]["code"] if event["venue"] else None
    return {**body, **changes}


def test_events_list_and_day_filter(admin, base):
    assert admin.get(f"{base}/events").json()["total"] == 31
    day = admin.get(f"{base}/events", params={"day": "2026-10-31"}).json()
    assert day["total"] > 0
    assert all(e["start_at"].startswith("2026-10-31") or e["start_at"].startswith("2026-10-30T1") for e in day["items"])


def test_schedule_returns_every_venue(admin, base):
    schedule = admin.get(f"{base}/events/schedule", params={"day": "2026-10-30"}).json()
    assert len(schedule["venues"]) == 9 and schedule["events"]


def test_conflicts_report_capacity(admin, base):
    conflicts = admin.get(f"{base}/events/conflicts").json()
    assert conflicts["venue"] == []
    assert conflicts["capacity"] and conflicts["participants"]


def test_venue_double_booking_needs_an_override_reason(admin, base):
    events = admin.get(f"{base}/events").json()["items"]
    first = events[0]
    other = next(e for e in events if e["venue"]["code"] != first["venue"]["code"])
    moved = event_body(scheduled(admin, base, other["code"]), venue_code=first["venue"]["code"], start_at=first["start_at"], end_at=first["end_at"])
    refused = admin.patch(f"{base}/events/{other['code']}", json=moved)
    assert refused.status_code == 409
    assert refused.json()["detail"]["conflicts"]
    original = event_body(scheduled(admin, base, other["code"]))
    accepted = admin.patch(f"{base}/events/{other['code']}", json={**moved, "override_reason": "Testing the override"})
    assert accepted.status_code == 200 and accepted.json()["overridden"]
    with SessionLocal() as db:
        reason = db.execute(text("SELECT reason FROM audit_logs WHERE entity_id=:c AND action='event.reschedule' ORDER BY id DESC LIMIT 1"), {"c": other["code"]}).scalar_one()
    assert reason == "Testing the override"
    assert admin.get(f"{base}/events/conflicts").json()["venue"]
    assert admin.patch(f"{base}/events/{other['code']}", json=original).status_code == 200
    assert admin.get(f"{base}/events/conflicts").json()["venue"] == []


def test_event_end_must_follow_start(admin, base):
    bad = {"name": "Bad", "participation_type": "solo", "start_at": "2026-10-30T12:00:00+05:30", "end_at": "2026-10-30T10:00:00+05:30"}
    assert admin.post(f"{base}/events", json=bad).status_code == 422


def test_event_create_and_soft_delete(admin, base):
    code = admin.post(f"{base}/events", json={"name": "Test Event", "participation_type": "solo"}).json()["code"]
    assert admin.request("DELETE", f"{base}/events/{code}", json={"reason": "Test cleanup"}).status_code == 204
    assert admin.get(f"{base}/events/{code}").status_code == 404
    with SessionLocal() as db:
        db.execute(text("DELETE FROM events WHERE code=:c"), {"c": code})
        db.commit()


def test_volunteer_role_cannot_edit_events(base):
    assert client_for("st07@festos.test").post(f"{base}/events", json={"name": "No", "participation_type": "solo"}).status_code == 403


def test_venues_show_assigned_events(admin, base):
    venues = admin.get(f"{base}/venues").json()
    assert len(venues) == 9
    assert sum(len(v["events"]) for v in venues) == 31


def test_venue_with_events_cannot_be_deleted(admin, base):
    venue = next(v for v in admin.get(f"{base}/venues").json() if v["events"])
    assert admin.delete(f"{base}/venues/{venue['code']}").status_code == 409


def test_damaged_stock_cannot_be_allocated(admin, base):
    item = admin.get(f"{base}/resources", params={"condition": "under repair"}).json()["items"][0]
    assert item["available"] == 0
    refused = admin.post(f"{base}/resources/{item['code']}/allocate", json={"event_code": "E01", "quantity": 1})
    assert refused.status_code == 409


def test_resource_allocation_reserves_stock(admin, base):
    item = admin.get(f"{base}/resources", params={"condition": "good"}).json()["items"][0]
    before = item["available"]
    done = admin.post(f"{base}/resources/{item['code']}/allocate", json={"event_code": "E02", "quantity": 1}).json()
    assert done["available"] == before - 1
    too_many = admin.post(f"{base}/resources/{item['code']}/allocate", json={"event_code": "E02", "quantity": before + 5})
    assert too_many.status_code == 409


def test_room_manual_allocation_enforces_hard_rules(admin, base):
    with SessionLocal() as db:
        code = db.execute(text("""SELECT p.code FROM participants p WHERE p.gender_as_given='Male' AND p.accessibility_needs='None' AND p.accommodation_required
            AND NOT EXISTS (SELECT 1 FROM accommodation_allocations a WHERE a.participant_id=p.id AND a.status<>'cancelled') LIMIT 1""")).scalar_one()
    girls_room, boys_room = "TESTG1", "TESTB1"
    for room, policy in ((girls_room, "girls"), (boys_room, "boys")):
        assert admin.post(f"{base}/accommodation/rooms", json={"hostel_code": "B1", "code": room, "floor": 1, "gender_policy": policy, "capacity": 1, "accessible": False}).status_code == 201
    refused = admin.post(f"{base}/accommodation/allocate", json={"participant_code": code, "room_code": girls_room})
    assert refused.status_code == 409 and refused.json()["detail"]["conflicts"]
    assert admin.post(f"{base}/accommodation/allocate", json={"participant_code": code, "room_code": boys_room}).status_code == 201
    assert admin.post(f"{base}/accommodation/allocate", json={"participant_code": code, "room_code": boys_room}).status_code == 409
    allocation = admin.get(f"{base}/accommodation/allocations", params={"q": code}).json()["items"][0]
    assert admin.post(f"{base}/accommodation/allocations/{allocation['id']}/check-out").status_code == 409
    assert admin.post(f"{base}/accommodation/allocations/{allocation['id']}/check-in").status_code == 200
    assert admin.post(f"{base}/accommodation/allocations/{allocation['id']}/check-out").status_code == 200
    assert admin.delete(f"{base}/accommodation/allocations/{allocation['id']}").status_code == 204


def test_plan_preview_writes_nothing_and_stale_preview_is_refused(admin, base):
    with SessionLocal() as db:
        before = db.execute(text("SELECT count(*) FROM accommodation_allocations WHERE status<>'cancelled'")).scalar_one()
    preview = admin.post(f"{base}/accommodation/plan", json={"budget_limit": 99999999}).json()
    with SessionLocal() as db:
        assert db.execute(text("SELECT count(*) FROM accommodation_allocations WHERE status<>'cancelled'")).scalar_one() == before
    assert preview["summary"]["placed"] > 0
    assert preview["summary"]["placed"] + preview["summary"]["held"] == preview["summary"]["people"]
    stale = admin.post(f"{base}/accommodation/plan/apply", json={"state_hash": "stale"})
    assert stale.status_code == 409


def test_a_second_plan_cannot_respend_the_budget(admin, base):
    """The seed plan already used the budget, so a new plan may only spend what is left."""
    preview = admin.post(f"{base}/accommodation/plan", json={}).json()
    left = float(preview["approved_budget"]) - float(preview["already_committed"])
    assert float(preview["already_committed"]) > 0
    assert preview["summary"]["external_cost"] <= left


def test_going_over_budget_needs_permission_and_reason(admin, base):
    accommodation_head = client_for("st02@festos.test")
    preview = admin.post(f"{base}/accommodation/plan", json={"budget_limit": 99999999}).json()
    assert preview["over_approved_budget"] is True
    body = {"budget_limit": 99999999, "state_hash": preview["state_hash"]}
    assert accommodation_head.post(f"{base}/accommodation/plan/apply", json=body).status_code == 403
    assert admin.post(f"{base}/accommodation/plan/apply", json=body).status_code == 422


def test_full_vehicle_refuses_another_passenger(admin, base):
    route = next(r for r in admin.get(f"{base}/transport/routes").json()["items"] if r["vehicle"] and r["passengers"] >= r["vehicle"]["capacity"])
    with SessionLocal() as db:
        spare = db.execute(text("""SELECT code FROM participants p WHERE NOT EXISTS
            (SELECT 1 FROM transport_assignments t JOIN routes r ON r.id=t.route_id WHERE t.participant_id=p.id AND r.code=:c) LIMIT 1"""), {"c": route["code"]}).scalar_one()
    assert admin.post(f"{base}/transport/routes/{route['code']}/passengers", json={"participant_code": spare}).status_code == 409


def test_delay_updates_arrival_and_finds_affected_events(admin, base):
    route = admin.get(f"{base}/transport/routes", params={"status": "delayed"}).json()["items"][0]
    before = datetime.fromisoformat(route["expected_arrival_at"])
    result = admin.post(f"{base}/transport/routes/{route['code']}/delay", json={"minutes": 30, "reason": "Test delay"}).json()
    assert datetime.fromisoformat(result["expected_arrival_at"]) - before == timedelta(minutes=30)
    assert isinstance(result["affected_events"], list)


def test_only_routes_with_known_stops_have_map_points(admin, base):
    routes = admin.get(f"{base}/transport/routes").json()["items"]
    assert any(r["points"] for r in routes)
    assert all(r["points"] is None for r in routes if "loop" in r["name"])


def test_shift_overlap_is_refused(admin, base):
    created = admin.post(f"{base}/volunteers", json={"name": "Test Helper", "skills": ["AV"]}).json()["code"]
    start = datetime(2026, 11, 2, 9, tzinfo=IST)
    shift = {"start_at": start.isoformat(), "end_at": (start + timedelta(hours=2)).isoformat(), "task": "Test"}
    assert admin.post(f"{base}/volunteers/{created}/shifts", json=shift).status_code == 201
    assert admin.post(f"{base}/volunteers/{created}/shifts", json=shift).status_code == 409
    with SessionLocal() as db:
        db.execute(text("DELETE FROM volunteers WHERE code=:c"), {"c": created})
        db.commit()


def test_cancelled_shifts_do_not_count_as_staffed(admin, base):
    event = admin.get(f"{base}/events").json()["items"][0]
    with SessionLocal() as db:
        shift = db.execute(text("""SELECT s.id FROM volunteer_shifts s JOIN events e ON e.id=s.event_id
                                   WHERE e.code=:c AND s.status<>'cancelled' LIMIT 1"""), {"c": event["code"]}).scalar_one()
    assert admin.patch(f"{base}/shifts/{shift}", json={"status": "cancelled"}).status_code == 200
    assert scheduled(admin, base, event["code"])["staffed"] == event["staffed"] - 1
    dashboard = admin.get(f"{base}/dashboard").json()
    assert dashboard["kpis"]


def test_replacements_are_free_during_the_event(admin, base):
    event = admin.get(f"{base}/volunteers/understaffed").json()[0]
    assert event["short"] > 0
    people = admin.get(f"{base}/volunteers/replacements", params={"event": event["code"]}).json()
    assert people
    detail = scheduled(admin, base, event["code"])
    start, end = datetime.fromisoformat(detail["start_at"]), datetime.fromisoformat(detail["end_at"])
    for person in people:
        for shift in admin.get(f"{base}/volunteers/{person['code']}").json()["shift_list"]:
            assert datetime.fromisoformat(shift["end_at"]) <= start or datetime.fromisoformat(shift["start_at"]) >= end

"""The Ripple Simulator's promises: previews write nothing, stale previews never apply, commits are atomic."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from app.database import SessionLocal
from app.simulation import engine
from app.simulation.graph import Hop, Impact, traverse
from app.simulation.scenarios import inr
from tests.helpers import client_for, festival_id

PRODUCTION_TABLES = ("accommodation_allocations", "hostels", "events", "volunteer_shifts", "volunteers", "budgets", "routes", "notifications")


@pytest.fixture(scope="module")
def director():
    return client_for("st08@festos.test")


@pytest.fixture(scope="module")
def base(director):
    return f"/api/festivals/{festival_id(director)}"


def snapshot():
    with SessionLocal() as db:
        return {t: db.execute(text(f"SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), '')) FROM {t} t")).scalar_one() for t in PRODUCTION_TABLES}


def simulate(client, base, scenario, change, expected=201):
    response = client.post(f"{base}/simulations", json={"scenario": scenario, "change": change})
    assert response.status_code == expected, response.text
    return response.json()


def scalar(sql, **params):
    with SessionLocal() as db:
        return db.execute(text(sql), params).scalar_one()


def seat_bearing_hostel():
    return scalar("""SELECT h.code FROM hostels h JOIN rooms r ON r.hostel_id=h.id JOIN accommodation_allocations a ON a.room_id=r.id
                     WHERE h.kind='on_campus' AND a.status<>'cancelled' AND h.status='available' GROUP BY h.code ORDER BY count(*) DESC, h.code LIMIT 1""")


# --- The graph ------------------------------------------------------------------------------

def test_traversal_marks_direct_and_indirect_and_ends_on_cycles():
    calls = []

    def rule(name, records):
        def run(_):
            calls.append(name)
            return Hop(records, [Impact("events", "low", "fact", name)])
        return run

    walk = traverse("volunteers", {"v1"}, [], {("volunteers", "events"): rule("a", {"e1"}), ("events", "volunteers"): rule("b", {"v2"}), ("events", "participants"): rule("c", {"p1"})})
    assert walk.depth == {"volunteers": 0, "events": 1, "participants": 2}
    assert sorted(calls) == ["a", "b", "c"]
    assert {i.explanation: i.is_direct for i in walk.impacts} == {"a": True, "b": False, "c": False}


def test_a_rule_with_no_result_stops_the_ripple():
    walk = traverse("transportation", {"t"}, [], {("transportation", "participants"): lambda _: Hop(), ("participants", "events"): lambda _: Hop({"never"})})
    assert walk.depth == {"transportation": 0}
    assert not walk.active


def test_inr_uses_indian_grouping():
    assert inr(1234567) == "₹12,34,567"
    assert inr(999) == "₹999"
    assert inr(-1000) == "-₹1,000"


# --- Preview --------------------------------------------------------------------------------

@pytest.mark.parametrize("scenario,change", [
    ("hostel_closure", {"hostel_code": "G2"}), ("bus_delay", {"route_code": "TRIP06", "minutes": 60}),
    ("volunteers_unavailable", {"volunteer_codes": ["VOL001", "VOL002"]}), ("budget_reduction", {"department": "Hospitality", "amount": 200000}),
    ("venue_change", {"event_code": "E01", "venue_code": "V04"}),
])
def test_preview_changes_no_production_data(director, base, scenario, change):
    before = snapshot()
    report = simulate(director, base, scenario, change)["report"]
    assert snapshot() == before
    assert report["impacts"]
    assert all(i["certainty"] in ("fact", "estimate") and i["severity"] and i["module"] for i in report["impacts"])
    assert report["before_after"] and report["current_state"]
    assert "plan" not in report


def test_report_never_exposes_participant_contact_or_names(director, base):
    report = simulate(director, base, "hostel_closure", {"hostel_code": seat_bearing_hostel()})
    name, phone = "x", "x"
    with SessionLocal() as db:
        name, phone = db.execute(text("SELECT full_name, phone FROM participants ORDER BY id LIMIT 1")).one()
    assert phone not in str(report)
    assert f"{name} (" not in str(report)


def test_graph_shows_how_far_the_change_travelled(director, base):
    report = simulate(director, base, "hostel_closure", {"hostel_code": seat_bearing_hostel(), "extra_budget": 5000000})["report"]
    depth = {n["id"]: n["depth"] for n in report["graph"]["nodes"]}
    assert depth["accommodation"] == 0
    assert depth["participants"] == 1 and depth["finance"] == 1
    assert depth["transportation"] == 2
    assert any(e["active"] for e in report["graph"]["edges"])
    assert report["summary"]["direct_records"] > 0 and report["summary"]["indirect_records"] > 0


def test_hostel_numbers_match_the_database(director, base):
    code = seat_bearing_hostel()
    housed = scalar("""SELECT count(*) FROM accommodation_allocations a JOIN rooms r ON r.id=a.room_id JOIN hostels h ON h.id=r.hostel_id
                       WHERE h.code=:c AND a.status<>'cancelled'""", c=code)
    report = simulate(director, base, "hostel_closure", {"hostel_code": code})["report"]
    assert dict((s["label"], s["value"]) for s in report["current_state"])["Occupied"] == housed
    assert any(f"{housed:,} participant" in i["explanation"] for i in report["impacts"])


def test_a_tight_budget_is_shown_and_extra_budget_places_more_people(director, base):
    code = seat_bearing_hostel()
    tight = simulate(director, base, "hostel_closure", {"hostel_code": code})["report"]
    assert tight["needs_reason"] and "budget" in str(tight["impacts"]).lower()
    held = [r for r in tight["before_after"] if r["metric"] == "People without a bed"][0]
    funded = simulate(director, base, "hostel_closure", {"hostel_code": code, "extra_budget": 5000000})["report"]
    held_funded = [r for r in funded["before_after"] if r["metric"] == "People without a bed"][0]
    assert held_funded["after"] < held["after"]
    assert 0 < funded["summary"]["budget_impact"] < 5000000, "Impact is the extra cost, not the money made available"


def test_invalid_scenarios_are_rejected(director, base):
    simulate(director, base, "nonsense", {}, expected=422)
    simulate(director, base, "bus_delay", {"route_code": "TRIP01", "minutes": 0}, expected=422)
    simulate(director, base, "bus_delay", {"route_code": "NOPE", "minutes": 5}, expected=404)
    simulate(director, base, "venue_change", {"event_code": "E01", "venue_code": "V01", "start_at": "2026-10-30T10:00:00+05:30"}, expected=422)
    simulate(director, base, "budget_reduction", {"amount": 999999999}, expected=422)


def test_venue_double_booking_is_found(director, base):
    events = director.get(f"{base}/events").json()["items"]
    first = next(e for e in events if e["venue"]["code"] == "V04")
    other = next(e for e in events if e["venue"]["code"] != "V04")
    report = simulate(director, base, "venue_change", {"event_code": other["code"], "venue_code": "V04", "start_at": first["start_at"], "end_at": first["end_at"]})["report"]
    assert any(first["name"] in i["explanation"] and i["severity"] == "critical" for i in report["impacts"])
    assert report["needs_reason"]


def test_equipment_shortage_uses_only_usable_stock(director, base):
    event = scalar("SELECT code FROM events WHERE equipment_needs LIKE 'camera%' LIMIT 1")
    current = scalar("SELECT v.code FROM events e JOIN venues v ON v.id=e.venue_id WHERE e.code=:c", c=event)
    target = "V02" if current != "V02" else "V03"
    report = simulate(director, base, "venue_change", {"event_code": event, "venue_code": target})["report"]
    assert any("camera" in i["explanation"] and "0 of 31" in i["explanation"] for i in report["impacts"])


def test_delay_matches_a_direct_count_and_flags_the_vehicles_next_trip(director, base):
    route = "TRIP02"
    riders = scalar("SELECT count(*) FROM transport_assignments t JOIN routes r ON r.id=t.route_id WHERE r.code=:c", c=route)
    report = simulate(director, base, "bus_delay", {"route_code": route, "minutes": 45})["report"]
    assert any(f"{riders} passengers" in i["explanation"] for i in report["impacts"])
    assert not any("before it gets back" in i["explanation"] for i in report["impacts"])

    arrival = scalar("SELECT expected_arrival_at FROM routes WHERE code=:c", c=route)
    vehicle = scalar("SELECT v.code FROM vehicles v JOIN routes r ON r.vehicle_id=v.id WHERE r.code=:c", c=route)
    follow_on = {"name": "Test follow-on", "pickup": "Campus", "destination": "Station", "vehicle_code": vehicle,
                 "departure_at": (arrival + timedelta(minutes=30)).isoformat(), "expected_arrival_at": (arrival + timedelta(minutes=60)).isoformat()}
    assert director.post(f"{base}/transport/routes", json=follow_on).status_code == 201
    knock_on = simulate(director, base, "bus_delay", {"route_code": route, "minutes": 45})["report"]
    assert any("before it gets back" in i["explanation"] for i in knock_on["impacts"])
    with SessionLocal() as db:
        db.execute(text("DELETE FROM routes WHERE name='Test follow-on'"))
        db.commit()


# --- Workflow -------------------------------------------------------------------------------

def test_only_approvers_can_apply(director, base):
    head = client_for("st02@festos.test")
    sim = simulate(head, base, "bus_delay", {"route_code": "TRIP02", "minutes": 15})
    assert sim["can_approve"] is False
    assert head.post(f"{base}/simulations/{sim['id']}/approve", json={}).status_code == 403
    assert client_for("st07@festos.test").get(f"{base}/simulations").status_code == 403


def test_requesting_approval_reaches_the_director(director, base):
    head = client_for("st02@festos.test")
    sim = simulate(head, base, "bus_delay", {"route_code": "TRIP02", "minutes": 16})
    assert head.post(f"{base}/simulations/{sim['id']}/submit", json={"reason": "Road works"}).status_code == 204
    assert head.post(f"{base}/simulations/{sim['id']}/submit", json={}).status_code == 409
    notes = director.get(f"{base}/notifications").json()
    assert notes["unread"] > 0 and any("Approval requested" == n["title"] for n in notes["items"])
    pending = scalar("SELECT count(*) FROM change_approvals WHERE simulation_id=:i AND status='pending'", i=sim["id"])
    assert pending == 1
    assert director.post(f"{base}/simulations/{sim['id']}/reject", json={"reason": "Not needed now"}).status_code == 204
    assert director.post(f"{base}/simulations/{sim['id']}/approve", json={}).status_code == 409
    assert scalar("SELECT status FROM change_approvals WHERE simulation_id=:i", i=sim["id"]) == "rejected"


def test_stale_preview_is_refused_and_changes_nothing(director, base):
    sim = simulate(director, base, "volunteers_unavailable", {"volunteer_codes": ["VOL010"]})
    assert director.get(f"{base}/simulations/{sim['id']}").json()["current"] is True
    # Someone else changes the data the preview relied on.
    other = director.post(f"{base}/volunteers/VOL010/shifts", json={"start_at": "2026-11-03T09:00:00+05:30", "end_at": "2026-11-03T10:00:00+05:30", "task": "Late change"})
    assert other.status_code == 201
    assert director.get(f"{base}/simulations/{sim['id']}").json()["current"] is False
    before = snapshot()
    refused = director.post(f"{base}/simulations/{sim['id']}/approve", json={"reason": "Testing staleness"})
    assert refused.status_code == 409 and "changed" in refused.json()["detail"]
    assert snapshot() == before
    assert director.get(f"{base}/simulations/{sim['id']}").json()["status"] == "stale"
    fresh = director.post(f"{base}/simulations/{sim['id']}/recalculate")
    assert fresh.status_code == 201 and fresh.json()["current"] is True and fresh.json()["id"] != sim["id"]


def test_unrelated_changes_do_not_invalidate_a_preview(director, base):
    sim = simulate(director, base, "bus_delay", {"route_code": "TRIP02", "minutes": 17})
    assert director.post(f"{base}/tasks", json={"title": "Unrelated", "department": "Events"}).status_code == 201
    assert director.get(f"{base}/simulations/{sim['id']}").json()["current"] is True


def test_cancel_leaves_everything_alone(director, base):
    sim = simulate(director, base, "bus_delay", {"route_code": "TRIP02", "minutes": 18})
    before = snapshot()
    assert director.post(f"{base}/simulations/{sim['id']}/cancel").status_code == 204
    assert snapshot() == before
    assert director.post(f"{base}/simulations/{sim['id']}/approve", json={}).status_code == 409


def test_commit_is_all_or_nothing(director, base, monkeypatch):
    sim = simulate(director, base, "bus_delay", {"route_code": "TRIP02", "minutes": 19})
    audits = scalar("SELECT count(*) FROM audit_logs")
    before = snapshot()

    def boom(*args, **kwargs):
        raise RuntimeError("notification service failed")

    monkeypatch.setattr(engine, "notify_roles", boom)
    with pytest.raises(RuntimeError):
        director.post(f"{base}/simulations/{sim['id']}/approve", json={})
    assert snapshot() == before
    assert scalar("SELECT count(*) FROM audit_logs") == audits
    assert director.get(f"{base}/simulations/{sim['id']}").json()["status"] == "previewed"


def test_bus_delay_commit_updates_the_route_and_notifies(director, base):
    before = scalar("SELECT expected_arrival_at FROM routes WHERE code='TRIP02'")
    sim = simulate(director, base, "bus_delay", {"route_code": "TRIP02", "minutes": 20})
    result = director.post(f"{base}/simulations/{sim['id']}/approve", json={})
    assert result.status_code == 200 and result.json()["notified"] > 0
    assert scalar("SELECT expected_arrival_at FROM routes WHERE code='TRIP02'") - before == timedelta(minutes=20)
    assert director.post(f"{base}/simulations/{sim['id']}/approve", json={}).status_code == 409
    assert scalar("SELECT count(*) FROM audit_logs WHERE action='change.bus_delay'") >= 1


def test_volunteer_commit_replaces_shifts_without_overlaps(director, base):
    code = scalar("""SELECT v.code FROM volunteers v JOIN volunteer_shifts s ON s.volunteer_id=v.id
                     WHERE v.status='active' AND s.status<>'cancelled' GROUP BY v.code ORDER BY count(*) DESC, v.code LIMIT 1""")
    sim = simulate(director, base, "volunteers_unavailable", {"volunteer_codes": [code]})
    assert director.post(f"{base}/simulations/{sim['id']}/approve", json={"reason": "Volunteer is unwell"}).status_code == 200
    assert scalar("SELECT status FROM volunteers WHERE code=:c", c=code) == "unavailable"
    assert scalar("""SELECT count(*) FROM volunteer_shifts s JOIN volunteers v ON v.id=s.volunteer_id WHERE v.code=:c AND s.status<>'cancelled'""", c=code) == 0
    assert scalar("""SELECT count(*) FROM volunteer_shifts a JOIN volunteer_shifts b ON a.volunteer_id=b.volunteer_id AND a.id<b.id
                     WHERE a.status<>'cancelled' AND b.status<>'cancelled' AND a.start_at<b.end_at AND b.start_at<a.end_at""") == 0


def test_budget_commit_reduces_the_budget_by_exactly_the_amount(director, base):
    total = scalar("SELECT sum(approved_amount) FROM budgets")
    sim = simulate(director, base, "budget_reduction", {"amount": 123456})
    response = director.post(f"{base}/simulations/{sim['id']}/approve", json={"reason": "Sponsor withdrew funding"})
    assert response.status_code == 200, response.text
    assert total - scalar("SELECT sum(approved_amount) FROM budgets") == 123456


def test_venue_commit_moves_the_event_and_its_shifts(director, base):
    event = scalar("""SELECT e.code FROM events e JOIN volunteer_shifts s ON s.event_id=e.id AND s.status<>'cancelled'
                      WHERE e.venue_id = (SELECT id FROM venues WHERE code='V04') GROUP BY e.code ORDER BY e.code LIMIT 1""")
    start = scalar("SELECT start_at FROM events WHERE code=:c", c=event)
    end = scalar("SELECT end_at FROM events WHERE code=:c", c=event)
    shift = scalar("SELECT min(s.start_at) FROM volunteer_shifts s JOIN events e ON e.id=s.event_id WHERE e.code=:c AND s.status<>'cancelled'", c=event)
    later = {"start_at": (start + timedelta(days=4)).isoformat(), "end_at": (end + timedelta(days=4)).isoformat()}
    sim = simulate(director, base, "venue_change", {"event_code": event, "venue_code": "V06", **later})
    assert sim["report"]["needs_reason"]
    assert director.post(f"{base}/simulations/{sim['id']}/approve", json={}).status_code == 422
    assert director.post(f"{base}/simulations/{sim['id']}/approve", json={"reason": "Testing a move with override"}).status_code == 200
    assert scalar("SELECT v.code FROM events e JOIN venues v ON v.id=e.venue_id WHERE e.code=:c", c=event) == "V06"
    assert scalar("SELECT schedule_source FROM events WHERE code=:c", c=event) == "manual"
    moved = scalar("SELECT min(s.start_at) FROM volunteer_shifts s JOIN events e ON e.id=s.event_id WHERE e.code=:c AND s.status<>'cancelled'", c=event)
    assert moved - shift == timedelta(days=4)


def test_hostel_closure_commit_keeps_every_hard_rule(director, base):
    code = seat_bearing_hostel()
    housed = scalar("SELECT count(*) FROM accommodation_allocations WHERE status<>'cancelled'")
    sim = simulate(director, base, "hostel_closure", {"hostel_code": code, "extra_budget": 5000000})
    moved_reason = {"reason": "Water main failure in the block"}
    assert director.post(f"{base}/simulations/{sim['id']}/approve", json=moved_reason).status_code == 200
    assert scalar("SELECT status FROM hostels WHERE code=:c", c=code) == "closed"
    assert scalar("""SELECT count(*) FROM accommodation_allocations a JOIN rooms r ON r.id=a.room_id JOIN hostels h ON h.id=r.hostel_id
                     WHERE h.code=:c AND a.status<>'cancelled'""", c=code) == 0
    assert scalar("SELECT count(*) FROM accommodation_allocations WHERE status<>'cancelled'") <= housed
    assert scalar("""SELECT count(*) FROM (SELECT r.id FROM rooms r JOIN accommodation_allocations a ON a.room_id=r.id AND a.status<>'cancelled'
                     GROUP BY r.id HAVING count(*) > r.capacity) x""") == 0, "No room may be over capacity"
    assert scalar("""SELECT count(*) FROM accommodation_allocations a JOIN rooms r ON r.id=a.room_id JOIN participants p ON p.id=a.participant_id
                     WHERE a.status<>'cancelled' AND r.gender_policy IS NOT NULL AND (p.gender_as_given IS NULL OR p.gender_as_given IN ('Non-binary','Prefer not to say'))""") == 0
    assert scalar("""SELECT count(*) FROM accommodation_allocations a JOIN rooms r ON r.id=a.room_id JOIN participants p ON p.id=a.participant_id
                     WHERE a.status<>'cancelled' AND ((r.gender_policy='boys' AND p.gender_as_given='Female') OR (r.gender_policy='girls' AND p.gender_as_given='Male'))""") == 0
    beds = director.get(f"{base}/dashboard").json()
    assert next(k for k in beds["kpis"] if k["key"] == "beds")["total"] < 1450


def test_closed_hostel_cannot_be_closed_again(director, base):
    code = scalar("SELECT code FROM hostels WHERE status='closed' LIMIT 1")
    simulate(director, base, "hostel_closure", {"hostel_code": code}, expected=422)


def test_history_and_audit(director, base):
    history = director.get(f"{base}/simulations").json()
    assert history["total"] > 5 and history["items"][0]["title"]
    assert director.get(f"{base}/simulations", params={"status": "applied"}).json()["total"] >= 4
    audit = director.get(f"{base}/audit", params={"q": "applied"}).json()
    assert audit["total"] >= 4
    assert client_for("st02@festos.test").get(f"{base}/audit").status_code == 403


def test_pending_approvals_are_counted_on_the_dashboard(director, base):
    before = next(k for k in director.get(f"{base}/dashboard").json()["kpis"] if k["key"] == "approvals")["value"]
    sim = simulate(director, base, "bus_delay", {"route_code": "TRIP03", "minutes": 5})
    assert director.post(f"{base}/simulations/{sim['id']}/submit", json={}).status_code == 204
    after = next(k for k in director.get(f"{base}/dashboard").json()["kpis"] if k["key"] == "approvals")["value"]
    assert after == before + 1


def test_dates_stored_are_timezone_aware():
    assert isinstance(scalar("SELECT created_at FROM simulations LIMIT 1"), datetime)

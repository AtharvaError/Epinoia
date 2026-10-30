"""The disruptions judges can inject. Named to run last, because these apply real changes."""
import pytest
from sqlalchemy import text

from app.database import SessionLocal
from app.services.equipment import double_bookings
from tests.helpers import client_for, festival_id


@pytest.fixture(scope="module")
def director():
    return client_for("st08@festos.test")


@pytest.fixture(scope="module")
def desk():
    return client_for("st07@festos.test")


@pytest.fixture(scope="module")
def base(director):
    return f"/api/festivals/{festival_id(director)}"


def scalar(sql, **params):
    with SessionLocal() as db:
        return db.execute(text(sql), params).scalar_one()


def simulate(client, base, scenario, **change):
    response = client.post(f"{base}/simulations", json={"scenario": scenario, "change": change})
    assert response.status_code == 201, response.text
    return response.json()


def approve(client, base, simulation, reason="Tested in the disruption suite"):
    response = client.post(f"{base}/simulations/{simulation['id']}/approve", json={"reason": reason})
    assert response.status_code == 200, response.text
    return response.json()


# --- Equipment ------------------------------------------------------------------------------

def test_the_sample_inventory_plan_assigns_stock_without_double_booking(base):
    assert scalar("SELECT count(*) FROM resource_allocations") > 0
    with SessionLocal() as db:
        assert double_bookings(db, 1) == []


def test_pack_shortages_are_reported_not_hidden(director, base):
    gaps = director.get(f"{base}/events/conflicts").json()["equipment"]
    assert {g["item"] for g in gaps} >= {"camera"}, "Every camera in the pack is under repair"
    assert all(g["allocated"] < g["needed"] for g in gaps)


def booked_lot(client, base):
    options = client.get(f"{base}/simulations/options").json()["equipment"]
    return max(options, key=lambda o: o["events"])


def test_previewing_a_failure_changes_nothing(director, base):
    lot = booked_lot(director, base)
    before = scalar("SELECT count(*) FROM resource_allocations"), scalar("SELECT quantity FROM resources WHERE code=:c", c=lot["code"])
    report = simulate(director, base, "equipment_failure", resource_code=lot["code"], units=lot["quantity"])["report"]
    assert report["graph"]["nodes"]
    assert (scalar("SELECT count(*) FROM resource_allocations"), scalar("SELECT quantity FROM resources WHERE code=:c", c=lot["code"])) == before


def test_a_failure_moves_kit_to_spares_and_never_double_books(director, base):
    lot = booked_lot(director, base)
    events = scalar("SELECT count(*) FROM resource_allocations a JOIN resources r ON r.id=a.resource_id WHERE r.code=:c", c=lot["code"])
    assert events
    simulation = simulate(director, base, "equipment_failure", resource_code=lot["code"], units=lot["quantity"])
    approve(director, base, simulation)
    assert scalar("SELECT condition FROM resources WHERE code=:c", c=lot["code"]) == "under repair"
    assert scalar("SELECT count(*) FROM resource_allocations a JOIN resources r ON r.id=a.resource_id WHERE r.code=:c", c=lot["code"]) == 0
    with SessionLocal() as db:
        assert double_bookings(db, 1) == []


def test_a_partial_failure_splits_the_lot(director, base):
    lot = booked_lot(director, base)
    assert lot["quantity"] > 1
    approve(director, base, simulate(director, base, "equipment_failure", resource_code=lot["code"], units=1))
    assert scalar("SELECT quantity FROM resources WHERE code=:c", c=lot["code"]) == lot["quantity"] - 1
    assert scalar("SELECT condition FROM resources WHERE code=:c", c=f"{lot['code']}-F1") == "under repair"


def test_broken_stock_cannot_fail_again(director, base):
    code = scalar("SELECT code FROM resources WHERE condition='under repair' LIMIT 1")
    response = director.post(f"{base}/simulations", json={"scenario": "equipment_failure", "change": {"resource_code": code, "units": 1}})
    assert response.status_code == 422


# --- Vendors --------------------------------------------------------------------------------

def jain_vendor(client, base):
    return next(v for v in client.get(f"{base}/vendors").json()["vendors"] if "jain" in v["dietary_options"])


def test_coverage_counts_only_compliant_vendors_and_names_the_gaps(director, base):
    data = director.get(f"{base}/vendors").json()
    rows = {c["diet"]: c for c in data["coverage"]}
    assert rows["Nut allergy"]["vendors"] == [], "No vendor in the pack lists nut-free food"
    assert data["unknown_diet"] == scalar("SELECT count(*) FROM participants WHERE festival_id=1 AND dietary_needs IS NULL AND deleted_at IS NULL AND registration_status<>'cancelled'")
    expired = [v["name"] for v in data["vendors"] if v["compliance"] == "expired"]
    assert expired
    assert not set(expired) & {name for row in data["coverage"] for name in row["vendors"]}


def test_shutting_down_a_vendor_recomputes_coverage(director, base):
    vendor = next(v for v in director.get(f"{base}/vendors").json()["vendors"] if v["can_serve"] and "vegetarian" in v["dietary_options"])
    before = {c["diet"]: c for c in director.get(f"{base}/vendors").json()["coverage"]}
    simulation = simulate(director, base, "vendor_shutdown", vendor_code=vendor["code"])
    assert next(n for n in simulation["report"]["graph"]["nodes"] if n["id"] == "vendors")["depth"] == 0
    assert scalar("SELECT status FROM vendors WHERE code=:c", c=vendor["code"]) == "active"
    approve(director, base, simulation)
    assert scalar("SELECT status FROM vendors WHERE code=:c", c=vendor["code"]) == "shut_down"
    after = {c["diet"]: c for c in director.get(f"{base}/vendors").json()["coverage"]}
    assert after["Vegetarian"]["seats"] == before["Vegetarian"]["seats"] - vendor["capacity"]


def test_a_closed_vendor_cannot_shut_down_twice(director, base):
    code = scalar("SELECT code FROM vendors WHERE status='shut_down' LIMIT 1")
    assert director.post(f"{base}/simulations", json={"scenario": "vendor_shutdown", "change": {"vendor_code": code}}).status_code == 422


def test_settlement_terms_reach_the_impact_report(director, base):
    vendor = jain_vendor(director, base)
    report = simulate(director, base, "vendor_shutdown", vendor_code=vendor["code"])["report"]
    assert any(i["module"] == "finance" and vendor["payment_terms"] in i["explanation"] for i in report["impacts"])


# --- Late contingent ------------------------------------------------------------------------

def test_a_late_contingent_moves_real_arrival_times(director, base):
    contingent = max(director.get(f"{base}/simulations/options").json()["contingents"], key=lambda c: c["members"])
    before = scalar("SELECT min(arrival_at) FROM participants p JOIN contingents c ON c.id=p.contingent_id WHERE c.code=:c", c=contingent["code"])
    simulation = simulate(director, base, "late_contingent", contingent_code=contingent["code"], minutes=240)
    assert simulation["report"]["summary"]["modules"] >= 2
    assert scalar("SELECT min(arrival_at) FROM participants p JOIN contingents c ON c.id=p.contingent_id WHERE c.code=:c", c=contingent["code"]) == before
    approve(director, base, simulation)
    after = scalar("SELECT min(arrival_at) FROM participants p JOIN contingents c ON c.id=p.contingent_id WHERE c.code=:c", c=contingent["code"])
    assert (after - before).total_seconds() == 240 * 60


# --- Merchandise ----------------------------------------------------------------------------

def token_for(client, base, code):
    assert client.get(f"{base}/participants/{code}/pass.svg").status_code == 200
    return scalar("SELECT qr_token FROM participants WHERE festival_id=1 AND code=:c", c=code)


def sized_participant():
    return scalar("""SELECT p.code FROM participants p JOIN resources r ON r.name='T-shirt' AND r.size=p.tshirt_size
                     WHERE p.festival_id=1 AND r.festival_id=1 AND p.deleted_at IS NULL AND p.registration_status<>'cancelled' AND r.quantity>r.reserved_quantity ORDER BY p.code LIMIT 1""")


def test_merchandise_is_issued_once_and_the_second_claim_is_recorded(director, desk, base):
    code = sized_participant()
    token = token_for(director, base, code)
    stock = "(SELECT r.quantity FROM resources r JOIN participants p ON r.size=p.tshirt_size WHERE r.festival_id=1 AND p.festival_id=1 AND r.name='T-shirt' AND p.code=:c)"
    before = scalar(f"SELECT {stock}", c=code)
    assert desk.post(f"{base}/passes/merch", json={"token": token, "item": "T-shirt"}).status_code == 201
    assert scalar(f"SELECT {stock}", c=code) == before - 1
    again = desk.post(f"{base}/passes/merch", json={"token": token, "item": "T-shirt"})
    assert again.status_code == 409
    assert "already collected" in again.json()["detail"]
    assert scalar(f"SELECT {stock}", c=code) == before - 1
    assert scalar("SELECT count(*) FROM audit_logs WHERE action='merch.duplicate_claim' AND entity_id=:c", c=code) == 1
    assert desk.post(f"{base}/passes/verify", json={"token": token}).json()["merch"][0]["item"] == "T-shirt"


def test_the_database_refuses_a_second_issue_even_if_the_check_is_bypassed(base):
    with SessionLocal() as db:
        row = db.execute(text("SELECT festival_id, participant_id, resource_id, item FROM merchandise_issues LIMIT 1")).mappings().one()
    with pytest.raises(Exception, match="uq_merchandise_issues"), SessionLocal() as db:
        db.execute(text("INSERT INTO merchandise_issues (festival_id, participant_id, resource_id, item) VALUES (:festival_id, :participant_id, :resource_id, :item)"), row)


def test_a_blank_size_is_never_guessed(director, desk, base):
    with SessionLocal() as db:
        db.execute(text("UPDATE participants SET tshirt_size=NULL WHERE festival_id=1 AND code='P0100'"))
        db.commit()
    token = token_for(director, base, "P0100")
    refused = desk.post(f"{base}/passes/merch", json={"token": token, "item": "T-shirt"})
    assert refused.status_code == 409
    assert "no T-shirt size" in refused.json()["detail"]


def test_items_that_depend_on_results_are_not_issued_at_the_desk(desk, base):
    assert desk.post(f"{base}/passes/merch", json={"token": "x" * 16, "item": "winner medal"}).status_code == 422


# --- Duplicate records ----------------------------------------------------------------------

def test_only_people_who_can_see_protected_fields_review_duplicates(base):
    assert client_for("st03@festos.test").get(f"{base}/participants/duplicates").status_code == 403
    assert client_for("st07@festos.test").get(f"{base}/participants/duplicates").status_code == 403


def test_a_merge_keeps_one_record_and_loses_nothing(director, base):
    keep, drop = director.get(f"{base}/participants/duplicates").json()[0][:2]
    events = scalar("""SELECT count(DISTINCT event_id) FROM registrations r JOIN participants p ON p.id=r.participant_id WHERE p.festival_id=1 AND p.code IN (:a, :b)""", a=keep["code"], b=drop["code"])
    response = director.post(f"{base}/participants/merge", json={"keep": keep["code"], "drop": drop["code"], "take_from_drop": ["tshirt_size"], "reason": "Same person, confirmed with the contingent leader"})
    assert response.status_code == 200, response.text
    merged = director.get(f"{base}/participants/{keep['code']}").json()
    assert merged["merged_from"] == [drop["code"]]
    assert merged["tshirt_size"] == drop["tshirt_size"]
    assert len(merged["events"]) == events
    assert director.get(f"{base}/participants/{drop['code']}").status_code == 404
    assert scalar("SELECT count(*) FROM accommodation_allocations a JOIN participants p ON p.id=a.participant_id WHERE p.festival_id=1 AND p.code=:c AND a.status<>'cancelled'", c=drop["code"]) == 0
    assert scalar("SELECT reason FROM audit_logs WHERE action='participant.merge' AND entity_id=:c", c=keep["code"]).startswith("Same person")
    assert scalar("SELECT count(*) FROM data_issues WHERE festival_id=1 AND entity_code=:c AND issue='possible_duplicate' AND status='open'", c=drop["code"]) == 0


def test_a_merge_needs_a_reason_and_known_fields(director, base):
    group = director.get(f"{base}/participants/duplicates").json()[0]
    keep, drop = group[0]["code"], group[1]["code"]
    assert director.post(f"{base}/participants/merge", json={"keep": keep, "drop": drop, "reason": ""}).status_code == 422
    assert director.post(f"{base}/participants/merge", json={"keep": keep, "drop": drop, "take_from_drop": ["qr_token"], "reason": "A valid reason"}).status_code == 422
    assert director.post(f"{base}/participants/merge", json={"keep": keep, "drop": keep, "reason": "A valid reason"}).status_code == 422


def test_records_can_be_marked_as_different_people(director, base):
    groups = director.get(f"{base}/participants/duplicates").json()
    codes = [r["code"] for r in groups[0]]
    assert director.post(f"{base}/participants/duplicates/dismiss", json={"codes": codes, "reason": "Twins, confirmed at the desk"}).status_code == 204
    assert len(director.get(f"{base}/participants/duplicates").json()) == len(groups) - 1


def test_the_new_decisions_reach_the_command_center(director, base):
    titles = [a["title"] for a in director.get(f"{base}/dashboard").json()["alerts"]]
    assert any("no vendor for their diet" in t for t in titles)
    assert any("short of equipment" in t for t in titles)
    assert any("look like duplicates" in t for t in titles)

"""Tests for Merchandise & Kit Distribution module."""
from datetime import datetime, timezone

from sqlalchemy import text

from app.database import SessionLocal
from tests.helpers import client_for, festival_id


def scalar(sql, **p):
    with SessionLocal() as db:
        return db.execute(text(sql), p).scalar_one()


def test_merchandise_initial_stock_and_shortage_truth():
    admin = client_for("admin@festos.test")
    fid = festival_id(admin)
    base = f"/api/festivals/{fid}/merchandise"

    summary = admin.get(f"{base}/summary").json()
    kpis = summary["kpis"]
    # Total T-shirt on hand = 449 + 115 + 234 + 713 + 136 = 1647
    # Reserved = 14 + 6 + 15 + 29 + 25 = 89
    # Issuable = 1558
    assert kpis["tshirts_on_hand"] == 1647
    assert kpis["tshirts_csv_reserved"] == 89
    assert kpis["tshirts_issuable"] == 1558
    # Non-duplicate participants: 2834
    assert summary["participants_summary"]["entitled_total"] == 2834
    assert kpis["tshirts_needed"] == 2834
    assert kpis["tshirts_net_gap"] == 1558 - 2834  # -1276
    assert kpis["tshirts_surplus_xl"] == 115  # XL issuable 684 vs 569 needed = +115

    # Check 10 merch items exist
    assert len(summary["items"]) == 10
    assert len(summary["size_curve"]) == 5


def test_deterministic_distribution_policies_p1_p2_p3():
    admin = client_for("admin@festos.test")
    fid = festival_id(admin)
    base = f"/api/festivals/{fid}/merchandise"

    prev = admin.get(f"{base}/distribution/preview", params={"policy": "P1"}).json()
    assert prev["policy"] == "P1"
    assert prev["totals"]["exact_tshirts"] == 1443
    assert prev["totals"]["substitute_proposed"] == 115
    assert prev["totals"]["owed_tshirts"] == 1276

    # Verify P1 wave counts: [351, 301, 200, 134, 143, 134, 99, 79, 2]
    wave_exacts = [w["exact_tshirts"] for w in prev["waves"]]
    assert wave_exacts == [351, 301, 200, 134, 143, 134, 99, 79, 2]

    # Verify comparison with P2 and P3
    comp = prev["comparison"]
    p2_waves = comp["P2"]["waves"]
    for w in p2_waves[:-1]:  # 8 valid waves
        assert 48.0 <= w["coverage_pct"] <= 53.0

    p3 = comp["P3"]
    assert 41.0 <= p3["min_contingent_coverage_pct"]
    assert p3["max_contingent_coverage_pct"] <= 61.0


def test_duplicate_blocking_at_desk():
    desk = client_for("st06@festos.test")  # Merchandise Desk staff
    fid = festival_id(desk)
    base = f"/api/festivals/{fid}/merchandise"

    # P_DUP01 is a probable duplicate of P0021
    res = desk.post(f"{base}/handover-kit", json={"recipient_code": "P_DUP01"})
    assert res.status_code == 409
    assert "probable duplicate" in res.json()["detail"].lower()

    # Normal non-duplicate participant can be looked up
    lookup = desk.get(f"{base}/lookup", params={"q": "P0001"}).json()
    assert len(lookup["results"]) >= 1
    p_card = lookup["results"][0]
    assert p_card["code"] == "P0001"
    assert not p_card["blocked_duplicate"]
    # Phone is masked to last 4 digits
    if p_card["phone_last4"]:
        assert len(p_card["phone_last4"]) <= 4


def test_express_kit_handover_and_double_issue_prevention():
    desk = client_for("st06@festos.test")
    fid = festival_id(desk)
    base = f"/api/festivals/{fid}/merchandise"

    # Confirm kit templates and approve P1 plan first
    admin = client_for("admin@festos.test")
    admin.post(f"{base}/distribution/approve", json={"policy": "P1", "confirm_kit_if_needed": True})

    # Find an eligible participant in Wave 1 with size S
    with SessionLocal() as db:
        p_code = db.execute(text("SELECT code FROM participants WHERE tshirt_size='S' AND code NOT LIKE 'P_DUP%' ORDER BY arrival_at, code LIMIT 1")).scalar_one()

    # Hand over kit in 1 atomic action
    handover = desk.post(f"{base}/handover-kit", json={"recipient_code": p_code}).json()
    issued_items = [i["item"] for i in handover["issued"]]
    assert "T-shirt" in issued_items
    assert "ID card" in issued_items
    assert "lanyard" in issued_items

    # Attempting to handover again should fail
    dup_handover = desk.post(f"{base}/handover-kit", json={"recipient_code": p_code})
    assert dup_handover.status_code == 409

    # Attempting single issue without override should fail
    dup_single = desk.post(f"{base}/issue", json={"recipient_code": p_code, "item": "T-shirt", "size": "S", "quantity": 1})
    assert dup_single.status_code == 409


def test_undo_and_size_substitution_rules():
    desk = client_for("st06@festos.test")
    fid = festival_id(desk)
    base = f"/api/festivals/{fid}/merchandise"

    with SessionLocal() as db:
        p_code = db.execute(text("SELECT code FROM participants WHERE tshirt_size='XL' AND code NOT LIKE 'P_DUP%' ORDER BY code DESC LIMIT 1")).scalar_one()

    # Issue single item
    res = desk.post(f"{base}/issue", json={"recipient_code": p_code, "item": "special badge", "size": "NA", "quantity": 1}).json()
    issue_id = res["id"]
    assert res["status"] == "issued"

    # 60-second Undo by same user restores stock
    undo = desk.post(f"{base}/issues/{issue_id}/undo").json()
    assert undo["status"] == "voided"

    # Test substitution without consent fails
    sub_fail = desk.post(f"{base}/issue", json={"recipient_code": p_code, "item": "T-shirt", "size": "XXL", "consent_recorded": False})
    assert sub_fail.status_code == 422
    assert "consent" in sub_fail.json()["detail"].lower()

    # With consent recorded, substitution succeeds
    sub_ok = desk.post(f"{base}/issue", json={"recipient_code": p_code, "item": "T-shirt", "size": "XXL", "consent_recorded": True}).json()
    assert sub_ok["status"] == "issued"


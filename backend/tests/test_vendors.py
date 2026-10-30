"""Comprehensive tests for the Data-Driven Vendors Intelligence Module against the actual 14 vendor records."""
from pathlib import Path
from app.config import ROOT
from tests.helpers import client_for, festival_id


def test_vendors_overview_and_directory_match_actual_pack():
    admin = client_for("admin@festos.test")
    fid = festival_id(admin)
    base = f"/api/festivals/{fid}/vendors"

    intel = admin.get(f"{base}/intelligence")
    assert intel.status_code == 200
    data = intel.json()

    ov = data["overview"]
    # 1. All 14 appear in the directory
    assert ov["total_vendors"] == 14
    assert len(data["vendors"]) == 14
    assert [v["code"] for v in data["vendors"]] == [f"VND{i:02d}" for i in range(1, 15)]

    # Imported vendors are not automatically labeled operationally active
    assert all(v["operational_status"] == "unconfirmed" for v in data["vendors"])
    assert all(v["source"] == "pack" and v["original_pack_data"]["vendor_id"] == v["code"] for v in data["vendors"])

    # 2. Four certificates are flagged as expired (2026-09-29)
    assert ov["certificates_expired"] == 4
    assert sorted(ov["expired_vendor_codes"]) == ["VND05", "VND08", "VND11", "VND13"]

    # 3. Four certificates are flagged as expiring on 31 October (2026-10-31) with boundary flag
    assert ov["certificates_expiring_oct31"] == 4
    assert sorted(ov["expiring_vendor_codes"]) == ["VND01", "VND02", "VND04", "VND07"]
    for v in data["vendors"]:
        if v["code"] in ("VND01", "VND02", "VND04", "VND07"):
            assert v["certificate"]["status"] == "expires_during_festival"
            assert v["certificate"]["boundary_flag"] is True

    # 4. Six are valid beyond the festival
    assert ov["certificates_valid_beyond"] == 6
    assert sorted(ov["valid_beyond_vendor_codes"]) == ["VND03", "VND06", "VND09", "VND10", "VND12", "VND14"]


def test_dietary_intelligence_and_menu_inconsistencies():
    admin = client_for("admin@festos.test")
    fid = festival_id(admin)
    data = admin.get(f"/api/festivals/{fid}/vendors/intelligence").json()
    di = data["dietary_intelligence"]
    matrix = {r["key"]: r for r in di["coverage_matrix"]}

    # 5 & 6. Nut-free coverage is absent from listed options & 411 declared nut allergies are recognized
    assert di["participant_counts"]["nut allergy"] == 411
    assert matrix["nut-free"]["participant_count"] == 411
    assert matrix["nut-free"]["listed_count"] == 0
    assert matrix["nut-free"]["status"] == "critical_gap"
    assert any(a["id"] == "nut-free-gap" and a["severity"] == "critical" for a in data["alerts"])

    # 7. Gluten-free coverage identifies VND02 (387 participants, expiring Oct 31)
    assert matrix["gluten-free"]["participant_count"] == 387
    assert matrix["gluten-free"]["listed_codes"] == ["VND02"]
    assert matrix["gluten-free"]["expiring_during_codes"] == ["VND02"]
    assert matrix["gluten-free"]["valid_beyond_codes"] == []
    assert matrix["gluten-free"]["status"] == "single_expiring_dependency"

    # 8. Jain coverage identifies VND13 (380 participants, expired certificate)
    assert matrix["jain"]["participant_count"] == 380
    assert matrix["jain"]["listed_codes"] == ["VND13"]
    assert matrix["jain"]["expired_codes"] == ["VND13"]
    assert matrix["jain"]["status"] == "expired_only"

    # 9. Vegan coverage identifies VND08 (expired certificate)
    assert matrix["vegan"]["listed_codes"] == ["VND08"]
    assert matrix["vegan"]["expired_codes"] == ["VND08"]
    assert matrix["vegan"]["status"] == "expired_only"

    # 10 & 11. VND11 and VND09 menu inconsistencies are flagged
    inconsistencies = {(inc["vendor_code"], inc["key"]): inc for inc in di["inconsistencies"]}
    assert ("VND11", "menu_dietary_conflict") in inconsistencies
    assert "Vegan bowls" in inconsistencies[("VND11", "menu_dietary_conflict")]["detail"]
    assert ("VND09", "jain_menu_unconfirmed") in inconsistencies
    assert "Jain thali" in inconsistencies[("VND09", "jain_menu_unconfirmed")]["detail"]
    assert ("VND04", "breakfast_hours_mismatch") in inconsistencies


def test_operating_hours_and_unverified_capacity_constraint():
    admin = client_for("admin@festos.test")
    fid = festival_id(admin)
    data = admin.get(f"/api/festivals/{fid}/vendors/intelligence").json()
    hc = data["hours_and_capacity"]

    # 12. Operating hours are correctly displayed (11 at 07:00-23:00, 3 at 10:00-16:00)
    hours_map = {h["hours"]: h for h in hc["hours_distribution"]}
    assert hours_map["07:00-23:00"]["count"] == 11
    assert hours_map["10:00-16:00"]["count"] == 3
    assert sorted(hours_map["10:00-16:00"]["vendor_codes"]) == ["VND04", "VND08", "VND12"]

    # 13. Capacity is 3,773 and is NOT interpreted without a verified unit
    assert hc["total_listed_capacity"] == 3773
    assert hc["capacity_unit_status"] == "Capacity unit not verified."
    assert hc["numerical_allocation_enabled"] is False
    for v in data["vendors"]:
        assert v["capacity_unit"] is None
        assert v["capacity_unit_display"] == "Capacity unit not verified."


def test_duplicate_prevention_on_csv_import():
    admin = client_for("admin@festos.test")
    fid = festival_id(admin)
    csv_bytes = Path(ROOT, "festos_dataset_pack", "vendors.csv").read_bytes()
    res = admin.post(
        f"/api/festivals/{fid}/vendors/import",
        files={"file": ("vendors.csv", csv_bytes, "text/csv")},
    )
    assert res.status_code == 200
    payload = res.json()
    assert payload["created"] == 0
    assert len(payload["skipped_duplicates"]) == 14
    assert admin.get(f"/api/festivals/{fid}/vendors").json()["total"] == 14


def test_vendor_verification_preserves_provenance_and_audits():
    admin = client_for("admin@festos.test")
    fid = festival_id(admin)
    base = f"/api/festivals/{fid}/vendors"

    # Cannot confirm expired vendor VND05 as active without certificate verification
    bad = admin.post(
        f"{base}/VND05/verify",
        json={"operational_status": "confirmed_active", "reason": "Trying to activate expired vendor"},
    )
    assert bad.status_code == 422

    # Verify capacity unit and operational status on valid vendor VND03
    try:
        ok = admin.post(
            f"{base}/VND03/verify",
            json={
                "operational_status": "confirmed_active",
                "capacity_unit": "meals/day",
                "reason": "Verified stall throughput with vendor supervisor",
            },
        )
        assert ok.status_code == 200
        v3 = ok.json()
        assert v3["operational_status"] == "confirmed_active"
        assert v3["capacity_unit"] == "meals/day"
        assert v3["original_pack_data"]["vendor_id"] == "VND03"
        assert v3["original_pack_data"]["capacity"] == "361"

        # Detail endpoint returns audit history
        detail = admin.get(f"{base}/VND03").json()
        assert any(a["action"] == "vendor.verify" and a["reason"] == "Verified stall throughput with vendor supervisor" for a in detail["audit_history"])
    finally:
        # Reset VND03 to clean unconfirmed state
        admin.post(
            f"{base}/VND03/verify",
            json={
                "operational_status": "unconfirmed",
                "clear_capacity_unit": True,
                "reason": "Resetting test verification state",
            },
        )


def test_interruption_simulation_and_authorization():
    admin = client_for("admin@festos.test")
    fid = festival_id(admin)
    base = f"/api/festivals/{fid}/vendors"

    # 14. Simulate interruption of VND02 (only gluten-free vendor)
    sim = admin.post(
        f"{base}/VND02/interruptions/simulate",
        json={"reason": "Kitchen equipment issue during lunch prep"},
    )
    assert sim.status_code == 201
    interruption = sim.json()
    report = interruption["simulation_report"]

    assert interruption["status"] == "simulated"
    assert report["auto_reassignment_performed"] is False
    assert report["sufficient_coverage_established"] is False
    # Gluten-free (387 participants) must be flagged as uncovered critical requirement
    uncovered = {u["requirement"]: u for u in report["uncovered_requirements"]}
    assert "gluten-free" in uncovered
    assert uncovered["gluten-free"]["participant_count"] == 387
    assert uncovered["gluten-free"]["severity"] == "critical"

    # Expired vendors (VND05, VND08, VND11, VND13) must be excluded from compliant alternatives
    excluded_codes = {e["code"] for e in report["excluded_non_compliant"]}
    assert {"VND05", "VND08", "VND11", "VND13"}.issubset(excluded_codes)

    # Vendor operational_status is still unconfirmed (simulation did not mutate vendor status)
    v2 = admin.get(f"{base}/VND02").json()["vendor"]
    assert v2["operational_status"] == "unconfirmed"

    # 15. Unauthorized users cannot make restricted changes
    # Volunteer (st07@festos.test) cannot read or write vendors
    volunteer = client_for("st07@festos.test")
    assert volunteer.get(f"{base}/intelligence").status_code == 403
    assert volunteer.post(f"{base}/VND02/verify", json={"reason": "Unauthorized"}).status_code == 403

    # Accommodation Head (st04@festos.test) has vendors.write (can simulate) but NOT vendors.approve
    hosp_head = client_for("st04@festos.test")
    assert hosp_head.post(
        f"{base}/interruptions/{interruption['id']}/approve",
        json={"reason": "Trying to approve without vendors.approve", "replacement_codes": ["VND03"]},
    ).status_code == 403

    # Authorized user (admin) can reject the simulation to keep VND02 active
    rejected = admin.post(
        f"{base}/interruptions/{interruption['id']}/reject",
        json={"reason": "Equipment repaired before service window", "replacement_codes": []},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"

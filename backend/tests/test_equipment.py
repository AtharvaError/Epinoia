"""Equipment Request & Allocation, run against the real events.csv and inventory_logistics.csv rows."""
from datetime import datetime, timezone

from sqlalchemy import text

from app.database import SessionLocal
from tests.helpers import client_for, festival_id


def scalar(sql, **p):
    with SessionLocal() as db:
        return db.execute(text(sql), p).scalar_one()


def event_with(admin, base, needs):
    with SessionLocal() as db:
        return db.execute(text("SELECT code FROM events WHERE equipment_needs=:n AND deleted_at IS NULL ORDER BY code LIMIT 1"), {"n": needs}).scalar_one()


def test_event_needs_are_read_from_the_pack_without_inventing_quantities():
    admin = client_for("admin@festos.test")
    base = f"/api/festivals/{festival_id(admin)}/equipment"
    mics = admin.get(f"{base}/events/{event_with(admin, base, '2 mics; 1 speaker')}").json()
    assert [(n["item"], n["quantity"]) for n in mics["parsed_needs"]] == [("mic", 2), ("speaker", 1)]
    tables = admin.get(f"{base}/events/{event_with(admin, base, 'barricades; tables')}").json()
    assert [(n["item"], n["quantity"]) for n in tables["parsed_needs"]] == [("barricade", None), ("table", None)]
    none = admin.get(f"{base}/events/{event_with(admin, base, 'no special equipment')}").json()
    assert none["parsed_needs"] == [] and none["readiness"] == "no_equipment_needed"


def test_availability_matches_the_inventory_file_and_excludes_bad_condition():
    admin = client_for("admin@festos.test")
    base = f"/api/festivals/{festival_id(admin)}/equipment"
    cam = admin.get(f"{base}/availability", params={"item": "camera", "quantity": 2}).json()
    assert cam["good_available"] == 0 and cam["under_repair"] == 31 and cam["feasibility"] == "none"
    spk = admin.get(f"{base}/availability", params={"item": "speaker", "quantity": 20, "destination": "Main Stage"}).json()
    assert spk["good_total"] == 9 and spk["under_repair"] == 30 and spk["needs_check"] == 16
    assert spk["shortage"] == 20 - spk["good_available"]
    assert all(p["from_location"] == "Main Stage" for p in spk["plan"])
    assert set(admin.get(f"{base}/options").json()["inventory_locations"]) == {"Registration Desk", "Store Room A", "Main Stage", "Van 1", "Sports Complex"}


def test_full_request_approve_dispatch_receive_return_cycle():
    manager, logistics, staff = client_for("st03@festos.test"), client_for("st04@festos.test"), client_for("st07@festos.test")
    fid = festival_id(manager)
    base = f"/api/festivals/{fid}/equipment"
    event = event_with(manager, base, "2 mics; 1 speaker")
    before = scalar("SELECT coalesce(sum(reserved_quantity),0) FROM resources WHERE festival_id=:f AND name='mic'", f=fid)

    body = {"event_code": event, "item": "mic", "quantity": 2, "destination": "Main Stage",
            "needed_by": "2026-10-31T09:00:00+05:30", "priority": "high", "source_text": "2 mics"}
    assert staff.post(f"{base}/requests", json=body).status_code == 403
    req = manager.post(f"{base}/requests", json=body).json()
    assert req["status"] == "submitted" and req["source"] == "event_csv" and req["readiness"] == "awaiting_approval"
    assert req["transfers"][0]["status"] == "REQUESTED"

    assert manager.post(f"{base}/requests/{req['code']}/approve", json={}).status_code == 403
    approved = logistics.post(f"{base}/requests/{req['code']}/approve", json={}).json()
    assert approved["status"] == "approved" and approved["readiness"] == "pending_transfer"
    t = approved["transfers"][0]
    assert t["status"] == "APPROVED" and t["reserved_quantity"] == 2
    assert scalar("SELECT coalesce(sum(reserved_quantity),0) FROM resources WHERE festival_id=:f AND name='mic'", f=fid) == before + 2

    adv = lambda client, status, **kw: client.post(f"{base}/transfers/{t['id']}/advance", json={"status": status, **kw})
    assert adv(staff, "DISPATCHED").status_code == 403
    assert adv(logistics, "RECEIVED", quantity=2).status_code == 409
    assert adv(logistics, "DISPATCHED").status_code == 200
    assert adv(logistics, "IN_TRANSIT").status_code == 200
    assert adv(staff, "RECEIVED").status_code == 422
    assert adv(staff, "RECEIVED", quantity=2).status_code == 200
    assert adv(staff, "COMPLETED").status_code == 422  # needs verification
    done = adv(staff, "COMPLETED", verified=True).json()
    assert done["readiness"] == "satisfied" and done["verified_received"] == 2

    with SessionLocal() as db:
        end = db.execute(text("SELECT end_at, status FROM events WHERE festival_id=:f AND code=:c"), {"f": fid, "c": event}).one()
    if not (end.status == "completed" or (end.end_at and end.end_at <= datetime.now(timezone.utc))):
        assert staff.post(f"{base}/requests/{req['code']}/return", json={}).status_code == 409
        with SessionLocal() as db:
            db.execute(text("UPDATE events SET status='completed' WHERE festival_id=:f AND code=:c"), {"f": fid, "c": event})
            db.commit()
    try:
        ret = staff.post(f"{base}/requests/{req['code']}/return", json={"note": "Event over"}).json()
        r = next(x for x in ret["transfers"] if x["kind"] == "return")
        assert r["to_location"] == t["from_location"] and r["quantity_planned"] == 2
        step = lambda client, status, **kw: client.post(f"{base}/transfers/{r['id']}/advance", json={"status": status, **kw})
        for client, status, kw in [(logistics, "APPROVED", {}), (logistics, "DISPATCHED", {}), (logistics, "IN_TRANSIT", {}),
                                   (staff, "RECEIVED", {"quantity": 2}), (staff, "COMPLETED", {"verified": True, "damaged": 0})]:
            assert step(client, status, **kw).status_code == 200, status
        closed = manager.get(f"{base}/requests", params={"event": event}).json()["items"]
        assert next(x for x in closed if x["code"] == req["code"])["status"] == "closed"
        assert scalar("SELECT coalesce(sum(reserved_quantity),0) FROM resources WHERE festival_id=:f AND name='mic'", f=fid) == before
    finally:
        with SessionLocal() as db:
            db.execute(text("UPDATE events SET status=:s WHERE festival_id=:f AND code=:c"), {"s": end.status, "f": fid, "c": event})
            db.commit()


def test_unavailable_equipment_cannot_be_allocated_and_needs_check_needs_inspection():
    manager, logistics = client_for("st03@festos.test"), client_for("st04@festos.test")
    base = f"/api/festivals/{festival_id(manager)}/equipment"
    event = event_with(manager, base, "camera; tripod")
    req = manager.post(f"{base}/requests", json={"event_code": event, "item": "camera", "quantity": 2, "destination": "Main Stage",
                                                 "needed_by": "2026-10-31T09:00:00+05:30"}).json()
    assert req["readiness"] == "unavailable" and req["transfers"] == []
    assert logistics.post(f"{base}/requests/{req['code']}/approve", json={}).status_code == 409
    assert logistics.post(f"{base}/requests/{req['code']}/reject", json={"reason": "All cameras under repair"}).json()["status"] == "rejected"
    assert manager.post(f"{base}/requests", json={"event_code": event, "item": "drone", "quantity": 1, "destination": "x1",
                                                  "needed_by": "2026-10-31T09:00:00+05:30"}).status_code == 422
    assert logistics.post(f"{base}/resources/L00-1/inspect", json={"passed": 1, "failed": 0, "note": "check"}).status_code == 409
    assert manager.post(f"{base}/resources/L01-4/inspect", json={"passed": 1, "failed": 0, "note": "check"}).status_code == 403


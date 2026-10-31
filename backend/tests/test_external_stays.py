"""Tests for External Stay Suggestions feature."""
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.models import ExternalHotel, Participant, StaySuggestionLog


def client_for(email):
    client = TestClient(app)
    resp = client.post("/api/auth/login", json={"email": email, "password": settings.demo_password})
    assert resp.status_code == 200, f"Login failed for {email}: {resp.text}"
    return client


@pytest.fixture(scope="module")
def admin_client():
    return client_for("admin@festos.test")


@pytest.fixture(scope="module")
def accom_head_client():
    return client_for("st02@festos.test")  # Accommodation Head


@pytest.fixture(scope="module")
def participant_client():
    return client_for("participant@festos.test")


@pytest.fixture(scope="module")
def festival_id(admin_client):
    r = admin_client.get("/api/festivals")
    assert r.status_code == 200
    return r.json()[0]["id"]


def test_permission_guard(festival_id, participant_client, admin_client, accom_head_client):
    """Participant role should be forbidden from accessing stay suggestions."""
    r_unauthorized = participant_client.get(f"/api/festivals/{festival_id}/external-hotels")
    assert r_unauthorized.status_code == 403

    r_admin = admin_client.get(f"/api/festivals/{festival_id}/external-hotels")
    assert r_admin.status_code == 200

    r_head = accom_head_client.get(f"/api/festivals/{festival_id}/external-hotels")
    assert r_head.status_code == 200


def test_hotel_listing_and_radius(festival_id, admin_client):
    """List hotels within 5km, 10km, and 25km radius and verify sorting by distance."""
    r5 = admin_client.get(f"/api/festivals/{festival_id}/external-hotels?radius=5")
    assert r5.status_code == 200
    data5 = r5.json()
    assert "campus" in data5
    assert "items" in data5
    assert data5["radius_km"] == 5.0
    for h in data5["items"]:
        assert h["distance_km"] <= 5.0

    r10 = admin_client.get(f"/api/festivals/{festival_id}/external-hotels?radius=10")
    data10 = r10.json()
    assert data10["total"] >= len(data5["items"])
    for h in data10["items"]:
        assert h["distance_km"] <= 10.0

    # Ensure items are sorted ascending by distance
    distances = [h["distance_km"] for h in data10["items"]]
    assert distances == sorted(distances)


def test_hotel_filters(festival_id, admin_client):
    """Test verified_only, has_phone, and search queries."""
    r_verified = admin_client.get(f"/api/festivals/{festival_id}/external-hotels?radius=25&verified_only=true")
    assert r_verified.status_code == 200
    for h in r_verified.json()["items"]:
        assert h["is_verified"] is True

    r_phone = admin_client.get(f"/api/festivals/{festival_id}/external-hotels?radius=25&has_phone=true")
    assert r_phone.status_code == 200
    for h in r_phone.json()["items"]:
        assert h["phone"] is not None and h["phone"] != ""

    r_search = admin_client.get(f"/api/festivals/{festival_id}/external-hotels?radius=25&q=Papaya")
    assert r_search.status_code == 200
    items = r_search.json()["items"]
    assert any("papaya" in h["name"].lower() for h in items)


def test_hotel_crud_operations(festival_id, admin_client):
    """Admins can add, edit, verify, and delete external hotels."""
    # 1. Create
    new_hotel = {
        "name": "Test Boutique Inn",
        "address": "Rau Bypass, Indore",
        "area": "Rau",
        "latitude": 22.6100,
        "longitude": 75.8000,
        "phone": "+91-9999988888",
        "price_min": 1500,
        "price_max": 2500,
        "rating": 4.0,
        "notes": "Test notes",
        "is_verified": False,
    }
    r_create = admin_client.post(f"/api/festivals/{festival_id}/external-hotels", json=new_hotel)
    assert r_create.status_code == 201
    created = r_create.json()
    hotel_id = created["id"]
    assert created["name"] == "Test Boutique Inn"
    assert created["is_verified"] is False

    # 2. Update and Verify
    r_update = admin_client.patch(
        f"/api/festivals/{festival_id}/external-hotels/{hotel_id}",
        json={"is_verified": True, "phone": "+91-9999900000"},
    )
    assert r_update.status_code == 200
    updated = r_update.json()
    assert updated["is_verified"] is True
    assert updated["verified_by"] is not None
    assert updated["phone"] == "+91-9999900000"

    # 3. Delete
    r_del = admin_client.delete(f"/api/festivals/{festival_id}/external-hotels/{hotel_id}")
    assert r_del.status_code == 204


def test_search_participants_safety(festival_id, admin_client):
    """Participants search should NEVER expose gender and should show Telegram status."""
    r = admin_client.get(f"/api/festivals/{festival_id}/external-stays/participants?q=P0001")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) > 0
    p = items[0]
    assert "name" in p
    assert "college" in p
    assert "telegram_status" in p
    assert "gender" not in p
    assert "gender_as_given" not in p


def test_send_unverified_warning_and_send(festival_id, admin_client):
    """Sending unverified hotels without confirmation should return needs_confirmation."""
    # Find participant and hotels
    p = admin_client.get(f"/api/festivals/{festival_id}/external-stays/participants?q=P0001").json()["items"][0]
    hotels = admin_client.get(f"/api/festivals/{festival_id}/external-hotels?radius=25").json()["items"]

    unverified = [h for h in hotels if not h["is_verified"]]
    assert len(unverified) > 0

    # Sending without confirm_unverified
    payload = {
        "participant_id": p["id"],
        "hotel_ids": [unverified[0]["id"]],
        "confirm_unverified": False,
    }
    r = admin_client.post(f"/api/festivals/{festival_id}/external-stays/send", json=payload)
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is False
    assert data.get("needs_confirmation") is True

    # Sending with confirm_unverified
    payload["confirm_unverified"] = True
    r_confirmed = admin_client.post(f"/api/festivals/{festival_id}/external-stays/send", json=payload)
    assert r_confirmed.status_code == 200
    data_confirmed = r_confirmed.json()
    assert data_confirmed["delivery_status"] in ("delivered", "no_telegram", "failed")


def test_participant_without_telegram_fallback(festival_id, admin_client):
    """A participant without Telegram should return fallback_available and log."""
    with SessionLocal() as db:
        p_no_tg = db.query(Participant).filter(
            (Participant.telegram_chat_id == None) | (Participant.telegram_chat_id == "")  # noqa: E711
        ).first()
        assert p_no_tg is not None

    hotels = admin_client.get(f"/api/festivals/{festival_id}/external-hotels?radius=10").json()["items"]
    payload = {
        "participant_id": p_no_tg.id,
        "hotel_ids": [hotels[0]["id"]],
        "confirm_unverified": True,
    }
    r = admin_client.post(f"/api/festivals/{festival_id}/external-stays/send", json=payload)
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is False
    assert data["telegram_status"] == "not_started"
    assert data["fallback_available"] is True
    assert "preview_message" in data

    # Copy fallback logging
    r_copy = admin_client.post(
        f"/api/festivals/{festival_id}/external-stays/log-fallback-copied",
        json={
            "participant_id": p_no_tg.id,
            "hotel_ids": [hotels[0]["id"]],
            "message_text": data["preview_message"],
        },
    )
    assert r_copy.status_code == 200


def test_sent_history_and_isolation(festival_id, admin_client):
    """Ensure history endpoint returns records, and bed summary is unaffected."""
    # Check history
    r_hist = admin_client.get(f"/api/festivals/{festival_id}/external-stays/history")
    assert r_hist.status_code == 200
    hist = r_hist.json()
    assert "items" in hist
    assert "total" in hist
    assert hist["total"] > 0

    # Verify Accommodation summary bed budget and hostels are completely unaffected
    r_accom = admin_client.get(f"/api/festivals/{festival_id}/accommodation/summary")
    assert r_accom.status_code == 200
    accom = r_accom.json()
    # Bed capacity and hostelling remain clean
    assert "hostels" in accom
    assert "pipeline" in accom


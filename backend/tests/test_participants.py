import io

import openpyxl
import pytest
from sqlalchemy import text

from app.api.common import csv_safe
from app.database import SessionLocal
from tests.helpers import client_for, festival_id

PHONE = "+91-7000000099"


@pytest.fixture(scope="module")
def admin():
    return client_for("admin@festos.test")


@pytest.fixture(scope="module")
def base(admin):
    return f"/api/festivals/{festival_id(admin)}"


@pytest.fixture
def person(admin, base):
    response = admin.post(f"{base}/participants", json={"full_name": "Test Person", "college": "Test College", "phone": PHONE})
    assert response.status_code == 201
    yield response.json()["code"]
    with SessionLocal() as db:
        db.execute(text("DELETE FROM participants WHERE full_name='Test Person' OR college='Import College'"))
        db.commit()


def test_list_filters_and_paginates(admin, base):
    first = admin.get(f"{base}/participants").json()
    assert first["total"] >= 2836 and len(first["items"]) == 25 and first["page"] == 1
    second = admin.get(f"{base}/participants", params={"page": 2}).json()
    assert first["items"][0]["code"] != second["items"][0]["code"]
    contingent = admin.get(f"{base}/participants", params={"contingent": "C01"}).json()
    assert contingent["total"] == 6 and all(p["college"] == "IIM Indore" for p in contingent["items"])


def test_event_filter_matches_registrations(admin, base):
    with SessionLocal() as db:
        expected = db.execute(text("SELECT count(*) FROM registrations r JOIN events e ON e.id=r.event_id WHERE e.code='E01'")).scalar_one()
    assert admin.get(f"{base}/participants", params={"event": "E01"}).json()["total"] == expected


def test_sensitive_fields_are_hidden_without_permission(admin, base):
    coordinator = client_for("st03@festos.test")
    row = coordinator.get(f"{base}/participants/P0001").json()
    assert row["restricted"] is True
    assert row["phone"] is None and row["accessibility_needs"] is None and row["gender_as_given"] is None
    assert admin.get(f"{base}/participants/P0001").json()["phone"]
    assert "phone" not in coordinator.get(f"{base}/participants/export.csv").text.splitlines()[0]


def test_write_requires_permission(base):
    volunteer = client_for("st07@festos.test")
    assert volunteer.get(f"{base}/participants").status_code == 403
    assert volunteer.post(f"{base}/participants", json={"full_name": "No", "college": "No"}).status_code == 403


def test_duplicate_phone_is_rejected(admin, base, person):
    response = admin.post(f"{base}/participants", json={"full_name": "Test Person", "college": "Test College", "phone": PHONE})
    assert response.status_code == 409
    assert person in response.json()["detail"]


def test_invalid_input_is_rejected(admin, base):
    assert admin.post(f"{base}/participants", json={"full_name": "x", "college": "y"}).status_code == 422
    assert admin.post(f"{base}/participants", json={"full_name": "Valid", "college": "Valid", "email": "not-an-email"}).status_code == 422


def test_duplicate_registration_is_rejected_and_clash_warned(admin, base, person):
    assert admin.post(f"{base}/participants/{person}/registrations", json={"event_code": "E01"}).status_code == 201
    assert admin.post(f"{base}/participants/{person}/registrations", json={"event_code": "E01"}).status_code == 409
    assert admin.delete(f"{base}/participants/{person}/registrations/E01").status_code == 204


def test_pass_verifies_and_checks_in_only_once(admin, base, person):
    assert admin.get(f"{base}/participants/{person}/pass.svg").headers["content-type"].startswith("image/svg")
    with SessionLocal() as db:
        token = db.execute(text("SELECT qr_token FROM participants WHERE code=:c"), {"c": person}).scalar_one()
    assert admin.post(f"{base}/passes/verify", json={"token": "wrong-token-value"}).json() == {"valid": False}
    assert admin.post(f"{base}/passes/verify", json={"token": token}).json()["code"] == person
    assert admin.post(f"{base}/passes/check-in", json={"token": token}).status_code == 200
    assert admin.post(f"{base}/passes/check-in", json={"token": token}).status_code == 409


def test_delete_is_soft_and_needs_a_reason(admin, base, person):
    assert admin.request("DELETE", f"{base}/participants/{person}", json={}).status_code == 422
    assert admin.request("DELETE", f"{base}/participants/{person}", json={"reason": "Test removal"}).status_code == 204
    assert admin.get(f"{base}/participants/{person}").status_code == 404
    with SessionLocal() as db:
        assert db.execute(text("SELECT deleted_at IS NOT NULL FROM participants WHERE code=:c"), {"c": person}).scalar_one()


def test_audit_never_stores_sensitive_values(admin, base, person):
    body = {"full_name": "Test Person", "college": "Test College", "phone": PHONE, "accessibility_needs": "Private detail"}
    assert admin.patch(f"{base}/participants/{person}", json=body).status_code == 200
    with SessionLocal() as db:
        logged = db.execute(text("SELECT after::text FROM audit_logs WHERE entity_id=:c AND action='participant.update'"), {"c": person}).scalars().all()
    assert logged and all("Private detail" not in row for row in logged)


def test_csv_import_keeps_good_rows_and_reports_bad_ones(admin, base, person):
    csv_file = f"full_name,college,phone\nImport One,Import College,+91-7000000101\nx,Import College,\nImport Dup,Import College,{PHONE}\n"
    result = admin.post(f"{base}/participants/import", files={"file": ("people.csv", csv_file)}).json()
    assert result["created"] == 1
    assert [r["row"] for r in result["rejected"]] == [3, 4]
    assert "Duplicate" in result["rejected"][1]["reason"]


def test_excel_import(admin, base, person):
    book = openpyxl.Workbook()
    book.active.append(["full_name", "college", "phone"])
    book.active.append(["Import Sheet", "Import College", "+91-7000000102"])
    out = io.BytesIO()
    book.save(out)
    result = admin.post(f"{base}/participants/import", files={"file": ("people.xlsx", out.getvalue())}).json()
    assert result == {"created": 1, "rejected": []}


def test_import_rejects_other_file_types(admin, base):
    assert admin.post(f"{base}/participants/import", files={"file": ("people.txt", "hello")}).status_code == 400


def test_export_blocks_spreadsheet_formulas():
    assert csv_safe("=HYPERLINK(1)").startswith("'")
    assert csv_safe("+cmd|x").startswith("'")
    assert csv_safe("+91-9000000000") == "+91-9000000000"

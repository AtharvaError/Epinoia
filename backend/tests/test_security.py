"""Security checks. Routes are enumerated from the API itself, so a new one cannot be missed."""
import re
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.auth import failures
from app.auth.permissions import ROLES
from app.auth.verifier import hash_password, issue_token
from app.config import Settings, settings
from app.database import SessionLocal
from app.main import app
from app.models import Festival, Role, User
from tests.helpers import client_for, festival_id

PUBLIC = {("get", "/api/health"), ("get", "/api/ready"), ("post", "/api/auth/login"), ("post", "/api/auth/logout")}
# Any signed-in person may use these: they only touch that person's own session or inbox.
ANY_MEMBER = {"/api/auth/me", "/api/auth/password", "/api/festivals", "/api/festivals/{festival_id}/notifications", "/api/festivals/{festival_id}/notifications/{notification_id}/read",
              "/api/festivals/{festival_id}/notifications/{notification_id}/acknowledge", "/api/festivals/{festival_id}/search", "/api/festivals/{festival_id}/assistant"}


def routes():
    client = TestClient(app)
    for path, methods in client.get("/api/openapi.json").json()["paths"].items():
        for method in methods:
            yield method, path


def filled(path, fid=1):
    return re.sub(r"\{(\w+)\}", lambda m: str(fid) if m[1] == "festival_id" else ("1" if m[1].endswith("id") else "X"), path)


def call(client, method, path):
    return client.request(method.upper(), filled(path), json={})


def scalar(sql, **params):
    with SessionLocal() as db:
        return db.execute(text(sql), params).scalar_one()


def test_the_route_list_is_real():
    assert len(list(routes())) > 100


def test_every_route_needs_a_session_except_the_public_ones():
    client = TestClient(app)
    open_routes = [(m, p) for m, p in routes() if (m, p) not in PUBLIC and call(client, m, p).status_code != 401]
    assert open_routes == []


def test_the_portal_has_no_participant_login():
    assert "participant" not in ROLES
    failures.clear()
    refused = TestClient(app).post("/api/auth/login", json={"email": "participant@festos.test", "password": settings.demo_password})
    assert refused.status_code == 401
    with SessionLocal() as db:
        assert db.scalar(text("SELECT count(*) FROM roles WHERE key='participant'")) == 0


def test_a_person_with_no_permissions_is_refused_everywhere():
    with SessionLocal() as db:
        role = Role(key="no_access", name="No access")
        db.add(role)
        db.flush()
        db.add(User(email="nobody@festos.test", password_hash=hash_password(settings.demo_password), name="Nobody", role_id=role.id, festival_id=db.query(Festival).first().id))
        db.commit()
    client = client_for("nobody@festos.test")
    exposed = []
    for method, path in routes():
        if (method, path) in PUBLIC or path in ANY_MEMBER or path.startswith(("/api/docs", "/api/openapi")):
            continue
        status = call(client, method, path).status_code
        if status not in (403, 404, 405):
            exposed.append((method, path, status))
    assert exposed == []


def test_members_of_one_festival_cannot_reach_another():
    with SessionLocal() as db:
        other = db.execute(text("INSERT INTO festivals (slug,name,start_date,end_date) VALUES ('isolated','Isolated','2027-01-01','2027-01-02') RETURNING id")).scalar_one()
        db.commit()
    director = client_for("st08@festos.test")
    try:
        leaks = [(m, p) for m, p in routes() if "{festival_id}" in p and director.request(m.upper(), filled(p, other), json={}).status_code not in (403, 404, 405, 422)]
        assert leaks == []
    finally:
        with SessionLocal() as db:
            db.execute(text("DELETE FROM festivals WHERE id=:i"), {"i": other})
            db.commit()


def test_records_cannot_be_reached_through_another_festivals_path():
    with SessionLocal() as db:
        other = db.execute(text("INSERT INTO festivals (slug,name,start_date,end_date) VALUES ('second','Second','2027-01-01','2027-01-02') RETURNING id")).scalar_one()
        expense = db.execute(text("SELECT id FROM expenses LIMIT 1")).scalar_one()
        shift = db.execute(text("SELECT id FROM volunteer_shifts LIMIT 1")).scalar_one()
        db.commit()
    admin = client_for("admin@festos.test")
    try:
        assert admin.post(f"/api/festivals/{other}/finance/expenses/{expense}/reject", json={"reason": "Cross festival"}).status_code == 404
        assert admin.patch(f"/api/festivals/{other}/shifts/{shift}", json={"status": "cancelled"}).status_code == 404
    finally:
        with SessionLocal() as db:
            db.execute(text("DELETE FROM festivals WHERE id=:i"), {"i": other})
            db.commit()


def test_logout_revokes_a_copied_token():
    client = client_for("st03@festos.test")
    stolen = client.cookies.get("festos_session")
    assert client.post("/api/auth/logout").status_code == 204
    thief = TestClient(app)
    thief.cookies.set("festos_session", stolen)
    assert thief.get("/api/auth/me").status_code == 401


def test_forged_and_tampered_tokens_fail():
    client = TestClient(app)
    client.cookies.set("festos_session", issue_token(1, 0) + "x")
    assert client.get("/api/auth/me").status_code == 401
    import jwt
    client.cookies.set("festos_session", jwt.encode({"sub": "1", "v": 0}, "wrong-secret-that-is-long-enough-for-hs256", algorithm="HS256"))
    assert client.get("/api/auth/me").status_code == 401
    client.cookies.set("festos_session", jwt.encode({"sub": "1", "v": 0}, "", algorithm="none"))
    assert client.get("/api/auth/me").status_code == 401


def test_session_cookie_flags():
    response = TestClient(app).post("/api/auth/login", json={"email": "st03@festos.test", "password": settings.demo_password})
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    client_for("st03@festos.test")


def test_unknown_accounts_take_as_long_as_real_ones():
    client = TestClient(app)

    def timed(email):
        failures.clear()
        started = time.perf_counter()
        client.post("/api/auth/login", json={"email": email, "password": "not-the-password"})
        return time.perf_counter() - started

    known = sum(timed("st03@festos.test") for _ in range(3))
    unknown = sum(timed("nobody@festos.test") for _ in range(3))
    assert unknown > known * 0.5


def test_one_address_cannot_spray_many_accounts():
    failures.clear()
    client = TestClient(app)
    codes = [client.post("/api/auth/login", json={"email": f"spray{i}@festos.test", "password": "x"}).status_code for i in range(32)]
    assert codes[0] == 401 and codes[-1] == 429
    failures.clear()


def test_changing_password_ends_other_sessions_and_needs_the_old_one():
    laptop = client_for("st05@festos.test")
    phone = client_for("st05@festos.test")
    assert laptop.post("/api/auth/password", json={"current_password": "wrong", "new_password": "a-long-new-password"}).status_code == 403
    assert laptop.post("/api/auth/password", json={"current_password": settings.demo_password, "new_password": "short"}).status_code == 422
    assert laptop.post("/api/auth/password", json={"current_password": settings.demo_password, "new_password": "a-long-new-password"}).status_code == 204
    assert phone.get("/api/auth/me").status_code == 401
    assert laptop.get("/api/auth/me").status_code == 200


def test_no_response_ever_contains_a_password_hash():
    client = client_for("admin@festos.test")
    fid = festival_id(client)
    assert "password_hash" not in client.get("/api/auth/me").text
    assert "$2b$" not in client.get(f"/api/festivals/{fid}/users").text


def test_users_cannot_raise_their_own_access():
    director = client_for("st08@festos.test")
    fid = festival_id(director)
    me = director.get("/api/auth/me").json()["id"]
    assert director.patch(f"/api/festivals/{fid}/users/{me}", json={"role_key": "super_admin"}).status_code == 409
    other = scalar("SELECT id FROM users WHERE email='st03@festos.test'")
    assert director.patch(f"/api/festivals/{fid}/users/{other}", json={"role_key": "super_admin"}).status_code == 403
    assert client_for("st03@festos.test").patch(f"/api/festivals/{fid}/users/{other}", json={"role_key": "festival_director"}).status_code == 403


def test_role_changes_apply_immediately():
    director = client_for("st08@festos.test")
    fid = festival_id(director)
    target = client_for("volunteers@festos.test")
    uid = target.get("/api/auth/me").json()["id"]
    assert director.patch(f"/api/festivals/{fid}/users/{uid}", json={"is_active": False}).status_code == 200
    assert target.get("/api/auth/me").status_code == 401
    assert director.patch(f"/api/festivals/{fid}/users/{uid}", json={"is_active": True}).status_code == 200


@pytest.mark.parametrize("attack", ["' OR '1'='1", "'; DROP TABLE participants; --", "%' OR 1=1 --", "\\\\ '"])
def test_sql_injection_in_search_is_harmless(attack):
    client = client_for("admin@festos.test")
    fid = festival_id(client)
    before = scalar("SELECT count(*) FROM participants")
    assert client.get(f"/api/festivals/{fid}/search", params={"q": attack}).status_code == 200
    assert client.get(f"/api/festivals/{fid}/participants", params={"q": attack}).json()["total"] == 0
    assert scalar("SELECT count(*) FROM participants") == before


def test_security_headers_are_on_every_api_response():
    response = TestClient(app).get("/api/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"]


def test_server_errors_reveal_nothing_but_a_reference(monkeypatch):
    client = client_for("admin@festos.test")
    fid = festival_id(client)
    import app.analytics.dashboard as module
    import app.api.festivals as festivals

    def boom(*_):
        raise RuntimeError("secret database detail")

    monkeypatch.setattr(festivals, "dashboard", boom)
    safe = TestClient(app, raise_server_exceptions=False)
    safe.cookies.update(client.cookies)
    response = safe.get(f"/api/festivals/{fid}/dashboard")
    assert response.status_code == 500
    assert "secret" not in response.text and "Reference:" in response.text
    assert module is not None


def test_readiness_reports_the_database():
    assert TestClient(app).get("/api/ready").json() == {"status": "ready"}


@pytest.mark.parametrize("change,message", [
    ({"jwt_secret": "short"}, "JWT_SECRET"),
    ({"jwt_secret": "change-me" * 5}, "JWT_SECRET"),
    ({"database_url": "postgresql://festos:change-me@db/festos"}, "DATABASE_URL"),
    ({"cookie_secure": False}, "COOKIE_SECURE"),
])
def test_production_refuses_unsafe_settings(change, message):
    good = {"environment": "production", "jwt_secret": "x" * 40, "database_url": "postgresql://u:strong@db/festos", "cookie_secure": True}
    with pytest.raises(ValueError, match=message):
        Settings(**{**good, **change})


def test_production_accepts_safe_settings():
    assert Settings(environment="production", jwt_secret="x" * 40, database_url="postgresql://u:strong@db/festos", cookie_secure=True).cookie_secure

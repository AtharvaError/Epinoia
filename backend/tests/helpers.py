from fastapi.testclient import TestClient

from app.api.auth import failures
from app.config import settings
from app.main import app


def client_for(email: str) -> TestClient:
    failures.clear()
    client = TestClient(app)
    response = client.post("/api/auth/login", json={"email": email, "password": settings.demo_password})
    assert response.status_code == 200
    return client


def festival_id(client: TestClient) -> int:
    return client.get("/api/festivals").json()[0]["id"]

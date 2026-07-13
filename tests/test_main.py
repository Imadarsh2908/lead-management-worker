from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app


def test_health_check(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["service"] == "lead-management-worker"


def test_root_endpoint(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "visit /docs for Swagger UI" in response.json()["message"]


def test_global_exception_handler():
    # Use raise_server_exceptions=False so FastAPI's global handler catches it
    # instead of the TestClient re-raising the raw exception.
    with TestClient(app, raise_server_exceptions=False) as err_client:
        with patch("sqlalchemy.orm.Session.query", side_effect=RuntimeError("Mocked DB Crash")):
            response = err_client.get("/v1/leads/")
    assert response.status_code == 500
    data = response.json()
    assert data["error"] == "Internal server error"
    assert "An unexpected error occurred" in data["detail"]

"""Health endpoints. Uses the dev databases from .env; "down" means a closed port."""

import pytest
from fastapi.testclient import TestClient

from app.main import app

CLOSED_POSTGRES = "postgresql+psycopg://av_app:x@127.0.0.1:1/av"
CLOSED_MONGO = "mongodb://127.0.0.1:1/?serverSelectionTimeoutMS=200"


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_liveness_does_not_touch_databases(monkeypatch):
    monkeypatch.setenv("APP_DATABASE_URL", CLOSED_POSTGRES)
    monkeypatch.setenv("MONGO_URL", CLOSED_MONGO)
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_when_both_databases_are_up(client):
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"postgres": "ok", "mongo": "ok"},
    }


def test_not_ready_when_postgres_is_down(monkeypatch):
    monkeypatch.setenv("APP_DATABASE_URL", CLOSED_POSTGRES)
    with TestClient(app) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "checks": {"postgres": "error", "mongo": "ok"},
    }


def test_not_ready_when_mongo_is_down(monkeypatch):
    monkeypatch.setenv("MONGO_URL", CLOSED_MONGO)
    with TestClient(app) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "checks": {"postgres": "ok", "mongo": "error"},
    }

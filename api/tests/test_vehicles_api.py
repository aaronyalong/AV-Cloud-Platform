"""Vehicle and telemetry APIs against the av_test databases."""

import uuid
from datetime import UTC, datetime, timedelta

import anyio
import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from app.main import app
from avdata import mongo as avmongo
from avdata.messages import Envelope
from avdata.mongo import TenantDocuments
from workers.seed import seed_demo_fleet

ACME = {"X-Tenant-Id": "t-acme"}
BETA = {"X-Tenant-Id": "t-beta"}
SAMPLE = {
    "speed_kmh": 30.0,
    "x": 1.0,
    "y": 2.0,
    "heading_deg": 90.0,
    "throttle": 0.3,
    "brake": 0.0,
    "steer": 0.1,
}


@pytest.fixture(scope="module")
def recent() -> datetime:
    return datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=20)


@pytest.fixture(scope="module")
def seeded(migrated_db, pg_url, pg_conninfo, mongo_url, recent):
    """Demo fleet with one vehicle in each connectivity state, and telemetry."""

    async def seed() -> None:
        owner = create_async_engine(pg_url("av_owner"))
        await seed_demo_fleet(owner)
        await owner.dispose()
        client = avmongo.connect(mongo_url)
        await client.drop_database("av_test")
        db = client["av_test"]
        await avmongo.ensure_indexes(db)
        docs = TenantDocuments(db, "t-acme")
        for i in range(10):
            await docs.add_telemetry_sample(
                Envelope(
                    msg_id=uuid.uuid4().hex,
                    tenant_id="t-acme",
                    vehicle_id="veh-07",
                    run_id="r1",
                    ts=recent + timedelta(seconds=i),
                    channel="telemetry",
                    data={**SAMPLE, "speed_kmh": float(i)},
                )
            )
        for tenant, vehicle, seconds in [
            ("t-acme", "veh-07", 1),
            ("t-acme", "veh-07", 5),
            ("t-beta", "veh-01", 3),
        ]:
            await TenantDocuments(db, tenant).add_event(
                Envelope(
                    msg_id=uuid.uuid4().hex,
                    tenant_id=tenant,
                    vehicle_id=vehicle,
                    run_id="r1",
                    ts=recent + timedelta(seconds=seconds),
                    channel="event",
                    data={"type": "collision", "severity": "low"},
                )
            )
        await client.close()

    anyio.run(seed)
    with psycopg.connect(pg_conninfo("postgres")) as conn:
        conn.execute("""
            UPDATE iot_device SET
              last_heartbeat_at = CASE vehicle_id
                WHEN 'veh-07' THEN now() - interval '10 seconds'
                WHEN 'veh-09' THEN now() - interval '60 seconds'
                WHEN 'veh-01' THEN now() - interval '5 seconds'
              END,
              last_disconnect_at = CASE vehicle_id
                WHEN 'veh-01' THEN now() END
            WHERE tenant_id IN ('t-acme', 't-beta')
        """)


@pytest.fixture
def client(seeded, monkeypatch, pg_url):
    monkeypatch.setenv("APP_DATABASE_URL", pg_url("av_app"))
    monkeypatch.setenv("MONGO_DB", "av_test")
    with TestClient(app) as test_client:
        yield test_client


def test_missing_tenant_header_is_401(client):
    assert client.get("/api/v1/vehicles").status_code == 401
    assert client.get("/api/v1/telemetry?vehicle_id=veh-07").status_code == 401


def test_vehicles_have_connectivity_status(client):
    response = client.get("/api/v1/vehicles", headers=ACME)
    assert response.status_code == 200
    statuses = {v["vehicle_id"]: v["status"] for v in response.json()["vehicles"]}
    assert statuses == {
        "veh-07": "ONLINE",
        "veh-09": "STALE",
        "veh-12": "NEVER_CONNECTED",
    }


def test_disconnected_vehicle_is_offline(client):
    vehicles = client.get("/api/v1/vehicles", headers=BETA).json()["vehicles"]
    assert [(v["vehicle_id"], v["status"]) for v in vehicles] == [("veh-01", "OFFLINE")]


def test_tenant_sees_only_its_own_vehicles(client):
    acme = {
        v["vehicle_id"]
        for v in client.get("/api/v1/vehicles", headers=ACME).json()["vehicles"]
    }
    beta = {
        v["vehicle_id"]
        for v in client.get("/api/v1/vehicles", headers=BETA).json()["vehicles"]
    }
    assert acme == {"veh-07", "veh-09", "veh-12"}
    assert beta == {"veh-01"}


def test_unknown_tenant_sees_nothing(client):
    response = client.get("/api/v1/vehicles", headers={"X-Tenant-Id": "t-nobody"})
    assert response.json()["vehicles"] == []


def test_telemetry_for_own_vehicle(client, recent):
    params = {"vehicle_id": "veh-07", "from": recent.isoformat()}
    body = client.get("/api/v1/telemetry", params=params, headers=ACME).json()
    assert body["total_samples"] == 10
    assert [p["speed_kmh"] for p in body["points"]] == [float(i) for i in range(10)]
    assert set(body["points"][0]) == {"ts", *SAMPLE}


def test_other_tenants_telemetry_is_not_found(client, recent):
    params = {"vehicle_id": "veh-07", "from": recent.isoformat()}
    response = client.get("/api/v1/telemetry", params=params, headers=BETA)
    assert response.status_code == 404
    assert response.json() == {"detail": "vehicle not found"}


def test_telemetry_fields_and_max_points(client, recent):
    params = {
        "vehicle_id": "veh-07",
        "from": recent.isoformat(),
        "fields": "speed_kmh",
        "max_points": 5,
    }
    body = client.get("/api/v1/telemetry", params=params, headers=ACME).json()
    assert [set(p) for p in body["points"]] == [{"ts", "speed_kmh"}] * 5
    assert [p["speed_kmh"] for p in body["points"]] == [0.0, 2.0, 4.0, 6.0, 8.0]


def test_telemetry_rejects_unknown_fields(client):
    params = {"vehicle_id": "veh-07", "fields": "speed_kmh,password"}
    response = client.get("/api/v1/telemetry", params=params, headers=ACME)
    assert response.status_code == 422


def test_events_are_newest_first_and_tenant_scoped(client, recent):
    acme = client.get("/api/v1/events", headers=ACME).json()["events"]
    beta = client.get("/api/v1/events", headers=BETA).json()["events"]
    assert [(e["vehicle_id"], e["ts"]) for e in acme] == [
        ("veh-07", (recent + timedelta(seconds=5)).isoformat().replace("+00:00", "Z")),
        ("veh-07", (recent + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")),
    ]
    assert [e["vehicle_id"] for e in beta] == ["veh-01"]
    assert acme[0]["type"] == "collision"


def test_other_tenants_vehicle_events_are_not_found(client):
    response = client.get("/api/v1/events?vehicle_id=veh-07", headers=BETA)
    assert response.status_code == 404

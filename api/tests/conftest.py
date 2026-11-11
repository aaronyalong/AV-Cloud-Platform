"""Shared API test fixtures: the demo fleet in av_test, one vehicle per status.

veh-07 ONLINE, veh-09 STALE, veh-12 NEVER_CONNECTED (t-acme); veh-01 OFFLINE (t-beta).
"""

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

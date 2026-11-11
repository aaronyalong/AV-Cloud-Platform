"""Ingest worker against the av_test databases, seeded with the demo fleet."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from avdata import mongo as avmongo
from avdata.messages import Envelope
from avdata.postgres import tenant_transaction
from workers.ingest import IngestWorker
from workers.queue import InProcessQueue
from workers.seed import seed_demo_fleet
from workers.simulator import Simulator

pytestmark = pytest.mark.anyio

T0 = datetime(2026, 11, 11, 19, 4, 10, tzinfo=UTC)
SAMPLE = {
    "speed_kmh": 34.2,
    "x": 112.4,
    "y": -8.1,
    "heading_deg": 90.0,
    "throttle": 0.3,
    "brake": 0.0,
    "steer": 0.05,
}


def message(channel="telemetry", tenant="t-acme", vehicle="veh-07", ts=T0, data=None):
    return Envelope(
        msg_id=uuid.uuid4().hex,
        tenant_id=tenant,
        vehicle_id=vehicle,
        run_id="r1",
        ts=ts,
        channel=channel,
        data=SAMPLE if data is None and channel == "telemetry" else (data or {}),
    ).to_message()


@pytest.fixture
async def worker(migrated_db, pg_url, mongo_url):
    owner = create_async_engine(pg_url("av_owner"))
    await seed_demo_fleet(owner)
    await owner.dispose()
    # Reset connection times as the superuser: av_owner is subject to RLS too.
    superuser = create_async_engine(pg_url("postgres"))
    async with superuser.begin() as conn:
        await conn.execute(
            text(
                "UPDATE iot_device SET last_heartbeat_at = NULL, last_disconnect_at = NULL"
            )
        )
        await conn.execute(text("UPDATE vehicle SET last_seen_at = NULL"))
    await superuser.dispose()

    engine = create_async_engine(pg_url("av_app"))
    client = avmongo.connect(mongo_url)
    await client.drop_database("av_test")
    db = client["av_test"]
    await avmongo.ensure_indexes(db)
    yield IngestWorker(engine, db)
    await engine.dispose()
    await client.close()


async def device_times(worker, tenant="t-acme", vehicle="veh-07"):
    async with tenant_transaction(worker.engine, tenant) as conn:
        row = await conn.execute(
            text(
                "SELECT d.last_heartbeat_at, d.last_disconnect_at, v.last_seen_at"
                " FROM iot_device d JOIN vehicle v USING (tenant_id, vehicle_id)"
                " WHERE d.vehicle_id = :v"
            ),
            {"v": vehicle},
        )
        return row.one()


async def test_duplicate_telemetry_is_stored_once(worker):
    msg = message()
    outcomes = [await worker.handle(msg) for _ in range(3)]
    assert outcomes == ["telemetry", "duplicate", "duplicate"]

    buckets = await worker.mongo_db["telemetry"].find().to_list()
    assert len(buckets) == 1
    assert buckets[0]["msg_ids"] == [msg["msg_id"]]
    assert len(buckets[0]["samples"]) == 1


async def test_samples_share_a_bucket_per_minute(worker):
    for seconds in (0, 30, 65):
        await worker.handle(message(ts=T0 + timedelta(seconds=seconds)))

    buckets = await worker.mongo_db["telemetry"].find().sort("bucket_start").to_list()
    assert [len(b["samples"]) for b in buckets] == [2, 1]
    assert buckets[0]["bucket_start"] == T0.replace(second=0)
    assert buckets[0]["tenant_id"] == "t-acme"
    assert buckets[0]["expires_at"] == T0.replace(second=0) + timedelta(days=90)


async def test_replayed_simulator_messages_are_stored_once(worker):
    """The simulator's --replay-duplicates path, end to end through the queue."""
    queue = InProcessQueue()
    simulator = Simulator(queue, run_id="r1", replay_duplicates=5)
    vehicle = simulator.vehicles[0]
    for _ in range(5):
        await simulator.send(vehicle, "telemetry", vehicle.step(0.5))
    for msg in list(simulator.recent):
        await queue.publish(msg)

    outcomes = []
    for _ in range(10):
        msg = await queue.receive()
        outcomes.append(await worker.handle(msg))
        queue.ack(msg)
    assert outcomes == ["telemetry"] * 5 + ["duplicate"] * 5


async def test_heartbeat_updates_times_and_never_moves_back(worker):
    await worker.handle(message("heartbeat", ts=T0))
    await worker.handle(message("heartbeat", ts=T0 - timedelta(seconds=30)))
    heartbeat, disconnect, last_seen = await device_times(worker)
    assert heartbeat == T0
    assert last_seen == T0
    assert disconnect is None


async def test_disconnect_is_recorded(worker):
    await worker.handle(message("heartbeat", ts=T0))
    await worker.handle(message("disconnect", ts=T0 + timedelta(seconds=5)))
    heartbeat, disconnect, _ = await device_times(worker)
    assert (heartbeat, disconnect) == (T0, T0 + timedelta(seconds=5))


async def test_telemetry_does_not_update_heartbeat(worker):
    await worker.handle(message("telemetry"))
    assert await device_times(worker) == (None, None, None)


async def test_vehicle_of_another_tenant_is_rejected(worker):
    """veh-07 belongs to t-acme, so t-beta can't write data for it."""
    assert await worker.handle(message(tenant="t-beta", vehicle="veh-07")) == "rejected"
    assert await worker.handle(message("heartbeat", tenant="t-beta")) == "rejected"
    assert await worker.mongo_db["telemetry"].count_documents({}) == 0
    assert await device_times(worker) == (None, None, None)


async def test_invalid_messages_are_rejected(worker):
    missing_id = message()
    del missing_id["msg_id"]
    bad_data = message(data={"speed_kmh": "fast"})
    assert await worker.handle(missing_id) == "rejected"
    assert await worker.handle(bad_data) == "rejected"
    assert worker.stats["rejected"] == 2


async def test_duplicate_event_is_stored_once(worker):
    event = message("event", data={"type": "collision", "severity": "high"})
    outcomes = [await worker.handle(event) for _ in range(2)]
    assert outcomes == ["event", "duplicate"]

    docs = await worker.mongo_db["events"].find().to_list()
    assert [d["_id"] for d in docs] == [f"t-acme:{event['msg_id']}"]
    assert docs[0]["type"] == "collision"
    assert docs[0]["expires_at"] == T0 + timedelta(days=365)


async def test_event_without_type_is_rejected(worker):
    assert await worker.handle(message("event", data={"severity": "low"})) == "rejected"

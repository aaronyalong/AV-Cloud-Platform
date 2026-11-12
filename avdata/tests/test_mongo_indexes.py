"""Mongo indexes: present with the right options, and safe to create twice."""

from datetime import UTC, datetime

import pytest
from pymongo import AsyncMongoClient
from pymongo.errors import DuplicateKeyError

from avdata.mongo import ALERTS, EVENTS, INDEXES, TELEMETRY, ensure_indexes

pytestmark = pytest.mark.anyio

TEST_DB = "av_test"


@pytest.fixture
async def db(mongo_url):
    """A fresh av_test database with indexes created."""
    client = AsyncMongoClient(mongo_url)
    await client.drop_database(TEST_DB)
    database = client[TEST_DB]
    await ensure_indexes(database)
    yield database
    await client.close()


async def index_specs(db, collection: str) -> dict[str, tuple]:
    """Index name -> (keys, unique, TTL seconds), excluding the built-in _id."""
    info = await db[collection].index_information()
    return {
        name: (spec["key"], spec.get("unique", False), spec.get("expireAfterSeconds"))
        for name, spec in info.items()
        if name != "_id_"
    }


async def test_telemetry_indexes(db):
    assert await index_specs(db, TELEMETRY) == {
        "bucket_key": (
            [("tenant_id", 1), ("run_id", 1), ("vehicle_id", 1), ("bucket_start", 1)],
            True,
            None,
        ),
        "tenant_vehicle_time": (
            [("tenant_id", 1), ("vehicle_id", 1), ("bucket_start", 1)],
            False,
            None,
        ),
        "ttl": ([("expires_at", 1)], False, 0),
    }


async def test_events_indexes(db):
    assert await index_specs(db, EVENTS) == {
        "tenant_vehicle_time": (
            [("tenant_id", 1), ("vehicle_id", 1), ("ts", 1)],
            False,
            None,
        ),
        "tenant_run_time": ([("tenant_id", 1), ("run_id", 1), ("ts", 1)], False, None),
        "ttl": ([("expires_at", 1)], False, 0),
    }


async def test_alerts_indexes(db):
    assert await index_specs(db, ALERTS) == {
        "tenant_status_vehicle": (
            [("tenant_id", 1), ("status", 1), ("vehicle_id", 1)],
            False,
            None,
        ),
        "tenant_vehicle_created": (
            [("tenant_id", 1), ("vehicle_id", 1), ("created_at", 1)],
            False,
            None,
        ),
        "tenant_created": (
            [("tenant_id", 1), ("created_at", 1)],
            False,
            None,
        ),
    }


async def test_every_query_index_starts_with_tenant_id(db):
    """Catches a future index that would let a query skip the tenant filter."""
    for collection in INDEXES:
        for name, (keys, _, ttl) in (await index_specs(db, collection)).items():
            if ttl is None:
                assert keys[0][0] == "tenant_id", f"{collection}.{name}"


async def test_ensure_indexes_is_idempotent(db):
    before = {c: await index_specs(db, c) for c in INDEXES}
    await ensure_indexes(db)
    assert {c: await index_specs(db, c) for c in INDEXES} == before


async def test_duplicate_telemetry_bucket_is_rejected(db):
    minute = datetime(2026, 11, 8, 19, 4, tzinfo=UTC)
    bucket = {
        "tenant_id": "tenant-a",
        "run_id": "r1",
        "vehicle_id": "v1",
        "bucket_start": minute,
    }
    await db[TELEMETRY].insert_one({**bucket, "samples": []})
    with pytest.raises(DuplicateKeyError):
        await db[TELEMETRY].insert_one({**bucket, "samples": []})

    # The same minute in another run, or for another tenant, is a new bucket.
    await db[TELEMETRY].insert_one({**bucket, "run_id": "r2", "samples": []})
    await db[TELEMETRY].insert_one({**bucket, "tenant_id": "tenant-b", "samples": []})

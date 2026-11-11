"""Shared worker test fixtures: the demo fleet in av_test, all devices reset."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from avdata import mongo as avmongo
from workers.ingest import IngestWorker
from workers.seed import seed_demo_fleet


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
        await conn.execute(
            text("DELETE FROM vehicle_command WHERE tenant_id IN ('t-acme', 't-beta')")
        )
    await superuser.dispose()

    engine = create_async_engine(pg_url("av_app"))
    client = avmongo.connect(mongo_url)
    await client.drop_database("av_test")
    db = client["av_test"]
    await avmongo.ensure_indexes(db)
    yield IngestWorker(engine, db)
    await engine.dispose()
    await client.close()

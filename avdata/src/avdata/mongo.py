"""MongoDB collections and indexes, limited to features DocumentDB supports.

Create the indexes once per environment (safe to repeat):
    uv run --env-file .env python -m avdata.mongo
"""

import asyncio
import os

from pymongo import ASCENDING, AsyncMongoClient, IndexModel
from pymongo.asynchronous.database import AsyncDatabase

TELEMETRY = "telemetry"
EVENTS = "events"

# Mongo has no row-level security, so every query index starts with tenant_id
# and every avdata query filters on it.
INDEXES: dict[str, list[IndexModel]] = {
    TELEMETRY: [
        # One bucket per tenant, run, vehicle, and minute. Also serves run queries.
        IndexModel(
            [
                ("tenant_id", ASCENDING),
                ("run_id", ASCENDING),
                ("vehicle_id", ASCENDING),
                ("bucket_start", ASCENDING),
            ],
            name="bucket_key",
            unique=True,
        ),
        IndexModel(
            [
                ("tenant_id", ASCENDING),
                ("vehicle_id", ASCENDING),
                ("bucket_start", ASCENDING),
            ],
            name="tenant_vehicle_time",
        ),
        # Each document expires at its own expires_at, set when it is written.
        IndexModel([("expires_at", ASCENDING)], name="ttl", expireAfterSeconds=0),
    ],
    EVENTS: [
        # _id is "tenant_id:msg_id", so a duplicate message fails on insert.
        IndexModel(
            [("tenant_id", ASCENDING), ("vehicle_id", ASCENDING), ("ts", ASCENDING)],
            name="tenant_vehicle_time",
        ),
        IndexModel(
            [("tenant_id", ASCENDING), ("run_id", ASCENDING), ("ts", ASCENDING)],
            name="tenant_run_time",
        ),
        IndexModel([("expires_at", ASCENDING)], name="ttl", expireAfterSeconds=0),
    ],
}


async def ensure_indexes(db: AsyncDatabase) -> None:
    """Create any missing indexes. Fails if an existing one has other options."""
    for collection, indexes in INDEXES.items():
        await db[collection].create_indexes(indexes)


async def main() -> None:
    url, name = os.environ.get("MONGO_URL"), os.environ.get("MONGO_DB")
    if not url or not name:
        raise SystemExit(
            "MONGO_URL and MONGO_DB must be set."
            " Run: uv run --env-file .env python -m avdata.mongo"
        )
    client = AsyncMongoClient(url)
    try:
        db = client[name]
        await ensure_indexes(db)
        for collection in INDEXES:
            indexes = await db[collection].index_information()
            print(f"{name}.{collection}: {', '.join(sorted(indexes))}")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())

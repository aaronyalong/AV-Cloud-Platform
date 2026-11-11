"""MongoDB collections and indexes, limited to features DocumentDB supports.

Create the indexes once per environment (safe to repeat):
    uv run --env-file .env python -m avdata.mongo
"""

import asyncio
import os
from datetime import datetime, timedelta

from pymongo import ASCENDING, AsyncMongoClient, IndexModel
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from avdata.messages import Envelope

TELEMETRY = "telemetry"
EVENTS = "events"

TELEMETRY_RETENTION = timedelta(days=90)
EVENT_RETENTION = timedelta(days=365)

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


def connect(url: str, **kwargs) -> AsyncMongoClient:
    """Client that returns timezone-aware datetimes, so they compare with ours."""
    return AsyncMongoClient(url, tz_aware=True, **kwargs)


async def ensure_indexes(db: AsyncDatabase) -> None:
    """Create any missing indexes. Fails if an existing one has other options."""
    for collection, indexes in INDEXES.items():
        await db[collection].create_indexes(indexes)


class TenantDocuments:
    """Mongo reads and writes for one tenant. Every query includes tenant_id.

    Mongo has no row-level security, so this class is the tenant barrier:
    code outside avdata should not query the collections directly.
    """

    def __init__(self, db: AsyncDatabase, tenant_id: str) -> None:
        if not tenant_id:
            raise ValueError("tenant_id is required")
        self._db = db
        self.tenant_id = tenant_id

    def _own(self, envelope: Envelope) -> None:
        if envelope.tenant_id != self.tenant_id:
            raise ValueError("message belongs to another tenant")

    async def add_telemetry_sample(self, envelope: Envelope) -> bool:
        """Store one sample in its minute bucket. False if msg_id was seen before."""
        self._own(envelope)
        bucket = {
            "tenant_id": self.tenant_id,
            "run_id": envelope.run_id,
            "vehicle_id": envelope.vehicle_id,
            "bucket_start": envelope.ts.replace(second=0, microsecond=0),
        }
        update = {
            "$push": {
                "samples": {"ts": envelope.ts, **envelope.data},
                "msg_ids": envelope.msg_id,
            },
            "$setOnInsert": {
                "expires_at": bucket["bucket_start"] + TELEMETRY_RETENTION
            },
        }
        # Skips buckets that already hold this msg_id. If that leaves no match,
        # the upsert tries to insert the bucket again and hits the unique index.
        for _ in range(2):
            try:
                await self._db[TELEMETRY].update_one(
                    {**bucket, "msg_ids": {"$ne": envelope.msg_id}}, update, upsert=True
                )
                return True
            except DuplicateKeyError:
                seen = await self._db[TELEMETRY].find_one(
                    {**bucket, "msg_ids": envelope.msg_id}, {"_id": 1}
                )
                if seen:
                    return False
                # Another writer created the bucket at the same moment; retry.
        raise RuntimeError(f"could not store telemetry {envelope.msg_id}")

    async def telemetry_samples(
        self,
        vehicle_id: str,
        start: datetime,
        end: datetime,
        run_id: str | None = None,
    ) -> list[dict]:
        """Samples with start <= ts <= end, oldest first."""
        query = {
            "tenant_id": self.tenant_id,
            "vehicle_id": vehicle_id,
            "bucket_start": {
                "$gte": start.replace(second=0, microsecond=0),
                "$lte": end,
            },
        }
        if run_id:
            query["run_id"] = run_id
        samples = []
        async for bucket in self._db[TELEMETRY].find(query, {"samples": 1}):
            samples.extend(s for s in bucket["samples"] if start <= s["ts"] <= end)
        samples.sort(key=lambda s: s["ts"])
        return samples

    async def add_event(self, envelope: Envelope) -> bool:
        """Store one event. False if msg_id was seen before (same _id)."""
        self._own(envelope)
        try:
            await self._db[EVENTS].insert_one(
                {
                    "_id": f"{self.tenant_id}:{envelope.msg_id}",
                    "tenant_id": self.tenant_id,
                    "vehicle_id": envelope.vehicle_id,
                    "run_id": envelope.run_id,
                    "ts": envelope.ts,
                    "type": envelope.data.get("type"),
                    "data": envelope.data,
                    "expires_at": envelope.ts + EVENT_RETENTION,
                }
            )
        except DuplicateKeyError:
            return False
        return True

    async def events(
        self,
        start: datetime,
        end: datetime,
        vehicle_id: str | None = None,
        run_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Events with start <= ts <= end, newest first."""
        query: dict = {"tenant_id": self.tenant_id, "ts": {"$gte": start, "$lte": end}}
        if vehicle_id:
            query["vehicle_id"] = vehicle_id
        if run_id:
            query["run_id"] = run_id
        cursor = self._db[EVENTS].find(query).sort("ts", -1).limit(limit)
        return [
            {
                "event_id": doc["_id"].split(":", 1)[1],
                "vehicle_id": doc["vehicle_id"],
                "run_id": doc["run_id"],
                "ts": doc["ts"],
                "type": doc["type"],
                "data": doc["data"],
            }
            async for doc in cursor
        ]


async def main() -> None:
    url, name = os.environ.get("MONGO_URL"), os.environ.get("MONGO_DB")
    if not url or not name:
        raise SystemExit(
            "MONGO_URL and MONGO_DB must be set."
            " Run: uv run --env-file .env python -m avdata.mongo"
        )
    client = connect(url)
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

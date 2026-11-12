"""MongoDB collections and indexes, limited to features DocumentDB supports.

Create the indexes once per environment (safe to repeat):
    uv run --env-file .env python -m avdata.mongo
"""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

from pymongo import ASCENDING, DESCENDING, AsyncMongoClient, IndexModel, ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from avdata.alerts import (
    ACTIVE_ALERT_STATUSES,
    ALERT_SEVERITIES,
    ALERT_STATUS_ACTIVE,
    ALERT_STATUSES,
    normalize_alert_status,
    resolve_alert_type,
)
from avdata.messages import Envelope

TELEMETRY = "telemetry"
EVENTS = "events"
ALERTS = "alerts"

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
    ALERTS: [
        IndexModel(
            [
                ("tenant_id", ASCENDING),
                ("status", ASCENDING),
                ("vehicle_id", ASCENDING),
            ],
            name="tenant_status_vehicle",
        ),
        IndexModel(
            [
                ("tenant_id", ASCENDING),
                ("vehicle_id", ASCENDING),
                ("created_at", ASCENDING),
            ],
            name="tenant_vehicle_created",
        ),
        IndexModel(
            [("tenant_id", ASCENDING), ("created_at", ASCENDING)],
            name="tenant_created",
        ),
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

    async def create_alert(
        self,
        *,
        vehicle_id: str,
        alert_type: str,
        severity: str,
        category: str | None = None,
        description: str | None = None,
        status: str = ALERT_STATUS_ACTIVE,
        source: str = "simulator",
        created_at: datetime | None = None,
        alert_id: str | None = None,
    ) -> dict:
        """Create and store a tenant-scoped alert."""
        if not vehicle_id:
            raise ValueError("vehicle_id is required")

        resolved_category, canonical_type = resolve_alert_type(alert_type, category)
        if severity not in ALERT_SEVERITIES:
            raise ValueError(f"unsupported severity: {severity!r}")
        if status not in ALERT_STATUSES:
            raise ValueError(f"unsupported alert status: {status!r}")

        cleaned_description = (
            description.strip() or None if description is not None else None
        )
        aid = alert_id or str(uuid.uuid4())
        ts = created_at or datetime.now(UTC)

        # Prefix _id with tenant_id so IDs stay isolated across tenants.
        doc = {
            "_id": f"{self.tenant_id}:{aid}",
            "alert_id": aid,
            "tenant_id": self.tenant_id,
            "vehicle_id": vehicle_id,
            "category": resolved_category,
            "alert_type": canonical_type,
            "severity": severity,
            "status": status,
            "description": cleaned_description,
            "source": source,
            "created_at": ts,
        }
        await self._db[ALERTS].insert_one(doc)
        return _format_alert(doc)

    async def alerts(
        self,
        *,
        vehicle_id: str | None = None,
        status: str | None = None,
        active_only: bool = False,
        limit: int = 100,
    ) -> list[dict]:
        """Alerts for this tenant, newest first."""
        query: dict = {"tenant_id": self.tenant_id}
        if vehicle_id:
            query["vehicle_id"] = vehicle_id

        # active_only restricts to unresolved statuses; RESOLVED and FALSE_ALARM are excluded.
        if status is not None and active_only:
            if status not in ACTIVE_ALERT_STATUSES:
                return []
            query["status"] = status
        elif status is not None:
            query["status"] = status
        elif active_only:
            query["status"] = {"$in": list(ACTIVE_ALERT_STATUSES)}

        cursor = (
            self._db[ALERTS].find(query).sort("created_at", DESCENDING).limit(limit)
        )
        return [_format_alert(doc) async for doc in cursor]

    async def alert(self, alert_id: str) -> dict | None:
        """Retrieve a single alert for this tenant, or None if not found."""
        if not alert_id:
            return None

        doc = await self._db[ALERTS].find_one(
            {"_id": f"{self.tenant_id}:{alert_id}", "tenant_id": self.tenant_id}
        )
        return _format_alert(doc) if doc else None

    async def update_alert_status(self, alert_id: str, status: str) -> dict | None:
        """Update an alert's status for this tenant, or None if not found."""
        if not alert_id:
            return None

        normalized = normalize_alert_status(status)
        doc = await self._db[ALERTS].find_one_and_update(
            {"_id": f"{self.tenant_id}:{alert_id}", "tenant_id": self.tenant_id},
            {"$set": {"status": normalized, "updated_at": datetime.now(UTC)}},
            return_document=ReturnDocument.AFTER,
        )
        return _format_alert(doc) if doc else None

    async def active_alert_counts(self) -> dict[str, int]:
        """Unresolved/active alert count per vehicle for this tenant."""
        pipeline = [
            {
                "$match": {
                    "tenant_id": self.tenant_id,
                    "status": {"$in": list(ACTIVE_ALERT_STATUSES)},
                }
            },
            {"$group": {"_id": "$vehicle_id", "count": {"$sum": 1}}},
        ]
        cursor = await self._db[ALERTS].aggregate(pipeline)
        return {doc["_id"]: int(doc["count"]) async for doc in cursor}


def _format_alert(doc: dict) -> dict:
    return {
        "alert_id": doc["alert_id"],
        "tenant_id": doc["tenant_id"],
        "vehicle_id": doc["vehicle_id"],
        "category": doc["category"],
        "alert_type": doc["alert_type"],
        "severity": doc["severity"],
        "status": doc["status"],
        "description": doc.get("description"),
        "source": doc.get("source"),
        "created_at": doc["created_at"],
        "updated_at": doc.get("updated_at"),
    }


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

"""Ingest worker: validates queued messages and stores each one exactly once.

All Postgres writes go through tenant_transaction and all Mongo writes
through TenantDocuments, so a message can only touch its own tenant's data.
"""

import logging
from collections import Counter

from pydantic import ValidationError
from pymongo.asynchronous.database import AsyncDatabase
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from avdata.messages import CommandAck, Envelope, EventData, TelemetryData
from avdata.mongo import TenantDocuments
from avdata.postgres import tenant_transaction
from workers.queue import MessageQueue

logger = logging.getLogger(__name__)


class IngestWorker:
    def __init__(self, engine: AsyncEngine, mongo_db: AsyncDatabase) -> None:
        self.engine = engine
        self.mongo_db = mongo_db
        self.stats: Counter[str] = Counter()
        self._known_vehicles: set[tuple[str, str]] = set()

    async def handle(self, message: dict) -> str:
        """Process one message and return what happened to it."""
        outcome = await self._handle(message)
        self.stats[outcome] += 1
        return outcome

    async def _handle(self, message: dict) -> str:
        try:
            envelope = Envelope.model_validate(message)
        except ValidationError as error:
            logger.warning("Rejected invalid message: %s", error.errors()[0]["msg"])
            return "rejected"
        if not await self._has_device(envelope):
            logger.warning(
                "Rejected message for unknown vehicle %s/%s",
                envelope.tenant_id,
                envelope.vehicle_id,
            )
            return "rejected"

        match envelope.channel:
            case "telemetry":
                try:
                    TelemetryData.model_validate(envelope.data)
                except ValidationError:
                    return "rejected"
                docs = TenantDocuments(self.mongo_db, envelope.tenant_id)
                stored = await docs.add_telemetry_sample(envelope)
                return "telemetry" if stored else "duplicate"
            case "heartbeat":
                await self._record_heartbeat(envelope)
                return "heartbeat"
            case "disconnect":
                await self._record_disconnect(envelope)
                return "disconnect"
            case "event":
                try:
                    EventData.model_validate(envelope.data)
                except ValidationError:
                    return "rejected"
                docs = TenantDocuments(self.mongo_db, envelope.tenant_id)
                stored = await docs.add_event(envelope)
                return "event" if stored else "duplicate"
            case "cmd-ack":
                try:
                    ack = CommandAck.model_validate(envelope.data)
                except ValidationError:
                    return "rejected"
                return await self._record_ack(envelope, ack)
            case _:
                return "ignored"

    async def _has_device(self, envelope: Envelope) -> bool:
        """Only registered vehicles with a device may send data. Cached once seen."""
        key = (envelope.tenant_id, envelope.vehicle_id)
        if key in self._known_vehicles:
            return True
        async with tenant_transaction(self.engine, envelope.tenant_id) as conn:
            found = await conn.scalar(
                text("SELECT 1 FROM iot_device WHERE vehicle_id = :v"),
                {"v": envelope.vehicle_id},
            )
        if found:
            self._known_vehicles.add(key)
        return bool(found)

    async def _record_heartbeat(self, envelope: Envelope) -> None:
        # GREATEST ignores NULLs and stops a replayed old heartbeat moving time back.
        params = {"v": envelope.vehicle_id, "ts": envelope.ts}
        async with tenant_transaction(self.engine, envelope.tenant_id) as conn:
            await conn.execute(
                text(
                    "UPDATE iot_device SET last_heartbeat_at = GREATEST(last_heartbeat_at, :ts)"
                    " WHERE vehicle_id = :v"
                ),
                params,
            )
            await conn.execute(
                text(
                    "UPDATE vehicle SET last_seen_at = GREATEST(last_seen_at, :ts)"
                    " WHERE vehicle_id = :v"
                ),
                params,
            )

    async def _record_disconnect(self, envelope: Envelope) -> None:
        async with tenant_transaction(self.engine, envelope.tenant_id) as conn:
            await conn.execute(
                text(
                    "UPDATE iot_device SET last_disconnect_at = GREATEST(last_disconnect_at, :ts)"
                    " WHERE vehicle_id = :v"
                ),
                {"v": envelope.vehicle_id, "ts": envelope.ts},
            )

    async def _record_ack(self, envelope: Envelope, ack: CommandAck) -> str:
        """SENT -> ACKED/REJECTED, once. A repeated ack changes nothing."""
        params = {
            "id": ack.command_id,
            "v": envelope.vehicle_id,
            "status": ack.status,
            "ts": envelope.ts,
        }
        async with tenant_transaction(self.engine, envelope.tenant_id) as conn:
            updated = await conn.execute(
                text(
                    "UPDATE vehicle_command SET status = :status, acked_at = :ts"
                    " WHERE command_id = :id AND vehicle_id = :v AND status = 'SENT'"
                ),
                params,
            )
            if updated.rowcount:
                return "cmd-ack"
            known = await conn.scalar(
                text(
                    "SELECT 1 FROM vehicle_command WHERE command_id = :id AND vehicle_id = :v"
                ),
                params,
            )
        return "duplicate" if known else "rejected"

    async def run(self, queue: MessageQueue) -> None:
        """Consume messages forever. A failed message is logged and dropped;
        with SQS it would be retried and then sent to the dead-letter queue."""
        while True:
            message = await queue.receive()
            try:
                await self.handle(message)
            except Exception:
                self.stats["failed"] += 1
                logger.exception("Failed to process message %s", message.get("msg_id"))
            finally:
                queue.ack(message)

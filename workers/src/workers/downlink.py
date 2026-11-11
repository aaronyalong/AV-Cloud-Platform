"""Local stand-in for IoT Core sending commands down to vehicles.

In AWS the API would publish each command to the vehicle's MQTT topic. Here a
poller finds new SENT commands and hands each one to the simulator once.
"""

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from avdata.postgres import tenant_transaction
from workers.simulator import Simulator

logger = logging.getLogger(__name__)

POLL_INTERVAL_S = 1.0


class CommandDownlink:
    def __init__(
        self, engine: AsyncEngine, simulator: Simulator, tenant_ids: list[str]
    ) -> None:
        self.engine = engine
        self.simulator = simulator
        self.tenant_ids = tenant_ids
        self.delivered: set[str] = set()
        self.pending: set[asyncio.Task] = set()

    async def poll_once(self) -> int:
        """Deliver commands sent in the last 2 minutes that haven't been delivered.
        Older SENT commands (from earlier runs) are left alone."""
        count = 0
        for tenant_id in self.tenant_ids:
            async with tenant_transaction(self.engine, tenant_id) as conn:
                rows = (
                    await conn.execute(
                        text(
                            "SELECT command_id, vehicle_id, command FROM vehicle_command"
                            " WHERE status = 'SENT' AND sent_at > now() - interval '2 minutes'"
                            " ORDER BY sent_at"
                        )
                    )
                ).all()
            for command_id, vehicle_id, command in rows:
                key = str(command_id)
                if key in self.delivered:
                    continue
                self.delivered.add(key)
                task = asyncio.create_task(
                    self.simulator.deliver_command(tenant_id, vehicle_id, key, command)
                )
                self.pending.add(task)
                task.add_done_callback(self.pending.discard)
                count += 1
        return count

    async def run(self) -> None:
        while True:
            try:
                await self.poll_once()
            except Exception:
                logger.exception("Command downlink poll failed")
            await asyncio.sleep(POLL_INTERVAL_S)

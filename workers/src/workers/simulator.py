"""Fake vehicles that publish envelope messages, standing in for the edge agent.

Each vehicle drives a circular track, sending telemetry at 2 Hz and a
heartbeat every 10 s. Run it through workers.pipeline, which also runs the
ingest worker on the same in-process queue.
"""

import asyncio
import logging
import math
import uuid
from collections import deque
from datetime import UTC, datetime

from avdata.messages import Envelope
from workers.queue import MessageQueue
from workers.seed import DEMO_FLEET

logger = logging.getLogger(__name__)

TELEMETRY_INTERVAL_S = 0.5
HEARTBEAT_INTERVAL_S = 10.0
# Each vehicle reports a scripted collision this often, staggered by vehicle.
EVENT_INTERVAL_S = 45.0
# How a vehicle obeys a command: hold this top speed for COMMAND_HOLD_S.
COMMAND_SPEED_LIMIT_KMH = {"PULL_OVER": 0.0, "SLOW_DOWN": 15.0}
COMMAND_HOLD_S = 25.0
COMMAND_ACK_DELAY_S = 1.0
MAX_DECEL_KMH_S = 12.0
MAX_ACCEL_KMH_S = 8.0


class SimVehicle:
    """A vehicle on a circular track whose speed rises and falls."""

    def __init__(self, tenant_id: str, vehicle_id: str, index: int) -> None:
        self.tenant_id = tenant_id
        self.vehicle_id = vehicle_id
        self.index = index
        self.radius_m = 40.0 + 15.0 * index
        self.base_speed = 28.0 + 6.0 * index
        self.cycle_s = 30.0 + 7.0 * index
        self.phase = index * 1.3
        self.angle = self.phase
        self.speed_kmh = self.base_speed
        self.elapsed = 0.0
        self.limit_kmh: float | None = None
        self.limit_until = 0.0

    def step(self, dt: float) -> dict:
        """Advance dt seconds and return the telemetry data."""
        self.elapsed += dt
        previous = self.speed_kmh
        wave = math.sin(2 * math.pi * self.elapsed / self.cycle_s + self.phase)
        target = max(0.0, self.base_speed + 18.0 * wave)
        if self.limit_kmh is not None and self.elapsed < self.limit_until:
            target = min(target, self.limit_kmh)
        # Change speed smoothly, so obeying a command shows as braking.
        self.speed_kmh = min(
            max(target, previous - MAX_DECEL_KMH_S * dt),
            previous + MAX_ACCEL_KMH_S * dt,
        )
        self.angle += (self.speed_kmh / 3.6) * dt / self.radius_m
        accel = (self.speed_kmh - previous) / dt
        return {
            "speed_kmh": round(self.speed_kmh, 2),
            "x": round(self.radius_m * math.cos(self.angle), 2),
            "y": round(self.radius_m * math.sin(self.angle), 2),
            "heading_deg": round((math.degrees(self.angle) + 90) % 360, 1),
            "throttle": round(min(1.0, max(0.0, 0.3 + accel / 10)), 2),
            "brake": round(min(1.0, max(0.0, -accel / 10)), 2),
            "steer": round(min(1.0, 2.5 / self.radius_m), 3),
        }

    def obey(self, command: str) -> None:
        self.limit_kmh = COMMAND_SPEED_LIMIT_KMH[command]
        self.limit_until = self.elapsed + COMMAND_HOLD_S


def collision_event(sample: dict) -> dict:
    """A minor scripted collision at the vehicle's current position."""
    return {
        "type": "collision",
        "severity": "high" if sample["speed_kmh"] > 40 else "low",
        "speed_kmh": sample["speed_kmh"],
        "x": sample["x"],
        "y": sample["y"],
    }


class Simulator:
    def __init__(
        self,
        queue: MessageQueue,
        run_id: str,
        stop: str | None = None,
        stop_after_s: float = 20.0,
        replay_duplicates: int = 0,
        replay_after_s: float = 15.0,
    ) -> None:
        self.queue = queue
        self.run_id = run_id
        self.stop = stop
        self.stop_after_s = stop_after_s
        self.replay_after_s = replay_after_s
        self.vehicles = [
            SimVehicle(tenant_id, vehicle_id, index)
            for index, (tenant_id, vehicle_id) in enumerate(
                (t, v) for t, (_, vehicles) in DEMO_FLEET.items() for v, _ in vehicles
            )
        ]
        self.recent: deque[dict] = deque(maxlen=replay_duplicates or None)
        self.replay_duplicates = replay_duplicates
        self.running: set[str] = set()

    async def send(self, vehicle: SimVehicle, channel: str, data: dict) -> None:
        envelope = Envelope(
            msg_id=uuid.uuid4().hex,
            tenant_id=vehicle.tenant_id,
            vehicle_id=vehicle.vehicle_id,
            run_id=self.run_id,
            ts=datetime.now(UTC),
            channel=channel,
            data=data,
        )
        message = envelope.to_message()
        await self.queue.publish(message)
        if self.replay_duplicates:
            self.recent.append(message)

    async def drive(self, vehicle: SimVehicle) -> None:
        loop = asyncio.get_running_loop()
        start = loop.time()
        next_heartbeat = start
        next_event = start + 8.0 + 9.0 * vehicle.index
        self.running.add(vehicle.vehicle_id)
        while True:
            now = loop.time()
            if vehicle.vehicle_id == self.stop and now - start >= self.stop_after_s:
                self.running.discard(vehicle.vehicle_id)
                logger.info(
                    "%s stopped sending (simulated lost connection)", vehicle.vehicle_id
                )
                return
            if now >= next_heartbeat:
                await self.send(vehicle, "heartbeat", {})
                next_heartbeat += HEARTBEAT_INTERVAL_S
            sample = vehicle.step(TELEMETRY_INTERVAL_S)
            await self.send(vehicle, "telemetry", sample)
            if now >= next_event:
                await self.send(vehicle, "event", collision_event(sample))
                next_event += EVENT_INTERVAL_S
            await asyncio.sleep(TELEMETRY_INTERVAL_S)

    async def replay(self) -> None:
        """Resend the last N messages once, as an edge outbox would after a reconnect."""
        await asyncio.sleep(self.replay_after_s)
        messages = list(self.recent)
        logger.info("Replaying %d already-sent messages as duplicates", len(messages))
        for message in messages:
            await self.queue.publish(message)

    async def deliver_command(
        self, tenant_id: str, vehicle_id: str, command_id: str, command: str
    ) -> bool:
        """A command reaches a vehicle, which obeys and acks it a moment later.
        A vehicle that isn't running never answers, so its command stays SENT."""
        vehicle = next(
            (
                v
                for v in self.vehicles
                if (v.tenant_id, v.vehicle_id) == (tenant_id, vehicle_id)
            ),
            None,
        )
        if vehicle is None or vehicle_id not in self.running:
            return False
        logger.info(
            "%s received %s; obeying for %.0f s", vehicle_id, command, COMMAND_HOLD_S
        )
        vehicle.obey(command)
        await asyncio.sleep(COMMAND_ACK_DELAY_S)
        await self.send(
            vehicle, "cmd-ack", {"command_id": command_id, "status": "ACKED"}
        )
        return True

    async def disconnect(self) -> None:
        """Send a disconnect for every vehicle still running (clean shutdown)."""
        for vehicle in self.vehicles:
            if vehicle.vehicle_id != self.stop:
                await self.send(vehicle, "disconnect", {})

    async def run(self) -> None:
        async with asyncio.TaskGroup() as tg:
            for vehicle in self.vehicles:
                tg.create_task(self.drive(vehicle))
            if self.replay_duplicates:
                tg.create_task(self.replay())

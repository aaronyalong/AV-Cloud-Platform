"""Commands reaching a vehicle: downlink delivery, the vehicle's ack, and the
worker marking the command ACKED exactly once."""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from avdata.messages import Envelope
from avdata.postgres import tenant_transaction
from workers import simulator as simulator_module
from workers.downlink import CommandDownlink
from workers.queue import InProcessQueue
from workers.simulator import COMMAND_HOLD_S, Simulator, SimVehicle

pytestmark = pytest.mark.anyio

T0 = datetime(2026, 11, 11, 20, 0, 0, tzinfo=UTC)


async def send_command(worker, tenant="t-acme", vehicle="veh-07") -> str:
    async with tenant_transaction(worker.engine, tenant) as conn:
        command_id = await conn.scalar(
            text(
                "INSERT INTO vehicle_command (tenant_id, vehicle_id, command)"
                " VALUES (:t, :v, 'PULL_OVER') RETURNING command_id"
            ),
            {"t": tenant, "v": vehicle},
        )
    return str(command_id)


async def command_state(worker, command_id, tenant="t-acme"):
    async with tenant_transaction(worker.engine, tenant) as conn:
        row = await conn.execute(
            text("SELECT status, acked_at FROM vehicle_command WHERE command_id = :id"),
            {"id": command_id},
        )
        return row.one()


def ack(command_id, tenant="t-acme", vehicle="veh-07", status="ACKED") -> dict:
    return Envelope(
        msg_id=uuid.uuid4().hex,
        tenant_id=tenant,
        vehicle_id=vehicle,
        run_id="r1",
        ts=T0,
        channel="cmd-ack",
        data={"command_id": command_id, "status": status},
    ).to_message()


async def test_ack_marks_command_acked_once(worker):
    command_id = await send_command(worker)
    assert await worker.handle(ack(command_id)) == "cmd-ack"
    assert await command_state(worker, command_id) == ("ACKED", T0)

    # A repeated ack (or a late REJECTED) can't change an answered command.
    assert await worker.handle(ack(command_id, status="REJECTED")) == "duplicate"
    assert await command_state(worker, command_id) == ("ACKED", T0)


async def test_ack_from_another_tenant_is_rejected(worker):
    command_id = await send_command(worker)
    assert (
        await worker.handle(ack(command_id, tenant="t-beta", vehicle="veh-01"))
        == "rejected"
    )
    assert await command_state(worker, command_id) == ("SENT", None)


async def test_ack_with_invalid_command_id_is_rejected(worker):
    assert await worker.handle(ack("not-a-uuid")) == "rejected"


async def test_downlink_delivers_once_and_vehicle_acks(worker, monkeypatch):
    monkeypatch.setattr(simulator_module, "COMMAND_ACK_DELAY_S", 0)
    queue = InProcessQueue()
    simulator = Simulator(queue, run_id="r1")
    simulator.running.add("veh-07")
    downlink = CommandDownlink(worker.engine, simulator, ["t-acme", "t-beta"])
    command_id = await send_command(worker)

    assert await downlink.poll_once() == 1
    await asyncio.gather(*downlink.pending)
    assert await downlink.poll_once() == 0  # never delivered twice

    vehicle = next(v for v in simulator.vehicles if v.vehicle_id == "veh-07")
    assert vehicle.limit_kmh == 0.0

    message = await queue.receive()
    assert (message["channel"], message["data"]["command_id"]) == (
        "cmd-ack",
        command_id,
    )
    assert await worker.handle(message) == "cmd-ack"
    assert (await command_state(worker, command_id))[0] == "ACKED"


async def test_stopped_vehicle_never_acks(worker):
    queue = InProcessQueue()
    simulator = Simulator(queue, run_id="r1")  # veh-07 not running
    command_id = await send_command(worker)
    assert not await simulator.deliver_command(
        "t-acme", "veh-07", command_id, "PULL_OVER"
    )
    assert queue._queue.empty()


def test_pull_over_stops_vehicle_then_it_resumes():
    vehicle = SimVehicle("t-acme", "veh-07", 0)
    for _ in range(10):
        vehicle.step(0.5)
    assert vehicle.speed_kmh > 0
    vehicle.obey("PULL_OVER")

    for _ in range(20):  # 10 s
        vehicle.step(0.5)
    assert vehicle.speed_kmh == 0

    for _ in range(int(COMMAND_HOLD_S * 2)):  # past the hold
        vehicle.step(0.5)
    assert vehicle.speed_kmh > 0

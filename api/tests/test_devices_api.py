import anyio
import psycopg
from sqlalchemy.ext.asyncio import create_async_engine

from workers.devices import SensorAgent
from workers.queue import InProcessQueue
from workers.simulator import Simulator

STAFF = {"X-Tenant-Id": "t-acme", "X-User-Role": "staff"}
OWNER = {**STAFF, "X-User-Role": "owner"}
BETA = {"X-Tenant-Id": "t-beta", "X-User-Role": "staff"}
BASE = "/api/v1/vehicles/veh-07"


def live(pg_conninfo):
    with psycopg.connect(pg_conninfo("postgres")) as conn:
        conn.execute(
            "UPDATE iot_device SET last_heartbeat_at=now(), last_disconnect_at=NULL WHERE vehicle_id='veh-07' AND tenant_id='t-acme'"
        )
        conn.execute(
            "UPDATE managed_sensor SET last_heartbeat_at=now() WHERE vehicle_id='veh-07' AND tenant_id='t-acme'"
        )


def test_inventory_roles_and_tenant_boundaries(client):
    response = client.get(BASE + "/devices", headers=OWNER)
    assert response.status_code == 200
    assert {d["device_type"] for d in response.json()["devices"]} == {
        "microphone",
        "camera",
        "lidar",
        "gnss",
    }
    assert client.get(BASE + "/devices", headers=BETA).status_code == 404
    assert client.get(BASE + "/devices", headers={}).status_code == 401
    assert (
        client.get(BASE + "/devices", headers={"X-Tenant-Id": "t-acme"}).status_code
        == 403
    )
    assert (
        client.post(
            BASE + "/devices",
            headers=OWNER,
            json={"device_id": "blocked", "device_type": "camera", "model": "Test"},
        ).status_code
        == 403
    )


def test_register_many_sensors_preserves_vehicle_gateway(client, pg_conninfo):
    body = {
        "device_id": "rear-mic",
        "device_type": "microphone",
        "model": "Rear microphone",
    }
    assert client.post(BASE + "/devices", headers=STAFF, json=body).status_code == 201
    assert client.post(BASE + "/devices", headers=STAFF, json=body).status_code == 409
    assert (
        client.post(
            BASE + "/devices", headers=BETA, json={**body, "device_id": "intruder"}
        ).status_code
        == 404
    )
    with psycopg.connect(pg_conninfo("postgres")) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM iot_device WHERE tenant_id='t-acme' AND vehicle_id='veh-07'"
            ).fetchone()[0]
            == 1
        )


def test_configuration_is_desired_until_agent_reports(client, pg_conninfo, pg_url):
    live(pg_conninfo)
    path = BASE + "/config?device_id=veh-07-microphone"
    response = client.put(
        path, headers=STAFF, json={"gain_db": 6.0, "sample_rate_hz": 44100}
    )
    assert response.status_code == 200
    assert response.json()["sync_status"] == "IN_PROGRESS"
    assert response.json()["reported_configuration"] != response.json()["configuration"]
    assert client.put(path, headers=OWNER, json={"gain_db": 8.0}).status_code == 403
    assert client.put(path, headers=STAFF, json={"gain_db": 100}).status_code == 422
    assert (
        client.put(
            BASE + "/config?device_id=veh-07-camera",
            headers=STAFF,
            json={"gain_db": 8.0},
        ).status_code
        == 422
    )
    assert client.put(path, headers=BETA, json={"gain_db": 8.0}).status_code == 404

    async def report():
        engine = create_async_engine(pg_url("av_app"))
        sim = Simulator(InProcessQueue(), run_id="sensor-test")
        sim.running.add("veh-07")
        try:
            await SensorAgent(engine, sim, ["t-acme"]).poll_once()
        finally:
            await engine.dispose()

    anyio.run(report)
    device = next(
        d
        for d in client.get(BASE + "/devices", headers=STAFF).json()["devices"]
        if d["device_id"] == "veh-07-microphone"
    )
    assert device["desired_config"] == device["reported_config"]
    assert device["sync_status"] == "SYNCHRONIZED"


def test_device_commands_lifecycle_and_expiry(client, pg_conninfo, pg_url):
    live(pg_conninfo)
    body = {
        "device_id": "veh-07-microphone",
        "command": "firmware_update",
        "firmware_version": "demo-2.0.0",
    }
    assert client.post(BASE + "/commands", headers=OWNER, json=body).status_code == 403
    assert client.post(BASE + "/commands", headers=BETA, json=body).status_code == 404
    assert (
        client.post(
            BASE + "/commands", headers=STAFF, json={**body, "firmware_version": None}
        ).status_code
        == 422
    )
    assert (
        client.post(
            BASE + "/commands",
            headers=STAFF,
            json={"device_id": "veh-07-camera", "command": "microphone_test"},
        ).status_code
        == 422
    )
    sent = client.post(BASE + "/commands", headers=STAFF, json=body)
    assert sent.status_code == 202
    assert client.post(BASE + "/commands", headers=STAFF, json=body).status_code == 409
    command_id = sent.json()["command_id"]

    async def tick(running=True):
        engine = create_async_engine(pg_url("av_app"))
        sim = Simulator(InProcessQueue(), run_id="sensor-test")
        if running:
            sim.running.add("veh-07")
        try:
            await SensorAgent(engine, sim, ["t-acme"]).poll_once()
        finally:
            await engine.dispose()

    anyio.run(tick)
    with psycopg.connect(pg_conninfo("postgres")) as conn:
        assert (
            conn.execute(
                "SELECT status FROM sensor_command WHERE command_id=%s", (command_id,)
            ).fetchone()[0]
            == "RUNNING"
        )
        conn.execute(
            "UPDATE sensor_command SET started_at=now()-interval '3 seconds' WHERE command_id=%s",
            (command_id,),
        )
    anyio.run(tick)
    commands = client.get(BASE + "/commands", headers=STAFF).json()["commands"]
    assert (
        next(c for c in commands if c["command_id"] == command_id)["status"]
        == "COMPLETED"
    )
    device = next(
        d
        for d in client.get(BASE + "/devices", headers=STAFF).json()["devices"]
        if d["device_id"] == body["device_id"]
    )
    assert device["firmware_version"] == "demo-2.0.0"
    sent = client.post(
        BASE + "/commands",
        headers=STAFF,
        json={"device_id": body["device_id"], "command": "reboot"},
    )
    assert sent.status_code == 202
    with psycopg.connect(pg_conninfo("postgres")) as conn:
        conn.execute(
            "UPDATE sensor_command SET expires_at=now()-interval '1 second' WHERE command_id=%s",
            (sent.json()["command_id"],),
        )
    anyio.run(tick, False)
    commands = client.get(BASE + "/commands", headers=STAFF).json()["commands"]
    assert (
        next(c for c in commands if c["command_id"] == sent.json()["command_id"])[
            "status"
        ]
        == "EXPIRED"
    )


def test_offline_command_rejected_and_configuration_queued(client):
    base = "/api/v1/vehicles/veh-12"
    assert (
        client.post(
            base + "/commands",
            headers=STAFF,
            json={"device_id": "veh-12-microphone", "command": "reboot"},
        ).status_code
        == 409
    )
    assert (
        client.put(
            base + "/config?device_id=veh-12-microphone",
            headers=STAFF,
            json={"gain_db": 8.0},
        ).status_code
        == 200
    )


def test_console_is_served(client):
    response = client.get("/devices")
    assert response.status_code == 200
    assert "IoT Device Manager" in response.text

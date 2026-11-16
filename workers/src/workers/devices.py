"""Simulated sensor agent for the local demo; no real MQTT/hardware operations.

Only vehicles actively running in Simulator may report health, apply config,
or complete maintenance. Desired versions are acknowledged in one transaction.
"""

import asyncio
import json
import logging

from sqlalchemy import text

from avdata.devices import audio_policy, default_configuration, network_policy
from avdata.postgres import tenant_transaction

logger = logging.getLogger(__name__)


async def seed_sensors(conn, tenant_id: str, vehicle_id: str):
    for kind in ("microphone", "camera", "lidar", "gnss"):
        config = json.dumps(default_configuration(kind))
        await conn.execute(
            text("""
            INSERT INTO managed_sensor (tenant_id, vehicle_id, device_id, device_type, model, desired_config)
            VALUES (:t, :v, :d, :kind, :model, CAST(:config AS jsonb)) ON CONFLICT DO NOTHING
        """),
            {
                "t": tenant_id,
                "v": vehicle_id,
                "d": f"{vehicle_id}-{kind}",
                "kind": kind,
                "model": f"Simulated {kind}",
                "config": config,
            },
        )


class SensorAgent:
    def __init__(self, engine, simulator, tenant_ids):
        self.engine = engine
        self.simulator = simulator
        self.tenant_ids = tenant_ids

    async def poll_once(self):
        for tenant_id in self.tenant_ids:
            async with tenant_transaction(self.engine, tenant_id) as conn:
                # Timeouts also progress when the edge is disconnected.
                await conn.execute(
                    text("""
                    UPDATE sensor_command SET status = 'EXPIRED', completed_at = now(),
                        result = '{"message":"No simulated acknowledgment before expiry"}'
                    WHERE status IN ('SENT', 'RUNNING') AND expires_at <= now()
                """)
                )
                active = await conn.scalar(text("SELECT status = 'ACTIVE' FROM tenant"))
                if not active:
                    continue
                for vehicle in self.simulator.vehicles:
                    if (
                        vehicle.tenant_id != tenant_id
                        or vehicle.vehicle_id not in self.simulator.running
                    ):
                        continue
                    await self.report_vehicle(conn, vehicle)

    async def report_vehicle(self, conn, vehicle):
        sensors = (
            (
                await conn.execute(
                    text("""
            SELECT * FROM managed_sensor WHERE vehicle_id = :v ORDER BY device_id FOR UPDATE
        """),
                    {"v": vehicle.vehicle_id},
                )
            )
            .mappings()
            .all()
        )
        for sensor in sensors:
            config = sensor["desired_config"]
            info = {
                "wifi_authenticated": True,
                "wifi_signal_dbm": -62,
                "cellular_signal_dbm": -76,
                "packet_loss_percent": 0.5,
                "upload_queue_items": 0,
                "storage_used_percent": 18,
                "uptime_seconds": int(vehicle.elapsed),
                "clipping_seconds": 0,
            }
            policy = network_policy(info, config.get("network_preference", "auto"))
            network = policy["recommended_network"]
            info.update(
                network=network,
                signal_strength_dbm=info["wifi_signal_dbm"]
                if network == "wifi"
                else info["cellular_signal_dbm"],
            )
            if sensor["device_type"] == "microphone":
                # The scripted collision is the same 8+9*i, then 45s cadence
                # used by Simulator. High quality stays active for 10 seconds.
                since_event = vehicle.elapsed - (8 + 9 * vehicle.index)
                emergency = since_event >= 0 and since_event % 45 < 10
                info["audio_mode"] = "emergency" if emergency else "normal"
                info["effective_audio"] = audio_policy(
                    config, emergency, info["clipping_seconds"]
                )
            await conn.execute(
                text("""
                UPDATE managed_sensor SET
                    last_heartbeat_at = CASE WHEN last_heartbeat_at IS NULL OR
                        last_heartbeat_at < now() - interval '10 seconds' THEN now() ELSE last_heartbeat_at END,
                    reported_config = desired_config, reported_version = config_version,
                    network_info = CAST(:info AS jsonb)
                WHERE device_id = :d
            """),
                {"d": sensor["device_id"], "info": json.dumps(info)},
            )
            commands = (
                (
                    await conn.execute(
                        text("""
                SELECT * FROM sensor_command WHERE device_id = :d
                    AND status IN ('SENT', 'RUNNING') AND expires_at > now()
                ORDER BY sent_at FOR UPDATE
            """),
                        {"d": sensor["device_id"]},
                    )
                )
                .mappings()
                .all()
            )
            for command in commands:
                params = {"id": command["command_id"]}
                if command["status"] == "SENT":
                    await conn.execute(
                        text(
                            "UPDATE sensor_command SET status = 'RUNNING', started_at = now() WHERE command_id = :id"
                        ),
                        params,
                    )
                    continue
                elapsed = await conn.scalar(
                    text("SELECT now() - :started >= interval '2 seconds'"),
                    {"started": command["started_at"]},
                )
                if not elapsed:
                    continue
                result = {
                    "simulated": True,
                    "message": f"Simulated {command['command']} completed",
                }
                if command["command"] == "firmware_update":
                    version = command["parameters"]["firmware_version"]
                    await conn.execute(
                        text(
                            "UPDATE managed_sensor SET firmware_version = :version WHERE device_id = :d"
                        ),
                        {"version": version, "d": sensor["device_id"]},
                    )
                    result["firmware_version"] = version
                elif command["command"] == "microphone_test":
                    result.update(test="PASS", clipping=False, signal_level_db=-18)
                elif command["command"] == "diagnostics":
                    result["network"] = info
                await conn.execute(
                    text("""
                    UPDATE sensor_command SET status = 'COMPLETED', completed_at = now(), result = CAST(:result AS jsonb)
                    WHERE command_id = :id AND status = 'RUNNING'
                """),
                    {**params, "result": json.dumps(result)},
                )

    async def run(self):
        while True:
            try:
                await self.poll_once()
            except Exception:
                logger.exception("Simulated sensor agent failed")
            await asyncio.sleep(1)

"""Tenant-scoped local IoT sensor management.

Headers are demo identity only, matching current_tenant. Production must
replace both tenant and role dependencies with verified identity claims.
"""

import json
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.auth import current_tenant
from app.routes.vehicles import vehicles_with_status
from avdata.devices import (
    DeviceType,
    SensorConfiguration,
    default_configuration,
    network_policy,
    sensor_status,
)
from avdata.postgres import tenant_transaction

router = APIRouter(prefix="/api/v1", tags=["IoT devices"])
Tenant = Annotated[str, Depends(current_tenant)]


async def demo_role(x_user_role: Annotated[str | None, Header()] = None) -> str:
    if x_user_role not in {"staff", "owner"}:
        raise HTTPException(403, "local demo requires X-User-Role: staff or owner")
    return x_user_role


async def staff(role: Annotated[str, Depends(demo_role)]) -> None:
    if role != "staff":
        raise HTTPException(403, "device changes require staff")


Viewer = Annotated[str, Depends(demo_role)]
Writer = Annotated[None, Depends(staff)]


class Registration(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    device_id: str = Field(
        min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$"
    )
    device_type: DeviceType
    model: str = Field(min_length=1, max_length=120)


class DeviceCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    device_id: str = Field(min_length=1, max_length=80)
    command: Literal["reboot", "microphone_test", "diagnostics", "firmware_update"]
    firmware_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$",
    )

    @model_validator(mode="after")
    def firmware_target(self):
        if self.command == "firmware_update" and not self.firmware_version:
            raise ValueError("firmware_version is required for firmware_update")
        if self.command != "firmware_update" and self.firmware_version is not None:
            raise ValueError("firmware_version applies only to firmware_update")
        return self


async def vehicle_in(conn, vehicle_id):
    now, vehicles = await vehicles_with_status(conn, vehicle_id)
    if not vehicles:
        raise HTTPException(404, "vehicle not found")
    return now, vehicles[0]


async def sensor_in(conn, vehicle_id, device_id, lock=False):
    row = (
        (
            await conn.execute(
                text(
                    "SELECT * FROM managed_sensor WHERE vehicle_id = :v AND device_id = :d"
                    + (" FOR UPDATE" if lock else "")
                ),
                {"v": vehicle_id, "d": device_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(404, "device not found")
    return dict(row)


async def inventory(conn, vehicle_id):
    now, vehicle = await vehicle_in(conn, vehicle_id)
    rows = (
        await conn.execute(
            text("""
        SELECT s.*, EXISTS (
            SELECT 1 FROM sensor_command c WHERE c.device_id = s.device_id
                AND c.tenant_id = s.tenant_id AND c.status = 'RUNNING'
                AND c.expires_at > now()
        ) AS maintenance
        FROM managed_sensor s WHERE s.vehicle_id = :v ORDER BY s.device_id
    """),
            {"v": vehicle_id},
        )
    ).mappings()
    devices = []
    for row in rows:
        device = dict(row)
        device["sync_status"] = (
            "SYNCHRONIZED"
            if row["config_version"] == row["reported_version"]
            else "IN_PROGRESS"
        )
        device["status"] = sensor_status(
            now,
            row["last_heartbeat_at"],
            vehicle["status"],
            row["health"],
            device["sync_status"] == "IN_PROGRESS",
            row["maintenance"],
            row["network_info"].get("signal_strength_dbm"),
        )
        device["network_policy"] = network_policy(
            row["network_info"], row["desired_config"].get("network_preference", "auto")
        )
        device["simulated"] = True
        devices.append(device)
    return {
        "vehicle_id": vehicle_id,
        "as_of": now,
        "gateway_status": vehicle["status"],
        "devices": devices,
    }


@router.get("/vehicles/{vehicle_id}/devices")
async def list_devices(
    request: Request, tenant_id: Tenant, role: Viewer, vehicle_id: str
):
    async with tenant_transaction(request.app.state.engine, tenant_id) as conn:
        return await inventory(conn, vehicle_id)


@router.post("/vehicles/{vehicle_id}/devices", status_code=201)
async def register_device(
    request: Request,
    tenant_id: Tenant,
    role: Writer,
    vehicle_id: str,
    body: Registration,
):
    try:
        async with tenant_transaction(request.app.state.engine, tenant_id) as conn:
            _, vehicle = await vehicle_in(conn, vehicle_id)
            if vehicle["status"] == "SUSPENDED":
                raise HTTPException(409, "tenant is suspended")
            await conn.execute(
                text("""
                INSERT INTO managed_sensor (tenant_id, vehicle_id, device_id, device_type, model, desired_config)
                VALUES (:t, :v, :d, :kind, :model, CAST(:config AS jsonb))
            """),
                {
                    "t": tenant_id,
                    "v": vehicle_id,
                    "d": body.device_id,
                    "kind": body.device_type,
                    "model": body.model,
                    "config": json.dumps(default_configuration(body.device_type)),
                },
            )
            return {
                **await sensor_in(conn, vehicle_id, body.device_id),
                "simulated": True,
            }
    except IntegrityError as exc:
        raise HTTPException(
            409, "device could not be registered; check for a duplicate device ID"
        ) from exc


@router.put("/vehicles/{vehicle_id}/config")
async def update_configuration(
    request: Request,
    tenant_id: Tenant,
    role: Writer,
    vehicle_id: str,
    device_id: str,
    body: SensorConfiguration,
):
    async with tenant_transaction(request.app.state.engine, tenant_id) as conn:
        _, vehicle = await vehicle_in(conn, vehicle_id)
        if vehicle["status"] == "SUSPENDED":
            raise HTTPException(409, "tenant is suspended")
        sensor = await sensor_in(conn, vehicle_id, device_id, lock=True)
        try:
            changes = body.for_device(sensor["device_type"])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        row = (
            (
                await conn.execute(
                    text("""
            UPDATE managed_sensor SET desired_config = desired_config || CAST(:changes AS jsonb),
                config_version = config_version + 1
            WHERE vehicle_id = :v AND device_id = :d
            RETURNING desired_config, reported_config, config_version
        """),
                    {"v": vehicle_id, "d": device_id, "changes": json.dumps(changes)},
                )
            )
            .mappings()
            .one()
        )
        return {
            "device_id": device_id,
            "status": "pending",
            "sync_status": "IN_PROGRESS",
            "configuration": row["desired_config"],
            "reported_configuration": row["reported_config"],
            "version": row["config_version"],
        }


@router.get("/vehicles/{vehicle_id}/connectivity")
async def get_connectivity(
    request: Request, tenant_id: Tenant, role: Viewer, vehicle_id: str
):
    async with tenant_transaction(request.app.state.engine, tenant_id) as conn:
        result = await inventory(conn, vehicle_id)
        return {
            **result,
            "heartbeat_interval_seconds": 10,
            "degraded_after_seconds": 30,
            "offline_after_seconds": 90,
        }


@router.post("/vehicles/{vehicle_id}/commands", status_code=202)
async def send_device_command(
    request: Request,
    tenant_id: Tenant,
    role: Writer,
    vehicle_id: str,
    body: DeviceCommand,
):
    async with tenant_transaction(request.app.state.engine, tenant_id) as conn:
        now, vehicle = await vehicle_in(conn, vehicle_id)
        sensor = await sensor_in(conn, vehicle_id, body.device_id, lock=True)
        last_seen = sensor["last_heartbeat_at"]
        if (
            vehicle["status"] != "ONLINE"
            or last_seen is None
            or (now - last_seen).total_seconds() > 30
        ):
            raise HTTPException(409, "device is not online; command not sent")
        if body.command == "microphone_test" and sensor["device_type"] != "microphone":
            raise HTTPException(422, "microphone_test requires a microphone")
        busy = await conn.scalar(
            text("""
            SELECT 1 FROM sensor_command WHERE device_id = :d
            AND status IN ('SENT', 'RUNNING') AND expires_at > now() LIMIT 1
        """),
            {"d": body.device_id},
        )
        if busy:
            raise HTTPException(409, "device already has a pending command")
        parameters = (
            {"firmware_version": body.firmware_version} if body.firmware_version else {}
        )
        row = (
            (
                await conn.execute(
                    text("""
            INSERT INTO sensor_command (tenant_id, vehicle_id, device_id, command, parameters)
            VALUES (:t, :v, :d, :command, CAST(:params AS jsonb)) RETURNING *
        """),
                    {
                        "t": tenant_id,
                        "v": vehicle_id,
                        "d": body.device_id,
                        "command": body.command,
                        "params": json.dumps(parameters),
                    },
                )
            )
            .mappings()
            .one()
        )
        return dict(row)


@router.get("/vehicles/{vehicle_id}/commands")
async def command_history(
    request: Request,
    tenant_id: Tenant,
    role: Viewer,
    vehicle_id: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    async with tenant_transaction(request.app.state.engine, tenant_id) as conn:
        await vehicle_in(conn, vehicle_id)
        rows = (
            await conn.execute(
                text("""
            SELECT * FROM sensor_command WHERE vehicle_id = :v
            ORDER BY sent_at DESC LIMIT :limit
        """),
                {"v": vehicle_id, "limit": limit},
            )
        ).mappings()
        return {"vehicle_id": vehicle_id, "commands": [dict(row) for row in rows]}

"""Vehicle list with connectivity, plus telemetry and events."""

import math
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.auth import current_tenant
from avdata.connectivity import connectivity_status
from avdata.messages import TELEMETRY_FIELDS
from avdata.mongo import TenantDocuments
from avdata.postgres import tenant_transaction

router = APIRouter(prefix="/api/v1", tags=["vehicles"])

Tenant = Annotated[str, Depends(current_tenant)]
DEFAULT_WINDOW = timedelta(minutes=5)
MAX_WINDOW = timedelta(hours=24)


@router.get("/vehicles")
async def list_vehicles(request: Request, tenant_id: Tenant) -> dict:
    async with tenant_transaction(request.app.state.engine, tenant_id) as conn:
        as_of, vehicles = await vehicles_with_status(conn)
    counts = await TenantDocuments(
        request.app.state.mongo_db, tenant_id
    ).active_alert_counts()
    for vehicle in vehicles:
        vehicle["alert_count"] = counts.get(vehicle["vehicle_id"], 0)
    return {"tenant_id": tenant_id, "as_of": as_of, "vehicles": vehicles}


async def vehicles_with_status(
    conn: AsyncConnection, vehicle_id: str | None = None
) -> tuple[datetime, list[dict]]:
    """The tenant's vehicles (or one of them) with connectivity status.

    Statuses use the database clock, and the page shows "ago" relative to
    as_of, so a skewed browser clock can't disagree with the status.
    """
    as_of = await conn.scalar(text("SELECT now()"))
    rows = (
        await conn.execute(
            text("""
                SELECT v.vehicle_id, v.name, t.status AS tenant_status,
                       d.device_id, d.last_heartbeat_at, d.last_disconnect_at
                FROM vehicle v
                JOIN tenant t USING (tenant_id)
                LEFT JOIN iot_device d USING (tenant_id, vehicle_id)
                WHERE CAST(:vehicle_id AS text) IS NULL OR v.vehicle_id = :vehicle_id
                ORDER BY v.vehicle_id
            """),
            {"vehicle_id": vehicle_id},
        )
    ).mappings()
    vehicles = [
        {
            "vehicle_id": row["vehicle_id"],
            "name": row["name"],
            "device_id": row["device_id"],
            "status": connectivity_status(
                as_of,
                row["last_heartbeat_at"],
                row["last_disconnect_at"],
                row["tenant_status"],
            ),
            "last_heartbeat_at": row["last_heartbeat_at"],
            "last_disconnect_at": row["last_disconnect_at"],
        }
        for row in rows
    ]
    return as_of, vehicles


@router.get("/telemetry")
async def get_telemetry(
    request: Request,
    tenant_id: Tenant,
    vehicle_id: str,
    run_id: str | None = None,
    start: Annotated[datetime | None, Query(alias="from")] = None,
    end: Annotated[datetime | None, Query(alias="to")] = None,
    fields: Annotated[
        str | None, Query(description="Comma-separated, e.g. speed_kmh,x,y")
    ] = None,
    max_points: Annotated[int, Query(ge=1, le=5000)] = 500,
) -> dict:
    selected = _parse_fields(fields)
    start, end = _window(start, end, DEFAULT_WINDOW)
    await require_vehicle(request, tenant_id, vehicle_id)

    docs = TenantDocuments(request.app.state.mongo_db, tenant_id)
    samples = await docs.telemetry_samples(vehicle_id, start, end, run_id)
    stride = max(1, math.ceil(len(samples) / max_points))
    points = [
        {"ts": s["ts"], **{f: s.get(f) for f in selected}} for s in samples[::stride]
    ]
    return {
        "vehicle_id": vehicle_id,
        "run_id": run_id,
        "from": start,
        "to": end,
        "fields": selected,
        "total_samples": len(samples),
        "points": points,
    }


@router.get("/events")
async def get_events(
    request: Request,
    tenant_id: Tenant,
    vehicle_id: str | None = None,
    run_id: str | None = None,
    start: Annotated[datetime | None, Query(alias="from")] = None,
    end: Annotated[datetime | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> dict:
    """Events such as collisions, newest first. Defaults to the last 24 hours."""
    start, end = _window(start, end, MAX_WINDOW)
    if vehicle_id:
        await require_vehicle(request, tenant_id, vehicle_id)
    docs = TenantDocuments(request.app.state.mongo_db, tenant_id)
    events = await docs.events(start, end, vehicle_id, run_id, limit)
    return {"from": start, "to": end, "events": events}


def _window(
    start: datetime | None, end: datetime | None, default: timedelta
) -> tuple[datetime, datetime]:
    end = _aware(end) if end else datetime.now(UTC)
    start = _aware(start) if start else end - default
    if start > end or end - start > MAX_WINDOW:
        raise HTTPException(422, "'from' must be before 'to', at most 24 hours apart")
    return start, end


async def require_vehicle(request: Request, tenant_id: str, vehicle_id: str) -> None:
    """404 unless the vehicle exists for this tenant. Postgres RLS decides, so
    another tenant's vehicle looks exactly like one that doesn't exist."""
    async with tenant_transaction(request.app.state.engine, tenant_id) as conn:
        exists = await conn.scalar(
            text("SELECT 1 FROM vehicle WHERE vehicle_id = :v"), {"v": vehicle_id}
        )
    if not exists:
        raise HTTPException(404, "vehicle not found")


def _parse_fields(fields: str | None) -> list[str]:
    if not fields:
        return list(TELEMETRY_FIELDS)
    selected = [f.strip() for f in fields.split(",") if f.strip()]
    unknown = sorted(set(selected) - set(TELEMETRY_FIELDS))
    if unknown:
        raise HTTPException(422, f"unknown fields: {', '.join(unknown)}")
    return selected


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)

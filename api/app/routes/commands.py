"""Vehicle commands, for the alert component (internal, not for the dashboard's users).

Locally the dashboard's "Send pull-over" button calls this directly, standing
in for the alert component.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import text

from app.auth import current_tenant
from app.routes.vehicles import vehicles_with_status
from avdata.connectivity import NEVER_CONNECTED, OFFLINE, SUSPENDED
from avdata.postgres import tenant_transaction

router = APIRouter(prefix="/internal/v1", tags=["commands"])

Tenant = Annotated[str, Depends(current_tenant)]
# A command can only reach a vehicle that has been heard from recently.
UNREACHABLE = {OFFLINE, NEVER_CONNECTED, SUSPENDED}
COMMAND_COLUMNS = "command_id, vehicle_id, alert_id, command, status, sent_at, acked_at"


class CommandRequest(BaseModel):
    command: Literal["SLOW_DOWN", "PULL_OVER"]
    alert_id: str | None = None


@router.post("/vehicles/{vehicle_id}/commands", status_code=202)
async def send_command(
    request: Request, tenant_id: Tenant, vehicle_id: str, body: CommandRequest
) -> dict:
    """Save the command as SENT. 409 if the vehicle can't receive it now.

    The status check and the insert share one transaction. Delivery to the
    vehicle happens separately (IoT Core in AWS; a local downlink here).
    """
    async with tenant_transaction(request.app.state.engine, tenant_id) as conn:
        _, found = await vehicles_with_status(conn, vehicle_id)
        if not found:
            raise HTTPException(404, "vehicle not found")
        status = found[0]["status"]
        if status in UNREACHABLE:
            raise HTTPException(409, f"vehicle is {status}; command not sent")
        row = (
            (
                await conn.execute(
                    text(
                        "INSERT INTO vehicle_command (tenant_id, vehicle_id, alert_id, command)"
                        " VALUES (:t, :v, :alert_id, :command)"
                        f" RETURNING {COMMAND_COLUMNS}"
                    ),
                    {
                        "t": tenant_id,
                        "v": vehicle_id,
                        "alert_id": body.alert_id,
                        "command": body.command,
                    },
                )
            )
            .mappings()
            .one()
        )
    return dict(row)


@router.get("/vehicles/{vehicle_id}/commands")
async def list_commands(
    request: Request,
    tenant_id: Tenant,
    vehicle_id: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 5,
) -> dict:
    """The vehicle's most recent commands, newest first."""
    async with tenant_transaction(request.app.state.engine, tenant_id) as conn:
        _, found = await vehicles_with_status(conn, vehicle_id)
        if not found:
            raise HTTPException(404, "vehicle not found")
        rows = (
            await conn.execute(
                text(
                    f"SELECT {COMMAND_COLUMNS} FROM vehicle_command"
                    " WHERE vehicle_id = :v ORDER BY sent_at DESC LIMIT :limit"
                ),
                {"v": vehicle_id, "limit": limit},
            )
        ).mappings()
        return {"vehicle_id": vehicle_id, "commands": [dict(r) for r in rows]}

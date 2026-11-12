"""Alert tracking and simulation endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator, model_validator

from app.auth import current_tenant
from app.routes.vehicles import require_vehicle
from avdata.alerts import (
    ALERT_STATUSES,
    AlertSeverity,
    alert_catalog,
    normalize_alert_status,
    resolve_alert_type,
)
from avdata.mongo import TenantDocuments

router = APIRouter(prefix="/api/v1", tags=["alerts"])

Tenant = Annotated[str, Depends(current_tenant)]


class CreateAlertRequest(BaseModel):
    vehicle_id: str = Field(min_length=1)
    alert_type: str = Field(min_length=1)
    category: str | None = None
    severity: AlertSeverity
    description: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_alert_type(self) -> "CreateAlertRequest":
        category, canonical_type = resolve_alert_type(self.alert_type, self.category)
        self.category = category
        self.alert_type = canonical_type
        return self


class UpdateAlertStatusRequest(BaseModel):
    status: str = Field(min_length=1)

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        return normalize_alert_status(value)


@router.get("/alerts/types")
async def get_alert_types(_tenant_id: Tenant) -> dict:
    """Supported alert categories, types, severities, and active statuses."""
    return alert_catalog()


@router.post("/alerts", status_code=201)
async def create_alert(
    request: Request, tenant_id: Tenant, body: CreateAlertRequest
) -> dict:
    """Create an alert for an existing vehicle in the caller's tenant."""
    await require_vehicle(request, tenant_id, body.vehicle_id)
    docs = TenantDocuments(request.app.state.mongo_db, tenant_id)
    return await docs.create_alert(
        vehicle_id=body.vehicle_id,
        alert_type=body.alert_type,
        category=body.category,
        severity=body.severity,
        description=body.description,
    )


@router.get("/alerts")
async def list_alerts(
    request: Request,
    tenant_id: Tenant,
    vehicle_id: str | None = None,
    status: str | None = None,
    active_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict:
    """Alerts for the caller's tenant, newest first."""
    if status is not None and status not in ALERT_STATUSES:
        raise HTTPException(422, f"unknown alert status: {status}")
    if vehicle_id:
        await require_vehicle(request, tenant_id, vehicle_id)
    docs = TenantDocuments(request.app.state.mongo_db, tenant_id)
    alerts = await docs.alerts(
        vehicle_id=vehicle_id,
        status=status,
        active_only=active_only,
        limit=limit,
    )
    return {"alerts": alerts}


@router.get("/alerts/{alert_id}")
async def get_alert(request: Request, tenant_id: Tenant, alert_id: str) -> dict:
    """Retrieve one alert belonging to the caller's tenant."""
    docs = TenantDocuments(request.app.state.mongo_db, tenant_id)
    alert = await docs.alert(alert_id)
    if alert is None:
        raise HTTPException(404, "alert not found")
    return alert


@router.patch("/alerts/{alert_id}")
async def update_alert_status(
    request: Request,
    tenant_id: Tenant,
    alert_id: str,
    body: UpdateAlertStatusRequest,
) -> dict:
    """Update an alert's status (e.g. ACKNOWLEDGED, RESOLVED, FALSE_ALARM)."""
    docs = TenantDocuments(request.app.state.mongo_db, tenant_id)
    updated = await docs.update_alert_status(alert_id, body.status)
    if updated is None:
        raise HTTPException(404, "alert not found")
    return updated

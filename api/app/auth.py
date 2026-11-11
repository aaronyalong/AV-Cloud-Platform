"""Local auth stub: the tenant comes from the X-Tenant-Id header.

Planned: Cognito sign-in, with the tenant and role taken from the verified
token instead of a header the caller controls.
"""

from typing import Annotated

from fastapi import Header, HTTPException


async def current_tenant(
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> str:
    if not x_tenant_id:
        raise HTTPException(status_code=401, detail="X-Tenant-Id header is required")
    return x_tenant_id

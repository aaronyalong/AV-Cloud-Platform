"""Short-lived download links for media files (camera frames, audio clips).

The API authorizes the key before issuing a link: its tenant must be the
caller's, and its vehicle must be visible to the caller through RLS.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from app.auth import current_tenant
from app.routes.vehicles import require_vehicle
from avdata.media import MediaKey, MediaKeyError

router = APIRouter(tags=["media"])

Tenant = Annotated[str, Depends(current_tenant)]
URL_LIFETIME_S = 300


@router.get("/api/v1/media/download-url")
async def download_url(request: Request, tenant_id: Tenant, key: str) -> dict:
    try:
        media_key = MediaKey.parse(key)
    except MediaKeyError as error:
        raise HTTPException(422, str(error)) from None
    # Another tenant's key looks exactly like a missing file.
    if media_key.tenant_id != tenant_id:
        raise HTTPException(404, "media not found")
    await require_vehicle(request, tenant_id, media_key.vehicle_id)
    url = request.app.state.media_store.download_url(tenant_id, key, URL_LIFETIME_S)
    return {"key": key, "url": url, "expires_in_s": URL_LIFETIME_S}


@router.get("/local-media/{key:path}", include_in_schema=False)
async def local_media(
    request: Request, key: str, expires: int, signature: str
) -> FileResponse:
    """Serves a signed link, standing in for S3. No tenant header: the
    signature is the permission, as with a presigned URL."""
    path = request.app.state.media_store.verified_path(key, expires, signature)
    if path is None:
        raise HTTPException(403, "link is invalid or has expired")
    if not path.is_file():
        raise HTTPException(404, "media not found")
    return FileResponse(path)

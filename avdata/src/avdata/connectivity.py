"""Vehicle connectivity, derived from heartbeats only."""

from datetime import datetime, timedelta

ONLINE_WITHIN = timedelta(seconds=30)
STALE_WITHIN = timedelta(seconds=90)

ONLINE = "ONLINE"
STALE = "STALE"
OFFLINE = "OFFLINE"
NEVER_CONNECTED = "NEVER_CONNECTED"
SUSPENDED = "SUSPENDED"


def connectivity_status(
    now: datetime,
    last_heartbeat_at: datetime | None,
    last_disconnect_at: datetime | None = None,
    tenant_status: str = "ACTIVE",
) -> str:
    """ONLINE <=30 s since the last heartbeat, STALE 30-90 s, OFFLINE >90 s or
    after a disconnect, NEVER_CONNECTED with no heartbeat, SUSPENDED if the
    tenant is suspended."""
    if tenant_status == "SUSPENDED":
        return SUSPENDED
    if last_heartbeat_at is None:
        return NEVER_CONNECTED
    if last_disconnect_at is not None and last_disconnect_at >= last_heartbeat_at:
        return OFFLINE
    age = now - last_heartbeat_at
    if age <= ONLINE_WITHIN:
        return ONLINE
    if age <= STALE_WITHIN:
        return STALE
    return OFFLINE

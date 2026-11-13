"""Business rules and decision tables from Section IV of the component design."""
from datetime import datetime, timezone

ROLE_ORDER = ["platform_admin", "tenant_admin", "fleet_operator", "support_staff", "ai_engineer", "vehicle_owner"]

# DT-1: page / function access by role
PERMISSIONS = {
    "platform_admin": {"dashboard:read", "alerts:read", "stats:read", "stats:export", "health:read",
                       "models:read", "tenants:switch"},
    "tenant_admin":   {"dashboard:read", "alerts:read", "alerts:act", "stats:read", "stats:export", "health:read"},
    "fleet_operator": {"dashboard:read", "alerts:read", "alerts:act", "stats:read", "stats:export"},
    "support_staff":  {"dashboard:read", "alerts:read", "alerts:act", "stats:read"},
    "ai_engineer":    {"dashboard:read", "alerts:read", "stats:read", "stats:export", "models:read", "models:write"},
    "vehicle_owner":  {"dashboard:read", "alerts:read"},
}
PAGES = {  # page -> permission needed to see it in the navigation
    "dashboard": "dashboard:read", "alerts": "alerts:read", "statistics": "stats:read",
    "health": "health:read", "models": "models:read",
}

# DT-2: alert action decision table
DT2 = {
    "acknowledge": {"from": {"open"}, "to": "acknowledged", "requires": []},
    "assign":      {"from": {"acknowledged"}, "to": "assigned", "requires": ["assignee"]},
    "dispatch":    {"from": {"acknowledged", "assigned"}, "to": "dispatched", "requires": ["note"]},
    "resolve":     {"from": {"acknowledged", "assigned", "dispatched"}, "to": "resolved", "requires": ["note"]},
    "false_alarm": {"from": {"open", "acknowledged", "assigned"}, "to": "false_alarm", "requires": ["note"]},
}
OPEN_STATUSES = ("open", "acknowledged", "assigned", "dispatched")


def dt2_check(status: str, action: str, note: str | None, assignee: str | None):
    """Returns (allowed, reason)."""
    rule = DT2.get(action)
    if not rule:
        return False, "unknown_action"
    if status not in rule["from"]:
        return False, f"invalid_transition: {action} not allowed from {status}"
    if "note" in rule["requires"] and (not note or len(note.strip()) < 5):
        return False, "note_required (min 5 characters)"
    if "assignee" in rule["requires"] and not assignee:
        return False, "assignee_required"
    return True, rule["to"]


# DT-3: vehicle connectivity from heartbeat age
def connectivity(last_heartbeat: datetime | None, now: datetime | None = None) -> str:
    if last_heartbeat is None:
        return "offline"
    age = ((now or datetime.now(timezone.utc)) - last_heartbeat).total_seconds()
    if age <= 30:
        return "online"
    if age <= 120:
        return "stale"
    return "offline"


# DT-4: KPI tile status thresholds -> good | warning | critical
def tile_status(key: str, value, extra: dict | None = None) -> str:
    extra = extra or {}
    if value is None:
        return "neutral"
    if key == "vehicles_online":
        total = extra.get("total") or 0
        ratio = (value / total) if total else 1
        return "good" if ratio >= 0.95 else "warning" if ratio >= 0.80 else "critical"
    if key == "critical_unacked":
        oldest = extra.get("oldest_s") or 0
        return "good" if value == 0 else "warning" if oldest <= 60 else "critical"
    if key == "mtta_s":
        return "good" if value <= 120 else "warning" if value <= 300 else "critical"
    if key == "latency_p95_s":
        return "good" if value <= 3.0 else "warning" if value <= 4.5 else "critical"
    if key == "false_alarm_rate":
        return "good" if value <= 0.10 else "warning" if value <= 0.20 else "critical"
    return "neutral"


# DT-7: statistics bucket selection (bucket=auto keeps series <= ~720 points)
def choose_bucket(span_seconds: float, requested: str = "auto") -> str:
    if requested in ("minute", "hour", "day"):
        return requested
    if span_seconds <= 12 * 3600:
        return "minute"
    if span_seconds <= 30 * 86400:
        return "hour"
    return "day"


# Alert types produced by the detectors (and the simulator)
ALERT_TYPES = {
    "collision":       {"severity": "critical", "model": "vision-hazard"},
    "pedestrian":      {"severity": "high",     "model": "vision-hazard"},
    "emergency_siren": {"severity": "high",     "model": "audio-hazard"},
    "stopped_vehicle": {"severity": "medium",   "model": "vision-hazard"},
    "red_light":       {"severity": "medium",   "model": "vision-hazard"},
    "horn":            {"severity": "low",      "model": "audio-hazard"},
}

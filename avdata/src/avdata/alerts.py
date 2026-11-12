"""Shared alert categories, types, severities, and status conventions."""

from typing import Literal

AlertSeverity = Literal["low", "medium", "high"]
ALERT_SEVERITIES: tuple[AlertSeverity, ...] = ("low", "medium", "high")

ALERT_STATUS_ACTIVE = "ACTIVE"
ALERT_STATUS_ACKNOWLEDGED = "ACKNOWLEDGED"
ALERT_STATUS_RESOLVED = "RESOLVED"
ALERT_STATUS_FALSE_ALARM = "FALSE_ALARM"

# Active alerts are still unresolved and count toward a vehicle's active alert badge.
ACTIVE_ALERT_STATUSES: tuple[str, ...] = (
    ALERT_STATUS_ACTIVE,
    "OCCURRED",
    "REPORTED",
    "OPEN",
    ALERT_STATUS_ACKNOWLEDGED,
    "DISPATCHED",
)

# Terminal alert states are excluded from active counts and active alert lists.
RESOLVED_ALERT_STATUSES: tuple[str, ...] = (
    ALERT_STATUS_RESOLVED,
    ALERT_STATUS_FALSE_ALARM,
)
ALERT_STATUSES: tuple[str, ...] = (*ACTIVE_ALERT_STATUSES, *RESOLVED_ALERT_STATUSES)

_STATUS_ALIASES: dict[str, str] = {
    **{s: s for s in ALERT_STATUSES},
    "FALSE ALARM": ALERT_STATUS_FALSE_ALARM,
}


def normalize_alert_status(status: str) -> str:
    """Validate and normalize an alert status string."""
    key = status.strip().upper().replace("-", "_")
    resolved = _STATUS_ALIASES.get(key)
    if resolved is None:
        raise ValueError(f"unsupported alert status: {status!r}")
    return resolved


ALERT_CATALOG: tuple[dict, ...] = (
    {
        "category": "Vehicle Security",
        "types": (
            {
                "type": "Glass Break-in Detected",
                "label": "Glass Break-in Detected",
            },
            {
                "type": "Vehicle Alarm Triggered",
                "label": "Vehicle Alarm Triggered",
            },
        ),
    },
    {
        "category": "Events",
        "types": (
            {
                "type": "Collision Detected",
                "label": "Collision Detected",
            },
            {
                "type": "Harsh Braking",
                "label": "Harsh Braking",
            },
        ),
    },
    {
        "category": "Emergency Audio",
        "types": (
            {
                "type": "Emergency Audio Detected",
                "label": "Emergency Audio Detected (siren, screaming, or crash sound)",
            },
        ),
    },
    {
        "category": "Connectivity",
        "types": (
            {
                "type": "Vehicle Disconnected",
                "label": "Vehicle Disconnected (vehicle stops sending telemetry)",
            },
        ),
    },
    {
        "category": "Vehicle Health",
        "types": (
            {
                "type": "Low Tire Pressure",
                "label": "Low Tire Pressure",
            },
            {
                "type": "Low Battery",
                "label": "Low Battery",
            },
            {
                "type": "Low Fuel",
                "label": "Low Fuel",
            },
        ),
    },
)

# Map both canonical type names and full UI labels back to (category, canonical_type).
_TYPE_LOOKUP: dict[str, tuple[str, str]] = {}
for _group in ALERT_CATALOG:
    _category = _group["category"]
    for _entry in _group["types"]:
        _canonical = _entry["type"]
        _TYPE_LOOKUP[_canonical] = (_category, _canonical)
        _TYPE_LOOKUP[_entry["label"]] = (_category, _canonical)


def resolve_alert_type(alert_type: str, category: str | None = None) -> tuple[str, str]:
    """Validate alert_type (and optional category) and return (category, canonical_type)."""
    resolved = _TYPE_LOOKUP.get(alert_type)
    if resolved is None:
        raise ValueError(f"unsupported alert_type: {alert_type!r}")

    expected_category, canonical_type = resolved
    if category is not None and category != expected_category:
        raise ValueError(
            f"alert_type {canonical_type!r} belongs to category {expected_category!r},"
            f" not {category!r}"
        )

    return expected_category, canonical_type


def is_active_alert_status(status: str) -> bool:
    """True when an alert is unresolved/active."""
    return status in ACTIVE_ALERT_STATUSES


def alert_catalog() -> dict:
    """JSON-serializable catalog shared with the frontend."""
    return {
        "categories": [
            {
                "category": group["category"],
                "types": [dict(t) for t in group["types"]],
            }
            for group in ALERT_CATALOG
        ],
        "severities": list(ALERT_SEVERITIES),
        "active_statuses": list(ACTIVE_ALERT_STATUSES),
    }

"""Alert catalog, category/type resolution, and status conventions."""

import pytest

from avdata.alerts import (
    ACTIVE_ALERT_STATUSES,
    ALERT_CATALOG,
    ALERT_STATUS_ACKNOWLEDGED,
    ALERT_STATUS_ACTIVE,
    ALERT_STATUS_FALSE_ALARM,
    ALERT_STATUS_RESOLVED,
    alert_catalog,
    is_active_alert_status,
    normalize_alert_status,
    resolve_alert_type,
)


def test_catalog_contains_all_supported_categories_and_types():
    by_category = {
        group["category"]: [entry["type"] for entry in group["types"]]
        for group in ALERT_CATALOG
    }
    assert by_category == {
        "Vehicle Security": [
            "Glass Break-in Detected",
            "Vehicle Alarm Triggered",
        ],
        "Events": [
            "Collision Detected",
            "Harsh Braking",
        ],
        "Emergency Audio": [
            "Emergency Audio Detected",
        ],
        "Connectivity": [
            "Vehicle Disconnected",
        ],
        "Vehicle Health": [
            "Low Tire Pressure",
            "Low Battery",
            "Low Fuel",
        ],
    }


def test_resolve_alert_type_and_labels():
    assert resolve_alert_type("Glass Break-in Detected") == (
        "Vehicle Security",
        "Glass Break-in Detected",
    )
    assert resolve_alert_type(
        "Emergency Audio Detected (siren, screaming, or crash sound)"
    ) == ("Emergency Audio", "Emergency Audio Detected")
    assert resolve_alert_type(
        "Vehicle Disconnected (vehicle stops sending telemetry)"
    ) == ("Connectivity", "Vehicle Disconnected")


def test_resolve_alert_type_rejects_unknown_or_mismatched_category():
    with pytest.raises(ValueError, match="unsupported alert_type"):
        resolve_alert_type("Alien Abduction")
    with pytest.raises(ValueError, match="belongs to category"):
        resolve_alert_type("Low Fuel", category="Vehicle Security")


def test_active_status_helpers():
    for status in ACTIVE_ALERT_STATUSES:
        assert is_active_alert_status(status)
    assert is_active_alert_status(ALERT_STATUS_ACTIVE)
    assert is_active_alert_status(ALERT_STATUS_ACKNOWLEDGED)
    assert is_active_alert_status(ALERT_STATUS_FALSE_ALARM)
    assert not is_active_alert_status(ALERT_STATUS_RESOLVED)
    assert normalize_alert_status("acknowledged") == ALERT_STATUS_ACKNOWLEDGED
    assert normalize_alert_status("resolved") == ALERT_STATUS_RESOLVED
    assert normalize_alert_status("false alarm") == ALERT_STATUS_FALSE_ALARM
    with pytest.raises(ValueError, match="unsupported alert status"):
        normalize_alert_status("unknown")
    assert len(alert_catalog()["categories"]) == 5

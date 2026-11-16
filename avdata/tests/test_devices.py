from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from avdata.devices import (
    SensorConfiguration,
    audio_policy,
    network_policy,
    sensor_status,
)


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"gain_db": 37},
        {"gain_db": None},
        {"sample_rate_hz": 123},
        {"secret": 1},
        {"noise_suppression": "false"},
    ],
)
def test_invalid_settings_rejected(changes):
    with pytest.raises(ValidationError):
        SensorConfiguration(**changes)


def test_type_specific_configuration():
    config = SensorConfiguration(gain_db=12.0)
    assert config.for_device("microphone") == {"gain_db": 12.0}
    with pytest.raises(ValueError):
        config.for_device("camera")


@pytest.mark.parametrize(
    "age,expected",
    [(30, "ONLINE"), (31, "DEGRADED"), (90, "DEGRADED"), (91, "OFFLINE")],
)
def test_heartbeat_boundaries(age, expected):
    now = datetime.now(UTC)
    assert (
        sensor_status(
            now, now - timedelta(seconds=age), "ONLINE", "OK", False, False, -62
        )
        == expected
    )


def test_health_priorities():
    now = datetime.now(UTC)
    assert sensor_status(now, now, "OFFLINE", "OK", False, False, -62) == "OFFLINE"
    assert sensor_status(now, now, "ONLINE", "FAULT", True, False, -62) == "DEGRADED"
    assert sensor_status(now, now, "ONLINE", "OK", True, False, -62) == "SYNCING"
    assert sensor_status(now, now, "ONLINE", "OK", False, True, -62) == "MAINTENANCE"
    assert (
        sensor_status(now, None, "ONLINE", "OK", False, False, None)
        == "NEVER_CONNECTED"
    )


def test_authenticated_wifi_threshold_and_fallback():
    info = {
        "wifi_authenticated": True,
        "wifi_signal_dbm": -69,
        "cellular_signal_dbm": -80,
    }
    assert network_policy(info)["recommended_network"] == "wifi"
    assert network_policy(info, "cellular")["recommended_network"] == "cellular"
    assert (
        network_policy({**info, "wifi_authenticated": False})["recommended_network"]
        == "cellular"
    )
    assert (
        network_policy({**info, "wifi_signal_dbm": -70})["recommended_network"]
        == "cellular"
    )
    assert network_policy({})["recommended_network"] == "offline"


def test_emergency_and_clipping_preserve_desired_settings():
    desired = {"sample_rate_hz": 16000, "gain_db": 12.0}
    assert audio_policy(desired, True, 2.1) == {"sample_rate_hz": 44100, "gain_db": 6.0}
    assert audio_policy(desired, False, 2) == desired
    assert desired["gain_db"] == 12

"""Connectivity status at each boundary."""

from datetime import UTC, datetime, timedelta

import pytest

from avdata.connectivity import (
    NEVER_CONNECTED,
    OFFLINE,
    ONLINE,
    STALE,
    SUSPENDED,
    connectivity_status,
)

NOW = datetime(2026, 11, 11, 20, 0, 0, tzinfo=UTC)
MS = timedelta(milliseconds=1)


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (timedelta(0), ONLINE),
        (timedelta(seconds=30), ONLINE),
        (timedelta(seconds=30) + MS, STALE),
        (timedelta(seconds=90), STALE),
        (timedelta(seconds=90) + MS, OFFLINE),
        (timedelta(hours=5), OFFLINE),
        (-timedelta(seconds=2), ONLINE),  # device clock slightly ahead
    ],
    ids=["0s", "30s", "30s+1ms", "90s", "90s+1ms", "5h", "future"],
)
def test_status_by_heartbeat_age(age, expected):
    assert connectivity_status(NOW, NOW - age) == expected


def test_never_connected_without_heartbeat():
    assert connectivity_status(NOW, None) == NEVER_CONNECTED


def test_disconnect_after_last_heartbeat_is_offline_immediately():
    heartbeat = NOW - timedelta(seconds=5)
    assert connectivity_status(NOW, heartbeat, heartbeat + MS) == OFFLINE
    assert connectivity_status(NOW, heartbeat, heartbeat) == OFFLINE


def test_heartbeat_after_disconnect_is_online_again():
    heartbeat = NOW - timedelta(seconds=5)
    assert connectivity_status(NOW, heartbeat, heartbeat - MS) == ONLINE


def test_suspended_tenant_overrides_everything():
    assert connectivity_status(NOW, NOW, tenant_status="SUSPENDED") == SUSPENDED
    assert connectivity_status(NOW, None, tenant_status="SUSPENDED") == SUSPENDED

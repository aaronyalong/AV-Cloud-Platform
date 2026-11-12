"""Alert APIs, per-vehicle active alert counts, and alert simulator page."""

import time
from datetime import UTC, datetime, timedelta

import anyio
import pytest

from avdata import mongo as avmongo
from avdata.alerts import (
    ALERT_STATUS_FALSE_ALARM,
    ALERT_STATUS_RESOLVED,
)
from avdata.mongo import ALERTS, TenantDocuments

ACME = {"X-Tenant-Id": "t-acme"}
BETA = {"X-Tenant-Id": "t-beta"}


@pytest.fixture(autouse=True)
def clean_alerts(seeded, mongo_url):
    async def _clear() -> None:
        client = avmongo.connect(mongo_url)
        try:
            await client["av_test"][ALERTS].delete_many({})
        finally:
            await client.close()

    anyio.run(_clear)


def test_alert_endpoints_require_tenant_header(client):
    assert client.get("/api/v1/alerts/types").status_code == 401
    assert client.get("/api/v1/alerts").status_code == 401
    assert (
        client.post(
            "/api/v1/alerts",
            json={
                "vehicle_id": "veh-07",
                "alert_type": "Low Battery",
                "severity": "medium",
            },
        ).status_code
        == 401
    )


def test_alert_catalog_exposes_supported_types(client):
    response = client.get("/api/v1/alerts/types", headers=ACME)
    assert response.status_code == 200
    body = response.json()
    assert body["severities"] == ["low", "medium", "high"]
    categories = {
        c["category"]: [t["type"] for t in c["types"]] for c in body["categories"]
    }
    assert categories == {
        "Vehicle Security": ["Glass Break-in Detected", "Vehicle Alarm Triggered"],
        "Events": ["Collision Detected", "Harsh Braking"],
        "Emergency Audio": ["Emergency Audio Detected"],
        "Connectivity": ["Vehicle Disconnected"],
        "Vehicle Health": ["Low Tire Pressure", "Low Battery", "Low Fuel"],
    }


def test_create_alert_records_timestamp_and_updates_vehicle_count(client):
    before = {
        v["vehicle_id"]: v["alert_count"]
        for v in client.get("/api/v1/vehicles", headers=ACME).json()["vehicles"]
    }
    assert before == {"veh-07": 0, "veh-09": 0, "veh-12": 0}

    created = client.post(
        "/api/v1/alerts",
        headers=ACME,
        json={
            "vehicle_id": "veh-07",
            "alert_type": "Glass Break-in Detected",
            "severity": "high",
            "description": "  Driver side window sensor triggered  ",
        },
    )
    assert created.status_code == 201
    alert = created.json()
    assert alert["vehicle_id"] == "veh-07"
    assert alert["tenant_id"] == "t-acme"
    assert alert["category"] == "Vehicle Security"
    assert alert["alert_type"] == "Glass Break-in Detected"
    assert alert["severity"] == "high"
    assert alert["status"] == "ACTIVE"
    assert alert["description"] == "Driver side window sensor triggered"
    assert alert["created_at"]
    parsed_ts = datetime.fromisoformat(alert["created_at"])
    assert parsed_ts.tzinfo is not None

    client.post(
        "/api/v1/alerts",
        headers=ACME,
        json={
            "vehicle_id": "veh-07",
            "alert_type": "Emergency Audio Detected (siren, screaming, or crash sound)",
            "severity": "medium",
        },
    )
    client.post(
        "/api/v1/alerts",
        headers=ACME,
        json={
            "vehicle_id": "veh-09",
            "alert_type": "Low Tire Pressure",
            "severity": "low",
            "description": "   ",
        },
    )

    after = {
        v["vehicle_id"]: v["alert_count"]
        for v in client.get("/api/v1/vehicles", headers=ACME).json()["vehicles"]
    }
    assert after == {"veh-07": 2, "veh-09": 1, "veh-12": 0}


def test_vehicle_alert_count_excludes_resolved_and_false_alarm(client, mongo_url):
    async def seed_mixed_statuses() -> None:
        client_mongo = avmongo.connect(mongo_url)
        try:
            docs = TenantDocuments(client_mongo["av_test"], "t-acme")
            now = datetime.now(UTC)
            await docs.create_alert(
                vehicle_id="veh-07",
                alert_type="Collision Detected",
                severity="high",
                status="ACTIVE",
                created_at=now - timedelta(minutes=3),
            )
            await docs.create_alert(
                vehicle_id="veh-07",
                alert_type="Harsh Braking",
                severity="medium",
                status="ACKNOWLEDGED",
                created_at=now - timedelta(minutes=2),
            )
            await docs.create_alert(
                vehicle_id="veh-07",
                alert_type="Low Battery",
                severity="low",
                status=ALERT_STATUS_RESOLVED,
                created_at=now - timedelta(minutes=1),
            )
            await docs.create_alert(
                vehicle_id="veh-07",
                alert_type="Vehicle Alarm Triggered",
                severity="medium",
                status=ALERT_STATUS_FALSE_ALARM,
                created_at=now,
            )
        finally:
            await client_mongo.close()

    anyio.run(seed_mixed_statuses)

    counts = {
        v["vehicle_id"]: v["alert_count"]
        for v in client.get("/api/v1/vehicles", headers=ACME).json()["vehicles"]
    }
    assert counts["veh-07"] == 2

    active_only = client.get(
        "/api/v1/alerts", params={"active_only": True}, headers=ACME
    ).json()["alerts"]
    assert len(active_only) == 2
    assert {a["status"] for a in active_only} == {"ACTIVE", "ACKNOWLEDGED"}


def test_alerts_are_tenant_isolated_and_reject_unknown_vehicles(client):
    res = client.post(
        "/api/v1/alerts",
        headers=ACME,
        json={
            "vehicle_id": "veh-07",
            "alert_type": "Vehicle Disconnected",
            "severity": "high",
        },
    )
    assert res.status_code == 201

    assert client.get("/api/v1/alerts", headers=BETA).json()["alerts"] == []
    beta_counts = {
        v["vehicle_id"]: v["alert_count"]
        for v in client.get("/api/v1/vehicles", headers=BETA).json()["vehicles"]
    }
    assert beta_counts == {"veh-01": 0}

    assert (
        client.post(
            "/api/v1/alerts",
            headers=BETA,
            json={
                "vehicle_id": "veh-07",
                "alert_type": "Low Fuel",
                "severity": "low",
            },
        ).status_code
        == 404
    )
    assert (
        client.get(
            "/api/v1/alerts", params={"vehicle_id": "veh-07"}, headers=BETA
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/alerts",
            headers=ACME,
            json={
                "vehicle_id": "veh-999",
                "alert_type": "Low Fuel",
                "severity": "low",
            },
        ).status_code
        == 404
    )


def test_invalid_alert_payload_is_rejected(client):
    bad_type = client.post(
        "/api/v1/alerts",
        headers=ACME,
        json={"vehicle_id": "veh-07", "alert_type": "Unknown Type", "severity": "high"},
    )
    assert bad_type.status_code == 422

    bad_category = client.post(
        "/api/v1/alerts",
        headers=ACME,
        json={
            "vehicle_id": "veh-07",
            "category": "Vehicle Security",
            "alert_type": "Low Battery",
            "severity": "medium",
        },
    )
    assert bad_category.status_code == 422

    bad_severity = client.post(
        "/api/v1/alerts",
        headers=ACME,
        json={
            "vehicle_id": "veh-07",
            "alert_type": "Low Battery",
            "severity": "extreme",
        },
    )
    assert bad_severity.status_code == 422

    bad_status_filter = client.get(
        "/api/v1/alerts", params={"status": "BOGUS"}, headers=ACME
    )
    assert bad_status_filter.status_code == 422


def test_list_alerts_newest_first_and_vehicle_filter(client):
    for alert_type in ("Low Fuel", "Low Battery"):
        client.post(
            "/api/v1/alerts",
            headers=ACME,
            json={"vehicle_id": "veh-07", "alert_type": alert_type, "severity": "low"},
        )
        time.sleep(0.01)
    client.post(
        "/api/v1/alerts",
        headers=ACME,
        json={
            "vehicle_id": "veh-09",
            "alert_type": "Harsh Braking",
            "severity": "medium",
        },
    )

    all_alerts = client.get("/api/v1/alerts", headers=ACME).json()["alerts"]
    assert [a["alert_type"] for a in all_alerts] == [
        "Harsh Braking",
        "Low Battery",
        "Low Fuel",
    ]

    veh_07 = client.get(
        "/api/v1/alerts", params={"vehicle_id": "veh-07"}, headers=ACME
    ).json()["alerts"]
    assert [a["alert_type"] for a in veh_07] == ["Low Battery", "Low Fuel"]


def test_get_single_alert_by_id_is_tenant_scoped(client):
    created = client.post(
        "/api/v1/alerts",
        headers=ACME,
        json={
            "vehicle_id": "veh-07",
            "alert_type": "Collision Detected",
            "severity": "high",
            "description": "Front bumper impact sensor",
        },
    ).json()
    alert_id = created["alert_id"]

    assert client.get(f"/api/v1/alerts/{alert_id}").status_code == 401
    fetched = client.get(f"/api/v1/alerts/{alert_id}", headers=ACME)
    assert fetched.status_code == 200
    assert fetched.json()["alert_id"] == alert_id
    assert fetched.json()["description"] == "Front bumper impact sensor"

    assert client.get(f"/api/v1/alerts/{alert_id}", headers=BETA).status_code == 404
    assert client.get("/api/v1/alerts/nonexistent-id", headers=ACME).status_code == 404


def test_update_alert_status_transitions_and_tenant_isolation(client):
    created = client.post(
        "/api/v1/alerts",
        headers=ACME,
        json={
            "vehicle_id": "veh-07",
            "alert_type": "Glass Break-in Detected",
            "severity": "high",
        },
    ).json()
    alert_id = created["alert_id"]

    assert (
        client.patch(
            f"/api/v1/alerts/{alert_id}", json={"status": "ACKNOWLEDGED"}
        ).status_code
        == 401
    )
    assert (
        client.patch(
            f"/api/v1/alerts/{alert_id}",
            headers=BETA,
            json={"status": "ACKNOWLEDGED"},
        ).status_code
        == 404
    )
    assert (
        client.patch(
            f"/api/v1/alerts/{alert_id}",
            headers=ACME,
            json={"status": "BOGUS"},
        ).status_code
        == 422
    )

    ack = client.patch(
        f"/api/v1/alerts/{alert_id}",
        headers=ACME,
        json={"status": "acknowledged"},
    )
    assert ack.status_code == 200
    assert ack.json()["status"] == "ACKNOWLEDGED"
    assert ack.json()["updated_at"] is not None
    assert (
        client.get("/api/v1/alerts", params={"active_only": True}, headers=ACME).json()[
            "alerts"
        ][0]["status"]
        == "ACKNOWLEDGED"
    )

    fa = client.patch(
        f"/api/v1/alerts/{alert_id}",
        headers=ACME,
        json={"status": "false alarm"},
    )
    assert fa.status_code == 200
    assert fa.json()["status"] == "FALSE_ALARM"
    assert (
        client.get("/api/v1/vehicles", headers=ACME).json()["vehicles"][0][
            "alert_count"
        ]
        == 0
    )
    assert (
        client.get("/api/v1/alerts", params={"active_only": True}, headers=ACME).json()[
            "alerts"
        ]
        == []
    )
    recent_after_fa = client.get("/api/v1/alerts", headers=ACME).json()["alerts"]
    assert len(recent_after_fa) == 1
    assert recent_after_fa[0]["status"] == "FALSE_ALARM"

    res = client.patch(
        f"/api/v1/alerts/{alert_id}",
        headers=ACME,
        json={"status": "RESOLVED"},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "RESOLVED"
    assert (
        client.get("/api/v1/vehicles", headers=ACME).json()["vehicles"][0][
            "alert_count"
        ]
        == 0
    )
    assert (
        client.get("/api/v1/alerts", params={"active_only": True}, headers=ACME).json()[
            "alerts"
        ]
        == []
    )
    recent = client.get("/api/v1/alerts", headers=ACME).json()["alerts"]
    assert len(recent) == 1
    assert recent[0]["status"] == "RESOLVED"


def test_frontend_pages_include_dashboard_management_and_simulator(client):
    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "<th>Alerts</th>" in dashboard.text
    assert "/alerts" in dashboard.text
    assert "/alert-simulator" in dashboard.text
    assert "openVehicleAlerts" in dashboard.text
    assert "openAlertDetail" in dashboard.text
    assert 'id="alert-modal"' in dashboard.text

    management = client.get("/alerts")
    assert management.status_code == 200
    assert management.headers["content-type"].startswith("text/html")
    assert "Alert Management" in management.text
    assert "Active Alerts" in management.text
    assert "Acknowledge" in management.text
    assert "Resolve" in management.text
    assert "False Alarm" in management.text
    assert "active_only" in management.text

    simulator = client.get("/alert-simulator")
    assert simulator.status_code == 200
    assert simulator.headers["content-type"].startswith("text/html")
    assert "Alert Simulator" in simulator.text
    assert "Generate Alert" in simulator.text
    assert "/alerts" in simulator.text
    assert "openVehicleAlerts" in simulator.text
    assert "openAlertDetail" in simulator.text
    assert 'id="alert-modal"' in simulator.text
    assert "/api/v1/alerts" in simulator.text

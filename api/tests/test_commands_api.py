"""Command API: saved as SENT only when the vehicle can be reached."""

import pytest

ACME = {"X-Tenant-Id": "t-acme"}
BETA = {"X-Tenant-Id": "t-beta"}


def send(client, vehicle_id, headers=ACME, command="PULL_OVER", **extra):
    return client.post(
        f"/internal/v1/vehicles/{vehicle_id}/commands",
        json={"command": command, **extra},
        headers=headers,
    )


@pytest.mark.parametrize("vehicle_id", ["veh-07", "veh-09"], ids=["online", "stale"])
def test_command_to_reachable_vehicle_is_sent(client, vehicle_id):
    response = send(client, vehicle_id, alert_id="alert-1")
    assert response.status_code == 202
    body = response.json()
    assert (body["vehicle_id"], body["command"], body["status"]) == (
        vehicle_id,
        "PULL_OVER",
        "SENT",
    )
    assert body["alert_id"] == "alert-1"
    assert body["acked_at"] is None

    listed = client.get(f"/internal/v1/vehicles/{vehicle_id}/commands", headers=ACME)
    assert listed.json()["commands"][0]["command_id"] == body["command_id"]


@pytest.mark.parametrize(
    ("headers", "vehicle_id", "status"),
    [(ACME, "veh-12", "NEVER_CONNECTED"), (BETA, "veh-01", "OFFLINE")],
    ids=["never-connected", "offline"],
)
def test_command_to_unreachable_vehicle_is_409(client, headers, vehicle_id, status):
    response = send(client, vehicle_id, headers=headers)
    assert response.status_code == 409
    assert status in response.json()["detail"]
    listed = client.get(f"/internal/v1/vehicles/{vehicle_id}/commands", headers=headers)
    assert listed.json()["commands"] == []


def test_command_to_other_tenants_vehicle_is_404(client):
    assert send(client, "veh-07", headers=BETA).status_code == 404
    listed = client.get("/internal/v1/vehicles/veh-07/commands", headers=BETA)
    assert listed.status_code == 404


def test_unknown_command_is_rejected(client):
    assert send(client, "veh-07", command="SELF_DESTRUCT").status_code == 422


def test_command_requires_tenant_header(client):
    assert send(client, "veh-07", headers={}).status_code == 401

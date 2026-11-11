"""Vehicle and telemetry APIs against the av_test databases."""

from datetime import timedelta

from avdata.messages import TELEMETRY_FIELDS

ACME = {"X-Tenant-Id": "t-acme"}
BETA = {"X-Tenant-Id": "t-beta"}


def test_missing_tenant_header_is_401(client):
    assert client.get("/api/v1/vehicles").status_code == 401
    assert client.get("/api/v1/telemetry?vehicle_id=veh-07").status_code == 401


def test_vehicles_have_connectivity_status(client):
    response = client.get("/api/v1/vehicles", headers=ACME)
    assert response.status_code == 200
    statuses = {v["vehicle_id"]: v["status"] for v in response.json()["vehicles"]}
    assert statuses == {
        "veh-07": "ONLINE",
        "veh-09": "STALE",
        "veh-12": "NEVER_CONNECTED",
    }


def test_disconnected_vehicle_is_offline(client):
    vehicles = client.get("/api/v1/vehicles", headers=BETA).json()["vehicles"]
    assert [(v["vehicle_id"], v["status"]) for v in vehicles] == [("veh-01", "OFFLINE")]


def test_tenant_sees_only_its_own_vehicles(client):
    acme = {
        v["vehicle_id"]
        for v in client.get("/api/v1/vehicles", headers=ACME).json()["vehicles"]
    }
    beta = {
        v["vehicle_id"]
        for v in client.get("/api/v1/vehicles", headers=BETA).json()["vehicles"]
    }
    assert acme == {"veh-07", "veh-09", "veh-12"}
    assert beta == {"veh-01"}


def test_unknown_tenant_sees_nothing(client):
    response = client.get("/api/v1/vehicles", headers={"X-Tenant-Id": "t-nobody"})
    assert response.json()["vehicles"] == []


def test_telemetry_for_own_vehicle(client, recent):
    params = {"vehicle_id": "veh-07", "from": recent.isoformat()}
    body = client.get("/api/v1/telemetry", params=params, headers=ACME).json()
    assert body["total_samples"] == 10
    assert [p["speed_kmh"] for p in body["points"]] == [float(i) for i in range(10)]
    assert set(body["points"][0]) == {"ts", *TELEMETRY_FIELDS}


def test_other_tenants_telemetry_is_not_found(client, recent):
    params = {"vehicle_id": "veh-07", "from": recent.isoformat()}
    response = client.get("/api/v1/telemetry", params=params, headers=BETA)
    assert response.status_code == 404
    assert response.json() == {"detail": "vehicle not found"}


def test_telemetry_fields_and_max_points(client, recent):
    params = {
        "vehicle_id": "veh-07",
        "from": recent.isoformat(),
        "fields": "speed_kmh",
        "max_points": 5,
    }
    body = client.get("/api/v1/telemetry", params=params, headers=ACME).json()
    assert [set(p) for p in body["points"]] == [{"ts", "speed_kmh"}] * 5
    assert [p["speed_kmh"] for p in body["points"]] == [0.0, 2.0, 4.0, 6.0, 8.0]


def test_telemetry_rejects_unknown_fields(client):
    params = {"vehicle_id": "veh-07", "fields": "speed_kmh,password"}
    response = client.get("/api/v1/telemetry", params=params, headers=ACME)
    assert response.status_code == 422


def test_events_are_newest_first_and_tenant_scoped(client, recent):
    acme = client.get("/api/v1/events", headers=ACME).json()["events"]
    beta = client.get("/api/v1/events", headers=BETA).json()["events"]
    assert [(e["vehicle_id"], e["ts"]) for e in acme] == [
        ("veh-07", (recent + timedelta(seconds=5)).isoformat().replace("+00:00", "Z")),
        ("veh-07", (recent + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")),
    ]
    assert [e["vehicle_id"] for e in beta] == ["veh-01"]
    assert acme[0]["type"] == "collision"


def test_other_tenants_vehicle_events_are_not_found(client):
    response = client.get("/api/v1/events?vehicle_id=veh-07", headers=BETA)
    assert response.status_code == 404

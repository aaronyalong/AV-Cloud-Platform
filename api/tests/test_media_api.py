"""Media download links: issued only for the caller's tenant and vehicles."""

import pytest
from fastapi.testclient import TestClient

from app.main import app

ACME = {"X-Tenant-Id": "t-acme"}
BETA = {"X-Tenant-Id": "t-beta"}
KEY = "t-acme/r1/veh-07/camera/frame-0001.jpg"


@pytest.fixture
def media_client(seeded, monkeypatch, pg_url, tmp_path):
    (tmp_path / KEY).parent.mkdir(parents=True)
    (tmp_path / KEY).write_bytes(b"fake jpeg bytes")
    monkeypatch.setenv("APP_DATABASE_URL", pg_url("av_app"))
    monkeypatch.setenv("MONGO_DB", "av_test")
    monkeypatch.setenv("MEDIA_DIR", str(tmp_path))
    with TestClient(app) as test_client:
        yield test_client


def link(client, key, headers=ACME):
    return client.get(
        "/api/v1/media/download-url", params={"key": key}, headers=headers
    )


def test_own_media_link_downloads_the_file(media_client):
    response = link(media_client, KEY)
    assert response.status_code == 200
    body = response.json()
    assert body["expires_in_s"] == 300

    # The link alone is the permission, like an S3 presigned URL.
    download = media_client.get(body["url"])
    assert download.status_code == 200
    assert download.content == b"fake jpeg bytes"


def test_other_tenants_key_is_not_found(media_client):
    assert link(media_client, KEY, headers=BETA).status_code == 404


def test_key_for_unknown_vehicle_is_not_found(media_client):
    assert link(media_client, "t-acme/r1/veh-99/camera/f.jpg").status_code == 404


def test_malformed_key_is_rejected(media_client):
    assert link(media_client, "t-acme/../t-beta/veh-01/camera/f.jpg").status_code == 422


def test_tampered_link_is_forbidden(media_client):
    url = link(media_client, KEY).json()["url"]
    assert media_client.get(url.replace("signature=", "signature=0")).status_code == 403
    assert media_client.get(url.replace("veh-07", "veh-09")).status_code == 403


def test_link_for_missing_file_is_404(media_client):
    url = link(media_client, "t-acme/r1/veh-07/camera/missing.jpg").json()["url"]
    assert media_client.get(url).status_code == 404


def test_link_requires_tenant_header(media_client):
    assert link(media_client, KEY, headers={}).status_code == 401

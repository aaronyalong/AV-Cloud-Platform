"""Media keys and signed download links."""

import time
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from avdata.media import LocalMediaStore, MediaKey, MediaKeyError

KEY = "t-acme/r1/veh-07/camera/frame-0001.jpg"


@pytest.fixture
def store(tmp_path):
    return LocalMediaStore(tmp_path, b"test-signing-key")


def link_parts(url: str) -> tuple[str, int, str]:
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    key = unquote(parts.path.removeprefix("/local-media/"))
    return key, int(query["expires"][0]), query["signature"][0]


@pytest.mark.parametrize(
    "key",
    [
        "t-acme/r1/veh-07/camera",
        "t-acme/r1/veh-07/camera/a/b.jpg",
        "t-acme/r1/../camera/f.jpg",
        "t-acme//veh-07/camera/f.jpg",
    ],
    ids=["too-short", "too-long", "dot-dot", "empty-part"],
)
def test_malformed_keys_are_rejected(key):
    with pytest.raises(MediaKeyError):
        MediaKey.parse(key)


def test_key_parts():
    key = MediaKey.parse(KEY)
    assert (key.tenant_id, key.vehicle_id, key.media_type) == (
        "t-acme",
        "veh-07",
        "camera",
    )
    assert str(key) == KEY


def test_signed_link_resolves_to_the_file(store, tmp_path):
    key, expires, signature = link_parts(store.download_url("t-acme", KEY, 300))
    assert key == KEY
    assert expires > time.time()
    assert store.verified_path(key, expires, signature) == (tmp_path / KEY).resolve()


def test_store_refuses_other_tenants_key(store):
    with pytest.raises(MediaKeyError):
        store.download_url("t-beta", KEY, 300)


def test_tampered_link_is_refused(store):
    key, expires, signature = link_parts(store.download_url("t-acme", KEY, 300))
    other = "t-beta/r1/veh-01/camera/frame-0001.jpg"
    assert store.verified_path(other, expires, signature) is None
    assert store.verified_path(key, expires + 3600, signature) is None
    assert store.verified_path(key, expires, "0" * 64) is None


def test_expired_link_is_refused(store):
    key, expires, signature = link_parts(store.download_url("t-acme", KEY, -1))
    assert store.verified_path(key, expires, signature) is None

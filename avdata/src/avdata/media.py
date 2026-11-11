"""Media files (camera frames, audio clips) and short-lived download URLs.

Keys follow tenant/run/vehicle/type/file. In AWS, S3 presigned URLs are
planned; locally, LocalMediaStore signs URLs the same way (HMAC with an
expiry) and the API serves the file from a folder.
"""

import hashlib
import hmac
import time
from dataclasses import astuple, dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import quote


class MediaKeyError(ValueError):
    """The key is malformed or belongs to another tenant."""


@dataclass(frozen=True)
class MediaKey:
    tenant_id: str
    run_id: str
    vehicle_id: str
    media_type: str
    filename: str

    @classmethod
    def parse(cls, key: str) -> "MediaKey":
        parts = key.split("/")
        if len(parts) != 5 or any(p in ("", ".", "..") for p in parts):
            raise MediaKeyError("key must be tenant/run/vehicle/type/file")
        return cls(*parts)

    def __str__(self) -> str:
        return "/".join(astuple(self))


class MediaStore(Protocol):
    def download_url(self, tenant_id: str, key: str, expires_in_s: int) -> str:
        """A URL that gives read access to one file until it expires."""


class LocalMediaStore:
    """Signed, expiring URLs for files in a local folder (stand-in for S3)."""

    def __init__(
        self, root: Path, signing_key: bytes, url_prefix: str = "/local-media"
    ) -> None:
        self.root = root
        self._signing_key = signing_key
        self.url_prefix = url_prefix

    def _signature(self, key: str, expires: int) -> str:
        message = f"{key}\n{expires}".encode()
        return hmac.new(self._signing_key, message, hashlib.sha256).hexdigest()

    def download_url(self, tenant_id: str, key: str, expires_in_s: int) -> str:
        media_key = MediaKey.parse(key)
        if media_key.tenant_id != tenant_id:
            raise MediaKeyError("key belongs to another tenant")
        expires = int(time.time()) + expires_in_s
        signature = self._signature(str(media_key), expires)
        return f"{self.url_prefix}/{quote(str(media_key))}?expires={expires}&signature={signature}"

    def verified_path(self, key: str, expires: int, signature: str) -> Path | None:
        """The file for a signed URL, or None if the signature or expiry is bad."""
        expected = self._signature(key, expires)
        if not hmac.compare_digest(expected, signature) or expires < time.time():
            return None
        path = (self.root / key).resolve()
        return path if path.is_relative_to(self.root.resolve()) else None

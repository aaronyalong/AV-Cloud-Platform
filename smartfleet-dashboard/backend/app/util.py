"""Shared helpers: JSON + ETag responses (304 when unchanged), cursors, request metrics."""
import hashlib
import json
import time
from collections import deque
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import Request, Response


def _default(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, (UUID,)):
        return str(o)
    if isinstance(o, Decimal):
        return float(o)
    raise TypeError(type(o))


def dumps(obj) -> str:
    return json.dumps(obj, default=_default, separators=(",", ":"))


def etag_json(request: Request, payload, max_age: int = 0) -> Response:
    """Strong ETag over the payload; returns 304 when If-None-Match matches (polling efficiency)."""
    body = dumps(payload)
    tag = '"' + hashlib.sha256(body.encode()).hexdigest()[:32] + '"'
    headers = {"ETag": tag, "Cache-Control": f"private, max-age={max_age}"}
    if request.headers.get("if-none-match") == tag:
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)


def json_response(payload, status: int = 200) -> Response:
    return Response(content=dumps(payload), media_type="application/json", status_code=status)


def encode_cursor(ts: datetime, alert_id) -> str:
    return f"{ts.isoformat()}|{alert_id}"


def decode_cursor(cur: str | None):
    if not cur or "|" not in cur:
        return None, None
    ts, aid = cur.split("|", 1)
    return datetime.fromisoformat(ts), aid


class RequestMetrics:
    """In-process request latency / error counters for the System Health page (last 5 minutes)."""

    def __init__(self, window_s: int = 300):
        self.window_s = window_s
        self.samples: deque = deque(maxlen=20000)   # (t, ms, status)

    def add(self, ms: float, status: int):
        self.samples.append((time.time(), ms, status))

    def snapshot(self):
        cutoff = time.time() - self.window_s
        recent = [s for s in self.samples if s[0] >= cutoff]
        if not recent:
            return {"requests": 0, "p50_ms": None, "p95_ms": None, "error_rate": 0.0, "rps": 0.0}
        lat = sorted(s[1] for s in recent)
        p = lambda q: round(lat[min(len(lat) - 1, int(q * len(lat)))], 1)  # noqa: E731
        errors = sum(1 for s in recent if s[2] >= 500)
        return {"requests": len(recent), "p50_ms": p(0.50), "p95_ms": p(0.95),
                "error_rate": round(errors / len(recent), 4), "rps": round(len(recent) / self.window_s, 2)}


metrics = RequestMetrics()

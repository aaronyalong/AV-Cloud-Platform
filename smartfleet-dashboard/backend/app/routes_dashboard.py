"""D-01 summary, D-02 live delta, D-03 map  (P1 Fleet dashboard)."""
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import text

from .auth import AuthContext, require
from .db import rows
from .rules import OPEN_STATUSES, connectivity, tile_status
from .util import decode_cursor, encode_cursor, etag_json

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])
WINDOWS = {"1h": 3600, "24h": 86400, "7d": 7 * 86400}
_cache: dict = {}            # (tenant, scope, window) -> (expires, payload)   2 s TTL


def scope(ctx: AuthContext, col: str = "vehicle_id"):
    """BR-2 owner scope as an extra SQL predicate."""
    if ctx.vehicle_scope is None:
        return "", {}
    return f" AND {col} = ANY(:vs)", {"vs": ctx.vehicle_scope or ["__none__"]}


ALERT_COLS = """alert_id, vehicle_id, type, severity, status, confidence, model_name, model_version,
                assignee, version, event_ts, created_at, updated_at, x, y"""


def build_summary(ctx: AuthContext, window: str):
    secs = WINDOWS.get(window, 86400)
    now = datetime.now(timezone.utc)
    sc, sp = scope(ctx)
    p = {"t": ctx.tenant_id, "w0": now - timedelta(seconds=secs), "w1": now - timedelta(seconds=2 * secs), **sp}
    with ctx.tx() as c:
        veh = rows(c.execute(text(f"SELECT vehicle_id, last_heartbeat, run_id FROM vehicle "
                                  f"WHERE tenant_id=:t{sc}"), p))
        k = c.execute(text(f"""
            SELECT count(*) FILTER (WHERE status = ANY(:open))                            AS open_alerts,
                   count(*) FILTER (WHERE severity='critical' AND status='open')          AS crit_unacked,
                   extract(epoch FROM now() - min(created_at)
                           FILTER (WHERE severity='critical' AND status='open'))          AS crit_oldest_s,
                   count(*) FILTER (WHERE created_at >= :w0)                              AS created_now,
                   count(*) FILTER (WHERE created_at >= :w1 AND created_at < :w0)         AS created_prev,
                   avg(extract(epoch FROM ack_at - created_at)) FILTER (WHERE ack_at >= :w0) AS mtta_s,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM first_delivered_at - event_ts))
                       FILTER (WHERE first_delivered_at >= now() - interval '15 minutes') AS lat_p95
            FROM alert WHERE tenant_id=:t{sc}"""), {**p, "open": list(OPEN_STATUSES)}).mappings().one()
        feed = rows(c.execute(text(f"""SELECT {ALERT_COLS} FROM alert
                   WHERE tenant_id=:t AND status = ANY(:open){sc}
                   ORDER BY created_at DESC LIMIT 20"""), {**p, "open": list(OPEN_STATUSES)}))
        cursor_row = c.execute(text(f"SELECT updated_at, alert_id FROM alert WHERE tenant_id=:t{sc} "
                                    f"ORDER BY updated_at DESC, alert_id DESC LIMIT 1"), p).first()
        # K5: first time an alert reaches a dashboard (alerts no dashboard saw within 10 s are not measured)
        new_ids = [str(a["alert_id"]) for a in feed]
        if new_ids:
            c.execute(text("UPDATE alert SET first_delivered_at=now() WHERE tenant_id=:t "
                           "AND first_delivered_at IS NULL AND created_at > now() - interval '10 seconds' "
                           "AND alert_id = ANY(CAST(:ids AS uuid[]))"),
                      {"t": ctx.tenant_id, "ids": new_ids})
    states = [connectivity(v["last_heartbeat"], now) for v in veh]
    online, total = states.count("online"), len(veh)
    runs = {v["run_id"] for v, s in zip(veh, states) if s == "online" and v["run_id"]}
    delta = None
    if k["created_prev"]:
        delta = round(100 * (k["created_now"] - k["created_prev"]) / k["created_prev"], 1)
    mtta = round(k["mtta_s"], 1) if k["mtta_s"] is not None else None
    p95 = round(k["lat_p95"], 2) if k["lat_p95"] is not None else None
    oldest = round(k["crit_oldest_s"] or 0)
    kpis = [
        {"key": "vehicles_online", "label": "Vehicles online", "value": online, "total": total,
         "sub": f"{states.count('stale')} stale · {states.count('offline')} offline",
         "status": tile_status("vehicles_online", online, {"total": total})},
        {"key": "open_alerts", "label": "Open alerts", "value": k["open_alerts"], "delta_pct": delta,
         "sub": (f"{'▲' if (delta or 0) >= 0 else '▼'} {abs(delta)}% vs prev. {window}" if delta is not None
                 else f"{k['created_now']} created in {window}"), "status": "neutral"},
        {"key": "critical_unacked", "label": "Critical unacked", "value": k["crit_unacked"],
         "sub": f"oldest {oldest} s" if k["crit_unacked"] else "none waiting",
         "status": tile_status("critical_unacked", k["crit_unacked"], {"oldest_s": oldest})},
        {"key": "mtta_s", "label": "Mean time to ack", "value": mtta, "unit": "s", "sub": "target ≤ 120 s",
         "status": tile_status("mtta_s", mtta)},
        {"key": "latency_p95_s", "label": "Alert latency p95", "value": p95, "unit": "s", "sub": "last 15 min · SLO 3.0 s",
         "status": tile_status("latency_p95_s", p95)},
        {"key": "active_runs", "label": "Active sim runs", "value": len(runs),
         "sub": ", ".join(sorted(runs)) or "none", "status": "neutral"},
    ]
    return {"tenant_id": ctx.tenant_id, "window": window, "generated_at": now, "kpis": kpis,
            "recent_alerts": feed,
            "cursor": encode_cursor(*cursor_row) if cursor_row else None}


@router.get("/summary")
def summary(request: Request, window: str = Query("24h", pattern="^(1h|24h|7d)$"),
            ctx: AuthContext = Depends(require("dashboard:read"))):
    key = (ctx.tenant_id, tuple(ctx.vehicle_scope or []), window)
    hit = _cache.get(key)
    if hit and hit[0] > time.time():
        payload = hit[1]
    else:
        payload = build_summary(ctx, window)
        _cache[key] = (time.time() + 2, payload)
    return etag_json(request, payload)


@router.get("/live")
def live(request: Request, since: str = Query(...), max: int = Query(50, ge=1, le=200),
         ctx: AuthContext = Depends(require("dashboard:read"))):
    ts, aid = decode_cursor(since)
    sc, sp = scope(ctx)
    with ctx.tx() as c:
        if ts is None:
            data = []
        else:
            data = rows(c.execute(text(f"""SELECT {ALERT_COLS} FROM alert
                       WHERE tenant_id=:t AND (updated_at, alert_id) > (:ts, CAST(:aid AS uuid)){sc}
                       ORDER BY updated_at, alert_id LIMIT :lim"""),
                       {"t": ctx.tenant_id, "ts": ts, "aid": aid, "lim": max + 1, **sp}))
        more = len(data) > max
        data = data[:max]
        if data:
            c.execute(text("UPDATE alert SET first_delivered_at=now() WHERE tenant_id=:t "
                           "AND first_delivered_at IS NULL AND created_at > now() - interval '10 seconds' "
                           "AND alert_id = ANY(CAST(:ids AS uuid[]))"),
                      {"t": ctx.tenant_id, "ids": [str(a["alert_id"]) for a in data]})
    nxt = encode_cursor(data[-1]["updated_at"], data[-1]["alert_id"]) if data else since
    return etag_json(request, {"alerts": data, "cursor": nxt, "has_more": more,
                               "server_time": datetime.now(timezone.utc)})


@router.get("/map")
def fleet_map(request: Request, ctx: AuthContext = Depends(require("dashboard:read"))):
    sc, sp = scope(ctx, "v.vehicle_id")
    now = datetime.now(timezone.utc)
    with ctx.tx() as c:
        data = rows(c.execute(text(f"""
            SELECT v.vehicle_id, v.name, v.x, v.y, v.speed_kmh, v.last_heartbeat, v.run_id,
                   (SELECT a.severity FROM alert a WHERE a.vehicle_id=v.vehicle_id AND a.status='open'
                     ORDER BY CASE a.severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1
                              WHEN 'medium' THEN 2 ELSE 3 END LIMIT 1) AS open_severity
            FROM vehicle v WHERE v.tenant_id=:t{sc} ORDER BY v.vehicle_id"""),
            {"t": ctx.tenant_id, **sp}))
    for v in data:
        v["status"] = connectivity(v["last_heartbeat"], now)
    return etag_json(request, {"vehicles": data})

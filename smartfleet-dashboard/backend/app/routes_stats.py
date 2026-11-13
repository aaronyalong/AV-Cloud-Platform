"""S-01..S-04: statistics from stat_rollup and CSV exports  (P8 Statistics)."""
import csv
import io
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import text

from . import config, db
from .auth import AuthContext, require
from .db import rows, tx
from .rules import choose_bucket
from .util import dumps, etag_json, json_response

router = APIRouter(prefix="/api/v1/stats", tags=["stats"])


def _range(frm: datetime | None, to: datetime | None):
    to = to or datetime.now(timezone.utc)
    frm = frm or to - timedelta(days=7)
    if frm >= to or (to - frm).days > 400:
        raise HTTPException(422, "invalid range: from < to and span <= 400 days")
    return frm, to


def _owner_block(ctx: AuthContext):
    if ctx.role == "vehicle_owner":                                     # BR-2: no fleet statistics
        raise HTTPException(403, "fleet statistics are not available to vehicle owners")


@router.get("/alerts")
def alert_series(request: Request, frm: datetime | None = Query(None, alias="from"), to: datetime | None = None,
                 bucket: str = Query("auto", pattern="^(auto|minute|hour|day)$"),
                 group_by: str = Query("type", pattern="^(type|severity)$"), tz: str = config.DEFAULT_TZ,
                 ctx: AuthContext = Depends(require("stats:read"))):
    _owner_block(ctx)
    frm, to = _range(frm, to)
    b = choose_bucket((to - frm).total_seconds(), bucket)
    part = 1 if group_by == "type" else 2
    with ctx.tx() as c:
        data = rows(c.execute(text(f"""
            SELECT date_trunc(:b, bucket_start AT TIME ZONE :tz) AS ts, split_part(dim_key,'|',{part}) AS g,
                   sum(count_val)::int AS n
            FROM stat_rollup WHERE tenant_id=:t AND metric='alerts' AND bucket_start >= :f AND bucket_start < :to
            GROUP BY 1, 2 ORDER BY 1"""), {"b": b, "tz": tz, "t": ctx.tenant_id, "f": frm, "to": to}))
    groups = sorted({r["g"] for r in data})
    times = sorted({r["ts"] for r in data})
    idx = {(r["ts"], r["g"]): r["n"] for r in data}
    points = [{"ts": t, **{g: idx.get((t, g), 0) for g in groups}} for t in times]
    return etag_json(request, {"bucket": b, "groups": groups, "points": points,
                               "total": sum(r["n"] for r in data)}, max_age=30)


@router.get("/heatmap")
def heatmap(request: Request, frm: datetime | None = Query(None, alias="from"), to: datetime | None = None,
            tz: str = config.DEFAULT_TZ, ctx: AuthContext = Depends(require("stats:read"))):
    _owner_block(ctx)
    frm, to = _range(frm, to)
    with ctx.tx() as c:
        data = rows(c.execute(text("""
            SELECT extract(isodow FROM bucket_start AT TIME ZONE :tz)::int AS dow,
                   extract(hour   FROM bucket_start AT TIME ZONE :tz)::int AS hour, sum(count_val)::int AS n
            FROM stat_rollup WHERE tenant_id=:t AND metric='alerts' AND bucket_start >= :f AND bucket_start < :to
            GROUP BY 1, 2"""), {"tz": tz, "t": ctx.tenant_id, "f": frm, "to": to}))
    grid = [[0] * 24 for _ in range(7)]
    for r in data:
        grid[r["dow"] - 1][r["hour"]] = r["n"]
    return etag_json(request, {"days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], "grid": grid,
                               "max": max(max(row) for row in grid)}, max_age=30)


@router.get("/latency")
def latency(request: Request, frm: datetime | None = Query(None, alias="from"), to: datetime | None = None,
            bucket: str = Query("auto", pattern="^(auto|minute|hour|day)$"), tz: str = config.DEFAULT_TZ,
            ctx: AuthContext = Depends(require("stats:read"))):
    _owner_block(ctx)
    frm, to = _range(frm, to)
    b = choose_bucket((to - frm).total_seconds(), bucket)
    with ctx.tx() as c:
        series = rows(c.execute(text("""
            SELECT date_trunc(:b, bucket_start AT TIME ZONE :tz) AS ts,
                   round((sum(p50_val*count_val)/nullif(sum(count_val),0))::numeric, 2) AS p50,
                   round(max(p95_val)::numeric, 2) AS p95, sum(count_val)::int AS n
            FROM stat_rollup WHERE tenant_id=:t AND metric='latency' AND bucket_start >= :f AND bucket_start < :to
            GROUP BY 1 ORDER BY 1"""), {"b": b, "tz": tz, "t": ctx.tenant_id, "f": frm, "to": to}))
        agg = c.execute(text("""
            SELECT percentile_cont(0.5)  WITHIN GROUP (ORDER BY extract(epoch FROM first_delivered_at-event_ts)) p50,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM first_delivered_at-event_ts)) p95,
                   avg(extract(epoch FROM ack_at-created_at)) mtta, avg(extract(epoch FROM resolved_at-created_at))
                   FILTER (WHERE status='resolved') mttr
            FROM alert WHERE tenant_id=:t AND created_at >= :f AND created_at < :to"""),
            {"t": ctx.tenant_id, "f": frm, "to": to}).mappings().one()
    r2 = lambda v: round(float(v), 2) if v is not None else None  # noqa: E731
    return etag_json(request, {"bucket": b, "slo_s": 3.0, "series": series,
                               "overall": {k: r2(v) for k, v in agg.items()}}, max_age=30)


@router.get("/detection-quality")
def detection_quality(request: Request, frm: datetime | None = Query(None, alias="from"),
                      to: datetime | None = None, ctx: AuthContext = Depends(require("stats:read"))):
    _owner_block(ctx)
    frm, to = _range(frm, to)
    with ctx.tx() as c:
        by_type = rows(c.execute(text("""
            SELECT type, count(*) FILTER (WHERE status='false_alarm')::int AS false_alarms,
                   count(*)::int AS closed,
                   round(avg(confidence)::numeric, 3) AS avg_conf
            FROM alert WHERE tenant_id=:t AND resolved_at >= :f AND resolved_at < :to
              AND status IN ('resolved','false_alarm')
            GROUP BY type ORDER BY type"""), {"t": ctx.tenant_id, "f": frm, "to": to}))
        by_model = rows(c.execute(text("""
            SELECT model_name, model_version, count(*)::int AS alerts,
                   count(*) FILTER (WHERE status='false_alarm')::int AS false_alarms,
                   round(avg(confidence)::numeric, 3) AS avg_conf
            FROM alert WHERE tenant_id=:t AND created_at >= :f AND created_at < :to
            GROUP BY 1, 2 ORDER BY 1, 2"""), {"t": ctx.tenant_id, "f": frm, "to": to}))
    for r in by_type:
        r["far"] = round(r["false_alarms"] / r["closed"], 3) if r["closed"] else 0
    return etag_json(request, {"by_type": by_type, "by_model": by_model}, max_age=30)


# ---------------- S-04 CSV export (async job, file in S3, link valid 24 h)
class ExportIn(BaseModel):
    days: int = 7


def _run_export(export_id: str, tenant_id: str, days: int):
    with tx(tenant_id) as c:
        data = rows(c.execute(text("""SELECT alert_id, vehicle_id, type, severity, status, confidence,
                   model_name, model_version, event_ts, created_at, ack_at, resolved_at, assignee
                   FROM alert WHERE tenant_id=:t AND created_at >= now() - make_interval(days => :d)
                   ORDER BY created_at LIMIT 100000"""), {"t": tenant_id, "d": days}))
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(data[0].keys()) if data else ["alert_id"])
    w.writeheader()
    w.writerows(data)
    key = f"{tenant_id}/exports/{export_id}.csv"
    db.s3.put_object(Bucket=config.S3_BUCKET, Key=key, Body=buf.getvalue().encode(), ContentType="text/csv")
    with tx(tenant_id) as c:
        c.execute(text("UPDATE export_job SET status='done', s3_key=:k, rows=:n WHERE export_id=CAST(:id AS uuid)"),
                  {"k": key, "n": len(data), "id": export_id})


@router.post("/exports", status_code=202)
def create_export(body: ExportIn, bg: BackgroundTasks, ctx: AuthContext = Depends(require("stats:export"))):
    with ctx.tx() as c:
        export_id = str(c.execute(text("INSERT INTO export_job (tenant_id, user_email, query) "
                                       "VALUES (:t,:u,CAST(:q AS jsonb)) RETURNING export_id"),
                                  {"t": ctx.tenant_id, "u": ctx.email, "q": dumps(body.model_dump())}).scalar())
    bg.add_task(_run_export, export_id, ctx.tenant_id, body.days)
    return json_response({"export_id": export_id, "status": "queued",
                          "location": f"/api/v1/stats/exports/{export_id}"}, 202)


@router.get("/exports/{export_id}")
def get_export(export_id: str, ctx: AuthContext = Depends(require("stats:export"))):
    with ctx.tx() as c:
        j = c.execute(text("SELECT * FROM export_job WHERE tenant_id=:t AND export_id=CAST(:id AS uuid)"),
                      {"t": ctx.tenant_id, "id": export_id}).mappings().first()
    if not j:
        raise HTTPException(404, "export not found")
    out = {"export_id": export_id, "status": j["status"], "rows": j["rows"]}
    if j["status"] == "done":
        out["url"] = db.presign_get(j["s3_key"], ttl=86400)
    return json_response(out)

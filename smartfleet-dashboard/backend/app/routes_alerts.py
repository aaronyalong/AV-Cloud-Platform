"""A-01..A-04: alert list, detail, actions (DT-2), evidence presign  (P3 Alerts workspace)."""
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from . import db
from .auth import AuthContext, require
from .db import rows
from .routes_dashboard import ALERT_COLS, _cache, scope
from .rules import dt2_check
from .util import dumps, etag_json, json_response

router = APIRouter(prefix="/api/v1", tags=["alerts"])


@router.get("/alerts")
def list_alerts(request: Request,
                status: list[str] | None = Query(None), severity: list[str] | None = Query(None),
                type: list[str] | None = Query(None), vehicle_id: str | None = None,
                sort: str = Query("-created_at", pattern="^(-created_at|-severity|age)$"),
                limit: int = Query(25, ge=1, le=100), cursor: str | None = None,
                ctx: AuthContext = Depends(require("alerts:read"))):
    sc, sp = scope(ctx)
    where, p = ["tenant_id=:t"], {"t": ctx.tenant_id, **sp}
    for col, val in (("status", status), ("severity", severity), ("type", type)):
        if val:
            where.append(f"{col} = ANY(:{col})")
            p[col] = val
    if vehicle_id:
        where.append("vehicle_id=:vid")
        p["vid"] = vehicle_id
    order = {"-created_at": "created_at DESC, alert_id DESC",
             "-severity": "CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 "
                          "ELSE 3 END, created_at DESC, alert_id DESC",
             "age": "created_at ASC, alert_id ASC"}[sort]
    offset = int(cursor) if cursor and cursor.isdigit() else 0      # simple offset cursor for sorted views
    with ctx.tx() as c:
        total = c.execute(text(f"SELECT count(*) FROM alert WHERE {' AND '.join(where)}{sc}"), p).scalar()
        data = rows(c.execute(text(f"SELECT {ALERT_COLS} FROM alert WHERE {' AND '.join(where)}{sc} "
                                   f"ORDER BY {order} LIMIT :lim OFFSET :off"),
                              {**p, "lim": limit, "off": offset}))
    nxt = str(offset + limit) if offset + limit < total else None
    return etag_json(request, {"items": data, "total": total, "next_cursor": nxt})


def _get_alert(c, ctx: AuthContext, alert_id: str):
    sc, sp = scope(ctx)
    a = c.execute(text(f"SELECT * FROM alert WHERE tenant_id=:t AND alert_id=CAST(:id AS uuid){sc}"),
                  {"t": ctx.tenant_id, "id": alert_id, **sp}).mappings().first()
    if not a:
        raise HTTPException(404, "alert not found")          # 404, never reveals other tenants' alerts
    return dict(a)


@router.get("/alerts/{alert_id}")
def alert_detail(alert_id: str, ctx: AuthContext = Depends(require("alerts:read"))):
    with ctx.tx() as c:
        a = _get_alert(c, ctx, alert_id)
        a["history"] = rows(c.execute(text(
            "SELECT actor, action, from_status, to_status, note, at FROM alert_action "
            "WHERE alert_id=CAST(:id AS uuid) ORDER BY at"), {"id": alert_id}))
        a["vehicle"] = dict(c.execute(text("SELECT vehicle_id, name, run_id, speed_kmh FROM vehicle "
                                           "WHERE vehicle_id=:v"), {"v": a["vehicle_id"]}).mappings().first())
    return json_response(a)


class ActionIn(BaseModel):
    action: str = Field(pattern="^(acknowledge|assign|dispatch|resolve|false_alarm)$")
    version: int
    note: str | None = Field(default=None, max_length=1000)
    assignee: str | None = None


@router.post("/alerts/{alert_id}/actions")
def alert_action(alert_id: str, body: ActionIn, idempotency_key: str = Header(...),
                 ctx: AuthContext = Depends(require("alerts:act"))):
    with ctx.tx() as c:
        prev = c.execute(text("SELECT response FROM idem_key WHERE user_email=:u AND idem_key=:k"),
                         {"u": ctx.email, "k": idempotency_key}).scalar()
        if prev:                                                    # safe replay
            return json_response(prev)
        a = _get_alert(c, ctx, alert_id)
        if body.version != a["version"]:
            raise HTTPException(409, f"stale_version: current version is {a['version']}")
        if body.action == "assign" and body.assignee:
            ok_user = c.execute(text("SELECT 1 FROM app_user WHERE email=:e AND tenant_id=:t AND role IN "
                                     "('fleet_operator','support_staff','tenant_admin')"),
                                {"e": body.assignee, "t": ctx.tenant_id}).first()
            if not ok_user:
                raise HTTPException(422, "assignee must be operator/support staff in this tenant")
        allowed, result = dt2_check(a["status"], body.action, body.note, body.assignee)
        if not allowed:
            raise HTTPException(409 if result.startswith("invalid") else 422, result)
        # Alert-module transition: status change + audit row in ONE transaction (BR-3)
        upd = c.execute(text("""
            UPDATE alert SET status=:to, version=version+1, updated_at=now(),
                   ack_at      = CASE WHEN :act='acknowledge' THEN now() ELSE ack_at END,
                   assignee    = CASE WHEN :act='assign' THEN :asg ELSE assignee END,
                   resolved_at = CASE WHEN :to IN ('resolved','false_alarm') THEN now() ELSE resolved_at END
            WHERE alert_id=CAST(:id AS uuid) AND version=:ver
            RETURNING alert_id, status, version, assignee, updated_at"""),
            {"to": result, "act": body.action, "asg": body.assignee, "id": alert_id,
             "ver": body.version}).mappings().first()
        if not upd:
            raise HTTPException(409, "stale_version")
        c.execute(text("INSERT INTO alert_action (alert_id, tenant_id, actor, action, from_status, to_status, note) "
                       "VALUES (CAST(:id AS uuid), :t, :u, :a, :f, :to, :n)"),
                  {"id": alert_id, "t": ctx.tenant_id, "u": ctx.email, "a": body.action,
                   "f": a["status"], "to": result, "n": body.note})
        resp = dict(upd)
        c.execute(text("INSERT INTO idem_key (user_email, idem_key, response) VALUES (:u,:k,CAST(:r AS jsonb))"),
                  {"u": ctx.email, "k": idempotency_key, "r": dumps(resp)})
    for key in [k for k in _cache if k[0] == ctx.tenant_id]:        # invalidate summary cache
        _cache.pop(key, None)
    return json_response(resp)


@router.get("/alerts/{alert_id}/evidence")
def evidence(alert_id: str, kind: str = Query(..., pattern="^(frame|audio|clip)$"),
             ctx: AuthContext = Depends(require("alerts:read"))):
    with ctx.tx() as c:
        a = _get_alert(c, ctx, alert_id)
    key = (a["evidence"] or {}).get(kind)
    if not key:
        raise HTTPException(404, f"no {kind} evidence for this alert")
    if not key.startswith(f"{ctx.tenant_id}/"):
        raise HTTPException(403, "evidence outside tenant prefix")
    return json_response({"url": db.presign_get(key), "expires_in": 300, "kind": kind},)


@router.get("/users")
def assignable_users(ctx: AuthContext = Depends(require("alerts:read"))):
    with ctx.tx() as c:
        data = rows(c.execute(text("SELECT email, name, role FROM app_user WHERE tenant_id=:t AND role IN "
                                   "('fleet_operator','support_staff','tenant_admin') ORDER BY name"),
                              {"t": ctx.tenant_id}))
    return json_response(data)

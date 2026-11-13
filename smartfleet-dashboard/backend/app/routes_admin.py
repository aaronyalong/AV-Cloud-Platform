"""U-01 profile/preferences, tenants, H-01 system health, model registry + deployment, dev login."""
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from . import config, db
from .auth import AuthContext, get_ctx, issue_dev_token, require
from .db import rows, tx
from .rules import PAGES
from .seed import USERS
from .util import dumps, json_response, metrics

router = APIRouter(prefix="/api/v1", tags=["admin"])


# ---------------- U-01
@router.get("/me")
def me(ctx: AuthContext = Depends(get_ctx)):
    with ctx.tx() as c:
        prefs = c.execute(text("SELECT prefs FROM dashboard_pref WHERE user_email=:e"), {"e": ctx.email}).scalar()
        tname = c.execute(text("SELECT name FROM tenant WHERE tenant_id=:t"), {"t": ctx.tenant_id}).scalar()
    return json_response({"email": ctx.email, "name": ctx.name, "role": ctx.role, "tenant_id": ctx.tenant_id,
                          "tenant_name": tname, "home_tenant": ctx.home_tenant, "permissions": sorted(ctx.perms),
                          "pages": [p for p, perm in PAGES.items() if perm in ctx.perms],
                          "vehicle_scope": ctx.vehicle_scope, "preferences": prefs or {"window": "24h"}})


class PrefsIn(BaseModel):
    window: str = Field("24h", pattern="^(1h|24h|7d)$")
    sound: bool = True


@router.put("/me/preferences")
def save_prefs(body: PrefsIn, ctx: AuthContext = Depends(get_ctx)):
    with ctx.tx() as c:
        c.execute(text("""INSERT INTO dashboard_pref (user_email, tenant_id, prefs) VALUES (:e,:t,CAST(:p AS jsonb))
                          ON CONFLICT (user_email) DO UPDATE SET prefs=EXCLUDED.prefs, updated_at=now()"""),
                  {"e": ctx.email, "t": ctx.home_tenant, "p": dumps(body.model_dump())})
    return json_response(body.model_dump())


@router.get("/tenants")
def tenants(ctx: AuthContext = Depends(get_ctx)):
    with ctx.tx() as c:
        data = rows(c.execute(text("SELECT tenant_id, name, status FROM tenant ORDER BY tenant_id")))
    return json_response(data)


# ---------------- H-01 system health (K7)
@router.get("/system/health")
def system_health(ctx: AuthContext = Depends(require("health:read"))):
    t0 = time.perf_counter()
    with tx(None, "platform_admin") as c:
        db_ms = round((time.perf_counter() - t0) * 1000, 1)
        conns = c.execute(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database()")).scalar()
        hb = c.execute(text("SELECT last_run, rows_written FROM worker_heartbeat WHERE name='rollup'")).first()
        usage = rows(c.execute(text("""
            SELECT t.tenant_id, t.name, t.vehicle_quota, count(v.*)::int AS vehicles,
                   count(v.*) FILTER (WHERE v.last_heartbeat > now() - interval '30 seconds')::int AS online,
                   (SELECT count(*) FROM alert a WHERE a.tenant_id=t.tenant_id
                     AND a.created_at > now() - interval '24 hours')::int AS alerts_24h
            FROM tenant t LEFT JOIN vehicle v ON v.tenant_id=t.tenant_id
            GROUP BY t.tenant_id ORDER BY t.tenant_id""")))
    if ctx.role != "platform_admin":                                  # TA sees only own usage
        usage = [u for u in usage if u["tenant_id"] == ctx.tenant_id]
    t1 = time.perf_counter()
    try:
        db.mongo().command("ping")
        docdb = {"ok": True, "ping_ms": round((time.perf_counter() - t1) * 1000, 1)}
    except Exception as e:  # noqa: BLE001
        docdb = {"ok": False, "error": str(e)[:120]}
    lag = (datetime.now(timezone.utc) - hb[0]).total_seconds() if hb and hb[0] else None
    api = metrics.snapshot()
    checks = [
        {"name": "API p95 latency", "value": api["p95_ms"], "unit": "ms",
         "status": "good" if (api["p95_ms"] or 0) < 500 else "warning"},
        {"name": "API 5xx rate", "value": round(api["error_rate"] * 100, 2), "unit": "%",
         "status": "good" if api["error_rate"] < 0.01 else "critical"},
        {"name": "PostgreSQL round trip", "value": db_ms, "unit": "ms", "status": "good" if db_ms < 50 else "warning"},
        {"name": "PostgreSQL connections", "value": conns, "unit": "", "status": "good" if conns < 80 else "warning"},
        {"name": "DocumentDB ping", "value": docdb.get("ping_ms"), "unit": "ms",
         "status": "good" if docdb["ok"] else "critical"},
        {"name": "Rollup worker lag", "value": round(lag) if lag is not None else None, "unit": "s",
         "status": "good" if lag is not None and lag < 120 else "critical"},
    ]
    return json_response({"generated_at": datetime.now(timezone.utc), "env": config.ENV, "api": api,
                          "checks": checks, "tenants": usage})


# ---------------- Model registry and deployment (AI component management)
class ModelIn(BaseModel):
    name: str = Field(pattern="^(audio-hazard|vision-hazard)$")
    version: str = Field(pattern=r"^\d+\.\d+(\.\d+)?$")
    framework: str
    precision: float = Field(ge=0, le=1)
    recall: float = Field(ge=0, le=1)
    threshold: float = Field(0.5, ge=0.05, le=0.99)
    notes: str | None = None


@router.get("/models")
def list_models(ctx: AuthContext = Depends(require("models:read"))):
    with tx(None, "platform_admin") as c:
        models = rows(c.execute(text("SELECT * FROM model_version ORDER BY name, model_id DESC")))
        events = rows(c.execute(text("""SELECT e.at, e.action, e.actor, m.name, m.version FROM model_event e
                                         JOIN model_version m USING (model_id) ORDER BY e.at DESC LIMIT 15""")))
    return json_response({"models": models, "events": events, "can_write": "models:write" in ctx.perms})


@router.post("/models", status_code=201)
def register_model(body: ModelIn, ctx: AuthContext = Depends(require("models:write"))):
    p, r = body.precision, body.recall
    m = {"precision": p, "recall": r, "f1": round(2 * p * r / (p + r), 3) if p + r else 0}
    key = f"models/{body.name}/{body.version}/model.bin"
    with tx(None, "platform_admin") as c:
        if c.execute(text("SELECT 1 FROM model_version WHERE name=:n AND version=:v"),
                     {"n": body.name, "v": body.version}).first():
            raise HTTPException(409, "version already registered")
        mid = c.execute(text("""INSERT INTO model_version (name, version, framework, artifact_key, metrics, threshold,
                                notes, registered_by) VALUES (:n,:v,:f,:k,CAST(:m AS jsonb),:th,:no,:u)
                                RETURNING model_id"""),
                        {"n": body.name, "v": body.version, "f": body.framework, "k": key, "m": dumps(m),
                         "th": body.threshold, "no": body.notes, "u": ctx.email}).scalar()
        c.execute(text("INSERT INTO model_event (model_id, action, actor) VALUES (:i,'registered',:u)"),
                  {"i": mid, "u": ctx.email})
    return json_response({"model_id": mid, "artifact_key": key,
                          "upload_url": db.presign_put(key, "application/octet-stream")}, 201)


@router.post("/models/{model_id}/deploy")
def deploy_model(model_id: int, ctx: AuthContext = Depends(require("models:write"))):
    """Make this version the active one for its model name; the previous active version is retired.
    Detectors (and the simulator) read GET /api/v1/sim/models/active, so the change takes effect live."""
    with tx(None, "platform_admin") as c:
        m = c.execute(text("SELECT name, version, status FROM model_version WHERE model_id=:i FOR UPDATE"),
                      {"i": model_id}).mappings().first()
        if not m:
            raise HTTPException(404, "model not found")
        if m["status"] == "active":
            raise HTTPException(409, "already active")
        c.execute(text("UPDATE model_version SET status='retired' WHERE name=:n AND status='active'"),
                  {"n": m["name"]})
        c.execute(text("UPDATE model_version SET status='active', deployed_by=:u, deployed_at=now() "
                       "WHERE model_id=:i"), {"u": ctx.email, "i": model_id})
        action = "rolled_back_to" if m["status"] == "retired" else "deployed"
        c.execute(text("INSERT INTO model_event (model_id, action, actor) VALUES (:i,:a,:u)"),
                  {"i": model_id, "a": action, "u": ctx.email})
    return json_response({"model_id": model_id, "name": m["name"], "version": m["version"], "status": "active"})


# ---------------- Dev login (AUTH_MODE=local only; disabled in AWS)
class DevLoginIn(BaseModel):
    email: str


@router.get("/dev/users")
def dev_users():
    if config.AUTH_MODE != "local":
        raise HTTPException(404)
    return json_response([{"email": e, "tenant_id": t, "role": r, "name": n} for e, t, r, n in USERS])


@router.post("/dev/login")
def dev_login(body: DevLoginIn):
    if config.AUTH_MODE != "local":
        raise HTTPException(404)
    u = next((x for x in USERS if x[0] == body.email), None)
    if not u:
        raise HTTPException(401, "unknown user")
    return json_response({"id_token": issue_dev_token(u[0], u[3], u[2], u[1])})

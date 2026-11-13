"""Simulator / alert-module stub endpoints (protected by X-Sim-Key).

They stand in for the peer components (edge agent, ingestion, detection, alert module) so the dashboard
can be demonstrated on its own. Real components would call the same contract.
"""
import random
import threading
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from . import db
from .auth import sim_key
from .db import rows, tx
from .rules import ALERT_TYPES
from .util import dumps, json_response
from .worker import rollup_range

router = APIRouter(prefix="/api/v1/sim", tags=["simulator"], dependencies=[Depends(sim_key)])


class Sample(BaseModel):
    vehicle_id: str
    ts: datetime
    x: float
    y: float
    speed_kmh: float
    heading: float = 0


class TelemetryIn(BaseModel):
    samples: list[Sample] = Field(max_length=500)


@router.post("/telemetry")
def telemetry(body: TelemetryIn):
    """Edge-agent heartbeat + telemetry: latest state to PostgreSQL, raw samples to DocumentDB buckets."""
    if not body.samples:
        return {"accepted": 0}
    with tx(None, "platform_admin") as c:
        tenants = dict(c.execute(text("SELECT vehicle_id, tenant_id FROM vehicle WHERE vehicle_id = ANY(:v)"),
                                 {"v": [s.vehicle_id for s in body.samples]}).all())
        for s in body.samples:
            if s.vehicle_id in tenants:
                c.execute(text("UPDATE vehicle SET last_heartbeat=:ts, x=:x, y=:y, speed_kmh=:sp "
                               "WHERE vehicle_id=:v"), {"ts": s.ts, "x": s.x, "y": s.y, "sp": s.speed_kmh,
                                                        "v": s.vehicle_id})
    col = db.mongo().telemetry
    for s in body.samples:
        if s.vehicle_id not in tenants:
            continue
        minute = s.ts.astimezone(timezone.utc).replace(second=0, microsecond=0, tzinfo=None)
        col.update_one({"tenant_id": tenants[s.vehicle_id], "vehicle_id": s.vehicle_id, "bucket_start": minute},
                       {"$push": {"samples": {"ts": s.ts, "x": s.x, "y": s.y, "speed_kmh": s.speed_kmh,
                                              "heading": s.heading}}}, upsert=True)
    return {"accepted": sum(1 for s in body.samples if s.vehicle_id in tenants)}


@router.get("/models/active")
def active_models():
    with tx(None, "platform_admin") as c:
        data = rows(c.execute(text("SELECT name, version, threshold FROM model_version WHERE status='active'")))
    return json_response({m["name"]: m for m in data})


class EvidenceUrlIn(BaseModel):
    vehicle_id: str
    kind: str = Field(pattern="^(frame|audio|clip)$")
    content_type: str


@router.post("/evidence-url")
def evidence_url(body: EvidenceUrlIn):
    with tx(None, "platform_admin") as c:
        v = c.execute(text("SELECT tenant_id, run_id FROM vehicle WHERE vehicle_id=:v"),
                      {"v": body.vehicle_id}).first()
    if not v:
        raise HTTPException(404, "unknown vehicle")
    ext = {"frame": "jpg", "audio": "wav", "clip": "mp4"}[body.kind]
    key = (f"{v[0]}/{v[1]}/{body.vehicle_id}/{body.kind}/"
           f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S%f}.{ext}")         # tenant/run/vehicle/type/file
    return {"key": key, "url": db.presign_put(key, body.content_type)}


class AlertIn(BaseModel):
    vehicle_id: str
    type: str
    confidence: float = Field(ge=0, le=1)
    event_ts: datetime
    x: float | None = None
    y: float | None = None
    evidence: dict = {}


@router.post("/alerts")
def create_alert(body: AlertIn):
    """Alert-module stub: applies the ACTIVE model's confidence threshold, then stores the alert."""
    meta = ALERT_TYPES.get(body.type)
    if not meta:
        raise HTTPException(422, "unknown alert type")
    with tx(None, "platform_admin") as c:
        m = c.execute(text("SELECT version, threshold FROM model_version WHERE name=:n AND status='active'"),
                      {"n": meta["model"]}).first()
        if m and body.confidence < m[1]:
            return json_response({"created": False, "reason": f"below threshold {m[1]} of "
                                  f"{meta['model']} v{m[0]}"}, 202)
        tid = c.execute(text("SELECT tenant_id FROM vehicle WHERE vehicle_id=:v"), {"v": body.vehicle_id}).scalar()
        if not tid:
            raise HTTPException(404, "unknown vehicle")
        aid = c.execute(text("""INSERT INTO alert (tenant_id, vehicle_id, type, severity, confidence, model_name,
                                model_version, x, y, evidence, event_ts)
                                VALUES (:t,:v,:ty,:s,:c,:mn,:mv,:x,:y,CAST(:e AS jsonb),:ts) RETURNING alert_id"""),
                        {"t": tid, "v": body.vehicle_id, "ty": body.type, "s": meta["severity"],
                         "c": body.confidence, "mn": meta["model"], "mv": m[0] if m else None,
                         "x": body.x, "y": body.y, "e": dumps(body.evidence), "ts": body.event_ts}).scalar()
    return json_response({"created": True, "alert_id": aid, "severity": meta["severity"],
                          "model_version": m[0] if m else None}, 201)


class BackfillIn(BaseModel):
    days: int = Field(7, ge=1, le=30)
    per_day: int = Field(120, ge=10, le=1000)


def _backfill(days: int, per_day: int):
    """Generate historical, already-closed alerts so P8 has realistic history, then roll them up."""
    now = datetime.now(timezone.utc)
    types = list(ALERT_TYPES)
    weights = [0.08, 0.2, 0.17, 0.25, 0.2, 0.1]
    far = {"collision": 0.04, "pedestrian": 0.09, "emergency_siren": 0.15, "stopped_vehicle": 0.07,
           "red_light": 0.06, "horn": 0.12}
    with tx(None, "platform_admin") as c:
        veh = rows(c.execute(text("SELECT vehicle_id, tenant_id FROM vehicle")))
        models = {r[0]: r[1] for r in c.execute(text("SELECT name, version FROM model_version WHERE status='active'"))}
        batch = []
        for d in range(days):
            for _ in range(per_day):
                # diurnal pattern: peaks around 8 am and 5 pm Pacific (16:00 and 01:00 UTC)
                hour = random.choice([15, 16, 16, 17, 0, 1, 1, 2] + list(range(24)))
                ts = (now - timedelta(days=d + 1)).replace(hour=hour, minute=random.randint(0, 59),
                                                           second=random.randint(0, 59))
                if ts >= now - timedelta(minutes=10):
                    continue
                t = random.choices(types, weights)[0]
                v = random.choice(veh)
                fa = random.random() < far[t]
                ack = ts + timedelta(seconds=random.uniform(15, 140))
                batch.append({"t": v["tenant_id"], "v": v["vehicle_id"], "ty": t,
                              "s": ALERT_TYPES[t]["severity"], "st": "false_alarm" if fa else "resolved",
                              "c": round(random.uniform(0.6, 0.98), 2), "mn": ALERT_TYPES[t]["model"],
                              "mv": models.get(ALERT_TYPES[t]["model"]), "ts": ts,
                              "fd": ts + timedelta(seconds=random.uniform(0.9, 2.4)), "ack": ack,
                              "res": ack + timedelta(seconds=random.uniform(60, 900))})
        c.execute(text("""INSERT INTO alert (tenant_id, vehicle_id, type, severity, status, confidence, model_name,
                          model_version, event_ts, created_at, updated_at, first_delivered_at, ack_at, resolved_at)
                          VALUES (:t,:v,:ty,:s,:st,:c,:mn,:mv,:ts,:ts,:res,:fd,:ack,:res)"""), batch)
    rollup_range(now - timedelta(days=days + 1), now + timedelta(minutes=1))


@router.post("/backfill", status_code=202)
def backfill(body: BackfillIn):
    threading.Thread(target=_backfill, args=(body.days, body.per_day), daemon=True).start()
    return {"started": True, "days": body.days, "per_day": body.per_day}

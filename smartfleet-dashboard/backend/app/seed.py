"""Demo seed data: tenants, users (mirrors the Cognito users), vehicles, and model versions."""
from sqlalchemy import text

from .db import tx

TENANTS = [("t01", "Bay Area Fleet", 50), ("t02", "SoCal Logistics", 20)]

# email, tenant, role, display name  (scripts/create-users.sh creates the same users in Cognito)
USERS = [
    ("admin@example.com",     "t01", "platform_admin", "Priya Patel"),
    ("tadmin@example.com",    "t01", "tenant_admin",   "Tom Nguyen"),
    ("operator@example.com",  "t01", "fleet_operator", "Jordan Rivera"),
    ("support@example.com",   "t01", "support_staff",  "Mei Chen"),
    ("owner@example.com",     "t01", "vehicle_owner",  "Owen Brooks"),
    ("ai@example.com",        "t01", "ai_engineer",    "Ana Lopez"),
    ("operator2@example.com", "t02", "fleet_operator", "Sam Ortiz"),
]

VEHICLES = ([(f"v{i:02d}", "t01", f"Unit {i:02d}", "owner@example.com" if i == 7 else None,
              "r12" if i <= 6 else "r13") for i in range(1, 13)]
            + [(f"w{i:02d}", "t02", f"SoCal {i:02d}", None, "r20") for i in range(1, 7)])

MODELS = [  # name, version, framework, metrics, threshold, status
    ("vision-hazard", "1.0", "YOLOv8n", {"precision": 0.84, "recall": 0.80, "f1": 0.82}, 0.50, "retired"),
    ("vision-hazard", "1.1", "YOLOv8s", {"precision": 0.91, "recall": 0.88, "f1": 0.89}, 0.55, "active"),
    ("audio-hazard",  "1.2", "CNN-MFCC", {"precision": 0.82, "recall": 0.86, "f1": 0.84}, 0.60, "active"),
    ("audio-hazard",  "1.3", "CNN-MFCC + aug", {"precision": 0.90, "recall": 0.87, "f1": 0.88}, 0.70, "registered"),
]


def seed():
    with tx(None, "platform_admin") as c:
        for tid, name, quota in TENANTS:
            c.execute(text("INSERT INTO tenant (tenant_id, name, vehicle_quota) VALUES (:t,:n,:q) "
                           "ON CONFLICT DO NOTHING"), {"t": tid, "n": name, "q": quota})
        for email, tid, role, name in USERS:
            c.execute(text("INSERT INTO app_user VALUES (:e,:t,:r,:n) ON CONFLICT DO NOTHING"),
                      {"e": email, "t": tid, "r": role, "n": name})
        for vid, tid, name, owner, run in VEHICLES:
            c.execute(text("INSERT INTO vehicle (vehicle_id, tenant_id, name, owner_email, run_id) "
                           "VALUES (:v,:t,:n,:o,:r) ON CONFLICT DO NOTHING"),
                      {"v": vid, "t": tid, "n": name, "o": owner, "r": run})
        for name, ver, fw, metrics, thr, status in MODELS:
            c.execute(text("""INSERT INTO model_version (name, version, framework, metrics, threshold, status,
                              artifact_key, registered_by, deployed_at, deployed_by)
                              VALUES (:n,:v,:f,CAST(:m AS jsonb),:th,:s,:k,'seed',
                                      CASE WHEN :s='active' THEN now() END,
                                      CASE WHEN :s='active' THEN 'seed' END)
                              ON CONFLICT DO NOTHING"""),
                      {"n": name, "v": ver, "f": fw, "m": __import__("json").dumps(metrics), "th": thr,
                       "s": status, "k": f"models/{name}/{ver}/model.bin"})

"""Component unit demo: exercises every dashboard API function and business rule, prints PASS/FAIL.

Local:  python api_unit_demo.py
AWS:    source ../scripts/sim-env.sh && python api_unit_demo.py --aws
(AWS mode signs in each demo user through Cognito with the AWS CLI: admin-initiate-auth.)
"""
import argparse
import json
import os
import subprocess
import time
import uuid

import requests

import carla_sim

API = os.getenv("SIM_API", "http://localhost:8000")
B = f"{API}/api/v1"
PASSWORD = os.getenv("DEMO_PASSWORD", "SmartFleet2026")
results = []


def cognito_token(email):
    out = subprocess.check_output(["aws", "cloudformation", "describe-stacks", "--stack-name", "SmartFleetDashboard",
                                   "--query", "Stacks[0].Outputs", "--output", "json"])
    o = {x["OutputKey"]: x["OutputValue"] for x in json.loads(out)}
    auth = subprocess.check_output([
        "aws", "cognito-idp", "admin-initiate-auth", "--user-pool-id", o["UserPoolId"],
        "--client-id", o["UserPoolClientId"], "--auth-flow", "ADMIN_USER_PASSWORD_AUTH",
        "--auth-parameters", f"USERNAME={email},PASSWORD={PASSWORD}", "--output", "json"])
    return json.loads(auth)["AuthenticationResult"]["IdToken"]


def local_token(email):
    return requests.post(f"{B}/dev/login", json={"email": email}, timeout=10).json()["id_token"]


def check(name, resp, expected, show=None):
    ok = resp.status_code == expected
    results.append(ok)
    extra = f"  {show(resp.json())}" if (show and ok and resp.content) else ""
    if not ok:
        extra = f"  got {resp.status_code}: {resp.text[:120]}"
    print(f"{'PASS' if ok else 'FAIL'}  {name:<52} expect {expected}{extra}")
    return resp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aws", action="store_true")
    a = ap.parse_args()
    tok = cognito_token if a.aws else local_token
    H = {u: {"Authorization": f"Bearer {tok(f'{u}@example.com')}"} for u in
         ["operator", "operator2", "owner", "ai", "tadmin", "admin", "support"]}
    idem = lambda: {"Idempotency-Key": str(uuid.uuid4())}  # noqa: E731

    print("\n== Authentication and access (DT-1, BR-1, BR-2)")
    check("no token -> 401", requests.get(f"{B}/dashboard/summary"), 401)
    check("profile / permitted pages (operator)", requests.get(f"{B}/me", headers=H["operator"]), 200,
          lambda j: j["pages"])
    check("owner cannot read fleet statistics", requests.get(f"{B}/stats/alerts", headers=H["owner"]), 403)
    check("operator cannot open system health", requests.get(f"{B}/system/health", headers=H["operator"]), 403)
    check("operator cannot switch tenant", requests.get(f"{B}/dashboard/summary",
                                                        headers={**H["operator"], "X-Tenant-Id": "t02"}), 403)
    check("platform admin can switch tenant (audited)", requests.get(f"{B}/dashboard/map",
                                                                     headers={**H["admin"], "X-Tenant-Id": "t02"}), 200)

    print("\n== Simulator -> alert module stub -> new alert with evidence")
    j = carla_sim.send_hazard("v07", "collision", (0, 0), 0.93)
    aid = str(j["alert_id"])

    print("\n== P1 dashboard APIs (D-01..D-03)")
    r = check("D-01 summary (KPI tiles + feed)", requests.get(f"{B}/dashboard/summary", headers=H["operator"]), 200,
              lambda j: {k["key"]: k["value"] for k in j["kpis"]})
    check("D-01 summary with If-None-Match -> 304", requests.get(
        f"{B}/dashboard/summary", headers={**H["operator"], "If-None-Match": r.headers.get("ETag", "")}), 304)
    check("D-02 live delta since cursor", requests.get(f"{B}/dashboard/live", params={"since": r.json()["cursor"]},
                                                       headers=H["operator"]), 200, lambda j: f"{len(j['alerts'])} changed")
    check("D-03 map (connectivity per vehicle, DT-3)", requests.get(f"{B}/dashboard/map", headers=H["operator"]), 200,
          lambda j: sorted({v["status"] for v in j["vehicles"]}))
    check("owner sees only own vehicle (BR-2)", requests.get(f"{B}/dashboard/map", headers=H["owner"]), 200,
          lambda j: [v["vehicle_id"] for v in j["vehicles"]])

    print("\n== P3 alert workflow (A-01..A-04, DT-2, BR-3, BR-4)")
    check("A-01 list open alerts", requests.get(f"{B}/alerts", params={"status": "open"}, headers=H["operator"]), 200,
          lambda j: f"{j['total']} open")
    check("A-02 detail", requests.get(f"{B}/alerts/{aid}", headers=H["operator"]), 200, lambda j: j["status"])
    ev = check("A-04 evidence presigned URL (300 s)", requests.get(f"{B}/alerts/{aid}/evidence", params={"kind": "frame"},
                                                                   headers=H["operator"]), 200)
    print(f"      evidence download: {requests.get(ev.json()['url'], timeout=20).status_code} "
          f"({len(requests.get(ev.json()['url'], timeout=20).content)} bytes)")
    check("other tenant cannot see alert -> 404", requests.get(f"{B}/alerts/{aid}", headers=H["operator2"]), 404)
    check("owner cannot act -> 403", requests.post(f"{B}/alerts/{aid}/actions", json={"action": "acknowledge",
          "version": 1}, headers={**H["owner"], **idem()}), 403)
    k = idem()
    check("acknowledge (v1 -> v2)", requests.post(f"{B}/alerts/{aid}/actions", json={"action": "acknowledge",
          "version": 1}, headers={**H["operator"], **k}), 200, lambda j: j["status"])
    check("same Idempotency-Key replays result", requests.post(f"{B}/alerts/{aid}/actions", json={"action": "acknowledge",
          "version": 1}, headers={**H["operator"], **k}), 200)
    check("stale version -> 409", requests.post(f"{B}/alerts/{aid}/actions", json={"action": "resolve", "version": 1,
          "note": "done here"}, headers={**H["operator"], **idem()}), 409)
    check("resolve without note -> 422", requests.post(f"{B}/alerts/{aid}/actions", json={"action": "resolve",
          "version": 2}, headers={**H["operator"], **idem()}), 422)
    check("assign to support staff", requests.post(f"{B}/alerts/{aid}/actions", json={"action": "assign", "version": 2,
          "assignee": "support@example.com"}, headers={**H["operator"], **idem()}), 200, lambda j: j["status"])
    check("support records dispatch", requests.post(f"{B}/alerts/{aid}/actions", json={"action": "dispatch",
          "version": 3, "note": "EMS unit 4 en route"}, headers={**H["support"], **idem()}), 200, lambda j: j["status"])
    check("resolve with note", requests.post(f"{B}/alerts/{aid}/actions", json={"action": "resolve", "version": 4,
          "note": "Tow requested, no injuries"}, headers={**H["operator"], **idem()}), 200, lambda j: j["status"])
    check("acknowledge a resolved alert -> 409", requests.post(f"{B}/alerts/{aid}/actions", json={
          "action": "acknowledge", "version": 5}, headers={**H["operator"], **idem()}), 409)
    check("audit history", requests.get(f"{B}/alerts/{aid}", headers=H["operator"]), 200,
          lambda j: [h["action"] for h in j["history"]])

    print("\n== P8 statistics (S-01..S-04)")
    check("S-01 alerts over time by type", requests.get(f"{B}/stats/alerts", headers=H["operator"]), 200,
          lambda j: f"bucket={j['bucket']} total={j['total']}")
    check("heatmap hour x weekday", requests.get(f"{B}/stats/heatmap", headers=H["operator"]), 200,
          lambda j: f"max cell={j['max']}")
    check("S-02 latency p50/p95, MTTA, MTTR", requests.get(f"{B}/stats/latency", headers=H["operator"]), 200,
          lambda j: j["overall"])
    check("S-03 detection quality by model", requests.get(f"{B}/stats/detection-quality", headers=H["operator"]), 200,
          lambda j: [(m["model_name"], m["model_version"], m["alerts"]) for m in j["by_model"]])
    e = check("S-04 CSV export job", requests.post(f"{B}/stats/exports", json={"days": 7}, headers=H["operator"]), 202)
    time.sleep(3)
    check("S-04 export status + download link", requests.get(f"{B}/stats/exports/{e.json()['export_id']}",
                                                             headers=H["operator"]), 200, lambda j: f"{j['status']} rows={j['rows']}")

    print("\n== P9 system health (H-01)")
    check("health checks (tenant admin)", requests.get(f"{B}/system/health", headers=H["tadmin"]), 200,
          lambda j: {c["name"]: c["status"] for c in j["checks"]})

    print("\n== Model management and deployment (AI component)")
    check("operator cannot open models -> 403", requests.get(f"{B}/models", headers=H["operator"]), 403)
    ver = f"2.0.{int(time.time()) % 100000}"
    r = check(f"register audio-hazard v{ver}", requests.post(f"{B}/models", json={
        "name": "audio-hazard", "version": ver, "framework": "CNN-MFCC", "precision": 0.9, "recall": 0.88,
        "threshold": 0.8}, headers=H["ai"]), 201)
    up = requests.put(r.json()["upload_url"], data=b"demo-weights", headers={"Content-Type": "application/octet-stream"})
    print(f"      artifact upload to S3: {up.status_code}")
    models = requests.get(f"{B}/models", headers=H["ai"]).json()["models"]
    prev = next(m for m in models if m["name"] == "audio-hazard" and m["status"] == "active")
    new = next(m for m in models if m["name"] == "audio-hazard" and m["version"] == ver)
    check("platform admin cannot deploy -> 403", requests.post(f"{B}/models/{new['model_id']}/deploy",
                                                              headers=H["admin"]), 403)
    check(f"deploy v{ver} (threshold 0.80)", requests.post(f"{B}/models/{new['model_id']}/deploy", headers=H["ai"]), 200)
    j = carla_sim.send_hazard("v05", "emergency_siren", (0, 0), 0.75)
    results.append(not j.get("created"))
    print(f"{'PASS' if not j.get('created') else 'FAIL'}  siren at 0.75 suppressed by new threshold")
    check(f"roll back to v{prev['version']}", requests.post(f"{B}/models/{prev['model_id']}/deploy", headers=H["ai"]), 200)

    print(f"\n{sum(results)}/{len(results)} checks passed")


if __name__ == "__main__":
    main()

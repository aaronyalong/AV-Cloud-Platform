"""CARLA-style fleet simulator for the SmartFleet dashboard demo.

Continuous mode (terminal 1):   python carla_sim.py
Trigger one hazard (terminal 2): python carla_sim.py --scenario collision --vehicle v07
Backfill 7 days of history:      python carla_sim.py --backfill 7

Vehicles drive loops on a Town03-like road grid and send 1 Hz telemetry (heartbeats). Hazards are sent
to the alert-module stub with evidence (a camera frame and/or audio clip) uploaded to S3 via presigned URLs.
Optional: --carla HOST reads real vehicle positions from a running CARLA server instead of the loops.
"""
import argparse
import io
import math
import os
import random
import struct
import sys
import time
import wave
from datetime import datetime, timezone

import requests
from PIL import Image, ImageDraw

API = os.getenv("SIM_API", "http://localhost:8000")
KEY = os.getenv("SIM_API_KEY", "sim-local-key")
ROADS_X = [-100, 0, 100, 200]          # must match the road grid drawn by the frontend map
ROADS_Y = [-150, -50, 50, 150]
FLEET = [f"v{i:02d}" for i in range(1, 13)] + [f"w{i:02d}" for i in range(1, 7)]
TYPES = {  # type: (weight, needs_audio)
    "collision": (0.08, False), "pedestrian": (0.22, False), "emergency_siren": (0.17, True),
    "stopped_vehicle": (0.25, False), "red_light": (0.18, False), "horn": (0.10, True),
}


def H():
    return {"X-Sim-Key": KEY}


class Car:
    """Drives clockwise around one block of the grid at a constant speed."""

    def __init__(self, vid: str, idx: int):
        self.vid = vid
        cx, cy = idx % 3, (idx // 3) % 3
        self.x0, self.x1 = ROADS_X[cx], ROADS_X[cx + 1]
        self.y0, self.y1 = ROADS_Y[cy], ROADS_Y[cy + 1]
        self.per = 2 * ((self.x1 - self.x0) + (self.y1 - self.y0))
        self.pos = random.uniform(0, self.per)
        self.speed = random.uniform(25, 45)          # km/h

    def step(self, dt: float):
        self.pos = (self.pos + self.speed / 3.6 * dt) % self.per
        w, h, p = self.x1 - self.x0, self.y1 - self.y0, self.pos
        if p < w:
            return self.x0 + p, self.y0, 0
        if p < w + h:
            return self.x1, self.y0 + (p - w), 90
        if p < 2 * w + h:
            return self.x1 - (p - w - h), self.y1, 180
        return self.x0, self.y1 - (p - 2 * w - h), 270


def make_frame(vid: str, hazard: str, conf: float) -> bytes:
    """Synthetic dash-cam frame with a detection box (stands in for a CARLA RGB camera image)."""
    img = Image.new("RGB", (640, 360), (135, 180, 225))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 190, 640, 360], fill=(70, 72, 76))
    d.polygon([(250, 360), (310, 190), (330, 190), (390, 360)], fill=(90, 92, 96))
    for y in range(200, 360, 40):
        d.rectangle([316, y, 324, y + 20], fill=(240, 240, 240))
    d.rectangle([0, 150, 640, 190], fill=(95, 140, 90))
    box = {"collision": (360, 210, 520, 300), "pedestrian": (420, 200, 460, 300),
           "stopped_vehicle": (280, 215, 380, 275), "red_light": (520, 60, 560, 150)}.get(hazard, (300, 220, 400, 290))
    if hazard == "pedestrian":
        d.ellipse([430, 200, 450, 220], fill=(230, 200, 170)); d.rectangle([428, 220, 452, 300], fill=(40, 60, 140))
    elif hazard == "red_light":
        d.rectangle([528, 60, 552, 150], fill=(30, 30, 30)); d.ellipse([532, 66, 548, 82], fill=(230, 30, 30))
    else:
        d.rectangle([box[0] + 10, box[1] + 20, box[2] - 10, box[3] - 10], fill=(200, 200, 210))
    d.rectangle(box, outline=(230, 30, 30), width=4)
    d.rectangle([box[0], box[1] - 22, box[0] + 190, box[1]], fill=(230, 30, 30))
    d.text((box[0] + 6, box[1] - 18), f"{hazard} {conf:.2f}", fill=(255, 255, 255))
    d.rectangle([0, 0, 640, 24], fill=(0, 0, 0))
    d.text((8, 6), f"{vid}  CAM_FRONT  {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}Z  CARLA Town03",
           fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def make_audio(hazard: str, seconds: float = 3.0, rate: int = 16000) -> bytes:
    """Siren (sweeping tone) or horn (two steady tones) as 16-bit mono WAV."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        frames = bytearray()
        for n in range(int(seconds * rate)):
            t = n / rate
            if hazard == "emergency_siren":
                f = 700 + 400 * math.sin(2 * math.pi * 0.5 * t)
                v = math.sin(2 * math.pi * f * t)
            else:
                v = 0.6 * math.sin(2 * math.pi * 420 * t) + 0.4 * math.sin(2 * math.pi * 520 * t)
            frames += struct.pack("<h", int(12000 * v))
        w.writeframes(bytes(frames))
    return buf.getvalue()


def upload(vid: str, kind: str, data: bytes, ctype: str) -> str:
    r = requests.post(f"{API}/api/v1/sim/evidence-url", headers=H(), timeout=10,
                      json={"vehicle_id": vid, "kind": kind, "content_type": ctype})
    r.raise_for_status()
    j = r.json()
    requests.put(j["url"], data=data, headers={"Content-Type": ctype}, timeout=20).raise_for_status()
    return j["key"]


def send_hazard(vid: str, hazard: str, pos=(0.0, 0.0), conf: float | None = None):
    conf = conf if conf is not None else round(random.uniform(0.5, 0.98), 2)
    event_ts = datetime.now(timezone.utc).isoformat()
    evidence = {"frame": upload(vid, "frame", make_frame(vid, hazard, conf), "image/jpeg")}
    if TYPES[hazard][1]:
        evidence["audio"] = upload(vid, "audio", make_audio(hazard), "audio/wav")
    r = requests.post(f"{API}/api/v1/sim/alerts", headers=H(), timeout=10,
                      json={"vehicle_id": vid, "type": hazard, "confidence": conf, "event_ts": event_ts,
                            "x": pos[0], "y": pos[1], "evidence": evidence})
    j = r.json()
    if j.get("created"):
        print(f"[ALERT] {hazard:<16} {vid}  conf={conf:.2f}  severity={j['severity']}  model v{j['model_version']}")
    else:
        print(f"[skip ] {hazard:<16} {vid}  conf={conf:.2f}  {j.get('reason')}")
    return j


def carla_positions(host: str):
    """Optional: real positions from CARLA (needs the carla Python package and a running server)."""
    import carla  # noqa: PLC0415
    world = carla.Client(host, 2000).get_world()
    actors = world.get_actors().filter("vehicle.*")
    out = {}
    for vid, a in zip(FLEET, actors):
        loc, vel = a.get_transform().location, a.get_velocity()
        out[vid] = (loc.x, loc.y, 3.6 * math.sqrt(vel.x ** 2 + vel.y ** 2 + vel.z ** 2),
                    a.get_transform().rotation.yaw)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", choices=list(TYPES), help="send ONE hazard and exit")
    ap.add_argument("--vehicle", default="v07")
    ap.add_argument("--confidence", type=float, default=None)
    ap.add_argument("--hazard-every", type=float, default=25, help="mean seconds between random hazards")
    ap.add_argument("--drop", nargs="*", default=["v11"], help="vehicles that stop sending heartbeats")
    ap.add_argument("--backfill", type=int, metavar="DAYS", help="generate DAYS of closed history and exit")
    ap.add_argument("--carla", metavar="HOST", help="read positions from a CARLA server")
    a = ap.parse_args()

    if a.backfill:
        r = requests.post(f"{API}/api/v1/sim/backfill", headers=H(), json={"days": a.backfill}, timeout=10)
        print(r.status_code, r.json(), "- history is generated in the background (about 10-30 s)")
        return
    if a.scenario:
        send_hazard(a.vehicle, a.scenario, conf=a.confidence or 0.93)
        return

    cars = {vid: Car(vid, i) for i, vid in enumerate(FLEET)}
    drop_at = time.time() + 45                   # dropped vehicles go quiet after 45 s (Stale, then Offline)
    next_hazard = time.time() + random.expovariate(1 / a.hazard_every)
    print(f"Simulating {len(cars)} vehicles -> {API}  (Ctrl+C to stop)")
    while True:
        t0 = time.time()
        live = carla_positions(a.carla) if a.carla else None
        samples = []
        for vid, car in cars.items():
            if vid in a.drop and time.time() > drop_at:
                continue
            x, y, hd = car.step(1.0)
            sp = car.speed + random.uniform(-3, 3)
            if live and vid in live:
                x, y, sp, hd = live[vid]
            samples.append({"vehicle_id": vid, "ts": datetime.now(timezone.utc).isoformat(), "x": round(x, 1),
                            "y": round(y, 1), "speed_kmh": round(sp, 1), "heading": hd})
        try:
            requests.post(f"{API}/api/v1/sim/telemetry", headers=H(), json={"samples": samples}, timeout=5)
            if time.time() >= next_hazard:
                s = random.choice([s for s in samples if s["vehicle_id"] not in a.drop])
                hz = random.choices(list(TYPES), [w for w, _ in TYPES.values()])[0]
                send_hazard(s["vehicle_id"], hz, (s["x"], s["y"]))
                next_hazard = time.time() + random.expovariate(1 / a.hazard_every)
        except requests.RequestException as e:
            print("API unreachable:", e, file=sys.stderr)
        time.sleep(max(0, 1 - (time.time() - t0)))


if __name__ == "__main__":
    main()

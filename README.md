# AV Cloud Platform

A multi-tenant cloud platform for simulated autonomous vehicles. CMPE 281 (Cloud Technologies) team project, San José State University, Fall 2026.

**Team:** Pradeep Vuyyuru (cloud lead) · Jose Quinteros (edge) · Aaron Yalong (back end) · Ralph Tchaya (AI and UI)

## What it does

Vehicles driven in the CARLA simulator stream telemetry and sensor media to AWS. Workers detect hazards using audio and vision models plus collision rules, and raise alerts. Fleet staff review the evidence and resolve the alerts on a web dashboard. Each fleet (tenant) sees only its own data.

**Core demo:** a scenario triggers an event → an alert appears on the dashboard → a staff member reviews the evidence and resolves it.

## Architecture

```
CARLA (SJSU HPC) ──► Edge agent ──MQTT──► IoT Core ──► SQS ──► Workers ──► PostgreSQL
                         │                                      │          DocumentDB
                         └──── media upload (presigned URL) ──► S3 ◄────────┘
                                                                 ▲
React dashboard ──HTTPS──► ALB ──► FastAPI (ECS Fargate) ────────┘
                (Cognito sign-in)
```

- **Edge agent:** captures camera, LiDAR, collision, and telemetry data next to CARLA. It buffers everything in SQLite, so nothing is lost while the uplink is down.
- **Workers:** validate and deduplicate messages, store telemetry, run audio and vision detection, and create alerts.
- **API:** serves the dashboard and enforces tenant and role access.
- **Storage:**
  - PostgreSQL holds tenants, users, vehicles, runs, model versions, and alerts, with row-level security per tenant.
  - DocumentDB (MongoDB locally) holds telemetry, detections, and run evidence.
  - S3 holds media.

## Repo layout

| Path | Contents | Owner |
|---|---|---|
| `edge/` | CARLA edge agent | Jose |
| `api/` | FastAPI service | Aaron |
| `workers/` | Queue consumers: ingestion, detection, alerts | Aaron / Ralph |
| `web/` | React dashboard | Ralph |
| `infra/` | Terraform | Pradeep |

These folders are created as each part is started.

## Tech stack

| Area | Choice |
|---|---|
| API and workers | Python, FastAPI, ECS Fargate |
| Databases | PostgreSQL (RDS), DocumentDB (MongoDB locally) |
| Media | S3 with short-lived presigned URLs |
| Messaging | AWS IoT Core (MQTT), SQS |
| Auth | Cognito |
| Frontend | React |
| Infrastructure | Terraform |

## Status

Early setup. We're building locally first, with Postgres and MongoDB in Docker Compose. AWS comes after permissions and budget are confirmed. Setup instructions will go here once the local environment is in place.

## Design documents

The system design (Deliverable 1) and the Deliverable 2 requirements aren't stored in this repo. Ask the team for the latest copy.

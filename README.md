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
| `avdata/` | Shared data-access package, database migrations, and Mongo indexes | Aaron |
| `workers/` | Queue consumers: ingestion, detection, alerts | Aaron / Ralph |
| `web/` | React dashboard | Ralph |
| `infra/` | Terraform | Pradeep |
| `db/init/` | One-time Postgres setup for local Docker | Aaron |

Folders that don't exist yet are created as each part is started.

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

Everything runs locally for now. AWS comes after permissions and budget are confirmed.

Done so far:
- **Postgres schema:** tenants, vehicles, IoT devices and vehicle commands, with row-level security per tenant.
- **MongoDB indexes:** for telemetry and events.
- **API:** a FastAPI app with health checks.

## Local development

You need [Docker](https://docs.docker.com/engine/install/) with Compose, and [uv](https://docs.astral.sh/uv/getting-started/installation/). uv installs the right Python version (3.13) by itself.

From the repo root:

```bash
cp .env.example .env                                  # local-only passwords and URLs
docker compose up -d --wait                           # Postgres 16 and MongoDB 7
uv sync                                               # Python dependencies
uv run --env-file .env alembic upgrade head           # Postgres tables and security policies
uv run --env-file .env python -m avdata.mongo         # MongoDB indexes
uv run --env-file .env pytest                         # all tests should pass
uv run --env-file .env uvicorn app.main:app --reload  # API on http://localhost:8000
```

Then check http://localhost:8000/health/ready. It should say `ok` for both databases. Interactive API docs are at http://localhost:8000/docs.

Every Python command takes `--env-file .env`, because that's where the database URLs come from.

### Things to know

- **The app can't bypass tenant isolation.** The API connects to Postgres as `av_app`. Row-level security means that role only sees rows for the tenant set on the current transaction, and it sees nothing if no tenant is set. Set the tenant with `avdata.postgres.tenant_transaction`. Never connect the app as the `postgres` superuser, because superusers skip row-level security.
- **Schema changes are migrations.** Add a file under `avdata/migrations/versions/` and run `alembic upgrade head`. Every new table with a `tenant_id` column needs row-level security; a test fails if it's missing.
- **Tests use separate databases.** They use `av_test` in both Postgres and Mongo, and rebuild it on every run, so they never touch your dev data.
- **Resetting:** `docker compose down -v` deletes all local data. You need it after changing passwords in `.env`, because the database roles are only created when the Postgres volume is first set up. Then run the setup commands again.
- **Port conflicts:** if 5432 or 27017 is already taken, change `POSTGRES_PORT` or `MONGO_PORT` in `.env`, and update the matching URLs in the same file.

## Design documents

The system design (Deliverable 1) and the Deliverable 2 requirements aren't stored in this repo. Ask the team for the latest copy.

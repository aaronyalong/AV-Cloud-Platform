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
| `scripts/` | `demo.sh`, which starts the whole local demo | Aaron |

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

The Data and IoT Management component works end to end on one machine:
- **Simulator:** 4 simulated vehicles (`t-acme`: veh-07, veh-09, veh-12; `t-beta`: veh-01) send telemetry at 2 Hz and a heartbeat every 10 s.
- **Ingest worker:** stores each message exactly once. Telemetry goes to MongoDB in per-minute buckets; heartbeats update Postgres.
- **API:** `GET /api/v1/vehicles` returns each vehicle's connectivity status, and `GET /api/v1/telemetry` returns its telemetry. Both are scoped to the caller's tenant by Postgres row-level security and a tenant-scoped MongoDB wrapper.
- **Dashboard:** a page at `/` with a tenant picker, a vehicle table with live status, and a speed chart.

## Running the demo

Prerequisites are the same as in [Local development](#local-development).

```bash
make demo                                   # or: ./scripts/demo.sh
```

This starts the databases, applies migrations and indexes, seeds the demo tenants, then runs the API and the simulator with the ingest worker. Open **http://localhost:8000**. Ctrl-C stops the simulator and API; the vehicles send a disconnect and show **Offline**.

Things to show:

| Show | How |
|---|---|
| Live status and speed chart | Click a vehicle row. The page refreshes every 2 seconds. |
| Tenant isolation | Switch the tenant picker to `t-beta`: only veh-01 is visible. `curl -H "X-Tenant-Id: t-beta" "localhost:8000/api/v1/telemetry?vehicle_id=veh-07"` returns 404. |
| Lost connection | `make demo ARGS="--stop veh-09"`. veh-09 stops sending after 20 s, shows **Stale** 30 s later, and **Offline** after 90 s. |
| Duplicates stored once | `make demo ARGS="--replay-duplicates 20"`. After 15 s the simulator resends its last 20 messages; the worker's log shows `'duplicate': 20` and nothing extra is stored. |
| Tests | `make test` |

You can link straight to a vehicle with `http://localhost:8000/?tenant=t-acme&vehicle=veh-07`.

Connectivity rules: **Online** ≤30 s since the last heartbeat, **Stale** 30–90 s, **Offline** >90 s or after a disconnect, **Never connected** before the first heartbeat, **Suspended** if the tenant is suspended.

## Planned (not built yet)

Skipped for the local demo, on purpose:
- **Real auth:** the tenant comes from an `X-Tenant-Id` header. Planned: Cognito, with the tenant and role read from the verified token.
- **AWS IoT Core and SQS:** the simulator and worker share an in-process queue behind a `MessageQueue` interface. Planned: MQTT to IoT Core, with an IoT rule that forwards to SQS and a dead-letter queue for failed messages.
- **S3 media and presigned URLs, events, and vehicle commands.**
- **Deployment:** ECS Fargate, RDS, and DocumentDB, via Terraform.

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

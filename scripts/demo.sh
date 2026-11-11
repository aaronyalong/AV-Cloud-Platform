#!/usr/bin/env bash
# Start the whole DIM demo: databases, schema, indexes, seed data, API, and
# the simulator plus ingest worker. Ctrl-C stops everything except Docker.
# Extra arguments go to the simulator, e.g. --stop veh-09 or --replay-duplicates 20
set -euo pipefail
cd "$(dirname "$0")/.."

API_PORT="${API_PORT:-8000}"
[ -f .env ] || cp .env.example .env

echo "==> Starting Postgres and MongoDB"
docker compose up -d --wait
echo "==> Installing dependencies"
uv sync --quiet
echo "==> Migrating Postgres, creating Mongo indexes, seeding demo tenants"
uv run --env-file .env alembic upgrade head
uv run --env-file .env python -m avdata.mongo
uv run --env-file .env python -m workers.seed

echo "==> Starting the API on port $API_PORT"
uv run --env-file .env uvicorn app.main:app --port "$API_PORT" --log-level warning &
API_PID=$!
# Stop the API on exit, keeping the simulator's exit status. "|| true" because
# set -e also applies here, and wait reports the API's SIGTERM as status 143.
trap 'status=$?; kill "$API_PID" 2>/dev/null; wait "$API_PID" 2>/dev/null || true; exit $status' EXIT

echo
echo "    Dashboard:  http://localhost:$API_PORT"
echo "    API docs:   http://localhost:$API_PORT/docs"
echo
echo "==> Starting the simulator and ingest worker (Ctrl-C to stop)"
uv run --env-file .env python -m workers.pipeline "$@"

#!/usr/bin/env bash
# Start the whole DIM demo: databases, schema, indexes, seed data, API, and
# the simulator plus ingest worker. Ctrl-C stops everything except Docker.
# Extra arguments go to the simulator, e.g. --stop veh-09 or --replay-duplicates 20
set -euo pipefail
cd "$(dirname "$0")/.."

# Explicit workspace paths also support local installations whose editable
# package hooks are not loaded by Python.
export PYTHONPATH="$PWD/avdata/src:$PWD/api:$PWD/workers/src${PYTHONPATH:+:$PYTHONPATH}"
API_PORT="${API_PORT:-8000}"
[ -f .env ] || cp .env.example .env

echo "==> Starting Postgres and MongoDB"
docker compose up -d --wait
echo "==> Installing dependencies"
uv sync --all-packages --quiet
echo "==> Migrating Postgres, creating Mongo indexes, seeding demo tenants"
uv run --env-file .env alembic upgrade head
uv run --env-file .env python -m avdata.mongo
uv run --env-file .env python -m workers.seed

# Do not mistake an older API on this port for the process started below.
API_PORT="$API_PORT" uv run --no-sync python -c 'import os, socket; s = socket.socket(); s.bind(("127.0.0.1", int(os.environ["API_PORT"]))); s.close()'
echo "==> Starting the API on port $API_PORT"
uv run --env-file .env uvicorn app.main:app --port "$API_PORT" --log-level warning &
API_PID=$!
# Stop the API on exit, keeping the simulator's exit status. "|| true" because
# set -e also applies here, and wait reports the API's SIGTERM as status 143.
trap 'status=$?; kill "$API_PID" 2>/dev/null; wait "$API_PID" 2>/dev/null || true; exit $status' EXIT
for attempt in {1..40}; do
    if ! kill -0 "$API_PID" 2>/dev/null; then
        wait "$API_PID"
        exit 1
    fi
    if curl --silent --fail "http://127.0.0.1:$API_PORT/health/ready" >/dev/null; then
        break
    fi
    if [ "$attempt" = 40 ]; then
        echo "API did not become ready on port $API_PORT" >&2
        kill "$API_PID" 2>/dev/null || true
        wait "$API_PID" 2>/dev/null || true
        exit 1
    fi
    sleep 0.5
done

echo
echo "    Dashboard:  http://localhost:$API_PORT"
echo "    API docs:   http://localhost:$API_PORT/docs"
echo
echo "==> Starting the simulator and ingest worker (Ctrl-C to stop)"
uv run --env-file .env python -m workers.pipeline "$@"

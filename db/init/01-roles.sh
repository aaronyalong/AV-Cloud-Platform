#!/bin/bash
# Runs once, when the Postgres volume is empty. To re-run: docker compose down -v
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v owner_pw="$AV_OWNER_PASSWORD" -v app_pw="$AV_APP_PASSWORD" <<'SQL'
-- Neither role may bypass row-level security.
CREATE ROLE av_owner LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD :'owner_pw';
CREATE ROLE av_app LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD :'app_pw';

-- Owning the database lets av_owner create tables in its public schema; av_app cannot.
ALTER DATABASE av OWNER TO av_owner;
CREATE DATABASE av_test OWNER av_owner;
SQL

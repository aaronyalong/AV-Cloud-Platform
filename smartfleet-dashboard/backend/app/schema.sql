-- SmartFleet dashboard schema (idempotent). Applied automatically when the API starts.
CREATE TABLE IF NOT EXISTS tenant (
  tenant_id     text PRIMARY KEY,
  name          text NOT NULL,
  status        text NOT NULL DEFAULT 'active',      -- active | suspended (BR-7)
  vehicle_quota int  NOT NULL DEFAULT 50
);
CREATE TABLE IF NOT EXISTS app_user (
  email     text PRIMARY KEY,
  tenant_id text NOT NULL REFERENCES tenant,
  role      text NOT NULL,
  name      text NOT NULL
);
CREATE TABLE IF NOT EXISTS vehicle (
  vehicle_id     text PRIMARY KEY,
  tenant_id      text NOT NULL REFERENCES tenant,
  name           text NOT NULL,
  owner_email    text,
  run_id         text,
  last_heartbeat timestamptz,
  x double precision, y double precision, speed_kmh double precision
);
CREATE TABLE IF NOT EXISTS alert (
  alert_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     text NOT NULL REFERENCES tenant,
  vehicle_id    text NOT NULL REFERENCES vehicle,
  type          text NOT NULL,
  severity      text NOT NULL,                       -- critical | high | medium | low
  status        text NOT NULL DEFAULT 'open',        -- open | acknowledged | assigned | dispatched | resolved | false_alarm
  confidence    real,
  model_name    text,
  model_version text,
  x double precision, y double precision,
  evidence      jsonb NOT NULL DEFAULT '{}'::jsonb,
  assignee      text,
  version       int  NOT NULL DEFAULT 1,             -- optimistic locking (BR-3)
  event_ts      timestamptz NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  first_delivered_at timestamptz,                    -- K5 latency
  ack_at        timestamptz,
  resolved_at   timestamptz
);
CREATE INDEX IF NOT EXISTS alert_live   ON alert (tenant_id, updated_at, alert_id);
CREATE INDEX IF NOT EXISTS alert_status ON alert (tenant_id, status, created_at DESC);
CREATE TABLE IF NOT EXISTS alert_action (
  action_id   bigserial PRIMARY KEY,
  alert_id    uuid NOT NULL REFERENCES alert ON DELETE CASCADE,
  tenant_id   text NOT NULL,
  actor       text NOT NULL,
  action      text NOT NULL,
  from_status text, to_status text, note text,
  at          timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS stat_rollup (
  tenant_id    text NOT NULL,
  metric       text NOT NULL,
  dim_key      text NOT NULL,
  bucket_size  text NOT NULL,
  bucket_start timestamptz NOT NULL,
  count_val    bigint NOT NULL DEFAULT 0,
  sum_val      double precision,
  p50_val      double precision,
  p95_val      double precision,
  PRIMARY KEY (tenant_id, metric, dim_key, bucket_size, bucket_start)
);
CREATE TABLE IF NOT EXISTS dashboard_pref (
  user_email  text PRIMARY KEY,
  tenant_id   text NOT NULL,
  prefs       jsonb NOT NULL DEFAULT '{}'::jsonb,
  updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS export_job (
  export_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   text NOT NULL,
  user_email  text NOT NULL,
  status      text NOT NULL DEFAULT 'queued',        -- queued | done | failed
  query       jsonb NOT NULL,
  s3_key      text,
  rows        int,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS idem_key (
  user_email text NOT NULL,
  idem_key   text NOT NULL,
  response   jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_email, idem_key)
);
-- Model registry (platform-wide, not tenant-scoped)
CREATE TABLE IF NOT EXISTS model_version (
  model_id      serial PRIMARY KEY,
  name          text NOT NULL,                       -- audio-hazard | vision-hazard
  version       text NOT NULL,
  framework     text NOT NULL,
  artifact_key  text,
  metrics       jsonb NOT NULL DEFAULT '{}'::jsonb,  -- precision, recall, f1
  threshold     real NOT NULL DEFAULT 0.5,           -- min confidence to raise an alert
  status        text NOT NULL DEFAULT 'registered',  -- registered | active | retired
  notes         text,
  registered_by text, registered_at timestamptz NOT NULL DEFAULT now(),
  deployed_by   text, deployed_at timestamptz,
  UNIQUE (name, version)
);
CREATE TABLE IF NOT EXISTS model_event (
  id       bigserial PRIMARY KEY,
  model_id int NOT NULL REFERENCES model_version,
  action   text NOT NULL,
  actor    text NOT NULL,
  at       timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS worker_heartbeat (
  name text PRIMARY KEY, last_run timestamptz, rows_written int
);

-- Row-level security: every tenant-scoped table is filtered by the session's tenant (BR-1).
-- FORCE makes the policy apply to the table owner too (the app connects as the owner).
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['tenant','app_user','vehicle','alert','alert_action','stat_rollup','dashboard_pref','export_job']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_iso ON %I', t);
    EXECUTE format($p$CREATE POLICY tenant_iso ON %I
      USING (tenant_id = current_setting('app.tenant_id', true)
             OR current_setting('app.role', true) = 'platform_admin')
      WITH CHECK (tenant_id = current_setting('app.tenant_id', true)
             OR current_setting('app.role', true) = 'platform_admin')$p$, t);
  END LOOP;
END $$;

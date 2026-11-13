"""Stats rollup worker (FN-06). Runs as its own ECS service:  python -m app.worker

Every minute it aggregates the previous minutes into stat_rollup (1-minute buckets) for all tenants.
The same set-based function is used for backfills, so any time range can be rolled up at once.
"""
import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from .db import migrate, mongo, tx

log = logging.getLogger("worker")

ROLLUP_SQL = [
    # alerts created per minute by type|severity
    """INSERT INTO stat_rollup (tenant_id, metric, dim_key, bucket_size, bucket_start, count_val)
       SELECT tenant_id, 'alerts', type || '|' || severity, '1m', date_trunc('minute', created_at), count(*)
       FROM alert WHERE created_at >= :a AND created_at < :b
       GROUP BY 1, 3, 5
       ON CONFLICT (tenant_id, metric, dim_key, bucket_size, bucket_start)
       DO UPDATE SET count_val = EXCLUDED.count_val""",
    # closed alerts per minute by type|outcome (false-alarm rate, K6)
    """INSERT INTO stat_rollup (tenant_id, metric, dim_key, bucket_size, bucket_start, count_val)
       SELECT tenant_id, 'closed', type || '|' || status, '1m', date_trunc('minute', resolved_at), count(*)
       FROM alert WHERE resolved_at >= :a AND resolved_at < :b AND status IN ('resolved','false_alarm')
       GROUP BY 1, 3, 5
       ON CONFLICT (tenant_id, metric, dim_key, bucket_size, bucket_start)
       DO UPDATE SET count_val = EXCLUDED.count_val""",
    # event-to-display latency per minute (K5)
    """INSERT INTO stat_rollup (tenant_id, metric, dim_key, bucket_size, bucket_start,
                                count_val, sum_val, p50_val, p95_val)
       SELECT tenant_id, 'latency', 'all', '1m', date_trunc('minute', first_delivered_at), count(*),
              sum(extract(epoch FROM first_delivered_at - event_ts)),
              percentile_cont(0.5)  WITHIN GROUP (ORDER BY extract(epoch FROM first_delivered_at - event_ts)),
              percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM first_delivered_at - event_ts))
       FROM alert WHERE first_delivered_at >= :a AND first_delivered_at < :b
       GROUP BY 1, 5
       ON CONFLICT (tenant_id, metric, dim_key, bucket_size, bucket_start)
       DO UPDATE SET count_val=EXCLUDED.count_val, sum_val=EXCLUDED.sum_val,
                     p50_val=EXCLUDED.p50_val, p95_val=EXCLUDED.p95_val""",
    # time to acknowledge per minute (K3 MTTA)
    """INSERT INTO stat_rollup (tenant_id, metric, dim_key, bucket_size, bucket_start, count_val, sum_val)
       SELECT tenant_id, 'mtta', 'all', '1m', date_trunc('minute', ack_at), count(*),
              sum(extract(epoch FROM ack_at - created_at))
       FROM alert WHERE ack_at >= :a AND ack_at < :b
       GROUP BY 1, 5
       ON CONFLICT (tenant_id, metric, dim_key, bucket_size, bucket_start)
       DO UPDATE SET count_val=EXCLUDED.count_val, sum_val=EXCLUDED.sum_val""",
]


def rollup_range(a: datetime, b: datetime) -> int:
    """Aggregate [a, b) into 1-minute rollups for every tenant (worker runs as platform role)."""
    n = 0
    with tx(None, "platform_admin") as c:
        for sql in ROLLUP_SQL:
            n += c.execute(text(sql), {"a": a, "b": b}).rowcount or 0
        # K9 telemetry throughput from DocumentDB: samples per vehicle-minute bucket
        try:
            pipeline = [{"$match": {"bucket_start": {"$gte": a, "$lt": b}}},
                        {"$project": {"tenant_id": 1, "bucket_start": 1, "n": {"$size": "$samples"}}},
                        {"$group": {"_id": {"t": "$tenant_id", "m": "$bucket_start"}, "n": {"$sum": "$n"},
                                    "veh": {"$sum": 1}}}]
            for d in mongo().telemetry.aggregate(pipeline):
                c.execute(text("""INSERT INTO stat_rollup (tenant_id, metric, dim_key, bucket_size, bucket_start,
                                  count_val, sum_val) VALUES (:t,'telemetry','all','1m',:m,:n,:v)
                                  ON CONFLICT (tenant_id, metric, dim_key, bucket_size, bucket_start)
                                  DO UPDATE SET count_val=EXCLUDED.count_val, sum_val=EXCLUDED.sum_val"""),
                          {"t": d["_id"]["t"], "m": d["_id"]["m"], "n": d["n"], "v": d["veh"]})
                n += 1
        except Exception as e:  # noqa: BLE001 - telemetry stats are best-effort
            log.warning("telemetry rollup skipped: %s", e)
        c.execute(text("""INSERT INTO worker_heartbeat VALUES ('rollup', now(), :n)
                          ON CONFLICT (name) DO UPDATE SET last_run=now(), rows_written=:n"""), {"n": n})
    return n


def run_forever():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    migrate()
    log.info("rollup worker started")
    while True:
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        try:
            # re-roll the last 3 minutes (late updates such as acks land in the right bucket)
            n = rollup_range(now - timedelta(minutes=3), now + timedelta(minutes=1))
            log.info("rollup ok rows=%s", n)
        except Exception:  # noqa: BLE001
            log.exception("rollup failed")
        time.sleep(max(5, 65 - datetime.now(timezone.utc).second))


if __name__ == "__main__":
    run_forever()

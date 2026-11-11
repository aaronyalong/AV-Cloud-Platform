"""Run the vehicle simulator and the ingest worker on one in-process queue.

    uv run --env-file .env python -m workers.pipeline
    uv run --env-file .env python -m workers.pipeline --stop veh-09
    uv run --env-file .env python -m workers.pipeline --replay-duplicates 20

Ctrl-C sends a disconnect for every running vehicle, waits for the worker to
store everything queued, then exits.
"""

import argparse
import asyncio
import logging
import os
import signal

from sqlalchemy.ext.asyncio import create_async_engine

from avdata import mongo as avmongo
from workers.ingest import IngestWorker
from workers.queue import InProcessQueue
from workers.simulator import Simulator

logger = logging.getLogger("workers.pipeline")
STATS_INTERVAL_S = 10


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--run-id", default="run-demo")
    parser.add_argument("--stop", metavar="VEHICLE_ID", help="vehicle that goes silent")
    parser.add_argument(
        "--stop-after",
        type=float,
        default=20.0,
        metavar="SECONDS",
        help="when --stop takes effect (default 20)",
    )
    parser.add_argument(
        "--replay-duplicates",
        type=int,
        default=0,
        metavar="N",
        help="resend the last N messages once, to show they are stored only once",
    )
    parser.add_argument(
        "--replay-after",
        type=float,
        default=15.0,
        metavar="SECONDS",
        help="when the replay happens (default 15)",
    )
    return parser.parse_args()


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} is not set. Run with: uv run --env-file .env ...")
    return value


async def log_stats(worker: IngestWorker) -> None:
    while True:
        await asyncio.sleep(STATS_INTERVAL_S)
        logger.info("Ingest totals: %s", dict(sorted(worker.stats.items())))


async def main() -> None:
    args = parse_args()
    engine = create_async_engine(require_env("APP_DATABASE_URL"))
    mongo = avmongo.connect(require_env("MONGO_URL"))
    queue = InProcessQueue()
    worker = IngestWorker(engine, mongo[require_env("MONGO_DB")])
    simulator = Simulator(
        queue,
        run_id=args.run_id,
        stop=args.stop,
        stop_after_s=args.stop_after,
        replay_duplicates=args.replay_duplicates,
        replay_after_s=args.replay_after,
    )

    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopping.set)

    logger.info(
        "Simulating %d vehicles on run %s. Ctrl-C to stop.",
        len(simulator.vehicles),
        args.run_id,
    )
    worker_task = asyncio.create_task(worker.run(queue))
    stats_task = asyncio.create_task(log_stats(worker))
    sim_task = asyncio.create_task(simulator.run())
    try:
        await stopping.wait()
    finally:
        sim_task.cancel()
        await asyncio.gather(sim_task, return_exceptions=True)
        await simulator.disconnect()
        await queue.drain()
        for task in (worker_task, stats_task):
            task.cancel()
        await asyncio.gather(worker_task, stats_task, return_exceptions=True)
        logger.info("Final ingest totals: %s", dict(sorted(worker.stats.items())))
        await engine.dispose()
        await mongo.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s"
    )
    asyncio.run(main())

"""Health checks, outside /api/v1 so the load balancer can reach them without auth.

/health is for liveness and never touches a database, so a database outage
doesn't make the load balancer replace every API task. /health/ready reports
whether both databases are reachable.
"""

import logging
from collections.abc import Awaitable, Callable

import anyio
from fastapi import APIRouter, Request, Response
from sqlalchemy import text

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])

CHECK_TIMEOUT_SECONDS = 2


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(request: Request, response: Response) -> dict:
    engine = request.app.state.engine
    mongo_db = request.app.state.mongo_db

    async def postgres() -> None:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    async def mongo() -> None:
        await mongo_db.command("ping")

    checks = {"postgres": await _check(postgres), "mongo": await _check(mongo)}
    healthy = all(result == "ok" for result in checks.values())
    if not healthy:
        response.status_code = 503
    return {"status": "ok" if healthy else "unavailable", "checks": checks}


async def _check(probe: Callable[[], Awaitable[None]]) -> str:
    """Run one probe with a time limit. Details are logged, never returned."""
    try:
        with anyio.fail_after(CHECK_TIMEOUT_SECONDS):
            await probe()
    except Exception:
        logger.warning("Readiness check %s failed", probe.__name__, exc_info=True)
        return "error"
    return "ok"

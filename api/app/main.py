"""FastAPI application.

Run locally: uv run --env-file .env uvicorn app.main:app --reload
"""

import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.routes import alerts, commands, health, media, vehicles
from avdata import mongo as avmongo
from avdata.media import LocalMediaStore


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create database clients once at startup. Neither connects until first use."""
    settings = Settings()
    engine = create_async_engine(settings.app_database_url, pool_pre_ping=True)
    mongo = avmongo.connect(settings.mongo_url)
    app.state.engine = engine
    app.state.mongo_db = mongo[settings.mongo_db]
    signing_key = settings.media_signing_key or secrets.token_hex(32)
    app.state.media_store = LocalMediaStore(settings.media_dir, signing_key.encode())
    try:
        yield
    finally:
        await engine.dispose()
        await mongo.close()


app = FastAPI(title="AV Cloud Platform API", lifespan=lifespan)
app.include_router(health.router)
app.include_router(vehicles.router)
app.include_router(alerts.router)
app.include_router(commands.router)
app.include_router(media.router)

STATIC_DIR = Path(__file__).parent / "static"
INDEX_HTML = STATIC_DIR / "index.html"
ALERTS_HTML = STATIC_DIR / "alerts.html"
ALERT_SIMULATOR_HTML = STATIC_DIR / "alert-simulator.html"


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    """The fleet dashboard: one static page that calls the API."""
    return FileResponse(INDEX_HTML)


@app.get("/alerts", include_in_schema=False)
async def alerts_page() -> FileResponse:
    """The alert management page for reviewing and resolving active alerts."""
    return FileResponse(ALERTS_HTML)


@app.get("/alert-simulator", include_in_schema=False)
async def alert_simulator() -> FileResponse:
    """The alert simulator page for generating test alerts during demos."""
    return FileResponse(ALERT_SIMULATOR_HTML)

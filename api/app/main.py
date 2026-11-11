"""FastAPI application.

Run locally: uv run --env-file .env uvicorn app.main:app --reload
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.routes import health, vehicles
from avdata import mongo as avmongo


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create database clients once at startup. Neither connects until first use."""
    settings = Settings()
    engine = create_async_engine(settings.app_database_url, pool_pre_ping=True)
    mongo = avmongo.connect(settings.mongo_url)
    app.state.engine = engine
    app.state.mongo_db = mongo[settings.mongo_db]
    try:
        yield
    finally:
        await engine.dispose()
        await mongo.close()


app = FastAPI(title="AV Cloud Platform API", lifespan=lifespan)
app.include_router(health.router)
app.include_router(vehicles.router)

INDEX_HTML = Path(__file__).parent / "static" / "index.html"


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    """The fleet dashboard: one static page that calls the API."""
    return FileResponse(INDEX_HTML)

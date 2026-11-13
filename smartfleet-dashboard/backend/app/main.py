"""SmartFleet System Dashboard API (FastAPI).  Run:  uvicorn app.main:app --host 0.0.0.0 --port 8000"""
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import config, db
from .routes_admin import router as admin_router
from .routes_alerts import router as alerts_router
from .routes_dashboard import router as dashboard_router
from .routes_sim import router as sim_router
from .routes_stats import router as stats_router
from .seed import seed
from .util import metrics

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.migrate()
    seed()
    db.ensure_bucket()
    yield


app = FastAPI(title="SmartFleet System Dashboard API", version="1.0.0", lifespan=lifespan,
              docs_url="/api/docs", openapi_url="/api/openapi.json")

if config.ENV == "local":   # in AWS the SPA and API share one CloudFront origin, so no CORS is needed
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"], expose_headers=["ETag"])


@app.middleware("http")
async def timing(request: Request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    if request.url.path.startswith("/api/v1") and not request.url.path.startswith("/api/v1/sim"):
        metrics.add((time.perf_counter() - t0) * 1000, response.status_code)
    return response


# RFC 9457 problem+json errors
@app.exception_handler(StarletteHTTPException)
async def http_problem(request: Request, exc: StarletteHTTPException):
    return JSONResponse({"type": "about:blank", "title": exc.detail if isinstance(exc.detail, str) else "error",
                         "status": exc.status_code, "detail": exc.detail, "instance": request.url.path},
                        status_code=exc.status_code, media_type="application/problem+json")


@app.exception_handler(RequestValidationError)
async def validation_problem(request: Request, exc: RequestValidationError):
    return JSONResponse({"type": "about:blank", "title": "validation error", "status": 422,
                         "detail": [e.get("msg") for e in exc.errors()], "instance": request.url.path},
                        status_code=422, media_type="application/problem+json")


@app.get("/healthz")
def healthz():
    with db.engine.connect() as c:
        c.execute(text("SELECT 1"))
    return {"ok": True}


for r in (dashboard_router, alerts_router, stats_router, admin_router, sim_router):
    app.include_router(r)

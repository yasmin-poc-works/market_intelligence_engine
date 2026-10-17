import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import get_settings
from db import init_db
from db import session as db_session
from errors import install_error_handlers
from routes import alerts, reports, research, watchlist
from scheduler import WatchlistScheduler

log = logging.getLogger("insightforge")


def build_scheduler() -> WatchlistScheduler | None:
    if not get_settings().scheduler_enabled:
        return None
    from agents.diff import make_llm_judge
    from services.pipeline_factory import build_deps

    return WatchlistScheduler(db_session.SessionLocal, build_deps, make_llm_judge())


def startup_sweep() -> None:
    """Drop stored run data older than the retention window (the database history stays)."""
    from services.retention import build_retention

    try:
        build_retention().sweep()
    except Exception:
        log.exception("startup cleanup failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    startup_sweep()
    app.state.scheduler = build_scheduler()
    if app.state.scheduler:
        app.state.scheduler.start()
    yield
    if app.state.scheduler:
        app.state.scheduler.shutdown()


app = FastAPI(title="InsightForge AI API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)
install_error_handlers(app)

app.include_router(research.router)
app.include_router(watchlist.router)
app.include_router(reports.router)
app.include_router(alerts.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

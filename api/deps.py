"""FastAPI dependencies. Tests override these via app.dependency_overrides."""
from collections.abc import Callable
from pathlib import Path

from config import get_settings
from db import session as db_session
from graph.events import EventRecorder
from graph.pipeline import PipelineDeps

DepsFactory = Callable[[EventRecorder], PipelineDeps]


def get_session_factory():
    return db_session.SessionLocal


def get_upload_dir() -> Path:
    return get_settings().upload_dir


def get_deps_factory() -> DepsFactory:
    from services.pipeline_factory import build_deps

    return build_deps

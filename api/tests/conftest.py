import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import db.models  # noqa: F401
from db.session import Base, make_engine
from deps import get_deps_factory, get_pdf_fetcher, get_retention, get_session_factory, get_upload_dir
from errors import ApiError
from main import app
from tests.fake_pipeline import make_deps_factory
from tests.test_fact_checker import EMB


async def fake_fetch_pdf(url: str) -> bytes:
    """Stands in for the network: .pdf links return a PDF, anything else is a web page."""
    if url.split("?")[0].lower().endswith(".pdf"):
        return b"%PDF-1.4\n%fake linked pdf\n"
    raise ApiError(415, f'"{url}" is not a direct link to a PDF file', "not_a_pdf")


@pytest.fixture(autouse=True)
def pinned_settings(monkeypatch):
    """Tests must not depend on the developer's .env (tuned for free-tier limits)."""
    from config import get_settings

    for key, value in {
        "MAX_PARALLEL_SUBTASKS": "6",
        "LLM_MAX_CONCURRENCY": "3",
        "LLM_MAX_RETRIES": "5",
        "EVIDENCE_TOKEN_BUDGET": "6000",
        "JWT_SECRET": "test-secret-not-used-anywhere-else-0123456789",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def make_retention(sf, tmp_path, **kw):
    """Retention wired to the test's own folders, in-memory Qdrant and a deterministic embedder."""
    from qdrant_client import QdrantClient

    from context.scratch_store import ScratchStore
    from context.vector_store import VectorStore
    from services.retention import Retention

    vectors = VectorStore(QdrantClient(":memory:"), EMB)
    return Retention(sf, ScratchStore(tmp_path / "scratch"), tmp_path / "uploads", vectors, lambda: EMB, **kw)


class Api:
    def __init__(self, client, session_factory, tmp_path):
        self.client, self.sf, self.tmp_path = client, session_factory, tmp_path

    def run_brief(self, text="Acme funding and EV market outlook", **kw):
        files = kw.pop("files", None)
        return self.client.post("/research/run", data={"brief": text, **kw}, files=files)

    def completed_report_id(self) -> str:
        run_id = self.run_brief().json()["run_id"]
        return self.client.get(f"/research/runs/{run_id}").json()["report_id"]


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr("main.init_db", lambda: None)  # tests use their own database
    monkeypatch.setattr("main.build_scheduler", lambda: None)  # scheduler tests build their own
    monkeypatch.setattr("main.startup_sweep", lambda: None)  # never touch the developer's data folders
    eng = make_engine(f"sqlite:///{(tmp_path / 'api.db').as_posix()}")
    Base.metadata.create_all(eng)
    sf = sessionmaker(bind=eng, expire_on_commit=False)
    app.dependency_overrides[get_session_factory] = lambda: sf
    app.dependency_overrides[get_deps_factory] = lambda: make_deps_factory(tmp_path)
    app.dependency_overrides[get_pdf_fetcher] = lambda: fake_fetch_pdf
    app.dependency_overrides[get_upload_dir] = lambda: tmp_path / "uploads"
    app.dependency_overrides[get_retention] = lambda: make_retention(sf, tmp_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield Api(client, sf, tmp_path)
    app.dependency_overrides.clear()
    eng.dispose()

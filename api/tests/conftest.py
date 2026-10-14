import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import db.models  # noqa: F401
from db.session import Base, make_engine
from deps import get_deps_factory, get_pdf_fetcher, get_session_factory, get_upload_dir
from errors import ApiError
from main import app
from tests.fake_pipeline import make_deps_factory


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
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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
    eng = make_engine(f"sqlite:///{(tmp_path / 'api.db').as_posix()}")
    Base.metadata.create_all(eng)
    sf = sessionmaker(bind=eng, expire_on_commit=False)
    app.dependency_overrides[get_session_factory] = lambda: sf
    app.dependency_overrides[get_deps_factory] = lambda: make_deps_factory(tmp_path)
    app.dependency_overrides[get_pdf_fetcher] = lambda: fake_fetch_pdf
    app.dependency_overrides[get_upload_dir] = lambda: tmp_path / "uploads"
    with TestClient(app, raise_server_exceptions=False) as client:
        yield Api(client, sf, tmp_path)
    app.dependency_overrides.clear()
    eng.dispose()

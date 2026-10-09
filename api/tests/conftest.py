import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import db.models  # noqa: F401
from db.session import Base, make_engine
from deps import get_deps_factory, get_session_factory, get_upload_dir
from main import app
from tests.fake_pipeline import make_deps_factory


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
    eng = make_engine(f"sqlite:///{(tmp_path / 'api.db').as_posix()}")
    Base.metadata.create_all(eng)
    sf = sessionmaker(bind=eng, expire_on_commit=False)
    app.dependency_overrides[get_session_factory] = lambda: sf
    app.dependency_overrides[get_deps_factory] = lambda: make_deps_factory(tmp_path)
    app.dependency_overrides[get_upload_dir] = lambda: tmp_path / "uploads"
    with TestClient(app, raise_server_exceptions=False) as client:
        yield Api(client, sf, tmp_path)
    app.dependency_overrides.clear()
    eng.dispose()

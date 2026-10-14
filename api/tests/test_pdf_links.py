import asyncio

import httpx
import pytest

from db.models import Run
from errors import ApiError
from services import uploads
from services.uploads import MAX_BYTES, fetch_pdf, name_from_url, parse_pdf_url

PDF = b"%PDF-1.4\n%fake pdf body\n"


def docs_of(api, run_id):
    with api.sf() as db:
        return db.get(Run, run_id).options


def test_link_is_downloaded_and_stored_like_an_upload(api):
    r = api.run_brief(document_urls=["https://example.com/reports/Q3%20Report.pdf"])
    assert r.status_code == 202
    opts = docs_of(api, r.json()["run_id"])
    assert opts["document_urls"] == ["https://example.com/reports/Q3%20Report.pdf"]
    assert opts["document_ids"] == ["1_Q3_Report.pdf"]
    stored = api.tmp_path / "uploads" / r.json()["run_id"] / "1_Q3_Report.pdf"
    assert stored.read_bytes().startswith(b"%PDF")


def test_files_and_links_are_combined_and_deduplicated(api):
    files = [("files", ("a.pdf", PDF, "application/pdf"))]
    r = api.run_brief(files=files, document_urls=["https://x.com/b.pdf", "https://x.com/b.pdf", " "])
    names = docs_of(api, r.json()["run_id"])["document_ids"]
    assert len(names) == 2 and names[0].endswith("a.pdf") and names[1].endswith("b.pdf")
    assert len({n.split("_")[0] for n in names}) == 2  # no index clash


def test_limit_counts_files_and_links_together(api):
    files = [("files", (f"{i}.pdf", PDF, "application/pdf")) for i in range(9)]
    urls = ["https://x.com/1.pdf", "https://x.com/2.pdf"]
    r = api.run_brief(files=files, document_urls=urls)
    assert r.status_code == 422 and r.json()["error"]["code"] == "too_many_files"
    assert api.run_brief(files=files, document_urls=urls[:1]).status_code == 202


@pytest.mark.parametrize("bad", ["ftp://x.com/a.pdf", "not a url", "file:///etc/passwd", "javascript:alert(1)"])
def test_non_http_links_rejected(api, bad):
    r = api.run_brief(document_urls=[bad])
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_url"


def test_link_to_a_web_page_is_rejected_and_leaves_no_run_behind(api):
    r = api.run_brief(document_urls=["https://example.com/article.html"])
    assert r.status_code == 415 and r.json()["error"]["code"] == "not_a_pdf"
    with api.sf() as db:
        assert db.query(Run).count() == 0
    assert not (api.tmp_path / "uploads").exists() or not any((api.tmp_path / "uploads").iterdir())


def test_name_from_url():
    assert name_from_url("https://x.com/a/b/Annual%20Report%202025.pdf?dl=1", 1) == "Annual_Report_2025.pdf"
    assert name_from_url("https://x.com/", 4) == "document_4.pdf"
    assert parse_pdf_url("  https://x.com/a.pdf ") == "https://x.com/a.pdf"


# ---------- the real downloader (no network: DNS check and transport are faked) ----------
@pytest.fixture
def downloader(monkeypatch):
    real_check = uploads._check_public_host
    real_client = httpx.AsyncClient  # captured once: install() may be called several times per test

    async def public(host):
        if host in ("localhost", "127.0.0.1", "10.0.0.5"):
            await real_check(host)

    monkeypatch.setattr(uploads, "_check_public_host", public)

    def install(handler):
        monkeypatch.setattr(
            uploads.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)
        )

    return install


def test_fetch_pdf_follows_redirect_and_returns_bytes(downloader):
    def handler(req):
        if req.url.path == "/old.pdf":
            return httpx.Response(302, headers={"location": "/new.pdf"})
        return httpx.Response(200, content=PDF)

    downloader(handler)
    assert asyncio.run(fetch_pdf("https://example.com/old.pdf")) == PDF


def test_fetch_pdf_error_cases(downloader):
    downloader(lambda req: httpx.Response(404))
    with pytest.raises(ApiError) as e:
        asyncio.run(fetch_pdf("https://example.com/a.pdf"))
    assert e.value.code == "fetch_failed"

    downloader(lambda req: httpx.Response(200, content=b"<html>hi</html>"))
    with pytest.raises(ApiError) as e:
        asyncio.run(fetch_pdf("https://example.com/a.pdf"))
    assert e.value.code == "not_a_pdf"

    downloader(lambda req: httpx.Response(200, content=PDF + b"0" * MAX_BYTES))
    with pytest.raises(ApiError) as e:
        asyncio.run(fetch_pdf("https://example.com/a.pdf"))
    assert e.value.code == "file_too_large"

    downloader(lambda req: httpx.Response(302, headers={"location": "/loop.pdf"}))
    with pytest.raises(ApiError) as e:
        asyncio.run(fetch_pdf("https://example.com/loop.pdf"))
    assert e.value.code == "fetch_failed"


@pytest.mark.parametrize("url", ["http://127.0.0.1/a.pdf", "http://localhost:8000/a.pdf", "http://10.0.0.5/a.pdf"])
def test_private_and_local_addresses_are_blocked(downloader, url):
    downloader(lambda req: httpx.Response(200, content=PDF))
    with pytest.raises(ApiError) as e:
        asyncio.run(fetch_pdf(url))
    assert e.value.code == "blocked_address"


def test_redirect_to_a_private_address_is_blocked(downloader):
    downloader(lambda req: httpx.Response(302, headers={"location": "http://127.0.0.1/secret.pdf"}))
    with pytest.raises(ApiError) as e:
        asyncio.run(fetch_pdf("https://example.com/a.pdf"))
    assert e.value.code == "blocked_address"

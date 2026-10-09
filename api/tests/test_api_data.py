from datetime import UTC, datetime, timedelta

import jwt

from config import get_settings
from deps import get_session_factory
from main import app
from services.export import markdown_to_pdf
from services.share import SCOPE, create_share_token


# ---------- 5.3 watchlist ----------
def test_watchlist_crud(api):
    c = api.client
    created = c.post("/watchlist", json={"topic": "  EV batteries  ", "frequency": "daily"})
    assert created.status_code == 201
    item = created.json()
    assert item["topic"] == "EV batteries" and item["active"] is True and item["last_run"] is None
    next_run = datetime.fromisoformat(item["next_run"])
    assert timedelta(hours=23) < next_run.replace(tzinfo=UTC) - datetime.now(UTC) < timedelta(hours=25)

    assert c.get(f"/watchlist/{item['id']}").json()["topic"] == "EV batteries"
    assert [i["id"] for i in c.get("/watchlist").json()] == [item["id"]]

    upd = c.patch(
        f"/watchlist/{item['id']}",
        json={"frequency": "weekly", "webhook_url": "https://hooks.slack.com/services/x", "active": False},
    ).json()
    assert upd["frequency"] == "weekly" and upd["active"] is False
    assert upd["webhook_url"] == "https://hooks.slack.com/services/x"
    assert datetime.fromisoformat(upd["next_run"]).replace(tzinfo=UTC) - datetime.now(UTC) > timedelta(days=6)

    cleared = c.patch(f"/watchlist/{item['id']}", json={"webhook_url": None}).json()
    assert cleared["webhook_url"] is None and cleared["topic"] == "EV batteries"

    assert c.delete(f"/watchlist/{item['id']}").status_code == 204
    assert c.get(f"/watchlist/{item['id']}").status_code == 404
    assert c.get("/watchlist").json() == []


def test_watchlist_defaults_to_weekly(api):
    item = api.client.post("/watchlist", json={"topic": "x"}).json()
    assert item["frequency"] == "weekly"


def test_watchlist_validation(api):
    c = api.client
    assert c.post("/watchlist", json={"topic": ""}).status_code == 422
    assert c.post("/watchlist", json={"topic": "x", "frequency": "hourly"}).status_code == 422
    assert c.post("/watchlist", json={"topic": "x", "webhook_url": "javascript:alert(1)"}).status_code == 422
    assert c.post("/watchlist", json={"topic": "x", "webhook_url": "not a url"}).status_code == 422
    assert c.post("/watchlist", json={}).status_code == 422


def test_watchlist_missing_item(api):
    c = api.client
    assert c.get("/watchlist/nope").status_code == 404
    assert c.patch("/watchlist/nope", json={"topic": "x"}).status_code == 404
    assert c.delete("/watchlist/nope").status_code == 404


# ---------- 5.4 reports ----------
def test_reports_list_and_retrieve(api):
    first = api.completed_report_id()
    second = api.completed_report_id()
    listing = api.client.get("/reports").json()
    assert listing["total"] == 2 and {i["id"] for i in listing["items"]} == {first, second}
    assert listing["items"][0]["fact_check_pass_rate"] == 1.0

    page = api.client.get("/reports", params={"limit": 1, "offset": 1}).json()
    assert len(page["items"]) == 1 and page["total"] == 2

    detail = api.client.get(f"/reports/{first}").json()
    assert detail["id"] == first and "## Executive Summary" in detail["markdown"]
    assert detail["report"]["sections"][0]["heading"] == "Funding"
    assert detail["fact_check"]["total_checked"] == 3


def test_reports_pagination_validation_and_empty(api):
    assert api.client.get("/reports").json() == {"items": [], "total": 0, "limit": 20, "offset": 0}
    assert api.client.get("/reports", params={"limit": 0}).status_code == 422
    assert api.client.get("/reports", params={"limit": 101}).status_code == 422
    assert api.client.get("/reports", params={"offset": -1}).status_code == 422


def test_report_not_found(api):
    for url in ["/reports/nope", "/reports/nope/export.md", "/reports/nope/export.pdf"]:
        assert api.client.get(url).status_code == 404
    assert api.client.post("/reports/nope/share").status_code == 404


# ---------- 5.5 export ----------
def test_markdown_export(api):
    rid = api.completed_report_id()
    r = api.client.get(f"/reports/{rid}/export.md")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
    assert 'attachment; filename="' in r.headers["content-disposition"]
    assert r.headers["content-disposition"].endswith('.md"')
    assert r.text == api.client.get(f"/reports/{rid}").json()["markdown"]


def test_pdf_export(api):
    rid = api.completed_report_id()
    r = api.client.get(f"/reports/{rid}/export.pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF") and len(r.content) > 1000
    assert r.headers["content-disposition"].endswith('.pdf"')


def test_pdf_handles_unicode_and_long_content():
    md = "# Title — “quoted”\n\n## Section\n\n*Confidence: HIGH*\n\n- bullet • one\n\n" + (
        "Electric‑vehicle sales grew 12%. [Source: a.com, 2026-05-01, credibility: 0.85] " * 200
    )
    pdf = markdown_to_pdf(md + "\n中文 and emoji \U0001f600 fallback")
    assert pdf.startswith(b"%PDF") and pdf.count(b"/Type /Page") >= 2


# ---------- 5.6 share links ----------
def test_share_link_roundtrip(api):
    rid = api.completed_report_id()
    r = api.client.post(f"/reports/{rid}/share")
    assert r.status_code == 201
    body = r.json()
    assert body["url"] == f"{get_settings().frontend_url.rstrip('/')}/share/{body['token']}"
    expires = datetime.fromisoformat(body["expires_at"])
    assert timedelta(days=6, hours=23) < expires - datetime.now(UTC) <= timedelta(days=7)

    shared = api.client.get(f"/share/{body['token']}")
    assert shared.status_code == 200 and shared.json()["id"] == rid


def test_expired_share_link_rejected(api):
    rid = api.completed_report_id()
    token, _ = create_share_token(rid, now=datetime.now(UTC) - timedelta(days=8))
    r = api.client.get(f"/share/{token}")
    assert r.status_code == 401 and r.json()["error"]["code"] == "share_expired"


def test_link_valid_just_inside_seven_days(api):
    rid = api.completed_report_id()
    token, _ = create_share_token(rid, now=datetime.now(UTC) - timedelta(days=6, hours=23))
    assert api.client.get(f"/share/{token}").status_code == 200


def test_tampered_and_foreign_tokens_rejected(api):
    rid = api.completed_report_id()
    token, _ = create_share_token(rid)
    secret = get_settings().jwt_secret
    exp = datetime.now(UTC) + timedelta(days=1)
    bad = {
        "garbage": "not.a.jwt",
        "tampered": token[:-3] + ("aaa" if not token.endswith("aaa") else "bbb"),
        "wrong secret": jwt.encode({"sub": rid, "scope": SCOPE, "exp": exp}, "other-secret", algorithm="HS256"),
        "wrong scope": jwt.encode({"sub": rid, "scope": "admin", "exp": exp}, secret, algorithm="HS256"),
        "no scope": jwt.encode({"sub": rid, "exp": exp}, secret, algorithm="HS256"),
        "no expiry": jwt.encode({"sub": rid, "scope": SCOPE}, secret, algorithm="HS256"),
        "alg none": jwt.encode({"sub": rid, "scope": SCOPE, "exp": exp}, None, algorithm="none"),
    }
    for name, tok in bad.items():
        r = api.client.get(f"/share/{tok}")
        assert r.status_code == 401, name
        assert r.json()["error"]["code"] == "share_invalid", name


def test_share_link_for_deleted_report_is_404(api):
    token, _ = create_share_token("deadbeef")
    assert api.client.get(f"/share/{token}").status_code == 404


def test_share_token_only_grants_that_report(api):
    a, b = api.completed_report_id(), api.completed_report_id()
    token, _ = create_share_token(a)
    assert api.client.get(f"/share/{token}").json()["id"] == a != b


# ---------- 5.7 error handling and CORS ----------
def test_unknown_route_uses_error_envelope(api):
    r = api.client.get("/nope")
    assert r.status_code == 404 and r.json() == {"error": {"code": "not_found", "message": "Not Found"}}


def test_method_not_allowed_envelope(api):
    r = api.client.delete("/health")
    assert r.status_code == 405 and r.json()["error"]["code"] == "method_not_allowed"


def test_validation_error_envelope_lists_fields(api):
    err = api.client.post("/watchlist", json={"topic": "x", "frequency": "hourly"}).json()["error"]
    assert err["code"] == "validation_error" and err["details"][0]["field"] == "frequency"


def test_unhandled_exception_returns_500_without_leaking(api):
    def broken():
        raise RuntimeError("secret internal detail /etc/passwd")

    app.dependency_overrides[get_session_factory] = broken
    r = api.client.get("/reports")
    assert r.status_code == 500
    assert r.json() == {"error": {"code": "internal_error", "message": "Internal server error"}}


def test_cors_allows_only_configured_origin(api):
    ok = api.client.options(
        "/reports", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"}
    )
    assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == "http://localhost:3000"
    other = api.client.options(
        "/reports", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"}
    )
    assert "access-control-allow-origin" not in other.headers


def test_cors_exposes_download_filename_header(api):
    rid = api.completed_report_id()
    r = api.client.get(f"/reports/{rid}/export.md", headers={"Origin": "http://localhost:3000"})
    assert "content-disposition" in r.headers["access-control-expose-headers"].lower()

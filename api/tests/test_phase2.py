import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fpdf import FPDF
from qdrant_client import QdrantClient

from agents.doc_reader import DocumentReaderAgent, chunk_id_from_url, make_chunk_lookup
from agents.entity_extractor import (
    Attribute,
    EntityExtractorAgent,
    ExtractedEntities,
    ExtractedEntity,
    ExtractedRelationship,
    build_graph,
    entity_key,
    load_graph,
    same_value,
)
from agents.web_search import WebSearchAgent, build_query
from context.scratch_store import ScratchStore
from context.vector_store import VectorStore
from schemas import SubTask
from services import netguard
from services.research_runtime import make_researcher
from tests.test_fact_checker import EMB
from tools import fetch as fetch_mod
from tools.credibility import credibility_score, domain_tier, recency_factor
from tools.documents import extract_chunks, merge_pieces
from tools.fetch import FetchedPage, SkippedSource, download_html, parse_html
from tools.search import SearchError, SearchResult, SerpApiProvider

NOW = datetime(2026, 6, 1, tzinfo=UTC)
PARAGRAPHS = [
    "Acme announced a Series B round of fifty million dollars led by Orbit Capital on Friday, the company said in a statement to reporters in Berlin.",
    "The funding will be used to expand its charging network across Europe over the next two years, executives told analysts during a call.",
    "Competitors including Voltra and Chargely have raised smaller rounds this year, according to data compiled by industry researchers.",
    "Analysts said the deal values the company at roughly four times last year's revenue, a premium to listed peers in the sector.",
]
ARTICLE = (
    "<html><head><title>Acme raises $50M</title><meta name='author' content='Jane Doe'>"
    "<meta property='article:published_time' content='2026-05-01'></head><body><article>"
    "<h1>Acme raises $50M</h1>" + "".join(f"<p>{p}</p>" for p in PARAGRAPHS) + "</article></body></html>"
)


def task(kind="web", title="Funding", scope="Series B funding of Acme"):
    return SubTask(id="t1", title=title, scope=scope, researcher=kind)


# ---------- 2.3 credibility ----------
def test_domain_tiers():
    assert domain_tier("www.reuters.com") > domain_tier("techcrunch.com") > domain_tier("randomblog.io")
    assert domain_tier("randomblog.io") > domain_tier("medium.com")
    assert domain_tier("data.worldbank.org") == domain_tier("sec.gov") == domain_tier("ox.ac.uk")


def test_recency_decays_and_undated_is_low():
    fresh = recency_factor(NOW - timedelta(days=1), NOW)
    old = recency_factor(NOW - timedelta(days=1460), NOW)
    assert fresh > recency_factor(NOW - timedelta(days=730), NOW) > old
    assert recency_factor(NOW - timedelta(days=730), NOW) == pytest.approx(0.5, abs=0.01)
    assert recency_factor(None) < fresh


def test_byline_and_bounds():
    a = credibility_score("example.com", NOW, "Jane Doe", NOW)
    b = credibility_score("example.com", NOW, None, NOW)
    assert a > b
    assert 0 <= credibility_score("medium.com", None, None) <= credibility_score("reuters.com", NOW, "x", NOW) <= 1
    assert credibility_score("reuters.com", NOW - timedelta(days=365), None, NOW) >= 0.7  # single source can reach MEDIUM


# ---------- 2.1 search ----------
def _serp(handler):
    return SerpApiProvider("key", httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def test_serpapi_parses_results_and_sends_params():
    seen = {}

    def handler(req):
        seen.update(dict(req.url.params))
        return httpx.Response(
            200,
            json={"organic_results": [
                {"title": "A", "link": "https://a.com/x", "snippet": "s", "date": "May 1, 2026"},
                {"title": "no link"},
                {"title": "B", "link": "https://b.com/y"},
            ]},
        )

    out = asyncio.run(_serp(handler).search("acme funding", 5))
    assert [r.url for r in out] == ["https://a.com/x", "https://b.com/y"]
    assert out[0].snippet == "s" and out[0].date == "May 1, 2026"
    assert seen["q"] == "acme funding" and seen["num"] == "5" and seen["api_key"] == "key"


@pytest.fixture(autouse=True)
def fast_search_retries(monkeypatch):
    from tools import search as search_mod

    monkeypatch.setattr(search_mod, "RETRY_DELAY", 0)


def test_serpapi_retries_transient_failures_but_not_client_errors():
    calls = []

    def flaky(req):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ReadTimeout("slow")
        if len(calls) == 2:
            return httpx.Response(503)
        return httpx.Response(200, json={"organic_results": [{"title": "A", "link": "https://a.com/x"}]})

    assert [r.url for r in asyncio.run(_serp(flaky).search("q"))] == ["https://a.com/x"]
    assert len(calls) == 3

    calls.clear()
    with pytest.raises(SearchError, match="HTTP 401"):
        asyncio.run(_serp(lambda r: (calls.append(1), httpx.Response(401))[1]).search("q"))
    assert len(calls) == 1  # a bad key is not retried

    calls.clear()
    with pytest.raises(SearchError, match="ReadTimeout"):
        def always(req):
            calls.append(1)
            raise httpx.ReadTimeout("slow")

        asyncio.run(_serp(always).search("q"))
    assert len(calls) == 3


def test_serpapi_errors():
    with pytest.raises(SearchError, match="not set"):
        SerpApiProvider("")
    with pytest.raises(SearchError, match="HTTP 429"):
        asyncio.run(_serp(lambda r: httpx.Response(429)).search("q"))
    with pytest.raises(SearchError, match="quota"):
        asyncio.run(_serp(lambda r: httpx.Response(200, json={"error": "quota exceeded"})).search("q"))


# ---------- 2.2 / 2.4 fetch, parse, wall detection ----------
def test_parse_html_extracts_body_and_metadata():
    page = parse_html(ARTICLE, "https://news.example.com/a")
    assert page.title == "Acme raises $50M" and page.byline == "Jane Doe"
    assert page.published_date and page.published_date.year == 2026
    assert "Orbit Capital" in page.text and len(page.text) > 400


def test_paywall_teaser_is_skipped():
    teaser = "<html><body><article><p>Acme raised money. Subscribe to continue reading this article.</p></article></body></html>"
    with pytest.raises(SkippedSource, match="paywall"):
        parse_html(teaser, "https://paywalled.example.com/a")


def test_empty_page_is_skipped_without_wall_claim():
    with pytest.raises(SkippedSource, match="no readable article body"):
        parse_html("<html><body><p>hi</p></body></html>", "https://x.example.com/a")


@pytest.fixture
def open_network(monkeypatch):
    async def public(host):
        if host in ("127.0.0.1", "localhost"):
            raise RuntimeError("private")

    monkeypatch.setattr(netguard, "check_public_host", public)


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("status", [401, 402, 403, 451])
def test_wall_statuses_are_skipped(open_network, status):
    with pytest.raises(SkippedSource, match=f"HTTP {status}"):
        asyncio.run(download_html("https://news.example.com/a", _client(lambda r: httpx.Response(status))))


def test_other_failures_are_skipped(open_network):
    with pytest.raises(SkippedSource, match="HTTP 404"):
        asyncio.run(download_html("https://x.example.com/a", _client(lambda r: httpx.Response(404))))
    pdf = httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF")
    with pytest.raises(SkippedSource, match="not an HTML page"):
        asyncio.run(download_html("https://x.example.com/a", _client(lambda r: pdf)))
    with pytest.raises(SkippedSource, match="not an http"):
        asyncio.run(download_html("ftp://x.example.com/a", _client(lambda r: httpx.Response(200))))


def test_redirect_to_private_host_is_blocked(open_network):
    handler = lambda r: httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})  # noqa: E731
    with pytest.raises(SkippedSource, match="blocked"):
        asyncio.run(download_html("https://x.example.com/a", _client(handler)))


def test_fetch_article_end_to_end_and_logs_skips(open_network, caplog):
    ok = asyncio.run(fetch_mod.fetch_article("https://news.example.com/a", _client(lambda r: httpx.Response(200, text=ARTICLE))))
    assert ok.byline == "Jane Doe"
    with caplog.at_level("WARNING", logger="insightforge"):
        with pytest.raises(SkippedSource):
            asyncio.run(fetch_mod.fetch_article("https://pay.example.com/a", _client(lambda r: httpx.Response(402))))
    assert "skipped source" in caplog.text and "402" in caplog.text


# ---------- web search agent ----------
class FakeProvider:
    def __init__(self, hits):
        self.hits, self.queries = hits, []

    async def search(self, query, num=5):
        self.queries.append((query, num))
        return self.hits


def _fetcher(pages, errors=()):
    async def fetch(url):
        if url in errors:
            raise ValueError("boom")
        if url not in pages:
            raise SkippedSource(url, "paywall or login wall")
        return pages[url]

    return fetch


def _page(url, text="Body text. " * 400):
    return FetchedPage(url=url, title="Title " + url[-1], text=text, published_date=NOW, byline="J")


def test_web_agent_stores_sources_and_returns_references_only(tmp_path):
    hits = [SearchResult("A", "https://reuters.com/a", "snip a"), SearchResult("B", "https://wall.com/b"), SearchResult("C", "https://x.com/c"), SearchResult("D", "https://x.com/d")]
    provider = FakeProvider(hits)
    store = ScratchStore(tmp_path)
    agent = WebSearchAgent(provider, store, _fetcher({"https://reuters.com/a": _page("https://reuters.com/a"), "https://x.com/d": _page("https://x.com/d")}, errors={"https://x.com/c"}), max_chars=500)
    outcome = asyncio.run(agent.search_and_store(task()))
    assert [r.url for r in outcome.refs] == ["https://reuters.com/a", "https://x.com/d"]
    assert {u for u, _ in outcome.skipped} == {"https://wall.com/b", "https://x.com/c"}
    assert provider.queries == [("Funding Series B funding of Acme", 5)]
    rec = store.read_source(outcome.refs[0].source_id)
    assert rec["credibility_score"] > 0.7 and len(rec["body_text"]) == 500 and rec["byline"] == "J"
    assert len(outcome.refs[0].summary) <= 300 and "reuters.com" in outcome.refs[0].summary
    assert outcome.refs[0].model_dump().keys() >= {"source_id", "summary"}  # no body in the reference


def test_web_agent_fails_when_nothing_usable(tmp_path):
    agent = WebSearchAgent(FakeProvider([SearchResult("B", "https://wall.com/b")]), ScratchStore(tmp_path), _fetcher({}))
    with pytest.raises(RuntimeError, match="no usable web sources.*paywall"):
        asyncio.run(agent.run(task()))
    empty = WebSearchAgent(FakeProvider([]), ScratchStore(tmp_path), _fetcher({}))
    with pytest.raises(RuntimeError, match="no results"):
        asyncio.run(empty.run(task()))


def test_planner_query_is_used_when_present():
    t = SubTask(id="t", title="Pricing", scope="each vendor's prices", search_query="B2B payroll software Southeast Asia pricing 2025")
    assert build_query(t) == "B2B payroll software Southeast Asia pricing 2025"
    assert build_query(t.model_copy(update={"search_query": "   "})) == "Pricing each vendor's prices"


def test_query_is_built_from_the_subtask_only_and_bounded():
    q = build_query(task(title="T", scope="x" * 500))
    assert q.startswith("T xxx") and len(q) == 180


# ---------- 2.5 / 2.6 documents ----------
def test_merge_pieces_groups_by_heading_and_drops_tiny_chunks():
    pieces = [
        {"text": "Revenue grew twelve percent in 2025 across all segments.", "page_number": 1, "section_heading": "Revenue"},
        {"text": "Credit losses were stable at one point one percent of volume.", "page_number": 1, "section_heading": "Revenue"},
        {"text": "We plan to expand into three new markets next year.", "page_number": 2, "section_heading": "Strategy"},
        {"text": "tiny", "page_number": 2, "section_heading": "Strategy"},
    ]
    chunks = merge_pieces(pieces, "report.pdf")
    assert [c["section_heading"] for c in chunks] == ["Revenue", "Strategy"]
    assert "Credit losses" in chunks[0]["text"] and chunks[0]["page_number"] == 1
    assert chunks[1]["source_filename"] == "report.pdf" and "tiny" not in chunks[1]["text"]


def test_long_pieces_are_split():
    long = ". ".join(f"Sentence number {i} about the company results" for i in range(120)) + "."
    chunks = merge_pieces([{"text": long, "page_number": 3, "section_heading": "H"}], "r.pdf")
    assert len(chunks) > 1 and all(len(c["text"]) <= 2000 for c in chunks)


def _make_pdf(path, pages):
    pdf = FPDF()
    for heading, body in pages:
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 14)
        pdf.cell(0, 10, heading, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 11)
        pdf.multi_cell(0, 6, body)
    pdf.output(str(path))


def test_pypdf_fallback_extracts_pages_with_numbers(tmp_path):
    pdf = tmp_path / "1_Annual_Report.pdf"
    _make_pdf(pdf, [("Revenue", "Revenue grew 12 percent to 4.2 billion dollars in 2025."), ("Strategy", "We plan to enter three new markets and cut funding costs.")])
    chunks = extract_chunks(pdf, use_docling=False)
    assert {c["page_number"] for c in chunks} == {1, 2}
    assert all(c["source_filename"] == "Annual_Report.pdf" for c in chunks)
    assert any("4.2 billion" in c["text"] for c in chunks)


@pytest.fixture
def vectors():
    client = QdrantClient(":memory:")
    yield VectorStore(client, EMB)
    client.close()


CHUNKS = [
    {"text": f"Provider revenue grew strongly in region {i} during fiscal 2025 reporting.", "source_filename": "a.pdf", "page_number": i + 1, "section_heading": "Revenue"}
    for i in range(12)
] + [{"text": "Credit losses and provisions rose slightly in the consumer portfolio.", "source_filename": "a.pdf", "page_number": 20, "section_heading": "Risk"}]


def test_chunks_are_searchable_scoped_by_run_and_limited_to_top_k(vectors):
    vectors.add_chunks("run1", CHUNKS)
    vectors.add_chunks("run2", [{**CHUNKS[-1], "text": "Other run: credit losses doubled."}])
    hits = vectors.search_chunks("run1", "credit losses provisions consumer portfolio")
    assert len(hits) == 8 and hits[0].payload["section_heading"] == "Risk"
    assert all(h.payload["run_id"] == "run1" for h in hits)
    assert vectors.has_documents("run1", "a.pdf") and not vectors.has_documents("run3", "a.pdf")
    assert vectors.get_chunk(hits[0].id)["page_number"] == 20 and vectors.get_chunk("not-an-id") is None
    assert vectors.search_chunks("unknown-run", "anything") == []


def test_reindexing_is_idempotent(vectors):
    vectors.add_chunks("run1", CHUNKS)
    vectors.add_chunks("run1", CHUNKS)
    assert len(vectors.search_chunks("run1", "revenue", k=50)) == len(CHUNKS)


def test_document_reader_indexes_once_and_returns_chunk_references(tmp_path, vectors):
    folder = tmp_path / "run1"
    folder.mkdir()
    (folder / "1_a.pdf").write_bytes(b"%PDF-1.4")
    calls = []

    def extractor(path):
        calls.append(path.name)
        return CHUNKS

    agent = DocumentReaderAgent(vectors, tmp_path, extractor)
    refs = asyncio.run(agent.run("run1", task("document", "Credit losses", "provisions and credit losses")))
    asyncio.run(agent.run("run1", task("document", "Revenue", "revenue growth")))
    assert calls == ["1_a.pdf"]  # parallel/sequential document sub-tasks share one indexing pass
    assert len(refs) == 8 and all(r.kind == "document_chunk" for r in refs)
    assert refs[0].url.startswith("document://a.pdf/page-20/") and chunk_id_from_url(refs[0].url) == refs[0].source_id

    record = make_chunk_lookup(vectors)(refs[0])
    assert record["body_text"].startswith("Credit losses") and record["title"].startswith("a.pdf, p.20")
    assert 0 < record["credibility_score"] <= 1
    assert make_chunk_lookup(vectors)(refs[0].model_copy(update={"kind": "web"})) is None


def test_document_reader_without_documents_fails_clearly(tmp_path, vectors):
    agent = DocumentReaderAgent(vectors, tmp_path, lambda p: [])
    with pytest.raises(RuntimeError, match="no relevant passages"):
        asyncio.run(agent.run("run1", task("document")))


# ---------- 2.7 / 2.8 / 2.9 entities ----------
def _rec(name, type_, *obs):
    return {"key": entity_key(name, type_), "name": name, "type": type_,
            "observations": [{"source_id": s, "credibility": c, "attributes": a} for s, c, a in obs]}


def test_entity_key_ignores_case_and_company_suffixes():
    assert entity_key("Acme Inc.", "company") == entity_key("ACME", "company") == "company:acme"


def test_equivalent_figures_do_not_conflict():
    assert same_value("$50 million", "50M") and same_value("USD 1,200", "1200")
    assert not same_value("$50 million", "$80 million") and not same_value("Berlin", "Munich")
    assert same_value("Berlin", "berlin")


def test_conflicting_values_flag_the_entity_and_keep_the_most_credible(tmp_path):
    graph = build_graph(
        [
            _rec("Acme", "company", ("s1", 0.5, {"funding_total": "$50 million", "hq": "Berlin"}), ("s2", 0.9, {"funding_total": "$80 million", "hq": "berlin"})),
            _rec("Orbit Capital", "company", ("s1", 0.5, {"hq": "London"})),
        ],
        [],
    )
    acme = next(e for e in graph.entities if e.name == "Acme")
    assert acme.conflict and acme.attributes["funding_total"] == "$80 million"  # s2 is more credible
    assert acme.attributes["funding_total (alternate)"] == "$50 million"
    assert "hq (alternate)" not in acme.attributes  # same city, different case: no conflict
    assert acme.source_ids == ["s2", "s1"]
    assert not next(e for e in graph.entities if e.name == "Orbit Capital").conflict


def test_relationships_are_deduped_and_need_known_endpoints():
    a, b = entity_key("Acme", "company"), entity_key("Orbit", "company")
    rels = [
        {"source_key": b, "target_key": a, "relation": "invested in", "source_id": "s1"},
        {"source_key": b, "target_key": a, "relation": "Invested In", "source_id": "s2"},
        {"source_key": b, "target_key": "company:ghost", "relation": "owns", "source_id": "s1"},
    ]
    graph = build_graph([_rec("Acme", "company", ("s1", 0.5, {})), _rec("Orbit", "company", ("s1", 0.5, {}))], rels)
    assert len(graph.relationships) == 1 and graph.relationships[0].source_ids == ["s1", "s2"]


def test_entity_agent_stores_entities_and_detects_conflicts_across_subtasks(tmp_path, vectors):
    store = ScratchStore(tmp_path)
    pages = {
        "https://a.example.com/1": _page("https://a.example.com/1"),
        "https://b.example.com/2": _page("https://b.example.com/2"),
    }
    values = iter(["$50 million", "$80 million"])

    async def extractor(source):
        return ExtractedEntities(
            entities=[
                ExtractedEntity(name="Acme Inc", type="company", attributes=[Attribute(key="funding_total", value=next(values))]),
                ExtractedEntity(name="Orbit Capital", type="company"),
            ],
            relationships=[ExtractedRelationship(source="Orbit Capital", target="Acme", relation="invested in")],
        )

    def make(url):
        web = WebSearchAgent(FakeProvider([SearchResult("t", url)]), store, _fetcher(pages))
        return EntityExtractorAgent(web, vectors, extractor)

    refs1 = asyncio.run(make("https://a.example.com/1").run("run1", task("entity")))
    assert len(refs1) == 1  # the sources are returned as evidence too
    assert not any(e.conflict for e in load_graph(vectors, "run1").entities)
    asyncio.run(make("https://b.example.com/2").run("run1", task("entity", "Funding again")))
    graph = load_graph(vectors, "run1")
    acme = next(e for e in graph.entities if e.name.startswith("Acme"))
    assert acme.conflict and len(acme.source_ids) == 2
    assert len(graph.entities) == 2 and len(graph.relationships) == 1  # one relation, seen in two sources
    assert len(graph.relationships[0].source_ids) == 2
    assert load_graph(vectors, "other-run").entities == []


# ---------- routing ----------
def test_researcher_dispatch_by_kind_and_run_context():
    from graph.run_context import current_run_id

    calls = []

    class Agent:
        def __init__(self, name):
            self.name = name

        async def run(self, *args):
            calls.append((self.name, args[-1].researcher, args[:-1]))
            return []

    route = make_researcher(Agent("web"), Agent("docs"), Agent("entities"))

    async def go():
        current_run_id.set("run9")
        for kind in ("web", "document", "entity"):
            await route(task(kind))
        current_run_id.set(None)
        with pytest.raises(RuntimeError, match="run id"):
            await route(task("document"))

    asyncio.run(go())
    assert calls == [("web", "web", ()), ("docs", "document", ("run9",)), ("entities", "entity", ("run9",))]


def test_pipeline_sets_the_run_context_for_researchers(api):
    from graph.events import EventRecorder
    from graph.pipeline import PipelineDeps, run_pipeline
    from graph.run_context import current_run_id
    from schemas import ResearchBrief, SourceRef
    from services.runner import create_run
    from tests.fake_pipeline import TOPICS
    from agents.planner import PlannedSubTask, PlannerOutput, plan_brief
    from tests.planner_fakes import FakeAgent

    seen = []

    async def planner(brief, memory_block=""):
        plan = PlannerOutput(subtasks=[PlannedSubTask(title=t, scope=f"{t} scope") for t in TOPICS])
        return await plan_brief(brief, agent=FakeAgent(plan))

    async def researcher(t):
        seen.append(current_run_id.get())
        return [SourceRef(source_id="a" * 32, summary="x")]

    brief = ResearchBrief(text="Acme funding and EV market outlook")
    run_id = create_run(api.sf, brief, [])
    asyncio.run(run_pipeline(run_id, brief, PipelineDeps(recorder=EventRecorder(api.sf), planner=planner, researcher=researcher), ""))
    assert seen == [run_id] * 3


def test_entity_extraction_failure_does_not_fail_the_subtask(tmp_path, vectors, caplog):
    store = ScratchStore(tmp_path)
    pages = {"https://a.example.com/1": _page("https://a.example.com/1")}

    async def broken(source):
        raise RuntimeError("tool_use_failed")

    web = WebSearchAgent(FakeProvider([SearchResult("t", "https://a.example.com/1")]), store, _fetcher(pages))
    agent = EntityExtractorAgent(web, vectors, broken)
    with caplog.at_level("WARNING", logger="insightforge"):
        refs = asyncio.run(agent.run("run1", task("entity")))
    assert len(refs) == 1  # the fetched source is still evidence
    assert "entity extraction failed" in caplog.text
    assert load_graph(vectors, "run1").entities == []


def test_entity_role_has_its_own_model_setting(monkeypatch):
    from config import Settings

    assert Settings(llm_model="base", entity_model="special").model_for("entity") == "special"
    assert Settings(llm_model="base", entity_model=None).model_for("entity") == "base"

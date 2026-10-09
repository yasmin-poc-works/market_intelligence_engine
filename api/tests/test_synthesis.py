import asyncio

import pytest

from agents.synthesis import domain_of, make_synthesis_stage, resolve_ref, synthesize
from context.run_log import RunLog
from context.scratch_store import ScratchStore
from context.token_budget import count_tokens, split_by_tokens
from schemas import Confidence, Entity, EntityGraph, ResearchBrief, SourceRef

BRIEF = "Acme funding and market outlook"


def words(n):
    return " ".join(f"w{i}" for i in range(n))


class Setup:
    def __init__(self, tmp_path):
        self.store = ScratchStore(tmp_path)
        self.claims = {}  # title -> claims the fake extractor returns

    def add(self, title, url, cred, date, claims):
        sid = self.store.write_source(
            {"url": url, "title": title, "published_date": date, "body_text": "body", "credibility_score": cred}
        )
        self.claims[title] = claims
        return SourceRef(source_id=sid, summary=title)

    async def extractor(self, chunk):
        return self.claims[chunk["title"]]


def shrink(text, target):
    return " ".join(text.split()[: max(1, target // 3)])


def run_synth(setup, refs, **kw):
    kw.setdefault("summarizer", shrink)
    return asyncio.run(synthesize(refs, BRIEF, store=setup.store, extractor=setup.extractor, **kw))


@pytest.fixture
def s(tmp_path):
    return Setup(tmp_path)


def test_domain_of():
    assert domain_of("https://www.Reuters.com/a/b?x=1") == "reuters.com"
    assert domain_of("http://news.site.org:8080/x") == "news.site.org"


def test_resolve_ref_missing_and_non_web(s):
    assert resolve_ref(SourceRef(source_id="0" * 32, summary="x"), s.store) is None
    chunk = SourceRef(source_id="c1", summary="x", kind="document_chunk")
    assert resolve_ref(chunk, s.store) is None
    assert resolve_ref(chunk, s.store, lambda r: {"url": "doc.pdf"}) == {"url": "doc.pdf"}


def test_unresolved_refs_are_reported_not_fatal(s):
    ok = s.add("A", "https://a.com/1", 0.9, "2026-05-01", ["Acme raised 50 million dollars in Series B funding"])
    bad = SourceRef(source_id="f" * 32, summary="gone")
    res = run_synth(s, [ok, bad])
    assert res.unresolved == ["f" * 32] and len(res.bundle.items) == 1


def test_dedupe_across_independent_sources_gives_high_confidence(s):
    r1 = s.add("A", "https://a.com/1", 0.9, "2026-05-01", ["Acme raised 50 million dollars in Series B funding"])
    r2 = s.add("B", "https://b.org/2", 0.6, "2026-05-03", ["Acme raised 50 million dollars in its Series B funding round"])
    item = run_synth(s, [r1, r2]).bundle.items[0]
    assert len(run_synth(s, [r1, r2]).bundle.items) == 1
    assert item.confidence == Confidence.HIGH
    assert set(item.source_ids) == {r1.source_id, r2.source_id}
    assert item.credibility_score == 0.9
    assert item.claim == "Acme raised 50 million dollars in Series B funding"  # highest-credibility phrasing


def test_same_domain_is_not_independent(s):
    r1 = s.add("A", "https://a.com/1", 0.8, "2026-05-01", ["Acme raised 50 million dollars in Series B funding"])
    r2 = s.add("B", "https://www.a.com/2", 0.8, "2026-05-02", ["Acme raised 50 million dollars in Series B funding"])
    item = run_synth(s, [r1, r2]).bundle.items[0]
    assert item.confidence == Confidence.MEDIUM


def test_single_source_confidence_by_credibility(s):
    hi = s.add("A", "https://a.com/1", 0.85, "2026-05-01", ["Beta Corp opened a new factory in Texas this year"])
    lo = s.add("B", "https://b.com/1", 0.3, "2026-05-01", ["Gamma Inc plans layoffs across several European offices"])
    conf = {i.claim.split()[0]: i.confidence for i in run_synth(s, [hi, lo]).bundle.items}
    assert conf == {"Beta": Confidence.MEDIUM, "Gamma": Confidence.LOW}


def test_conflicting_entity_forces_low(s):
    r = s.add("A", "https://a.com/1", 0.95, "2026-05-01", ["Acme revenue was 10 billion dollars"])
    graph = EntityGraph(entities=[Entity(id="1", name="Acme", type="company", conflict=True)])
    item = run_synth(s, [r], entity_graph=graph).bundle.items[0]
    assert item.confidence == Confidence.LOW and item.conflict


def test_same_figures_about_same_entity_are_duplicates(s):
    r1 = s.add("A", "https://a.com/1", 0.9, "2026-05-01", ["Acme revenue reached 10 billion dollars"])
    r2 = s.add("B", "https://b.com/1", 0.9, "2026-05-01", ["The Acme top line hit 10 billion"])
    graph = EntityGraph(entities=[Entity(id="1", name="Acme", type="company")])
    assert len(run_synth(s, [r1, r2], entity_graph=graph).bundle.items) == 1


def test_ranking_recency_then_credibility_then_relevance(s):
    old = s.add("Old", "https://a.com/1", 0.99, "2025-01-01", ["Old claim about something entirely different here"])
    new_lo = s.add("NewLo", "https://b.com/1", 0.4, "2026-06-01", ["Newer lower credibility statement regarding markets"])
    new_hi = s.add("NewHi", "https://c.com/1", 0.9, "2026-06-01", ["Newer higher credibility statement regarding funding"])
    items = run_synth(s, [old, new_lo, new_hi]).bundle.items
    assert [i.id for i in items] == ["E1", "E2", "E3"]
    assert [i.claim.split()[0] for i in items] == ["Newer", "Newer", "Old"]
    assert items[0].credibility_score == 0.9 and items[1].credibility_score == 0.4


def test_missing_dates_rank_last(s):
    dated = s.add("D", "https://a.com/1", 0.2, "2024-01-01", ["Dated claim about the quarterly results published"])
    undated = s.add("U", "https://b.com/1", 0.99, None, ["Undated claim concerning an unrelated product launch"])
    items = run_synth(s, [undated, dated]).bundle.items
    assert items[0].claim.startswith("Dated")


def test_budget_compresses_lowest_ranked_first_and_logs(s):
    refs = [
        s.add(f"S{i}", f"https://s{i}.com/x", 0.9, f"2026-0{i + 1}-01", [f"Claim{i} " + " ".join(f"zq{i}w{j}" for j in range(120))]) for i in range(5)
    ]
    log = RunLog("r1")
    res = run_synth(s, refs, budget=300, run_log=log)
    assert res.bundle.compressed and res.bundle.token_count <= 300
    rec = log.records[0]
    assert rec.boundary == "synthesis->writer" and rec.tokens_in > rec.tokens_out and rec.cuts
    ids = [i.id for i in res.bundle.items]
    assert ids == sorted(ids)
    first = res.bundle.items[0]  # best-ranked keeps its full text
    assert count_tokens(first.claim) > 100


def test_under_budget_not_compressed(s):
    r = s.add("A", "https://a.com/1", 0.9, "2026-05-01", ["Short claim about Acme funding totals"])
    res = run_synth(s, [r], budget=6000)
    assert not res.bundle.compressed


def test_sources_metadata_only_for_used_sources_without_bodies(s):
    r = s.add("A", "https://www.a.com/1", 0.9, "2026-05-01", ["Short claim about Acme funding totals"])
    unused = s.add("Z", "https://z.com/1", 0.9, "2026-05-01", [])
    res = run_synth(s, [r, unused])
    assert set(res.sources) == {r.source_id}
    meta = res.sources[r.source_id]
    assert meta["domain"] == "a.com" and "body_text" not in meta


def test_long_body_is_chunked_for_extraction_without_loss(s):
    pieces = split_by_tokens("\n".join(words(40) for _ in range(10)), 100)
    assert len(pieces) > 1
    assert "\n".join(pieces).count("w0") == 10
    assert all(count_tokens(p) <= 100 for p in pieces)


def test_oversized_single_line_is_split_on_words():
    pieces = split_by_tokens(words(500), 50)
    assert len(pieces) > 1 and all(count_tokens(p) <= 50 for p in pieces)
    assert " ".join(pieces).split() == words(500).split()


def test_stage_returns_evidence_and_sources_and_fails_when_empty(s):
    r = s.add("A", "https://a.com/1", 0.9, "2026-05-01", ["Short claim about Acme funding totals"])
    stage = make_synthesis_stage(s.extractor, shrink, store=s.store)
    state = {"run_id": "r", "brief": ResearchBrief(text=BRIEF), "refs": [r]}
    out = asyncio.run(stage(state))
    assert out["evidence"].items and r.source_id in out["sources"]
    with pytest.raises(RuntimeError):
        asyncio.run(stage({**state, "refs": []}))

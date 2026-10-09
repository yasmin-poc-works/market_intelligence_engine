import asyncio
import hashlib
import math
import re

from agents.fact_checker import (
    best_similarity,
    cited_ids,
    fact_check,
    make_fact_check_stage,
    sample_size,
)
from agents.writer import DraftSection, ReportDraft, render_report
from schemas import Confidence, EvidenceBundle, EvidenceItem


class HashEmbedder:
    """Deterministic bag-of-words embedding: similar wording gives high cosine."""

    def embed(self, texts):
        out = []
        for t in texts:
            v = [0.0] * 256
            for w in re.findall(r"[a-z0-9]+", t.lower()):
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 256] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out


EMB = HashEmbedder()
CLAIMS = [f"Company{i} reported record quarterly revenue growth in region{i} during the year" for i in range(8)]
SOURCES = {f"s{i}": {"url": f"https://site{i}.com/a", "domain": f"site{i}.com", "published_date": None, "credibility_score": 0.8} for i in range(8)}
EVIDENCE = EvidenceBundle(
    items=[
        EvidenceItem(id=f"E{i + 1}", claim=c, source_ids=[f"s{i}"], confidence=Confidence.MEDIUM, credibility_score=0.8)
        for i, c in enumerate(CLAIMS)
    ]
)
DRAFT = ReportDraft(
    title="T",
    executive_summary="Overview [E1].",
    sections=[DraftSection(heading="H", body=" ".join(f"{c} [E{i + 1}]." for i, c in enumerate(CLAIMS)))],
)
PAGES = {s["url"]: f"Intro text that is long enough to be a sentence here. {CLAIMS[i]}. Closing remarks follow in this sentence." for i, s in enumerate(SOURCES.values())}


def run(coro):
    return asyncio.run(coro)


def test_sample_size_is_20_percent_with_minimum_five():
    assert [sample_size(n) for n in (0, 3, 5, 10, 25, 50, 100)] == [0, 3, 5, 5, 5, 10, 20]


def test_cited_ids_unique_in_order():
    assert cited_ids(DRAFT) == [f"E{i}" for i in range(1, 9)]


def test_similarity_high_for_matching_passage_low_for_unrelated():
    page = PAGES["https://site0.com/a"]
    assert best_similarity(CLAIMS[0], [page], EMB) >= 0.85
    other = "Completely different subject about gardening tools and watering schedules for tomatoes."
    assert best_similarity(CLAIMS[0], [other], EMB) < 0.5
    assert best_similarity(CLAIMS[0], [], EMB) == 0.0


async def refetch_ok(url):
    return PAGES[url]


def test_all_supported_claims_pass_and_sample_is_five_of_eight():
    summary, failed = run(fact_check(DRAFT, EVIDENCE, SOURCES, "run1", refetch=refetch_ok, embedder=EMB))
    assert summary.total_checked == 5 and summary.passed == 5 and summary.failed == 0
    assert summary.pass_rate == 1.0 and summary.unverified_claims == [] and not failed


def test_sampling_is_deterministic_per_run():
    a = run(fact_check(DRAFT, EVIDENCE, SOURCES, "same", refetch=refetch_ok, embedder=EMB))
    b = run(fact_check(DRAFT, EVIDENCE, SOURCES, "same", refetch=refetch_ok, embedder=EMB))
    assert a[0] == b[0]


def test_unsupported_and_unreachable_sources_fail():
    async def refetch(url):
        if "site0" in url:
            return "Nothing relevant here at all, just gardening tips and watering schedules for tomatoes."
        if "site1" in url:
            raise ConnectionError("down")
        return PAGES[url]

    # 8 claims, sample 5 -> pick a seed where both bad sources are sampled; check all seeds consistent
    seen_fail = set()
    for seed in range(30):
        summary, failed = run(fact_check(DRAFT, EVIDENCE, SOURCES, f"seed{seed}", refetch=refetch, embedder=EMB))
        assert failed <= {"E1", "E2"}
        assert summary.failed == len(failed) == len(summary.unverified_claims)
        assert summary.passed + summary.failed == summary.total_checked
        assert summary.pass_rate == summary.passed / summary.total_checked
        seen_fail |= failed
    assert seen_fail == {"E1", "E2"}


def test_threshold_is_respected():
    summary, failed = run(
        fact_check(DRAFT, EVIDENCE, SOURCES, "r", refetch=refetch_ok, embedder=EMB, threshold=1.01)
    )
    assert summary.failed == summary.total_checked


def test_claim_with_two_sources_passes_if_either_supports_it():
    ev = EvidenceBundle(items=[EVIDENCE.items[0].model_copy(update={"source_ids": ["s0", "s1"]})])
    draft = ReportDraft(title="T", executive_summary="", sections=[DraftSection(heading="H", body="x [E1].")])

    async def refetch(url):
        return "unrelated gardening text about tomatoes and soil preparation methods" if "site1" in url else PAGES[url]

    summary, _ = run(fact_check(draft, ev, SOURCES, "r", refetch=refetch, embedder=EMB))
    assert summary.passed == 1


def test_stage_marks_unverified_in_report_and_keeps_report_id():
    bad_url = "https://site0.com/a"

    async def refetch(url):
        return "unrelated gardening text about tomatoes and soil preparation methods" if url == bad_url else PAGES[url]

    base = render_report(DRAFT, EVIDENCE, SOURCES, "run1")
    stage = make_fact_check_stage(EMB, refetch)
    state = {"draft": DRAFT, "evidence": EVIDENCE, "sources": SOURCES, "run_id": "run1", "report": base}
    # sampling is seeded by run id; use the first seed whose sample includes the bad claim
    for seed in range(50):
        state["run_id"] = f"run{seed}"
        out = run(stage(state))
        if out["fact_check"].failed:
            break
    assert out["fact_check"].failed == 1
    report = out["report"]
    assert report.id == base.id and report.created_at == base.created_at
    assert out["fact_check"].unverified_claims == [CLAIMS[0]]
    body = report.sections[0].body
    assert "[UNVERIFIED]" in body.split("Company1")[0]  # the Company0 sentence is flagged
    assert body.count("[UNVERIFIED]") == 1  # and nothing else in the section
    assert "## Fact-check" in report.markdown and "sampled claims verified" in report.markdown

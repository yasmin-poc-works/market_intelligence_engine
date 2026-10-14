import json
import sys

import pytest

from config import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "evaluation"))

import metrics  # noqa: E402
import run_eval  # noqa: E402

SOURCES = {
    "s1": {"domain": "funding.example.com", "published_date": "2026-05-01T00:00:00+00:00", "credibility_score": 0.85},
    "s2": {"domain": "market.example.com", "published_date": None, "credibility_score": 0.6},
}
EVIDENCE = [
    {"id": "E1", "claim": "Acme raised $50 million in Series B funding led by Orbit Capital", "source_ids": ["s1"]},
    {"id": "E2", "claim": "The electric vehicle market grew 12% last year across Europe", "source_ids": ["s2"]},
]
CITE1 = "[Source: funding.example.com, 2026-05-01, credibility: 0.85]"
CITE2 = "[Source: market.example.com, n.d., credibility: 0.60]"


def report(summary, *bodies, gaps="- none"):
    return {
        "executive_summary": summary,
        "sections": [{"heading": f"S{i}", "body": b, "confidence": "HIGH"} for i, b in enumerate(bodies)],
        "gaps_and_limitations": gaps,
    }


def test_supported_and_unsupported_citations():
    r = report(
        f"Acme raised $50 million in Series B funding led by Orbit Capital {CITE1}.",
        f"The electric vehicle market grew 40% last year across Europe {CITE2}.",  # wrong figure
    )
    out = metrics.citation_metrics(r, SOURCES, EVIDENCE)
    assert out["cited_sentences"] == 2 and out["resolvable_citations"] == 2
    assert out["citation_accuracy"] == 0.5
    assert "40%" in out["unsupported_examples"][0]


def test_uncited_and_empty_sections_are_not_counted_as_accurate():
    r = report(
        "Funding is strong this quarter for several companies.",
        "_No sufficiently supported evidence was found for this section._",
    )
    out = metrics.citation_metrics(r, SOURCES, EVIDENCE)
    assert out["claim_sentences"] == 1 and out["cited_sentences"] == 0
    assert out["citation_coverage"] == 0 and out["citation_accuracy"] == 0


def test_unresolvable_citation_is_unsupported():
    r = report(f"Acme raised $50 million in Series B funding [Source: ghost.com, n.d., credibility: 0.5].")
    out = metrics.citation_metrics(r, SOURCES, EVIDENCE)
    assert out["resolvable_citations"] == 0 and out["citation_accuracy"] == 0


def test_coverage_handles_alternatives_and_parentheses():
    cov, missing = metrics.coverage("The CAGR is high and capacity reached 100 GWh.", ["CAGR or growth rate", "capacity (GWh)", "tariffs"])
    assert missing == ["tariffs"] and cov == pytest.approx(2 / 3)
    assert metrics.coverage("x", [])[0] == 1.0


def test_synthesis_quality_rewards_structure_and_penalizes_gaps():
    spec = {"must_cover": ["funding", "pricing"]}
    good = report("Short summary about funding and pricing.", "Body one.", "Body two.", "Body three.")
    q = metrics.synthesis_quality(spec, good, SOURCES)
    assert q["structure"] == 1.0 and q["coverage"] == 1.0 and 0.8 < q["score"] <= 1.0
    long_summary = report("word " * 200, "Body.", "Body.", "Body.")
    assert metrics.synthesis_quality(spec, long_summary, SOURCES)["structure"] == 0.5
    two_sections = report("Summary.", "Body.", "Body.", gaps="")
    assert metrics.synthesis_quality(spec, two_sections, SOURCES)["structure"] == 0.5  # summary only
    assert metrics.synthesis_quality(spec, report("", "x"), {})["score"] < 0.5


def test_context_summary_counts_cuts():
    log = [
        {"boundary": "a", "tokens_in": 9000, "tokens_out": 4000, "budget": 4000,
         "cuts": [{"item_id": "E3", "action": "compressed", "reason": "r", "tokens_before": 3000, "tokens_after": 500}]},
        {"boundary": "b", "tokens_in": 100, "tokens_out": 100, "budget": None, "cuts": []},
    ]
    assert metrics.context_summary(log) == {"boundaries": 2, "boundaries_over_budget": 1, "cuts": 1, "tokens_saved": 2500}


def test_evaluate_a_report_from_the_faked_pipeline(api):
    report_id = api.completed_report_id()
    detail = api.client.get(f"/reports/{report_id}").json()
    spec = {"must_cover": ["funding", "electric vehicle", "battery"]}
    out = metrics.evaluate_report(spec, {k: detail[k] for k in ("report", "fact_check", "sources", "evidence")})
    assert out["citations"]["citation_accuracy"] == 1.0  # the fake pipeline cites exactly what it extracted
    assert out["fact_check"]["pass_rate"] is not None
    assert out["quality"]["coverage"] == 1.0 and out["quality"]["section_count"] == 3


def test_markdown_report_renders_results_and_skips():
    result = {
        "id": "x", "title": "T", "run_id": "r1", "status": "COMPLETE", "error": None, "seconds": 42,
        "fact_check": {"pass_rate": 0.9, "checked": 5, "failed": 0, "meets_target": True},
        "citations": {"citation_accuracy": 0.75, "meets_target": False, "cited_sentences": 6, "claim_sentences": 8,
                      "citations": 7, "resolvable_citations": 7, "citation_coverage": 0.75, "unsupported_examples": ["bad claim"]},
        "quality": {"score": 0.8, "structure": 1, "executive_summary_words": 90, "section_count": 4, "coverage": 0.8,
                    "missing_topics": ["pricing"], "non_low_confidence_sections": "3/4", "source_domains": 5},
        "context": {"boundaries": 3, "boundaries_over_budget": 1, "cuts": 2, "tokens_saved": 1200},
    }
    failed = {"id": "y", "title": "T2", "run_id": "r2", "status": "FAILED", "error": "boom", "seconds": 5,
              "context": {"boundaries": 0, "boundaries_over_budget": 0, "cuts": 0, "tokens_saved": 0}}
    md = run_eval.render_markdown([result, failed], ["z (needs --pdf files)"])
    assert "| x | COMPLETE | 90% (5 checked) ✓ | 75% ✗ | 6/8 | 0.80 | 42s |" in md
    assert "| y | FAILED | - |" in md and "Error: `boom`" in md
    assert "missing: pricing" in md and "bad claim" in md and "Not run: z" in md
    json.dumps(result)


def test_load_specs_filters_and_rejects_unknown_ids():
    assert [s["id"] for s in run_eval.load_specs(["payroll-sea"])] == ["payroll-sea"]
    assert len(run_eval.load_specs(None)) == 5
    with pytest.raises(SystemExit):
        run_eval.load_specs(["nope"])

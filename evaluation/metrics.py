"""Scoring for the evaluation run. Pure functions over a stored report, so they are unit-tested
without any LLM or network.

Three things are measured per brief (targets from the assignment where it sets one):

* fact-check pass rate: taken from the Fact-Checker's own summary (target >= 80%)
* citation accuracy: of the factual sentences that carry a citation, the share whose cited sources
  really contain what the sentence says (same figures, most of the wording); sentences that only
  say the evidence is missing are counted apart
* synthesis quality: automated proxies (structure, must-cover coverage, confidence mix, source
  diversity). They are proxies, not a human judgement; the report adds manual notes.
"""
import re
from typing import Any

PASS_RATE_TARGET = 0.8
CITATION_ACCURACY_TARGET = 0.8
SUPPORT_OVERLAP = 0.6  # share of a sentence's words that must appear in the cited claims
EXEC_SUMMARY_MAX_WORDS = 150

_CITATION = re.compile(r"\[Source: ([^,\]]+), ([^,\]]+), credibility: ([\d.]+)\]")
_MARKS = re.compile(r"\[(?:Source:[^\]]*|UNVERIFIED|E\d+)\]")
_STOP = set("a an the of in on at to for and or is are was were be been by with as from that this it its has have had".split())
_NO_EVIDENCE = "No sufficiently supported evidence"
# Sentences that honestly say the evidence is missing ("the evidence lacks payroll-specific data") carry a
# citation but are not factual claims, so they are reported separately instead of being scored as claims.
_GAP_STATEMENT = re.compile(
    r"\b(?:evidence|source material|sources?|material|data)\b[^.]{0,60}?\b(?:does not|do not|lacks?|is limited|are limited|contains? no|provides? no)\b"
    r"|\b(?:does not|do not) (?:provide|contain|include|identify|disclose)\b|\bprovides? no\b|\bno (?:payroll|specific|sufficient)\b",
    re.IGNORECASE,
)


def is_gap_statement(sentence: str) -> bool:
    return bool(_GAP_STATEMENT.search(_MARKS.sub("", sentence)))


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP}


def figures(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in re.findall(r"\d[\d,]*\.?\d*", text)}


def sentences(text: str) -> list[str]:
    out = []
    for line in text.split("\n"):
        line = line.strip().lstrip("-* ").strip()
        if not line:
            continue
        for part in re.split(r"(?<=[.!?\]])\s+(?=[A-Z0-9\[])", line):
            if not part.strip():
                continue
            if out and not _MARKS.sub("", part).strip(" .!?"):  # citations follow their sentence's full stop
                out[-1] += " " + part.strip()
            else:
                out.append(part.strip())
    return out


def body_sentences(report: dict[str, Any]) -> list[str]:
    """Claim sentences of the report: summary and section bodies (not the gaps list)."""
    texts = [report.get("executive_summary", "")] + [s["body"] for s in report.get("sections", [])]
    return [s for t in texts for s in sentences(t) if _NO_EVIDENCE not in s and len(words(_MARKS.sub("", s))) >= 3]


def citation_metrics(report: dict[str, Any], sources: dict[str, dict], evidence: list[dict]) -> dict[str, Any]:
    claims = body_sentences(report)
    by_source: dict[str, list[str]] = {}
    for item in evidence:
        for sid in item["source_ids"]:
            by_source.setdefault(sid, []).append(item["claim"])

    def resolve(domain: str, date: str) -> list[str]:
        exact = [sid for sid, m in sources.items()
                 if m.get("domain") == domain and ((m.get("published_date") or "")[:10] or "n.d.") == date]
        return exact or [sid for sid, m in sources.items() if m.get("domain") == domain]

    cited = resolvable = total_citations = supported = gap_statements = 0
    unsupported: list[str] = []
    for sentence in claims:
        cites = _CITATION.findall(sentence)
        if not cites:
            continue
        if is_gap_statement(sentence):
            gap_statements += 1
            continue
        cited += 1
        total_citations += len(cites)
        ids = [sid for domain, date, _ in cites for sid in resolve(domain, date)]
        resolvable += sum(1 for domain, date, _ in cites if resolve(domain, date))
        text = _MARKS.sub("", sentence)
        support = " ".join(c for sid in set(ids) for c in by_source.get(sid, []))
        sw = words(text)
        ok = bool(ids) and bool(sw) and len(sw & words(support)) / len(sw) >= SUPPORT_OVERLAP and figures(text) <= figures(support)
        supported += ok
        if not ok:
            unsupported.append(text[:160])
    return {
        "claim_sentences": len(claims),
        "cited_sentences": cited,
        "gap_statements": gap_statements,
        "citation_coverage": round((cited + gap_statements) / len(claims), 3) if claims else 0.0,
        "citations": total_citations,
        "resolvable_citations": resolvable,
        "citation_accuracy": round(supported / cited, 3) if cited else 0.0,
        "unsupported_examples": unsupported[:3],
    }


def _alternatives(term: str) -> list[str]:
    """'CAGR or growth rate' -> two alternatives; '(GWh)' style parentheses are dropped."""
    cleaned = re.sub(r"\(.*?\)", " ", term)
    return [a.strip().lower() for a in re.split(r"\s+or\s+", cleaned) if a.strip()]


def coverage(text: str, must_cover: list[str]) -> tuple[float, list[str]]:
    low = text.lower()
    missing = [t for t in must_cover if not any(a in low for a in _alternatives(t))]
    return (1 - len(missing) / len(must_cover) if must_cover else 1.0), missing


def synthesis_quality(
    spec: dict[str, Any], report: dict[str, Any], sources: dict[str, dict], limits: tuple[int, int] = (3, 6)
) -> dict[str, Any]:
    sections = report.get("sections", [])
    summary_words = len(_MARKS.sub("", report.get("executive_summary", "")).split())
    has_summary = summary_words > 0
    structure = (
        0.5 * (has_summary and summary_words <= EXEC_SUMMARY_MAX_WORDS)
        + 0.25 * (limits[0] <= len(sections) <= limits[1])
        + 0.25 * bool(report.get("gaps_and_limitations"))
    )
    full_text = "\n".join([report.get("executive_summary", "")] + [s["heading"] + " " + s["body"] for s in sections])
    cov, missing = coverage(full_text, spec.get("must_cover", []))
    non_low = sum(1 for s in sections if s["confidence"] != "LOW")
    confidence_mix = non_low / len(sections) if sections else 0.0
    domains = {m.get("domain") for m in sources.values() if m.get("domain")}
    diversity = min(1.0, len(domains) / 4)
    score = 0.3 * structure + 0.35 * cov + 0.2 * confidence_mix + 0.15 * diversity
    return {
        "score": round(score, 3),
        "structure": round(structure, 3),
        "executive_summary_words": summary_words,
        "section_count": len(sections),
        "coverage": round(cov, 3),
        "missing_topics": missing,
        "non_low_confidence_sections": f"{non_low}/{len(sections)}",
        "source_domains": len(domains),
    }


def evaluate_report(spec: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    """data is the stored report row data: report, fact_check, sources, evidence."""
    report = data.get("report") or {}
    sources = data.get("sources") or {}
    fact_check = data.get("fact_check") or {}
    citations = citation_metrics(report, sources, data.get("evidence") or [])
    quality = synthesis_quality(spec, report, sources)
    pass_rate = fact_check.get("pass_rate")
    return {
        "fact_check": {
            "pass_rate": pass_rate,
            "checked": fact_check.get("total_checked"),
            "failed": fact_check.get("failed"),
            "meets_target": pass_rate is not None and pass_rate >= PASS_RATE_TARGET,
        },
        "citations": {**citations, "meets_target": citations["citation_accuracy"] >= CITATION_ACCURACY_TARGET},
        "quality": quality,
    }


def context_summary(run_log: list[dict[str, Any]]) -> dict[str, Any]:
    """Context-engineering facts from the run log: where the budget forced cuts."""
    cuts = [c for rec in run_log for c in rec.get("cuts", [])]
    over = [r for r in run_log if r.get("budget") and r["tokens_in"] > r["budget"]]
    return {
        "boundaries": len(run_log),
        "boundaries_over_budget": len(over),
        "cuts": len(cuts),
        "tokens_saved": sum(max(0, c["tokens_before"] - c["tokens_after"]) for c in cuts),
    }

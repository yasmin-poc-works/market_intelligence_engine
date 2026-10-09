"""Writer: turns an EvidenceBundle into a cited report.

The LLM cites evidence by id ("[E3]"); code verifies every statement is grounded in the
cited evidence (retrying with explicit constraints on violations) and only then resolves
the markers into "[Source: {domain}, {date}, credibility: {score}]" citations.
"""
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from llm import call_llm, make_chat_model
from schemas import (
    Confidence,
    EvidenceBundle,
    EvidenceItem,
    FactCheckSummary,
    Report,
    ReportSection,
    TaskGraph,
)

MAX_SUMMARY_WORDS = 150
MAX_RETRIES = 2
NO_SUPPORT = "_No sufficiently supported evidence was found for this section._"
UNVERIFIED = "[UNVERIFIED]"
NL = chr(10)

_MARKER = re.compile(r"\[(E\d+)\]")
_CONF_ORDER = {Confidence.LOW: 0, Confidence.MEDIUM: 1, Confidence.HIGH: 2}


class DraftSection(BaseModel):
    heading: str
    body: str


class WriterOutput(BaseModel):
    executive_summary: str
    sections: list[DraftSection]
    gaps_and_limitations: str = ""


class ReportDraft(BaseModel):
    """Grounded text that still carries [E#] markers; rendered into the final Report."""

    title: str
    executive_summary: str
    sections: list[DraftSection]
    gaps: list[str] = Field(default_factory=list)


Generate = Callable[[str], Awaitable[WriterOutput]]


# ---------- sentence handling and grounding ----------
def split_sentences(text: str) -> list[str]:
    """Sentences per line; a fragment holding only markers is glued to the previous sentence."""
    out: list[str] = []
    for line in text.split(NL):
        if not line.strip():
            continue
        parts = re.split(r"(?<=[.!?])\s+", line.strip())
        for part in parts:
            if out and _MARKER.sub("", part).strip(" .!?") == "":
                out[-1] += " " + part
            else:
                out.append(part)
    return out


def _numbers(text: str) -> set[str]:
    """Significant figures: percentages, decimals, thousands separators, or 2+ digits."""
    found = set()
    for n in re.findall(r"\d[\d,]*\.?\d*%?", text):
        norm = n.replace(",", "").rstrip(".")
        if "%" in norm or "." in norm or len(norm) >= 2:
            found.add(norm)
    return found


def check_sentence(sentence: str, evidence: dict[str, EvidenceItem]) -> str | None:
    """None if grounded, else the reason it is not."""
    ids = _MARKER.findall(sentence)
    if not ids:
        return "no [E#] citation"
    unknown = [i for i in ids if i not in evidence]
    if unknown:
        return f"cites unknown evidence {', '.join(unknown)}"
    cited = " ".join(evidence[i].claim for i in ids)
    missing = _numbers(_MARKER.sub("", sentence)) - _numbers(cited)
    if missing:
        return f"figures not found in the cited evidence: {', '.join(sorted(missing))}"
    return None


def find_violations(out: WriterOutput, evidence: dict[str, EvidenceItem], expected_sections: int) -> list[str]:
    problems: list[str] = []
    words = len(_MARKER.sub("", out.executive_summary).split())
    if words > MAX_SUMMARY_WORDS:
        problems.append(f"executive summary has {words} words; the limit is {MAX_SUMMARY_WORDS}")
    if len(out.sections) != expected_sections:
        problems.append(f"expected exactly {expected_sections} sections, got {len(out.sections)}")
    texts = [("executive summary", out.executive_summary)] + [(s.heading, s.body) for s in out.sections]
    for where, text in texts:
        for sentence in split_sentences(text):
            reason = check_sentence(sentence, evidence)
            if reason:
                problems.append(f'In "{where}": "{sentence[:120]}" -> {reason}')
    return problems


def drop_unsupported(text: str, evidence: dict[str, EvidenceItem]) -> tuple[str, int]:
    kept, dropped = [], 0
    for sentence in split_sentences(text):
        if check_sentence(sentence, evidence):
            dropped += 1
        else:
            kept.append(sentence)
    return " ".join(kept), dropped


def _trim_summary(text: str) -> str:
    """Whole sentences only, from the start, until the word limit is respected."""
    kept: list[str] = []
    for s in split_sentences(text):
        if len(_MARKER.sub("", " ".join(kept + [s])).split()) > MAX_SUMMARY_WORDS:
            break
        kept.append(s)
    return " ".join(kept)


# ---------- generation ----------
def build_prompt(brief: str, evidence: EvidenceBundle, headings: list[str], violations: list[str]) -> str:
    lines = [f"[{e.id}] ({e.confidence.value}) {e.claim}" for e in evidence.items]
    prompt = (
        f"Write a market intelligence report for this brief:\n{brief}\n\n"
        "EVIDENCE (the only facts you may use):\n" + NL.join(lines) + "\n\n"
        f"Write an executive summary of at most {MAX_SUMMARY_WORDS} words and exactly these sections, "
        "in this order, using these exact headings:\n"
        + NL.join(f"- {h}" for h in headings)
        + "\n\nRules:\n"
        "- Every sentence must cite its evidence by id, placed before the final period, e.g. "
        '"Revenue grew 12% [E3]." Several ids are fine: "[E1][E4]".\n'
        "- Use only facts and figures present in the cited evidence. Never add outside knowledge.\n"
        "- If a section has no supporting evidence, say so briefly without making claims.\n"
        "- gaps_and_limitations: what the evidence does not cover. No citations needed there."
    )
    if violations:
        prompt += (
            "\n\nYour previous draft broke these rules. Fix every one and do not repeat them:\n- "
            + (NL + "- ").join(violations[:15])
        )
    return prompt


def make_llm_generator() -> Generate:
    model = make_chat_model("writer").with_structured_output(WriterOutput)

    async def generate(prompt: str) -> WriterOutput:
        out = await call_llm(lambda: model.ainvoke(prompt))
        return out if isinstance(out, WriterOutput) else WriterOutput.model_validate(out)

    return generate


async def write_report(
    brief_text: str,
    evidence: EvidenceBundle,
    task_graph: TaskGraph,
    subtask_status: dict[str, str],
    generate: Generate,
) -> ReportDraft:
    by_id = {e.id: e for e in evidence.items}
    active = [t for t in task_graph.subtasks if subtask_status.get(t.id) != "failed"]
    headings = [t.title for t in active]
    gaps: list[str] = [
        f'Research on "{t.title}" failed, so this topic is not covered.'
        for t in task_graph.subtasks
        if subtask_status.get(t.id) == "failed"
    ]

    violations: list[str] = []
    for _ in range(MAX_RETRIES + 1):  # first draft plus retries with explicit constraints
        out = await generate(build_prompt(brief_text, evidence, headings, violations))
        violations = find_violations(out, by_id, len(headings))
        if not violations:
            break

    summary, dropped_total = drop_unsupported(out.executive_summary, by_id)
    summary = _trim_summary(summary)
    sections: list[DraftSection] = []
    for i, heading in enumerate(headings):
        body, dropped = drop_unsupported(out.sections[i].body, by_id) if i < len(out.sections) else ("", 0)
        dropped_total += dropped
        sections.append(DraftSection(heading=heading, body=body or NO_SUPPORT))
    if dropped_total:
        gaps.append(f"{dropped_total} statement(s) without sufficient evidence were removed from the draft.")
    if any(e.conflict for e in evidence.items):
        gaps.append("Some sources disagree on key facts; conflicting claims are marked LOW confidence.")
    if out.gaps_and_limitations.strip():
        gaps.insert(0, out.gaps_and_limitations.strip())
    return ReportDraft(
        title=task_graph.brief.split(NL)[0][:120], executive_summary=summary, sections=sections, gaps=gaps
    )


# ---------- rendering ----------
def _citation(source_id: str, sources: dict[str, dict]) -> str:
    meta = sources.get(source_id)
    if not meta:
        return "[Source: unknown]"
    date = str(meta.get("published_date") or "")[:10] or "n.d."
    return f"[Source: {meta['domain']}, {date}, credibility: {meta['credibility_score']:.2f}]"


def render_text(text: str, evidence: dict[str, EvidenceItem], sources: dict[str, dict], unverified: set[str]) -> str:
    lines = []
    for line in text.split(NL):
        rendered = []
        for sentence in split_sentences(line):
            ids = _MARKER.findall(sentence)
            cites = " ".join(
                _citation(sid, sources) for i in dict.fromkeys(ids) if i in evidence for sid in evidence[i].source_ids
            )
            body = _MARKER.sub("", sentence).replace(" .", ".").replace("  ", " ").strip()
            body = re.sub(r"\s+([.!?])$", r"\1", body)
            out = f"{body} {cites}".strip() if cites else body
            if any(i in unverified for i in ids):
                out += f" {UNVERIFIED}"
            rendered.append(out)
        lines.append(" ".join(rendered))
    return NL.join(lines)


def section_confidence(body: str, evidence: dict[str, EvidenceItem]) -> Confidence:
    """Lowest confidence among the evidence the section cites; LOW if it cites nothing."""
    cited = [evidence[i].confidence for i in _MARKER.findall(body) if i in evidence]
    return min(cited, key=_CONF_ORDER.get) if cited else Confidence.LOW


def render_report(
    draft: ReportDraft,
    evidence: EvidenceBundle,
    sources: dict[str, dict],
    run_id: str,
    *,
    unverified: set[str] | None = None,
    fact_check: FactCheckSummary | None = None,
    report_id: str | None = None,
    created_at: datetime | None = None,
) -> Report:
    unverified = unverified or set()
    by_id = {e.id: e for e in evidence.items}
    summary = render_text(draft.executive_summary, by_id, sources, unverified)
    sections = [
        ReportSection(
            heading=s.heading,
            body=render_text(s.body, by_id, sources, unverified),
            confidence=section_confidence(s.body, by_id),
        )
        for s in draft.sections
    ]
    gaps = NL.join(f"- {g}" for g in draft.gaps) if draft.gaps else "None identified."

    md = [f"# {draft.title}", "", "## Executive Summary", "", summary, ""]
    for s in sections:
        md += [f"## {s.heading}", "", f"*Confidence: {s.confidence.value}*", "", s.body, ""]
    md += ["## Gaps & limitations", "", gaps, ""]
    if fact_check:
        md += [
            "## Fact-check",
            "",
            f"{fact_check.passed}/{fact_check.total_checked} sampled claims verified "
            f"(pass rate {fact_check.pass_rate:.0%}).",
            "",
        ]
    return Report(
        id=report_id or uuid.uuid4().hex,
        run_id=run_id,
        title=draft.title,
        executive_summary=summary,
        sections=sections,
        gaps_and_limitations=gaps,
        markdown=NL.join(md),
        created_at=created_at or datetime.now(UTC),
    )


def make_write_stage(generate: Generate):
    """Pipeline stage: state -> {"draft", "report"}."""

    async def stage(state: dict) -> dict:
        draft = await write_report(
            state["brief"].text, state["evidence"], state["task_graph"], state["subtask_status"], generate
        )
        report = render_report(draft, state["evidence"], state["sources"], state["run_id"])
        return {"draft": draft, "report": report}

    return stage

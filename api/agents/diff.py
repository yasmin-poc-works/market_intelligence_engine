"""Diff Agent: compares a new report with the previous one for the same watchlist item.

Sentences are normalized (citations and markers stripped), then matched by token overlap:
a close match with the same figures is a rephrasing and is ignored; a close match with
different figures is a changed finding; the rest are added or removed. An optional LLM judge
then drops leftover pairs that only rephrase each other.
"""
import re
from collections.abc import Awaitable, Callable

from pydantic import BaseModel

from agents.writer import split_sentences
from llm import call_llm, make_chat_model
from schemas import DiffSummary, Report

RELATED_MIN = 0.5  # token overlap needed to treat two sentences as the same finding
BULK_CHANGE = 3  # this many added+removed findings count as material on their own

Judge = Callable[[DiffSummary], Awaitable[DiffSummary]]

_CITATION = re.compile(r"\[(?:Source:[^\]]*|E\d+|UNVERIFIED)\]")
_STOP = set("a an the of in on at to for and or is are was were be been by with as from that this it its".split())


class JudgedDiff(BaseModel):
    added: list[str]
    removed: list[str]
    changed: list[str]


def _clean(sentence: str) -> str:
    return re.sub(r"\s+", " ", _CITATION.sub("", sentence)).strip()


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP}


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in re.findall(r"\d[\d,]*\.?\d*", text)}


def _overlap(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def report_findings(report: Report) -> list[str]:
    """Claim sentences of a report: executive summary and section bodies, without citations."""
    texts = [report.executive_summary, *(s.body for s in report.sections)]
    seen: set[str] = set()
    out: list[str] = []
    for text in texts:
        for sentence in split_sentences(text):
            clean = _clean(sentence)
            if len(_tokens(clean)) >= 3 and clean.lower() not in seen:
                seen.add(clean.lower())
                out.append(clean)
    return out


def is_material(diff: DiffSummary) -> bool:
    if diff.changed:
        return True
    if any(_numbers(s) for s in [*diff.added, *diff.removed]):
        return True
    return len(diff.added) + len(diff.removed) >= BULK_CHANGE


def diff_findings(old: list[str], new: list[str]) -> DiffSummary:
    """Deterministic comparison of two finding lists (rephrasing is ignored)."""
    old_tokens = [_tokens(s) for s in old]
    unmatched_old = set(range(len(old)))
    added: list[str] = []
    changed: list[str] = []
    for sentence in new:
        toks = _tokens(sentence)
        best, best_score = None, 0.0
        for i in unmatched_old:
            score = _overlap(toks, old_tokens[i])
            if score > best_score:
                best, best_score = i, score
        if best is None or best_score < RELATED_MIN:
            added.append(sentence)
            continue
        unmatched_old.discard(best)
        if _numbers(sentence) != _numbers(old[best]):  # same figures means rephrasing: ignored
            changed.append(f"{old[best]} -> {sentence}")
    removed = [old[i] for i in sorted(unmatched_old)]
    diff = DiffSummary(added=added, removed=removed, changed=changed)
    diff.material = is_material(diff)
    return diff


async def diff_reports(old: Report, new: Report, judge: Judge | None = None) -> DiffSummary:
    diff = diff_findings(report_findings(old), report_findings(new))
    if judge is not None and (diff.added or diff.removed or diff.changed):
        diff = await judge(diff)
        diff.material = is_material(diff)
    return diff


def make_llm_judge() -> Judge:
    model = make_chat_model("synthesis").with_structured_output(JudgedDiff)

    async def judge(diff: DiffSummary) -> DiffSummary:
        prompt = (
            "Below are differences between two versions of a market research report. Remove every "
            "entry that is only a rephrasing of the same fact (same figures, same meaning). If an "
            "item in 'removed' and one in 'added' say the same thing, drop both. Keep new facts, "
            "removed facts and facts whose figures or conclusion changed. Return the three lists "
            "with entries copied verbatim.\n\n"
            f"ADDED:\n{chr(10).join(diff.added) or '-'}\n\n"
            f"REMOVED:\n{chr(10).join(diff.removed) or '-'}\n\n"
            f"CHANGED:\n{chr(10).join(diff.changed) or '-'}"
        )
        out = await call_llm(lambda: model.ainvoke(prompt))
        out = out if isinstance(out, JudgedDiff) else JudgedDiff.model_validate(out)

        def keep(items: list[str], allowed: list[str]) -> list[str]:  # never accept invented entries
            return [s for s in items if s in allowed]

        return DiffSummary(
            added=keep(out.added, diff.added),
            removed=keep(out.removed, diff.removed),
            changed=keep(out.changed, diff.changed),
        )

    return judge

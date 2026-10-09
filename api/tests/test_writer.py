import asyncio

from agents.writer import (
    NO_SUPPORT,
    DraftSection,
    ReportDraft,
    WriterOutput,
    check_sentence,
    render_report,
    split_sentences,
    write_report,
)
from schemas import Confidence, EvidenceBundle, EvidenceItem, SubTask, TaskGraph

SOURCES = {
    "s1": {"url": "https://reuters.com/a", "domain": "reuters.com", "published_date": "2026-05-01T00:00:00+00:00", "credibility_score": 0.9},
    "s2": {"url": "https://blog.io/b", "domain": "blog.io", "published_date": None, "credibility_score": 0.4},
}


def item(i, claim, sids, conf):
    return EvidenceItem(id=f"E{i}", claim=claim, source_ids=sids, confidence=conf, credibility_score=0.8)


EVIDENCE = EvidenceBundle(
    items=[
        item(1, "Acme raised $50 million in Series B funding", ["s1"], Confidence.HIGH),
        item(2, "Acme plans to hire 200 engineers in 2026", ["s2"], Confidence.LOW),
        item(3, "The EV market grew 12% last year", ["s1", "s2"], Confidence.MEDIUM),
    ]
)
BY_ID = {e.id: e for e in EVIDENCE.items}
GRAPH = TaskGraph(
    brief="Acme outlook",
    subtasks=[SubTask(id=f"t{i}", title=f"Topic {i}", scope="s") for i in (1, 2, 3)],
)
OK_STATUS = {"t1": "done", "t2": "done", "t3": "done"}

GOOD = WriterOutput(
    executive_summary="Acme raised $50 million [E1]. The EV market grew 12% [E3].",
    sections=[
        DraftSection(heading="Topic 1", body="Acme raised $50 million in Series B funding [E1]."),
        DraftSection(heading="Topic 2", body="Acme plans to hire 200 engineers [E2]."),
        DraftSection(heading="Topic 3", body="The EV market grew 12% [E3]."),
    ],
    gaps_and_limitations="No data on competitors.",
)


class Gen:
    """Replays canned outputs; the last one repeats. Records prompts."""

    def __init__(self, *outs):
        self.outs, self.prompts = list(outs), []

    async def __call__(self, prompt):
        self.prompts.append(prompt)
        return self.outs[min(len(self.prompts) - 1, len(self.outs) - 1)]


def write(gen, status=OK_STATUS):
    return asyncio.run(write_report("Acme outlook", EVIDENCE, GRAPH, status, gen))


def with_section(body, idx=0, summary=None):
    secs = [s.model_copy() for s in GOOD.sections]
    secs[idx] = DraftSection(heading=secs[idx].heading, body=body)
    return GOOD.model_copy(update={"sections": secs, **({"executive_summary": summary} if summary else {})})


# ---------- grounding primitives ----------
def test_split_sentences_glues_marker_only_fragments():
    assert split_sentences("Acme grew 5%. [E1]. Next one [E2].") == ["Acme grew 5%. [E1].", "Next one [E2]."]


def test_check_sentence_cases():
    assert check_sentence("Acme raised $50 million [E1].", BY_ID) is None
    assert "no [E#] citation" in check_sentence("Acme is great.", BY_ID)
    assert "unknown evidence E9" in check_sentence("Acme is great [E9].", BY_ID)
    assert "75" in check_sentence("Acme raised $75 million [E1].", BY_ID)
    assert check_sentence("Acme raised $50 million [E1][E3].", BY_ID) is None


def test_small_integers_are_not_treated_as_figures():
    assert check_sentence("Acme has 3 products [E1].", BY_ID) is None


# ---------- 4.3 structure and citations ----------
def test_grounded_draft_is_accepted_first_time():
    gen = Gen(GOOD)
    draft = write(gen)
    assert len(gen.prompts) == 1 and [s.heading for s in draft.sections] == ["Topic 1", "Topic 2", "Topic 3"]


def test_render_resolves_citations_and_labels_confidence():
    report = render_report(write(Gen(GOOD)), EVIDENCE, SOURCES, "run1")
    assert "[E1]" not in report.markdown
    assert "[Source: reuters.com, 2026-05-01, credibility: 0.90]" in report.markdown
    assert "[Source: blog.io, n.d., credibility: 0.40]" in report.markdown
    conf = {s.heading: s.confidence for s in report.sections}
    assert conf == {"Topic 1": Confidence.HIGH, "Topic 2": Confidence.LOW, "Topic 3": Confidence.MEDIUM}
    md = report.markdown
    order = [md.index(h) for h in ["## Executive Summary", "## Topic 1", "## Topic 2", "## Topic 3", "## Gaps & limitations"]]
    assert order == sorted(order)
    assert "*Confidence: HIGH*" in md and "No data on competitors." in md


def test_section_without_citations_is_low_confidence():
    draft = ReportDraft(
        title="t", executive_summary="", sections=[DraftSection(heading="H", body=NO_SUPPORT)], gaps=[]
    )
    assert render_report(draft, EVIDENCE, SOURCES, "r").sections[0].confidence == Confidence.LOW


def test_prompt_lists_only_evidence_and_headings():
    gen = Gen(GOOD)
    write(gen)
    p = gen.prompts[0]
    assert "[E1] (HIGH) Acme raised $50 million" in p and "- Topic 2" in p and "150 words" in p


def test_failed_subtask_has_no_section_and_is_listed_in_gaps():
    out = GOOD.model_copy(update={"sections": [GOOD.sections[0], GOOD.sections[2]]})
    draft = write(Gen(out), {"t1": "done", "t2": "failed", "t3": "done"})
    assert [s.heading for s in draft.sections] == ["Topic 1", "Topic 3"]
    assert any("Topic 2" in g and "failed" in g for g in draft.gaps)


# ---------- 4.4 grounding retry ----------
def test_ungrounded_claim_triggers_retry_with_explicit_constraint():
    bad = with_section("Acme will definitely dominate the market.")
    gen = Gen(bad, GOOD)
    draft = write(gen)
    assert len(gen.prompts) == 2
    assert "broke these rules" in gen.prompts[1] and "no [E#] citation" in gen.prompts[1]
    assert "dominate" not in draft.sections[0].body


def test_fabricated_figure_triggers_retry():
    gen = Gen(with_section("Acme raised $500 million [E1]."), GOOD)
    write(gen)
    assert "figures not found" in gen.prompts[1]


def test_wrong_section_count_triggers_retry():
    short = GOOD.model_copy(update={"sections": GOOD.sections[:2]})
    gen = Gen(short, GOOD)
    write(gen)
    assert "expected exactly 3 sections" in gen.prompts[1]


def test_long_summary_triggers_retry_and_is_trimmed_to_whole_sentences_if_persistent():
    long_summary = " ".join(["Acme raised $50 million [E1]."] * 40)
    gen = Gen(GOOD.model_copy(update={"executive_summary": long_summary}))
    draft = write(gen)
    assert len(gen.prompts) == 3  # first draft + 2 retries
    assert "words; the limit is 150" in gen.prompts[1]
    assert len(draft.executive_summary.replace("[E1]", "").split()) <= 150
    assert draft.executive_summary.endswith("[E1].")


def test_persistent_violations_remove_unsupported_sentences_and_note_gap():
    bad = with_section("Totally invented claim. Acme raised $50 million [E1].")
    gen = Gen(bad)
    draft = write(gen)
    assert len(gen.prompts) == 3
    assert draft.sections[0].body == "Acme raised $50 million [E1]."
    assert any("removed" in g for g in draft.gaps)


def test_section_with_nothing_supported_gets_placeholder():
    gen = Gen(with_section("Nothing here is cited."))
    draft = write(gen)
    assert draft.sections[0].body == NO_SUPPORT


def test_conflicting_evidence_adds_gap_note():
    ev = EvidenceBundle(items=[EVIDENCE.items[0].model_copy(update={"conflict": True})] + EVIDENCE.items[1:])
    draft = asyncio.run(write_report("b", ev, GRAPH, OK_STATUS, Gen(GOOD)))
    assert any("disagree" in g for g in draft.gaps)


# ---------- rendering [UNVERIFIED] ----------
def test_unverified_marks_only_sentences_citing_failed_claims():
    draft = write(Gen(GOOD))
    report = render_report(draft, EVIDENCE, SOURCES, "r", unverified={"E2"})
    assert report.sections[1].body.endswith("[UNVERIFIED]")
    assert "[UNVERIFIED]" not in report.sections[0].body
    assert "[UNVERIFIED]" not in report.executive_summary

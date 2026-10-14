"""Runs sample briefs through the real pipeline and writes the evaluation report.

Run from api/ (it uses the API's settings and database):

    uv run python ../evaluation/run_eval.py                      # all briefs that need no PDFs
    uv run python ../evaluation/run_eval.py --briefs payroll-sea # just one (saves free-tier quota)
    uv run python ../evaluation/run_eval.py --briefs bnpl-annual-reports --pdf a.pdf --pdf b.pdf

Each brief costs about 3 web searches and a few dozen LLM calls. Outputs go to evaluation/:
results.json, report.md and outputs/<brief>.md (the generated report) and outputs/<brief>.runlog.json.
"""
import argparse
import asyncio
import json
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "api"))

from metrics import CITATION_ACCURACY_TARGET, PASS_RATE_TARGET, context_summary, evaluate_report  # noqa: E402

SAMPLES = HERE.parent / "samples" / "briefs.json"
OUT = HERE / "outputs"


def load_specs(selected: list[str] | None) -> list[dict]:
    specs = json.loads(SAMPLES.read_text(encoding="utf-8"))["briefs"]
    if selected:
        unknown = set(selected) - {s["id"] for s in specs}
        if unknown:
            raise SystemExit(f"unknown brief id(s): {', '.join(sorted(unknown))}")
        specs = [s for s in specs if s["id"] in selected]
    return specs


async def run_brief(spec: dict, pdfs: list[Path]) -> dict:
    from config import get_settings
    from db import SessionLocal, init_db
    from db.models import Report as ReportRow
    from db.models import Run
    from schemas import ResearchBrief
    from services.pipeline_factory import build_deps
    from services.runner import create_run, execute_run

    init_db()
    names: list[str] = []
    brief = ResearchBrief(
        text=spec["brief"], include_web=spec["include_web"], include_documents=spec["include_documents"]
    )
    run_id = create_run(SessionLocal, brief, [])
    if pdfs:
        dest = get_settings().upload_dir / run_id
        dest.mkdir(parents=True, exist_ok=True)
        for i, pdf in enumerate(pdfs, 1):
            names.append(f"{i}_{pdf.name}")
            shutil.copy(pdf, dest / names[-1])
        brief = brief.model_copy(update={"document_ids": names})

    started = time.monotonic()
    await execute_run(run_id, brief, build_deps, SessionLocal)
    elapsed = time.monotonic() - started

    with SessionLocal() as db:
        run = db.get(Run, run_id)
        row = db.query(ReportRow).filter(ReportRow.run_id == run_id).first()
        result = {
            "id": spec["id"],
            "title": spec["title"],
            "run_id": run_id,
            "status": run.status,
            "error": run.error,
            "seconds": round(elapsed),
        }
        if row is not None:
            result["report_id"] = row.id
            result.update(evaluate_report(spec, row.data))
            OUT.mkdir(exist_ok=True)
            (OUT / f"{spec['id']}.md").write_text(row.markdown, encoding="utf-8")
        result["context"] = context_summary(run.run_log or [])
        OUT.mkdir(exist_ok=True)
        (OUT / f"{spec['id']}.runlog.json").write_text(json.dumps(run.run_log or [], indent=2), encoding="utf-8")
    return result


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def render_markdown(results: list[dict], skipped: list[str]) -> str:
    done = [r for r in results if "fact_check" in r]
    lines = [
        "# Evaluation report",
        "",
        f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC by `evaluation/run_eval.py` against the live pipeline "
        "(real search, real LLM calls). Raw numbers: [results.json](results.json); generated reports and run logs: "
        "[outputs/](outputs/).",
        "",
        "## Results",
        "",
        "| Brief | Run | Fact-check pass rate | Citation accuracy | Cited sentences | Synthesis score | Time |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        if "fact_check" not in r:
            lines.append(f"| {r['id']} | {r['status']} | - | - | - | - | {r['seconds']}s |")
            continue
        fc, ct, q = r["fact_check"], r["citations"], r["quality"]
        lines.append(
            f"| {r['id']} | {r['status']} | {pct(fc['pass_rate'])} ({fc['checked']} checked) "
            f"{'✓' if fc['meets_target'] else '✗'} | {pct(ct['citation_accuracy'])} {'✓' if ct['meets_target'] else '✗'} "
            f"| {ct['cited_sentences']}/{ct['claim_sentences']} | {q['score']:.2f} | {r['seconds']}s |"
        )
    if done:
        avg = lambda f: sum(f(r) for r in done) / len(done)  # noqa: E731
        lines += [
            "",
            f"**Averages over {len(done)} brief(s):** fact-check pass rate "
            f"{pct(avg(lambda r: r['fact_check']['pass_rate'] or 0))} (target {pct(PASS_RATE_TARGET)}), citation accuracy "
            f"{pct(avg(lambda r: r['citations']['citation_accuracy']))} (target {pct(CITATION_ACCURACY_TARGET)}), "
            f"synthesis score {avg(lambda r: r['quality']['score']):.2f}.",
        ]
    if skipped:
        lines += ["", f"Not run: {', '.join(skipped)} (see notes)."]
    lines += ["", "## Per-brief detail", ""]
    for r in results:
        lines += [f"### {r['id']}: {r['title']}", "", f"Run `{r['run_id']}`, status **{r['status']}**, {r['seconds']}s."]
        if r.get("error"):
            lines += ["", f"Error: `{r['error'][:300]}`"]
        if "quality" in r:
            q, ct, fc, cx = r["quality"], r["citations"], r["fact_check"], r["context"]
            lines += [
                "",
                f"- Fact-check: {fc['failed']} of {fc['checked']} sampled claims failed verification.",
                f"- Citations: {ct['citations']} in total, {ct['resolvable_citations']} resolve to a stored source; "
                f"{pct(ct['citation_coverage'])} of claim sentences carry a citation.",
                f"- Structure: {q['section_count']} sections, executive summary {q['executive_summary_words']} words "
                f"(limit 150), {q['non_low_confidence_sections']} sections above LOW confidence, "
                f"{q['source_domains']} distinct source domains.",
                f"- Must-cover topics: {pct(q['coverage'])}"
                + (f"; missing: {', '.join(q['missing_topics'])}" if q["missing_topics"] else "."),
                f"- Context engineering: {cx['boundaries']} boundaries logged, {cx['boundaries_over_budget']} over budget, "
                f"{cx['cuts']} cuts saving about {cx['tokens_saved']} tokens.",
            ]
            for ex in ct["unsupported_examples"]:
                lines.append(f"- Unsupported by its cited sources: \"{ex}\"")
        lines.append("")
    lines += [
        "## How the numbers are computed",
        "",
        "- **Fact-check pass rate**: the Fact-Checker's own result. It samples 20% of the cited claims (at least 5), "
        "re-fetches each source and passes a claim when some passage reaches cosine similarity 0.85.",
        "- **Citation accuracy**: of the claim sentences that carry a `[Source: ...]` citation, the share whose cited "
        "sources' stored evidence contains the sentence's figures and at least 60% of its words. An independent "
        "check of the writer's own grounding check.",
        "- **Synthesis score** (0 to 1): 0.30 structure (summary present and at most 150 words, 3 to 6 sections, gaps "
        "section) + 0.35 coverage of the brief's must-cover topics + 0.20 share of sections above LOW confidence + "
        "0.15 source diversity (4 or more distinct domains is full marks). These are proxies; a human reads the "
        "generated reports in `outputs/` for the quality judgement.",
        "",
    ]
    return "\n".join(lines)


async def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--briefs", nargs="*", help="brief ids to run (default: all that need no PDFs)")
    parser.add_argument("--pdf", action="append", default=[], type=Path, help="PDF for document briefs (repeatable)")
    args = parser.parse_args()

    specs = load_specs(args.briefs)
    results, skipped = [], []
    for spec in specs:
        if spec["include_documents"] and spec["documents"] and not args.pdf:
            skipped.append(f"{spec['id']} (needs --pdf files)")
            continue
        print(f"== {spec['id']} ...", flush=True)
        result = await run_brief(spec, args.pdf if spec["documents"] else [])
        results.append(result)
        print(f"   {result['status']} in {result['seconds']}s", flush=True)

    OUT.mkdir(exist_ok=True)
    (HERE / "results.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    (HERE / "report.md").write_text(render_markdown(results, skipped), encoding="utf-8")
    print(f"wrote {HERE / 'report.md'}")


if __name__ == "__main__":
    asyncio.run(main())

"""Exports a finished run as the README's sample report (Markdown and PDF) and prints its timeline.

The PDF uses the same code path as the app's "Download PDF" button. Identical citations that the
renderer used to repeat back to back (fixed in agents/writer.py after this run) are collapsed so the
sample reads like current output; nothing else in the text is changed.

Run from api/:  uv run python scripts/export_sample.py <run_id_prefix>
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import SessionLocal  # noqa: E402
from db.models import Report, Run, TaskGraphLog  # noqa: E402
from services.export import markdown_to_pdf  # noqa: E402

DOCS = Path(__file__).resolve().parents[2] / "docs"
REPEATED = re.compile(r"(\[Source: [^\]]+\])(?: \1)+")


def main(prefix: str) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    with SessionLocal() as db:
        run = db.query(Run).filter(Run.id.like(f"{prefix}%")).one()
        report = db.query(Report).filter(Report.run_id == run.id).one()
        events = db.query(TaskGraphLog).filter(TaskGraphLog.run_id == run.id).order_by(TaskGraphLog.id).all()
        markdown = REPEATED.sub(r"\1", report.markdown)
        DOCS.mkdir(exist_ok=True)
        (DOCS / "sample_report.md").write_text(markdown, encoding="utf-8")
        (DOCS / "sample_report.pdf").write_bytes(markdown_to_pdf(markdown))
        print(f"run {run.id} created {run.created_at:%H:%M:%S} completed {run.completed_at:%H:%M:%S}")
        start = run.created_at
        for e in events:
            if e.event in ("status", "task_graph_created") or e.event.startswith("subtask"):
                label = e.payload.get("status") if e.event == "status" else e.event
                extra = e.payload.get("title") or e.payload.get("refs") or ""
                print(f"  +{int((e.created_at - start).total_seconds()):>4}s  {label} {e.subtask_id or ''} {extra}")
        print("citations before/after:", report.markdown.count("[Source:"), markdown.count("[Source:"))


if __name__ == "__main__":
    main(sys.argv[1])

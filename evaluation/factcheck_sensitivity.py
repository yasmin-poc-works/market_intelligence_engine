"""How sensitive is the fact-check pass rate to the similarity threshold?

For a stored run, re-fetches each source once, embeds its passages once, and scores every evidence
claim against its own sources (best cosine similarity of any passage, the same measure the
Fact-Checker uses). Reports the share of claims that would pass at several thresholds. Uses no LLM
calls: only page fetches and the local embedding model.

Run from api/:  uv run python ../evaluation/factcheck_sensitivity.py <run_id_prefix>
"""
import asyncio
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "api"))

THRESHOLDS = [0.85, 0.80, 0.75, 0.70]


async def main(prefix: str) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    import numpy as np

    from agents.fact_checker import chunk_passages, default_embedder, fetch_page_text
    from db import SessionLocal
    from db.models import Report

    with SessionLocal() as db:
        row = db.query(Report).filter(Report.run_id.like(f"{prefix}%")).first()
        if row is None:
            raise SystemExit(f"no report for run {prefix}")
        data = row.data

    sources, evidence = data["sources"], data["evidence"]
    embedder = default_embedder()

    passages: dict[str, np.ndarray] = {}
    for sid, meta in sources.items():
        try:
            text = await fetch_page_text(meta["url"])
        except Exception as exc:
            print(f"  could not re-fetch {meta['url']}: {type(exc).__name__}")
            continue
        parts = chunk_passages(text)
        if parts:
            passages[sid] = np.array(embedder.embed(parts))

    claim_vectors = np.array(embedder.embed([e["claim"] for e in evidence]))
    scores = []
    for item, vec in zip(evidence, claim_vectors, strict=True):
        best = [float((passages[s] @ vec).max()) for s in item["source_ids"] if s in passages]
        if best:
            scores.append(max(best))

    result = {
        "run": prefix,
        "claims_scored": len(scores),
        "sources_refetched": f"{len(passages)}/{len(sources)}",
        "median_similarity": round(sorted(scores)[len(scores) // 2], 3) if scores else None,
        "pass_share": {str(t): round(sum(s >= t for s in scores) / len(scores), 3) for t in THRESHOLDS} if scores else {},
    }
    print(json.dumps(result, indent=2))
    (HERE / "factcheck_sensitivity.json").write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else ""))

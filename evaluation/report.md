# Evaluation report

Generated 2026-10-16 05:51 UTC by `evaluation/run_eval.py` against the live pipeline (real search, real LLM calls). Raw numbers: [results.json](results.json); generated reports and run logs: [outputs/](outputs/).

## Results

| Brief | Run | Fact-check pass rate | Citation accuracy | Cited sentences | Synthesis score | Time |
|---|---|---|---|---|---|---|
| payroll-sea | COMPLETE | 62% (8 checked) ✗ | 59% ✗ | 17/18 | 0.68 | 948s |

**Averages over 1 brief(s):** fact-check pass rate 62% (target 80%), citation accuracy 59% (target 80%), synthesis score 0.68.

## Per-brief detail

### payroll-sea: B2B payroll software in Southeast Asia

Run `b9bec0414bf547a6a417fd2e44d94de5`, status **COMPLETE**, 948s.

- Fact-check: 3 of 8 sampled claims failed verification.
- Citations: 55 in total, 55 resolve to a stored source; 100% of claim sentences carry a citation; 1 cited sentence(s) only state that evidence is missing and are not scored.
- Structure: 3 sections, executive summary 77 words (limit 150), 0/3 sections above LOW confidence, 4 distinct source domains.
- Must-cover topics: 67%; missing: Indonesia, competitor names
- Context engineering: 1 boundaries logged, 1 over budget, 185 cuts saving about 3788 tokens.
- Unsupported by its cited sources: "The Southeast Asian B2B payroll software market remains highly fragmented due to diverse national regulations and frequent legislative changes.       "
- Unsupported by its cited sources: "The broader category of global payroll software provides centralized processing for multinational employers and is referenced as a key solution type in the evid"
- Unsupported by its cited sources: "The evidence shows a severe contraction in Southeast Asian venture capital activity, with only 14 VC funds closing in 2024 versus 32 the prior year and a 33.9% "

## How the numbers are computed

- **Fact-check pass rate**: the Fact-Checker's own result. It samples 20% of the cited claims (at least 5), re-fetches each source and passes a claim when some passage reaches cosine similarity 0.85.
- **Citation accuracy**: of the factual sentences that carry a `[Source: ...]` citation, the share whose cited sources' stored evidence contains the sentence's figures and at least 60% of its words. An independent check of the writer's own grounding check. Sentences that only say the evidence is missing ("the evidence lacks pricing data") carry a citation but make no factual claim, so they are counted separately.
- **Synthesis score** (0 to 1): 0.30 structure (summary present and at most 150 words, 3 to 6 sections, gaps section) + 0.35 coverage of the brief's must-cover topics + 0.20 share of sections above LOW confidence + 0.15 source diversity (4 or more distinct domains is full marks). These are proxies; a human reads the generated reports in `outputs/` for the quality judgement.

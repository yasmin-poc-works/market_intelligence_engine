import json

from config import REPO_ROOT
from schemas import ResearchBrief

SAMPLES = REPO_ROOT / "samples"
APP_BRIEF_LIMIT = 500  # the UI character limit from the assignment


def load():
    return json.loads((SAMPLES / "briefs.json").read_text(encoding="utf-8"))


def test_five_unique_samples_across_industries_and_question_types():
    briefs = load()["briefs"]
    assert len(briefs) == 5
    for field in ("id", "industry", "question_type"):
        assert len({b[field] for b in briefs}) == 5


def test_briefs_fit_the_limit_and_are_valid_pipeline_input():
    for b in load()["briefs"]:
        assert 0 < len(b["brief"]) <= APP_BRIEF_LIMIT, b["id"]
        ResearchBrief(text=b["brief"], include_web=b["include_web"], include_documents=b["include_documents"])


def test_expected_structure_is_consistent():
    low, high = load()["common_expectations"]["subtask_count"]
    for b in load()["briefs"]:
        assert low <= len(b["expected_subtasks"]) <= high, b["id"]
        sections = b["expected_sections"]
        assert sections[0] == "Executive Summary" and sections[-1] == "Gaps & limitations"
        assert sections[1:-1] == b["expected_subtasks"], b["id"]
        assert b["must_cover"] and b["notes"]
        assert bool(b["documents"]) == b["include_documents"], b["id"]


def test_readme_documents_every_sample():
    readme = (SAMPLES / "README.md").read_text(encoding="utf-8")
    for b in load()["briefs"]:
        assert f"`{b['id']}`" in readme
        assert b["brief"] in readme, f"README text for {b['id']} drifted from briefs.json"

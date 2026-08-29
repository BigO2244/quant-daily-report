import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
ALPHA_ROOT = REPO_ROOT / "projects" / "alpha_lab"
HYPOTHESIS_TEMPLATE = ALPHA_ROOT / "templates" / "HYPOTHESIS.md"
LEGACY_ROOT = ALPHA_ROOT / "legacy_model_intakes"
WORKFLOW = ALPHA_ROOT / "RESEARCH_IDEA_GENERATION_WORKFLOW.md"


def test_hypothesis_template_requires_prior_model_and_legacy_review():
    template = HYPOTHESIS_TEMPLATE.read_text(encoding="utf-8")

    for required in (
        "## Memory-derived opportunity map and prior-model review — required before freeze",
        "Model compendium path and SHA-256",
        "Closest registered experiment IDs",
        "Closest registered strategy IDs",
        "Relevant completed legacy intake IDs",
        "NEW_MECHANISM",
        "CHILD_EXPERIMENT",
        "COMBINED_MECHANISMS",
        "DUPLICATE_REJECT",
        "Why institutional memory led to this proposal",
        "incremental claim beyond each standalone model",
        "Material economic difference",
        "Prior construction or failure mode this test must not repeat",
    ):
        assert required in template


def test_agent_rules_fail_closed_before_freeze():
    rules = (ALPHA_ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert "HYP-2026-015" in rules
    assert "hard freeze gate" in rules
    assert "Missing review evidence" in rules
    assert "`DUPLICATE_REJECT`" in rules
    assert "blocks freeze and testing" in rules
    assert "legacy_model_intakes/" in rules
    assert "RESEARCH_IDEA_GENERATION_WORKFLOW.md" in rules


def test_memory_first_workflow_precedes_idea_generation():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    memory = workflow.index("## 1. Review institutional memory")
    opportunity_map = workflow.index("## 2. Build the opportunity map")
    generation = workflow.index("## 3. Generate candidates")
    classification = workflow.index("## 4. Classify each candidate")
    assert memory < opportunity_map < generation < classification

    for required in (
        "MODEL_COMPENDIUM.md",
        "EXPERIMENT_LEDGER.md",
        "STRATEGY_BACKLOG.md",
        "config/research/strategy_registry.json",
        "legacy_model_intakes/",
        "COMBINED_MECHANISMS",
        "Two failed models do not become promising merely because they are combined",
        "beat both standalone components",
    ):
        assert required in workflow


def test_all_completed_legacy_intakes_are_in_compendium():
    compendium = (ALPHA_ROOT / "MODEL_COMPENDIUM.md").read_text(encoding="utf-8")
    legacy_ids = {
        match.group(1)
        for path in LEGACY_ROOT.glob("LEGACY-*.md")
        if (match := re.match(r"(LEGACY-\d{4}-\d{3})_", path.name))
    }

    missing = sorted(
        legacy_id for legacy_id in legacy_ids if f"`{legacy_id}`" not in compendium
    )
    assert missing == []


def test_future_hypotheses_carry_completed_prior_model_review():
    required_fields = (
        "Model compendium path and SHA-256:",
        "Closest registered experiment IDs:",
        "Closest registered strategy IDs:",
        "Relevant completed legacy intake IDs, or `NONE`:",
        "Proposed relationship:",
        "Material economic difference from the closest prior work:",
    )

    for path in (ALPHA_ROOT / "hypotheses").glob("HYP-*.md"):
        match = re.match(r"HYP-(\d{4})-(\d{3})_", path.name)
        if not match or (int(match.group(1)), int(match.group(2))) < (2026, 15):
            continue

        hypothesis = path.read_text(encoding="utf-8")
        assert (
            "## Memory-derived opportunity map and prior-model review — required before freeze"
            in hypothesis
        )
        for field in required_fields:
            assert field in hypothesis
            value = hypothesis.split(field, 1)[1].splitlines()[0].strip()
            assert value not in {"", "TBD", "TODO"}


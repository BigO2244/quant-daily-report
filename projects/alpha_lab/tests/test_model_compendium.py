import json
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
ALPHA_ROOT = REPO_ROOT / "projects" / "alpha_lab"


def test_compendium_covers_every_experiment_ledger_entry():
    ledger = (ALPHA_ROOT / "EXPERIMENT_LEDGER.md").read_text(encoding="utf-8")
    compendium = (ALPHA_ROOT / "MODEL_COMPENDIUM.md").read_text(encoding="utf-8")

    experiment_ids = set(re.findall(r"\| (EXP-\d{4}-\d{4}) \|", ledger))
    assert experiment_ids

    missing = sorted(
        experiment_id
        for experiment_id in experiment_ids
        if f"`{experiment_id}`" not in compendium
    )
    assert missing == []


def test_compendium_covers_every_strategy_registry_entry():
    registry = json.loads(
        (REPO_ROOT / "config" / "research" / "strategy_registry.json").read_text(
            encoding="utf-8"
        )
    )
    compendium = (ALPHA_ROOT / "MODEL_COMPENDIUM.md").read_text(encoding="utf-8")

    missing = sorted(
        strategy["strategy_id"]
        for strategy in registry["strategies"]
        if f"`{strategy['strategy_id']}`" not in compendium
    )
    assert missing == []


def test_negative_experiments_have_reusable_lessons():
    compendium = (ALPHA_ROOT / "MODEL_COMPENDIUM.md").read_text(encoding="utf-8")

    for experiment_id in ("EXP-2026-0006", "EXP-2026-0007", "EXP-2026-0008"):
        row = next(
            line
            for line in compendium.splitlines()
            if line.startswith(f"| `{experiment_id}`")
        )
        assert "`NEGATIVE`" in row
        assert len(row.split("|")) >= 6
        assert len(row.rsplit("|", 2)[-2].strip()) >= 40


def test_compendium_declares_non_authoritative_boundary():
    compendium = (ALPHA_ROOT / "MODEL_COMPENDIUM.md").read_text(encoding="utf-8")

    assert "not a parallel strategy registry or research ledger" in compendium
    assert "canonical GCP global research ledger" in compendium
    assert "data-blocked run is not a negative model result" in (
        ALPHA_ROOT / "AGENTS.md"
    ).read_text(encoding="utf-8")


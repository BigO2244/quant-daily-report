from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from aiops.plan import make_plan_run_id, render_plan, render_plan_without_hash, run_plan


def test_make_plan_run_id_format_is_deterministic() -> None:
    dt = datetime(2026, 2, 28, 9, 30, 45, tzinfo=timezone.utc)
    run_id = make_plan_run_id(dt, "abc1234")

    assert run_id == "20260228_093045_abc1234"
    assert re.fullmatch(r"\d{8}_\d{6}_[A-Za-z0-9]+", run_id)


def test_plan_hash_is_stable_for_same_inputs() -> None:
    spec_path = Path("specs/aiops_orchestration_layer.md")
    spec_hash = "3a" * 32
    files_section = "create:\n- none\n\nmodify:\n- aiops/cli.py"
    acceptance = "- Deterministic behavior\n- Exit codes propagate correctly"

    body = render_plan_without_hash(
        spec_path=spec_path,
        mode="BUILD",
        spec_hash=spec_hash,
        files_section=files_section,
        acceptance_criteria_section=acceptance,
    )
    plan_text_1, plan_hash_1 = render_plan(
        spec_path=spec_path,
        mode="BUILD",
        spec_hash=spec_hash,
        files_section=files_section,
        acceptance_criteria_section=acceptance,
    )
    plan_text_2, plan_hash_2 = render_plan(
        spec_path=spec_path,
        mode="BUILD",
        spec_hash=spec_hash,
        files_section=files_section,
        acceptance_criteria_section=acceptance,
    )

    expected_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert plan_hash_1 == expected_hash
    assert plan_hash_2 == expected_hash
    assert plan_text_1 == plan_text_2
    assert plan_text_1.endswith(f"PLAN_HASH: {expected_hash}\n")


def test_run_plan_regeneration_produces_identical_output(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path
    spec_dir = repo_root / "specs"
    spec_dir.mkdir(parents=True)
    spec_path = spec_dir / "unit_plan_spec.md"
    spec_text = (
        "OBJECTIVE: Build deterministic planning artifacts.\n"
        "MODE: BUILD\n"
        "\n"
        "## FILES\n"
        "\n"
        "create:\n"
        "- aiops/plan.py\n"
        "\n"
        "modify:\n"
        "- aiops/cli.py\n"
        "\n"
        "## ACCEPTANCE CRITERIA\n"
        "\n"
        "- Deterministic behavior\n"
        "- Plan hash preserved\n"
    )
    spec_path.write_text(spec_text, encoding="utf-8")

    monkeypatch.chdir(repo_root)
    fixed_dt = datetime(2026, 2, 28, 15, 4, 5, tzinfo=timezone.utc)
    monkeypatch.setattr("aiops.plan.now_local", lambda: fixed_dt)
    monkeypatch.setattr("aiops.plan.get_git_metadata", lambda _: {"available": True, "short_sha": "abc1234"})

    first_exit, first_run_id = run_plan(spec_path, mode_override="BUILD")
    run_id = "20260228_150405_abc1234"
    run_dir = repo_root / "reports" / "ai_runs" / run_id
    plan_path = run_dir / "plan.md"
    snapshot_path = run_dir / "spec_snapshot.md"
    first_plan = plan_path.read_text(encoding="utf-8")

    second_exit, second_run_id = run_plan(spec_path, mode_override="BUILD")
    second_plan = plan_path.read_text(encoding="utf-8")

    assert first_exit == 0
    assert second_exit == 0
    assert first_run_id == run_id
    assert second_run_id == run_id
    assert snapshot_path.read_text(encoding="utf-8") == spec_text
    assert first_plan == second_plan

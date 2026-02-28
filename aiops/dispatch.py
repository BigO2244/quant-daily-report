"""Dispatch workflow for executing aiops plan contracts."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .util import VALID_MODES


def _parse_plan_field(plan_text: str, field: str) -> str:
    prefix = f"{field}:"
    for line in plan_text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return ""


def _build_codex_task_text(run_id: str, plan_path: Path) -> str:
    return "\n".join(
        [
            f"RUN_ID: {run_id}",
            f"PLAN_PATH: {plan_path}",
            "",
            "INSTRUCTIONS:",
            "- Follow the plan contract exactly.",
            "- Modify only files declared in the plan FILES section.",
            "- Run tests required by the plan before finalizing.",
            f"- Open or update branch: aiops/{run_id}",
            "",
        ]
    )


def run_dispatch(run_id: str) -> int:
    """Execute codex for a plan contract and then run verify."""

    repo_root = Path.cwd()
    run_dir = repo_root / "reports" / "ai_runs" / run_id
    if not run_dir.exists() or not run_dir.is_dir():
        print(f"ERROR: Run directory not found: {run_dir}")
        return 1

    plan_path = run_dir / "plan.md"
    if not plan_path.exists() or not plan_path.is_file():
        print(f"ERROR: Missing plan contract: {plan_path}")
        return 1

    plan_text = plan_path.read_text(encoding="utf-8")
    mode = _parse_plan_field(plan_text, "MODE")
    if mode not in VALID_MODES:
        print(f"ERROR: Invalid or missing MODE in plan.md. Allowed values: {', '.join(VALID_MODES)}")
        return 1

    plan_hash = _parse_plan_field(plan_text, "PLAN_HASH")
    if not plan_hash:
        print(f"ERROR: Missing PLAN_HASH in plan contract: {plan_path}")
        return 1

    codex_path = shutil.which("codex")
    if not codex_path:
        task_path = run_dir / "codex_task.txt"
        task_path.write_text(_build_codex_task_text(run_id, plan_path), encoding="utf-8")
        print(f"ERROR: codex not found on PATH; wrote task file: {task_path}")
        return 2

    codex_result = subprocess.run(["codex", str(plan_path)], cwd=repo_root, check=False)
    if codex_result.returncode != 0:
        return codex_result.returncode

    spec_snapshot_path = run_dir / "spec_snapshot.md"
    verify_result = subprocess.run(
        ["aiops", "verify", str(spec_snapshot_path), "--mode", mode],
        cwd=repo_root,
        check=False,
    )
    return verify_result.returncode

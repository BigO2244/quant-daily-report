"""Dispatch workflow for executing aiops plan contracts."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .util import VALID_MODES


DEFAULT_CODEX_TIMEOUT_SECONDS = 1800


def _parse_plan_field(plan_text: str, field: str) -> str:
    prefix = f"{field}:"
    for line in plan_text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return ""


def _build_codex_task_text(run_id: str, plan_path: Path, spec_snapshot_path: Path, mode: str) -> str:
    return "\n".join(
        [
            f"RUN_ID: {run_id}",
            f"PLAN_PATH: {plan_path}",
            f"SPEC_SNAPSHOT_PATH: {spec_snapshot_path}",
            f"MODE: {mode}",
            "",
            "TEST_COMMAND: pytest -q",
            f"VERIFY_COMMAND: aiops verify {spec_snapshot_path} --mode {mode}",
            f"BRANCH: aiops/{run_id}",
            "",
            "EXECUTION_CHECKLIST:",
            "- Implement strictly per plan contract.",
            "- Modify only files declared in plan FILES section.",
            "- Run TEST_COMMAND and verify all tests pass.",
            "- Run VERIFY_COMMAND (must exit 0).",
            "- Ensure git status is clean.",
            f"- Commit with message containing RUN_ID {run_id}.",
            "- Push branch.",
            "",
        ]
    )


def _codex_timeout_seconds() -> int:
    raw_value = os.environ.get("AIOPS_CODEX_TIMEOUT_SECONDS", str(DEFAULT_CODEX_TIMEOUT_SECONDS)).strip()
    try:
        value = int(raw_value)
    except ValueError:
        return DEFAULT_CODEX_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_CODEX_TIMEOUT_SECONDS


def run_dispatch(run_id: str, run_verify_step: bool = True) -> int:
    """Execute codex for a plan contract and optionally run verify."""

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

    spec_snapshot_path = run_dir / "spec_snapshot.md"
    task_text = _build_codex_task_text(run_id, plan_path, spec_snapshot_path, mode)

    codex_path = shutil.which("codex")
    if not codex_path:
        task_path = run_dir / "codex_task.txt"
        task_path.write_text(task_text, encoding="utf-8")
        print(f"ERROR: codex not found on PATH; wrote task file: {task_path}")
        return 2

    try:
        codex_result = subprocess.run(
            ["codex", "exec", task_text],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=_codex_timeout_seconds(),
        )
    except subprocess.TimeoutExpired:
        print("ERROR: codex exec timed out")
        return 124
    except OSError as exc:
        print(f"ERROR: failed to execute codex: {exc}")
        return 1

    if codex_result.returncode != 0:
        print(f"ERROR: codex exec failed with exit code {codex_result.returncode}")
        return codex_result.returncode

    if not run_verify_step:
        return 0

    verify_result = subprocess.run(
        ["aiops", "verify", str(spec_snapshot_path), "--mode", mode],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    return verify_result.returncode

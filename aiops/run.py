"""End-to-end run workflow for aiops: parse → plan → dispatch."""

from __future__ import annotations

from pathlib import Path

from .dispatch import run_dispatch
from .plan import run_plan
from .spec_parser import REQUIRED_PARSE_HEADERS, SpecValidationError, parse_headers, validate_headers


def run_for_run_id(run_id: str) -> int:
    """Run lifecycle step for an existing run id."""

    repo_root = Path.cwd()
    run_dir = repo_root / "reports" / "ai_runs" / run_id
    if not run_dir.exists() or not run_dir.is_dir():
        print(f"ERROR: Run directory not found: {run_dir}")
        return 1

    plan_path = run_dir / "plan.md"
    if not plan_path.exists() or not plan_path.is_file():
        print(f"ERROR: Missing plan contract: {plan_path}")
        return 1

    spec_snapshot_path = run_dir / "spec_snapshot.md"
    if not spec_snapshot_path.exists() or not spec_snapshot_path.is_file():
        print(f"ERROR: Missing spec snapshot: {spec_snapshot_path}")
        return 1

    return 0


def run_end_to_end(spec_path: Path, mode_override: str | None = None) -> int:
    """Execute parse → plan → dispatch flow in-process.
    
    Returns:
        Exit code from dispatch (or earlier stage if failed).
    """

    # 1. Parse validation
    try:
        headers = parse_headers(spec_path)
        validate_headers(headers, REQUIRED_PARSE_HEADERS)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}")
        return 1
    except SpecValidationError as exc:
        print(f"ERROR: {exc}")
        return 1

    # 2. Plan generation
    plan_exit, run_id = run_plan(spec_path, mode_override=mode_override)
    if plan_exit != 0:
        return plan_exit

    # 3. Dispatch execution
    return run_dispatch(run_id)

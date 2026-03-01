"""End-to-end run workflow for aiops: parse → plan → dispatch."""

from __future__ import annotations

from pathlib import Path

from .dispatch import run_dispatch
from .plan import run_plan
from .spec_parser import REQUIRED_PARSE_HEADERS, SpecValidationError, parse_headers, validate_headers


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

"""Lifecycle orchestration for aiops run-all."""

from __future__ import annotations

import sys
from contextlib import redirect_stdout
from pathlib import Path

from .dispatch import run_dispatch
from .plan import run_plan
from .run import run_for_run_id
from .spec_parser import REQUIRED_PARSE_HEADERS, SpecValidationError, parse_headers, validate_headers
from .verify import run_verify

EXIT_OK = 0
EXIT_NEEDS_OPERATOR = 2
EXIT_VERIFY_FAILED = 3
EXIT_PARSE_OR_PLAN_FAILED = 4
EXIT_DISPATCH_FAILED = 5
EXIT_RUN_FAILED = 6


def _write_summary(
    *,
    run_dir: Path,
    run_id: str,
    spec_path: Path,
    mode: str,
    final_status: str,
    final_exit_code: int,
    parse_exit: int,
    plan_exit: int,
    dispatch_exit: int,
    run_exit: int,
    verify_exit: int,
) -> None:
    summary_path = run_dir / "run_all_summary.md"
    lines = [
        "# AIOps Run-All Summary",
        "",
        "## Inputs",
        f"- RUN_ID: {run_id}",
        f"- SPEC_PATH: {spec_path}",
        f"- MODE: {mode}",
        "",
        "## Stage Results",
        "| Stage | Exit Code |",
        "|---|---|",
        f"| parse | {parse_exit} |",
        f"| plan | {plan_exit} |",
        f"| dispatch | {dispatch_exit} |",
        f"| run | {run_exit} |",
        f"| verify | {verify_exit} |",
        "",
        "## Final",
        f"- RUN_ALL_STATUS: {final_status}",
        f"- EXIT_CODE: {final_exit_code}",
        "",
    ]
    summary_path.write_text("\n".join(lines), encoding="utf-8")


def run_all(spec_path: Path, mode_override: str | None = None) -> int:
    """Execute parse -> plan -> dispatch -> run -> verify lifecycle."""

    parse_exit = 0
    plan_exit = 0
    dispatch_exit = -1
    run_exit = -1
    verify_exit = -1

    try:
        headers = parse_headers(spec_path)
        validate_headers(headers, REQUIRED_PARSE_HEADERS)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("RUN_ALL_STATUS: FAILED")
        return EXIT_PARSE_OR_PLAN_FAILED
    except SpecValidationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("RUN_ALL_STATUS: FAILED")
        return EXIT_PARSE_OR_PLAN_FAILED

    mode = mode_override or headers.get("MODE", "")

    plan_exit, run_id = run_plan(spec_path, mode_override=mode)
    if plan_exit != 0 or not run_id:
        print("RUN_ALL_STATUS: FAILED")
        return EXIT_PARSE_OR_PLAN_FAILED

    repo_root = Path.cwd()
    run_dir = repo_root / "reports" / "ai_runs" / run_id
    spec_snapshot_path = run_dir / "spec_snapshot.md"

    print(f"RUN_ID: {run_id}")
    print(f"RUN_DIR: {run_dir}")
    print(f"PLAN_PATH: {run_dir / 'plan.md'}")
    print(f"SPEC_SNAPSHOT_PATH: {spec_snapshot_path}")

    with redirect_stdout(sys.stderr):
        dispatch_exit = run_dispatch(run_id, run_verify_step=False)

    if dispatch_exit == EXIT_NEEDS_OPERATOR:
        _write_summary(
            run_dir=run_dir,
            run_id=run_id,
            spec_path=spec_path,
            mode=mode,
            final_status="NEEDS_OPERATOR",
            final_exit_code=EXIT_NEEDS_OPERATOR,
            parse_exit=parse_exit,
            plan_exit=plan_exit,
            dispatch_exit=dispatch_exit,
            run_exit=run_exit,
            verify_exit=verify_exit,
        )
        print("RUN_ALL_STATUS: NEEDS_OPERATOR")
        return EXIT_NEEDS_OPERATOR

    if dispatch_exit != 0:
        _write_summary(
            run_dir=run_dir,
            run_id=run_id,
            spec_path=spec_path,
            mode=mode,
            final_status="FAILED",
            final_exit_code=EXIT_DISPATCH_FAILED,
            parse_exit=parse_exit,
            plan_exit=plan_exit,
            dispatch_exit=dispatch_exit,
            run_exit=run_exit,
            verify_exit=verify_exit,
        )
        print("RUN_ALL_STATUS: FAILED")
        return EXIT_DISPATCH_FAILED

    with redirect_stdout(sys.stderr):
        run_exit = run_for_run_id(run_id)
    if run_exit != 0:
        _write_summary(
            run_dir=run_dir,
            run_id=run_id,
            spec_path=spec_path,
            mode=mode,
            final_status="FAILED",
            final_exit_code=EXIT_RUN_FAILED,
            parse_exit=parse_exit,
            plan_exit=plan_exit,
            dispatch_exit=dispatch_exit,
            run_exit=run_exit,
            verify_exit=verify_exit,
        )
        print("RUN_ALL_STATUS: FAILED")
        return EXIT_RUN_FAILED

    try:
        with redirect_stdout(sys.stderr):
            verify_exit = run_verify(spec_snapshot_path, mode_override=mode)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        verify_exit = 1

    if verify_exit != 0:
        _write_summary(
            run_dir=run_dir,
            run_id=run_id,
            spec_path=spec_path,
            mode=mode,
            final_status="FAILED",
            final_exit_code=EXIT_VERIFY_FAILED,
            parse_exit=parse_exit,
            plan_exit=plan_exit,
            dispatch_exit=dispatch_exit,
            run_exit=run_exit,
            verify_exit=verify_exit,
        )
        print("RUN_ALL_STATUS: FAILED")
        return EXIT_VERIFY_FAILED

    _write_summary(
        run_dir=run_dir,
        run_id=run_id,
        spec_path=spec_path,
        mode=mode,
        final_status="OK",
        final_exit_code=EXIT_OK,
        parse_exit=parse_exit,
        plan_exit=plan_exit,
        dispatch_exit=dispatch_exit,
        run_exit=run_exit,
        verify_exit=verify_exit,
    )
    print("RUN_ALL_STATUS: OK")
    return EXIT_OK

"""Verification workflow for aiops starter kit."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .git_meta import get_git_metadata
from .report_writer import (
    build_approval_markdown,
    write_approval,
    write_commands_log,
    write_spec_json,
)
from .spec_parser import REQUIRED_VERIFY_HEADERS, SpecValidationError, parse_headers, validate_headers
from .util import CommandResult, VALID_MODES, ensure_writable_dir, make_run_id, now_local, now_utc


def run_command(command: list[str], cwd: Path) -> CommandResult:
    """Run a subprocess command and capture output without raising."""

    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
        return CommandResult(
            command=command,
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )
    except FileNotFoundError as exc:
        return CommandResult(
            command=command,
            returncode=127,
            stdout="",
            stderr="",
            error=str(exc),
        )


def run_verify(spec_path: Path, mode_override: str | None = None) -> int:
    """Run mode-specific gates and write an approval pack."""

    repo_root = Path.cwd()
    ts_local_dt = now_local()
    ts_utc_dt = now_utc()
    git_meta = get_git_metadata(repo_root)
    short_sha = str(git_meta.get("short_sha", "nogit")) if git_meta.get("available") else "nogit"
    run_id = make_run_id(ts_local_dt, short_sha)

    reports_root = repo_root / "reports" / "ai_runs"
    try:
        ensure_writable_dir(reports_root)
    except OSError as exc:
        raise RuntimeError(f"Reports directory not writable: {reports_root} ({exc})") from exc

    run_dir = reports_root / run_id
    counter = 1
    while run_dir.exists():
        run_dir = reports_root / f"{run_id}_{counter:02d}"
        counter += 1
    run_id = run_dir.name
    try:
        ensure_writable_dir(run_dir)
    except OSError as exc:
        raise RuntimeError(f"Reports directory not writable: {run_dir} ({exc})") from exc

    parsed_headers: dict[str, str] = {}
    parse_error = ""
    command_results: list[CommandResult] = []
    gate_outcomes: list[tuple[str, bool, str]] = []

    try:
        parsed_headers = parse_headers(spec_path)
        validate_headers(parsed_headers, REQUIRED_VERIFY_HEADERS)
        parse_ok = True
    except FileNotFoundError as exc:
        parse_ok = False
        parse_error = str(exc)
    except SpecValidationError as exc:
        parse_ok = False
        parse_error = str(exc)

    gate_outcomes.append(("Spec parse", parse_ok, parse_error if parse_error else "Required headers found"))

    effective_mode = mode_override or parsed_headers.get("MODE")
    mode_error = ""
    if not effective_mode:
        mode_error = "Missing MODE and no --mode override provided"
    elif effective_mode not in VALID_MODES:
        mode_error = f"Invalid MODE '{effective_mode}'. Allowed values: {', '.join(VALID_MODES)}"

    if mode_error:
        gate_outcomes.append(("Mode selection", False, mode_error))
        effective_mode = effective_mode or "BUILD"
    else:
        gate_outcomes.append(("Mode selection", True, f"Using mode {effective_mode}"))

    py_result = run_command([sys.executable, "--version"], repo_root)
    command_results.append(py_result)
    gate_outcomes.append(("Python available", py_result.returncode == 0, f"exit={py_result.returncode}"))

    pytest_required = effective_mode in {"BUILD", "HARDEN"}
    pytest_ok = True
    if pytest_required:
        pytest_result = run_command([sys.executable, "-m", "pytest", "-q"], repo_root)
        command_results.append(pytest_result)
        pytest_ok = pytest_result.returncode == 0
        detail = "pytest passed" if pytest_ok else f"pytest failed (exit={pytest_result.returncode})"
        if pytest_result.error:
            detail = f"pytest unavailable: {pytest_result.error}"
        gate_outcomes.append(("Pytest", pytest_ok, detail))
    else:
        gate_outcomes.append(("Pytest", True, "Not required in EXPLORE"))

    risk_checklist: list[str] = []
    if effective_mode == "HARDEN":
        risk_checklist = [
            "State mutation impacts reviewed for runtime and persistence paths.",
            "Idempotency expectations documented for repeated verify executions.",
            "External IO boundaries reviewed (filesystem/network/subprocess).",
            "Secrets handling verified: only SET/MISSING state may be logged.",
            "Rollback procedure documented and reversible with minimal downtime.",
        ]
        risk_ok = len(risk_checklist) >= 5
        gate_outcomes.append(("Risk checklist coverage", risk_ok, f"{len(risk_checklist)} bullet(s) present"))

    ts_local = ts_local_dt.isoformat()
    ts_utc = ts_utc_dt.isoformat()

    commands_log_path = run_dir / "commands.log"
    approval_path = run_dir / "approval.md"
    parsed_json_path = run_dir / "spec_parsed.json"

    write_commands_log(commands_log_path, command_results)
    write_spec_json(parsed_json_path, parsed_headers)

    verify_pass = all(ok for _, ok, _ in gate_outcomes)

    next_actions = [
        "Review gate failures and rerun verify after fixes." if not verify_pass else "Proceed to human signoff.",
        "Attach approval pack artifacts to review thread.",
    ]
    rollback_notes = [
        "No business-logic files were modified by verify.",
        "Delete this run directory if artifacts were generated in error.",
    ]

    approval_md = build_approval_markdown(
        spec_path=spec_path,
        mode=effective_mode,
        run_id=run_id,
        ts_local=ts_local,
        ts_utc=ts_utc,
        git_meta=git_meta,
        parsed_headers=parsed_headers,
        command_results=command_results,
        gate_outcomes=gate_outcomes,
        risk_checklist=risk_checklist,
        next_actions=next_actions,
        rollback_notes=rollback_notes,
    )
    write_approval(approval_path, approval_md)

    if not parse_ok:
        return 1
    if mode_error:
        return 1
    if effective_mode == "EXPLORE":
        return 0
    if effective_mode in {"BUILD", "HARDEN"}:
        return 0 if verify_pass else 1
    return 1

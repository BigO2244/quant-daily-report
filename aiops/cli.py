"""CLI interface for aiops."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .dispatch import run_dispatch
from .plan import run_plan
from .run import run_end_to_end
from .spec_parser import REQUIRED_PARSE_HEADERS, SpecValidationError, parse_headers, validate_headers
from .util import VALID_MODES
from .verify import run_verify


def build_parser() -> argparse.ArgumentParser:
    """Create top-level CLI parser."""

    parser = argparse.ArgumentParser(prog="aiops", description="Brett AI OS starter-kit CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    parse_cmd = subparsers.add_parser("parse", help="Parse and validate a spec")
    parse_cmd.add_argument("spec_path", help="Path to spec markdown file")

    verify_cmd = subparsers.add_parser("verify", help="Run mode-gated verification")
    verify_cmd.add_argument("spec_path", help="Path to spec markdown file")
    verify_cmd.add_argument("--mode", choices=VALID_MODES, help="Override mode from spec")

    plan_cmd = subparsers.add_parser("plan", help="Create deterministic planning artifacts")
    plan_cmd.add_argument("spec_path", help="Path to spec markdown file")
    plan_cmd.add_argument("--mode", choices=VALID_MODES, help="Override mode from spec")

    dispatch_cmd = subparsers.add_parser("dispatch", help="Execute a plan contract and run verify")
    dispatch_cmd.add_argument("--run", required=True, dest="run_id", help="Plan run ID under reports/ai_runs")

    run_cmd = subparsers.add_parser("run", help="Execute parse → plan → dispatch end-to-end")
    run_cmd.add_argument("spec_path", help="Path to spec markdown file")
    run_cmd.add_argument("--mode", choices=VALID_MODES, default="BUILD", help="Execution mode (default: BUILD)")

    return parser


def handle_parse(spec_path: Path) -> int:
    """Handle parse command output and exit code."""

    try:
        headers = parse_headers(spec_path)
        validate_headers(headers, REQUIRED_PARSE_HEADERS)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}")
        return 1
    except SpecValidationError as exc:
        print(f"ERROR: {exc}")
        return 1

    ordered = {
        "MODE": headers.get("MODE", ""),
        "PROJECT_TYPE": headers.get("PROJECT_TYPE", ""),
        "RISK_TIER": headers.get("RISK_TIER", ""),
        "OBJECTIVE": headers.get("OBJECTIVE", ""),
    }
    print(json.dumps(ordered, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "parse":
        return handle_parse(Path(args.spec_path))
    if args.command == "verify":
        try:
            return run_verify(Path(args.spec_path), mode_override=args.mode)
        except RuntimeError as exc:
            print(f"ERROR: {exc}")
            return 1
    if args.command == "plan":
        exit_code, payload = run_plan(Path(args.spec_path), mode_override=args.mode)
    if exit_code != 0:
        return exit_code

    # payload appears to be a RUN_ID string in this repo. Support dict too (future-proof),
    # but keep stdout deterministic and EXACTLY four lines on success.
    if isinstance(payload, str):
        run_id = payload
        run_dir = f"reports/ai_runs/{run_id}"
    elif isinstance(payload, dict):
        run_id = payload.get("run_id") or payload.get("RUN_ID")
        run_dir = payload.get("run_dir") or payload.get("RUN_DIR")
        if not run_id or not run_dir:
            print("ERROR: run_plan payload missing run_id/run_dir")
            return 1
    else:
        print(f"ERROR: unexpected run_plan payload type: {type(payload).__name__}")
        return 1

    print(f"RUN_ID: {run_id}")
    print(f"RUN_DIR: {run_dir}")
    print(f"PLAN_PATH: {run_dir}/plan.md")
    print(f"SPEC_SNAPSHOT_PATH: {run_dir}/spec_snapshot.md")
    return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

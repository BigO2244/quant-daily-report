"""CLI interface for aiops."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .dispatch import run_dispatch
from .plan import run_plan
from .run import run_end_to_end
from .run_all import run_all
from .spec_parser import REQUIRED_PARSE_HEADERS, SpecValidationError, parse_headers, validate_headers
from .util import VALID_MODES
from .verify import run_verify
from .aegis import AegisService, AegisStore
from .aegis.api import serve as serve_aegis
from .aegis.dashboard import render_mission_control


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

    run_all_cmd = subparsers.add_parser("run-all", help="Execute parse → plan → dispatch → run → verify")
    run_all_cmd.add_argument("--spec", required=True, dest="spec_path", help="Path to spec markdown file")
    run_all_cmd.add_argument("--mode", required=True, choices=VALID_MODES, help="Execution mode")

    aegis_cmd = subparsers.add_parser("aegis", help="Operate the local, non-trading Aegis control plane")
    aegis_cmd.add_argument("--db", default="reports/aegis/aegis.sqlite", help="SQLite database path")
    aegis_sub = aegis_cmd.add_subparsers(dest="aegis_command", required=True)
    create_cmd = aegis_sub.add_parser("create", help="Create a deterministic mission")
    create_cmd.add_argument("--objective", required=True)
    inspect_cmd = aegis_sub.add_parser("inspect", help="Inspect one mission or the mission portfolio")
    inspect_cmd.add_argument("--mission")
    approve_cmd = aegis_sub.add_parser("approve", help="Record explicit approval for an approval-required mission")
    approve_cmd.add_argument("--mission", required=True)
    approve_cmd.add_argument("--rationale", required=True)
    brief_cmd = aegis_sub.add_parser("brief", help="Render a deterministic executive brief")
    brief_cmd.add_argument("--dashboard-out", help="Optional local HTML artifact path; does not deploy it")
    serve_cmd = aegis_sub.add_parser("serve", help="Start an explicit local REST server")
    serve_cmd.add_argument("--host", default="127.0.0.1")
    serve_cmd.add_argument("--port", type=int, default=8765)

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
        exit_code, run_id = run_plan(Path(args.spec_path), mode_override=args.mode)
        if exit_code == 0 and run_id:
            repo_root = Path.cwd()
            run_dir = repo_root / "reports" / "ai_runs" / run_id
            print(f"RUN_ID: {run_id}")
            print(f"RUN_DIR: {run_dir}")
            print(f"PLAN_PATH: {run_dir / 'plan.md'}")
            print(f"SPEC_SNAPSHOT_PATH: {run_dir / 'spec_snapshot.md'}")
        return exit_code
    if args.command == "dispatch":
        try:
            return run_dispatch(args.run_id)
        except (FileNotFoundError, RuntimeError) as exc:
            print(f"ERROR: {exc}")
            return 1
    if args.command == "run":
        try:
            return run_end_to_end(Path(args.spec_path), mode_override=args.mode)
        except (FileNotFoundError, RuntimeError) as exc:
            print(f"ERROR: {exc}")
            return 1
    if args.command == "run-all":
        return run_all(Path(args.spec_path), mode_override=args.mode)
    if args.command == "aegis":
        service = AegisService(AegisStore(Path(args.db)))
        if args.aegis_command == "create":
            print(json.dumps(service.create_mission(args.objective), indent=2, sort_keys=True))
            return 0
        if args.aegis_command == "inspect":
            payload = service.store.mission(args.mission) if args.mission else service.store.missions()
            if args.mission and payload is None:
                print(f"ERROR: mission not found: {args.mission}")
                return 1
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0
        if args.aegis_command == "approve":
            try:
                print(json.dumps(service.approve(args.mission, args.rationale), indent=2, sort_keys=True))
                return 0
            except (KeyError, ValueError) as exc:
                print(f"ERROR: {exc}")
                return 1
        if args.aegis_command == "brief":
            brief = service.executive_brief()
            print(brief, end="")
            if args.dashboard_out:
                destination = Path(args.dashboard_out)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(render_mission_control(service), encoding="utf-8")
            return 0
        if args.aegis_command == "serve":
            serve_aegis(service, args.host, args.port)
            return 0

    print(f"ERROR: unknown command: {args.command}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

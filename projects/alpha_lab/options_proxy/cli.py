"""CLI for the isolated forward options proxy lane."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from projects.alpha_lab.factory import canonical_json
from projects.alpha_lab.factory.canonical import parse_datetime

from .config import default_config_path, load_config
from .automation import maturation_readiness, mature_all, run_daily
from .pipeline import (
    build_from_snapshot,
    collect_and_build,
    collect_snapshot,
    mature_signal,
    write_boundary_attestation,
)


def _timestamp(value: Optional[str]) -> Optional[datetime]:
    return parse_datetime(value) if value else None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Standalone research-only yfinance options proxy collector"
    )
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--config", type=Path, default=default_config_path())
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect = subparsers.add_parser("collect", help="write one immutable raw snapshot")
    collect.add_argument("--collected-at", help="aware ISO timestamp; defaults to now")

    observe = subparsers.add_parser(
        "observe", help="collect a snapshot and build proxy features/targets"
    )
    observe.add_argument("--collected-at", help="aware ISO timestamp; defaults to now")

    build = subparsers.add_parser("build", help="build from an existing research snapshot")
    build.add_argument("--snapshot", type=Path, required=True)

    mature = subparsers.add_parser(
        "mature", help="fetch future daily bars and evaluate a matured proxy signal"
    )
    mature.add_argument("--signal", type=Path, required=True)
    mature.add_argument("--through-date", type=date.fromisoformat, required=True)

    mature_batch = subparsers.add_parser(
        "mature-all", help="mature all eligible, not-yet-complete signal cohorts"
    )
    mature_batch.add_argument("--through-date", type=date.fromisoformat, required=True)

    readiness = subparsers.add_parser(
        "maturation-status",
        help="show which collected signal cohorts are mature, waiting, or blocked",
    )
    readiness.add_argument("--through-date", type=date.fromisoformat, required=True)

    daily = subparsers.add_parser(
        "daily", help="idempotent session-gated observation, maturity sweep, and health run"
    )
    daily.add_argument("--now", help="aware ISO timestamp; defaults to now")

    subparsers.add_parser(
        "validate-boundary", help="write a static non-trading boundary attestation"
    )
    return parser


def _summary(result: MappingLike) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "classification": "PROXY_FORWARD_OBSERVATION_ONLY",
        "alpha_claim_permitted": False,
        "trading_behavior_changed": False,
    }
    for key in (
        "snapshot_path",
        "manifest_path",
        "features_path",
        "signal_path",
        "price_path",
        "evaluation_path",
        "scoreboard_path",
        "path",
        "health_path",
    ):
        if key in result:
            summary[key] = str(result[key])
    signal = result.get("signal")
    if isinstance(signal, dict):
        summary["decision_eligible"] = signal.get("decision_eligible")
        summary["research_target_count"] = len(signal.get("research_targets", []))
        summary["decision_blockers"] = signal.get("decision_blockers", [])
    evaluation = result.get("evaluation")
    if isinstance(evaluation, dict):
        summary["evaluation_status"] = evaluation.get("status")
    attestation = result.get("attestation")
    if isinstance(attestation, dict):
        summary["production_boundary_status"] = attestation.get(
            "production_boundary_status"
        )
        summary["findings"] = attestation.get("findings", [])
    health = result.get("health")
    if isinstance(health, dict):
        summary["overall_status"] = health.get("overall_status")
        summary["session_status"] = health.get("session_status")
        summary["observation_status"] = health.get("observation", {}).get("status")
    batch = result.get("batch")
    if isinstance(batch, dict):
        summary["matured_count"] = len(batch.get("processed", []))
        summary["maturation_error_count"] = len(batch.get("errors", []))
    if "cohorts" in result and "counts" in result:
        summary["maturation_counts"] = result["counts"]
        summary["next_maturity_date"] = result.get("next_maturity_date")
    return summary


MappingLike = Dict[str, Any]


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    repo_root = args.repo_root.expanduser().resolve()
    config = load_config(args.config)
    if args.command == "collect":
        result = collect_snapshot(
            repo_root=repo_root,
            config=config,
            collected_at=_timestamp(args.collected_at),
        )
    elif args.command == "observe":
        result = collect_and_build(
            repo_root=repo_root,
            config=config,
            collected_at=_timestamp(args.collected_at),
        )
    elif args.command == "build":
        result = build_from_snapshot(
            repo_root=repo_root,
            config=config,
            snapshot_path=args.snapshot,
        )
    elif args.command == "mature":
        result = mature_signal(
            repo_root=repo_root,
            config=config,
            signal_path=args.signal,
            through_date=args.through_date,
            generated_at=datetime.now(timezone.utc),
        )
    elif args.command == "mature-all":
        result = mature_all(
            repo_root=repo_root,
            config=config,
            through_date=args.through_date,
            generated_at=datetime.now(timezone.utc),
        )
    elif args.command == "maturation-status":
        result = maturation_readiness(
            repo_root=repo_root,
            config=config,
            through_date=args.through_date,
        )
    elif args.command == "daily":
        result = run_daily(
            repo_root=repo_root,
            config=config,
            now=_timestamp(args.now),
        )
    elif args.command == "validate-boundary":
        result = write_boundary_attestation(repo_root=repo_root)
    else:  # pragma: no cover
        raise AssertionError("unreachable command")
    print(canonical_json(_summary(result)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

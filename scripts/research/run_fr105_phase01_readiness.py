#!/usr/bin/env python3
"""Run FR-105 Phase 0/1 readiness artifacts in research-only mode."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.fr105_phase01_completeness import write_fr105_phase01_completeness  # noqa: E402
from research.fr105_phase1_baseline import write_fr105_phase1_baseline  # noqa: E402
from research.fr105_replay_contract import DEFAULT_OUTPUT_ROOT, write_fr105_replay_contract  # noqa: E402


def _validation_status(payload: dict[str, Any]) -> str:
    validation = payload.get("validation_status") if isinstance(payload.get("validation_status"), dict) else {}
    return str(validation.get("status") or "UNAVAILABLE")


def run_phase01_readiness(
    *,
    repo_root: Path | str,
    trade_date: str,
    run_id: str | None = None,
    output_root: Path | str = DEFAULT_OUTPUT_ROOT,
    generated_at: str = "unavailable",
) -> tuple[Path, dict[str, Any]]:
    root = Path(repo_root).resolve()
    phase0_path, phase0 = write_fr105_replay_contract(
        repo_root=root,
        trade_date=trade_date,
        run_id=run_id,
        output_root=Path(output_root),
        generated_at=generated_at,
    )
    phase1_path, phase1 = write_fr105_phase1_baseline(
        repo_root=root,
        trade_date=trade_date,
        run_id=run_id,
        input_contract_path=phase0_path,
        output_root=Path(output_root),
        generated_at=generated_at,
    )
    completeness_path, completeness = write_fr105_phase01_completeness(
        repo_root=root,
        trade_date=trade_date,
        run_id=run_id,
        input_contract_path=phase0_path,
        input_baseline_path=phase1_path,
        output_root=Path(output_root),
        generated_at=generated_at,
    )
    summary = {
        "trade_date": trade_date,
        "run_id": run_id,
        "contract_id": completeness["metadata"]["contract_id"],
        "status": completeness["readiness"]["status"],
        "alpha_chase_evaluation_ready": completeness["readiness"]["alpha_chase_evaluation_ready"],
        "shadow_comparison_ready": completeness["readiness"]["shadow_comparison_ready"],
        "phase0_replay_contract": {
            "path": str(phase0_path),
            "validation_status": _validation_status(phase0),
        },
        "phase1_current_policy_baseline": {
            "path": str(phase1_path),
            "validation_status": _validation_status(phase1),
        },
        "phase01_artifact_completeness": {
            "path": str(completeness_path),
            "validation_status": _validation_status(completeness),
        },
        "closed_gaps": completeness["summary"]["found_fields"],
        "remaining_blocking_gaps": completeness["readiness"]["blocking_gaps"],
        "unavailable_fields": completeness["summary"]["unavailable_fields"],
        "missing_fields": completeness["summary"]["missing_fields"],
        "safety": {
            "mode": "research_only",
            "alpha_chase_default": "off",
            "trading_behavior_changed": False,
            "optimizer_behavior_changed": False,
            "broker_behavior_changed": False,
            "sizing_behavior_changed": False,
            "paper_behavior_changed": False,
            "live_pilot_behavior_changed": False,
            "production_execution_modules_invoked": [],
        },
    }
    return completeness_path, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run FR-105 Phase 0/1 research-only readiness artifact generation."
    )
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--generated-at", default="unavailable")
    args = parser.parse_args(argv)

    _, summary = run_phase01_readiness(
        repo_root=Path(args.repo_root),
        trade_date=args.trade_date,
        run_id=args.run_id,
        output_root=Path(args.output_root),
        generated_at=args.generated_at,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    validation_statuses = [
        summary["phase0_replay_contract"]["validation_status"],
        summary["phase1_current_policy_baseline"]["validation_status"],
        summary["phase01_artifact_completeness"]["validation_status"],
    ]
    return 0 if all(status == "PASS" for status in validation_statuses) else 1


if __name__ == "__main__":
    raise SystemExit(main())

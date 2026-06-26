#!/usr/bin/env python3
"""Build the FR-105 Phase 3 optimizer-derived holding-count research artifact."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.fr105_phase3_holding_count import write_fr105_phase3_holding_count  # noqa: E402
from research.fr105_replay_contract import DEFAULT_OUTPUT_ROOT  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the FR-105 Phase 3 holding-count research artifact.")
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--input-contract", default=None)
    parser.add_argument("--input-baseline", default=None)
    parser.add_argument("--input-frontier", default=None)
    parser.add_argument("--input-completeness", default=None)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--generated-at", default="unavailable")
    args = parser.parse_args(argv)

    path, payload = write_fr105_phase3_holding_count(
        repo_root=Path(args.repo_root),
        trade_date=args.trade_date,
        run_id=args.run_id,
        input_contract_path=Path(args.input_contract) if args.input_contract else None,
        input_baseline_path=Path(args.input_baseline) if args.input_baseline else None,
        input_frontier_path=Path(args.input_frontier) if args.input_frontier else None,
        input_completeness_path=Path(args.input_completeness) if args.input_completeness else None,
        output_root=Path(args.output_root),
        generated_at=args.generated_at,
    )
    print(
        json.dumps(
            {
                "status": payload["validation_status"]["status"],
                "artifact_path": str(path),
                "trade_date": payload["metadata"]["trade_date"],
                "contract_id": payload["metadata"]["contract_id"],
                "readiness": payload.get("readiness"),
                "selected_research_variant": payload["selected_research_variant"],
                "comparison_to_current_policy": payload["comparison_to_current_policy"],
                "data_quality": payload["data_quality"],
                "validation_status": payload["validation_status"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if payload["validation_status"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

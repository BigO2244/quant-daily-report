#!/usr/bin/env python3
"""Build the FR-105 Phase 0/1 artifact completeness report."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.fr105_phase01_completeness import write_fr105_phase01_completeness  # noqa: E402
from research.fr105_replay_contract import DEFAULT_OUTPUT_ROOT  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build FR-105 Phase 0/1 artifact completeness.")
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--input-contract", default=None)
    parser.add_argument("--input-baseline", default=None)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--generated-at", default="unavailable")
    args = parser.parse_args(argv)

    path, payload = write_fr105_phase01_completeness(
        repo_root=Path(args.repo_root),
        trade_date=args.trade_date,
        run_id=args.run_id,
        input_contract_path=Path(args.input_contract) if args.input_contract else None,
        input_baseline_path=Path(args.input_baseline) if args.input_baseline else None,
        output_root=Path(args.output_root),
        generated_at=args.generated_at,
    )
    print(
        json.dumps(
            {
                "artifact_path": str(path),
                "trade_date": payload["metadata"]["trade_date"],
                "contract_id": payload["metadata"]["contract_id"],
                "status": payload["summary"]["status"],
                "readiness": payload["readiness"],
                "missing_fields": payload["summary"]["missing_fields"],
                "unavailable_fields": payload["summary"]["unavailable_fields"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

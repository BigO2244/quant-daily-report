#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research_data.parity import build_sleeve_parity_report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run read-only FR-DH observe-only sleeve parity.")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--as-of-date", default=None)
    parser.add_argument("--sleeve", "--sleeve-id", dest="sleeve_id", default=None)
    parser.add_argument("--migration-readiness-path", type=Path, default=None)
    parser.add_argument("--legacy-candidates-root", type=Path, default=None)
    parser.add_argument("--output-root", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload = build_sleeve_parity_report(
        repo_root=args.repo_root,
        as_of_date=args.as_of_date,
        sleeve_id=args.sleeve_id,
        migration_readiness_path=args.migration_readiness_path,
        legacy_candidates_root=args.legacy_candidates_root,
        output_root=args.output_root,
    )
    print(
        json.dumps(
            {
                key: payload.get(key)
                for key in (
                    "schema_version",
                    "as_of_date",
                    "selected_sleeve",
                    "parity_status",
                    "recommendation",
                    "input_parity_status",
                    "fail_reasons",
                    "warning_reasons",
                    "broker_submission_invoked",
                    "sleeve_runtime_invoked",
                    "allocation_mutation_invoked",
                    "json_path",
                    "markdown_path",
                )
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

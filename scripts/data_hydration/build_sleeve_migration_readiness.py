#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research_data.migration import build_sleeve_migration_readiness


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build read-only FR-DH sleeve migration readiness artifacts.")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--as-of-date", default=None)
    parser.add_argument("--sleeve-manifest-path", type=Path, default=None)
    parser.add_argument("--observability-path", type=Path, default=None)
    parser.add_argument("--output-root", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload = build_sleeve_migration_readiness(
        repo_root=args.repo_root,
        as_of_date=args.as_of_date,
        sleeve_manifest_path=args.sleeve_manifest_path,
        observability_path=args.observability_path,
        output_root=args.output_root,
    )
    print(
        json.dumps(
            {
                key: payload[key]
                for key in (
                    "schema_version",
                    "sleeve_count",
                    "ready_observe_only_count",
                    "warn_count",
                    "blocked_count",
                    "broker_submission_invoked",
                    "sleeve_runtime_invoked",
                )
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

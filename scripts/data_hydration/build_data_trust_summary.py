#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research_data.data_trust import build_data_trust_summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build read-only FR-DH data trust summary artifacts.")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--observability-path", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    summary = build_data_trust_summary(
        repo_root=args.repo_root,
        observability_path=args.observability_path,
        output_dir=args.output_dir,
    )
    print(
        json.dumps(
            {
                key: summary[key]
                for key in (
                    "schema_version",
                    "readiness_status",
                    "dataset_count",
                    "observe_only_count",
                    "blocked_count",
                    "missing_artifact_count",
                    "critical_count",
                    "warning_count",
                    "broker_submission_invoked",
                )
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

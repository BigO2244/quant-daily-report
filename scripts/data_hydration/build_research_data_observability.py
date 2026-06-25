#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research_data.observability import build_research_data_observability


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build read-only FR-DH research data observability manifest.")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--as-of-date", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    manifest = build_research_data_observability(repo_root=args.repo_root, as_of_date=args.as_of_date)
    print(
        json.dumps(
            {
                key: manifest[key]
                for key in (
                    "schema_version",
                    "dataset_count",
                    "artifact_available_count",
                    "observe_only_count",
                    "blocked_count",
                    "missing_artifact_count",
                )
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

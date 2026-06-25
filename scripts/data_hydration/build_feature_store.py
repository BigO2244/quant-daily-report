#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research_data.features import FEATURE_SETS, build_feature_store


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build read-only FR-DH canonical feature artifacts.")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--as-of-date", default=None)
    parser.add_argument("--feature-set", action="append", default=[], choices=sorted(FEATURE_SETS), help="Feature set to build. Repeatable.")
    parser.add_argument("--feature-sets", nargs="+", default=[], choices=sorted(FEATURE_SETS), help="Feature sets to build.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    feature_sets = set(args.feature_set or []) | set(args.feature_sets or [])
    manifest = build_feature_store(
        repo_root=args.repo_root,
        as_of_date=args.as_of_date,
        feature_sets=feature_sets or None,
    )
    print(json.dumps({key: manifest[key] for key in ("schema_version", "feature_set_count", "built_feature_set_count", "failed_feature_set_count")}, indent=2, sort_keys=True))
    return 1 if manifest["failed_feature_set_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

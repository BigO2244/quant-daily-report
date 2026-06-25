#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research_data.normalization import P2_DATASETS, normalize_p2


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalize read-only FR-DH P2 hydrated samples into canonical artifacts.")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--as-of-date", default=None)
    parser.add_argument("--dataset", action="append", default=[], choices=sorted(P2_DATASETS), help="P2 dataset to normalize. Repeatable.")
    parser.add_argument("--datasets", nargs="+", default=[], choices=sorted(P2_DATASETS), help="P2 datasets to normalize.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    dataset_ids = set(args.dataset or []) | set(args.datasets or [])
    manifest = normalize_p2(
        repo_root=args.repo_root,
        as_of_date=args.as_of_date,
        dataset_ids=dataset_ids or None,
    )
    print(json.dumps({key: manifest[key] for key in ("schema_version", "dataset_count", "normalized_dataset_count", "failed_dataset_count")}, indent=2, sort_keys=True))
    return 1 if manifest["failed_dataset_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

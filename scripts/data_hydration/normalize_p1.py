#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research_data.normalization import P1_DATASETS, normalize_p1
from scripts.data_hydration.run_data_hydration_swarm import _resolve_symbols


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalize read-only FR-DH P1 hydrated samples into canonical artifacts.")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--as-of-date", default=None)
    parser.add_argument("--dataset", action="append", default=[], choices=sorted(P1_DATASETS), help="P1 dataset to normalize. Repeatable.")
    parser.add_argument("--datasets", nargs="+", default=[], choices=sorted(P1_DATASETS), help="P1 datasets to normalize.")
    parser.add_argument("--symbol", action="append", default=[], help="Symbol to retain in normalized security-level P1 artifacts. Repeatable.")
    parser.add_argument("--symbols", nargs="+", default=[], help="Symbols to retain in normalized security-level P1 artifacts.")
    parser.add_argument("--sleeve", "--sleeve-id", dest="sleeve_id", default=None, help="Resolve symbols from the latest legacy candidate for this sleeve.")
    parser.add_argument("--legacy-candidates-root", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    dataset_ids = set(args.dataset or []) | set(args.datasets or [])
    symbols = set(args.symbol or []) | set(args.symbols or [])
    if args.sleeve_id:
        symbols.update(
            _resolve_symbols(
                args.repo_root,
                str(args.as_of_date) if args.as_of_date else "9999-12-31",
                symbols=None,
                sleeve_id=args.sleeve_id,
                legacy_candidates_root=args.legacy_candidates_root,
            )
        )
    manifest = normalize_p1(
        repo_root=args.repo_root,
        as_of_date=args.as_of_date,
        dataset_ids=dataset_ids or None,
        symbols=symbols or None,
    )
    print(json.dumps({key: manifest[key] for key in ("schema_version", "dataset_count", "normalized_dataset_count", "failed_dataset_count")}, indent=2, sort_keys=True))
    return 1 if manifest["failed_dataset_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

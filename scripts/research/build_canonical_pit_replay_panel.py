#!/usr/bin/env python3
"""Build canonical security_id PIT replay price panel (RESEARCH_ONLY)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.canonical_replay_panel import build_canonical_price_panel, write_panel_artifacts  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build canonical security_id PIT replay price panel.")
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--family", default="caerus_large_cap")
    parser.add_argument("--start-date", default="2014-01-02")
    parser.add_argument("--end-date", default="2024-12-31")
    parser.add_argument("--replay-id", default="canonical_pit_replay")
    parser.add_argument("--output-dir", default="outputs/research/canonical_pit_replay/latest")
    args = parser.parse_args(argv)

    result = build_canonical_price_panel(
        repo_root=Path(args.repo_root),
        membership_family=args.family,
        start_date=args.start_date,
        end_date=args.end_date,
        replay_id=args.replay_id,
    )
    paths = write_panel_artifacts(result, Path(args.repo_root) / args.output_dir)
    print(json.dumps({"paths": paths, "manifest": result.manifest}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

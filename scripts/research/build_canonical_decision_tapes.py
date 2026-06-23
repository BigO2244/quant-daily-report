#!/usr/bin/env python3
"""Build canonical PIT decision tapes for governed sleeves (RESEARCH_ONLY)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.canonical_decision_tape import DEFAULT_SLEEVES, build_and_write_decision_tapes  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build canonical PIT decision tapes.")
    parser.add_argument("--panel", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output-dir", default="outputs/research/canonical_pit_replay/latest")
    parser.add_argument("--start-date", default="2014-01-02")
    parser.add_argument("--end-date", default="2024-12-31")
    parser.add_argument("--sleeves", default=",".join(DEFAULT_SLEEVES))
    args = parser.parse_args(argv)

    sleeves = tuple(item.strip() for item in args.sleeves.split(",") if item.strip())
    result = build_and_write_decision_tapes(
        panel_path=args.panel,
        manifest_path=args.manifest,
        output_dir=args.output_dir,
        start_date=args.start_date,
        end_date=args.end_date,
        sleeves=sleeves,
    )
    print(json.dumps({"paths": result.tape_paths, "manifest": result.manifest}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

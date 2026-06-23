#!/usr/bin/env python3
"""Build Polaris/Orion holdings-count concentration frontier artifacts."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.holdings_concentration_frontier import (  # noqa: E402
    DEFAULT_END_DATE,
    DEFAULT_MANIFEST_PATH,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_PANEL_PATH,
    DEFAULT_START_DATE,
    build_frontier_artifact,
)


def _floats(text: str) -> tuple[float, ...]:
    return tuple(float(item.strip()) for item in text.split(",") if item.strip())


def _ints(text: str) -> tuple[int, ...]:
    return tuple(int(item.strip()) for item in text.split(",") if item.strip())


def _strings(text: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in text.split(",") if item.strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Polaris/Orion holdings concentration frontier.")
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--artifact-date", default=date.today().isoformat())
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", default=DEFAULT_END_DATE)
    parser.add_argument("--panel", default=str(DEFAULT_PANEL_PATH))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST_PATH))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--top-n", default="3,4,5,6,7,8,9,10,12,15")
    parser.add_argument("--weighting-methods", default="equal,rank,score,score2")
    parser.add_argument("--max-position-weights", default="0.20,0.25,0.30,0.35,0.40")
    parser.add_argument("--min-position-weights", default="0.0,0.02,0.03,0.05")
    args = parser.parse_args(argv)

    payload = build_frontier_artifact(
        repo_root=Path(args.repo_root),
        artifact_date=args.artifact_date,
        start_date=args.start_date,
        end_date=args.end_date,
        panel_path=Path(args.panel),
        manifest_path=Path(args.manifest),
        output_root=Path(args.output_root),
        top_n_values=_ints(args.top_n),
        weighting_methods=_strings(args.weighting_methods),
        max_position_weights=_floats(args.max_position_weights),
        min_position_weights=_floats(args.min_position_weights),
    )
    print(json.dumps({
        "status": "OK",
        "variant_count": payload["variant_count"],
        "artifact_paths": payload.get("artifact_paths", {}),
        "recommendations": {
            sleeve: payload["best_variants"][sleeve]["final_recommendation"]
            for sleeve in payload["best_variants"]
        },
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

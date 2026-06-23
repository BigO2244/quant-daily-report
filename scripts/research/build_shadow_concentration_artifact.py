#!/usr/bin/env python3
"""Build daily research-only concentration shadow artifacts for Polaris and Orion."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.shadow_concentration import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_PRICE_PANEL_PATH,
    DEFAULT_SHADOW_CANDIDATE_ROOT,
    build_shadow_concentration_artifact,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build non-executing Polaris/Orion concentration shadow evidence artifacts."
    )
    parser.add_argument("--trade-date", default=None, help="YYYY-MM-DD. Defaults to latest available shadow candidate date.")
    parser.add_argument("--shadow-candidate-root", default=str(DEFAULT_SHADOW_CANDIDATE_ROOT))
    parser.add_argument("--price-panel", default=str(DEFAULT_PRICE_PANEL_PATH))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    args = parser.parse_args(argv)

    payload = build_shadow_concentration_artifact(
        trade_date=args.trade_date,
        shadow_candidate_root=Path(args.shadow_candidate_root),
        price_panel_path=Path(args.price_panel),
        output_root=Path(args.output_root),
    )
    print(
        json.dumps(
            {
                "status": "OK",
                "governance_label": payload["governance_label"],
                "trade_date": payload["trade_date"],
                "artifact_paths": payload["artifact_paths"],
                "return_status": payload["performance"]["next_day_return_context"]["status"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

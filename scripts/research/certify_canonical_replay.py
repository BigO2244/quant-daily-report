#!/usr/bin/env python3
"""Certify canonical PIT replay artifacts (RESEARCH_ONLY)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.replay_certification import certify_panel_artifacts  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Certify canonical PIT replay price panel artifacts.")
    parser.add_argument("--panel", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", default=None)
    parser.add_argument("--require-decision-grade-membership", action="store_true")
    parser.add_argument(
        "--require-decision-grade-scale",
        action="store_true",
        help="Backward-compatible alias for --require-decision-grade-membership.",
    )
    args = parser.parse_args(argv)

    result = certify_panel_artifacts(
        panel_path=args.panel,
        manifest_path=args.manifest,
        require_decision_grade_membership=(
            args.require_decision_grade_membership or args.require_decision_grade_scale
        ),
        output_path=args.output,
    )
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    return 0 if result.status == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(main())

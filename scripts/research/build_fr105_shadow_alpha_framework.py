#!/usr/bin/env python3
"""Write the default-off FR-105 Shadow Alpha Chase framework artifact."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.fr105_replay_contract import DEFAULT_OUTPUT_ROOT  # noqa: E402
from research.fr105_shadow_alpha_framework import write_shadow_alpha_chase_framework  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the default-off FR-105 Shadow Alpha Chase framework.")
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--generated-at", default="unavailable")
    args = parser.parse_args(argv)

    path, payload = write_shadow_alpha_chase_framework(
        repo_root=Path(args.repo_root),
        output_root=Path(args.output_root),
        generated_at=args.generated_at,
    )
    print(
        json.dumps(
            {
                "artifact_path": str(path),
                "enabled": payload["metadata"]["enabled"],
                "status": payload["evaluation_status"]["status"],
                "paper_or_live_influence_allowed": payload["evaluation_status"]["paper_or_live_influence_allowed"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

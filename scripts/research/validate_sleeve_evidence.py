#!/usr/bin/env python3
"""Validate a research-only FR-069 sleeve evidence envelope."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research_registry.sleeves import DEFAULT_MANIFEST_PATH, validate_sleeve_evidence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate an FR-069 sleeve evidence envelope.")
    parser.add_argument("--artifact", required=True, help="Path to sleeve evidence JSON artifact.")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST_PATH), help="Path to sleeve manifest JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = validate_sleeve_evidence(args.artifact, manifest_path=args.manifest)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if bool(payload.get("valid")) else 1


if __name__ == "__main__":
    raise SystemExit(main())

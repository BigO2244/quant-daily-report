#!/usr/bin/env python3
"""Validate the FR-069 research-only sleeve manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research_registry.sleeves import DEFAULT_MANIFEST_PATH, sleeve_inventory_payload, validate_sleeve_manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate FR-069 sleeve manifest metadata.")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST_PATH), help="Path to sleeve manifest JSON.")
    parser.add_argument("--inventory", action="store_true", help="Print compact MCP-style inventory instead of full validation payload.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifest_path = Path(args.manifest)
    payload: dict[str, Any]
    if args.inventory:
        payload = sleeve_inventory_payload(manifest_path)
        ok = payload.get("status") == "OK"
    else:
        payload = validate_sleeve_manifest(manifest_path)
        ok = bool(payload.get("valid"))
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

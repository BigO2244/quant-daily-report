#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research_data.catalog import validate_catalog
from research_data.hydration import read_json


def validate_catalog_artifact(path: Path) -> list[str]:
    payload = read_json(path)
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return ["catalog artifact missing entries list"]
    errors = validate_catalog(entries)
    if payload.get("schema_version") != "research_data_catalog_v1":
        errors.append("catalog schema_version must be research_data_catalog_v1")
    return errors


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate research data catalog artifact.")
    parser.add_argument("--path", type=Path, default=REPO_ROOT / "data" / "manifests" / "research_data_catalog.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    errors = validate_catalog_artifact(args.path)
    print(json.dumps({"status": "FAIL" if errors else "OK", "errors": errors}, indent=2, sort_keys=True))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

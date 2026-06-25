#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research_data.hydration import read_json


ALLOWED_FRESHNESS = {"OK", "WARN_STALE", "WARN_PARTIAL", "FAIL_MISSING", "FAIL_SCHEMA", "FAIL_PIT_VIOLATION"}


def validate_freshness_artifact(path: Path) -> list[str]:
    payload = read_json(path)
    errors: list[str] = []
    rows = payload.get("datasets")
    if payload.get("schema_version") != "dataset_freshness_v1":
        errors.append("dataset_freshness schema_version must be dataset_freshness_v1")
    if not isinstance(rows, list):
        return errors + ["dataset_freshness artifact missing datasets list"]
    for row in rows:
        dataset_id = row.get("dataset_id", "<missing>")
        if row.get("freshness_status") not in ALLOWED_FRESHNESS:
            errors.append(f"{dataset_id}: invalid freshness_status {row.get('freshness_status')}")
        for field in ("hydration_status", "latest_ingestion_timestamp", "as_of_date", "validation_status", "PIT_safe_status"):
            if field not in row:
                errors.append(f"{dataset_id}: missing {field}")
    return errors


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate dataset freshness artifact.")
    parser.add_argument("--path", type=Path, default=REPO_ROOT / "data" / "manifests" / "dataset_freshness.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    errors = validate_freshness_artifact(args.path)
    print(json.dumps({"status": "FAIL" if errors else "OK", "errors": errors}, indent=2, sort_keys=True))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

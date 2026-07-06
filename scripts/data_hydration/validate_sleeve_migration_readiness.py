#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research_data.hydration import read_json
from research_data.migration import RUNTIME_IMPACT, SCHEMA_VERSION


ALLOWED_STATUS = {"READY_OBSERVE_ONLY", "WARN", "BLOCKED"}
ALLOWED_REQUIREMENT_STATUS = {"READY", "WARN", "BLOCKED"}


def validate_sleeve_migration_readiness(path: Path) -> list[str]:
    payload = read_json(path)
    errors: list[str] = []
    if payload.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if payload.get("runtime_impact") != RUNTIME_IMPACT:
        errors.append(f"runtime_impact must be {RUNTIME_IMPACT}")
    for flag in ("broker_submission_invoked", "sleeve_runtime_invoked"):
        if payload.get(flag) is not False:
            errors.append(f"{flag} must be false")
    sleeves = payload.get("sleeves")
    if not isinstance(sleeves, list):
        return errors + ["sleeves must be a list"]
    if payload.get("sleeve_count") != len(sleeves):
        errors.append("sleeve_count mismatch")
    if payload.get("ready_observe_only_count") != sum(1 for row in sleeves if row.get("migration_readiness_status") == "READY_OBSERVE_ONLY"):
        errors.append("ready_observe_only_count mismatch")
    if payload.get("warn_count") != sum(1 for row in sleeves if row.get("migration_readiness_status") == "WARN"):
        errors.append("warn_count mismatch")
    if payload.get("blocked_count") != sum(1 for row in sleeves if row.get("migration_readiness_status") == "BLOCKED"):
        errors.append("blocked_count mismatch")
    seen = set()
    for row in sleeves:
        errors.extend(_validate_sleeve_row(row, seen))
    return errors


def _validate_sleeve_row(row: dict[str, Any], seen: set[str]) -> list[str]:
    errors: list[str] = []
    sleeve_id = str(row.get("sleeve_id") or "<missing>")
    if sleeve_id in seen:
        errors.append(f"duplicate sleeve_id: {sleeve_id}")
    seen.add(sleeve_id)
    for field in ("sleeve_id", "strategy_id", "family", "migration_readiness_status", "required_dataset_ids", "dataset_requirements"):
        if row.get(field) in (None, "", []):
            errors.append(f"{sleeve_id}: missing {field}")
    status = row.get("migration_readiness_status")
    if status not in ALLOWED_STATUS:
        errors.append(f"{sleeve_id}: invalid migration_readiness_status {status}")
    requirements = row.get("dataset_requirements") or []
    blocking = _unique([item.get("dataset_id") for item in requirements if item.get("requirement_status") == "BLOCKED"])
    warnings = _unique([item.get("dataset_id") for item in requirements if item.get("requirement_status") == "WARN"])
    if row.get("blocking_dataset_ids") != blocking:
        errors.append(f"{sleeve_id}: blocking_dataset_ids mismatch")
    if row.get("warning_dataset_ids") != warnings:
        errors.append(f"{sleeve_id}: warning_dataset_ids mismatch")
    if status == "READY_OBSERVE_ONLY" and (blocking or warnings):
        errors.append(f"{sleeve_id}: ready sleeve cannot include blockers or warnings")
    if status == "WARN" and (blocking or not warnings):
        errors.append(f"{sleeve_id}: warn sleeve requires warnings and no blockers")
    if status == "BLOCKED" and not blocking:
        errors.append(f"{sleeve_id}: blocked sleeve requires blocking datasets")
    for item in requirements:
        if item.get("requirement_status") not in ALLOWED_REQUIREMENT_STATUS:
            errors.append(f"{sleeve_id}: invalid requirement_status for {item.get('dataset_id')}")
    return errors


def _unique(values: list[Any]) -> list[Any]:
    seen = set()
    unique_values = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique_values.append(value)
    return unique_values


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate read-only FR-DH sleeve migration readiness artifacts.")
    parser.add_argument("--path", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    path = args.path or _latest_default()
    errors = validate_sleeve_migration_readiness(path)
    print(json.dumps({"status": "FAIL" if errors else "OK", "errors": errors}, indent=2, sort_keys=True))
    return 1 if errors else 0


def _latest_default() -> Path:
    root = REPO_ROOT / "outputs" / "research" / "data_migration"
    dates = sorted(path for path in root.iterdir() if path.is_dir()) if root.exists() else []
    if not dates:
        return root / "missing" / "migration_readiness.json"
    return dates[-1] / "migration_readiness.json"


if __name__ == "__main__":
    raise SystemExit(main())

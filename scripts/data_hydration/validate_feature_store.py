#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research_data.features import FEATURE_DEFINITIONS, FEATURE_SETS
from research_data.hydration import read_json


ALLOWED_STATUS = {"OK", "WARN", "MISSING_INPUT"}
ALLOWED_VALIDATION_STATUS = {"PASS", "WARN"}


def validate_feature_store_manifest(path: Path, *, repo_root: Path | None = None) -> list[str]:
    payload = read_json(path)
    root = Path(repo_root or REPO_ROOT)
    errors: list[str] = []
    if payload.get("schema_version") != "feature_store_manifest_v1":
        errors.append("feature store schema_version must be feature_store_manifest_v1")
    if payload.get("runtime_impact") != "read_only_feature_generation_no_trading_path_changes":
        errors.append("feature store runtime_impact must be read_only_feature_generation_no_trading_path_changes")
    feature_sets = payload.get("feature_sets")
    if not isinstance(feature_sets, list):
        return errors + ["feature store manifest missing feature_sets list"]
    if payload.get("feature_set_count") != len(feature_sets):
        errors.append(f"feature_set_count mismatch: {payload.get('feature_set_count')} != {len(feature_sets)}")
    built_count = sum(1 for item in feature_sets if item.get("status") in {"OK", "WARN"})
    failed_count = sum(1 for item in feature_sets if item.get("status") not in {"OK", "WARN"})
    if payload.get("built_feature_set_count") != built_count:
        errors.append(f"built_feature_set_count mismatch: {payload.get('built_feature_set_count')} != {built_count}")
    if payload.get("failed_feature_set_count") != failed_count:
        errors.append(f"failed_feature_set_count mismatch: {payload.get('failed_feature_set_count')} != {failed_count}")
    errors.extend(_validate_metadata_artifacts(payload, root))

    seen: set[str] = set()
    for item in feature_sets:
        feature_set = str(item.get("feature_set") or "<missing>")
        seen.add(feature_set)
        status = item.get("status")
        if feature_set not in FEATURE_SETS:
            errors.append(f"{feature_set}: unsupported feature set")
        if status not in ALLOWED_STATUS:
            errors.append(f"{feature_set}: invalid status {status}")
        if item.get("validation_status") not in ALLOWED_VALIDATION_STATUS:
            errors.append(f"{feature_set}: invalid validation_status {item.get('validation_status')}")
        artifact_value = item.get("artifact_path")
        if status == "MISSING_INPUT":
            if artifact_value:
                errors.append(f"{feature_set}: missing-input feature set must not point to an artifact")
            continue
        if not artifact_value:
            errors.append(f"{feature_set}: built feature set missing artifact_path")
            continue
        artifact_path = _resolve_path(root, artifact_value)
        if not artifact_path.exists():
            errors.append(f"{feature_set}: feature artifact does not exist: {artifact_path}")
            continue
        errors.extend(_validate_feature_artifact(feature_set, artifact_path, int(item.get("row_count") or 0), root))
    duplicate_count = len(feature_sets) - len(seen)
    if duplicate_count:
        errors.append(f"feature store manifest contains {duplicate_count} duplicate feature sets")
    return errors


def _validate_metadata_artifacts(payload: dict[str, Any], repo_root: Path) -> list[str]:
    errors: list[str] = []
    definitions_path = payload.get("feature_definitions_path")
    coverage_path = payload.get("feature_coverage_path")
    if not definitions_path:
        errors.append("feature_store_manifest missing feature_definitions_path")
    else:
        path = _resolve_path(repo_root, str(definitions_path))
        if not path.exists():
            errors.append(f"feature definitions artifact missing: {path}")
        else:
            errors.extend(_validate_feature_definitions(path))
    if not coverage_path:
        errors.append("feature_store_manifest missing feature_coverage_path")
    else:
        path = _resolve_path(repo_root, str(coverage_path))
        if not path.exists():
            errors.append(f"feature coverage artifact missing: {path}")
        else:
            errors.extend(_validate_feature_coverage(path, payload))
    return errors


def _validate_feature_definitions(path: Path) -> list[str]:
    payload = read_json(path)
    errors: list[str] = []
    if payload.get("schema_version") != "feature_definitions_v1":
        errors.append("feature definitions schema_version must be feature_definitions_v1")
    rows = payload.get("definitions")
    if not isinstance(rows, list):
        return errors + ["feature definitions missing definitions list"]
    if payload.get("feature_definition_count") != len(rows):
        errors.append("feature_definition_count mismatch")
    seen_sets = {row.get("feature_set") for row in rows}
    if seen_sets != set(FEATURE_DEFINITIONS):
        errors.append(f"feature definitions set mismatch: {sorted(seen_sets)}")
    for idx, row in enumerate(rows):
        for field in ("feature_set", "feature_name", "feature_version", "input_dataset_ids", "input_fields", "definition", "PIT_safe_status"):
            if row.get(field) in (None, "", []):
                errors.append(f"feature definition {idx} missing {field}")
    return errors


def _validate_feature_coverage(path: Path, manifest: dict[str, Any]) -> list[str]:
    payload = read_json(path)
    errors: list[str] = []
    if payload.get("schema_version") != "feature_coverage_v1":
        errors.append("feature coverage schema_version must be feature_coverage_v1")
    rows = payload.get("coverage")
    if not isinstance(rows, list):
        return errors + ["feature coverage missing coverage list"]
    if payload.get("feature_set_count") != len(rows):
        errors.append("feature coverage feature_set_count mismatch")
    manifest_counts = {item.get("feature_set"): int(item.get("row_count") or 0) for item in manifest.get("feature_sets") or []}
    seen = set()
    for row in rows:
        feature_set = row.get("feature_set")
        seen.add(feature_set)
        if row.get("row_count") != manifest_counts.get(feature_set):
            errors.append(f"{feature_set}: coverage row_count mismatch")
        for feature in row.get("features") or []:
            coverage_ratio = feature.get("coverage_ratio")
            if coverage_ratio is None or not 0 <= float(coverage_ratio) <= 1:
                errors.append(f"{feature_set}: invalid coverage_ratio for {feature.get('feature_name')}")
    if seen != set(manifest_counts):
        errors.append(f"feature coverage set mismatch: {sorted(seen)}")
    return errors


def _validate_feature_artifact(feature_set: str, artifact_path: Path, expected_row_count: int, repo_root: Path) -> list[str]:
    payload = read_json(artifact_path)
    errors: list[str] = []
    if payload.get("feature_set") != feature_set:
        errors.append(f"{feature_set}: artifact feature_set mismatch: {payload.get('feature_set')}")
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return errors + [f"{feature_set}: feature artifact missing rows list"]
    if payload.get("row_count") != len(rows):
        errors.append(f"{feature_set}: artifact row_count mismatch: {payload.get('row_count')} != {len(rows)}")
    if expected_row_count != len(rows):
        errors.append(f"{feature_set}: manifest row_count mismatch: {expected_row_count} != {len(rows)}")
    input_artifacts = payload.get("input_artifacts")
    if not isinstance(input_artifacts, list) or not input_artifacts:
        errors.append(f"{feature_set}: feature artifact missing input_artifacts")
    else:
        errors.extend(_validate_input_artifacts(feature_set, input_artifacts, repo_root))
    for idx, row in enumerate(rows):
        for field in ("feature_id", "feature_set", "feature_version", "security_id", "feature_date", "as_of_date", "input_artifact_digest", "generated_at"):
            if row.get(field) in (None, ""):
                errors.append(f"{feature_set}: row {idx} missing {field}")
        if row.get("feature_date") and row.get("as_of_date") and str(row["feature_date"]) > str(row["as_of_date"]):
            errors.append(f"{feature_set}: row {idx} violates feature_date <= as_of_date")
    return errors


def _validate_input_artifacts(feature_set: str, input_artifacts: list[dict[str, Any]], repo_root: Path) -> list[str]:
    errors: list[str] = []
    for idx, source in enumerate(input_artifacts):
        path_value = source.get("path")
        digest = source.get("sha256")
        if not path_value:
            errors.append(f"{feature_set}: input_artifacts[{idx}] missing path")
            continue
        if not digest:
            errors.append(f"{feature_set}: input_artifacts[{idx}] missing sha256")
            continue
        path = _resolve_path(repo_root, str(path_value))
        if path.exists():
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != digest:
                errors.append(f"{feature_set}: input_artifacts[{idx}] sha256 mismatch")
    return errors


def _resolve_path(repo_root: Path, path_value: str) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else repo_root / path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate read-only FR-DH feature-store artifacts.")
    parser.add_argument("--path", type=Path, default=REPO_ROOT / "data" / "manifests" / "feature_store_manifest.json")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    errors = validate_feature_store_manifest(args.path, repo_root=args.repo_root)
    print(json.dumps({"status": "FAIL" if errors else "OK", "errors": errors}, indent=2, sort_keys=True))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

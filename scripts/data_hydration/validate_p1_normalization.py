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

from research_data.hydration import read_json
from research_data.normalization import P1_DATASETS


ALLOWED_DATASET_STATUS = {"OK", "WARN", "MISSING_SOURCE"}
ALLOWED_VALIDATION_STATUS = {"PASS", "WARN"}
DATE_FIELDS = {
    "trade_date",
    "effective_date",
    "effective_start_date",
    "latest_source_observation_date",
    "as_of_date",
}


def validate_p1_normalization_artifact(path: Path, *, repo_root: Path | None = None) -> list[str]:
    payload = read_json(path)
    root = Path(repo_root or REPO_ROOT)
    errors: list[str] = []

    if payload.get("schema_version") != "p1_normalization_manifest_v1":
        errors.append("p1 normalization schema_version must be p1_normalization_manifest_v1")
    if payload.get("runtime_impact") != "read_only_normalization_no_trading_path_changes":
        errors.append("p1 normalization runtime_impact must be read_only_normalization_no_trading_path_changes")
    for field in ("generated_at", "as_of_date", "dataset_count", "normalized_dataset_count", "failed_dataset_count"):
        if field not in payload:
            errors.append(f"p1 normalization manifest missing {field}")

    datasets = payload.get("datasets")
    if not isinstance(datasets, list):
        return errors + ["p1 normalization manifest missing datasets list"]

    if payload.get("dataset_count") != len(datasets):
        errors.append(f"dataset_count mismatch: {payload.get('dataset_count')} != {len(datasets)}")
    normalized_count = sum(1 for item in datasets if item.get("status") in {"OK", "WARN"})
    failed_count = sum(1 for item in datasets if item.get("status") not in {"OK", "WARN"})
    if payload.get("normalized_dataset_count") != normalized_count:
        errors.append(f"normalized_dataset_count mismatch: {payload.get('normalized_dataset_count')} != {normalized_count}")
    if payload.get("failed_dataset_count") != failed_count:
        errors.append(f"failed_dataset_count mismatch: {payload.get('failed_dataset_count')} != {failed_count}")

    seen_dataset_ids: set[str] = set()
    for item in datasets:
        dataset_id = str(item.get("dataset_id") or "<missing>")
        seen_dataset_ids.add(dataset_id)
        status = item.get("status")
        if dataset_id not in P1_DATASETS:
            errors.append(f"{dataset_id}: unsupported P1 dataset")
        if status not in ALLOWED_DATASET_STATUS:
            errors.append(f"{dataset_id}: invalid status {status}")
        if item.get("validation_status") not in ALLOWED_VALIDATION_STATUS:
            errors.append(f"{dataset_id}: invalid validation_status {item.get('validation_status')}")

        artifact_value = item.get("artifact_path")
        if status == "MISSING_SOURCE":
            if artifact_value:
                errors.append(f"{dataset_id}: missing-source dataset must not point to a normalized artifact")
            if int(item.get("row_count") or 0) != 0:
                errors.append(f"{dataset_id}: missing-source dataset row_count must be 0")
            continue

        if not artifact_value:
            errors.append(f"{dataset_id}: normalized dataset missing artifact_path")
            continue

        artifact_path = _resolve_path(root, artifact_value)
        if not artifact_path.exists():
            errors.append(f"{dataset_id}: normalized artifact does not exist: {artifact_path}")
            continue
        errors.extend(_validate_normalized_dataset_artifact(dataset_id, artifact_path, int(item.get("row_count") or 0), root))

    duplicate_count = len(datasets) - len(seen_dataset_ids)
    if duplicate_count:
        errors.append(f"p1 normalization manifest contains {duplicate_count} duplicate dataset ids")
    return errors


def _validate_normalized_dataset_artifact(dataset_id: str, artifact_path: Path, expected_row_count: int, repo_root: Path) -> list[str]:
    payload = read_json(artifact_path)
    errors: list[str] = []
    if payload.get("dataset_id") != dataset_id:
        errors.append(f"{dataset_id}: artifact dataset_id mismatch: {payload.get('dataset_id')}")
    if payload.get("schema_version") != f"{dataset_id}_normalized_v1":
        errors.append(f"{dataset_id}: artifact schema_version mismatch: {payload.get('schema_version')}")
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return errors + [f"{dataset_id}: normalized artifact missing rows list"]
    if payload.get("row_count") != len(rows):
        errors.append(f"{dataset_id}: artifact row_count mismatch: {payload.get('row_count')} != {len(rows)}")
    if expected_row_count != len(rows):
        errors.append(f"{dataset_id}: manifest row_count mismatch: {expected_row_count} != {len(rows)}")
    validation = payload.get("validation") or {}
    if validation.get("status") not in ALLOWED_VALIDATION_STATUS:
        errors.append(f"{dataset_id}: artifact validation status invalid: {validation.get('status')}")
    source_artifacts = payload.get("source_artifacts")
    if not isinstance(source_artifacts, list) or not source_artifacts:
        errors.append(f"{dataset_id}: normalized artifact missing source_artifacts")
    else:
        errors.extend(_validate_source_artifacts(dataset_id, source_artifacts, repo_root))
    errors.extend(_validate_row_dates(dataset_id, rows))
    if dataset_id == "security_master_pit":
        errors.extend(_validate_security_master_artifact(payload, rows))
    return errors


def _validate_source_artifacts(dataset_id: str, source_artifacts: list[dict[str, Any]], repo_root: Path) -> list[str]:
    errors: list[str] = []
    for idx, source in enumerate(source_artifacts):
        source_path_value = source.get("path")
        source_digest = source.get("sha256")
        if not source_path_value:
            errors.append(f"{dataset_id}: source_artifacts[{idx}] missing path")
            continue
        if not source_digest:
            errors.append(f"{dataset_id}: source_artifacts[{idx}] missing sha256")
            continue
        source_path = _resolve_path(repo_root, str(source_path_value))
        if source_path.exists():
            actual = hashlib.sha256(source_path.read_bytes()).hexdigest()
            if actual != source_digest:
                errors.append(f"{dataset_id}: source_artifacts[{idx}] sha256 mismatch")
    return errors


def _validate_row_dates(dataset_id: str, rows: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for idx, row in enumerate(rows):
        as_of_date = row.get("as_of_date")
        if not as_of_date:
            errors.append(f"{dataset_id}: row {idx} missing as_of_date")
            continue
        for field in DATE_FIELDS:
            value = row.get(field)
            if value in (None, "") or field == "as_of_date":
                continue
            text = str(value)[:10]
            if len(text) == 10 and text > str(as_of_date)[:10]:
                errors.append(f"{dataset_id}: row {idx} violates {field} <= as_of_date: {text} > {as_of_date}")
    return errors


def _validate_security_master_artifact(payload: dict[str, Any], rows: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    validation = payload.get("validation") or {}
    validation_errors = [str(item) for item in validation.get("errors") or []]
    grade = payload.get("security_master_grade") or {}
    if grade.get("status") not in {"PIT_GRADE", "CURRENT_REFERENCE_ONLY", "PARTIAL_PIT_GRADE", "EMPTY"}:
        errors.append(f"security_master_pit: invalid security_master_grade status {grade.get('status')}")
    pit_count = sum(1 for row in rows if str(row.get("pit_grade_status") or "").startswith("PIT_GRADE"))
    current_count = sum(1 for row in rows if row.get("is_current_reference") is True)
    if int(grade.get("pit_grade_row_count") or 0) != pit_count:
        errors.append("security_master_pit: pit_grade_row_count mismatch")
    if int(grade.get("current_reference_row_count") or 0) != current_count:
        errors.append("security_master_pit: current_reference_row_count mismatch")
    by_ticker: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for idx, row in enumerate(rows):
        for field in ("security_id", "source_security_id", "ticker", "source_table", "effective_start_date", "pit_grade_status", "validation_status"):
            if row.get(field) in (None, ""):
                errors.append(f"security_master_pit: row {idx} missing {field}")
        if row.get("is_current_reference") is True and validation.get("status") != "WARN":
            errors.append(f"security_master_pit: row {idx} current-reference fallback must keep artifact validation at WARN")
        if row.get("is_current_reference") is True and not any("current-reference security master fallback" in item for item in validation_errors):
            errors.append(f"security_master_pit: row {idx} current-reference fallback missing validation warning")
        if str(row.get("pit_grade_status") or "").startswith("PIT_GRADE") and row.get("is_current_reference") is True:
            errors.append(f"security_master_pit: row {idx} cannot be both PIT-grade and current-reference")
        if row.get("is_active") is False and not row.get("effective_end_date"):
            errors.append(f"security_master_pit: row {idx} inactive security missing effective_end_date")
        if row.get("delisting_date") and row.get("is_active") is True:
            errors.append(f"security_master_pit: row {idx} active security has delisting_date")
        ticker = str(row.get("ticker") or "")
        if ticker:
            by_ticker.setdefault(ticker, []).append((idx, row))
    for ticker, indexed_rows in by_ticker.items():
        sorted_rows = sorted(indexed_rows, key=lambda item: str(item[1].get("effective_start_date") or ""))
        seen_windows: set[tuple[str, str]] = set()
        previous_end: str | None = None
        for idx, row in sorted_rows:
            start = str(row.get("effective_start_date") or "")
            end = str(row.get("effective_end_date") or "9999-12-31")
            window = (start, end)
            if window in seen_windows:
                errors.append(f"security_master_pit: {ticker} duplicate effective window {start}..{end}")
            seen_windows.add(window)
            if previous_end and start and start <= previous_end:
                errors.append(f"security_master_pit: {ticker} overlapping effective window at row {idx}")
            previous_end = end
    return errors


def _resolve_path(repo_root: Path, path_value: str) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else repo_root / path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate P1 normalized research data artifacts.")
    parser.add_argument("--path", type=Path, default=REPO_ROOT / "data" / "manifests" / "p1_normalization_manifest.json")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    errors = validate_p1_normalization_artifact(args.path, repo_root=args.repo_root)
    print(json.dumps({"status": "FAIL" if errors else "OK", "errors": errors}, indent=2, sort_keys=True))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

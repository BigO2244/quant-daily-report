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


ALLOWED_READINESS = {"OBSERVE_ONLY", "BLOCKED", "MISSING_ARTIFACT"}
ALLOWED_LINEAGE = {"LINEAGE_RECORDED", "LINEAGE_MISSING", "NO_ARTIFACT"}


def validate_research_data_observability(path: Path, *, repo_root: Path | None = None) -> list[str]:
    payload = read_json(path)
    root = Path(repo_root or REPO_ROOT)
    errors: list[str] = []
    if payload.get("schema_version") != "research_data_observability_v1":
        errors.append("observability schema_version must be research_data_observability_v1")
    if payload.get("runtime_impact") != "read_only_observability_no_trading_path_changes":
        errors.append("observability runtime_impact must be read_only_observability_no_trading_path_changes")
    rows = payload.get("datasets")
    if not isinstance(rows, list):
        return errors + ["observability manifest missing datasets list"]
    if payload.get("dataset_count") != len(rows):
        errors.append(f"dataset_count mismatch: {payload.get('dataset_count')} != {len(rows)}")
    if payload.get("artifact_available_count") != sum(1 for row in rows if row.get("artifact_exists")):
        errors.append("artifact_available_count mismatch")
    if payload.get("observe_only_count") != sum(1 for row in rows if row.get("readiness_status") == "OBSERVE_ONLY"):
        errors.append("observe_only_count mismatch")
    if payload.get("blocked_count") != sum(1 for row in rows if row.get("readiness_status") == "BLOCKED"):
        errors.append("blocked_count mismatch")
    if payload.get("missing_artifact_count") != sum(1 for row in rows if row.get("readiness_status") == "MISSING_ARTIFACT"):
        errors.append("missing_artifact_count mismatch")

    seen: set[str] = set()
    for idx, row in enumerate(rows):
        dataset_id = str(row.get("dataset_id") or f"<row {idx}>")
        if dataset_id in seen:
            errors.append(f"duplicate dataset_id: {dataset_id}")
        seen.add(dataset_id)
        for field in ("dataset_id", "dataset_name", "tier", "domain", "catalog_status", "readiness_status", "normalization_stage", "lineage_status", "as_of_date", "generated_at"):
            if row.get(field) in (None, ""):
                errors.append(f"{dataset_id}: missing {field}")
        if row.get("readiness_status") not in ALLOWED_READINESS:
            errors.append(f"{dataset_id}: invalid readiness_status {row.get('readiness_status')}")
        if row.get("lineage_status") not in ALLOWED_LINEAGE:
            errors.append(f"{dataset_id}: invalid lineage_status {row.get('lineage_status')}")
        artifact_value = row.get("artifact_path")
        if row.get("artifact_exists"):
            if not artifact_value:
                errors.append(f"{dataset_id}: artifact_exists true but artifact_path missing")
                continue
            artifact_path = _resolve_path(root, str(artifact_value))
            if not artifact_path.exists():
                errors.append(f"{dataset_id}: artifact does not exist: {artifact_path}")
                continue
            actual_digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
            if row.get("artifact_sha256") != actual_digest:
                errors.append(f"{dataset_id}: artifact_sha256 mismatch")
            artifact_payload = read_json(artifact_path)
            if int(row.get("row_count") or 0) != int(artifact_payload.get("row_count") or 0):
                errors.append(f"{dataset_id}: row_count mismatch against artifact")
            errors.extend(_validate_lineage_paths(dataset_id, row.get("source_artifacts") or [], root))
            errors.extend(_validate_lineage_paths(dataset_id, row.get("input_artifacts") or [], root))
    return errors


def _validate_lineage_paths(dataset_id: str, artifacts: list[dict[str, Any]], repo_root: Path) -> list[str]:
    errors: list[str] = []
    for idx, artifact in enumerate(artifacts):
        path_value = artifact.get("path")
        digest = artifact.get("sha256")
        if not path_value:
            errors.append(f"{dataset_id}: lineage artifact {idx} missing path")
            continue
        if not digest:
            errors.append(f"{dataset_id}: lineage artifact {idx} missing sha256")
            continue
        path = _resolve_path(repo_root, str(path_value))
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            errors.append(f"{dataset_id}: lineage artifact {idx} sha256 mismatch")
    return errors


def _resolve_path(repo_root: Path, path_value: str) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else repo_root / path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate read-only FR-DH research data observability manifest.")
    parser.add_argument("--path", type=Path, default=REPO_ROOT / "data" / "manifests" / "research_data_observability.json")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    errors = validate_research_data_observability(args.path, repo_root=args.repo_root)
    print(json.dumps({"status": "FAIL" if errors else "OK", "errors": errors}, indent=2, sort_keys=True))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

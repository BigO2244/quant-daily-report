from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from research_data.api import NORMALIZED_ARTIFACTS
from research_data.catalog import catalog_entries
from research_data.features import FEATURE_SETS
from research_data.hydration import read_json, utc_now_iso, write_json
from research_data.normalization import P1_DATASETS, P2_DATASETS, P3_DATASETS


def build_research_data_observability(*, repo_root: Path, as_of_date: str | None = None) -> dict[str, Any]:
    repo_root = Path(repo_root)
    effective_as_of = as_of_date or datetime.now(UTC).date().isoformat()
    generated_at = utc_now_iso()

    freshness_by_dataset = _freshness_by_dataset(repo_root)
    normalization_by_dataset = _normalization_by_dataset(repo_root)
    feature_by_dataset = _feature_by_dataset(repo_root)
    rows = []
    for entry in catalog_entries():
        dataset_id = str(entry["dataset_id"])
        rows.append(
            _dataset_observability_row(
                repo_root=repo_root,
                entry=entry,
                as_of_date=effective_as_of,
                generated_at=generated_at,
                freshness=freshness_by_dataset.get(dataset_id),
                normalization=normalization_by_dataset.get(dataset_id),
                feature=feature_by_dataset.get(dataset_id),
            )
        )

    payload = {
        "schema_version": "research_data_observability_v1",
        "generated_at": generated_at,
        "as_of_date": effective_as_of,
        "runtime_impact": "read_only_observability_no_trading_path_changes",
        "dataset_count": len(rows),
        "artifact_available_count": sum(1 for row in rows if row["artifact_exists"]),
        "observe_only_count": sum(1 for row in rows if row["readiness_status"] == "OBSERVE_ONLY"),
        "blocked_count": sum(1 for row in rows if row["readiness_status"] == "BLOCKED"),
        "missing_artifact_count": sum(1 for row in rows if row["readiness_status"] == "MISSING_ARTIFACT"),
        "datasets": rows,
    }
    write_json(repo_root / "data" / "manifests" / "research_data_observability.json", payload)
    return payload


def _dataset_observability_row(
    *,
    repo_root: Path,
    entry: dict[str, Any],
    as_of_date: str,
    generated_at: str,
    freshness: dict[str, Any] | None,
    normalization: dict[str, Any] | None,
    feature: dict[str, Any] | None,
) -> dict[str, Any]:
    dataset_id = str(entry["dataset_id"])
    rel_artifact = NORMALIZED_ARTIFACTS.get(dataset_id)
    artifact_path = repo_root / rel_artifact if rel_artifact else None
    artifact_payload = _read_optional_json(artifact_path)
    manifest_row = normalization or feature
    row_count = _coalesce_int(
        (artifact_payload or {}).get("row_count"),
        (manifest_row or {}).get("row_count"),
        (freshness or {}).get("records_written"),
    )
    validation = (artifact_payload or {}).get("validation") or {}
    source_artifacts = (artifact_payload or {}).get("source_artifacts") or []
    input_artifacts = (artifact_payload or {}).get("input_artifacts") or []
    artifact_exists = bool(artifact_path and artifact_path.exists())
    artifact_digest = _sha256(artifact_path) if artifact_exists and artifact_path else None
    catalog_status = str(entry.get("status") or "")
    readiness_status = _readiness_status(
        catalog_status=catalog_status,
        artifact_exists=artifact_exists,
        manifest_row=manifest_row,
        freshness=freshness,
    )
    return {
        "dataset_id": dataset_id,
        "dataset_name": entry.get("dataset_name"),
        "tier": entry.get("tier"),
        "domain": entry.get("domain"),
        "catalog_status": catalog_status,
        "readiness_status": readiness_status,
        "normalization_stage": _normalization_stage(dataset_id),
        "artifact_path": str(artifact_path) if artifact_path else None,
        "artifact_exists": artifact_exists,
        "artifact_sha256": artifact_digest,
        "row_count": row_count,
        "schema_version": (artifact_payload or {}).get("schema_version"),
        "validation_status": validation.get("status") or (manifest_row or {}).get("validation_status") or (freshness or {}).get("validation_status"),
        "validation_errors": validation.get("errors") or (manifest_row or {}).get("validation_errors") or [],
        "freshness_status": (freshness or {}).get("freshness_status"),
        "hydration_status": (freshness or {}).get("hydration_status"),
        "latest_ingestion_timestamp": (freshness or {}).get("latest_ingestion_timestamp"),
        "PIT_safe_status": (freshness or {}).get("PIT_safe_status") or _artifact_pit_status(artifact_payload),
        "source_artifact_count": len(source_artifacts),
        "input_artifact_count": len(input_artifacts),
        "lineage_status": _lineage_status(source_artifacts, input_artifacts, artifact_exists),
        "source_artifacts": source_artifacts,
        "input_artifacts": input_artifacts,
        "blocker_reason": _blocker_reason(entry, readiness_status, manifest_row, freshness),
        "as_of_date": as_of_date,
        "generated_at": generated_at,
    }


def _freshness_by_dataset(repo_root: Path) -> dict[str, dict[str, Any]]:
    path = repo_root / "data" / "manifests" / "dataset_freshness.json"
    payload = _read_optional_json(path) or {}
    return {str(row.get("dataset_id")): row for row in payload.get("datasets") or [] if row.get("dataset_id")}


def _normalization_by_dataset(repo_root: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for rel_path in (
        "data/manifests/p1_normalization_manifest.json",
        "data/manifests/p2_normalization_manifest.json",
        "data/manifests/p3_normalization_manifest.json",
    ):
        payload = _read_optional_json(repo_root / rel_path) or {}
        for row in payload.get("datasets") or []:
            if row.get("dataset_id"):
                rows[str(row["dataset_id"])] = row
    return rows


def _feature_by_dataset(repo_root: Path) -> dict[str, dict[str, Any]]:
    payload = _read_optional_json(repo_root / "data" / "manifests" / "feature_store_manifest.json") or {}
    rows: dict[str, dict[str, Any]] = {}
    for row in payload.get("feature_sets") or []:
        feature_set = row.get("feature_set")
        if feature_set in FEATURE_SETS:
            rows[str(feature_set)] = row
    return rows


def _normalization_stage(dataset_id: str) -> str:
    if dataset_id in P1_DATASETS:
        return "P1"
    if dataset_id in P2_DATASETS:
        return "P2"
    if dataset_id in P3_DATASETS:
        return "P3"
    if dataset_id in FEATURE_SETS:
        return "FEATURE"
    return "UNNORMALIZED_OR_BLOCKED"


def _readiness_status(
    *,
    catalog_status: str,
    artifact_exists: bool,
    manifest_row: dict[str, Any] | None,
    freshness: dict[str, Any] | None,
) -> str:
    if catalog_status == "BLOCKED":
        return "BLOCKED"
    if manifest_row and manifest_row.get("status") == "MISSING_SOURCE":
        return "MISSING_ARTIFACT"
    if artifact_exists:
        return "OBSERVE_ONLY"
    if freshness and str(freshness.get("hydration_status") or "").startswith("BLOCKED"):
        return "BLOCKED"
    return "MISSING_ARTIFACT"


def _lineage_status(source_artifacts: list[dict[str, Any]], input_artifacts: list[dict[str, Any]], artifact_exists: bool) -> str:
    if not artifact_exists:
        return "NO_ARTIFACT"
    if source_artifacts or input_artifacts:
        return "LINEAGE_RECORDED"
    return "LINEAGE_MISSING"


def _artifact_pit_status(payload: dict[str, Any] | None) -> str | None:
    rows = (payload or {}).get("rows") or []
    statuses = {row.get("PIT_safe_status") for row in rows if row.get("PIT_safe_status")}
    if len(statuses) == 1:
        return next(iter(statuses))
    return None


def _blocker_reason(entry: dict[str, Any], readiness_status: str, manifest_row: dict[str, Any] | None, freshness: dict[str, Any] | None) -> str:
    if readiness_status == "OBSERVE_ONLY":
        return ""
    if manifest_row and manifest_row.get("validation_errors"):
        return "; ".join(str(item) for item in manifest_row["validation_errors"][:3])
    if freshness and freshness.get("reason"):
        return str(freshness["reason"])
    return "; ".join(str(item) for item in entry.get("known_risks") or []) or "No read-only canonical artifact exists yet."


def _read_optional_json(path: Path | None) -> dict[str, Any] | None:
    if not path or not path.exists():
        return None
    return read_json(path)


def _coalesce_int(*values: Any) -> int:
    for value in values:
        if value not in (None, ""):
            return int(value)
    return 0


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

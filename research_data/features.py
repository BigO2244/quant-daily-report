from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from research_data.hydration import read_json, utc_now_iso, write_json


FEATURE_SETS = {"fundamental_features"}


def build_feature_store(
    *,
    repo_root: Path,
    as_of_date: str | None = None,
    feature_sets: set[str] | None = None,
) -> dict[str, Any]:
    repo_root = Path(repo_root)
    selected = set(feature_sets or FEATURE_SETS)
    unsupported = selected - FEATURE_SETS
    if unsupported:
        raise ValueError(f"Unsupported feature sets: {sorted(unsupported)}")
    effective_as_of = as_of_date or datetime.now(UTC).date().isoformat()
    generated_at = utc_now_iso()
    results = []

    builders = {"fundamental_features": _build_fundamental_features}
    for feature_set in sorted(selected):
        try:
            results.append(builders[feature_set](repo_root, effective_as_of, generated_at))
        except FileNotFoundError as exc:
            results.append(
                {
                    "feature_set": feature_set,
                    "status": "MISSING_INPUT",
                    "artifact_path": None,
                    "row_count": 0,
                    "validation_status": "WARN",
                    "validation_errors": [str(exc)],
                    "as_of_date": effective_as_of,
                    "generated_at": generated_at,
                }
            )

    manifest = {
        "schema_version": "feature_store_manifest_v1",
        "generated_at": generated_at,
        "as_of_date": effective_as_of,
        "runtime_impact": "read_only_feature_generation_no_trading_path_changes",
        "feature_set_count": len(results),
        "built_feature_set_count": sum(1 for item in results if item["status"] in {"OK", "WARN"}),
        "failed_feature_set_count": sum(1 for item in results if item["status"] not in {"OK", "WARN"}),
        "feature_sets": results,
    }
    write_json(repo_root / "data" / "manifests" / "feature_store_manifest.json", manifest)
    return manifest


def _build_fundamental_features(repo_root: Path, as_of_date: str, generated_at: str) -> dict[str, Any]:
    input_path = repo_root / "data" / "normalized" / "fundamentals" / "statements.json"
    if not input_path.exists():
        raise FileNotFoundError(f"normalized fundamentals input missing: {input_path}")
    payload = read_json(input_path)
    rows = []
    input_digest = _sha256(input_path)
    for item in payload.get("rows") or []:
        filing_date = str(item.get("filing_date") or "")[:10]
        if filing_date and filing_date > as_of_date:
            continue
        revenue = _float_or_none(item.get("revenue"))
        net_income = _float_or_none(item.get("net_income"))
        feature_date = filing_date or str(item.get("as_of_date") or as_of_date)[:10]
        raw_key = "|".join(str(part) for part in ("fundamental_features_v1", item.get("fundamental_id"), feature_date))
        rows.append(
            {
                "feature_id": hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:24],
                "feature_set": "fundamental_features",
                "feature_version": "fundamental_features_v1_observe_only",
                "security_id": item.get("security_id"),
                "source_symbol": item.get("source_symbol"),
                "feature_date": feature_date,
                "as_of_date": as_of_date,
                "dimension": item.get("dimension"),
                "revenue": revenue,
                "net_income": net_income,
                "net_margin": _ratio(net_income, revenue),
                "revenue_positive": revenue is not None and revenue > 0,
                "profit_positive": net_income is not None and net_income > 0,
                "input_dataset_id": "fundamentals_pit",
                "input_fundamental_id": item.get("fundamental_id"),
                "input_dataset_schema_version": payload.get("schema_version"),
                "input_artifact_digest": input_digest,
                "lineage_status": "OBSERVE_ONLY_INPUT_LINEAGE_RECORDED",
                "PIT_safe_status": "PIT_DERIVED_FROM_NORMALIZED_FUNDAMENTALS_OBSERVE_ONLY",
                "security_id_resolution_status": item.get("security_id_resolution_status"),
                "restatement_policy": item.get("restatement_policy"),
                "generated_at": generated_at,
            }
        )
    artifact = repo_root / "data" / "features" / "fundamental_features" / "features.json"
    feature_payload = {
        "schema_version": "fundamental_features_v1",
        "feature_set": "fundamental_features",
        "generated_at": generated_at,
        "as_of_date": as_of_date,
        "row_count": len(rows),
        "input_artifacts": [{"path": str(input_path), "sha256": input_digest}],
        "rows": rows,
    }
    validation = _validate_fundamental_features(rows)
    feature_payload["validation"] = {"status": "PASS" if not validation else "WARN", "errors": validation}
    write_json(artifact, feature_payload)
    return {
        "feature_set": "fundamental_features",
        "status": "OK" if not validation else "WARN",
        "artifact_path": str(artifact),
        "row_count": len(rows),
        "validation_status": "PASS" if not validation else "WARN",
        "validation_errors": validation,
        "input_artifacts": feature_payload["input_artifacts"],
        "as_of_date": as_of_date,
        "generated_at": generated_at,
    }


def _validate_fundamental_features(rows: list[dict[str, Any]]) -> list[str]:
    errors = []
    for idx, row in enumerate(rows):
        missing = [field for field in ("feature_id", "security_id", "feature_date", "as_of_date", "input_artifact_digest", "generated_at") if row.get(field) in (None, "")]
        if missing:
            errors.append(f"row {idx} missing required fields: {', '.join(missing)}")
        if row.get("feature_date") and row.get("as_of_date") and str(row["feature_date"]) > str(row["as_of_date"]):
            errors.append(f"row {idx} violates feature_date <= as_of_date: {row['feature_date']} > {row['as_of_date']}")
        if row.get("security_id_resolution_status") == "UNRESOLVED_SOURCE_SYMBOL_ONLY":
            errors.append(f"row {idx} uses unresolved source-symbol security id")
        if row.get("restatement_policy") == "source_dimension_preserved_needs_version_audit":
            errors.append(f"row {idx} restatement/version policy remains observe-only")
    return errors


def _float_or_none(value: Any) -> float | None:
    if value in (None, "", "."):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return round(numerator / denominator, 8)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

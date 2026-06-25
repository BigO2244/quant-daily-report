from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from research_data.hydration import read_json, utc_now_iso, write_json


FEATURE_SETS = {"fundamental_features", "macro_regime_features"}
FEATURE_DEFINITIONS: dict[str, list[dict[str, Any]]] = {
    "fundamental_features": [
        {
            "feature_name": "net_margin",
            "feature_version": "fundamental_features_v1_observe_only",
            "input_dataset_ids": ["fundamentals_pit"],
            "input_fields": ["net_income", "revenue"],
            "definition": "net_income / revenue when both values are present and revenue is non-zero.",
            "PIT_safe_status": "PIT_DERIVED_FROM_NORMALIZED_FUNDAMENTALS_OBSERVE_ONLY",
        },
        {
            "feature_name": "revenue_positive",
            "feature_version": "fundamental_features_v1_observe_only",
            "input_dataset_ids": ["fundamentals_pit"],
            "input_fields": ["revenue"],
            "definition": "True when revenue is present and greater than zero.",
            "PIT_safe_status": "PIT_DERIVED_FROM_NORMALIZED_FUNDAMENTALS_OBSERVE_ONLY",
        },
        {
            "feature_name": "profit_positive",
            "feature_version": "fundamental_features_v1_observe_only",
            "input_dataset_ids": ["fundamentals_pit"],
            "input_fields": ["net_income"],
            "definition": "True when net income is present and greater than zero.",
            "PIT_safe_status": "PIT_DERIVED_FROM_NORMALIZED_FUNDAMENTALS_OBSERVE_ONLY",
        },
    ],
    "macro_regime_features": [
        {
            "feature_name": "yield_curve_inverted",
            "feature_version": "macro_regime_features_v1_observe_only",
            "input_dataset_ids": ["yield_curve"],
            "input_fields": ["slope_10y_2y"],
            "definition": "True when the normalized 10-year minus 2-year yield-curve slope is below zero.",
            "PIT_safe_status": "PIT_DERIVED_FROM_NORMALIZED_MACRO_OBSERVE_ONLY_RELEASE_DATE_UNVERIFIED",
        },
        {
            "feature_name": "credit_stress",
            "feature_version": "macro_regime_features_v1_observe_only",
            "input_dataset_ids": ["credit_spreads"],
            "input_fields": ["spread_percent"],
            "definition": "True when BAA10Y credit spread is at least 3.0 percentage points.",
            "PIT_safe_status": "PIT_DERIVED_FROM_NORMALIZED_MACRO_OBSERVE_ONLY_RELEASE_DATE_UNVERIFIED",
        },
        {
            "feature_name": "high_volatility",
            "feature_version": "macro_regime_features_v1_observe_only",
            "input_dataset_ids": ["vix_volatility_regime"],
            "input_fields": ["vix_close"],
            "definition": "True when VIX close is at least 25.0.",
            "PIT_safe_status": "PIT_DERIVED_FROM_NORMALIZED_MACRO_OBSERVE_ONLY",
        },
        {
            "feature_name": "rate_10y_percent",
            "feature_version": "macro_regime_features_v1_observe_only",
            "input_dataset_ids": ["macro_rates"],
            "input_fields": ["value_percent"],
            "definition": "Normalized DGS10 10-year Treasury rate level.",
            "PIT_safe_status": "PIT_DERIVED_FROM_NORMALIZED_MACRO_OBSERVE_ONLY_RELEASE_DATE_UNVERIFIED",
        },
    ],
}


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

    builders = {
        "fundamental_features": _build_fundamental_features,
        "macro_regime_features": _build_macro_regime_features,
    }
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
        "feature_definitions_path": str(repo_root / "data" / "manifests" / "feature_definitions.json"),
        "feature_coverage_path": str(repo_root / "data" / "manifests" / "feature_coverage.json"),
        "feature_sets": results,
    }
    write_json(repo_root / "data" / "manifests" / "feature_store_manifest.json", manifest)
    _write_feature_definitions(repo_root, effective_as_of, generated_at)
    _write_feature_coverage(repo_root, manifest, effective_as_of, generated_at)
    return manifest


def _write_feature_definitions(repo_root: Path, as_of_date: str, generated_at: str) -> None:
    rows = []
    for feature_set in sorted(FEATURE_DEFINITIONS):
        rows.extend({"feature_set": feature_set, **definition} for definition in FEATURE_DEFINITIONS[feature_set])
    write_json(
        repo_root / "data" / "manifests" / "feature_definitions.json",
        {
            "schema_version": "feature_definitions_v1",
            "generated_at": generated_at,
            "as_of_date": as_of_date,
            "runtime_impact": "read_only_feature_metadata_no_trading_path_changes",
            "feature_set_count": len(FEATURE_DEFINITIONS),
            "feature_definition_count": len(rows),
            "definitions": rows,
        },
    )


def _write_feature_coverage(repo_root: Path, manifest: dict[str, Any], as_of_date: str, generated_at: str) -> None:
    rows = []
    for item in manifest["feature_sets"]:
        rows.append(_feature_coverage_row(repo_root, item))
    write_json(
        repo_root / "data" / "manifests" / "feature_coverage.json",
        {
            "schema_version": "feature_coverage_v1",
            "generated_at": generated_at,
            "as_of_date": as_of_date,
            "runtime_impact": "read_only_feature_coverage_no_trading_path_changes",
            "feature_set_count": len(rows),
            "coverage": rows,
        },
    )


def _feature_coverage_row(repo_root: Path, manifest_item: dict[str, Any]) -> dict[str, Any]:
    feature_set = str(manifest_item["feature_set"])
    path_value = manifest_item.get("artifact_path")
    if not path_value:
        return {
            "feature_set": feature_set,
            "status": manifest_item.get("status"),
            "artifact_path": None,
            "row_count": 0,
            "first_feature_date": None,
            "last_feature_date": None,
            "features": [],
        }
    artifact_path = Path(str(path_value))
    artifact = artifact_path if artifact_path.is_absolute() else repo_root / artifact_path
    payload = read_json(artifact)
    rows = payload.get("rows") or []
    dates = sorted(str(row.get("feature_date")) for row in rows if row.get("feature_date"))
    features = []
    for definition in FEATURE_DEFINITIONS.get(feature_set, []):
        name = definition["feature_name"]
        non_null = sum(1 for row in rows if row.get(name) is not None)
        total = len(rows)
        features.append(
            {
                "feature_name": name,
                "non_null_count": non_null,
                "missing_count": total - non_null,
                "coverage_ratio": round(non_null / total, 8) if total else 0.0,
            }
        )
    return {
        "feature_set": feature_set,
        "status": manifest_item.get("status"),
        "artifact_path": str(artifact),
        "row_count": len(rows),
        "first_feature_date": dates[0] if dates else None,
        "last_feature_date": dates[-1] if dates else None,
        "validation_status": manifest_item.get("validation_status"),
        "features": features,
    }


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


def _build_macro_regime_features(repo_root: Path, as_of_date: str, generated_at: str) -> dict[str, Any]:
    input_paths = {
        "macro_rates": repo_root / "data" / "normalized" / "macro" / "macro_rates.json",
        "yield_curve": repo_root / "data" / "normalized" / "macro" / "yield_curve.json",
        "credit_spreads": repo_root / "data" / "normalized" / "macro" / "credit_spreads.json",
        "vix_volatility_regime": repo_root / "data" / "normalized" / "volatility" / "vix.json",
    }
    missing = [str(path) for path in input_paths.values() if not path.exists()]
    if missing:
        raise FileNotFoundError(f"normalized macro regime inputs missing: {', '.join(missing)}")

    payloads = {dataset_id: read_json(path) for dataset_id, path in input_paths.items()}
    input_artifacts = [{"path": str(path), "sha256": _sha256(path)} for path in input_paths.values()]
    by_date: dict[str, dict[str, Any]] = {}
    release_policy_unverified = False

    for item in payloads["macro_rates"].get("rows") or []:
        observation_date = _feature_date(item, "observation_date", as_of_date)
        if not observation_date:
            continue
        release_policy_unverified = release_policy_unverified or _release_policy_unverified(item)
        row = _macro_row(by_date, observation_date, as_of_date, generated_at)
        if item.get("series_id") == "DGS10":
            row["rate_10y_percent"] = _float_or_none(item.get("value_percent"))

    for item in payloads["yield_curve"].get("rows") or []:
        observation_date = _feature_date(item, "observation_date", as_of_date)
        if not observation_date:
            continue
        release_policy_unverified = release_policy_unverified or _release_policy_unverified(item)
        row = _macro_row(by_date, observation_date, as_of_date, generated_at)
        row["dgs10_percent"] = _float_or_none(item.get("dgs10"))
        row["slope_10y_2y"] = _float_or_none(item.get("slope_10y_2y"))
        row["slope_30y_10y"] = _float_or_none(item.get("slope_30y_10y"))
        row["yield_curve_inverted"] = row["slope_10y_2y"] is not None and row["slope_10y_2y"] < 0

    for item in payloads["credit_spreads"].get("rows") or []:
        observation_date = _feature_date(item, "observation_date", as_of_date)
        if not observation_date:
            continue
        release_policy_unverified = release_policy_unverified or _release_policy_unverified(item)
        row = _macro_row(by_date, observation_date, as_of_date, generated_at)
        if item.get("series_id") == "BAA10Y":
            spread = _float_or_none(item.get("spread_percent"))
            row["credit_spread_baa10y_percent"] = spread
            row["credit_stress"] = spread is not None and spread >= 3.0

    for item in payloads["vix_volatility_regime"].get("rows") or []:
        observation_date = _feature_date(item, "observation_date", as_of_date)
        if not observation_date:
            continue
        row = _macro_row(by_date, observation_date, as_of_date, generated_at)
        vix_close = _float_or_none(item.get("vix_close"))
        row["vix_close"] = vix_close
        row["high_volatility"] = vix_close is not None and vix_close >= 25.0

    rows = [
        row
        for row in (_finalize_macro_row(row, payloads, input_artifacts, release_policy_unverified) for _, row in sorted(by_date.items()))
        if _has_macro_feature_values(row)
    ]
    artifact = repo_root / "data" / "features" / "macro_regime_features" / "features.json"
    feature_payload = {
        "schema_version": "macro_regime_features_v1",
        "feature_set": "macro_regime_features",
        "generated_at": generated_at,
        "as_of_date": as_of_date,
        "row_count": len(rows),
        "input_artifacts": input_artifacts,
        "rows": rows,
    }
    validation = _validate_macro_regime_features(rows, release_policy_unverified)
    feature_payload["validation"] = {"status": "PASS" if not validation else "WARN", "errors": validation}
    write_json(artifact, feature_payload)
    return {
        "feature_set": "macro_regime_features",
        "status": "OK" if not validation else "WARN",
        "artifact_path": str(artifact),
        "row_count": len(rows),
        "validation_status": "PASS" if not validation else "WARN",
        "validation_errors": validation,
        "input_artifacts": input_artifacts,
        "as_of_date": as_of_date,
        "generated_at": generated_at,
    }


def _macro_row(by_date: dict[str, dict[str, Any]], feature_date: str, as_of_date: str, generated_at: str) -> dict[str, Any]:
    if feature_date not in by_date:
        by_date[feature_date] = {
            "feature_id": hashlib.sha256(f"macro_regime_features_v1|{feature_date}".encode("utf-8")).hexdigest()[:24],
            "feature_set": "macro_regime_features",
            "feature_version": "macro_regime_features_v1_observe_only",
            "security_id": "MACRO:GLOBAL",
            "feature_date": feature_date,
            "as_of_date": as_of_date,
            "rate_10y_percent": None,
            "dgs10_percent": None,
            "slope_10y_2y": None,
            "slope_30y_10y": None,
            "credit_spread_baa10y_percent": None,
            "vix_close": None,
            "yield_curve_inverted": None,
            "credit_stress": None,
            "high_volatility": None,
            "input_dataset_ids": ["macro_rates", "yield_curve", "credit_spreads", "vix_volatility_regime"],
            "generated_at": generated_at,
        }
    return by_date[feature_date]


def _finalize_macro_row(
    row: dict[str, Any],
    payloads: dict[str, dict[str, Any]],
    input_artifacts: list[dict[str, str]],
    release_policy_unverified: bool,
) -> dict[str, Any]:
    row = dict(row)
    row["input_dataset_schema_versions"] = {dataset_id: payload.get("schema_version") for dataset_id, payload in payloads.items()}
    row["input_artifact_digest"] = hashlib.sha256("|".join(item["sha256"] for item in input_artifacts).encode("utf-8")).hexdigest()
    row["lineage_status"] = "OBSERVE_ONLY_INPUT_LINEAGE_RECORDED"
    row["release_date_policy_status"] = "UNVERIFIED_RELEASE_DATE_POLICY" if release_policy_unverified else "RELEASE_DATE_POLICY_RECORDED"
    row["PIT_safe_status"] = (
        "PIT_DERIVED_FROM_NORMALIZED_MACRO_OBSERVE_ONLY_RELEASE_DATE_UNVERIFIED"
        if release_policy_unverified
        else "PIT_DERIVED_FROM_NORMALIZED_MACRO_OBSERVE_ONLY"
    )
    return row


def _validate_macro_regime_features(rows: list[dict[str, Any]], release_policy_unverified: bool) -> list[str]:
    errors = []
    if not rows:
        errors.append("macro_regime_features: no rows generated")
    if release_policy_unverified:
        errors.append("macro_regime_features: release-date policy remains observe-only for one or more inputs")
    for idx, row in enumerate(rows):
        missing = [field for field in ("feature_id", "security_id", "feature_date", "as_of_date", "input_artifact_digest", "generated_at") if row.get(field) in (None, "")]
        if missing:
            errors.append(f"row {idx} missing required fields: {', '.join(missing)}")
        if row.get("feature_date") and row.get("as_of_date") and str(row["feature_date"]) > str(row["as_of_date"]):
            errors.append(f"row {idx} violates feature_date <= as_of_date: {row['feature_date']} > {row['as_of_date']}")
        if not _has_macro_feature_values(row):
            errors.append(f"row {idx} has no macro feature values")
    return errors


def _has_macro_feature_values(row: dict[str, Any]) -> bool:
    return any(
        row.get(field) is not None
        for field in ("rate_10y_percent", "dgs10_percent", "slope_10y_2y", "credit_spread_baa10y_percent", "vix_close")
    )


def _feature_date(item: dict[str, Any], field: str, as_of_date: str) -> str | None:
    value = str(item.get(field) or "")[:10]
    if not value or value > as_of_date:
        return None
    return value


def _release_policy_unverified(item: dict[str, Any]) -> bool:
    status = str(item.get("publication_date_status") or "").upper()
    return "UNVERIFIED" in status or not item.get("release_date")


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

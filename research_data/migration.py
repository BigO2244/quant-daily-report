from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from research_data.hydration import read_json, utc_now_iso, write_json


SCHEMA_VERSION = "sleeve_migration_readiness_v1"
RUNTIME_IMPACT = "read_only_sleeve_migration_audit_no_trading_path_changes"
DEFAULT_SLEEVE_MANIFEST = Path("research_registry/sleeves/manifest.json")
DEFAULT_OBSERVABILITY = Path("data/manifests/research_data_observability.json")
DEFAULT_OUTPUT_ROOT = Path("outputs/research/data_migration")

SLEEVE_DATASET_REQUIREMENTS = {
    "core_momentum": ["ohlcv_prices", "security_master_pit", "corporate_actions", "dataset_freshness"],
    "crisis_reversal": [
        "ohlcv_prices",
        "security_master_pit",
        "corporate_actions",
        "dataset_freshness",
        "vix_volatility_regime",
        "macro_regime_features",
        "short_interest",
    ],
    "earnings_drift": [
        "security_master_pit",
        "fundamentals_pit",
        "fundamental_features",
        "sec_10q_10k_metadata",
        "analyst_estimate_revisions",
    ],
    "event_driven": [
        "security_master_pit",
        "sec_8k_events",
        "insider_form4",
        "institutional_13f",
        "news_metadata",
        "etf_index_constituents",
    ],
    "regime_overlay": [
        "macro_rates",
        "yield_curve",
        "credit_spreads",
        "vix_volatility_regime",
        "macro_regime_features",
        "dataset_freshness",
    ],
}


def build_sleeve_migration_readiness(
    *,
    repo_root: Path,
    as_of_date: str | None = None,
    sleeve_manifest_path: Path | None = None,
    observability_path: Path | None = None,
    output_root: Path | None = None,
) -> dict[str, Any]:
    root = Path(repo_root)
    effective_as_of = as_of_date or datetime.now(UTC).date().isoformat()
    generated_at = utc_now_iso()
    manifest_path = _resolve(root, sleeve_manifest_path or DEFAULT_SLEEVE_MANIFEST)
    obs_path = _resolve(root, observability_path or DEFAULT_OBSERVABILITY)
    destination = _resolve(root, output_root or DEFAULT_OUTPUT_ROOT) / effective_as_of
    sleeve_manifest = read_json(manifest_path)
    observability = read_json(obs_path)
    diagnostics = {row["dataset_id"]: row for row in observability.get("datasets") or [] if row.get("dataset_id")}
    sleeves = [
        _sleeve_row(sleeve=sleeve, diagnostics=diagnostics, repo_root=root, as_of_date=effective_as_of)
        for sleeve in sleeve_manifest.get("sleeves") or []
    ]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "as_of_date": effective_as_of,
        "runtime_impact": RUNTIME_IMPACT,
        "source_sleeve_manifest_path": _display_path(root, manifest_path),
        "source_observability_path": _display_path(root, obs_path),
        "sleeve_count": len(sleeves),
        "ready_observe_only_count": sum(1 for row in sleeves if row["migration_readiness_status"] == "READY_OBSERVE_ONLY"),
        "warn_count": sum(1 for row in sleeves if row["migration_readiness_status"] == "WARN"),
        "blocked_count": sum(1 for row in sleeves if row["migration_readiness_status"] == "BLOCKED"),
        "broker_submission_invoked": False,
        "sleeve_runtime_invoked": False,
        "sleeves": sleeves,
    }
    write_json(destination / "migration_readiness.json", payload)
    (destination / "migration_readiness.md").write_text(render_migration_readiness_markdown(payload), encoding="utf-8")
    return payload


def render_migration_readiness_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Sleeve Migration Readiness",
        "",
        f"- As of date: {payload.get('as_of_date')}",
        f"- Sleeves: {payload.get('sleeve_count')}",
        f"- Ready observe-only: {payload.get('ready_observe_only_count')}",
        f"- Warn: {payload.get('warn_count')}",
        f"- Blocked: {payload.get('blocked_count')}",
        f"- Broker submission invoked: {str(payload.get('broker_submission_invoked')).lower()}",
        f"- Sleeve runtime invoked: {str(payload.get('sleeve_runtime_invoked')).lower()}",
        "",
        "| sleeve_id | family | status | required | blockers |",
        "|---|---|---|---:|---|",
    ]
    for row in payload.get("sleeves") or []:
        blockers = ", ".join(f"`{item}`" for item in row.get("blocking_dataset_ids") or []) or "None"
        lines.append(
            f"| `{row.get('sleeve_id')}` | {row.get('family')} | {row.get('migration_readiness_status')} | "
            f"{len(row.get('required_dataset_ids') or [])} | {blockers} |"
        )
    lines.append("")
    lines.append("Runtime impact: read-only migration audit only; no sleeve runtime, broker, execution, scheduler, allocation, or model behavior change.")
    lines.append("")
    return "\n".join(lines)


def _sleeve_row(*, sleeve: dict[str, Any], diagnostics: dict[str, dict[str, Any]], repo_root: Path, as_of_date: str) -> dict[str, Any]:
    family = str(sleeve.get("family") or "unknown")
    required = SLEEVE_DATASET_REQUIREMENTS.get(family, ["dataset_freshness"])
    dataset_rows = [_dataset_requirement(dataset_id, diagnostics.get(dataset_id)) for dataset_id in required]
    symbol_coverage = _symbol_coverage_requirement(repo_root, sleeve, required, as_of_date)
    dataset_rows.extend(symbol_coverage["dataset_requirements"])
    blocking = _unique([row["dataset_id"] for row in dataset_rows if row["requirement_status"] == "BLOCKED"])
    warnings = _unique([row["dataset_id"] for row in dataset_rows if row["requirement_status"] == "WARN"])
    if blocking:
        readiness = "BLOCKED"
    elif warnings:
        readiness = "WARN"
    else:
        readiness = "READY_OBSERVE_ONLY"
    return {
        "sleeve_id": sleeve.get("sleeve_id"),
        "strategy_id": sleeve.get("strategy_id"),
        "family": family,
        "lifecycle_stage": sleeve.get("lifecycle_stage"),
        "implementation_status": sleeve.get("implementation_status"),
        "behavior_change_allowed": bool(sleeve.get("behavior_change_allowed")),
        "required_dataset_ids": required,
        "migration_readiness_status": readiness,
        "blocking_dataset_ids": blocking,
        "warning_dataset_ids": warnings,
        "symbol_coverage": symbol_coverage,
        "dataset_requirements": dataset_rows,
    }


def _dataset_requirement(dataset_id: str, diagnostic: dict[str, Any] | None) -> dict[str, Any]:
    if diagnostic is None:
        return {
            "dataset_id": dataset_id,
            "requirement_status": "BLOCKED",
            "readiness_status": "MISSING_DIAGNOSTICS",
            "validation_status": None,
            "PIT_safe_status": None,
            "reason": "Dataset is required but absent from observability manifest.",
        }
    readiness = diagnostic.get("readiness_status")
    validation = str(diagnostic.get("validation_status") or "")
    pit_status = str(diagnostic.get("PIT_safe_status") or "")
    freshness = str(diagnostic.get("freshness_status") or "")
    if readiness != "OBSERVE_ONLY":
        status = "BLOCKED"
    elif _critical_validation(validation) or _pit_violation(pit_status):
        status = "BLOCKED"
    elif validation.upper().startswith("WARN") or freshness not in {"", "OK", "CURRENT", "FRESH"}:
        status = "WARN"
    else:
        status = "READY"
    return {
        "dataset_id": dataset_id,
        "requirement_status": status,
        "readiness_status": readiness,
        "validation_status": diagnostic.get("validation_status"),
        "freshness_status": diagnostic.get("freshness_status"),
        "PIT_safe_status": diagnostic.get("PIT_safe_status"),
        "lineage_status": diagnostic.get("lineage_status"),
        "artifact_exists": bool(diagnostic.get("artifact_exists")),
        "row_count": int(diagnostic.get("row_count") or 0),
        "reason": diagnostic.get("blocker_reason") or _reason(status, diagnostic),
    }


def _critical_validation(value: str) -> bool:
    text = value.upper()
    return text.startswith("FAIL") or "FAILED" in text or "SCHEMA_ERROR" in text


def _pit_violation(value: str) -> bool:
    text = value.upper()
    return "PIT_VIOLATION" in text or "PIT_UNSAFE" in text or "LOOKAHEAD" in text or "LOOK_AHEAD" in text


def _reason(status: str, diagnostic: dict[str, Any]) -> str:
    if status == "READY":
        return "Dataset has observe-only canonical artifact and no blocking diagnostic."
    if status == "WARN":
        return "Dataset is observe-only but has freshness or validation warnings."
    return "Dataset is not ready for observe-only migration."


def _symbol_coverage_requirement(repo_root: Path, sleeve: dict[str, Any], required: list[str], as_of_date: str) -> dict[str, Any]:
    if "ohlcv_prices" not in required or "security_master_pit" not in required:
        return {
            "status": "NOT_APPLICABLE",
            "required_symbols": [],
            "dataset_requirements": [],
            "reason": "No security-level price/master coverage check is required for this sleeve family.",
        }
    strategy_id = str(sleeve.get("strategy_id") or "")
    legacy_candidate = _find_legacy_candidate(repo_root / "outputs" / "shadow_candidates", strategy_id, as_of_date)
    if legacy_candidate is None:
        return {
            "status": "BLOCKED",
            "required_symbols": [],
            "legacy_candidate_path": None,
            "dataset_requirements": [
                _coverage_dataset_requirement(
                    "sleeve_legacy_candidate",
                    "BLOCKED",
                    "No legacy sleeve candidate exists on or before the readiness as_of_date, so symbol-level coverage cannot be proven.",
                )
            ],
            "reason": "Missing legacy candidate for sleeve symbol universe.",
        }
    legacy = read_json(legacy_candidate)
    required_symbols = _legacy_symbols(legacy)
    coverage_by_dataset = {
        "ohlcv_prices": _normalized_symbol_coverage(repo_root, "data/normalized/prices/ohlcv_prices.json", "source_symbol", required_symbols),
        "security_master_pit": _normalized_symbol_coverage(repo_root, "data/normalized/security_master/security_master.json", "ticker", required_symbols),
        "corporate_actions": _corporate_action_query_coverage(repo_root, required_symbols),
        "dataset_freshness": _freshness_coverage(repo_root, [dataset_id for dataset_id in required if dataset_id != "dataset_freshness"]),
    }
    dataset_requirements = []
    for dataset_id, coverage in coverage_by_dataset.items():
        missing = coverage.get("missing_symbols") or coverage.get("missing_dataset_ids") or []
        if missing:
            status = "BLOCKED"
            reason = f"Missing coverage: {', '.join(missing)}"
        elif dataset_id == "security_master_pit" and coverage.get("pit_grade_status") != "PIT_GRADE":
            status = "WARN"
            reason = f"Security master is {coverage.get('pit_grade_status')}; PIT_GRADE is required for clean sleeve readiness."
        else:
            status = "READY"
            reason = "Symbol/freshness coverage is complete."
        dataset_requirements.append(_coverage_dataset_requirement(dataset_id, status, reason, coverage))
    blocked = [row for row in dataset_requirements if row["requirement_status"] == "BLOCKED"]
    return {
        "status": "READY" if not blocked else "BLOCKED",
        "required_symbols": required_symbols,
        "legacy_candidate_path": _display_path(repo_root, legacy_candidate),
        "coverage_by_dataset": coverage_by_dataset,
        "dataset_requirements": dataset_requirements,
        "reason": "Symbol-level coverage complete." if not blocked else "One or more required datasets lack sleeve-symbol or freshness coverage.",
    }


def _coverage_dataset_requirement(dataset_id: str, status: str, reason: str, coverage: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "dataset_id": dataset_id,
        "requirement_status": status,
        "readiness_status": "SYMBOL_COVERAGE",
        "validation_status": None,
        "freshness_status": None,
        "PIT_safe_status": None,
        "lineage_status": None,
        "artifact_exists": bool((coverage or {}).get("artifact_exists")),
        "row_count": int((coverage or {}).get("row_count") or 0),
        "reason": reason,
    }


def _normalized_symbol_coverage(repo_root: Path, rel_path: str, symbol_field: str, required_symbols: list[str]) -> dict[str, Any]:
    path = repo_root / rel_path
    if not path.exists():
        return {"artifact_exists": False, "row_count": 0, "covered_symbols": [], "missing_symbols": required_symbols}
    payload = read_json(path)
    coverage = payload.get("coverage") or {}
    grade = payload.get("security_master_grade") or {}
    covered = set(str(symbol).upper() for symbol in coverage.get("covered_symbols") or [])
    if not covered:
        covered = {str(row.get(symbol_field) or "").upper() for row in payload.get("rows") or [] if row.get(symbol_field)}
    required = set(required_symbols)
    return {
        "artifact_exists": True,
        "row_count": int(payload.get("row_count") or len(payload.get("rows") or [])),
        "covered_symbols": sorted(covered & required),
        "missing_symbols": sorted(required - covered),
        "coverage_pct": round(len(covered & required) / len(required), 6) if required else None,
        "pit_grade_status": grade.get("status"),
        "pit_grade_row_count": grade.get("pit_grade_row_count"),
        "current_reference_row_count": grade.get("current_reference_row_count"),
    }


def _corporate_action_query_coverage(repo_root: Path, required_symbols: list[str]) -> dict[str, Any]:
    path = repo_root / "data/normalized/corporate_actions/actions.json"
    if not path.exists():
        return {"artifact_exists": False, "row_count": 0, "covered_symbols": [], "missing_symbols": required_symbols}
    payload = read_json(path)
    coverage = payload.get("coverage") or {}
    covered = set(str(symbol).upper() for symbol in coverage.get("query_covered_symbols") or coverage.get("covered_symbols") or [])
    if not covered:
        covered = {str(row.get("source_symbol") or "").upper() for row in payload.get("rows") or [] if row.get("source_symbol")}
    required = set(required_symbols)
    return {
        "artifact_exists": True,
        "row_count": int(payload.get("row_count") or len(payload.get("rows") or [])),
        "covered_symbols": sorted(covered & required),
        "missing_symbols": sorted(required - covered),
        "coverage_pct": round(len(covered & required) / len(required), 6) if required else None,
    }


def _freshness_coverage(repo_root: Path, required_dataset_ids: list[str]) -> dict[str, Any]:
    path = repo_root / "data/normalized/freshness/dataset_freshness.json"
    if not path.exists():
        return {"artifact_exists": False, "row_count": 0, "covered_dataset_ids": [], "missing_dataset_ids": sorted(required_dataset_ids)}
    payload = read_json(path)
    covered = {str(row.get("dataset_id")) for row in payload.get("rows") or [] if row.get("dataset_id")}
    required = set(required_dataset_ids)
    return {
        "artifact_exists": True,
        "row_count": int(payload.get("row_count") or len(payload.get("rows") or [])),
        "covered_dataset_ids": sorted(covered & required),
        "missing_dataset_ids": sorted(required - covered),
        "coverage_pct": round(len(covered & required) / len(required), 6) if required else None,
    }


def _find_legacy_candidate(root: Path, strategy_id: str, as_of_date: str) -> Path | None:
    if not root.exists():
        return None
    candidates = []
    for dated_dir in root.iterdir():
        if not dated_dir.is_dir() or dated_dir.name > as_of_date:
            continue
        candidate = dated_dir / f"{strategy_id}.json"
        if candidate.exists():
            candidates.append(candidate)
    return sorted(candidates)[-1] if candidates else None


def _legacy_symbols(payload: dict[str, Any]) -> list[str]:
    symbols = {str(row.get("ticker") or row.get("symbol") or "").upper() for row in payload.get("holdings") or []}
    symbols.update(str(symbol).upper() for symbol in (payload.get("target_weights") or {}).keys())
    return sorted(symbol for symbol in symbols if symbol)


def _resolve(repo_root: Path, path: Path) -> Path:
    return path if path.is_absolute() else repo_root / path


def _display_path(repo_root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def _unique(values: list[Any]) -> list[Any]:
    seen = set()
    unique_values = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique_values.append(value)
    return unique_values

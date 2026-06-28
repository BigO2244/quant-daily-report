"""FR-105 research-only replay contract builder.

This module reads already-written artifacts and emits a stable replay contract
for global optimizer research. It does not invoke allocation, sizing,
execution, broker, scheduler, paper, or live trading code.
"""
from __future__ import annotations

import json
import math
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


FR_ID = "FR-105"
SCHEMA_VERSION = "fr105_global_optimizer_replay_contract.v1"
PROVENANCE_SCHEMA_VERSION = "fr105_candidate_provenance.v1"
CANDIDATE_UNIVERSE_SCHEMA_VERSION = "fr105_candidate_universe.v1"
CANDIDATE_POOL_SCHEMA_VERSION = "fr105_candidate_pool.v1"
TARGET_PORTFOLIO_SCHEMA_VERSION = "fr105_target_portfolio.v1"
CANDIDATE_LIFECYCLE_SCHEMA_VERSION = "fr105_candidate_lifecycle.v1"
DEFAULT_OUTPUT_ROOT = Path("outputs/research/fr_105")

REQUIRED_TOP_LEVEL_SECTIONS = (
    "metadata",
    "source_artifacts",
    "universe_snapshot",
    "sleeve_candidates",
    "current_portfolio",
    "constraints_snapshot",
    "execution_residuals",
    "provenance_schema_version",
    "validation_status",
)

REQUIRED_SOURCE_ARTIFACT_KEYS = (
    "candidate_trade_lifecycle_path",
    "target_portfolio_path",
    "sleeve_artifacts",
    "execution_results_path",
    "reconciliation_path",
    "broker_positions_path",
    "price_source",
)

REQUIRED_METADATA_KEYS = (
    "trade_date",
    "generated_at",
    "git_sha",
    "mode",
    "fr_id",
    "schema_version",
)

REQUIRED_CANDIDATE_KEYS = (
    "ticker",
    "sleeve_id",
    "strategy_id",
    "source_model",
    "lifecycle",
    "rank",
    "score",
    "conviction_score",
    "expected_alpha",
    "expected_risk",
    "target_weight",
    "target_notional",
    "current_weight",
    "current_notional",
    "delta_notional",
    "reason_included",
    "reason_excluded",
    "data_asof",
    "source_artifact_path",
)

REQUIRED_CONSTRAINT_KEYS = (
    "max_single_name_weight",
    "sector_caps",
    "effective_n_floor",
    "turnover_cap",
    "liquidity_constraints",
    "min_trade_dollars",
    "cash_target",
    "gross_exposure_target",
    "buying_power_available",
    "rebudget_policy",
    "min_notional_policy",
)

REQUIRED_EXECUTION_RESIDUAL_KEYS = (
    "planned_candidates",
    "executable_candidates",
    "intended_orders",
    "submitted_orders",
    "filled_orders",
    "suppressed_count",
    "clipped_count",
    "suppression_reason_counts",
    "clipping_reason_counts",
    "estimated_unexecuted_notional_total",
)

PROHIBITED_PRODUCTION_MODULES = (
    "daily_quant_report",
    "paper.paper_broker",
    "paper_broker",
    "scripts.run_precomputed_alpaca_execution",
    "brokers.alpaca",
    "brokers.alpaca_snapshot",
)


@dataclass(frozen=True)
class FR105ValidationResult:
    status: str
    findings: list[str]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "findings": list(self.findings),
            "warnings": list(self.warnings),
        }


def _read_json(path: Path | None) -> Any:
    if path is None or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _float(value: Any) -> float | None:
    try:
        if value in ("", None):
            return None
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _int(value: Any) -> int | None:
    try:
        if value in ("", None):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _clean(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean(item) for item in value]
    return value


def _git_sha(repo_root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    sha = result.stdout.strip()
    return sha if result.returncode == 0 and sha else "unavailable"


def _relative(path: Path | str | None, repo_root: Path) -> str | None:
    if path is None:
        return None
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    try:
        return candidate.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return str(candidate)


def _path_from_text(value: Any, repo_root: Path) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value.strip())
    return path if path.is_absolute() else repo_root / path


def _existing_path(repo_root: Path, *candidates: Path | str | None) -> Path | None:
    for candidate in candidates:
        if candidate is None:
            continue
        path = Path(candidate)
        if not path.is_absolute():
            path = repo_root / path
        if path.exists():
            return path
    return None


def _find_precompute_path(repo_root: Path, trade_date: str, filename: str) -> Path | None:
    return _existing_path(repo_root, repo_root / "outputs" / "precompute" / trade_date / filename)


def _find_run_root(repo_root: Path, trade_date: str, run_id: str | None) -> Path | None:
    runs_root = repo_root / "outputs" / "runs"
    if run_id:
        explicit = Path(run_id)
        if explicit.is_absolute() and explicit.exists():
            return explicit
        candidate = runs_root / run_id
        return candidate if candidate.exists() else None
    if not runs_root.exists():
        return None
    candidates: list[Path] = []
    for path in runs_root.iterdir():
        if not path.is_dir():
            continue
        if path.name.startswith(trade_date):
            candidates.append(path)
            continue
        payload = _as_dict(_read_json(path / "execution_payload.json"))
        results = _as_dict(_read_json(path / "execution_results.json"))
        if payload.get("trade_date") == trade_date or results.get("trade_date") == trade_date:
            candidates.append(path)
    return sorted(candidates, key=lambda item: item.name)[-1] if candidates else None


def _source_artifact_path(payload: Mapping[str, Any], key: str, repo_root: Path) -> Path | None:
    return _path_from_text(payload.get(key), repo_root)


def _find_lifecycle_path(
    repo_root: Path,
    trade_date: str,
    run_root: Path | None,
    execution_results: Mapping[str, Any],
    execution_payload: Mapping[str, Any],
) -> Path | None:
    from_payload = _source_artifact_path(execution_results, "candidate_trade_lifecycle_artifact", repo_root)
    from_execution = _source_artifact_path(execution_payload, "candidate_trade_lifecycle_artifact", repo_root)
    expected = run_root / "audit" / f"candidate_trade_lifecycle_{trade_date}.json" if run_root else None
    return _existing_path(repo_root, from_payload, from_execution, expected)


def _find_reconciliation_path(
    repo_root: Path,
    trade_date: str,
    run_root: Path | None,
    execution_results: Mapping[str, Any],
    execution_payload: Mapping[str, Any],
) -> Path | None:
    explicit = _source_artifact_path(execution_results, "posttrade_reconciliation_path", repo_root)
    explicit = explicit or _source_artifact_path(execution_payload, "posttrade_reconciliation_path", repo_root)
    if not run_root:
        return _existing_path(repo_root, explicit)
    broker = run_root / "broker"
    return _existing_path(
        repo_root,
        explicit,
        broker / f"recon_posttrade_{trade_date}.json",
        broker / f"posttrade_reconciliation_{trade_date}.json",
        broker / f"reconciliation_{trade_date}.json",
        run_root / "posttrade_reconciliation.json",
        run_root / "reconciliation.json",
    )


def _find_target_portfolio_path(repo_root: Path, trade_date: str, run_root: Path | None) -> Path | None:
    return _existing_path(
        repo_root,
        run_root / "target_portfolio.json" if run_root else None,
        run_root / f"target_portfolio_{trade_date}.json" if run_root else None,
        repo_root / "outputs" / "precompute" / trade_date / "target_portfolio.json",
        repo_root / "outputs" / "precompute" / trade_date / f"target_portfolio_{trade_date}.json",
        repo_root / "outputs" / "target_portfolio" / f"target_portfolio_{trade_date}.json",
        repo_root / "outputs" / "targets" / f"target_portfolio_{trade_date}.json",
    )


def _find_broker_positions_path(repo_root: Path, run_root: Path | None) -> Path | None:
    if not run_root:
        return None
    return _existing_path(
        repo_root,
        run_root / "broker" / "posttrade_positions.json",
        run_root / "broker" / "positions_posttrade.json",
        run_root / "posttrade_positions.json",
    )


def _find_posttrade_account_path(repo_root: Path, run_root: Path | None) -> Path | None:
    if not run_root:
        return None
    return _existing_path(
        repo_root,
        run_root / "broker" / "posttrade_account_snapshot.json",
        run_root / "posttrade_account_snapshot.json",
    )


def _find_risk_adjusted_targets_path(repo_root: Path, trade_date: str, run_root: Path | None) -> Path | None:
    return _existing_path(
        repo_root,
        run_root / "snapshots" / f"risk_adjusted_{trade_date}.json" if run_root else None,
        repo_root / "outputs" / "precompute" / trade_date / f"risk_adjusted_{trade_date}.json",
    )


def _find_risk_controls_path(repo_root: Path, run_root: Path | None) -> Path | None:
    if not run_root:
        return None
    return _existing_path(repo_root, run_root / "audit" / "risk_controls.json")


def _find_execution_target_attainment_path(repo_root: Path, trade_date: str, run_root: Path | None) -> Path | None:
    if not run_root:
        return None
    return _existing_path(repo_root, run_root / "audit" / f"execution_target_attainment_{trade_date}.json")


def _find_execution_integrity_path(repo_root: Path, run_root: Path | None) -> Path | None:
    if not run_root:
        return None
    return _existing_path(repo_root, run_root / "audit" / "execution_integrity.json")


def _find_intended_orders_path(repo_root: Path, trade_date: str, run_root: Path | None) -> Path | None:
    if not run_root:
        return None
    return _existing_path(repo_root, run_root / "broker" / f"intended_orders_{trade_date}.json")


def _find_construction_provenance_path(repo_root: Path, trade_date: str, run_root: Path | None) -> Path | None:
    return _existing_path(
        repo_root,
        run_root / "audit" / f"construction_provenance_{trade_date}.json" if run_root else None,
        repo_root / "outputs" / "research" / "construction_provenance" / trade_date / "construction_provenance.json",
    )


def _find_post_sell_rebudget_path(repo_root: Path, trade_date: str, run_root: Path | None) -> Path | None:
    if not run_root:
        return None
    return _existing_path(repo_root, run_root / "broker" / f"post_sell_rebudget_{trade_date}.json")


def _source_artifacts_from_lifecycle(lifecycle: Mapping[str, Any], repo_root: Path) -> list[str]:
    paths: list[str] = []
    for row in _as_list(lifecycle.get("candidates")):
        if isinstance(row, Mapping):
            value = row.get("source_artifact_path")
            if isinstance(value, str) and value.strip():
                paths.append(_relative(value, repo_root) or value)
    return sorted(set(paths))


def _price_source(
    lifecycle: Mapping[str, Any],
    execution_results: Mapping[str, Any],
    execution_payload: Mapping[str, Any],
) -> str:
    value = _first_present(
        execution_results.get("price_source"),
        execution_results.get("pricing_source"),
        execution_payload.get("price_source"),
        execution_payload.get("pricing_source"),
        _as_dict(lifecycle.get("execution_config")).get("price_source"),
    )
    return str(value) if value is not None else "unavailable"


def _metadata_value(payload: Mapping[str, Any], key: str) -> Any:
    metadata = _as_dict(payload.get("meta")) or _as_dict(payload.get("metadata"))
    return _first_present(metadata.get(key), payload.get(key))


def _artifact_asof_values(
    *,
    precompute_signals: Mapping[str, Any],
    risk_adjusted_targets: Mapping[str, Any],
    execution_payload: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "data_asof": _first_present(
            _metadata_value(risk_adjusted_targets, "asof_date"),
            _metadata_value(precompute_signals, "asof_date"),
            execution_payload.get("security_master_asof_date"),
        ),
        "price_asof": _first_present(
            execution_payload.get("pricing_asof"),
            _metadata_value(risk_adjusted_targets, "asof_date"),
            _metadata_value(precompute_signals, "asof_date"),
        ),
    }


def _signal_rows(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [row for row in _as_list(payload.get("signals")) if isinstance(row, Mapping)]


def _ticker(value: Any) -> str | None:
    ticker = str(value or "").strip().upper()
    return ticker if ticker and ticker != "CASH" else None


def _source_entry(path: Path | None, repo_root: Path) -> dict[str, Any]:
    return {
        "path": _relative(path, repo_root),
        "status": "FOUND" if path is not None and path.exists() else "UNAVAILABLE",
    }


def _candidate_universe_from_signals(
    *,
    repo_root: Path,
    trade_date: str,
    run_id: str,
    precompute_signals: Mapping[str, Any],
    precompute_signals_path: Path | None,
    generated_at: str,
    git_sha: str,
) -> dict[str, Any]:
    symbols = sorted(
        {
            ticker
            for ticker in (_ticker(row.get("ticker")) for row in _signal_rows(precompute_signals))
            if ticker is not None
        }
    )
    asof = _metadata_value(precompute_signals, "asof_date")
    found = bool(symbols and precompute_signals_path is not None and precompute_signals_path.exists())
    unavailable = [] if found else ["symbols"]
    return {
        "schema_version": CANDIDATE_UNIVERSE_SCHEMA_VERSION,
        "metadata": {
            "trade_date": trade_date,
            "run_id": run_id,
            "generated_at": generated_at,
            "git_sha": git_sha,
            "mode": "research_only",
            "fr_id": FR_ID,
            "alpha_chase_default": "off",
            "production_execution_modules_invoked": [],
        },
        "trade_date": trade_date,
        "run_id": run_id,
        "readiness": {
            "status": "FOUND" if found else "MISSING",
            "reason": "precompute_signals_symbols_found" if found else "precompute_signals_symbols_unavailable",
        },
        "source_artifacts": {
            "precompute_signals": _source_entry(precompute_signals_path, repo_root),
        },
        "candidate_universe_count": len(symbols),
        "symbols": symbols,
        "field_sources": {
            "symbols": "precompute_signals.signals[].ticker" if found else "unavailable",
            "candidate_universe_count": "precompute_signals.signals[].ticker" if found else "unavailable",
            "asof": "precompute_signals.meta.asof_date" if asof else "unavailable",
        },
        "asof": asof,
        "unavailable_fields": unavailable,
        "trading_behavior_changed": False,
    }


def _candidate_pool_from_signals(
    *,
    repo_root: Path,
    trade_date: str,
    run_id: str,
    precompute_signals: Mapping[str, Any],
    precompute_signals_path: Path | None,
    generated_at: str,
    git_sha: str,
) -> dict[str, Any]:
    grouped: dict[str, dict[str, Any]] = {}
    source_path = _relative(precompute_signals_path, repo_root)
    for row in _signal_rows(precompute_signals):
        ticker = _ticker(row.get("ticker"))
        if ticker is None:
            continue
        candidate = grouped.setdefault(
            ticker,
            {
                "ticker": ticker,
                "source_artifacts": [source_path] if source_path else [],
                "sleeve_sources": [],
                "candidate_status": "available",
                "score_source": "UNAVAILABLE",
                "score_value": None,
                "field_sources": {
                    "ticker": "precompute_signals.signals[].ticker",
                    "sleeve_sources": "precompute_signals.signals[].sleeve",
                    "candidate_status": "precompute_signals.signals[]",
                    "score_source": "unavailable_no_explicit_non_weight_score_provenance",
                    "score_value": "unavailable_no_explicit_non_weight_score_provenance",
                },
                "unavailable_fields": ["score_source", "score_value"],
                "trading_behavior_changed": False,
            },
        )
        sleeve = row.get("sleeve")
        if sleeve not in (None, ""):
            candidate["sleeve_sources"].append(str(sleeve))
    candidates = []
    for ticker in sorted(grouped):
        candidate = grouped[ticker]
        candidate["sleeve_sources"] = sorted(set(candidate["sleeve_sources"]))
        candidates.append(candidate)
    found = bool(candidates and precompute_signals_path is not None and precompute_signals_path.exists())
    return {
        "schema_version": CANDIDATE_POOL_SCHEMA_VERSION,
        "metadata": {
            "trade_date": trade_date,
            "run_id": run_id,
            "generated_at": generated_at,
            "git_sha": git_sha,
            "mode": "research_only",
            "fr_id": FR_ID,
            "alpha_chase_default": "off",
            "production_execution_modules_invoked": [],
        },
        "trade_date": trade_date,
        "run_id": run_id,
        "readiness": {
            "status": "FOUND" if found else "MISSING",
            "reason": "precompute_signals_candidates_found" if found else "precompute_signals_candidates_unavailable",
        },
        "source_artifacts": {
            "precompute_signals": _source_entry(precompute_signals_path, repo_root),
        },
        "candidate_count": len(candidates),
        "candidates": candidates,
        "field_sources": {
            "candidates": "precompute_signals.signals[]",
            "candidate_count": "precompute_signals.signals[].ticker",
            "score_source": "unavailable_no_explicit_non_weight_score_provenance",
            "score_value": "unavailable_no_explicit_non_weight_score_provenance",
        },
        "unavailable_fields": ["score_source", "score_value"] if candidates else ["candidates"],
        "trading_behavior_changed": False,
    }


def _candidate_artifact_found(payload: Mapping[str, Any]) -> bool:
    return _as_dict(payload.get("readiness")).get("status") == "FOUND"


def _target_portfolio_from_risk_adjusted_targets(
    *,
    repo_root: Path,
    trade_date: str,
    run_id: str,
    risk_adjusted_targets: Mapping[str, Any],
    risk_adjusted_targets_path: Path | None,
    generated_at: str,
    git_sha: str,
) -> dict[str, Any]:
    targets: list[dict[str, Any]] = []
    source_path = _relative(risk_adjusted_targets_path, repo_root)
    for row in _signal_rows(risk_adjusted_targets):
        ticker = _ticker(row.get("ticker"))
        if ticker is None:
            continue
        target_weight = _float(row.get("target_weight"))
        if target_weight is None:
            continue
        target_notional = _float(_first_present(row.get("target_notional"), row.get("notional")))
        unavailable_fields = []
        if target_notional is None:
            unavailable_fields.append("target_notional")
        targets.append(
            {
                "ticker": ticker,
                "target_weight": target_weight,
                "target_notional": target_notional,
                "sleeve_sources": [str(row.get("sleeve"))] if row.get("sleeve") not in (None, "") else [],
                "source_artifacts": [source_path] if source_path else [],
                "field_sources": {
                    "ticker": "risk_adjusted_targets.signals[].ticker",
                    "target_weight": "risk_adjusted_targets.signals[].target_weight",
                    "target_notional": (
                        "risk_adjusted_targets.signals[].target_notional"
                        if target_notional is not None
                        else "unavailable"
                    ),
                    "sleeve_sources": "risk_adjusted_targets.signals[].sleeve" if row.get("sleeve") else "unavailable",
                },
                "unavailable_fields": unavailable_fields,
                "trading_behavior_changed": False,
            }
        )
    targets = sorted(targets, key=lambda item: str(item["ticker"]))
    found = bool(targets and risk_adjusted_targets_path is not None and risk_adjusted_targets_path.exists())
    return {
        "schema_version": TARGET_PORTFOLIO_SCHEMA_VERSION,
        "metadata": {
            "trade_date": trade_date,
            "run_id": run_id,
            "generated_at": generated_at,
            "git_sha": git_sha,
            "mode": "research_only",
            "fr_id": FR_ID,
            "alpha_chase_default": "off",
            "production_execution_modules_invoked": [],
        },
        "trade_date": trade_date,
        "run_id": run_id,
        "readiness": {
            "status": "FOUND" if found else "MISSING",
            "reason": (
                "risk_adjusted_targets_found"
                if found
                else "risk_adjusted_targets_unavailable_or_missing_target_weights"
            ),
        },
        "source_artifacts": {
            "risk_adjusted_targets": _source_entry(risk_adjusted_targets_path, repo_root),
        },
        "target_count": len(targets),
        "targets": targets,
        "field_sources": {
            "targets": "risk_adjusted_targets.signals[]",
            "target_count": "risk_adjusted_targets.signals[].ticker",
            "target_weight": "risk_adjusted_targets.signals[].target_weight",
            "target_notional": "risk_adjusted_targets.signals[].target_notional if present",
        },
        "unavailable_fields": ["target_notional"] if any("target_notional" in row["unavailable_fields"] for row in targets) else [],
        "trading_behavior_changed": False,
    }


def _target_weights_from_artifact(payload: Mapping[str, Any]) -> dict[str, float]:
    weights: dict[str, float] = {}
    for row in _as_list(payload.get("targets")):
        if not isinstance(row, Mapping):
            continue
        ticker = _ticker(row.get("ticker"))
        weight = _float(row.get("target_weight"))
        if ticker is not None and weight is not None:
            weights[ticker] = weight
    return weights


def _current_weights_from_contract(contract: Mapping[str, Any]) -> dict[str, float]:
    weights: dict[str, float] = {}
    for row in _as_list(_as_dict(contract.get("current_portfolio")).get("positions")):
        if not isinstance(row, Mapping):
            continue
        ticker = _ticker(_first_present(row.get("ticker"), row.get("symbol")))
        weight = _float(_first_present(row.get("current_weight"), row.get("weight")))
        if ticker is not None and weight is not None:
            weights[ticker] = weight
    return weights


def _reason_map(*rows: list[Any]) -> dict[str, dict[str, Any]]:
    reasons: dict[str, dict[str, Any]] = {}
    for group in rows:
        for item in group:
            if not isinstance(item, Mapping):
                continue
            ticker = _ticker(item.get("ticker"))
            if ticker is None:
                continue
            reason = _first_present(item.get("reason"), item.get("block_reason"), item.get("decision_reason"), item.get("code"))
            if reason in (None, ""):
                continue
            reasons[ticker] = {
                "reason": str(reason),
                "source_fields": [
                    key
                    for key in ("reason", "block_reason", "decision_reason", "code")
                    if item.get(key) not in (None, "")
                ],
            }
    return reasons


def _candidate_lifecycle_from_artifacts(
    *,
    repo_root: Path,
    trade_date: str,
    run_id: str,
    contract: Mapping[str, Any],
    candidate_pool: Mapping[str, Any],
    candidate_pool_path: Path | None,
    target_portfolio: Mapping[str, Any],
    target_portfolio_path: Path | None,
    post_sell_rebudget: Mapping[str, Any],
    post_sell_rebudget_path: Path | None,
    target_attainment: Mapping[str, Any],
    target_attainment_path: Path | None,
    execution_integrity: Mapping[str, Any],
    execution_integrity_path: Path | None,
    generated_at: str,
    git_sha: str,
) -> dict[str, Any]:
    target_weights = _target_weights_from_artifact(target_portfolio)
    current_weights = _current_weights_from_contract(contract)
    skipped_reasons = _reason_map(_as_list(post_sell_rebudget.get("skipped_buy_orders")))
    suppressed_reasons = _reason_map(
        _as_list(target_attainment.get("missing_intended_buys")),
        _as_list(execution_integrity.get("missing_intended_orders")),
        _as_list(execution_integrity.get("missing_buy_orders")),
    )
    lifecycle_rows: list[dict[str, Any]] = []
    for row in _as_list(candidate_pool.get("candidates")):
        if not isinstance(row, Mapping):
            continue
        ticker = _ticker(row.get("ticker"))
        if ticker is None:
            continue
        target_weight = target_weights.get(ticker)
        current_weight = current_weights.get(ticker)
        unavailable_fields: list[str] = []
        suppression_or_block_reason = None
        reason_source_artifact = None
        if ticker in skipped_reasons:
            status = "skipped"
            suppression_or_block_reason = skipped_reasons[ticker]["reason"]
            reason_source_artifact = _relative(post_sell_rebudget_path, repo_root)
        elif ticker in suppressed_reasons:
            status = "suppressed"
            suppression_or_block_reason = suppressed_reasons[ticker]["reason"]
            reason_source_artifact = _first_present(
                _relative(target_attainment_path, repo_root),
                _relative(execution_integrity_path, repo_root),
            )
        elif target_weight is not None and current_weight is not None and current_weight > 0:
            status = "retained"
            unavailable_fields.append("suppression_or_block_reason")
        elif target_weight is not None:
            status = "selected"
            unavailable_fields.append("suppression_or_block_reason")
        else:
            status = "unavailable"
            unavailable_fields.extend(["target_weight", "suppression_or_block_reason"])
        lifecycle_rows.append(
            {
                "ticker": ticker,
                "lifecycle_status": status,
                "target_weight": target_weight,
                "current_weight": current_weight,
                "suppression_or_block_reason": suppression_or_block_reason,
                "reason_source_artifact": reason_source_artifact,
                "source_artifacts": sorted(
                    artifact
                    for artifact in {
                        _relative(candidate_pool_path, repo_root),
                        _relative(target_portfolio_path, repo_root),
                        reason_source_artifact,
                    }
                    if artifact
                ),
                "field_sources": {
                    "ticker": "candidate_pool.candidates[].ticker",
                    "lifecycle_status": "target_portfolio/current_portfolio plus explicit skip/suppression artifacts",
                    "target_weight": "target_portfolio.targets[].target_weight" if target_weight is not None else "unavailable",
                    "current_weight": "global_optimizer_replay_contract.current_portfolio.positions[].current_weight" if current_weight is not None else "unavailable",
                    "suppression_or_block_reason": reason_source_artifact or "unavailable",
                },
                "unavailable_fields": sorted(set(unavailable_fields)),
                "trading_behavior_changed": False,
            }
        )
    lifecycle_rows = sorted(lifecycle_rows, key=lambda item: str(item["ticker"]))
    found = bool(
        lifecycle_rows
        and candidate_pool_path is not None
        and candidate_pool_path.exists()
        and target_portfolio_path is not None
        and target_portfolio_path.exists()
    )
    status_counts = dict(sorted(Counter(str(row["lifecycle_status"]) for row in lifecycle_rows).items()))
    return {
        "schema_version": CANDIDATE_LIFECYCLE_SCHEMA_VERSION,
        "metadata": {
            "trade_date": trade_date,
            "run_id": run_id,
            "generated_at": generated_at,
            "git_sha": git_sha,
            "mode": "research_only",
            "fr_id": FR_ID,
            "alpha_chase_default": "off",
            "production_execution_modules_invoked": [],
        },
        "trade_date": trade_date,
        "run_id": run_id,
        "readiness": {
            "status": "FOUND" if found else "MISSING",
            "reason": "candidate_pool_and_target_portfolio_found" if found else "candidate_pool_or_target_portfolio_unavailable",
        },
        "source_artifacts": {
            "candidate_pool": _source_entry(candidate_pool_path, repo_root),
            "target_portfolio": _source_entry(target_portfolio_path, repo_root),
            "post_sell_rebudget": _source_entry(post_sell_rebudget_path, repo_root),
            "execution_target_attainment": _source_entry(target_attainment_path, repo_root),
            "execution_integrity": _source_entry(execution_integrity_path, repo_root),
        },
        "lifecycle_count": len(lifecycle_rows),
        "status_counts": status_counts,
        "candidates": lifecycle_rows,
        "field_sources": {
            "candidates": "candidate_pool.candidates[]",
            "target_weight": "target_portfolio.targets[].target_weight",
            "current_weight": "global_optimizer_replay_contract.current_portfolio.positions[]",
            "suppression_or_block_reason": "post_sell_rebudget/execution_target_attainment/execution_integrity when present",
        },
        "unavailable_fields": sorted(
            set(field for row in lifecycle_rows for field in _as_list(row.get("unavailable_fields")))
        ),
        "trading_behavior_changed": False,
    }


def _universe_snapshot_from_artifact(payload: Mapping[str, Any], path: Path, repo_root: Path) -> dict[str, Any]:
    return {
        "status": "FOUND",
        "universe_id": f"fr105_candidate_universe_{payload.get('trade_date')}",
        "asof": payload.get("asof"),
        "ticker_count": _int(payload.get("candidate_universe_count")),
        "source_artifact_path": _relative(path, repo_root),
    }


def _sleeve_candidates_from_candidate_pool(
    payload: Mapping[str, Any],
    path: Path,
    repo_root: Path,
    data_asof: Any,
) -> list[dict[str, Any]]:
    source_path = _relative(path, repo_root)
    rows: list[dict[str, Any]] = []
    for row in _as_list(payload.get("candidates")):
        if not isinstance(row, Mapping):
            continue
        sleeves = [str(item) for item in _as_list(row.get("sleeve_sources")) if item not in (None, "")]
        sleeve = sleeves[0] if len(sleeves) == 1 else None
        rows.append(
            {
                "ticker": row.get("ticker"),
                "sleeve_id": sleeve,
                "strategy_id": "current_policy_sleeve_merge",
                "source_model": sleeve,
                "lifecycle": {
                    "decision_stage": "candidate_pool_artifact",
                    "decision_reason": row.get("candidate_status"),
                },
                "rank": None,
                "score": None,
                "conviction_score": None,
                "expected_alpha": None,
                "expected_risk": None,
                "target_weight": None,
                "target_notional": None,
                "current_weight": None,
                "current_notional": None,
                "delta_notional": None,
                "reason_included": "present_in_candidate_pool",
                "reason_excluded": None,
                "data_asof": data_asof,
                "source_artifact_path": source_path,
            }
        )
    return rows


def _selected_target_candidates(
    *,
    precompute_signals: Mapping[str, Any],
    risk_adjusted_targets: Mapping[str, Any],
    precompute_signals_path: Path | None,
    risk_adjusted_targets_path: Path | None,
    repo_root: Path,
) -> list[dict[str, Any]]:
    pre_rows = {
        str(row.get("ticker") or "").strip().upper(): row
        for row in _signal_rows(precompute_signals)
        if str(row.get("ticker") or "").strip().upper() and str(row.get("ticker") or "").strip().upper() != "CASH"
    }
    final_rows = {
        str(row.get("ticker") or "").strip().upper(): row
        for row in _signal_rows(risk_adjusted_targets)
        if str(row.get("ticker") or "").strip().upper() and str(row.get("ticker") or "").strip().upper() != "CASH"
    }
    tickers = sorted(set(pre_rows) | set(final_rows))
    data_asof = _first_present(
        _metadata_value(risk_adjusted_targets, "asof_date"),
        _metadata_value(precompute_signals, "asof_date"),
    )
    rows: list[dict[str, Any]] = []
    for ticker in tickers:
        pre = pre_rows.get(ticker, {})
        final = final_rows.get(ticker, {})
        source_path = risk_adjusted_targets_path if final else precompute_signals_path
        rows.append(
            {
                "ticker": ticker,
                "sleeve_id": _first_present(final.get("sleeve"), pre.get("sleeve")),
                "strategy_id": "current_policy_sleeve_merge",
                "source_model": _first_present(final.get("sleeve"), pre.get("sleeve")),
                "pre_risk_target_weight": _float(pre.get("target_weight")),
                "final_target_weight": _float(_first_present(final.get("target_weight"), pre.get("target_weight"))),
                "target_weight": _float(_first_present(final.get("target_weight"), pre.get("target_weight"))),
                "target_weight_source": "risk_adjusted_targets" if final else "precompute_signals",
                "data_asof": data_asof,
                "source_artifact_path": _relative(source_path, repo_root),
                "score": None,
                "conviction_score": None,
                "expected_alpha": None,
                "score_source": None,
                "score_status": "UNAVAILABLE",
                "score_reason": "target_or_allocation_weights_are_not_alpha_scores",
            }
        )
    return rows


def _candidate_lifecycle_snapshot(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "side": row.get("side"),
        "candidate_source": row.get("candidate_source"),
        "stages": row.get("stages"),
        "submitted": row.get("submitted"),
        "accepted": row.get("accepted"),
        "filled": row.get("filled"),
        "rejected": row.get("rejected"),
        "clipped": row.get("clipped"),
        "decision_stage": row.get("decision_stage"),
        "decision_reason": row.get("decision_reason") or row.get("suppression_or_clipping_reason"),
        "code_stage_responsible": row.get("code_stage_responsible"),
        "code_path_responsible": row.get("code_path_responsible") or row.get("responsible_code_path"),
        "precompute": {
            "shares": row.get("precompute_shares"),
            "price": row.get("precompute_price"),
            "notional": row.get("precompute_notional"),
        },
        "executable": {
            "shares": row.get("normalized_executable_shares"),
            "price": row.get("normalized_executable_price"),
            "notional": row.get("normalized_executable_notional"),
            "passed_min_notional": row.get("passed_min_notional"),
        },
        "intended": {
            "shares": row.get("intended_shares"),
            "price": row.get("intended_price"),
            "notional": row.get("intended_notional"),
            "reached_intended_orders": row.get("reached_intended_orders"),
        },
        "final": {
            "submitted_shares": row.get("final_submitted_shares"),
            "filled_shares": row.get("final_filled_shares"),
            "broker_statuses": row.get("broker_statuses"),
        },
    }


def _sleeve_candidates_from_lifecycle(
    lifecycle: Mapping[str, Any],
    lifecycle_path: Path | None,
    repo_root: Path,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    source_artifact_path = _relative(lifecycle_path, repo_root) if lifecycle_path else None
    for item in _as_list(lifecycle.get("candidates")):
        if not isinstance(item, Mapping):
            continue
        submitted = bool(item.get("submitted"))
        rows.append(
            {
                "ticker": item.get("ticker"),
                "sleeve_id": item.get("sleeve_id"),
                "strategy_id": item.get("strategy_id"),
                "source_model": item.get("source_model"),
                "lifecycle": _candidate_lifecycle_snapshot(item),
                "rank": item.get("candidate_rank"),
                "score": item.get("score"),
                "conviction_score": item.get("conviction_score"),
                "expected_alpha": item.get("expected_alpha"),
                "expected_risk": item.get("expected_risk"),
                "target_weight": item.get("target_weight"),
                "target_notional": item.get("target_notional"),
                "current_weight": item.get("current_weight"),
                "current_notional": item.get("current_notional"),
                "delta_notional": item.get("delta_notional"),
                "reason_included": item.get("reason_included") or item.get("precompute_reason"),
                "reason_excluded": (
                    item.get("reason_excluded")
                    or (item.get("decision_reason") or item.get("suppression_or_clipping_reason"))
                    if not submitted
                    else None
                ),
                "data_asof": _first_present(item.get("data_asof"), item.get("asof"), item.get("decision_date")),
                "source_artifact_path": item.get("source_artifact_path") or source_artifact_path,
            }
        )
    return rows


def _account_equity(payload: Any) -> float | None:
    account = _as_dict(_as_dict(payload).get("account"))
    return _float(_first_present(_as_dict(payload).get("equity"), account.get("equity"), account.get("portfolio_value")))


def _positions_from_payload(payload: Any, account_payload: Any = None) -> tuple[list[dict[str, Any]], int | None]:
    if payload is None:
        return [], None
    if isinstance(payload, list):
        raw_rows = payload
    else:
        obj = _as_dict(payload)
        raw_rows = (
            _as_list(obj.get("positions"))
            or _as_list(obj.get("broker_positions"))
            or _as_list(obj.get("posttrade_positions"))
            or _as_list(obj.get("holdings"))
        )
    positions: list[dict[str, Any]] = []
    equity = _account_equity(account_payload)
    for row in raw_rows:
        if not isinstance(row, Mapping):
            continue
        market_value = _float(_first_present(row.get("market_value"), row.get("notional")))
        current_weight = _float(_first_present(row.get("current_weight"), row.get("weight")))
        if current_weight is None and market_value is not None and equity is not None and equity > 0:
            current_weight = market_value / equity
        positions.append(
            {
                "ticker": _first_present(row.get("ticker"), row.get("symbol")),
                "quantity": _float(_first_present(row.get("quantity"), row.get("qty"), row.get("shares"))),
                "market_value": market_value,
                "current_weight": current_weight,
            }
        )
    return positions, len(positions)


def _execution_residuals_from_lifecycle(lifecycle: Mapping[str, Any]) -> dict[str, Any]:
    candidates = [row for row in _as_list(lifecycle.get("candidates")) if isinstance(row, Mapping)]
    counts = _as_dict(lifecycle.get("counts"))
    has_lifecycle = bool(lifecycle)
    unexecuted_values = [
        value
        for value in (_float(row.get("estimated_unexecuted_notional")) for row in candidates)
        if value is not None
    ]
    total_unexecuted = round(float(sum(unexecuted_values)), 10) if has_lifecycle else None
    return {
        "planned_candidates": _first_present(counts.get("precompute_candidates"), len(candidates) if has_lifecycle else None),
        "executable_candidates": _first_present(
            counts.get("passed_executable_filter"),
            sum(1 for row in candidates if row.get("passed_min_notional") is True) if has_lifecycle else None,
        ),
        "intended_orders": _first_present(
            counts.get("intended_orders"),
            sum(1 for row in candidates if row.get("reached_intended_orders")) if has_lifecycle else None,
        ),
        "submitted_orders": _first_present(
            counts.get("submitted"),
            sum(1 for row in candidates if row.get("submitted")) if has_lifecycle else None,
        ),
        "filled_orders": _first_present(
            counts.get("filled"),
            sum(1 for row in candidates if row.get("filled")) if has_lifecycle else None,
        ),
        "suppressed_count": _first_present(
            counts.get("suppressed"),
            sum(
                1
                for row in candidates
                if not row.get("submitted") and row.get("suppression_or_clipping_reason")
            )
            if has_lifecycle
            else None,
        ),
        "clipped_count": _first_present(
            counts.get("clipped"),
            sum(1 for row in candidates if row.get("clipped")) if has_lifecycle else None,
        ),
        "suppression_reason_counts": _as_dict(counts.get("suppression_reason_counts")),
        "clipping_reason_counts": _as_dict(counts.get("clipping_reason_counts")),
        "estimated_unexecuted_notional_total": total_unexecuted,
    }


def _count_reasons(rows: list[Any], default_reason: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for item in rows:
        row = item if isinstance(item, Mapping) else {}
        reason = str(
            _first_present(
                row.get("reason"),
                row.get("block_reason"),
                row.get("decision_reason"),
                row.get("code"),
                default_reason,
            )
        )
        counts[reason] += 1
    return dict(sorted(counts.items()))


def _merge_reason_counts(*counts: Mapping[str, Any]) -> dict[str, int]:
    merged: Counter[str] = Counter()
    for mapping in counts:
        for key, value in _as_dict(mapping).items():
            try:
                merged[str(key)] += int(value)
            except (TypeError, ValueError):
                merged[str(key)] += 1
    return dict(sorted(merged.items()))


def _execution_residuals_from_artifacts(
    *,
    lifecycle: Mapping[str, Any],
    execution_results: Mapping[str, Any],
    execution_payload: Mapping[str, Any],
    target_attainment: Mapping[str, Any],
    execution_integrity: Mapping[str, Any],
    post_sell_rebudget: Mapping[str, Any],
    intended_orders: Mapping[str, Any],
) -> dict[str, Any]:
    if lifecycle:
        return _execution_residuals_from_lifecycle(lifecycle)
    skipped_buy_orders = _as_list(post_sell_rebudget.get("skipped_buy_orders"))
    missing_intended = _as_list(
        _first_present(
            target_attainment.get("missing_intended_buys"),
            execution_integrity.get("missing_intended_orders"),
            execution_integrity.get("missing_buy_orders"),
        )
    )
    suppression_reason_counts = _merge_reason_counts(
        _count_reasons(skipped_buy_orders, "post_sell_rebudget_skipped_buy"),
        {} if skipped_buy_orders else _count_reasons(missing_intended, "missing_intended_buy"),
    )
    explicit_block_reasons = [str(item) for item in _as_list(execution_integrity.get("explicit_block_reasons"))]
    for reason in explicit_block_reasons:
        suppression_reason_counts[reason] = suppression_reason_counts.get(reason, 0) + 1
    clipped_count = sum(
        1
        for row in skipped_buy_orders
        if isinstance(row, Mapping)
        and (
            "clip" in str(row.get("block_reason") or "").lower()
            or row.get("allowed_notional") is not None
        )
    )
    return {
        "planned_candidates": _first_present(
            execution_results.get("planned_payload_trade_count"),
            execution_payload.get("planned_payload_trade_count"),
            execution_payload.get("planner_intended_trades_count"),
        ),
        "executable_candidates": _first_present(
            execution_results.get("executable_trades_count"),
            execution_payload.get("executable_trades_count"),
            execution_payload.get("execution_eligible_trades_count"),
        ),
        "intended_orders": _first_present(
            execution_integrity.get("intended_orders_count"),
            intended_orders.get("orders_intended_count"),
            execution_payload.get("planner_intended_trades_count"),
        ),
        "submitted_orders": _first_present(execution_results.get("submitted_count"), execution_payload.get("submitted_count")),
        "filled_orders": _first_present(execution_results.get("orders_filled_count"), execution_payload.get("orders_filled_count")),
        "suppressed_count": _first_present(
            execution_results.get("skipped_buy_count"),
            len(skipped_buy_orders) if skipped_buy_orders else len(missing_intended) if missing_intended else None,
        ),
        "clipped_count": clipped_count if skipped_buy_orders else None,
        "suppression_reason_counts": dict(sorted(suppression_reason_counts.items())),
        "clipping_reason_counts": (
            {"post_sell_rebudget_budget_clip": clipped_count}
            if clipped_count
            else {}
        ),
        "estimated_unexecuted_notional_total": _float(target_attainment.get("skipped_deferred_buy_notional")),
    }


def _config_constraints(repo_root: Path) -> tuple[dict[str, Any], Path | None]:
    config_path = repo_root / "paper" / "config_paper.json"
    config = _as_dict(_read_json(config_path))
    constraints = _as_dict(config.get("constraints"))
    risk = _as_dict(config.get("risk"))
    if not config:
        return {
            "max_single_name_weight": None,
            "sector_caps": None,
            "effective_n_floor": None,
            "turnover_cap": None,
            "liquidity_constraints": "unavailable",
            "min_trade_dollars": None,
            "cash_target": None,
            "gross_exposure_target": None,
            "buying_power_available": None,
            "rebudget_policy": "unavailable",
            "min_notional_policy": "unavailable",
            "source_artifact_path": None,
        }, None
    min_trade_dollars = _float(constraints.get("min_trade_dollars"))
    return {
        "max_single_name_weight": _float(risk.get("max_position_pct")),
        "sector_caps": None,
        "effective_n_floor": None,
        "turnover_cap": _float(risk.get("max_turnover_pct")),
        "liquidity_constraints": "unavailable",
        "min_trade_dollars": min_trade_dollars,
        "cash_target": _float(_first_present(constraints.get("cash_target_weight"), constraints.get("target_cash_weight"))),
        "gross_exposure_target": None,
        "buying_power_available": None,
        "rebudget_policy": "unavailable",
        "min_notional_policy": (
            {
                "min_trade_dollars": min_trade_dollars,
                "source_artifact_path": _relative(config_path, repo_root),
            }
            if min_trade_dollars is not None
            else "unavailable"
        ),
        "source_artifact_path": _relative(config_path, repo_root),
    }, config_path


def _constraints_snapshot(
    repo_root: Path,
    execution_results: Mapping[str, Any],
    execution_payload: Mapping[str, Any],
    lifecycle: Mapping[str, Any],
    post_sell_rebudget: Mapping[str, Any],
    risk_controls: Mapping[str, Any],
) -> dict[str, Any]:
    constraints, _ = _config_constraints(repo_root)
    cash_gate = _as_dict(
        execution_results.get("cash_gate_diagnostics")
        or execution_payload.get("cash_gate_diagnostics")
    )
    rebudget = post_sell_rebudget or _as_dict(cash_gate.get("post_sell_rebudget"))
    lifecycle_config = _as_dict(lifecycle.get("execution_config"))
    min_trade = _float(_first_present(lifecycle_config.get("min_trade_dollars"), constraints.get("min_trade_dollars")))
    if min_trade is not None:
        constraints["min_trade_dollars"] = min_trade
        constraints["min_notional_policy"] = {
            "min_trade_dollars": min_trade,
            "source_artifact_path": _first_present(
                _relative(Path(_as_dict(lifecycle.get("source_artifacts")).get("precompute_payload")), repo_root)
                if _as_dict(lifecycle.get("source_artifacts")).get("precompute_payload")
                else None,
                constraints.get("source_artifact_path"),
            ),
        }
    constraints["buying_power_available"] = _float(
        _first_present(
            execution_results.get("buying_power_at_buy_decision"),
            execution_payload.get("buying_power_at_buy_decision"),
            cash_gate.get("buying_power_at_buy_decision"),
            rebudget.get("post_sell_buying_power"),
            rebudget.get("buying_power_at_buy_decision"),
        )
    )
    constraints["cash_target"] = _float(
        _first_present(
            rebudget.get("target_cash_weight"),
            rebudget.get("risk_cash_target"),
            execution_payload.get("cash_target_weight"),
            execution_payload.get("target_cash_weight"),
            constraints.get("cash_target"),
        )
    )
    constraints["rebudget_policy"] = (
        {
            "enabled": rebudget.get("enabled"),
            "status": rebudget.get("status"),
            "reason_codes": rebudget.get("reason_codes"),
            "schema_version": rebudget.get("schema_version"),
        }
        if rebudget
        else "unavailable"
    )
    risk_result = _as_dict(risk_controls.get("result"))
    actions = _as_list(risk_result.get("actions"))
    if actions:
        constraints["risk_control_actions"] = actions
    metrics = _as_dict(risk_result.get("metrics"))
    if metrics:
        constraints["risk_control_metrics"] = metrics
    return constraints


def _contract_id(run_root: Path | None, run_id: str | None, trade_date: str) -> str:
    if run_id:
        return Path(run_id).name
    if run_root:
        return run_root.name
    return trade_date


def build_fr105_replay_contract(
    *,
    repo_root: Path | str,
    trade_date: str,
    run_id: str | None = None,
    generated_at: str | None = None,
    git_sha: str | None = None,
) -> dict[str, Any]:
    """Build, but do not write, the FR-105 replay contract."""

    root = Path(repo_root).resolve()
    run_root = _find_run_root(root, trade_date, run_id)
    execution_results_path = _existing_path(root, run_root / "execution_results.json" if run_root else None)
    execution_payload_path = _existing_path(root, run_root / "execution_payload.json" if run_root else None)
    execution_results = _as_dict(_read_json(execution_results_path))
    execution_payload = _as_dict(_read_json(execution_payload_path))
    lifecycle_path = _find_lifecycle_path(root, trade_date, run_root, execution_results, execution_payload)
    lifecycle = _as_dict(_read_json(lifecycle_path))
    reconciliation_path = _find_reconciliation_path(root, trade_date, run_root, execution_results, execution_payload)
    broker_positions_path = _find_broker_positions_path(root, run_root)
    posttrade_account_path = _find_posttrade_account_path(root, run_root)
    target_portfolio_path = _find_target_portfolio_path(root, trade_date, run_root)
    rebudget_path = _find_post_sell_rebudget_path(root, trade_date, run_root)
    post_sell_rebudget = _as_dict(_read_json(rebudget_path))
    precompute_signals_path = _find_precompute_path(root, trade_date, "signals.json")
    precompute_signals = _as_dict(_read_json(precompute_signals_path))
    daily_snapshot_path = _find_precompute_path(root, trade_date, "daily_snapshot.json")
    planned_payload_path = _find_precompute_path(root, trade_date, "planned_execution_payload.json")
    planned_payload = _as_dict(_read_json(planned_payload_path))
    risk_adjusted_targets_path = _find_risk_adjusted_targets_path(root, trade_date, run_root)
    risk_adjusted_targets = _as_dict(_read_json(risk_adjusted_targets_path))
    risk_controls_path = _find_risk_controls_path(root, run_root)
    risk_controls = _as_dict(_read_json(risk_controls_path))
    target_attainment_path = _find_execution_target_attainment_path(root, trade_date, run_root)
    target_attainment = _as_dict(_read_json(target_attainment_path))
    execution_integrity_path = _find_execution_integrity_path(root, run_root)
    execution_integrity = _as_dict(_read_json(execution_integrity_path))
    intended_orders_path = _find_intended_orders_path(root, trade_date, run_root)
    intended_orders = _as_dict(_read_json(intended_orders_path))
    construction_provenance_path = _find_construction_provenance_path(root, trade_date, run_root)
    asofs = _artifact_asof_values(
        precompute_signals=precompute_signals,
        risk_adjusted_targets=risk_adjusted_targets,
        execution_payload=planned_payload or execution_payload,
    )
    positions, positions_count = _positions_from_payload(
        _read_json(broker_positions_path),
        _read_json(posttrade_account_path),
    )
    contract_key = _contract_id(run_root, run_id, trade_date)

    contract: dict[str, Any] = {
        "metadata": {
            "trade_date": trade_date,
            "generated_at": generated_at or "unavailable",
            "git_sha": git_sha or _git_sha(root),
            "mode": "research_only",
            "fr_id": FR_ID,
            "schema_version": SCHEMA_VERSION,
            "contract_id": contract_key,
            "data_asof": asofs.get("data_asof"),
            "price_asof": asofs.get("price_asof"),
            "production_execution_modules_invoked": [],
        },
        "source_artifacts": {
            "candidate_universe_path": None,
            "candidate_pool_path": None,
            "candidate_trade_lifecycle_path": _relative(lifecycle_path, root),
            "target_portfolio_path": _relative(target_portfolio_path, root),
            "sleeve_artifacts": _source_artifacts_from_lifecycle(lifecycle, root),
            "execution_results_path": _relative(execution_results_path, root),
            "execution_payload_path": _relative(execution_payload_path, root),
            "reconciliation_path": _relative(reconciliation_path, root),
            "broker_positions_path": _relative(broker_positions_path, root),
            "posttrade_account_path": _relative(posttrade_account_path, root),
            "price_source": _price_source(lifecycle, execution_results, execution_payload),
            "post_sell_rebudget_path": _relative(rebudget_path, root),
            "precompute_signals_path": _relative(precompute_signals_path, root),
            "precompute_daily_snapshot_path": _relative(daily_snapshot_path, root),
            "planned_execution_payload_path": _relative(planned_payload_path, root),
            "risk_adjusted_targets_path": _relative(risk_adjusted_targets_path, root),
            "risk_controls_path": _relative(risk_controls_path, root),
            "execution_target_attainment_path": _relative(target_attainment_path, root),
            "execution_integrity_path": _relative(execution_integrity_path, root),
            "intended_orders_path": _relative(intended_orders_path, root),
            "construction_provenance_path": _relative(construction_provenance_path, root),
        },
        "universe_snapshot": {
            "status": "unavailable",
            "universe_id": None,
            "asof": trade_date,
            "ticker_count": None,
            "source_artifact_path": None,
        },
        "sleeve_candidates": _sleeve_candidates_from_lifecycle(lifecycle, lifecycle_path, root),
        "selected_target_candidates": _selected_target_candidates(
            precompute_signals=precompute_signals,
            risk_adjusted_targets=risk_adjusted_targets,
            precompute_signals_path=precompute_signals_path,
            risk_adjusted_targets_path=risk_adjusted_targets_path,
            repo_root=root,
        ),
        "current_portfolio": {
            "source_artifact_path": _relative(broker_positions_path, root),
            "account_source_artifact_path": _relative(posttrade_account_path, root),
            "positions_count": positions_count,
            "positions": positions,
        },
        "constraints_snapshot": _constraints_snapshot(
            root,
            execution_results,
            execution_payload,
            lifecycle,
            post_sell_rebudget,
            risk_controls,
        ),
        "execution_residuals": _execution_residuals_from_artifacts(
            lifecycle=lifecycle,
            execution_results=execution_results,
            execution_payload=execution_payload,
            target_attainment=target_attainment,
            execution_integrity=execution_integrity,
            post_sell_rebudget=post_sell_rebudget,
            intended_orders=intended_orders,
        ),
        "provenance_schema_version": PROVENANCE_SCHEMA_VERSION,
        "validation_status": {
            "status": "UNVALIDATED",
            "findings": [],
            "warnings": [],
        },
    }
    contract = _clean(contract)
    contract["validation_status"] = validate_fr105_replay_contract(contract).to_dict()
    return contract


def write_fr105_replay_contract(
    *,
    repo_root: Path | str,
    trade_date: str,
    run_id: str | None = None,
    output_root: Path | str = DEFAULT_OUTPUT_ROOT,
    generated_at: str | None = None,
    git_sha: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    root = Path(repo_root).resolve()
    contract = build_fr105_replay_contract(
        repo_root=root,
        trade_date=trade_date,
        run_id=run_id,
        generated_at=generated_at,
        git_sha=git_sha,
    )
    output = Path(output_root)
    if not output.is_absolute():
        output = root / output
    out_path = output / str(contract["metadata"]["contract_id"]) / "global_optimizer_replay_contract.json"
    out_dir = out_path.parent
    precompute_signals_path = _path_from_text(
        _as_dict(contract.get("source_artifacts")).get("precompute_signals_path"),
        root,
    )
    precompute_signals = _as_dict(_read_json(precompute_signals_path))
    generated = str(contract["metadata"]["generated_at"])
    git = str(contract["metadata"]["git_sha"])
    contract_id = str(contract["metadata"]["contract_id"])

    candidate_universe = _candidate_universe_from_signals(
        repo_root=root,
        trade_date=trade_date,
        run_id=contract_id,
        precompute_signals=precompute_signals,
        precompute_signals_path=precompute_signals_path,
        generated_at=generated,
        git_sha=git,
    )
    if _candidate_artifact_found(candidate_universe):
        candidate_universe_path = out_dir / "candidate_universe.json"
        _write_json(candidate_universe_path, candidate_universe)
        contract["source_artifacts"]["candidate_universe_path"] = _relative(candidate_universe_path, root)
        contract["universe_snapshot"] = _universe_snapshot_from_artifact(candidate_universe, candidate_universe_path, root)

    candidate_pool = _candidate_pool_from_signals(
        repo_root=root,
        trade_date=trade_date,
        run_id=contract_id,
        precompute_signals=precompute_signals,
        precompute_signals_path=precompute_signals_path,
        generated_at=generated,
        git_sha=git,
    )
    if _candidate_artifact_found(candidate_pool):
        candidate_pool_path = out_dir / "candidate_pool.json"
        _write_json(candidate_pool_path, candidate_pool)
        contract["source_artifacts"]["candidate_pool_path"] = _relative(candidate_pool_path, root)
        if not contract.get("sleeve_candidates"):
            contract["sleeve_candidates"] = _sleeve_candidates_from_candidate_pool(
                candidate_pool,
                candidate_pool_path,
                root,
                _as_dict(candidate_universe).get("asof") or _as_dict(contract.get("metadata")).get("data_asof"),
            )

    risk_adjusted_targets_path = _path_from_text(
        _as_dict(contract.get("source_artifacts")).get("risk_adjusted_targets_path"),
        root,
    )
    risk_adjusted_targets = _as_dict(_read_json(risk_adjusted_targets_path))
    target_portfolio = _target_portfolio_from_risk_adjusted_targets(
        repo_root=root,
        trade_date=trade_date,
        run_id=contract_id,
        risk_adjusted_targets=risk_adjusted_targets,
        risk_adjusted_targets_path=risk_adjusted_targets_path,
        generated_at=generated,
        git_sha=git,
    )
    if contract["source_artifacts"].get("target_portfolio_path") is None and _candidate_artifact_found(target_portfolio):
        target_portfolio_path = out_dir / "target_portfolio.json"
        _write_json(target_portfolio_path, target_portfolio)
        contract["source_artifacts"]["target_portfolio_path"] = _relative(target_portfolio_path, root)

    candidate_pool_path = _path_from_text(
        _as_dict(contract.get("source_artifacts")).get("candidate_pool_path"),
        root,
    )
    target_portfolio_path = _path_from_text(
        _as_dict(contract.get("source_artifacts")).get("target_portfolio_path"),
        root,
    )
    post_sell_rebudget_path = _path_from_text(
        _as_dict(contract.get("source_artifacts")).get("post_sell_rebudget_path"),
        root,
    )
    target_attainment_path = _path_from_text(
        _as_dict(contract.get("source_artifacts")).get("execution_target_attainment_path"),
        root,
    )
    execution_integrity_path = _path_from_text(
        _as_dict(contract.get("source_artifacts")).get("execution_integrity_path"),
        root,
    )
    candidate_lifecycle = _candidate_lifecycle_from_artifacts(
        repo_root=root,
        trade_date=trade_date,
        run_id=contract_id,
        contract=contract,
        candidate_pool=_as_dict(_read_json(candidate_pool_path)),
        candidate_pool_path=candidate_pool_path,
        target_portfolio=_as_dict(_read_json(target_portfolio_path)),
        target_portfolio_path=target_portfolio_path,
        post_sell_rebudget=_as_dict(_read_json(post_sell_rebudget_path)),
        post_sell_rebudget_path=post_sell_rebudget_path,
        target_attainment=_as_dict(_read_json(target_attainment_path)),
        target_attainment_path=target_attainment_path,
        execution_integrity=_as_dict(_read_json(execution_integrity_path)),
        execution_integrity_path=execution_integrity_path,
        generated_at=generated,
        git_sha=git,
    )
    if contract["source_artifacts"].get("candidate_trade_lifecycle_path") is None and _candidate_artifact_found(candidate_lifecycle):
        candidate_lifecycle_path = out_dir / "candidate_lifecycle.json"
        _write_json(candidate_lifecycle_path, candidate_lifecycle)
        contract["source_artifacts"]["candidate_trade_lifecycle_path"] = _relative(candidate_lifecycle_path, root)

    contract = _clean(contract)
    contract["validation_status"] = validate_fr105_replay_contract(contract).to_dict()
    _write_json(out_path, contract)
    return out_path, contract


def read_fr105_replay_contract(path: Path | str) -> dict[str, Any]:
    payload = _read_json(Path(path))
    return _as_dict(payload)


def _empty_string_paths(value: Any, prefix: str = "$") -> list[str]:
    if isinstance(value, str):
        return [prefix] if value == "" else []
    if isinstance(value, list):
        paths: list[str] = []
        for idx, item in enumerate(value):
            paths.extend(_empty_string_paths(item, f"{prefix}[{idx}]"))
        return paths
    if isinstance(value, Mapping):
        paths = []
        for key, item in value.items():
            paths.extend(_empty_string_paths(item, f"{prefix}.{key}"))
        return paths
    return []


def validate_fr105_replay_contract(contract: Mapping[str, Any]) -> FR105ValidationResult:
    findings: list[str] = []
    warnings: list[str] = []

    missing_sections = [key for key in REQUIRED_TOP_LEVEL_SECTIONS if key not in contract]
    if missing_sections:
        findings.append(f"MISSING_TOP_LEVEL_SECTIONS:{','.join(missing_sections)}")

    metadata = _as_dict(contract.get("metadata"))
    missing_metadata = [key for key in REQUIRED_METADATA_KEYS if key not in metadata]
    if missing_metadata:
        findings.append(f"MISSING_METADATA_KEYS:{','.join(missing_metadata)}")
    if metadata.get("mode") != "research_only":
        findings.append("MODE_NOT_RESEARCH_ONLY")
    if metadata.get("fr_id") != FR_ID:
        findings.append("FR_ID_MISMATCH")
    if not metadata.get("schema_version"):
        findings.append("MISSING_SCHEMA_VERSION")
    if "production_execution_modules_invoked" not in metadata:
        findings.append("MISSING_PRODUCTION_EXECUTION_MODULE_INVOCATION_RECORD")
    invoked = metadata.get("production_execution_modules_invoked")
    if invoked not in ([], None):
        findings.append("PRODUCTION_EXECUTION_MODULES_INVOKED")
    if isinstance(invoked, list):
        prohibited = sorted(set(str(item) for item in invoked if str(item) in PROHIBITED_PRODUCTION_MODULES))
        if prohibited:
            findings.append(f"PROHIBITED_PRODUCTION_MODULES:{','.join(prohibited)}")

    source_artifacts = _as_dict(contract.get("source_artifacts"))
    missing_source_keys = [key for key in REQUIRED_SOURCE_ARTIFACT_KEYS if key not in source_artifacts]
    if missing_source_keys:
        findings.append(f"MISSING_SOURCE_ARTIFACT_KEYS:{','.join(missing_source_keys)}")
    if not isinstance(source_artifacts.get("sleeve_artifacts"), list):
        findings.append("MALFORMED_LIST:source_artifacts.sleeve_artifacts")

    if not isinstance(contract.get("sleeve_candidates"), list):
        findings.append("MALFORMED_LIST:sleeve_candidates")
    else:
        for idx, row in enumerate(contract.get("sleeve_candidates") or []):
            if not isinstance(row, Mapping):
                findings.append(f"MALFORMED_CANDIDATE:{idx}")
                continue
            missing_candidate = [key for key in REQUIRED_CANDIDATE_KEYS if key not in row]
            if missing_candidate:
                findings.append(f"MISSING_CANDIDATE_KEYS:{idx}:{','.join(missing_candidate)}")

    current_portfolio = _as_dict(contract.get("current_portfolio"))
    if not isinstance(current_portfolio.get("positions"), list):
        findings.append("MALFORMED_LIST:current_portfolio.positions")

    constraints = _as_dict(contract.get("constraints_snapshot"))
    missing_constraints = [key for key in REQUIRED_CONSTRAINT_KEYS if key not in constraints]
    if missing_constraints:
        findings.append(f"MISSING_CONSTRAINT_KEYS:{','.join(missing_constraints)}")

    residuals = _as_dict(contract.get("execution_residuals"))
    missing_residuals = [key for key in REQUIRED_EXECUTION_RESIDUAL_KEYS if key not in residuals]
    if missing_residuals:
        findings.append(f"MISSING_EXECUTION_RESIDUAL_KEYS:{','.join(missing_residuals)}")
    for key in ("suppression_reason_counts", "clipping_reason_counts"):
        if key in residuals and not isinstance(residuals.get(key), Mapping):
            findings.append(f"MALFORMED_MAPPING:execution_residuals.{key}")

    if not contract.get("provenance_schema_version"):
        findings.append("MISSING_PROVENANCE_SCHEMA_VERSION")

    empty_strings = _empty_string_paths(contract)
    if empty_strings:
        findings.append(f"EMPTY_STRING_VALUES:{','.join(empty_strings[:10])}")
        if len(empty_strings) > 10:
            warnings.append(f"EMPTY_STRING_VALUES_TRUNCATED:{len(empty_strings)}")

    status = "PASS" if not findings else "FAIL"
    return FR105ValidationResult(
        status=status,
        findings=sorted(set(findings)),
        warnings=sorted(set(warnings)),
    )

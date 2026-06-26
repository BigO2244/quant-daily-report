"""FR-105 research-only replay contract builder.

This module reads already-written artifacts and emits a stable replay contract
for global optimizer research. It does not invoke allocation, sizing,
execution, broker, scheduler, paper, or live trading code.
"""
from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


FR_ID = "FR-105"
SCHEMA_VERSION = "fr105_global_optimizer_replay_contract.v1"
PROVENANCE_SCHEMA_VERSION = "fr105_candidate_provenance.v1"
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


def _positions_from_payload(payload: Any) -> tuple[list[dict[str, Any]], int | None]:
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
    for row in raw_rows:
        if not isinstance(row, Mapping):
            continue
        positions.append(
            {
                "ticker": _first_present(row.get("ticker"), row.get("symbol")),
                "quantity": _float(_first_present(row.get("quantity"), row.get("qty"), row.get("shares"))),
                "market_value": _float(_first_present(row.get("market_value"), row.get("notional"))),
                "current_weight": _float(_first_present(row.get("current_weight"), row.get("weight"))),
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
    target_portfolio_path = _find_target_portfolio_path(root, trade_date, run_root)
    rebudget_path = _find_post_sell_rebudget_path(root, trade_date, run_root)
    post_sell_rebudget = _as_dict(_read_json(rebudget_path))
    positions, positions_count = _positions_from_payload(_read_json(broker_positions_path))
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
            "production_execution_modules_invoked": [],
        },
        "source_artifacts": {
            "candidate_trade_lifecycle_path": _relative(lifecycle_path, root),
            "target_portfolio_path": _relative(target_portfolio_path, root),
            "sleeve_artifacts": _source_artifacts_from_lifecycle(lifecycle, root),
            "execution_results_path": _relative(execution_results_path, root),
            "reconciliation_path": _relative(reconciliation_path, root),
            "broker_positions_path": _relative(broker_positions_path, root),
            "price_source": _price_source(lifecycle, execution_results, execution_payload),
            "execution_payload_path": _relative(execution_payload_path, root),
            "post_sell_rebudget_path": _relative(rebudget_path, root),
        },
        "universe_snapshot": {
            "status": "unavailable",
            "universe_id": None,
            "asof": trade_date,
            "ticker_count": None,
            "source_artifact_path": None,
        },
        "sleeve_candidates": _sleeve_candidates_from_lifecycle(lifecycle, lifecycle_path, root),
        "current_portfolio": {
            "source_artifact_path": _relative(broker_positions_path, root),
            "positions_count": positions_count,
            "positions": positions,
        },
        "constraints_snapshot": _constraints_snapshot(
            root,
            execution_results,
            execution_payload,
            lifecycle,
            post_sell_rebudget,
        ),
        "execution_residuals": _execution_residuals_from_lifecycle(lifecycle),
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

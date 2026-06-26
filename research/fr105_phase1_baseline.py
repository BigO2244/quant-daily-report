"""FR-105 Phase 1 current-policy baseline harness.

This module consumes Phase 0 replay contracts and writes a research-only
baseline/control artifact. It does not invoke allocation, optimization, sizing,
execution, broker, scheduler, paper, or live trading code.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from research.fr105_replay_contract import (
    DEFAULT_OUTPUT_ROOT,
    FR_ID,
    PROHIBITED_PRODUCTION_MODULES,
    read_fr105_replay_contract,
    validate_fr105_replay_contract,
)


PHASE1_SCHEMA_VERSION = "fr105_phase1_current_policy_baseline.v1"
ARTIFACT_NAME = "phase1_current_policy_baseline.json"

REQUIRED_TOP_LEVEL_SECTIONS = (
    "metadata",
    "input_contract",
    "pit_controls",
    "current_policy_snapshot",
    "replay_window",
    "baseline_positions",
    "baseline_trades",
    "baseline_metrics",
    "data_quality",
    "validation_status",
)

REQUIRED_PIT_CONTROL_KEYS = (
    "trade_date",
    "data_asof",
    "universe_asof",
    "price_asof",
    "source_artifact_paths",
    "no_forward_returns_used",
    "no_production_modules_invoked",
    "unavailable_fields",
)

REQUIRED_BASELINE_METRIC_KEYS = (
    "position_count",
    "gross_exposure",
    "cash_weight",
    "max_single_name_weight",
    "HHI",
    "effective_N",
    "turnover",
    "planned_candidates",
    "submitted_orders",
    "filled_orders",
    "suppressed_count",
    "clipped_count",
    "estimated_unexecuted_notional_total",
)


@dataclass(frozen=True)
class FR105Phase1ValidationResult:
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


def _clean(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean(item) for item in value]
    return value


def _round(value: float | None, digits: int = 10) -> float | None:
    return round(float(value), digits) if value is not None and math.isfinite(float(value)) else None


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


def _contract_id(contract: Mapping[str, Any], trade_date: str) -> str:
    return str(_as_dict(contract.get("metadata")).get("contract_id") or trade_date)


def find_phase0_contract_path(
    *,
    repo_root: Path | str,
    trade_date: str,
    run_id: str | None = None,
    input_contract_path: Path | str | None = None,
    output_root: Path | str = DEFAULT_OUTPUT_ROOT,
) -> Path | None:
    root = Path(repo_root).resolve()
    if input_contract_path is not None:
        path = Path(input_contract_path)
        if not path.is_absolute():
            path = root / path
        return path if path.exists() else None
    out_root = Path(output_root)
    if not out_root.is_absolute():
        out_root = root / out_root
    if run_id:
        path = out_root / run_id / "global_optimizer_replay_contract.json"
        return path if path.exists() else None
    date_path = out_root / trade_date / "global_optimizer_replay_contract.json"
    if date_path.exists():
        return date_path
    matches: list[Path] = []
    for path in sorted(out_root.glob("*/global_optimizer_replay_contract.json")):
        payload = _as_dict(_read_json(path))
        if _as_dict(payload.get("metadata")).get("trade_date") == trade_date:
            matches.append(path)
    return matches[-1] if matches else None


def _source_artifact_paths(contract: Mapping[str, Any], input_contract_path: Path | None, repo_root: Path) -> dict[str, Any]:
    paths = dict(_as_dict(contract.get("source_artifacts")))
    paths["phase0_replay_contract_path"] = _relative(input_contract_path, repo_root)
    return paths


def _unavailable_fields(
    *,
    contract: Mapping[str, Any],
    source_artifact_paths: Mapping[str, Any],
    positions: list[Mapping[str, Any]],
    trades: list[Mapping[str, Any]],
    metrics: Mapping[str, Any],
    data_asof: Any,
    universe_asof: Any,
    price_asof: Any,
) -> list[str]:
    unavailable: list[str] = []
    for key, value in source_artifact_paths.items():
        if value is None or value == "unavailable" or value == []:
            unavailable.append(f"source_artifact_paths.{key}")
    if not positions and _as_dict(contract.get("current_portfolio")).get("positions_count") is None:
        unavailable.append("baseline_positions")
    if not trades:
        unavailable.append("baseline_trades")
    if data_asof is None:
        unavailable.append("pit_controls.data_asof")
    if universe_asof is None:
        unavailable.append("pit_controls.universe_asof")
    if price_asof is None:
        unavailable.append("pit_controls.price_asof")
    for key, value in metrics.items():
        if value is None or value == "unavailable":
            unavailable.append(f"baseline_metrics.{key}")
    return sorted(set(unavailable))


def _positions(contract: Mapping[str, Any]) -> tuple[list[dict[str, Any]], int | None]:
    portfolio = _as_dict(contract.get("current_portfolio"))
    rows: list[dict[str, Any]] = []
    for row in _as_list(portfolio.get("positions")):
        if not isinstance(row, Mapping):
            continue
        rows.append(
            {
                "ticker": _first_present(row.get("ticker"), row.get("symbol")),
                "quantity": _float(_first_present(row.get("quantity"), row.get("qty"), row.get("shares"))),
                "market_value": _float(_first_present(row.get("market_value"), row.get("notional"))),
                "current_weight": _float(_first_present(row.get("current_weight"), row.get("weight"))),
            }
        )
    count = portfolio.get("positions_count")
    if count is None and rows:
        count = len(rows)
    return rows, int(count) if isinstance(count, int) else count


def _trades(contract: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in _as_list(contract.get("sleeve_candidates")):
        if not isinstance(item, Mapping):
            continue
        lifecycle = _as_dict(item.get("lifecycle"))
        precompute = _as_dict(lifecycle.get("precompute"))
        executable = _as_dict(lifecycle.get("executable"))
        intended = _as_dict(lifecycle.get("intended"))
        final = _as_dict(lifecycle.get("final"))
        rows.append(
            {
                "ticker": item.get("ticker"),
                "side": lifecycle.get("side"),
                "sleeve_id": item.get("sleeve_id"),
                "strategy_id": item.get("strategy_id"),
                "rank": item.get("rank"),
                "conviction_score": item.get("conviction_score"),
                "precompute_notional": _float(precompute.get("notional")),
                "executable_notional": _float(executable.get("notional")),
                "intended_notional": _float(intended.get("notional")),
                "submitted": lifecycle.get("submitted"),
                "accepted": lifecycle.get("accepted"),
                "filled": lifecycle.get("filled"),
                "clipped": lifecycle.get("clipped"),
                "submitted_shares": _float(final.get("submitted_shares")),
                "filled_shares": _float(final.get("filled_shares")),
                "delta_notional": _float(item.get("delta_notional")),
                "estimated_unexecuted_notional": _float(
                    _first_present(
                        item.get("estimated_unexecuted_notional"),
                        _as_dict(lifecycle.get("execution_residual")).get("estimated_unexecuted_notional"),
                    )
                ),
                "reason_excluded": item.get("reason_excluded"),
                "decision_stage": lifecycle.get("decision_stage"),
                "decision_reason": lifecycle.get("decision_reason"),
                "source_artifact_path": item.get("source_artifact_path"),
            }
        )
    return rows


def _weights(positions: list[Mapping[str, Any]]) -> list[float]:
    values: list[float] = []
    for row in positions:
        value = _float(row.get("current_weight"))
        if value is None:
            return []
        values.append(abs(value))
    return values


def _portfolio_nav_estimate(positions: list[Mapping[str, Any]]) -> float | None:
    estimates: list[float] = []
    for row in positions:
        market_value = _float(row.get("market_value"))
        weight = _float(row.get("current_weight"))
        if market_value is not None and weight is not None and abs(weight) > 1e-12:
            estimates.append(abs(market_value) / abs(weight))
    if estimates:
        return float(sorted(estimates)[len(estimates) // 2])
    market_values = [_float(row.get("market_value")) for row in positions]
    numeric = [abs(float(value)) for value in market_values if value is not None]
    return float(sum(numeric)) if numeric else None


def _turnover(trades: list[Mapping[str, Any]], positions: list[Mapping[str, Any]]) -> float | None:
    nav = _portfolio_nav_estimate(positions)
    if nav is None or nav <= 0:
        return None
    deltas = [_float(row.get("delta_notional")) for row in trades]
    numeric = [abs(float(value)) for value in deltas if value is not None]
    if not numeric:
        return None
    return 0.5 * float(sum(numeric)) / float(nav)


def _metrics(
    *,
    contract: Mapping[str, Any],
    positions: list[Mapping[str, Any]],
    positions_count: int | None,
    trades: list[Mapping[str, Any]],
) -> dict[str, Any]:
    weights = _weights(positions)
    gross_exposure = float(sum(weights)) if weights or positions_count == 0 else None
    hhi = float(sum(weight * weight for weight in weights)) if weights or positions_count == 0 else None
    residuals = _as_dict(contract.get("execution_residuals"))
    return {
        "position_count": positions_count,
        "gross_exposure": _round(gross_exposure),
        "cash_weight": _round(1.0 - gross_exposure) if gross_exposure is not None else None,
        "max_single_name_weight": _round(max(weights)) if weights else (0.0 if positions_count == 0 else None),
        "HHI": _round(hhi),
        "effective_N": _round(1.0 / hhi) if hhi is not None and hhi > 0 else None,
        "turnover": _round(_turnover(trades, positions)),
        "planned_candidates": residuals.get("planned_candidates"),
        "submitted_orders": residuals.get("submitted_orders"),
        "filled_orders": residuals.get("filled_orders"),
        "suppressed_count": residuals.get("suppressed_count"),
        "clipped_count": residuals.get("clipped_count"),
        "estimated_unexecuted_notional_total": residuals.get("estimated_unexecuted_notional_total"),
    }


def _asof_values(contract: Mapping[str, Any]) -> tuple[Any, Any, Any]:
    metadata = _as_dict(contract.get("metadata"))
    universe = _as_dict(contract.get("universe_snapshot"))
    candidates = [row for row in _as_list(contract.get("sleeve_candidates")) if isinstance(row, Mapping)]
    candidate_asofs = sorted({str(row.get("data_asof")) for row in candidates if row.get("data_asof")})
    data_asof = candidate_asofs[0] if len(candidate_asofs) == 1 else None
    universe_asof = universe.get("asof") if universe.get("status") != "unavailable" else None
    price_asof = metadata.get("price_asof")
    return data_asof, universe_asof, price_asof


def build_fr105_phase1_baseline(
    *,
    repo_root: Path | str,
    input_contract_path: Path | str,
    generated_at: str | None = None,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    contract_path = Path(input_contract_path)
    if not contract_path.is_absolute():
        contract_path = root / contract_path
    contract = read_fr105_replay_contract(contract_path)
    contract_validation = validate_fr105_replay_contract(contract).to_dict()
    metadata = _as_dict(contract.get("metadata"))
    trade_date = str(metadata.get("trade_date") or "unavailable")
    positions, positions_count = _positions(contract)
    trades = _trades(contract)
    metrics = _metrics(contract=contract, positions=positions, positions_count=positions_count, trades=trades)
    source_paths = _source_artifact_paths(contract, contract_path, root)
    data_asof, universe_asof, price_asof = _asof_values(contract)
    unavailable = _unavailable_fields(
        contract=contract,
        source_artifact_paths=source_paths,
        positions=positions,
        trades=trades,
        metrics=metrics,
        data_asof=data_asof,
        universe_asof=universe_asof,
        price_asof=price_asof,
    )
    sparse = not positions and not trades
    baseline: dict[str, Any] = {
        "metadata": {
            "trade_date": trade_date,
            "generated_at": generated_at or "unavailable",
            "git_sha": metadata.get("git_sha") or "unavailable",
            "mode": "research_only",
            "fr_id": FR_ID,
            "phase": "Phase 1",
            "schema_version": PHASE1_SCHEMA_VERSION,
            "contract_id": _contract_id(contract, trade_date),
            "production_execution_modules_invoked": [],
        },
        "input_contract": {
            "path": _relative(contract_path, root),
            "schema_version": metadata.get("schema_version"),
            "contract_id": metadata.get("contract_id"),
            "trade_date": metadata.get("trade_date"),
            "validation_status": contract_validation,
        },
        "pit_controls": {
            "trade_date": trade_date,
            "data_asof": data_asof,
            "universe_asof": universe_asof,
            "price_asof": price_asof,
            "source_artifact_paths": source_paths,
            "no_forward_returns_used": True,
            "no_production_modules_invoked": True,
            "unavailable_fields": unavailable,
        },
        "current_policy_snapshot": {
            "policy_id": "current_policy_baseline",
            "scope": "current_policy_control_only",
            "source": "phase0_replay_contract",
            "status": "SPARSE_INPUT" if sparse else "AVAILABLE_FROM_PHASE0_CONTRACT",
            "constraints_snapshot": contract.get("constraints_snapshot"),
        },
        "replay_window": {
            "start_date": trade_date,
            "end_date": trade_date,
            "frequency": "single_trade_date_snapshot",
            "forward_return_window": None,
            "return_calculation": "not_performed_phase1_current_policy_control",
        },
        "baseline_positions": {
            "source_artifact_path": _as_dict(contract.get("current_portfolio")).get("source_artifact_path"),
            "positions_count": positions_count,
            "positions": positions,
        },
        "baseline_trades": {
            "source_artifact_path": _as_dict(contract.get("source_artifacts")).get("candidate_trade_lifecycle_path"),
            "candidates_count": len(trades) if trades else None,
            "trades": trades,
        },
        "baseline_metrics": metrics,
        "data_quality": {
            "status": "SPARSE" if sparse else "PARTIAL",
            "sparse_artifact_handling": "PASS",
            "missing_source_artifacts": [
                key
                for key, value in source_paths.items()
                if value is None or value == "unavailable" or value == []
            ],
            "unavailable_fields": unavailable,
            "diagnostics": (
                ["phase0_contract_sparse_no_positions_or_trades"]
                if sparse
                else ["phase0_contract_supplied_positions_or_trades"]
            ),
        },
        "validation_status": {
            "status": "UNVALIDATED",
            "findings": [],
            "warnings": [],
        },
    }
    baseline = _clean(baseline)
    baseline["validation_status"] = validate_fr105_phase1_baseline(baseline).to_dict()
    return baseline


def write_fr105_phase1_baseline(
    *,
    repo_root: Path | str,
    trade_date: str,
    run_id: str | None = None,
    input_contract_path: Path | str | None = None,
    output_root: Path | str = DEFAULT_OUTPUT_ROOT,
    generated_at: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    root = Path(repo_root).resolve()
    contract_path = find_phase0_contract_path(
        repo_root=root,
        trade_date=trade_date,
        run_id=run_id,
        input_contract_path=input_contract_path,
        output_root=output_root,
    )
    if contract_path is None:
        raise FileNotFoundError(
            f"Phase 0 replay contract not found for trade_date={trade_date!r}; "
            "run scripts/research/build_fr105_replay_contract.py first or pass --input-contract."
        )
    baseline = build_fr105_phase1_baseline(
        repo_root=root,
        input_contract_path=contract_path,
        generated_at=generated_at,
    )
    out_root = Path(output_root)
    if not out_root.is_absolute():
        out_root = root / out_root
    out_path = out_root / str(baseline["metadata"]["contract_id"]) / ARTIFACT_NAME
    _write_json(out_path, baseline)
    return out_path, baseline


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


def _metric_is_valid(value: Any) -> bool:
    if value is None or value == "unavailable":
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    return False


def validate_fr105_phase1_baseline(baseline: Mapping[str, Any]) -> FR105Phase1ValidationResult:
    findings: list[str] = []
    warnings: list[str] = []

    missing_sections = [key for key in REQUIRED_TOP_LEVEL_SECTIONS if key not in baseline]
    if missing_sections:
        findings.append(f"MISSING_TOP_LEVEL_SECTIONS:{','.join(missing_sections)}")

    metadata = _as_dict(baseline.get("metadata"))
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

    controls = _as_dict(baseline.get("pit_controls"))
    missing_controls = [key for key in REQUIRED_PIT_CONTROL_KEYS if key not in controls]
    if missing_controls:
        findings.append(f"MISSING_PIT_CONTROL_KEYS:{','.join(missing_controls)}")
    if controls.get("no_forward_returns_used") is not True:
        findings.append("FORWARD_RETURNS_USED_OR_UNCONFIRMED")
    if controls.get("no_production_modules_invoked") is not True:
        findings.append("PRODUCTION_MODULE_INVOCATION_FLAG_NOT_TRUE")
    if "source_artifact_paths" in controls and not isinstance(controls.get("source_artifact_paths"), Mapping):
        findings.append("MALFORMED_MAPPING:pit_controls.source_artifact_paths")
    if "unavailable_fields" in controls and not isinstance(controls.get("unavailable_fields"), list):
        findings.append("MALFORMED_LIST:pit_controls.unavailable_fields")

    positions = _as_dict(baseline.get("baseline_positions"))
    if "positions" in positions and not isinstance(positions.get("positions"), list):
        findings.append("MALFORMED_LIST:baseline_positions.positions")
    trades = _as_dict(baseline.get("baseline_trades"))
    if "trades" in trades and not isinstance(trades.get("trades"), list):
        findings.append("MALFORMED_LIST:baseline_trades.trades")

    metrics = _as_dict(baseline.get("baseline_metrics"))
    missing_metrics = [key for key in REQUIRED_BASELINE_METRIC_KEYS if key not in metrics]
    if missing_metrics:
        findings.append(f"MISSING_BASELINE_METRIC_KEYS:{','.join(missing_metrics)}")
    for key in REQUIRED_BASELINE_METRIC_KEYS:
        if key in metrics and not _metric_is_valid(metrics.get(key)):
            findings.append(f"MALFORMED_BASELINE_METRIC:{key}")

    data_quality = _as_dict(baseline.get("data_quality"))
    if data_quality.get("sparse_artifact_handling") != "PASS":
        findings.append("SPARSE_ARTIFACT_HANDLING_NOT_PASS")
    if "unavailable_fields" in data_quality and not isinstance(data_quality.get("unavailable_fields"), list):
        findings.append("MALFORMED_LIST:data_quality.unavailable_fields")

    empty_strings = _empty_string_paths(baseline)
    if empty_strings:
        findings.append(f"EMPTY_STRING_VALUES:{','.join(empty_strings[:10])}")
        if len(empty_strings) > 10:
            warnings.append(f"EMPTY_STRING_VALUES_TRUNCATED:{len(empty_strings)}")

    status = "PASS" if not findings else "FAIL"
    return FR105Phase1ValidationResult(
        status=status,
        findings=sorted(set(findings)),
        warnings=sorted(set(warnings)),
    )

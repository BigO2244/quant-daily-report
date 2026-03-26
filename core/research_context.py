from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from paper.run_manager import safe_write_text

RESEARCH_CONTEXT_ROOT = Path("outputs/research_context")
RESEARCH_CONTEXT_ARTIFACT_TYPE = "caerus_research_context"
RESEARCH_CONTEXT_SCHEMA_VERSION = 1
RUN_RESEARCH_CONTEXT_FILENAME = "research_context.json"
RUNS_ROOT = Path("outputs/runs")


def _read_json(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _coerce_path(value: Any) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    return Path(text)


def _normalize_factor_artifacts(factor_artifacts: dict[str, str] | None) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in dict(factor_artifacts or {}).items():
        name = str(key or "").strip()
        path = str(value or "").strip()
        if not name or not path:
            continue
        normalized[name] = path
    return normalized


def _merge_factor_artifacts_into_payload(
    payload: dict[str, Any],
    factor_artifacts: dict[str, str],
) -> dict[str, Any]:
    merged = dict(payload)
    research_inputs = dict(merged.get("research_inputs") or {})
    existing = _normalize_factor_artifacts(research_inputs.get("factor_artifacts"))
    updated = {**existing, **_normalize_factor_artifacts(factor_artifacts)}
    research_inputs["factor_artifacts"] = updated
    merged["research_inputs"] = research_inputs

    completeness = dict(merged.get("data_completeness") or {})
    completeness["has_factor_artifacts"] = bool(updated)
    merged["data_completeness"] = completeness
    return merged


def build_research_context(
    *,
    trade_date: str,
    run_id: str | None,
    mode: str,
    daily_snapshot: dict[str, Any] | None,
    execution_payload: dict[str, Any] | None,
    paper_summary: dict[str, Any] | None,
    signals_payload: dict[str, Any] | None = None,
    factor_artifacts: dict[str, str] | None = None,
) -> dict[str, Any]:
    snapshot = daily_snapshot or {}
    execution = execution_payload or {}
    summary = paper_summary or {}

    if signals_payload is None:
        signals_path = _coerce_path(snapshot.get("signals_snapshot_path"))
        signals_payload = _read_json(signals_path) or {}
    else:
        signals_path = _coerce_path(snapshot.get("signals_snapshot_path"))

    target_weights = list((signals_payload or {}).get("signals") or [])
    proposed_trades = list(snapshot.get("proposed_trades") or [])
    intended_orders = list(execution.get("trades") or [])
    broker_responses = list(summary.get("broker_responses") or [])

    return {
        "artifact_type": RESEARCH_CONTEXT_ARTIFACT_TYPE,
        "schema_version": RESEARCH_CONTEXT_SCHEMA_VERSION,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "trade_date": str(trade_date),
        "run_id": str(run_id or ""),
        "mode": str(mode).upper(),
        "market_context": {
            "regime_summary": snapshot.get("regime_summary") or {},
            "breaker": snapshot.get("breaker") or {},
            "market_analyzer": snapshot.get("market_analyzer") or {},
        },
        "allocation_context": {
            "target_cash_weight": snapshot.get("target_cash_weight"),
            "cash_target_weight_from_signals": (signals_payload or {}).get("cash_target_weight"),
            "sleeve_allocations": snapshot.get("sleeve_allocations") or {},
            "allocations": snapshot.get("allocations") or {},
            "performance_summary": snapshot.get("performance_summary") or {},
            "allocation_diagnostics": snapshot.get("allocation_diagnostics") or {},
        },
        "selection_context": {
            "target_weights": target_weights,
            "holdings": list(snapshot.get("holdings") or []),
            "risk_levels": list(snapshot.get("risk_levels") or []),
            "proposed_trades": proposed_trades,
        },
        "execution_context": {
            "execution_status": execution.get("execution_status"),
            "status_label": execution.get("status_label"),
            "halt_reason": execution.get("halt_reason"),
            "submitted_count": execution.get("submitted_count"),
            "accepted_count": execution.get("accepted_count"),
            "rejected_count": execution.get("rejected_count"),
            "intended_orders": intended_orders,
            "realized_broker_responses": broker_responses,
            "market_status": summary.get("market_status"),
            "broker_recon_status": summary.get("broker_recon_status"),
            "posttrade_recon_status": summary.get("posttrade_recon_status"),
        },
        "research_inputs": {
            "signals_snapshot_path": str(signals_path) if signals_path else None,
            "alpha_attribution": snapshot.get("alpha_attribution") or {},
            "inception_metrics": snapshot.get("inception_metrics") or {},
            "factor_artifacts": factor_artifacts or {},
        },
        "data_completeness": {
            "has_regime_summary": bool(snapshot.get("regime_summary")),
            "has_target_weights": bool(target_weights),
            "has_proposed_trades": bool(proposed_trades),
            "has_intended_orders": bool(intended_orders),
            "has_realized_broker_responses": bool(broker_responses),
            "has_factor_artifacts": bool(factor_artifacts),
        },
    }


def write_research_context(
    *,
    trade_date: str,
    run_id: str | None,
    mode: str,
    daily_snapshot: dict[str, Any] | None,
    execution_payload: dict[str, Any] | None,
    paper_summary: dict[str, Any] | None,
    run_root: Path | None = None,
    allow_overwrite: bool = True,
    factor_artifacts: dict[str, str] | None = None,
    output_root: Path = RESEARCH_CONTEXT_ROOT,
) -> dict[str, str]:
    payload = build_research_context(
        trade_date=trade_date,
        run_id=run_id,
        mode=mode,
        daily_snapshot=daily_snapshot,
        execution_payload=execution_payload,
        paper_summary=paper_summary,
        factor_artifacts=factor_artifacts,
    )

    output_root.mkdir(parents=True, exist_ok=True)
    canonical_path = output_root / f"{trade_date}.json"
    safe_write_text(
        canonical_path,
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        allow_overwrite=allow_overwrite,
    )

    result = {"canonical_path": str(canonical_path)}
    if run_root is not None:
        run_path = Path(run_root) / "research" / RUN_RESEARCH_CONTEXT_FILENAME
        run_path.parent.mkdir(parents=True, exist_ok=True)
        safe_write_text(
            run_path,
            json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
            allow_overwrite=allow_overwrite,
        )
        result["run_path"] = str(run_path)
    return result


def register_factor_artifacts(
    *,
    trade_date: str,
    factor_artifacts: dict[str, str],
    output_root: Path = RESEARCH_CONTEXT_ROOT,
    runs_root: Path = RUNS_ROOT,
    allow_overwrite: bool = True,
) -> dict[str, str]:
    artifacts = _normalize_factor_artifacts(factor_artifacts)
    if not artifacts:
        return {}

    canonical_path = Path(output_root) / f"{trade_date}.json"
    canonical_payload = _read_json(canonical_path)
    if canonical_payload is None:
        return {}

    canonical_payload = _merge_factor_artifacts_into_payload(canonical_payload, artifacts)
    safe_write_text(
        canonical_path,
        json.dumps(canonical_payload, indent=2, sort_keys=True, default=str) + "\n",
        allow_overwrite=allow_overwrite,
    )

    result = {"canonical_path": str(canonical_path)}
    run_id = str(canonical_payload.get("run_id") or "").strip()
    if run_id:
        run_path = Path(runs_root) / run_id / "research" / RUN_RESEARCH_CONTEXT_FILENAME
        run_payload = _read_json(run_path) or canonical_payload
        run_payload = _merge_factor_artifacts_into_payload(run_payload, artifacts)
        run_path.parent.mkdir(parents=True, exist_ok=True)
        safe_write_text(
            run_path,
            json.dumps(run_payload, indent=2, sort_keys=True, default=str) + "\n",
            allow_overwrite=allow_overwrite,
        )
        result["run_path"] = str(run_path)
    return result

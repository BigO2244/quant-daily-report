from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Mapping

from paper.run_manager import safe_write_text


TIMELINE_SCHEMA_VERSION = "execution_lifecycle_timeline.v1"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}
    return {}


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    try:
        if not path.exists():
            return []
        with path.open(encoding="utf-8") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    except Exception:
        return []


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _text(value: Any) -> str:
    return str(value or "").strip()


def _first_nonempty(*values: Any) -> str | None:
    for value in values:
        text = _text(value)
        if text:
            return text
    return None


def _artifact_entry(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "present": bool(path.exists()),
    }


def _safe_relative(path: Path) -> str:
    return path.as_posix()


def _candidate_json(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    return _read_json(Path(path))


def _count_side(orders: list[Mapping[str, Any]], side: str) -> int:
    expected = side.upper()
    return sum(1 for order in orders if _text(order.get("side")).upper() == expected)


def _first_submitted_at(orders: list[Mapping[str, Any]], side: str | None = None) -> str | None:
    expected = side.upper() if side else None
    values = []
    for order in orders:
        if expected and _text(order.get("side")).upper() != expected:
            continue
        submitted_at = _first_nonempty(order.get("submitted_at"), order.get("created_at"))
        if submitted_at:
            values.append(submitted_at)
    return sorted(values)[0] if values else None


def _event(
    *,
    sequence: int,
    checkpoint: str,
    status: str,
    summary: str,
    timestamp: str | None = None,
    source_artifacts: list[str] | None = None,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "sequence": int(sequence),
        "checkpoint": checkpoint,
        "status": status,
        "timestamp": timestamp,
        "summary": summary,
        "source_artifacts": list(source_artifacts or []),
        "details": dict(details or {}),
    }


def build_execution_lifecycle_timeline(
    *,
    run_root: str | Path,
    trade_date: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    root = Path(run_root)
    operator_summary_path = root / "operator_summary.json"
    execution_payload_path = root / "execution_payload.json"
    execution_results_path = root / "execution_results.json"
    equality_gate_path = root / "equality_gate.json"
    integrity_path = root / "audit" / "execution_integrity.json"
    trading_summary_path = root / "trading_day_summary.json"

    operator_summary = _read_json(operator_summary_path)
    execution_payload = _read_json(execution_payload_path)
    execution_results = _read_json(execution_results_path)
    equality_gate = _read_json(equality_gate_path)
    integrity = _read_json(integrity_path)
    trading_summary = _read_json(trading_summary_path)

    resolved_trade_date = (
        _first_nonempty(
            trade_date,
            execution_payload.get("trade_date"),
            execution_results.get("trade_date"),
            operator_summary.get("trade_date"),
            trading_summary.get("trade_date"),
        )
        or ""
    )
    resolved_run_id = (
        _first_nonempty(
            run_id,
            execution_payload.get("run_id"),
            execution_results.get("run_id"),
            operator_summary.get("run_id"),
            trading_summary.get("run_id"),
            root.name,
        )
        or ""
    )

    precompute_contract_path = Path("outputs/precompute") / resolved_trade_date / "contract.json"
    workflow_dir = Path("outputs/workflow") / resolved_trade_date
    execution_bundle_validation_path = workflow_dir / "execution_bundle_validation.json"
    precompute_bundle_validation_path = workflow_dir / "precompute_bundle_validation.json"
    broker_orders_path = root / "broker" / f"orders_{resolved_trade_date}.csv"
    recon_path = Path(
        _first_nonempty(operator_summary.get("post_execution_recon_path"))
        or root / "broker" / f"recon_posttrade_{resolved_trade_date}.json"
    )

    precompute_contract = _read_json(precompute_contract_path)
    execution_bundle_validation = _read_json(execution_bundle_validation_path)
    precompute_bundle_validation = _read_json(precompute_bundle_validation_path)
    recon = _candidate_json(recon_path)
    broker_orders = _read_csv_rows(broker_orders_path)
    broker_responses = [
        dict(response)
        for response in _as_list(execution_results.get("broker_responses"))
        if isinstance(response, Mapping)
    ]
    order_events: list[Mapping[str, Any]] = broker_responses or broker_orders

    submitted_count = _to_int(
        execution_results.get("submitted_count")
        or execution_payload.get("submitted_count")
        or operator_summary.get("submitted_count")
    )
    submitted_buy_count = _to_int(
        execution_payload.get("submitted_buy_count")
        or operator_summary.get("submitted_buy_count")
    ) or _count_side(order_events, "BUY")
    submitted_sell_count = _to_int(
        execution_payload.get("submitted_sell_count")
        or operator_summary.get("submitted_sell_count")
    ) or _count_side(order_events, "SELL")

    events = [
        _event(
            sequence=1,
            checkpoint="precompute_started",
            status="OBSERVED" if precompute_contract else "UNAVAILABLE",
            timestamp=_first_nonempty(precompute_contract.get("created_at")),
            summary=(
                "Precompute bundle contract is present"
                if precompute_contract
                else "Precompute bundle contract was not available to the timeline builder"
            ),
            source_artifacts=[_safe_relative(precompute_contract_path)],
            details={
                "workflow_stage": precompute_contract.get("workflow_stage"),
                "source_run_id": precompute_contract.get("source_run_id"),
            },
        ),
        _event(
            sequence=2,
            checkpoint="precompute_completed",
            status="OK"
            if str(precompute_contract.get("status") or "").lower() == "complete"
            else ("UNAVAILABLE" if not precompute_contract else "WARN"),
            timestamp=_first_nonempty(precompute_contract.get("created_at")),
            summary=f"Precompute status: {_text(precompute_contract.get('status')) or 'unavailable'}",
            source_artifacts=[_safe_relative(precompute_contract_path)],
            details={
                "validated_for_execution": precompute_contract.get("validated_for_execution"),
                "validation_reason": precompute_contract.get("validation_reason"),
                "summary": precompute_contract.get("summary"),
            },
        ),
        _event(
            sequence=3,
            checkpoint="bundle_validation",
            status=_first_nonempty(
                execution_bundle_validation.get("status"),
                precompute_bundle_validation.get("status"),
                execution_payload.get("bundle_status"),
                operator_summary.get("bundle_status"),
                "UNAVAILABLE",
            )
            or "UNAVAILABLE",
            timestamp=_first_nonempty(
                execution_bundle_validation.get("validated_at"),
                precompute_bundle_validation.get("validated_at"),
            ),
            summary="Execution bundle validation state",
            source_artifacts=[
                _safe_relative(execution_bundle_validation_path),
                _safe_relative(precompute_bundle_validation_path),
            ],
            details={
                "execution_bundle_validation_present": bool(execution_bundle_validation),
                "precompute_bundle_validation_present": bool(precompute_bundle_validation),
                "bundle_reason": _first_nonempty(
                    execution_bundle_validation.get("reason"),
                    precompute_bundle_validation.get("reason"),
                    execution_payload.get("bundle_reason"),
                ),
            },
        ),
        _event(
            sequence=4,
            checkpoint="execution_source_selection",
            status="OK" if execution_payload.get("execution_source") else "UNKNOWN",
            timestamp=_first_nonempty(operator_summary.get("actual_execution_start_et")),
            summary=(
                f"Execution source selected: {execution_payload.get('execution_source')}"
                if execution_payload.get("execution_source")
                else "Execution source was not recorded"
            ),
            source_artifacts=[_safe_relative(execution_payload_path)],
            details={
                "execution_source": execution_payload.get("execution_source"),
                "planning_price_basis": execution_payload.get("planning_price_basis"),
                "pricing_asof": execution_payload.get("pricing_asof"),
                "execution_price_requirement": execution_payload.get("execution_price_requirement"),
                "price_freshness_scope": execution_payload.get("price_freshness_scope"),
            },
        ),
        _event(
            sequence=5,
            checkpoint="freshness_scope",
            status="OK"
            if execution_payload.get("price_freshness_scope")
            or execution_payload.get("execution_price_requirement")
            else "UNKNOWN",
            timestamp=_first_nonempty(operator_summary.get("actual_execution_start_et")),
            summary=(
                f"Freshness scope: {execution_payload.get('price_freshness_scope') or 'unknown'}"
            ),
            source_artifacts=[_safe_relative(execution_payload_path)],
            details={
                "execution_price_requirement": execution_payload.get("execution_price_requirement"),
                "price_freshness_scope": execution_payload.get("price_freshness_scope"),
            },
        ),
        _event(
            sequence=6,
            checkpoint="sell_phase_start",
            status="OBSERVED" if submitted_sell_count > 0 else "SKIPPED",
            timestamp=_first_submitted_at(order_events, "SELL"),
            summary=f"Submitted SELL count: {submitted_sell_count}",
            source_artifacts=[_safe_relative(broker_orders_path), _safe_relative(execution_results_path)],
            details={"submitted_sell_count": submitted_sell_count},
        ),
        _event(
            sequence=7,
            checkpoint="sell_phase_completion",
            status=_first_nonempty(execution_payload.get("sell_phase_status"), "UNKNOWN") or "UNKNOWN",
            timestamp=None,
            summary=(
                f"Sell phase status: {_text(execution_payload.get('sell_phase_status')) or 'unknown'}"
            ),
            source_artifacts=[_safe_relative(execution_payload_path)],
            details={
                "sell_phase_status": execution_payload.get("sell_phase_status"),
                "sell_phase_completion_reason": execution_payload.get("sell_phase_completion_reason"),
            },
        ),
        _event(
            sequence=8,
            checkpoint="buy_phase_start",
            status="OBSERVED" if submitted_buy_count > 0 else "SKIPPED",
            timestamp=_first_submitted_at(order_events, "BUY"),
            summary=f"Submitted BUY count: {submitted_buy_count}",
            source_artifacts=[_safe_relative(broker_orders_path), _safe_relative(execution_results_path)],
            details={
                "buy_phase_planned": execution_payload.get("buy_phase_planned"),
                "submitted_buy_count": submitted_buy_count,
                "buy_phase_block_reason": execution_payload.get("buy_phase_block_reason"),
            },
        ),
        _event(
            sequence=9,
            checkpoint="buy_phase_completion",
            status="OK"
            if submitted_buy_count > 0 and not execution_payload.get("buy_phase_block_reason")
            else ("WARN" if execution_payload.get("buy_phase_block_reason") else "SKIPPED"),
            timestamp=None,
            summary=(
                f"Buy phase submitted {submitted_buy_count}; "
                f"block_reason={_text(execution_payload.get('buy_phase_block_reason')) or 'none'}"
            ),
            source_artifacts=[_safe_relative(execution_payload_path)],
            details={
                "buy_phase_planned": execution_payload.get("buy_phase_planned"),
                "buy_phase_submitted": execution_payload.get("buy_phase_submitted"),
                "submitted_buy_count": submitted_buy_count,
                "pending_buy_count": execution_payload.get("pending_buy_count"),
                "buy_phase_block_reason": execution_payload.get("buy_phase_block_reason"),
            },
        ),
        _event(
            sequence=10,
            checkpoint="first_order_submitted",
            status="OBSERVED" if submitted_count > 0 else "SKIPPED",
            timestamp=_first_nonempty(
                operator_summary.get("first_submit_et"),
                _first_submitted_at(order_events),
            ),
            summary=f"First order submission observed; submitted_count={submitted_count}",
            source_artifacts=[_safe_relative(execution_results_path), _safe_relative(broker_orders_path)],
            details={"submitted_count": submitted_count},
        ),
        _event(
            sequence=11,
            checkpoint="execution_completed",
            status=_first_nonempty(
                execution_results.get("status"),
                execution_payload.get("execution_status"),
                operator_summary.get("operator_execution_status"),
                "UNKNOWN",
            )
            or "UNKNOWN",
            timestamp=None,
            summary=(
                f"Execution completed with status "
                f"{_first_nonempty(execution_results.get('status'), execution_payload.get('execution_status'), 'UNKNOWN')}"
            ),
            source_artifacts=[_safe_relative(execution_results_path), _safe_relative(operator_summary_path)],
            details={
                "operator_execution_status": operator_summary.get("operator_execution_status"),
                "submitted_count": submitted_count,
                "accepted_count": _to_int(execution_results.get("accepted_count") or operator_summary.get("accepted_count")),
                "rejected_count": _to_int(execution_results.get("rejected_count") or operator_summary.get("rejected_count")),
                "execution_outcome": execution_results.get("execution_outcome") or operator_summary.get("execution_outcome"),
                "execution_reason": execution_results.get("execution_reason") or operator_summary.get("execution_reason"),
            },
        ),
        _event(
            sequence=12,
            checkpoint="reconciliation_state",
            status=_first_nonempty(
                operator_summary.get("post_execution_recon_status"),
                recon.get("drift_status"),
                recon.get("verdict"),
                "UNAVAILABLE",
            )
            or "UNAVAILABLE",
            timestamp=_first_nonempty(recon.get("generated_at"), recon.get("created_at")),
            summary="Post-execution reconciliation state",
            source_artifacts=[_safe_relative(recon_path)],
            details={
                "post_execution_recon_status": operator_summary.get("post_execution_recon_status"),
                "drift_status": recon.get("drift_status"),
                "verdict": recon.get("verdict"),
                "affected_symbols": recon.get("affected_symbols"),
                "repair_suggestions": recon.get("repair_suggestions"),
            },
        ),
        _event(
            sequence=13,
            checkpoint="integrity_findings",
            status=_first_nonempty(integrity.get("status"), "UNAVAILABLE") or "UNAVAILABLE",
            timestamp=None,
            summary=(
                f"Execution integrity status: {_first_nonempty(integrity.get('status'), 'unavailable')}"
            ),
            source_artifacts=[_safe_relative(integrity_path)],
            details={
                "finding_codes": [
                    _text(finding.get("code"))
                    for finding in _as_list(integrity.get("findings"))
                    if isinstance(finding, Mapping) and _text(finding.get("code"))
                ],
                "finding_count": len(_as_list(integrity.get("findings"))),
            },
        ),
        _event(
            sequence=14,
            checkpoint="retry_continuation_state",
            status="OBSERVED"
            if _to_int(operator_summary.get("retry_attempt_count"))
            or operator_summary.get("continuation_mode")
            or operator_summary.get("continuation_eligible")
            else "NONE",
            timestamp=None,
            summary="Retry and continuation state",
            source_artifacts=[_safe_relative(operator_summary_path), _safe_relative(execution_payload_path)],
            details={
                "retry_attempt_count": operator_summary.get("retry_attempt_count"),
                "retry_eligible": operator_summary.get("retry_eligible"),
                "retry_reason": operator_summary.get("retry_reason"),
                "continuation_eligible": operator_summary.get("continuation_eligible"),
                "continuation_reason": operator_summary.get("continuation_reason"),
                "continuation_mode": operator_summary.get("continuation_mode"),
                "continuation_source": operator_summary.get("continuation_source"),
            },
        ),
        _event(
            sequence=15,
            checkpoint="terminal_status",
            status=_first_nonempty(operator_summary.get("terminal_status"), "UNKNOWN") or "UNKNOWN",
            timestamp=None,
            summary=f"Terminal status: {_first_nonempty(operator_summary.get('terminal_status'), 'unknown')}",
            source_artifacts=[_safe_relative(operator_summary_path)],
            details={
                "terminal_status": operator_summary.get("terminal_status"),
                "pretrade_halt_reason": operator_summary.get("pretrade_halt_reason"),
                "exception_type": operator_summary.get("exception_type"),
                "exception_message": operator_summary.get("exception_message"),
            },
        ),
    ]

    if equality_gate:
        decision = _text(equality_gate.get("decision"))
        if decision == "WOULD_PROCEED":
            gate_status = "OK"
        elif decision in {
            "WOULD_HALT_HASH_MISMATCH",
            "WOULD_HALT_SOURCE_MISMATCH",
            "WOULD_HALT_PRICING_ASOF_MISMATCH",
            "OBSERVE_ERROR",
        }:
            gate_status = "WARN"
        else:
            gate_status = "UNKNOWN"
        equality_event = _event(
            sequence=6,
            checkpoint="equality_gate_observe",
            status=gate_status,
            timestamp=_first_nonempty(equality_gate.get("timestamp_utc")),
            summary=f"Equality gate observe decision: {decision or 'unknown'}; submission unaffected",
            source_artifacts=[_safe_relative(equality_gate_path)],
            details={
                "decision": equality_gate.get("decision"),
                "would_block": equality_gate.get("would_block"),
                "hashes_equal": equality_gate.get("hashes_equal"),
                "pricing_asof_match": equality_gate.get("pricing_asof_match"),
                "execution_source": equality_gate.get("execution_source"),
                "artifact_ref": _safe_relative(equality_gate_path),
                "note": "observe-only; submission unaffected",
            },
        )
        adjusted_events: list[dict[str, Any]] = []
        inserted = False
        for event in events:
            if not inserted and _to_int(event.get("sequence")) >= 6:
                adjusted_events.append(equality_event)
                inserted = True
            if _to_int(event.get("sequence")) >= 6:
                event = dict(event)
                event["sequence"] = _to_int(event.get("sequence")) + 1
            adjusted_events.append(event)
        if not inserted:
            adjusted_events.append(equality_event)
        events = adjusted_events

    return {
        "schema_version": TIMELINE_SCHEMA_VERSION,
        "run_id": resolved_run_id,
        "trade_date": resolved_trade_date,
        "run_root": str(root),
        "provenance": {
            "execution_source": execution_payload.get("execution_source"),
            "planning_price_basis": execution_payload.get("planning_price_basis"),
            "pricing_asof": execution_payload.get("pricing_asof"),
            "execution_price_requirement": execution_payload.get("execution_price_requirement"),
            "price_freshness_scope": execution_payload.get("price_freshness_scope"),
        },
        "source_artifacts": {
            "operator_summary": _artifact_entry(operator_summary_path),
            "execution_payload": _artifact_entry(execution_payload_path),
            "execution_results": _artifact_entry(execution_results_path),
            "equality_gate": _artifact_entry(equality_gate_path),
            "execution_integrity": _artifact_entry(integrity_path),
            "trading_day_summary": _artifact_entry(trading_summary_path),
            "precompute_contract": _artifact_entry(precompute_contract_path),
            "execution_bundle_validation": _artifact_entry(execution_bundle_validation_path),
            "precompute_bundle_validation": _artifact_entry(precompute_bundle_validation_path),
            "broker_orders": _artifact_entry(broker_orders_path),
            "posttrade_reconciliation": _artifact_entry(recon_path),
        },
        "event_count": len(events),
        "events": events,
    }


def render_execution_lifecycle_timeline_markdown(timeline: Mapping[str, Any]) -> str:
    provenance = dict(timeline.get("provenance") or {})
    lines = [
        "# Execution Lifecycle Timeline",
        "",
        f"- run_id: `{timeline.get('run_id') or ''}`",
        f"- trade_date: `{timeline.get('trade_date') or ''}`",
        f"- execution_source: `{provenance.get('execution_source') or ''}`",
        f"- planning_price_basis: `{provenance.get('planning_price_basis') or ''}`",
        f"- pricing_asof: `{provenance.get('pricing_asof') or ''}`",
        f"- execution_price_requirement: `{provenance.get('execution_price_requirement') or ''}`",
        f"- price_freshness_scope: `{provenance.get('price_freshness_scope') or ''}`",
        "",
        "| # | Checkpoint | Status | Timestamp | Summary |",
        "|---:|---|---|---|---|",
    ]
    for event in _as_list(timeline.get("events")):
        if not isinstance(event, Mapping):
            continue
        lines.append(
            "| {sequence} | `{checkpoint}` | `{status}` | {timestamp} | {summary} |".format(
                sequence=event.get("sequence"),
                checkpoint=event.get("checkpoint") or "",
                status=event.get("status") or "",
                timestamp=event.get("timestamp") or "",
                summary=str(event.get("summary") or "").replace("|", "\\|"),
            )
        )
    lines.append("")
    return "\n".join(lines)


def write_execution_lifecycle_timeline(
    *,
    run_root: str | Path,
    trade_date: str | None = None,
    run_id: str | None = None,
) -> tuple[Path, Path]:
    root = Path(run_root)
    timeline = build_execution_lifecycle_timeline(
        run_root=root,
        trade_date=trade_date,
        run_id=run_id,
    )
    json_path = root / "execution_timeline.json"
    md_path = root / "execution_timeline.md"
    safe_write_text(
        json_path,
        json.dumps(timeline, indent=2, sort_keys=True, default=str) + "\n",
        allow_overwrite=True,
    )
    safe_write_text(
        md_path,
        render_execution_lifecycle_timeline_markdown(timeline),
        allow_overwrite=True,
    )
    return json_path, md_path

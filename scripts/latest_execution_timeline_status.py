#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.run_pointer import read_latest_run_pointer
from core.execution_equality_gate import classify_equality_gate_observe_status


def _read_json(path: Path) -> tuple[dict[str, Any], str | None]:
    if not path.exists():
        return {}, "MISSING_FILE"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, f"UNREADABLE_JSON:{type(exc).__name__}"
    if not isinstance(payload, dict):
        return {}, "JSON_NOT_OBJECT"
    return payload, None


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _text(value: Any) -> str:
    return str(value or "").strip()


def _resolve_run_root(repo_root: Path, latest: Mapping[str, Any]) -> Path:
    raw = _text(latest.get("run_root") or latest.get("path"))
    if not raw:
        raw = str(repo_root / "outputs" / "runs" / _text(latest.get("run_id")))
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = repo_root / path
    return path


def _path_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "present": bool(path.exists()),
    }


def build_latest_execution_timeline_status(repo_root: str | Path = _REPO_ROOT) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    latest_path = root / "outputs" / "latest_run.json"
    latest = read_latest_run_pointer(str(root))
    if not latest:
        return {
            "status": "NEEDS_OPERATOR",
            "reason": "latest_run_missing",
            "message": "outputs/latest_run.json is missing or does not identify a run.",
            "paths": {"latest_run": _path_record(latest_path)},
        }

    run_root = _resolve_run_root(root, latest)
    operator_summary_path = run_root / "operator_summary.json"
    execution_payload_path = run_root / "execution_payload.json"
    execution_results_path = run_root / "execution_results.json"
    equality_gate_path = run_root / "equality_gate.json"
    timeline_json_path = run_root / "execution_timeline.json"
    timeline_md_path = run_root / "execution_timeline.md"
    integrity_path = run_root / "audit" / "execution_integrity.json"

    operator_summary, operator_error = _read_json(operator_summary_path)
    execution_payload, payload_error = _read_json(execution_payload_path)
    execution_results, results_error = _read_json(execution_results_path)
    equality_gate, equality_gate_error = _read_json(equality_gate_path)
    timeline, timeline_error = _read_json(timeline_json_path)
    integrity, integrity_error = _read_json(integrity_path)
    lifecycle_artifact = (
        execution_results.get("candidate_trade_lifecycle_artifact")
        or execution_payload.get("candidate_trade_lifecycle_artifact")
    )
    lifecycle_trade_date = _text(
        latest.get("trade_date")
        or execution_payload.get("trade_date")
        or execution_results.get("trade_date")
        or run_root.name[:10]
    )
    lifecycle_path = Path(str(lifecycle_artifact)) if lifecycle_artifact else (
        run_root / "audit" / f"candidate_trade_lifecycle_{lifecycle_trade_date}.json"
    )
    warnings = []
    lifecycle_summary = (
        execution_results.get("candidate_trade_lifecycle_summary")
        or execution_payload.get("candidate_trade_lifecycle_summary")
        or {}
    )
    if not lifecycle_summary and lifecycle_path.exists():
        lifecycle_payload, lifecycle_error = _read_json(lifecycle_path)
        if lifecycle_error:
            warnings.append(f"candidate_trade_lifecycle:{lifecycle_error}")
        lifecycle_summary = dict(lifecycle_payload.get("counts") or {}) if lifecycle_payload else {}
    else:
        lifecycle_error = None

    provenance = dict(timeline.get("provenance") or {})
    if not provenance:
        provenance = {
            "execution_source": execution_payload.get("execution_source"),
            "planning_price_basis": execution_payload.get("planning_price_basis"),
            "pricing_asof": execution_payload.get("pricing_asof"),
            "execution_price_requirement": execution_payload.get("execution_price_requirement"),
            "price_freshness_scope": execution_payload.get("price_freshness_scope"),
        }

    finding_codes = [
        _text(finding.get("code"))
        for finding in _as_list(integrity.get("findings"))
        if isinstance(finding, Mapping) and _text(finding.get("code"))
    ]
    if timeline_error:
        warnings.append(f"execution_timeline_json:{timeline_error}")
    if operator_error:
        warnings.append(f"operator_summary:{operator_error}")
    if payload_error:
        warnings.append(f"execution_payload:{payload_error}")
    if results_error:
        warnings.append(f"execution_results:{results_error}")
    if equality_gate_error and equality_gate_error != "MISSING_FILE":
        warnings.append(f"equality_gate:{equality_gate_error}")
    if integrity_error:
        warnings.append(f"execution_integrity:{integrity_error}")

    equality_record: Mapping[str, Any] = equality_gate
    if not equality_record:
        candidate = operator_summary.get("equality_gate_observe")
        equality_record = candidate if isinstance(candidate, Mapping) else {}
    equality_status = classify_equality_gate_observe_status(equality_record)
    equality_artifact_ref = (
        equality_record.get("artifact_ref")
        if isinstance(equality_record, Mapping) and equality_record.get("artifact_ref")
        else str(equality_gate_path)
    )

    status = "OK" if not timeline_error else "NEEDS_OPERATOR"
    return {
        "status": status,
        "reason": "" if status == "OK" else "execution_timeline_missing_or_unreadable",
        "run_id": _text(latest.get("run_id") or timeline.get("run_id") or execution_payload.get("run_id")),
        "trade_date": _text(latest.get("trade_date") or timeline.get("trade_date") or execution_payload.get("trade_date")),
        "latest_run_status": latest.get("status"),
        "terminal_status": operator_summary.get("terminal_status"),
        "operator_execution_status": operator_summary.get("operator_execution_status"),
        "execution_source": provenance.get("execution_source"),
        "planning_price_basis": provenance.get("planning_price_basis"),
        "pricing_asof": provenance.get("pricing_asof"),
        "execution_price_requirement": provenance.get("execution_price_requirement"),
        "price_freshness_scope": provenance.get("price_freshness_scope"),
        "submitted_count": _to_int(execution_results.get("submitted_count") or execution_payload.get("submitted_count") or operator_summary.get("submitted_count")),
        "accepted_count": _to_int(execution_results.get("accepted_count") or execution_payload.get("accepted_count") or operator_summary.get("accepted_count")),
        "rejected_count": _to_int(execution_results.get("rejected_count") or execution_payload.get("rejected_count") or operator_summary.get("rejected_count")),
        "planned_payload_trade_count": _to_int(execution_results.get("planned_payload_trade_count") or execution_payload.get("planned_payload_trade_count") or lifecycle_summary.get("precompute_candidates")),
        "executable_filter_passed_count": _to_int(execution_results.get("executable_filter_passed_count") or execution_payload.get("executable_filter_passed_count") or lifecycle_summary.get("passed_executable_filter")),
        "executable_trades_count": _to_int(execution_results.get("executable_trades_count") or execution_payload.get("execution_eligible_trades_count") or execution_payload.get("executable_trades_count")),
        "final_executable_trades_count": _to_int(execution_results.get("final_executable_trades_count") or execution_results.get("executable_trades_count") or execution_payload.get("execution_eligible_trades_count") or execution_payload.get("executable_trades_count")),
        "intended_orders_count": _to_int(execution_results.get("intended_orders_count") or execution_payload.get("intended_orders_count") or lifecycle_summary.get("intended_orders")),
        "filled_count": _to_int(execution_results.get("orders_filled_count") or execution_payload.get("orders_filled_count") or lifecycle_summary.get("filled")),
        "candidate_trade_lifecycle_artifact": str(lifecycle_path),
        "candidate_trade_lifecycle_present": bool(lifecycle_path.exists()),
        "candidate_trade_lifecycle_summary": lifecycle_summary,
        "candidate_trade_lifecycle_reasons": lifecycle_summary.get("suppression_reason_counts") or {},
        "candidate_trade_clipping_reasons": lifecycle_summary.get("clipping_reason_counts") or {},
        "asset_validation_status": execution_payload.get("asset_validation_status") or execution_results.get("asset_validation_status"),
        "invalid_asset_count": _to_int(execution_payload.get("invalid_asset_count") or execution_results.get("invalid_asset_count")),
        "invalid_symbols": list(execution_payload.get("invalid_symbols") or execution_results.get("invalid_symbols") or []),
        "buy_phase_decision_reason": execution_payload.get("buy_phase_decision_reason") or execution_results.get("buy_phase_decision_reason"),
        "pending_sell_count_at_buy_decision": execution_payload.get("pending_sell_count_at_buy_decision") or execution_results.get("pending_sell_count_at_buy_decision"),
        "buying_power_at_buy_decision": execution_payload.get("buying_power_at_buy_decision") or execution_results.get("buying_power_at_buy_decision"),
        "cash_at_buy_decision": execution_payload.get("cash_at_buy_decision") or execution_results.get("cash_at_buy_decision"),
        "execution_integrity_status": integrity.get("status") or operator_summary.get("execution_integrity_status"),
        "findings": finding_codes or list(operator_summary.get("execution_integrity_findings") or []),
        "timeline_event_count": timeline.get("event_count"),
        "equality_gate_observe_status": equality_status,
        "equality_gate_decision": equality_record.get("decision") if isinstance(equality_record, Mapping) else None,
        "equality_gate_would_block": equality_record.get("would_block") if isinstance(equality_record, Mapping) else None,
        "equality_gate_hashes_equal": equality_record.get("hashes_equal") if isinstance(equality_record, Mapping) else None,
        "equality_gate_pricing_asof_match": equality_record.get("pricing_asof_match") if isinstance(equality_record, Mapping) else None,
        "equality_gate_artifact_ref": equality_artifact_ref,
        "warnings": warnings,
        "paths": {
            "latest_run": _path_record(latest_path),
            "operator_summary": _path_record(operator_summary_path),
            "execution_payload": _path_record(execution_payload_path),
            "execution_results": _path_record(execution_results_path),
            "equality_gate": _path_record(equality_gate_path),
            "execution_timeline_json": _path_record(timeline_json_path),
            "execution_timeline_md": _path_record(timeline_md_path),
            "execution_integrity": _path_record(integrity_path),
            "candidate_trade_lifecycle": _path_record(lifecycle_path),
        },
    }


def render_text(payload: Mapping[str, Any]) -> str:
    lines = [
        "Latest Execution Timeline Status",
        f"status: {payload.get('status')}",
    ]
    if payload.get("reason"):
        lines.append(f"reason: {payload.get('reason')}")
    for key in (
        "run_id",
        "trade_date",
        "latest_run_status",
        "terminal_status",
        "operator_execution_status",
        "execution_source",
        "planning_price_basis",
        "pricing_asof",
        "execution_price_requirement",
        "price_freshness_scope",
        "submitted_count",
        "accepted_count",
        "rejected_count",
        "planned_payload_trade_count",
        "executable_filter_passed_count",
        "executable_trades_count",
        "final_executable_trades_count",
        "intended_orders_count",
        "filled_count",
        "candidate_trade_lifecycle_artifact",
        "candidate_trade_lifecycle_present",
        "candidate_trade_lifecycle_reasons",
        "candidate_trade_clipping_reasons",
        "asset_validation_status",
        "invalid_asset_count",
        "invalid_symbols",
        "buy_phase_decision_reason",
        "pending_sell_count_at_buy_decision",
        "buying_power_at_buy_decision",
        "cash_at_buy_decision",
        "execution_integrity_status",
        "equality_gate_observe_status",
        "equality_gate_decision",
        "equality_gate_would_block",
        "equality_gate_hashes_equal",
        "equality_gate_pricing_asof_match",
        "equality_gate_artifact_ref",
    ):
        lines.append(f"{key}: {payload.get(key)}")
    findings = payload.get("findings") or []
    lines.append(f"findings: {', '.join(str(item) for item in findings) if findings else 'none'}")
    warnings = payload.get("warnings") or []
    if warnings:
        lines.append(f"warnings: {', '.join(str(item) for item in warnings)}")
    lines.append("paths:")
    for name, record in dict(payload.get("paths") or {}).items():
        if isinstance(record, Mapping):
            lines.append(f"  {name}: {record.get('path')} present={str(record.get('present')).lower()}")
    return "\n".join(lines)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize the latest execution timeline and provenance.")
    parser.add_argument("--repo-root", type=Path, default=_REPO_ROOT)
    parser.add_argument("--json", action="store_true", help="Emit deterministic JSON.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    payload = build_latest_execution_timeline_status(args.repo_root)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        print(render_text(payload))
    return 0 if payload.get("status") == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.recovery.interrupted_state import (
    BrokerState,
    ExecutionLifecycleState,
    IntendedOrder,
    InterruptedRunSnapshot,
    OrderState,
    coerce_float,
)
from core.recovery.drift_analysis import analyze_portfolio_drift
from core.recovery.eventual_settlement import assess_eventual_settlement
from core.recovery.execution_timeline import reconstruct_execution_timeline
from core.recovery.incident_package import build_incident_package
from core.recovery.recovery_artifacts import (
    build_simulation_artifact,
    write_simulation_artifacts,
)
from core.recovery.recovery_certification import certify_recovery_outputs
from core.recovery.recovery_classifier import (
    classify_interrupted_run,
    summarize_position_drift,
)
from core.recovery.recovery_delta import compute_recovery_delta, target_positions_from_intent
from core.recovery.recovery_governance import build_governance_report
from core.recovery.recovery_lineage import build_lifecycle_graph, build_recovery_lineage
from core.recovery.recovery_risk import score_recovery_risk
from core.recovery.recovery_validator import validate_recovery_candidate
from core.recovery.state_transitions import ALLOWED_TRANSITIONS, validate_transition_path


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _positions_from_payload(payload: dict[str, Any]) -> dict[str, float]:
    positions: dict[str, float] = {}
    raw_positions = payload.get("positions_current") or payload.get("positions") or {}
    if isinstance(raw_positions, dict):
        for symbol, qty in raw_positions.items():
            positions[str(symbol).upper()] = coerce_float(qty)
    elif isinstance(raw_positions, list):
        for item in raw_positions:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or item.get("ticker") or "").upper()
            if symbol:
                positions[symbol] = coerce_float(item.get("qty") or item.get("shares"))
    return positions


def _orders_from_payload(payload: dict[str, Any]) -> list[OrderState]:
    raw_orders = payload.get("orders_report_date") or payload.get("orders") or []
    out: list[OrderState] = []
    for item in raw_orders:
        if not isinstance(item, dict):
            continue
        out.append(
            OrderState(
                client_order_id=str(item.get("client_order_id") or item.get("order_id") or ""),
                symbol=str(item.get("symbol") or item.get("ticker") or "").upper(),
                side=str(item.get("side") or ""),
                qty=coerce_float(item.get("qty") or item.get("shares")),
                filled_qty=coerce_float(item.get("filled_qty")),
                status=str(item.get("status") or ""),
                filled_avg_price=(
                    coerce_float(item.get("filled_avg_price"))
                    if item.get("filled_avg_price") not in (None, "")
                    else None
                ),
            )
        )
    return out


def _broker_state_from_payload(payload: dict[str, Any]) -> BrokerState:
    account = payload.get("account") if isinstance(payload.get("account"), dict) else {}
    raw_open_orders = payload.get("open_orders_count")
    open_orders_count = int(raw_open_orders) if raw_open_orders is not None else 0
    return BrokerState(
        captured_at=str(payload.get("generated_at") or payload.get("captured_at") or ""),
        account_status=str(account.get("status") or payload.get("status") or ""),
        trading_blocked=bool(account.get("trading_blocked") or payload.get("trading_blocked") or False),
        cash=coerce_float(account.get("cash")) if account.get("cash") not in (None, "") else None,
        equity=coerce_float(account.get("equity") or account.get("portfolio_value"))
        if (account.get("equity") or account.get("portfolio_value")) not in (None, "")
        else None,
        buying_power=coerce_float(account.get("buying_power"))
        if account.get("buying_power") not in (None, "")
        else None,
        positions=_positions_from_payload(payload),
        orders=_orders_from_payload(payload),
        open_orders_count=open_orders_count,
    )


def _fills_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    fills = payload.get("fills_report_date") or payload.get("fills") or []
    return [dict(item) for item in fills if isinstance(item, dict)]


def _raw_orders_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    orders = payload.get("orders_report_date") or payload.get("orders") or []
    return [dict(item) for item in orders if isinstance(item, dict)]


def _intended_orders_from_payload(payload: dict[str, Any]) -> list[IntendedOrder]:
    raw_orders = (
        payload.get("orders_intended")
        or payload.get("trades")
        or payload.get("orders")
        or []
    )
    out: list[IntendedOrder] = []
    for item in raw_orders:
        if not isinstance(item, dict):
            continue
        out.append(
            IntendedOrder(
                symbol=str(item.get("ticker") or item.get("symbol") or "").upper(),
                side=str(item.get("side") or item.get("action") or "").upper(),
                qty=coerce_float(item.get("shares") or item.get("qty")),
                planned_notional=(
                    coerce_float(item.get("notional"))
                    if item.get("notional") not in (None, "")
                    else None
                ),
                reason=item.get("reason") or item.get("notes"),
            )
        )
    return [order for order in out if order.symbol and order.qty > 0]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dev-only dry-run simulation for interrupted rebalance recovery."
    )
    parser.add_argument("--source-run-id", required=True)
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--pretrade-positions", required=True, type=Path)
    parser.add_argument("--intended-orders", required=True, type=Path)
    parser.add_argument("--current-broker-state", required=True, type=Path)
    parser.add_argument("--execution-payload", type=Path)
    parser.add_argument("--posttrade-reconciliation-status", default="UNKNOWN")
    parser.add_argument("--execution-lock-present", action="store_true")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Defaults to outputs/recovery_simulations/<trade_date>_<source_run_id>",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pretrade_payload = _load_json(args.pretrade_positions)
    intended_payload = _load_json(args.intended_orders)
    broker_payload = _load_json(args.current_broker_state)
    execution_payload = _load_json(args.execution_payload) if args.execution_payload else {}

    intended_orders = _intended_orders_from_payload(intended_payload)
    broker_state = _broker_state_from_payload(broker_payload)
    snapshot = InterruptedRunSnapshot(
        source_run_id=args.source_run_id,
        trade_date=args.trade_date,
        execution_status=execution_payload.get("execution_status"),
        execution_outcome=execution_payload.get("execution_outcome"),
        halt_reason=execution_payload.get("halt_reason"),
        submitted_count=int(execution_payload.get("submitted_count") or 0),
        accepted_count=int(execution_payload.get("accepted_count") or 0),
        intended_orders=intended_orders,
        pretrade_positions=_positions_from_payload(pretrade_payload),
        current_broker_state=broker_state,
        execution_lock_present=bool(args.execution_lock_present),
        posttrade_reconciliation_status=args.posttrade_reconciliation_status,
    )

    target_positions = target_positions_from_intent(
        pretrade_positions=snapshot.pretrade_positions,
        intended_orders=snapshot.intended_orders,
    )
    recovery_delta = compute_recovery_delta(
        current_positions=snapshot.current_broker_state.positions,
        target_positions=target_positions,
        intended_orders=snapshot.intended_orders,
        buy_only=True,
    )
    classification = classify_interrupted_run(snapshot, recovery_delta=recovery_delta)
    validation = validate_recovery_candidate(
        broker_state=snapshot.current_broker_state,
        recovery_delta=recovery_delta,
        target_positions=target_positions,
        trade_date=snapshot.trade_date,
        stale_execution_lock_present=snapshot.execution_lock_present,
    )
    drift_summary = summarize_position_drift(
        current_positions=snapshot.current_broker_state.positions,
        target_positions=target_positions,
    )
    settlement = assess_eventual_settlement(
        observed_orders=snapshot.current_broker_state.orders,
        reconciliation_passed=args.posttrade_reconciliation_status.upper() in {"PASS", "OK_RECONCILED"},
    )
    portfolio_drift = analyze_portfolio_drift(
        current_positions=snapshot.current_broker_state.positions,
        target_positions=target_positions,
        current_cash=snapshot.current_broker_state.cash,
        current_equity=snapshot.current_broker_state.equity,
    )
    risk_report = score_recovery_risk(
        broker_state=snapshot.current_broker_state,
        recovery_delta=recovery_delta,
        validation=validation,
        drift_analysis=portfolio_drift,
        settlement=settlement,
        artifact_completeness={
            "pretrade_positions": args.pretrade_positions.exists(),
            "intended_orders": args.intended_orders.exists(),
            "current_broker_state": args.current_broker_state.exists(),
            "execution_payload": bool(args.execution_payload and args.execution_payload.exists()),
        },
        duplicate_order_risk=any(
            order.client_order_id.startswith(f"{snapshot.trade_date}:recovery_01:")
            for order in snapshot.current_broker_state.orders
        ),
        stale_broker_state=False,
    )
    transition_trace = validate_transition_path(
        [
            classification.state,
            # This dry-run can only model a simulation transition; it never
            # advances to approved/executed states.
            ExecutionLifecycleState.RECOVERY_SIMULATED,
        ]
        if classification.recovery_candidate
        else [classification.state]
    )
    timeline = reconstruct_execution_timeline(
        source_run_id=snapshot.source_run_id,
        trade_date=snapshot.trade_date,
        execution_payload=execution_payload,
        broker_orders=_raw_orders_from_payload(broker_payload),
        fills=_fills_from_payload(broker_payload),
        recovery_summary={
            "verdict": "SIMULATION_PASS" if validation.ok else "SIMULATION_BLOCKED",
            "recovery_timestamp": None,
        },
        posttrade_reconciliation={
            "verdict": "PASS"
            if args.posttrade_reconciliation_status.upper() in {"PASS", "OK_RECONCILED"}
            else args.posttrade_reconciliation_status,
            "drift_status": args.posttrade_reconciliation_status,
        },
    )
    governance_report = build_governance_report(
        lifecycle_state=classification.state,
        validation={
            "ok": validation.ok,
            "failures": validation.failures,
            "warnings": validation.warnings,
        },
        risk_report=risk_report,
        recovery_delta_count=len(recovery_delta),
        dry_run=True,
    )
    artifact = build_simulation_artifact(
        snapshot=snapshot,
        lifecycle_state=classification.state,
        target_positions=target_positions,
        recovery_delta=recovery_delta,
        validation=validation,
        drift_summary=drift_summary,
        recovery_id="recovery_01",
    )

    out_dir = args.output_dir
    if out_dir is None:
        safe_run_id = args.source_run_id.replace("/", "_").replace(":", "")
        out_dir = Path("outputs") / "recovery_simulations" / f"{args.trade_date}_{safe_run_id}"
    lineage = build_recovery_lineage(
        source_failed_run_id=snapshot.source_run_id,
        trade_date=snapshot.trade_date,
        lifecycle_state=classification.state.value,
        timeline=timeline,
        simulation_artifact=artifact,
        governance_report=governance_report,
    )
    lifecycle_graph = build_lifecycle_graph(lineage)
    transition_matrix = {
        "states": sorted(state.value for state in ALLOWED_TRANSITIONS),
        "allowed_transitions": {
            state.value: sorted(next_state.value for next_state in next_states)
            for state, next_states in ALLOWED_TRANSITIONS.items()
        },
    }
    paths = write_simulation_artifacts(
        output_dir=out_dir,
        artifact=artifact,
        execution_timeline=timeline,
        state_transition_trace=transition_trace,
        recovery_risk_report=risk_report,
        portfolio_drift=portfolio_drift,
        eventual_settlement=settlement.to_artifact(),
        recovery_governance_report=governance_report,
        recovery_lineage=lineage,
        lifecycle_graph=lifecycle_graph,
        transition_matrix=transition_matrix,
    )
    certification = certify_recovery_outputs(output_dir=out_dir, replay_count=1)
    from core.recovery.recovery_artifacts import write_json

    certification_path = write_json(out_dir / "recovery_certification_summary.json", certification)
    paths.append(certification_path)
    incident_manifest = build_incident_package(output_dir=out_dir)
    print(
        json.dumps(
            {
                "verdict": artifact["verdict"],
                "certification_ok": certification["ok"],
                "incident_package_complete": incident_manifest["complete"],
                "paths": [str(path) for path in paths],
            },
            indent=2,
        )
    )
    return 0 if validation.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())

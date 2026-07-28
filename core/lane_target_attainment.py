"""Target-attainment evidence for the unified paper/live-pilot executor."""

from __future__ import annotations

import math
from typing import Any, Mapping


SCHEMA_VERSION = "caerus_lane_target_attainment_v1"


def _number(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def build_lane_target_attainment(
    *,
    plan: Mapping[str, Any],
    post_snapshot: Mapping[str, Any],
    reconciliation: Mapping[str, Any],
    run_id: str,
    trade_date: str,
    mode: str,
    dry_run: bool,
    drift_tolerance: float = 0.02,
) -> dict[str, Any]:
    target = {
        str(row.get("symbol") or row.get("ticker") or "").strip().upper(): float(
            row.get("target_weight") or 0.0
        )
        for row in plan.get("target_portfolio") or []
        if isinstance(row, Mapping)
        and str(row.get("symbol") or row.get("ticker") or "").strip()
        and _number(row.get("target_weight")) is not None
    }
    target_cash = _number(plan.get("cash_target_weight"))
    if target_cash is None:
        target_cash = max(0.0, 1.0 - sum(target.values()))

    account = (
        post_snapshot.get("account")
        if isinstance(post_snapshot.get("account"), Mapping)
        else {}
    )
    equity = _number(
        account.get("equity")
        or account.get("portfolio_value")
    )
    cash = _number(account.get("cash"))
    actual_values: dict[str, float] = {}
    for row in post_snapshot.get("positions") or []:
        if not isinstance(row, Mapping):
            continue
        symbol = str(row.get("symbol") or row.get("ticker") or "").strip().upper()
        market_value = _number(row.get("market_value"))
        if symbol and market_value is not None:
            actual_values[symbol] = actual_values.get(symbol, 0.0) + market_value

    actual = (
        {symbol: value / equity for symbol, value in actual_values.items()}
        if equity is not None and equity > 0.0
        else {}
    )
    actual_cash = (
        cash / equity
        if cash is not None and equity is not None and equity > 0.0
        else None
    )
    symbols = sorted(set(target).union(actual))
    rows = [
        {
            "symbol": symbol,
            "target_weight": round(float(target.get(symbol, 0.0)), 10),
            "actual_weight": round(float(actual.get(symbol, 0.0)), 10),
            "weight_drift": round(
                float(actual.get(symbol, 0.0) - target.get(symbol, 0.0)),
                10,
            ),
        }
        for symbol in symbols
    ]
    max_position_drift = (
        max(abs(float(row["weight_drift"])) for row in rows) if rows else 0.0
    )
    cash_drift = (
        actual_cash - target_cash if actual_cash is not None else None
    )
    recon_status = str(reconciliation.get("status") or "").strip().upper()
    if dry_run:
        status = "DRY_RUN_NOT_APPLICABLE"
        reason = "dry_run_has_no_posttrade_target_attainment"
    elif equity is None or cash is None:
        status = "UNKNOWN_INSUFFICIENT_BROKER_SNAPSHOT"
        reason = "posttrade_equity_or_cash_missing"
    elif recon_status != "CLEAN":
        status = "FAIL_EXECUTION_INCOMPLETE"
        reason = f"reconciliation_{recon_status.lower() or 'unknown'}"
    elif (
        max_position_drift > float(drift_tolerance)
        or cash_drift is None
        or abs(cash_drift) > float(drift_tolerance)
    ):
        status = "WARN_TARGET_DRIFT"
        reason = "posttrade_weights_outside_tolerance"
    else:
        status = "OK_TARGET_ATTAINED"
        reason = "posttrade_weights_within_tolerance"

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "trade_date": trade_date,
        "account_scope": str(mode or "").strip().upper(),
        "status": status,
        "reason_code": reason,
        "drift_tolerance": float(drift_tolerance),
        "target_cash_weight": round(float(target_cash), 10),
        "achieved_cash_weight": (
            round(float(actual_cash), 10) if actual_cash is not None else None
        ),
        "cash_target_drift": (
            round(float(cash_drift), 10) if cash_drift is not None else None
        ),
        "max_absolute_position_weight_drift": round(max_position_drift, 10),
        "target_equity_name_count": len(target),
        "actual_equity_name_count": len(actual),
        "posttrade_equity": equity,
        "posttrade_cash": cash,
        "reconciliation_status": recon_status or None,
        "positions": rows,
        "source_artifacts": {
            "plan": "source plan passed to unified lane executor",
            "posttrade_snapshot": "live_pilot_broker_snapshot_post.json",
            "reconciliation": "live_pilot_reconciliation.json",
        },
    }

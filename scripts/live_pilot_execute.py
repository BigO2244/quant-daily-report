from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from brokers.alpaca_broker import AlpacaBroker
from core.live_pilot_guardrails import (
    LIVE_PILOT_MODE,
    LIVE_PILOT_MAX_SLIPPAGE_BPS_ENV,
    account_id_hash,
    build_live_pilot_gate_result,
    expected_account_matches,
    validate_live_pilot_asset,
    validate_live_pilot_plan,
)
from core.live_pilot_gate_state import write_live_pilot_gate_state
from paper.trading_calendar import ET_TZ, market_session_status
from paper.run_manager import generate_run_id, safe_write_text
from scripts.live_pilot_transition import (
    LIVE_PILOT_BLOCKED_INSUFFICIENT_BUYING_POWER as _ADAPTER_BLOCK_INSUFFICIENT,
    buy_intents_to_trades,
    capital_gate_artifact,
    compute_live_transition,
    transition_plan_artifact,
)


def _now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def _write_json(path: Path, payload: Mapping[str, Any]) -> Path:
    safe_write_text(
        path,
        json.dumps(_json_safe(dict(payload)), indent=2, sort_keys=True) + "\n",
        allow_overwrite=True,
    )
    return path


def _load_plan(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return {"trades": payload}
    if not isinstance(payload, dict):
        raise ValueError("live pilot plan must be a JSON object or list")
    return payload


def _trades_from_plan(plan: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    trades = plan.get("trades") or plan.get("orders") or []
    if not isinstance(trades, list):
        raise ValueError("live pilot plan trades/orders must be a list")
    return [trade for trade in trades if isinstance(trade, Mapping)]


def _trade_date_from_context(
    *,
    plan: Mapping[str, Any] | None,
    env: Mapping[str, str],
    now_et: dt.datetime | None,
) -> str:
    value = str((plan or {}).get("trade_date") or env.get("REPORT_DATE") or "").strip()
    if value:
        return value
    if now_et is not None:
        return now_et.astimezone(ET_TZ).date().isoformat()
    return dt.datetime.now(ET_TZ).date().isoformat()


PLAN_PROVENANCE_KEYS = (
    "ticker",
    "sleeve",
    "sleeve_source",
    "sleeve_provenance",
    "source_strategy_id",
    "source_signal_sleeve",
    "source_signal_target_weight",
    "source_signal_raw_score",
    "source_precompute_index",
    "source_reason",
    "limit_price_source",
    "scaled_to_pilot_cap",
    "source_notional",
    "pilot_notional_cap",
    "source_order_qty",
    "original_qty",
    "pre_normalization_qty",
    "pilot_qty",
    "final_qty",
    "scale_reason",
    "approved_sleeve_override",
)


def _plan_provenance_by_client_id(
    orders: list[Any],
    source_trades: list[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for order, source_trade in zip(orders, source_trades):
        client_order_id = str(getattr(order, "client_order_id", "") or "").strip()
        if not client_order_id:
            continue
        out[client_order_id] = {
            key: source_trade.get(key)
            for key in PLAN_PROVENANCE_KEYS
            if key in source_trade
        }
    return out


def _public_account(account: Mapping[str, Any] | None) -> dict[str, Any]:
    account = dict(account or {})
    account_id = str(account.get("id") or "").strip()
    return {
        "account_id_hash": account_id_hash(account_id) if account_id else None,
        "status": account.get("status"),
        "cash": account.get("cash"),
        "equity": account.get("equity"),
        "buying_power": account.get("buying_power"),
        "portfolio_value": account.get("portfolio_value"),
    }


OPEN_ORDER_STATUSES = frozenset(
    {
        "accepted",
        "accepted_for_bidding",
        "calculated",
        "done_for_day",
        "held",
        "new",
        "partially_filled",
        "pending_cancel",
        "pending_new",
        "pending_replace",
    }
)

LIVE_PILOT_ENTRY_EXECUTION_POLICY = "live_pilot_buy_market_order_immediate"
LIVE_PILOT_ENTRY_ESCALATION_SESSION_LIMIT = 3
NO_ESCALATION_REASON = "none"
LIVE_PILOT_BLOCKED_EXISTING_POSITIONS_REQUIRE_ROTATION = (
    "LIVE_PILOT_BLOCKED_EXISTING_POSITIONS_REQUIRE_ROTATION"
)
LIVE_PILOT_BLOCKED_INSUFFICIENT_BUYING_POWER = "LIVE_PILOT_BLOCKED_INSUFFICIENT_BUYING_POWER"
LIVE_PILOT_BLOCKED_BUYING_POWER_UNAVAILABLE = "LIVE_PILOT_BLOCKED_BUYING_POWER_UNAVAILABLE"
LIVE_PILOT_SELL_FIRST_SUPPORTED = False
LIVE_PILOT_REBUDGET_AFTER_SELL_SUPPORTED = False

CAPITAL_GATE_REPORT_KEYS = (
    "live_positions_before",
    "live_open_orders_before",
    "live_buying_power_before",
    "approved_cap_usd",
    "required_sell_count",
    "sell_first_supported",
    "rebudget_after_sell_supported",
    "strategy_allocation_cap_usd",
    "planned_buy_notional_usd",
    "tradable_capital_usd",
    "buy_block_reason",
)


def _safe_float(value: object) -> float | None:
    try:
        numeric = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return numeric


def _parse_time(value: object) -> dt.datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _list_open_orders(broker: Any) -> list[dict[str, Any]]:
    if not hasattr(broker, "list_orders"):
        return []
    try:
        orders = broker.list_orders(status="open", limit=100)
    except Exception as exc:
        return [{"status": "OPEN_ORDER_LOOKUP_FAILED", "error": str(exc)}]
    if not isinstance(orders, list):
        return []
    return [dict(order) for order in orders if isinstance(order, Mapping)]


def _order_status(order: Mapping[str, Any]) -> str:
    return str(order.get("status") or "").strip().lower()


def _is_open_order(order: Mapping[str, Any]) -> bool:
    status = _order_status(order)
    return status in OPEN_ORDER_STATUSES or status == "open" or not status


def _client_order_id(order: Mapping[str, Any]) -> str:
    return str(order.get("client_order_id") or order.get("client_order_id".upper()) or "").strip()


def _symbol(order: Mapping[str, Any]) -> str:
    return str(order.get("symbol") or order.get("ticker") or "").strip().upper()


def _open_pilot_order_check(broker: Any, *, intended_symbols: set[str]) -> dict[str, Any]:
    if not hasattr(broker, "list_orders"):
        return {
            "schema_version": "live_pilot_open_order_check.v1",
            "generated_at": _now_utc(),
            "status": "SKIPPED_NO_BROKER_SUPPORT",
            "open_orders": [],
            "blocking_open_orders": [],
            "block_submission": False,
            "operator_action": "Open-order lookup was unavailable on the injected broker; production Alpaca broker supports this check.",
        }
    open_orders = _list_open_orders(broker)
    lookup_failed = any(str(order.get("status")) == "OPEN_ORDER_LOOKUP_FAILED" for order in open_orders)
    if lookup_failed:
        return {
            "schema_version": "live_pilot_open_order_check.v1",
            "generated_at": _now_utc(),
            "status": "BLOCKED_LOOKUP_FAILED",
            "open_orders": _json_safe(open_orders),
            "blocking_open_orders": _json_safe(open_orders),
            "block_submission": True,
            "operator_action": "Open-order lookup failed; do not submit live pilot orders until broker truth is known.",
        }
    blocking: list[dict[str, Any]] = []
    for order in open_orders:
        if not _is_open_order(order):
            continue
        client_id = _client_order_id(order).lower()
        symbol = _symbol(order)
        is_pilot = client_id.startswith("caerus-live-pilot")
        same_exposure = bool(symbol and symbol in intended_symbols)
        if is_pilot or same_exposure:
            row = dict(order)
            row["duplicate_reason"] = "open_live_pilot_order" if is_pilot else "same_symbol_open_order"
            blocking.append(row)
    return {
        "schema_version": "live_pilot_open_order_check.v1",
        "generated_at": _now_utc(),
        "status": "BLOCKED_OPEN_PILOT_ORDER" if blocking else "PASS",
        "open_orders": _json_safe(open_orders),
        "blocking_open_orders": _json_safe(blocking),
        "block_submission": bool(blocking),
        "operator_action": (
            "Skip this live pilot attempt; leave existing open order untouched and review/cancel manually if needed."
            if blocking
            else "No open live-pilot or same-symbol order blocks this attempt."
        ),
    }


def _broker_snapshot(broker: Any) -> dict[str, Any]:
    account = broker.get_account() if hasattr(broker, "get_account") else {}
    positions = broker.get_positions() if hasattr(broker, "get_positions") else []
    open_orders = _list_open_orders(broker)
    return {
        "captured_at": _now_utc(),
        "account": _public_account(account),
        "positions": _json_safe(positions or []),
        "open_orders": _json_safe(open_orders),
    }


def _position_public_row(position: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "symbol": str(position.get("symbol") or "").strip().upper(),
        "qty": position.get("qty"),
        "market_value": position.get("market_value"),
        "cost_basis": position.get("cost_basis"),
        "side": position.get("side"),
    }


def _active_live_positions(positions: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not isinstance(positions, list):
        return rows
    for raw in positions:
        if not isinstance(raw, Mapping):
            continue
        row = _position_public_row(raw)
        qty = _safe_float(row.get("qty"))
        if qty is not None and abs(qty) <= 1e-12:
            continue
        if not row.get("symbol"):
            continue
        rows.append(row)
    return rows


def _open_order_public_row(order: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "symbol": _symbol(order),
        "side": str(order.get("side") or "").strip().upper(),
        "qty": order.get("qty"),
        "notional": order.get("notional"),
        "status": order.get("status"),
        "client_order_id": _client_order_id(order),
    }


def _capital_gate_report_fields(capital_gate: Mapping[str, Any] | None) -> dict[str, Any]:
    if not capital_gate:
        return {}
    return {key: capital_gate.get(key) for key in CAPITAL_GATE_REPORT_KEYS if key in capital_gate}


def _build_live_pilot_capital_gate(
    *,
    transition_plan: Any,
    pre_snapshot: Mapping[str, Any],
    approved_cap_usd: float | None,
) -> dict[str, Any]:
    """Thin wrapper mapping the shared Transition Engine's blocking output to the
    ``live_pilot_capital_gate.v1`` evidence shape.

    Workstream C Phase 2: the capital decision (rotation-required, buying-power
    adequacy, cap-as-ceiling) now comes from ``transition.compute_transition`` via
    ``scripts.live_pilot_transition``, not a parallel implementation here. The gate
    artifact schema/fields are preserved; ``required_sell_count`` now reflects the
    engine's actual sell intents (exits + reduces) rather than "any position held".
    """
    positions_before = _active_live_positions(pre_snapshot.get("positions") or [])
    open_orders_before = [
        _open_order_public_row(order)
        for order in (pre_snapshot.get("open_orders") or [])
        if isinstance(order, Mapping)
    ]
    return capital_gate_artifact(
        transition_plan,
        positions_before=positions_before,
        open_orders_before=open_orders_before,
        approved_cap_usd=approved_cap_usd,
        generated_at=_now_utc(),
    )


def _status_norm(status: object) -> str:
    value = str(status or "").strip().lower()
    if "." in value:
        value = value.rsplit(".", 1)[-1]
    return value


def _status_bucket(status: object) -> str:
    value = _status_norm(status)
    if value in {"filled"}:
        return "filled"
    if value in {"partially_filled"}:
        return "partial"
    if value in {"rejected", "canceled", "cancelled", "expired", "failed"}:
        return "rejected"
    if value in {"accepted", "pending_new", "new", "done_for_day", "pending_replace", "pending_cancel"}:
        return "accepted_open"
    return "unresolved"


def _load_json_if_present(path: Path) -> Any:
    try:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _orders_payload_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, Mapping):
        rows = payload.get("orders") or payload.get("trades") or []
    else:
        rows = []
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def _order_side(row: Mapping[str, Any]) -> str:
    return str(row.get("side") or "").strip().upper()


def _order_symbol(row: Mapping[str, Any]) -> str:
    return str(row.get("symbol") or row.get("ticker") or "").strip().upper()


def _submitted_order_type(row: Mapping[str, Any]) -> str:
    order = row.get("order") if isinstance(row.get("order"), Mapping) else {}
    return str(
        row.get("submitted_order_type")
        or row.get("order_type_submitted")
        or row.get("order_type")
        or order.get("order_type")
        or order.get("type")
        or ""
    ).strip().lower()


def _is_dry_run_row(row: Mapping[str, Any]) -> bool:
    return str(row.get("status") or "").strip().upper() == "DRY_RUN_NOT_SUBMITTED"


def _is_unfilled_submitted_buy(row: Mapping[str, Any]) -> bool:
    if _order_side(row) != "BUY" or _is_dry_run_row(row):
        return False
    order = row.get("order") if isinstance(row.get("order"), Mapping) else {}
    bucket = _status_bucket(row.get("status") or order.get("status"))
    return bucket in {"accepted_open", "partial", "unresolved"}


def _attempt_trade_date(run_root: Path, summary: Mapping[str, Any], row: Mapping[str, Any]) -> str | None:
    for value in (
        row.get("trade_date"),
        summary.get("trade_date"),
        summary.get("generated_at"),
        summary.get("submitted_at"),
        run_root.name,
    ):
        raw = str(value or "").strip()
        if len(raw) >= 10 and raw[4:5] == "-" and raw[7:8] == "-":
            return raw[:10]
    return None


def _prior_unfilled_buy_attempts(
    *,
    output_root: Path | str,
    current_run_id: str,
    symbol: str,
) -> list[dict[str, Any]]:
    runs_root = Path(output_root) / "runs"
    if not runs_root.exists():
        return []
    symbol_norm = str(symbol or "").strip().upper()
    attempts: list[dict[str, Any]] = []
    for run_root in sorted(path for path in runs_root.iterdir() if path.is_dir()):
        if run_root.name == current_run_id:
            continue
        submitted_payload = _load_json_if_present(run_root / "live_pilot_orders_submitted.json")
        summary = _load_json_if_present(run_root / "live_pilot_operator_summary.json")
        summary = summary if isinstance(summary, Mapping) else {}
        for row in _orders_payload_rows(submitted_payload):
            if _order_symbol(row) != symbol_norm or not _is_unfilled_submitted_buy(row):
                continue
            attempts.append(
                {
                    "run_id": str(summary.get("run_id") or run_root.name),
                    "trade_date": _attempt_trade_date(run_root, summary, row),
                    "symbol": symbol_norm,
                    "side": "BUY",
                    "status": str(row.get("status") or ""),
                    "submitted_order_type": _submitted_order_type(row) or None,
                    "client_order_id": str(row.get("client_order_id") or ""),
                    "reason": "prior_live_buy_submitted_not_filled",
                }
            )
    return attempts


def _entry_policy_for_order(
    order: Any,
    *,
    output_root: Path | str,
    run_id: str,
) -> dict[str, Any]:
    approved_order_type = str(getattr(order, "order_type", "") or "limit").strip().lower()
    side = str(getattr(order, "side", "") or "").strip().upper()
    if side != "BUY":
        passive = approved_order_type == "limit"
        return {
            "entry_execution_policy": "not_applicable_non_buy_order",
            "approved_order_type": approved_order_type,
            "submitted_order_type": approved_order_type,
            "order_type_submitted": approved_order_type,
            "is_marketable": approved_order_type == "market",
            "is_passive": passive,
            "marketable_or_passive": "passive" if passive else "marketable",
            "prior_unfilled_attempts": 0,
            "prior_unfilled_attempts_detail": [],
            "escalation_reason": "not_applicable_non_buy_order",
        }

    prior_attempts = _prior_unfilled_buy_attempts(
        output_root=output_root,
        current_run_id=run_id,
        symbol=str(getattr(order, "symbol", "") or ""),
    )
    prior_count = len(prior_attempts)
    if prior_count >= LIVE_PILOT_ENTRY_ESCALATION_SESSION_LIMIT:
        escalation_reason = "prior_unfilled_attempts_reached_three_session_limit"
    elif prior_count > 0:
        escalation_reason = "prior_unfilled_live_buy_attempts_detected"
    elif approved_order_type != "market":
        escalation_reason = "approved_limit_buy_overridden_to_market"
    else:
        escalation_reason = NO_ESCALATION_REASON
    return {
        "entry_execution_policy": LIVE_PILOT_ENTRY_EXECUTION_POLICY,
        "approved_order_type": approved_order_type,
        "submitted_order_type": "market",
        "order_type_submitted": "market",
        "is_marketable": True,
        "is_passive": False,
        "marketable_or_passive": "marketable",
        "prior_unfilled_attempts": prior_count,
        "prior_unfilled_attempts_detail": prior_attempts,
        "escalation_reason": escalation_reason,
    }


def _entry_policy_summary(
    *,
    intended: list[dict[str, Any]],
    submitted: list[dict[str, Any]],
    dry_run: bool,
) -> dict[str, Any]:
    approved_buys = [row for row in intended if _order_side(row) == "BUY"]
    submitted_buys = [
        row for row in submitted
        if _order_side(row) == "BUY" and not _is_dry_run_row(row)
    ]
    unfilled_buys = [row for row in submitted_buys if _is_unfilled_submitted_buy(row)]
    escalated_buys = [
        row for row in (submitted or intended)
        if _order_side(row) == "BUY"
        and str(row.get("escalation_reason") or "").strip()
        and str(row.get("escalation_reason")) != NO_ESCALATION_REASON
    ]
    escalation_reasons = sorted(
        {
            str(row.get("escalation_reason") or "").strip()
            for row in escalated_buys
            if str(row.get("escalation_reason") or "").strip()
        }
    )
    policies = sorted(
        {
            str(row.get("entry_execution_policy") or "").strip()
            for row in (submitted or intended)
            if str(row.get("entry_execution_policy") or "").strip()
        }
    )
    order_types = sorted(
        {
            _submitted_order_type(row)
            for row in (submitted or intended)
            if _submitted_order_type(row)
        }
    )
    passive_count = sum(1 for row in (submitted or intended) if bool(row.get("is_passive")))
    marketable_count = sum(1 for row in (submitted or intended) if bool(row.get("is_marketable")))
    prior_counts = [
        int(row.get("prior_unfilled_attempts") or 0)
        for row in (submitted or intended)
        if _order_side(row) == "BUY"
    ]
    blocked_or_suppressed = max(len(approved_buys) - (0 if dry_run else len(submitted_buys)), 0)
    return {
        "approved_buy_count": len(approved_buys),
        "submitted_buy_count": 0 if dry_run else len(submitted_buys),
        "unfilled_buy_count": 0 if dry_run else len(unfilled_buys),
        "escalated_buy_count": len(escalated_buys),
        "entry_execution_policy": policies[0] if len(policies) == 1 else "mixed" if policies else None,
        "order_type_submitted": order_types[0] if len(order_types) == 1 else "mixed" if order_types else None,
        "submitted_order_type": order_types[0] if len(order_types) == 1 else "mixed" if order_types else None,
        "marketable_order_count": marketable_count,
        "passive_order_count": passive_count,
        "prior_unfilled_attempts": max(prior_counts) if prior_counts else 0,
        "escalation_reason": (
            escalation_reasons[0]
            if len(escalation_reasons) == 1
            else "mixed"
            if escalation_reasons
            else NO_ESCALATION_REASON
        ),
        "remaining_blocked_or_suppressed_buy_count": blocked_or_suppressed,
    }


def _build_live_pilot_execution_results(
    *,
    run_id: str,
    trade_date: str,
    terminal_status: str,
    reason_code: object,
    intended: list[dict[str, Any]],
    submitted: list[dict[str, Any]],
    reconciliation: Mapping[str, Any],
    dry_run: bool,
    run_root: Path,
    extra_fields: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    entry_summary = _entry_policy_summary(
        intended=intended,
        submitted=submitted,
        dry_run=dry_run,
    )
    if int(entry_summary.get("remaining_blocked_or_suppressed_buy_count") or 0) > 0:
        entry_summary["blocked_or_suppressed_buy_reason"] = reason_code
    else:
        entry_summary["blocked_or_suppressed_buy_reason"] = NO_ESCALATION_REASON
    filled_qty_total = 0.0
    fill_prices: list[float] = []
    for row in submitted:
        order = row.get("order") if isinstance(row.get("order"), Mapping) else {}
        if _status_bucket(row.get("status") or order.get("status")) != "filled":
            continue
        qty = _safe_float(
            row.get("filled_qty")
            or row.get("filled_quantity")
            or order.get("filled_qty")
            or order.get("filled_quantity")
            or row.get("qty")
        )
        if qty is not None:
            filled_qty_total += qty
        fill_price = _fill_price(row)
        if fill_price is not None:
            fill_prices.append(fill_price)
    submitted_count = 0 if dry_run else len(submitted)
    filled_count = int(reconciliation.get("filled_count") or 0)
    if dry_run:
        idle_cash_reason = "dry_run_no_capital_submitted"
    elif submitted_count > 0 and filled_count == 0:
        idle_cash_reason = "submitted_not_filled"
    elif submitted_count > 0 and filled_count >= submitted_count:
        idle_cash_reason = "capital_deployed_within_cap"
    else:
        idle_cash_reason = "partial_cap_deployment"
    return {
        "schema_version": "live_pilot_execution_results.v1",
        "run_id": run_id,
        "trade_date": trade_date,
        "mode": LIVE_PILOT_MODE.upper(),
        "status": terminal_status,
        "reason": reason_code,
        "halt_reason": None if terminal_status in {"DRY_RUN", "SUBMITTED"} else reason_code,
        "operator_execution_status": (
            "dry_run"
            if dry_run
            else "executed"
            if terminal_status == "SUBMITTED"
            else "halted"
        ),
        "submitted_count": submitted_count,
        "accepted_count": int(reconciliation.get("accepted_count") or 0),
        "rejected_count": int(reconciliation.get("rejected_count") or 0),
        "filled_count": filled_count,
        "orders_filled_count": filled_count,
        "filled_qty": filled_qty_total if filled_qty_total > 0 else None,
        "fill_qty": filled_qty_total if filled_qty_total > 0 else None,
        "avg_fill_price": _mean(fill_prices),
        "open_orders_count": int(reconciliation.get("open_count") or 0),
        "idle_cash_reason": reconciliation.get("idle_cash_reason") or idle_cash_reason,
        "broker_status_refresh": reconciliation.get("broker_status_refresh"),
        "broker_status_refresh_at": reconciliation.get("broker_status_refresh_at"),
        "broker_status_refresh_errors": list(reconciliation.get("broker_status_refresh_errors") or []),
        "broker_status_refresh_claims_broker_truth": reconciliation.get("broker_status_refresh_claims_broker_truth"),
        "broker_responses": submitted,
        "order_lifecycle": submitted,
        "run_root": str(run_root),
        **entry_summary,
        **dict(extra_fields or {}),
    }


def _reconcile(
    *,
    dry_run: bool,
    intended: list[dict[str, Any]],
    submitted: list[dict[str, Any]],
    errors: list[str],
) -> dict[str, Any]:
    accepted = 0
    filled = 0
    rejected = 0
    unresolved = 0
    partial = 0
    open_count = 0
    for row in submitted:
        order = row.get("order") if isinstance(row.get("order"), Mapping) else {}
        status_value = row.get("status") or order.get("status")
        bucket = _status_bucket(status_value)
        if bucket in {"accepted_open", "partial", "filled"}:
            accepted += 1
        if bucket == "accepted_open":
            open_count += 1
        if bucket == "partial":
            partial += 1
        if bucket == "filled":
            filled += 1
        elif bucket == "rejected":
            rejected += 1
        elif bucket == "unresolved":
            unresolved += 1

    if dry_run:
        state = "DRY_RUN"
        status = "DRY_RUN_NO_SUBMISSION"
        action = "Review artifacts and keep dry run enabled until human approval is recorded."
    elif errors or rejected:
        state = "REJECTED"
        status = "FAILED_RECONCILIATION"
        action = "Do not continue live pilot; inspect broker state and resolve rejected/unresolved orders manually."
    elif partial:
        state = "PARTIAL"
        status = "FAILED_RECONCILIATION"
        action = "Do not continue live pilot; wait for broker terminal truth or manually review partial fill state."
    elif unresolved or len(submitted) != len(intended):
        state = "UNRESOLVED"
        status = "FAILED_RECONCILIATION"
        action = "Do not continue live pilot; inspect broker state and resolve rejected/unresolved orders manually."
    else:
        state = "CLEAN"
        status = "CLEAN"
        action = "Monitor broker terminal states and preserve all live pilot artifacts."

    return {
        "schema_version": "live_pilot_reconciliation.v1",
        "generated_at": _now_utc(),
        "status": status,
        "state": state,
        "intended_count": len(intended),
        "submitted_count": len(submitted),
        "accepted_count": accepted,
        "filled_count": filled,
        "partial_count": partial,
        "open_count": open_count,
        "rejected_count": rejected + len(errors),
        "unresolved_count": unresolved,
        "errors": list(errors),
        "operator_action": action,
        "rollback_recommendation": (
            "No auto-liquidation. Cancel/flatten only under a separately approved live incident runbook."
            if status != "CLEAN"
            else "No rollback action required unless broker state later diverges."
        ),
    }


def _normal_market_hours_gate(*, now_et: dt.datetime | None = None) -> dict[str, Any]:
    current = now_et or dt.datetime.now(ET_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=ET_TZ)
    current = current.astimezone(ET_TZ)
    status = market_session_status(
        run_date=current.date().isoformat(),
        now_et=current,
        cutoff_time_et="16:00",
    )
    return {
        "schema_version": "live_pilot_market_hours_gate.v1",
        "generated_at": _now_utc(),
        "status": "PASS" if status.is_open_now else "BLOCKED",
        "market_is_open": bool(status.is_open_now),
        "reason_code": status.reason,
        "now_et": current.isoformat(),
        "session_open_et": status.session_open_et.isoformat() if status.session_open_et else None,
        "session_close_et": status.session_close_et.isoformat() if status.session_close_et else None,
        "operator_action": (
            "Normal market hours confirmed for FR-104 live pilot submission."
            if status.is_open_now
            else "Do not submit FR-104 live pilot market orders outside normal market hours."
        ),
    }


def _fill_price(row: Mapping[str, Any]) -> float | None:
    order = row.get("order") if isinstance(row.get("order"), Mapping) else {}
    for key in ("filled_avg_price", "fill_price", "avg_fill_price", "price"):
        value = _safe_float(row.get(key))
        if value is not None and value > 0:
            return value
        value = _safe_float(order.get(key))
        if value is not None and value > 0:
            return value
    return None


def _time_to_fill_seconds(row: Mapping[str, Any]) -> float | None:
    order = row.get("order") if isinstance(row.get("order"), Mapping) else {}
    submitted_at = (
        _parse_time(row.get("submitted_at"))
        or _parse_time(order.get("submitted_at"))
        or _parse_time(order.get("created_at"))
    )
    filled_at = _parse_time(row.get("filled_at")) or _parse_time(order.get("filled_at"))
    if submitted_at is None or filled_at is None:
        return None
    return max((filled_at - submitted_at).total_seconds(), 0.0)


def _slippage_bps(row: Mapping[str, Any]) -> float | None:
    expected = _safe_float(row.get("expected_price") or row.get("cap_enforcement_price") or row.get("limit_price"))
    fill = _fill_price(row)
    if expected is None or expected <= 0 or fill is None:
        return None
    side = str(row.get("side") or "").strip().upper()
    direction = 1.0 if side != "SELL" else -1.0
    return direction * ((fill - expected) / expected) * 10000.0


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _build_evidence_metrics(
    *,
    dry_run: bool,
    intended: list[dict[str, Any]],
    submitted: list[dict[str, Any]],
    reconciliation: Mapping[str, Any],
    capital_cap_usd: float | None,
    open_order_check: Mapping[str, Any] | None = None,
    capital_gate: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    submitted_count = 0 if dry_run else len(submitted)
    accepted_count = int(reconciliation.get("accepted_count") or 0)
    filled_count = int(reconciliation.get("filled_count") or 0)
    rejected_count = int(reconciliation.get("rejected_count") or 0)
    fill_rate = (filled_count / submitted_count) if submitted_count else None
    fill_seconds = [
        value for value in (_time_to_fill_seconds(row) for row in submitted)
        if value is not None
    ]
    slippage_values = [
        value for value in (_slippage_bps(row) for row in submitted)
        if value is not None
    ]
    filled_notional = 0.0
    for row in submitted:
        if str(row.get("status") or "").strip().lower() != "filled":
            continue
        fill = _fill_price(row)
        qty = _safe_float(row.get("qty"))
        if fill is not None and qty is not None:
            filled_notional += fill * qty
        else:
            filled_notional += float(row.get("notional") or 0.0)
    cap = float(capital_cap_usd or 0.0)
    cash_deployment_rate = (filled_notional / cap) if cap > 0 else None
    if dry_run:
        idle_reason = "dry_run_no_capital_submitted"
    elif open_order_check and bool(open_order_check.get("block_submission")):
        idle_reason = "open_order_blocked_duplicate_exposure"
    elif submitted_count == 0:
        idle_reason = "no_live_pilot_submission"
    elif filled_count == 0:
        idle_reason = "submitted_not_filled"
    elif cash_deployment_rate is not None and cash_deployment_rate < 0.99:
        idle_reason = "partial_cap_deployment"
    else:
        idle_reason = "capital_deployed_within_cap"
    metrics = {
        "schema_version": "live_pilot_evidence_metrics.v1",
        "generated_at": _now_utc(),
        "intended_count": len(intended),
        "submitted_count": submitted_count,
        "accepted_count": accepted_count,
        "filled_count": filled_count,
        "fill_rate": fill_rate,
        "average_time_to_fill_seconds": _mean(fill_seconds),
        "slippage_bps": _mean(slippage_values),
        "rejected_count": rejected_count,
        "reconciliation_clean": str(reconciliation.get("status") or "").upper() == "CLEAN",
        "reconciliation_clean_rate": 1.0 if str(reconciliation.get("status") or "").upper() == "CLEAN" else 0.0,
        "cash_deployment_rate": cash_deployment_rate,
        "filled_notional_usd": round(filled_notional, 6),
        "capital_cap_usd": capital_cap_usd,
        "idle_cash_reason": idle_reason,
    }
    metrics.update(_capital_gate_report_fields(capital_gate))
    return metrics


def _write_blocked_artifacts(
    *,
    run_root: Path,
    run_id: str,
    trade_date: str,
    env: Mapping[str, str],
    reason_code: str,
    operator_action: str,
    preflight: Mapping[str, Any],
    intended: list[dict[str, Any]] | None = None,
    open_order_check: Mapping[str, Any] | None = None,
    capital_gate: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    intended = intended or []
    submitted: list[dict[str, Any]] = []
    dry_run = bool(preflight.get("dry_run"))
    capital_gate_fields = _capital_gate_report_fields(capital_gate)
    reconciliation = _reconcile(
        dry_run=dry_run,
        intended=intended,
        submitted=submitted,
        errors=[reason_code],
    )
    if capital_gate:
        reconciliation.update(
            {
                "status": "BLOCKED_PRE_SUBMISSION",
                "state": "BLOCKED",
                "operator_action": operator_action,
                "idle_cash_reason": reason_code,
                **capital_gate_fields,
            }
        )
    entry_summary = _entry_policy_summary(
        intended=intended,
        submitted=submitted,
        dry_run=dry_run,
    )
    summary = {
        "schema_version": "live_pilot_operator_summary.v1",
        "generated_at": _now_utc(),
        "run_id": run_id,
        "mode": LIVE_PILOT_MODE.upper(),
        "terminal_status": "BLOCKED",
        "reason_code": reason_code,
        "live_orders_allowed": False,
        "submitted_count": 0,
        "operator_action": operator_action,
        **entry_summary,
        **capital_gate_fields,
    }
    write_live_pilot_gate_state(
        run_root=run_root,
        run_id=run_id,
        trade_date=trade_date,
        env=env,
        repo_root=REPO_ROOT,
        decision="BLOCKED",
        block_reason=reason_code,
        broker_orders_submitted=0,
        base_url=str(preflight.get("base_url") or ""),
    )
    _write_json(run_root / "live_pilot_orders_intended.json", {"orders": intended})
    _write_json(run_root / "live_pilot_orders_submitted.json", {"orders": submitted})
    if capital_gate:
        _write_json(run_root / "live_pilot_capital_gate.json", capital_gate)
    blocked_snapshot = {
        "captured_at": _now_utc(),
        "status": "NOT_CAPTURED_BLOCKED_BEFORE_BROKER_SNAPSHOT",
        "account": {},
        "positions": [],
    }
    for snapshot_name in (
        "live_pilot_broker_snapshot_pre.json",
        "live_pilot_broker_snapshot_post.json",
    ):
        snapshot_path = run_root / snapshot_name
        if not snapshot_path.exists():
            _write_json(snapshot_path, blocked_snapshot)
    _write_json(run_root / "live_pilot_reconciliation.json", reconciliation)
    _write_json(
        run_root / "live_pilot_evidence_metrics.json",
        {
            **_build_evidence_metrics(
                dry_run=dry_run,
                intended=intended,
                submitted=submitted,
                reconciliation=reconciliation,
                capital_cap_usd=_safe_float(preflight.get("capital_cap_usd")),
                open_order_check=open_order_check,
                capital_gate=capital_gate,
            ),
            **entry_summary,
        },
    )
    _write_json(
        run_root / "live_pilot_capital_usage.json",
        {
            "schema_version": "live_pilot_capital_usage.v1",
            "capital_used_usd": 0.0,
            **capital_gate_fields,
        },
    )
    _write_json(run_root / "live_pilot_operator_summary.json", summary)
    _write_json(run_root / "live_pilot_preflight.json", dict(preflight))
    _write_json(
        run_root / "execution_results.json",
        _build_live_pilot_execution_results(
            run_id=run_id,
            trade_date=trade_date,
            terminal_status="BLOCKED",
            reason_code=reason_code,
            intended=intended,
            submitted=submitted,
            reconciliation=reconciliation,
            dry_run=dry_run,
            run_root=run_root,
            extra_fields=capital_gate_fields,
        ),
    )
    return summary


def run_live_pilot(
    *,
    plan: Mapping[str, Any],
    plan_path: str | None = None,
    broker: Any | None = None,
    env: Mapping[str, str] | None = None,
    run_id: str | None = None,
    output_root: Path | str = Path("outputs/live_pilot"),
    now_et: dt.datetime | None = None,
) -> dict[str, Any]:
    environ = env if env is not None else os.environ
    run_id = str(run_id or environ.get("RUN_ID") or generate_run_id())
    trade_date = _trade_date_from_context(
        plan=plan,
        env=environ,
        now_et=now_et,
    )
    run_root = Path(output_root) / "runs" / run_id
    run_root.mkdir(parents=True, exist_ok=True)

    broker = broker or AlpacaBroker.from_env()
    broker_paper = bool(getattr(broker, "paper", True))
    base_url = str(getattr(broker, "base_url", "") or "")
    gate = build_live_pilot_gate_result(
        broker_paper=broker_paper,
        base_url=base_url,
        env=environ,
        submission_intent=False,
    )
    preflight = gate.to_dict()
    preflight["schema_version"] = "live_pilot_preflight.v1"
    preflight["run_id"] = run_id
    preflight["trade_date"] = trade_date
    preflight["generated_at"] = _now_utc()
    preflight["orders_submitted"] = 0
    _write_json(run_root / "live_pilot_preflight.json", preflight)

    payload = {
        "schema_version": "live_pilot_execution_payload.v1",
        "generated_at": _now_utc(),
        "run_id": run_id,
        "trade_date": trade_date,
        "mode": LIVE_PILOT_MODE,
        "plan_path": plan_path,
        "dry_run": bool(gate.dry_run),
        "paper_paths_touched": False,
        "order_policy": LIVE_PILOT_ENTRY_EXECUTION_POLICY,
        "entry_execution_policy": LIVE_PILOT_ENTRY_EXECUTION_POLICY,
        "entry_escalation_session_limit": LIVE_PILOT_ENTRY_ESCALATION_SESSION_LIMIT,
    }
    _write_json(run_root / "live_pilot_execution_payload.json", payload)

    if gate.status != "PASS":
        return _write_blocked_artifacts(
            run_root=run_root,
            run_id=run_id,
            trade_date=trade_date,
            env=environ,
            reason_code=gate.reason_code,
            operator_action=gate.operator_action,
            preflight=preflight,
        )

    try:
        pre_snapshot = _broker_snapshot(broker)
    except Exception as exc:
        return _write_blocked_artifacts(
            run_root=run_root,
            run_id=run_id,
            trade_date=trade_date,
            env=environ,
            reason_code="live_pilot_pre_snapshot_failed",
            operator_action=f"Resolve read-only broker snapshot failure before live pilot: {exc}",
            preflight=preflight,
        )
    _write_json(run_root / "live_pilot_broker_snapshot_pre.json", pre_snapshot)

    account_public = pre_snapshot.get("account") or {}
    account_hash = str(account_public.get("account_id_hash") or "")
    # Route the account-hash match through the orchestrated gate result so the
    # pinned-account check is a single authoritative decision (fail closed on a
    # missing broker id or a mismatch) rather than an ad-hoc executor-only check.
    raw_account = broker.get_account() if hasattr(broker, "get_account") else {}
    account_gate = build_live_pilot_gate_result(
        broker_paper=broker_paper,
        base_url=base_url,
        env=environ,
        account_id=(raw_account or {}).get("id"),
        account_id_hash=account_hash,
        enforce_account_match=True,
    )
    if not account_gate.account_id_match:
        return _write_blocked_artifacts(
            run_root=run_root,
            run_id=run_id,
            trade_date=trade_date,
            env=environ,
            reason_code=account_gate.account_match_reason or "live_pilot_account_id_mismatch",
            operator_action="Expected live pilot account id/hash does not match broker account.",
            preflight=preflight,
        )

    # --- Transition Engine (Workstream C Phase 2, Option A) --------------------
    # Full target portfolio + broker snapshot -> keep/reduce/sell/buy/block. This
    # replaces the buy-only narrowing: the engine selects the single buy by target
    # weight priority (max_orders=1), sizes it against min(cash, buying_power, cap,
    # incremental need) with the $100 min-trade floor, and blocks on rotation (sells
    # unsupported under Option A) or insufficient buying power. Cap is a ceiling,
    # never treated as spendable cash. The capital gate is now a thin wrapper over
    # this engine output.
    transition_plan = compute_live_transition(
        pre_snapshot=pre_snapshot,
        plan=plan,
        approved_cap_usd=gate.capital_cap_usd,
        env=environ,
        max_orders=int(gate.max_orders or 1),
    )
    capital_gate = _build_live_pilot_capital_gate(
        transition_plan=transition_plan,
        pre_snapshot=pre_snapshot,
        approved_cap_usd=gate.capital_cap_usd,
    )
    _write_json(
        run_root / "live_pilot_transition_plan.json",
        transition_plan_artifact(transition_plan, generated_at=_now_utc()),
    )
    # capital_gate.json is written by _write_blocked_artifacts on the blocked paths and
    # once inline below on the allowed path (avoids the previous double-write).
    if transition_plan.blocked:
        reason_code = str(capital_gate.get("buy_block_reason") or capital_gate.get("block_reason") or "")
        return _write_blocked_artifacts(
            run_root=run_root,
            run_id=run_id,
            trade_date=trade_date,
            env=environ,
            reason_code=reason_code or _ADAPTER_BLOCK_INSUFFICIENT,
            operator_action=str(
                capital_gate.get("operator_action")
                or "Live-pilot transition engine blocked before broker submission."
            ),
            preflight=preflight,
            capital_gate=capital_gate,
        )

    # Engine-selected buy intent(s) become the source trades for validation/submission.
    source_trades = buy_intents_to_trades(transition_plan, source_plan=plan)
    if not source_trades:
        return _write_blocked_artifacts(
            run_root=run_root,
            run_id=run_id,
            trade_date=trade_date,
            env=environ,
            reason_code="live_pilot_transition_no_actionable_buy",
            operator_action=(
                "Transition engine produced no buy intent (holdings already satisfy the "
                "target within the min-trade floor); no live order required."
            ),
            preflight=preflight,
            capital_gate=capital_gate,
        )

    # Allowed path: write the capital-gate evidence exactly once.
    _write_json(run_root / "live_pilot_capital_gate.json", capital_gate)
    plan_validation = validate_live_pilot_plan(
        source_trades,
        env=environ,
        capital_cap_usd=float(gate.capital_cap_usd or 0.0),
        max_orders=int(gate.max_orders or 0),
        run_id=run_id,
    )
    plan_provenance_by_client_id = _plan_provenance_by_client_id(
        plan_validation.orders,
        source_trades,
    )
    policy_by_client_id = {
        order.client_order_id: _entry_policy_for_order(
            order,
            output_root=output_root,
            run_id=run_id,
        )
        for order in plan_validation.orders
    }
    intended = [
        {
            **plan_provenance_by_client_id.get(order.client_order_id, {}),
            **order.to_dict(),
            **policy_by_client_id.get(order.client_order_id, {}),
        }
        for order in plan_validation.orders
    ]
    intended_payload = plan_validation.to_dict()
    intended_payload["orders"] = intended
    intended_payload.update(
        _entry_policy_summary(
            intended=intended,
            submitted=[],
            dry_run=bool(gate.dry_run),
        )
    )
    _write_json(run_root / "live_pilot_orders_intended.json", intended_payload)
    _write_json(
        run_root / "live_pilot_entry_attempt_history.json",
        {
        "schema_version": "live_pilot_entry_attempt_history.v1",
            "generated_at": _now_utc(),
            "run_id": run_id,
            "trade_date": trade_date,
            "orders": [
                {
                    "symbol": row.get("symbol"),
                    "side": row.get("side"),
                    "approved_order_type": row.get("approved_order_type"),
                    "submitted_order_type": row.get("submitted_order_type"),
                    "entry_execution_policy": row.get("entry_execution_policy"),
                    "prior_unfilled_attempts": row.get("prior_unfilled_attempts"),
                    "prior_unfilled_attempts_detail": row.get("prior_unfilled_attempts_detail"),
                    "escalation_reason": row.get("escalation_reason"),
                    "sleeve": row.get("sleeve"),
                    "sleeve_source": row.get("sleeve_source"),
                    "source_strategy_id": row.get("source_strategy_id"),
                    "source_signal_sleeve": row.get("source_signal_sleeve"),
                    "sleeve_provenance": row.get("sleeve_provenance"),
                }
                for row in intended
            ],
        },
    )
    if plan_validation.status != "PASS":
        return _write_blocked_artifacts(
            run_root=run_root,
            run_id=run_id,
            trade_date=trade_date,
            env=environ,
            reason_code=";".join(plan_validation.reason_codes),
            operator_action=plan_validation.operator_action,
            preflight=preflight,
            intended=intended,
        )

    asset_errors: list[str] = []
    for order in plan_validation.orders:
        asset = broker.get_asset(order.symbol) if hasattr(broker, "get_asset") else None
        error = validate_live_pilot_asset(asset, order.symbol)
        if error:
            asset_errors.append(error)
    if asset_errors:
        return _write_blocked_artifacts(
            run_root=run_root,
            run_id=run_id,
            trade_date=trade_date,
            env=environ,
            reason_code=";".join(asset_errors),
            operator_action="Unsupported or non-tradable assets blocked before submission.",
            preflight=preflight,
            intended=intended,
        )

    open_order_check = _open_pilot_order_check(
        broker,
        intended_symbols={order.symbol for order in plan_validation.orders},
    )
    _write_json(run_root / "live_pilot_open_order_check.json", open_order_check)
    if bool(open_order_check.get("block_submission")):
        return _write_blocked_artifacts(
            run_root=run_root,
            run_id=run_id,
            trade_date=trade_date,
            env=environ,
            reason_code=str(open_order_check.get("status") or "BLOCKED_OPEN_PILOT_ORDER"),
            operator_action=str(open_order_check.get("operator_action") or "Open pilot order blocks duplicate submission."),
            preflight=preflight,
            intended=intended,
            open_order_check=open_order_check,
        )

    market_hours_gate = _normal_market_hours_gate(now_et=now_et)
    _write_json(run_root / "live_pilot_market_hours_gate.json", market_hours_gate)
    if not gate.dry_run and market_hours_gate.get("status") != "PASS":
        return _write_blocked_artifacts(
            run_root=run_root,
            run_id=run_id,
            trade_date=trade_date,
            env=environ,
            reason_code=f"live_pilot_market_closed:{market_hours_gate.get('reason_code')}",
            operator_action=str(market_hours_gate.get("operator_action") or "Market is closed."),
            preflight=preflight,
            intended=intended,
        )

    # Capital gate already ran (and wrote its artifact) via the Transition Engine
    # immediately after the broker snapshot; nothing re-checks it here.
    submitted: list[dict[str, Any]] = []
    submit_errors: list[str] = []
    max_slippage_bps = _safe_float(environ.get(LIVE_PILOT_MAX_SLIPPAGE_BPS_ENV))
    if gate.dry_run:
        submitted = [
            {
                **plan_provenance_by_client_id.get(order.client_order_id, {}),
                **order.to_dict(),
                **policy_by_client_id.get(order.client_order_id, {}),
                "status": "DRY_RUN_NOT_SUBMITTED",
                "order": None,
            }
            for order in plan_validation.orders
        ]
    else:
        for order in plan_validation.orders:
            policy = policy_by_client_id.get(order.client_order_id, {})
            submitted_order_type = str(policy.get("submitted_order_type") or order.order_type).strip().lower()
            try:
                if submitted_order_type == "market":
                    broker_result = broker.submit_market_order(
                        symbol=order.symbol,
                        qty=order.qty,
                        side=order.side,
                        client_order_id=order.client_order_id,
                        tif="day",
                        estimated_notional=order.notional,
                    )
                    submitted_price = None
                else:
                    broker_result = broker.submit_limit_order(
                        symbol=order.symbol,
                        qty=order.qty,
                        side=order.side,
                        limit_price=order.limit_price,
                        client_order_id=order.client_order_id,
                        tif="day",
                    )
                    submitted_price = order.limit_price
                submitted_row = {
                    **plan_provenance_by_client_id.get(order.client_order_id, {}),
                    **order.to_dict(),
                    **policy,
                    "status": str((broker_result or {}).get("status") or "accepted"),
                    "order": broker_result,
                    "submitted_order_type": submitted_order_type,
                    "order_type_submitted": submitted_order_type,
                    "submitted_price": submitted_price,
                    "fill_price": _fill_price({"order": broker_result}),
                    "submission_policy": policy.get("entry_execution_policy") or LIVE_PILOT_ENTRY_EXECUTION_POLICY,
                }
                slip = _slippage_bps(submitted_row)
                submitted_row["slippage_bps"] = slip
                submitted_row["slippage_warning"] = (
                    bool(max_slippage_bps is not None and slip is not None and abs(slip) > max_slippage_bps)
                )
                submitted.append(
                    submitted_row
                )
            except Exception as exc:
                submit_errors.append(f"{order.symbol}:broker_submit_failed:{exc}")
                submitted.append(
                    {
                        **plan_provenance_by_client_id.get(order.client_order_id, {}),
                        **order.to_dict(),
                        **policy,
                        "status": "REJECTED",
                        "error": str(exc),
                        "submitted_order_type": submitted_order_type,
                        "order_type_submitted": submitted_order_type,
                        "submission_policy": policy.get("entry_execution_policy") or LIVE_PILOT_ENTRY_EXECUTION_POLICY,
                    }
                )

    _write_json(run_root / "live_pilot_orders_submitted.json", {"orders": submitted})

    try:
        post_snapshot = _broker_snapshot(broker)
    except Exception as exc:
        post_snapshot = {
            "captured_at": _now_utc(),
            "status": "SNAPSHOT_FAILED",
            "error": str(exc),
        }
        submit_errors.append(f"post_snapshot_failed:{exc}")
    _write_json(run_root / "live_pilot_broker_snapshot_post.json", post_snapshot)

    reconciliation = _reconcile(
        dry_run=bool(gate.dry_run),
        intended=intended,
        submitted=submitted,
        errors=submit_errors,
    )
    _write_json(run_root / "live_pilot_reconciliation.json", reconciliation)
    evidence_metrics = _build_evidence_metrics(
        dry_run=bool(gate.dry_run),
        intended=intended,
        submitted=submitted,
        reconciliation=reconciliation,
        capital_cap_usd=gate.capital_cap_usd,
        open_order_check=open_order_check,
        capital_gate=capital_gate,
    )
    entry_summary = _entry_policy_summary(
        intended=intended,
        submitted=submitted,
        dry_run=bool(gate.dry_run),
    )
    evidence_metrics.update(entry_summary)
    _write_json(run_root / "live_pilot_evidence_metrics.json", evidence_metrics)
    _write_json(
        run_root / "live_pilot_capital_usage.json",
        {
            "schema_version": "live_pilot_capital_usage.v1",
            "capital_cap_usd": gate.capital_cap_usd,
            "planned_notional_usd": plan_validation.total_notional,
            "submitted_notional_usd": 0.0 if gate.dry_run else sum(float(row.get("notional") or 0.0) for row in submitted if row.get("status") != "REJECTED"),
            "filled_notional_usd": evidence_metrics.get("filled_notional_usd"),
            "cash_deployment_rate": evidence_metrics.get("cash_deployment_rate"),
            "dry_run": bool(gate.dry_run),
            **_capital_gate_report_fields(capital_gate),
        },
    )
    write_live_pilot_gate_state(
        run_root=run_root,
        run_id=run_id,
        trade_date=trade_date,
        env=environ,
        repo_root=REPO_ROOT,
        decision="ALLOWED",
        block_reason=None,
        broker_orders_submitted=0 if gate.dry_run else len(submitted),
        base_url=base_url,
    )

    terminal_status = (
        "DRY_RUN"
        if gate.dry_run
        else ("SUBMITTED" if reconciliation.get("status") == "CLEAN" else "FAILED_RECONCILIATION")
    )
    summary = {
        "schema_version": "live_pilot_operator_summary.v1",
        "generated_at": _now_utc(),
        "run_id": run_id,
        "trade_date": trade_date,
        "mode": LIVE_PILOT_MODE.upper(),
        "terminal_status": terminal_status,
        "reason_code": reconciliation.get("status"),
        "live_orders_allowed": bool(gate.live_orders_allowed),
        "dry_run": bool(gate.dry_run),
        "intended_count": len(intended),
        "submitted_count": 0 if gate.dry_run else len(submitted),
        "filled_count": reconciliation.get("filled_count"),
        "fill_rate": evidence_metrics.get("fill_rate"),
        "cash_deployment_rate": evidence_metrics.get("cash_deployment_rate"),
        "idle_cash_reason": evidence_metrics.get("idle_cash_reason"),
        "operator_action": reconciliation.get("operator_action"),
        "run_root": str(run_root),
        **entry_summary,
        **_capital_gate_report_fields(capital_gate),
    }
    _write_json(run_root / "live_pilot_operator_summary.json", summary)
    _write_json(
        run_root / "execution_results.json",
        _build_live_pilot_execution_results(
            run_id=run_id,
            trade_date=trade_date,
            terminal_status=terminal_status,
            reason_code=reconciliation.get("status"),
            intended=intended,
            submitted=submitted,
            reconciliation=reconciliation,
            dry_run=bool(gate.dry_run),
            run_root=run_root,
            extra_fields=_capital_gate_report_fields(capital_gate),
        ),
    )
    return summary



def _extract_broker_order_id(row: Mapping[str, Any]) -> str:
    order = row.get("order") if isinstance(row.get("order"), Mapping) else {}
    return str((order or {}).get("id") or row.get("broker_order_id") or row.get("order_id") or "").strip()


def refresh_live_pilot_reconciliation(
    *,
    run_root: Path | str,
    broker: Any | None = None,
) -> dict[str, Any]:
    run_root = Path(run_root)
    broker = broker or AlpacaBroker.from_env()
    intended_payload = _load_plan(run_root / "live_pilot_orders_intended.json")
    submitted_payload = _load_plan(run_root / "live_pilot_orders_submitted.json")
    intended = _trades_from_plan(intended_payload)
    submitted = [dict(row) for row in _trades_from_plan(submitted_payload)]

    refresh_errors: list[str] = []
    refreshed: list[dict[str, Any]] = []
    for row in submitted:
        order_id = _extract_broker_order_id(row)
        if not order_id:
            refresh_errors.append(f"{row.get('symbol') or row.get('ticker')}:missing_broker_order_id")
            refreshed.append(row)
            continue
        try:
            broker_order = broker.get_order(order_id)
            if not broker_order:
                refresh_errors.append(f"{row.get('symbol') or row.get('ticker')}:broker_order_not_found:{order_id}")
                refreshed.append(row)
                continue
            refreshed.append({
                **row,
                "status": str((broker_order or {}).get("status") or row.get("status")),
                "order": broker_order,
                "filled_qty": (broker_order or {}).get("filled_qty") or (broker_order or {}).get("filled_quantity") or row.get("filled_qty"),
                "filled_quantity": (broker_order or {}).get("filled_quantity") or (broker_order or {}).get("filled_qty") or row.get("filled_quantity"),
                "filled_avg_price": (broker_order or {}).get("filled_avg_price") or row.get("filled_avg_price"),
                "avg_fill_price": (broker_order or {}).get("filled_avg_price") or row.get("avg_fill_price"),
                "refreshed_at": _now_utc(),
            })
        except Exception as exc:
            refresh_errors.append(f"{row.get('symbol') or row.get('ticker')}:broker_order_refresh_failed:{exc}")
            refreshed.append(row)

    _write_json(run_root / "live_pilot_orders_submitted.json", {"orders": refreshed})
    try:
        post_snapshot = _broker_snapshot(broker)
    except Exception as exc:
        post_snapshot = {"captured_at": _now_utc(), "status": "SNAPSHOT_FAILED", "error": str(exc)}
        refresh_errors.append(f"post_snapshot_failed:{exc}")
    _write_json(run_root / "live_pilot_broker_snapshot_post.json", post_snapshot)

    reconciliation = _reconcile(
        dry_run=False,
        intended=intended,
        submitted=refreshed,
        errors=refresh_errors,
    )
    reconciliation["refreshed_existing_run"] = True
    reconciliation["broker_status_refresh"] = "FAILED" if refresh_errors else "OK"
    reconciliation["broker_status_refresh_at"] = _now_utc()
    reconciliation["broker_status_refresh_errors"] = list(refresh_errors)
    reconciliation["broker_status_refresh_claims_broker_truth"] = not bool(refresh_errors)
    _write_json(run_root / "live_pilot_reconciliation.json", reconciliation)

    summary = {
        "schema_version": "live_pilot_operator_summary.v1",
        "generated_at": _now_utc(),
        "run_id": run_root.name,
        "mode": LIVE_PILOT_MODE.upper(),
        "terminal_status": "SUBMITTED" if reconciliation.get("status") == "CLEAN" else "FAILED_RECONCILIATION",
        "reason_code": reconciliation.get("status"),
        "live_orders_allowed": True,
        "dry_run": False,
        "intended_count": len(intended),
        "submitted_count": len(refreshed),
        "operator_action": reconciliation.get("operator_action"),
        "run_root": str(run_root),
        "refreshed_existing_run": True,
    }
    summary.update(
        _entry_policy_summary(
            intended=[dict(row) for row in intended if isinstance(row, Mapping)],
            submitted=refreshed,
            dry_run=False,
        )
    )
    _write_json(run_root / "live_pilot_operator_summary.json", summary)
    _write_json(
        run_root / "execution_results.json",
        _build_live_pilot_execution_results(
            run_id=run_root.name,
            trade_date=str(
                (
                    json.loads((run_root / "live_pilot_preflight.json").read_text(encoding="utf-8"))
                    if (run_root / "live_pilot_preflight.json").exists()
                    else {}
                ).get("trade_date")
                or os.getenv("REPORT_DATE")
                or ""
            ),
            terminal_status=str(summary.get("terminal_status") or ""),
            reason_code=reconciliation.get("status"),
            intended=[dict(row) for row in intended if isinstance(row, Mapping)],
            submitted=refreshed,
            reconciliation=reconciliation,
            dry_run=False,
            run_root=run_root,
        ),
    )
    return summary

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the isolated Caerus LIVE_PILOT executor")
    parser.add_argument("--plan", default=None, help="Path to a live pilot JSON plan")
    parser.add_argument("--refresh-run", default=None, help="Read-only refresh for an existing live pilot run root")
    parser.add_argument("--run-id", default=None, help="Optional deterministic run id")
    parser.add_argument("--output-root", default="outputs/live_pilot", help="Isolated live pilot output root")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.refresh_run:
        result = refresh_live_pilot_reconciliation(run_root=Path(args.refresh_run))
    else:
        if not args.plan:
            raise SystemExit("--plan is required unless --refresh-run is provided")
        plan_path = Path(args.plan)
        result = run_live_pilot(
            plan=_load_plan(plan_path),
            plan_path=str(plan_path),
            run_id=args.run_id,
            output_root=Path(args.output_root),
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if str(result.get("terminal_status") or "").upper() in {"DRY_RUN", "SUBMITTED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

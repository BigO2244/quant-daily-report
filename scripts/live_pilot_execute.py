from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import math
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from brokers.alpaca_broker import AlpacaBroker, alpaca_client_order_id
from core.live_trade_ledger import record_live_order
from core.live_pilot_guardrails import (
    LIVE_PILOT_MODE,
    LIVE_PILOT_MAX_SLIPPAGE_BPS_ENV,
    _is_live_host,
    account_id_hash,
    build_live_pilot_gate_result,
    expected_account_matches,
    resolve_dynamic_cap,
    validate_live_pilot_asset,
    validate_live_pilot_plan,
)
from core.live_pilot_gate_state import write_live_pilot_gate_state
from execution.core import (
    AccountSnapshot as CoreAccountSnapshot,
    ExecutionRequest,
    OrderIntent,
    SubmitResult,
    apply_capital_budget_and_execution_filter,
    compute_transition_trades,
    execute_lifecycle,
    live_pilot_execution_config,
)
from paper.trading_calendar import ET_TZ, market_session_status
from paper.run_manager import generate_run_id, safe_write_text


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
LIVE_PILOT_TOTAL_NOTIONAL_EXCEEDS_CAP = "live_pilot_total_notional_exceeds_cap"
LIVE_PILOT_EQUITY_EXCEEDS_CAP_REGIME = "live_pilot_equity_exceeds_cap_regime"
LIVE_PILOT_EQUITY_UNAVAILABLE = "live_pilot_equity_unavailable"
LIVE_PILOT_SELL_SETTLEMENT_TIMEOUT = "live_pilot_sell_settlement_timeout"
LIVE_PILOT_SELL_SETTLEMENT_CASH_NOT_REFLECTED = "live_pilot_sell_settlement_cash_not_reflected"
LIVE_PILOT_MALFORMED_HOLDING = "live_pilot_malformed_holding"
LIVE_PILOT_SELL_FIRST_SUPPORTED = True
LIVE_PILOT_REBUDGET_AFTER_SELL_SUPPORTED = True
LIVE_PILOT_SETTLEMENT_TIMEOUT_ENV = "CAERUS_LIVE_PILOT_SETTLEMENT_TIMEOUT_SECONDS"
LIVE_PILOT_SETTLEMENT_POLL_ENV = "CAERUS_LIVE_PILOT_SETTLEMENT_POLL_SECONDS"
LIVE_PILOT_SETTLEMENT_REFLECTION_FACTOR = 0.95
LIVE_PILOT_SETTLEMENT_DOLLAR_TOLERANCE = 0.01

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


def _finite_float(value: object) -> float | None:
    numeric = _safe_float(value)
    if numeric is None or not math.isfinite(numeric):
        return None
    return float(numeric)


def _derived_position_price(position: Mapping[str, Any]) -> float | None:
    qty = _finite_float(position.get("qty"))
    market_value = _finite_float(position.get("market_value"))
    if qty is None or abs(qty) <= 1e-12:
        return None
    if market_value is None or market_value <= 0.0:
        return None
    price = abs(market_value / qty)
    if not math.isfinite(price) or price <= 0.0:
        return None
    return float(price)


def _malformed_holding_reason(position: Mapping[str, Any]) -> str | None:
    symbol = str(position.get("symbol") or "").strip().upper()
    qty = _finite_float(position.get("qty"))
    market_value = _finite_float(position.get("market_value"))
    reasons: list[str] = []
    if not symbol:
        reasons.append("missing_symbol")
    if qty is None or abs(qty) <= 1e-12:
        reasons.append("missing_zero_or_nonfinite_qty")
    if market_value is None or market_value <= 0.0:
        reasons.append("missing_nonpositive_or_nonfinite_market_value")
    if not reasons and _derived_position_price(position) is None:
        reasons.append("degenerate_price")
    return ",".join(reasons) if reasons else None


def _holding_frames_from_snapshot(pre_snapshot: Mapping[str, Any]) -> tuple[pd.DataFrame, pd.Series, list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    price_by_symbol: dict[str, float] = {}
    malformed: list[dict[str, Any]] = []
    for raw in pre_snapshot.get("positions") or []:
        if not isinstance(raw, Mapping):
            continue
        reason = _malformed_holding_reason(raw)
        symbol = str(raw.get("symbol") or "").strip().upper()
        if reason:
            malformed.append({"symbol": symbol or None, "reason": reason, "position": dict(raw)})
            continue
        qty = float(_finite_float(raw.get("qty")) or 0.0)
        price = float(_derived_position_price(raw) or 0.0)
        if not symbol or abs(qty) <= 1e-12 or price <= 0.0:
            continue
        rows.append({"ticker": symbol, "sleeve": "live_pilot", "shares": abs(qty)})
        price_by_symbol[symbol] = price
    frame = pd.DataFrame(rows, columns=["ticker", "sleeve", "shares"])
    return frame, pd.Series(price_by_symbol, dtype=float), malformed


def _target_rows_from_plan(plan: Mapping[str, Any], *, equity: float) -> tuple[pd.DataFrame, pd.Series]:
    rows = plan.get("target_portfolio")
    if not isinstance(rows, list) or not rows:
        rows = plan.get("trades") or plan.get("orders") or []
    targets: list[dict[str, Any]] = []
    price_by_symbol: dict[str, float] = {}
    equity_value = float(equity or 0.0)
    for raw in rows or []:
        if not isinstance(raw, Mapping):
            continue
        symbol = str(raw.get("symbol") or raw.get("ticker") or "").strip().upper()
        if not symbol:
            continue
        price = _finite_float(
            raw.get("price")
            or raw.get("limit_price")
            or raw.get("expected_price")
            or raw.get("normalized_limit_price")
            or raw.get("entry_price")
        )
        if price is None or price <= 0.0:
            continue
        weight = _finite_float(raw.get("target_weight"))
        if weight is None or weight <= 0.0:
            notional = _finite_float(raw.get("notional"))
            qty = _finite_float(raw.get("shares") or raw.get("qty") or raw.get("quantity"))
            if notional is None and qty is not None:
                notional = qty * price
            if notional is None or notional <= 0.0:
                continue
            weight = (notional / equity_value) if equity_value > 0.0 else notional
        targets.append({"ticker": symbol, "sleeve": str(raw.get("sleeve") or "live_pilot"), "target_weight": float(weight)})
        price_by_symbol[symbol] = float(price)
    frame = pd.DataFrame(targets, columns=["ticker", "sleeve", "target_weight"])
    return frame, pd.Series(price_by_symbol, dtype=float)


def _plan_rows_by_symbol(plan: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = plan.get("target_portfolio")
    if not isinstance(rows, list) or not rows:
        rows = plan.get("trades") or plan.get("orders") or []
    out: dict[str, Mapping[str, Any]] = {}
    for raw in rows or []:
        if not isinstance(raw, Mapping):
            continue
        symbol = str(raw.get("symbol") or raw.get("ticker") or "").strip().upper()
        if symbol and symbol not in out:
            out[symbol] = raw
    return out


def _build_core_request(
    *,
    pre_snapshot: Mapping[str, Any],
    plan: Mapping[str, Any],
    run_id: str,
    planning_equity_cap: float | None = None,
) -> tuple[ExecutionRequest | None, list[dict[str, Any]]]:
    account = pre_snapshot.get("account") if isinstance(pre_snapshot.get("account"), Mapping) else {}
    equity = _finite_float((account or {}).get("equity") or (account or {}).get("portfolio_value"))
    if equity is None or equity <= 0.0:
        return None, [{"reason": LIVE_PILOT_EQUITY_UNAVAILABLE, "symbol": None}]
    cash = float(_finite_float((account or {}).get("cash")) or 0.0)
    # Staging-scale pin (CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP; paper lane only).
    # Target sizing is weight * total_equity / price, so without this pin a paper
    # account far above the pinned capital cap computes single-name needs that
    # exceed the approved cap and hard-blocks with
    # live_pilot_total_notional_exceeds_cap. Clamping the planning equity to the
    # same scale as the cap makes plan sizing and execution agree at the pinned
    # scale. The clamp only ever TIGHTENS (min); the live lane leaves the env
    # unset and continues to size against real account equity.
    if planning_equity_cap is not None and planning_equity_cap > 0.0 and equity > planning_equity_cap:
        equity = float(planning_equity_cap)
        # Keep the planning account internally coherent at the pinned scale
        # (cash can never exceed equity); the buy budget is already bounded by
        # approved_cap_usd, so this only tightens.
        cash = min(cash, equity)
    holdings, holding_prices, malformed = _holding_frames_from_snapshot(pre_snapshot)
    targets, target_prices = _target_rows_from_plan(plan, equity=equity)
    prices = holding_prices.copy()
    for symbol, price in target_prices.items():
        prices.loc[symbol] = float(price)
    # Carry the risk-adjusted cash target through execution so live matches paper.
    # Paper holds this cash back (circuit breaker, sector trim); a legacy plan without
    # the field defaults to 0.0 (prior behavior).
    cash_target_weight = _finite_float(plan.get("cash_target_weight"))
    if cash_target_weight is None or cash_target_weight < 0.0:
        cash_target_weight = 0.0
    planning_account = {
        "cash": str(cash),
        "equity": str(equity),
        "portfolio_value": str(equity),
        "buying_power": (account or {}).get("buying_power"),
        "status": (account or {}).get("status") or "ACTIVE",
    }
    request = ExecutionRequest(
        holdings=holdings,
        targets=targets,
        prices=prices,
        total_equity=float(equity),
        starting_cash=float(cash),
        target_cash_weight=float(cash_target_weight),
        planning_account=planning_account,
        run_id=run_id,
        price_basis="live_broker_snapshot",
    )
    return request, malformed


def _max_incremental_need(request: ExecutionRequest) -> tuple[str | None, float]:
    if request.targets is None or request.targets.empty:
        return None, 0.0
    held = (
        request.holdings.set_index("ticker")["shares"].astype(float).to_dict()
        if request.holdings is not None and not request.holdings.empty
        else {}
    )
    max_symbol: str | None = None
    max_need = 0.0
    for _, row in request.targets.iterrows():
        symbol = str(row.get("ticker") or "").strip().upper()
        if not symbol or symbol not in request.prices.index:
            continue
        price = float(request.prices.loc[symbol])
        if price <= 0.0:
            continue
        target_shares = float(row.get("target_weight") or 0.0) * float(request.total_equity) / price
        current_shares = float(held.get(symbol, 0.0))
        need = max(0.0, target_shares - current_shares) * price
        if need > max_need:
            max_symbol = symbol
            max_need = float(need)
    return max_symbol, float(max_need)


def _core_rows_from_frame(frame: pd.DataFrame, *, plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    source_by_symbol = _plan_rows_by_symbol(plan)
    rows: list[dict[str, Any]] = []
    if frame is None or frame.empty:
        return rows
    for _, row in frame.iterrows():
        symbol = str(row.get("ticker") or row.get("symbol") or "").strip().upper()
        source = source_by_symbol.get(symbol, {})
        order_type = str(source.get("order_type") or "market").strip().lower()
        if order_type not in {"market", "limit"}:
            order_type = "market"
        rows.append(
            {
                "ticker": symbol,
                "symbol": symbol,
                "side": str(row.get("side") or "").strip().upper(),
                "shares": float(row.get("shares") or 0.0),
                "qty": float(row.get("shares") or 0.0),
                "limit_price": float(row.get("price") or 0.0),
                "price": float(row.get("price") or 0.0),
                "expected_price": float(row.get("price") or 0.0),
                "cap_enforcement_price": float(row.get("price") or 0.0),
                "notional": float(row.get("notional") or 0.0),
                "order_type": order_type,
                "source_reason": row.get("reason"),
                **{key: source[key] for key in PLAN_PROVENANCE_KEYS if key in source},
            }
        )
    return rows


def _trade_frame_orders(frame: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if frame is None or frame.empty:
        return rows
    for _, row in frame.iterrows():
        rows.append(
            {
                "symbol": str(row.get("ticker") or row.get("symbol") or "").strip().upper(),
                "side": str(row.get("side") or "").strip().upper(),
                "shares": float(row.get("shares") or 0.0),
                "price": float(row.get("price") or 0.0),
                "notional": float(row.get("notional") or 0.0),
                "reason": str(row.get("reason") or ""),
            }
        )
    return rows


def _frame_len(frame: Any) -> int:
    return 0 if frame is None else int(len(frame))


def _build_live_pilot_capital_gate(
    *,
    result: Any,
    pre_snapshot: Mapping[str, Any],
    approved_cap_usd: float | None,
) -> dict[str, Any]:
    positions_before = _active_live_positions(pre_snapshot.get("positions") or [])
    open_orders_before = [
        _open_order_public_row(order)
        for order in (pre_snapshot.get("open_orders") or [])
        if isinstance(order, Mapping)
    ]
    budget = dict(getattr(result, "capital_budget", {}) or {})
    post_budget = dict(getattr(result, "post_sell_budget_meta", {}) or {})
    sell_count = _frame_len(getattr(result, "sell_trades", None))
    buy_count = _frame_len(getattr(result, "rebuilt_buy_trades", None))
    blocked_reason = None
    if buy_count == 0 and float(budget.get("requested_buy_notional") or 0.0) > 0.0:
        blocked_reason = LIVE_PILOT_BLOCKED_INSUFFICIENT_BUYING_POWER
    return {
        "schema_version": "live_pilot_capital_gate.v1",
        "generated_at": _now_utc(),
        "decision": "ALLOWED" if blocked_reason is None else "BLOCKED",
        "block_reason": blocked_reason,
        "live_positions_before": positions_before,
        "live_open_orders_before": open_orders_before,
        "live_buying_power_before": budget.get("broker_buying_power_at_planning"),
        "approved_cap_usd": _safe_float(approved_cap_usd),
        "required_sell_count": sell_count,
        "sell_first_supported": LIVE_PILOT_SELL_FIRST_SUPPORTED,
        "rebudget_after_sell_supported": LIVE_PILOT_REBUDGET_AFTER_SELL_SUPPORTED,
        "strategy_allocation_cap_usd": budget.get("requested_buy_notional") or None,
        "planned_buy_notional_usd": budget.get("requested_buy_notional"),
        "tradable_capital_usd": (budget.get("reserve_cash_policy") or {}).get("available_for_buys"),
        "buy_block_reason": blocked_reason,
        "broker_orders_submitted": int(
            len(getattr(result, "submitted_sells", ()) or ())
            + len(getattr(result, "submitted_buys", ()) or ())
        ),
        "post_sell_buy_budget": post_budget,
    }


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


def _ledger_outcome_event(status: object) -> str | None:
    value = _status_norm(status)
    if value == "expired":
        return "expired"
    bucket = _status_bucket(value)
    if bucket == "filled":
        return "filled"
    if bucket == "rejected":
        return "rejected"
    return None


def _ledger_filled_qty(order: Any, fallback_qty: Any) -> Any:
    if isinstance(order, Mapping):
        filled_qty = order.get("filled_qty")
        if filled_qty is None:
            filled_qty = order.get("filled_quantity")
        if filled_qty not in (None, ""):
            return filled_qty
    return fallback_qty


def _record_live_order_submitted(
    *,
    order: Any,
    run_root: Path,
    output_root: Path | str,
    env: Mapping[str, str],
    submitted_order_type: str,
) -> None:
    record_live_order(
        event="submitted",
        symbol=order.symbol,
        side=order.side,
        qty=order.qty,
        filled_qty=None,
        limit_price=order.limit_price if submitted_order_type == "limit" else None,
        notional=order.notional,
        client_order_id=order.client_order_id,
        broker_order_id=None,
        run_root=run_root,
        status="SUBMIT_ATTEMPTED",
        reason=None,
        output_root=output_root,
        env=env,
    )


def _record_live_order_outcome(
    *,
    order: Any,
    broker_result: Mapping[str, Any] | None,
    run_root: Path,
    output_root: Path | str,
    env: Mapping[str, str],
    submitted_order_type: str,
    error: str | None = None,
) -> None:
    broker_payload = broker_result if isinstance(broker_result, Mapping) else {}
    status = "REJECTED" if error is not None else broker_payload.get("status")
    event = "rejected" if error is not None else _ledger_outcome_event(status)
    if event is None:
        return
    record_live_order(
        event=event,
        symbol=broker_payload.get("symbol") or order.symbol,
        side=broker_payload.get("side") or order.side,
        qty=broker_payload.get("qty") or order.qty,
        filled_qty=_ledger_filled_qty(broker_payload, order.qty if event == "filled" else None),
        limit_price=order.limit_price if submitted_order_type == "limit" else None,
        notional=order.notional,
        client_order_id=broker_payload.get("client_order_id") or order.client_order_id,
        broker_order_id=broker_payload.get("id"),
        run_root=run_root,
        status=status,
        reason=error or broker_payload.get("error"),
        output_root=output_root,
        env=env,
        ts_utc=broker_payload.get("filled_at") if event == "filled" else None,
    )


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


def _holdings_frame_from_broker_positions(positions: Any) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if not isinstance(positions, list):
        return pd.DataFrame(columns=["ticker", "sleeve", "shares"])
    for raw in positions:
        if not isinstance(raw, Mapping):
            continue
        reason = _malformed_holding_reason(raw)
        if reason:
            continue
        symbol = str(raw.get("symbol") or "").strip().upper()
        qty = float(_finite_float(raw.get("qty")) or 0.0)
        if symbol and abs(qty) > 1e-12:
            rows.append({"ticker": symbol, "sleeve": "live_pilot", "shares": abs(qty)})
    return pd.DataFrame(rows, columns=["ticker", "sleeve", "shares"])


class LivePilotCoreAdapter:
    """Core adapter for the isolated live-pilot executor.

    The production broker methods are only exercised when the caller supplies a
    broker; Phase 1d tests inject a fake broker. Sells are refreshed before buys,
    and unresolved sell settlement returns the broker's actual refreshed account
    with ``sell_phase_allows_buy=False`` so the core cannot size buys from expected
    proceeds.
    """

    def __init__(
        self,
        *,
        broker: Any,
        env: Mapping[str, str],
        run_root: Path,
        output_root: Path | str,
        run_id: str,
        pre_sell_account: Mapping[str, Any] | None = None,
        source_plan: Mapping[str, Any] | None = None,
    ) -> None:
        self.broker = broker
        self.env = env
        self.run_root = run_root
        self.output_root = output_root
        self.run_id = run_id
        self.pre_sell_account = dict(pre_sell_account or {})
        self.source_by_symbol = _plan_rows_by_symbol(source_plan or {})
        self.submitted_rows: list[dict[str, Any]] = []
        self.submit_errors: list[str] = []
        self._sequence = 0

    def _client_order_id(self, intent: OrderIntent) -> str:
        self._sequence += 1
        # Hash-collapse to a UNIQUE <=48-char id. Naive [:48] truncation dropped the
        # -{seq}-{symbol} suffix (run_id alone fills the 48 chars), so every order in a
        # run shared one client_order_id and the broker rejected all but the first as
        # "client_order_id must be unique" (2026-07-10 live incident).
        return alpaca_client_order_id(
            f"caerus-live-pilot-{self.run_id}-{self._sequence}-{intent.symbol}".lower()
        )

    def _policy(self, intent: OrderIntent) -> dict[str, Any]:
        source = self.source_by_symbol.get(intent.symbol, {})
        order_type = str(source.get("order_type") or "market").strip().lower()
        if order_type not in {"market", "limit"}:
            order_type = "market"
        order_like = SimpleNamespace(
            symbol=intent.symbol,
            side=intent.side,
            order_type=order_type,
        )
        return _entry_policy_for_order(
            order_like,
            output_root=self.output_root,
            run_id=self.run_id,
        )

    def submit(self, intent: OrderIntent) -> SubmitResult:
        client_order_id = self._client_order_id(intent)
        submitted_intent = OrderIntent(
            symbol=intent.symbol,
            side=intent.side,
            shares=float(intent.shares),
            price=float(intent.price),
            notional=float(intent.notional),
            reason=str(intent.reason or ""),
            order=int(intent.order),
            slippage_cost=float(intent.slippage_cost or 0.0),
            client_order_id=client_order_id,
        )
        policy = self._policy(submitted_intent)
        submitted_order_type = str(policy.get("submitted_order_type") or "market").strip().lower()
        try:
            if submitted_order_type == "limit":
                broker_result = self.broker.submit_limit_order(
                    symbol=submitted_intent.symbol,
                    qty=submitted_intent.shares,
                    side=submitted_intent.side,
                    limit_price=submitted_intent.price,
                    client_order_id=client_order_id,
                    tif="day",
                )
                submitted_price = submitted_intent.price
            else:
                broker_result = self.broker.submit_market_order(
                    symbol=submitted_intent.symbol,
                    qty=submitted_intent.shares,
                    side=submitted_intent.side,
                    client_order_id=client_order_id,
                    tif="day",
                    estimated_notional=submitted_intent.notional,
                )
                submitted_price = None
            broker_payload = broker_result if isinstance(broker_result, Mapping) else {}
            status = str(broker_payload.get("status") or "accepted")
            source = self.source_by_symbol.get(submitted_intent.symbol, {})
            provenance = {key: source[key] for key in PLAN_PROVENANCE_KEYS if key in source}
            row = {
                **provenance,
                "symbol": submitted_intent.symbol,
                "ticker": submitted_intent.symbol,
                "side": submitted_intent.side,
                "qty": submitted_intent.shares,
                "shares": submitted_intent.shares,
                "limit_price": submitted_intent.price,
                "expected_price": submitted_intent.price,
                "cap_enforcement_price": submitted_intent.price,
                "notional": submitted_intent.notional,
                "client_order_id": client_order_id,
                "status": status,
                "order": broker_payload,
                "submitted_order_type": submitted_order_type,
                "order_type_submitted": submitted_order_type,
                "submitted_price": submitted_price,
                "fill_price": _fill_price({"order": broker_payload}),
                "submission_policy": policy.get("entry_execution_policy"),
                "source_reason": submitted_intent.reason,
                **policy,
            }
            row["slippage_bps"] = _slippage_bps(row)
            self.submitted_rows.append(row)
            return SubmitResult(
                intent=submitted_intent,
                status=status,
                broker_order_id=str(broker_payload.get("id") or "") or None,
                filled_qty=_safe_float(
                    broker_payload.get("filled_qty")
                    or broker_payload.get("filled_quantity")
                    or (submitted_intent.shares if _status_bucket(status) == "filled" else None)
                ),
                filled_notional=None,
                raw=broker_payload,
            )
        except Exception as exc:
            self.submit_errors.append(f"{submitted_intent.symbol}:broker_submit_failed:{exc}")
            source = self.source_by_symbol.get(submitted_intent.symbol, {})
            provenance = {key: source[key] for key in PLAN_PROVENANCE_KEYS if key in source}
            row = {
                **provenance,
                "symbol": submitted_intent.symbol,
                "ticker": submitted_intent.symbol,
                "side": submitted_intent.side,
                "qty": submitted_intent.shares,
                "shares": submitted_intent.shares,
                "limit_price": submitted_intent.price,
                "expected_price": submitted_intent.price,
                "notional": submitted_intent.notional,
                "client_order_id": client_order_id,
                "status": "REJECTED",
                "error": str(exc),
                "submitted_order_type": submitted_order_type,
                "order_type_submitted": submitted_order_type,
                "source_reason": submitted_intent.reason,
                **policy,
            }
            self.submitted_rows.append(row)
            return SubmitResult(
                intent=submitted_intent,
                status="REJECTED",
                broker_order_id=None,
                raw={"error": str(exc)},
            )

    def snapshot(self) -> CoreAccountSnapshot:
        account = self.broker.get_account() if hasattr(self.broker, "get_account") else {}
        positions = self.broker.get_positions() if hasattr(self.broker, "get_positions") else []
        return CoreAccountSnapshot(
            account=dict(account or {}),
            holdings=_holdings_frame_from_broker_positions(positions),
            raw={"snapshot_source": "live_pilot_adapter"},
        )

    def _settlement_reflection(
        self,
        *,
        account: Mapping[str, Any],
        expected_freed: float,
    ) -> dict[str, Any]:
        required_delta = max(0.0, float(expected_freed or 0.0)) * LIVE_PILOT_SETTLEMENT_REFLECTION_FACTOR
        pre_buying_power = _finite_float(self.pre_sell_account.get("buying_power"))
        post_buying_power = _finite_float(account.get("buying_power"))
        pre_cash = _finite_float(self.pre_sell_account.get("cash"))
        post_cash = _finite_float(account.get("cash"))
        tolerance = LIVE_PILOT_SETTLEMENT_DOLLAR_TOLERANCE
        bp_reflected = (
            pre_buying_power is not None
            and post_buying_power is not None
            and post_buying_power + tolerance >= pre_buying_power + required_delta
        )
        cash_reflected = (
            pre_cash is not None
            and post_cash is not None
            and post_cash + tolerance >= pre_cash + required_delta
        )
        return {
            "expected_freed_proceeds": float(expected_freed or 0.0),
            "required_reflected_delta": float(required_delta),
            "reflection_factor": LIVE_PILOT_SETTLEMENT_REFLECTION_FACTOR,
            "dollar_tolerance": tolerance,
            "pre_sell_buying_power": pre_buying_power,
            "post_sell_buying_power": post_buying_power,
            "pre_sell_cash": pre_cash,
            "post_sell_cash": post_cash,
            "buying_power_reflected": bool(bp_reflected),
            "cash_reflected": bool(cash_reflected),
            # Fail closed: live buys require both cash and buying_power to reflect
            # at least 95% of submitted sell notional before rebudget runs.
            "settlement_reflected": bool(bp_reflected and cash_reflected and expected_freed > 0.0),
        }

    def wait_or_refresh(self, submitted_sells: list[SubmitResult] | tuple[SubmitResult, ...]) -> CoreAccountSnapshot:
        timeout = max(0.0, float(_safe_float(self.env.get(LIVE_PILOT_SETTLEMENT_TIMEOUT_ENV)) or 0.0))
        poll = max(0.0, float(_safe_float(self.env.get(LIVE_PILOT_SETTLEMENT_POLL_ENV)) or 0.0))
        deadline = time.monotonic() + timeout
        expected_freed = float(
            sum(
                max(0.0, float(getattr(result.intent, "notional", 0.0) or 0.0))
                for result in submitted_sells or []
            )
        )
        sell_records: list[dict[str, Any]] = []
        pending: list[str] = []
        snapshot = self.snapshot()
        settlement_reflection = self._settlement_reflection(
            account=snapshot.account,
            expected_freed=expected_freed,
        )
        while True:
            sell_records = []
            pending = []
            for result in submitted_sells or []:
                order_id = str(result.broker_order_id or "").strip()
                broker_order: Mapping[str, Any] = result.raw if isinstance(result.raw, Mapping) else {}
                if order_id and hasattr(self.broker, "get_order"):
                    refreshed = self.broker.get_order(order_id)
                    if isinstance(refreshed, Mapping):
                        broker_order = refreshed
                status = str(broker_order.get("status") or result.status or "").strip()
                bucket = _status_bucket(status)
                if bucket != "filled":
                    pending.append(order_id or result.intent.client_order_id or result.intent.symbol)
                filled_qty = _safe_float(
                    broker_order.get("filled_qty")
                    or broker_order.get("filled_quantity")
                    or result.filled_qty
                )
                fill_price = _safe_float(
                    broker_order.get("filled_avg_price")
                    or broker_order.get("avg_fill_price")
                    or result.intent.price
                )
                filled_notional = (
                    float(filled_qty) * float(fill_price)
                    if filled_qty is not None and fill_price is not None
                    else 0.0
                )
                sell_records.append(
                    {
                        "symbol": result.intent.symbol,
                        "broker_order_id": order_id or None,
                        "status": status,
                        "filled_qty": filled_qty,
                        "filled_notional": filled_notional,
                    }
                )
            snapshot = self.snapshot()
            settlement_reflection = self._settlement_reflection(
                account=snapshot.account,
                expected_freed=expected_freed,
            )
            if (
                not pending and settlement_reflection["settlement_reflected"]
            ) or time.monotonic() >= deadline:
                break
            time.sleep(poll)
        confirmed_proceeds = float(sum(float(row.get("filled_notional") or 0.0) for row in sell_records))
        raw = dict(snapshot.raw or {})
        raw["sell_fill_meta"] = {
            "sell_orders": sell_records,
            "confirmed_sell_proceeds": confirmed_proceeds,
            "expected_freed_proceeds": expected_freed,
            "settlement_reflection": dict(settlement_reflection),
            "pending_sell_order_ids": list(pending),
            "fill_model": "broker_refresh_until_settled",
        }
        raw["pending_sell_order_ids"] = list(pending)
        raw["sell_phase_allows_buy"] = bool(
            not pending and settlement_reflection["settlement_reflected"]
        )
        if pending:
            raw["sell_phase_block_reason"] = LIVE_PILOT_SELL_SETTLEMENT_TIMEOUT
        elif not settlement_reflection["settlement_reflected"]:
            raw["sell_phase_block_reason"] = LIVE_PILOT_SELL_SETTLEMENT_CASH_NOT_REFLECTED
        return CoreAccountSnapshot(account=snapshot.account, holdings=snapshot.holdings, raw=raw)


def _split_execution_frame(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if frame is None or frame.empty or "side" not in frame.columns:
        empty = pd.DataFrame(columns=list(frame.columns) if frame is not None else [])
        return empty.copy(), empty.copy()
    sides = frame["side"].astype(str).str.upper()
    return frame[sides.isin({"SELL", "CLOSE", "REDUCE"})].copy(), frame[sides.eq("BUY")].copy()


def _limit_planning_buy_orders(frame: pd.DataFrame, *, max_buy_orders: int | None) -> pd.DataFrame:
    if frame is None or frame.empty or max_buy_orders is None or "side" not in frame.columns:
        return frame
    limit = max(0, int(max_buy_orders))
    sides = frame["side"].astype(str).str.upper()
    sells = frame[~sides.eq("BUY")].copy()
    buys = frame[sides.eq("BUY")].copy()
    if len(buys) <= limit:
        return frame
    return pd.concat([sells, buys.head(limit)], ignore_index=True, sort=False).reindex(columns=frame.columns)


def _transition_artifact_from_core(
    *,
    result: Any | None,
    raw_trades: pd.DataFrame | None,
    executable_trades: pd.DataFrame | None,
    generated_at: str,
) -> dict[str, Any]:
    sell_frame, buy_frame = _split_execution_frame(executable_trades)
    final_buys = getattr(result, "rebuilt_buy_trades", None) if result is not None else buy_frame
    final_sells = getattr(result, "sell_trades", None) if result is not None else sell_frame
    sell_orders = _trade_frame_orders(final_sells)
    buy_orders = _trade_frame_orders(final_buys)
    return {
        "schema_version": "caerus.execution_core_transition_plan.v1",
        "generated_at": generated_at,
        "blocked": False,
        "block_reason": None,
        "holdings_to_sell": [row["symbol"] for row in sell_orders],
        "holdings_to_increase": [row["symbol"] for row in buy_orders],
        "holdings_to_keep": [],
        "holdings_to_reduce": [],
        "sell_orders_intended": sell_orders,
        "buy_orders_intended": buy_orders,
        "raw_trades": _trade_frame_orders(raw_trades if raw_trades is not None else pd.DataFrame()),
        "executable_trades": _trade_frame_orders(executable_trades if executable_trades is not None else pd.DataFrame()),
        "rebudget_skipped": list(getattr(result, "rebudget_skipped", []) or []) if result is not None else [],
        "diagnostics": {
            "post_sell_budget": dict(getattr(result, "post_sell_budget_meta", {}) or {}) if result is not None else {},
            "rebudget": dict(getattr(result, "rebudget_meta", {}) or {}) if result is not None else {},
            "capital_budget": dict(getattr(result, "capital_budget", {}) or {}) if result is not None else {},
            "sell_fill_meta": dict(getattr(result, "sell_fill_meta", {}) or {}) if result is not None else {},
        },
    }


def _write_blocked_transition_artifact(
    *,
    run_root: Path,
    reason: str,
    diagnostics: Mapping[str, Any] | None = None,
) -> None:
    _write_json(
        run_root / "live_pilot_transition_plan.json",
        {
            "schema_version": "caerus.execution_core_transition_plan.v1",
            "generated_at": _now_utc(),
            "blocked": True,
            "block_reason": reason,
            "holdings_to_sell": [],
            "holdings_to_increase": [],
            "holdings_to_keep": [],
            "holdings_to_reduce": [],
            "sell_orders_intended": [],
            "buy_orders_intended": [],
            "diagnostics": dict(diagnostics or {}),
        },
    )


def _capital_gate_from_planning(
    *,
    pre_snapshot: Mapping[str, Any],
    capital_budget: Mapping[str, Any],
    sell_trades: pd.DataFrame,
    approved_cap_usd: float | None,
    block_reason: str | None = None,
) -> dict[str, Any]:
    positions_before = _active_live_positions(pre_snapshot.get("positions") or [])
    open_orders_before = [
        _open_order_public_row(order)
        for order in (pre_snapshot.get("open_orders") or [])
        if isinstance(order, Mapping)
    ]
    return {
        "schema_version": "live_pilot_capital_gate.v1",
        "generated_at": _now_utc(),
        "decision": "BLOCKED" if block_reason else "ALLOWED",
        "block_reason": block_reason,
        "live_positions_before": positions_before,
        "live_open_orders_before": open_orders_before,
        "live_buying_power_before": capital_budget.get("broker_buying_power_at_planning"),
        "approved_cap_usd": _safe_float(approved_cap_usd),
        "required_sell_count": int(len(sell_trades) if sell_trades is not None else 0),
        "sell_first_supported": LIVE_PILOT_SELL_FIRST_SUPPORTED,
        "rebudget_after_sell_supported": LIVE_PILOT_REBUDGET_AFTER_SELL_SUPPORTED,
        "strategy_allocation_cap_usd": capital_budget.get("requested_buy_notional") or None,
        "planned_buy_notional_usd": capital_budget.get("requested_buy_notional"),
        "tradable_capital_usd": (capital_budget.get("reserve_cash_policy") or {}).get("available_for_buys"),
        "buy_block_reason": block_reason,
        "broker_orders_submitted": 0,
    }


def _intended_from_validation(
    *,
    orders: list[Any],
    source_trades: list[Mapping[str, Any]],
    output_root: Path | str,
    run_id: str,
) -> list[dict[str, Any]]:
    provenance = _plan_provenance_by_client_id(orders, source_trades)
    return [
        {
            **provenance.get(order.client_order_id, {}),
            **order.to_dict(),
            **_entry_policy_for_order(order, output_root=output_root, run_id=run_id),
        }
        for order in orders
    ]


def _intended_from_submitted(submitted: list[dict[str, Any]]) -> list[dict[str, Any]]:
    omitted = {"status", "order", "error", "fill_price", "slippage_bps", "slippage_warning"}
    return [{key: value for key, value in row.items() if key not in omitted} for row in submitted]


def _run_live_pilot_core_path(
    *,
    plan: Mapping[str, Any],
    broker: Any,
    env: Mapping[str, str],
    gate: Any,
    preflight: Mapping[str, Any],
    pre_snapshot: Mapping[str, Any],
    run_root: Path,
    run_id: str,
    trade_date: str,
    output_root: Path | str,
    now_et: dt.datetime | None,
) -> dict[str, Any]:
    planning_equity_cap = _finite_float(env.get("CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP"))
    request, malformed = _build_core_request(
        pre_snapshot=pre_snapshot,
        plan=plan,
        run_id=run_id,
        planning_equity_cap=planning_equity_cap,
    )
    if request is None:
        _write_blocked_transition_artifact(
            run_root=run_root,
            reason=LIVE_PILOT_EQUITY_UNAVAILABLE,
            diagnostics={"equity_unavailable": True},
        )
        capital_gate = _capital_gate_from_planning(
            pre_snapshot=pre_snapshot,
            capital_budget={},
            sell_trades=pd.DataFrame(),
            approved_cap_usd=gate.capital_cap_usd,
            block_reason=LIVE_PILOT_EQUITY_UNAVAILABLE,
        )
        return _write_blocked_artifacts(
            run_root=run_root,
            run_id=run_id,
            trade_date=trade_date,
            env=env,
            reason_code=LIVE_PILOT_EQUITY_UNAVAILABLE,
            operator_action="Broker account equity was unavailable; block before any live pilot submission.",
            preflight=preflight,
            capital_gate=capital_gate,
        )
    # Minimum per-trade notional floor. Defaults to 100.0 (unchanged legacy behavior).
    # For a small live account, a full rebalance to a many-name target needs a lower
    # floor so the weight-priority rebudget can fill the top names it can afford instead
    # of skipping every sub-$100 buy and parking proceeds in cash. Operator-controlled.
    min_trade_usd = _finite_float(env.get("CAERUS_LIVE_PILOT_MIN_TRADE_USD"))
    if min_trade_usd is None or min_trade_usd <= 0.0:
        min_trade_usd = 100.0
    config = live_pilot_execution_config(
        approved_cap_usd=gate.capital_cap_usd,
        allow_fractional=str(env.get("CAERUS_LIVE_PILOT_ALLOW_FRACTIONAL") or "").strip().lower()
        in {"1", "true", "yes", "y", "on"},
        max_orders=int(gate.max_orders or 1),
        min_trade_usd=float(min_trade_usd),
        ledger_output_root=output_root,
        ledger_enabled=not bool(gate.dry_run),
    )
    if malformed and str(config.constraints.malformed_holding_policy) == "fail_closed":
        _write_blocked_transition_artifact(
            run_root=run_root,
            reason=LIVE_PILOT_BLOCKED_EXISTING_POSITIONS_REQUIRE_ROTATION,
            diagnostics={
                "unpriceable_holding_symbol": malformed[0].get("symbol"),
                "unpriceable_holding_reason": malformed[0].get("reason"),
            },
        )
        capital_gate = _capital_gate_from_planning(
            pre_snapshot=pre_snapshot,
            capital_budget={},
            sell_trades=pd.DataFrame(),
            approved_cap_usd=gate.capital_cap_usd,
            block_reason=LIVE_PILOT_MALFORMED_HOLDING,
        )
        return _write_blocked_artifacts(
            run_root=run_root,
            run_id=run_id,
            trade_date=trade_date,
            env=env,
            reason_code=LIVE_PILOT_BLOCKED_EXISTING_POSITIONS_REQUIRE_ROTATION,
            operator_action=f"Malformed live holding blocks execution: {malformed[0].get('reason')}",
            preflight=preflight,
            capital_gate=capital_gate,
        )
    if (
        config.constraints.equity_collar_max_usd is not None
        and request.total_equity > float(config.constraints.equity_collar_max_usd)
    ):
        _write_blocked_transition_artifact(
            run_root=run_root,
            reason=LIVE_PILOT_EQUITY_EXCEEDS_CAP_REGIME,
            diagnostics={
                "equity_usd": float(request.total_equity),
                "equity_cap_regime_ceiling_usd": float(config.constraints.equity_collar_max_usd),
            },
        )
        capital_gate = _capital_gate_from_planning(
            pre_snapshot=pre_snapshot,
            capital_budget={},
            sell_trades=pd.DataFrame(),
            approved_cap_usd=gate.capital_cap_usd,
            block_reason=LIVE_PILOT_EQUITY_EXCEEDS_CAP_REGIME,
        )
        return _write_blocked_artifacts(
            run_root=run_root,
            run_id=run_id,
            trade_date=trade_date,
            env=env,
            reason_code=LIVE_PILOT_EQUITY_EXCEEDS_CAP_REGIME,
            operator_action="Account equity exceeds the approved live-pilot cap regime.",
            preflight=preflight,
            capital_gate=capital_gate,
        )
    raw_bp = _finite_float(request.planning_account.get("buying_power"))
    if raw_bp is not None and raw_bp <= 0.0:
        _write_blocked_transition_artifact(
            run_root=run_root,
            reason=LIVE_PILOT_BLOCKED_BUYING_POWER_UNAVAILABLE,
            diagnostics={"buying_power_nonpositive": True},
        )
        capital_gate = _capital_gate_from_planning(
            pre_snapshot=pre_snapshot,
            capital_budget={"broker_buying_power_at_planning": raw_bp},
            sell_trades=pd.DataFrame(),
            approved_cap_usd=gate.capital_cap_usd,
            block_reason=LIVE_PILOT_BLOCKED_BUYING_POWER_UNAVAILABLE,
        )
        return _write_blocked_artifacts(
            run_root=run_root,
            run_id=run_id,
            trade_date=trade_date,
            env=env,
            reason_code=LIVE_PILOT_BLOCKED_BUYING_POWER_UNAVAILABLE,
            operator_action="Live broker buying_power is non-positive; block before any submit.",
            preflight=preflight,
            capital_gate=capital_gate,
        )
    over_cap_symbol, full_need = _max_incremental_need(request)
    if (
        config.capital.approved_cap_usd is not None
        and str(config.capital.over_cap_behavior or "").lower() == "block"
        and full_need > float(config.capital.approved_cap_usd) + 1e-9
    ):
        capital_gate = _capital_gate_from_planning(
            pre_snapshot=pre_snapshot,
            capital_budget={
                "requested_buy_notional": full_need,
                "broker_buying_power_at_planning": raw_bp,
                "reserve_cash_policy": {"available_for_buys": gate.capital_cap_usd},
            },
            sell_trades=pd.DataFrame(),
            approved_cap_usd=gate.capital_cap_usd,
            block_reason=LIVE_PILOT_TOTAL_NOTIONAL_EXCEEDS_CAP,
        )
        _write_json(
            run_root / "live_pilot_transition_plan.json",
            {
                "schema_version": "caerus.execution_core_transition_plan.v1",
                "generated_at": _now_utc(),
                "blocked": True,
                "block_reason": LIVE_PILOT_TOTAL_NOTIONAL_EXCEEDS_CAP,
                "diagnostics": {
                    "over_cap_intent": True,
                    "over_cap_symbol": over_cap_symbol,
                    "over_cap_full_need_usd": full_need,
                },
                "sell_orders_intended": [],
                "buy_orders_intended": [],
            },
        )
        return _write_blocked_artifacts(
            run_root=run_root,
            run_id=run_id,
            trade_date=trade_date,
            env=env,
            reason_code=LIVE_PILOT_TOTAL_NOTIONAL_EXCEEDS_CAP,
            operator_action="Live-pilot full incremental need exceeds the approved cap; block instead of clipping.",
            preflight=preflight,
            capital_gate=capital_gate,
        )

    raw_trades, _trade_meta = compute_transition_trades(request=request, config=config)
    capital_trades, capital_budget, executable_trades, _execution_filter = apply_capital_budget_and_execution_filter(
        trades=raw_trades,
        planning_account=request.planning_account,
        config=config,
    )
    executable_trades = _limit_planning_buy_orders(
        executable_trades,
        max_buy_orders=int(gate.max_orders or 1),
    )
    sell_trades, _buy_trades = _split_execution_frame(executable_trades)
    capital_gate = _capital_gate_from_planning(
        pre_snapshot=pre_snapshot,
        capital_budget=capital_budget,
        sell_trades=sell_trades,
        approved_cap_usd=gate.capital_cap_usd,
    )
    _write_json(
        run_root / "live_pilot_transition_plan.json",
        _transition_artifact_from_core(
            result=None,
            raw_trades=raw_trades,
            executable_trades=executable_trades,
            generated_at=_now_utc(),
        ),
    )
    source_trades = _core_rows_from_frame(executable_trades, plan=plan)
    if not source_trades:
        reason_code = (
            LIVE_PILOT_BLOCKED_INSUFFICIENT_BUYING_POWER
            if float(capital_budget.get("requested_buy_notional") or 0.0) > 0.0
            or full_need + 1e-9 >= float(config.orders.min_trade_usd)
            else "live_pilot_transition_no_actionable_order"
        )
        capital_gate["planned_buy_notional_usd"] = max(
            float(capital_gate.get("planned_buy_notional_usd") or 0.0),
            float(full_need or 0.0),
        )
        capital_gate["decision"] = "BLOCKED"
        capital_gate["block_reason"] = reason_code
        capital_gate["buy_block_reason"] = reason_code
        return _write_blocked_artifacts(
            run_root=run_root,
            run_id=run_id,
            trade_date=trade_date,
            env=env,
            reason_code=reason_code,
            operator_action="Execution core produced no actionable live-pilot order.",
            preflight=preflight,
            capital_gate=capital_gate,
        )
    plan_validation = validate_live_pilot_plan(
        source_trades,
        env=env,
        capital_cap_usd=float(gate.capital_cap_usd or 0.0),
        max_orders=int(gate.max_orders or 0),
        run_id=run_id,
    )
    intended = _intended_from_validation(
        orders=plan_validation.orders,
        source_trades=source_trades,
        output_root=output_root,
        run_id=run_id,
    )
    intended_payload = plan_validation.to_dict()
    intended_payload["orders"] = intended
    intended_payload.update(_entry_policy_summary(intended=intended, submitted=[], dry_run=bool(gate.dry_run)))
    _write_json(run_root / "live_pilot_orders_intended.json", intended_payload)
    _write_json(
        run_root / "live_pilot_entry_attempt_history.json",
        {
            "schema_version": "live_pilot_entry_attempt_history.v1",
            "generated_at": _now_utc(),
            "run_id": run_id,
            "trade_date": trade_date,
            "orders": intended,
        },
    )
    if plan_validation.status != "PASS":
        return _write_blocked_artifacts(
            run_root=run_root,
            run_id=run_id,
            trade_date=trade_date,
            env=env,
            reason_code=";".join(plan_validation.reason_codes),
            operator_action=plan_validation.operator_action,
            preflight=preflight,
            intended=intended,
            capital_gate=capital_gate,
        )
    _write_json(run_root / "live_pilot_capital_gate.json", capital_gate)

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
            env=env,
            reason_code=";".join(asset_errors),
            operator_action="Unsupported or non-tradable assets blocked before submission.",
            preflight=preflight,
            intended=intended,
            capital_gate=capital_gate,
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
            env=env,
            reason_code=str(open_order_check.get("status") or "BLOCKED_OPEN_PILOT_ORDER"),
            operator_action=str(open_order_check.get("operator_action") or "Open pilot order blocks duplicate submission."),
            preflight=preflight,
            intended=intended,
            open_order_check=open_order_check,
            capital_gate=capital_gate,
        )

    market_hours_gate = _normal_market_hours_gate(now_et=now_et)
    _write_json(run_root / "live_pilot_market_hours_gate.json", market_hours_gate)
    if not gate.dry_run and market_hours_gate.get("status") != "PASS":
        return _write_blocked_artifacts(
            run_root=run_root,
            run_id=run_id,
            trade_date=trade_date,
            env=env,
            reason_code=f"live_pilot_market_closed:{market_hours_gate.get('reason_code')}",
            operator_action=str(market_hours_gate.get("operator_action") or "Market is closed."),
            preflight=preflight,
            intended=intended,
            capital_gate=capital_gate,
        )

    submitted: list[dict[str, Any]]
    submit_errors: list[str] = []
    if gate.dry_run:
        submitted = [
            {**row, "status": "DRY_RUN_NOT_SUBMITTED", "order": None}
            for row in intended
        ]
        result = SimpleNamespace(
            capital_budget=capital_budget,
            post_sell_budget_meta={},
            sell_trades=sell_trades,
            rebuilt_buy_trades=_split_execution_frame(executable_trades)[1],
            submitted_sells=(),
            submitted_buys=(),
            rebudget_skipped=[],
            rebudget_meta={},
        )
    else:
        adapter = LivePilotCoreAdapter(
            broker=broker,
            env=env,
            run_root=run_root,
            output_root=output_root,
            run_id=run_id,
            pre_sell_account=request.planning_account,
            source_plan=plan,
        )
        result = execute_lifecycle(request=request, adapter=adapter, config=config)
        submitted = list(adapter.submitted_rows)
        submit_errors = list(adapter.submit_errors)
        intended = _intended_from_submitted(submitted)
        _write_json(
            run_root / "live_pilot_orders_intended.json",
            {
                **plan_validation.to_dict(),
                "orders": intended,
                **_entry_policy_summary(intended=intended, submitted=[], dry_run=False),
            },
        )
        capital_gate = _build_live_pilot_capital_gate(
            result=result,
            pre_snapshot=pre_snapshot,
            approved_cap_usd=gate.capital_cap_usd,
        )
        _write_json(run_root / "live_pilot_capital_gate.json", capital_gate)
        _write_json(
            run_root / "live_pilot_transition_plan.json",
            _transition_artifact_from_core(
                result=result,
                raw_trades=result.raw_trades,
                executable_trades=result.executable_trades,
                generated_at=_now_utc(),
            ),
        )

    _write_json(run_root / "live_pilot_orders_submitted.json", {"orders": submitted})
    try:
        post_snapshot = _broker_snapshot(broker)
    except Exception as exc:
        post_snapshot = {"captured_at": _now_utc(), "status": "SNAPSHOT_FAILED", "error": str(exc)}
        submit_errors.append(f"post_snapshot_failed:{exc}")
    _write_json(run_root / "live_pilot_broker_snapshot_post.json", post_snapshot)

    reconciliation = _reconcile(dry_run=bool(gate.dry_run), intended=intended, submitted=submitted, errors=submit_errors)
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
    entry_summary = _entry_policy_summary(intended=intended, submitted=submitted, dry_run=bool(gate.dry_run))
    evidence_metrics.update(entry_summary)
    _write_json(run_root / "live_pilot_evidence_metrics.json", evidence_metrics)
    _write_json(
        run_root / "live_pilot_capital_usage.json",
        {
            "schema_version": "live_pilot_capital_usage.v1",
            "capital_cap_usd": gate.capital_cap_usd,
            "planned_notional_usd": plan_validation.total_notional,
            "submitted_notional_usd": 0.0
            if gate.dry_run
            else sum(float(row.get("notional") or 0.0) for row in submitted if row.get("status") != "REJECTED"),
            "filled_notional_usd": evidence_metrics.get("filled_notional_usd"),
            "cash_deployment_rate": evidence_metrics.get("cash_deployment_rate"),
            "dry_run": bool(gate.dry_run),
            **_capital_gate_report_fields(capital_gate),
        },
    )
    _cap_pv = _finite_float(
        (pre_snapshot.get("account") or {}).get("portfolio_value")
        or (pre_snapshot.get("account") or {}).get("equity")
    )
    _resolved_cap, _cap_source = resolve_dynamic_cap(_cap_pv, env)
    write_live_pilot_gate_state(
        run_root=run_root,
        run_id=run_id,
        trade_date=trade_date,
        env=env,
        repo_root=REPO_ROOT,
        decision="ALLOWED",
        block_reason=None,
        broker_orders_submitted=0 if gate.dry_run else len(submitted),
        base_url=str(preflight.get("base_url") or ""),
        resolved_cap_usd=gate.capital_cap_usd,
        cap_source_override=_cap_source,
        portfolio_value_usd=_cap_pv,
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
        # Honest mode label (PAPER for the unified paper lane, LIVE_PILOT for live).
        "mode": str(getattr(gate, "requested_mode", "") or LIVE_PILOT_MODE).upper(),
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
        # Honest mode label (PAPER for the unified paper lane, LIVE_PILOT for live).
        "mode": str(preflight.get("requested_mode") or LIVE_PILOT_MODE).upper(),
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

    if broker is None:
        alpaca_paper_raw = str(environ.get("ALPACA_PAPER") or "1").strip().lower()
        broker_paper = alpaca_paper_raw not in {"0", "false", "no", "n", "off"}
        base_url = str(environ.get("ALPACA_BASE_URL") or "").strip()
    else:
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
        # Honest mode label: the unified PAPER lane runs this same engine with
        # MODE=paper; recording LIVE_PILOT for paper runs would be misleading.
        # Schema is unchanged (same key, canonical mode value).
        "mode": gate.requested_mode,
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

    if broker is None:
        broker = AlpacaBroker.from_env()
        broker_paper = bool(getattr(broker, "paper", broker_paper))
        base_url = str(getattr(broker, "base_url", "") or base_url)

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
    # Account-pin gate applies to the LIVE lane only. A paper broker on the paper
    # host already passed the gate above via the paper branch, which short-circuits
    # BEFORE the account-match logic (account_id_match stays None) — enforcing it
    # here would fail-closed every unified paper-lane run for the wrong reason.
    # Do NOT revert this to an unconditional check "because the old code did it":
    # the old unconditional `if not account_gate.account_id_match:` was latently
    # broken for paper (account_id_match=None -> `not None` == True -> every paper
    # run BLOCKED). Paper accounts carry no pinned id/hash by design; the live
    # path is unchanged and still blocks on a missing broker id or an id/hash
    # mismatch.
    if not broker_paper:
        # Defense-in-depth: a non-paper broker MUST be pointed at the canonical
        # LIVE Alpaca host before the account pin is consulted. A misconfigured
        # broker adapter (wrong host while reporting paper=False) fails closed
        # here instead of falling through to the pin check.
        if not _is_live_host(base_url):
            return _write_blocked_artifacts(
                run_root=run_root,
                run_id=run_id,
                trade_date=trade_date,
                env=environ,
                reason_code="live_pilot_requires_live_alpaca_endpoint",
                operator_action=(
                    "Non-paper broker is not pointed at the live Alpaca host "
                    f"(base_url={base_url!r}); refusing to run the account-pin check."
                ),
                preflight=preflight,
            )
        print(
            f"live_pilot_account_pin_check: enforcing pinned account on live endpoint base_url={base_url}",
            file=sys.stderr,
        )
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

    # Resolve the capital cap dynamically from the account's current portfolio value
    # (fully dynamic ceiling; an optional CAERUS_LIVE_PILOT_CAPITAL_CAP only tightens
    # it). Fail closed if the account value is unknown and no override is set — a run
    # must never size against an unknown account.
    portfolio_value = _finite_float(
        account_public.get("portfolio_value") or account_public.get("equity")
    )
    resolved_cap, cap_source = resolve_dynamic_cap(portfolio_value, environ)
    if resolved_cap is None:
        return _write_blocked_artifacts(
            run_root=run_root,
            run_id=run_id,
            trade_date=trade_date,
            env=environ,
            reason_code="live_pilot_capital_cap_unresolved",
            operator_action=(
                "Live capital cap could not be resolved: broker portfolio value was "
                "unavailable and no CAERUS_LIVE_PILOT_CAPITAL_CAP override is set."
            ),
            preflight=preflight,
        )
    gate = dataclasses.replace(gate, capital_cap_usd=resolved_cap)

    return _run_live_pilot_core_path(
        plan=plan,
        broker=broker,
        env=environ,
        gate=gate,
        preflight=preflight,
        pre_snapshot=pre_snapshot,
        run_root=run_root,
        run_id=run_id,
        trade_date=trade_date,
        output_root=output_root,
        now_et=now_et,
    )


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

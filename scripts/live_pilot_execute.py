from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import math
import numbers
import os
import sys
import time
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from brokers.alpaca_broker import AlpacaBroker, alpaca_client_order_id
from core.broker_retry_policy import is_retryable_broker_read_error
from core.live_trade_ledger import record_live_order
from core.live_pilot_guardrails import (
    LIVE_PILOT_MODE,
    PAPER_MODE,
    LIVE_PILOT_MAX_SLIPPAGE_BPS_ENV,
    _is_live_host,
    _is_paper_host,
    account_id_hash,
    build_live_pilot_gate_result,
    expected_account_matches,
    resolve_dynamic_cap,
    validate_live_pilot_asset,
    validate_live_pilot_plan,
    validate_live_pilot_submission_guardrails,
)
from core.live_pilot_gate_state import write_live_pilot_gate_state
from core.settled_cash import (
    _fill_date as _settled_cash_fill_date,
    compute_settled_cash,
    detect_gfv_risky_sells,
    settlement_date,
)
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
from paper.trading_calendar import ET_TZ, market_session_status, prev_trading_day
from paper.run_manager import generate_run_id, safe_write_text


class _DryRunEconomicNotApplicable(Exception):
    """Internal control flow: dry runs validate state but create no fill economics."""


def _now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real):
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def _write_json(path: Path, payload: Mapping[str, Any]) -> Path:
    safe_write_text(
        path,
        json.dumps(
            _json_safe(dict(payload)),
            indent=2,
            sort_keys=True,
            allow_nan=False,
        ) + "\n",
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
# Bounded exponential backoff for the sell-settlement wait (2026-07-09/10 incident
# hardening). The wait refreshes broker order status + account cash between
# attempts; delays double from the base up to the per-sleep ceiling. An explicit
# CAERUS_LIVE_PILOT_SETTLEMENT_TIMEOUT_SECONDS additionally caps total wall time
# (setting it to 0 preserves the legacy single-pass fail-fast behavior).
LIVE_PILOT_SETTLEMENT_MAX_ATTEMPTS_ENV = "CAERUS_LIVE_PILOT_SETTLEMENT_MAX_ATTEMPTS"
LIVE_PILOT_SETTLEMENT_BASE_DELAY_ENV = "CAERUS_LIVE_PILOT_SETTLEMENT_BASE_DELAY_SECONDS"
LIVE_PILOT_SETTLEMENT_MAX_DELAY_ENV = "CAERUS_LIVE_PILOT_SETTLEMENT_MAX_DELAY_SECONDS"
LIVE_PILOT_SETTLEMENT_DEFAULT_MAX_ATTEMPTS = 5
LIVE_PILOT_SETTLEMENT_DEFAULT_BASE_DELAY_SECONDS = 2.0
LIVE_PILOT_SETTLEMENT_DEFAULT_MAX_DELAY_SECONDS = 30.0
LIVE_PILOT_SETTLEMENT_REFLECTION_FACTOR = 0.95
LIVE_PILOT_SETTLEMENT_DOLLAR_TOLERANCE = 0.01
PAPER_CONFIRMED_PROCEEDS_REUSE_ENV = "CAERUS_PAPER_REUSE_CONFIRMED_SELL_PROCEEDS"

# Settled-cash / GFV guard (PRE_ARM_SWEEP Blocker #2). US equities settle T+1; Alpaca
# credits sale proceeds to cash immediately though unsettled. Live-capital buys remain
# clamped to settled cash (broker cash - unsettled sale proceeds) times a slippage
# buffer. The separately pinned PAPER lane may reuse only current-run confirmed fills.
LIVE_PILOT_BUY_BUFFER_PCT_ENV = "CAERUS_LIVE_PILOT_BUY_BUFFER_PCT"
LIVE_PILOT_BUY_BUFFER_PCT_DEFAULT = 0.98
LIVE_PILOT_SETTLED_CASH_LOOKBACK_ENV = "CAERUS_LIVE_PILOT_SETTLED_CASH_ORDER_LOOKBACK"
LIVE_PILOT_SETTLED_CASH_LOOKBACK_DEFAULT = 100
LIVE_PILOT_GFV_SETTLED_CASH_UNAVAILABLE = "live_pilot_gfv_settled_cash_unavailable"
LIVE_PILOT_GFV_SELL_OF_UNSETTLED_ACQUISITION = "live_pilot_gfv_sell_of_unsettled_acquisition"
LIVE_PILOT_FRACTIONAL_POLICY_MISMATCH = "live_pilot_fractional_policy_mismatch"
LIVE_PILOT_FRACTIONAL_POLICY_INVALID = "live_pilot_fractional_policy_invalid"

_TRUE_POLICY_VALUES = frozenset({"1", "true", "yes", "y", "on"})
_FALSE_POLICY_VALUES = frozenset({"0", "false", "no", "n", "off"})


def _parse_optional_policy_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    text = str(value or "").strip().lower()
    if text in _TRUE_POLICY_VALUES:
        return True
    if text in _FALSE_POLICY_VALUES:
        return False
    return None


def _resolve_fractional_policy(
    plan: Mapping[str, Any],
    env: Mapping[str, str],
) -> tuple[bool, str | None, dict[str, Any]]:
    """Resolve one immutable fractional policy for dry-run and submission.

    Versioned plans produced by the current builder carry ``allow_fractional``.
    That declared value is authoritative. A contradictory runtime override must
    fail closed instead of silently changing target quantities after a clean dry
    run. Legacy plans without the field retain the historical env/default-false
    behavior.
    """

    env_name = "CAERUS_LIVE_PILOT_ALLOW_FRACTIONAL"
    plan_declared = "allow_fractional" in plan
    plan_value = _parse_optional_policy_bool(plan.get("allow_fractional")) if plan_declared else None
    env_raw = env.get(env_name)
    env_declared = env_raw is not None and str(env_raw).strip() != ""
    env_value = _parse_optional_policy_bool(env_raw) if env_declared else None

    metadata: dict[str, Any] = {
        "plan_allow_fractional_declared": plan_declared,
        "plan_allow_fractional": plan_value,
        "runtime_allow_fractional_declared": env_declared,
        "runtime_allow_fractional": env_value,
    }
    if plan_declared and plan_value is None:
        metadata.update(
            {
                "fractional_policy_status": "INVALID",
                "fractional_policy_source": "plan",
                "effective_allow_fractional": None,
            }
        )
        return False, LIVE_PILOT_FRACTIONAL_POLICY_INVALID, metadata
    if env_declared and env_value is None:
        metadata.update(
            {
                "fractional_policy_status": "INVALID",
                "fractional_policy_source": "runtime",
                "effective_allow_fractional": None,
            }
        )
        return False, LIVE_PILOT_FRACTIONAL_POLICY_INVALID, metadata
    if plan_declared and env_declared and plan_value != env_value:
        metadata.update(
            {
                "fractional_policy_status": "MISMATCH",
                "fractional_policy_source": "plan",
                "effective_allow_fractional": plan_value,
            }
        )
        return bool(plan_value), LIVE_PILOT_FRACTIONAL_POLICY_MISMATCH, metadata

    effective = bool(plan_value if plan_declared else env_value if env_declared else False)
    metadata.update(
        {
            "fractional_policy_status": "PASS",
            "fractional_policy_source": "plan" if plan_declared else "runtime_or_default",
            "effective_allow_fractional": effective,
        }
    )
    return effective, None, metadata

# Machine-readable halt-state convergence contract. Every halted run records
# whether the NEXT scheduled run is expected to self-heal (the next plan is
# computed from CURRENT broker positions and every gate re-evaluates fresh; no
# halt latch is persisted anywhere) or whether an operator must act first.
NEXT_RUN_CONVERGES = "converges_next_run"
NEXT_RUN_REQUIRES_MANUAL_ACTION = "requires_manual_action"

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
    "settled_cash_usd",
    "execution_spendable_cash_usd",
    "unsettled_proceeds_usd",
    "buy_buffer_pct",
    "settled_cash_fail_closed",
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


def _buy_buffer_pct(env: Mapping[str, str]) -> float:
    """Slippage/settled-cash buffer for live buys. Safe default 0.98; clamped to (0,1]."""
    raw = _finite_float(env.get(LIVE_PILOT_BUY_BUFFER_PCT_ENV))
    if raw is None or raw <= 0.0 or raw > 1.0:
        return LIVE_PILOT_BUY_BUFFER_PCT_DEFAULT
    return float(raw)


def _settled_cash_lookback(env: Mapping[str, str]) -> int:
    raw = _finite_float(env.get(LIVE_PILOT_SETTLED_CASH_LOOKBACK_ENV))
    if raw is None or raw < 1.0:
        return LIVE_PILOT_SETTLED_CASH_LOOKBACK_DEFAULT
    return int(raw)


def _unsettled_window_after_date(as_of_date: str) -> str | None:
    """Start of the date-bounded order query for the settled-cash recompute.

    A fill on ``prev_trading_day(as_of)`` settles ON ``as_of`` (already settled), so
    every possibly-unsettled fill has fill_date > prev_trading_day(as_of). Querying
    ``after=prev_trading_day`` covers the whole unsettled window with one trading day
    of margin.
    """
    try:
        return prev_trading_day(str(as_of_date))
    except Exception:  # noqa: BLE001 - unparseable date -> unbounded query, page check still guards
        return None


def _fetch_order_history(
    broker: Any, *, limit: int, after: str | None = None
) -> tuple[list[dict[str, Any]] | None, str]:
    """Fetch recent FILLED-inclusive order history for the settled-cash recompute.

    Returns ``(orders, availability)`` where ``orders is None`` fails the guard CLOSED
    (broker cannot report history -> assume cash is fully unsettled). ``availability``
    is a short status string for observability. ``after`` date-bounds the query to the
    unsettled window on brokers that support it (injected test brokers may not; the
    page-full check in ``_settled_cash_context`` still guards truncation either way).
    """
    if not hasattr(broker, "list_orders"):
        return None, "no_broker_support"
    try:
        try:
            if after:
                orders = broker.list_orders(status="all", limit=int(limit), after=after)
                availability = "ok_date_bounded"
            else:
                orders = broker.list_orders(status="all", limit=int(limit))
                availability = "ok"
        except TypeError:
            # Broker without `after` support (stub/test brokers): count-bounded query.
            orders = broker.list_orders(status="all", limit=int(limit))
            availability = "ok_no_after_support"
    except Exception as exc:  # noqa: BLE001 - fail closed on any lookup error
        return None, f"lookup_failed:{exc}"
    if not isinstance(orders, list):
        return None, "non_list_response"
    return [dict(order) for order in orders if isinstance(order, Mapping)], availability


def _order_history_page_truncation_risk(
    orders: list[dict[str, Any]], *, limit: int, as_of_date: str
) -> bool:
    """True when the returned page may have truncated unsettled sells (fail closed).

    A full page (len >= limit) whose OLDEST parseable fill date is still inside the
    unsettled window means older, unreturned orders could also be unsettled — the
    recompute would silently undercount unsettled proceeds. A full page whose oldest
    row is already settled proves the window is fully covered.
    """
    if len(orders) < int(limit):
        return False
    fill_dates = [d for d in (_settled_cash_fill_date(o) for o in orders) if d]
    if not fill_dates:
        return True  # cannot prove coverage -> fail closed
    oldest = min(fill_dates)
    return settlement_date(oldest) > str(as_of_date)


def _settled_cash_context(
    broker: Any,
    *,
    broker_cash: Any,
    as_of_date: str,
    env: Mapping[str, str],
    confirmed_sells: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
) -> Any:
    """Stateless settled-cash recompute from broker truth (fails closed).

    ``confirmed_sells`` are this run's per-order-polled filled sells; they cross-check
    the bulk history for freshness (a lagging bulk read cannot hide their proceeds).
    """
    limit = _settled_cash_lookback(env)
    orders, availability = _fetch_order_history(
        broker,
        limit=limit,
        after=_unsettled_window_after_date(as_of_date),
    )
    if orders is not None and _order_history_page_truncation_risk(
        orders, limit=limit, as_of_date=str(as_of_date)
    ):
        # FAIL CLOSED: the page is full and its oldest row is still unsettled, so
        # unsettled sells may have been truncated off the page.
        orders = None
        availability = "page_full_unsettled_window"
    result = compute_settled_cash(
        broker_cash=broker_cash,
        orders=orders,
        as_of_date=str(as_of_date),
        buy_buffer_pct=_buy_buffer_pct(env),
        orders_available=orders is not None,
        confirmed_sells=confirmed_sells,
    )
    return result, orders, availability


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


def _broker_snapshot(
    broker: Any,
    *,
    fail_on_open_order_lookup: bool = False,
) -> dict[str, Any]:
    account = broker.get_account() if hasattr(broker, "get_account") else {}
    positions = broker.get_positions() if hasattr(broker, "get_positions") else []
    open_orders = _list_open_orders(broker)
    lookup_failure = next(
        (
            order
            for order in open_orders
            if str(order.get("status") or "") == "OPEN_ORDER_LOOKUP_FAILED"
        ),
        None,
    )
    if fail_on_open_order_lookup and lookup_failure is not None:
        raise RuntimeError(
            "paper broker open-order snapshot failed: "
            f"{lookup_failure.get('error') or 'unknown broker lookup error'}"
        )
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
    position_side = str(position.get("side") or "").strip().lower()
    qty = _finite_float(position.get("qty"))
    market_value = _finite_float(position.get("market_value"))
    reasons: list[str] = []
    if not symbol:
        reasons.append("missing_symbol")
    if qty is None or abs(qty) <= 1e-12:
        reasons.append("missing_zero_or_nonfinite_qty")
    elif qty < 0.0:
        # The live pilot is long-only. Never coerce a short broker position into
        # positive sell inventory, which could authorize an accidental short sale.
        reasons.append("negative_qty_not_supported")
    if position_side == "short":
        reasons.append("short_position_not_supported")
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
        if not symbol or qty <= 1e-12 or price <= 0.0:
            continue
        rows.append({"ticker": symbol, "sleeve": "live_pilot", "shares": qty})
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
    approved_package_payload = (
        plan.get("approved_execution_package")
        if isinstance(plan.get("approved_execution_package"), Mapping)
        else None
    )
    if approved_package_payload is not None:
        from authority.pipeline import execution_package_from_dict

        approved_package = execution_package_from_dict(approved_package_payload)
        approved_plan = {
            "target_portfolio": [dict(row) for row in approved_package.approved_target_rows]
        }
        targets, target_prices = _target_rows_from_plan(approved_plan, equity=equity)
    else:
        approved_package = None
        targets, target_prices = _target_rows_from_plan(plan, equity=equity)
    prices = holding_prices.copy()
    for symbol, price in target_prices.items():
        prices.loc[symbol] = float(price)
    # Carry the risk-adjusted cash target through execution so live matches paper.
    # Paper holds this cash back (circuit breaker, sector trim); a legacy plan without
    # the field defaults to 0.0 (prior behavior).
    cash_target_weight = (
        float(approved_package.approved_cash_weight)
        if approved_package is not None
        else _finite_float(plan.get("cash_target_weight"))
    )
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
        approved_execution_package=approved_package_payload,
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


def _sell_inventory_from_request(request: ExecutionRequest) -> dict[str, float]:
    """Return immutable positive long inventory used to authorize live SELLs."""
    inventory: dict[str, float] = {}
    if request.holdings is None or request.holdings.empty:
        return inventory
    for _, row in request.holdings.iterrows():
        symbol = str(row.get("ticker") or "").strip().upper()
        qty = _finite_float(row.get("shares"))
        if symbol and qty is not None and qty > 0.0:
            inventory[symbol] = inventory.get(symbol, 0.0) + float(qty)
    return inventory


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
        exact_row = {
                "ticker": symbol,
                "symbol": symbol,
                "side": str(row.get("side") or "").strip().upper(),
                "shares": float(row.get("shares") or 0.0),
                "qty": float(row.get("shares") or 0.0),
                "price": float(row.get("price") or 0.0),
                "expected_price": float(row.get("price") or 0.0),
                "cap_enforcement_price": float(row.get("price") or 0.0),
                "notional": float(row.get("notional") or 0.0),
                "order_type": order_type,
                "source_reason": row.get("reason"),
                **{key: source[key] for key in PLAN_PROVENANCE_KEYS if key in source},
            }
        if order_type == "limit":
            exact_row["limit_price"] = float(row.get("price") or 0.0)
        rows.append(exact_row)
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


def _settled_cash_gate_fields(capital_budget: Mapping[str, Any] | None) -> dict[str, Any]:
    """Surface the settled-cash guard into the capital gate for the dry-run proof."""
    guard = dict((capital_budget or {}).get("settled_cash_guard") or {})
    if not guard:
        return {}
    settled = guard.get("settled_cash")
    broker_cash = _safe_float((capital_budget or {}).get("broker_cash_at_planning"))
    unsettled = None
    if settled is not None and broker_cash is not None:
        unsettled = max(0.0, float(broker_cash) - float(settled))
    return {
        "settled_cash_usd": settled,
        "execution_spendable_cash_usd": guard.get("execution_spendable_cash"),
        "unsettled_proceeds_usd": unsettled,
        "buy_buffer_pct": guard.get("buy_buffer_pct"),
        "settled_cash_fail_closed": bool(guard.get("settled_cash_fail_closed")),
        "settled_cash_guard": guard,
    }


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
        # Post-sell guard reflects the account AFTER today's sells credited unsettled
        # proceeds; fall back to the planning guard when there was no rebudget.
        **_settled_cash_gate_fields(
            budget if not post_budget.get("settled_cash_guard") else {
                **budget,
                "settled_cash_guard": post_budget.get("settled_cash_guard"),
                "broker_cash_at_planning": post_budget.get("post_sell_cash", budget.get("broker_cash_at_planning")),
            }
        ),
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


def _pretrade_symbols(snapshot: Mapping[str, Any]) -> list[str]:
    return sorted(
        {
            str(row.get("symbol") or row.get("ticker") or "").strip().upper()
            for row in snapshot.get("positions") or []
            if isinstance(row, Mapping)
            and str(row.get("symbol") or row.get("ticker") or "").strip()
        }
    )


def _write_canonical_authority_artifacts(
    *,
    run_root: Path,
    run_id: str,
    trade_date: str,
    plan: Mapping[str, Any],
    preflight: Mapping[str, Any],
    pre_snapshot: Mapping[str, Any],
    submitted: list[dict[str, Any]],
    reconciliation: Mapping[str, Any],
    target_attainment: Mapping[str, Any],
    summary: Mapping[str, Any],
) -> None:
    """Persist canonical Trader/Auditor artifacts for an approved package run."""
    package_payload = plan.get("approved_execution_package")
    if not isinstance(package_payload, Mapping):
        return

    findings: list[str] = []
    audit_payload: dict[str, Any] = {}
    package_hash = str(package_payload.get("content_hash") or "")
    try:
        from authority.pipeline import audit_execution_package, execution_package_from_dict

        package = execution_package_from_dict(package_payload)
        audit = audit_execution_package(
            package,
            submitted,
            authorized_exit_symbols=_pretrade_symbols(pre_snapshot),
        )
        audit_payload = audit.to_dict()
        findings.extend(audit.findings)
        package_hash = package.content_hash
    except Exception as exc:
        findings.append(f"EXECUTION_PACKAGE_INVALID:{exc}")

    decision_source = (
        dict(plan.get("decision_source_artifact") or {})
        if isinstance(plan.get("decision_source_artifact"), Mapping)
        else {}
    )
    source_path_raw = str(decision_source.get("path") or "").strip()
    expected_source_hash = str(decision_source.get("sha256") or "").strip()
    if source_path_raw and expected_source_hash:
        source_path = Path(source_path_raw)
        if not source_path.is_absolute():
            source_path = REPO_ROOT / source_path
        if not source_path.exists():
            findings.append("DECISION_SOURCE_MISSING")
        else:
            import hashlib

            actual_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
            if actual_hash != expected_source_hash:
                findings.append("DECISION_SOURCE_HASH_MISMATCH")
            decision_source["verified_sha256"] = actual_hash
            decision_source["hash_verified"] = actual_hash == expected_source_hash
    else:
        findings.append("DECISION_SOURCE_PROVENANCE_MISSING")

    reconciliation_status = str(reconciliation.get("status") or "").upper()
    target_status = str(target_attainment.get("status") or "").upper()
    equality_path = run_root / "equality_gate.json"
    equality_payload = _load_plan(equality_path) if equality_path.exists() else {}
    from core.execution_equality_gate import classify_equality_gate_observe_status

    equality_status = classify_equality_gate_observe_status(equality_payload)
    if equality_status != "ok":
        findings.append(f"EXECUTION_EQUALITY_{equality_status.upper()}")
    from core.target_attainment_policy import target_status_passes

    integrity_status = (
        "OK"
        if not findings
        and reconciliation_status in {"CLEAN", "DRY_RUN", "DRY_RUN_NO_SUBMISSION"}
        and (
            target_status == "DRY_RUN_NOT_APPLICABLE"
            or target_status_passes(target_status)
        )
        else "FAIL"
    )
    canonical_payload = {
        "schema_version": "caerus.execution_payload.v1",
        "generated_at": _now_utc(),
        "run_id": run_id,
        "trade_date": trade_date,
        "mode": summary.get("mode"),
        "execution_source": "approved_execution_package",
        "price_freshness_scope": "approved_target_package_and_broker_snapshot",
        "planning_price_basis": "BROKER_SNAPSHOT",
        "pricing_asof": trade_date,
        "execution_price_requirement": "BROKER_SNAPSHOT_VALIDATED",
        "asset_validation_status": preflight.get("asset_validation_status") or "PASS",
        "approved_execution_package": dict(package_payload),
        "approved_execution_package_hash": package_hash,
        "target_portfolio": [dict(row) for row in package_payload.get("approved_target_rows") or []],
        "cash_target_weight": package_payload.get("approved_cash_weight"),
        "decision_source_artifact": decision_source,
        "target_attainment_tolerance": plan.get("target_attainment_tolerance"),
        "target_attainment_policy": plan.get("target_attainment_policy"),
        "whole_share_feasibility": target_attainment.get(
            "whole_share_feasibility"
        ),
    }
    canonical_summary = {
        **dict(summary),
        "schema_version": "caerus.operator_summary.v1",
        "execution_source": "approved_execution_package",
        "approved_execution_package_hash": package_hash,
        "execution_integrity_status": integrity_status,
        "audit_findings_count": len(findings),
    }
    integrity = {
        "schema_version": "caerus.execution_integrity.v1",
        "generated_at": _now_utc(),
        "run_id": run_id,
        "trade_date": trade_date,
        "status": integrity_status,
        "findings": findings,
        "approved_execution_package_hash": package_hash,
        "decision_source_artifact": decision_source,
        "reconciliation_status": reconciliation_status,
        "target_attainment_status": target_status,
        "equality_gate_status": equality_status,
        "equality_gate_artifact": str(equality_path),
        "read_only_auditor": True,
    }
    audit_dir = run_root / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    if audit_payload:
        _write_json(run_root / "authority_audit_package.json", audit_payload)
    _write_json(run_root / "execution_payload.json", canonical_payload)
    _write_json(run_root / "operator_summary.json", canonical_summary)
    _write_json(audit_dir / "execution_integrity.json", integrity)
    try:
        from core.execution_lifecycle_timeline import write_execution_lifecycle_timeline

        write_execution_lifecycle_timeline(
            run_root=run_root,
            trade_date=trade_date,
            run_id=run_id,
        )
    except Exception as exc:
        integrity["status"] = "FAIL"
        integrity["findings"] = [*findings, f"EXECUTION_TIMELINE_BUILD_FAILED:{exc}"]
        _write_json(audit_dir / "execution_integrity.json", integrity)


def _write_unified_equality_gate(
    *,
    run_root: Path,
    run_id: str,
    trade_date: str,
    result: Any,
    intended: list[dict[str, Any]],
    submitted: list[dict[str, Any]],
    plan: Mapping[str, Any],
    enforce: bool = False,
) -> dict[str, Any]:
    """Prove that Trader submitted exactly its final mechanical order set."""

    from core.execution_equality_gate import (
        write_equality_gate_observe_artifacts,
        write_equality_gate_observe_error_artifacts,
    )

    final_frame = getattr(result, "final_execution_trades", None)
    planned_orders = (
        _trade_frame_orders(final_frame)
        if isinstance(final_frame, pd.DataFrame)
        else [dict(row) for row in intended]
    )
    try:
        _, _, artifact = write_equality_gate_observe_artifacts(
            run_root=run_root,
            planned_orders=planned_orders,
            submission_orders=submitted,
            execution_source="planned_payload_exact",
            planning_price_basis="BROKER_SNAPSHOT",
            pricing_asof_planned=trade_date,
            pricing_asof_context=trade_date,
            run_id=run_id,
            trade_date=trade_date,
            artifact_refs={
                "approved_execution_package_hash": (
                    (plan.get("approved_execution_package") or {}).get(
                        "content_hash"
                    )
                    if isinstance(plan.get("approved_execution_package"), Mapping)
                    else None
                )
            },
        )
        if enforce:
            from core.execution_equality_gate import write_equality_gate_artifacts

            artifact.update(
                {
                    "gate_version": "pre_trade_equality_gate.enforce.v1",
                    "mode": "enforce",
                    "enforced": True,
                    "submission_proceeded": artifact.get("decision")
                    == "WOULD_PROCEED",
                }
            )
            write_equality_gate_artifacts(run_root=run_root, artifact=artifact)
        return artifact
    except Exception as exc:
        _, _, artifact = write_equality_gate_observe_error_artifacts(
            run_root=run_root,
            run_id=run_id,
            trade_date=trade_date,
            execution_source="planned_payload_exact",
            planning_price_basis="BROKER_SNAPSHOT",
            pricing_asof_planned=trade_date,
            pricing_asof_context=trade_date,
            observe_error=exc,
        )
        if enforce:
            from core.execution_equality_gate import write_equality_gate_artifacts

            artifact.update(
                {
                    "gate_version": "pre_trade_equality_gate.enforce.v1",
                    "mode": "enforce",
                    "enforced": True,
                    "submission_proceeded": False,
                    "would_block": True,
                    "halt_reason": "equality_gate_error",
                }
            )
            write_equality_gate_artifacts(run_root=run_root, artifact=artifact)
        return artifact


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
    prior_sessions = {
        str(row.get("trade_date") or "").strip()
        for row in prior_attempts
        if str(row.get("trade_date") or "").strip()
    }
    prior_count = len(prior_sessions)
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


def _derive_execution_mode(run_root: Path | str) -> str:
    """Derive the reporting mode/lane from the run's output-root ancestry.

    The SAME executor services both the paper lane (``--output-root
    outputs/paper_lane``) and the live pilot lane (default
    ``outputs/live_pilot``). The run_root is always ``<output_root>/runs/<id>``,
    so the lane is unambiguous from the path — no reliance on process env that a
    shared subprocess might inherit incorrectly. Falls back to the run_id token
    and finally to LIVE_PILOT (fail-safe: an unknown lane is labelled as the
    higher-consequence live lane so it can never silently masquerade as paper).
    """
    parts = {p.lower() for p in Path(run_root).parts}
    name = Path(run_root).name.lower()
    if "paper_lane" in parts or "_paper_" in f"_{name}_" or name.endswith("_paper"):
        return PAPER_MODE.upper()
    if "live_pilot" in parts or "live_pilot" in name:
        return LIVE_PILOT_MODE.upper()
    return LIVE_PILOT_MODE.upper()


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
        "mode": _derive_execution_mode(run_root),
        "status": terminal_status,
        "reason": reason_code,
        "halt_reason": (
            None if terminal_status in {"DRY_RUN", "SUBMITTED", "SUBMITTED_UNFILLED"} else reason_code
        ),
        "operator_execution_status": (
            "dry_run"
            if dry_run
            else "executed"
            if terminal_status == "SUBMITTED"
            else "submitted_unfilled"
            if terminal_status == "SUBMITTED_UNFILLED"
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

    # WARNING §f fix (PRE_ARM_SWEEP_2026-07-13): two prior gaps in this state
    # machine:
    #   1. CLEAN did not require fills — a fully-submitted batch sitting
    #      accepted/new/pending_new/done_for_day at the broker (zero fills)
    #      reported CLEAN.
    #   2. A `partially_filled` snapshot (still OPEN at the broker — it may
    #      fill further) was treated the same as a rejected/failed order and
    #      reported FAILED_RECONCILIATION.
    # Both are non-terminal, in-flight broker states, not failures. They now
    # get their own status: state=OPEN / status=SUBMITTED_UNFILLED — "orders are
    # live at the broker, not yet (fully) filled". A genuinely TERMINAL partial
    # (order done/canceled after only partially filling) is unaffected: its raw
    # broker status is canceled/expired/rejected, which buckets as "rejected"
    # above and correctly stays FAILED_RECONCILIATION.
    if dry_run:
        state = "DRY_RUN"
        status = "DRY_RUN_NO_SUBMISSION"
        action = "Review artifacts and keep dry run enabled until human approval is recorded."
    elif errors or rejected:
        state = "REJECTED"
        status = "FAILED_RECONCILIATION"
        action = "Do not continue live pilot; inspect broker state and resolve rejected/unresolved orders manually."
    elif unresolved or len(submitted) != len(intended):
        state = "UNRESOLVED"
        status = "FAILED_RECONCILIATION"
        action = "Do not continue live pilot; inspect broker state and resolve rejected/unresolved orders manually."
    elif open_count or partial:
        state = "OPEN"
        status = "SUBMITTED_UNFILLED"
        action = (
            "Orders are live at the broker and not yet fully filled. This is not a "
            "failure — monitor broker terminal states and re-run reconciliation "
            "(--refresh-run) until every order reaches a terminal state."
        )
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
            "No rollback action required unless broker state later diverges."
            if status == "CLEAN"
            else "No auto-liquidation. Orders remain open at the broker; wait for a "
            "terminal state or cancel manually if needed."
            if status == "SUBMITTED_UNFILLED"
            else "No auto-liquidation. Cancel/flatten only under a separately approved live incident runbook."
        ),
    }


# Blocked-run reasons that clear WITHOUT operator action: nothing was submitted,
# no broker state was mutated, and the condition is transient by construction.
_CONVERGENT_BLOCK_REASON_MARKERS = (
    "live_pilot_market_closed",
    "live_pilot_pre_snapshot_failed",
    LIVE_PILOT_EQUITY_UNAVAILABLE,
    LIVE_PILOT_BLOCKED_INSUFFICIENT_BUYING_POWER,
    LIVE_PILOT_SELL_SETTLEMENT_TIMEOUT,
    LIVE_PILOT_SELL_SETTLEMENT_CASH_NOT_REFLECTED,
    "live_pilot_transition_no_actionable_order",
)


def _next_run_expectation(
    *,
    terminal_status: str,
    reason_code: object,
    reconciliation_state: object = None,
) -> dict[str, str]:
    """Classify whether a run's end state converges on the next scheduled cycle.

    Design invariant (verified by the post-halt regression tests): the executor
    persists NO halt latch — the next run replans from CURRENT broker positions
    and re-evaluates every gate fresh. A halt therefore self-heals unless it
    left non-terminal broker state (partial/unresolved orders) or reflects a
    configuration/approval condition that cannot clear on its own.
    """
    status = str(terminal_status or "").strip().upper()
    reason = str(reason_code or "").strip()
    state = str(reconciliation_state or "").strip().upper()
    if status in {"SUBMITTED", "DRY_RUN"}:
        return {
            "next_run_expectation": NEXT_RUN_CONVERGES,
            "next_run_expectation_reason": (
                "run reached a terminal non-halt state; the next run replans from current broker positions"
            ),
        }
    if status == "SUBMITTED_UNFILLED":
        return {
            "next_run_expectation": NEXT_RUN_REQUIRES_MANUAL_ACTION,
            "next_run_expectation_reason": (
                "orders were submitted and accepted by the broker but are not yet fully "
                "filled; this is not a failure, but the open-pilot-order check blocks a "
                "new submission until they reach a terminal state (filled/rejected/expired) "
                "— monitor the broker directly and re-run reconciliation"
            ),
        }
    if status == "FAILED_RECONCILIATION":
        if state == "REJECTED":
            return {
                "next_run_expectation": NEXT_RUN_CONVERGES,
                "next_run_expectation_reason": (
                    "rejected orders are terminal at the broker and leave no open orders; "
                    "the next run replans from current broker positions and re-evaluates all gates fresh"
                ),
            }
        if state == "PARTIAL":
            return {
                "next_run_expectation": NEXT_RUN_REQUIRES_MANUAL_ACTION,
                "next_run_expectation_reason": (
                    "a partially filled order may remain open at the broker; an open pilot order "
                    "blocks the next submission until it reaches a terminal state — confirm or cancel first"
                ),
            }
        return {
            "next_run_expectation": NEXT_RUN_REQUIRES_MANUAL_ACTION,
            "next_run_expectation_reason": (
                "broker order state is not known-terminal; refresh reconciliation (--refresh-run) "
                "or inspect the broker before the next run"
            ),
        }
    # BLOCKED (pre-submission) paths.
    if "BLOCKED_OPEN_PILOT_ORDER" in reason:
        return {
            "next_run_expectation": NEXT_RUN_CONVERGES,
            "next_run_expectation_reason": (
                "blocked by an open pilot DAY order; it reaches a terminal state by session close "
                "and the next run replans from current broker positions"
            ),
        }
    if any(marker in reason for marker in _CONVERGENT_BLOCK_REASON_MARKERS):
        return {
            "next_run_expectation": NEXT_RUN_CONVERGES,
            "next_run_expectation_reason": (
                "no orders were submitted and no broker state was mutated; the block condition is "
                "transient and the next run replans from current broker positions"
            ),
        }
    return {
        "next_run_expectation": NEXT_RUN_REQUIRES_MANUAL_ACTION,
        "next_run_expectation_reason": (
            f"halt reason {reason or 'unknown'!r} is a configuration/approval or unknown-broker-truth "
            "condition that will not clear on its own; operator action required before the next run"
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
    filled_buy_notional = 0.0
    for row in submitted:
        order = row.get("order") if isinstance(row.get("order"), Mapping) else {}
        if _status_bucket(row.get("status") or order.get("status")) != "filled":
            continue
        fill = _fill_price(row)
        qty = _safe_float(
            row.get("filled_qty")
            or row.get("filled_quantity")
            or order.get("filled_qty")
            or order.get("filled_quantity")
            or row.get("qty")
        )
        if fill is not None and qty is not None:
            row_filled_notional = fill * qty
        else:
            row_filled_notional = _finite_float(row.get("notional")) or 0.0
        filled_notional += row_filled_notional
        if _order_side(row) == "BUY":
            filled_buy_notional += row_filled_notional
    cap = float(capital_cap_usd or 0.0)
    cash_deployment_rate = (filled_buy_notional / cap) if cap > 0 else None
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
        "filled_buy_notional_usd": round(filled_buy_notional, 6),
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
        if symbol and qty > 1e-12:
            rows.append({"ticker": symbol, "sleeve": "live_pilot", "shares": qty})
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
        trade_date: str | None = None,
    ) -> None:
        self.broker = broker
        self.env = env
        self.run_root = run_root
        self.output_root = output_root
        self.run_id = run_id
        self.pre_sell_account = dict(pre_sell_account or {})
        self.source_by_symbol = _plan_rows_by_symbol(source_plan or {})
        self.trade_date = str(trade_date or "")
        self.execution_mode = _derive_execution_mode(run_root)
        reuse_policy = _parse_optional_policy_bool(
            env.get(PAPER_CONFIRMED_PROCEEDS_REUSE_ENV)
        )
        self.reuse_confirmed_sell_proceeds = bool(
            reuse_policy is True
            and self.execution_mode == PAPER_MODE.upper()
            and bool(getattr(broker, "paper", False))
            and _is_paper_host(str(getattr(broker, "base_url", "") or ""))
        )
        self.submitted_rows: list[dict[str, Any]] = []
        self.submit_errors: list[str] = []
        self._sequence = 0
        # Last settled-cash recompute (post-sell), surfaced for observability.
        self.settled_cash_post_sell: dict[str, Any] | None = None
        # Sells THIS RUN confirmed filled via per-order polling (get_order). Feeds the
        # bulk-history freshness cross-check in the settled-cash recompute: a lagging
        # list_orders payload cannot hide these proceeds from the clamp.
        self._confirmed_sell_fills: list[dict[str, Any]] = []

    def _assert_current_sell_inventory(self, intent: OrderIntent) -> None:
        """Fail closed if broker inventory shrank after the planning snapshot."""
        try:
            positions = self.broker.get_positions()
        except Exception as exc:
            raise RuntimeError(f"live_pilot_sell_inventory_refresh_failed:{exc}") from exc
        if not isinstance(positions, list):
            raise RuntimeError("live_pilot_sell_inventory_refresh_invalid")

        symbol = str(intent.symbol or "").strip().upper()
        current_qty = Decimal("0")
        for raw in positions:
            if not isinstance(raw, Mapping):
                continue
            if str(raw.get("symbol") or "").strip().upper() != symbol:
                continue
            malformed_reason = _malformed_holding_reason(raw)
            if malformed_reason:
                raise RuntimeError(
                    f"live_pilot_sell_inventory_malformed:{symbol}:{malformed_reason}"
                )
            qty = _finite_float(raw.get("qty"))
            if qty is not None and qty > 0.0:
                current_qty += Decimal(str(qty))

        requested_qty = Decimal(str(intent.shares))
        if requested_qty <= 0 or current_qty < requested_qty:
            raise RuntimeError(
                "live_pilot_sell_inventory_changed:"
                f"{symbol}:held={current_qty}:requested={requested_qty}"
            )

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
            if str(submitted_intent.side or "").strip().upper() == "SELL":
                self._assert_current_sell_inventory(submitted_intent)
            # Re-read the mutable runtime gates at the last possible point before
            # each broker submission. A gate flip after planning must fail closed.
            validate_live_pilot_submission_guardrails(
                broker_paper=bool(getattr(self.broker, "paper", True)),
                base_url=str(getattr(self.broker, "base_url", "") or ""),
                env=self.env,
                order_notional=float(submitted_intent.notional),
            )
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
        account = dict(self.broker.get_account() if hasattr(self.broker, "get_account") else {})
        positions = self.broker.get_positions() if hasattr(self.broker, "get_positions") else []
        # PRIMARY GFV DEFENSE: recompute settled cash from CURRENT broker truth. After
        # sells fill, broker cash includes today's freshly credited (but unsettled)
        # proceeds. Live capital remains clamped to settled cash; the explicitly pinned
        # PAPER lane may add only this run's confirmed fills to its execution ceiling.
        settled_result, _orders, availability = _settled_cash_context(
            self.broker,
            broker_cash=account.get("cash"),
            as_of_date=self.trade_date,
            env=self.env,
            confirmed_sells=list(self._confirmed_sell_fills),
        )
        account["settled_cash"] = float(settled_result.settled_cash)
        account["settled_cash_fail_closed"] = bool(settled_result.fail_closed)
        settled_report = settled_result.to_report()
        confirmed_proceeds = float(
            sum(
                max(0.0, float(row.get("proceeds") or 0.0))
                for row in self._confirmed_sell_fills
            )
        )
        broker_cash = _finite_float(account.get("cash"))
        execution_spendable_cash: float | None = None
        if (
            self.reuse_confirmed_sell_proceeds
            and not settled_result.fail_closed
            and broker_cash is not None
        ):
            # PAPER-only correction: a fully confirmed current-run sell may fund the
            # mechanical buy phase after broker cash and buying power reflect it. Keep
            # any unrelated unsettled proceeds excluded by adding only this run's
            # confirmed fills to the regulatory settled-cash ledger, capped by broker
            # cash. The live-capital lane never sets this override.
            execution_spendable_cash = min(
                max(0.0, float(broker_cash)),
                max(0.0, float(settled_result.settled_cash)) + confirmed_proceeds,
            )
            account["execution_spendable_cash"] = execution_spendable_cash
            account["execution_spendable_cash_source"] = (
                "paper_settled_plus_confirmed_current_run_sells"
            )
        settled_report["order_history_availability"] = availability
        settled_report["phase"] = "post_sell"
        settled_report["confirmed_current_run_sell_proceeds"] = confirmed_proceeds
        settled_report["confirmed_proceeds_reuse_enabled"] = bool(
            self.reuse_confirmed_sell_proceeds
        )
        settled_report["execution_spendable_cash"] = execution_spendable_cash
        settled_report["execution_spendable_cash_source"] = account.get(
            "execution_spendable_cash_source"
        )
        self.settled_cash_post_sell = settled_report
        return CoreAccountSnapshot(
            account=account,
            holdings=_holdings_frame_from_broker_positions(positions),
            raw={"snapshot_source": "live_pilot_adapter", "settled_cash_guard": settled_report},
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

    def _settlement_wait_params(self) -> dict[str, Any]:
        """Env-tunable bounded exponential backoff for the sell-settlement wait.

        Safe defaults apply when the CAERUS_LIVE_PILOT_SETTLEMENT_* envs are
        unset. An explicitly set TIMEOUT additionally caps total wall time;
        TIMEOUT=0 preserves the legacy single-pass fail-fast behavior. The
        legacy POLL env doubles as the backoff base when the new BASE_DELAY
        env is unset.
        """
        raw_attempts = _safe_float(self.env.get(LIVE_PILOT_SETTLEMENT_MAX_ATTEMPTS_ENV))
        max_attempts = (
            int(raw_attempts)
            if raw_attempts is not None and raw_attempts >= 1.0
            else LIVE_PILOT_SETTLEMENT_DEFAULT_MAX_ATTEMPTS
        )
        base_delay = _safe_float(self.env.get(LIVE_PILOT_SETTLEMENT_BASE_DELAY_ENV))
        if base_delay is None or base_delay < 0.0:
            base_delay = _safe_float(self.env.get(LIVE_PILOT_SETTLEMENT_POLL_ENV))
        if base_delay is None or base_delay < 0.0:
            base_delay = LIVE_PILOT_SETTLEMENT_DEFAULT_BASE_DELAY_SECONDS
        max_delay = _safe_float(self.env.get(LIVE_PILOT_SETTLEMENT_MAX_DELAY_ENV))
        if max_delay is None or max_delay < 0.0:
            max_delay = LIVE_PILOT_SETTLEMENT_DEFAULT_MAX_DELAY_SECONDS
        raw_timeout = str(self.env.get(LIVE_PILOT_SETTLEMENT_TIMEOUT_ENV) or "").strip()
        timeout = _safe_float(raw_timeout) if raw_timeout else None
        if timeout is not None:
            timeout = max(0.0, float(timeout))
        return {
            "max_attempts": int(max_attempts),
            "base_delay_seconds": float(base_delay),
            "max_delay_seconds": float(max_delay),
            "timeout_seconds": timeout,
        }

    def wait_or_refresh(self, submitted_sells: list[SubmitResult] | tuple[SubmitResult, ...]) -> CoreAccountSnapshot:
        params = self._settlement_wait_params()
        timeout = params["timeout_seconds"]
        deadline = (time.monotonic() + float(timeout)) if timeout is not None else None
        expected_freed = float(
            sum(
                max(0.0, float(getattr(result.intent, "notional", 0.0) or 0.0))
                for result in submitted_sells or []
            )
        )
        sell_records: list[dict[str, Any]] = []
        pending: list[str] = []
        terminal_failed: list[str] = []
        attempts_used = 0
        delays_slept: list[float] = []
        exhausted_reason: str | None = None
        while True:
            attempts_used += 1
            sell_records = []
            pending = []
            terminal_failed = []
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
                if bucket == "rejected":
                    terminal_failed.append(order_id or result.intent.client_order_id or result.intent.symbol)
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
            # Freshness cross-check input (GFV guard): sells confirmed filled (fully
            # or partially) by the per-order polling above. snapshot()'s settled-cash
            # recompute verifies their proceeds are represented in the BULK order
            # history it uses; a lagging bulk read gets the shortfall injected as
            # unsettled instead of silently counting it as settled.
            self._confirmed_sell_fills = [
                {
                    "order_id": row.get("broker_order_id") or "",
                    "proceeds": float(row.get("filled_notional") or 0.0),
                    "symbol": row.get("symbol"),
                }
                for row in sell_records
                if _status_bucket(row.get("status")) in {"filled", "partial"}
                and float(row.get("filled_notional") or 0.0) > 0.0
            ]
            snapshot = self.snapshot()
            settlement_reflection = self._settlement_reflection(
                account=snapshot.account,
                expected_freed=expected_freed,
            )
            if not pending and settlement_reflection["settlement_reflected"]:
                break
            if terminal_failed:
                # A rejected/canceled/expired sell can never settle; retrying only
                # delays the (unchanged) fail-closed outcome.
                exhausted_reason = "terminal_failed_sell_orders"
                break
            if attempts_used >= int(params["max_attempts"]):
                exhausted_reason = "max_attempts_exhausted"
                break
            if deadline is not None and time.monotonic() >= deadline:
                exhausted_reason = "timeout_exhausted"
                break
            delay = min(
                float(params["base_delay_seconds"]) * (2.0 ** (attempts_used - 1)),
                float(params["max_delay_seconds"]),
            )
            if deadline is not None:
                delay = min(delay, max(0.0, deadline - time.monotonic()))
            delays_slept.append(float(delay))
            if delay > 0.0:
                time.sleep(delay)
        confirmed_proceeds = float(sum(float(row.get("filled_notional") or 0.0) for row in sell_records))
        raw = dict(snapshot.raw or {})
        raw["sell_fill_meta"] = {
            "sell_orders": sell_records,
            "confirmed_sell_proceeds": confirmed_proceeds,
            "expected_freed_proceeds": expected_freed,
            "settlement_reflection": dict(settlement_reflection),
            "pending_sell_order_ids": list(pending),
            "fill_model": "broker_refresh_until_settled",
            "settlement_wait": {
                "backoff": "bounded_exponential",
                "attempts_used": attempts_used,
                "max_attempts": int(params["max_attempts"]),
                "base_delay_seconds": float(params["base_delay_seconds"]),
                "max_delay_seconds": float(params["max_delay_seconds"]),
                "timeout_seconds": params["timeout_seconds"],
                "delays_slept_seconds": list(delays_slept),
                "exhausted_reason": exhausted_reason,
                "terminal_failed_sell_order_ids": list(terminal_failed),
            },
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
            "whole_share_feasibility": dict(
                (getattr(result, "trade_meta", {}) or {}).get(
                    "whole_share_feasibility"
                )
                or {}
            )
            if result is not None
            else {},
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
        **_settled_cash_gate_fields(capital_budget),
    }


def _request_excluding_dropped_orders(
    request: ExecutionRequest,
    dropped_orders: list[Mapping[str, Any]],
) -> ExecutionRequest:
    """Neutralize orders dropped at validation so the submission engine agrees.

    BLOCKER 3 fix (PRE_ARM_SWEEP_2026-07-13 §d): per-order partitioning in
    ``validate_live_pilot_plan``/the asset-check loop only decides what the
    *validation gate* allows. For a real (non-dry) run, ``execute_lifecycle``
    independently recomputes trades from this ``ExecutionRequest`` — without
    this filter, a symbol dropped at validation (bad symbol, non-tradable
    asset, sell inventory mismatch, sells disabled, ...) would still be
    recomputed and submitted by the engine, silently defeating the partition
    (and, for a dropped SELL, submitting an order guardrails explicitly
    rejected). A dropped BUY target is simply removed from the target book
    (nothing is currently held, so no target row = no trade proposed). A
    dropped SELL is neutralized by pinning its target weight to its CURRENT
    weight so the rebalance engine computes a zero delta for that symbol —
    the held position is left exactly as-is rather than exited.
    """
    if not dropped_orders:
        return request
    dropped_buy_symbols = {
        str(row.get("symbol") or "").strip().upper()
        for row in dropped_orders
        if str(row.get("side") or "").strip().upper() == "BUY"
    }
    dropped_sell_symbols = {
        str(row.get("symbol") or "").strip().upper()
        for row in dropped_orders
        if str(row.get("side") or "").strip().upper() == "SELL"
    }
    dropped_buy_symbols.discard("")
    dropped_sell_symbols.discard("")
    if not dropped_buy_symbols and not dropped_sell_symbols:
        return request
    targets = (
        request.targets.copy()
        if request.targets is not None and not request.targets.empty
        else pd.DataFrame(columns=["ticker", "sleeve", "target_weight"])
    )
    if dropped_buy_symbols and not targets.empty:
        targets = targets[
            ~targets["ticker"].astype(str).str.upper().isin(dropped_buy_symbols)
        ].copy()
    if dropped_sell_symbols:
        held_shares: dict[str, float] = {}
        if request.holdings is not None and not request.holdings.empty:
            held_shares = (
                request.holdings.set_index("ticker")["shares"].astype(float).to_dict()
            )
        equity = float(request.total_equity or 0.0)
        if not targets.empty:
            targets = targets[
                ~targets["ticker"].astype(str).str.upper().isin(dropped_sell_symbols)
            ].copy()
        pin_rows: list[dict[str, Any]] = []
        for symbol in dropped_sell_symbols:
            shares = float(held_shares.get(symbol, 0.0) or 0.0)
            price = (
                float(request.prices.get(symbol, 0.0) or 0.0)
                if request.prices is not None
                else 0.0
            )
            current_weight = (
                (shares * price / equity) if equity > 0.0 and price > 0.0 else 0.0
            )
            pin_rows.append(
                {
                    "ticker": symbol,
                    "sleeve": "live_pilot_validation_dropped_sell_hold",
                    "target_weight": current_weight,
                }
            )
        if pin_rows:
            targets = pd.concat(
                [targets, pd.DataFrame(pin_rows, columns=["ticker", "sleeve", "target_weight"])],
                ignore_index=True,
            )
    return dataclasses.replace(request, targets=targets)


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
    allow_fractional: bool,
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
    # PRIMARY GFV DEFENSE (Blocker #2): recompute settled cash from broker truth and
    # clamp planning buys to it. Alpaca credits sale proceeds instantly though
    # unsettled (T+1); buying with them then selling next morning is a GFV. Injecting
    # settled_cash into the planning account makes the capital budget size against
    # settled funds only. Fails closed if order history is unavailable.
    buy_buffer_pct = _buy_buffer_pct(env)
    settled_result, _settled_orders, settled_availability = _settled_cash_context(
        broker,
        broker_cash=request.planning_account.get("cash"),
        as_of_date=trade_date,
        env=env,
    )
    request.planning_account["settled_cash"] = float(settled_result.settled_cash)
    request.planning_account["settled_cash_fail_closed"] = bool(settled_result.fail_closed)
    settled_report = settled_result.to_report()
    settled_report["order_history_availability"] = settled_availability
    settled_report["phase"] = "planning"
    _write_json(run_root / "live_pilot_settled_cash.json", settled_report)
    paper_fractional_exit_enabled = (
        _derive_execution_mode(run_root) == PAPER_MODE.upper()
        and str(env.get("CAERUS_PAPER_FRACTIONAL_EXIT_ENABLED") or "").strip().lower()
        in {"1", "true", "yes", "y", "on"}
    )
    fractional_sell_min_trade_usd = _finite_float(
        env.get("CAERUS_PAPER_FRACTIONAL_EXIT_MIN_NOTIONAL_USD")
    )
    if fractional_sell_min_trade_usd is None or fractional_sell_min_trade_usd <= 0.0:
        fractional_sell_min_trade_usd = 1.0
    config = live_pilot_execution_config(
        approved_cap_usd=gate.capital_cap_usd,
        allow_fractional=bool(allow_fractional),
        allow_fractional_sells=paper_fractional_exit_enabled,
        fractional_sell_min_trade_usd=float(fractional_sell_min_trade_usd),
        max_orders=int(gate.max_orders or 1),
        min_trade_usd=float(min_trade_usd),
        buy_buffer_pct=float(buy_buffer_pct),
        ledger_output_root=output_root,
        ledger_enabled=not bool(gate.dry_run),
    )
    # The shared executor deliberately reuses the live-pilot constraint surface
    # for both endpoints, but the governed whole-share attainment policy is
    # PAPER-only and keys off the execution-core mode.  Preserve the shared
    # constraints while carrying the lane identity into the core; otherwise a
    # valid PAPER authority package is misclassified as LIVE_PILOT and fails
    # before the first order can be submitted.
    if _derive_execution_mode(run_root) == PAPER_MODE.upper():
        config = dataclasses.replace(config, mode=PAPER_MODE)
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
        # The execution core is authoritative after whole-share rounding,
        # deadband, and min-trade filtering. ``full_need`` is a pre-rounding
        # cap diagnostic and must not turn a genuinely attained target into an
        # insufficient-buying-power error.
        tradable_capital = float(capital_gate.get("tradable_capital_usd") or 0.0)
        had_buy_demand = (
            float(capital_budget.get("requested_buy_notional") or 0.0) > 0.0
            or (
                full_need + 1e-9 >= float(config.orders.min_trade_usd)
                and full_need > tradable_capital + 1e-9
            )
        )
        if settled_result.fail_closed and had_buy_demand:
            # Order history was unavailable -> settled cash treated as $0 -> buys blocked.
            reason_code = LIVE_PILOT_GFV_SETTLED_CASH_UNAVAILABLE
        elif had_buy_demand:
            reason_code = LIVE_PILOT_BLOCKED_INSUFFICIENT_BUYING_POWER
        else:
            reason_code = "live_pilot_transition_no_actionable_order"
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
        sell_inventory=_sell_inventory_from_request(request),
        allow_fractional_sells=paper_fractional_exit_enabled,
    )
    # BLOCKER 3 fix (PRE_ARM_SWEEP_2026-07-13 §d): validate_live_pilot_plan now
    # partitions PER ORDER — a bad symbol/inventory/sells-disabled problem drops
    # only that order (recorded below) rather than blocking the whole batch.
    # plan_validation.status is only "BLOCKED" when NOT ONE order survived, or a
    # genuine batch-level constraint (order-count/cap) was tripped.
    dropped_orders: list[dict[str, Any]] = [
        dropped.to_dict() for dropped in plan_validation.dropped_orders
    ]
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
    governed_package_required = bool(
        isinstance(plan.get("approved_execution_package"), Mapping)
        and str(env.get("CAERUS_REQUIRE_APPROVED_EXECUTION_PACKAGE") or "")
        .strip()
        .lower()
        in {"1", "true", "yes", "y", "on"}
    )
    if governed_package_required and dropped_orders:
        equality_gate = _write_unified_equality_gate(
            run_root=run_root,
            run_id=run_id,
            trade_date=trade_date,
            result=SimpleNamespace(final_execution_trades=executable_trades),
            intended=intended,
            submitted=intended,
            plan=plan,
            enforce=True,
        )
        return _write_blocked_artifacts(
            run_root=run_root,
            run_id=run_id,
            trade_date=trade_date,
            env=env,
            reason_code="authorized_plan_order_suppressed",
            operator_action=(
                "An order in the immutable authorized plan failed validation; "
                "the complete batch was blocked before broker submission."
            ),
            preflight=preflight,
            intended=intended,
            capital_gate=capital_gate,
            dropped_orders=dropped_orders,
            execution_equality=equality_gate,
        )
    _write_json(run_root / "live_pilot_capital_gate.json", capital_gate)

    # Belt-and-suspenders (defense #3): block any planned SELL of a position whose
    # acquiring buy has not passed its own T+1 settlement (canonical GFV shape). With
    # the buy clamp (defense #2) in place this NEVER fires under daily rotation; a hit
    # means the buy clamp regressed, so it is a loud, blocking alert.
    planned_sell_symbols = [
        str(order.symbol)
        for order in plan_validation.orders
        if str(getattr(order, "side", "")).strip().upper() in {"SELL", "CLOSE", "REDUCE"}
    ]
    gfv_sell_alerts = detect_gfv_risky_sells(
        planned_sell_symbols=planned_sell_symbols,
        orders=_settled_orders,
        as_of_date=trade_date,
    )
    if gfv_sell_alerts:
        gfv_alert_payload = {
            "schema_version": "live_pilot_gfv_alert.v1",
            "generated_at": _now_utc(),
            "run_id": run_id,
            "trade_date": trade_date,
            "reason_code": LIVE_PILOT_GFV_SELL_OF_UNSETTLED_ACQUISITION,
            "alerts": gfv_sell_alerts,
            "note": (
                "Sell-side GFV guard fired: selling a position acquired with funds that "
                "may not have settled. This should be impossible while the settled-cash "
                "buy clamp is active; investigate a buy-clamp regression before arming."
            ),
        }
        _write_json(run_root / "live_pilot_gfv_alert.json", gfv_alert_payload)
        capital_gate["decision"] = "BLOCKED"
        capital_gate["block_reason"] = LIVE_PILOT_GFV_SELL_OF_UNSETTLED_ACQUISITION
        _write_json(run_root / "live_pilot_capital_gate.json", capital_gate)
        return _write_blocked_artifacts(
            run_root=run_root,
            run_id=run_id,
            trade_date=trade_date,
            env=env,
            reason_code=LIVE_PILOT_GFV_SELL_OF_UNSETTLED_ACQUISITION,
            operator_action=(
                "Sell-side GFV guard blocked the run: a planned sell targets a position "
                "acquired with possibly-unsettled funds. Investigate the buy clamp."
            ),
            preflight=preflight,
            intended=intended,
            capital_gate=capital_gate,
        )

    # Asset validation (halted/delisted/inactive/unsupported asset class) is also
    # PER ORDER: a bad asset drops only that order — critically, a bad BUY
    # candidate must never block the SELLs that free capital. Only a totally
    # empty surviving set blocks the run.
    valid_orders: list[Any] = []
    for order in plan_validation.orders:
        asset = broker.get_asset(order.symbol) if hasattr(broker, "get_asset") else None
        error = validate_live_pilot_asset(asset, order.symbol)
        if error:
            severity = "critical" if order.side == "SELL" else "warning"
            dropped_orders.append(
                {
                    "symbol": order.symbol,
                    "side": order.side,
                    "reason_code": error,
                    "severity": severity,
                    "stage": "asset_validation",
                }
            )
            if severity == "critical":
                print(
                    f"live_pilot_sell_dropped_at_asset_check: symbol={order.symbol} "
                    f"reason={error} — an intended SELL of a held position was dropped; "
                    "it will NOT be submitted this run. Other orders proceed.",
                    file=sys.stderr,
                )
            continue
        valid_orders.append(order)

    if not valid_orders:
        return _write_blocked_artifacts(
            run_root=run_root,
            run_id=run_id,
            trade_date=trade_date,
            env=env,
            reason_code=";".join(row["reason_code"] for row in dropped_orders if row.get("stage") == "asset_validation")
            or "live_pilot_all_orders_failed_asset_validation",
            operator_action="Unsupported or non-tradable assets blocked before submission.",
            preflight=preflight,
            intended=intended,
            capital_gate=capital_gate,
        )

    if governed_package_required and dropped_orders:
        surviving_intended = _intended_from_validation(
            orders=valid_orders,
            source_trades=source_trades,
            output_root=output_root,
            run_id=run_id,
        )
        equality_gate = _write_unified_equality_gate(
            run_root=run_root,
            run_id=run_id,
            trade_date=trade_date,
            result=SimpleNamespace(final_execution_trades=executable_trades),
            intended=intended,
            submitted=surviving_intended,
            plan=plan,
            enforce=True,
        )
        return _write_blocked_artifacts(
            run_root=run_root,
            run_id=run_id,
            trade_date=trade_date,
            env=env,
            reason_code="authorized_plan_order_suppressed",
            operator_action=(
                "An order in the immutable authorized plan failed broker-asset "
                "validation; the complete batch was blocked before submission."
            ),
            preflight=preflight,
            intended=intended,
            capital_gate=capital_gate,
            dropped_orders=dropped_orders,
            execution_equality=equality_gate,
        )

    # Recompute the intended/artifact set against the FINAL (post-asset-check)
    # surviving orders, and re-write orders_intended so the artifact reflects
    # exactly what will be submitted plus a full audit trail of every drop.
    intended = _intended_from_validation(
        orders=valid_orders,
        source_trades=source_trades,
        output_root=output_root,
        run_id=run_id,
    )
    intended_payload = plan_validation.to_dict()
    intended_payload["orders"] = intended
    intended_payload["dropped_orders"] = dropped_orders
    intended_payload["dropped_orders_count"] = len(dropped_orders)
    intended_payload["dropped_sell_orders_count"] = sum(
        1 for row in dropped_orders if str(row.get("side") or "").upper() == "SELL"
    )
    intended_payload.update(_entry_policy_summary(intended=intended, submitted=[], dry_run=bool(gate.dry_run)))
    _write_json(run_root / "live_pilot_orders_intended.json", intended_payload)

    pre_submit_equality = _write_unified_equality_gate(
        run_root=run_root,
        run_id=run_id,
        trade_date=trade_date,
        result=SimpleNamespace(final_execution_trades=executable_trades),
        intended=intended,
        submitted=intended,
        plan=plan,
        enforce=governed_package_required,
    )
    if governed_package_required and pre_submit_equality.get("decision") != "WOULD_PROCEED":
        return _write_blocked_artifacts(
            run_root=run_root,
            run_id=run_id,
            trade_date=trade_date,
            env=env,
            reason_code="authorized_plan_equality_failed",
            operator_action=(
                "The exact broker order set did not match the immutable authorized "
                "plan; no order was submitted."
            ),
            preflight=preflight,
            intended=intended,
            capital_gate=capital_gate,
            execution_equality=pre_submit_equality,
        )

    open_order_check = _open_pilot_order_check(
        broker,
        intended_symbols={order.symbol for order in valid_orders},
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
    reporting_intended = [dict(row) for row in intended]
    suppressed_orders: list[dict[str, Any]] = []
    submit_errors: list[str] = []
    if gate.dry_run:
        submitted = [
            {**row, "status": "DRY_RUN_NOT_SUBMITTED", "order": None}
            for row in intended
        ]
        result = SimpleNamespace(
            trade_meta=_trade_meta,
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
        # BLOCKER 3 fix: execute_lifecycle independently recomputes trades from
        # `request` (holdings/targets), so orders dropped at validation must be
        # neutralized in the request too — otherwise the submission engine would
        # silently recompute and submit them anyway, defeating the partition.
        submission_request = _request_excluding_dropped_orders(request, dropped_orders)
        adapter = LivePilotCoreAdapter(
            broker=broker,
            env=env,
            run_root=run_root,
            output_root=output_root,
            run_id=run_id,
            pre_sell_account=request.planning_account,
            source_plan=plan,
            trade_date=trade_date,
        )
        result = execute_lifecycle(request=submission_request, adapter=adapter, config=config)
        submitted = list(adapter.submitted_rows)
        submit_errors = list(adapter.submit_errors)
        if adapter.settled_cash_post_sell is not None:
            _write_json(
                run_root / "live_pilot_settled_cash.json",
                adapter.settled_cash_post_sell,
            )
        intended = _intended_from_submitted(submitted)
        suppressed_orders = [
            {
                **dict(row),
                "symbol": str(row.get("symbol") or row.get("ticker") or "").strip().upper(),
                "ticker": str(row.get("ticker") or row.get("symbol") or "").strip().upper(),
                "side": str(row.get("side") or "BUY").strip().upper(),
                "suppressed": True,
                "suppression_reason": row.get("block_reason") or row.get("reason"),
            }
            for row in (getattr(result, "rebudget_skipped", []) or [])
            if isinstance(row, Mapping)
        ]
        reporting_intended = [*intended, *suppressed_orders]
        suppression_reasons = sorted(
            {
                str(row.get("suppression_reason") or "").strip()
                for row in suppressed_orders
                if str(row.get("suppression_reason") or "").strip()
            }
        )
        suppressed_reason = (
            suppression_reasons[0]
            if len(suppression_reasons) == 1
            else "mixed"
            if suppression_reasons
            else NO_ESCALATION_REASON
        )
        reporting_entry_summary = _entry_policy_summary(
            intended=reporting_intended,
            submitted=submitted,
            dry_run=False,
        )
        reporting_entry_summary["blocked_or_suppressed_buy_reason"] = suppressed_reason
        _write_json(
            run_root / "live_pilot_orders_intended.json",
            {
                **plan_validation.to_dict(),
                "orders": intended,
                "approved_orders": reporting_intended,
                "suppressed_orders": suppressed_orders,
                "suppressed_orders_count": len(suppressed_orders),
                "dropped_orders": dropped_orders,
                "dropped_orders_count": len(dropped_orders),
                "dropped_sell_orders_count": sum(
                    1 for row in dropped_orders if str(row.get("side") or "").upper() == "SELL"
                ),
                **reporting_entry_summary,
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
    equality_gate = _write_unified_equality_gate(
        run_root=run_root,
        run_id=run_id,
        trade_date=trade_date,
        result=result,
        intended=intended,
        submitted=submitted,
        plan=plan,
        enforce=governed_package_required,
    )
    try:
        from core.lane_target_attainment import build_lane_target_attainment

        target_attainment = build_lane_target_attainment(
            plan=plan,
            post_snapshot=post_snapshot,
            reconciliation=reconciliation,
            run_id=run_id,
            trade_date=trade_date,
            mode=str(getattr(gate, "requested_mode", "") or LIVE_PILOT_MODE),
            dry_run=bool(gate.dry_run),
            drift_tolerance=float(
                _finite_float(plan.get("target_attainment_tolerance")) or 0.02
            ),
            feasibility_evidence=(
                (getattr(result, "trade_meta", {}) or {}).get(
                    "whole_share_feasibility"
                )
            ),
        )
        (run_root / "audit").mkdir(parents=True, exist_ok=True)
        _write_json(
            run_root
            / "audit"
            / f"execution_target_attainment_{trade_date}.json",
            target_attainment,
        )
    except Exception as exc:
        target_attainment = {
            "status": "UNKNOWN_INSUFFICIENT_ARTIFACTS",
            "reason_code": f"target_attainment_build_failed:{exc}",
        }
    evidence_metrics = _build_evidence_metrics(
        dry_run=bool(gate.dry_run),
        intended=reporting_intended,
        submitted=submitted,
        reconciliation=reconciliation,
        capital_cap_usd=gate.capital_cap_usd,
        open_order_check=open_order_check,
        capital_gate=capital_gate,
    )
    entry_summary = _entry_policy_summary(
        intended=reporting_intended,
        submitted=submitted,
        dry_run=bool(gate.dry_run),
    )
    if suppressed_orders:
        entry_summary["blocked_or_suppressed_buy_reason"] = reporting_entry_summary[
            "blocked_or_suppressed_buy_reason"
        ]
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
            "filled_buy_notional_usd": evidence_metrics.get("filled_buy_notional_usd"),
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
    # Reconciliation status semantics (WARNING §f fix): CLEAN now requires actual
    # fills; a fully-submitted-but-unfilled/open batch is its own non-failure
    # status (SUBMITTED_UNFILLED) rather than CLEAN-with-zero-fills or a false
    # FAILED_RECONCILIATION. See _reconcile() for the full state machine.
    _recon_status = str(reconciliation.get("status") or "")
    if governed_package_required and equality_gate.get("decision") != "WOULD_PROCEED":
        terminal_status = "FAILED_PLAN_INTEGRITY"
    elif gate.dry_run:
        terminal_status = "DRY_RUN"
    elif _recon_status == "CLEAN":
        terminal_status = "SUBMITTED"
    elif _recon_status == "SUBMITTED_UNFILLED":
        terminal_status = "SUBMITTED_UNFILLED"
    else:
        terminal_status = "FAILED_RECONCILIATION"
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
        "intended_count": len(reporting_intended),
        "submitted_count": 0 if gate.dry_run else len(submitted),
        "filled_count": reconciliation.get("filled_count"),
        "fill_rate": evidence_metrics.get("fill_rate"),
        "cash_deployment_rate": evidence_metrics.get("cash_deployment_rate"),
        "idle_cash_reason": evidence_metrics.get("idle_cash_reason"),
        "operator_action": reconciliation.get("operator_action"),
        "execution_target_attainment_status": target_attainment.get("status"),
        "execution_target_attainment_reason": target_attainment.get("reason_code"),
        "execution_equality_status": equality_gate.get("decision"),
        "run_root": str(run_root),
        # BLOCKER 3 audit trail: every order dropped at validation (plan-level or
        # asset-level), with its own reason_code — never silently absorbed into
        # the intended/submitted counts.
        "dropped_orders_count": len(dropped_orders),
        "dropped_sell_orders_count": sum(
            1 for row in dropped_orders if str(row.get("side") or "").upper() == "SELL"
        ),
        "dropped_orders": dropped_orders,
        **_next_run_expectation(
            terminal_status=terminal_status,
            reason_code=reconciliation.get("status"),
            reconciliation_state=reconciliation.get("state"),
        ),
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
            intended=reporting_intended,
            submitted=submitted,
            reconciliation=reconciliation,
            dry_run=bool(gate.dry_run),
            run_root=run_root,
            extra_fields={
                **_capital_gate_report_fields(capital_gate),
                **entry_summary,
                "cash_deployment_rate": evidence_metrics.get("cash_deployment_rate"),
                "idle_cash_reason": evidence_metrics.get("idle_cash_reason"),
                "execution_target_attainment_status": target_attainment.get("status"),
                "execution_target_attainment_reason": target_attainment.get("reason_code"),
            },
        ),
    )
    _write_canonical_authority_artifacts(
        run_root=run_root,
        run_id=run_id,
        trade_date=trade_date,
        plan=plan,
        preflight=preflight,
        pre_snapshot=pre_snapshot,
        submitted=submitted,
        reconciliation=reconciliation,
        target_attainment=target_attainment,
        summary=summary,
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
    dropped_orders: list[dict[str, Any]] | None = None,
    execution_equality: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    intended = intended or []
    dropped_orders = list(dropped_orders or [])
    submitted: list[dict[str, Any]] = []
    dry_run = bool(preflight.get("dry_run"))
    capital_gate_fields = _capital_gate_report_fields(capital_gate)
    reconciliation = _reconcile(
        dry_run=dry_run,
        intended=intended,
        submitted=submitted,
        errors=[reason_code],
    )
    reconciliation.update(
        {
            "terminal_status": "BLOCKED",
            "terminal_outcome": "SYSTEM_FAILURE",
            "failure_class": "AUTHORIZATION_FAILURE",
            "reason_code": reason_code,
            "reconciliation_status": "FAILED_PRE_SUBMIT",
        }
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
        "trade_date": trade_date,
        "terminal_status": "BLOCKED",
        "terminal_outcome": "SYSTEM_FAILURE",
        "failure_class": "AUTHORIZATION_FAILURE",
        "reconciliation_status": "FAILED_PRE_SUBMIT",
        "reason_code": reason_code,
        "live_orders_allowed": False,
        "submitted_count": 0,
        "operator_action": operator_action,
        "run_root": str(run_root),
        "dropped_orders_count": len(dropped_orders),
        "dropped_sell_orders_count": sum(
            1
            for row in dropped_orders
            if str(row.get("side") or "").upper() == "SELL"
        ),
        "dropped_orders": dropped_orders,
        "execution_equality_status": (execution_equality or {}).get("decision"),
        **_next_run_expectation(
            terminal_status="BLOCKED",
            reason_code=reason_code,
            reconciliation_state=reconciliation.get("state"),
        ),
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
    _write_json(
        run_root / "live_pilot_orders_intended.json",
        {
            "orders": intended,
            "dropped_orders": dropped_orders,
            "dropped_orders_count": len(dropped_orders),
            "dropped_sell_orders_count": sum(
                1
                for row in dropped_orders
                if str(row.get("side") or "").upper() == "SELL"
            ),
        },
    )
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
    _write_json(run_root / "operator_summary.json", summary)
    _write_json(run_root / "live_pilot_preflight.json", dict(preflight))
    _write_json(
        run_root / "execution_payload.json",
        {
            "schema_version": "caerus.execution_payload.v3",
            "run_id": run_id,
            "trade_date": trade_date,
            "status": "BLOCKED",
            "reason_code": reason_code,
            "execution_source": "exact_execution_plan_v3",
            "execution_status": "HALTED",
            "operator_execution_status": "system_failure",
            "orders_requested_count": len(intended),
            "orders_submitted_count": len(submitted),
            "orders_filled_count": 0,
            "trades": [
                {
                    "ticker": row.get("symbol"),
                    "side": row.get("side"),
                    "shares": row.get("quantity"),
                    "entry_price": (
                        row.get("filled_avg_price")
                        or row.get("expected_price")
                        or row.get("limit_price")
                    ),
                    "notional": row.get("notional"),
                    "order_id": row.get("order_id"),
                    "client_order_id": row.get("client_order_id"),
                }
                for row in intended
            ],
            "orders": intended,
        },
    )
    _write_json(
        run_root / "execution_timeline.json",
        {
            "schema_version": "caerus.execution_lifecycle_timeline.v2",
            "run_id": run_id,
            "trade_date": trade_date,
            "terminal_status": "BLOCKED",
            "terminal_outcome": "SYSTEM_FAILURE",
            "reason_code": reason_code,
            "reconciliation_status": "FAILED_PRE_SUBMIT",
            "stages": [{"stage": "AUTHORIZE", "status": "FAILED"}],
        },
    )
    _write_json(
        run_root / "audit" / "execution_integrity.json",
        {
            "schema_version": "caerus.execution_integrity.v2",
            "generated_at": _now_utc(),
            "run_id": run_id,
            "trade_date": trade_date,
            "status": "FAIL",
            "terminal_outcome": "SYSTEM_FAILURE",
            "reconciliation_status": "FAILED_PRE_SUBMIT",
            "findings": [reason_code],
        },
    )
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
            extra_fields={
                **capital_gate_fields,
                "terminal_outcome": "SYSTEM_FAILURE",
                "failure_class": "AUTHORIZATION_FAILURE",
                "reconciliation_status": "FAILED_PRE_SUBMIT",
            },
        ),
    )
    if str(summary.get("mode") or "").upper() == "PAPER":
        try:
            from core.execution_attempt_registry import (
                AttemptRecord,
                append_attempt_and_update_selection,
                attempt_path,
            )
            from core.failure_semantics import FailureClass, TerminalOutcome

            registry_root = run_root.parent.parent / "execution_attempts"
            def build_blocked_record(prior):
                return AttemptRecord(
                    attempt_id=run_id,
                    trade_date=trade_date,
                    run_id=run_id,
                    lane="paper",
                    sequence=len(prior) + 1,
                    terminal_outcome=TerminalOutcome.SYSTEM_FAILURE,
                    recorded_at=_now_utc(),
                    run_root=str(run_root),
                    submitted_count=0,
                    filled_count=0,
                    failure_class=FailureClass.AUTHORIZATION_FAILURE,
                    reason_code=reason_code,
                    source_artifacts=(
                        str(run_root / "execution_payload.json"),
                        str(run_root / "live_pilot_reconciliation.json"),
                    ),
                )

            _attempt_path, selection, _selection_path = (
                append_attempt_and_update_selection(
                    registry_root,
                    trade_date=trade_date,
                    build_record=build_blocked_record,
                )
            )
            summary.update(
                {
                    "attempt_registry_status": selection.status.value,
                    "attempt_registry_selection": str(
                        registry_root / trade_date / "selection.json"
                    ),
                }
            )
        except Exception as exc:
            summary.update(
                {
                    "attempt_registry_status": "FAILED",
                    "attempt_registry_error": str(exc),
                }
            )
        _write_json(run_root / "live_pilot_operator_summary.json", summary)
        _write_json(run_root / "operator_summary.json", summary)
    return summary


def _run_exact_execution_path(
    *,
    plan: Mapping[str, Any],
    broker: Any,
    env: Mapping[str, str],
    preflight: Mapping[str, Any],
    pre_snapshot: Mapping[str, Any],
    run_root: Path,
    run_id: str,
    trade_date: str,
    output_root: Path | str,
    dry_run: bool,
) -> dict[str, Any]:
    """Consume v3 as exact orders. No target reconstruction is reachable here."""
    from core.failure_semantics import TerminalOutcome
    from execution.exact_executor import execute_exact_plan

    package = plan.get("exact_execution_plan")
    if not isinstance(package, Mapping):
        return _write_blocked_artifacts(
            run_root=run_root,
            run_id=run_id,
            trade_date=trade_date,
            env=env,
            reason_code="exact_execution_plan_required",
            operator_action="Publish one fresh broker-state-bound exact v3 plan.",
            preflight=preflight,
        )
    workflow_plan_id: str | None = None
    state_root = Path(output_root) / "orchestrator_state"
    try:
        from authority.exact_plan import exact_execution_plan_from_dict

        expected_plan_id = str(plan.get("exact_execution_plan_id") or "").strip()
        expected_plan_hash = str(plan.get("exact_execution_plan_hash") or "").strip()
        expected_authority_run = str(
            plan.get("exact_execution_authority_run_id") or ""
        ).strip()
        expected_status = (
            "AUTHORIZED_NO_TRADE"
            if not list(package.get("sell_orders") or [])
            and not list(package.get("buy_orders") or [])
            else "AUTHORIZED_EXACT_PLAN"
        )
        if str(plan.get("schema_version") or "") != "caerus.authorized_execution_handoff.v1":
            raise ValueError("governed exact-plan handoff schema is required")
        if str(plan.get("status") or "").strip().upper() != expected_status:
            raise ValueError("governed exact-plan handoff status is invalid")
        if str(plan.get("execution_authority") or "") != "exact_execution_plan_only":
            raise ValueError("exact execution authority boundary is required")
        if plan.get("precompute_execution_authority") is not False:
            raise ValueError("precompute artifacts cannot carry execution authority")
        if not expected_plan_id or not expected_plan_hash or not expected_authority_run:
            raise ValueError("outer exact-plan identity binding is required")
        bound = exact_execution_plan_from_dict(
            package,
            expected_plan_id=expected_plan_id,
            expected_run_id=expected_authority_run,
            expected_account_scope="PAPER",
        )
        if bound.content_hash != expected_plan_hash:
            raise ValueError("outer exact-plan hash does not match inner package")
        if bound.trade_date != trade_date:
            raise ValueError("outer trade_date does not match inner exact plan")
    except Exception as exc:
        return _write_blocked_artifacts(
            run_root=run_root,
            run_id=run_id,
            trade_date=trade_date,
            env=env,
            reason_code=f"exact_execution_plan_invalid:{exc}",
            operator_action="Preserve the invalid plan and obtain a new authorization.",
            preflight=preflight,
        )

    can_append_reconcile = False
    if not dry_run:
        registry_root = Path(output_root) / "execution_attempts"
        record_appended = False
        try:
            from core.orchestrator_state import STAGES, append_orchestrator_transition

            workflow_plan_id = f"{bound.plan_id}:{run_id}"
            decision_source = plan.get("decision_source_artifact")
            decision_source_path = (
                str(decision_source.get("path") or "")
                if isinstance(decision_source, Mapping)
                else ""
            )
            source_refs = tuple(str(item) for item in bound.source_precompute_ids)
            source_hash_refs = tuple(
                f"{path}=sha256:{digest}"
                for path, digest in sorted(bound.source_artifact_hashes.items())
            )
            stage_refs = {
                "OBSERVE": (
                    str(run_root / "live_pilot_broker_snapshot_pre.json"),
                    f"market_state:{bound.market_state_id}",
                ),
                "RESEARCH": tuple(
                    item for item in (*source_refs, *source_hash_refs) if item
                ),
                "PRECOMPUTE": tuple(
                    item for item in (*source_refs, *source_hash_refs) if item
                ),
                "DECIDE": tuple(
                    item for item in (decision_source_path, *source_hash_refs) if item
                ),
                "AUTHORIZE": (
                    f"plan_id:{bound.plan_id}",
                    f"sha256:{bound.content_hash}",
                ),
            }
            # These imported stages are accepted only after their immutable
            # source hashes and the exact authorization have been revalidated
            # above. Each stage names its own evidence rather than manufacturing
            # one indistinguishable set of references for the whole lifecycle.
            for stage in STAGES[:5]:
                append_orchestrator_transition(
                    state_root,
                    trade_date=trade_date,
                    plan_id=workflow_plan_id,
                    stage=stage,
                    status="PASS",
                    recorded_at=_now_utc(),
                    artifact_refs=stage_refs[stage],
                )
        except Exception as exc:
            return _write_blocked_artifacts(
                run_root=run_root,
                run_id=run_id,
                trade_date=trade_date,
                env=env,
                reason_code=f"orchestrator_state_persistence_failed:{exc}",
                operator_action="Repair append-only state persistence before retrying.",
                preflight=preflight,
            )

    try:
        outcome = execute_exact_plan(
            plan_payload=package,
            broker=broker,
            env=env,
            wal_root=Path(output_root) / "submission_wal",
            attempt_id=run_id,
            dry_run=bool(dry_run),
        )
    except Exception as exc:
        return _write_blocked_artifacts(
            run_root=run_root,
            run_id=run_id,
            trade_date=trade_date,
            env=env,
            reason_code=f"exact_execution_internal_failure:{exc}",
            operator_action="Inspect WAL evidence and recover by stable client order ID before retrying.",
            preflight=preflight,
        )

    result = outcome.to_dict()
    intended = [dict(row) for row in outcome.orders_requested]
    submitted = [dict(row) for row in outcome.orders_submitted]
    suppressed = [dict(row) for row in outcome.orders_suppressed]
    _write_json(
        run_root / "live_pilot_orders_intended.json",
        {
            "schema_version": "caerus.exact_orders_intended.v1",
            "plan_id": outcome.plan_id_received,
            "plan_hash": outcome.plan_hash_received,
            "orders": intended,
            "dropped_orders": [],
            "suppressed_orders": suppressed,
        },
    )
    _write_json(
        run_root / "live_pilot_orders_submitted.json",
        {"schema_version": "caerus.exact_orders_submitted.v1", "orders": submitted},
    )
    reconciliation = {
        "schema_version": "live_pilot_reconciliation.v1",
        "generated_at": _now_utc(),
        "status": outcome.reconciliation_status,
        "state": outcome.status,
        "intended_count": len(intended),
        "submitted_count": len(submitted),
        "filled_count": len(outcome.orders_filled),
        "rejected_count": len(outcome.orders_rejected),
        "suppressed_count": len(suppressed),
        "suppressed_orders": suppressed,
        "terminal_outcome": outcome.terminal_outcome.value,
        "reason_code": outcome.reason_code,
        "plan_id": outcome.plan_id_received,
        "plan_hash": outcome.plan_hash_received,
        "final_positions": [dict(row) for row in outcome.final_positions],
        "final_cash": outcome.final_cash,
    }
    _write_json(run_root / "live_pilot_reconciliation.json", reconciliation)
    try:
        post_snapshot = _broker_snapshot(broker)
    except Exception as exc:
        post_snapshot = {
            "captured_at": _now_utc(),
            "status": "SNAPSHOT_FAILED",
            "error": str(exc),
            "positions": [dict(row) for row in outcome.final_positions],
            "account": {"cash": outcome.final_cash},
        }
    _write_json(run_root / "live_pilot_broker_snapshot_post.json", post_snapshot)

    economic_status = "NOT_RUN"
    economic_reason: str | None = None
    try:
        if dry_run:
            if str(post_snapshot.get("status") or "").upper() == "SNAPSHOT_FAILED":
                raise RuntimeError("dry-run post-validation broker snapshot failed")
            raise _DryRunEconomicNotApplicable()
        from authority.exact_plan import exact_execution_plan_from_dict
        from core.economic_reconciliation import (
            EconomicTolerance,
            Fill,
            MarkedPosition,
            SleeveAttributionRow,
            reconcile_economic_truth,
            reconcile_sleeve_attribution,
            verify_canonical_economics,
            write_canonical_economic_verification,
        )

        exact = exact_execution_plan_from_dict(package)
        fills = []
        for row in outcome.orders_filled:
            quantity = _finite_float(
                row.get("filled_qty")
                or row.get("filled_quantity")
                or row.get("quantity")
                or row.get("qty")
            )
            price = _finite_float(
                row.get("filled_avg_price")
                or row.get("fill_price")
                or row.get("average_price")
            )
            if quantity is None or quantity <= 0 or price is None or price < 0:
                raise ValueError(f"actual broker fill economics missing for {row.get('order_id')}")
            fills.append(
                Fill(
                    symbol=str(row.get("symbol") or ""),
                    side=str(row.get("side") or ""),
                    quantity=quantity,
                    price=price,
                    fees=float(_finite_float(row.get("fees")) or 0.0),
                    sleeve=str(row.get("sleeve") or "caerus_orion"),
                    order_id=str(row.get("id") or row.get("order_id") or ""),
                )
            )
        marked_positions = []
        broker_position_value = 0.0
        for row in post_snapshot.get("positions") or []:
            if not isinstance(row, Mapping):
                continue
            quantity = _finite_float(
                row.get("qty") or row.get("quantity") or row.get("shares")
            )
            market_value = _finite_float(row.get("market_value"))
            mark = _finite_float(
                row.get("current_price")
                or row.get("mark")
                or (
                    market_value / quantity
                    if market_value is not None and quantity not in {None, 0.0}
                    else None
                )
            )
            if quantity is None or mark is None:
                raise ValueError(f"broker mark missing for {row.get('symbol')}")
            if market_value is not None:
                broker_position_value += market_value
            marked_positions.append(
                MarkedPosition(
                    symbol=str(row.get("symbol") or ""),
                    quantity=quantity,
                    mark=mark,
                    broker_market_value=market_value,
                )
            )
        post_account = (
            post_snapshot.get("account")
            if isinstance(post_snapshot.get("account"), Mapping)
            else {}
        )
        ending_cash = _finite_float((post_account or {}).get("cash"))
        ending_equity = _finite_float(
            (post_account or {}).get("portfolio_value")
            or (post_account or {}).get("equity")
        )
        if ending_cash is None or ending_equity is None:
            raise ValueError("broker ending cash/equity missing")
        economic = reconcile_economic_truth(
            trade_date=trade_date,
            starting_cash=exact.starting_cash,
            starting_positions={
                str(row["symbol"]): float(row["quantity"])
                for row in exact.starting_positions
            },
            fills=fills,
            ending_cash=ending_cash,
            ending_positions=marked_positions,
            broker_equity=ending_equity,
            broker_position_value=(broker_position_value if marked_positions else 0.0),
            # Alpaca account and position endpoints are not an atomic market
            # snapshot. Preserve cent-level cash/quantity truth while allowing
            # at most 5 bps of mark movement between those two read calls.
            tolerance=EconomicTolerance(
                nav_abs=max(0.01, abs(ending_equity) * 0.0005),
                position_value_abs=max(0.01, abs(ending_equity) * 0.0005),
            ),
        )
        portfolio_result = ending_equity - exact.portfolio_nav
        starting_position_value = sum(
            float(_finite_float(row.get("market_value")) or 0.0)
            for row in pre_snapshot.get("positions") or []
            if isinstance(row, Mapping)
        )
        ending_position_value = sum(
            float(_finite_float(row.get("market_value")) or 0.0)
            for row in post_snapshot.get("positions") or []
            if isinstance(row, Mapping)
        )
        independently_attributed_orion_result = (
            ending_position_value
            - starting_position_value
            + ending_cash
            - exact.starting_cash
        )
        attribution = reconcile_sleeve_attribution(
            trade_date=trade_date,
            portfolio_result=portfolio_result,
            rows=[
                SleeveAttributionRow(
                    trade_date=trade_date,
                    sleeve="caerus_orion",
                    result_dollars=independently_attributed_orion_result,
                    source_artifact=(
                        f"{run_root / 'live_pilot_broker_snapshot_pre.json'};"
                        f"{run_root / 'live_pilot_broker_snapshot_post.json'}"
                    ),
                )
            ],
            # Attribution compares account NAV with separately fetched position
            # marks at both the pre- and posttrade snapshots. Alpaca does not
            # provide those as one atomic read, so allow the same 5 bps mark
            # movement budget per snapshot (10 bps total), while quantity/cash
            # reconciliation above remains exact to its own strict tolerances.
            tolerance=max(
                0.01,
                max(abs(ending_equity), abs(exact.portfolio_nav)) * 0.001,
            ),
        )
        verification = verify_canonical_economics(
            economic_reconciliation=economic,
            sleeve_attribution_reconciliation=attribution,
        )
        write_canonical_economic_verification(
            run_root / "canonical_economic_verification.json",
            verification,
        )
        economic_status = verification.status.value
        if not verification.reconciled:
            economic_reason = "canonical_economic_or_sleeve_attribution_mismatch"
    except _DryRunEconomicNotApplicable:
        economic_status = "NOT_APPLICABLE_DRY_RUN"
        economic_reason = None
    except Exception as exc:
        economic_status = "FAILED_RECONCILIATION"
        economic_reason = f"canonical_economic_verification_failed:{exc}"
        _write_json(
            run_root / "canonical_economic_verification_failure.json",
            {
                "schema_version": "caerus.canonical_economic_verification_failure.v1",
                "trade_date": trade_date,
                "status": economic_status,
                "reason_code": economic_reason,
            },
        )

    dry_validation_suppressions = bool(dry_run) and all(
        str((row.get("suppression") or {}).get("reason_code") or "")
        == "DRY_RUN_VALIDATION_ONLY"
        for row in outcome.orders_suppressed
    )
    equality_ok = (
        (not outcome.orders_suppressed or dry_validation_suppressions)
        and len(outcome.orders_submitted) in {0, len(outcome.orders_requested)}
        and outcome.terminal_outcome
        not in {TerminalOutcome.SUBMISSION_UNKNOWN}
    )
    equality = {
        "schema_version": "caerus.execution_equality_gate.v2",
        "generated_at": _now_utc(),
        "plan_id": outcome.plan_id_received,
        "authorized_plan_hash": outcome.plan_hash_received,
        "plan_hash_validated": outcome.plan_hash_validated,
        "authorization_validated": outcome.authorization_validated,
        "authorized_order_ids": [row.get("order_id") for row in intended],
        "submitted_order_ids": [row.get("order_id") for row in submitted],
        "orders_suppressed": suppressed,
        "decision": (
            "VALIDATED_NO_SUBMISSION"
            if dry_validation_suppressions and equality_ok
            else "WOULD_PROCEED"
            if equality_ok
            else "WOULD_HALT_HASH_MISMATCH"
        ),
        "enforced_pre_submit": True,
        "execution_source": "exact_execution_plan_v3",
    }
    _write_json(run_root / "equality_gate.json", equality)

    if outcome.status == "DRY_RUN":
        terminal_status = "DRY_RUN"
    elif outcome.terminal_outcome is TerminalOutcome.AUTHORIZED_NO_TRADE:
        terminal_status = "AUTHORIZED_NO_TRADE"
    elif outcome.terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS:
        terminal_status = "SUBMITTED"
    elif outcome.terminal_outcome is TerminalOutcome.SUBMISSION_UNKNOWN:
        terminal_status = "SUBMISSION_UNKNOWN"
    elif outcome.status in {"FAILED_RECONCILIATION", "SUBMITTED_UNFILLED", "SELL_PHASE_UNRESOLVED"}:
        terminal_status = "FAILED_RECONCILIATION"
    else:
        terminal_status = "FAILED_PLAN_INTEGRITY" if "plan" in outcome.reason_code else "BLOCKED"
    if (
        economic_status == "FAILED_RECONCILIATION"
        and outcome.terminal_outcome is not TerminalOutcome.SUBMISSION_UNKNOWN
    ):
        terminal_status = "BLOCKED" if dry_run else "FAILED_RECONCILIATION"
    final_terminal_outcome = outcome.terminal_outcome
    final_failure_class = outcome.failure_class
    final_reason = outcome.reason_code
    final_reconciliation_status = outcome.reconciliation_status
    if (
        economic_status == "FAILED_RECONCILIATION"
        and outcome.terminal_outcome is not TerminalOutcome.SUBMISSION_UNKNOWN
    ):
        final_terminal_outcome = TerminalOutcome.SYSTEM_FAILURE
        from core.failure_semantics import FailureClass

        final_failure_class = FailureClass.RECONCILIATION_FAILURE
        final_reason = economic_reason or "canonical_economic_verification_failed"
        final_reconciliation_status = "FAILED_RECONCILIATION"
    summary = {
        "schema_version": "live_pilot_operator_summary.v1",
        "generated_at": _now_utc(),
        "run_id": run_id,
        "trade_date": trade_date,
        "mode": "PAPER",
        "terminal_status": terminal_status,
        "terminal_outcome": final_terminal_outcome.value,
        "reason_code": final_reason,
        "dry_run": bool(dry_run),
        "execution_source": "exact_execution_plan_v3",
        "plan_id_received": outcome.plan_id_received,
        "plan_hash_received": outcome.plan_hash_received,
        "plan_hash_validated": outcome.plan_hash_validated,
        "authorization_validated": outcome.authorization_validated,
        "intended_count": len(intended),
        "submitted_count": 0 if dry_run else len(submitted),
        "filled_count": len(outcome.orders_filled),
        "rejected_count": len(outcome.orders_rejected),
        "suppressed_count": len(suppressed),
        "suppressed_orders": suppressed,
        "reconciliation_status": final_reconciliation_status,
        "canonical_economic_verification_status": economic_status,
        "canonical_economic_verification_reason": economic_reason,
        "execution_equality_status": equality["decision"],
        "run_root": str(run_root),
    }
    _write_json(run_root / "live_pilot_operator_summary.json", summary)
    _write_json(
        run_root / "execution_results.json",
        {
            **result,
            "raw_execution_status": result.get("status"),
            "status": terminal_status,
            "run_id": run_id,
            "trade_date": trade_date,
            "mode": "PAPER",
            "terminal_status": terminal_status,
            "run_root": str(run_root),
        },
    )
    _write_json(
        run_root / "execution_payload.json",
        {
            "schema_version": "caerus.execution_payload.v2",
            "generated_at": _now_utc(),
            "run_id": run_id,
            "trade_date": trade_date,
            "mode": "PAPER",
            "execution_source": "exact_execution_plan_v3",
            "price_freshness_scope": "fresh_broker_state_at_authorization",
            "execution_status": (
                "EXECUTED"
                if terminal_status == "SUBMITTED"
                else (
                    "NO_ACTION"
                    if terminal_status == "AUTHORIZED_NO_TRADE"
                    else "HALTED"
                )
            ),
            "operator_execution_status": (
                "reconciled_success"
                if final_terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
                else final_terminal_outcome.value.lower()
            ),
            "orders_requested_count": len(intended),
            "orders_submitted_count": len(submitted),
            "orders_filled_count": len(outcome.orders_filled),
            "orders_suppressed_count": len(suppressed),
            "orders_suppressed": suppressed,
            "trades": [
                {
                    "ticker": row.get("symbol"),
                    "side": row.get("side"),
                    "shares": row.get("quantity"),
                    "entry_price": (
                        row.get("filled_avg_price")
                        or row.get("expected_price")
                        or row.get("limit_price")
                    ),
                    "notional": row.get("notional"),
                    "order_id": row.get("order_id"),
                    "client_order_id": row.get("client_order_id"),
                }
                for row in intended
            ],
            "exact_execution_plan": dict(package),
            "exact_execution_plan_hash": outcome.plan_hash_received,
            # Preserve the governed Orion target lineage for downstream
            # reconciliation.  It is evidence only here; the executor has
            # already consumed the immutable exact orders above.
            "approved_execution_package": (
                dict(plan.get("approved_execution_package"))
                if isinstance(plan.get("approved_execution_package"), Mapping)
                else None
            ),
            "decision_source_artifact": (
                dict(plan.get("decision_source_artifact"))
                if isinstance(plan.get("decision_source_artifact"), Mapping)
                else None
            ),
        },
    )
    audit_dir = run_root / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    integrity_ok = (
        equality_ok
        and outcome.terminal_outcome
        in {TerminalOutcome.RECONCILED_SUCCESS, TerminalOutcome.AUTHORIZED_NO_TRADE}
        and economic_status == "RECONCILED"
    )
    if dry_run:
        integrity_ok = equality_ok and economic_status != "FAILED_RECONCILIATION"
    _write_json(
        audit_dir / "execution_integrity.json",
        {
            "schema_version": "caerus.execution_integrity.v2",
            "generated_at": _now_utc(),
            "run_id": run_id,
            "trade_date": trade_date,
            "status": "OK" if integrity_ok else "FAIL",
            "plan_id": outcome.plan_id_received,
            "plan_hash": outcome.plan_hash_received,
            "terminal_outcome": final_terminal_outcome.value,
            "reconciliation_status": final_reconciliation_status,
            "canonical_economic_verification_status": economic_status,
            "equality_gate_status": equality["decision"],
            "findings": [] if integrity_ok else [final_reason],
        },
    )
    _write_json(
        run_root / "execution_timeline.json",
        {
            "schema_version": "caerus.execution_lifecycle_timeline.v2",
            "run_id": run_id,
            "trade_date": trade_date,
            "plan_id": outcome.plan_id_received,
            "provenance": {
                "execution_source": "exact_execution_plan_v3",
                "price_freshness_scope": "fresh_broker_state_at_authorization",
                "plan_hash": outcome.plan_hash_received,
            },
            "stages": [
                {"stage": "AUTHORIZE", "status": "PASS"},
                {"stage": "EXECUTE", "status": outcome.status},
                {"stage": "RECONCILE", "status": outcome.reconciliation_status},
            ],
        },
    )
    if not dry_run:
        try:
            from core.orchestrator_state import STAGES, append_orchestrator_transition

            if workflow_plan_id is None:
                raise RuntimeError("pre-execution orchestrator state was not established")
            # RECONCILE and LEARN are appended only after the canonical attempt
            # registry and selection pointer are durable below. This prevents a
            # persistence failure from leaving an immutable all-PASS lifecycle.
            for stage in STAGES[5:7]:
                if stage == "EXECUTE":
                    stage_status = (
                        "NO_ACTION"
                        if final_terminal_outcome is TerminalOutcome.AUTHORIZED_NO_TRADE
                        else "PASS"
                        if outcome.terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
                        else "FAILED"
                    )
                elif stage == "VERIFY":
                    stage_status = (
                        "PASS"
                        if final_terminal_outcome
                        in {
                            TerminalOutcome.RECONCILED_SUCCESS,
                            TerminalOutcome.AUTHORIZED_NO_TRADE,
                        }
                        else "FAILED"
                    )
                else:
                    stage_status = "PASS"
                transition = append_orchestrator_transition(
                    state_root,
                    trade_date=trade_date,
                    plan_id=workflow_plan_id,
                    stage=stage,
                    status=stage_status,
                    recorded_at=_now_utc(),
                    artifact_refs=(
                        str(run_root / "execution_payload.json"),
                        str(run_root / "live_pilot_reconciliation.json"),
                    ),
                )
                if stage_status == "FAILED":
                    break
            can_append_reconcile = (
                transition.stage == "VERIFY" and transition.status == "PASS"
            )
            summary["orchestrator_state_status"] = transition.status
            summary["orchestrator_state_hash"] = transition.content_hash
            summary["orchestrator_state_root"] = str(state_root)
            summary["orchestrator_workflow_plan_id"] = workflow_plan_id
        except Exception as exc:
            from core.failure_semantics import FailureClass

            final_terminal_outcome = TerminalOutcome.SYSTEM_FAILURE
            final_failure_class = FailureClass.STATE_FAILURE
            final_reason = f"orchestrator_state_persistence_failed:{exc}"
            final_reconciliation_status = "FAILED_RECONCILIATION"
            terminal_status = "FAILED_RECONCILIATION"
            summary.update(
                {
                    "terminal_status": terminal_status,
                    "terminal_outcome": final_terminal_outcome.value,
                    "reason_code": final_reason,
                    "reconciliation_status": final_reconciliation_status,
                    "orchestrator_state_status": "FAILED",
                }
            )
    if not dry_run:
        try:
            from core.execution_attempt_registry import (
                AttemptRecord,
                append_attempt_and_update_selection,
                attempt_path,
            )

            from core.submission_wal import OrderIntent
            from core.paper_drill_epoch import plan_drill_epoch, scoped_wal_root

            wal_clients: list[str] = []
            effective_wal_root = scoped_wal_root(
                Path(output_root) / "submission_wal",
                plan_drill_epoch(bound),
            )
            wal_dir = effective_wal_root / trade_date / "intents"
            for path in sorted(wal_dir.glob("*.json")) if wal_dir.exists() else []:
                intent = OrderIntent.from_dict(
                    json.loads(path.read_text(encoding="utf-8"))
                )
                if intent.plan_id == outcome.plan_id_received:
                    wal_clients.append(intent.client_order_id)
            def build_exact_record(prior_attempts):
                resolves = ()
                if final_terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS and any(
                    bool(row.get("recovered_by_client_order_id"))
                    for row in outcome.orders_submitted
                ):
                    recovered_clients = {
                        str(row.get("client_order_id") or "")
                        for row in outcome.orders_submitted
                        if bool(row.get("recovered_by_client_order_id"))
                    }
                    resolves = tuple(
                        row.attempt_id
                        for row in prior_attempts
                        if row.terminal_outcome is TerminalOutcome.SUBMISSION_UNKNOWN
                        and row.plan_id == outcome.plan_id_received
                        and bool(row.client_order_ids)
                        and set(row.client_order_ids).issubset(recovered_clients)
                    )
                return AttemptRecord(
                    attempt_id=run_id,
                    trade_date=trade_date,
                    run_id=run_id,
                    lane="paper",
                    sequence=len(prior_attempts) + 1,
                    terminal_outcome=final_terminal_outcome,
                    recorded_at=_now_utc(),
                    run_root=str(run_root),
                    submitted_count=len(outcome.orders_submitted),
                    filled_count=len(outcome.orders_filled),
                    failure_class=final_failure_class,
                    reason_code=final_reason,
                    source_artifacts=(
                        str(run_root / "execution_payload.json"),
                        str(run_root / "live_pilot_reconciliation.json"),
                    ),
                    resolves_attempt_ids=resolves,
                    plan_id=outcome.plan_id_received,
                    client_order_ids=tuple(sorted(set(wal_clients))),
                )

            _attempt_path, selection, _selection_path = (
                append_attempt_and_update_selection(
                    registry_root,
                    trade_date=trade_date,
                    build_record=build_exact_record,
                )
            )
            record_appended = True
            summary["attempt_registry_status"] = selection.status.value
            summary["attempt_registry_selection"] = str(
                registry_root / trade_date / "selection.json"
            )
            _write_json(run_root / "live_pilot_operator_summary.json", summary)
        except Exception as exc:
            from core.failure_semantics import FailureClass

            record_appended = record_appended or attempt_path(
                registry_root,
                trade_date=trade_date,
                attempt_id=run_id,
            ).exists()

            summary["attempt_registry_status"] = "FAILED"
            summary["attempt_registry_error"] = str(exc)
            if terminal_status in {"SUBMITTED", "AUTHORIZED_NO_TRADE"}:
                terminal_status = "FAILED_RECONCILIATION"
                final_terminal_outcome = TerminalOutcome.SYSTEM_FAILURE
                final_failure_class = FailureClass.STATE_FAILURE
                final_reason = "execution_attempt_registry_persistence_failed"
                final_reconciliation_status = "FAILED_RECONCILIATION"
                summary.update(
                    {
                        "terminal_status": terminal_status,
                        "terminal_outcome": final_terminal_outcome.value,
                        "reason_code": final_reason,
                        "reconciliation_status": final_reconciliation_status,
                    }
                )
            # If the immutable economic attempt was already appended but its
            # required selection pointer was not durable, preserve the later
            # workflow failure as a second immutable terminal attempt. The
            # selector always honors the latest terminal attempt.
            if record_appended:
                try:
                    def build_finalization_correction(correction_prior):
                        return AttemptRecord(
                            attempt_id=f"{run_id}.finalization_failure",
                            trade_date=trade_date,
                            run_id=run_id,
                            lane="paper",
                            sequence=len(correction_prior) + 1,
                            terminal_outcome=TerminalOutcome.SYSTEM_FAILURE,
                            recorded_at=_now_utc(),
                            run_root=str(run_root),
                            submitted_count=len(outcome.orders_submitted),
                            filled_count=len(outcome.orders_filled),
                            failure_class=FailureClass.STATE_FAILURE,
                            reason_code="execution_attempt_selection_persistence_failed",
                            source_artifacts=(
                                str(run_root / "execution_payload.json"),
                                str(run_root / "live_pilot_reconciliation.json"),
                            ),
                            plan_id=outcome.plan_id_received,
                            client_order_ids=tuple(sorted(set(wal_clients))),
                        )

                    _path, corrected_selection, _pointer = (
                        append_attempt_and_update_selection(
                            registry_root,
                            trade_date=trade_date,
                            build_record=build_finalization_correction,
                        )
                    )
                    summary["attempt_registry_status"] = (
                        corrected_selection.status.value
                    )
                except Exception as correction_exc:
                    summary["attempt_registry_correction_error"] = str(correction_exc)
    if not dry_run and can_append_reconcile:
        try:
            reconciliation_stage_status = (
                "PASS"
                if final_terminal_outcome
                in {
                    TerminalOutcome.RECONCILED_SUCCESS,
                    TerminalOutcome.AUTHORIZED_NO_TRADE,
                }
                else "FAILED"
            )
            transition = append_orchestrator_transition(
                state_root,
                trade_date=trade_date,
                plan_id=workflow_plan_id,
                stage="RECONCILE",
                status=reconciliation_stage_status,
                recorded_at=_now_utc(),
                artifact_refs=(
                    str(run_root / "execution_payload.json"),
                    str(run_root / "live_pilot_reconciliation.json"),
                    str(Path(output_root) / "execution_attempts" / trade_date / "selection.json"),
                ),
            )
            if transition.status == "PASS":
                transition = append_orchestrator_transition(
                    state_root,
                    trade_date=trade_date,
                    plan_id=workflow_plan_id,
                    stage="LEARN",
                    status="PASS",
                    recorded_at=_now_utc(),
                    artifact_refs=(
                        str(Path(output_root) / "execution_attempts" / trade_date / "selection.json"),
                        str(run_root / "canonical_economic_verification.json"),
                    ),
                )
            summary["orchestrator_state_status"] = transition.status
            summary["orchestrator_state_hash"] = transition.content_hash
            summary["orchestrator_workflow_plan_id"] = workflow_plan_id
        except Exception as exc:
            from core.failure_semantics import FailureClass

            learn_followed_success = final_terminal_outcome in {
                TerminalOutcome.RECONCILED_SUCCESS,
                TerminalOutcome.AUTHORIZED_NO_TRADE,
            }
            if final_terminal_outcome in {
                TerminalOutcome.RECONCILED_SUCCESS,
                TerminalOutcome.AUTHORIZED_NO_TRADE,
            }:
                terminal_status = "FAILED_RECONCILIATION"
                final_terminal_outcome = TerminalOutcome.SYSTEM_FAILURE
                final_failure_class = FailureClass.STATE_FAILURE
                final_reason = f"orchestrator_learn_persistence_failed:{exc}"
                final_reconciliation_status = "FAILED_RECONCILIATION"
            summary["orchestrator_state_status"] = "FAILED"
            if learn_followed_success:
                try:
                    def build_learn_correction(learn_prior):
                        return AttemptRecord(
                            attempt_id=f"{run_id}.learn_failure",
                            trade_date=trade_date,
                            run_id=run_id,
                            lane="paper",
                            sequence=len(learn_prior) + 1,
                            terminal_outcome=TerminalOutcome.SYSTEM_FAILURE,
                            recorded_at=_now_utc(),
                            run_root=str(run_root),
                            submitted_count=len(outcome.orders_submitted),
                            filled_count=len(outcome.orders_filled),
                            failure_class=FailureClass.STATE_FAILURE,
                            reason_code=final_reason,
                            source_artifacts=(
                                str(run_root / "execution_payload.json"),
                                str(run_root / "live_pilot_reconciliation.json"),
                            ),
                            plan_id=outcome.plan_id_received,
                            client_order_ids=tuple(sorted(set(wal_clients))),
                        )

                    _path, corrected_selection, _pointer = (
                        append_attempt_and_update_selection(
                            registry_root,
                            trade_date=trade_date,
                            build_record=build_learn_correction,
                        )
                    )
                    summary["attempt_registry_status"] = corrected_selection.status.value
                except Exception as correction_exc:
                    summary["attempt_registry_correction_error"] = str(correction_exc)
    # Rewrite the final views only after every required terminal persistence step
    # so summary/results/integrity cannot disagree about success versus failure.
    summary.update(
        {
            "terminal_status": terminal_status,
            "terminal_outcome": final_terminal_outcome.value,
            "reason_code": final_reason,
            "reconciliation_status": final_reconciliation_status,
        }
    )
    _write_json(run_root / "live_pilot_operator_summary.json", summary)
    # The canonical workflow/health/confirmation readers use operator_summary.
    # Keep the legacy-named view as an identical compatibility alias so every
    # consumer observes the same final terminal semantics.
    _write_json(run_root / "operator_summary.json", summary)
    reconciliation.update(
        {
            "status": final_reconciliation_status,
            "state": terminal_status,
            "terminal_outcome": final_terminal_outcome.value,
            "reason_code": final_reason,
        }
    )
    _write_json(run_root / "live_pilot_reconciliation.json", reconciliation)
    timeline_payload = _load_plan(run_root / "execution_timeline.json")
    timeline_payload["terminal_status"] = terminal_status
    timeline_payload["terminal_outcome"] = final_terminal_outcome.value
    timeline_payload["reason_code"] = final_reason
    timeline_payload["reconciliation_status"] = final_reconciliation_status
    if final_terminal_outcome is TerminalOutcome.SYSTEM_FAILURE:
        stages = timeline_payload.get("stages")
        if isinstance(stages, list):
            for stage in stages:
                if isinstance(stage, dict) and stage.get("stage") in {
                    "VERIFY",
                    "RECONCILE",
                    "LEARN",
                }:
                    stage["status"] = "FAILED"
            stages.append(
                {
                    "stage": "FINALIZE",
                    "status": "FAILED",
                    "reason_code": final_reason,
                }
            )
    _write_json(run_root / "execution_timeline.json", timeline_payload)
    execution_payload_path = run_root / "execution_payload.json"
    execution_payload = _load_plan(execution_payload_path)
    execution_payload.update(
        {
            "execution_status": (
                "EXECUTED"
                if terminal_status == "SUBMITTED"
                else (
                    "NO_ACTION"
                    if terminal_status == "AUTHORIZED_NO_TRADE"
                    else "HALTED"
                )
            ),
            "operator_execution_status": (
                "reconciled_success"
                if final_terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
                else final_terminal_outcome.value.lower()
            ),
            "terminal_status": terminal_status,
            "terminal_outcome": final_terminal_outcome.value,
            "reconciliation_status": final_reconciliation_status,
            "halt_reason": (
                None
                if terminal_status in {"SUBMITTED", "AUTHORIZED_NO_TRADE", "DRY_RUN"}
                else final_reason
            ),
        }
    )
    _write_json(execution_payload_path, execution_payload)
    _write_json(
        run_root / "execution_results.json",
        {
            **result,
            "raw_execution_status": result.get("status"),
            "status": terminal_status,
            "run_id": run_id,
            "trade_date": trade_date,
            "mode": "PAPER",
            "terminal_status": terminal_status,
            "terminal_outcome": final_terminal_outcome.value,
            "reason_code": final_reason,
            "failure_class": (
                final_failure_class.value if final_failure_class is not None else None
            ),
            "reconciliation_status": final_reconciliation_status,
            "run_root": str(run_root),
        },
    )
    integrity_path = audit_dir / "execution_integrity.json"
    integrity_payload = _load_plan(integrity_path) if integrity_path.exists() else {}
    integrity_payload.update(
        {
            "status": (
                "OK"
                if terminal_status in {"SUBMITTED", "AUTHORIZED_NO_TRADE", "DRY_RUN"}
                and final_terminal_outcome
                in {
                    TerminalOutcome.RECONCILED_SUCCESS,
                    TerminalOutcome.AUTHORIZED_NO_TRADE,
                }
                else "FAIL"
            ),
            "terminal_outcome": final_terminal_outcome.value,
            "reconciliation_status": final_reconciliation_status,
            "findings": (
                []
                if terminal_status in {"SUBMITTED", "AUTHORIZED_NO_TRADE", "DRY_RUN"}
                else [final_reason]
            ),
        }
    )
    _write_json(integrity_path, integrity_payload)
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
    allow_fractional, fractional_policy_error, fractional_policy = _resolve_fractional_policy(
        plan,
        environ,
    )
    preflight.update(fractional_policy)
    _write_json(run_root / "live_pilot_preflight.json", preflight)

    require_approved_package = str(
        environ.get("CAERUS_REQUIRE_APPROVED_EXECUTION_PACKAGE") or ""
    ).strip().lower() in {"1", "true", "yes", "y", "on"}
    require_exact_package = str(
        environ.get("CAERUS_REQUIRE_EXACT_EXECUTION_PLAN") or ""
    ).strip().lower() in {"1", "true", "yes", "y", "on"}
    # PAPER has one structural execution authority. Environment flags may
    # tighten behavior but can never re-enable mutable target reconstruction.
    test_only_legacy_fake = bool(
        os.environ.get("PYTEST_CURRENT_TEST")
        and broker is not None
        and not isinstance(broker, AlpacaBroker)
        and str(environ.get("CAERUS_TEST_ONLY_ALLOW_LEGACY_FAKE_EXECUTION") or "")
        .strip()
        .lower()
        in {"1", "true", "yes"}
    )
    paper_scope_requires_exact = (
        str(gate.requested_mode or "").strip().lower() == PAPER_MODE
        and not test_only_legacy_fake
    )
    if (require_exact_package or paper_scope_requires_exact) and not isinstance(
        plan.get("exact_execution_plan"), Mapping
    ):
        return _write_blocked_artifacts(
            run_root=run_root,
            run_id=run_id,
            trade_date=trade_date,
            env=environ,
            reason_code="exact_execution_plan_required",
            operator_action=(
                "Run the fresh broker-state Decision/Authorization stage. The "
                "executor cannot reconstruct orders from targets or precompute."
            ),
            preflight=preflight,
        )
    live_capital_scope = (
        str(gate.requested_mode or "").strip().lower() == LIVE_PILOT_MODE
        or not broker_paper
    )
    if live_capital_scope and not test_only_legacy_fake:
        return _write_blocked_artifacts(
            run_root=run_root,
            run_id=run_id,
            trade_date=trade_date,
            env=environ,
            reason_code="live_capital_disabled_by_owner_policy",
            operator_action=(
                "Live capital remains code-level disabled. A future owner-approved "
                "transition must introduce a separately reviewed exact-plan scope."
            ),
            preflight=preflight,
        )
    if require_approved_package and not isinstance(
        plan.get("approved_execution_package"), Mapping
    ):
        return _write_blocked_artifacts(
            run_root=run_root,
            run_id=run_id,
            trade_date=trade_date,
            env=environ,
            reason_code="approved_execution_package_required",
            operator_action=(
                "Rebuild the plan through Decision and Pre-Trade Risk; Trader "
                "may not execute mutable target rows directly."
            ),
            preflight=preflight,
        )

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
        **fractional_policy,
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

    if fractional_policy_error:
        return _write_blocked_artifacts(
            run_root=run_root,
            run_id=run_id,
            trade_date=trade_date,
            env=environ,
            reason_code=fractional_policy_error,
            operator_action=(
                "Make CAERUS_LIVE_PILOT_ALLOW_FRACTIONAL match the immutable plan "
                "allow_fractional policy, then repeat the dry run before submission."
            ),
            preflight=preflight,
        )

    if broker is None:
        broker = AlpacaBroker.from_env()
        broker_paper = bool(getattr(broker, "paper", broker_paper))
        base_url = str(getattr(broker, "base_url", "") or base_url)

    paper_mode = str(gate.requested_mode or "").strip().lower() == PAPER_MODE
    try:
        pre_snapshot = _broker_snapshot(
            broker,
            fail_on_open_order_lookup=paper_mode,
        )
    except Exception as exc:
        transient_paper_failure = paper_mode and is_retryable_broker_read_error(exc)
        reason_code = (
            "paper_broker_snapshot_transient_failed"
            if transient_paper_failure
            else "paper_broker_snapshot_non_retryable"
            if paper_mode
            else "live_pilot_pre_snapshot_failed"
        )
        return _write_blocked_artifacts(
            run_root=run_root,
            run_id=run_id,
            trade_date=trade_date,
            env=environ,
            reason_code=reason_code,
            operator_action=f"Resolve read-only broker snapshot failure before execution: {exc}",
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
        preflight.update(
            {
                "account_id_match": account_gate.account_id_match,
                "account_match_reason": account_gate.account_match_reason,
                "expected_account_id_present": account_gate.expected_account_id_present,
                "expected_account_id_hash_present": account_gate.expected_account_id_hash_present,
                "account_pin_checked_at": _now_utc(),
            }
        )
        _write_json(run_root / "live_pilot_preflight.json", preflight)
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

    if isinstance(plan.get("exact_execution_plan"), Mapping):
        return _run_exact_execution_path(
            plan=plan,
            broker=broker,
            env=environ,
            preflight=preflight,
            pre_snapshot=pre_snapshot,
            run_root=run_root,
            run_id=run_id,
            trade_date=trade_date,
            output_root=output_root,
            dry_run=bool(gate.dry_run),
        )

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
        allow_fractional=allow_fractional,
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
    if _derive_execution_mode(run_root) == LIVE_PILOT_MODE.upper():
        # Confirmation is allowed to load the shared repository .env for SMTP,
        # but broker truth for a LIVE_PILOT run must never be fetched from the
        # paper account (or a different live account). Validate the read-only
        # broker context before looking up orders or overwriting canonical run
        # artifacts.
        account = broker.get_account() if hasattr(broker, "get_account") else {}
        actual_hash = account_id_hash((account or {}).get("id")) if (account or {}).get("id") else ""
        pre_snapshot_path = run_root / "live_pilot_broker_snapshot_pre.json"
        pre_snapshot = _load_plan(pre_snapshot_path) if pre_snapshot_path.exists() else {}
        run_account_hash = str(
            ((pre_snapshot.get("account") or {}).get("account_id_hash") or "")
        ).strip().lower()
        pinned_account_hash = str(
            os.getenv("CAERUS_LIVE_PILOT_ACCOUNT_ID_HASH") or ""
        ).strip().lower()
        context_errors: list[str] = []
        if getattr(broker, "paper", None) is True:
            context_errors.append("paper_broker")
        base_url = str(getattr(broker, "base_url", "") or "").strip()
        if base_url and not _is_live_host(base_url):
            context_errors.append("non_live_endpoint")
        if not actual_hash:
            context_errors.append("missing_broker_account_id")
        if run_account_hash and actual_hash.lower() != run_account_hash:
            context_errors.append("run_account_hash_mismatch")
        if pinned_account_hash and actual_hash.lower() != pinned_account_hash:
            context_errors.append("pinned_account_hash_mismatch")
        if context_errors:
            raise RuntimeError(
                "live_pilot_refresh_context_invalid:" + ",".join(context_errors)
            )

    intended_payload = _load_plan(run_root / "live_pilot_orders_intended.json")
    submitted_payload = _load_plan(run_root / "live_pilot_orders_submitted.json")
    intended = _trades_from_plan(intended_payload)
    approved_rows = intended_payload.get("approved_orders")
    reporting_intended = (
        [dict(row) for row in approved_rows if isinstance(row, Mapping)]
        if isinstance(approved_rows, list)
        else [dict(row) for row in intended if isinstance(row, Mapping)]
    )
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

    preflight_payload = (
        _load_plan(run_root / "live_pilot_preflight.json")
        if (run_root / "live_pilot_preflight.json").exists()
        else {}
    )
    execution_payload = (
        _load_plan(run_root / "live_pilot_execution_payload.json")
        if (run_root / "live_pilot_execution_payload.json").exists()
        else {}
    )
    canonical_execution_payload = (
        _load_plan(run_root / "execution_payload.json")
        if (run_root / "execution_payload.json").exists()
        else {}
    )
    trade_date = str(
        preflight_payload.get("trade_date")
        or execution_payload.get("trade_date")
        or os.getenv("REPORT_DATE")
        or ""
    )
    equality_gate = _write_unified_equality_gate(
        run_root=run_root,
        run_id=run_root.name,
        trade_date=trade_date,
        result=SimpleNamespace(),
        intended=intended,
        submitted=refreshed,
        plan={
            "approved_execution_package": canonical_execution_payload.get(
                "approved_execution_package"
            )
        },
        enforce=isinstance(
            canonical_execution_payload.get("approved_execution_package"), Mapping
        ),
    )

    # Reconciliation refresh changes the broker-truth lifecycle state, so the
    # dependent target-attainment audit must be refreshed in the same write
    # boundary. Prefer targets already captured by the run audit; this avoids
    # rereading a mutable external plan after execution. Older runs without a
    # captured audit may fall back to their recorded plan path.
    target_attainment_path = (
        run_root / "audit" / f"execution_target_attainment_{trade_date}.json"
    )
    prior_target_attainment = (
        _load_plan(target_attainment_path) if target_attainment_path.exists() else {}
    )
    captured_positions = prior_target_attainment.get("positions")
    target_plan: dict[str, Any] | None = None
    target_plan_source = ""
    if isinstance(captured_positions, list):
        target_plan = {
            "target_portfolio": [
                {
                    "symbol": row.get("symbol"),
                    "target_weight": row.get("target_weight"),
                }
                for row in captured_positions
                if isinstance(row, Mapping)
                and str(row.get("symbol") or "").strip()
                and _finite_float(row.get("target_weight")) is not None
            ],
            "cash_target_weight": prior_target_attainment.get("target_cash_weight"),
            "target_attainment_policy": (
                prior_target_attainment.get("target_attainment_policy")
                or canonical_execution_payload.get("target_attainment_policy")
            ),
            "approved_execution_package": canonical_execution_payload.get(
                "approved_execution_package"
            ),
        }
        target_plan_source = f"{target_attainment_path}:captured_targets"
    else:
        plan_reference = str(execution_payload.get("plan_path") or "").strip()
        if plan_reference:
            recorded_plan_path = Path(plan_reference)
            if not recorded_plan_path.is_absolute():
                recorded_plan_path = REPO_ROOT / recorded_plan_path
            try:
                target_plan = _load_plan(recorded_plan_path)
                target_plan_source = str(recorded_plan_path)
            except Exception as exc:
                target_plan = None
                target_plan_source = f"{recorded_plan_path}:unavailable:{exc}"

    target_attainment: dict[str, Any] = {}
    if target_plan is not None:
        try:
            from core.lane_target_attainment import build_lane_target_attainment

            target_attainment = build_lane_target_attainment(
                plan=target_plan,
                post_snapshot=post_snapshot,
                reconciliation=reconciliation,
                run_id=run_root.name,
                trade_date=trade_date,
                mode=_derive_execution_mode(run_root),
                dry_run=False,
                drift_tolerance=float(
                    _finite_float(
                        canonical_execution_payload.get(
                            "target_attainment_tolerance"
                        )
                    )
                    or 0.02
                ),
                feasibility_evidence=(
                    prior_target_attainment.get("whole_share_feasibility")
                    or canonical_execution_payload.get("whole_share_feasibility")
                ),
            )
            target_sources = dict(target_attainment.get("source_artifacts") or {})
            target_sources["plan"] = target_plan_source
            target_attainment["source_artifacts"] = target_sources
        except Exception as exc:
            target_attainment = {
                "schema_version": "caerus_lane_target_attainment_v2",
                "run_id": run_root.name,
                "trade_date": trade_date,
                "account_scope": _derive_execution_mode(run_root),
                "status": "UNKNOWN_INSUFFICIENT_BROKER_SNAPSHOT",
                "reason_code": f"target_attainment_refresh_failed:{exc}",
                "source_artifacts": {"plan": target_plan_source},
            }
        target_attainment_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(target_attainment_path, target_attainment)

    capital_gate = (
        _load_plan(run_root / "live_pilot_capital_gate.json")
        if (run_root / "live_pilot_capital_gate.json").exists()
        else {}
    )
    open_order_check = (
        _load_plan(run_root / "live_pilot_open_order_check.json")
        if (run_root / "live_pilot_open_order_check.json").exists()
        else {}
    )
    prior_evidence = (
        _load_plan(run_root / "live_pilot_evidence_metrics.json")
        if (run_root / "live_pilot_evidence_metrics.json").exists()
        else {}
    )
    capital_cap_usd = _finite_float(
        prior_evidence.get("capital_cap_usd")
        or capital_gate.get("approved_cap_usd")
        or capital_gate.get("capital_cap_usd")
    )
    entry_summary = _entry_policy_summary(
        intended=reporting_intended,
        submitted=refreshed,
        dry_run=False,
    )
    suppressed_reason = str(
        intended_payload.get("blocked_or_suppressed_buy_reason")
        or prior_evidence.get("blocked_or_suppressed_buy_reason")
        or NO_ESCALATION_REASON
    )
    if int(entry_summary.get("remaining_blocked_or_suppressed_buy_count") or 0) > 0:
        entry_summary["blocked_or_suppressed_buy_reason"] = suppressed_reason
    else:
        entry_summary["blocked_or_suppressed_buy_reason"] = NO_ESCALATION_REASON
    evidence_metrics = _build_evidence_metrics(
        dry_run=False,
        intended=reporting_intended,
        submitted=refreshed,
        reconciliation=reconciliation,
        capital_cap_usd=capital_cap_usd,
        open_order_check=open_order_check,
        capital_gate=capital_gate,
    )
    evidence_metrics.update(entry_summary)
    _write_json(run_root / "live_pilot_evidence_metrics.json", evidence_metrics)

    prior_capital_usage = (
        _load_plan(run_root / "live_pilot_capital_usage.json")
        if (run_root / "live_pilot_capital_usage.json").exists()
        else {}
    )
    _write_json(
        run_root / "live_pilot_capital_usage.json",
        {
            **prior_capital_usage,
            "schema_version": "live_pilot_capital_usage.v1",
            "generated_at": _now_utc(),
            "capital_cap_usd": capital_cap_usd,
            "submitted_notional_usd": round(
                sum(
                    abs(_finite_float(row.get("notional")) or 0.0)
                    for row in refreshed
                    if _status_bucket(row.get("status")) != "rejected"
                ),
                6,
            ),
            "filled_notional_usd": evidence_metrics.get("filled_notional_usd"),
            "filled_buy_notional_usd": evidence_metrics.get("filled_buy_notional_usd"),
            "cash_deployment_rate": evidence_metrics.get("cash_deployment_rate"),
            "dry_run": False,
            **_capital_gate_report_fields(capital_gate),
        },
    )

    _refreshed_recon_status = str(reconciliation.get("status") or "")
    if _refreshed_recon_status == "CLEAN":
        _refreshed_terminal_status = "SUBMITTED"
    elif _refreshed_recon_status == "SUBMITTED_UNFILLED":
        _refreshed_terminal_status = "SUBMITTED_UNFILLED"
    else:
        _refreshed_terminal_status = "FAILED_RECONCILIATION"
    prior_summary = (
        _load_plan(run_root / "live_pilot_operator_summary.json")
        if (run_root / "live_pilot_operator_summary.json").exists()
        else {}
    )
    summary = {
        **prior_summary,
        "schema_version": "live_pilot_operator_summary.v1",
        "generated_at": _now_utc(),
        "run_id": run_root.name,
        "mode": _derive_execution_mode(run_root),
        "terminal_status": _refreshed_terminal_status,
        "reason_code": reconciliation.get("status"),
        "live_orders_allowed": True,
        "dry_run": False,
        "intended_count": len(reporting_intended),
        "submitted_count": len(refreshed),
        "filled_count": reconciliation.get("filled_count"),
        "fill_rate": evidence_metrics.get("fill_rate"),
        "cash_deployment_rate": evidence_metrics.get("cash_deployment_rate"),
        "idle_cash_reason": evidence_metrics.get("idle_cash_reason"),
        "operator_action": reconciliation.get("operator_action"),
        "execution_equality_status": equality_gate.get("decision"),
        "run_root": str(run_root),
        "refreshed_existing_run": True,
        **_next_run_expectation(
            terminal_status=_refreshed_terminal_status,
            reason_code=reconciliation.get("status"),
            reconciliation_state=reconciliation.get("state"),
        ),
    }
    if target_attainment:
        summary.update(
            {
                "execution_target_attainment_status": target_attainment.get("status"),
                "execution_target_attainment_reason": target_attainment.get("reason_code"),
            }
        )
    summary.update(entry_summary)
    _write_json(run_root / "live_pilot_operator_summary.json", summary)
    _write_json(
        run_root / "execution_results.json",
        _build_live_pilot_execution_results(
            run_id=run_root.name,
            trade_date=trade_date,
            terminal_status=str(summary.get("terminal_status") or ""),
            reason_code=reconciliation.get("status"),
            intended=reporting_intended,
            submitted=refreshed,
            reconciliation=reconciliation,
            dry_run=False,
            run_root=run_root,
            extra_fields={
                **_capital_gate_report_fields(capital_gate),
                **entry_summary,
                "cash_deployment_rate": evidence_metrics.get("cash_deployment_rate"),
                "idle_cash_reason": evidence_metrics.get("idle_cash_reason"),
                **(
                    {
                        "execution_target_attainment_status": target_attainment.get("status"),
                        "execution_target_attainment_reason": target_attainment.get("reason_code"),
                    }
                    if target_attainment
                    else {}
                ),
            },
        ),
    )
    authority_plan = {
        "approved_execution_package": canonical_execution_payload.get(
            "approved_execution_package"
        ),
        "decision_source_artifact": canonical_execution_payload.get(
            "decision_source_artifact"
        ),
        "target_attainment_tolerance": canonical_execution_payload.get(
            "target_attainment_tolerance"
        ),
        "target_attainment_policy": canonical_execution_payload.get(
            "target_attainment_policy"
        ),
    }
    pre_snapshot = (
        _load_plan(run_root / "live_pilot_broker_snapshot_pre.json")
        if (run_root / "live_pilot_broker_snapshot_pre.json").exists()
        else {}
    )
    _write_canonical_authority_artifacts(
        run_root=run_root,
        run_id=run_root.name,
        trade_date=trade_date,
        plan=authority_plan,
        preflight=preflight_payload,
        pre_snapshot=pre_snapshot,
        submitted=refreshed,
        reconciliation=reconciliation,
        target_attainment=target_attainment,
        summary=summary,
    )
    return summary

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the isolated Caerus LIVE_PILOT executor")
    parser.add_argument("--plan", default=None, help="Path to a live pilot JSON plan")
    parser.add_argument("--refresh-run", default=None, help="Read-only refresh for an existing live pilot run root")
    parser.add_argument("--run-id", default=None, help="Optional deterministic run id")
    parser.add_argument("--output-root", default="outputs/live_pilot", help="Isolated live pilot output root")
    return parser.parse_args(argv)


def recover_exact_run(run_root: Path) -> dict[str, Any]:
    """Resume one exact-v3 PAPER run through WAL/client-ID recovery only."""

    payload = _load_plan(run_root / "execution_payload.json")
    exact_payload = payload.get("exact_execution_plan")
    if (
        payload.get("execution_source") != "exact_execution_plan_v3"
        or not isinstance(exact_payload, Mapping)
    ):
        raise RuntimeError("refresh-run is not an exact-v3 PAPER recovery artifact")
    from authority.exact_plan import exact_execution_plan_from_dict

    exact = exact_execution_plan_from_dict(
        exact_payload,
        expected_account_scope="PAPER",
    )
    recovery_id = (
        f"{run_root.name}.recovery."
        f"{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}"
    )
    handoff = {
        "schema_version": "caerus.authorized_execution_handoff.v1",
        "trade_date": exact.trade_date,
        "status": "AUTHORIZED_NO_TRADE" if not exact.orders else "AUTHORIZED_EXACT_PLAN",
        "exact_execution_plan": exact.to_dict(),
        "exact_execution_plan_id": exact.plan_id,
        "exact_execution_plan_hash": exact.content_hash,
        "exact_execution_authority_run_id": exact.run_id,
        "execution_authority": "exact_execution_plan_only",
        "precompute_execution_authority": False,
        "recovery_source_run_root": str(run_root),
    }
    return run_live_pilot(
        plan=handoff,
        run_id=recovery_id,
        output_root=run_root.parent.parent,
        env=os.environ,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.refresh_run:
        refresh_root = Path(args.refresh_run)
        payload_path = refresh_root / "execution_payload.json"
        payload = _load_plan(payload_path) if payload_path.exists() else {}
        if payload.get("execution_source") == "exact_execution_plan_v3":
            result = recover_exact_run(refresh_root)
        else:
            result = refresh_live_pilot_reconciliation(run_root=refresh_root)
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
    return 0 if str(result.get("terminal_status") or "").upper() in {
        "DRY_RUN",
        "SUBMITTED",
        "AUTHORIZED_NO_TRADE",
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())

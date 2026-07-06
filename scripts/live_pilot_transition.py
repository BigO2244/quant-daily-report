"""Adapter: live-pilot broker snapshot + precompute target -> shared Transition Engine.

Workstream C Phase 2. This is the seam where the live pilot lane stops using its
bespoke buy-only narrowing and instead consumes ``transition.compute_transition``.
It is a pure mapping layer (no broker calls, no I/O, no clock) between the live
artifacts and the engine contracts, plus serializers for the evidence artifacts.

Confirmed operator decisions in force (Option A):
- ``sells_supported=False``: rotation required -> the engine blocks with
  ``EXISTING_POSITIONS_REQUIRE_ROTATION``; the operator flattens manually.
- ``order_policy``: shares, fractional per the live flag, ``min_trade_usd=100``.
- Live capital semantics match the merged capital gate: ``tradable_capital =
  min(cash, broker_buying_power, approved_cap, per-name need)`` with **no reserve**
  (the approved cap is the risk ceiling; the $100 paper reserve does not apply to a
  $500 pilot). The cap is a ceiling, never treated as spendable cash.
"""

from __future__ import annotations

from typing import Any, Mapping

from transition import (
    AccountSnapshot,
    BLOCK_BUYING_POWER_UNAVAILABLE,
    BLOCK_INSUFFICIENT_BUYING_POWER,
    BLOCK_ROTATION_UNSUPPORTED,
    BLOCK_SELLS_NOT_FILLED,
    CapitalPolicy,
    Holdings,
    ModeConstraints,
    OrderPolicy,
    Position,
    TargetPortfolio,
    TargetPosition,
    TransitionPlan,
    compute_transition,
)

# Capital-gate.v1 block-reason constants (values mirror scripts/live_pilot_execute.py;
# duplicated here as literals to avoid a circular import with the executor).
LIVE_PILOT_BLOCKED_EXISTING_POSITIONS_REQUIRE_ROTATION = (
    "LIVE_PILOT_BLOCKED_EXISTING_POSITIONS_REQUIRE_ROTATION"
)
LIVE_PILOT_BLOCKED_INSUFFICIENT_BUYING_POWER = "LIVE_PILOT_BLOCKED_INSUFFICIENT_BUYING_POWER"
LIVE_PILOT_BLOCKED_BUYING_POWER_UNAVAILABLE = "LIVE_PILOT_BLOCKED_BUYING_POWER_UNAVAILABLE"

# Engine block_reason -> capital_gate.v1 constant (the live lane's operator-facing code).
_BLOCK_REASON_TO_GATE_CONSTANT = {
    BLOCK_ROTATION_UNSUPPORTED: LIVE_PILOT_BLOCKED_EXISTING_POSITIONS_REQUIRE_ROTATION,
    BLOCK_BUYING_POWER_UNAVAILABLE: LIVE_PILOT_BLOCKED_BUYING_POWER_UNAVAILABLE,
    BLOCK_INSUFFICIENT_BUYING_POWER: LIVE_PILOT_BLOCKED_INSUFFICIENT_BUYING_POWER,
    # Option A never sells, so SELLS_NOT_FILLED only arises from unresolved open sell
    # orders in the snapshot; surface it as an insufficient-capital block for the lane.
    BLOCK_SELLS_NOT_FILLED: LIVE_PILOT_BLOCKED_INSUFFICIENT_BUYING_POWER,
}

LIVE_PILOT_ALLOW_FRACTIONAL_ENV = "CAERUS_LIVE_PILOT_ALLOW_FRACTIONAL"
LIVE_PILOT_MIN_TRADE_USD = 100.0
TRANSITION_PLAN_SCHEMA = "caerus.transition_plan.v1"

_TRUE_TOKENS = frozenset({"1", "true", "yes", "y", "on"})


def _safe_float(value: object) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _norm(symbol: object) -> str:
    return str(symbol or "").strip().upper()


def gate_block_reason(engine_reason: str | None) -> str | None:
    if engine_reason is None:
        return None
    return _BLOCK_REASON_TO_GATE_CONSTANT.get(engine_reason, engine_reason)


# --------------------------------------------------------------------------- #
# Snapshot / plan -> engine contracts
# --------------------------------------------------------------------------- #
def holdings_from_snapshot(pre_snapshot: Mapping[str, Any]) -> Holdings:
    """Broker positions -> engine Holdings. Reference price = market_value / qty."""
    positions: list[Position] = []
    for raw in pre_snapshot.get("positions") or []:
        if not isinstance(raw, Mapping):
            continue
        symbol = _norm(raw.get("symbol"))
        qty = _safe_float(raw.get("qty"))
        if not symbol or qty is None or abs(qty) <= 1e-12:
            continue
        market_value = _safe_float(raw.get("market_value"))
        price = abs(market_value / qty) if market_value is not None and qty != 0 else 0.0
        positions.append(Position(symbol=symbol, shares=qty, price=price))
    return Holdings(tuple(positions))


def account_from_snapshot(pre_snapshot: Mapping[str, Any]) -> AccountSnapshot:
    account = pre_snapshot.get("account") if isinstance(pre_snapshot.get("account"), Mapping) else {}
    account = account or {}
    open_sell_orders = 0
    for order in pre_snapshot.get("open_orders") or []:
        if isinstance(order, Mapping) and str(order.get("side") or "").strip().upper() == "SELL":
            open_sell_orders += 1
    return AccountSnapshot(
        cash=_safe_float(account.get("cash")),
        buying_power=_safe_float(account.get("buying_power")),
        equity=_safe_float(account.get("equity") or account.get("portfolio_value")),
        as_of=str(pre_snapshot.get("captured_at") or ""),
        open_sell_orders=open_sell_orders,
    )


def _target_price(row: Mapping[str, Any]) -> float | None:
    for key in ("price", "limit_price", "expected_price", "normalized_limit_price", "entry_price"):
        value = _safe_float(row.get(key))
        if value is not None and value > 0:
            return value
    return None


def _target_notional(row: Mapping[str, Any], price: float) -> float | None:
    notional = _safe_float(row.get("notional"))
    if notional is not None and notional > 0:
        return notional
    shares = _safe_float(row.get("shares") or row.get("qty") or row.get("quantity"))
    if shares is not None and shares > 0 and price > 0:
        return shares * price
    return None


def target_from_plan(plan: Mapping[str, Any], *, equity: float) -> TargetPortfolio:
    """Build the full target portfolio the engine sizes against.

    Prefers the builder's ``target_portfolio`` block (schema
    ``caerus.transition_target.v1``, all names + weights). Falls back to deriving a
    target from the plan's ``trades`` rows (weight = row_notional / equity) so that
    pre-existing single-order plans still drive the engine. Weight is only used for
    (a) max_orders selection priority and (b) target-share sizing; when equity is
    unknown, weights collapse to notional ordering, which preserves priority.
    """
    rows = plan.get("target_portfolio")
    if not isinstance(rows, list) or not rows:
        rows = plan.get("trades") or plan.get("orders") or []
    positions: list[TargetPosition] = []
    eq = float(equity) if equity and equity > 0 else 0.0
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        symbol = _norm(raw.get("symbol") or raw.get("ticker"))
        if not symbol:
            continue
        price = _target_price(raw)
        if price is None:
            continue
        explicit_weight = _safe_float(raw.get("target_weight"))
        if explicit_weight is not None and explicit_weight > 0:
            weight = explicit_weight
        else:
            notional = _target_notional(raw, price)
            if notional is None:
                continue
            # Weight as a fraction of equity so target_dollars = weight*equity = notional.
            # When equity is unknown, use the notional directly as the priority key.
            weight = (notional / eq) if eq > 0 else notional
        positions.append(TargetPosition(symbol=symbol, target_weight=weight, price=price))
    return TargetPortfolio(tuple(positions), cash_buffer=1.0)


def order_policy_from_env(env: Mapping[str, str]) -> OrderPolicy:
    fractional = str(env.get(LIVE_PILOT_ALLOW_FRACTIONAL_ENV) or "").strip().lower() in _TRUE_TOKENS
    return OrderPolicy(units="shares", fractional=fractional, min_trade_usd=LIVE_PILOT_MIN_TRADE_USD)


def live_capital_policy(approved_cap_usd: float | None) -> CapitalPolicy:
    # Live: no reserve (the approved cap is the risk ceiling; the paper $100 post-sell
    # reserve does not apply to a $500 pilot). Matches the merged capital gate.
    return CapitalPolicy(
        approved_cap_usd=_safe_float(approved_cap_usd),
        reserve=0.0,
        risk_cash_weight=0.0,
        reserve_equity_pct=0.0,
        reserve_max_pct=0.0,
    )


def compute_live_transition(
    *,
    pre_snapshot: Mapping[str, Any],
    plan: Mapping[str, Any],
    approved_cap_usd: float | None,
    env: Mapping[str, str],
    max_orders: int = 1,
) -> TransitionPlan:
    account = account_from_snapshot(pre_snapshot)
    return compute_transition(
        current_holdings=holdings_from_snapshot(pre_snapshot),
        target_holdings=target_from_plan(plan, equity=account.equity or 0.0),
        account_snapshot=account,
        capital_policy=live_capital_policy(approved_cap_usd),
        order_policy=order_policy_from_env(env),
        mode_constraints=ModeConstraints(sells_supported=False, max_orders=max_orders),
    )


# --------------------------------------------------------------------------- #
# TransitionPlan -> live artifacts
# --------------------------------------------------------------------------- #
def transition_plan_artifact(plan: TransitionPlan, *, generated_at: str) -> dict[str, Any]:
    def _sell(s: Any) -> dict[str, Any]:
        return {"symbol": s.symbol, "shares": s.shares, "price": s.price,
                "notional": s.notional, "reason": s.reason}

    def _buy(b: Any) -> dict[str, Any]:
        return {"symbol": b.symbol, "shares": b.shares, "price": b.price,
                "notional": b.notional, "reason": b.reason}

    return {
        "schema_version": TRANSITION_PLAN_SCHEMA,
        "generated_at": generated_at,
        "blocked": plan.blocked,
        "block_reason": plan.block_reason,
        "holdings_to_keep": list(plan.holdings_to_keep),
        "holdings_to_reduce": list(plan.holdings_to_reduce),
        "holdings_to_sell": list(plan.holdings_to_sell),
        "holdings_to_increase": list(plan.holdings_to_increase),
        "buy_needs": list(plan.buy_needs),
        "sell_orders_intended": [_sell(s) for s in plan.sell_orders_intended],
        "buy_orders_intended": [_buy(b) for b in plan.buy_orders_intended],
        "diagnostics": dict(plan.diagnostics),
    }


def capital_gate_artifact(
    plan: TransitionPlan,
    *,
    positions_before: list[dict[str, Any]],
    open_orders_before: list[dict[str, Any]],
    approved_cap_usd: float | None,
    generated_at: str,
) -> dict[str, Any]:
    """Map the engine plan to the live_pilot_capital_gate.v1 evidence shape.

    Preserves the merged artifact schema/fields so downstream consumers and evidence
    are unchanged; the DECISION now comes from the shared engine, not a parallel
    implementation. ``required_sell_count`` now counts actual engine-computed sells
    (exits + reduces), not "any position held" — a behavior refinement documented for
    operator sign-off.
    """
    diag = plan.diagnostics
    gate_reason = gate_block_reason(plan.block_reason)
    return {
        "schema_version": "live_pilot_capital_gate.v1",
        "generated_at": generated_at,
        "decision": "BLOCKED" if plan.blocked else "ALLOWED",
        "block_reason": gate_reason,
        "live_positions_before": positions_before,
        "live_open_orders_before": open_orders_before,
        "live_buying_power_before": diag.get("buying_power"),
        "approved_cap_usd": _safe_float(approved_cap_usd),
        "required_sell_count": int(diag.get("required_sell_count") or 0),
        "sell_first_supported": False,
        "rebudget_after_sell_supported": False,
        "strategy_allocation_cap_usd": diag.get("planned_buy_notional") or None,
        "planned_buy_notional_usd": diag.get("planned_buy_notional"),
        "tradable_capital_usd": diag.get("tradable_capital"),
        "buy_block_reason": gate_reason,
        "broker_orders_submitted": 0,
        "engine_block_reason": plan.block_reason,
        "operator_action": _operator_action(plan.block_reason),
    }


def _operator_action(engine_reason: str | None) -> str:
    if engine_reason == BLOCK_ROTATION_UNSUPPORTED:
        return (
            "Existing live positions require sell-first rotation before any new live-pilot buy. "
            "The live-pilot lane does not automate sell-first (Option A); flatten the exit "
            "positions manually and leave the kill switch engaged until re-approved."
        )
    if engine_reason == BLOCK_BUYING_POWER_UNAVAILABLE:
        return "Live broker buying power was unavailable; the approved cap cannot be treated as cash."
    if engine_reason == BLOCK_INSUFFICIENT_BUYING_POWER:
        return (
            "Live broker buying power is below the planned buy notional. The approved pilot cap "
            "is a maximum risk limit, not a substitute for broker buying power."
        )
    if engine_reason == BLOCK_SELLS_NOT_FILLED:
        return "Unresolved live sell orders remain; buys are blocked until they fully resolve."
    return "Broker buying power, approved cap, and planned buy notional permit this live-pilot buy gate."


# Provenance/metadata fields carried through from the source plan onto the engine's
# selected buy intent (sizing fields are always taken from the engine, not the source).
_PROVENANCE_KEYS = (
    "sleeve",
    "sleeve_source",
    "sleeve_provenance",
    "source_strategy_id",
    "source_signal_sleeve",
    "source_signal_target_weight",
    "source_signal_raw_score",
    "source_precompute_index",
    "approved_sleeve_override",
    "stop_loss",
    "take_profit",
)


def _plan_rows_by_symbol(source_plan: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    rows: dict[str, Mapping[str, Any]] = {}
    if not source_plan:
        return rows
    candidates = source_plan.get("target_portfolio")
    if not isinstance(candidates, list) or not candidates:
        candidates = source_plan.get("trades") or source_plan.get("orders") or []
    for raw in candidates:
        if not isinstance(raw, Mapping):
            continue
        symbol = _norm(raw.get("symbol") or raw.get("ticker"))
        if symbol and symbol not in rows:
            rows[symbol] = raw
    return rows


def buy_intents_to_trades(
    plan: TransitionPlan, *, source_plan: Mapping[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Engine buy intents -> live-pilot plan trade rows for validate_live_pilot_plan.

    Market DAY orders (Option A order_policy). limit_price carries the reference price
    used for cap enforcement in the validator (validate_live_pilot_plan requires a
    positive cap-enforcement price even for market orders). Provenance metadata
    (sleeve, strategy id, signal sleeve, ...) is carried through from the matching
    source-plan row so downstream evidence keeps its lineage; sizing (shares/notional/
    price) always comes from the engine.
    """
    by_symbol = _plan_rows_by_symbol(source_plan)
    trades: list[dict[str, Any]] = []
    for b in plan.buy_orders_intended:
        source_row = by_symbol.get(b.symbol, {})
        provenance = {
            key: source_row[key] for key in _PROVENANCE_KEYS if key in source_row
        }
        # Carry the source order-type hint through so the executor's entry policy
        # applies the existing limit->market override evidence. The live order_policy
        # is market DAY (Option A); a limit hint is normalized to a marketable order
        # downstream, preserving the audit trail.
        order_type = str(source_row.get("order_type") or "market").strip().lower()
        if order_type not in {"market", "limit"}:
            order_type = "market"
        trades.append(
            {
                **provenance,
                "ticker": b.symbol,
                "symbol": b.symbol,
                "side": "BUY",
                "shares": b.shares,
                "qty": b.shares,
                "limit_price": b.price,
                "price": b.price,
                "expected_price": b.price,
                "cap_enforcement_price": b.price,
                "notional": b.notional,
                "order_type": order_type,
                "time_in_force": "day",
                "source_reason": b.reason,
            }
        )
    return trades

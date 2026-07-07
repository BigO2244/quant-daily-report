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

import math
from dataclasses import replace
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
LIVE_PILOT_TOTAL_NOTIONAL_EXCEEDS_CAP = "live_pilot_total_notional_exceeds_cap"
LIVE_PILOT_EQUITY_EXCEEDS_CAP_REGIME = "live_pilot_equity_exceeds_cap_regime"
LIVE_PILOT_EQUITY_UNAVAILABLE = "live_pilot_equity_unavailable"

# Live-pilot fail-closed policy (FR-104 pilot, operator decision 2026-07-06): an
# intended buy whose FULL incremental need exceeds the approved cap is HALTED, not
# silently right-sized to the cap. The shared engine would clip-and-deploy (paper
# semantics); for a first live-money run an over-cap intent is an anomaly to stop on.
# Relax to clip-and-deploy only after live behavior is trusted.
BLOCK_OVER_CAP_INTENT = "OVER_CAP_INTENT"

# Live-pilot equity-regime guard (operator decision 2026-07-06). The engine sizes
# targets to weight x live-account-equity and applies the approved cap PER RUN, not as
# a total-exposure ceiling. That is only correct while account equity ~= cap (the pilot
# is funded at ~$500 == the cap). If equity exceeds this ceiling, weight-based targets
# would be sized above the cap and the per-run cap would not bound total exposure (a
# partly-held over-target name could be topped up past the cap). Rather than make the
# cap exposure-aware now, we HALT if equity leaves the cap regime. Missing equity also
# blocks (we cannot confirm the regime). The ceiling is a tight collar just above the
# ~$500 pilot funding so there is no top-up tolerance band above the cap.
#   TODO / FR (deferred): when the pilot account is funded above the cap, replace this
#   guard with an exposure-aware cap (cap_remaining = approved_cap - current_exposure)
#   so weight-based targets can be sized correctly above the cap. Until then: block.
LIVE_PILOT_EQUITY_CAP_REGIME_CEILING_USD = 520.0
BLOCK_EQUITY_EXCEEDS_CAP_REGIME = "EQUITY_EXCEEDS_CAP_REGIME"
BLOCK_EQUITY_UNAVAILABLE = "EQUITY_UNAVAILABLE"

# Engine block_reason -> capital_gate.v1 constant (the live lane's operator-facing code).
_BLOCK_REASON_TO_GATE_CONSTANT = {
    BLOCK_ROTATION_UNSUPPORTED: LIVE_PILOT_BLOCKED_EXISTING_POSITIONS_REQUIRE_ROTATION,
    BLOCK_BUYING_POWER_UNAVAILABLE: LIVE_PILOT_BLOCKED_BUYING_POWER_UNAVAILABLE,
    BLOCK_INSUFFICIENT_BUYING_POWER: LIVE_PILOT_BLOCKED_INSUFFICIENT_BUYING_POWER,
    BLOCK_OVER_CAP_INTENT: LIVE_PILOT_TOTAL_NOTIONAL_EXCEEDS_CAP,
    BLOCK_EQUITY_EXCEEDS_CAP_REGIME: LIVE_PILOT_EQUITY_EXCEEDS_CAP_REGIME,
    BLOCK_EQUITY_UNAVAILABLE: LIVE_PILOT_EQUITY_UNAVAILABLE,
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


def _derived_position_price(market_value_raw: object, qty_raw: object) -> float | None:
    """Return the engine reference price for a clean broker position, else None."""
    qty = _safe_float(qty_raw)
    market_value = _safe_float(market_value_raw)
    if qty is None or not math.isfinite(qty) or abs(qty) <= 1e-12:
        return None
    if market_value is None or not math.isfinite(market_value) or market_value <= 0:
        return None
    price = abs(market_value / qty)
    if not math.isfinite(price) or price <= 0.0:
        return None
    return price


def gate_block_reason(engine_reason: str | None) -> str | None:
    if engine_reason is None:
        return None
    return _BLOCK_REASON_TO_GATE_CONSTANT.get(engine_reason, engine_reason)


# --------------------------------------------------------------------------- #
# Shared "is this a real holding?" predicate (single source of truth)
# --------------------------------------------------------------------------- #
def _holding_rejection_reasons(raw: Mapping[str, Any]) -> list[str]:
    """Reasons a snapshot position is NOT a clean real open holding; empty == real.

    This is the SINGLE source of the field checks. Both holdings_from_snapshot
    (inclusion) and the rotation guard (fail-closed) derive their decision from it, so
    no missing/blank/non-finite field (symbol, qty, market_value, or a future one) can
    be checked in one place but not the other. A real holding requires: truthy symbol,
    finite non-zero qty, and finite positive market_value.
    """
    reasons: list[str] = []
    if not _norm(raw.get("symbol")):
        reasons.append("missing_symbol")
    qty = _safe_float(raw.get("qty"))
    if qty is None or not math.isfinite(qty) or abs(qty) <= 1e-12:
        reasons.append("missing_zero_or_nonfinite_qty")
    market_value = _safe_float(raw.get("market_value"))
    if market_value is None or not math.isfinite(market_value) or market_value <= 0:
        reasons.append("missing_nonpositive_or_nonfinite_market_value")
    # Validate the exact reference price the engine will consume. qty and market_value
    # can each pass individually yet produce a degenerate quotient -- underflow to 0.0
    # or overflow to inf -- which the engine treats as unpriceable. Only classify this
    # after the field-level checks pass, preserving reason-code ordering.
    if not reasons and _derived_position_price(raw.get("market_value"), raw.get("qty")) is None:
        reasons.append("degenerate_price")
    return reasons


def _position_is_real_holding(raw: Any) -> bool:
    """True iff ``raw`` is a snapshot position that counts as a clean real open holding."""
    return isinstance(raw, Mapping) and not _holding_rejection_reasons(raw)


# --------------------------------------------------------------------------- #
# Snapshot / plan -> engine contracts
# --------------------------------------------------------------------------- #
def holdings_from_snapshot(pre_snapshot: Mapping[str, Any]) -> Holdings:
    """Broker positions -> engine Holdings.

    Includes exactly the positions that pass _position_is_real_holding, so the price
    derivation is always well-defined (finite non-zero qty, finite positive market_value).
    """
    positions: list[Position] = []
    for raw in pre_snapshot.get("positions") or []:
        if not _position_is_real_holding(raw):
            continue
        qty = _safe_float(raw.get("qty"))
        price = _derived_position_price(raw.get("market_value"), raw.get("qty"))
        if qty is None or price is None:
            continue
        positions.append(Position(symbol=_norm(raw.get("symbol")), shares=qty, price=price))
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


def _blocked_plan(
    plan: TransitionPlan, *, reason: str, extra_diag: Mapping[str, Any] | None = None
) -> TransitionPlan:
    """Return a fail-closed copy of ``plan``: blocked, no buys, deployed reset to 0.

    TODO (evidence nit, non-blocking): holdings_to_increase / buy_needs are left as the
    engine computed them, so a blocked plan can still list an "increase" for a symbol
    whose buy was removed. Execution is unaffected (blocked paths early-return before
    submission); revisit for artifact consistency.
    """
    diag = dict(plan.diagnostics)
    diag["deployed_buy_notional"] = 0.0
    if extra_diag:
        diag.update(extra_diag)
    return replace(
        plan,
        blocked=True,
        block_reason=reason,
        buy_orders_intended=(),
        diagnostics=diag,
    )


def _apply_equity_regime_policy(plan: TransitionPlan, account: AccountSnapshot) -> TransitionPlan:
    """Halt if account equity leaves the cap regime (equity ~= cap).

    See LIVE_PILOT_EQUITY_CAP_REGIME_CEILING_USD: the engine sizes targets to
    weight x equity and applies the cap per-run, which only bounds total exposure while
    equity ~= cap. Above the ceiling, block rather than mis-size.
    """
    if plan.blocked:
        return plan
    equity = account.equity
    if equity is None:
        # Fail closed with a DISTINCT reason: missing equity (e.g. a data-feed gap) is
        # semantically different from equity exceeding the cap, so the operator is not
        # misdirected to investigate an over-funded account.
        return _blocked_plan(
            plan,
            reason=BLOCK_EQUITY_UNAVAILABLE,
            extra_diag={"equity_usd": None, "equity_unavailable": True},
        )
    if float(equity) > LIVE_PILOT_EQUITY_CAP_REGIME_CEILING_USD:
        return _blocked_plan(
            plan,
            reason=BLOCK_EQUITY_EXCEEDS_CAP_REGIME,
            extra_diag={
                "equity_usd": float(equity),
                "equity_cap_regime_ceiling_usd": LIVE_PILOT_EQUITY_CAP_REGIME_CEILING_USD,
            },
        )
    return plan


def _apply_unpriceable_holdings_policy(
    plan: TransitionPlan, pre_snapshot: Mapping[str, Any]
) -> TransitionPlan:
    """Fail closed on any held position we cannot cleanly count AND price.

    A held position that maps to price 0.0 (missing market_value) or is uncountable
    (missing qty) is silently dropped by holdings_from_snapshot and the engine's exit
    loop, defeating the Option A rotation guard. The root property: a held open position
    is safe to skip ONLY if it is BOTH cleanly countable (qty present, non-null, non-zero)
    AND cleanly priceable (market_value present, non-null, positive). Both checks run
    before any skip, so no single missing field (qty, market_value, or a future field)
    can hide a real live holding. Anything failing either check -> rotation-required.
    """
    if plan.blocked:
        return plan
    for raw in pre_snapshot.get("positions") or []:
        if not isinstance(raw, Mapping):
            # Not a position object at all (list-level garbage), same as holdings_from_snapshot.
            continue
        if _position_is_real_holding(raw):
            # Clean holding: holdings_from_snapshot includes it and the engine accounts
            # for it (rotation/keep/increase). Nothing to fail closed on.
            continue
        # A position entry the engine would silently DROP because it is not a clean real
        # holding. We cannot rule out a mis-dropped real holding, so fail closed. Uses the
        # SAME field checks as the inclusion predicate (via _holding_rejection_reasons).
        return _blocked_plan(
            plan,
            reason=BLOCK_ROTATION_UNSUPPORTED,
            extra_diag={
                "unpriceable_holding_symbol": _norm(raw.get("symbol")) or None,
                "unpriceable_holding_reason": ",".join(_holding_rejection_reasons(raw)),
            },
        )
    return plan


def _apply_buying_power_policy(plan: TransitionPlan, account: AccountSnapshot) -> TransitionPlan:
    """Treat buying_power <= 0 (a real 0.0, not just None) as UNAVAILABLE.

    Broker buying power is the truth for live; cash must never substitute for it. The
    shared engine only blocks on None (and otherwise falls back to sizing against cash),
    so a legitimate 0.0 buying power must be blocked here to restore the merged-gate
    behavior.
    """
    if plan.blocked:
        return plan
    bp = account.buying_power
    # Fire on the raw broker fact plus ANY signal of buy intent -- either a sized buy
    # (buy_orders_intended) OR a pre-sizing need (planned_buy_notional > 0). The OR of
    # both is strictly more fail-closed than either alone: it also catches a bp=0 run
    # whose candidates were all clipped away by sizing, without decoupling from the fact.
    planned = float(plan.diagnostics.get("planned_buy_notional") or 0.0)
    if bp is not None and float(bp) <= 0.0 and (plan.buy_orders_intended or planned > 0.0):
        return _blocked_plan(
            plan,
            reason=BLOCK_BUYING_POWER_UNAVAILABLE,
            extra_diag={"buying_power_nonpositive": True},
        )
    return plan


def _apply_over_cap_policy(plan: TransitionPlan, approved_cap_usd: float | None) -> TransitionPlan:
    """Fail-closed over-cap halt for the live pilot (FR-104).

    The shared engine clips an over-cap need down to the cap and deploys it. For the
    live pilot we instead HALT: if the FULL incremental need of any intended buy
    exceeds the approved cap, block the run (no order) rather than silently right-sizing.
    Fails closed if the incremental need for a buy symbol is missing (cannot verify).
    """
    cap = _safe_float(approved_cap_usd)
    if plan.blocked or cap is None:
        return plan
    incremental = plan.diagnostics.get("incremental_need_shares") or {}
    for b in plan.buy_orders_intended:
        need_shares = incremental.get(b.symbol)
        if need_shares is None:
            # Fail closed: cannot verify this buy against the cap ceiling.
            return _blocked_plan(
                plan,
                reason=BLOCK_OVER_CAP_INTENT,
                extra_diag={
                    "over_cap_intent": True,
                    "over_cap_symbol": b.symbol,
                    "over_cap_reason": "missing_incremental_need",
                },
            )
        full_need = float(need_shares) * float(b.price)
        if full_need > cap + 1e-6:
            return _blocked_plan(
                plan,
                reason=BLOCK_OVER_CAP_INTENT,
                extra_diag={
                    "over_cap_intent": True,
                    "over_cap_symbol": b.symbol,
                    "over_cap_full_need_usd": round(full_need, 6),
                },
            )
    return plan


def compute_live_transition(
    *,
    pre_snapshot: Mapping[str, Any],
    plan: Mapping[str, Any],
    approved_cap_usd: float | None,
    env: Mapping[str, str],
    max_orders: int = 1,
) -> TransitionPlan:
    account = account_from_snapshot(pre_snapshot)
    engine_plan = compute_transition(
        current_holdings=holdings_from_snapshot(pre_snapshot),
        target_holdings=target_from_plan(plan, equity=account.equity or 0.0),
        account_snapshot=account,
        capital_policy=live_capital_policy(approved_cap_usd),
        order_policy=order_policy_from_env(env),
        mode_constraints=ModeConstraints(sells_supported=False, max_orders=max_orders),
    )
    # Live-pilot fail-closed guards, in priority order. Each is a no-op once the plan is
    # already blocked, so the first applicable reason wins.
    guarded = _apply_equity_regime_policy(engine_plan, account)
    guarded = _apply_unpriceable_holdings_policy(guarded, pre_snapshot)
    guarded = _apply_buying_power_policy(guarded, account)
    guarded = _apply_over_cap_policy(guarded, approved_cap_usd)
    return guarded


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
    if engine_reason == BLOCK_OVER_CAP_INTENT:
        return (
            "The intended buy's full need exceeds the approved cap. The pilot halts on "
            "over-cap intents rather than silently right-sizing them; review the target "
            "and cap, and leave the kill switch engaged until re-approved."
        )
    if engine_reason == BLOCK_EQUITY_EXCEEDS_CAP_REGIME:
        return (
            "Account equity exceeds the pilot cap regime; the live sizing model assumes "
            "equity is approximately the approved cap. Halt until either the account is "
            "brought back to the cap regime or an exposure-aware cap is approved."
        )
    if engine_reason == BLOCK_EQUITY_UNAVAILABLE:
        return (
            "Broker account equity was unavailable in the snapshot (likely a data-feed "
            "gap); the pilot cannot confirm the account is in the cap regime. Halt and "
            "verify the broker snapshot before any live action."
        )
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

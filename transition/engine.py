"""Pure transition engine: current + target + constraints -> transition plan.

Lifted from the paper transition core (``paper/paper_broker.py``) and the
live-pilot capital gate (branch ``codex/live-pilot-existing-positions-capital-gate``
/ commit ``6243757``), refactored into a pure, deterministic module:

* ``build_rebalance_trades``           -> target-minus-current diff (keep/reduce/sell/buy)
* ``_rebuild_post_sell_buy_trades``    -> the canonical post-sell buy rebudget/sizing
* ``_post_sell_buy_budget`` /
  ``_compute_buy_budget`` /
  ``_reserve_cash_for_equity``         -> capital budget (min of broker truth, cap, risk)
* ``_build_live_pilot_capital_gate``   -> the blocking predicate (rotation / buying power)

Design rules (V2.1 Parts II, VI, XI):
- No I/O, no broker calls, no environment reads, no ``datetime.now()``, no
  module-level configuration. Everything is injected via the frozen dataclasses
  below; the account ``as_of`` timestamp is passed in, never read from a clock.
- Deterministic: identical inputs -> identical plan. All ordering uses stable
  ``(-target_weight, symbol)`` sort keys.
- Fail closed for capital risk: missing buying power, unsupported rotation, or
  unresolved sells block before any buy intent is produced. The approved cap is a
  ceiling, never treated as spendable cash.

The engine ships alongside the existing code; nothing calls it in production yet
(Workstream B Task 1). Parity tests are the no-behavior-change bridge.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping

# --------------------------------------------------------------------------- #
# Block reasons (align with V2.1 Part VIII state machine + the capital gate)
# --------------------------------------------------------------------------- #
BLOCK_ROTATION_UNSUPPORTED = "EXISTING_POSITIONS_REQUIRE_ROTATION"
BLOCK_BUYING_POWER_UNAVAILABLE = "BLOCKED_BUYING_POWER_UNAVAILABLE"
BLOCK_INSUFFICIENT_BUYING_POWER = "BLOCKED_INSUFFICIENT_BUYING_POWER"
BLOCK_SELLS_NOT_FILLED = "BLOCKED_SELLS_NOT_FILLED"

_EPS = 1e-9
_SHARE_EPS = 1e-12


# --------------------------------------------------------------------------- #
# Input contracts (all frozen)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Position:
    """A currently-held position. ``price`` is the reference/valuation price."""

    symbol: str
    shares: float
    price: float

    @property
    def market_value(self) -> float:
        return float(self.shares) * float(self.price)


@dataclass(frozen=True)
class Holdings:
    positions: tuple[Position, ...] = ()

    def shares_by_symbol(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for pos in self.positions:
            symbol = _norm(pos.symbol)
            if symbol:
                out[symbol] = out.get(symbol, 0.0) + float(pos.shares or 0.0)
        return out

    def price_by_symbol(self) -> dict[str, float]:
        return {_norm(p.symbol): float(p.price) for p in self.positions if _norm(p.symbol)}


@dataclass(frozen=True)
class TargetPosition:
    """A desired holding as a weight of total equity. ``price`` sizes it to shares."""

    symbol: str
    target_weight: float
    price: float


@dataclass(frozen=True)
class TargetPortfolio:
    positions: tuple[TargetPosition, ...] = ()
    # cash_buffer is a fraction in [0, 1] (paper: 1 - cash_buffer_bps/10000).
    cash_buffer: float = 1.0

    def symbols(self) -> set[str]:
        return {_norm(t.symbol) for t in self.positions if _norm(t.symbol)}


@dataclass(frozen=True)
class AccountSnapshot:
    """Broker-confirmed account truth. ``buying_power=None`` means UNAVAILABLE.

    ``open_sell_orders`` is the count of live/partially-filled sell orders at the
    time of the snapshot; > 0 means sells are not fully resolved and buys must be
    blocked (V2.1 Decision 6 / invariant 6).
    """

    cash: float | None
    buying_power: float | None
    equity: float | None
    as_of: str
    open_sell_orders: int = 0


@dataclass(frozen=True)
class CapitalPolicy:
    """Governance capital envelope. ``approved_cap_usd=None`` means no cap ceiling.

    ``reserve`` (min-cash floor) plus the equity-scaled parameters reproduce the
    paper reserve (``_reserve_cash_for_equity``): the effective reserve is
    ``min(max(reserve, equity*reserve_equity_pct), equity*reserve_max_pct)``.
    """

    approved_cap_usd: float | None = None
    reserve: float = 100.0
    risk_cash_weight: float = 0.0
    reserve_equity_pct: float = 0.005
    reserve_max_pct: float = 0.05


@dataclass(frozen=True)
class OrderPolicy:
    units: str = "shares"
    fractional: bool = True
    min_trade_usd: float = 100.0


@dataclass(frozen=True)
class ModeConstraints:
    sells_supported: bool
    max_orders: int | None = None


# --------------------------------------------------------------------------- #
# Output contracts
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SellIntent:
    symbol: str
    shares: float
    price: float
    notional: float
    reason: str


@dataclass(frozen=True)
class BuyIntent:
    symbol: str
    shares: float
    price: float
    notional: float
    reason: str


@dataclass(frozen=True)
class TransitionPlan:
    holdings_to_keep: tuple[str, ...]
    holdings_to_reduce: tuple[str, ...]
    holdings_to_sell: tuple[str, ...]
    holdings_to_increase: tuple[str, ...]
    buy_needs: tuple[str, ...]
    sell_orders_intended: tuple[SellIntent, ...]
    buy_orders_intended: tuple[BuyIntent, ...]
    blocked: bool
    block_reason: str | None
    diagnostics: Mapping[str, object] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Helpers (pure)
# --------------------------------------------------------------------------- #
def _norm(symbol: object) -> str:
    return str(symbol or "").strip().upper()


def _round_toward_zero(shares: float) -> float:
    if shares > 0:
        return float(math.floor(shares))
    if shares < 0:
        return float(math.ceil(shares))
    return 0.0


def _reserve_cash(equity: float | None, policy: CapitalPolicy) -> float:
    """Reproduce ``_reserve_cash_for_equity`` (paper/paper_broker.py:86-99)."""
    eq = max(0.0, float(equity or 0.0))
    reserve_floor = max(float(policy.reserve), eq * float(policy.reserve_equity_pct))
    reserve_cap = eq * float(policy.reserve_max_pct)
    if reserve_cap > 0.0:
        return float(min(reserve_floor, reserve_cap))
    return float(reserve_floor)


def _target_shares(target_dollars: float, price: float, fractional: bool) -> float:
    if price <= 0:
        return 0.0
    shares = target_dollars / price
    if not fractional:
        shares = float(math.floor(max(0.0, shares)))
    return float(shares)


def _buy_budget(account: AccountSnapshot, policy: CapitalPolicy) -> tuple[float, dict[str, object]]:
    """Scalar buy budget = min(cash, broker_capacity, risk_capacity, cap_remaining).

    Reproduces ``_post_sell_buy_budget`` + ``_compute_buy_budget`` with the cap
    ceiling folded in. Broker truth (buying power) is preferred over cash for
    broker-backed modes; the approved cap is a *ceiling*, never spendable cash.
    """
    cash = max(0.0, float(account.cash or 0.0))
    equity = account.equity
    buying_power = account.buying_power
    reserve = _reserve_cash(equity, policy)

    # broker capacity: buying power (broker truth) when positive, else cash — minus reserve.
    if buying_power is not None and float(buying_power) > 0.0:
        broker_capacity = max(0.0, float(buying_power) - reserve)
        basis = "broker_buying_power"
    else:
        broker_capacity = max(0.0, cash - reserve)
        basis = "cash"

    risk_cash_target = max(0.0, float(equity or 0.0) * float(policy.risk_cash_weight or 0.0))
    risk_capacity = max(0.0, cash - risk_cash_target)

    cap_remaining = math.inf if policy.approved_cap_usd is None else max(0.0, float(policy.approved_cap_usd))

    budget = min(cash, broker_capacity, risk_capacity, cap_remaining)
    budget = max(0.0, float(budget))
    diagnostics = {
        "buy_budget": budget,
        "buy_budget_basis": basis,
        "reserve_cash": reserve,
        "broker_capacity": broker_capacity,
        "risk_capacity": risk_capacity,
        "risk_cash_target": risk_cash_target,
        "cap_remaining": None if cap_remaining == math.inf else cap_remaining,
        "cash_before_safeguards": cash,
    }
    return budget, diagnostics


# --------------------------------------------------------------------------- #
# Diff: target-minus-current -> keep / reduce / sell / buy-needs
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class _Diff:
    keep: tuple[str, ...]
    reduce: tuple[str, ...]
    sell: tuple[str, ...]
    increase: tuple[str, ...]
    buy_needs: tuple[str, ...]
    sell_intents: tuple[SellIntent, ...]
    buy_candidates: tuple[BuyIntent, ...]  # pre-budget, incremental need
    # symbol -> raw incremental need in shares (max(0, target - current)), BEFORE
    # the min-trade floor. Proves held-and-targeted names get incremental (never
    # blind) sizing even when the top-up is too small to trade (the ALL case).
    incremental_need: Mapping[str, float] = field(default_factory=dict)


def _compute_diff(
    holdings: Holdings,
    target: TargetPortfolio,
    equity: float,
    order_policy: OrderPolicy,
) -> _Diff:
    """Target-minus-current classification.

    Sell sizing mirrors ``build_rebalance_trades`` (reduce = -delta subject to the
    min-trade floor; full exit of held-not-targeted names is unconditional). Buy
    *need* sizing mirrors ``_rebuild_post_sell_buy_trades`` (incremental
    ``max(0, target_shares - current_shares)``). Slippage is an adapter concern and
    is not applied here; reference prices are used for notionals.
    """
    held = holdings.shares_by_symbol()
    price_by_symbol = holdings.price_by_symbol()
    cash_buffer = max(0.0, min(1.0, float(target.cash_buffer)))
    equity_value = max(0.0, float(equity or 0.0))
    min_trade = float(order_policy.min_trade_usd)
    fractional = bool(order_policy.fractional)

    keep: list[str] = []
    reduce: list[str] = []
    increase: list[str] = []
    buy_needs: list[str] = []
    sell_intents: list[SellIntent] = []
    buy_candidates: list[BuyIntent] = []
    incremental_need: dict[str, float] = {}

    target_symbols = target.symbols()
    ordered_targets = sorted(
        target.positions, key=lambda t: (-float(t.target_weight or 0.0), _norm(t.symbol))
    )

    for t in ordered_targets:
        symbol = _norm(t.symbol)
        if not symbol:
            continue
        price = float(t.price)
        if price <= 0:
            continue
        target_dollars = float(t.target_weight or 0.0) * equity_value * cash_buffer
        target_shares = _target_shares(target_dollars, price, fractional)
        current_shares = float(held.get(symbol, 0.0))
        incremental_need[symbol] = max(0.0, target_shares - current_shares)

        if current_shares > _SHARE_EPS:
            raw_delta = target_shares - current_shares
            delta = raw_delta if fractional else _round_toward_zero(raw_delta)
            if abs(delta) < _SHARE_EPS or (not fractional and abs(delta) < 1.0):
                keep.append(symbol)
                continue
            if delta < 0:
                sell_shares = abs(delta)
                notional = sell_shares * price
                if notional < min_trade:
                    keep.append(symbol)
                    continue
                reduce.append(symbol)
                sell_intents.append(
                    SellIntent(symbol, sell_shares, price, notional, "rebalance_to_target")
                )
                continue
            # delta > 0: falls through to the incremental buy-need block below.

        # Buy need: brand-new position, or top-up of an existing holding.
        desired = max(0.0, target_shares - current_shares)
        if not fractional:
            desired = float(math.floor(desired))
        if desired <= _SHARE_EPS or (not fractional and desired < 1.0):
            continue
        notional = desired * price
        if notional + _EPS < min_trade:
            continue
        buy_needs.append(symbol)
        buy_candidates.append(BuyIntent(symbol, desired, price, notional, "transition_buy"))
        if current_shares > _SHARE_EPS:
            increase.append(symbol)

    # Full exits: held names not in the target are sold in full (never deadbanded,
    # never min-trade gated) — a name leaving the sleeve must be closed.
    sell_symbols: list[str] = []
    for symbol in sorted(held):
        shares = float(held.get(symbol, 0.0))
        if symbol in target_symbols or abs(shares) <= _SHARE_EPS:
            continue
        price = float(price_by_symbol.get(symbol, 0.0))
        if price <= 0:
            continue
        sell_symbols.append(symbol)
        sell_intents.append(
            SellIntent(symbol, abs(shares), price, abs(shares) * price, "removed_from_targets")
        )

    return _Diff(
        keep=tuple(keep),
        reduce=tuple(reduce),
        sell=tuple(sell_symbols),
        increase=tuple(increase),
        buy_needs=tuple(buy_needs),
        sell_intents=tuple(sell_intents),
        buy_candidates=tuple(buy_candidates),
        incremental_need=incremental_need,
    )


def _size_buys(
    candidates: tuple[BuyIntent, ...],
    tradable_capital: float,
    order_policy: OrderPolicy,
    max_orders: int | None,
) -> tuple[list[BuyIntent], float]:
    """Greedy per-order fit against ``tradable_capital``.

    Reproduces the buy loop of ``_rebuild_post_sell_buy_trades`` (paper_broker.py
    3617-3681). ``candidates`` arrive already ordered by ``(-target_weight, symbol)``
    from ``_compute_diff`` (the same key paper sorts by). Each order is clipped to
    the remaining budget, floored to whole shares when not fractional, and dropped
    if it falls below the min-trade floor after clipping.
    """
    fractional = bool(order_policy.fractional)
    min_trade = float(order_policy.min_trade_usd)
    kept: list[BuyIntent] = []
    spent = 0.0
    limit = None if max_orders is None else max(0, int(max_orders))

    for candidate in candidates:
        if limit is not None and len(kept) >= limit:
            break
        remaining = max(0.0, float(tradable_capital) - spent)
        price = float(candidate.price)
        requested_shares = float(candidate.shares)
        requested_notional = float(candidate.notional)
        if price <= 0:
            continue
        if requested_notional <= remaining + _EPS:
            allowed_shares = requested_shares
        else:
            allowed_shares = min(requested_shares, remaining / price)
            if not fractional:
                allowed_shares = float(math.floor(max(0.0, allowed_shares)))
        allowed_notional = allowed_shares * price
        if allowed_shares <= _SHARE_EPS:
            continue
        if allowed_notional + _EPS < min_trade:
            continue
        reason = candidate.reason
        if allowed_shares + _SHARE_EPS < requested_shares:
            reason = "transition_buy_capital_clipped"
        kept.append(BuyIntent(candidate.symbol, allowed_shares, price, allowed_notional, reason))
        spent += allowed_notional

    return kept, spent


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def compute_transition(
    *,
    current_holdings: Holdings,
    target_holdings: TargetPortfolio,
    account_snapshot: AccountSnapshot,
    capital_policy: CapitalPolicy,
    order_policy: OrderPolicy,
    mode_constraints: ModeConstraints,
) -> TransitionPlan:
    """Convert (current, target, constraints) into a ``TransitionPlan``.

    Ordering of intents: sells always precede buys. Buy sizing is only performed
    against a settled, post-sell snapshot — when the transition requires sells,
    buys are deferred (blocked) so the caller re-invokes the engine after sells
    confirm and the snapshot's buying power is refreshed.
    """
    equity = float(account_snapshot.equity or 0.0)
    diff = _compute_diff(current_holdings, target_holdings, equity, order_policy)

    budget, budget_diag = _buy_budget(account_snapshot, capital_policy)
    planned_buy_notional = round(sum(c.notional for c in diff.buy_candidates), 6)
    rotation_required = len(diff.sell_intents) > 0
    sells_supported = bool(mode_constraints.sells_supported)

    diagnostics: dict[str, object] = {
        "as_of": account_snapshot.as_of,
        "planned_buy_notional": planned_buy_notional,
        "tradable_capital": budget,
        "rotation_required": rotation_required,
        "sells_supported": sells_supported,
        "required_sell_count": len(diff.sell_intents),
        "open_sell_orders": int(account_snapshot.open_sell_orders or 0),
        "buying_power": account_snapshot.buying_power,
        "approved_cap_usd": capital_policy.approved_cap_usd,
        "incremental_need_shares": dict(diff.incremental_need),
        **budget_diag,
    }

    def _plan(
        *,
        sell_intents: tuple[SellIntent, ...],
        buy_intents: tuple[BuyIntent, ...],
        blocked: bool,
        block_reason: str | None,
        extra: dict[str, object] | None = None,
    ) -> TransitionPlan:
        diag = dict(diagnostics)
        if extra:
            diag.update(extra)
        diag["deployed_buy_notional"] = round(sum(b.notional for b in buy_intents), 6)
        return TransitionPlan(
            holdings_to_keep=diff.keep,
            holdings_to_reduce=diff.reduce,
            holdings_to_sell=diff.sell,
            holdings_to_increase=diff.increase,
            buy_needs=diff.buy_needs,
            sell_orders_intended=sell_intents,
            buy_orders_intended=buy_intents,
            blocked=blocked,
            block_reason=block_reason,
            diagnostics=diag,
        )

    # 1. Fail closed on missing broker truth when a buy is even contemplated.
    if planned_buy_notional > 0 and account_snapshot.buying_power is None:
        return _plan(
            sell_intents=(),
            buy_intents=(),
            blocked=True,
            block_reason=BLOCK_BUYING_POWER_UNAVAILABLE,
        )

    # 2. Rotation is required but this mode cannot sell -> block (Option A).
    if rotation_required and not sells_supported:
        return _plan(
            sell_intents=(),
            buy_intents=(),
            blocked=True,
            block_reason=BLOCK_ROTATION_UNSUPPORTED,
        )

    # 3. Rotation required and sells supported -> emit sells, defer buys until a
    #    fresh post-sell snapshot confirms fills and refreshes buying power.
    if rotation_required:
        return _plan(
            sell_intents=diff.sell_intents,
            buy_intents=(),
            blocked=True,
            block_reason=BLOCK_SELLS_NOT_FILLED,
            extra={"buys_deferred_until_sells_settle": True},
        )

    # 4. No new sells needed, but prior sells are still unresolved in the snapshot.
    if int(account_snapshot.open_sell_orders or 0) > 0 and planned_buy_notional > 0:
        return _plan(
            sell_intents=(),
            buy_intents=(),
            blocked=True,
            block_reason=BLOCK_SELLS_NOT_FILLED,
        )

    # 5. Settled snapshot: size buys against tradable capital (cap is a ceiling).
    buy_intents, deployed = _size_buys(
        diff.buy_candidates, budget, order_policy, mode_constraints.max_orders
    )
    if planned_buy_notional > 0 and deployed <= 0.0:
        return _plan(
            sell_intents=(),
            buy_intents=(),
            blocked=True,
            block_reason=BLOCK_INSUFFICIENT_BUYING_POWER,
        )
    return _plan(
        sell_intents=(),
        buy_intents=tuple(buy_intents),
        blocked=False,
        block_reason=None,
    )

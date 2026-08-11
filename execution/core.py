from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

import pandas as pd

from core.live_trade_ledger import record_live_order
from paper.paper_broker import (
    BUY_BLOCKED_RISK_CASH_TARGET,
    PaperConfig,
    _apply_capital_budget_to_trades,
    _build_capital_budget,
    _build_shadow_orders,
    _compute_buy_budget,
    _normalize_and_filter_executable_trades,
    _post_sell_buy_budget,
    _rebuild_post_sell_buy_trades,
)


@dataclass(frozen=True)
class CapitalPolicy:
    """Capital configuration, not mode-specific branching.

    Paper config keeps the current paper reserve and 95% pre-settlement sell-proceeds
    haircut. Live-pilot config sets reserve to zero, uses an approved-cap ceiling,
    and can fail closed on over-cap intents while still flowing through the same
    lifecycle runner.
    """

    target_cash_weight: float = 0.0
    sell_proceeds_haircut: float = 0.95
    reserve_min_cash: float = 100.0
    reserve_equity_pct: float = 0.005
    reserve_max_pct: float = 0.05
    approved_cap_usd: float | None = None
    over_cap_behavior: str = "clip"
    broker_budget_source: str = "buying_power_then_cash"
    # Slippage/settled-cash buffer applied to the buy budget on the live cash-account
    # path (PRE_ARM_SWEEP Blocker #2 / #8). 1.0 = inert (paper). Live sets ~0.98 so
    # market-order buys sized at snapshot prices keep headroom against adverse fills.
    buy_buffer_pct: float = 1.0


@dataclass(frozen=True)
class OrderPolicy:
    """Order sizing configuration shared by paper and live modes."""

    units: str = "shares"
    allow_fractional: bool = True
    min_trade_usd: float = 100.0
    slippage_bps: float = 0.0
    cash_buffer_bps: float = 0.0
    allow_fractional_sells: bool = False
    fractional_sell_min_trade_usd: float = 1.0


@dataclass(frozen=True)
class ModeConstraints:
    """Operational constraints injected by the caller instead of core forks."""

    sell_first: bool = True
    rebudget_after_sell: bool = True
    max_trades_per_day: int = 50
    max_buy_orders: int | None = None
    max_one_order: bool = False
    equity_collar_min_usd: float | None = None
    equity_collar_max_usd: float | None = None
    malformed_holding_policy: str = "ignore"


@dataclass(frozen=True)
class ExecutionCoreConfig:
    """Complete execution-core configuration surface."""

    mode: str
    capital: CapitalPolicy
    orders: OrderPolicy
    constraints: ModeConstraints
    paper_config: PaperConfig
    ledger_output_root: str | Path | None = None
    ledger_enabled: bool = True


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: str
    shares: float
    price: float
    notional: float
    reason: str
    order: int
    slippage_cost: float = 0.0
    client_order_id: str | None = None


@dataclass(frozen=True)
class SubmitResult:
    intent: OrderIntent
    status: str
    broker_order_id: str | None = None
    filled_qty: float | None = None
    filled_notional: float | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AccountSnapshot:
    account: Mapping[str, Any]
    holdings: pd.DataFrame | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)


class ExecutionAdapter(Protocol):
    def submit(self, intent: OrderIntent) -> SubmitResult:
        ...

    def wait_or_refresh(self, submitted_sells: Sequence[SubmitResult]) -> AccountSnapshot:
        ...

    def snapshot(self) -> AccountSnapshot:
        ...


@dataclass(frozen=True)
class ExecutionRequest:
    holdings: pd.DataFrame
    targets: pd.DataFrame
    prices: pd.Series
    total_equity: float
    starting_cash: float
    target_cash_weight: float
    planning_account: Mapping[str, Any]
    run_id: str
    price_basis: str = "fixture_price"
    precomputed_trades: tuple[dict[str, Any], ...] = ()
    precomputed_trade_plan_used: bool = False
    rebudget_total_equity: float | None = None
    artifact_expectations: Mapping[str, Any] = field(default_factory=dict)
    capital_budget_override: Mapping[str, Any] = field(default_factory=dict)
    # Migrated authority path: when present, Trader must use these approved
    # targets exactly and may not discover or substitute a target source.
    approved_execution_package: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ExecutionResult:
    request: ExecutionRequest
    config: ExecutionCoreConfig
    raw_trades: pd.DataFrame
    trade_meta: Mapping[str, Any]
    capital_trades: pd.DataFrame
    capital_budget: Mapping[str, Any]
    executable_trades: pd.DataFrame
    execution_filter_stats: Mapping[str, Any]
    sell_trades: pd.DataFrame
    original_buy_trades: pd.DataFrame
    post_sell_snapshot: AccountSnapshot
    sell_fill_meta: Mapping[str, Any]
    post_sell_budget_meta: Mapping[str, Any]
    rebuilt_buy_trades: pd.DataFrame
    rebudget_meta: Mapping[str, Any]
    rebudget_skipped: list[dict[str, Any]]
    final_execution_trades: pd.DataFrame
    final_buy_orders: list[dict[str, Any]]
    submitted_sells: tuple[SubmitResult, ...]
    submitted_buys: tuple[SubmitResult, ...]


def compute_transition_trades(
    *,
    request: ExecutionRequest,
    config: ExecutionCoreConfig,
) -> tuple[pd.DataFrame, Mapping[str, Any]]:
    """Compute paper's transition trade frame without submitting orders."""

    cfg = config.paper_config
    if request.approved_execution_package is not None:
        from authority.contracts import AuthorityContractError
        from authority.pipeline import execution_package_from_dict

        try:
            package = execution_package_from_dict(request.approved_execution_package)
        except AuthorityContractError as exc:
            raise ValueError(f"invalid approved execution package: {exc}") from exc
        approved_rows = [dict(row) for row in package.approved_target_rows]
        if approved_rows and not all("target_weight" in row for row in approved_rows):
            raise ValueError("approved execution package rows must contain target_weight")
        approved_targets = pd.DataFrame(
            [
                {
                    "ticker": str(row.get("symbol") or row.get("ticker") or "").upper(),
                    "sleeve": str(row.get("sleeve") or "approved_execution_package"),
                    "target_weight": float(row.get("target_weight") or 0.0),
                }
                for row in approved_rows
            ],
            columns=["ticker", "sleeve", "target_weight"],
        )
        approved_prices = (
            request.prices.copy()
            if isinstance(request.prices, pd.Series)
            else pd.Series(dtype=float)
        )
        for row in approved_rows:
            symbol = str(row.get("symbol") or row.get("ticker") or "").upper()
            price = _safe_float(row.get("price") or row.get("entry_price"), 0.0)
            if symbol and price > 0.0:
                approved_prices.loc[symbol] = price
        policy_payload = package.constraints.get("target_attainment_policy")
        if policy_payload:
            if str(config.mode or "").strip().lower() != "paper":
                raise ValueError("whole-share target-attainment policy is PAPER-only")
            from core.whole_share_feasibility import (
                build_nearest_feasible_whole_share_trades,
                seal_whole_share_proof,
            )

            raw_trades, feasibility = build_nearest_feasible_whole_share_trades(
                holdings=request.holdings,
                targets=approved_targets,
                prices=approved_prices,
                total_equity=float(request.total_equity),
                cfg=cfg,
                policy=policy_payload,
                max_orders=int(config.constraints.max_trades_per_day or 0),
            )
            feasibility = seal_whole_share_proof(
                {
                    **dict(feasibility),
                    "approved_execution_package_hash": package.content_hash,
                    "approved_risk_package_hash": package.risk_hash,
                }
            )
            base_meta = {
                "deadband_skipped": [],
                "deadband_skipped_count": 0,
                "cash_sweep_added_shares": 0,
                "cash_sweep_iterations": 0,
                "cash_sweep_tickers": [],
                "cash_sweep_remaining_dollars": feasibility.get("projected_cash"),
                "whole_share_feasibility": feasibility,
            }
        else:
            from paper import paper_broker as paper_broker_module

            raw_trades, base_meta = paper_broker_module.build_rebalance_trades(
                holdings=request.holdings,
                targets=approved_targets,
                prices=approved_prices,
                total_equity=float(request.total_equity),
                starting_cash=float(request.starting_cash),
                target_cash_weight=float(package.approved_cash_weight),
                cfg=cfg,
            )
        return raw_trades, {
            **dict(base_meta),
            "source": "approved_execution_package",
            "precomputed_trade_plan_used": False,
            "authority_package_id": package.package_id,
            "authority_package_hash": package.content_hash,
            "risk_package_id": package.risk_package_id,
            "risk_hash": package.risk_hash,
            "target_cash_weight": float(package.approved_cash_weight),
        }
    if request.precomputed_trade_plan_used or request.precomputed_trades:
        raw_trades = _precomputed_trades_frame(request.precomputed_trades)
        trade_meta: dict[str, Any] = {
            "source": "precomputed_execution_payload",
            "precomputed_trade_plan_used": True,
            "deadband_skipped": [],
            "deadband_skipped_count": 0,
            "cash_sweep_added_shares": 0,
            "cash_sweep_iterations": 0,
            "cash_sweep_tickers": [],
            "cash_sweep_remaining_dollars": None,
            "target_cash_weight": float(request.target_cash_weight),
        }
        return raw_trades, trade_meta

    from paper import paper_broker as paper_broker_module

    return paper_broker_module.build_rebalance_trades(
        holdings=request.holdings,
        targets=request.targets,
        prices=request.prices,
        total_equity=float(request.total_equity),
        starting_cash=float(request.starting_cash),
        target_cash_weight=float(request.target_cash_weight),
        cfg=cfg,
    )


def _approved_target_attainment_policy(
    request: ExecutionRequest,
) -> Mapping[str, Any] | None:
    payload = request.approved_execution_package
    if not isinstance(payload, Mapping):
        return None
    constraints = payload.get("constraints")
    if not isinstance(constraints, Mapping):
        return None
    policy = constraints.get("target_attainment_policy")
    if not isinstance(policy, Mapping) or not policy:
        return None
    from core.target_attainment_policy import validate_target_attainment_policy

    return validate_target_attainment_policy(
        policy,
        expected_target_cash_weight=float(request.target_cash_weight),
    )


def _lifecycle_targets(
    request: ExecutionRequest,
    trade_meta: Mapping[str, Any],
) -> pd.DataFrame:
    """Carry Decision's proven integer quantities through every lifecycle phase."""

    targets = request.targets.copy()
    proof = trade_meta.get("whole_share_feasibility")
    if not isinstance(proof, Mapping):
        return targets
    allocation = proof.get("allocation")
    if not isinstance(allocation, list):
        return targets
    quantities = {
        str(row.get("symbol") or "").strip().upper(): int(row.get("target_quantity"))
        for row in allocation
        if isinstance(row, Mapping)
        and str(row.get("symbol") or "").strip()
        and row.get("target_quantity") is not None
    }
    if not quantities:
        return targets
    targets["target_quantity"] = targets["ticker"].map(quantities)
    if targets["target_quantity"].isna().any():
        raise ValueError("whole-share feasibility proof omits an approved target")
    return targets


def apply_capital_budget_and_execution_filter(
    *,
    trades: pd.DataFrame,
    planning_account: Mapping[str, Any],
    config: ExecutionCoreConfig,
    precomputed_trade_plan_used: bool = False,
) -> tuple[pd.DataFrame, Mapping[str, Any], pd.DataFrame, Mapping[str, Any]]:
    """Apply paper's capital budget and executable-trade filter."""

    cfg = config.paper_config
    requested_buy_notional = (
        float(
            trades.loc[
                trades["side"].astype(str).str.upper() == "BUY",
                "notional",
            ].astype(float).sum()
        )
        if trades is not None and not trades.empty
        else 0.0
    )
    expected_sell_proceeds = (
        float(
            trades.loc[
                trades["side"].astype(str).str.upper().isin({"SELL", "CLOSE", "REDUCE"}),
                "notional",
            ].astype(float).sum()
        )
        if trades is not None and not trades.empty
        else 0.0
    )
    if _uses_configured_capital_policy(config):
        capital_budget = _build_configured_capital_budget(
            planning_account=planning_account,
            config=config,
            expected_sell_proceeds=expected_sell_proceeds,
            requested_buy_notional=requested_buy_notional,
        )
    else:
        capital_budget = _build_capital_budget(
            broker_cash=planning_account.get("cash"),
            broker_equity=planning_account.get("equity")
            or planning_account.get("portfolio_value"),
            broker_buying_power=planning_account.get("buying_power"),
            expected_sell_proceeds=expected_sell_proceeds,
            requested_buy_notional=requested_buy_notional,
        )
    if precomputed_trade_plan_used:
        capital_trades = trades.copy()
        capital_budget = dict(capital_budget)
        capital_budget["capital_budget_application"] = "skipped_precomputed_trade_plan"
        capital_budget["allowed_buy_notional"] = requested_buy_notional
        capital_budget["capital_constraint_triggered"] = False
        capital_budget["clipped_or_deferred_buys_count"] = 0
    else:
        capital_trades, capital_budget = _apply_capital_budget_to_trades(trades, cfg, capital_budget)
    executable_trades, execution_filter_stats = _normalize_and_filter_executable_trades(capital_trades, cfg)
    return capital_trades, capital_budget, executable_trades, execution_filter_stats


def paper_execution_config(
    cfg: PaperConfig,
    *,
    target_cash_weight: float,
    ledger_output_root: str | Path | None = None,
    ledger_enabled: bool = True,
) -> ExecutionCoreConfig:
    """Paper's current execution values as explicit core config."""

    return ExecutionCoreConfig(
        mode="paper",
        capital=CapitalPolicy(target_cash_weight=float(target_cash_weight)),
        orders=OrderPolicy(
            allow_fractional=bool(cfg.allow_fractional),
            allow_fractional_sells=bool(cfg.allow_fractional_sells),
            fractional_sell_min_trade_usd=float(cfg.fractional_sell_min_trade_dollars),
            min_trade_usd=float(cfg.min_trade_dollars),
            slippage_bps=float(cfg.slippage_bps),
            cash_buffer_bps=float(cfg.cash_buffer_bps),
        ),
        constraints=ModeConstraints(
            sell_first=True,
            rebudget_after_sell=True,
            max_trades_per_day=int(cfg.max_trades_per_day or 0),
            max_buy_orders=None,
            max_one_order=False,
            malformed_holding_policy="ignore",
        ),
        paper_config=cfg,
        ledger_output_root=ledger_output_root,
        ledger_enabled=ledger_enabled,
    )


def live_pilot_execution_config(
    *,
    approved_cap_usd: float | None,
    allow_fractional: bool = False,
    allow_fractional_sells: bool = False,
    fractional_sell_min_trade_usd: float = 1.0,
    max_orders: int = 1,
    min_trade_usd: float = 100.0,
    equity_collar_max_usd: float | None = None,
    buy_buffer_pct: float = 0.98,
    ledger_output_root: str | Path | None = None,
    ledger_enabled: bool = True,
) -> ExecutionCoreConfig:
    """Live pilot's conservative values expressed as config.

    Phase 1B does not wire live to this core. This factory documents the intended
    same-path settings for Phase 1D: cap blocks instead of clipping, one max buy,
    no paper reserve, and fail-closed malformed holdings. The equity collar is
    disabled by default (None): the capital cap now tracks the account's portfolio
    value, so there is no fixed account-size ceiling; the account-id pin is the
    wrong-account guard.
    """

    cfg = PaperConfig(
        initial_equity=float(approved_cap_usd or 0.0),
        benchmark_ticker="SPY",
        slippage_bps=0.0,
        allow_fractional=bool(allow_fractional),
        min_trade_dollars=float(min_trade_usd),
        cash_buffer_bps=0.0,
        trading_mode="alpaca",
        portfolio_id="live_pilot",
        strategy_version="live_pilot",
        max_turnover_pct=1.0,
        max_trades_per_day=int(max_orders or 1),
        max_position_change_pct=1.0,
        max_position_pct=1.0,
        risk_action="block",
        cash_target_weight_default=0.0,
        rebalance_deadband_pct=0.0,
        allow_fractional_sells=bool(allow_fractional_sells),
        fractional_sell_min_trade_dollars=float(fractional_sell_min_trade_usd),
    )
    return ExecutionCoreConfig(
        mode="live_pilot",
        capital=CapitalPolicy(
            target_cash_weight=0.0,
            sell_proceeds_haircut=0.95,
            reserve_min_cash=0.0,
            reserve_equity_pct=0.0,
            reserve_max_pct=0.0,
            approved_cap_usd=approved_cap_usd,
            over_cap_behavior="block",
            broker_budget_source="buying_power",
            buy_buffer_pct=float(buy_buffer_pct),
        ),
        orders=OrderPolicy(
            allow_fractional=bool(allow_fractional),
            allow_fractional_sells=bool(allow_fractional_sells),
            fractional_sell_min_trade_usd=float(fractional_sell_min_trade_usd),
            min_trade_usd=float(min_trade_usd),
            slippage_bps=0.0,
            cash_buffer_bps=0.0,
        ),
        constraints=ModeConstraints(
            sell_first=True,
            rebudget_after_sell=True,
            max_trades_per_day=int(max_orders or 1),
            max_buy_orders=int(max_orders or 1),
            max_one_order=int(max_orders or 1) == 1,
            equity_collar_min_usd=None,
            equity_collar_max_usd=equity_collar_max_usd,
            malformed_holding_policy="fail_closed",
        ),
        paper_config=cfg,
        ledger_output_root=ledger_output_root,
        ledger_enabled=ledger_enabled,
    )


def _precomputed_trades_frame(rows: tuple[dict[str, Any], ...]) -> pd.DataFrame:
    cols = ["ticker", "side", "shares", "price", "slippage_cost", "notional", "reason"]
    normalized: list[dict[str, Any]] = []
    for row in rows:
        ticker = str(row.get("ticker") or row.get("symbol") or "").upper()
        side = str(row.get("side") or "").upper()
        shares = abs(_safe_float(row.get("shares") or row.get("quantity"), 0.0))
        price = _safe_float(row.get("price") or row.get("entry_price"), 0.0)
        notional = abs(_safe_float(row.get("notional"), shares * price))
        if not ticker or side not in {"BUY", "SELL", "CLOSE", "REDUCE"}:
            continue
        if shares <= 0.0 or price <= 0.0 or notional <= 0.0:
            continue
        normalized.append(
            {
                "ticker": ticker,
                "side": "SELL" if side in {"SELL", "CLOSE", "REDUCE"} else "BUY",
                "shares": shares,
                "price": price,
                "slippage_cost": float(row.get("slippage_cost") or 0.0),
                "notional": notional,
                "reason": str(row.get("reason") or row.get("notes") or "precomputed_execution_payload"),
            }
        )
    return pd.DataFrame(normalized, columns=cols)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return float(default)
    return numeric if math.isfinite(numeric) else float(default)


def _reserve_cash_for_policy(equity_value: float | None, policy: CapitalPolicy) -> float:
    equity = max(0.0, float(equity_value or 0.0))
    reserve_floor = max(
        float(policy.reserve_min_cash or 0.0),
        equity * float(policy.reserve_equity_pct or 0.0),
    )
    reserve_cap = equity * float(policy.reserve_max_pct or 0.0)
    if reserve_cap > 0.0:
        return float(min(reserve_floor, reserve_cap))
    return float(reserve_floor)


def _uses_configured_capital_policy(config: ExecutionCoreConfig) -> bool:
    policy = config.capital
    return bool(
        policy.approved_cap_usd is not None
        or str(policy.broker_budget_source or "") != "buying_power_then_cash"
        or abs(float(policy.reserve_min_cash or 0.0) - 100.0) > 1e-9
        or abs(float(policy.reserve_equity_pct or 0.0) - 0.005) > 1e-12
        or abs(float(policy.reserve_max_pct or 0.0) - 0.05) > 1e-12
        or abs(float(policy.sell_proceeds_haircut or 0.0) - 0.95) > 1e-12
        or abs(float(policy.buy_buffer_pct or 1.0) - 1.0) > 1e-12
    )


def _apply_settled_cash_and_buffer(
    budget: float,
    account: Mapping[str, Any],
    policy: CapitalPolicy,
) -> tuple[float, dict[str, Any]]:
    """Clamp a buy budget to *settled* cash and apply the slippage buffer.

    PRIMARY GFV DEFENSE (PRE_ARM_SWEEP Blocker #2). On the live cash account, Alpaca
    credits sale proceeds to ``cash`` immediately though they are unsettled (T+1). The
    executor injects ``settled_cash`` (= broker cash - unsettled sale proceeds) and, on
    a fail-closed history lookup, ``settled_cash_fail_closed=True``. By default,
    clamping the buy budget to settled cash guarantees the invariant::

        sum(buy_notional) <= settled_cash * buy_buffer_pct

    so no live-capital buy spends unsettled funds and no held lot can become
    GFV-vulnerable. The PAPER adapter may additionally provide an
    ``execution_spendable_cash`` ceiling consisting only of regulatory settled cash
    plus current-run confirmed sell fills. The buffer then trims the effective ceiling
    (default 0.98) so market-order buys sized at snapshot prices keep headroom against
    adverse fills.
    """
    buffer = float(policy.buy_buffer_pct if policy.buy_buffer_pct is not None else 1.0)
    if buffer < 0.0:
        buffer = 0.0
    settled_raw = account.get("settled_cash")
    execution_spendable_raw = account.get("execution_spendable_cash")
    fail_closed = bool(account.get("settled_cash_fail_closed"))
    settled_ceiling: float | None = None
    if fail_closed:
        settled_ceiling = 0.0
    elif settled_raw is not None:
        settled_ceiling = max(0.0, _safe_float(settled_raw, 0.0))

    execution_spendable_ceiling: float | None = None
    if not fail_closed and execution_spendable_raw is not None:
        execution_spendable_ceiling = max(
            0.0, _safe_float(execution_spendable_raw, 0.0)
        )
    effective_cash_ceiling = (
        execution_spendable_ceiling
        if execution_spendable_ceiling is not None
        else settled_ceiling
    )

    budget_in = max(0.0, float(budget or 0.0))
    clamped = budget_in
    settled_clamp_applied = False
    if effective_cash_ceiling is not None and effective_cash_ceiling < clamped:
        clamped = effective_cash_ceiling
        settled_clamp_applied = True
    buffered = clamped * buffer
    diag = {
        "settled_cash": settled_ceiling,
        "execution_spendable_cash": execution_spendable_ceiling,
        "effective_cash_ceiling": effective_cash_ceiling,
        "cash_ceiling_source": (
            str(account.get("execution_spendable_cash_source") or "")
            if execution_spendable_ceiling is not None
            else "regulatory_settled_cash"
        ),
        "settled_cash_fail_closed": fail_closed,
        "buy_buffer_pct": buffer,
        "budget_before_settled_clamp": budget_in,
        "budget_after_settled_clamp": clamped,
        "settled_cash_clamp_applied": settled_clamp_applied,
        "budget_after_buffer": buffered,
    }
    return buffered, diag


def _build_configured_capital_budget(
    *,
    planning_account: Mapping[str, Any],
    config: ExecutionCoreConfig,
    expected_sell_proceeds: float,
    requested_buy_notional: float,
) -> Mapping[str, Any]:
    policy = config.capital
    cash_value = _safe_float(planning_account.get("cash"), 0.0)
    equity_value = _safe_float(
        planning_account.get("equity") or planning_account.get("portfolio_value"),
        0.0,
    )
    buying_power_value_raw = planning_account.get("buying_power")
    buying_power_value = (
        None
        if buying_power_value_raw is None
        else _safe_float(buying_power_value_raw, 0.0)
    )
    reserve_cash = _reserve_cash_for_policy(equity_value, policy)
    expected_sell_proceeds_value = max(0.0, float(expected_sell_proceeds or 0.0))
    expected_sell_proceeds_conservative = expected_sell_proceeds_value * float(
        policy.sell_proceeds_haircut or 0.0
    )
    requested_buy_notional_value = max(0.0, float(requested_buy_notional or 0.0))

    cash_budget = max(
        0.0,
        float(cash_value or 0.0) + expected_sell_proceeds_conservative - reserve_cash,
    )
    budget_source = str(policy.broker_budget_source or "buying_power_then_cash").strip().lower()
    if budget_source == "buying_power":
        broker_budget = (
            max(0.0, float(buying_power_value) + expected_sell_proceeds_conservative - reserve_cash)
            if buying_power_value is not None and float(buying_power_value) > 0.0
            else 0.0
        )
        available_for_buys = min(cash_budget, broker_budget)
        budget_basis = "broker_buying_power"
    else:
        available_for_buys = cash_budget
        budget_basis = "cash"

    approved_cap = policy.approved_cap_usd
    if approved_cap is not None:
        available_for_buys = min(float(available_for_buys), max(0.0, float(approved_cap)))

    # PRIMARY GFV DEFENSE: clamp to settled cash and apply the slippage buffer.
    available_for_buys, settled_cash_diag = _apply_settled_cash_and_buffer(
        float(available_for_buys), planning_account, policy
    )

    allowed_buy_notional = min(requested_buy_notional_value, max(0.0, float(available_for_buys)))
    return {
        "broker_cash_at_planning": cash_value,
        "broker_equity_at_planning": equity_value,
        "broker_buying_power_at_planning": buying_power_value,
        "reserve_cash_policy": {
            "min_cash_dollars": float(policy.reserve_min_cash or 0.0),
            "equity_reserve_pct": float(policy.reserve_equity_pct or 0.0),
            "sell_proceeds_haircut": float(policy.sell_proceeds_haircut or 0.0),
            "reserve_cash": float(reserve_cash),
            "available_for_buys": float(available_for_buys),
            "approved_cap_usd": approved_cap,
            "budget_source": budget_basis,
        },
        "settled_cash_guard": settled_cash_diag,
        "expected_sell_proceeds": float(expected_sell_proceeds_value),
        "expected_sell_proceeds_conservative": float(expected_sell_proceeds_conservative),
        "requested_buy_notional": float(requested_buy_notional_value),
        "allowed_buy_notional": float(allowed_buy_notional),
        "capital_constraint_triggered": bool(
            requested_buy_notional_value > allowed_buy_notional + 1e-9
        ),
        "clipped_or_deferred_buys_count": 0,
        "approved_cap_usd": approved_cap,
        "over_cap_behavior": str(policy.over_cap_behavior or "clip"),
        "broker_budget_source": budget_source,
    }


def _apply_configured_post_sell_budget(
    *,
    buy_budget: float,
    post_sell_budget_meta: Mapping[str, Any],
    account: Mapping[str, Any],
    config: ExecutionCoreConfig,
) -> tuple[float, Mapping[str, Any]]:
    if not _uses_configured_capital_policy(config):
        return float(buy_budget or 0.0), post_sell_budget_meta

    policy = config.capital
    meta = dict(post_sell_budget_meta)
    cash_value = _safe_float(account.get("cash"), 0.0)
    equity_value = _safe_float(
        account.get("equity") or account.get("portfolio_value") or meta.get("post_sell_equity"),
        0.0,
    )
    buying_power_raw = account.get("buying_power")
    buying_power_value = (
        None if buying_power_raw is None else _safe_float(buying_power_raw, 0.0)
    )
    reserve_cash = _reserve_cash_for_policy(equity_value, policy)
    cash_budget = max(0.0, float(cash_value or 0.0) - reserve_cash)
    source = str(policy.broker_budget_source or "buying_power_then_cash").strip().lower()
    if source == "buying_power":
        broker_budget = (
            max(0.0, float(buying_power_value) - reserve_cash)
            if buying_power_value is not None and float(buying_power_value) > 0.0
            else 0.0
        )
        basis = "broker_buying_power"
    else:
        broker_budget = cash_budget
        basis = str(meta.get("buy_budget_basis") or "cash")

    risk_cash_target = _safe_float(meta.get("risk_cash_target"), 0.0)
    risk_budget = max(0.0, float(cash_value or 0.0) - risk_cash_target)
    budget = min(cash_budget, broker_budget, risk_budget)
    if policy.approved_cap_usd is not None:
        budget = min(budget, max(0.0, float(policy.approved_cap_usd)))

    # PRIMARY GFV DEFENSE: the post-sell account's cash now INCLUDES today's freshly
    # credited (but unsettled) sale proceeds. Live capital clamps strictly to settled
    # cash. PAPER may supply a narrower explicit override containing only settled cash
    # plus current-run confirmed fills; unrelated unsettled proceeds remain excluded.
    budget, settled_cash_diag = _apply_settled_cash_and_buffer(float(budget), account, policy)

    meta.update(
        {
            "post_sell_cash": float(cash_value),
            "post_sell_equity": float(equity_value),
            "post_sell_buying_power": buying_power_value,
            "broker_safeguard_buy_budget": float(broker_budget),
            "risk_cash_target_buy_budget": float(risk_budget),
            "buy_budget_after_safeguards": float(max(0.0, budget)),
            "buy_budget_basis": basis,
            "approved_cap_usd": policy.approved_cap_usd,
            "broker_budget_source": source,
            "reserve_cash": float(reserve_cash),
            "settled_cash_guard": settled_cash_diag,
        }
    )
    return float(max(0.0, budget)), meta


def _intent_from_row(row: Mapping[str, Any], *, order: int) -> OrderIntent:
    symbol = str(row.get("ticker") or row.get("symbol") or "").upper()
    side = str(row.get("side") or "").upper()
    shares = abs(_safe_float(row.get("shares") or row.get("quantity")))
    price = _safe_float(row.get("price"))
    notional = abs(_safe_float(row.get("notional"), shares * price))
    return OrderIntent(
        symbol=symbol,
        side=side,
        shares=shares,
        price=price,
        notional=notional,
        reason=str(row.get("reason") or ""),
        order=order,
        slippage_cost=_safe_float(row.get("slippage_cost")),
    )


def _intents_from_frame(frame: pd.DataFrame) -> list[OrderIntent]:
    if frame is None or frame.empty:
        return []
    return [_intent_from_row(row.to_dict(), order=idx) for idx, (_, row) in enumerate(frame.iterrows(), start=1)]


def _submit_with_ledger(
    *,
    adapter: ExecutionAdapter,
    intent: OrderIntent,
    config: ExecutionCoreConfig,
    request: ExecutionRequest,
) -> SubmitResult:
    result = adapter.submit(intent)
    ledger_intent = result.intent or intent
    if config.ledger_enabled:
        record_live_order(
            event="submitted",
            symbol=ledger_intent.symbol,
            side=ledger_intent.side,
            qty=ledger_intent.shares,
            limit_price=ledger_intent.price,
            notional=ledger_intent.notional,
            client_order_id=ledger_intent.client_order_id,
            broker_order_id=result.broker_order_id,
            run_root=request.run_id,
            status=result.status,
            reason=ledger_intent.reason,
            output_root=config.ledger_output_root,
        )
    return result


def _split_trades(executable_trades: pd.DataFrame, capital_trades: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    columns = list(capital_trades.columns) if capital_trades is not None else []
    if executable_trades is None or executable_trades.empty or "side" not in executable_trades.columns:
        empty = pd.DataFrame(columns=columns)
        return empty.copy(), empty.copy()
    sides = executable_trades["side"].astype(str).str.upper()
    sells = executable_trades[sides.isin({"SELL", "CLOSE", "REDUCE"})].copy()
    buys = executable_trades[sides.isin({"BUY", "ADD"})].copy()
    return sells, buys


def _apply_buy_order_limit(
    buy_trades: pd.DataFrame,
    *,
    config: ExecutionCoreConfig,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    if buy_trades is None or buy_trades.empty:
        return buy_trades, []
    limit = config.constraints.max_buy_orders
    if config.constraints.max_one_order:
        limit = 1 if limit is None else min(int(limit), 1)
    if limit is None:
        return buy_trades, []
    limit = max(0, int(limit))
    if len(buy_trades) <= limit:
        return buy_trades, []
    kept = buy_trades.head(limit).copy()
    skipped = [
        {**row.to_dict(), "block_reason": "max_trades_per_day"}
        for _, row in buy_trades.iloc[limit:].iterrows()
    ]
    return kept, skipped


def execute_lifecycle(
    *,
    request: ExecutionRequest,
    adapter: ExecutionAdapter,
    config: ExecutionCoreConfig,
) -> ExecutionResult:
    """Run paper's sell-first/rebudget/buy lifecycle behind an adapter boundary."""

    cfg = config.paper_config
    precomputed_trade_plan_used = bool(
        request.precomputed_trade_plan_used or request.precomputed_trades
    )
    raw_trades, trade_meta = compute_transition_trades(
        request=request,
        config=config,
    )
    capital_trades, capital_budget, executable_trades, execution_filter_stats = (
        apply_capital_budget_and_execution_filter(
            trades=raw_trades,
            planning_account=request.planning_account,
            config=config,
            precomputed_trade_plan_used=precomputed_trade_plan_used,
        )
    )
    if request.capital_budget_override:
        capital_budget = dict(request.capital_budget_override)
    requested_buy_notional = float(capital_budget.get("requested_buy_notional") or 0.0)
    sell_trades, original_buy_trades = _split_trades(executable_trades, capital_trades)
    lifecycle_targets = _lifecycle_targets(request, trade_meta)
    target_policy = _approved_target_attainment_policy(request)
    governed_cash_floor_weight = (
        float(target_policy["minimum_cash_weight"])
        if target_policy is not None
        else float(request.target_cash_weight)
    )

    submitted_sells = tuple(
        _submit_with_ledger(adapter=adapter, intent=intent, config=config, request=request)
        for intent in _intents_from_frame(sell_trades)
    )
    sell_orders_present = not sell_trades.empty
    post_sell_snapshot = (
        adapter.wait_or_refresh(submitted_sells)
        if sell_orders_present
        else adapter.snapshot()
    )
    post_sell_account = dict(post_sell_snapshot.account or {})
    post_sell_holdings = (
        post_sell_snapshot.holdings.copy()
        if post_sell_snapshot.holdings is not None
        else request.holdings.copy()
    )
    sell_fill_meta = dict(post_sell_snapshot.raw.get("sell_fill_meta") or {})
    sell_phase_allows_buy = bool(post_sell_snapshot.raw.get("sell_phase_allows_buy", True))

    capital_constraint_clear = not bool(capital_budget.get("capital_constraint_triggered"))
    if sell_orders_present and config.constraints.rebudget_after_sell and sell_phase_allows_buy:
        buy_budget, post_sell_budget_meta = _post_sell_buy_budget(
            account=post_sell_account,
            cfg=cfg,
            target_cash_weight=governed_cash_floor_weight,
            fallback_equity=float(request.total_equity),
            capital_constraint_clear=capital_constraint_clear,
        )
        buy_budget, post_sell_budget_meta = _apply_configured_post_sell_budget(
            buy_budget=buy_budget,
            post_sell_budget_meta=post_sell_budget_meta,
            account=post_sell_account,
            config=config,
        )
        pending_sell_order_ids = list(post_sell_snapshot.raw.get("pending_sell_order_ids") or [])
        if pending_sell_order_ids:
            buy_budget = 0.0
            post_sell_budget_meta = dict(post_sell_budget_meta)
            post_sell_budget_meta["buy_budget_after_safeguards"] = 0.0
            post_sell_budget_meta["pending_sell_order_ids"] = list(pending_sell_order_ids)
        max_rebudget_buy_orders = max(0, int(cfg.max_trades_per_day or 0) - int(len(sell_trades)))
        if config.constraints.max_buy_orders is not None:
            max_rebudget_buy_orders = min(max_rebudget_buy_orders, int(config.constraints.max_buy_orders))
        if config.constraints.max_one_order:
            max_rebudget_buy_orders = min(max_rebudget_buy_orders, 1)
        zero_budget_block_reason = None
        if float(buy_budget or 0.0) <= 1e-9:
            if pending_sell_order_ids:
                zero_budget_block_reason = BUY_BLOCKED_RISK_CASH_TARGET
            elif float(post_sell_budget_meta.get("risk_cash_target_buy_budget") or 0.0) <= 1e-9:
                zero_budget_block_reason = BUY_BLOCKED_RISK_CASH_TARGET
        rebuilt_buy_trades, rebudget_meta, rebudget_skipped = _rebuild_post_sell_buy_trades(
            holdings=post_sell_holdings,
            targets=lifecycle_targets,
            prices=request.prices,
            total_equity=float(
                request.total_equity
                if request.approved_execution_package is not None
                else (
                    request.rebudget_total_equity
                    if request.rebudget_total_equity is not None
                    else post_sell_budget_meta.get("post_sell_equity")
                    or request.total_equity
                )
            ),
            buy_budget=float(buy_budget or 0.0),
            cfg=cfg,
            max_buy_orders=max_rebudget_buy_orders,
            zero_budget_block_reason=zero_budget_block_reason,
        )
    elif sell_orders_present and not sell_phase_allows_buy:
        buy_budget, post_sell_budget_meta = _post_sell_buy_budget(
            account=post_sell_account,
            cfg=cfg,
            target_cash_weight=governed_cash_floor_weight,
            fallback_equity=float(request.total_equity),
            capital_constraint_clear=capital_constraint_clear,
        )
        buy_budget, post_sell_budget_meta = _apply_configured_post_sell_budget(
            buy_budget=buy_budget,
            post_sell_budget_meta=post_sell_budget_meta,
            account=post_sell_account,
            config=config,
        )
        rebuilt_buy_trades = original_buy_trades.iloc[0:0].copy()
        block_reason = str(
            post_sell_snapshot.raw.get("sell_phase_block_reason")
            or "sell_phase_not_fully_confirmed"
        )
        rebudget_meta = {
            "status": "SKIPPED",
            "reason_codes": [block_reason],
            "candidate_count": int(len(original_buy_trades)),
            "recomputed_requested_buy_notional": requested_buy_notional,
            "recomputed_buy_notional": 0.0,
        }
        rebudget_skipped = [
            {**row.to_dict(), "block_reason": block_reason}
            for _, row in original_buy_trades.iterrows()
        ]
    else:
        buy_budget, buy_budget_basis = _compute_buy_budget(
            post_sell_account,
            cfg,
            capital_constraint_clear=capital_constraint_clear,
        )
        post_sell_equity = float(
            post_sell_account.get("equity")
            or post_sell_account.get("portfolio_value")
            or request.total_equity
            or 0.0
        )
        post_sell_cash = float(post_sell_account.get("cash") or 0.0)
        risk_cash_target = max(0.0, post_sell_equity * governed_cash_floor_weight)
        risk_cash_target_buy_budget = max(0.0, post_sell_cash - risk_cash_target)
        buy_budget = min(float(buy_budget or 0.0), risk_cash_target_buy_budget)
        post_sell_budget_meta = {
            "post_sell_cash": post_sell_cash,
            "post_sell_equity": post_sell_equity,
            "post_sell_buying_power": post_sell_account.get("buying_power"),
            "risk_cash_target": risk_cash_target,
            "target_cash_weight": float(request.target_cash_weight or 0.0),
            "minimum_cash_weight": governed_cash_floor_weight,
            "buy_budget_before_safeguards": post_sell_cash,
            "broker_safeguard_buy_budget": float(buy_budget or 0.0),
            "risk_cash_target_buy_budget": risk_cash_target_buy_budget,
            "buy_budget_after_safeguards": float(buy_budget or 0.0),
            "buy_budget_basis": buy_budget_basis,
        }
        buy_budget, post_sell_budget_meta = _apply_configured_post_sell_budget(
            buy_budget=buy_budget,
            post_sell_budget_meta=post_sell_budget_meta,
            account=post_sell_account,
            config=config,
        )
        max_rebudget_buy_orders = max(0, int(cfg.max_trades_per_day or 0))
        if config.constraints.max_buy_orders is not None:
            max_rebudget_buy_orders = min(
                max_rebudget_buy_orders,
                int(config.constraints.max_buy_orders),
            )
        if config.constraints.max_one_order:
            max_rebudget_buy_orders = min(max_rebudget_buy_orders, 1)
        if target_policy is None:
            # Historical precomputed callers do not carry an immutable target
            # quantity proof. Preserve their already-approved symbols and only
            # clip quantities to the available cash budget; rebuilding from the
            # ambient portfolio target would be downstream target substitution.
            requested_legacy_buy_notional = float(
                original_buy_trades.get("notional", pd.Series(dtype=float))
                .fillna(0.0)
                .astype(float)
                .abs()
                .sum()
            )
            legacy_budget = {
                "reserve_cash_policy": {
                    "available_for_buys": float(buy_budget or 0.0),
                },
                "requested_buy_notional": requested_legacy_buy_notional,
            }
            rebuilt_buy_trades, legacy_budget_meta = _apply_capital_budget_to_trades(
                original_buy_trades,
                cfg,
                legacy_budget,
            )
            rebuilt_buy_trades, rebudget_skipped = _apply_buy_order_limit(
                rebuilt_buy_trades,
                config=config,
            )
            rebudget_meta = {
                "status": (
                    "CLIPPED"
                    if bool(legacy_budget_meta.get("capital_constraint_triggered"))
                    else "OK"
                ),
                "reason_codes": ["legacy_approved_buys_cash_rebudgeted"],
                "candidate_count": int(len(original_buy_trades)),
                "recomputed_requested_buy_notional": requested_legacy_buy_notional,
                "recomputed_buy_notional": float(
                    rebuilt_buy_trades.get("notional", pd.Series(dtype=float))
                    .fillna(0.0)
                    .astype(float)
                    .abs()
                    .sum()
                ),
                "clipped_or_deferred_buys_count": int(
                    legacy_budget_meta.get("clipped_or_deferred_buys_count") or 0
                ),
            }
        else:
            rebuilt_buy_trades, rebudget_meta, rebudget_skipped = (
                _rebuild_post_sell_buy_trades(
                    holdings=post_sell_holdings,
                    targets=lifecycle_targets,
                    prices=request.prices,
                    total_equity=float(request.total_equity),
                    buy_budget=float(buy_budget or 0.0),
                    cfg=cfg,
                    max_buy_orders=max_rebudget_buy_orders,
                    zero_budget_block_reason=(
                        BUY_BLOCKED_RISK_CASH_TARGET
                        if float(buy_budget or 0.0) <= 1e-9
                        else None
                    ),
                    cash_at_decision=post_sell_cash,
                    buying_power_at_decision=_safe_float(
                        post_sell_account.get("buying_power"), 0.0
                    ),
                    risk_cash_target=risk_cash_target,
                    buy_budget_basis=str(
                        post_sell_budget_meta.get("buy_budget_basis") or ""
                    ),
                )
            )
        rebudget_meta = dict(rebudget_meta)
        rebudget_meta["reason_codes"] = [
            "no_sell_orders_rebudgeted",
            *list(rebudget_meta.get("reason_codes") or []),
        ]

    final_trade_frames = [
        frame
        for frame in (sell_trades.copy(), rebuilt_buy_trades)
        if not frame.empty
    ]
    if final_trade_frames:
        final_execution_trades = pd.concat(
            final_trade_frames,
            ignore_index=True,
            sort=False,
        )
    else:
        final_execution_trades = pd.DataFrame()
    final_execution_trades = final_execution_trades.reindex(
        columns=executable_trades.columns if executable_trades is not None else None
    )
    final_buy_orders = _build_shadow_orders(
        rebuilt_buy_trades,
        request.run_id,
        allow_fractional=bool(cfg.allow_fractional),
    )
    submitted_buys = tuple(
        _submit_with_ledger(adapter=adapter, intent=intent, config=config, request=request)
        for intent in _intents_from_frame(rebuilt_buy_trades)
    )

    return ExecutionResult(
        request=request,
        config=config,
        raw_trades=raw_trades,
        trade_meta=trade_meta,
        capital_trades=capital_trades,
        capital_budget=capital_budget,
        executable_trades=executable_trades,
        execution_filter_stats=execution_filter_stats,
        sell_trades=sell_trades,
        original_buy_trades=original_buy_trades,
        post_sell_snapshot=post_sell_snapshot,
        sell_fill_meta=sell_fill_meta,
        post_sell_budget_meta=post_sell_budget_meta,
        rebuilt_buy_trades=rebuilt_buy_trades,
        rebudget_meta=rebudget_meta,
        rebudget_skipped=list(rebudget_skipped or []),
        final_execution_trades=final_execution_trades,
        final_buy_orders=final_buy_orders,
        submitted_sells=submitted_sells,
        submitted_buys=submitted_buys,
    )


class SynchronousTestAdapter:
    """Synchronous fill adapter for parity tests.

    This deliberately mirrors paper's current assumption: submitted sells are
    immediately reflected in cash and holdings before the post-sell rebudget.
    """

    def __init__(
        self,
        *,
        holdings: pd.DataFrame,
        starting_cash: float,
        post_sell_account: Mapping[str, Any] | None,
        planning_account: Mapping[str, Any],
        preserve_post_sell_account_cash: bool = False,
        portfolio_id: str = "parity",
    ) -> None:
        self._starting_holdings = holdings.copy()
        self._starting_cash = float(starting_cash)
        self._post_sell_account = dict(post_sell_account or planning_account)
        self._planning_account = dict(planning_account)
        self._preserve_post_sell_account_cash = bool(preserve_post_sell_account_cash)
        self._portfolio_id = str(portfolio_id or "parity")
        self.submissions: list[SubmitResult] = []

    def submit(self, intent: OrderIntent) -> SubmitResult:
        result = SubmitResult(
            intent=intent,
            status="FILLED",
            broker_order_id=None,
            filled_qty=float(intent.shares),
            filled_notional=float(intent.notional),
        )
        self.submissions.append(result)
        return result

    def snapshot(self) -> AccountSnapshot:
        return self.wait_or_refresh(())

    def wait_or_refresh(self, submitted_sells: Sequence[SubmitResult]) -> AccountSnapshot:
        shares_by_symbol: dict[str, float] = {}
        for _, row in self._starting_holdings.iterrows():
            symbol = str(row.get("ticker") or "").upper()
            if symbol:
                shares_by_symbol[symbol] = shares_by_symbol.get(symbol, 0.0) + float(row.get("shares") or 0.0)

        cash = float(self._starting_cash)
        proceeds = 0.0
        computed_post_sell_cash = float(cash)
        sell_records: list[dict[str, Any]] = []
        for result in submitted_sells:
            intent = result.intent
            if str(intent.side).upper() not in {"SELL", "CLOSE", "REDUCE"}:
                continue
            proceeds += float(intent.notional)
            cash += float(intent.notional)
            shares_by_symbol[intent.symbol] = max(
                0.0,
                float(shares_by_symbol.get(intent.symbol, 0.0)) - float(intent.shares),
            )
            sell_records.append(
                {
                    "symbol": intent.symbol,
                    "shares": float(intent.shares),
                    "notional": float(intent.notional),
                    "fill_status": "FILLED",
                }
            )
        computed_post_sell_cash = float(cash)

        account = dict(self._post_sell_account or self._planning_account)
        if self._preserve_post_sell_account_cash and account.get("cash") is not None:
            cash = float(account.get("cash") or 0.0)
            proceeds = max(0.0, float(cash) - self._starting_cash)
        account["cash"] = str(cash)
        if "buying_power" not in account or account.get("buying_power") is None:
            account["buying_power"] = str(cash)

        post_holdings = [
            {"ticker": symbol, "sleeve": self._portfolio_id, "shares": shares}
            for symbol, shares in sorted(shares_by_symbol.items())
            if shares > 1e-12
        ]
        return AccountSnapshot(
            account=account,
            holdings=pd.DataFrame(post_holdings, columns=["ticker", "sleeve", "shares"]),
            raw={
                "sell_fill_meta": {
                    "sell_orders": sell_records,
                    "confirmed_sell_proceeds": proceeds,
                    "post_sell_cash_from_sync_fills": computed_post_sell_cash,
                    "post_sell_cash_used_for_rebudget": cash,
                    "fill_model": "synchronous_all_sells_filled",
                }
            },
        )

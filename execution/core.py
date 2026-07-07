from __future__ import annotations

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


@dataclass(frozen=True)
class OrderPolicy:
    """Order sizing configuration shared by paper and live modes."""

    units: str = "shares"
    allow_fractional: bool = True
    min_trade_usd: float = 100.0
    slippage_bps: float = 0.0
    cash_buffer_bps: float = 0.0


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
    max_orders: int = 1,
    equity_collar_max_usd: float | None = 520.0,
    ledger_output_root: str | Path | None = None,
    ledger_enabled: bool = True,
) -> ExecutionCoreConfig:
    """Live pilot's conservative values expressed as config.

    Phase 1B does not wire live to this core. This factory documents the intended
    same-path settings for Phase 1D: cap blocks instead of clipping, one max buy,
    no paper reserve, equity collar, and fail-closed malformed holdings.
    """

    cfg = PaperConfig(
        initial_equity=float(approved_cap_usd or 0.0),
        benchmark_ticker="SPY",
        slippage_bps=0.0,
        allow_fractional=bool(allow_fractional),
        min_trade_dollars=100.0,
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
        ),
        orders=OrderPolicy(
            allow_fractional=bool(allow_fractional),
            min_trade_usd=100.0,
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
        ticker = str(row.get("ticker") or "").upper()
        side = str(row.get("side") or "").upper()
        shares = abs(float(row.get("shares") or row.get("quantity") or 0.0))
        price = float(row.get("price") or row.get("entry_price") or 0.0)
        notional = abs(float(row.get("notional") or (shares * price)))
        if not ticker or side not in {"BUY", "SELL", "CLOSE", "REDUCE"}:
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
        return float(value)
    except (TypeError, ValueError):
        return float(default)


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
    if config.ledger_enabled:
        record_live_order(
            event="submitted",
            symbol=intent.symbol,
            side=intent.side,
            qty=intent.shares,
            limit_price=intent.price,
            notional=intent.notional,
            client_order_id=intent.client_order_id,
            broker_order_id=result.broker_order_id,
            run_root=request.run_id,
            status=result.status,
            reason=intent.reason,
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
            target_cash_weight=float(request.target_cash_weight),
            fallback_equity=float(request.total_equity),
            capital_constraint_clear=capital_constraint_clear,
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
            targets=request.targets,
            prices=request.prices,
            total_equity=float(
                request.rebudget_total_equity
                if request.rebudget_total_equity is not None
                else post_sell_budget_meta.get("post_sell_equity")
                or request.total_equity
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
            target_cash_weight=float(request.target_cash_weight),
            fallback_equity=float(request.total_equity),
            capital_constraint_clear=capital_constraint_clear,
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
        risk_cash_target = max(0.0, post_sell_equity * float(request.target_cash_weight or 0.0))
        post_sell_budget_meta = {
            "post_sell_cash": post_sell_cash,
            "post_sell_equity": post_sell_equity,
            "post_sell_buying_power": post_sell_account.get("buying_power"),
            "risk_cash_target": risk_cash_target,
            "target_cash_weight": float(request.target_cash_weight or 0.0),
            "buy_budget_before_safeguards": post_sell_cash,
            "broker_safeguard_buy_budget": float(buy_budget or 0.0),
            "risk_cash_target_buy_budget": max(0.0, post_sell_cash - risk_cash_target),
            "buy_budget_after_safeguards": float(buy_budget or 0.0),
            "buy_budget_basis": buy_budget_basis,
        }
        rebuilt_buy_trades = original_buy_trades.copy()
        rebudget_meta = {
            "status": "SKIPPED",
            "reason_codes": ["no_sell_orders"],
            "candidate_count": int(len(rebuilt_buy_trades)),
            "recomputed_requested_buy_notional": requested_buy_notional,
            "recomputed_buy_notional": requested_buy_notional,
        }
        rebudget_skipped = []

    final_execution_trades = pd.concat(
        [sell_trades.copy(), rebuilt_buy_trades],
        ignore_index=True,
        sort=False,
    ).reindex(columns=executable_trades.columns if executable_trades is not None else None)
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

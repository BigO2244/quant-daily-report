from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

import pandas as pd

from paper.paper_broker import PaperConfig


@dataclass(frozen=True)
class PaperParityScenario:
    name: str
    source: str
    notes: str
    holdings: tuple[dict[str, Any], ...]
    targets: tuple[dict[str, Any], ...]
    prices: dict[str, float]
    total_equity: float
    starting_cash: float
    target_cash_weight: float
    planning_account: dict[str, Any]
    post_sell_account: dict[str, Any] | None = None
    price_basis: str = "fixture_price"
    cfg_overrides: dict[str, Any] = field(default_factory=dict)
    precomputed_trades: tuple[dict[str, Any], ...] = ()
    rebudget_total_equity: float | None = None
    artifact_expectations: dict[str, Any] = field(default_factory=dict)
    preserve_post_sell_account_cash: bool = False


def _cfg(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "initial_equity": 10000.0,
        "benchmark_ticker": "SPY",
        "slippage_bps": 0.0,
        "allow_fractional": True,
        "min_trade_dollars": 100.0,
        "cash_buffer_bps": 0.0,
        "trading_mode": "paper",
        "portfolio_id": "parity",
        "strategy_version": "paper_native",
        "max_turnover_pct": 1.0,
        "max_trades_per_day": 50,
        "max_position_change_pct": 1.0,
        "max_position_pct": 1.0,
        "risk_action": "warn",
        "cash_target_weight_default": 0.0,
        "rebalance_deadband_pct": 0.01,
    }
    defaults.update(overrides)
    return defaults


def make_config(scenario: PaperParityScenario) -> PaperConfig:
    return PaperConfig(**_cfg(**scenario.cfg_overrides))


def holdings_frame(scenario: PaperParityScenario) -> pd.DataFrame:
    return pd.DataFrame(list(scenario.holdings), columns=["ticker", "sleeve", "shares"])


def targets_frame(scenario: PaperParityScenario) -> pd.DataFrame:
    return pd.DataFrame(list(scenario.targets), columns=["ticker", "target_weight", "sleeve"])


def prices_series(scenario: PaperParityScenario) -> pd.Series:
    return pd.Series(scenario.prices, dtype=float)


def _current_weight(*, shares: float, price: float, equity: float) -> float:
    return float(shares * price / equity)


def _july_2026_scenario() -> PaperParityScenario:
    pre_equity = 11200.0
    post_equity = 10682.0
    target_cash_weight = 939.0 / post_equity
    holdings: list[dict[str, Any]] = [
        {"ticker": "WBD", "sleeve": "parity", "shares": 10.0},
        {"ticker": "CVS", "sleeve": "parity", "shares": 2.0},
        {"ticker": "MO", "sleeve": "parity", "shares": 4.0},
        {"ticker": "NEE", "sleeve": "parity", "shares": 1.0},
    ]
    prices: dict[str, float] = {
        "WBD": 10.0,
        "CVS": 50.0,
        "MO": 25.0,
        "NEE": 100.0,
        "GE": 99.0,
        "PNC": 75.0,
        "AXP": 105.0,
        "CAT": 105.0,
        "GLW": 52.5,
        "MS": 70.0,
        "RTX": 105.0,
        "TXN": 105.0,
        "USB": 52.5,
    }
    targets: list[dict[str, Any]] = [
        {"ticker": "GE", "target_weight": 330.0 / pre_equity, "sleeve": "parity"},
        {"ticker": "PNC", "target_weight": 295.0 / pre_equity, "sleeve": "parity"},
        {"ticker": "AXP", "target_weight": 0.0105, "sleeve": "parity"},
        {"ticker": "CAT", "target_weight": 0.0105, "sleeve": "parity"},
        {"ticker": "GLW", "target_weight": 0.0105, "sleeve": "parity"},
        {"ticker": "MS", "target_weight": 0.0105, "sleeve": "parity"},
        {"ticker": "RTX", "target_weight": 0.0105, "sleeve": "parity"},
        {"ticker": "TXN", "target_weight": 0.0105, "sleeve": "parity"},
        {"ticker": "USB", "target_weight": 0.0105, "sleeve": "parity"},
    ]
    for idx in range(1, 35):
        ticker = f"H{idx:02d}"
        price = 100.0 + float(idx % 5)
        shares = 1.0 + float(idx % 3)
        holdings.append({"ticker": ticker, "sleeve": "parity", "shares": shares})
        prices[ticker] = price
        targets.append(
            {
                "ticker": ticker,
                "target_weight": _current_weight(
                    shares=shares,
                    price=price,
                    equity=pre_equity,
                ),
                "sleeve": "parity",
            }
        )
    return PaperParityScenario(
        name="2026_07_07_synthetic_38pos",
        source=(
            "Synthesized because outputs/runs/2026-07-07T093509-0400_b6bf4a2 "
            "was not present in the local worktree."
        ),
        notes=(
            "38-position book matching the operator email shape: sells WBD/CVS/MO/NEE; "
            "nine post-sell buy candidates; GE and PNC resized by post-sell rebudget; "
            "seven lower-priority buys suppressed; estimated ending cash is 939.00, "
            "8.79% of post-sell equity."
        ),
        holdings=tuple(holdings),
        targets=tuple(targets),
        prices=prices,
        total_equity=pre_equity,
        starting_cash=1100.0,
        target_cash_weight=target_cash_weight,
        planning_account={
            "cash": "1100.0",
            "equity": str(pre_equity),
            "buying_power": "1100.0",
            "status": "ACTIVE",
        },
        post_sell_account={
            "cash": "1500.0",
            "equity": str(post_equity),
            "buying_power": "1500.0",
            "status": "ACTIVE",
        },
        price_basis="synthetic_prev_close_no_slippage",
        cfg_overrides=_cfg(
            initial_equity=pre_equity,
            allow_fractional=True,
            min_trade_dollars=100.0,
            max_trades_per_day=13,
            cash_target_weight_default=target_cash_weight,
        ),
    )


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _real_fixture_root() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "real" / "2026_07_07"


def _artifact_rebudget_equity(
    *,
    skipped_buy_orders: list[dict[str, Any]],
    post_sell_shares: dict[str, float],
) -> float:
    implied: list[float] = []
    for row in skipped_buy_orders:
        target_weight = float(row.get("target_weight") or 0.0)
        price = float(row.get("price") or 0.0)
        shares = float(row.get("shares") or 0.0)
        ticker = str(row.get("ticker") or "").upper()
        if target_weight <= 0.0 or price <= 0.0 or not ticker:
            continue
        implied.append((float(post_sell_shares.get(ticker, 0.0)) + shares) * price / target_weight)
    if not implied:
        raise ValueError("real 2026-07-07 fixture cannot derive rebudget equity")
    return float(round(sum(implied) / len(implied), 8))


def _real_2026_07_07_scenario() -> PaperParityScenario:
    root = _real_fixture_root()
    pretrade_positions = _load_json(root / "pretrade_positions.json")
    post_sell_rebudget = _load_json(root / "post_sell_rebudget_2026-07-07.json")
    execution_results = _load_json(root / "execution_results.json")
    execution_payload = _load_json(root / "execution_payload.json")
    planned_execution_payload = _load_json(root / "planned_execution_payload.json")
    signals = _load_json(root / "signals.json")

    holdings: list[dict[str, Any]] = []
    post_sell_shares: dict[str, float] = {}
    for row in pretrade_positions.get("positions") or []:
        ticker = str(row.get("symbol") or "").upper()
        if not ticker:
            continue
        shares = float(row.get("qty") or 0.0)
        holdings.append({"ticker": ticker, "sleeve": "main", "shares": shares})
        post_sell_shares[ticker] = shares

    exact_sells: list[dict[str, Any]] = []
    for row in execution_payload.get("trades") or []:
        ticker = str(row.get("ticker") or "").upper()
        side = str(row.get("side") or "").upper()
        shares = abs(float(row.get("shares") or 0.0))
        notional = abs(float(row.get("notional") or 0.0))
        if side != "SELL" or not ticker or shares <= 0.0:
            continue
        price = notional / shares
        exact_sells.append(
            {
                "ticker": ticker,
                "side": "SELL",
                "shares": shares,
                "price": price,
                "slippage_cost": 0.0,
                "notional": notional,
                "reason": str(row.get("reason") or "removed_from_targets"),
            }
        )
        post_sell_shares[ticker] = max(0.0, float(post_sell_shares.get(ticker, 0.0)) - shares)

    rebudget_equity = _artifact_rebudget_equity(
        skipped_buy_orders=list(post_sell_rebudget.get("skipped_buy_orders") or []),
        post_sell_shares=post_sell_shares,
    )
    signal_by_ticker = {
        str(row.get("ticker") or "").upper(): dict(row)
        for row in signals.get("signals") or []
    }
    prices: dict[str, float] = {}
    targets: list[dict[str, Any]] = []

    for row in post_sell_rebudget.get("final_buy_orders_submitted") or []:
        ticker = str(row.get("ticker") or "").upper()
        shares = float(row.get("quantity") or 0.0)
        notional = float(row.get("notional") or 0.0)
        if not ticker or shares <= 0.0:
            continue
        price = notional / shares
        prices[ticker] = price
        if ticker == "PNC":
            target_weight = (float(post_sell_shares.get(ticker, 0.0)) + shares) * price / rebudget_equity
        else:
            target_weight = float((signal_by_ticker.get(ticker) or {}).get("target_weight") or 0.0)
        targets.append(
            {
                "ticker": ticker,
                "target_weight": target_weight,
                "sleeve": str((signal_by_ticker.get(ticker) or {}).get("sleeve") or "main"),
            }
        )

    for row in post_sell_rebudget.get("skipped_buy_orders") or []:
        ticker = str(row.get("ticker") or "").upper()
        if not ticker:
            continue
        prices[ticker] = float(row.get("price") or 0.0)
        targets.append(
            {
                "ticker": ticker,
                "target_weight": float(row.get("target_weight") or 0.0),
                "sleeve": str((signal_by_ticker.get(ticker) or {}).get("sleeve") or "main"),
            }
        )

    planned_buys: list[dict[str, Any]] = []
    for row in planned_execution_payload.get("trades") or []:
        ticker = str(row.get("ticker") or "").upper()
        side = str(row.get("side") or "").upper()
        if side != "BUY" or not ticker:
            continue
        price = float(row.get("entry_price") or row.get("price") or 0.0)
        planned_buys.append(
            {
                "ticker": ticker,
                "side": "BUY",
                "shares": abs(float(row.get("shares") or 0.0)),
                "price": price,
                "slippage_cost": 0.0,
                "notional": abs(float(row.get("notional") or 0.0)),
                "reason": str(row.get("reason") or row.get("notes") or "rebalance_to_target"),
            }
        )
        prices.setdefault(ticker, price)

    return PaperParityScenario(
        name="2026_07_07_real",
        source=(
            "Read-only VM artifact copy from "
            "/home/brettolson/quant-daily-report/outputs/runs/"
            "2026-07-07T093509-0400_b6bf4a2 plus referenced precompute target artifacts."
        ),
        notes=(
            "Ground-truth paper run anchor. Exact submitted sells come from execution_payload; "
            "original planned buys come from planned_execution_payload; post-sell target "
            "candidates and expected cash outputs come from post_sell_rebudget_2026-07-07.json."
        ),
        holdings=tuple(holdings),
        targets=tuple(targets),
        prices=prices,
        total_equity=float(post_sell_rebudget.get("pre_sell_equity") or 0.0),
        starting_cash=float(post_sell_rebudget.get("pre_sell_cash") or 0.0),
        target_cash_weight=float(post_sell_rebudget.get("target_cash_weight") or 0.0),
        planning_account={
            "cash": str(post_sell_rebudget.get("pre_sell_cash")),
            "equity": str(post_sell_rebudget.get("pre_sell_equity")),
            "buying_power": str(post_sell_rebudget.get("pre_sell_buying_power")),
            "status": "ACTIVE",
        },
        post_sell_account={
            "cash": str(post_sell_rebudget.get("post_sell_cash")),
            "equity": str(post_sell_rebudget.get("post_sell_equity")),
            "buying_power": str(post_sell_rebudget.get("post_sell_buying_power")),
            "status": "ACTIVE",
        },
        price_basis="real_run_precompute_prev_close",
        cfg_overrides=_cfg(
            initial_equity=float(post_sell_rebudget.get("pre_sell_equity") or 0.0),
            allow_fractional=True,
            min_trade_dollars=100.0,
            max_trades_per_day=50,
            portfolio_id="main",
            strategy_version="growth_engine_v4",
            cash_target_weight_default=float(post_sell_rebudget.get("target_cash_weight") or 0.0),
        ),
        precomputed_trades=tuple([*exact_sells, *planned_buys]),
        rebudget_total_equity=rebudget_equity,
        artifact_expectations={
            "ending_cash": float(post_sell_rebudget.get("ending_cash") or 0.0),
            "estimated_ending_cash": float(post_sell_rebudget.get("estimated_ending_cash") or 0.0),
            "target_cash_weight": float(post_sell_rebudget.get("target_cash_weight") or 0.0),
            "post_sell_equity": float(post_sell_rebudget.get("post_sell_equity") or 0.0),
            "buys": [str(row.get("ticker") or "") for row in post_sell_rebudget.get("final_buy_orders_submitted") or []],
            "skipped": [str(row.get("ticker") or "") for row in post_sell_rebudget.get("skipped_buy_orders") or []],
            "source_run_id": str(execution_results.get("run_id") or ""),
        },
        preserve_post_sell_account_cash=True,
    )


def _buy_only_scenario() -> PaperParityScenario:
    return PaperParityScenario(
        name="buy_only_no_sells",
        source="Synthetic parity fixture.",
        notes="No sells are present; paper skips post-sell rebuild and preserves planned buys.",
        holdings=(),
        targets=(
            {"ticker": "AAPL", "target_weight": 0.30, "sleeve": "parity"},
            {"ticker": "MSFT", "target_weight": 0.20, "sleeve": "parity"},
        ),
        prices={"AAPL": 200.0, "MSFT": 100.0},
        total_equity=10000.0,
        starting_cash=10000.0,
        target_cash_weight=0.20,
        planning_account={
            "cash": "10000.0",
            "equity": "10000.0",
            "buying_power": "10000.0",
            "status": "ACTIVE",
        },
        post_sell_account={
            "cash": "10000.0",
            "equity": "10000.0",
            "buying_power": "10000.0",
            "status": "ACTIVE",
        },
        cfg_overrides=_cfg(initial_equity=10000.0),
    )


def _sells_only_scenario() -> PaperParityScenario:
    return PaperParityScenario(
        name="sells_only_removed_targets",
        source="Synthetic parity fixture.",
        notes="All held names are absent from targets, so paper emits removed_from_targets exits only.",
        holdings=(
            {"ticker": "OLD1", "sleeve": "parity", "shares": 2.0},
            {"ticker": "OLD2", "sleeve": "parity", "shares": 3.0},
        ),
        targets=(),
        prices={"OLD1": 150.0, "OLD2": 80.0},
        total_equity=1000.0,
        starting_cash=460.0,
        target_cash_weight=1.0,
        planning_account={
            "cash": "460.0",
            "equity": "1000.0",
            "buying_power": "460.0",
            "status": "ACTIVE",
        },
        post_sell_account={
            "cash": "1000.0",
            "equity": "1000.0",
            "buying_power": "1000.0",
            "status": "ACTIVE",
        },
        cfg_overrides=_cfg(initial_equity=1000.0, max_trades_per_day=5),
    )


def _deadband_scenario() -> PaperParityScenario:
    return PaperParityScenario(
        name="deadband_tiny_drift",
        source="Synthetic parity fixture.",
        notes="Target drift is below the 1% deadband and is surfaced in trade_meta.deadband_skipped.",
        holdings=({"ticker": "TINY", "sleeve": "parity", "shares": 10.0},),
        targets=({"ticker": "TINY", "target_weight": 0.105, "sleeve": "parity"},),
        prices={"TINY": 100.0},
        total_equity=10000.0,
        starting_cash=9000.0,
        target_cash_weight=0.0,
        planning_account={
            "cash": "9000.0",
            "equity": "10000.0",
            "buying_power": "9000.0",
            "status": "ACTIVE",
        },
        post_sell_account={
            "cash": "9000.0",
            "equity": "10000.0",
            "buying_power": "9000.0",
            "status": "ACTIVE",
        },
        cfg_overrides=_cfg(initial_equity=10000.0, min_trade_dollars=1.0),
    )


def _whole_share_sweep_scenario() -> PaperParityScenario:
    return PaperParityScenario(
        name="whole_share_sweep_adds_share",
        source="Synthetic parity fixture based on Tests/test_market_window_planning.py.",
        notes="Whole-share mode leaves enough investable cash for paper's deterministic cash sweep to add a share.",
        holdings=(),
        targets=(
            {"ticker": "AAPL", "target_weight": 0.20, "sleeve": "parity"},
            {"ticker": "MSFT", "target_weight": 0.15, "sleeve": "parity"},
            {"ticker": "NVDA", "target_weight": 0.10, "sleeve": "parity"},
        ),
        prices={"AAPL": 501.0, "MSFT": 499.0, "NVDA": 497.0},
        total_equity=10000.0,
        starting_cash=10000.0,
        target_cash_weight=0.55,
        planning_account={
            "cash": "10000.0",
            "equity": "10000.0",
            "buying_power": "10000.0",
            "status": "ACTIVE",
        },
        post_sell_account={
            "cash": "10000.0",
            "equity": "10000.0",
            "buying_power": "10000.0",
            "status": "ACTIVE",
        },
        cfg_overrides=_cfg(initial_equity=10000.0, allow_fractional=False),
    )


def _over_budget_suppression_scenario() -> PaperParityScenario:
    return PaperParityScenario(
        name="post_sell_over_budget_suppression",
        source="Synthetic parity fixture.",
        notes="A synchronous sell creates cash, but the risk-cash target leaves only one clipped buy budget and suppresses the remainder.",
        holdings=({"ticker": "LEGACY", "sleeve": "parity", "shares": 1.0},),
        targets=(
            {"ticker": "BIG", "target_weight": 0.45, "sleeve": "parity"},
            {"ticker": "MID", "target_weight": 0.30, "sleeve": "parity"},
            {"ticker": "SMALL", "target_weight": 0.20, "sleeve": "parity"},
        ),
        prices={"LEGACY": 500.0, "BIG": 300.0, "MID": 200.0, "SMALL": 100.0},
        total_equity=2000.0,
        starting_cash=200.0,
        target_cash_weight=0.275,
        planning_account={
            "cash": "200.0",
            "equity": "2000.0",
            "buying_power": "200.0",
            "status": "ACTIVE",
        },
        post_sell_account={
            "cash": "700.0",
            "equity": "2000.0",
            "buying_power": "700.0",
            "status": "ACTIVE",
        },
        cfg_overrides=_cfg(initial_equity=2000.0, max_trades_per_day=4),
    )


def scenarios() -> tuple[PaperParityScenario, ...]:
    return (
        _july_2026_scenario(),
        _real_2026_07_07_scenario(),
        _buy_only_scenario(),
        _sells_only_scenario(),
        _deadband_scenario(),
        _whole_share_sweep_scenario(),
        _over_budget_suppression_scenario(),
    )


def scenario_by_name(name: str) -> PaperParityScenario:
    for scenario in scenarios():
        if scenario.name == name:
            return scenario
    raise KeyError(name)

import pandas as pd

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from paper.paper_broker import PaperConfig, build_rebalance_trades, apply_trades_to_holdings


def _cfg() -> PaperConfig:
    return PaperConfig(
        initial_equity=10_000.0,
        benchmark_ticker="SPY",
        slippage_bps=0.0,
        allow_fractional=False,
        min_trade_dollars=0.0,
        cash_buffer_bps=0.0,
    )


def test_cash_enforced_with_rounding_pressure():
    holdings = pd.DataFrame(columns=["ticker", "sleeve", "shares"])
    targets = pd.DataFrame(
        [
            {"ticker": "AAA", "sleeve": "core", "target_weight": 0.35},
            {"ticker": "BBB", "sleeve": "core", "target_weight": 0.35},
        ]
    )
    prices = pd.Series({"AAA": 3334.0, "BBB": 3334.0}, dtype=float)

    trades, meta = build_rebalance_trades(
        holdings=holdings,
        targets=targets,
        prices=prices,
        total_equity=10_000.0,
        starting_cash=10_000.0,
        target_cash_weight=0.30,
        cfg=_cfg(),
    )
    holdings_new, cash_new = apply_trades_to_holdings(
        holdings=holdings,
        targets=targets,
        trades=trades,
        starting_cash=10_000.0,
    )
    if holdings_new.empty:
        invested = 0.0
    else:
        valued = holdings_new.merge(prices.rename("price"), left_on="ticker", right_index=True)
        invested = float((valued["shares"] * valued["price"]).sum())

    assert cash_new >= 0.0
    assert invested <= 7000.0 + 1e-9
    one_share_step = float(prices.iloc[0]) / 10_000.0
    assert abs((cash_new / 10_000.0) - 0.30) <= max(0.01, one_share_step)
    assert isinstance(meta["overspend_prevented"], bool)


def test_all_sell_day_increases_cash():
    holdings = pd.DataFrame([
        {"ticker": "AAA", "sleeve": "core", "shares": 10.0},
    ])
    targets = pd.DataFrame([
        {"ticker": "BBB", "sleeve": "core", "target_weight": 0.0},
    ])
    prices = pd.Series({"AAA": 100.0, "BBB": 100.0}, dtype=float)

    trades, _ = build_rebalance_trades(
        holdings=holdings,
        targets=targets,
        prices=prices,
        total_equity=1_000.0,
        starting_cash=0.0,
        target_cash_weight=1.0,
        cfg=_cfg(),
    )
    holdings_new, cash_new = apply_trades_to_holdings(holdings, targets, trades, 0.0)

    assert cash_new >= 1_000.0
    assert holdings_new.empty


def test_no_trades_day_is_deterministic():
    holdings = pd.DataFrame([
        {"ticker": "AAA", "sleeve": "core", "shares": 10.0},
    ])
    targets = pd.DataFrame([
        {"ticker": "AAA", "sleeve": "core", "target_weight": 1.0},
    ])
    prices = pd.Series({"AAA": 100.0}, dtype=float)

    trades1, _ = build_rebalance_trades(
        holdings=holdings,
        targets=targets,
        prices=prices,
        total_equity=1_000.0,
        starting_cash=0.0,
        target_cash_weight=0.0,
        cfg=_cfg(),
    )
    trades2, _ = build_rebalance_trades(
        holdings=holdings,
        targets=targets,
        prices=prices,
        total_equity=1_000.0,
        starting_cash=0.0,
        target_cash_weight=0.0,
        cfg=_cfg(),
    )

    assert trades1.empty
    assert trades2.empty

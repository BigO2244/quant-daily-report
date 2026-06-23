from __future__ import annotations

import pandas as pd

from research.alpha_lab_v1.signals import build_alpha_lab_signal_frame
from research.alpha_lab_v2.engine import prepare_backtest_inputs
from scripts.research.build_conviction_allocation_research import (
    PolicySpec,
    _backtest_weight_history,
    _run_conviction_policy,
    allocate_score_weighted,
    rank_bucket_forward_returns,
)


def _make_panel() -> pd.DataFrame:
    dates = pd.date_range("2022-01-03", periods=340, freq="B")
    slopes = {
        "AAA": 0.0030,
        "BBB": 0.0022,
        "CCC": 0.0015,
        "DDD": 0.0005,
        "EEE": -0.0002,
        "FFF": -0.0005,
        "GGG": -0.0007,
        "HHH": -0.0008,
        "III": -0.0009,
        "JJJ": -0.0010,
        "KKK": -0.0011,
        "LLL": -0.0012,
        "MMM": -0.0013,
        "NNN": -0.0014,
        "SPY": 0.0010,
    }
    rows = []
    for ticker, slope in slopes.items():
        price = 100.0
        for dt in dates:
            price *= 1.0 + slope
            rows.append(
                {
                    "date": dt,
                    "ticker": ticker,
                    "open": price * 0.99,
                    "high": price * 1.01,
                    "low": price * 0.98,
                    "close": price,
                    "volume": 1_000_000,
                }
            )
    return pd.DataFrame(rows)


def test_score_weighted_allocator_respects_cap_and_floor() -> None:
    daily = pd.DataFrame(
        [
            {"ticker": "AAA", "momentum_score": 10.0, "momentum_rank": 1, "signal_ready": True},
            {"ticker": "BBB", "momentum_score": 5.0, "momentum_rank": 2, "signal_ready": True},
            {"ticker": "CCC", "momentum_score": 1.0, "momentum_rank": 3, "signal_ready": True},
            {"ticker": "DDD", "momentum_score": 0.1, "momentum_rank": 4, "signal_ready": True},
        ]
    )

    weights = allocate_score_weighted(
        daily,
        max_position_weight=0.40,
        min_position_weight=0.15,
        top_n=4,
    )

    assert not weights.empty
    assert float(weights.max()) <= 0.40 + 1e-12
    assert float(weights.min()) >= 0.15 - 1e-12
    assert abs(float(weights.sum()) - 1.0) < 1e-12


def test_rank_bucket_forward_returns_show_top_rank_edge_on_synthetic_panel() -> None:
    signals = build_alpha_lab_signal_frame(_make_panel())
    frame, returns_matrix, _ = prepare_backtest_inputs(
        signals,
        start_date="2022-01-03",
        end_date="2023-04-01",
    )

    rows = rank_bucket_forward_returns(frame, returns_matrix, horizons=(1,))
    by_bucket = {row["rank_bucket"]: row for row in rows}

    assert by_bucket["top_1"]["mean_forward_return"] > by_bucket["residual_after_10"]["mean_forward_return"]
    assert by_bucket["top_1"]["observation_count"] > 0


def test_conviction_policy_backtest_produces_weights_returns_and_cash() -> None:
    signals = build_alpha_lab_signal_frame(_make_panel())
    frame, returns_matrix, trading_dates = prepare_backtest_inputs(
        signals,
        start_date="2022-01-03",
        end_date="2023-04-01",
    )
    spec = PolicySpec(
        policy_id="conviction_score_top10_cap40_min0",
        policy_family="conviction_score_weighted",
        max_position_weight=0.40,
        min_position_weight=0.0,
        top_n=5,
    )

    weights = _run_conviction_policy(frame, trading_dates, spec)
    returns, cash = _backtest_weight_history(weights, returns_matrix, transaction_cost_bps=10.0)

    assert not weights.empty
    assert not returns.empty
    assert not cash.empty
    assert float(weights.max(axis=1).max()) <= 0.40 + 1e-12

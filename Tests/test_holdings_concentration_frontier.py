from __future__ import annotations

import pandas as pd

from research.holdings_concentration_frontier import (
    VariantSpec,
    apply_position_constraints,
    raw_weights_for_selection,
    run_variant,
    select_tickers_for_date,
)


def test_position_cap_leaves_cash_when_cap_prevents_full_deployment() -> None:
    raw = pd.Series({"A": 1.0, "B": 1.0, "C": 1.0})

    weights = apply_position_constraints(raw, max_position_weight=0.20, min_position_weight=0.0)

    assert weights.to_dict() == {"A": 0.2, "B": 0.2, "C": 0.2}
    assert round(float(weights.sum()), 6) == 0.6


def test_score2_floor_drops_dust_positions_and_reallocates() -> None:
    selected = pd.DataFrame(
        [
            {"ticker": "A", "momentum_score": 10.0, "momentum_rank": 1.0},
            {"ticker": "B", "momentum_score": 1.0, "momentum_rank": 2.0},
            {"ticker": "C", "momentum_score": 0.5, "momentum_rank": 3.0},
        ]
    )
    raw = raw_weights_for_selection(selected, weighting_method="score2")

    weights = apply_position_constraints(raw, max_position_weight=1.00, min_position_weight=0.05)

    assert "C" not in set(weights.index)
    assert float(weights.max()) <= 1.00
    assert float(weights.min()) >= 0.05


def test_orion_rank_decay_exit_keeps_existing_name_inside_exit_cutoff() -> None:
    daily = pd.DataFrame(
        [
            {"ticker": "A", "momentum_score": 3.0, "momentum_rank": 1.0, "signal_ready": True},
            {"ticker": "B", "momentum_score": 2.0, "momentum_rank": 2.0, "signal_ready": True},
            {"ticker": "C", "momentum_score": 1.0, "momentum_rank": 3.0, "signal_ready": True},
            {"ticker": "D", "momentum_score": 0.5, "momentum_rank": 4.0, "signal_ready": True},
        ]
    )
    previous = pd.Series({"D": 0.25})

    selected = select_tickers_for_date(
        daily,
        sleeve="caerus_orion",
        top_n=2,
        previous_weights=previous,
        exit_rank_multiple=2.0,
    )

    assert selected == ["D", "A"]


def test_run_variant_uses_next_day_returns_after_decision_date() -> None:
    dates = [pd.Timestamp("2020-01-02"), pd.Timestamp("2020-01-03")]
    daily_frames = {
        dates[0]: pd.DataFrame(
            [
                {"date": dates[0], "ticker": "A", "momentum_score": 2.0, "momentum_rank": 1.0, "signal_ready": True},
                {"date": dates[0], "ticker": "B", "momentum_score": 1.0, "momentum_rank": 2.0, "signal_ready": True},
            ]
        ),
        dates[1]: pd.DataFrame(
            [
                {"date": dates[1], "ticker": "A", "momentum_score": 1.0, "momentum_rank": 2.0, "signal_ready": True},
                {"date": dates[1], "ticker": "B", "momentum_score": 2.0, "momentum_rank": 1.0, "signal_ready": True},
            ]
        ),
    }
    returns_by_date = pd.DataFrame(
        {"A": [0.10, 0.00], "B": [-0.10, 0.20]},
        index=pd.DatetimeIndex(dates),
    ).to_dict("index")

    result = run_variant(
        spec=VariantSpec(
            sleeve="caerus_polaris",
            top_n=1,
            weighting_method="equal",
            max_position_weight=1.0,
            min_position_weight=0.0,
        ),
        daily_frames=daily_frames,
        returns_by_date=returns_by_date,
        trading_dates=dates,
        trailing_vol=pd.DataFrame(index=pd.DatetimeIndex(dates), columns=["A", "B"]),
        transaction_cost_bps=0.0,
    )

    assert result["observation_count"] == 2
    assert result["total_return"] == 0.32
    assert result["average_holdings_count"] == 1.0

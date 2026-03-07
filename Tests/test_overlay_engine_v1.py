from __future__ import annotations

import pandas as pd

from research.overlay_engine.overlay_backtest import run_overlay_backtest
from research.overlay_engine.overlay_signals import derive_overlay_signal_frame


def test_no_lookahead_lagging_behavior():
    df = pd.DataFrame(
        {
            "date": ["2026-02-24", "2026-02-25", "2026-02-26"],
            "strategy_nav": [10000.0, 10100.0, 10200.0],
            "strategy_return": [0.0, 0.01, 0.01],
            "overlay_signal": ["OFF", "LOCK", "OFF"],
            "active_overlay": [False, True, False],
            "overlay_multiplier": [1.0, 0.0, 1.0],
        }
    )

    out = run_overlay_backtest(df, enforce_lag=True)

    # Day 2 return still uses day-1 multiplier (1.0), day 3 uses day-2 lock (0.0)
    assert out.loc[1, "overlay_return"] == 0.01
    assert out.loc[2, "overlay_return"] == 0.0


def test_overlay_signal_derivation():
    canonical = pd.DataFrame(
        {
            "date": ["2026-02-24", "2026-02-25", "2026-02-26"],
            "overlay_signal": ["OFF", "PARTIAL", "CRISIS"],
            "active_overlay": [False, True, True],
        }
    )

    signals = derive_overlay_signal_frame(canonical)
    assert list(signals["overlay_multiplier"]) == [1.0, 0.5, 0.0]


def test_no_lag_mode_uses_same_day_multiplier():
    df = pd.DataFrame(
        {
            "date": ["2026-02-24", "2026-02-25"],
            "strategy_nav": [10000.0, 10100.0],
            "strategy_return": [0.0, 0.01],
            "overlay_multiplier": [1.0, 0.0],
        }
    )

    out = run_overlay_backtest(df, enforce_lag=False)
    assert out.loc[1, "overlay_return"] == 0.0

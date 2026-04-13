from __future__ import annotations

import pandas as pd


def test_sleeve1_ma_state_is_not_a_hard_gate() -> None:
    from sleeves.sleeve_1.selection import select_and_weight

    asof = pd.Timestamp("2026-04-08")
    signals = pd.DataFrame(
        [
            {
                "date": asof,
                "ticker": "TREND",
                "mom_12_1": 0.18,
                "mom_sector_rel": 0.06,
                "mom_atr_norm": 5.0,
                "ma_uptrend": True,
                "close": 100.0,
                "avg_volume_20d": 1_500_000.0,
                "realized_vol": 0.25,
                "sector": "Tech",
            },
            {
                "date": asof,
                "ticker": "EARLY",
                "mom_12_1": 0.17,
                "mom_sector_rel": 0.05,
                "mom_atr_norm": 4.8,
                "ma_uptrend": False,
                "close": 90.0,
                "avg_volume_20d": 1_400_000.0,
                "realized_vol": 0.27,
                "sector": "Tech",
            },
        ]
    )

    result = select_and_weight(signals, asof_date=asof, top_n=2)
    assert not result.empty
    assert set(result["ticker"]) == {"TREND", "EARLY"}

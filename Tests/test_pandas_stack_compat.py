from __future__ import annotations

import pandas as pd

from audit.policy_backtest import _build_trades


def test_build_trades_stack_compat_runs_without_dropna_kwarg() -> None:
    prices_wide = pd.DataFrame(
        {
            "AAA": [100.0, 101.0],
            "BBB": [200.0, 199.0],
        },
        index=pd.to_datetime(["2022-01-03", "2022-01-04"]),
    )
    trades_df = pd.DataFrame(
        [
            {
                "date": "2022-01-03",
                "ticker": "AAA",
                "side": "BUY",
                "notional": 1000.0,
            }
        ]
    )

    out = _build_trades(trades_df, prices_wide=prices_wide)

    assert not out.empty
    assert {
        "date",
        "ticker",
        "sleeve",
        "side",
        "shares",
        "price",
        "notional",
        "reason",
    }.issubset(set(out.columns))

from __future__ import annotations

import pandas as pd

from research.run_polaris_pit_priced_rebaseline import _norm_ticker, attribution


def test_norm_ticker_class_suffix() -> None:
    assert _norm_ticker("BRK-B") == "BRK.B"
    assert _norm_ticker("AAPL") == "AAPL"
    assert _norm_ticker("FOO-BARR") == "FOO-BARR"


def _signals():
    # minimal signals frame within the validation window (3 dates, 2 names + SPY)
    dates = ["2014-01-02", "2014-01-03", "2014-01-06"]
    rows = []
    closes = {"AAA": [100, 110, 121], "BBB": [50, 49, 48], "SPY": [180, 181, 182]}
    scores = {"AAA": 2.0, "BBB": 1.0, "SPY": 0.5}
    for tk, cs in closes.items():
        for d, c in zip(dates, cs):
            rows.append({"date": d, "ticker": tk, "close": c,
                         "momentum_score": scores[tk], "signal_ready": True})
    return pd.DataFrame(rows)


def test_attribution_reconstructs_contrib_and_excludes_spy() -> None:
    contrib = attribution(_signals(), top_n=10)
    assert "SPY" not in contrib            # benchmark excluded from attribution
    assert "AAA" in contrib and "BBB" in contrib
    # AAA rises (positive contribution), BBB falls (negative)
    assert contrib["AAA"] > 0
    assert contrib["BBB"] < 0


def test_attribution_empty_on_empty_signals() -> None:
    assert attribution(pd.DataFrame()) == {}

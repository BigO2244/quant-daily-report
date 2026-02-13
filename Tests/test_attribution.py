import json

import pandas as pd

from reporting.attribution import compute_daily_attribution


def test_contribution_sums_to_portfolio_return(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "outputs/perf").mkdir(parents=True, exist_ok=True)
    holdings = pd.DataFrame([
        {"ticker": "AAA", "market_value": 50.0, "sleeve": "s1"},
        {"ticker": "BBB", "market_value": 50.0, "sleeve": "s2"},
    ])
    holdings.to_csv("outputs/perf/holdings_mtm_2026-01-01.csv", index=False)
    with open("outputs/perf/nav_2026-01-01.json", "w", encoding="utf-8") as f:
        json.dump({"equity": 100.0}, f)

    def fake_prev_close(tickers, asof_date):
        if asof_date == "2026-01-01":
            return pd.DataFrame([{"ticker": "AAA", "prev_close": 100.0}, {"ticker": "BBB", "prev_close": 100.0}])
        return pd.DataFrame([{"ticker": "AAA", "prev_close": 110.0}, {"ticker": "BBB", "prev_close": 100.0}])

    monkeypatch.setattr("reporting.attribution.fetch_prev_closes_yfinance", fake_prev_close)
    out = compute_daily_attribution("2026-01-02", "2026-01-01")
    total_contrib = float(out["tickers"]["contribution"].sum())
    assert abs(total_contrib - 0.05) < 1e-9

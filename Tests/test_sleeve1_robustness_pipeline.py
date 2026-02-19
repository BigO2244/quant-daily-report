from pathlib import Path

import pandas as pd

import daily_quant_report as dqr
from backtests import sleeve1_robustness as rb


def test_charlie_not_referenced_in_allocation(monkeypatch):
    monkeypatch.setattr(dqr, "run_sleeve_1", lambda: (pd.DataFrame(), pd.DataFrame()))
    monkeypatch.setattr(
        dqr,
        "run_sleeve_trend",
        lambda: (
            pd.DataFrame({"date": pd.to_datetime(["2024-01-31", "2024-02-29"]), "equity": [10000.0, 10100.0]}),
            pd.DataFrame(),
            pd.DataFrame(),
        ),
    )
    monkeypatch.setattr(dqr, "download_prices", lambda *args, **kwargs: pd.DataFrame())
    monkeypatch.setattr(dqr, "load_benchmark_prices", lambda **kwargs: pd.Series([500.0], index=pd.to_datetime(["2024-02-29"])))
    monkeypatch.setattr(dqr, "calc_alpha_stats", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        dqr,
        "run_paper_day",
        lambda **kwargs: {
            "date": "2024-02-29",
            "trading_mode": "SHADOW",
            "shadow_orders": [],
            "run_id": "r1",
            "blocked_reasons": ["market_guard_closed"],
            "market_status": "CLOSED",
            "total_equity": 10000.0,
            "cash": 10000.0,
            "num_trades": 0,
            "turnover_notional": 0.0,
        },
    )
    dqr.main([])
    # smoke: this should run with disabled sleeves without charlie dependency


def test_backtest_outputs_required_files(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    idx = pd.date_range("2004-01-01", "2024-12-31", freq="B")

    def fake_download(tickers, period="max", interval="1d"):
        rows = []
        for t in tickers:
            base = 100 + (hash(t) % 20)
            for i, d in enumerate(idx):
                rows.append({"date": d, "ticker": t, "close": base + 0.03 * i})
        return pd.DataFrame(rows)

    monkeypatch.setattr(rb, "download_prices", fake_download)
    monkeypatch.setattr(rb, "load_universe", lambda path="data/universe.csv": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOG", "META", "JPM", "XOM", "UNH", "HD"])

    rb.main(["--start", "2005-01-01", "--end", "2024-12-31", "--top_n", "10"])

    out = Path("outputs/backtests/sleeve1_robustness")
    required = [
        "summary.json",
        "metrics.csv",
        "regimes.csv",
        "report.md",
        "strategy_nav_monthly.csv",
        "spy_nav_monthly.csv",
        "rolling_36m_sharpe.csv",
        "rolling_12m_drawdown.csv",
    ]
    for f in required:
        assert (out / f).exists(), f"missing {f}"


def test_trend_gate_moves_to_cash_when_spy_below_200d(monkeypatch):
    idx = pd.date_range("2010-01-01", "2012-12-31", freq="B")

    def fake_download(tickers, period="max", interval="1d"):
        rows = []
        for t in tickers:
            for i, d in enumerate(idx):
                px = 200 - 0.2 * i if t == "SPY" else 100 + 0.01 * i
                rows.append({"date": d, "ticker": t, "close": px})
        return pd.DataFrame(rows)

    monkeypatch.setattr(rb, "download_prices", fake_download)
    monkeypatch.setattr(rb, "load_universe", lambda path="data/universe.csv": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOG", "META", "JPM", "XOM", "UNH", "HD"])

    res = rb.run_backtest(rb.RobustnessConfig(start="2010-01-01", end="2012-12-31"))
    ts = res["timeseries"]
    assert ts["exposure"].iloc[-1] == 0.0


def test_momentum_12_1_excludes_most_recent_month():
    monthly = pd.Series([100, 110, 121, 133.1, 146.41, 161.051, 177.1561, 194.8717, 214.3589, 235.7948, 259.3743, 285.3117, 313.8429])
    mom = rb.compute_12_1_momentum(monthly)
    expected = (monthly.iloc[-2] / monthly.iloc[-13]) - 1
    assert abs(mom.iloc[-1] - expected) < 1e-12

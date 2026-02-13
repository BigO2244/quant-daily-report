import pandas as pd

from paper.mark_to_market import update_nav_timeseries


def test_append_then_replace_row(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ledger = pd.DataFrame([{"trade_date": "2026-01-02", "notional": 100.0}])
    nav = {"equity": 1000.0, "cash": 100.0, "gross_exposure": 0.9, "net_exposure": 0.9}
    update_nav_timeseries("2026-01-02", nav, ledger)
    nav2 = {"equity": 1100.0, "cash": 100.0, "gross_exposure": 0.9, "net_exposure": 0.9}
    update_nav_timeseries("2026-01-02", nav2, ledger)
    ts = pd.read_csv("outputs/perf/nav_timeseries.csv")
    assert len(ts) == 1
    assert float(ts.iloc[0]["equity"]) == 1100.0


def test_return_1d_correctness(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ledger = pd.DataFrame([{"trade_date": "2026-01-02", "notional": 50.0}, {"trade_date": "2026-01-03", "notional": 50.0}])
    update_nav_timeseries("2026-01-02", {"equity": 100.0, "cash": 10.0, "gross_exposure": 0.9, "net_exposure": 0.9}, ledger)
    update_nav_timeseries("2026-01-03", {"equity": 110.0, "cash": 10.0, "gross_exposure": 0.9, "net_exposure": 0.9}, ledger)
    ts = pd.read_csv("outputs/perf/nav_timeseries.csv")
    assert abs(float(ts.iloc[1]["return_1d"]) - 0.1) < 1e-9

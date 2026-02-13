import pandas as pd

from paper.positions import rebuild_positions_from_ledger


def _ledger(rows):
    return pd.DataFrame(rows)


def test_buy_buy_avg_cost_correctness(monkeypatch):
    monkeypatch.setattr("paper.positions._resolve_starting_cash", lambda default_cash=100000.0: 1000.0)
    led = _ledger([
        {"trade_date": "2026-01-01", "timestamp_et": "1", "order_id": "1", "ticker": "AAPL", "side": "BUY", "quantity": 1.0, "fill_price": 100.0, "fees": 0.0, "sleeve": "s"},
        {"trade_date": "2026-01-02", "timestamp_et": "2", "order_id": "2", "ticker": "AAPL", "side": "BUY", "quantity": 1.0, "fill_price": 200.0, "fees": 0.0, "sleeve": "s"},
    ])
    out = rebuild_positions_from_ledger(led, "2026-01-02")
    pos = out["positions"].iloc[0]
    assert pos["shares"] == 2.0
    assert pos["avg_cost"] == 150.0


def test_sell_realized_pnl_correctness(monkeypatch):
    monkeypatch.setattr("paper.positions._resolve_starting_cash", lambda default_cash=100000.0: 1000.0)
    led = _ledger([
        {"trade_date": "2026-01-01", "timestamp_et": "1", "order_id": "1", "ticker": "AAPL", "side": "BUY", "quantity": 2.0, "fill_price": 100.0, "fees": 0.0, "sleeve": "s"},
        {"trade_date": "2026-01-02", "timestamp_et": "2", "order_id": "2", "ticker": "AAPL", "side": "SELL", "quantity": 1.0, "fill_price": 130.0, "fees": 0.0, "sleeve": "s"},
    ])
    out = rebuild_positions_from_ledger(led, "2026-01-02")
    pos = out["positions"].iloc[0]
    assert pos["realized_pnl"] == 30.0


def test_sell_to_zero_resets_avg_cost(monkeypatch):
    monkeypatch.setattr("paper.positions._resolve_starting_cash", lambda default_cash=100000.0: 1000.0)
    led = _ledger([
        {"trade_date": "2026-01-01", "timestamp_et": "1", "order_id": "1", "ticker": "AAPL", "side": "BUY", "quantity": 1.0, "fill_price": 100.0, "fees": 0.0, "sleeve": "s"},
        {"trade_date": "2026-01-02", "timestamp_et": "2", "order_id": "2", "ticker": "AAPL", "side": "SELL", "quantity": 1.0, "fill_price": 100.0, "fees": 0.0, "sleeve": "s"},
    ])
    out = rebuild_positions_from_ledger(led, "2026-01-02")
    pos = out["positions"].iloc[0]
    assert pos["shares"] == 0.0
    assert pos["avg_cost"] == 0.0

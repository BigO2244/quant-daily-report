import json

import pandas as pd

from daily_quant_report import write_integrity_artifact
from paper.ledger2 import append_rows, ensure_dirs
from paper.nav2 import update_nav


def test_ledger2_idempotent_append(tmp_path):
    ledger_path = tmp_path / "outputs" / "ledger" / "trades.csv"
    ensure_dirs(str(ledger_path))
    row = {
        "timestamp_et": "2026-01-01T00:00:00-05:00",
        "run_id": "r1",
        "source": "SHADOW",
        "trade_date": "2026-01-01",
        "asof_date": "2025-12-31",
        "order_id": "oid-1",
        "ticker": "AAPL",
        "sleeve": "main",
        "side": "BUY",
        "quantity": 1,
        "fill_price": 100,
        "notional": 100,
        "fees": 0,
        "reason": "test",
        "execution_status": "READY",
    }
    appended_first, skipped_first = append_rows(str(ledger_path), [row])
    appended_second, skipped_second = append_rows(str(ledger_path), [row])

    assert appended_first == 1
    assert skipped_first == 0
    assert appended_second == 0
    assert skipped_second > 0


def test_nav2_upsert_no_duplicate_dates(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ledger_path = tmp_path / "outputs" / "ledger" / "trades.csv"
    ensure_dirs(str(ledger_path))
    append_rows(
        str(ledger_path),
        [
            {
                "timestamp_et": "2026-01-02T10:00:00-05:00",
                "run_id": "r1",
                "source": "SHADOW",
                "trade_date": "2026-01-02",
                "asof_date": "2026-01-01",
                "order_id": "oid-1",
                "ticker": "AAPL",
                "sleeve": "main",
                "side": "BUY",
                "quantity": 10,
                "fill_price": 100,
                "notional": 1000,
                "fees": 0,
                "reason": "seed",
                "execution_status": "READY",
            }
        ],
    )

    prices = {"AAPL": 110.0}

    def get_price_fn(ticker: str, asof_date: str):
        _ = asof_date
        return prices.get(ticker)

    update_nav("2026-01-02", "2026-01-02", get_price_fn, "SHADOW", "run-1", ledger_path=str(ledger_path))
    update_nav("2026-01-02", "2026-01-02", get_price_fn, "SHADOW", "run-2", ledger_path=str(ledger_path))

    ts = pd.read_csv("outputs/perf/nav_timeseries.csv")
    assert (ts["date"] == "2026-01-02").sum() == 1


def test_integrity_artifact_written(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    payload = {"trade_date": "2026-01-02", "asof_date": "2026-01-01", "mode": "SHADOW"}
    path = write_integrity_artifact("2026-01-01", payload)
    assert (tmp_path / path).exists()
    written = json.loads((tmp_path / path).read_text(encoding="utf-8"))
    assert written["mode"] == "SHADOW"

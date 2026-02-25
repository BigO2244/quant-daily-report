import json

import pandas as pd

from daily_quant_report import _write_execution_email_payload
from paper.ledger2 import append_ledger2_rows, ensure_ledger2_exists
from paper.paper_broker import reset_orders_sent_ledger_for_date


def test_preserve_non_empty_execution_payload(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_date = "2026-02-02"
    existing = {"execution_status": "READY", "trades": [{"ticker": "AAPL", "shares": 1}]}
    halted = {"execution_status": "HALTED", "halt_reason": "MARKET CLOSED", "trades": []}

    original_path, _, _ = _write_execution_email_payload(existing, run_date)
    before = json.loads((tmp_path / original_path).read_text(encoding="utf-8"))

    written_path, preserved, preserved_path = _write_execution_email_payload(halted, run_date)

    after = json.loads((tmp_path / original_path).read_text(encoding="utf-8"))
    assert before == after
    assert preserved is True
    assert preserved_path.endswith(f"{run_date}.json")
    assert ".halted." in written_path
    assert json.loads((tmp_path / written_path).read_text(encoding="utf-8"))["execution_status"] == "HALTED"


def test_shadow_rerun_reset_behavior(tmp_path):
    sent_path = tmp_path / "orders_sent.csv"
    pd.DataFrame(
        [
            {"date": "2026-02-02", "run_id": "r1", "order_id": "o1", "ticker": "AAPL", "side": "BUY"},
            {"date": "2026-02-01", "run_id": "r0", "order_id": "o0", "ticker": "MSFT", "side": "SELL"},
        ]
    ).to_csv(sent_path, index=False)

    removed = reset_orders_sent_ledger_for_date(str(sent_path), "2026-02-02")
    assert removed == 1
    kept = pd.read_csv(sent_path)
    assert len(kept) == 1
    assert kept.iloc[0]["date"] == "2026-02-01"


def test_ledger2_idempotent_append(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ledger_path = tmp_path / "trades.csv"
    ensure_ledger2_exists(str(ledger_path))
    rows = [
        {
            "timestamp_et": "2026-01-01T00:00:00-05:00",
            "run_id": "r1",
            "source": "SHADOW",
            "trade_date": "2026-01-01",
            "asof_date": "2025-12-31",
            "order_id": "oid-1",
            "ticker": "AAPL",
            "sleeve": "core",
            "side": "BUY",
            "quantity": 1,
            "fill_price": 100,
            "notional": 100,
            "fees": 0,
            "reason": "test",
            "execution_status": "READY",
        }
    ]
    assert append_ledger2_rows(rows, str(ledger_path)) == 1
    assert append_ledger2_rows(rows, str(ledger_path)) == 0
    assert len(pd.read_csv(ledger_path)) == 1

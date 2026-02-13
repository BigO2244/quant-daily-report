import json

from paper.ledger import append_ledger_rows, ensure_ledger_exists, ledger_rows_from_execution_payload, load_ledger


def test_create_ledger_headers(tmp_path):
    p = tmp_path / "trades.csv"
    ensure_ledger_exists(str(p))
    df = load_ledger(str(p))
    assert "trade_date" in df.columns
    assert "order_id" in df.columns


def test_append_idempotent(tmp_path):
    p = tmp_path / "trades.csv"
    ensure_ledger_exists(str(p))
    rows = [{
        "timestamp_et": "2026-01-01T00:00:00-05:00", "run_id": "r1", "source": "SHADOW", "trade_date": "2026-01-01", "asof_date": "2025-12-31", "order_id": "o1", "ticker": "AAPL", "sleeve": "s", "side": "BUY", "quantity": 1.0, "fill_price": 100.0, "notional": 100.0, "fees": 0.0, "reason": "x", "signal_hash": "h", "status": "FILLED_ESTIMATE"
    }]
    assert append_ledger_rows(rows, str(p)) == 1
    assert append_ledger_rows(rows, str(p)) == 0
    assert len(load_ledger(str(p))) == 1


def test_uniqueness_key_enforced(tmp_path):
    payload = {"trades": [{"ticker": "MSFT", "side": "BUY", "shares": 2, "entry_price": 10.0, "order_id": "OID1"}]}
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    rows = ledger_rows_from_execution_payload(str(payload_path), "2026-01-02", "2026-01-01", "SHADOW", "run", "sig")
    p = tmp_path / "trades.csv"
    ensure_ledger_exists(str(p))
    append_ledger_rows(rows, str(p))
    append_ledger_rows(rows, str(p))
    assert len(load_ledger(str(p))) == 1

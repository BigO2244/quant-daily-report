import json
from pathlib import Path

import pytest

import reconciliation


def test_pretrade_fails_with_bootstrap_hint_when_model_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RECON_ENABLE", "1")
    monkeypatch.setenv("RECON_STRICT_PRE", "1")

    monkeypatch.setattr(
        reconciliation,
        "_load_broker_snapshot",
        lambda mode: {
            "source": "test",
            "positions": {"AAPL": 10.0},
            "position_count": 1,
            "cash": 1000.0,
            "equity": 2000.0,
        },
    )

    missing_ledger = tmp_path / "paper_state" / "ledger.csv"
    sent_ledger = tmp_path / "shadow_orders" / "orders_sent.csv"

    with pytest.raises(SystemExit) as exc:
        reconciliation.pre_trade_reconcile_or_exit(
            run_date="2026-03-02",
            trading_mode="alpaca",
            ledger_path=str(missing_ledger),
            sent_ledger_path=str(sent_ledger),
        )
    assert int(exc.value.code) == 2

    report_path = Path("outputs/broker/recon_pretrade_2026-03-02.json")
    assert report_path.exists()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    errors = list(((payload.get("diffs") or {}).get("errors") or []))
    joined = "\n".join(str(e) for e in errors)
    assert "bootstrap-model-ledger-from-broker" in joined


def test_bootstrap_writes_canonical_snapshot_without_orders(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        reconciliation,
        "_load_broker_snapshot",
        lambda mode: {
            "source": "test",
            "positions": {"MSFT": 3.0, "AAPL": 1.0},
            "position_count": 2,
            "cash": 2500.0,
            "equity": 5000.0,
        },
    )

    ok = reconciliation.bootstrap_model_ledger_from_broker(
        trading_mode="alpaca",
        ledger_path=str(tmp_path / "paper_state" / "ledger.csv"),
        sent_ledger_path=str(tmp_path / "shadow_orders" / "orders_sent.csv"),
        run_date="2026-03-02",
    )
    assert ok is True

    canonical = Path("outputs/paper_state/canonical_positions.json")
    assert canonical.exists()
    payload = json.loads(canonical.read_text(encoding="utf-8"))
    assert payload["positions"] == {"MSFT": 3.0, "AAPL": 1.0}
    assert payload["reason"] == "bootstrap_from_broker"


def test_ensure_sent_ledger_exists_creates_headers_idempotently(tmp_path):
    sent_path = tmp_path / "shadow_orders" / "orders_sent.csv"

    reconciliation.ensure_sent_ledger_exists(str(sent_path))
    reconciliation.ensure_sent_ledger_exists(str(sent_path))

    assert sent_path.exists()
    content = sent_path.read_text(encoding="utf-8").splitlines()
    assert len(content) == 1
    assert content[0] == "date,run_id,order_id,ticker,side"

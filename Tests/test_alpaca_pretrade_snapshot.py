import json
from pathlib import Path

import daily_quant_report as dqr
from brokers import alpaca_snapshot as snapshot_mod
from brokers.alpaca_snapshot import (
    fetch_pretrade_snapshot,
    write_pretrade_snapshot_artifacts,
)


class _FakeBroker:
    def __init__(self):
        self.base_url = "https://paper-api.alpaca.markets"
        self.paper = True

    def get_account(self):
        return {
            "id": "acct-123",
            "status": "ACTIVE",
            "cash": "1000.00",
            "equity": "1250.00",
            "buying_power": "2000.00",
            "portfolio_value": "1250.00",
        }

    def get_positions(self):
        return [
            {
                "symbol": "AAPL",
                "qty": "5",
                "market_value": "950.00",
                "current_price": "190.00",
                "side": "long",
            }
        ]


def _raise_init_error(cls):
    raise RuntimeError("auth failed")


def _make_run_context(run_root: Path) -> dqr.RunContext:
    return dqr.RunContext(
        run_id="run-123",
        run_root=run_root,
        allow_overwrite=True,
        created_at="2026-03-12T13:35:00Z",
        git_sha=None,
        mode="alpaca",
        trading_mode="alpaca",
        report_date_env="2026-03-12",
        report_date="2026-03-12",
        paper_trading=True,
    )


def test_fetch_pretrade_snapshot_success(monkeypatch):
    monkeypatch.setattr(
        snapshot_mod.AlpacaBroker,
        "from_env",
        classmethod(lambda cls: _FakeBroker()),
    )

    snapshot = fetch_pretrade_snapshot()

    assert snapshot["ok"] is True
    assert snapshot["account"]["status"] == "ACTIVE"
    assert snapshot["positions"][0]["symbol"] == "AAPL"
    assert snapshot["account_error"] is None
    assert snapshot["positions_error"] is None


def test_fetch_pretrade_snapshot_records_init_failure(monkeypatch):
    monkeypatch.setattr(
        snapshot_mod.AlpacaBroker,
        "from_env",
        classmethod(_raise_init_error),
    )

    snapshot = fetch_pretrade_snapshot()

    assert snapshot["ok"] is False
    assert snapshot["account"] == {}
    assert snapshot["positions"] == []
    assert "auth failed" in str(snapshot["account_error"])
    assert "auth failed" in str(snapshot["positions_error"])


def test_write_pretrade_snapshot_artifacts_writes_both_files(tmp_path: Path):
    run_root = tmp_path / "outputs" / "runs" / "run-123"
    snapshot = {
        "broker": "alpaca",
        "captured_at": "2026-03-12T13:35:00Z",
        "base_url": "https://paper-api.alpaca.markets",
        "paper": True,
        "account": {"status": "ACTIVE", "cash": "1000.00"},
        "positions": [{"symbol": "AAPL", "qty": "5"}],
        "account_error": None,
        "positions_error": None,
        "ok": True,
    }

    account_path, positions_path = write_pretrade_snapshot_artifacts(
        run_root=run_root,
        run_id="run-123",
        trade_date="2026-03-12",
        snapshot=snapshot,
    )

    assert account_path == run_root / "broker" / "pretrade_account_snapshot.json"
    assert positions_path == run_root / "broker" / "pretrade_positions.json"
    account_payload = json.loads(account_path.read_text(encoding="utf-8"))
    positions_payload = json.loads(positions_path.read_text(encoding="utf-8"))
    assert account_payload["trade_date"] == "2026-03-12"
    assert account_payload["account"]["status"] == "ACTIVE"
    assert positions_payload["positions_count"] == 1
    assert positions_payload["positions"][0]["symbol"] == "AAPL"


def test_daily_quant_report_capture_writes_run_root_broker_artifacts(tmp_path: Path, monkeypatch):
    run_root = tmp_path / "outputs" / "runs" / "run-123"
    run_root.mkdir(parents=True, exist_ok=True)
    ctx = _make_run_context(run_root)
    snapshot = {
        "broker": "alpaca",
        "captured_at": "2026-03-12T13:35:00Z",
        "base_url": "https://paper-api.alpaca.markets",
        "paper": True,
        "account": {"status": "ACTIVE"},
        "positions": [{"symbol": "AAPL", "qty": "5"}],
        "account_error": None,
        "positions_error": None,
        "ok": True,
    }

    monkeypatch.setattr(dqr, "_RUN_CONTEXT", ctx)
    monkeypatch.setattr(dqr, "fetch_pretrade_snapshot", lambda: snapshot)

    result = dqr._capture_pretrade_broker_snapshot(
        trade_date="2026-03-12",
        alpaca_requested=True,
    )

    assert result is not None
    assert (run_root / "broker" / "pretrade_account_snapshot.json").exists()
    assert (run_root / "broker" / "pretrade_positions.json").exists()

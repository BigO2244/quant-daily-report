import json
from pathlib import Path

import daily_trade_execution_email as mod


def test_writes_artifact_for_halted_and_no_trades(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    class Cfg:
        trading_mode = "shadow"

    monkeypatch.setattr(mod, "load_config", lambda *_: Cfg())
    sent = {"count": 0}
    monkeypatch.setattr(mod, "send_execution_email", lambda **_: sent.__setitem__("count", sent["count"] + 1))

    payload_dir = Path("outputs") / "execution_email"
    payload_dir.mkdir(parents=True, exist_ok=True)

    halted_payload = {
        "trade_date": "2026-02-05",
        "mode": "SHADOW",
        "execution_status": "HALTED",
        "halt_reason": "MARKET CLOSED",
        "trades": [],
    }
    (payload_dir / "2026-02-05.json").write_text(json.dumps(halted_payload), encoding="utf-8")
    monkeypatch.setenv("REPORT_DATE", "2026-02-05")
    mod.main([])
    halted_artifact = Path("outputs") / "daily" / "trade_execution_2026-02-05.txt"
    assert halted_artifact.exists()
    assert "Execution Status: HALTED — MARKET CLOSED" in halted_artifact.read_text(encoding="utf-8")

    no_trades_payload = {
        "trade_date": "2026-02-06",
        "mode": "SHADOW",
        "execution_status": "READY",
        "trades": [],
    }
    (payload_dir / "2026-02-06.json").write_text(json.dumps(no_trades_payload), encoding="utf-8")
    monkeypatch.setenv("REPORT_DATE", "2026-02-06")
    mod.main([])
    no_trades_artifact = Path("outputs") / "daily" / "trade_execution_2026-02-06.txt"
    assert no_trades_artifact.exists()
    assert "NO TRADES TODAY" in no_trades_artifact.read_text(encoding="utf-8")

    trade_payload = {
        "trade_date": "2026-02-07",
        "mode": "SHADOW",
        "execution_status": "READY",
        "trades": [{"ticker": "AAPL", "side": "BUY", "shares": 2, "entry_price": 100.0, "stop_loss": 95.0, "take_profit": 110.0, "notional": 200.0}],
    }
    (payload_dir / "2026-02-07.json").write_text(json.dumps(trade_payload), encoding="utf-8")
    monkeypatch.setenv("REPORT_DATE", "2026-02-07")
    mod.main([])
    trade_artifact = Path("outputs") / "daily" / "trade_execution_2026-02-07.txt"
    assert trade_artifact.exists()
    trade_text = trade_artifact.read_text(encoding="utf-8")
    assert "AAPL | BUY | 2" in trade_text
    assert trade_text.endswith("\n")

    assert sent["count"] == 3


def test_dry_run_writes_artifact_and_skips_send_without_smtp(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    class Cfg:
        trading_mode = "shadow"

    monkeypatch.setattr(mod, "load_config", lambda *_: Cfg())
    called = {"count": 0}
    monkeypatch.setattr(mod, "send_execution_email", lambda **_: called.__setitem__("count", called["count"] + 1))

    payload_dir = Path("outputs") / "execution_email"
    payload_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "trade_date": "2026-02-08",
        "mode": "SHADOW",
        "execution_status": "READY",
        "trades": [],
    }
    (payload_dir / "2026-02-08.json").write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setenv("REPORT_DATE", "2026-02-08")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_PORT", raising=False)
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASS", raising=False)
    monkeypatch.delenv("REPORT_FROM_EMAIL", raising=False)
    monkeypatch.delenv("REPORT_TO_EMAIL", raising=False)

    mod.main(["--dry-run"])

    artifact = Path("outputs") / "daily" / "trade_execution_2026-02-08.txt"
    assert artifact.exists()
    text = artifact.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert not text.rstrip("\n").endswith("%")
    assert called["count"] == 0


def test_reset_ledger_date_flag_invokes_reset(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    class Cfg:
        trading_mode = "shadow"

    monkeypatch.setattr(mod, "load_config", lambda *_: Cfg())
    monkeypatch.setattr(mod, "send_execution_email", lambda **_: None)

    called = {"args": None}

    def _fake_reset(path, date):
        called["args"] = (path, date)
        return 2

    monkeypatch.setattr(mod, "reset_orders_sent_ledger_for_date", _fake_reset)

    payload_dir = Path("outputs") / "execution_email"
    payload_dir.mkdir(parents=True, exist_ok=True)
    payload = {"trade_date": "2026-02-09", "mode": "SHADOW", "execution_status": "READY", "trades": []}
    (payload_dir / "2026-02-09.json").write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setenv("REPORT_DATE", "2026-02-09")
    mod.main(["--dry-run", "--reset-ledger-date", "2026-02-09"])

    assert called["args"] == ("outputs/shadow_orders/orders_sent.csv", "2026-02-09")

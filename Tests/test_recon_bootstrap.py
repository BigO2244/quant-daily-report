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

    preflight = Path("outputs/logs/preflight_failure.json")
    assert preflight.exists()
    preflight_payload = json.loads(preflight.read_text(encoding="utf-8"))
    assert preflight_payload["halt_stage"] == "pretrade_reconciliation"
    assert preflight_payload["block_reason"] == "stale_state"
    assert "recommended_action" in preflight_payload
    assert "source_state_notes" in preflight_payload
    assert any("Preferred canonical snapshot missing" in note for note in preflight_payload["source_state_notes"])


def test_missing_preferred_canonical_recovers_from_legacy_snapshot(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RECON_ENABLE", "1")
    monkeypatch.setenv("RECON_STRICT_PRE", "1")

    legacy = tmp_path / "canonical-model-snapshot" / "canonical_positions.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(
        json.dumps(
            {
                "positions": {"AAPL": 10.0},
                "position_count": 1,
                "cash": 1000.0,
                "equity": 2000.0,
                "reason": "legacy_cache_restore",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

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

    reconciliation.pre_trade_reconcile_or_exit(
        run_date="2026-03-08",
        trading_mode="alpaca",
        ledger_path=str(tmp_path / "paper_state" / "ledger.csv"),
        sent_ledger_path=str(tmp_path / "shadow_orders" / "orders_sent.csv"),
    )

    preferred = Path("outputs/paper_state/canonical_positions.json")
    assert preferred.exists()
    preferred_payload = json.loads(preferred.read_text(encoding="utf-8"))
    assert preferred_payload["positions"] == {"AAPL": 10.0}
    assert str(preferred_payload.get("reason") or "").startswith("recovered_from_legacy_snapshot")

    pretrade_payload = json.loads(
        Path("outputs/broker/recon_pretrade_2026-03-08.json").read_text(encoding="utf-8")
    )
    assert pretrade_payload["verdict"] == "PASS"


def test_pretrade_auto_bootstrap_recovers_missing_canonical(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RECON_ENABLE", "1")
    monkeypatch.setenv("RECON_STRICT_PRE", "1")
    monkeypatch.setenv("AUTO_BOOTSTRAP_ON_RECON_FAIL", "1")

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

    # Should recover by bootstrapping canonical snapshot and then passing pretrade reconcile.
    reconciliation.pre_trade_reconcile_or_exit(
        run_date="2026-03-03",
        trading_mode="alpaca",
        ledger_path=str(tmp_path / "paper_state" / "ledger.csv"),
        sent_ledger_path=str(tmp_path / "shadow_orders" / "orders_sent.csv"),
    )

    canonical = Path("outputs/paper_state/canonical_positions.json")
    assert canonical.exists()

    report = Path("outputs/broker/recon_pretrade_2026-03-03.json")
    assert report.exists()
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["verdict"] in {"PASS", "WARN"}

    assert not Path("outputs/broker/recon_execution_blocked_2026-03-03.json").exists()
    assert not Path("outputs/logs/preflight_failure.json").exists()


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

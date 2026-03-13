import json
from pathlib import Path

import reconciliation


def _write_canonical(
    *,
    positions: dict[str, float],
    cash: float = 1000.0,
    equity: float = 10000.0,
    timestamp_utc: str = "2026-03-10T09:30:00+00:00",
    reason: str = "bootstrap_from_broker",
) -> None:
    path = Path("outputs/paper_state/canonical_positions.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "positions": positions,
                "position_count": len(positions),
                "cash": cash,
                "equity": equity,
                "timestamp_utc": timestamp_utc,
                "reason": reason,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _read_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def test_classify_drift_symbol_set_mismatch_self_heals(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    broker_positions = {
        "AAPL": 1.0,
        "ABBV": 1.0,
        "ABNB": 1.0,
        "FDX": 1.0,
        "GLW": 1.0,
        "HWM": 1.0,
        "KMI": 1.0,
        "MPC": 1.0,
        "ROST": 1.0,
        "VZ": 1.0,
    }
    _write_canonical(
        positions={
            "AAPL": 1.0,
            "ABBV": 1.0,
            "ABNB": 1.0,
            "EOG": 1.0,
            "FDX": 1.0,
            "GLW": 1.0,
            "KMI": 1.0,
            "ROST": 1.0,
            "VZ": 1.0,
        }
    )
    monkeypatch.setattr(
        reconciliation,
        "_load_broker_snapshot_v2",
        lambda mode: {
            "source": "test",
            "positions": broker_positions,
            "position_count": len(broker_positions),
            "cash": 1000.0,
            "equity": 10000.0,
            "account_status": "ACTIVE",
            "raw_account_fields_found": ["cash", "equity", "status"],
            "account_error": None,
            "positions_error": None,
            "errors": [],
        },
    )

    payload = reconciliation.pre_trade_reconcile_and_classify(
        run_date="2026-03-10",
        trading_mode="alpaca",
        ledger_path=str(tmp_path / "paper_state" / "ledger.csv"),
        sent_ledger_path=str(tmp_path / "shadow_orders" / "orders_sent.csv"),
    )

    assert payload["reconciliation_decision"] == "SELF_HEAL"
    assert payload["missing_in_model"] == ["HWM", "MPC"]
    assert payload["missing_in_broker"] == ["EOG"]
    refreshed = _read_json("outputs/paper_state/canonical_positions.json")
    assert refreshed["positions"] == broker_positions

    report = _read_json("outputs/broker/recon_pretrade_2026-03-10.json")
    assert report["reconciliation_decision"] == "SELF_HEAL"
    assert "symbol_set_drift" in report["self_heals"]


def test_pretrade_v2_missing_canonical_self_heals_and_writes_report(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    broker_positions = {"AAPL": 10.0}
    monkeypatch.setattr(
        reconciliation,
        "_load_broker_snapshot_v2",
        lambda mode: {
            "source": "test",
            "positions": broker_positions,
            "position_count": 1,
            "cash": 500.0,
            "equity": 2500.0,
            "account_status": "ACTIVE",
            "raw_account_fields_found": ["cash", "equity", "status"],
            "account_error": None,
            "positions_error": None,
            "errors": [],
        },
    )

    payload = reconciliation.pre_trade_reconcile_and_classify(
        run_date="2026-03-10",
        trading_mode="alpaca",
        ledger_path=str(tmp_path / "paper_state" / "ledger.csv"),
        sent_ledger_path=str(tmp_path / "shadow_orders" / "orders_sent.csv"),
    )

    assert payload["reconciliation_decision"] == "SELF_HEAL"
    assert Path("outputs/broker/recon_pretrade_2026-03-10.json").exists()
    canonical = _read_json("outputs/paper_state/canonical_positions.json")
    assert canonical["positions"] == broker_positions


def test_pretrade_v2_stale_canonical_self_heals(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_canonical(
        positions={"AAPL": 10.0},
        timestamp_utc="2026-03-09T20:00:00+00:00",
    )
    monkeypatch.setattr(
        reconciliation,
        "_load_broker_snapshot_v2",
        lambda mode: {
            "source": "test",
            "positions": {"AAPL": 10.0},
            "position_count": 1,
            "cash": 1000.0,
            "equity": 10000.0,
            "account_status": "ACTIVE",
            "raw_account_fields_found": ["cash", "equity", "status"],
            "account_error": None,
            "positions_error": None,
            "errors": [],
        },
    )

    payload = reconciliation.pre_trade_reconcile_and_classify(
        run_date="2026-03-10",
        trading_mode="alpaca",
        ledger_path=str(tmp_path / "paper_state" / "ledger.csv"),
        sent_ledger_path=str(tmp_path / "shadow_orders" / "orders_sent.csv"),
    )

    assert payload["reconciliation_decision"] == "SELF_HEAL"
    assert "canonical_snapshot_stale" in payload["self_heals"]
    canonical = _read_json("outputs/paper_state/canonical_positions.json")
    assert canonical["reason"] == "pretrade_self_heal_from_broker"


def test_pretrade_v2_benign_cash_drift_warns(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_canonical(positions={"AAPL": 10.0}, cash=1000.0, equity=10000.0)
    monkeypatch.setattr(
        reconciliation,
        "_load_broker_snapshot_v2",
        lambda mode: {
            "source": "test",
            "positions": {"AAPL": 10.0},
            "position_count": 1,
            "cash": 1050.0,
            "equity": 10000.0,
            "account_status": "ACTIVE",
            "raw_account_fields_found": ["cash", "equity", "status"],
            "account_error": None,
            "positions_error": None,
            "errors": [],
        },
    )

    payload = reconciliation.pre_trade_reconcile_and_classify(
        run_date="2026-03-10",
        trading_mode="alpaca",
        ledger_path=str(tmp_path / "paper_state" / "ledger.csv"),
        sent_ledger_path=str(tmp_path / "shadow_orders" / "orders_sent.csv"),
    )

    assert payload["reconciliation_decision"] == "WARN"
    assert "cash_drift_lt_1pct_equity" in payload["warnings"]
    assert Path("outputs/broker/recon_pretrade_2026-03-10.json").exists()


def test_classify_drift_broker_auth_failure_blocks(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_canonical(positions={"AAPL": 10.0})
    monkeypatch.setattr(
        reconciliation,
        "_load_broker_snapshot_v2",
        lambda mode: {
            "source": "test",
            "positions": {},
            "position_count": 0,
            "cash": None,
            "equity": None,
            "account_status": None,
            "raw_account_fields_found": [],
            "account_error": "RuntimeError: auth failed",
            "positions_error": "RuntimeError: auth failed",
            "errors": ["broker_auth_failure"],
        },
    )

    payload = reconciliation.pre_trade_reconcile_and_classify(
        run_date="2026-03-10",
        trading_mode="alpaca",
        ledger_path=str(tmp_path / "paper_state" / "ledger.csv"),
        sent_ledger_path=str(tmp_path / "shadow_orders" / "orders_sent.csv"),
    )

    assert payload["reconciliation_decision"] == "BLOCK"
    assert "broker_auth_failure" in payload["hard_blocks"]
    assert Path("outputs/broker/recon_pretrade_2026-03-10.json").exists()
    assert Path("outputs/broker/recon_execution_blocked_2026-03-10.json").exists()
    assert Path("outputs/logs/preflight_failure.json").exists()

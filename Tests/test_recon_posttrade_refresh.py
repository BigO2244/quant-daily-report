import json
from pathlib import Path

import pytest

import reconciliation


def test_refresh_canonical_snapshot_from_broker_updates_positions(tmp_path, monkeypatch):
    """Test that posttrade refresh updates canonical snapshot from broker positions."""
    monkeypatch.chdir(tmp_path)
    
    # Mock broker snapshot with updated positions
    monkeypatch.setattr(
        reconciliation,
        "_load_broker_snapshot",
        lambda mode: {
            "source": "test",
            "positions": {"AAPL": 15.0, "MSFT": 8.0, "CME": 2.0},
            "position_count": 3,
            "cash": 5000.0,
            "equity": 25000.0,
        },
    )
    
    # Call refresh function
    ok = reconciliation.refresh_canonical_snapshot_from_broker(
        trading_mode="alpaca",
        run_date="2026-03-02",
    )
    assert ok is True
    
    # Verify canonical snapshot was written
    canonical = Path("outputs/paper_state/canonical_positions.json")
    assert canonical.exists()
    payload = json.loads(canonical.read_text(encoding="utf-8"))
    
    # Verify positions were updated
    assert payload["positions"] == {"AAPL": 15.0, "MSFT": 8.0, "CME": 2.0}
    assert payload["position_count"] == 3
    assert payload["cash"] == 5000.0
    assert payload["equity"] == 25000.0
    assert payload["reason"] == "posttrade_refresh_from_broker"


def test_refresh_canonical_snapshot_replaces_old_snapshot(tmp_path, monkeypatch):
    """Test that posttrade refresh replaces the old canonical snapshot."""
    monkeypatch.chdir(tmp_path)
    
    # Create old snapshot with missing positions
    old_bootstrap_canonical = Path("outputs/paper_state/canonical_positions.json")
    old_bootstrap_canonical.parent.mkdir(parents=True, exist_ok=True)
    old_payload = {
        "positions": {"AAPL": 10.0},
        "position_count": 1,
        "cash": 1000.0,
        "equity": 20000.0,
        "reason": "bootstrap_from_broker",
    }
    old_bootstrap_canonical.write_text(json.dumps(old_payload, indent=2) + "\n", encoding="utf-8")
    
    # Mock broker snapshot with updated positions
    monkeypatch.setattr(
        reconciliation,
        "_load_broker_snapshot",
        lambda mode: {
            "source": "test",
            "positions": {"AAPL": 10.0, "MSFT": 8.0, "CME": 2.0, "VZ": 5.0},
            "position_count": 4,
            "cash": 5500.0,
            "equity": 26000.0,
        },
    )
    
    # Call refresh function
    ok = reconciliation.refresh_canonical_snapshot_from_broker(
        trading_mode="alpaca",
        run_date="2026-03-02",
    )
    assert ok is True
    
    # Verify canonical snapshot was updated
    payload = json.loads(old_bootstrap_canonical.read_text(encoding="utf-8"))
    
    # Verify old positions replaced with new ones
    assert payload["positions"] == {"AAPL": 10.0, "MSFT": 8.0, "CME": 2.0, "VZ": 5.0}
    assert payload["position_count"] == 4
    assert payload["cash"] == 5500.0
    assert payload["equity"] == 26000.0
    assert payload["reason"] == "posttrade_refresh_from_broker"


def test_refresh_canonical_snapshot_non_alpaca_mode_fails(tmp_path, monkeypatch):
    """Test that refresh fails gracefully when trading mode is not alpaca."""
    monkeypatch.chdir(tmp_path)
    
    # Call refresh function with non-alpaca mode
    ok = reconciliation.refresh_canonical_snapshot_from_broker(
        trading_mode="shadow",
        run_date="2026-03-02",
    )
    assert ok is False
    
    # Verify canonical snapshot was NOT written
    canonical = Path("outputs/paper_state/canonical_positions.json")
    assert not canonical.exists()


def test_refresh_canonical_snapshot_handles_exception_gracefully(tmp_path, monkeypatch):
    """Test that refresh handles broker snapshot load exception gracefully."""
    monkeypatch.chdir(tmp_path)
    
    # Mock broker snapshot that raises exception
    def mock_broker_snapshot(mode):
        raise RuntimeError("Broker connection failed")
    
    monkeypatch.setattr(
        reconciliation,
        "_load_broker_snapshot",
        mock_broker_snapshot,
    )
    
    # Call refresh function
    ok = reconciliation.refresh_canonical_snapshot_from_broker(
        trading_mode="alpaca",
        run_date="2026-03-02",
    )
    assert ok is False
    
    # Verify canonical snapshot was NOT written
    canonical = Path("outputs/paper_state/canonical_positions.json")
    assert not canonical.exists()


def test_posttrade_recon_passes_with_refreshed_canonical(tmp_path, monkeypatch):
    """Test that posttrade reconciliation passes when canonical snapshot is refreshed."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RECON_ENABLE", "1")
    monkeypatch.setenv("RECON_STRICT_POST", "0")
    
    # Start with canonical snapshot having fewer positions
    initial_canonical = Path("outputs/paper_state/canonical_positions.json")
    initial_canonical.parent.mkdir(parents=True, exist_ok=True)
    initial_payload = {
        "positions": {"AAPL": 10.0},
        "position_count": 1,
        "cash": 1000.0,
        "equity": 20000.0,
        "reason": "bootstrap_from_broker",
    }
    initial_canonical.write_text(json.dumps(initial_payload, indent=2) + "\n", encoding="utf-8")
    
    # Mock broker snapshot with matching positions
    monkeypatch.setattr(
        reconciliation,
        "_load_broker_snapshot",
        lambda mode: {
            "source": "test",
            "positions": {"AAPL": 10.0},
            "position_count": 1,
            "cash": 1000.0,
            "equity": 20000.0,
        },
    )
    
    # Refresh canonical snapshot
    ok = reconciliation.refresh_canonical_snapshot_from_broker(
        trading_mode="alpaca",
        run_date="2026-03-02",
    )
    assert ok is True
    
    # Run posttrade reconciliation
    missing_ledger = tmp_path / "paper_state" / "ledger.csv"
    sent_ledger = tmp_path / "shadow_orders" / "orders_sent.csv"
    
    verdict, report_path = reconciliation._reconcile(
        stage="posttrade",
        run_date="2026-03-02",
        trading_mode="alpaca",
        ledger_path=str(missing_ledger),
        sent_ledger_path=str(sent_ledger),
        allow_empty=False,
        strict=False,
    )
    
    # Verify reconciliation passed
    assert verdict == "PASS", f"Reconciliation failed with verdict={verdict}, report={report_path}"
    report_payload = json.loads(Path(report_path).read_text(encoding="utf-8"))
    assert report_payload["diffs"]["missing_in_broker"] == []
    assert report_payload["diffs"]["missing_in_model"] == []

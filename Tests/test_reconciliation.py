import json
from pathlib import Path

import pytest

import reconciliation
from reconciliation import _normalize_positions, compare_positions, verdict_from_diffs


def test_compare_positions_exact_match_passes():
    diffs = compare_positions(
        broker_positions={"AAPL": 10.0, "MSFT": 5.0},
        model_positions={"AAPL": 10.0, "MSFT": 5.0},
        max_qty_diff=0.0,
    )
    assert verdict_from_diffs(diffs, strict=True) == "PASS"


def test_compare_positions_qty_mismatch_fails():
    diffs = compare_positions(
        broker_positions={"AAPL": 10.0},
        model_positions={"AAPL": 9.0},
        max_qty_diff=0.0,
    )
    assert diffs["qty_mismatches"]
    assert verdict_from_diffs(diffs) == "FAIL"


def test_compare_positions_missing_symbol_fails():
    diffs = compare_positions(
        broker_positions={"AAPL": 10.0},
        model_positions={"AAPL": 10.0, "MSFT": 1.0},
        max_qty_diff=0.0,
    )
    assert "MSFT" in diffs["missing_in_broker"]
    assert verdict_from_diffs(diffs) == "FAIL"


def test_verdict_cash_equity_within_tolerance_passes():
    diffs = compare_positions(
        broker_positions={"AAPL": 10.0},
        model_positions={"AAPL": 10.0},
        max_qty_diff=0.0,
    )
    verdict = verdict_from_diffs(
        diffs,
        cash_delta=2.0,
        equity_delta=3.0,
        equity_base=10000.0,
        cash_tol=5.0,
        equity_tol_abs=10.0,
        equity_tol_pct=0.001,
        strict=False,
    )
    assert verdict == "PASS"


def test_verdict_cash_delta_warn_when_not_strict():
    diffs = compare_positions(
        broker_positions={"AAPL": 10.0},
        model_positions={"AAPL": 10.0},
        max_qty_diff=0.0,
    )
    verdict = verdict_from_diffs(
        diffs,
        cash_delta=50.0,
        equity_delta=0.0,
        equity_base=10000.0,
        cash_tol=5.0,
        equity_tol_abs=10.0,
        equity_tol_pct=0.001,
        strict=False,
    )
    assert verdict == "WARN"


def test_verdict_equity_drift_hard_fail_when_flag_set():
    """Equity drift only escalates to FAIL when hard_fail_on_equity_drift=True."""
    diffs = compare_positions(
        broker_positions={"AAPL": 10.0},
        model_positions={"AAPL": 10.0},
        max_qty_diff=0.0,
    )
    verdict = verdict_from_diffs(
        diffs,
        cash_delta=50.0,
        equity_delta=0.0,
        equity_base=10000.0,
        cash_tol=5.0,
        equity_tol_abs=10.0,
        equity_tol_pct=0.001,
        hard_fail_on_equity_drift=True,
    )
    assert verdict == "FAIL"


def test_equity_drift_only_warns_not_fails():
    """Positions match but equity drifted => WARN, not FAIL (the 2026-03-06 scenario)."""
    diffs = compare_positions(
        broker_positions={"AAPL": 10.0, "MSFT": 5.0},
        model_positions={"AAPL": 10.0, "MSFT": 5.0},
        max_qty_diff=0.0,
    )
    # Delta of $58.54 on $9991 equity — both abs and pct thresholds exceeded
    verdict = verdict_from_diffs(
        diffs,
        cash_delta=0.0,
        equity_delta=-58.54,
        equity_base=9991.06,
        cash_tol=5.0,
        equity_tol_abs=10.0,
        equity_tol_pct=0.001,
        strict=True,
        hard_fail_on_equity_drift=False,
    )
    assert verdict == "WARN", f"Expected WARN for equity-only drift, got {verdict}"


def test_equity_drift_only_warns_even_when_strict():
    """strict=True alone should NOT escalate equity drift to FAIL under new policy."""
    diffs = compare_positions(
        broker_positions={"AAPL": 10.0},
        model_positions={"AAPL": 10.0},
        max_qty_diff=0.0,
    )
    verdict = verdict_from_diffs(
        diffs,
        cash_delta=0.0,
        equity_delta=-58.54,
        equity_base=9991.06,
        cash_tol=5.0,
        equity_tol_abs=10.0,
        equity_tol_pct=0.001,
        strict=True,
        hard_fail_on_equity_drift=False,
    )
    assert verdict == "WARN"


def test_positions_mismatch_always_fails_regardless_of_equity_flag():
    """Missing broker position => FAIL even with hard_fail_on_equity_drift=False."""
    diffs = compare_positions(
        broker_positions={"AAPL": 10.0},
        model_positions={"AAPL": 10.0, "MSFT": 5.0},
        max_qty_diff=0.0,
    )
    assert "MSFT" in diffs["missing_in_broker"]
    verdict = verdict_from_diffs(diffs, hard_fail_on_equity_drift=False)
    assert verdict == "FAIL"


def test_quantity_mismatch_always_fails():
    """Qty mismatch => FAIL regardless of equity drift flags."""
    diffs = compare_positions(
        broker_positions={"AAPL": 10.0},
        model_positions={"AAPL": 7.0},
        max_qty_diff=0.0,
    )
    assert diffs["qty_mismatches"]
    assert verdict_from_diffs(diffs, hard_fail_on_equity_drift=False) == "FAIL"


def test_stale_canonical_errors_fail():
    """Parse/stale errors in diffs => FAIL."""
    diffs = {"missing_in_model": [], "missing_in_broker": [], "qty_mismatches": [], "errors": ["canonical_snapshot_missing"]}
    assert verdict_from_diffs(diffs) == "FAIL"


# ---------------------------------------------------------------------------
# Integration tests: pre_trade_reconcile_or_exit gate behaviour
# ---------------------------------------------------------------------------

def _broker_positions_match_equity_drift(mode):
    """Positions match broker exactly; equity drifted by $58.54 (2026-03-06 scenario)."""
    return {
        "source": "test",
        "positions": {"AAPL": 10.0, "MSFT": 5.0},
        "position_count": 2,
        "cash": 1000.0,
        "equity": 9932.52,
    }


def _broker_positions_mismatch(mode):
    """Broker has an extra symbol the model doesn't know about."""
    return {
        "source": "test",
        "positions": {"AAPL": 10.0, "MSFT": 5.0, "EXTRA": 3.0},
        "position_count": 3,
        "cash": 1000.0,
        "equity": 10000.0,
    }


def _write_canonical(tmp_path, positions, equity=9991.06):
    canonical = Path("outputs/paper_state/canonical_positions.json")
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text(
        json.dumps({"positions": positions, "equity": equity, "reason": "bootstrap_from_broker"}, indent=2) + "\n",
        encoding="utf-8",
    )


def test_pretrade_equity_drift_only_does_not_block(tmp_path, monkeypatch):
    """2026-03-06 scenario: positions match, equity drifted => WARN => execution proceeds."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RECON_ENABLE", "1")
    monkeypatch.setenv("RECON_STRICT_PRE", "1")
    monkeypatch.delenv("RECON_EQUITY_HARD_FAIL", raising=False)
    monkeypatch.setattr(reconciliation, "_load_broker_snapshot", _broker_positions_match_equity_drift)
    _write_canonical(tmp_path, positions={"AAPL": 10.0, "MSFT": 5.0}, equity=9991.06)

    sent = tmp_path / "shadow_orders" / "orders_sent.csv"
    # Must not raise SystemExit
    reconciliation.pre_trade_reconcile_or_exit(
        run_date="2026-03-06",
        trading_mode="alpaca",
        ledger_path=str(tmp_path / "ledger.csv"),
        sent_ledger_path=str(sent),
    )

    report = Path("outputs/broker/recon_pretrade_2026-03-06.json")
    assert report.exists()
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["verdict"] == "WARN"
    assert payload["block_reason"] == "equity_only_drift"
    assert payload["recon_summary"]["positions_ok"] is True
    # Blocked artifact must NOT be written when WARN (execution was not blocked)
    assert not Path("outputs/broker/recon_execution_blocked_2026-03-06.json").exists()


def test_pretrade_position_mismatch_blocks_and_writes_artifact(tmp_path, monkeypatch):
    """Position mismatch => FAIL => SystemExit(2) + blocked artifact written."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RECON_ENABLE", "1")
    monkeypatch.setenv("RECON_STRICT_PRE", "1")
    monkeypatch.delenv("RECON_EQUITY_HARD_FAIL", raising=False)
    monkeypatch.setattr(reconciliation, "_load_broker_snapshot", _broker_positions_mismatch)
    _write_canonical(tmp_path, positions={"AAPL": 10.0, "MSFT": 5.0}, equity=10000.0)

    sent = tmp_path / "shadow_orders" / "orders_sent.csv"
    with pytest.raises(SystemExit) as exc:
        reconciliation.pre_trade_reconcile_or_exit(
            run_date="2026-03-06",
            trading_mode="alpaca",
            ledger_path=str(tmp_path / "ledger.csv"),
            sent_ledger_path=str(sent),
        )
    assert int(exc.value.code) == 2

    report = Path("outputs/broker/recon_pretrade_2026-03-06.json")
    assert report.exists()
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["verdict"] == "FAIL"
    assert payload["block_reason"] == "positions_mismatch"

    blocked = Path("outputs/broker/recon_execution_blocked_2026-03-06.json")
    assert blocked.exists()
    blocked_payload = json.loads(blocked.read_text(encoding="utf-8"))
    assert blocked_payload["status"] == "blocked_by_recon"
    assert blocked_payload["block_reason"] == "positions_mismatch"
    assert blocked_payload["orders_intended_count"] is None


def test_pretrade_qty_mismatch_blocks(tmp_path, monkeypatch):
    """Quantity mismatch => FAIL => SystemExit(2)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RECON_ENABLE", "1")
    monkeypatch.delenv("RECON_EQUITY_HARD_FAIL", raising=False)
    monkeypatch.setattr(
        reconciliation,
        "_load_broker_snapshot",
        lambda mode: {
            "source": "test",
            "positions": {"AAPL": 10.0, "MSFT": 5.0},
            "position_count": 2,
            "cash": 1000.0,
            "equity": 10000.0,
        },
    )
    # Model has different qty for MSFT
    _write_canonical(tmp_path, positions={"AAPL": 10.0, "MSFT": 3.0}, equity=10000.0)

    sent = tmp_path / "shadow_orders" / "orders_sent.csv"
    with pytest.raises(SystemExit) as exc:
        reconciliation.pre_trade_reconcile_or_exit(
            run_date="2026-03-06",
            trading_mode="alpaca",
            ledger_path=str(tmp_path / "ledger.csv"),
            sent_ledger_path=str(sent),
        )
    assert int(exc.value.code) == 2

    payload = json.loads(
        Path("outputs/broker/recon_pretrade_2026-03-06.json").read_text(encoding="utf-8")
    )
    assert payload["verdict"] == "FAIL"
    assert payload["block_reason"] == "quantity_mismatch"


def test_pretrade_stale_canonical_blocks(tmp_path, monkeypatch):
    """Missing canonical snapshot => stale_state FAIL => SystemExit(2)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RECON_ENABLE", "1")
    monkeypatch.delenv("RECON_EQUITY_HARD_FAIL", raising=False)
    monkeypatch.setattr(
        reconciliation,
        "_load_broker_snapshot",
        lambda mode: {
            "source": "test",
            "positions": {"AAPL": 10.0},
            "position_count": 1,
            "cash": 1000.0,
            "equity": 10000.0,
        },
    )
    # No canonical file written; no legacy ledger either

    sent = tmp_path / "shadow_orders" / "orders_sent.csv"
    with pytest.raises(SystemExit) as exc:
        reconciliation.pre_trade_reconcile_or_exit(
            run_date="2026-03-06",
            trading_mode="alpaca",
            ledger_path=str(tmp_path / "missing_ledger.csv"),
            sent_ledger_path=str(sent),
        )
    assert int(exc.value.code) == 2

    payload = json.loads(
        Path("outputs/broker/recon_pretrade_2026-03-06.json").read_text(encoding="utf-8")
    )
    assert payload["verdict"] == "FAIL"
    assert payload["block_reason"] == "stale_state"


def test_pretrade_equity_hard_fail_env_restores_old_behaviour(tmp_path, monkeypatch):
    """RECON_EQUITY_HARD_FAIL=1 makes equity drift block even with matching positions."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RECON_ENABLE", "1")
    monkeypatch.setenv("RECON_EQUITY_HARD_FAIL", "1")
    monkeypatch.setattr(reconciliation, "_load_broker_snapshot", _broker_positions_match_equity_drift)
    _write_canonical(tmp_path, positions={"AAPL": 10.0, "MSFT": 5.0}, equity=9991.06)

    sent = tmp_path / "shadow_orders" / "orders_sent.csv"
    with pytest.raises(SystemExit) as exc:
        reconciliation.pre_trade_reconcile_or_exit(
            run_date="2026-03-06",
            trading_mode="alpaca",
            ledger_path=str(tmp_path / "ledger.csv"),
            sent_ledger_path=str(sent),
        )
    assert int(exc.value.code) == 2

    payload = json.loads(
        Path("outputs/broker/recon_pretrade_2026-03-06.json").read_text(encoding="utf-8")
    )
    assert payload["verdict"] == "FAIL"
    assert payload["block_reason"] == "equity_only_drift"


def test_normalize_positions_handles_list_and_dict_inputs():
    out_list = _normalize_positions(
        [
            {"symbol": "aapl", "qty": "1"},
            {"ticker": "AAPL", "shares": 2},
            {"symbol": "MSFT", "quantity": "3.5"},
        ]
    )
    out_dict = _normalize_positions({"aapl": "1", "MSFT": {"qty": "3.5"}})

    assert out_list["AAPL"] == 3.0
    assert out_list["MSFT"] == 3.5
    assert out_dict["AAPL"] == 1.0
    assert out_dict["MSFT"] == 3.5

from __future__ import annotations

import pytest

from authority.contracts import build_decision_package, build_evidence_package, build_risk_package
from authority.pipeline import execution_package_from_risk
from execution.core import ExecutionRequest, compute_transition_trades, paper_execution_config
from paper.paper_broker import PaperConfig


def _config():
    return paper_execution_config(
        PaperConfig(initial_equity=1000.0, benchmark_ticker="SPY", slippage_bps=0.0,
                    allow_fractional=False, min_trade_dollars=1.0),
        target_cash_weight=0.0, ledger_enabled=False,
    )


def test_approved_execution_package_is_the_only_source_on_migrated_path():
    rows = [{"symbol": "AAPL", "side": "BUY", "shares": 2, "price": 100.0, "notional": 200.0}]
    evidence = build_evidence_package(
        package_id="evidence:test", trade_date="2026-08-07", source_refs=["signals.json"], observations=rows,
    )
    decision = build_decision_package(
        package_id="decision:test", trade_date="2026-08-07", evidence=evidence,
        target_rows=rows, source_refs=["signals.json"],
    )
    risk = build_risk_package(
        package_id="risk:test", decision=decision, approved_target_rows=rows,
        constraints={}, source_refs=["decision:decision:test"],
    )
    package = execution_package_from_risk(risk).to_dict()
    request = ExecutionRequest(
        holdings=None,
        targets=None,
        prices=None,
        total_equity=1000.0,
        starting_cash=1000.0,
        target_cash_weight=0.0,
        planning_account={"cash": 1000.0, "equity": 1000.0},
        run_id="authority-test",
        approved_execution_package=package,
    )
    config = _config()
    trades, meta = compute_transition_trades(request=request, config=config)
    assert list(trades["ticker"]) == ["AAPL"]
    assert meta["source"] == "approved_execution_package"


@pytest.mark.parametrize("package", [{}, {"schema_version": "wrong"}, {"schema_version": "caerus.execution.v1", "approved_target_rows": []}])
def test_migrated_path_fails_closed_for_malformed_package(package):
    request = ExecutionRequest(
        holdings=None, targets=None, prices=None, total_equity=1000.0, starting_cash=1000.0,
        target_cash_weight=0.0, planning_account={}, run_id="bad", approved_execution_package=package,
    )
    with pytest.raises(ValueError):
        compute_transition_trades(request=request, config=_config())


def test_migrated_path_rejects_tampered_hash():
    rows = [{"symbol": "AAPL", "side": "BUY", "shares": 1, "price": 100.0}]
    evidence = build_evidence_package(
        package_id="evidence:test", trade_date="2026-08-07", source_refs=["signals.json"], observations=rows,
    )
    decision = build_decision_package(
        package_id="decision:test", trade_date="2026-08-07", evidence=evidence,
        target_rows=rows, source_refs=["signals.json"],
    )
    risk = build_risk_package(
        package_id="risk:test", decision=decision, approved_target_rows=rows,
        constraints={}, source_refs=["decision:decision:test"],
    )
    package = execution_package_from_risk(risk).to_dict()
    package["approved_target_rows"][0]["shares"] = 2
    request = ExecutionRequest(
        holdings=None, targets=None, prices=None, total_equity=1000.0, starting_cash=1000.0,
        target_cash_weight=0.0, planning_account={}, run_id="tampered", approved_execution_package=package,
    )
    with pytest.raises(ValueError, match="content_hash mismatch"):
        compute_transition_trades(request=request, config=_config())


def test_migrated_path_preserves_approved_no_action():
    evidence = build_evidence_package(
        package_id="evidence:none", trade_date="2026-08-07",
        source_refs=["signals.json"], observations=[],
    )
    decision = build_decision_package(
        package_id="decision:none", trade_date="2026-08-07", evidence=evidence,
        target_rows=[], source_refs=["signals.json"],
    )
    risk = build_risk_package(
        package_id="risk:none", decision=decision, approved_target_rows=[],
        constraints={}, source_refs=["decision:decision:none"],
    )
    request = ExecutionRequest(
        holdings=None, targets=None, prices=None, total_equity=1000.0, starting_cash=1000.0,
        target_cash_weight=1.0, planning_account={}, run_id="no-action",
        approved_execution_package=execution_package_from_risk(risk).to_dict(),
    )
    trades, meta = compute_transition_trades(request=request, config=_config())
    assert trades.empty
    assert meta["source"] == "approved_execution_package"

from __future__ import annotations

import pytest
import pandas as pd

from authority.contracts import build_decision_package, build_evidence_package, build_risk_package
from authority.pipeline import execution_package_from_risk
from execution.core import ExecutionRequest, compute_transition_trades, paper_execution_config
from paper.paper_broker import PaperConfig
from scripts.live_pilot_execute import _build_core_request


def _config():
    return paper_execution_config(
        PaperConfig(initial_equity=1000.0, benchmark_ticker="SPY", slippage_bps=0.0,
                    allow_fractional=False, min_trade_dollars=1.0),
        target_cash_weight=0.0, ledger_enabled=False,
    )


def test_approved_execution_package_is_the_only_source_on_migrated_path():
    rows = [{"symbol": "AAPL", "target_weight": 0.2, "price": 100.0}]
    evidence = build_evidence_package(
        package_id="evidence:test", trade_date="2026-08-07", source_refs=["signals.json"], observations=rows,
    )
    decision = build_decision_package(
        package_id="decision:test", trade_date="2026-08-07", evidence=evidence,
        target_rows=rows, target_cash_weight=0.8, source_refs=["signals.json"],
    )
    risk = build_risk_package(
        package_id="risk:test", decision=decision, approved_target_rows=rows,
        approved_cash_weight=0.8, constraints={}, source_refs=["decision:decision:test"],
    )
    package = execution_package_from_risk(risk).to_dict()
    request = ExecutionRequest(
        holdings=pd.DataFrame(columns=["ticker", "shares"]),
        targets=None,
        prices=pd.Series(dtype=float),
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
    assert list(trades["shares"]) == [2.0]
    assert meta["source"] == "approved_execution_package"
    assert meta["target_cash_weight"] == 0.8


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
        target_rows=[], target_cash_weight=1.0, source_refs=["signals.json"],
    )
    risk = build_risk_package(
        package_id="risk:none", decision=decision, approved_target_rows=[],
        approved_cash_weight=1.0, constraints={}, source_refs=["decision:decision:none"],
    )
    request = ExecutionRequest(
        holdings=pd.DataFrame(columns=["ticker", "shares"]), targets=None,
        prices=pd.Series(dtype=float), total_equity=1000.0, starting_cash=1000.0,
        target_cash_weight=1.0, planning_account={}, run_id="no-action",
        approved_execution_package=execution_package_from_risk(risk).to_dict(),
    )
    trades, meta = compute_transition_trades(request=request, config=_config())
    assert trades.empty
    assert meta["source"] == "approved_execution_package"


def test_live_request_rebudget_inputs_come_from_approved_package():
    rows = [{"symbol": "AAPL", "target_weight": 0.2, "price": 100.0}]
    evidence = build_evidence_package(
        package_id="evidence:test", trade_date="2026-08-07", source_refs=["signals.json"], observations=rows,
    )
    decision = build_decision_package(
        package_id="decision:test", trade_date="2026-08-07", evidence=evidence,
        target_rows=rows, target_cash_weight=0.8, source_refs=["signals.json"],
    )
    risk = build_risk_package(
        package_id="risk:test", decision=decision, approved_target_rows=rows,
        approved_cash_weight=0.8, constraints={}, source_refs=["decision:decision:test"],
    )
    package = execution_package_from_risk(risk).to_dict()
    request, malformed = _build_core_request(
        pre_snapshot={
            "account": {"equity": "1000", "cash": "1000", "buying_power": "1000"},
            "positions": [],
        },
        plan={
            "cash_target_weight": 0.0,
            "target_portfolio": [{"symbol": "MSFT", "target_weight": 0.9, "price": 50.0}],
            "approved_execution_package": package,
        },
        run_id="authority-request",
    )
    assert malformed == []
    assert request is not None
    assert list(request.targets["ticker"]) == ["AAPL"]
    assert request.target_cash_weight == 0.8

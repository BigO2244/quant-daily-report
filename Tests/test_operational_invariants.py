from __future__ import annotations

import json
from pathlib import Path

from core.operational_invariants import (
    build_execution_reliability_report,
    write_execution_reliability_report,
)


TRADE_DATE = "2026-06-19"
RUN_ID = "2026-06-19T093506-0400_reliability"


def _write_json(path: Path, payload: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _run_root(tmp_path: Path) -> Path:
    root = tmp_path / "outputs" / "runs" / RUN_ID
    (root / "audit").mkdir(parents=True)
    (root / "broker").mkdir(parents=True)
    return root


def _write_base_run(
    run_root: Path,
    *,
    execution_payload: dict[str, object] | None = None,
    execution_results: dict[str, object] | None = None,
    operator_summary: dict[str, object] | None = None,
) -> None:
    payload = {
        "run_id": RUN_ID,
        "trade_date": TRADE_DATE,
        "execution_status": "EXECUTED",
        "operator_execution_status": "executed",
        "planned_payload_trade_count": 1,
        "planner_intended_trades_count": 1,
        "execution_eligible_trades_count": 1,
        "submitted_count": 1,
        "accepted_count": 1,
        "orders_filled_count": 1,
        "trades": [{"ticker": "AAPL", "side": "BUY", "shares": 1}],
    }
    payload.update(execution_payload or {})
    results = {
        "run_id": RUN_ID,
        "trade_date": TRADE_DATE,
        "status": payload.get("execution_status"),
        "submitted_count": payload.get("submitted_count", 1),
        "accepted_count": payload.get("accepted_count", 1),
        "orders_filled_count": payload.get("orders_filled_count", 1),
        "broker_responses": [{"ticker": "AAPL", "side": "BUY", "status": "FILLED"}],
    }
    results.update(execution_results or {})
    summary = {
        "run_id": RUN_ID,
        "trade_date": TRADE_DATE,
        "terminal_status": "success",
        "operator_execution_status": payload.get("operator_execution_status", "executed"),
        "planner_intended_trades_count": payload.get("planner_intended_trades_count", 1),
        "execution_eligible_trades_count": payload.get("execution_eligible_trades_count", 1),
        "submitted_count": payload.get("submitted_count", 1),
        "accepted_count": payload.get("accepted_count", 1),
    }
    summary.update(operator_summary or {})
    _write_json(run_root / "execution_payload.json", payload)
    _write_json(run_root / "execution_results.json", results)
    _write_json(run_root / "operator_summary.json", summary)


def _result(report: dict[str, object], invariant_id: str) -> dict[str, object]:
    for row in report["invariant_results"]:
        if row["invariant_id"] == invariant_id:
            return row
    raise AssertionError(f"missing invariant {invariant_id}")


def test_nonempty_planned_payload_zero_submitted_fails_with_drop_reason(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    run_root = _run_root(tmp_path)
    _write_base_run(
        run_root,
        execution_payload={
            "execution_status": "HALTED",
            "operator_execution_status": "failed",
            "halt_reason": "planned_payload_trades_dropped_before_execution",
            "planned_payload_trade_count": 8,
            "planner_intended_trades_count": 8,
            "execution_eligible_trades_count": 0,
            "submitted_count": 0,
            "accepted_count": 0,
            "orders_filled_count": 0,
            "trades": [],
        },
        execution_results={
            "status": "HALTED",
            "halt_reason": "planned_payload_trades_dropped_before_execution",
            "planned_payload_trade_count": 8,
            "executable_trades_count": 0,
            "submitted_count": 0,
            "accepted_count": 0,
            "orders_filled_count": 0,
            "broker_responses": [],
        },
        operator_summary={"terminal_status": "failed_pre_execution"},
    )

    report = build_execution_reliability_report(
        run_root=run_root,
        trade_date=TRADE_DATE,
        run_id=RUN_ID,
    )

    invariant = _result(report, "planned_payload_nonempty_zero_execution")
    assert invariant["status"] == "FAIL"
    assert invariant["reason_code"] == "planned_payload_trades_dropped_before_execution"
    assert report["score"] < 100
    assert report["overall_status"] == "FAIL"
    assert report["top_failure_reason"] == "planned_payload_trades_dropped_before_execution"
    assert "Halt the run" in report["recommended_operator_actions"][0]


def test_empty_planned_payload_zero_submitted_is_legitimate_no_action(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    run_root = _run_root(tmp_path)
    _write_base_run(
        run_root,
        execution_payload={
            "execution_status": "NO_ACTION",
            "operator_execution_status": "skipped",
            "planned_payload_trade_count": 0,
            "planner_intended_trades_count": 0,
            "execution_eligible_trades_count": 0,
            "submitted_count": 0,
            "accepted_count": 0,
            "orders_filled_count": 0,
            "trades": [],
        },
        execution_results={
            "status": "NO_ACTION",
            "planned_payload_trade_count": 0,
            "executable_trades_count": 0,
            "submitted_count": 0,
            "accepted_count": 0,
            "orders_filled_count": 0,
            "broker_responses": [],
        },
        operator_summary={"terminal_status": "no_action", "planner_intended_trades_count": 0},
    )

    report = build_execution_reliability_report(
        run_root=run_root,
        trade_date=TRADE_DATE,
        run_id=RUN_ID,
    )

    assert _result(report, "planned_payload_nonempty_zero_execution")["status"] == "PASS"
    assert _result(report, "terminal_status_requires_reason")["status"] == "PASS"
    assert report["overall_status"] == "PASS"
    assert report["score"] == 100


def test_submitted_orders_without_acceptance_fails_with_action(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    run_root = _run_root(tmp_path)
    _write_base_run(
        run_root,
        execution_payload={"submitted_count": 3, "accepted_count": 0, "orders_filled_count": 0},
        execution_results={
            "submitted_count": 3,
            "accepted_count": 0,
            "orders_filled_count": 0,
            "broker_responses": [{"ticker": "AAPL", "side": "BUY", "status": "REJECTED"}],
        },
    )

    report = build_execution_reliability_report(
        run_root=run_root,
        trade_date=TRADE_DATE,
        run_id=RUN_ID,
    )

    invariant = _result(report, "submitted_orders_without_acceptance")
    assert invariant["status"] == "FAIL"
    assert invariant["reason_code"] == "submitted_orders_not_accepted_by_broker"
    assert invariant["operator_action"]


def test_accepted_orders_zero_fills_with_unresolved_state_warns(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    run_root = _run_root(tmp_path)
    _write_base_run(
        run_root,
        execution_payload={
            "submitted_count": 2,
            "accepted_count": 2,
            "orders_filled_count": 0,
            "posttrade_unresolved_orders_count": 2,
        },
        execution_results={
            "submitted_count": 2,
            "accepted_count": 2,
            "orders_filled_count": 0,
            "posttrade_unresolved_orders_count": 2,
            "broker_responses": [{"ticker": "AAPL", "side": "BUY", "status": "ACCEPTED"}],
        },
    )
    _write_json(
        tmp_path / "outputs" / "precompute" / TRADE_DATE / "planned_execution_payload.json",
        {"trade_date": TRADE_DATE, "trades": [{"ticker": "AAPL", "side": "BUY", "shares": 1}]},
    )

    report = build_execution_reliability_report(
        run_root=run_root,
        trade_date=TRADE_DATE,
        run_id=RUN_ID,
    )

    invariant = _result(report, "accepted_orders_zero_fills_unresolved")
    assert invariant["status"] == "WARN"
    assert invariant["reason_code"] == "accepted_orders_unfilled_with_unresolved_status"
    assert report["top_failure_reason"] == "accepted_orders_unfilled_with_unresolved_status"


def test_target_cash_materially_above_intended_cash_warns_with_evidence(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    run_root = _run_root(tmp_path)
    _write_base_run(run_root)
    _write_json(
        run_root / "audit" / f"execution_target_attainment_{TRADE_DATE}.json",
        {
            "status": "WARN_RECONCILED_BUT_UNDERDEPLOYED",
            "target_cash_weight": 0.05,
            "achieved_cash_weight": 0.21,
            "cash_target_drift": 0.16,
        },
    )

    report = build_execution_reliability_report(
        run_root=run_root,
        trade_date=TRADE_DATE,
        run_id=RUN_ID,
    )

    invariant = _result(report, "target_cash_actual_cash_drift")
    assert invariant["status"] == "WARN"
    assert invariant["reason_code"] == "target_cash_materially_differs_from_actual_cash"
    assert invariant["evidence"]["target_cash_weight"] == 0.05
    assert invariant["evidence"]["actual_cash_weight"] == 0.21


def test_reconciliation_mismatch_fails_with_model_broker_fields(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    run_root = _run_root(tmp_path)
    _write_base_run(run_root)
    _write_json(
        run_root / "broker" / f"recon_posttrade_{TRADE_DATE}.json",
        {
            "status": "FAIL",
            "model_equity": 10000.0,
            "broker_equity": 9925.0,
            "broker_minus_model_equity_delta": -75.0,
        },
    )

    report = build_execution_reliability_report(
        run_root=run_root,
        trade_date=TRADE_DATE,
        run_id=RUN_ID,
    )

    invariant = _result(report, "model_broker_reconciliation")
    assert invariant["status"] == "FAIL"
    assert invariant["reason_code"] == "model_broker_reconciliation_mismatch"
    assert invariant["evidence"]["model_equity"] == 10000.0
    assert invariant["evidence"]["broker_equity"] == 9925.0
    assert invariant["operator_action"]


def test_missing_reason_regression_fails_terminal_status_invariant(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    run_root = _run_root(tmp_path)
    _write_base_run(
        run_root,
        execution_payload={
            "execution_status": "NO_ACTION",
            "operator_execution_status": "skipped",
            "halt_reason": None,
            "reason": None,
            "execution_reason": None,
            "planned_payload_trade_count": 0,
            "planner_intended_trades_count": 2,
            "execution_eligible_trades_count": 0,
            "submitted_count": 0,
            "accepted_count": 0,
            "orders_filled_count": 0,
            "trades": [],
        },
        execution_results={
            "status": "NO_ACTION",
            "submitted_count": 0,
            "accepted_count": 0,
            "orders_filled_count": 0,
            "broker_responses": [],
        },
        operator_summary={"terminal_status": "no_action", "planner_intended_trades_count": 2},
    )

    report = build_execution_reliability_report(
        run_root=run_root,
        trade_date=TRADE_DATE,
        run_id=RUN_ID,
    )

    invariant = _result(report, "terminal_status_requires_reason")
    assert invariant["status"] == "FAIL"
    assert invariant["reason_code"] == "terminal_execution_status_missing_reason"


def test_non_finite_sleeve_numeric_state_fails_with_first_event(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    run_root = _run_root(tmp_path)
    _write_base_run(run_root)
    _write_json(
        run_root / "audit" / f"sleeve_numeric_trace_trend_{TRADE_DATE}.json",
        {
            "status": "BLOCKING",
            "reason_code": "sleeve_terminal_equity_nan",
            "sleeve_id": "trend",
            "first_event": {
                "sleeve_id": "trend",
                "field": "terminal_equity",
                "value": "nan",
                "calculation_stage": "sleeve_validation",
            },
            "events": [],
        },
    )

    report = build_execution_reliability_report(
        run_root=run_root,
        trade_date=TRADE_DATE,
        run_id=RUN_ID,
    )

    invariant = _result(report, "sleeve_numeric_finiteness")
    assert invariant["status"] == "FAIL"
    assert invariant["reason_code"] == "sleeve_terminal_equity_nan"
    assert invariant["evidence"]["sleeve_id"] == "trend"
    assert invariant["evidence"]["field"] == "terminal_equity"
    assert invariant["evidence"]["first_event"]["calculation_stage"] == "sleeve_validation"


def test_write_execution_reliability_report_writes_daily_audit_artifact(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    run_root = _run_root(tmp_path)
    _write_base_run(run_root)
    _write_json(
        tmp_path / "outputs" / "precompute" / TRADE_DATE / "planned_execution_payload.json",
        {"trade_date": TRADE_DATE, "trades": [{"ticker": "AAPL", "side": "BUY", "shares": 1}]},
    )

    out_path = write_execution_reliability_report(
        run_root=run_root,
        trade_date=TRADE_DATE,
        run_id=RUN_ID,
    )

    assert out_path == run_root / "audit" / f"execution_reliability_report_{TRADE_DATE}.json"
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["run_id"] == RUN_ID
    assert payload["trade_date"] == TRADE_DATE
    assert payload["score"] == 100
    assert payload["top_failure_reason"] is None

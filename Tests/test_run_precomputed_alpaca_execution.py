from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import scripts.run_precomputed_alpaca_execution as live_exec

from scripts.run_precomputed_alpaca_execution import _precompute_reconciliation_halt_reason


def test_precompute_reconciliation_halt_reason_pass_allows_execution() -> None:
    assert _precompute_reconciliation_halt_reason({"reconciliation_decision": "PASS"}) is None


def test_precompute_reconciliation_halt_reason_blocks_self_heal() -> None:
    assert (
        _precompute_reconciliation_halt_reason({"reconciliation_decision": "SELF_HEAL"})
        == "precompute_reconciliation_self_heal"
    )


def test_precompute_reconciliation_halt_reason_preserves_block_reason() -> None:
    assert (
        _precompute_reconciliation_halt_reason(
            {
                "reconciliation_decision": "BLOCK",
                "block_reason": "pretrade_blocked_reconciliation",
            }
        )
        == "pretrade_blocked_reconciliation"
    )


def test_main_pass_path_keeps_precompute_plan_and_submissions_aligned(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REPORT_DATE", "2026-03-26")
    monkeypatch.setenv("PRECOMPUTE_EXECUTE_EXACT_PLAN", "1")

    run_id = "run-pass"
    run_root = tmp_path / "outputs" / "runs" / run_id
    signals_path = tmp_path / "signals.json"
    signals_path.write_text(
        json.dumps(
            {
                "snapshot_date": "2026-03-26",
                "cash_target_weight": 0.278,
                "meta": {},
                "signals": [],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    planned_trades = [
        {
            "ticker": "FTNT",
            "side": "BUY",
            "shares": 3,
            "price": 79.34,
            "notional": 238.02,
            "reason": "selected_for_quality",
            "order_id": "run:FTNT:BUY",
        },
        {
            "ticker": "QCOM",
            "side": "BUY",
            "shares": 2,
            "price": 128.67,
            "notional": 257.34,
            "reason": "selected_for_quality",
            "order_id": "run:QCOM:BUY",
        },
        {
            "ticker": "WBD",
            "side": "SELL",
            "shares": 14,
            "price": 27.27,
            "notional": 381.78,
            "reason": "removed_from_targets",
            "order_id": "run:WBD:SELL",
        },
    ]
    planned_payload = {"trade_date": "2026-03-26", "mode": "ALPACA", "trades": planned_trades}
    daily_snapshot = {
        "holdings": [
            {"ticker": "WBD", "shares": 14.0, "last_price": 27.27},
            {"ticker": "CASH", "shares": 1.0, "last_price": 2780.0},
        ],
        "risk_levels": [
            {"ticker": "FTNT", "entry_price": 79.34, "stop_loss": 72.63, "take_profit": 89.40},
            {"ticker": "QCOM", "entry_price": 128.67, "stop_loss": 118.32, "take_profit": 144.20},
            {"ticker": "WBD", "entry_price": 27.27, "stop_loss": None, "take_profit": None},
        ],
        "proposed_trades": [dict(item) for item in planned_trades],
        "signals_snapshot_path": str(signals_path),
        "target_cash_weight": 0.278,
        "performance_diagnostics": {"current_equity": 9608.38},
    }
    paper_summary = {
        "trading_mode": "ALPACA",
        "market_status": "OPEN",
        "planned_for": "2026-03-26T09:35:15-04:00",
        "execution_trades": [dict(item) for item in planned_trades],
        "trade_plan": [dict(item) for item in planned_trades],
        "alpaca_submissions": [
            {"ticker": "FTNT", "side": "BUY", "quantity": 3.0, "order_id": "run:FTNT:BUY", "submitted_at": "2026-03-26T09:35:31-04:00"},
            {"ticker": "QCOM", "side": "BUY", "quantity": 2.0, "order_id": "run:QCOM:BUY", "submitted_at": "2026-03-26T09:35:31-04:00"},
            {"ticker": "WBD", "side": "SELL", "quantity": 14.0, "order_id": "run:WBD:SELL", "submitted_at": "2026-03-26T09:35:31-04:00"},
        ],
        "alpaca_submission_summary": {
            "submit_success": 3,
            "submit_failed": 0,
            "remote_existing_orders": 0,
        },
        "cash": 3042.11,
        "execution_outcome": None,
        "execution_reason": None,
        "cash_rebalance_status": "complete",
        "execution_submitted_symbols": ["FTNT", "QCOM", "WBD"],
        "alpaca_positions_snapshot": [],
    }

    observed_run_paper_day: dict[str, object] = {}

    monkeypatch.setattr(live_exec, "_acquire_execution_lock", lambda trade_date: Path(tmp_path / f"{trade_date}.lock"))
    monkeypatch.setattr(live_exec, "get_run_id", lambda: run_id)
    monkeypatch.setattr(live_exec, "get_run_dir", lambda _run_id: run_root)
    monkeypatch.setattr(
        live_exec,
        "current_et",
        lambda: dt.datetime(2026, 3, 26, 9, 35, 15, tzinfo=ZoneInfo("America/New_York")),
    )
    monkeypatch.setattr(
        live_exec,
        "classify_timing",
        lambda **kwargs: {
            "timing_status": "on_time",
            "preferred_target_et": "2026-03-26T09:35:00-04:00",
            "degraded_auto_trade_deadline_et": "2026-03-26T09:45:00-04:00",
            "actual_workflow_start_et": "2026-03-26T09:34:00-04:00",
            "actual_execution_start_et": "2026-03-26T09:35:15-04:00",
            "first_submit_et": "2026-03-26T09:35:31-04:00",
        },
    )
    monkeypatch.setattr(
        live_exec,
        "load_precompute_inputs",
        lambda **kwargs: (daily_snapshot, planned_payload, {"version": 1}, None),
    )
    monkeypatch.setattr(live_exec, "fetch_pretrade_snapshot", lambda: {"ok": True, "positions": []})
    monkeypatch.setattr(live_exec, "write_pretrade_snapshot_artifacts", lambda **kwargs: None)
    monkeypatch.setattr(
        live_exec,
        "summarize_pretrade_broker_policy",
        lambda snapshot: {
            "broker_preflight_status": "READY",
            "broker_preflight_account_status": "ACTIVE",
            "broker_preflight_cash": 2780.0,
            "broker_preflight_equity": 9608.38,
            "broker_preflight_buying_power": 6900.0,
            "broker_preflight_restriction_flags": [],
            "broker_preflight_warning_flags": [],
            "broker_pdt_risk_status": "OK",
            "broker_pdt_daytrade_count": 0,
            "broker_pdt_daytrading_buying_power": 0.0,
            "broker_pdt_flags": [],
            "broker_pdt_warning_message": "",
            "pdt_constrained": False,
        },
    )
    monkeypatch.setattr(live_exec, "ensure_sent_ledger_exists", lambda path: None)
    monkeypatch.setattr(
        live_exec,
        "ensure_paper_state_files",
        lambda: (str(tmp_path / "ledger.csv"), str(tmp_path / "trades.csv")),
    )
    monkeypatch.setattr(
        live_exec,
        "pre_trade_reconcile_and_classify",
        lambda **kwargs: {"reconciliation_decision": "PASS", "report_path": str(tmp_path / "recon.json")},
    )
    monkeypatch.setattr(
        live_exec,
        "_apply_pre_execution_risk_controls",
        lambda **kwargs: (str(signals_path), 0.278),
    )

    def _fake_run_paper_day(**kwargs):
        observed_run_paper_day.update(kwargs)
        return dict(paper_summary)

    monkeypatch.setattr(live_exec, "run_paper_day", _fake_run_paper_day)
    monkeypatch.setattr(live_exec, "evaluate_live_retry", lambda **kwargs: {"retry_allowed": False, "retry_reason": ""})
    monkeypatch.setattr(live_exec, "write_planner_audit", lambda **kwargs: None)
    monkeypatch.setattr(live_exec, "write_operator_summary", lambda *args, **kwargs: None)
    monkeypatch.setattr(live_exec, "load_operator_summary", lambda run_root: {})
    monkeypatch.setattr(live_exec, "format_operator_summary_log", lambda summary: "")
    monkeypatch.setattr(live_exec, "format_execution_health_banner", lambda summary: "")
    monkeypatch.setattr(live_exec, "write_executor_audit", lambda **kwargs: None)
    monkeypatch.setattr(live_exec, "write_execution_artifacts", lambda **kwargs: None)
    monkeypatch.setattr(live_exec, "write_trading_day_summary", lambda **kwargs: None)
    monkeypatch.setattr(live_exec, "write_latest_run_pointer", lambda **kwargs: None)
    monkeypatch.setattr(live_exec, "build_execution_email_text", lambda payload: ("subject", "body"))
    monkeypatch.setitem(
        sys.modules,
        "core.benchmark_tracking",
        SimpleNamespace(update_benchmark_vs_spy=lambda **kwargs: None),
    )

    exit_code = live_exec.main([])

    assert exit_code == 0
    assert observed_run_paper_day["run_date"] == "2026-03-26"
    assert observed_run_paper_day["signals_path"] == str(signals_path)
    assert observed_run_paper_day["constraints"] == {"cash_target_weight": 0.278}
    assert observed_run_paper_day["precomputed_trade_plan"] == planned_trades

    execution_payload = json.loads((run_root / "execution_payload.json").read_text(encoding="utf-8"))
    execution_results = json.loads((run_root / "execution_results.json").read_text(encoding="utf-8"))
    email_payload = json.loads((tmp_path / "outputs" / "execution_email" / "2026-03-26.json").read_text(encoding="utf-8"))

    expected_trade_rows = {
        (str(item["ticker"]).upper(), str(item["side"]).upper(), int(item["shares"]))
        for item in planned_payload["trades"]
    }
    payload_trade_rows = {
        (str(item["ticker"]).upper(), str(item["side"]).upper(), int(item["shares"]))
        for item in execution_payload["trades"]
    }
    email_trade_rows = {
        (str(item["ticker"]).upper(), str(item["side"]).upper(), int(item["shares"]))
        for item in email_payload["trades"]
    }
    broker_submission_rows = {
        (str(item["ticker"]).upper(), str(item["side"]).upper(), int(float(item["quantity"])))
        for item in execution_results["broker_responses"]
    }

    assert payload_trade_rows == expected_trade_rows
    assert email_trade_rows == expected_trade_rows
    assert broker_submission_rows == expected_trade_rows
    assert execution_payload["submitted_count"] == 3
    assert execution_payload["orders_submitted_count"] == 3
    assert execution_payload["operator_execution_status"] == "executed"
    assert execution_results["submitted_count"] == 3
    assert execution_results["status"] == "EXECUTED"

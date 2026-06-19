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


def test_run_pretrade_reconciliation_retries_once_after_self_heal(monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    outcomes = [
        {"reconciliation_decision": "SELF_HEAL", "report_path": "first.json"},
        {"reconciliation_decision": "WARN", "report_path": "second.json"},
    ]

    def _fake_reconcile(**kwargs):
        calls.append(dict(kwargs))
        return outcomes[len(calls) - 1]

    monkeypatch.setattr(live_exec, "pre_trade_reconcile_and_classify", _fake_reconcile)

    result = live_exec._run_pretrade_reconciliation(
        trade_date="2026-03-27",
        paper_ledger_path="ledger.csv",
    )

    assert len(calls) == 2
    assert result["reconciliation_decision"] == "WARN"
    assert result["reconciliation_rechecked_after_self_heal"] is True
    assert result["initial_reconciliation_decision"] == "SELF_HEAL"
    assert result["initial_reconciliation_report_path"] == "first.json"


def test_main_pass_path_keeps_precompute_plan_and_submissions_aligned(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REPORT_DATE", "2026-03-26")
    monkeypatch.delenv("PRECOMPUTE_EXECUTE_EXACT_PLAN", raising=False)

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
    planned_payload = {
        "trade_date": "2026-03-26",
        "mode": "PAPER",
        "execution_status": "PLANNED",
        "pricing_source": "PREV_CLOSE",
        "pricing_asof": "2026-03-25",
        "trades_count": len(planned_trades),
        "trades": planned_trades,
    }
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
        "trading_mode": "PAPER",
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

    monkeypatch.setattr(
        live_exec,
        "_acquire_execution_lock",
        lambda trade_date, allow_existing=False: Path(tmp_path / f"{trade_date}.lock"),
    )
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
    monkeypatch.setattr(live_exec, "write_trade_stage_pointer", lambda **kwargs: None)
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
    assert observed_run_paper_day["force"] is False
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
    assert execution_payload["execution_source"] == "planned_payload_exact"
    assert execution_payload["exact_plan_enabled"] is True
    assert execution_payload["planned_payload_trade_count"] == 3
    assert execution_payload["planning_price_basis"] == "PREV_CLOSE"
    assert execution_payload["pricing_asof"] == "2026-03-25"
    assert execution_payload["execution_price_requirement"] == "PRECOMPUTE_VALIDATED"
    assert execution_payload["price_freshness_scope"] == "precompute_bundle"
    assert execution_results["submitted_count"] == 3
    assert execution_results["status"] == "EXECUTED"
    assert execution_results["exact_plan_enabled"] is True
    assert execution_results["planned_payload_trade_count"] == 3


def test_nonempty_planned_payload_zero_submitted_fails_with_drop_reason(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REPORT_DATE", "2026-06-19")
    monkeypatch.delenv("PRECOMPUTE_EXECUTE_EXACT_PLAN", raising=False)

    run_id = "2026-06-19T093506-0400_071a61a"
    run_root = tmp_path / "outputs" / "runs" / run_id
    planned_trades = [
        {"ticker": "AAPL", "side": "BUY", "shares": 1, "price": 100.0, "notional": 100.0},
        {"ticker": "MSFT", "side": "BUY", "shares": 1, "price": 200.0, "notional": 200.0},
        {"ticker": "GOOG", "side": "SELL", "shares": 1, "price": 150.0, "notional": 150.0},
    ]
    planned_payload = {
        "trade_date": "2026-06-19",
        "mode": "PAPER",
        "execution_status": "PLANNED",
        "pricing_source": "PREV_CLOSE",
        "pricing_asof": "2026-06-18",
        "trades_count": len(planned_trades),
        "trades": planned_trades,
    }
    daily_snapshot = {
        "holdings": [],
        "risk_levels": [],
        "proposed_trades": [dict(item) for item in planned_trades],
        "signals_snapshot_path": str(tmp_path / "signals.json"),
        "target_cash_weight": 0.05,
        "performance_diagnostics": {"current_equity": 10000.0},
    }
    paper_summary = {
        "trading_mode": "PAPER",
        "market_status": "OPEN",
        "execution_trades": [],
        "trade_plan": [],
        "execution_filter": {"raw": len(planned_trades), "kept": 0},
        "alpaca_submissions": [],
        "alpaca_submission_summary": {"submit_success": 0, "submit_failed": 0},
        "execution_outcome": None,
        "execution_reason": None,
        "cash_rebalance_status": None,
        "cash": 10000.0,
        "alpaca_positions_snapshot": [],
    }
    observed_run_paper_day: dict[str, object] = {}

    monkeypatch.setattr(live_exec, "_acquire_execution_lock", lambda _trade_date, allow_existing=False: None)
    monkeypatch.setattr(live_exec, "get_run_id", lambda: run_id)
    monkeypatch.setattr(live_exec, "get_run_dir", lambda _run_id: run_root)
    monkeypatch.setattr(
        live_exec,
        "current_et",
        lambda: dt.datetime(2026, 6, 19, 9, 35, 15, tzinfo=ZoneInfo("America/New_York")),
    )
    monkeypatch.setattr(
        live_exec,
        "classify_timing",
        lambda **kwargs: {
            "timing_status": "on_time",
            "preferred_target_et": "2026-06-19T09:35:00-04:00",
            "degraded_auto_trade_deadline_et": "2026-06-19T09:45:00-04:00",
            "actual_workflow_start_et": "2026-06-19T09:34:00-04:00",
            "actual_execution_start_et": "2026-06-19T09:35:15-04:00",
            "first_submit_et": "",
        },
    )
    monkeypatch.setattr(
        live_exec,
        "load_precompute_inputs",
        lambda **kwargs: (daily_snapshot, planned_payload, {"version": 1}, None),
    )
    monkeypatch.setattr(live_exec, "fetch_pretrade_snapshot", lambda: {"ok": True, "positions": []})
    monkeypatch.setattr(live_exec, "write_pretrade_snapshot_artifacts", lambda **kwargs: None)
    monkeypatch.setattr(live_exec, "summarize_pretrade_broker_policy", lambda snapshot: {})
    monkeypatch.setattr(live_exec, "ensure_sent_ledger_exists", lambda path: None)
    monkeypatch.setattr(
        live_exec,
        "ensure_paper_state_files",
        lambda: (str(tmp_path / "ledger.csv"), str(tmp_path / "trades.csv")),
    )
    monkeypatch.setattr(live_exec, "pre_trade_reconcile_and_classify", lambda **kwargs: {"reconciliation_decision": "PASS"})
    monkeypatch.setattr(live_exec, "_apply_pre_execution_risk_controls", lambda **kwargs: ("signals.json", 0.05))

    def _fake_run_paper_day(**kwargs):
        observed_run_paper_day.update(kwargs)
        return dict(paper_summary)

    monkeypatch.setattr(live_exec, "run_paper_day", _fake_run_paper_day)
    monkeypatch.setattr(
        live_exec.dqr,
        "build_execution_email_payload",
        lambda **kwargs: {
            "trade_date": "2026-06-19",
            "mode": "PAPER",
            "execution_status": "NO_ACTION",
            "halt_reason": None,
            "execution_outcome": None,
            "execution_reason": None,
            "trades": [],
            "planner_intended_trades_count": len(planned_trades),
            "execution_eligible_trades_count": 0,
            "executable_trades_count": 0,
            "submitted_count": 0,
            "accepted_count": 0,
            "rejected_count": 0,
            "orders_submitted_count": 0,
        },
    )
    monkeypatch.setattr(live_exec, "evaluate_live_retry", lambda **kwargs: {"retry_allowed": False, "retry_reason": ""})
    monkeypatch.setattr(live_exec, "write_planner_audit", lambda **kwargs: None)
    monkeypatch.setattr(live_exec, "write_operator_summary", lambda *args, **kwargs: None)
    monkeypatch.setattr(live_exec, "write_trading_day_summary", lambda **kwargs: None)
    monkeypatch.setattr(live_exec, "write_execution_artifacts", lambda **kwargs: None)
    monkeypatch.setattr(live_exec, "write_executor_audit", lambda **kwargs: None)
    monkeypatch.setattr(live_exec, "load_operator_summary", lambda run_root: {})
    monkeypatch.setattr(live_exec, "format_operator_summary_log", lambda summary: "")
    monkeypatch.setattr(live_exec, "format_execution_health_banner", lambda summary: "")
    monkeypatch.setattr(live_exec, "build_execution_email_text", lambda payload: ("subject", "body"))

    exit_code = live_exec.main([])

    payload = json.loads((run_root / "execution_payload.json").read_text(encoding="utf-8"))
    results = json.loads((run_root / "execution_results.json").read_text(encoding="utf-8"))
    reliability = json.loads(
        (run_root / "audit" / "execution_reliability_report_2026-06-19.json").read_text(
            encoding="utf-8"
        )
    )
    email_payload = json.loads((tmp_path / "outputs" / "execution_email" / "2026-06-19.json").read_text(encoding="utf-8"))

    assert exit_code == 1
    assert observed_run_paper_day["precomputed_trade_plan"] == planned_trades
    assert payload["execution_status"] == "HALTED"
    assert payload["operator_execution_status"] == "failed"
    assert payload["halt_reason"] == "planned_payload_trades_dropped_before_execution"
    assert email_payload["halt_reason"] == "planned_payload_trades_dropped_before_execution"
    assert results["status"] == "HALTED"
    assert results["halt_reason"] == "planned_payload_trades_dropped_before_execution"
    assert results["reason"] == "planned_payload_trades_dropped_before_execution"
    assert results["planned_payload_trade_count"] == len(planned_trades)
    assert results["executable_trades_count"] == 0
    assert results["submitted_count"] == 0
    assert results["exact_plan_enabled"] is True
    assert results["execution_source"] == "planned_payload_exact"
    assert results["execution_reliability_classification"] == "RELIABILITY_RED"
    assert results["execution_reliability_top_reason"] == "planned_payload_trades_dropped_before_execution"
    assert reliability["overall_status"] == "FAIL"
    assert reliability["classification"] == "RELIABILITY_RED"
    assert reliability["score"] < 100
    planned_invariant = next(
        item
        for item in reliability["invariant_results"]
        if item["invariant_id"] == "planned_payload_nonempty_zero_execution"
    )
    assert planned_invariant["reason_code"] == "planned_payload_trades_dropped_before_execution"


def test_main_stale_price_exception_finalizes_pointer_and_releases_lock(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REPORT_DATE", "2026-04-03")

    run_id = "run-stale-prices"
    run_root = tmp_path / "outputs" / "runs" / run_id
    signals_path = tmp_path / "signals.json"
    signals_path.write_text(
        json.dumps(
            {
                "snapshot_date": "2026-04-03",
                "cash_target_weight": 0.05,
                "meta": {},
                "signals": [],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    daily_snapshot = {
        "holdings": [],
        "risk_levels": [],
        "proposed_trades": [],
        "signals_snapshot_path": str(signals_path),
        "target_cash_weight": 0.05,
        "performance_diagnostics": {"current_equity": 9610.63},
    }
    planned_payload = {"trade_date": "2026-04-03", "mode": "PAPER", "trades": []}
    lock_path = tmp_path / "2026-04-03.lock"

    def _fake_acquire(_trade_date: str, allow_existing: bool = False) -> Path:
        del allow_existing
        lock_path.write_text("locked\n", encoding="utf-8")
        return lock_path

    monkeypatch.setattr(live_exec, "_acquire_execution_lock", _fake_acquire)
    monkeypatch.setattr(live_exec, "get_run_id", lambda: run_id)
    monkeypatch.setattr(live_exec, "get_run_dir", lambda _run_id: run_root)
    monkeypatch.setattr(
        live_exec,
        "current_et",
        lambda: dt.datetime(2026, 4, 3, 9, 35, 15, tzinfo=ZoneInfo("America/New_York")),
    )
    monkeypatch.setattr(
        live_exec,
        "classify_timing",
        lambda **kwargs: {
            "timing_status": "on_time",
            "preferred_target_et": "2026-04-03T09:35:00-04:00",
            "degraded_auto_trade_deadline_et": "2026-04-03T09:45:00-04:00",
            "actual_workflow_start_et": "2026-04-03T09:34:00-04:00",
            "actual_execution_start_et": "2026-04-03T09:35:15-04:00",
            "first_submit_et": "",
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
            "broker_preflight_status": "WARN",
            "broker_preflight_account_status": "ACTIVE",
            "broker_preflight_cash": 1200.47,
            "broker_preflight_equity": 9610.63,
            "broker_preflight_buying_power": 10811.1,
            "broker_preflight_restriction_flags": [],
            "broker_preflight_warning_flags": [],
            "broker_pdt_risk_status": "WARN",
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
        lambda **kwargs: (str(signals_path), 0.05),
    )
    monkeypatch.setattr(
        live_exec,
        "run_paper_day",
        lambda **kwargs: (_ for _ in ()).throw(
            RuntimeError("[HALT] stale_prices detected (last_price_date=2026-04-02)")
        ),
    )
    monkeypatch.setattr(live_exec, "write_pretrade_snapshot_artifacts", lambda **kwargs: None)
    monkeypatch.setattr(live_exec, "write_operator_summary", lambda *args, **kwargs: None)
    monkeypatch.setattr(live_exec, "write_trading_day_summary", lambda **kwargs: None)

    exit_code = live_exec.main([])

    pointer = json.loads(
        (tmp_path / "outputs" / "workflow" / "2026-04-03" / "execution.json").read_text(encoding="utf-8")
    )
    payload = json.loads((run_root / "execution_payload.json").read_text(encoding="utf-8"))
    email_payload = json.loads(
        (tmp_path / "outputs" / "execution_email" / "2026-04-03.json").read_text(encoding="utf-8")
    )

    assert exit_code == 1
    assert not lock_path.exists()
    assert pointer["status"] == "failed_pre_execution"
    assert payload["execution_status"] == "HALTED"
    assert "stale_prices" in str(payload["halt_reason"])
    assert email_payload["execution_status"] == "HALTED"


def test_exact_precompute_fails_closed_on_malformed_planned_payload(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REPORT_DATE", "2026-03-26")
    monkeypatch.setenv("PRECOMPUTE_EXECUTE_EXACT_PLAN", "1")

    run_id = "run-bad-plan"
    run_root = tmp_path / "outputs" / "runs" / run_id
    planned_payload = {
        "trade_date": "2026-03-26",
        "mode": "PAPER",
        "execution_status": "READY",
        "pricing_source": "PREV_CLOSE",
        "pricing_asof": "2026-03-25",
        "trades_count": 1,
        "trades": [{"ticker": "AAPL", "side": "BUY", "shares": 1, "price": 100.0}],
    }
    daily_snapshot = {
        "holdings": [],
        "risk_levels": [],
        "proposed_trades": [],
        "signals_snapshot_path": str(tmp_path / "signals.json"),
        "target_cash_weight": 0.05,
        "performance_diagnostics": {"current_equity": 10000.0},
    }

    monkeypatch.setattr(live_exec, "_acquire_execution_lock", lambda _trade_date, allow_existing=False: None)
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
            "first_submit_et": "",
        },
    )
    monkeypatch.setattr(
        live_exec,
        "load_precompute_inputs",
        lambda **kwargs: (daily_snapshot, planned_payload, {"version": 1}, None),
    )
    monkeypatch.setattr(live_exec, "fetch_pretrade_snapshot", lambda: {"ok": True, "positions": []})
    monkeypatch.setattr(live_exec, "write_pretrade_snapshot_artifacts", lambda **kwargs: None)
    monkeypatch.setattr(live_exec, "summarize_pretrade_broker_policy", lambda snapshot: {})
    monkeypatch.setattr(live_exec, "ensure_sent_ledger_exists", lambda path: None)
    monkeypatch.setattr(live_exec, "ensure_paper_state_files", lambda: (str(tmp_path / "ledger.csv"), str(tmp_path / "trades.csv")))
    monkeypatch.setattr(live_exec, "pre_trade_reconcile_and_classify", lambda **kwargs: {"reconciliation_decision": "PASS"})
    monkeypatch.setattr(live_exec, "run_paper_day", lambda **kwargs: (_ for _ in ()).throw(AssertionError("run_paper_day should not be called")))
    monkeypatch.setattr(live_exec, "write_operator_summary", lambda *args, **kwargs: None)
    monkeypatch.setattr(live_exec, "write_trading_day_summary", lambda **kwargs: None)

    exit_code = live_exec.main([])

    payload = json.loads((run_root / "execution_payload.json").read_text(encoding="utf-8"))
    assert exit_code == 1
    assert payload["execution_status"] == "HALTED"
    assert "planned_execution_payload_status_not_planned" in payload["halt_reason"]

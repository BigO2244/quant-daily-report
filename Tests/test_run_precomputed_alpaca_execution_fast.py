from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from zoneinfo import ZoneInfo


def _module(name: str, **attrs) -> ModuleType:
    mod = ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


def _load_module(tmp_path: Path):
    stub_module_names = (
        "pandas",
        "brokers",
        "brokers.alpaca_broker",
        "brokers.alpaca_snapshot",
        "core",
        "core.execution_audit",
        "core.execution_integrity",
        "core.execution_lifecycle_timeline",
        "core.execution_payload",
        "core.execution_summary",
        "core.live_retry_policy",
        "core.operator_summary",
        "core.precompute_contract",
        "core.run_pointer",
        "core.security_master",
        "core.timing_policy",
        "core.trading_mode",
        "core.trading_day_summary",
        "paper",
        "paper.build_execution_email",
        "paper.paper_broker",
        "paper.run_manager",
        "paper.state_paths",
        "reconciliation",
        "daily_quant_report",
    )
    missing = object()
    original_modules = {
        name: sys.modules.get(name, missing)
        for name in stub_module_names
    }

    def _safe_write_text(path, text, allow_overwrite=True):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and not allow_overwrite:
            raise FileExistsError(path)
        path.write_text(text, encoding="utf-8")
        return path

    def _write_canonical_execution_payload(payload, trade_date, run_root, allow_overwrite=True):
        out_path = Path(run_root) / "execution_payload.json"
        _safe_write_text(out_path, json.dumps(payload, indent=2) + "\n", allow_overwrite=allow_overwrite)
        return str(out_path)

    sys.modules["pandas"] = _module("pandas", read_csv=lambda *_args, **_kwargs: None)
    sys.modules["brokers"] = _module("brokers")
    sys.modules["brokers.alpaca_broker"] = _module(
        "brokers.alpaca_broker",
        CASH_REBALANCE_INCOMPLETE="cash_rebalance_incomplete",
        EXECUTION_OUTCOME_PARTIAL_BROKER_ABORT="partial_broker_abort",
        EXECUTION_OUTCOME_POST_SUBMIT_ARTIFACT_FAILURE="post_submit_artifact_failure",
    )
    sys.modules["brokers.alpaca_snapshot"] = _module(
        "brokers.alpaca_snapshot",
        fetch_pretrade_snapshot=lambda: {"ok": True, "positions": []},
        summarize_pretrade_broker_policy=lambda _snapshot: {
            "broker_preflight_status": "WARN",
            "broker_preflight_account_status": "ACTIVE",
            "broker_preflight_cash": 3991.70,
            "broker_preflight_equity": 9631.53,
            "broker_preflight_buying_power": 7983.40,
            "broker_preflight_restriction_flags": [],
            "broker_preflight_warning_flags": [],
            "broker_pdt_risk_status": "OK",
            "broker_pdt_daytrade_count": 0,
            "broker_pdt_daytrading_buying_power": 0.0,
            "broker_pdt_flags": [],
            "broker_pdt_warning_message": "",
            "pdt_constrained": False,
        },
        write_pretrade_snapshot_artifacts=lambda **_kwargs: None,
    )
    sys.modules["core"] = _module("core", __path__=[])
    sys.modules["core.execution_audit"] = _module(
        "core.execution_audit",
        write_executor_audit=lambda **_kwargs: None,
        write_planner_audit=lambda **_kwargs: None,
    )

    def _write_execution_integrity_audit(**kwargs):
        out_path = Path(kwargs["run_root"]) / "audit" / "execution_integrity.json"
        _safe_write_text(
            out_path,
            json.dumps({"status": "OK", "findings": []}, indent=2) + "\n",
            allow_overwrite=True,
        )
        return out_path

    sys.modules["core.execution_integrity"] = _module(
        "core.execution_integrity",
        write_execution_integrity_audit=_write_execution_integrity_audit,
    )
    sys.modules["core.execution_lifecycle_timeline"] = _module(
        "core.execution_lifecycle_timeline",
        write_execution_lifecycle_timeline=lambda **_kwargs: None,
    )
    def _compute_final_execution_status(**kwargs):
        # Faithful, dependency-free mirror of
        # core.execution_payload.compute_final_execution_status. Note the stubbed
        # EXECUTION_OUTCOME_PARTIAL_BROKER_ABORT value is "partial_broker_abort".
        raw_status = str(kwargs.get("raw_execution_status") or "").strip().upper()
        raw_operator = str(kwargs.get("raw_operator_execution_status") or "").strip().lower()
        outcome = str(kwargs.get("execution_outcome") or "").strip()
        recon = str(kwargs.get("posttrade_recon_status") or "").strip().upper()
        raw_reason = kwargs.get("raw_execution_reason") or None

        def _i(value):
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0

        result = {
            "raw_execution_status": raw_status or None,
            "raw_operator_execution_status": raw_operator or None,
            "raw_execution_reason": raw_reason,
            "final_execution_status": raw_status or None,
            "final_operator_execution_status": raw_operator or None,
            "final_execution_reason": raw_reason,
            "reconciled_to_target_state": False,
            "reconciliation_override_applied": False,
        }
        if raw_operator == "partial" and outcome in {
            "partial_broker_abort",
            "post_submit_artifact_failure",
        }:
            if (
                recon in {"OK_RECONCILED", "OK"}
                and _i(kwargs.get("posttrade_unresolved_orders_count")) == 0
                and _i(kwargs.get("skipped_buy_count")) == 0
                and _i(kwargs.get("blocked_buy_count")) == 0
                and _i(kwargs.get("pending_buy_count")) == 0
                and _i(kwargs.get("rejected_count")) == 0
                and not str(kwargs.get("broker_reject_status") or "").strip()
                and _i(kwargs.get("submitted_count")) > 0
            ):
                result.update(
                    {
                        "final_execution_status": "RECONCILED_SUCCESS",
                        "final_operator_execution_status": "reconciled_success",
                        "final_execution_reason": "raw_partial_reconciled_to_target_state",
                        "reconciled_to_target_state": True,
                        "reconciliation_override_applied": True,
                    }
                )
        return result

    sys.modules["core.execution_payload"] = _module(
        "core.execution_payload",
        normalize_status=lambda **_kwargs: "HALTED",
        write_canonical_execution_payload=_write_canonical_execution_payload,
        compute_final_execution_status=_compute_final_execution_status,
    )
    sys.modules["core.execution_summary"] = _module(
        "core.execution_summary",
        write_execution_artifacts=lambda **_kwargs: None,
    )
    sys.modules["core.live_retry_policy"] = _module(
        "core.live_retry_policy",
        evaluate_live_retry=lambda **_kwargs: {
            "retry_allowed": True,
            "retry_reason": "precompute_reconciliation_self_heal",
        },
    )
    sys.modules["core.operator_summary"] = _module(
        "core.operator_summary",
        format_execution_health_banner=lambda _summary: "",
        format_operator_summary_log=lambda _summary: "",
        load_operator_summary=lambda _run_root: {},
        write_operator_summary=lambda *_args, **_kwargs: None,
    )
    sys.modules["core.precompute_contract"] = _module(
        "core.precompute_contract",
        load_precompute_inputs=lambda **_kwargs: (
            {"proposed_trades": [], "holdings": [], "performance_diagnostics": {}},
            {"trade_date": "2026-03-26", "mode": "ALPACA", "trades": []},
            {"version": 1},
            None,
        ),
    )
    sys.modules["core.run_pointer"] = _module(
        "core.run_pointer",
        write_latest_run_pointer=lambda **_kwargs: None,
        write_trade_stage_pointer=lambda **_kwargs: None,
    )
    sys.modules["core.security_master"] = _module(
        "core.security_master",
        resolve_trade_plan_symbols=lambda trades, **_kwargs: _module(
            "SymbolResolutionResult",
            trades=[dict(trade) for trade in trades],
            status="PASS",
            reason="all_symbols_resolved",
        ),
    )
    sys.modules["core.timing_policy"] = _module(
        "core.timing_policy",
        classify_timing=lambda **_kwargs: {
            "timing_status": "on_time",
            "preferred_target_et": "2026-03-26T09:35:00-04:00",
            "degraded_auto_trade_deadline_et": "2026-03-26T13:00:00-04:00",
            "actual_workflow_start_et": "2026-03-26T09:35:00-04:00",
            "actual_execution_start_et": "2026-03-26T09:35:15-04:00",
            "first_submit_et": None,
        },
        current_et=lambda: dt.datetime(2026, 3, 26, 9, 35, 15, tzinfo=ZoneInfo("America/New_York")),
    )
    sys.modules["core.trading_mode"] = _module(
        "core.trading_mode",
        canonical_trading_mode_label=lambda value, field_name=None: str(value or field_name or "paper").upper(),
    )
    sys.modules["core.trading_day_summary"] = _module(
        "core.trading_day_summary",
        write_trading_day_summary=lambda **_kwargs: None,
    )
    sys.modules["paper"] = _module("paper")
    sys.modules["paper.build_execution_email"] = _module(
        "paper.build_execution_email",
        build_execution_email_text=lambda _payload: ("subject", "body"),
    )
    sys.modules["paper.paper_broker"] = _module(
        "paper.paper_broker",
        run_paper_day=lambda **_kwargs: {},
    )
    sys.modules["paper.run_manager"] = _module(
        "paper.run_manager",
        ensure_dir=lambda path: Path(path).mkdir(parents=True, exist_ok=True),
        get_run_dir=lambda run_id: tmp_path / "outputs" / "runs" / run_id,
        get_run_id=lambda: "run-self-heal",
        safe_write_text=_safe_write_text,
    )
    sys.modules["paper.state_paths"] = _module(
        "paper.state_paths",
        ensure_paper_state_files=lambda: (str(tmp_path / "ledger.csv"), str(tmp_path / "trades.csv")),
    )
    sys.modules["reconciliation"] = _module(
        "reconciliation",
        ensure_sent_ledger_exists=lambda _path: None,
        pre_trade_reconcile_and_classify=lambda **_kwargs: {
            "reconciliation_decision": "SELF_HEAL",
            "report_path": str(tmp_path / "recon.json"),
        },
    )
    sys.modules["daily_quant_report"] = _module(
        "daily_quant_report",
        format_broker_preflight_banner=lambda _policy: "",
        build_execution_email_payload=lambda **_kwargs: {},
    )

    module_path = Path(__file__).resolve().parents[1] / "scripts" / "run_precomputed_alpaca_execution.py"
    spec = importlib.util.spec_from_file_location("live_exec_fast_test", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    for name, original in original_modules.items():
        if original is missing:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = original
    return module


def test_warn_reconciliation_does_not_halt(tmp_path) -> None:
    live_exec = _load_module(tmp_path)
    assert live_exec._precompute_reconciliation_halt_reason({"reconciliation_decision": "WARN"}) is None


def test_nonempty_planned_payload_enables_exact_plan_by_default(tmp_path, monkeypatch) -> None:
    live_exec = _load_module(tmp_path)
    planned_payload = {
        "trade_date": "2026-06-19",
        "execution_status": "PLANNED",
        "trades": [{"ticker": "AAPL", "side": "BUY", "shares": 1, "price": 100.0}],
    }

    monkeypatch.delenv("PRECOMPUTE_EXECUTE_EXACT_PLAN", raising=False)
    assert live_exec._exact_plan_enabled_for_payload(planned_payload) is True

    monkeypatch.setenv("PRECOMPUTE_EXECUTE_EXACT_PLAN", "1")
    assert live_exec._exact_plan_enabled_for_payload(planned_payload) is True


def test_exact_plan_has_named_explicit_opt_out_and_empty_plan_no_action_path(
    tmp_path,
    monkeypatch,
) -> None:
    live_exec = _load_module(tmp_path)
    planned_payload = {
        "trade_date": "2026-06-19",
        "execution_status": "PLANNED",
        "trades": [{"ticker": "AAPL", "side": "BUY", "shares": 1, "price": 100.0}],
    }

    monkeypatch.setenv("PRECOMPUTE_EXECUTE_EXACT_PLAN", "0")
    assert live_exec._exact_plan_enabled_for_payload(planned_payload) is False

    monkeypatch.delenv("PRECOMPUTE_EXECUTE_EXACT_PLAN", raising=False)
    assert live_exec._exact_plan_enabled_for_payload({"trade_date": "2026-06-19", "trades": []}) is False


def test_cash_gate_diagnostics_mark_raw_gate_reconciled_success(tmp_path) -> None:
    live_exec = _load_module(tmp_path)

    diagnostics = live_exec._finalize_cash_gate_diagnostics(
        {
            "schema_version": "cash_gate_diagnostics.v1",
            "raw_cash_gate_triggered": True,
            "temporary_cash_shortfall_later_resolved": False,
        },
        {
            "raw_execution_status": "HALTED",
            "raw_operator_execution_status": "partial",
            "raw_execution_reason": (
                "partial_execution_broker_abort:"
                "buy_blocked_pending_sells_required_for_cash:"
                "cash_rebalance_incomplete"
            ),
            "final_execution_status": "RECONCILED_SUCCESS",
            "final_operator_execution_status": "reconciled_success",
            "final_execution_reason": "raw_partial_reconciled_to_target_state",
            "reconciled_to_target_state": True,
            "reconciliation_override_applied": True,
        },
    )

    assert diagnostics["raw_cash_gate_despite_final_reconciled_success"] is True
    assert diagnostics["temporary_cash_shortfall_later_resolved"] is True
    assert diagnostics["raw_execution_reason"].endswith("cash_rebalance_incomplete")


def test_equality_gate_observer_writes_artifacts_and_operator_summary(tmp_path, monkeypatch) -> None:
    live_exec = _load_module(tmp_path)
    from core import operator_summary

    monkeypatch.setattr(live_exec, "write_operator_summary", operator_summary.write_operator_summary)
    run_root = tmp_path / "outputs" / "runs" / "run-eq"
    observer = live_exec._make_equality_gate_observer(
        run_root=run_root,
        run_id="run-eq",
        trade_date="2026-05-28",
        planned_payload={
            "trade_date": "2026-05-28",
            "pricing_source": "PREV_CLOSE",
            "pricing_asof": "2026-05-27",
            "trades": [{"ticker": "AAPL", "side": "BUY", "shares": "1.0", "entry_price": 200.0}],
        },
        provenance={
            "execution_source": "planned_payload_exact",
            "planning_price_basis": "PREV_CLOSE",
            "pricing_asof": "2026-05-27",
        },
    )

    assert observer is not None
    submission_orders = [{"ticker": "AAPL", "side": "BUY", "quantity": 1, "order_type": "MKT"}]
    before = [dict(item) for item in submission_orders]
    observer(submission_orders)

    artifact = json.loads((run_root / "equality_gate.json").read_text(encoding="utf-8"))
    summary = json.loads((run_root / "operator_summary.json").read_text(encoding="utf-8"))
    assert submission_orders == before
    assert artifact["decision"] == "WOULD_PROCEED"
    assert artifact["submission_proceeded"] is True
    assert summary["equality_gate_observe"]["decision"] == "WOULD_PROCEED"


def test_equality_gate_observer_records_observe_error_without_raising(tmp_path, monkeypatch) -> None:
    live_exec = _load_module(tmp_path)
    from core import execution_equality_gate, operator_summary

    def _raise(*_args, **_kwargs):
        raise RuntimeError("forced observe failure")

    monkeypatch.setattr(live_exec, "write_operator_summary", operator_summary.write_operator_summary)
    monkeypatch.setattr(execution_equality_gate, "write_equality_gate_observe_artifacts", _raise)
    run_root = tmp_path / "outputs" / "runs" / "run-eq-error"
    observer = live_exec._make_equality_gate_observer(
        run_root=run_root,
        run_id="run-eq-error",
        trade_date="2026-05-28",
        planned_payload={
            "trade_date": "2026-05-28",
            "pricing_source": "PREV_CLOSE",
            "pricing_asof": "2026-05-27",
            "trades": [{"ticker": "AAPL", "side": "BUY", "shares": 1}],
        },
        provenance={
            "execution_source": "planned_payload_exact",
            "planning_price_basis": "PREV_CLOSE",
            "pricing_asof": "2026-05-27",
        },
    )

    assert observer is not None
    observer([{"ticker": "AAPL", "side": "BUY", "quantity": 1}])

    artifact = json.loads((run_root / "equality_gate.json").read_text(encoding="utf-8"))
    assert artifact["decision"] == "OBSERVE_ERROR"
    assert artifact["observe_error"]["message"] == "forced observe failure"


def test_pretrade_self_heal_releases_same_day_lock(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REPORT_DATE", "2026-03-26")
    live_exec = _load_module(tmp_path)

    lock_path = tmp_path / "2026-03-26.lock"

    def _fake_acquire(_trade_date: str, allow_existing: bool = False) -> Path:
        del allow_existing
        lock_path.write_text("locked\n", encoding="utf-8")
        return lock_path

    live_exec._acquire_execution_lock = _fake_acquire

    exit_code = live_exec.main([])

    assert exit_code == 1
    assert not lock_path.exists()
    payload = json.loads(
        (tmp_path / "outputs" / "runs" / "run-self-heal" / "execution_payload.json").read_text(encoding="utf-8")
    )
    assert payload["halt_reason"] == "precompute_reconciliation_self_heal"


def test_pending_capital_allowed_buy_leg_marks_partial_and_continuable(tmp_path) -> None:
    live_exec = _load_module(tmp_path)
    paper_summary = {
        "alpaca_submissions": [
            {"ticker": "CVS", "side": "SELL", "quantity": 1, "order_id": "run:CVS:SELL"},
            {"ticker": "GOOG", "side": "SELL", "quantity": 1, "order_id": "run:GOOG:SELL"},
        ],
        "alpaca_submission_summary": {
            "submit_success": 2,
            "submit_failed": 0,
            "sell_phase_submitted": 2,
            "buy_phase_submitted": 0,
            "buy_phase_planned": 0,
            "buy_phase_block_reason": "sell_phase_timeout",
        },
        "sell_phase_status": "TIMEOUT",
        "sell_phase_completion_reason": "poll_timeout",
        "budget_skipped_orders": [
            {"ticker": "ELV", "side": "BUY", "quantity": 1, "notional": 250.0, "order_id": "run:ELV:BUY"},
            {"ticker": "SLB", "side": "BUY", "quantity": 2, "notional": 300.0, "order_id": "run:SLB:BUY"},
        ],
        "capital_budget": {
            "requested_buy_notional": 550.0,
            "allowed_buy_notional": 550.0,
            "capital_constraint_triggered": False,
            "clipped_or_deferred_buys_count": 0,
        },
        "blocked_reasons": [],
    }

    state = live_exec._apply_pending_buy_leg_guard(paper_summary)

    assert state["pending_buy_count"] == 2
    assert state["capital_allows_pending_buys"] is True
    assert paper_summary["execution_outcome"] == "partial_broker_abort"
    assert paper_summary["execution_reason"] == "sell_phase_timeout"
    assert paper_summary["cash_rebalance_status"] == "cash_rebalance_incomplete"
    assert live_exec._pending_buy_continuation_eligible(
        paper_summary=paper_summary,
        pending_buy_state=state,
        submitted_count=2,
        timing={"timing_status": "on_time"},
    ) is True
    assert live_exec._operator_execution_status(
        {
            "submitted_count": 2,
            "execution_outcome": paper_summary["execution_outcome"],
            "execution_status": "HALTED",
        }
    ) == "partial"


def test_pending_buy_leg_with_broker_reject_is_not_continuable(tmp_path) -> None:
    live_exec = _load_module(tmp_path)
    paper_summary = {
        "alpaca_submissions": [
            {"ticker": "CVS", "side": "SELL", "quantity": 1, "order_id": "run:CVS:SELL"},
        ],
        "alpaca_submission_summary": {
            "submit_success": 1,
            "buy_phase_submitted": 0,
            "buy_phase_block_reason": "broker_reject_pdt",
        },
        "broker_reject_status": "BROKER_REJECT_PDT",
        "budget_skipped_orders": [
            {"ticker": "ELV", "side": "BUY", "quantity": 1, "notional": 250.0, "order_id": "run:ELV:BUY"},
        ],
        "capital_budget": {
            "requested_buy_notional": 250.0,
            "allowed_buy_notional": 250.0,
            "capital_constraint_triggered": False,
            "clipped_or_deferred_buys_count": 0,
        },
    }

    state = live_exec._apply_pending_buy_leg_guard(paper_summary)

    assert state["pending_buy_count"] == 1
    assert state["has_broker_reject"] is True
    assert "execution_outcome" not in paper_summary
    assert live_exec._pending_buy_continuation_eligible(
        paper_summary=paper_summary,
        pending_buy_state=state,
        submitted_count=1,
        timing={"timing_status": "on_time"},
    ) is False


def test_pretrade_asset_validation_halt_is_not_marked_partial(tmp_path) -> None:
    live_exec = _load_module(tmp_path)
    paper_summary = {
        "alpaca_submissions": [],
        "alpaca_submission_summary": {
            "submit_success": 0,
            "buy_phase_submitted": 0,
            "buy_phase_block_reason": "buy_blocked_asset_validation_failed",
        },
        "asset_validation_status": "FAIL",
        "invalid_symbols": ["BK"],
        "budget_skipped_orders": [
            {"ticker": "BK", "side": "BUY", "quantity": 1, "notional": 50.0, "order_id": "run:BK:BUY"},
        ],
        "capital_budget": {
            "requested_buy_notional": 50.0,
            "allowed_buy_notional": 50.0,
            "capital_constraint_triggered": False,
            "clipped_or_deferred_buys_count": 0,
        },
    }

    state = live_exec._apply_pending_buy_leg_guard(paper_summary)

    assert state["pending_buy_count"] == 1
    assert state["requires_partial"] is False
    assert "execution_outcome" not in paper_summary


def test_pending_buy_leg_with_cash_shortfall_is_partial_but_not_continuable(tmp_path) -> None:
    live_exec = _load_module(tmp_path)
    paper_summary = {
        "alpaca_submissions": [
            {"ticker": "CVS", "side": "SELL", "quantity": 1, "order_id": "run:CVS:SELL"},
        ],
        "alpaca_submission_summary": {
            "submit_success": 1,
            "buy_phase_submitted": 0,
            "buy_phase_block_reason": "post_sell_cash_below_reserve",
        },
        "budget_skipped_orders": [
            {"ticker": "ELV", "side": "BUY", "quantity": 1, "notional": 250.0, "order_id": "run:ELV:BUY"},
        ],
        "capital_budget": {
            "requested_buy_notional": 250.0,
            "allowed_buy_notional": 250.0,
            "capital_constraint_triggered": False,
            "clipped_or_deferred_buys_count": 0,
        },
    }

    state = live_exec._apply_pending_buy_leg_guard(paper_summary)

    assert state["requires_partial"] is True
    assert paper_summary["execution_outcome"] == "partial_broker_abort"
    assert live_exec._pending_buy_continuation_eligible(
        paper_summary=paper_summary,
        pending_buy_state=state,
        submitted_count=1,
        timing={"timing_status": "on_time"},
    ) is False


def test_buy_continuation_plan_hydrates_only_buys_from_intended_orders(tmp_path) -> None:
    live_exec = _load_module(tmp_path)
    intended_path = tmp_path / "intended_orders_2026-05-27.json"
    intended_path.write_text(
        json.dumps(
            {
                "orders_intended": [
                    {"ticker": "CVS", "side": "SELL", "shares": 5, "notional": 464.13},
                    {"ticker": "ELV", "side": "BUY", "shares": 1, "notional": 389.88, "reason": "rebalance_to_target"},
                    {"ticker": "SLB", "side": "BUY", "shares": 14, "notional": 802.80, "reason": "rebalance_to_target"},
                ]
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    plan = live_exec._load_buy_continuation_plan_from_intended_orders(intended_path)

    assert [row["ticker"] for row in plan] == ["ELV", "SLB"]
    assert {row["side"] for row in plan} == {"BUY"}
    assert plan[0]["shares"] == 1.0
    assert round(float(plan[0]["price"]), 2) == 389.88


def test_buy_observation_contract_blocks_ok_recon_when_fields_missing(tmp_path) -> None:
    live_exec = _load_module(tmp_path)
    payload = {
        "submitted_buy_count": 2,
        "pending_buy_count": 0,
        "posttrade_recon_status": "OK_RECONCILED",
    }
    paper_summary = {
        "posttrade_recon_status": "OK_RECONCILED",
        "posttrade_recon_path": "broker/recon_posttrade_2026-06-16.json",
        "posttrade_account_snapshot_path": "broker/posttrade_account_snapshot.json",
    }

    live_exec._enforce_buy_observation_contract(payload, paper_summary)

    assert payload["buy_phase_status"] == "BUY_STATUS_UNKNOWN"
    assert payload["buy_phase_completion_reason"] == "buy_fill_observation_missing"
    assert payload["buy_fill_poll_count"] == 0
    assert payload["buy_fill_observation_window_seconds"] == 0.0
    assert payload["filled_buy_count"] == 0
    assert payload["pending_buy_count"] == 2
    assert payload["posttrade_recon_status"] == "NOT_COMPARABLE"
    assert payload["posttrade_unresolved_orders_count"] == 2
    assert paper_summary["posttrade_recon_status"] == "NOT_COMPARABLE"


def test_main_pending_buy_leg_does_not_report_clean_success(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REPORT_DATE", "2026-05-27")
    monkeypatch.setenv("PRECOMPUTE_EXECUTE_EXACT_PLAN", "1")
    live_exec = _load_module(tmp_path)

    planned_trades = [
        {"ticker": "CVS", "side": "SELL", "shares": 1, "price": 60.0, "notional": 60.0, "order_id": "run:CVS:SELL"},
        {"ticker": "ELV", "side": "BUY", "shares": 1, "price": 250.0, "notional": 250.0, "order_id": "run:ELV:BUY"},
        {"ticker": "SLB", "side": "BUY", "shares": 2, "price": 150.0, "notional": 300.0, "order_id": "run:SLB:BUY"},
    ]
    snapshot = {
        "holdings": [{"ticker": "CVS", "shares": 1, "last_price": 60.0}],
        "risk_levels": [],
        "proposed_trades": [dict(item) for item in planned_trades],
        "performance_diagnostics": {"current_equity": 10000.0},
        "target_cash_weight": 0.05,
    }
    planned_payload = {
        "trade_date": "2026-05-27",
        "mode": "PAPER",
        "execution_status": "PLANNED",
        "pricing_source": "PREV_CLOSE",
        "pricing_asof": "2026-05-26",
        "trades_count": len(planned_trades),
        "trades": planned_trades,
    }
    paper_summary = {
        "trading_mode": "PAPER",
        "market_status": "OPEN",
        "planned_for": "2026-05-27T09:35:00-04:00",
        "execution_trades": [dict(item) for item in planned_trades],
        "trade_plan": [dict(item) for item in planned_trades],
        "alpaca_submissions": [
            {"ticker": "CVS", "side": "SELL", "quantity": 1, "order_id": "run:CVS:SELL", "submitted_at": "2026-05-27T09:35:31-04:00"},
        ],
        "alpaca_submission_summary": {
            "submit_success": 1,
            "submit_failed": 0,
            "sell_phase_submitted": 1,
            "buy_phase_submitted": 0,
            "buy_phase_planned": 0,
            "buy_phase_block_reason": "sell_phase_timeout",
        },
        "sell_phase_status": "TIMEOUT",
        "sell_phase_completion_reason": "poll_timeout",
        "budget_skipped_orders": [dict(item) for item in planned_trades if item["side"] == "BUY"],
        "capital_budget": {
            "requested_buy_notional": 550.0,
            "allowed_buy_notional": 550.0,
            "capital_constraint_triggered": False,
            "clipped_or_deferred_buys_count": 0,
        },
        "cash": 3823.0,
        "target_cash_weight": 0.05,
        "achieved_cash_weight": 0.3823,
        "execution_submitted_symbols": ["CVS"],
        "alpaca_positions_snapshot": [],
    }

    def _build_payload(*, trade_date, daily_snapshot, paper_summary):
        return {
            "trade_date": trade_date,
            "mode": "PAPER",
            "execution_status": "HALTED" if paper_summary.get("execution_outcome") else "READY",
            "halt_reason": paper_summary.get("halt_reason"),
            "execution_outcome": paper_summary.get("execution_outcome"),
            "execution_reason": paper_summary.get("execution_reason"),
            "cash_rebalance_status": paper_summary.get("cash_rebalance_status"),
            "trades": [
                {"ticker": "CVS", "side": "SELL", "shares": 1, "order_id": "run:CVS:SELL"},
            ],
            "planner_intended_trades_count": 3,
            "execution_eligible_trades_count": 1,
            "orders_submitted_count": 1,
            "submitted_count": 1,
            "accepted_count": 1,
            "rejected_count": 0,
            "target_cash_weight": paper_summary.get("target_cash_weight"),
            "achieved_cash_weight": paper_summary.get("achieved_cash_weight"),
        }

    monkeypatch.setattr(live_exec, "load_precompute_inputs", lambda **_kwargs: (snapshot, planned_payload, {"version": 1}, None))
    monkeypatch.setattr(live_exec, "pre_trade_reconcile_and_classify", lambda **_kwargs: {"reconciliation_decision": "PASS"})
    monkeypatch.setattr(live_exec, "_apply_pre_execution_risk_controls", lambda **_kwargs: ("signals.json", 0.05))
    monkeypatch.setattr(live_exec, "run_paper_day", lambda **_kwargs: dict(paper_summary))
    monkeypatch.setattr(live_exec.dqr, "build_execution_email_payload", _build_payload)
    monkeypatch.setattr(live_exec, "evaluate_live_retry", lambda **_kwargs: {"retry_allowed": False, "retry_reason": ""})
    monkeypatch.setattr(live_exec, "_acquire_execution_lock", lambda _trade_date, allow_existing=False: tmp_path / "execution.lock")

    exit_code = live_exec.main([])

    payload = json.loads(
        (tmp_path / "outputs" / "runs" / "run-self-heal" / "execution_payload.json").read_text(encoding="utf-8")
    )
    assert exit_code == 1
    assert payload["operator_execution_status"] == "partial"
    assert payload["execution_outcome"] == "partial_broker_abort"
    assert payload["execution_reason"] == "sell_phase_timeout"
    assert payload["pending_buy_count"] == 2
    assert payload["continuation_eligible"] is True
    assert [order["ticker"] for order in payload["pending_buy_orders"]] == ["ELV", "SLB"]
    assert payload["submitted_sell_count"] == 1
    assert payload["submitted_buy_count"] == 0


def test_buy_only_continuation_filters_precompute_plan_to_buys(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REPORT_DATE", "2026-05-27")
    monkeypatch.delenv("PRECOMPUTE_EXECUTE_EXACT_PLAN", raising=False)
    live_exec = _load_module(tmp_path)

    planned_trades = [
        {"ticker": "CVS", "side": "SELL", "shares": 1, "price": 60.0, "notional": 60.0, "order_id": "run:CVS:SELL"},
        {"ticker": "ELV", "side": "BUY", "shares": 1, "price": 250.0, "notional": 250.0, "order_id": "run:ELV:BUY"},
        {"ticker": "SLB", "side": "BUY", "shares": 2, "price": 150.0, "notional": 300.0, "order_id": "run:SLB:BUY"},
    ]
    snapshot = {
        "holdings": [{"ticker": "CVS", "shares": 1, "last_price": 60.0}],
        "risk_levels": [],
        "proposed_trades": [dict(item) for item in planned_trades],
        "performance_diagnostics": {"current_equity": 10000.0},
        "target_cash_weight": 0.05,
    }
    planned_payload = {"trade_date": "2026-05-27", "mode": "PAPER", "trades": planned_trades}
    observed_run_paper_day: dict[str, object] = {}
    paper_summary = {
        "trading_mode": "PAPER",
        "market_status": "OPEN",
        "planned_for": "2026-05-27T09:35:00-04:00",
        "execution_trades": [dict(item) for item in planned_trades if item["side"] == "BUY"],
        "trade_plan": [dict(item) for item in planned_trades if item["side"] == "BUY"],
        "alpaca_submissions": [
            {"ticker": "ELV", "side": "BUY", "quantity": 1, "order_id": "run:ELV:BUY", "submitted_at": "2026-05-27T10:05:00-04:00"},
            {"ticker": "SLB", "side": "BUY", "quantity": 2, "order_id": "run:SLB:BUY", "submitted_at": "2026-05-27T10:05:00-04:00"},
        ],
        "alpaca_submission_summary": {
            "submit_success": 2,
            "submit_failed": 0,
            "sell_phase_submitted": 0,
            "buy_phase_submitted": 2,
            "buy_phase_planned": 2,
        },
        "budget_skipped_orders": [],
        "capital_budget": {
            "requested_buy_notional": 550.0,
            "allowed_buy_notional": 550.0,
            "capital_constraint_triggered": False,
            "clipped_or_deferred_buys_count": 0,
        },
        "cash": 3273.0,
        "execution_outcome": None,
        "execution_reason": None,
        "cash_rebalance_status": "complete",
        "execution_submitted_symbols": ["ELV", "SLB"],
        "alpaca_positions_snapshot": [],
    }

    def _fake_run_paper_day(**kwargs):
        observed_run_paper_day.update(kwargs)
        return dict(paper_summary)

    def _build_payload(*, trade_date, daily_snapshot, paper_summary):
        return {
            "trade_date": trade_date,
            "mode": "PAPER",
            "execution_status": "READY",
            "execution_outcome": paper_summary.get("execution_outcome"),
            "execution_reason": paper_summary.get("execution_reason"),
            "trades": [
                {"ticker": "ELV", "side": "BUY", "shares": 1, "order_id": "run:ELV:BUY"},
                {"ticker": "SLB", "side": "BUY", "shares": 2, "order_id": "run:SLB:BUY"},
            ],
            "planner_intended_trades_count": 3,
            "execution_eligible_trades_count": 2,
            "orders_submitted_count": 2,
            "submitted_count": 2,
            "accepted_count": 2,
            "rejected_count": 0,
        }

    monkeypatch.setattr(live_exec, "load_precompute_inputs", lambda **_kwargs: (snapshot, planned_payload, {"version": 1}, None))
    monkeypatch.setattr(live_exec, "pre_trade_reconcile_and_classify", lambda **_kwargs: {"reconciliation_decision": "PASS"})
    monkeypatch.setattr(live_exec, "_apply_pre_execution_risk_controls", lambda **_kwargs: ("signals.json", 0.05))
    monkeypatch.setattr(live_exec, "run_paper_day", _fake_run_paper_day)
    monkeypatch.setattr(live_exec.dqr, "build_execution_email_payload", _build_payload)
    monkeypatch.setattr(live_exec, "evaluate_live_retry", lambda **_kwargs: {"retry_allowed": False, "retry_reason": ""})
    monkeypatch.setattr(live_exec, "_acquire_execution_lock", lambda _trade_date, allow_existing=False: None)

    exit_code = live_exec.main(["--continuation-mode", "buy_only"])

    assert exit_code == 0
    assert observed_run_paper_day["force"] is True
    assert observed_run_paper_day["precomputed_trade_plan"] == [
        {"ticker": "ELV", "side": "BUY", "shares": 1, "price": 250.0, "notional": 250.0, "order_id": "run:ELV:BUY"},
        {"ticker": "SLB", "side": "BUY", "shares": 2, "price": 150.0, "notional": 300.0, "order_id": "run:SLB:BUY"},
    ]
    payload = json.loads(
        (tmp_path / "outputs" / "runs" / "run-self-heal" / "execution_payload.json").read_text(encoding="utf-8")
    )
    assert payload["operator_execution_status"] == "executed"
    assert payload["pending_buy_count"] == 0
    assert payload["submitted_buy_count"] == 2
    assert payload["submitted_sell_count"] == 0

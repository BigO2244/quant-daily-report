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
    sys.modules["core"] = _module("core")
    sys.modules["core.execution_audit"] = _module(
        "core.execution_audit",
        write_executor_audit=lambda **_kwargs: None,
        write_planner_audit=lambda **_kwargs: None,
    )
    sys.modules["core.execution_payload"] = _module(
        "core.execution_payload",
        normalize_status=lambda **_kwargs: "HALTED",
        write_canonical_execution_payload=_write_canonical_execution_payload,
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
    return module


def test_warn_reconciliation_does_not_halt(tmp_path) -> None:
    live_exec = _load_module(tmp_path)
    assert live_exec._precompute_reconciliation_halt_reason({"reconciliation_decision": "WARN"}) is None


def test_pretrade_self_heal_releases_same_day_lock(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REPORT_DATE", "2026-03-26")
    live_exec = _load_module(tmp_path)

    lock_path = tmp_path / "2026-03-26.lock"

    def _fake_acquire(_trade_date: str) -> Path:
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

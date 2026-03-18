from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from brokers.alpaca_broker import (
    EXECUTION_OUTCOME_PARTIAL_BROKER_ABORT,
    EXECUTION_OUTCOME_POST_SUBMIT_ARTIFACT_FAILURE,
)
from brokers.alpaca_snapshot import (
    fetch_pretrade_snapshot,
    summarize_pretrade_broker_policy,
    write_pretrade_snapshot_artifacts,
)
from core.execution_audit import write_executor_audit, write_planner_audit
from core.execution_payload import normalize_status, write_canonical_execution_payload
from core.execution_summary import write_execution_artifacts
from core.live_retry_policy import evaluate_live_retry
from core.operator_summary import (
    format_execution_health_banner,
    format_operator_summary_log,
    load_operator_summary,
    write_operator_summary,
)
from core.precompute_contract import load_precompute_inputs
from core.run_pointer import write_latest_run_pointer
from core.timing_policy import classify_timing, current_et
from core.trading_day_summary import write_trading_day_summary
from paper.build_execution_email import build_execution_email_text
from paper.paper_broker import run_paper_day
from paper.run_manager import ensure_dir, get_run_dir, get_run_id, safe_write_text
from paper.state_paths import ensure_paper_state_files
from reconciliation import ensure_sent_ledger_exists, pre_trade_reconcile_and_classify

import daily_quant_report as dqr


logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Execute Alpaca orders from precomputed daily bundle")
    parser.add_argument("--retry-attempt", type=int, default=0)
    parser.add_argument(
        "--continuation-mode",
        choices=("none", "buy_only"),
        default="none",
        help="Allow buy-only continuation after a non-trading partial failure.",
    )
    return parser.parse_args(argv)


def _trade_date() -> str:
    report_date = str(os.getenv("REPORT_DATE", "")).strip()
    if report_date:
        return report_date
    return current_et().strftime("%Y-%m-%d")


def _workflow_start_utc() -> dt.datetime | None:
    raw = str(os.getenv("WORKFLOW_STARTED_AT_UTC", "")).strip()
    if not raw:
        return None
    try:
        return dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _init_run_root(run_root: Path, trade_date: str) -> None:
    for subdir in ("logs", "reports", "broker", "ledger", "snapshots", "audit"):
        ensure_dir(run_root / subdir)
    write_latest_run_pointer(
        run_id=get_run_id(),
        trade_date=trade_date,
        mode="ALPACA",
        run_root=str(run_root),
        status="running",
    )
    safe_write_text(
        run_root / "meta.json",
        json.dumps(
            {
                "run_id": get_run_id(),
                "trade_date": trade_date,
                "mode": "ALPACA",
                "run_root": str(run_root),
                "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            },
            indent=2,
        )
        + "\n",
        allow_overwrite=True,
    )


def _first_submit_et(paper_summary: dict[str, object]) -> dt.datetime | None:
    submissions = list((paper_summary or {}).get("alpaca_submissions") or [])
    for item in submissions:
        raw = str((item or {}).get("submitted_at") or "").strip()
        if not raw:
            continue
        try:
            return dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(ET)
        except Exception:
            continue
    return None


def _operator_execution_status(execution_payload: dict[str, object]) -> str:
    submitted = int(
        execution_payload.get("submitted_count")
        or execution_payload.get("orders_submitted_count")
        or 0
    )
    outcome = str(execution_payload.get("execution_outcome") or "").strip()
    status = str(execution_payload.get("execution_status") or "").upper()
    if submitted > 0 and outcome in {
        EXECUTION_OUTCOME_PARTIAL_BROKER_ABORT,
        EXECUTION_OUTCOME_POST_SUBMIT_ARTIFACT_FAILURE,
    }:
        return "partial"
    if submitted > 0:
        return "executed"
    if status in {"NO_ACTION", "PLANNED"}:
        return "skipped"
    if status == "HALTED":
        return "failed"
    return "skipped"


def _write_execution_email_payload(trade_date: str, payload: dict[str, object]) -> Path:
    out_dir = Path("outputs/execution_email")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{trade_date}.json"
    safe_write_text(
        out_path,
        json.dumps(payload, indent=2, default=str) + "\n",
        allow_overwrite=True,
    )
    return out_path


def _write_execution_results(run_root: Path, payload: dict[str, object], paper_summary: dict[str, object]) -> Path:
    submission_summary = dict((paper_summary or {}).get("alpaca_submission_summary") or {})
    operator_status = str(payload.get("operator_execution_status") or "").strip().lower()
    if operator_status == "partial":
        results_status = "PARTIAL"
    elif operator_status == "failed":
        results_status = "HALTED"
    elif operator_status == "executed":
        results_status = "EXECUTED"
    else:
        results_status = str(payload.get("execution_status") or "NO_ACTION")
    results = {
        "run_id": str(payload.get("run_id") or get_run_id()),
        "trade_date": str(payload.get("trade_date") or _trade_date()),
        "mode": str(payload.get("mode") or "ALPACA"),
        "status": results_status,
        "halt_reason": payload.get("halt_reason"),
        "submitted_count": int(payload.get("submitted_count") or 0),
        "accepted_count": int(payload.get("accepted_count") or 0),
        "rejected_count": int(payload.get("rejected_count") or 0),
        "duplicate_count": int(submission_summary.get("remote_existing_orders") or 0),
        "broker_responses": list((paper_summary or {}).get("alpaca_submissions") or []),
        "execution_outcome": payload.get("execution_outcome"),
        "execution_reason": payload.get("execution_reason"),
        "cash_rebalance_status": payload.get("cash_rebalance_status"),
        "broker_reject_status": payload.get("broker_reject_status"),
        "broker_reject_message": payload.get("broker_reject_message"),
        "timing_status": payload.get("timing_status"),
        "operator_execution_status": payload.get("operator_execution_status"),
    }
    out_path = run_root / "execution_results.json"
    safe_write_text(out_path, json.dumps(results, indent=2, default=str) + "\n", allow_overwrite=True)
    return out_path


def _failure_payload(*, trade_date: str, run_id: str, reason: str, timing: dict[str, object]) -> dict[str, object]:
    workflow_context = _workflow_context()
    payload = {
        "run_id": run_id,
        "trade_date": trade_date,
        "mode": "ALPACA",
        "execution_status": "HALTED",
        "halt_reason": reason,
        "trades": [],
        "execution_outcome": None,
        "execution_reason": reason,
        "cash_rebalance_status": None,
        "submitted_count": 0,
        "accepted_count": 0,
        "rejected_count": 0,
        "orders_submitted_count": 0,
        "orders_filled_count": 0,
        "operator_execution_status": "failed",
        **timing,
        **workflow_context,
    }
    return payload


def _workflow_context() -> dict[str, object]:
    return {
        "workflow_kind": str(os.getenv("WORKFLOW_KIND", "")).strip() or None,
        "event_freshness_status": str(os.getenv("EVENT_FRESHNESS_STATUS", "")).strip() or None,
        "bundle_status": str(os.getenv("BUNDLE_STATUS", "")).strip() or None,
        "bundle_source": str(os.getenv("BUNDLE_SOURCE", "")).strip() or None,
        "precompute_bundle_required": str(os.getenv("PRECOMPUTE_BUNDLE_REQUIRED", "")).strip().lower() == "true",
        "precompute_bundle_found": str(os.getenv("PRECOMPUTE_BUNDLE_FOUND", "")).strip().lower() == "true",
        "bundle_report_date": str(os.getenv("BUNDLE_REPORT_DATE", "")).strip() or None,
        "execution_window_status": str(os.getenv("EXECUTION_WINDOW_STATUS", "")).strip() or None,
    }


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parse_args(argv)
    trade_date = _trade_date()
    run_id = get_run_id()
    run_root = get_run_dir(run_id)
    _init_run_root(run_root, trade_date)

    workflow_start_utc = _workflow_start_utc()
    workflow_start_et = workflow_start_utc.astimezone(ET) if workflow_start_utc else None
    execution_start_et = current_et()
    retry_attempt = int(args.retry_attempt or 0)
    timing = classify_timing(
        now=execution_start_et,
        workflow_start_et=workflow_start_et,
        execution_start_et=execution_start_et,
        retry_attempt=retry_attempt > 0,
    )

    snapshot, planned_payload, _contract, precompute_reason = load_precompute_inputs(
        trade_date=trade_date,
        expected_mode="ALPACA",
    )
    if snapshot is None or planned_payload is None:
        payload = _failure_payload(
            trade_date=trade_date,
            run_id=run_id,
            reason=str(precompute_reason or "precompute_missing"),
            timing=timing,
        )
        write_canonical_execution_payload(payload, trade_date, run_root=run_root, allow_overwrite=True)
        _write_execution_email_payload(trade_date, payload)
        retry = evaluate_live_retry(
            submitted_count=0,
            retry_attempt_count=retry_attempt,
            reason=precompute_reason,
            now=execution_start_et,
        )
        write_operator_summary(
            run_root,
            run_id=run_id,
            trade_date=trade_date,
            mode="ALPACA",
            pretrade_status="HALTED",
            pretrade_halt_reason=str(precompute_reason),
            planner_completed=False,
            execution_payload_written=True,
            execution_stage_reached=False,
            terminal_status="failed_pre_execution",
            execution_reason=str(precompute_reason),
            operator_execution_status="failed",
            retry_attempt_count=retry_attempt,
            retry_eligible=bool(retry.get("retry_allowed")),
            retry_reason=str(retry.get("retry_reason") or ""),
            workflow_kind=payload.get("workflow_kind"),
            event_freshness_status=payload.get("event_freshness_status"),
            bundle_status=payload.get("bundle_status"),
            execution_window_status=payload.get("execution_window_status"),
            **timing,
        )
        write_latest_run_pointer(
            run_id=run_id,
            trade_date=trade_date,
            mode="ALPACA",
            run_root=str(run_root),
            status="failed_pre_execution",
            substatus=str(precompute_reason or ""),
            status_message=str(precompute_reason or ""),
        )
        write_trading_day_summary(run_root=run_root, run_id=run_id, trade_date=trade_date)
        logger.error("[LIVE_EXECUTION] precompute unavailable reason=%s", precompute_reason)
        return 1

    pretrade_snapshot = fetch_pretrade_snapshot()
    write_pretrade_snapshot_artifacts(
        run_root=run_root,
        run_id=run_id,
        trade_date=trade_date,
        snapshot=pretrade_snapshot,
        allow_overwrite=True,
    )
    pretrade_policy = summarize_pretrade_broker_policy(pretrade_snapshot)
    logger.info("%s", dqr.format_broker_preflight_banner(pretrade_policy))

    ensure_sent_ledger_exists("outputs/orders_sent/orders_sent.csv")
    paper_ledger_path, paper_trades_path = ensure_paper_state_files()
    recon_result = pre_trade_reconcile_and_classify(
        run_date=trade_date,
        trading_mode="alpaca",
        ledger_path=paper_ledger_path,
        sent_ledger_path="outputs/orders_sent/orders_sent.csv",
    )
    if str((recon_result or {}).get("reconciliation_decision") or "PASS") == "BLOCK":
        reason = str((recon_result or {}).get("block_reason") or "pretrade_blocked_reconciliation")
        payload = _failure_payload(trade_date=trade_date, run_id=run_id, reason=reason, timing=timing)
        write_canonical_execution_payload(payload, trade_date, run_root=run_root, allow_overwrite=True)
        _write_execution_email_payload(trade_date, payload)
        retry = evaluate_live_retry(
            submitted_count=0,
            retry_attempt_count=retry_attempt,
            reason=reason,
            now=execution_start_et,
        )
        write_operator_summary(
            run_root,
            run_id=run_id,
            trade_date=trade_date,
            mode="ALPACA",
            pretrade_status="HALTED",
            pretrade_halt_reason=reason,
            planner_completed=True,
            execution_payload_written=True,
            execution_stage_reached=False,
            terminal_status="failed_pre_execution",
            execution_reason=reason,
            operator_execution_status="failed",
            broker_preflight_status=pretrade_policy.get("broker_preflight_status"),
            broker_preflight_account_status=pretrade_policy.get("broker_preflight_account_status"),
            broker_preflight_cash=pretrade_policy.get("broker_preflight_cash"),
            broker_preflight_equity=pretrade_policy.get("broker_preflight_equity"),
            broker_preflight_buying_power=pretrade_policy.get("broker_preflight_buying_power"),
            broker_preflight_restriction_flags=pretrade_policy.get("broker_preflight_restriction_flags"),
            broker_preflight_warning_flags=pretrade_policy.get("broker_preflight_warning_flags"),
            broker_pdt_risk_status=pretrade_policy.get("broker_pdt_risk_status"),
            broker_pdt_daytrade_count=pretrade_policy.get("broker_pdt_daytrade_count"),
            broker_pdt_daytrading_buying_power=pretrade_policy.get("broker_pdt_daytrading_buying_power"),
            broker_pdt_flags=pretrade_policy.get("broker_pdt_flags"),
            broker_pdt_warning_message=pretrade_policy.get("broker_pdt_warning_message"),
            retry_attempt_count=retry_attempt,
            retry_eligible=bool(retry.get("retry_allowed")),
            retry_reason=str(retry.get("retry_reason") or ""),
            workflow_kind=str(os.getenv("WORKFLOW_KIND", "")).strip() or "live",
            event_freshness_status=str(os.getenv("EVENT_FRESHNESS_STATUS", "")).strip() or None,
            bundle_status=str(os.getenv("BUNDLE_STATUS", "")).strip() or None,
            execution_window_status=str(os.getenv("EXECUTION_WINDOW_STATUS", "")).strip() or None,
            **timing,
        )
        write_latest_run_pointer(
            run_id=run_id,
            trade_date=trade_date,
            mode="ALPACA",
            run_root=str(run_root),
            status="failed_pre_execution",
            substatus=reason,
            status_message=reason,
        )
        write_trading_day_summary(run_root=run_root, run_id=run_id, trade_date=trade_date)
        logger.error("[LIVE_EXECUTION] blocked by pretrade reconciliation reason=%s", reason)
        return 1

    if args.continuation_mode == "buy_only":
        os.environ["ALLOW_PARTIAL_BUY_CONTINUATION"] = "1"

    paper_summary = run_paper_day(
        run_date=trade_date,
        signals_path=str(snapshot.get("signals_snapshot_path") or ""),
        ledger_path=paper_ledger_path,
        trades_path=paper_trades_path,
        config_path="paper/config_paper.json",
        force=False,
        plan_only=False,
        constraints={
            "cash_target_weight": float(snapshot.get("target_cash_weight") or 0.0),
        },
    )
    paper_summary["run_id"] = run_id
    first_submit_et = _first_submit_et(paper_summary)
    timing = classify_timing(
        now=current_et(),
        workflow_start_et=workflow_start_et,
        execution_start_et=execution_start_et,
        first_submit_et=first_submit_et,
        retry_attempt=retry_attempt > 0,
    )

    execution_payload = dqr.build_execution_email_payload(
        trade_date=trade_date,
        daily_snapshot=snapshot,
        paper_summary=paper_summary,
    )
    execution_payload["run_id"] = run_id
    execution_payload.update(timing)
    execution_payload.update(_workflow_context())
    execution_payload["retry_attempt_count"] = retry_attempt

    submission_summary = dict((paper_summary or {}).get("alpaca_submission_summary") or {})
    submitted_count = int(submission_summary.get("submit_success") or 0)
    rejected_count = int(submission_summary.get("submit_failed") or 0)
    execution_payload["submitted_count"] = submitted_count
    execution_payload["accepted_count"] = submitted_count
    execution_payload["rejected_count"] = rejected_count
    execution_payload["orders_submitted_count"] = submitted_count

    pending_buy_count = len(
        [
            order
            for order in list((paper_summary or {}).get("budget_skipped_orders") or [])
            if str((order or {}).get("side") or "").upper() == "BUY"
        ]
    )
    continuation_eligible = bool(
        submitted_count > 0
        and str((paper_summary or {}).get("execution_outcome") or "").strip() == EXECUTION_OUTCOME_POST_SUBMIT_ARTIFACT_FAILURE
        and pending_buy_count > 0
        and timing.get("timing_status") != "after_deadline"
    )
    execution_payload["pending_buy_count"] = pending_buy_count
    execution_payload["continuation_eligible"] = continuation_eligible
    execution_payload["continuation_reason"] = (
        str((paper_summary or {}).get("execution_reason") or "post_submit_artifact_failure")
        if continuation_eligible
        else None
    )
    execution_payload["operator_execution_status"] = _operator_execution_status(execution_payload)

    retry = evaluate_live_retry(
        submitted_count=submitted_count,
        retry_attempt_count=retry_attempt,
        reason=execution_payload.get("execution_reason") or execution_payload.get("halt_reason"),
        now=current_et(),
    )
    execution_payload["retry_eligible"] = bool(retry.get("retry_allowed"))
    execution_payload["retry_reason"] = str(retry.get("retry_reason") or "")

    write_planner_audit(
        run_id=run_id,
        trade_date=trade_date,
        mode="ALPACA",
        today_et=trade_date,
        is_planning_run=False,
        market_is_open=str((paper_summary or {}).get("market_status") or "").upper() == "OPEN",
        should_execute=True,
        market_guard=(paper_summary or {}).get("market_guard"),
        broker_cash_before=None,
        broker_positions_before=(pretrade_snapshot or {}).get("positions"),
        canonical_positions_before=None,
        signals_generated=len((snapshot or {}).get("proposed_trades") or []),
        proposed_trades=list((snapshot or {}).get("proposed_trades") or []),
        blocked_reasons=list((paper_summary or {}).get("blocked_reasons") or []),
        execution_payload=execution_payload,
        extra={"workflow_stage": "live_execution_from_precompute"},
    )

    write_canonical_execution_payload(
        execution_payload,
        trade_date,
        run_root=run_root,
        allow_overwrite=True,
    )
    _write_execution_email_payload(trade_date, execution_payload)
    _write_execution_results(run_root, execution_payload, paper_summary)

    write_operator_summary(
        run_root,
        run_id=run_id,
        trade_date=trade_date,
        mode="ALPACA",
        pretrade_status=normalize_status(
            execution_status=execution_payload.get("execution_status"),
            halt_reason=execution_payload.get("halt_reason"),
            executable_trades_count=int(execution_payload.get("executable_trades_count") or 0),
        ),
        pretrade_halt_reason=execution_payload.get("halt_reason"),
        proposed_trades_count=int(execution_payload.get("planner_intended_trades_count") or 0),
        executable_trades_count=int(execution_payload.get("execution_eligible_trades_count") or 0),
        submitted_count=submitted_count,
        accepted_count=submitted_count,
        rejected_count=rejected_count,
        model_proposed_trades_count=int(execution_payload.get("model_proposed_trades_count") or 0),
        planner_intended_trades_count=int(execution_payload.get("planner_intended_trades_count") or 0),
        execution_eligible_trades_count=int(execution_payload.get("execution_eligible_trades_count") or 0),
        orders_submitted_count=submitted_count,
        planner_completed=True,
        executor_completed=True,
        execution_payload_written=True,
        execution_stage_reached=True,
        terminal_status=(
            "failed_pre_execution"
            if execution_payload["operator_execution_status"] in {"failed", "partial"}
            else ("no_action" if execution_payload["operator_execution_status"] == "skipped" else "success")
        ),
        exception_type=str((paper_summary or {}).get("artifact_failure_stage") or "") or None,
        exception_message=str((paper_summary or {}).get("artifact_failure_message") or "") or None,
        broker_pretrade_snapshot_ok=bool((pretrade_snapshot or {}).get("ok")),
        broker_posttrade_snapshot_ok=bool((paper_summary or {}).get("posttrade_account_snapshot_path") and (paper_summary or {}).get("posttrade_positions_snapshot_path")),
        broker_authoritative_state=bool((paper_summary or {}).get("posttrade_positions_snapshot_path")),
        post_execution_recon_status=(paper_summary or {}).get("posttrade_recon_status"),
        post_execution_recon_path=(paper_summary or {}).get("posttrade_recon_path"),
        affected_symbols=list((paper_summary or {}).get("execution_submitted_symbols") or []),
        repair_suggestions=list((paper_summary or {}).get("posttrade_repair_suggestions") or []),
        duplicate_fill_suspicions_count=int((paper_summary or {}).get("posttrade_duplicate_fill_suspicions_count") or 0),
        broker_reject_status=(paper_summary or {}).get("broker_reject_status"),
        broker_reject_message=(paper_summary or {}).get("broker_reject_message"),
        execution_outcome=(paper_summary or {}).get("execution_outcome"),
        execution_reason=(paper_summary or {}).get("execution_reason"),
        cash_rebalance_status=(paper_summary or {}).get("cash_rebalance_status"),
        broker_preflight_status=pretrade_policy.get("broker_preflight_status"),
        broker_preflight_account_status=pretrade_policy.get("broker_preflight_account_status"),
        broker_preflight_cash=pretrade_policy.get("broker_preflight_cash"),
        broker_preflight_equity=pretrade_policy.get("broker_preflight_equity"),
        broker_preflight_buying_power=pretrade_policy.get("broker_preflight_buying_power"),
        broker_preflight_restriction_flags=pretrade_policy.get("broker_preflight_restriction_flags"),
        broker_preflight_warning_flags=pretrade_policy.get("broker_preflight_warning_flags"),
        broker_pdt_risk_status=pretrade_policy.get("broker_pdt_risk_status"),
        broker_pdt_daytrade_count=pretrade_policy.get("broker_pdt_daytrade_count"),
        broker_pdt_daytrading_buying_power=pretrade_policy.get("broker_pdt_daytrading_buying_power"),
        broker_pdt_flags=pretrade_policy.get("broker_pdt_flags"),
        broker_pdt_warning_message=pretrade_policy.get("broker_pdt_warning_message"),
        timing_status=str(timing.get("timing_status") or ""),
        preferred_target_et=str(timing.get("preferred_target_et") or ""),
        degraded_auto_trade_deadline_et=str(timing.get("degraded_auto_trade_deadline_et") or ""),
        actual_workflow_start_et=str(timing.get("actual_workflow_start_et") or ""),
        actual_execution_start_et=str(timing.get("actual_execution_start_et") or ""),
        first_submit_et=str(timing.get("first_submit_et") or ""),
        operator_execution_status=str(execution_payload.get("operator_execution_status") or ""),
        retry_attempt_count=retry_attempt,
        retry_eligible=bool(retry.get("retry_allowed")),
        retry_reason=str(retry.get("retry_reason") or ""),
        continuation_eligible=continuation_eligible,
        continuation_reason=str(execution_payload.get("continuation_reason") or ""),
        pending_buy_count=pending_buy_count,
        workflow_kind=execution_payload.get("workflow_kind"),
        event_freshness_status=execution_payload.get("event_freshness_status"),
        bundle_status=execution_payload.get("bundle_status"),
        execution_window_status=execution_payload.get("execution_window_status"),
    )

    print(format_operator_summary_log(load_operator_summary(run_root) or {}))
    print(format_execution_health_banner(load_operator_summary(run_root) or {}))

    write_executor_audit(
        run_id=run_id,
        pretrade_status=str(execution_payload.get("execution_status") or "UNKNOWN"),
        halt_reason=str(execution_payload.get("halt_reason") or "") or None,
        submitted_count=submitted_count,
        accepted_count=submitted_count,
        rejected_count=rejected_count,
        duplicate_count=int(submission_summary.get("remote_existing_orders") or 0),
        broker_responses=list((paper_summary or {}).get("alpaca_submissions") or []),
        broker_cash_after=(paper_summary or {}).get("cash"),
        broker_positions_after=(paper_summary or {}).get("alpaca_positions_snapshot"),
        execution_status=str(execution_payload.get("operator_execution_status") or execution_payload.get("execution_status") or "UNKNOWN").upper(),
    )

    try:
        write_execution_artifacts(
            run_id=run_id,
            trade_date=trade_date,
            run_dir=run_root,
            execution_payload=execution_payload,
            paper_summary=paper_summary,
            nav_snapshot={
                "position_count": len([h for h in (snapshot.get("holdings") or []) if str(h.get("ticker", "")).upper() != "CASH"]),
                "equity": ((snapshot.get("performance_diagnostics") or {}).get("current_equity")),
                "cash": (paper_summary or {}).get("cash"),
                "turnover_pct": execution_payload.get("turnover_pct"),
                "holdings": (snapshot.get("holdings") or []),
            },
        )
    except Exception as exc:
        logger.warning("[LIVE_EXECUTION] execution summary artifacts skipped: %s", exc)

    write_trading_day_summary(run_root=run_root, run_id=run_id, trade_date=trade_date)

    try:
        from core.benchmark_tracking import update_benchmark_vs_spy
        update_benchmark_vs_spy(trade_date=trade_date)
    except Exception as _bench_exc:
        logger.warning("[BENCHMARK] benchmark update skipped: %s", _bench_exc)

    exec_subject, exec_body = build_execution_email_text(execution_payload)
    safe_write_text(
        run_root / "reports" / f"trade_execution_{trade_date}.txt",
        exec_body.rstrip() + "\n",
        allow_overwrite=True,
    )
    logger.info("[LIVE_EXECUTION] email_subject=%s", exec_subject)

    terminal_status = (
        "failed_pre_execution"
        if execution_payload["operator_execution_status"] in {"failed", "partial"}
        else ("no_action" if execution_payload["operator_execution_status"] == "skipped" else "success")
    )
    write_latest_run_pointer(
        run_id=run_id,
        trade_date=trade_date,
        mode="ALPACA",
        run_root=str(run_root),
        status=terminal_status,
        substatus=str((paper_summary or {}).get("execution_reason") or ""),
        status_message=str(execution_payload.get("halt_reason") or ""),
    )

    if execution_payload["operator_execution_status"] in {"failed", "partial"}:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

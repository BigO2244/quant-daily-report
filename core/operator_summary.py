from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any


def _now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _summary_path(run_root: str | Path) -> Path:
    return Path(run_root) / "operator_summary.json"


def _failure_path(run_root: str | Path) -> Path:
    return Path(run_root) / "logs" / "planner_failure.json"


def _normalize_mode(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).strip().upper() or None


def _normalize_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def load_operator_summary(run_root: str | Path) -> dict[str, Any]:
    path = _summary_path(run_root)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_operator_summary(run_root: str | Path, **fields: Any) -> Path:
    path = _summary_path(run_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_operator_summary(run_root)
    payload: dict[str, Any] = {
        "generated_at": existing.get("generated_at") or _now_utc(),
        "updated_at": _now_utc(),
    }
    payload.update(existing)
    payload.update({key: value for key, value in fields.items() if value is not None})
    payload["updated_at"] = _now_utc()
    payload["mode"] = _normalize_mode(payload.get("mode"))
    if "planner_intended_trades_count" not in payload and "proposed_trades_count" in payload:
        payload["planner_intended_trades_count"] = payload.get("proposed_trades_count")
    if "execution_eligible_trades_count" not in payload and "executable_trades_count" in payload:
        payload["execution_eligible_trades_count"] = payload.get("executable_trades_count")
    if "orders_submitted_count" not in payload and "submitted_count" in payload:
        payload["orders_submitted_count"] = payload.get("submitted_count")
    for key in (
        "planner_completed",
        "executor_completed",
        "report_completed",
        "skipped_duplicate",
        "confirmation_email_sent",
    ):
        payload.setdefault(key, False)
    for key in (
        "proposed_trades_count",
        "executable_trades_count",
        "submitted_count",
        "accepted_count",
        "rejected_count",
    ):
        payload.setdefault(key, 0)
    for key in (
        "broker_preflight_warning_flags",
        "broker_pdt_flags",
        "affected_symbols",
        "repair_suggestions",
    ):
        if key in payload:
            payload[key] = _normalize_list(payload.get(key))
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def write_preflight_failure(
    run_root: str | Path,
    *,
    run_id: str,
    stage: str,
    terminal_status: str,
    exception_type: str | None = None,
    exception_message: str | None = None,
) -> Path:
    payload = {
        "run_id": run_id,
        "stage": stage,
        "terminal_status": terminal_status,
        "exception_type": exception_type,
        "exception_message": exception_message,
        "generated_at": _now_utc(),
    }
    path = _failure_path(run_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def format_broker_preflight_banner(policy: dict[str, Any]) -> str:
    if not policy:
        return "[BROKER_PREFLIGHT] unavailable"
    restriction_flags = policy.get("broker_preflight_restriction_flags")
    if isinstance(restriction_flags, dict):
        restriction_text = ",".join(
            f"{key}:{value}" for key, value in sorted(restriction_flags.items())
        ) or "none"
    else:
        restriction_text = ",".join(_normalize_list(restriction_flags)) or "none"
    warning_flags = ",".join(_normalize_list(policy.get("broker_preflight_warning_flags"))) or "none"
    return (
        "[BROKER_PREFLIGHT] "
        f"status={policy.get('broker_preflight_status') or 'UNKNOWN'} "
        f"account_status={policy.get('broker_preflight_account_status') or 'UNKNOWN'} "
        f"cash={policy.get('broker_preflight_cash')} "
        f"equity={policy.get('broker_preflight_equity')} "
        f"buying_power={policy.get('broker_preflight_buying_power')} "
        f"restrictions={restriction_text} "
        f"warnings={warning_flags} "
        f"pdt_status={policy.get('broker_pdt_risk_status') or 'UNKNOWN'}"
    )


def format_operator_summary_log(summary: dict[str, Any]) -> str:
    if not summary:
        return "[OPERATOR_SUMMARY] unavailable"
    status = str(summary.get("terminal_status") or "").strip().upper()
    if not status:
        if bool(summary.get("skipped_duplicate")):
            status = "SKIPPED_DUPLICATE"
        elif int(summary.get("submitted_count") or summary.get("orders_submitted_count") or 0) > 0:
            status = "EXECUTED"
        else:
            status = str(summary.get("pretrade_status") or "UNKNOWN").strip().upper()
    return (
        "[OPERATOR_SUMMARY] "
        f"run_id={summary.get('run_id') or 'unknown'} "
        f"trade_date={summary.get('trade_date') or 'unknown'} "
        f"mode={summary.get('mode') or 'UNKNOWN'} "
        f"status={status or 'UNKNOWN'} "
        f"pretrade_status={summary.get('pretrade_status') or 'UNKNOWN'} "
        f"executable={summary.get('executable_trades_count') or summary.get('execution_eligible_trades_count') or 0} "
        f"submitted={summary.get('submitted_count') or summary.get('orders_submitted_count') or 0} "
        f"accepted={summary.get('accepted_count') or 0} "
        f"rejected={summary.get('rejected_count') or 0} "
        f"confirmation_email={bool(summary.get('confirmation_email_sent'))} "
        f"post_recon={summary.get('post_execution_recon_status') or 'UNKNOWN'} "
        f"integrity={summary.get('execution_integrity_status') or 'UNKNOWN'} "
        f"reliability={summary.get('execution_reliability_status') or 'UNKNOWN'} "
        f"reliability_classification={summary.get('execution_reliability_classification') or 'UNKNOWN'} "
        f"reliability_score={summary.get('execution_reliability_score') if summary.get('execution_reliability_score') is not None else 'UNKNOWN'} "
        f"clean_run_streak={summary.get('execution_reliability_clean_run_streak') if summary.get('execution_reliability_clean_run_streak') is not None else 'UNKNOWN'} "
        f"reliability_reason={summary.get('execution_reliability_top_reason') or 'none'} "
        f"authoritative={bool(summary.get('broker_authoritative_state'))}"
    )


def format_execution_health_banner(summary: dict[str, Any]) -> str:
    if not summary:
        return "[EXECUTION_HEALTH] unavailable"
    authority = "BROKER_AUTHORITATIVE" if summary.get("broker_authoritative_state") else "MODEL_LEDGER"
    affected_symbols = ",".join(_normalize_list(summary.get("affected_symbols"))) or "none"
    repair_suggestions = "; ".join(_normalize_list(summary.get("repair_suggestions"))) or "none"
    pdt_flags = ",".join(_normalize_list(summary.get("broker_pdt_flags"))) or "none"
    broker_reject = summary.get("broker_reject_status") or "none"
    broker_reject_message = summary.get("broker_reject_message") or "none"
    integrity_status = summary.get("execution_integrity_status") or "UNKNOWN"
    integrity_findings = "; ".join(_normalize_list(summary.get("execution_integrity_findings"))) or "none"
    reliability_status = summary.get("execution_reliability_status") or "UNKNOWN"
    reliability_classification = summary.get("execution_reliability_classification") or "UNKNOWN"
    reliability_score = summary.get("execution_reliability_score")
    reliability_score = reliability_score if reliability_score is not None else "UNKNOWN"
    clean_run_streak = summary.get("execution_reliability_clean_run_streak")
    clean_run_streak = clean_run_streak if clean_run_streak is not None else "UNKNOWN"
    reliability_reason = summary.get("execution_reliability_top_reason") or "none"
    capital_constraint = (
        "TRIGGERED" if bool(summary.get("capital_constraint_triggered")) else "CLEAR"
    )
    return (
        "[EXECUTION_HEALTH] "
        f"trade_date={summary.get('trade_date') or 'unknown'} "
        f"mode={summary.get('mode') or 'UNKNOWN'} "
        f"pretrade={summary.get('pretrade_status') or 'UNKNOWN'} "
        f"post_execution_recon={summary.get('post_execution_recon_status') or 'UNKNOWN'} "
        f"duplicate_guard={summary.get('duplicate_guard_status') or 'UNKNOWN'} "
        f"broker_pdt={summary.get('broker_pdt_risk_status') or 'UNKNOWN'} "
        f"pdt_flags={pdt_flags} "
        f"broker_reject={broker_reject} "
        f"broker_reject_message={broker_reject_message} "
        f"affected_symbols={affected_symbols} "
        f"duplicate_fill_suspicions={int(summary.get('duplicate_fill_suspicions_count') or 0)} "
        f"repairs={repair_suggestions} "
        f"capital_constraint={capital_constraint} "
        f"requested_buys={summary.get('requested_buy_notional')} "
        f"allowed_buys={summary.get('allowed_buy_notional')} "
        f"integrity={integrity_status} "
        f"integrity_findings={integrity_findings} "
        f"reliability={reliability_status} "
        f"Reliability Status:{reliability_classification} "
        f"Reliability Score:{reliability_score} "
        f"Clean Run Streak:{clean_run_streak} "
        f"Top Failure Reason:{reliability_reason} "
        f"reliability_reason={reliability_reason} "
        f"state={authority}"
    )

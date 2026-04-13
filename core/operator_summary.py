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
    return Path(run_root) / "planner_failure.json"


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


def write_operator_summary(run_root: str | Path, **fields: Any) -> dict[str, Any]:
    path = _summary_path(run_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_operator_summary(run_root)
    payload: dict[str, Any] = {
        "generated_at": existing.get("generated_at") or _now_utc(),
        "updated_at": _now_utc(),
    }
    payload.update(existing)
    payload.update({key: value for key, value in fields.items() if value is not None})
    payload["mode"] = _normalize_mode(payload.get("mode"))
    for key in (
        "broker_preflight_restriction_flags",
        "broker_preflight_warning_flags",
        "broker_pdt_flags",
        "affected_symbols",
        "repair_suggestions",
    ):
        if key in payload:
            payload[key] = _normalize_list(payload.get(key))
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def write_preflight_failure(
    run_root: str | Path,
    *,
    run_id: str,
    stage: str,
    terminal_status: str,
    exception_type: str | None = None,
    exception_message: str | None = None,
) -> dict[str, Any]:
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
    return payload


def format_broker_preflight_banner(policy: dict[str, Any]) -> str:
    if not policy:
        return "[BROKER_PREFLIGHT] unavailable"
    restriction_flags = ",".join(_normalize_list(policy.get("broker_preflight_restriction_flags"))) or "none"
    warning_flags = ",".join(_normalize_list(policy.get("broker_preflight_warning_flags"))) or "none"
    return (
        "[BROKER_PREFLIGHT] "
        f"status={policy.get('broker_preflight_status') or 'UNKNOWN'} "
        f"account={policy.get('broker_preflight_account_status') or 'UNKNOWN'} "
        f"cash={policy.get('broker_preflight_cash')} "
        f"equity={policy.get('broker_preflight_equity')} "
        f"buying_power={policy.get('broker_preflight_buying_power')} "
        f"restrictions={restriction_flags} "
        f"warnings={warning_flags} "
        f"pdt={policy.get('broker_pdt_risk_status') or 'UNKNOWN'}"
    )


def format_operator_summary_log(summary: dict[str, Any]) -> str:
    if not summary:
        return "[OPERATOR_SUMMARY] unavailable"
    return (
        "[OPERATOR_SUMMARY] "
        f"trade_date={summary.get('trade_date') or 'unknown'} "
        f"mode={summary.get('mode') or 'UNKNOWN'} "
        f"pretrade_status={summary.get('pretrade_status') or 'UNKNOWN'} "
        f"post_recon={summary.get('post_execution_recon_status') or 'UNKNOWN'} "
        f"authoritative={bool(summary.get('broker_authoritative_state'))}"
    )


def format_execution_health_banner(summary: dict[str, Any]) -> str:
    if not summary:
        return "[EXECUTION_HEALTH] unavailable"
    authority = "BROKER_AUTHORITATIVE" if summary.get("broker_authoritative_state") else "MODEL_LEDGER"
    return (
        "[EXECUTION_HEALTH] "
        f"trade_date={summary.get('trade_date') or 'unknown'} "
        f"mode={summary.get('mode') or 'UNKNOWN'} "
        f"pretrade={summary.get('pretrade_status') or 'UNKNOWN'} "
        f"posttrade={summary.get('post_execution_recon_status') or 'UNKNOWN'} "
        f"duplicate_guard={summary.get('duplicate_guard_status') or 'UNKNOWN'} "
        f"state={authority}"
    )

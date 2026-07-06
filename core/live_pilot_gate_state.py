from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Mapping

from core.live_pilot_guardrails import (
    LIVE_PILOT_APPROVED_ENV,
    LIVE_PILOT_CAPITAL_CAP_ENV,
    LIVE_PILOT_DRY_RUN_ENV,
    LIVE_PILOT_KILL_SWITCH_ENV,
    LIVE_PILOT_MAX_CAP_USD,
    LIVE_PILOT_MAX_ORDERS_ENV,
    LIVE_PILOT_SLEEVE_ID_ENV,
)
from paper.run_manager import safe_write_text


LIVE_PILOT_GATE_STATE_FILENAME = "live_pilot_gate_state.json"
TRUE_VALUES = frozenset({"1", "true", "yes", "y", "on", "approve_live_pilot"})


def _now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in TRUE_VALUES


def _safe_float(value: object) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _safe_int(value: object) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _endpoint_category(base_url: object) -> str:
    value = str(base_url or "").strip().lower()
    if not value:
        return "unset"
    if "paper-api.alpaca.markets" in value:
        return "paper"
    if "api.alpaca.markets" in value:
        return "live"
    return "other"


def _git_sha(repo_root: Path) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def cap_source_from_env(env: Mapping[str, str] | None = None) -> tuple[float | None, str]:
    environ = env if env is not None else os.environ
    raw = str(environ.get(LIVE_PILOT_CAPITAL_CAP_ENV) or "").strip()
    if not raw:
        return None, "missing"
    configured = _safe_float(raw)
    if configured is None or configured <= 0:
        return None, "invalid"
    if configured > LIVE_PILOT_MAX_CAP_USD:
        return configured, "env_over_limit"
    return configured, "env"


def build_live_pilot_gate_state(
    *,
    run_id: str,
    trade_date: str | None = None,
    env: Mapping[str, str] | None = None,
    repo_root: Path | str = Path("."),
    decision: str,
    block_reason: str | None = None,
    broker_orders_submitted: int = 0,
    base_url: str | None = None,
) -> dict[str, Any]:
    environ = env if env is not None else os.environ
    configured_cap, cap_source = cap_source_from_env(environ)
    effective_cap = configured_cap if cap_source == "env" else None
    endpoint = base_url if base_url is not None else str(environ.get("ALPACA_BASE_URL") or "")
    trading_mode = str(environ.get("TRADING_MODE") or environ.get("MODE") or "").strip()
    return {
        "schema_version": "live_pilot_gate_state.v1",
        "timestamp": _now_utc(),
        "git_sha": _git_sha(Path(repo_root)),
        "run_id": run_id,
        "trade_date": str(trade_date or environ.get("REPORT_DATE") or ""),
        "trading_mode": trading_mode,
        "alpaca_endpoint_category": _endpoint_category(endpoint),
        "ALPACA_PAPER": str(environ.get("ALPACA_PAPER") or ""),
        "schedule_enabled": _truthy(environ.get("CAERUS_LIVE_PILOT_SCHEDULE_ENABLED")),
        "cron_approved": _truthy(environ.get("CAERUS_LIVE_PILOT_CRON_APPROVED")),
        "submit_approved": _truthy(environ.get("CAERUS_LIVE_PILOT_SUBMIT_APPROVED")),
        "live_pilot_approved": _truthy(environ.get(LIVE_PILOT_APPROVED_ENV)),
        "kill_switch_set": _truthy(environ.get(LIVE_PILOT_KILL_SWITCH_ENV)),
        "configured_cap_usd": configured_cap,
        "approved_max_cap_usd": LIVE_PILOT_MAX_CAP_USD,
        "effective_cap_usd": effective_cap,
        "cap_source": cap_source,
        "max_orders": _safe_int(environ.get(LIVE_PILOT_MAX_ORDERS_ENV)),
        "sleeve_id": str(environ.get(LIVE_PILOT_SLEEVE_ID_ENV) or "").strip() or None,
        "dry_run": str(environ.get(LIVE_PILOT_DRY_RUN_ENV) or ""),
        "decision": str(decision or "").strip().upper(),
        "block_reason": block_reason,
        "broker_orders_submitted": int(broker_orders_submitted or 0),
    }


def write_live_pilot_gate_state(
    *,
    run_root: Path | str,
    run_id: str,
    trade_date: str | None = None,
    env: Mapping[str, str] | None = None,
    repo_root: Path | str = Path("."),
    decision: str,
    block_reason: str | None = None,
    broker_orders_submitted: int = 0,
    base_url: str | None = None,
) -> Path:
    run_root = Path(run_root)
    run_root.mkdir(parents=True, exist_ok=True)
    payload = build_live_pilot_gate_state(
        run_id=run_id,
        trade_date=trade_date,
        env=env,
        repo_root=repo_root,
        decision=decision,
        block_reason=block_reason,
        broker_orders_submitted=broker_orders_submitted,
        base_url=base_url,
    )
    path = run_root / LIVE_PILOT_GATE_STATE_FILENAME
    safe_write_text(
        path,
        json.dumps(_json_safe(payload), indent=2, sort_keys=True) + "\n",
        allow_overwrite=True,
    )
    return path

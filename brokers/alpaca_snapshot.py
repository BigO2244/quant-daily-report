from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple

from brokers.alpaca_broker import AlpacaBroker
from paper.run_manager import ensure_dir, safe_write_text

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _coerce_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except Exception:
        return None


def _coerce_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return None


def normalize_account_status(value: Any) -> str | None:
    if value is None:
        return None

    raw = getattr(value, "value", value)
    text = str(raw).strip()
    if not text and getattr(value, "name", None):
        text = str(value.name).strip()
    if not text:
        return None

    upper = text.upper()
    if "." in upper:
        upper = upper.split(".")[-1].strip()
    return upper or None


def summarize_broker_pdt_risk(snapshot: Dict[str, Any] | None) -> Dict[str, Any]:
    account = dict((snapshot or {}).get("account") or {})
    raw_account = dict(account.get("raw") or {})

    def _field(name: str) -> Any:
        value = account.get(name)
        if value is None:
            value = raw_account.get(name)
        return value

    pattern_day_trader = _coerce_bool(_field("pattern_day_trader"))
    daytrade_count = _coerce_int(_field("daytrade_count"))
    daytrading_buying_power = _field("daytrading_buying_power")
    daytrading_buying_power_value = _coerce_float(daytrading_buying_power)

    flags: List[str] = []
    risk_markers: List[str] = []

    if pattern_day_trader is True:
        flags.append("pattern_day_trader:true")
        risk_markers.append("pattern_day_trader")
    elif pattern_day_trader is False:
        flags.append("pattern_day_trader:false")

    if daytrade_count is not None:
        flags.append(f"daytrade_count:{daytrade_count}")
        if daytrade_count >= 3:
            flags.append("daytrade_count_high")
            risk_markers.append("daytrade_count_high")

    if daytrading_buying_power is not None:
        flags.append(f"daytrading_buying_power:{daytrading_buying_power}")
    if daytrading_buying_power_value is not None and daytrading_buying_power_value <= 0:
        flags.append("daytrading_buying_power_non_positive")
        risk_markers.append("daytrading_buying_power_non_positive")

    if not account:
        status = "UNKNOWN"
    elif risk_markers:
        status = "WARN"
    else:
        status = "CLEAR"

    warning_message = None
    if risk_markers:
        details: List[str] = []
        if pattern_day_trader is True:
            details.append("pattern_day_trader=true")
        if daytrade_count is not None:
            details.append(f"daytrade_count={daytrade_count}")
        if daytrading_buying_power is not None:
            details.append(f"daytrading_buying_power={daytrading_buying_power}")
        warning_message = "PDT risk signaled by broker account fields"
        if details:
            warning_message = f"{warning_message}: {', '.join(details)}"

    # Detect the specific high-risk combination: near PDT limit with no
    # daytrading buying power.  This is the condition that warrants an
    # operator-visible alert in the email subject.
    pdt_constrained = bool(
        daytrade_count is not None
        and daytrade_count >= 3
        and daytrading_buying_power_value is not None
        and daytrading_buying_power_value <= 0
    )
    if pdt_constrained:
        logger.warning(
            "[PDT][WARN] daytrade_count=%s daytrading_buying_power=%s — pdt_constrained=true",
            daytrade_count,
            daytrading_buying_power,
        )

    return {
        "broker_pdt_risk_status": status,
        "broker_pdt_daytrade_count": daytrade_count,
        "broker_pdt_daytrading_buying_power": daytrading_buying_power,
        "broker_pdt_flags": flags,
        "broker_pdt_warning_message": warning_message,
        "pdt_constrained": pdt_constrained,
    }


def summarize_pretrade_broker_policy(snapshot: Dict[str, Any] | None) -> Dict[str, Any]:
    account = dict((snapshot or {}).get("account") or {})
    raw_account = dict(account.get("raw") or {})

    def _field(name: str) -> Any:
        value = account.get(name)
        if value is None:
            value = raw_account.get(name)
        return value

    account_status = normalize_account_status(_field("status"))
    cash = _field("cash")
    equity = _field("equity") or _field("portfolio_value")
    buying_power = _field("buying_power")
    pdt_summary = summarize_broker_pdt_risk(snapshot)

    restriction_flags: Dict[str, Any] = {}
    for key in (
        "trading_blocked",
        "account_blocked",
        "transfers_blocked",
        "shorting_enabled",
        "pattern_day_trader",
        "daytrade_count",
        "daytrading_buying_power",
    ):
        value = _field(key)
        if value is not None:
            restriction_flags[key] = value

    warning_flags: List[str] = []
    if account_status and account_status != "ACTIVE":
        warning_flags.append(f"account_status_not_active:{account_status}")
    for key in ("trading_blocked", "account_blocked", "transfers_blocked", "pattern_day_trader"):
        value = restriction_flags.get(key)
        truthy = _coerce_bool(value)
        if truthy is True:
            warning_flags.append(f"{key}:true")

    buying_power_value = _coerce_float(buying_power)
    if buying_power_value is not None and buying_power_value <= 0:
        warning_flags.append("buying_power_non_positive")
    for flag in list(pdt_summary.get("broker_pdt_flags") or []):
        if flag not in warning_flags:
            warning_flags.append(flag)

    if not account:
        status = "UNKNOWN"
    elif warning_flags:
        status = "WARN"
    else:
        status = "CLEAR"

    return {
        "broker_preflight_status": status,
        "broker_preflight_account_status": account_status,
        "broker_preflight_cash": cash,
        "broker_preflight_equity": equity,
        "broker_preflight_buying_power": buying_power,
        "broker_preflight_restriction_flags": restriction_flags,
        "broker_preflight_warning_flags": warning_flags,
        "broker_preflight_warning_count": int(len(warning_flags)),
        "broker_pdt_risk_status": pdt_summary.get("broker_pdt_risk_status"),
        "broker_pdt_daytrade_count": pdt_summary.get("broker_pdt_daytrade_count"),
        "broker_pdt_daytrading_buying_power": pdt_summary.get("broker_pdt_daytrading_buying_power"),
        "broker_pdt_flags": list(pdt_summary.get("broker_pdt_flags") or []),
        "broker_pdt_warning_message": pdt_summary.get("broker_pdt_warning_message"),
        "pdt_constrained": bool(pdt_summary.get("pdt_constrained")),
    }


def fetch_pretrade_snapshot() -> Dict[str, Any]:
    """
    Best-effort Alpaca snapshot captured before run_paper_day().

    This is observability-only in Phase 1: failures are recorded in the payload
    but must not alter execution behavior.
    """
    captured_at = _utc_now_iso()
    base_payload: Dict[str, Any] = {
        "broker": "alpaca",
        "captured_at": captured_at,
        "base_url": None,
        "paper": None,
        "account": {},
        "positions": [],
        "account_error": None,
        "positions_error": None,
        "ok": False,
    }

    try:
        broker = AlpacaBroker.from_env()
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        logger.warning("[BROKER][PRETRADE] broker init failed (non-blocking): %s", err)
        return {
            **base_payload,
            "account_error": err,
            "positions_error": err,
        }

    base_payload["base_url"] = broker.base_url
    base_payload["paper"] = bool(broker.paper)

    try:
        base_payload["account"] = broker.get_account() or {}
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        logger.warning("[BROKER][PRETRADE] account snapshot failed (non-blocking): %s", err)
        base_payload["account_error"] = err

    try:
        base_payload["positions"] = broker.get_positions() or []
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        logger.warning("[BROKER][PRETRADE] positions snapshot failed (non-blocking): %s", err)
        base_payload["positions_error"] = err

    base_payload["ok"] = (
        base_payload["account_error"] is None
        and base_payload["positions_error"] is None
    )
    return base_payload


def write_pretrade_snapshot_artifacts(
    *,
    run_root: Path,
    run_id: str,
    trade_date: str,
    snapshot: Dict[str, Any],
    allow_overwrite: bool = True,
) -> Tuple[Path, Path]:
    broker_dir = Path(run_root) / "broker"
    ensure_dir(broker_dir)

    account_payload = {
        "run_id": str(run_id),
        "trade_date": str(trade_date),
        "captured_at": str(snapshot.get("captured_at") or _utc_now_iso()),
        "broker": str(snapshot.get("broker") or "alpaca"),
        "base_url": snapshot.get("base_url"),
        "paper": snapshot.get("paper"),
        "ok": snapshot.get("account_error") is None,
        "error": snapshot.get("account_error"),
        "account": snapshot.get("account") or {},
    }
    positions: List[Dict[str, Any]] = list(snapshot.get("positions") or [])
    positions_payload = {
        "run_id": str(run_id),
        "trade_date": str(trade_date),
        "captured_at": str(snapshot.get("captured_at") or _utc_now_iso()),
        "broker": str(snapshot.get("broker") or "alpaca"),
        "base_url": snapshot.get("base_url"),
        "paper": snapshot.get("paper"),
        "ok": snapshot.get("positions_error") is None,
        "error": snapshot.get("positions_error"),
        "positions_count": int(len(positions)),
        "positions": positions,
    }

    account_path = broker_dir / "pretrade_account_snapshot.json"
    positions_path = broker_dir / "pretrade_positions.json"
    safe_write_text(
        account_path,
        json.dumps(account_payload, indent=2, sort_keys=True, default=str) + "\n",
        allow_overwrite=allow_overwrite,
    )
    safe_write_text(
        positions_path,
        json.dumps(positions_payload, indent=2, sort_keys=True, default=str) + "\n",
        allow_overwrite=allow_overwrite,
    )
    return account_path, positions_path

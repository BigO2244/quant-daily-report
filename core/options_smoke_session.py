from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from brokers.alpaca_broker import AlpacaBroker
from core.options_execution import build_option_symbol
from core.execution_authority_policy import (
    OPTIONS_CAPITAL_EXECUTION_AUTHORITY,
    OPTIONS_MUTATION_REASON,
)

DEFAULT_SMOKE_POLICY: dict[str, Any] = {
    "benchmark": "SPY",
    "mode": "paper_smoke_session",
    "open_contracts": 1,
    "close_same_day": False,
    "hold_between_sessions": True,
    "default_expiry_days": 7,
    "default_strike_offset_pct": 0.0,
}


def _now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _load_policy(path: str | Path | None) -> dict[str, Any]:
    policy = json.loads(json.dumps(DEFAULT_SMOKE_POLICY))
    if path is None:
        return policy
    config_path = Path(path)
    if not config_path.exists():
        return policy
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return policy
    if isinstance(payload, dict):
        policy.update(payload)
    return policy


def _trade_date_today() -> str:
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def _session_path(root: str | Path) -> Path:
    return Path(root) / "options_smoke_session_state.json"


def _read_session_state(root: str | Path) -> dict[str, Any]:
    path = _session_path(root)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_session_state(root: str | Path, payload: dict[str, Any]) -> Path:
    path = _session_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _option_positions(positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [dict(pos) for pos in positions or [] if str((pos or {}).get("asset_class") or "").lower() == "us_option"]


def _position_qty(position: dict[str, Any]) -> int:
    try:
        return abs(int(float(position.get("qty") or 0)))
    except Exception:
        return 0


def build_options_smoke_session(
    *,
    trade_date: str,
    asof_date: str | None,
    broker: AlpacaBroker,
    account: dict[str, Any] | None,
    positions: list[dict[str, Any]] | None,
    policy_path: str | Path | None = Path("config/options_smoke_session_policy.json"),
    state_root: str | Path = "outputs/options_execution",
    allow_submission: bool = False,
) -> dict[str, Any]:
    policy = _load_policy(policy_path)
    session_state = _read_session_state(state_root)
    option_positions = _option_positions(list(positions or []))
    open_symbols = [str(pos.get("symbol") or "") for pos in option_positions]
    open_qty = sum(_position_qty(pos) for pos in option_positions)
    today = str(trade_date or asof_date or _trade_date_today())
    last_action_date = str(session_state.get("last_action_date") or "")
    last_open_date = str(session_state.get("last_open_date") or "")
    last_close_date = str(session_state.get("last_close_date") or "")
    same_day = last_open_date == today and open_qty > 0

    call_symbol = build_option_symbol(
        underlying=str(policy.get("benchmark") or "SPY"),
        expiry=str(policy.get("default_expiry") or "2026-04-17"),
        option_type="CALL",
        strike=float(policy.get("default_strike") or 680.0),
    )
    put_symbol = build_option_symbol(
        underlying=str(policy.get("benchmark") or "SPY"),
        expiry=str(policy.get("default_expiry") or "2026-04-17"),
        option_type="PUT",
        strike=float(policy.get("default_strike") or 680.0),
    )

    action = "hold"
    reasons: list[str] = []
    submitted_orders: list[dict[str, Any]] = []
    submission_requested = bool(allow_submission)
    allow_submission = False
    if submission_requested:
        reasons.append(OPTIONS_MUTATION_REASON)
    if open_qty == 0:
        action = "open_pair"
        reasons.append("no open option positions")
    elif same_day and not bool(policy.get("close_same_day", False)):
        action = "hold"
        reasons.append("same-day close suppressed to avoid PDT")
    elif open_qty > 0 and last_open_date and last_open_date != today:
        action = "close_open_positions"
        reasons.append(f"open positions from prior session {last_open_date}")
    else:
        action = "hold"
        reasons.append("existing open position requires next-session close")

    if action == "open_pair":
        for symbol in (call_symbol, put_symbol):
            if allow_submission:
                submitted_orders.append(
                    broker.submit_option_market_order(
                        symbol=symbol,
                        qty=1,
                        side="buy",
                        client_order_id=f"opt-smoke:{trade_date}:{symbol}",
                    )
                )
        if submitted_orders:
            last_open_date = today
            last_action_date = today
            _write_session_state(
                state_root,
                {
                    "generated_at": _now_utc(),
                    "trade_date": trade_date,
                    "asof_date": asof_date,
                    "last_action_date": last_action_date,
                    "last_open_date": last_open_date,
                    "last_close_date": last_close_date,
                    "open_symbols": [call_symbol, put_symbol],
                    "action": action,
                },
            )
    elif action == "close_open_positions":
        for pos in option_positions:
            symbol = str(pos.get("symbol") or "")
            qty = _position_qty(pos)
            if qty <= 0:
                continue
            if allow_submission:
                submitted_orders.append(
                    broker.submit_option_market_order(
                        symbol=symbol,
                        qty=float(qty),
                        side="sell",
                        client_order_id=f"opt-smoke-close:{trade_date}:{symbol}",
                    )
                )
        if submitted_orders:
            last_close_date = today
            last_action_date = today
            _write_session_state(
                state_root,
                {
                    "generated_at": _now_utc(),
                    "trade_date": trade_date,
                    "asof_date": asof_date,
                    "last_action_date": last_action_date,
                    "last_open_date": last_open_date,
                    "last_close_date": last_close_date,
                    "open_symbols": [],
                    "action": action,
                },
            )

    review = {
        "generated_at": _now_utc(),
        "trade_date": trade_date,
        "asof_date": asof_date,
        "benchmark": str(policy.get("benchmark") or "SPY"),
        "mode": str(policy.get("mode") or "paper_smoke_session"),
        "action": action,
        "reasons": reasons,
        "account": {
            "equity": (account or {}).get("equity"),
            "options_buying_power": (account or {}).get("options_buying_power"),
            "options_trading_level": (account or {}).get("options_trading_level"),
        },
        "open_option_positions": open_symbols,
        "open_option_count": open_qty,
        "session_state": {
            "last_action_date": last_action_date,
            "last_open_date": last_open_date,
            "last_close_date": last_close_date,
        },
        "submitted_orders": submitted_orders,
        "submitted_count": len(submitted_orders),
        "submission_requested": submission_requested,
        "execution_authority": OPTIONS_CAPITAL_EXECUTION_AUTHORITY,
        "execution_status": (
            "BLOCKED_OWNER_POLICY" if submission_requested else "REVIEW_ONLY"
        ),
        "policy": policy,
    }
    return review

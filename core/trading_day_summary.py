from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from core.operator_summary import load_operator_summary


def _read_json(path: Path) -> dict[str, Any] | list[Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("$", "").replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def _resolve_metric(snapshot: dict[str, Any] | None, *keys: str) -> float | None:
    if not snapshot:
        return None
    base = snapshot.get("account") if isinstance(snapshot.get("account"), dict) else snapshot
    for key in keys:
        if key in base:
            value = _float_or_none(base.get(key))
            if value is not None:
                return value
    return None


def _resolve_positions_count(snapshot: dict[str, Any] | list[Any] | None) -> int | None:
    if snapshot is None:
        return None
    if isinstance(snapshot, list):
        return len(snapshot)
    positions = snapshot.get("positions")
    if isinstance(positions, list):
        return len(positions)
    return None


def _find_snapshot(run_root: Path, phase: str, kind: str) -> dict[str, Any] | list[Any] | None:
    candidates = [
        run_root / "broker" / f"{phase}_{kind}_snapshot.json",
        Path("outputs") / "broker" / f"{phase}_{kind}_snapshot.json",
    ]
    for path in candidates:
        payload = _read_json(path)
        if payload is not None:
            return payload
    return None


def write_trading_day_summary(
    *,
    run_root: str | Path,
    run_id: str,
    trade_date: str,
) -> dict[str, Any]:
    root = Path(run_root)
    operator_summary = load_operator_summary(root)
    pretrade_account = _find_snapshot(root, "pretrade", "account")
    pretrade_positions = _find_snapshot(root, "pretrade", "positions")
    posttrade_account = _find_snapshot(root, "posttrade", "account")
    posttrade_positions = _find_snapshot(root, "posttrade", "positions")

    pretrade_cash = _resolve_metric(pretrade_account, "cash")
    pretrade_equity = _resolve_metric(pretrade_account, "equity", "portfolio_value")
    posttrade_cash = _resolve_metric(posttrade_account, "cash")
    posttrade_equity = _resolve_metric(posttrade_account, "equity", "portfolio_value")

    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "run_id": run_id,
        "trade_date": trade_date,
        "broker_authoritative_state": bool(operator_summary.get("broker_authoritative_state")),
        "pretrade_status": operator_summary.get("pretrade_status"),
        "post_execution_recon_status": operator_summary.get("post_execution_recon_status"),
        "pretrade_positions_count": _resolve_positions_count(pretrade_positions),
        "posttrade_positions_count": _resolve_positions_count(posttrade_positions),
        "pretrade_cash": pretrade_cash if pretrade_cash is not None else _float_or_none(operator_summary.get("broker_preflight_cash")),
        "pretrade_equity": pretrade_equity if pretrade_equity is not None else _float_or_none(operator_summary.get("broker_preflight_equity")),
        "posttrade_cash": posttrade_cash,
        "posttrade_equity": posttrade_equity,
        "cash_delta": (
            round(posttrade_cash - pretrade_cash, 2)
            if pretrade_cash is not None and posttrade_cash is not None
            else None
        ),
        "equity_delta": (
            round(posttrade_equity - pretrade_equity, 2)
            if pretrade_equity is not None and posttrade_equity is not None
            else None
        ),
        "positions_count_delta": (
            _resolve_positions_count(posttrade_positions) - _resolve_positions_count(pretrade_positions)
            if _resolve_positions_count(posttrade_positions) is not None
            and _resolve_positions_count(pretrade_positions) is not None
            else None
        ),
        "affected_symbols": operator_summary.get("affected_symbols") or [],
        "repair_suggestions": operator_summary.get("repair_suggestions") or [],
    }
    path = root / "trading_day_summary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload

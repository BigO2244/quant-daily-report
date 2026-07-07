from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any, Mapping

LIVE_TRADE_LEDGER_FILENAME = "live_trade_ledger.jsonl"
LIVE_PILOT_OUTPUT_ROOT_ENV = "CAERUS_LIVE_PILOT_OUTPUT_ROOT"

_LEDGER_EVENTS = {"submitted", "filled", "rejected", "expired"}


def _now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def _output_root_from_run_root(run_root: str | Path | None) -> Path | None:
    if run_root is None:
        return None
    path = Path(run_root)
    if path.parent.name == "runs":
        return path.parent.parent
    return None


def live_trade_ledger_path(
    *,
    output_root: str | Path | None = None,
    run_root: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    environ = env if env is not None else os.environ
    root = environ.get(LIVE_PILOT_OUTPUT_ROOT_ENV)
    if root:
        return Path(root) / LIVE_TRADE_LEDGER_FILENAME
    if output_root is not None:
        return Path(output_root) / LIVE_TRADE_LEDGER_FILENAME
    run_output_root = _output_root_from_run_root(run_root)
    if run_output_root is not None:
        return run_output_root / LIVE_TRADE_LEDGER_FILENAME
    return Path("outputs/live_pilot") / LIVE_TRADE_LEDGER_FILENAME


def live_order_record(
    *,
    event: str,
    symbol: Any = None,
    side: Any = None,
    qty: Any = None,
    filled_qty: Any = None,
    limit_price: Any = None,
    notional: Any = None,
    client_order_id: Any = None,
    broker_order_id: Any = None,
    run_root: str | Path | None = None,
    status: Any = None,
    reason: Any = None,
    ts_utc: str | None = None,
) -> dict[str, Any]:
    return {
        "ts_utc": ts_utc or _now_utc(),
        "event": str(event),
        "symbol": _json_safe(symbol),
        "side": _json_safe(side),
        "qty": _json_safe(qty),
        "filled_qty": _json_safe(filled_qty),
        "limit_price": _json_safe(limit_price),
        "notional": _json_safe(notional),
        "client_order_id": _json_safe(client_order_id),
        "broker_order_id": _json_safe(broker_order_id),
        "run_root": str(run_root) if run_root is not None else None,
        "status": _json_safe(status),
        "reason": _json_safe(reason),
    }


def record_live_order(
    *,
    event: str,
    symbol: Any = None,
    side: Any = None,
    qty: Any = None,
    filled_qty: Any = None,
    limit_price: Any = None,
    notional: Any = None,
    client_order_id: Any = None,
    broker_order_id: Any = None,
    run_root: str | Path | None = None,
    status: Any = None,
    reason: Any = None,
    output_root: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    ts_utc: str | None = None,
) -> None:
    try:
        event_name = str(event).strip().lower()
        if event_name not in _LEDGER_EVENTS:
            return
        path = live_trade_ledger_path(output_root=output_root, run_root=run_root, env=env)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = live_order_record(
            event=event_name,
            symbol=symbol,
            side=side,
            qty=qty,
            filled_qty=filled_qty,
            limit_price=limit_price,
            notional=notional,
            client_order_id=client_order_id,
            broker_order_id=broker_order_id,
            run_root=run_root,
            status=status,
            reason=reason,
            ts_utc=ts_utc,
        )
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:
        return

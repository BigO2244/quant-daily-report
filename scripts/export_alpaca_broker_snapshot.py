#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from brokers.alpaca_broker import AlpacaBroker

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export a redacted Alpaca broker snapshot for audit/debugging."
    )
    parser.add_argument(
        "--report-date",
        default=None,
        help="Report date in YYYY-MM-DD format. Defaults to REPORT_DATE env or today ET.",
    )
    parser.add_argument(
        "--output-dir",
        default="outputs/broker_snapshot",
        help="Directory for broker snapshot artifact.",
    )
    parser.add_argument(
        "--order-limit",
        type=int,
        default=200,
        help="Max number of recent orders to request per query.",
    )
    return parser.parse_args()


def _resolve_report_date(value: str | None) -> str:
    if value and str(value).strip():
        return str(value).strip()
    env_value = (os.getenv("REPORT_DATE") or "").strip()
    if env_value:
        return env_value
    try:
        from zoneinfo import ZoneInfo

        return dt.datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        return dt.datetime.utcnow().date().isoformat()


def _field(obj: dict[str, Any], *keys: str) -> Any:
    raw = obj.get("raw")
    raw_dict = raw if isinstance(raw, dict) else {}
    for key in keys:
        value = obj.get(key)
        if value not in (None, ""):
            return value
    for key in keys:
        value = raw_dict.get(key)
        if value not in (None, ""):
            return value
    return None


def _date_prefix(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    return text[:10]


def _order_on_report_date(order: dict[str, Any], report_date: str) -> bool:
    for key in ("submitted_at", "filled_at", "created_at", "updated_at"):
        if _date_prefix(_field(order, key)) == report_date:
            return True
    return False


def _sanitize_account(account: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _field(account, "id"),
        "status": _field(account, "status"),
        "currency": _field(account, "currency"),
        "cash": _field(account, "cash"),
        "equity": _field(account, "equity"),
        "buying_power": _field(account, "buying_power"),
        "portfolio_value": _field(account, "portfolio_value"),
        "last_equity": _field(account, "last_equity"),
        "last_maintenance_margin": _field(account, "last_maintenance_margin"),
        "pattern_day_trader": _field(account, "pattern_day_trader"),
        "trading_blocked": _field(account, "trading_blocked"),
    }


def _sanitize_position(position: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": _field(position, "symbol"),
        "side": _field(position, "side"),
        "qty": _field(position, "qty"),
        "market_value": _field(position, "market_value"),
        "current_price": _field(position, "current_price"),
        "cost_basis": _field(position, "cost_basis"),
        "unrealized_pl": _field(position, "unrealized_pl"),
        "unrealized_plpc": _field(position, "unrealized_plpc"),
    }


def _sanitize_order(order: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _field(order, "id"),
        "client_order_id": _field(order, "client_order_id"),
        "symbol": _field(order, "symbol"),
        "side": _field(order, "side"),
        "type": _field(order, "type", "order_type"),
        "time_in_force": _field(order, "time_in_force"),
        "status": _field(order, "status"),
        "qty": _field(order, "qty"),
        "filled_qty": _field(order, "filled_qty"),
        "notional": _field(order, "notional"),
        "limit_price": _field(order, "limit_price"),
        "stop_price": _field(order, "stop_price"),
        "submitted_at": _field(order, "submitted_at"),
        "filled_at": _field(order, "filled_at"),
        "canceled_at": _field(order, "canceled_at"),
        "failed_at": _field(order, "failed_at"),
    }


def _sanitize_fill(fill: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _field(fill, "id"),
        "activity_type": _field(fill, "activity_type"),
        "symbol": _field(fill, "symbol"),
        "side": _field(fill, "side"),
        "qty": _field(fill, "qty"),
        "price": _field(fill, "price"),
        "order_id": _field(fill, "order_id"),
        "transaction_time": _field(fill, "transaction_time"),
    }


def _position_sort_key(item: dict[str, Any]) -> tuple[str, str]:
    return (str(item.get("symbol") or ""), str(item.get("side") or ""))


def _order_sort_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(item.get("submitted_at") or ""),
        str(item.get("id") or ""),
        str(item.get("client_order_id") or ""),
    )


def _fill_sort_key(item: dict[str, Any]) -> tuple[str, str]:
    return (str(item.get("transaction_time") or ""), str(item.get("id") or ""))


def build_snapshot_payload(
    *,
    report_date: str,
    workflow_run_id: str | None,
    git_sha: str | None,
    account: dict[str, Any],
    positions: list[dict[str, Any]],
    orders_all: list[dict[str, Any]],
    orders_closed: list[dict[str, Any]],
    fills: list[dict[str, Any]],
) -> dict[str, Any]:
    positions_clean = sorted(
        [_sanitize_position(p) for p in positions], key=_position_sort_key
    )
    orders_report_date = sorted(
        [
            _sanitize_order(order)
            for order in orders_all
            if _order_on_report_date(order, report_date)
        ],
        key=_order_sort_key,
    )
    orders_closed_recent = sorted(
        [_sanitize_order(order) for order in orders_closed], key=_order_sort_key
    )
    fills_report_date = sorted(
        [_sanitize_fill(fill) for fill in fills], key=_fill_sort_key
    )

    return {
        "meta": {
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "report_date": report_date,
            "workflow_run_id": workflow_run_id,
            "workflow_run_attempt": os.getenv("GITHUB_RUN_ATTEMPT"),
            "git_sha": git_sha,
        },
        "account": _sanitize_account(account),
        "positions_current": positions_clean,
        "orders_report_date": orders_report_date,
        "orders_closed_recent": orders_closed_recent,
        "fills_report_date": fills_report_date,
        "counts": {
            "positions_current": len(positions_clean),
            "orders_report_date": len(orders_report_date),
            "orders_closed_recent": len(orders_closed_recent),
            "fills_report_date": len(fills_report_date),
        },
    }


def write_snapshot_json(payload: dict[str, Any], output_dir: str | Path, report_date: str) -> Path:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"broker_snapshot_{report_date}.json"
    out_path.write_text(
    json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
    encoding="utf-8",
    )
    return out_path


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args()
    report_date = _resolve_report_date(args.report_date)
    order_limit = max(1, int(args.order_limit))

    broker = AlpacaBroker.from_env()
    account = broker.get_account()
    positions = broker.get_positions()
    orders_all = broker.list_orders(status="all", limit=order_limit)
    orders_closed = broker.list_orders(status="closed", limit=order_limit)
    fills = broker.list_fills_since(report_date)

    payload = build_snapshot_payload(
        report_date=report_date,
        workflow_run_id=os.getenv("GITHUB_RUN_ID"),
        git_sha=os.getenv("GITHUB_SHA"),
        account=account,
        positions=positions,
        orders_all=orders_all,
        orders_closed=orders_closed,
        fills=fills,
    )

    out_path = write_snapshot_json(payload, args.output_dir, report_date)
    logger.info(
        "[BROKER_SNAPSHOT] wrote %s (positions=%d orders_today=%d orders_closed_recent=%d fills_today=%d)",
        out_path,
        payload["counts"]["positions_current"],
        payload["counts"]["orders_report_date"],
        payload["counts"]["orders_closed_recent"],
        payload["counts"]["fills_report_date"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

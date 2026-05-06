#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from brokers.alpaca_broker import AlpacaBroker, load_alpaca_env
from reconciliation import write_post_execution_drift_report

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
    parser.add_argument(
        "--env-file",
        default=None,
        help="Optional shell env file with Alpaca credentials (supports `export KEY=value`).",
    )
    parser.add_argument(
        "--repo-root",
        default=str(REPO_ROOT),
        help="Repository root for compatibility artifact writes.",
    )
    parser.add_argument(
        "--no-supporting-artifacts",
        action="store_true",
        help="Only write outputs/broker_snapshot/broker_snapshot_<date>.json.",
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


def load_env_file(path: str | Path | None) -> None:
    if path is None:
        return
    env_path = Path(path).expanduser()
    if not env_path.exists():
        raise FileNotFoundError(f"env file not found: {env_path}")
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if (value.startswith("'") and value.endswith("'")) or (value.startswith('"') and value.endswith('"')):
            value = value[1:-1]
        os.environ[key] = value


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


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _signed_qty(side: Any, qty: Any) -> float:
    numeric = _to_float(qty) or 0.0
    side_text = str(side or "").strip().lower()
    if side_text in {"sell", "short"}:
        return -abs(numeric)
    return abs(numeric)


def _extract_position_map(items: list[dict[str, Any]] | dict[str, Any] | None) -> dict[str, float]:
    if isinstance(items, dict):
        raw_items = items.get("positions")
        if not isinstance(raw_items, list):
            raw_items = []
    elif isinstance(items, list):
        raw_items = items
    else:
        raw_items = []
    out: dict[str, float] = {}
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        qty = _to_float(item.get("qty"))
        side = item.get("side")
        if qty is None and isinstance(item.get("raw"), dict):
            qty = _to_float(item["raw"].get("qty"))
            side = side or item["raw"].get("side")
        if qty is None:
            continue
        out[symbol] = _signed_qty(side, qty)
    return out


def _apply_fills_to_positions(expected: dict[str, float], fills: list[dict[str, Any]]) -> dict[str, float]:
    next_positions = dict(expected)
    for fill in fills:
        symbol = str(fill.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        delta = _signed_qty(fill.get("side"), fill.get("qty"))
        next_positions[symbol] = round(float(next_positions.get(symbol, 0.0)) + float(delta), 10)
        if abs(next_positions[symbol]) <= 1e-9:
            next_positions.pop(symbol, None)
    return next_positions


def _filled_orders_as_fills(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fills: list[dict[str, Any]] = []
    for order in orders or []:
        if not isinstance(order, dict):
            continue
        status = str(order.get("status") or "").strip().lower().replace("orderstatus.", "")
        if status not in {"filled", "partially_filled"}:
            continue
        symbol = str(order.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        qty = _to_float(order.get("filled_qty"))
        if qty is None:
            qty = _to_float(order.get("qty"))
        if qty is None or abs(qty) <= 1e-12:
            continue
        side = str(order.get("side") or "").strip().lower().replace("orderside.", "")
        fills.append({"symbol": symbol, "side": side, "qty": abs(qty)})
    return fills


def _market_value_total(positions: list[dict[str, Any]]) -> float | None:
    values = [_to_float(item.get("market_value")) for item in positions]
    numeric = [value for value in values if value is not None]
    if not numeric:
        return None
    return round(sum(numeric), 2)


def _rest_json(
    *,
    url: str,
    headers: dict[str, str],
    timeout: int = 20,
) -> dict[str, Any] | list[Any]:
    request = urllib.request.Request(url, headers=headers)
    context = ssl.create_default_context()
    with urllib.request.urlopen(request, context=context, timeout=timeout) as response:
        body = response.read().decode("utf-8", errors="replace")
    payload = json.loads(body)
    if isinstance(payload, (dict, list)):
        return payload
    raise RuntimeError(f"Unexpected Alpaca response type for {url}: {type(payload).__name__}")


def _rest_get(
    *,
    base_url: str,
    path: str,
    headers: dict[str, str],
    params: dict[str, Any] | None = None,
) -> dict[str, Any] | list[Any]:
    query = urllib.parse.urlencode(
        {key: value for key, value in (params or {}).items() if value not in (None, "")},
        doseq=True,
    )
    url = f"{base_url.rstrip('/')}{path}"
    if query:
        url = f"{url}?{query}"
    return _rest_json(url=url, headers=headers)


def _rest_list_fills(
    *,
    base_url: str,
    headers: dict[str, str],
    report_date: str,
    page_size: int = 100,
) -> list[dict[str, Any]]:
    fills: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        payload = _rest_get(
            base_url=base_url,
            path="/v2/account/activities/FILL",
            headers=headers,
            params={
                "date": report_date,
                "direction": "desc",
                "page_size": page_size,
                "page_token": page_token,
            },
        )
        batch = payload if isinstance(payload, list) else []
        if not batch:
            break
        fills.extend(dict(item) for item in batch if isinstance(item, dict))
        if len(batch) < page_size:
            break
        next_token = str(batch[-1].get("id") or "").strip()
        if not next_token or next_token == page_token:
            break
        page_token = next_token
    return fills


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
        "filled_avg_price": _field(order, "filled_avg_price"),
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


def fetch_snapshot_inputs(*, report_date: str, order_limit: int) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], str]:
    try:
        broker = AlpacaBroker.from_env()
        account = broker.get_account()
        positions = broker.get_positions()
        orders_all = broker.list_orders(status="all", limit=order_limit)
        orders_closed = broker.list_orders(status="closed", limit=order_limit)
        fills = broker.list_fills_since(report_date)
        return account, positions, orders_all, orders_closed, fills, "alpaca_py"
    except Exception as exc:
        if "alpaca-py is required" not in str(exc):
            raise

    cfg = load_alpaca_env()
    headers = {
        "APCA-API-KEY-ID": cfg.key_id,
        "APCA-API-SECRET-KEY": cfg.secret_key,
    }
    account = _rest_get(base_url=cfg.base_url, path="/v2/account", headers=headers)
    positions = _rest_get(base_url=cfg.base_url, path="/v2/positions", headers=headers)
    orders_all = _rest_get(
        base_url=cfg.base_url,
        path="/v2/orders",
        headers=headers,
        params={"status": "all", "limit": order_limit, "direction": "desc", "nested": "false"},
    )
    orders_closed = _rest_get(
        base_url=cfg.base_url,
        path="/v2/orders",
        headers=headers,
        params={"status": "closed", "limit": order_limit, "direction": "desc", "nested": "false"},
    )
    fills = _rest_list_fills(base_url=cfg.base_url, headers=headers, report_date=report_date)
    return (
        dict(account) if isinstance(account, dict) else {},
        [dict(item) for item in positions] if isinstance(positions, list) else [],
        [dict(item) for item in orders_all] if isinstance(orders_all, list) else [],
        [dict(item) for item in orders_closed] if isinstance(orders_closed, list) else [],
        fills,
        "alpaca_rest_api",
    )


def write_supporting_broker_artifacts(
    *,
    repo_root: Path,
    payload: dict[str, Any],
    source_mode: str,
) -> dict[str, Path]:
    broker_dir = repo_root / "outputs" / "broker"
    broker_dir.mkdir(parents=True, exist_ok=True)
    report_date = str(payload.get("meta", {}).get("report_date") or "")
    captured_at = str(payload.get("meta", {}).get("generated_at") or dt.datetime.now(dt.timezone.utc).isoformat())
    account = payload.get("account") if isinstance(payload.get("account"), dict) else {}
    positions = payload.get("positions_current") if isinstance(payload.get("positions_current"), list) else []
    market_value = _market_value_total(positions)

    posttrade_account = {
        "trade_date": report_date,
        "captured_at": captured_at,
        "source": source_mode,
        "status": account.get("status"),
        "cash": account.get("cash"),
        "equity": account.get("equity") or account.get("portfolio_value"),
        "buying_power": account.get("buying_power"),
        "portfolio_value": account.get("portfolio_value") or account.get("equity"),
        "last_equity": account.get("last_equity"),
        "account": account,
    }
    posttrade_positions = {
        "trade_date": report_date,
        "captured_at": captured_at,
        "source": source_mode,
        "positions_count": len(positions),
        "positions": positions,
    }
    broker_snapshot_latest = {
        "trade_date": report_date,
        "as_of": captured_at,
        "captured_at": captured_at,
        "source": source_mode,
        "trust_level": "authoritative",
        "status": account.get("status") or "ACTIVE",
        "cash": account.get("cash"),
        "equity": account.get("equity") or account.get("portfolio_value"),
        "portfolio_value": account.get("portfolio_value") or account.get("equity"),
        "buying_power": account.get("buying_power"),
        "last_equity": account.get("last_equity"),
        "market_value": market_value,
        "positions_count": len(positions),
        "account": account,
    }

    posttrade_account_path = broker_dir / "posttrade_account_snapshot.json"
    posttrade_positions_path = broker_dir / "posttrade_positions.json"
    latest_path = broker_dir / "broker_snapshot_latest.json"
    posttrade_account_path.write_text(json.dumps(posttrade_account, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    posttrade_positions_path.write_text(json.dumps(posttrade_positions, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    latest_path.write_text(json.dumps(broker_snapshot_latest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "posttrade_account_snapshot": posttrade_account_path,
        "posttrade_positions": posttrade_positions_path,
        "broker_snapshot_latest": latest_path,
    }


def write_posttrade_recon_from_snapshot(
    *,
    repo_root: Path,
    payload: dict[str, Any],
    report_date: str,
) -> dict[str, Any] | None:
    latest_run_path = repo_root / "outputs" / "latest_run.json"
    if not latest_run_path.exists():
        return None
    latest = json.loads(latest_run_path.read_text(encoding="utf-8"))
    if str(latest.get("trade_date") or "").strip() != report_date:
        return None
    run_root = Path(str(latest.get("run_root") or "")).expanduser()
    if not run_root.is_absolute():
        run_root = repo_root / run_root
    pretrade_positions_path = run_root / "broker" / "pretrade_positions.json"
    if not pretrade_positions_path.exists():
        return None
    pretrade_positions = json.loads(pretrade_positions_path.read_text(encoding="utf-8"))
    fills = payload.get("fills_report_date") if isinstance(payload.get("fills_report_date"), list) else []
    if not fills:
        fills = _filled_orders_as_fills(
            payload.get("orders_report_date") if isinstance(payload.get("orders_report_date"), list) else []
        )
    expected_positions = _apply_fills_to_positions(_extract_position_map(pretrade_positions), fills)
    actual_positions = _extract_position_map(
        payload.get("positions_current") if isinstance(payload.get("positions_current"), list) else [],
    )
    account = payload.get("account") if isinstance(payload.get("account"), dict) else {}
    return write_post_execution_drift_report(
        run_date=report_date,
        expected_positions=expected_positions,
        actual_positions=actual_positions,
        actual_cash=_to_float(account.get("cash")),
        actual_equity=_to_float(account.get("equity") or account.get("portfolio_value")),
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args()
    load_env_file(args.env_file)
    repo_root = Path(args.repo_root).resolve()
    report_date = _resolve_report_date(args.report_date)
    order_limit = max(1, int(args.order_limit))
    account, positions, orders_all, orders_closed, fills, source_mode = fetch_snapshot_inputs(
        report_date=report_date,
        order_limit=order_limit,
    )

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
    supporting_paths: dict[str, Path] = {}
    recon_payload: dict[str, Any] | None = None
    if not args.no_supporting_artifacts:
        supporting_paths = write_supporting_broker_artifacts(
            repo_root=repo_root,
            payload=payload,
            source_mode=source_mode,
        )
        recon_payload = write_posttrade_recon_from_snapshot(
            repo_root=repo_root,
            payload=payload,
            report_date=report_date,
        )
    logger.info(
        "[BROKER_SNAPSHOT] wrote %s (source=%s positions=%d orders_today=%d orders_closed_recent=%d fills_today=%d)",
        out_path,
        source_mode,
        payload["counts"]["positions_current"],
        payload["counts"]["orders_report_date"],
        payload["counts"]["orders_closed_recent"],
        payload["counts"]["fills_report_date"],
    )
    if supporting_paths:
        logger.info(
            "[BROKER_SNAPSHOT] supporting artifacts: %s",
            ", ".join(str(path) for path in supporting_paths.values()),
        )
    if recon_payload:
        logger.info(
            "[BROKER_SNAPSHOT] posttrade recon: %s status=%s",
            recon_payload.get("report_path"),
            recon_payload.get("drift_status"),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

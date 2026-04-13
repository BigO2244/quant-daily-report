#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze realized exit P&L and open buy P&L for a trade date."
    )
    parser.add_argument(
        "--trade-date",
        default=None,
        help="Trade date in YYYY-MM-DD format. Defaults to REPORT_DATE env or today ET.",
    )
    parser.add_argument(
        "--run-root",
        default=None,
        help="Explicit run root. Defaults to outputs/latest_run.json when it matches the trade date.",
    )
    parser.add_argument(
        "--broker-snapshot",
        default=None,
        help="Path to broker snapshot JSON. Defaults to outputs/broker_snapshot/broker_snapshot_<trade-date>.json.",
    )
    parser.add_argument(
        "--output-json",
        default=None,
        help="Optional JSON output path.",
    )
    parser.add_argument(
        "--output-csv",
        default=None,
        help="Optional CSV output path.",
    )
    return parser.parse_args()


def _resolve_trade_date(value: str | None) -> str:
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


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_side(value: Any) -> str:
    text = _normalize_text(value).upper()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text


def _resolve_run_root(trade_date: str, explicit_run_root: str | None) -> Path:
    if explicit_run_root:
        run_root = Path(explicit_run_root)
        if not run_root.exists():
            raise FileNotFoundError(f"run root not found: {run_root}")
        return run_root

    latest_run_path = Path("outputs/latest_run.json")
    if not latest_run_path.exists():
        raise FileNotFoundError(
            "Missing outputs/latest_run.json. Pass --run-root explicitly."
        )
    latest = _load_json(latest_run_path)
    if str(latest.get("trade_date") or "") != trade_date:
        raise RuntimeError(
            f"Latest run trade_date={latest.get('trade_date')} does not match requested trade_date={trade_date}. "
            "Pass --run-root explicitly."
        )
    run_root = Path(str(latest.get("run_root") or ""))
    if not run_root.exists():
        raise FileNotFoundError(f"resolved run root does not exist: {run_root}")
    return run_root


def _resolve_broker_snapshot(trade_date: str, explicit_snapshot: str | None) -> Path:
    if explicit_snapshot:
        path = Path(explicit_snapshot)
    else:
        path = Path("outputs/broker_snapshot") / f"broker_snapshot_{trade_date}.json"
    if not path.exists():
        raise FileNotFoundError(f"broker snapshot not found: {path}")
    return path


def _pretrade_position_map(pretrade_positions: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in pretrade_positions.get("positions", []):
        symbol = _normalize_text(item.get("symbol") or (item.get("raw") or {}).get("symbol")).upper()
        if not symbol:
            continue
        raw = item.get("raw") or {}
        out[symbol] = {
            "qty": _to_float(raw.get("qty")),
            "avg_entry_price": _to_float(raw.get("avg_entry_price")),
            "cost_basis": _to_float(raw.get("cost_basis")),
            "current_price": _to_float(raw.get("current_price")),
        }
    return out


def _current_position_map(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in snapshot.get("positions_current", []):
        symbol = _normalize_text(item.get("symbol")).upper()
        if not symbol:
            continue
        out[symbol] = {
            "qty": _to_float(item.get("qty")),
            "current_price": _to_float(item.get("current_price")),
            "cost_basis": _to_float(item.get("cost_basis")),
            "unrealized_pl": _to_float(item.get("unrealized_pl")),
        }
    return out


def _unique_orders(snapshot: dict[str, Any], trade_date: str) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for item in snapshot.get("orders_report_date", []):
        order_id = _normalize_text(item.get("id"))
        submitted_at = _normalize_text(item.get("submitted_at"))
        if submitted_at[:10] != trade_date:
            continue
        if not order_id:
            continue
        deduped[order_id] = dict(item)
    return sorted(
        deduped.values(),
        key=lambda item: (
            _normalize_text(item.get("submitted_at")),
            _normalize_text(item.get("symbol")),
            _normalize_text(item.get("id")),
        ),
    )


def build_trade_rows(
    *,
    trade_date: str,
    orders: list[dict[str, Any]],
    pretrade_by_symbol: dict[str, dict[str, Any]],
    current_by_symbol: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for order in orders:
        symbol = _normalize_text(order.get("symbol")).upper()
        side = _normalize_side(order.get("side"))
        qty = _to_float(order.get("filled_qty")) or _to_float(order.get("qty")) or 0.0
        fill_price = _to_float(order.get("filled_avg_price"))
        pretrade = pretrade_by_symbol.get(symbol) or {}
        current = current_by_symbol.get(symbol) or {}

        pretrade_qty = _to_float(pretrade.get("qty"))
        pretrade_avg_entry = _to_float(pretrade.get("avg_entry_price"))
        if pretrade_avg_entry is None:
            pretrade_cost_basis = _to_float(pretrade.get("cost_basis"))
            if pretrade_cost_basis is not None and pretrade_qty not in (None, 0.0):
                pretrade_avg_entry = pretrade_cost_basis / pretrade_qty

        current_price = _to_float(current.get("current_price"))
        realized_pnl = None
        open_mark_pnl = None
        if side == "SELL" and fill_price is not None and pretrade_avg_entry is not None:
            realized_pnl = qty * (fill_price - pretrade_avg_entry)
        elif side == "BUY" and fill_price is not None and current_price is not None:
            open_mark_pnl = qty * (current_price - fill_price)

        rows.append(
            {
                "trade_date": trade_date,
                "submitted_at": _normalize_text(order.get("submitted_at")),
                "filled_at": _normalize_text(order.get("filled_at")),
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "fill_price": fill_price,
                "pretrade_qty": pretrade_qty,
                "pretrade_avg_entry": pretrade_avg_entry,
                "current_position_qty": _to_float(current.get("qty")),
                "current_price": current_price,
                "status": _normalize_text(order.get("status")),
                "realized_pnl": realized_pnl,
                "open_mark_pnl": open_mark_pnl,
            }
        )
    return rows


def build_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    sells = [row for row in rows if row["side"] == "SELL"]
    buys = [row for row in rows if row["side"] == "BUY"]
    realized_values = [float(row["realized_pnl"]) for row in sells if row["realized_pnl"] is not None]
    open_values = [float(row["open_mark_pnl"]) for row in buys if row["open_mark_pnl"] is not None]
    return {
        "trade_count": len(rows),
        "sell_count": len(sells),
        "buy_count": len(buys),
        "realized_exit_pnl": round(sum(realized_values), 6),
        "open_buy_mark_pnl": round(sum(open_values), 6),
        "winning_exits": sum(1 for value in realized_values if value > 0),
        "losing_exits": sum(1 for value in realized_values if value < 0),
        "winning_buys_on_mark": sum(1 for value in open_values if value > 0),
        "losing_buys_on_mark": sum(1 for value in open_values if value < 0),
    }


def _fmt_money(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:+.2f}"


def _fmt_price(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _print_table(title: str, rows: list[dict[str, Any]], pnl_key: str) -> None:
    print(title)
    print("symbol  side  qty   fill      basis/mark  pnl")
    for row in rows:
        basis = row.get("pretrade_avg_entry") if pnl_key == "realized_pnl" else row.get("current_price")
        print(
            f"{row['symbol']:<6} "
            f"{row['side']:<4} "
            f"{row['qty']:>4.0f}  "
            f"{_fmt_price(row.get('fill_price')):>8}  "
            f"{_fmt_price(basis):>10}  "
            f"{_fmt_money(row.get(pnl_key)):>8}"
        )
    print()


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "trade_date",
        "submitted_at",
        "filled_at",
        "symbol",
        "side",
        "qty",
        "fill_price",
        "pretrade_qty",
        "pretrade_avg_entry",
        "current_position_qty",
        "current_price",
        "status",
        "realized_pnl",
        "open_mark_pnl",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    trade_date = _resolve_trade_date(args.trade_date)
    run_root = _resolve_run_root(trade_date, args.run_root)
    broker_snapshot_path = _resolve_broker_snapshot(trade_date, args.broker_snapshot)
    pretrade_positions_path = run_root / "broker" / "pretrade_positions.json"
    if not pretrade_positions_path.exists():
        raise FileNotFoundError(f"pretrade positions artifact not found: {pretrade_positions_path}")

    pretrade_positions = _load_json(pretrade_positions_path)
    broker_snapshot = _load_json(broker_snapshot_path)
    rows = build_trade_rows(
        trade_date=trade_date,
        orders=_unique_orders(broker_snapshot, trade_date),
        pretrade_by_symbol=_pretrade_position_map(pretrade_positions),
        current_by_symbol=_current_position_map(broker_snapshot),
    )
    summary = build_summary(rows)

    sells = [row for row in rows if row["side"] == "SELL"]
    buys = [row for row in rows if row["side"] == "BUY"]
    sells.sort(key=lambda row: float(row.get("realized_pnl") or 0.0), reverse=True)
    buys.sort(key=lambda row: float(row.get("open_mark_pnl") or 0.0), reverse=True)

    print(f"Trade date: {trade_date}")
    print(f"Run root: {run_root}")
    print(f"Broker snapshot: {broker_snapshot_path}")
    print(
        "Summary: "
        f"realized_exit_pnl={_fmt_money(summary['realized_exit_pnl'])} "
        f"open_buy_mark_pnl={_fmt_money(summary['open_buy_mark_pnl'])} "
        f"winning_exits={summary['winning_exits']} "
        f"losing_exits={summary['losing_exits']}"
    )
    print()
    _print_table("Realized Exits", sells, "realized_pnl")
    _print_table("Open Buys Marked To Current Price", buys, "open_mark_pnl")

    payload = {"summary": summary, "rows": rows}
    if args.output_json:
        out_path = Path(args.output_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.output_csv:
        _write_csv(Path(args.output_csv), rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

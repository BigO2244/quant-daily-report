from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


TRANSACTION_FIELDS = [
    "date",
    "timestamp",
    "source",
    "order_id",
    "activity_id",
    "ticker",
    "side",
    "quantity",
    "fill_price",
    "notional",
    "signed_notional",
    "status",
    "sleeve",
    "reason",
]

POSITION_FIELDS = [
    "as_of_date",
    "source",
    "ticker",
    "quantity",
    "current_price",
    "market_value",
    "cost_basis",
    "unrealized_pl",
    "unrealized_plpc",
    "weight",
]

NAV_FIELDS = [
    "date",
    "equity",
    "cash",
    "gross_exposure",
    "net_exposure",
    "return_1d",
    "turnover_dollars",
    "turnover_pct",
    "cumulative_return",
    "source",
]

ATTRIBUTION_FIELDS = [
    "ticker",
    "quantity",
    "market_value",
    "weight",
    "unrealized_pl",
    "unrealized_plpc",
    "buy_count",
    "sell_count",
    "net_quantity_traded",
    "traded_notional",
]


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size <= 0:
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fieldnames})
    return path


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _iso_date(value: Any) -> str | None:
    text = str(value or "").strip()
    match = re.search(r"\d{4}-\d{2}-\d{2}", text)
    if match:
        return match.group(0)
    return None


def _upper(value: Any) -> str:
    return str(value or "").strip().upper()


def _relative(repo_root: Path, path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def _snapshot_report_date(path: Path, payload: dict[str, Any]) -> str | None:
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    return (
        _iso_date(meta.get("report_date"))
        or _iso_date(payload.get("trade_date"))
        or _iso_date(payload.get("captured_at"))
        or _iso_date(path.name)
    )


def _broker_snapshot_paths(repo_root: Path) -> list[Path]:
    return sorted((repo_root / "outputs" / "broker_snapshot").glob("broker_snapshot_*.json"))


def _transaction_from_fill(fill: dict[str, Any], *, report_date: str, source: str) -> dict[str, Any] | None:
    ticker = str(fill.get("symbol") or fill.get("ticker") or "").strip().upper()
    side = _upper(fill.get("side"))
    qty = _to_float(fill.get("qty") or fill.get("filled_qty") or fill.get("quantity"))
    price = _to_float(fill.get("price") or fill.get("filled_avg_price") or fill.get("fill_price"))
    if not ticker or not side or qty is None or price is None:
        return None
    timestamp = str(fill.get("transaction_time") or fill.get("submitted_at") or fill.get("filled_at") or "").strip()
    date = _iso_date(timestamp) or report_date
    notional = abs(float(qty) * float(price))
    signed = notional if side == "BUY" else -notional if side == "SELL" else notional
    return {
        "date": date,
        "timestamp": timestamp,
        "source": source,
        "order_id": str(fill.get("order_id") or fill.get("id") or "").strip(),
        "activity_id": str(fill.get("id") or "").strip(),
        "ticker": ticker,
        "side": side,
        "quantity": abs(float(qty)),
        "fill_price": float(price),
        "notional": notional,
        "signed_notional": signed,
        "status": "FILLED",
        "sleeve": "",
        "reason": "",
    }


def _ledger_transaction(row: dict[str, Any]) -> dict[str, Any] | None:
    ticker = str(row.get("ticker") or "").strip().upper()
    side = _upper(row.get("side"))
    qty = _to_float(row.get("quantity") or row.get("shares"))
    price = _to_float(row.get("fill_price") or row.get("price"))
    notional = _to_float(row.get("notional"))
    if not ticker or not side:
        return None
    if notional is None and qty is not None and price is not None:
        notional = abs(float(qty) * float(price))
    if notional is None:
        return None
    signed = abs(notional) if side == "BUY" else -abs(notional) if side == "SELL" else notional
    return {
        "date": _iso_date(row.get("trade_date") or row.get("date")) or "",
        "timestamp": str(row.get("timestamp_et") or "").strip(),
        "source": f"ledger_{str(row.get('source') or '').strip() or 'unknown'}",
        "order_id": str(row.get("order_id") or "").strip(),
        "activity_id": "",
        "ticker": ticker,
        "side": side,
        "quantity": abs(float(qty)) if qty is not None else "",
        "fill_price": float(price) if price is not None else "",
        "notional": abs(float(notional)),
        "signed_notional": signed,
        "status": str(row.get("execution_status") or "").strip(),
        "sleeve": str(row.get("sleeve") or "").strip(),
        "reason": str(row.get("reason") or "").strip(),
    }


def _build_transactions(repo_root: Path, report_date: str | None) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    rows: list[dict[str, Any]] = []
    sources: list[str] = []
    warnings: list[str] = []
    seen: set[tuple[Any, ...]] = set()

    for path in _broker_snapshot_paths(repo_root):
        payload = _read_json(path)
        if not isinstance(payload, dict):
            continue
        snap_date = _snapshot_report_date(path, payload)
        if report_date and snap_date and snap_date > report_date:
            continue
        fills = payload.get("fills_report_date")
        fill_source = "broker_fill"
        if not isinstance(fills, list) or not fills:
            fills = payload.get("orders_report_date")
            fill_source = "broker_order"
        if not isinstance(fills, list):
            continue
        used = False
        for fill in fills:
            if not isinstance(fill, dict):
                continue
            if fill_source == "broker_order" and str(fill.get("status") or "").lower() not in {"filled", "partially_filled"}:
                continue
            tx = _transaction_from_fill(fill, report_date=snap_date or report_date or "", source=fill_source)
            if tx is None:
                continue
            key = (
                tx["source"],
                tx["activity_id"],
                tx["order_id"],
                tx["ticker"],
                tx["side"],
                tx["quantity"],
                tx["fill_price"],
                tx["timestamp"],
            )
            if key in seen:
                continue
            seen.add(key)
            rows.append(tx)
            used = True
        if used:
            sources.append(_relative(repo_root, path))

    if rows:
        rows.sort(key=lambda row: (str(row.get("date") or ""), str(row.get("timestamp") or ""), str(row.get("ticker") or "")))
        return rows, sources, warnings

    ledger_path = repo_root / "outputs" / "ledger" / "trades.csv"
    for row in _read_csv_rows(ledger_path):
        if report_date and _iso_date(row.get("trade_date") or row.get("date")) and _iso_date(row.get("trade_date") or row.get("date")) > report_date:
            continue
        tx = _ledger_transaction(row)
        if tx is not None:
            rows.append(tx)
    if rows:
        rows.sort(key=lambda row: (str(row.get("date") or ""), str(row.get("timestamp") or ""), str(row.get("ticker") or "")))
        sources.append(_relative(repo_root, ledger_path))
        warnings.append("Broker fill history unavailable; transaction table fell back to the model ledger.")
    else:
        warnings.append("No broker fills or ledger transactions were available.")
    return rows, sources, warnings


def _latest_snapshot_payload(repo_root: Path, report_date: str | None) -> tuple[dict[str, Any] | None, Path | None, str | None]:
    candidates: list[tuple[str, Path, dict[str, Any]]] = []
    for path in _broker_snapshot_paths(repo_root):
        payload = _read_json(path)
        if not isinstance(payload, dict):
            continue
        snap_date = _snapshot_report_date(path, payload)
        if report_date and snap_date and snap_date > report_date:
            continue
        candidates.append((snap_date or "", path, payload))
    if candidates:
        snap_date, path, payload = sorted(candidates, key=lambda item: (item[0], str(item[1])))[-1]
        return payload, path, snap_date

    path = repo_root / "outputs" / "broker" / "posttrade_positions.json"
    payload = _read_json(path)
    if isinstance(payload, dict):
        return payload, path, _iso_date(payload.get("trade_date") or payload.get("captured_at"))
    return None, None, None


def _build_positions(repo_root: Path, report_date: str | None) -> tuple[list[dict[str, Any]], str | None, str | None, list[str]]:
    payload, path, snap_date = _latest_snapshot_payload(repo_root, report_date)
    if not isinstance(payload, dict):
        return [], None, None, ["No broker position snapshot was available."]

    positions = payload.get("positions_current")
    if not isinstance(positions, list):
        positions = payload.get("positions")
    if not isinstance(positions, list):
        return [], _relative(repo_root, path), snap_date, ["Broker position snapshot did not contain a positions list."]

    account = payload.get("account") if isinstance(payload.get("account"), dict) else {}
    equity = _to_float(account.get("equity") or account.get("portfolio_value"))
    rows: list[dict[str, Any]] = []
    for item in positions:
        if not isinstance(item, dict):
            continue
        ticker = str(item.get("symbol") or item.get("ticker") or "").strip().upper()
        qty = _to_float(item.get("qty") or item.get("quantity") or item.get("shares"))
        market_value = _to_float(item.get("market_value"))
        if not ticker:
            continue
        rows.append(
            {
                "as_of_date": snap_date or report_date or "",
                "source": _relative(repo_root, path),
                "ticker": ticker,
                "quantity": qty,
                "current_price": _to_float(item.get("current_price") or item.get("price")),
                "market_value": market_value,
                "cost_basis": _to_float(item.get("cost_basis")),
                "unrealized_pl": _to_float(item.get("unrealized_pl")),
                "unrealized_plpc": _to_float(item.get("unrealized_plpc")),
                "weight": (market_value / equity) if market_value is not None and equity not in (None, 0) else None,
            }
        )
    rows.sort(key=lambda row: (-(abs(float(row["market_value"])) if row.get("market_value") not in (None, "") else 0.0), row["ticker"]))
    return rows, _relative(repo_root, path), snap_date, []


def _build_nav(repo_root: Path, report_date: str | None) -> tuple[list[dict[str, Any]], str | None, list[str]]:
    candidates = [
        repo_root / "outputs" / "perf" / "live_overlay_nav_series.csv",
        repo_root / "outputs" / "perf" / "nav_timeseries.csv",
    ]
    source_path = next((path for path in candidates if _read_csv_rows(path)), None)
    if source_path is None:
        return [], None, ["No NAV history CSV was available."]

    rows: list[dict[str, Any]] = []
    for raw in _read_csv_rows(source_path):
        date = _iso_date(raw.get("date"))
        if not date:
            continue
        if report_date and date > report_date:
            continue
        rows.append(
            {
                "date": date,
                "equity": _to_float(raw.get("equity") or raw.get("portfolio_value")),
                "cash": _to_float(raw.get("cash")),
                "gross_exposure": _to_float(raw.get("gross_exposure")),
                "net_exposure": _to_float(raw.get("net_exposure")),
                "return_1d": _to_float(raw.get("return_1d")),
                "turnover_dollars": _to_float(raw.get("turnover_dollars")),
                "turnover_pct": _to_float(raw.get("turnover_pct") or raw.get("turnover")),
                "source": _relative(repo_root, source_path),
            }
        )
    rows.sort(key=lambda row: row["date"])
    first_equity = next((row["equity"] for row in rows if row.get("equity") not in (None, 0)), None)
    prev_equity: float | None = None
    for row in rows:
        equity = row.get("equity")
        if row.get("return_1d") is None and equity is not None and prev_equity not in (None, 0):
            row["return_1d"] = (float(equity) / float(prev_equity)) - 1.0
        row["cumulative_return"] = (
            (float(equity) / float(first_equity)) - 1.0
            if equity is not None and first_equity not in (None, 0)
            else None
        )
        if equity is not None:
            prev_equity = float(equity)
    return rows, _relative(repo_root, source_path), []


def _build_attribution(positions: list[dict[str, Any]], transactions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tx_by_ticker: dict[str, dict[str, float]] = defaultdict(lambda: {"buy_count": 0.0, "sell_count": 0.0, "net_quantity": 0.0, "notional": 0.0})
    for tx in transactions:
        ticker = str(tx.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        side = _upper(tx.get("side"))
        qty = _to_float(tx.get("quantity")) or 0.0
        notional = _to_float(tx.get("notional")) or 0.0
        if side == "BUY":
            tx_by_ticker[ticker]["buy_count"] += 1.0
            tx_by_ticker[ticker]["net_quantity"] += qty
        elif side == "SELL":
            tx_by_ticker[ticker]["sell_count"] += 1.0
            tx_by_ticker[ticker]["net_quantity"] -= qty
        tx_by_ticker[ticker]["notional"] += abs(notional)

    rows: list[dict[str, Any]] = []
    position_tickers = set()
    for pos in positions:
        ticker = str(pos.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        position_tickers.add(ticker)
        tx = tx_by_ticker[ticker]
        rows.append(
            {
                "ticker": ticker,
                "quantity": pos.get("quantity"),
                "market_value": pos.get("market_value"),
                "weight": pos.get("weight"),
                "unrealized_pl": pos.get("unrealized_pl"),
                "unrealized_plpc": pos.get("unrealized_plpc"),
                "buy_count": int(tx["buy_count"]),
                "sell_count": int(tx["sell_count"]),
                "net_quantity_traded": tx["net_quantity"],
                "traded_notional": tx["notional"],
            }
        )
    for ticker, tx in sorted(tx_by_ticker.items()):
        if ticker in position_tickers:
            continue
        rows.append(
            {
                "ticker": ticker,
                "quantity": 0.0,
                "market_value": 0.0,
                "weight": 0.0,
                "unrealized_pl": "",
                "unrealized_plpc": "",
                "buy_count": int(tx["buy_count"]),
                "sell_count": int(tx["sell_count"]),
                "net_quantity_traded": tx["net_quantity"],
                "traded_notional": tx["notional"],
            }
        )
    rows.sort(key=lambda row: (-(abs(float(row["market_value"])) if row.get("market_value") not in (None, "") else 0.0), row["ticker"]))
    return rows


def build_portfolio_history(repo_root: Path | str = ".", *, report_date: str | None = None) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    out_dir = root / "outputs" / "portfolio_history"
    report_date = _iso_date(report_date) if report_date else None

    transactions, transaction_sources, tx_warnings = _build_transactions(root, report_date)
    positions, position_source, positions_as_of, pos_warnings = _build_positions(root, report_date)
    nav_rows, nav_source, nav_warnings = _build_nav(root, report_date)
    attribution = _build_attribution(positions, transactions)

    tx_path = _write_csv(out_dir / "transactions.csv", TRANSACTION_FIELDS, transactions)
    pos_path = _write_csv(out_dir / "positions.csv", POSITION_FIELDS, positions)
    nav_path = _write_csv(out_dir / "nav.csv", NAV_FIELDS, nav_rows)
    attr_path = _write_csv(out_dir / "attribution.csv", ATTRIBUTION_FIELDS, attribution)

    latest_nav = nav_rows[-1] if nav_rows else {}
    latest_equity = latest_nav.get("equity")
    total_traded = sum(abs(float(tx.get("notional") or 0.0)) for tx in transactions)
    summary = {
        "report_date": report_date or latest_nav.get("date") or positions_as_of,
        "as_of_date": positions_as_of or latest_nav.get("date"),
        "source_priority": [
            "broker_fills",
            "broker_positions",
            "live_overlay_nav_series",
            "model_ledger_fallback",
        ],
        "counts": {
            "transactions": len(transactions),
            "positions": len(positions),
            "nav_rows": len(nav_rows),
            "attribution_rows": len(attribution),
        },
        "latest": {
            "equity": latest_equity,
            "cash": latest_nav.get("cash"),
            "return_1d": latest_nav.get("return_1d"),
            "cumulative_return": latest_nav.get("cumulative_return"),
            "positions_count": len(positions),
            "total_traded_notional": total_traded,
            "turnover_pct": (total_traded / float(latest_equity)) if latest_equity not in (None, 0) else None,
        },
        "paths": {
            "transactions": _relative(root, tx_path),
            "positions": _relative(root, pos_path),
            "nav": _relative(root, nav_path),
            "attribution": _relative(root, attr_path),
            "transaction_sources": transaction_sources,
            "position_source": position_source or "",
            "nav_source": nav_source or "",
        },
        "warnings": tx_warnings + pos_warnings + nav_warnings,
    }
    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return {
        "summary": summary,
        "transactions": transactions,
        "positions": positions,
        "nav": nav_rows,
        "attribution": attribution,
    }


def load_portfolio_history(repo_root: Path | str = ".", *, report_date: str | None = None) -> dict[str, Any]:
    return build_portfolio_history(repo_root, report_date=report_date)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build canonical portfolio history artifacts for the dashboard.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--trade-date", default=None)
    args = parser.parse_args(argv)
    payload = build_portfolio_history(args.repo_root, report_date=args.trade_date)
    print(json.dumps({"summary": payload["summary"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

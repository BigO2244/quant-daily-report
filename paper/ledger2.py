from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from paper.paper_broker import fetch_prev_closes_yfinance

logger = logging.getLogger(__name__)

LEDGER2_COLUMNS = [
    "timestamp_et",
    "run_id",
    "source",
    "trade_date",
    "asof_date",
    "order_id",
    "ticker",
    "sleeve",
    "side",
    "quantity",
    "fill_price",
    "notional",
    "fees",
    "reason",
    "execution_status",
]


def ensure_ledger2_exists(path: str = "outputs/ledger/trades.csv") -> None:
    ledger_path = Path(path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    if ledger_path.exists() and ledger_path.stat().st_size > 0:
        return
    pd.DataFrame(columns=LEDGER2_COLUMNS).to_csv(ledger_path, index=False)


def append_ledger2_rows(rows: list[dict[str, Any]], path: str = "outputs/ledger/trades.csv") -> int:
    ensure_ledger2_exists(path)
    if not rows:
        return 0
    existing = pd.read_csv(path)
    incoming = pd.DataFrame(rows).reindex(columns=LEDGER2_COLUMNS)

    key_cols = ["trade_date", "order_id", "source"]
    if existing.empty:
        to_write = incoming
    else:
        existing_keys = set(existing[key_cols].astype(str).apply(tuple, axis=1).tolist())
        to_write = incoming[~incoming[key_cols].astype(str).apply(tuple, axis=1).isin(existing_keys)].copy()
    if to_write.empty:
        return 0
    to_write.to_csv(path, mode="a", header=False, index=False)
    return int(len(to_write))


def _deterministic_order_id(trade_date: str, ticker: str, side: str, sleeve: str) -> str:
    raw = f"{trade_date}|{ticker}|{side}|{sleeve}".encode("utf-8")
    return f"derived:{hashlib.sha1(raw).hexdigest()[:16]}"


def _asof_prices(tickers: list[str], asof_date: str) -> dict[str, float]:
    if not tickers:
        return {}
    px = fetch_prev_closes_yfinance(sorted(set(tickers)), asof_date=asof_date)
    if px.empty:
        return {}
    return {str(r["ticker"]).upper(): float(r["prev_close"]) for _, r in px.iterrows()}


def ledger2_rows_from_execution_payload(
    payload: dict[str, Any],
    trade_date: str,
    asof_date: str,
    source: str,
    run_id: str,
    execution_status: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    trades = payload.get("trades", []) if isinstance(payload, dict) else []
    if not isinstance(trades, list) or not trades:
        return [], []

    tickers = [str(t.get("ticker", "")).upper() for t in trades if t.get("ticker")]
    px_map = _asof_prices(tickers, asof_date)
    missing_prices: list[str] = []
    rows: list[dict[str, Any]] = []

    for t in trades:
        ticker = str(t.get("ticker", "")).upper()
        side = str(t.get("side", "")).upper()
        sleeve = str(t.get("sleeve") or "")
        quantity = float(t.get("shares", t.get("quantity", 0)) or 0)
        if quantity <= 0:
            continue
        order_id = str(t.get("order_id") or _deterministic_order_id(trade_date, ticker, side, sleeve))

        fill_price = t.get("fill_price")
        if fill_price is None:
            fill_price = t.get("entry_price")
        if fill_price is None:
            fill_price = t.get("price")
        if fill_price is None:
            fill_price = px_map.get(ticker)
        if fill_price is None:
            missing_prices.append(ticker)
            logger.warning("[LEDGER2] missing fill_price for %s; skipping row", ticker)
            continue

        fill_price = float(fill_price)
        rows.append(
            {
                "timestamp_et": datetime.now().astimezone().isoformat(),
                "run_id": run_id,
                "source": str(source).upper(),
                "trade_date": trade_date,
                "asof_date": asof_date,
                "order_id": order_id,
                "ticker": ticker,
                "sleeve": sleeve,
                "side": side,
                "quantity": quantity,
                "fill_price": fill_price,
                "notional": abs(quantity * fill_price),
                "fees": float(t.get("fees", 0.0) or 0.0),
                "reason": str(t.get("reason") or t.get("notes") or ""),
                "execution_status": str(execution_status).upper(),
            }
        )
    return rows, sorted(set(missing_prices))

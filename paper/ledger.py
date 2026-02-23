"""Ledger utilities for append-only execution tracking."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from paper.paths import LEDGER_TRADES_PATH
from paper.paper_broker import fetch_prev_closes_yfinance

logger = logging.getLogger(__name__)

LEDGER_COLUMNS = [
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
    "signal_hash",
    "status",
]



def ensure_output_dirs() -> None:
    """Create ledger output directories if they are missing."""
    Path("outputs/ledger").mkdir(parents=True, exist_ok=True)



def ensure_ledger_exists(path: str) -> None:
    """Create an empty ledger with the expected schema."""
    ensure_output_dirs()
    ledger_path = Path(path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    if ledger_path.exists() and ledger_path.stat().st_size > 0:
        return
    pd.DataFrame(columns=LEDGER_COLUMNS).to_csv(ledger_path, index=False)
    logger.info("[LEDGER] initialized ledger file: %s", ledger_path)



def compute_signal_hash(signals_path: str) -> str:
    """Compute a deterministic sha1 hash for a signals payload."""
    if not signals_path or not os.path.exists(signals_path):
        return ""
    with open(signals_path, "rb") as handle:
        payload = handle.read()
    return hashlib.sha1(payload).hexdigest()



def make_run_id() -> str:
    """Return a UUIDv4 run identifier."""
    return str(uuid.uuid4())



def load_ledger(path: str = str(LEDGER_TRADES_PATH)) -> pd.DataFrame:
    """Load the execution ledger."""
    ensure_ledger_exists(path)
    return pd.read_csv(path)



def append_ledger_rows(rows: list[dict[str, Any]], path: str = str(LEDGER_TRADES_PATH)) -> int:
    """Append unique rows based on (trade_date, order_id, source)."""
    ensure_ledger_exists(path)
    if not rows:
        return 0

    existing = pd.read_csv(path)
    incoming = pd.DataFrame(rows)
    incoming = incoming[LEDGER_COLUMNS]

    if existing.empty:
        to_write = incoming
    else:
        key_cols = ["trade_date", "order_id", "source"]
        existing_keys = set(existing[key_cols].astype(str).apply(tuple, axis=1).tolist())
        to_write = incoming[
            ~incoming[key_cols].astype(str).apply(tuple, axis=1).isin(existing_keys)
        ].copy()

    if to_write.empty:
        logger.info("[LEDGER] no new rows to append")
        return 0

    to_write.to_csv(path, mode="a", header=False, index=False)
    logger.info("[LEDGER] appended rows=%d path=%s", len(to_write), path)
    return int(len(to_write))



def _asof_prices_for_tickers(tickers: list[str], asof_date: str) -> dict[str, float]:
    px = fetch_prev_closes_yfinance(sorted(set(tickers)), asof_date=asof_date)
    if px.empty:
        return {}
    return {str(r["ticker"]).upper(): float(r["prev_close"]) for _, r in px.iterrows()}



def ledger_rows_from_execution_payload(
    payload_path: str,
    trade_date: str,
    asof_date: str,
    source: str,
    run_id: str,
    signal_hash: str,
) -> list[dict[str, Any]]:
    """Build normalized ledger rows from an execution payload JSON file."""
    with open(payload_path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)

    trades = payload.get("trades", []) or []
    if not trades:
        return []

    tickers = [str(t.get("ticker", "")).upper() for t in trades if t.get("ticker")]
    asof_price_map = _asof_prices_for_tickers(tickers, asof_date)
    rows: list[dict[str, Any]] = []

    for idx, trade in enumerate(trades):
        ticker = str(trade.get("ticker", "")).upper()
        side = str(trade.get("side", "")).upper()
        qty = float(trade.get("shares", trade.get("quantity", 0.0)) or 0.0)
        order_id = str(trade.get("order_id") or f"{run_id}-{ticker}-{side}-{idx}")
        sleeve = str(trade.get("sleeve") or "")
        reason = str(trade.get("reason") or "")

        fill_price = trade.get("fill_price") or trade.get("entry_price")
        if fill_price is None:
            fill_price = asof_price_map.get(ticker)
        if fill_price is None:
            raise ValueError(
                f"Missing fill_price for ticker={ticker} asof_date={asof_date}. "
                "Unable to infer from asof close prices."
            )
        fill_price = float(fill_price)

        status = "FILLED_ESTIMATE" if source.upper() == "SHADOW" else "FILLED"
        notional = abs(qty * fill_price)

        rows.append(
            {
                "timestamp_et": datetime.now().astimezone().isoformat(),
                "run_id": run_id,
                "source": source.upper(),
                "trade_date": trade_date,
                "asof_date": asof_date,
                "order_id": order_id,
                "ticker": ticker,
                "sleeve": sleeve,
                "side": side,
                "quantity": qty,
                "fill_price": fill_price,
                "notional": notional,
                "fees": 0.0,
                "reason": reason,
                "signal_hash": signal_hash,
                "status": status,
            }
        )

    return rows

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from paper.paper_broker import fetch_prev_closes_yfinance

logger = logging.getLogger(__name__)

LEDGER2_PATH = "outputs/ledger/trades.csv"
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
UNIQUE_KEY_COLS = ["trade_date", "order_id", "source"]


def ensure_dirs(path: str = LEDGER2_PATH) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def load_ledger(path: str = LEDGER2_PATH) -> pd.DataFrame:
    ensure_dirs(path)
    ledger_path = Path(path)
    if not ledger_path.exists() or ledger_path.stat().st_size == 0:
        return pd.DataFrame(columns=LEDGER2_COLUMNS)
    df = pd.read_csv(ledger_path)
    return df.reindex(columns=LEDGER2_COLUMNS)


def append_rows(path: str, rows: list[dict[str, Any]]) -> tuple[int, int]:
    ledger_df = load_ledger(path)
    incoming = pd.DataFrame(rows or []).reindex(columns=LEDGER2_COLUMNS)
    if incoming.empty:
        if ledger_df.empty and not Path(path).exists():
            incoming.to_csv(path, index=False)
        elif ledger_df.empty and Path(path).exists() and Path(path).stat().st_size == 0:
            pd.DataFrame(columns=LEDGER2_COLUMNS).to_csv(path, index=False)
        return 0, 0

    existing_keys = set()
    if not ledger_df.empty:
        existing_keys = set(ledger_df[UNIQUE_KEY_COLS].astype(str).apply(tuple, axis=1).tolist())

    incoming_keys = incoming[UNIQUE_KEY_COLS].astype(str).apply(tuple, axis=1)
    mask_new = ~incoming_keys.isin(existing_keys)
    to_append = incoming[mask_new].copy()
    skipped = int((~mask_new).sum())

    ensure_dirs(path)
    ledger_path = Path(path)
    if not ledger_path.exists() or ledger_path.stat().st_size == 0:
        pd.DataFrame(columns=LEDGER2_COLUMNS).to_csv(ledger_path, index=False)

    if to_append.empty:
        return 0, skipped

    to_append.to_csv(ledger_path, mode="a", header=False, index=False)
    return int(len(to_append)), skipped


def _default_get_price_fn(ticker: str, asof_date: str) -> float | None:
    px = fetch_prev_closes_yfinance([ticker], asof_date=asof_date)
    if px.empty:
        return None
    return float(px.iloc[0]["prev_close"])


def _normalize_payload_trades(execution_payload: dict[str, Any] | list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if isinstance(execution_payload, list):
        return [t for t in execution_payload if isinstance(t, dict)]
    if isinstance(execution_payload, dict):
        trades = execution_payload.get("trades", [])
        if isinstance(trades, list):
            return [t for t in trades if isinstance(t, dict)]
    return []


def _derived_order_id(trade_date: str, ticker: str, side: str, sleeve: str) -> str:
    return f"{trade_date}:{ticker}:{side}:{sleeve or 'main'}"


def payload_to_rows(
    execution_payload: dict[str, Any] | list[dict[str, Any]],
    trade_date: str,
    asof_date: str,
    source: str,
    run_id: str,
    signal_hash: str | None,
    get_price_fn: Callable[[str, str], float | None] | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    trades = _normalize_payload_trades(execution_payload)
    if not trades:
        return [], []

    resolved_price_fn = get_price_fn or _default_get_price_fn
    payload_status = "UNKNOWN"
    if isinstance(execution_payload, dict):
        payload_status = str(execution_payload.get("execution_status") or "UNKNOWN").upper()

    rows: list[dict[str, Any]] = []
    missing_prices: list[str] = []

    for trade in trades:
        ticker = str(trade.get("ticker") or "").upper()
        if not ticker:
            continue
        side = str(trade.get("side") or "").upper()
        sleeve = str(trade.get("sleeve") or "main")
        quantity_raw = trade.get("quantity", trade.get("shares", 0.0))
        try:
            quantity = float(quantity_raw or 0.0)
        except Exception:
            quantity = 0.0
        if quantity <= 0:
            continue

        order_id = str(trade.get("order_id") or _derived_order_id(trade_date, ticker, side, sleeve))

        fill_price = trade.get("fill_price")
        if fill_price is None:
            fill_price = trade.get("entry_price")
        if fill_price is None:
            fill_price = trade.get("price")
        if fill_price is None:
            fill_price = resolved_price_fn(ticker, asof_date)
        if fill_price is None:
            missing_prices.append(ticker)
            logger.warning("[LEDGER2] missing price for ticker=%s asof=%s", ticker, asof_date)
            continue

        fill_price = float(fill_price)
        fees = float(trade.get("fees", 0.0) or 0.0)
        notional = abs(quantity * fill_price)

        rows.append(
            {
                "timestamp_et": datetime.now().astimezone().isoformat(),
                "run_id": run_id,
                "source": str(source).upper(),
                "trade_date": str(trade_date),
                "asof_date": str(asof_date),
                "order_id": order_id,
                "ticker": ticker,
                "sleeve": sleeve,
                "side": side,
                "quantity": quantity,
                "fill_price": fill_price,
                "notional": notional,
                "fees": fees,
                "reason": str(trade.get("reason") or ""),
                "execution_status": payload_status,
            }
        )

    return rows, sorted(set(missing_prices))


# backward compatibility wrappers
ensure_ledger2_exists = ensure_dirs


def append_ledger2_rows(rows: list[dict[str, Any]], path: str = LEDGER2_PATH) -> int:
    appended, _ = append_rows(path, rows)
    return appended


def ledger2_rows_from_execution_payload(
    payload: dict[str, Any] | list[dict[str, Any]],
    trade_date: str,
    asof_date: str,
    source: str,
    run_id: str,
    execution_status: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    normalized_payload = payload
    if isinstance(payload, dict):
        normalized_payload = dict(payload)
        normalized_payload["execution_status"] = execution_status
    return payload_to_rows(
        execution_payload=normalized_payload,
        trade_date=trade_date,
        asof_date=asof_date,
        source=source,
        run_id=run_id,
        signal_hash=None,
        get_price_fn=None,
    )

from __future__ import annotations

import csv
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from paper.paths import (
    LEDGER_TRADES_PATH,
    ensure_no_legacy_ledger,
)
from paper.paper_broker import fetch_prev_closes_yfinance

logger = logging.getLogger(__name__)

LEDGER2_PATH = str(LEDGER_TRADES_PATH)
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
    "signal_hash",
    "execution_status",
]
LEDGER2_COLUMNS_NO_SIGNAL_HASH = [
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
LEGACY_LEDGER_COLUMNS_WITH_STATUS = [
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
UNIQUE_KEY_COLS = ["trade_date", "order_id"]


def ensure_dirs(path: str = LEDGER2_PATH) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def _normalize_raw_row(row: list[str]) -> dict[str, Any] | None:
    """Normalize historical ledger row shapes into canonical LEDGER2_COLUMNS."""
    n = len(row)
    if n >= len(LEDGER2_COLUMNS):
        values = list(row[: len(LEDGER2_COLUMNS)])
        return dict(zip(LEDGER2_COLUMNS, values))
    if n == len(LEDGER2_COLUMNS_NO_SIGNAL_HASH):
        out = dict(zip(LEDGER2_COLUMNS_NO_SIGNAL_HASH, row))
        out["signal_hash"] = ""
        return {col: out.get(col, "") for col in LEDGER2_COLUMNS}
    if n == len(LEGACY_LEDGER_COLUMNS_WITH_STATUS):
        out = dict(zip(LEGACY_LEDGER_COLUMNS_WITH_STATUS, row))
        out["execution_status"] = out.get("status", "")
        return {
            "timestamp_et": out.get("timestamp_et", ""),
            "run_id": out.get("run_id", ""),
            "source": out.get("source", ""),
            "trade_date": out.get("trade_date", ""),
            "asof_date": out.get("asof_date", ""),
            "order_id": out.get("order_id", ""),
            "ticker": out.get("ticker", ""),
            "sleeve": out.get("sleeve", ""),
            "side": out.get("side", ""),
            "quantity": out.get("quantity", ""),
            "fill_price": out.get("fill_price", ""),
            "notional": out.get("notional", ""),
            "fees": out.get("fees", ""),
            "reason": out.get("reason", ""),
            "signal_hash": out.get("signal_hash", ""),
            "execution_status": out.get("execution_status", ""),
        }
    return None


def _load_and_normalize_rows(path: str) -> tuple[pd.DataFrame, bool]:
    ensure_dirs(path)
    ledger_path = Path(path)
    if not ledger_path.exists() or ledger_path.stat().st_size == 0:
        return pd.DataFrame(columns=LEDGER2_COLUMNS), False

    with ledger_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        raw_rows = list(reader)

    if not raw_rows:
        return pd.DataFrame(columns=LEDGER2_COLUMNS), False

    header = [str(c).strip() for c in raw_rows[0]]
    body = raw_rows[1:]
    normalized_rows: list[dict[str, Any]] = []
    needs_rewrite = header != LEDGER2_COLUMNS

    for idx, row in enumerate(body, start=2):
        if not row or all(str(v).strip() == "" for v in row):
            continue
        normalized = _normalize_raw_row(row)
        if normalized is None:
            logger.warning(
                "[LEDGER2] skipping malformed row line=%d cols=%d path=%s",
                idx,
                len(row),
                path,
            )
            needs_rewrite = True
            continue
        if len(row) != len(LEDGER2_COLUMNS):
            needs_rewrite = True
        normalized_rows.append(normalized)

    df = pd.DataFrame(normalized_rows).reindex(columns=LEDGER2_COLUMNS)
    return df, needs_rewrite


def load_ledger(path: str = LEDGER2_PATH, *, rewrite_if_needed: bool = True) -> pd.DataFrame:
    ensure_no_legacy_ledger(logger=logger, when="load_ledger")
    df, needs_rewrite = _load_and_normalize_rows(path)
    if not df.empty and all(col in df.columns for col in UNIQUE_KEY_COLS):
        before = len(df)
        sort_cols = ["trade_date"]
        if "timestamp_et" in df.columns:
            sort_cols.append("timestamp_et")
        df = (
            df.sort_values(sort_cols, na_position="last")
            .drop_duplicates(subset=UNIQUE_KEY_COLS, keep="last")
            .reset_index(drop=True)
        )
        if len(df) != before:
            needs_rewrite = True
            logger.info(
                "[LEDGER2] dropped duplicate rows on load path=%s removed=%d key=%s",
                path,
                before - len(df),
                UNIQUE_KEY_COLS,
            )
    if rewrite_if_needed and needs_rewrite:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=False)
        logger.info("[LEDGER2] normalized ledger schema/path=%s rows=%d", path, len(df))
    return df.reindex(columns=LEDGER2_COLUMNS)


def append_rows(path: str, rows: list[dict[str, Any]]) -> tuple[int, int]:
    ensure_no_legacy_ledger(logger=logger, when="append_rows_pre")
    ledger_df = load_ledger(path, rewrite_if_needed=True)
    existing_count = int(len(ledger_df))
    incoming = pd.DataFrame(rows or []).reindex(columns=LEDGER2_COLUMNS)
    if incoming.empty:
        if ledger_df.empty and not Path(path).exists():
            incoming.to_csv(path, index=False)
        elif ledger_df.empty and Path(path).exists() and Path(path).stat().st_size == 0:
            pd.DataFrame(columns=LEDGER2_COLUMNS).to_csv(path, index=False)
        logger.info(
            "[LEDGER] path=%s rows_appended=0 total_rows=%d duplicates_removed=0",
            path,
            existing_count,
        )
        return 0, 0

    before_incoming = int(len(incoming))
    incoming = (
        incoming.sort_values(["trade_date", "timestamp_et"], na_position="last")
        .drop_duplicates(subset=UNIQUE_KEY_COLS, keep="last")
        .reset_index(drop=True)
    )
    incoming_dupes_removed = max(0, before_incoming - int(len(incoming)))

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
        duplicates_removed = incoming_dupes_removed + skipped
        logger.info(
            "[LEDGER] path=%s rows_appended=0 total_rows=%d duplicates_removed=%d",
            path,
            existing_count,
            duplicates_removed,
        )
        return 0, skipped

    to_append.to_csv(ledger_path, mode="a", header=False, index=False)
    refreshed = load_ledger(path, rewrite_if_needed=True)
    total_rows = int(len(refreshed))
    rows_appended = int(len(to_append))
    duplicates_removed = incoming_dupes_removed + skipped
    logger.info(
        "[LEDGER] path=%s rows_appended=%d total_rows=%d duplicates_removed=%d",
        path,
        rows_appended,
        total_rows,
        duplicates_removed,
    )
    ensure_no_legacy_ledger(logger=logger, when="append_rows_post")
    return rows_appended, skipped


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
    signal_hash_value = str(signal_hash or "")

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
                "signal_hash": signal_hash_value,
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


# ---------------------------------------------------------------------------
# Signal hashing (migrated from paper.ledger to consolidate ledger utilities)
# ---------------------------------------------------------------------------
import hashlib as _hashlib
import os as _os


def compute_signal_hash(signals_path: str) -> str:
    """Compute a deterministic sha1 hash for a signals payload."""
    if not signals_path or not _os.path.exists(signals_path):
        return ""
    with open(signals_path, "rb") as handle:
        payload = handle.read()
    return _hashlib.sha1(payload).hexdigest()

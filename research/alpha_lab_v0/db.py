"""DuckDB schema management for Alpha Lab research database."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    import duckdb
except ImportError:
    duckdb = None  # type: ignore


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS signals_raw (
    signal_date DATE,
    ticker TEXT,
    target_weight DOUBLE,
    score DOUBLE,
    rank INTEGER,
    sleeve TEXT,
    source_file TEXT,
    ingested_at_run_id TEXT
);

CREATE TABLE IF NOT EXISTS trades_ledger (
    trade_date DATE,
    ticker TEXT,
    side TEXT,
    qty DOUBLE,
    fill_price DOUBLE,
    order_id TEXT,
    sleeve TEXT,
    source TEXT,
    reason TEXT,
    run_id TEXT,
    source_file TEXT,
    ingested_at_run_id TEXT
);

CREATE TABLE IF NOT EXISTS positions_eod (
    asof_date DATE,
    ticker TEXT,
    shares DOUBLE,
    avg_cost DOUBLE,
    market_price DOUBLE,
    market_value DOUBLE,
    sleeve TEXT,
    ingested_at_run_id TEXT
);

CREATE TABLE IF NOT EXISTS nav_series (
    date DATE,
    nav DOUBLE,
    cash DOUBLE,
    equity_ex_cash DOUBLE,
    ingested_at_run_id TEXT
);

CREATE TABLE IF NOT EXISTS prices (
    date DATE,
    ticker TEXT,
    close DOUBLE,
    ingested_at_run_id TEXT
);

CREATE TABLE IF NOT EXISTS metrics_daily (
    date DATE,
    return DOUBLE,
    drawdown DOUBLE,
    turnover DOUBLE,
    ingested_at_run_id TEXT
);
"""


def ensure_duckdb_available() -> None:
    """Raise error if duckdb not installed."""
    if duckdb is None:
        raise RuntimeError(
            "duckdb package not installed. Install: pip install duckdb"
        )


def init_research_db(db_path: Path, run_id: str) -> duckdb.DuckDBPyConnection:
    """Initialize or connect to research database and ensure schema exists."""
    ensure_duckdb_available()
    
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(db_path))
    
    # Execute schema creation
    for statement in SCHEMA_SQL.strip().split(";"):
        if statement.strip():
            conn.execute(statement)
    
    logger.info(f"[ALPHA_LAB] Initialized DuckDB at {db_path}")
    return conn


def insert_signals(
    conn: duckdb.DuckDBPyConnection,
    signals: list[dict[str, Any]],
    run_id: str
) -> int:
    """Insert signals into signals_raw table."""
    if not signals:
        return 0
    
    conn.executemany(
        """
        INSERT INTO signals_raw 
        (signal_date, ticker, target_weight, score, rank, sleeve, source_file, ingested_at_run_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                s.get("signal_date"),
                s.get("ticker"),
                s.get("target_weight"),
                s.get("score"),
                s.get("rank"),
                s.get("sleeve"),
                s.get("source_file"),
                run_id
            )
            for s in signals
        ]
    )
    return len(signals)


def insert_trades(
    conn: duckdb.DuckDBPyConnection,
    trades: list[dict[str, Any]],
    run_id: str
) -> int:
    """Insert trades into trades_ledger table."""
    if not trades:
        return 0
    
    conn.executemany(
        """
        INSERT INTO trades_ledger 
        (trade_date, ticker, side, qty, fill_price, order_id, sleeve, 
         source, reason, run_id, source_file, ingested_at_run_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                t.get("trade_date"),
                t.get("ticker"),
                t.get("side"),
                t.get("qty"),
                t.get("fill_price"),
                t.get("order_id"),
                t.get("sleeve"),
                t.get("source"),
                t.get("reason"),
                t.get("run_id"),
                t.get("source_file"),
                run_id
            )
            for t in trades
        ]
    )
    return len(trades)


def insert_positions(
    conn: duckdb.DuckDBPyConnection,
    positions: list[dict[str, Any]],
    run_id: str
) -> int:
    """Insert positions into positions_eod table."""
    if not positions:
        return 0
    
    conn.executemany(
        """
        INSERT INTO positions_eod 
        (asof_date, ticker, shares, avg_cost, market_price, market_value, sleeve, ingested_at_run_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                p.get("asof_date"),
                p.get("ticker"),
                p.get("shares"),
                p.get("avg_cost"),
                p.get("market_price"),
                p.get("market_value"),
                p.get("sleeve"),
                run_id
            )
            for p in positions
        ]
    )
    return len(positions)


def insert_nav_series(
    conn: duckdb.DuckDBPyConnection,
    nav_rows: list[dict[str, Any]],
    run_id: str
) -> int:
    """Insert NAV series into nav_series table."""
    if not nav_rows:
        return 0
    
    conn.executemany(
        """
        INSERT INTO nav_series 
        (date, nav, cash, equity_ex_cash, ingested_at_run_id)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (
                n.get("date"),
                n.get("nav"),
                n.get("cash"),
                n.get("equity_ex_cash"),
                run_id
            )
            for n in nav_rows
        ]
    )
    return len(nav_rows)


def insert_prices(
    conn: duckdb.DuckDBPyConnection,
    prices: list[dict[str, Any]],
    run_id: str
) -> int:
    """Insert prices into prices table."""
    if not prices:
        return 0
    
    conn.executemany(
        """
        INSERT INTO prices (date, ticker, close, ingested_at_run_id)
        VALUES (?, ?, ?, ?)
        """,
        [
            (
                p.get("date"),
                p.get("ticker"),
                p.get("close"),
                run_id
            )
            for p in prices
        ]
    )
    return len(prices)

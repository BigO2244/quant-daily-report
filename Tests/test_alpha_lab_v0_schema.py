"""Test Alpha Lab V0 DuckDB schema creation."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


def test_schema_creation():
    """Test that DuckDB schema is created correctly."""
    from research.alpha_lab_v0.db import init_research_db, ensure_duckdb_available
    
    # Skip if duckdb not installed
    try:
        ensure_duckdb_available()
    except RuntimeError:
        pytest.skip("duckdb not installed")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.duckdb"
        conn = init_research_db(db_path, "test-run")
        
        # Check that tables exist
        tables = conn.execute("SHOW TABLES").fetchall()
        table_names = [t[0] for t in tables]
        
        assert "signals_raw" in table_names
        assert "trades_ledger" in table_names
        assert "positions_eod" in table_names
        assert "nav_series" in table_names
        assert "prices" in table_names
        assert "metrics_daily" in table_names
        
        conn.close()


def test_insert_signals():
    """Test inserting signals into database."""
    from research.alpha_lab_v0.db import init_research_db, insert_signals, ensure_duckdb_available
    
    try:
        ensure_duckdb_available()
    except RuntimeError:
        pytest.skip("duckdb not installed")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.duckdb"
        conn = init_research_db(db_path, "test-run")
        
        signals = [
            {
                "signal_date": "2026-01-05",
                "ticker": "AAPL",
                "target_weight": 0.25,
                "score": 95.5,
                "rank": 1,
                "sleeve": "sleeve1",
                "source_file": "test.csv"
            },
            {
                "signal_date": "2026-01-05",
                "ticker": "MSFT",
                "target_weight": 0.30,
                "score": 89.2,
                "rank": 2,
                "sleeve": "sleeve1",
                "source_file": "test.csv"
            }
        ]
        
        count = insert_signals(conn, signals, "test-run")
        assert count == 2
        
        # Verify data
        result = conn.execute("SELECT COUNT(*) FROM signals_raw").fetchone()
        assert result[0] == 2
        
        conn.close()


def test_insert_trades():
    """Test inserting trades into database."""
    from research.alpha_lab_v0.db import init_research_db, insert_trades, ensure_duckdb_available
    
    try:
        ensure_duckdb_available()
    except RuntimeError:
        pytest.skip("duckdb not installed")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.duckdb"
        conn = init_research_db(db_path, "test-run")
        
        trades = [
            {
                "trade_date": "2026-01-05",
                "ticker": "AAPL",
                "side": "BUY",
                "qty": 100.0,
                "fill_price": 180.50,
                "order_id": "ord-001",
                "sleeve": "sleeve1",
                "source": "paper",
                "reason": "open_position",
                "run_id": "test-001",
                "source_file": "ledger.csv"
            }
        ]
        
        count = insert_trades(conn, trades, "test-run")
        assert count == 1
        
        # Verify data
        result = conn.execute("SELECT COUNT(*) FROM trades_ledger").fetchone()
        assert result[0] == 1
        
        conn.close()

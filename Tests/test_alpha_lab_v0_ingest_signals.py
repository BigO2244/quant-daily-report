"""Test Alpha Lab V0 signal ingestion."""
from __future__ import annotations

from pathlib import Path

import pytest


def test_ingest_signals_from_fixtures():
    """Test ingesting signals from fixture CSV."""
    from research.alpha_lab_v0.ingest_signals import ingest_signals_from_store
    
    fixtures_path = Path("research/alpha_lab_v0/fixtures/signals_store")
    
    if not fixtures_path.exists():
        pytest.skip("Fixtures not found")
    
    signals = ingest_signals_from_store(fixtures_path, "test-run")
    
    # Should have 3 signals from 2026-01-05.csv
    assert len(signals) >= 3
    
    # Check that tickers are uppercase
    for sig in signals:
        assert sig["ticker"] == sig["ticker"].upper()
    
    # Check required fields exist
    for sig in signals:
        assert "signal_date" in sig
        assert "ticker" in sig
        assert "source_file" in sig


def test_parse_date_from_filename():
    """Test date parsing from filenames."""
    from research.alpha_lab_v0.ingest_signals import parse_date_from_filename
    
    assert parse_date_from_filename("2026-01-05.csv") == "2026-01-05"
    assert parse_date_from_filename("2026-02-23.parquet") == "2026-02-23"
    assert parse_date_from_filename("signals_2026-12-31.csv") == "2026-12-31"
    assert parse_date_from_filename("invalid.csv") is None


def test_normalize_signal_columns():
    """Test column normalization."""
    from research.alpha_lab_v0.ingest_signals import normalize_signal_columns
    import pandas as pd
    
    # Test with different weight column names
    df1 = pd.DataFrame({
        "ticker": ["AAPL", "MSFT"],
        "weight": [0.5, 0.5]
    })
    
    normalized = normalize_signal_columns(df1)
    assert "target_weight" in normalized.columns
    assert normalized["target_weight"].tolist() == [0.5, 0.5]
    
    # Test with "w" column
    df2 = pd.DataFrame({
        "ticker": ["AAPL"],
        "w": [1.0]
    })
    
    normalized = normalize_signal_columns(df2)
    assert "target_weight" in normalized.columns
    
    # Test uppercase conversion
    df3 = pd.DataFrame({
        "ticker": ["aapl", "msft"],
        "target_weight": [0.5, 0.5]
    })
    
    normalized = normalize_signal_columns(df3)
    assert normalized["ticker"].tolist() == ["AAPL", "MSFT"]


def test_compute_signal_stats():
    """Test signal statistics computation."""
    from research.alpha_lab_v0.ingest_signals import compute_signal_stats
    
    signals = [
        {"signal_date": "2026-01-05", "ticker": "AAPL", "target_weight": 0.25, "score": 95.5, "source_file": "test.csv"},
        {"signal_date": "2026-01-05", "ticker": "MSFT", "target_weight": 0.30, "score": 89.2, "source_file": "test.csv"},
        {"signal_date": "2026-01-05", "ticker": "GOOGL", "target_weight": 0.25, "score": 92.1, "source_file": "test.csv"},
        {"signal_date": "2026-01-06", "ticker": "AAPL", "target_weight": 0.40, "score": 96.0, "source_file": "test2.csv"},
    ]
    
    stats = compute_signal_stats(signals)
    
    assert stats["num_rows"] == 4
    assert stats["num_files"] == 2
    assert stats["num_dates"] == 2
    assert stats["date_range"] == ("2026-01-05", "2026-01-06")
    
    # Check weight sums
    assert stats["weight_sums"]["min"] is not None
    assert stats["weight_sums"]["max"] is not None
    
    # Check top tickers
    assert "AAPL" in stats["tickers"]
    assert stats["tickers"]["AAPL"] == 2  # Appears twice


def test_empty_signals():
    """Test handling of empty signals."""
    from research.alpha_lab_v0.ingest_signals import compute_signal_stats
    
    stats = compute_signal_stats([])
    
    assert stats["num_files"] == 0
    assert stats["num_rows"] == 0
    assert stats["num_dates"] == 0
    assert stats["date_range"] is None

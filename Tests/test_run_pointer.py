"""
Tests for canonical run pointer management.

The latest_run.json file is the single source of truth for the current trading
run's location and metadata.
"""

import json
import os
import tempfile
from pathlib import Path
import pytest

from core.run_pointer import (
    write_latest_run_pointer,
    read_latest_run_pointer,
    get_canonical_run_root,
    get_canonical_run_id,
    is_pointer_fresh,
    LATEST_RUN_POINTER,
)


class TestRunPointerWrite:
    """Test writing the canonical run pointer."""
    
    def test_write_and_read_pointer(self):
        """Should write pointer and be able to read it back."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = write_latest_run_pointer(
                run_id='20260309T093456Z_paper_1',
                trade_date='2026-03-09',
                mode='PAPER',
                run_root='outputs/runs/20260309T093456Z_paper_1/',
                status='success',
                workspace_root=tmpdir,
            )
            
            # File should exist
            assert Path(path).exists()
            pointer_data = read_latest_run_pointer(tmpdir)
            
            assert pointer_data is not None
            assert pointer_data['run_id'] == '20260309T093456Z_paper_1'
            assert pointer_data['trade_date'] == '2026-03-09'
            assert pointer_data['mode'] == 'PAPER'
            assert pointer_data['status'] == 'success'
    
    def test_pointer_file_location(self):
        """Pointer should be at outputs/latest_run.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            write_latest_run_pointer(
                run_id='test',
                trade_date='2026-03-09',
                mode='PAPER',
                run_root='outputs/runs/test/',
                workspace_root=tmpdir,
            )
            
            expected_path = Path(tmpdir) / LATEST_RUN_POINTER
            assert expected_path.exists()


class TestRunPointerRead:
    """Test reading the canonical run pointer."""
    
    def test_read_missing_pointer_returns_none(self):
        """Should return None if pointer doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = read_latest_run_pointer(tmpdir)
            assert result is None
    
    def test_read_malformed_pointer_raises(self):
        """Should raise error if pointer is malformed JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pointer_path = Path(tmpdir) / 'outputs' / 'latest_run.json'
            pointer_path.parent.mkdir(parents=True, exist_ok=True)
            pointer_path.write_text('{ invalid json }')
            
            with pytest.raises(json.JSONDecodeError):
                read_latest_run_pointer(tmpdir)


class TestGetCanonicalValues:
    """Test helper functions to extract values from pointer."""
    
    def test_get_canonical_run_root(self):
        """Should extract run_root from pointer."""
        with tempfile.TemporaryDirectory() as tmpdir:
            write_latest_run_pointer(
                run_id='test',
                trade_date='2026-03-09',
                mode='PAPER',
                run_root='outputs/runs/test/',
                workspace_root=tmpdir,
            )
            
            root = get_canonical_run_root(tmpdir)
            assert root == 'outputs/runs/test/'
    
    def test_get_canonical_run_id(self):
        """Should extract run_id from pointer."""
        with tempfile.TemporaryDirectory() as tmpdir:
            write_latest_run_pointer(
                run_id='20260309T093456Z_paper_1',
                trade_date='2026-03-09',
                mode='PAPER',
                run_root='outputs/runs/test/',
                workspace_root=tmpdir,
            )
            
            run_id = get_canonical_run_id(tmpdir)
            assert run_id == '20260309T093456Z_paper_1'
    
    def test_get_values_when_pointer_missing(self):
        """Should return None when pointer doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            assert get_canonical_run_root(tmpdir) is None
            assert get_canonical_run_id(tmpdir) is None


class TestPointerFreshness:
    """Test checking if pointer is fresh for the trading day."""
    
    def test_fresh_pointer_same_date(self):
        """Pointer is fresh if it matches trade_date and is recent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Use today's date to ensure freshness
            import datetime
            today = datetime.date.today().isoformat()
            
            write_latest_run_pointer(
                run_id='test',
                trade_date=today,
                mode='PAPER',
                run_root='outputs/runs/test/',
                workspace_root=tmpdir,
            )
            
            assert is_pointer_fresh(today, tmpdir) is True
    
    def test_stale_pointer_different_date(self):
        """Pointer is stale if trade_date doesn't match."""
        with tempfile.TemporaryDirectory() as tmpdir:
            write_latest_run_pointer(
                run_id='test',
                trade_date='2026-01-01',  # Old date
                mode='PAPER',
                run_root='outputs/runs/test/',
                workspace_root=tmpdir,
            )
            
            assert is_pointer_fresh('2026-03-09', tmpdir) is False
    
    def test_missing_pointer_not_fresh(self):
        """Missing pointer is not fresh."""
        with tempfile.TemporaryDirectory() as tmpdir:
            assert is_pointer_fresh('2026-03-09', tmpdir) is False


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

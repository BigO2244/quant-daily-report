"""
Tests for execution_summary module.
"""

import pytest
from pathlib import Path
import pandas as pd
import csv

from core.execution_summary import (
    EXECUTION_HISTORY_COLUMNS,
    build_execution_summary,
    write_execution_summary_text,
    write_execution_summary_csv,
    write_latest_execution_summary,
    update_execution_history_csv,
    _extract_tickers_by_side,
    _infer_status,
    write_execution_artifacts,
)


class TestExtractTickersBySide:
    """Test ticker extraction by trade side."""
    
    def test_extract_buys_empty(self):
        payload = {}
        assert _extract_tickers_by_side(payload, "BUY") == ""
    
    def test_extract_buys_single(self):
        payload = {
            "trades": [
                {"side": "BUY", "ticker": "AAPL"},
            ]
        }
        assert _extract_tickers_by_side(payload, "BUY") == "AAPL"
    
    def test_extract_buys_multiple(self):
        payload = {
            "trades": [
                {"side": "BUY", "ticker": "AAPL"},
                {"side": "SELL", "ticker": "XOM"},
                {"side": "BUY", "ticker": "MSFT"},
                {"side": "BUY", "ticker": "AAPL"},  # Duplicate
            ]
        }
        result = _extract_tickers_by_side(payload, "BUY")
        assert result == "AAPL|MSFT"
    
    def test_extract_sells(self):
        payload = {
            "trades": [
                {"side": "BUY", "ticker": "AAPL"},
                {"side": "SELL", "ticker": "CVX"},
                {"side": "SELL", "ticker": "XOM"},
                {"side": "BUY", "ticker": "MSFT"},
            ]
        }
        result = _extract_tickers_by_side(payload, "SELL")
        assert result == "CVX|XOM"
    
    def test_extract_with_case_insensitive(self):
        payload = {
            "trades": [
                {"side": "buy", "ticker": "aapl"},
                {"side": "BUY", "ticker": "MSFT"},
            ]
        }
        result = _extract_tickers_by_side(payload, "BUY")
        assert result == "AAPL|MSFT"
    
    def test_extract_filters_empty_tickers(self):
        payload = {
            "trades": [
                {"side": "BUY", "ticker": "AAPL"},
                {"side": "BUY", "ticker": ""},
                {"side": "BUY", "ticker": None},
            ]
        }
        result = _extract_tickers_by_side(payload, "BUY")
        assert result == "AAPL"


class TestInferStatus:
    """Test status inference from multiple sources."""
    
    def test_infer_from_health_payload(self):
        health = {"status": "PASS"}
        result = _infer_status(health, None, None, "status")
        assert result == "PASS"
    
    def test_infer_from_execution_payload(self):
        execution = {"status": "EXECUTED"}
        result = _infer_status(None, execution, None, "status")
        assert result == "EXECUTED"
    
    def test_infer_prefers_health_over_execution(self):
        health = {"status": "FAIL"}
        execution = {"status": "EXECUTED"}
        result = _infer_status(health, execution, None, "status")
        assert result == "FAIL"
    
    def test_infer_returns_unknown_when_no_source(self):
        result = _infer_status(None, None, None, "status")
        assert result == "UNKNOWN"


class TestBuildExecutionSummary:
    """Test execution summary building."""
    
    def test_summary_with_minimal_data(self):
        summary = build_execution_summary(
            run_id="test_run_1",
            trade_date="2026-03-10",
        )
        assert summary["run_id"] == "test_run_1"
        assert summary["date"] == "2026-03-10"
        assert summary["buys"] == ""
        assert summary["sells"] == ""
        assert summary["execution_status"] == "UNKNOWN"
    
    def test_summary_with_execution_payload(self):
        payload = {
            "status": "EXECUTED",
            "executable_trades_count": 3,
            "trades": [
                {"side": "BUY", "ticker": "AAPL"},
                {"side": "BUY", "ticker": "MSFT"},
                {"side": "SELL", "ticker": "XOM"},
            ],
        }
        summary = build_execution_summary(
            run_id="test_run_2",
            trade_date="2026-03-10",
            execution_payload=payload,
        )
        assert summary["buys"] == "AAPL|MSFT"
        assert summary["sells"] == "XOM"
        assert summary["buys_count"] == 2
        assert summary["sells_count"] == 1
        assert summary["payload_status"] == "CREATED"

    def test_summary_keeps_planned_intended_submitted_and_filled_distinct(self):
        payload = {
            "status": "READY",
            "execution_status": "RECONCILED_SUCCESS",
            "planned_payload_trade_count": 8,
            "execution_eligible_trades_count": 2,
            "candidate_trade_lifecycle_summary": {
                "precompute_candidates": 8,
                "passed_executable_filter": 6,
                "intended_orders": 6,
                "submitted": 2,
                "accepted": 2,
                "filled": 2,
                "clipped": 1,
                "suppressed": 6,
                "artifact_path": "outputs/runs/run/audit/candidate_trade_lifecycle_2026-06-25.json",
            },
            "candidate_trade_lifecycle": [
                {"ticker": "MAR", "side": "SELL", "submitted": True},
                {"ticker": "SPG", "side": "BUY", "submitted": True},
                {
                    "ticker": "VZ",
                    "side": "BUY",
                    "submitted": False,
                    "decision_reason": "buy_blocked_insufficient_buying_power",
                },
            ],
        }
        results = {
            "status": "RECONCILED_SUCCESS",
            "submitted_count": 2,
            "accepted_count": 2,
            "orders_filled_count": 2,
            "rejected_count": 0,
            "planned_payload_trade_count": 8,
            "executable_filter_passed_count": 6,
            "executable_trades_count": 2,
            "final_executable_trades_count": 2,
            "candidate_trade_lifecycle_summary": payload["candidate_trade_lifecycle_summary"],
            "candidate_trade_lifecycle": payload["candidate_trade_lifecycle"],
        }

        summary = build_execution_summary(
            run_id="run",
            trade_date="2026-06-25",
            execution_payload=payload,
            execution_results=results,
        )

        assert summary["planned_payload_trade_count"] == 8
        assert summary["executable_filter_passed_count"] == 6
        assert summary["executable_trades_count"] == 2
        assert summary["final_executable_trades_count"] == 2
        assert summary["intended_orders_count"] == 6
        assert summary["orders_submitted"] == 2
        assert summary["orders_accepted"] == 2
        assert summary["orders_filled"] == 2
        assert summary["orders_rejected"] == 0
        assert summary["buys"] == "SPG"
        assert summary["sells"] == "MAR"
        assert summary["execution_status"] == "SUCCESS"
        assert summary["candidate_trade_lifecycle_reasons"] == "VZ BUY:buy_blocked_insufficient_buying_power"
    
    def test_summary_with_nav_snapshot(self):
        nav = {
            "position_count": 5,
            "equity": 100000.0,
            "cash": 20000.0,
            "turnover_pct": 0.032,
        }
        summary = build_execution_summary(
            run_id="test_run_3",
            trade_date="2026-03-10",
            nav_snapshot=nav,
        )
        assert summary["total_assets_held"] == 5
        assert summary["net_asset_value"] == 100000.0
        assert summary["cash"] == 20000.0
        assert "3.20%" in str(summary["turnover"])
    
    def test_summary_with_health_error(self):
        health = {
            "status": "FAIL",
            "error": "health_check_failed",
        }
        summary = build_execution_summary(
            run_id="test_run_4",
            trade_date="2026-03-10",
            health_payload=health,
        )
        assert summary["planner_status"] == "FAILED"
        assert summary["broker_status"] == "ERROR"
        assert summary["reconciliation_status"] == "FAIL"


class TestWriteExecutionSummaryText:
    """Test text summary writing."""
    
    def test_write_summary_text(self, tmp_path):
        summary = {
            "run_id": "test_run_1",
            "date": "2026-03-10",
            "buys": "AAPL|MSFT",
            "sells": "XOM",
            "buys_count": 2,
            "sells_count": 1,
            "total_assets_held": 5,
            "net_asset_value": 100000.0,
            "cash": 20000.0,
            "turnover": "3.20%",
            "planner_status": "SUCCESS",
            "payload_status": "CREATED",
            "execution_status": "SUCCESS",
            "broker_status": "OK",
            "reconciliation_status": "PASS",
            "signals_generated": 8,
            "orders_submitted": 3,
            "orders_filled": 3,
            "orders_rejected": 0,
            "trade_decision": "EXECUTED",
            "primary_reason": "orders_submitted",
        }
        
        result_path = write_execution_summary_text(summary, tmp_path)
        
        assert result_path.exists()
        assert result_path.name == "execution_summary.txt"
        
        content = result_path.read_text()
        assert "RUN_ID: test_run_1" in content
        assert "DATE: 2026-03-10" in content
        assert "Buys: AAPL|MSFT" in content
        assert "Sells: XOM" in content
        assert "Total Assets Held: 5" in content
        assert "Net Asset Value:" in content
        assert "100,000" in content


class TestWriteExecutionSummaryCSV:
    """Test per-run CSV summary writing."""

    def test_write_summary_csv(self, tmp_path):
        summary = {col: f"value_{col}" for col in EXECUTION_HISTORY_COLUMNS}
        summary["run_id"] = "test_run_1"
        summary["date"] = "2026-03-10"

        result_path = write_execution_summary_csv(summary, tmp_path)

        assert result_path.exists()
        assert result_path.name == "execution_summary.csv"

        with result_path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            assert reader.fieldnames == EXECUTION_HISTORY_COLUMNS
            rows = list(reader)

        assert len(rows) == 1
        assert rows[0]["run_id"] == "test_run_1"
        assert rows[0]["date"] == "2026-03-10"

    def test_write_summary_csv_with_sparse_fields(self, tmp_path):
        summary = {
            "run_id": "test_run_sparse",
            "date": "2026-03-11",
            "orders_submitted": None,
            # Intentionally omitting most fields to validate default empty serialization
        }

        result_path = write_execution_summary_csv(summary, tmp_path)

        with result_path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 1
        assert rows[0]["run_id"] == "test_run_sparse"
        assert rows[0]["date"] == "2026-03-11"
        assert rows[0]["orders_submitted"] == ""
        assert rows[0]["cash"] == ""
        assert rows[0]["orders_submitted"] != "None"
        assert rows[0]["cash"] != "None"


class TestWriteLatestExecutionSummary:
    """Test latest execution summary pointer copy."""
    
    def test_write_latest_summary(self, tmp_path):
        # Override Path behavior for testing
        import core.execution_summary as es
        original_path = es.Path
        
        try:
            # Use tmp_path for testing
            es.Path = lambda p: tmp_path / p if isinstance(p, str) else p
            
            summary = {
                "run_id": "test_run_1",
                "date": "2026-03-10",
                "buys": "AAPL",
                "sells": "XOM",
                "total_assets_held": 5,
                "net_asset_value": 100000.0,
                "cash": 20000.0,
                "planner_status": "SUCCESS",
                "payload_status": "CREATED",
                "execution_status": "SUCCESS",
                "broker_status": "OK",
                "reconciliation_status": "PASS",
                "signals_generated": 8,
                "orders_submitted": 3,
                "orders_filled": 3,
                "orders_rejected": 0,
                "trade_decision": "EXECUTED",
                "primary_reason": "orders_submitted",
            }
            
            result_path = write_latest_execution_summary(summary)
            
            # Verify content was written
            content_files = list(tmp_path.rglob("*.txt"))
            assert len(content_files) > 0
            content = content_files[0].read_text()
            assert "RUN_ID: test_run_1" in content
        finally:
            es.Path = original_path


class TestUpdateExecutionHistoryCSV:
    """Test CSV export creation and updates."""
    
    def test_create_new_csv(self, tmp_path):
        csv_path = tmp_path / "execution_history.csv"
        summary = {
            "run_id": "run_1",
            "date": "2026-03-10",
            "buys": "AAPL",
            "sells": "XOM",
            "total_assets_held": 5,
            "net_asset_value": 100000.0,
            "cash": 20000.0,
            "planner_status": "SUCCESS",
            "payload_status": "CREATED",
            "execution_status": "SUCCESS",
            "broker_status": "OK",
            "reconciliation_status": "PASS",
            "signals_generated": 8,
            "orders_submitted": 3,
            "orders_filled": 3,
            "orders_rejected": 0,
            "turnover": "3.20%",
            "trade_decision": "EXECUTED",
            "primary_reason": "orders_submitted",
        }
        
        result_path = update_execution_history_csv(summary, csv_path)
        
        assert result_path.exists()
        df = pd.read_csv(result_path)
        assert len(df) == 1
        assert df.iloc[0]["run_id"] == "run_1"
        assert df.iloc[0]["date"] == "2026-03-10"
        assert df.iloc[0]["buys"] == "AAPL"
    
    def test_append_new_row_to_csv(self, tmp_path):
        csv_path = tmp_path / "execution_history.csv"
        
        # Write first row
        summary1 = {
            "run_id": "run_1",
            "date": "2026-03-10",
            "buys": "AAPL",
            "sells": "XOM",
            "total_assets_held": 5,
            "net_asset_value": 100000.0,
            "cash": 20000.0,
            "planner_status": "SUCCESS",
            "payload_status": "CREATED",
            "execution_status": "SUCCESS",
            "broker_status": "OK",
            "reconciliation_status": "PASS",
            "signals_generated": 8,
            "orders_submitted": 3,
            "orders_filled": 3,
            "orders_rejected": 0,
            "turnover": "3.20%",
            "trade_decision": "EXECUTED",
            "primary_reason": "orders_submitted",
        }
        update_execution_history_csv(summary1, csv_path)
        
        # Append second row (different date)
        summary2 = {
            "run_id": "run_2",
            "date": "2026-03-11",
            "buys": "MSFT",
            "sells": "CVX",
            "total_assets_held": 6,
            "net_asset_value": 105000.0,
            "cash": 21000.0,
            "planner_status": "SUCCESS",
            "payload_status": "CREATED",
            "execution_status": "SUCCESS",
            "broker_status": "OK",
            "reconciliation_status": "PASS",
            "signals_generated": 9,
            "orders_submitted": 2,
            "orders_filled": 2,
            "orders_rejected": 0,
            "turnover": "2.50%",
            "trade_decision": "EXECUTED",
            "primary_reason": "orders_submitted",
        }
        update_execution_history_csv(summary2, csv_path)
        
        # Verify both rows exist
        df = pd.read_csv(csv_path)
        assert len(df) == 2
        assert df[df["run_id"] == "run_1"].iloc[0]["date"] == "2026-03-10"
        assert df[df["run_id"] == "run_2"].iloc[0]["date"] == "2026-03-11"
    
    def test_update_existing_row(self, tmp_path):
        csv_path = tmp_path / "execution_history.csv"
        
        # Write first version
        summary1 = {
            "run_id": "run_1",
            "date": "2026-03-10",
            "buys": "AAPL",
            "sells": "XOM",
            "total_assets_held": 5,
            "net_asset_value": 100000.0,
            "cash": 20000.0,
            "planner_status": "SUCCESS",
            "payload_status": "CREATED",
            "execution_status": "SUCCESS",
            "broker_status": "OK",
            "reconciliation_status": "PASS",
            "signals_generated": 8,
            "orders_submitted": 3,
            "orders_filled": 3,
            "orders_rejected": 0,
            "turnover": "3.20%",
            "trade_decision": "EXECUTED",
            "primary_reason": "orders_submitted",
        }
        update_execution_history_csv(summary1, csv_path)
        
        # Update same run_id with new data
        summary1_updated = {
            "run_id": "run_1",
            "date": "2026-03-10",
            "buys": "AAPL|MSFT",  # Changed
            "sells": "XOM|CVX",  # Changed
            "total_assets_held": 6,  # Changed
            "net_asset_value": 102000.0,  # Changed
            "cash": 21000.0,
            "planner_status": "SUCCESS",
            "payload_status": "CREATED",
            "execution_status": "SUCCESS",
            "broker_status": "OK",
            "reconciliation_status": "PASS",
            "signals_generated": 8,
            "orders_submitted": 3,
            "orders_filled": 3,
            "orders_rejected": 0,
            "turnover": "3.20%",
            "trade_decision": "EXECUTED",
            "primary_reason": "orders_submitted",
        }
        update_execution_history_csv(summary1_updated, csv_path)
        
        # Verify only one row exists with updated data
        df = pd.read_csv(csv_path)
        assert len(df) == 1
        assert df.iloc[0]["buys"] == "AAPL|MSFT"
        assert df.iloc[0]["sells"] == "XOM|CVX"
        assert df.iloc[0]["total_assets_held"] == 6
        assert df.iloc[0]["net_asset_value"] == 102000.0
    
    def test_csv_sorted_by_date_then_run_id(self, tmp_path):
        csv_path = tmp_path / "execution_history.csv"
        
        # Write in reverse chronological order
        summaries = [
            ("run_3", "2026-03-12"),
            ("run_1", "2026-03-10"),
            ("run_2", "2026-03-11"),
        ]
        
        for run_id, date in summaries:
            summary = {
                "run_id": run_id,
                "date": date,
                "buys": "AAPL",
                "sells": "XOM",
                "total_assets_held": 5,
                "net_asset_value": 100000.0,
                "cash": 20000.0,
                "planner_status": "SUCCESS",
                "payload_status": "CREATED",
                "execution_status": "SUCCESS",
                "broker_status": "OK",
                "reconciliation_status": "PASS",
                "signals_generated": 8,
                "orders_submitted": 3,
                "orders_filled": 3,
                "orders_rejected": 0,
                "turnover": "3.20%",
                "trade_decision": "EXECUTED",
                "primary_reason": "orders_submitted",
            }
            update_execution_history_csv(summary, csv_path)
        
        # Verify sorted by date
        df = pd.read_csv(csv_path)
        assert len(df) == 3
        assert list(df["date"]) == ["2026-03-10", "2026-03-11", "2026-03-12"]
        assert list(df["run_id"]) == ["run_1", "run_2", "run_3"]


class TestWriteExecutionArtifacts:
    """Test the main entry point."""
    
    def test_write_artifacts_non_blocking(self, tmp_path):
        """Verify that missing data doesn't cause failures."""
        run_dir = tmp_path / "runs" / "test_run_1"
        
        summary_results = write_execution_artifacts(
            run_id="test_run_1",
            trade_date="2026-03-10",
            run_dir=run_dir,
            execution_payload=None,  # Missing
            paper_summary=None,  # Missing
            health_payload=None,  # Missing
            nav_snapshot=None,  # Missing
            reconciliation_result=None,  # Missing
        )
        
        # Should complete without errors
        assert "summary" in summary_results
        assert summary_results["summary"]["run_id"] == "test_run_1"
        assert "csv_run_path" in summary_results
        assert Path(summary_results["csv_run_path"]).exists()
        # Error fields may be present but should not block
        assert "builder_error" not in summary_results or summary_results.get("builder_error") is None

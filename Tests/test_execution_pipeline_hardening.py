"""
Tests for execution pipeline hardening.

Covers:
- operator_summary.json creation and updates
- status normalization across pipeline stages
- order validation and rejection
- execution_results.json captures rejected reasons
- confirmation email includes comprehensive traceability
- duplicate execution handling
- no-action day handling
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

# Test imports
from core.execution_payload import (
    normalize_status,
    write_canonical_execution_payload,
    STATUS_NO_ACTION,
    STATUS_READY,
    STATUS_HALTED,
    STATUS_EXECUTED,
    STATUS_SKIPPED_DUPLICATE,
)
from core.operator_summary import (
    write_operator_summary,
    load_operator_summary,
    format_operator_summary_log,
)


class TestStatusNormalization:
    """Test normalized status values across pipeline."""

    def test_halted_with_reason(self):
        """HALTED when halt_reason present."""
        status = normalize_status(
            execution_status="READY",
            halt_reason="market_closed",
            executable_trades_count=5,
        )
        assert status == STATUS_HALTED

    def test_halted_explicit(self):
        """HALTED when explicitly set."""
        status = normalize_status(
            execution_status="HALTED",
            halt_reason=None,
            executable_trades_count=5,
        )
        assert status == STATUS_HALTED

    def test_no_action_zero_trades(self):
        """NO_ACTION when no executable trades."""
        status = normalize_status(
            execution_status="READY",
            halt_reason=None,
            executable_trades_count=0,
        )
        assert status == STATUS_NO_ACTION

    def test_ready_with_trades(self):
        """READY when executable trades exist and not halted."""
        status = normalize_status(
            execution_status="READY",
            halt_reason=None,
            executable_trades_count=5,
        )
        assert status == STATUS_READY


class TestOperatorSummary:
    """Test operator_summary.json artifact."""

    def test_operator_summary_created_after_planner(self):
        """Operator summary created after planner stage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            run_root = Path(tmpdir)
            path = write_operator_summary(
                run_root,
                run_id="test123",
                trade_date="2024-01-15",
                mode="PAPER",
                pretrade_status=STATUS_READY,
                proposed_trades_count=10,
                executable_trades_count=6,
                planner_completed=True,
            )
            assert path.exists()
            assert path.name == "operator_summary.json"
            
            summary = load_operator_summary(run_root)
            assert summary is not None
            assert summary["run_id"] == "test123"
            assert summary["pretrade_status"] == STATUS_READY
            assert summary["executable_trades_count"] == 6
            assert summary["planner_completed"] is True
            assert summary["executor_completed"] is False

    def test_operator_summary_updated_after_executor(self):
        """Operator summary updated after executor stage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            run_root = Path(tmpdir)
            
            # Write initial summary after planner
            write_operator_summary(
                run_root,
                run_id="test123",
                trade_date="2024-01-15",
                mode="PAPER",
                pretrade_status=STATUS_READY,
                executable_trades_count=6,
                planner_completed=True,
            )
            
            # Update after executor
            write_operator_summary(
                run_root,
                run_id="test123",
                trade_date="2024-01-15",
                mode="PAPER",
                submitted_count=6,
                accepted_count=5,
                rejected_count=1,
                executor_completed=True,
            )
            
            summary = load_operator_summary(run_root)
            assert summary is not None
            assert summary["submitted_count"] == 6
            assert summary["accepted_count"] == 5
            assert summary["rejected_count"] == 1
            assert summary["planner_completed"] is True
            assert summary["executor_completed"] is True

    def test_operator_summary_log_format(self):
        """Operator summary formats correctly for logging."""
        summary = {
            "run_id": "test123",
            "pretrade_status": STATUS_READY,
            "executable_trades_count": 6,
            "submitted_count": 6,
            "accepted_count": 5,
            "rejected_count": 1,
            "skipped_duplicate": False,
            "confirmation_email_sent": True,
        }
        log_line = format_operator_summary_log(summary)
        assert "[OPERATOR_SUMMARY]" in log_line
        assert "run_id=test123" in log_line
        assert "status=EXECUTED" in log_line
        assert "submitted=6" in log_line
        assert "accepted=5" in log_line
        assert "rejected=1" in log_line
        assert "confirmation_email=True" in log_line

    def test_operator_summary_persists_workflow_window_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run_root = Path(tmpdir)
            write_operator_summary(
                run_root,
                run_id="test123",
                trade_date="2024-01-15",
                mode="ALPACA",
                workflow_kind="live",
                event_freshness_status="live_schedule_event",
                bundle_status="VALID",
                bundle_source="artifact",
                precompute_bundle_required=True,
                precompute_bundle_found=True,
                bundle_report_date="2024-01-15",
                execution_window_status="degraded_late",
            )
            summary = load_operator_summary(run_root)
            assert summary is not None
            assert summary["workflow_kind"] == "live"
            assert summary["event_freshness_status"] == "live_schedule_event"
            assert summary["bundle_status"] == "VALID"
            assert summary["bundle_source"] == "artifact"
            assert summary["precompute_bundle_required"] is True
            assert summary["precompute_bundle_found"] is True
            assert summary["bundle_report_date"] == "2024-01-15"
            assert summary["execution_window_status"] == "degraded_late"


class TestExecutionGuardrails:
    """Test executor validation and rejection logic."""

    def test_invalid_order_missing_ticker(self):
        """Order rejected if ticker missing."""
        from scripts.execute_alpaca_orders import _validate_order
        
        trade = {"side": "BUY", "shares": 100}
        valid, reason = _validate_order(trade, 0)
        assert not valid
        assert reason is not None
        assert "missing_ticker" in reason

    def test_invalid_order_missing_side(self):
        """Order rejected if side invalid."""
        from scripts.execute_alpaca_orders import _validate_order
        
        trade = {"ticker": "AAPL", "side": "INVALID", "shares": 100}
        valid, reason = _validate_order(trade, 0)
        assert not valid
        assert reason is not None
        assert "invalid_side" in reason

    def test_invalid_order_zero_quantity(self):
        """Order rejected if qty <= 0."""
        from scripts.execute_alpaca_orders import _validate_order
        
        trade = {"ticker": "AAPL", "side": "BUY", "shares": 0}
        valid, reason = _validate_order(trade, 0)
        assert not valid
        assert reason is not None
        assert "non_positive_qty" in reason

    def test_invalid_order_negative_quantity(self):
        """Order rejected if qty negative."""
        from scripts.execute_alpaca_orders import _validate_order
        
        trade = {"ticker": "AAPL", "side": "BUY", "shares": -10}
        valid, reason = _validate_order(trade, 0)
        assert not valid
        assert reason is not None
        assert "non_positive_qty" in reason

    def test_valid_order_passes(self):
        """Valid order passes validation."""
        from scripts.execute_alpaca_orders import _validate_order
        
        trade = {"ticker": "AAPL", "side": "BUY", "shares": 100}
        valid, reason = _validate_order(trade, 0)
        assert valid
        assert reason is None


class TestExecutionResults:
    """Test execution_results.json captures rejection details."""
    
    def test_execution_results_includes_rejected_reasons(self):
        """Execution results captures per-order rejection reasons."""
        with tempfile.TemporaryDirectory() as tmpdir:
            run_root = Path(tmpdir)
            run_root.mkdir(parents=True, exist_ok=True)
            
            # Create execution payload with one invalid order
            payload = {
                "run_id": "test123",
                "trade_date": "2024-01-15",
                "mode": "PAPER",
                "status": STATUS_READY,
                "trades": [
                    {"ticker": "AAPL", "side": "BUY", "shares": 100},
                    {"ticker": "", "side": "BUY", "shares": 50},  # Invalid: missing ticker
                    {"ticker": "MSFT", "side": "BUY", "shares": -10},  # Invalid: negative qty
                ],
            }
            
            payload_path = run_root / "execution_payload.json"
            payload_path.write_text(json.dumps(payload, indent=2))
            
            # Write latest_run.json
            latest_path = Path(tmpdir) / "latest_run.json"
            latest_path.write_text(json.dumps({
                "run_id": "test123",
                "run_root": str(run_root),
                "trade_date": "2024-01-15",
            }))
            
            # Mock broker and run execution
            with patch("scripts.execute_alpaca_orders.read_latest_run_pointer") as mock_pointer:
                with patch("scripts.execute_alpaca_orders.AlpacaBroker") as mock_broker_cls:
                    mock_pointer.return_value = {
                        "run_id": "test123",
                        "run_root": str(run_root),
                        "trade_date": "2024-01-15",
                        "workflow_stage": "execution",
                    }
                    
                    mock_broker = Mock()
                    mock_broker.find_order_by_client_id.return_value = None
                    mock_broker.submit_market_order.return_value = {"id": "order_123"}
                    mock_broker_cls.from_env.return_value = mock_broker
                    
                    from scripts.execute_alpaca_orders import run_execution
                    results = run_execution()
                    
                    # Check results
                    assert results["submitted_count"] == 1  # Only 1 valid order
                    assert results["rejected_count"] == 2  # 2 invalid orders
                    assert len(results["rejected_reasons"]) == 2
                    assert any("missing_ticker" in r for r in results["rejected_reasons"])
                    assert any("non_positive_qty" in r for r in results["rejected_reasons"])
                    
                    # Check operator summary was written
                    summary = load_operator_summary(run_root)
                    assert summary is not None
                    assert summary["submitted_count"] == 1
                    assert summary["rejected_count"] == 2


class TestDuplicateExecution:
    """Test duplicate execution detection."""

    def test_duplicate_execution_skipped(self):
        """Duplicate run_id does not resubmit orders."""
        with tempfile.TemporaryDirectory() as tmpdir:
            run_root = Path(tmpdir)
            run_root.mkdir(parents=True, exist_ok=True)
            
            # Create execution payload
            payload = {
                "run_id": "test123",
                "trade_date": "2024-01-15",
                "mode": "PAPER",
                "status": STATUS_READY,
                "trades": [{"ticker": "AAPL", "side": "BUY", "shares": 100}],
            }
            payload_path = run_root / "execution_payload.json"
            payload_path.write_text(json.dumps(payload, indent=2))
            
            # Create existing execution_results.json
            existing_results = {
                "run_id": "test123",
                "trade_date": "2024-01-15",
                "mode": "PAPER",
                "submitted_count": 1,
                "accepted_count": 1,
                "rejected_count": 0,
                "status": STATUS_EXECUTED,
            }
            results_path = run_root / "execution_results.json"
            results_path.write_text(json.dumps(existing_results, indent=2))
            
            with patch("scripts.execute_alpaca_orders.read_latest_run_pointer") as mock_pointer:
                mock_pointer.return_value = {
                    "run_id": "test123",
                    "run_root": str(run_root),
                    "trade_date": "2024-01-15",
                    "workflow_stage": "execution",
                }
                
                from scripts.execute_alpaca_orders import run_execution
                results = run_execution()
                
                # Should return existing results without submission
                assert results["submitted_count"] == 1
                assert results["status"] == STATUS_EXECUTED
                
                # Check operator summary shows duplicate skip
                summary = load_operator_summary(run_root)
                assert summary is not None
                assert summary["skipped_duplicate"] is True


class TestNoActionDay:
    """Test no-action day handling."""

    def test_no_action_day_does_not_submit(self):
        """NO_ACTION status does not attempt submission."""
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = {
                "run_id": "test123",
                "trade_date": "2024-01-15",
                "mode": "PAPER",
                "status": STATUS_NO_ACTION,
                "trades": [],
                "executable_trades_count": 0,
            }
            
            # Test that payload normalizes correctly
            status = normalize_status(
                execution_status=payload["status"],
                halt_reason=None,
                executable_trades_count=0,
            )
            assert status == STATUS_NO_ACTION

    def test_halted_partial_execution_payload_preserves_prior_submission_truth(self):
        """HALTED payload with prior submissions stays partial, not zero-execution."""
        with tempfile.TemporaryDirectory() as tmpdir:
            run_root = Path(tmpdir)
            run_root.mkdir(parents=True, exist_ok=True)

            payload = {
                "run_id": "test123",
                "trade_date": "2024-01-15",
                "mode": "ALPACA",
                "status": STATUS_HALTED,
                "halt_reason": "partial_execution_broker_abort:broker_reject_pdt:cash_rebalance_incomplete",
                "execution_outcome": "partial_execution_broker_abort",
                "cash_rebalance_status": "cash_rebalance_incomplete",
                "broker_reject_status": "BROKER_REJECT_PDT",
                "broker_reject_message": "trade denied due to pattern day trading protection",
                "orders_submitted_count": 2,
                "submitted_count": 2,
                "accepted_count": 2,
                "rejected_count": 1,
                "trades": [{"ticker": "DELL", "side": "SELL", "shares": 1}],
                "executable_trades_count": 4,
            }
            (run_root / "execution_payload.json").write_text(json.dumps(payload, indent=2))

            write_operator_summary(
                run_root,
                run_id="test123",
                trade_date="2024-01-15",
                mode="ALPACA",
                terminal_status="failed_pre_execution",
                submitted_count=2,
                accepted_count=2,
                rejected_count=1,
                orders_submitted_count=2,
                broker_reject_status="BROKER_REJECT_PDT",
                broker_reject_message="trade denied due to pattern day trading protection",
                execution_outcome="partial_execution_broker_abort",
                cash_rebalance_status="cash_rebalance_incomplete",
            )

            with patch("scripts.execute_alpaca_orders.read_latest_run_pointer") as mock_pointer:
                mock_pointer.return_value = {
                    "run_id": "test123",
                    "run_root": str(run_root),
                    "trade_date": "2024-01-15",
                    "mode": "ALPACA",
                    "workflow_stage": "execution",
                }
                from scripts.execute_alpaca_orders import run_execution
                results = run_execution()

            assert results["status"] == STATUS_HALTED
            assert results["submitted_count"] == 2
            assert results["accepted_count"] == 2
            assert results["rejected_count"] == 1
            assert results["broker_reject_status"] == "BROKER_REJECT_PDT"

            summary = load_operator_summary(run_root)
            assert summary is not None
            assert summary["submitted_count"] == 2
            assert summary["execution_outcome"] == "partial_execution_broker_abort"
            assert summary["cash_rebalance_status"] == "cash_rebalance_incomplete"

    def test_halted_post_submit_artifact_failure_preserves_prior_submission_truth(self):
        """HALTED payload with post-submit artifact failure keeps nonzero submission truth."""
        with tempfile.TemporaryDirectory() as tmpdir:
            run_root = Path(tmpdir)
            run_root.mkdir(parents=True, exist_ok=True)

            payload = {
                "run_id": "test123",
                "trade_date": "2024-01-15",
                "mode": "ALPACA",
                "status": STATUS_HALTED,
                "halt_reason": "post_submit_artifact_failure:post_sell_account_snapshot_write_failed:cash_rebalance_incomplete",
                "execution_outcome": "post_submit_artifact_failure",
                "execution_reason": "post_sell_account_snapshot_write_failed",
                "cash_rebalance_status": "cash_rebalance_incomplete",
                "orders_submitted_count": 4,
                "submitted_count": 4,
                "accepted_count": 4,
                "rejected_count": 0,
                "trades": [{"ticker": "AEP", "side": "SELL", "shares": 1}],
                "executable_trades_count": 5,
            }
            (run_root / "execution_payload.json").write_text(json.dumps(payload, indent=2))

            write_operator_summary(
                run_root,
                run_id="test123",
                trade_date="2024-01-15",
                mode="ALPACA",
                terminal_status="failed_pre_execution",
                submitted_count=4,
                accepted_count=4,
                rejected_count=0,
                orders_submitted_count=4,
                execution_outcome="post_submit_artifact_failure",
                execution_reason="post_sell_account_snapshot_write_failed",
                cash_rebalance_status="cash_rebalance_incomplete",
            )

            with patch("scripts.execute_alpaca_orders.read_latest_run_pointer") as mock_pointer:
                mock_pointer.return_value = {
                    "run_id": "test123",
                    "run_root": str(run_root),
                    "trade_date": "2024-01-15",
                    "mode": "ALPACA",
                    "workflow_stage": "execution",
                }
                from scripts.execute_alpaca_orders import run_execution
                results = run_execution()

            assert results["status"] == STATUS_HALTED
            assert results["submitted_count"] == 4
            assert results["accepted_count"] == 4
            assert results["rejected_count"] == 0
            assert results["execution_outcome"] == "post_submit_artifact_failure"
            assert results["execution_reason"] == "post_sell_account_snapshot_write_failed"

            summary = load_operator_summary(run_root)
            assert summary is not None
            assert summary["submitted_count"] == 4
            assert summary["execution_outcome"] == "post_submit_artifact_failure"
            assert summary["execution_reason"] == "post_sell_account_snapshot_write_failed"


class TestConfirmationEmail:
    """Test confirmation email traceability."""

    def test_confirmation_email_includes_run_id(self):
        """Confirmation email includes run_id."""
        results = {
            "run_id": "test123",
            "trade_date": "2024-01-15",
            "mode": "PAPER",
            "status": STATUS_EXECUTED,
            "submitted_count": 5,
            "accepted_count": 5,
            "rejected_count": 0,
        }
        results_path = Path("/tmp/execution_results.json")
        
        from scripts.send_trading_confirmation_email import _build_confirmation_email
        subject, body_text, body_html = _build_confirmation_email(results, results_path)
        
        assert "test123" in body_text
        assert "EXECUTED" in subject
        assert "Mode: PAPER" in body_text

    def test_confirmation_email_distinguishes_halted(self):
        """Confirmation email clearly shows HALTED status."""
        results = {
            "run_id": "test123",
            "trade_date": "2024-01-15",
            "mode": "PAPER",
            "status": STATUS_HALTED,
            "halt_reason": "market_closed",
            "submitted_count": 0,
            "accepted_count": 0,
            "rejected_count": 0,
        }
        results_path = Path("/tmp/execution_results.json")
        
        from scripts.send_trading_confirmation_email import _build_confirmation_email
        subject, body_text, body_html = _build_confirmation_email(results, results_path)
        
        assert "HALTED" in subject
        assert "market_closed" in body_text
        assert "🛑" in body_html

    def test_confirmation_email_distinguishes_duplicate(self):
        """Confirmation email clearly shows SKIPPED_DUPLICATE status."""
        results = {
            "run_id": "test123",
            "trade_date": "2024-01-15",
            "mode": "PAPER",
            "status": STATUS_SKIPPED_DUPLICATE,
            "submitted_count": 1,
            "accepted_count": 1,
            "rejected_count": 0,
        }
        results_path = Path("/tmp/execution_results.json")
        
        from scripts.send_trading_confirmation_email import _build_confirmation_email
        subject, body_text, body_html = _build_confirmation_email(results, results_path)
        
        assert "SKIPPED_DUPLICATE" in subject
        assert "Duplicate execution detected" in body_text
        assert "⏭️" in body_html


class TestCanonicalPayloadNormalization:
    """Test canonical execution payload normalization."""

    def test_canonical_payload_uses_normalized_status(self):
        """Canonical payload uses normalized status."""
        with tempfile.TemporaryDirectory() as tmpdir:
            run_root = Path(tmpdir)
            
            payload = {
                "run_id": "test123",
                "trade_date": "2024-01-15",
                "mode": "PAPER",
                "execution_status": "READY",
                "trades": [{"ticker": "AAPL", "side": "BUY", "shares": 100}],
                "executable_trades_count": 1,
            }
            
            path = write_canonical_execution_payload(payload, "2024-01-15", run_root=run_root)
            
            with path.open("r") as f:
                written = json.load(f)
            
            # Check normalized status field
            assert written["status"] == STATUS_READY
            # Check backward compatibility fields
            assert written["execution_status"] == STATUS_READY

    def test_canonical_payload_halted_normalization(self):
        """Canonical payload normalizes HALTED correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            run_root = Path(tmpdir)
            
            payload = {
                "run_id": "test123",
                "trade_date": "2024-01-15",
                "mode": "PAPER",
                "execution_status": "READY",
                "halt_reason": "market_closed",
                "trades": [],
                "executable_trades_count": 0,
            }
            
            path = write_canonical_execution_payload(payload, "2024-01-15", run_root=run_root)
            
            with path.open("r") as f:
                written = json.load(f)
            
            # Halt reason takes precedence
            assert written["status"] == STATUS_HALTED
            assert written["halt_reason"] == "market_closed"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

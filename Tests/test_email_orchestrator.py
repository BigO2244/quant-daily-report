"""Tests for core/email_orchestrator.py - PRE/POST/ALERT email consolidation."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.email_orchestrator import (
    orchestrate_email,
    send_alert_email,
    send_post_email,
    send_pre_email,
    _check_email_sent,
    _get_idempotency_key,
    _mark_email_sent,
    _redact_secrets,
)


@pytest.fixture
def temp_run_root(tmp_path):
    """Create a temporary run root directory."""
    run_root = tmp_path / "runs" / "test_run"
    run_root.mkdir(parents=True, exist_ok=True)
    return run_root


@pytest.fixture
def execution_payload_with_trades():
    """Sample execution payload with trades."""
    return {
        "trade_date": "2026-03-02",
        "mode": "SHADOW",
        "execution_status": "READY",
        "trades": [
            {
                "ticker": "AAPL",
                "side": "BUY",
                "shares": 10,
                "entry_price": 180.0,
                "notional": 1800.0,
            },
            {
                "ticker": "MSFT",
                "side": "SELL",
                "shares": 5,
                "entry_price": 400.0,
                "notional": 2000.0,
            },
        ],
    }


@pytest.fixture
def execution_payload_no_trades():
    """Sample execution payload without trades."""
    return {
        "trade_date": "2026-03-02",
        "mode": "SHADOW",
        "execution_status": "READY",
        "trades": [],
        "no_trades_reason": "No signals above threshold",
    }


@pytest.fixture
def paper_summary_with_fills():
    """Sample paper summary with fills."""
    return {
        "trading_mode": "SHADOW",
        "fills": [
            {"ticker": "AAPL", "shares": 10, "fill_price": 180.5},
            {"ticker": "MSFT", "shares": 5, "fill_price": 399.8},
        ],
        "execution_trades": [
            {"ticker": "AAPL", "shares": 10},
            {"ticker": "MSFT", "shares": 5},
        ],
        "reconciliation": {
            "status": "MATCH",
            "target_cash_pct": 0.15,
            "achieved_cash_pct": 0.148,
            "invariant_failures": None,
        },
    }


@pytest.fixture
def paper_summary_no_fills():
    """Sample paper summary without fills."""
    return {
        "trading_mode": "SHADOW",
        "fills": [],
        "execution_trades": [],
        "reconciliation": {
            "status": "MATCH",
            "target_cash_pct": 0.15,
            "achieved_cash_pct": 0.15,
            "invariant_failures": None,
        },
    }


# ========== Idempotency Tests ==========


def test_idempotency_key_generation():
    """Test that idempotency keys are deterministic and unique per context."""
    key1 = _get_idempotency_key("2026-03-02", "SHADOW", "PRE", "run123")
    key2 = _get_idempotency_key("2026-03-02", "SHADOW", "PRE", "run123")
    key3 = _get_idempotency_key("2026-03-02", "SHADOW", "POST", "run123")
    key4 = _get_idempotency_key("2026-03-03", "SHADOW", "PRE", "run123")

    assert key1 == key2, "Same inputs should produce same key"
    assert key1 != key3, "Different email_type should produce different key"
    assert key1 != key4, "Different trade_date should produce different key"


def test_check_email_sent_returns_false_when_not_sent(temp_run_root):
    """Test that check_email_sent returns False when marker doesn't exist."""
    assert _check_email_sent(temp_run_root, "PRE") is False


def test_mark_and_check_email_sent(temp_run_root):
    """Test that marking email as sent creates a marker file that can be checked."""
    _mark_email_sent(
        run_root=temp_run_root,
        email_type="PRE",
        trade_date="2026-03-02",
        mode="SHADOW",
        run_id="run123",
        subject="PROPOSED TRADES — 2026-03-02 (SHADOW)",
    )

    assert _check_email_sent(temp_run_root, "PRE") is True

    # Verify marker file contains correct data
    marker_path = temp_run_root / "email_sent" / "pre.json"
    assert marker_path.exists()

    marker_data = json.loads(marker_path.read_text())
    assert marker_data["email_type"] == "PRE"
    assert marker_data["trade_date"] == "2026-03-02"
    assert marker_data["mode"] == "SHADOW"
    assert marker_data["run_id"] == "run123"
    assert marker_data["subject"] == "PROPOSED TRADES — 2026-03-02 (SHADOW)"
    assert "sent_at" in marker_data
    assert "idempotency_key" in marker_data


def test_dedup_prevents_duplicate_email(temp_run_root, execution_payload_with_trades):
    """Test that second email send with same key is skipped (idempotency)."""
    with patch("core.email_orchestrator.send_email") as mock_send:
        # First send - should succeed
        result1 = send_pre_email(
            execution_payload=execution_payload_with_trades,
            run_root=temp_run_root,
            trade_date="2026-03-02",
            mode="SHADOW",
            run_id="run123",
        )
        assert result1 is True
        assert mock_send.call_count == 1

        # Second send with same parameters - should be skipped
        result2 = send_pre_email(
            execution_payload=execution_payload_with_trades,
            run_root=temp_run_root,
            trade_date="2026-03-02",
            mode="SHADOW",
            run_id="run123",
        )
        assert result2 is False, "Second send should be skipped"
        assert mock_send.call_count == 1, "Email should not be sent twice"


def test_force_flag_bypasses_idempotency(temp_run_root, execution_payload_with_trades):
    """Test that force=True bypasses idempotency check."""
    with patch("core.email_orchestrator.send_email") as mock_send:
        # First send
        send_pre_email(
            execution_payload=execution_payload_with_trades,
            run_root=temp_run_root,
            trade_date="2026-03-02",
            mode="SHADOW",
            run_id="run123",
        )

        # Second send with force=True should succeed
        result = send_pre_email(
            execution_payload=execution_payload_with_trades,
            run_root=temp_run_root,
            trade_date="2026-03-02",
            mode="SHADOW",
            run_id="run123",
            force=True,
        )
        assert result is True
        assert mock_send.call_count == 2, "Force should bypass idempotency"


# ========== PRE Email Tests ==========


def test_pre_email_with_trades(temp_run_root, execution_payload_with_trades):
    """Test PRE email sends successfully with trades."""
    with patch("core.email_orchestrator.send_email") as mock_send:
        result = send_pre_email(
            execution_payload=execution_payload_with_trades,
            run_root=temp_run_root,
            trade_date="2026-03-02",
            mode="SHADOW",
            run_id="run123",
        )

        assert result is True
        assert mock_send.call_count == 1

        # Check subject format
        call_args = mock_send.call_args
        assert call_args.kwargs["subject"] == "PROPOSED TRADES — 2026-03-02 (SHADOW)"


def test_pre_email_without_trades(temp_run_root, execution_payload_no_trades):
    """Test PRE email sends successfully even without trades (NO TRADE decision)."""
    with patch("core.email_orchestrator.send_email") as mock_send:
        result = send_pre_email(
            execution_payload=execution_payload_no_trades,
            run_root=temp_run_root,
            trade_date="2026-03-02",
            mode="SHADOW",
            run_id="run123",
        )

        assert result is True
        assert mock_send.call_count == 1

        # Verify NO TRADE content appears
        call_args = mock_send.call_args
        body_text = call_args.kwargs["body_text"]
        assert "no_trades_reason" in execution_payload_no_trades or "NO TRADE" in body_text.upper()


# ========== POST Email Tests ==========


def test_post_email_with_fills(temp_run_root, execution_payload_with_trades, paper_summary_with_fills):
    """Test POST email sends successfully with fills."""
    with patch("core.email_orchestrator.send_email") as mock_send:
        result = send_post_email(
            execution_payload=execution_payload_with_trades,
            paper_summary=paper_summary_with_fills,
            run_root=temp_run_root,
            trade_date="2026-03-02",
            mode="SHADOW",
            run_id="run123",
        )

        assert result is True
        assert mock_send.call_count == 1

        # Check subject format
        call_args = mock_send.call_args
        assert call_args.kwargs["subject"] == "EXECUTION REPORT — 2026-03-02 (SHADOW)"

        # Verify reconciliation info is included
        body_text = call_args.kwargs["body_text"]
        assert "RECONCILIATION" in body_text


def test_post_email_without_fills(temp_run_root, execution_payload_no_trades, paper_summary_no_fills):
    """Test POST email sends successfully even without fills."""
    with patch("core.email_orchestrator.send_email") as mock_send:
        result = send_post_email(
            execution_payload=execution_payload_no_trades,
            paper_summary=paper_summary_no_fills,
            run_root=temp_run_root,
            trade_date="2026-03-02",
            mode="SHADOW",
            run_id="run123",
        )

        assert result is True
        assert mock_send.call_count == 1


def test_post_email_missing_paper_summary_raises(temp_run_root, execution_payload_with_trades):
    """Test POST email raises ValueError when paper_summary is missing."""
    with pytest.raises(ValueError, match="POST email requires paper_summary"):
        send_post_email(
            execution_payload=execution_payload_with_trades,
            paper_summary=None,
            run_root=temp_run_root,
            trade_date="2026-03-02",
            mode="SHADOW",
            run_id="run123",
        )


# ========== ALERT Email Tests ==========


def test_alert_email_on_failure(temp_run_root):
    """Test ALERT email sends on pipeline failure."""
    with patch("core.email_orchestrator.send_email") as mock_send:
        result = send_alert_email(
            error_summary="Test error: execution failed",
            trade_date="2026-03-02",
            mode="SHADOW",
            step="POST",
            run_root=temp_run_root,
            run_id="run123",
            workflow_url="https://github.com/repo/actions/runs/123",
        )

        assert result is True
        assert mock_send.call_count == 1

        # Check subject format
        call_args = mock_send.call_args
        assert call_args.kwargs["subject"] == "ALERT — Quant Daily pipeline failed — 2026-03-02 (POST)"

        # Verify error summary and investigation info
        body_text = call_args.kwargs["body_text"]
        assert "Test error: execution failed" in body_text
        assert "https://github.com/repo/actions/runs/123" in body_text
        assert str(temp_run_root) in body_text


def test_alert_email_redacts_secrets(temp_run_root):
    """Test that ALERT email redacts secrets from error messages."""
    with patch("core.email_orchestrator.send_email") as mock_send:
        send_alert_email(
            error_summary="Error: ALPACA_API_KEY=secret123 failed to authenticate",
            trade_date="2026-03-02",
            mode="SHADOW",
            step="PRE",
            run_root=temp_run_root,
        )

        call_args = mock_send.call_args
        body_text = call_args.kwargs["body_text"]

        # Secret should be redacted
        assert "secret123" not in body_text
        assert "[REDACTED]" in body_text


def test_secret_redaction():
    """Test secret redaction utility function."""
    text_with_secrets = "Error: ALPACA_API_KEY_ID=key123 and SECRET_TOKEN=tok456"
    redacted = _redact_secrets(text_with_secrets)

    assert "key123" not in redacted
    assert "tok456" not in redacted
    assert "[REDACTED]" in redacted


# ========== Orchestration Tests ==========


def test_orchestrate_pre_workflow(temp_run_root, execution_payload_with_trades):
    """Test orchestration routes to PRE email for PRE workflow."""
    with patch("core.email_orchestrator.send_email") as mock_send:
        result = orchestrate_email(
            workflow_step="PRE",
            execution_payload=execution_payload_with_trades,
            paper_summary=None,
            run_root=temp_run_root,
            trade_date="2026-03-02",
            mode="SHADOW",
            run_id="run123",
        )

        assert result["sent"] is True
        assert result["email_type"] == "PRE"
        assert result["error"] is None
        assert mock_send.call_count == 1


def test_orchestrate_post_workflow(
    temp_run_root,
    execution_payload_with_trades,
    paper_summary_with_fills,
):
    """Test orchestration routes to POST email for POST workflow."""
    with patch("core.email_orchestrator.send_email") as mock_send:
        result = orchestrate_email(
            workflow_step="POST",
            execution_payload=execution_payload_with_trades,
            paper_summary=paper_summary_with_fills,
            run_root=temp_run_root,
            trade_date="2026-03-02",
            mode="SHADOW",
            run_id="run123",
        )

        assert result["sent"] is True
        assert result["email_type"] == "POST"
        assert result["error"] is None


def test_orchestrate_post_missing_artifacts_sends_alert(temp_run_root, execution_payload_with_trades):
    """Test orchestration sends ALERT when POST artifacts are missing."""
    with patch("core.email_orchestrator.send_email") as mock_send:
        result = orchestrate_email(
            workflow_step="POST",
            execution_payload=execution_payload_with_trades,
            paper_summary=None,  # Missing required input
            run_root=temp_run_root,
            trade_date="2026-03-02",
            mode="SHADOW",
            run_id="run123",
        )

        # Should have sent an ALERT instead of POST
        assert result["email_type"] == "ALERT"
        assert result["error"] is not None
        assert "paper_summary" in result["error"]
        assert mock_send.call_count == 1  # ALERT email


def test_orchestrate_exception_sends_alert(temp_run_root, execution_payload_with_trades):
    """Test orchestration sends ALERT on unexpected exceptions."""
    with patch("core.email_orchestrator.send_email") as mock_send:
        # Make build_execution_email_text raise an exception
        with patch("core.email_orchestrator.build_execution_email_text") as mock_build:
            mock_build.side_effect = RuntimeError("Unexpected build error")

            result = orchestrate_email(
                workflow_step="PRE",
                execution_payload=execution_payload_with_trades,
                paper_summary=None,
                run_root=temp_run_root,
                trade_date="2026-03-02",
                mode="SHADOW",
                run_id="run123",
            )

            # Should have sent an ALERT
            assert result["email_type"] == "ALERT"
            assert result["error"] is not None
            assert "Unexpected build error" in result["error"]


def test_orchestrate_skipped_when_already_sent(temp_run_root, execution_payload_with_trades):
    """Test orchestration skips sending when email already sent."""
    with patch("core.email_orchestrator.send_email") as mock_send:
        # First call - should send
        result1 = orchestrate_email(
            workflow_step="PRE",
            execution_payload=execution_payload_with_trades,
            paper_summary=None,
            run_root=temp_run_root,
            trade_date="2026-03-02",
            mode="SHADOW",
            run_id="run123",
        )
        assert result1["sent"] is True
        assert result1["skipped"] is False

        # Second call - should skip
        result2 = orchestrate_email(
            workflow_step="PRE",
            execution_payload=execution_payload_with_trades,
            paper_summary=None,
            run_root=temp_run_root,
            trade_date="2026-03-02",
            mode="SHADOW",
            run_id="run123",
        )
        assert result2["sent"] is False
        assert result2["skipped"] is True
        assert mock_send.call_count == 1  # Only sent once

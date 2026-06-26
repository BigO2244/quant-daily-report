"""Confirmation-email rendering for the final-state reconciliation override.

Validates the user-visible symptoms of the 2026-06-03 defect:
  - Filled count reflects the final observed broker fills (never 0 when fills
    actually occurred).
  - A raw broker-abort PARTIAL that post-trade reconciliation upgraded surfaces
    as RECONCILED_SUCCESS while preserving the raw status/reason diagnostic.
"""

import importlib.util
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "send_trading_confirmation_email",
    _REPO_ROOT / "scripts" / "send_trading_confirmation_email.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
_build_confirmation_email = _mod._build_confirmation_email


def _results(**overrides):
    base = {
        "run_id": "run-123",
        "trade_date": "2026-06-03",
        "mode": "PAPER",
        "status": "PARTIAL",
        "submitted_count": 10,
        "accepted_count": 10,
        "rejected_count": 0,
        "orders_filled_count": 10,
        "operator_execution_status": "partial",
        "halt_reason": (
            "partial_execution_broker_abort:"
            "buy_blocked_pending_sells_required_for_cash:cash_rebalance_incomplete"
        ),
    }
    base.update(overrides)
    return base


def test_reconciled_success_surfaces_final_status_and_preserves_raw(tmp_path):
    results = _results(
        status="RECONCILED_SUCCESS",
        operator_execution_status="reconciled_success",
        reconciled_to_target_state=True,
        raw_execution_status="HALTED",
        raw_execution_reason=(
            "partial_execution_broker_abort:"
            "buy_blocked_pending_sells_required_for_cash:cash_rebalance_incomplete"
        ),
        final_execution_status="RECONCILED_SUCCESS",
        final_execution_reason="raw_partial_reconciled_to_target_state",
        halt_reason=None,
    )
    subject, body_text, body_html = _build_confirmation_email(
        results, tmp_path / "execution_results.json"
    )

    assert "[RECONCILED_SUCCESS]" in subject
    assert "Status: RECONCILED_SUCCESS" in body_text
    # Filled count reflects final observed fills, never 0.
    assert "Filled: 10" in body_text
    # Raw (pre-reconciliation) status preserved as an auditable diagnostic.
    assert "Raw Execution (pre-reconciliation)" in body_text
    assert "Raw status: HALTED" in body_text
    assert "cash_rebalance_incomplete" in body_text
    assert "raw_partial_reconciled_to_target_state" in body_text


def test_genuine_partial_remains_partial_with_observed_fills(tmp_path):
    # No override fields => raw partial stays PARTIAL, but the filled count still
    # reflects observed fills (the sells that did fill), not 0.
    results = _results(orders_filled_count=5)
    subject, body_text, _ = _build_confirmation_email(
        results, tmp_path / "execution_results.json"
    )

    assert "[PARTIAL]" in subject
    assert "Status: PARTIAL" in body_text
    assert "Filled: 5" in body_text
    assert "Raw Execution (pre-reconciliation)" not in body_text


def test_confirmation_email_renders_live_pilot_buy_lifecycle(tmp_path):
    results = _results(
        mode="LIVE_PILOT",
        status="SUBMITTED",
        operator_execution_status="executed",
        halt_reason=None,
        submitted_count=1,
        accepted_count=1,
        orders_filled_count=0,
        approved_buy_count=1,
        submitted_buy_count=1,
        unfilled_buy_count=1,
        escalated_buy_count=1,
        entry_execution_policy="live_pilot_buy_market_order_immediate",
        submitted_order_type="market",
        marketable_order_count=1,
        passive_order_count=0,
        prior_unfilled_attempts=3,
        escalation_reason="prior_unfilled_attempts_reached_three_session_limit",
        remaining_blocked_or_suppressed_buy_count=0,
        blocked_or_suppressed_buy_reason="none",
    )

    _, body_text, body_html = _build_confirmation_email(
        results, tmp_path / "execution_results.json"
    )

    assert "Live Pilot Buy Lifecycle" in body_text
    assert "Approved buys: 1" in body_text
    assert "Submitted buys: 1" in body_text
    assert "Unfilled buys: 1" in body_text
    assert "Escalated buys: 1" in body_text
    assert "Entry execution policy: live_pilot_buy_market_order_immediate" in body_text
    assert "Submitted order type: market" in body_text
    assert "Prior unfilled attempts: 3" in body_text
    assert "prior_unfilled_attempts_reached_three_session_limit" in body_text
    assert "Live Pilot Buy Lifecycle" in body_html

"""Tests for the final-state reconciliation override and final-observed fill count.

Covers the 2026-06-03 defect where a broker-abort PARTIAL was reported even though
post-trade reconciliation confirmed the broker matched the expected post-execution
state, and the email reported Filled: 0 while orders had actually filled.

Validation scenarios (per audit spec):
  1. raw partial + clean recon + no skipped/deferred + no rejects => RECONCILED_SUCCESS.
  2. raw partial + recon not clean => remains PARTIAL.
  3. raw partial + skipped/deferred planned orders => remains PARTIAL.
  4. rejected order => remains PARTIAL/FAILED.
  5. filled count reflects final observed fills, not the submit-time snapshot.
"""

from core.execution_payload import (
    RECONCILED_SUCCESS_OPERATOR_STATUS,
    RECONCILED_TO_TARGET_REASON,
    STATUS_RECONCILED_SUCCESS,
    compute_final_execution_status,
)
from core.trade_count_contract import compute_trade_count_contract


def _partial_kwargs(**overrides):
    base = dict(
        raw_execution_status="HALTED",
        raw_operator_execution_status="partial",
        execution_outcome="partial_execution_broker_abort",
        raw_execution_reason=(
            "partial_execution_broker_abort:"
            "buy_blocked_pending_sells_required_for_cash:cash_rebalance_incomplete"
        ),
        posttrade_recon_status="OK_RECONCILED",
        posttrade_unresolved_orders_count=0,
        skipped_buy_count=0,
        blocked_buy_count=0,
        pending_buy_count=0,
        rejected_count=0,
        broker_reject_status=None,
        submitted_count=10,
    )
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
# Scenario 1 — clean reconcile upgrades to RECONCILED_SUCCESS, raw preserved.
# --------------------------------------------------------------------------- #
def test_scenario1_clean_recon_upgrades_to_reconciled_success():
    result = compute_final_execution_status(**_partial_kwargs())

    assert result["reconciled_to_target_state"] is True
    assert result["reconciliation_override_applied"] is True
    assert result["final_execution_status"] == STATUS_RECONCILED_SUCCESS
    assert result["final_operator_execution_status"] == RECONCILED_SUCCESS_OPERATOR_STATUS
    assert result["final_execution_reason"] == RECONCILED_TO_TARGET_REASON

    # Raw status / reason are preserved verbatim as diagnostic metadata.
    assert result["raw_execution_status"] == "HALTED"
    assert result["raw_operator_execution_status"] == "partial"
    assert "cash_rebalance_incomplete" in result["raw_execution_reason"]


def test_scenario1_accepts_plain_ok_recon_status():
    result = compute_final_execution_status(**_partial_kwargs(posttrade_recon_status="OK"))
    assert result["final_execution_status"] == STATUS_RECONCILED_SUCCESS


# --------------------------------------------------------------------------- #
# Scenario 2 — reconciliation not clean => remains PARTIAL.
# --------------------------------------------------------------------------- #
def test_scenario2_drifted_recon_remains_partial():
    result = compute_final_execution_status(
        **_partial_kwargs(posttrade_recon_status="DRIFT_DETECTED")
    )
    assert result["reconciled_to_target_state"] is False
    assert result["final_execution_status"] == "HALTED"
    assert result["final_operator_execution_status"] == "partial"


def test_scenario2_unresolved_orders_remain_partial():
    result = compute_final_execution_status(
        **_partial_kwargs(posttrade_unresolved_orders_count=1)
    )
    assert result["reconciled_to_target_state"] is False
    assert result["final_operator_execution_status"] == "partial"


# --------------------------------------------------------------------------- #
# Scenario 3 — skipped/deferred planned orders => remains PARTIAL (no masking).
# --------------------------------------------------------------------------- #
def test_scenario3_skipped_buys_remain_partial():
    result = compute_final_execution_status(**_partial_kwargs(skipped_buy_count=2))
    assert result["reconciled_to_target_state"] is False
    assert result["final_operator_execution_status"] == "partial"


def test_scenario3_blocked_buys_remain_partial():
    result = compute_final_execution_status(**_partial_kwargs(blocked_buy_count=3))
    assert result["reconciled_to_target_state"] is False


def test_scenario3_pending_buys_remain_partial():
    result = compute_final_execution_status(**_partial_kwargs(pending_buy_count=1))
    assert result["reconciled_to_target_state"] is False


# --------------------------------------------------------------------------- #
# Scenario 4 — rejected orders => remains PARTIAL/FAILED, never upgraded.
# --------------------------------------------------------------------------- #
def test_scenario4_rejected_count_remains_partial():
    result = compute_final_execution_status(**_partial_kwargs(rejected_count=1))
    assert result["reconciled_to_target_state"] is False
    assert result["final_operator_execution_status"] == "partial"


def test_scenario4_broker_reject_status_remains_partial():
    result = compute_final_execution_status(
        **_partial_kwargs(broker_reject_status="BROKER_REJECT_PDT")
    )
    assert result["reconciled_to_target_state"] is False


# --------------------------------------------------------------------------- #
# Guardrails — never upgrade non-broker-abort halts or no-submission states.
# --------------------------------------------------------------------------- #
def test_pretrade_halt_is_never_upgraded():
    # A HALTED from pretrade reconciliation / market-closed is not a candidate,
    # even if a recon artifact happens to read OK.
    result = compute_final_execution_status(
        raw_execution_status="HALTED",
        raw_operator_execution_status="failed",
        execution_outcome=None,
        raw_execution_reason="pretrade_blocked_reconciliation",
        posttrade_recon_status="OK_RECONCILED",
        submitted_count=0,
    )
    assert result["reconciled_to_target_state"] is False
    assert result["final_execution_status"] == "HALTED"


def test_no_submissions_is_never_upgraded():
    result = compute_final_execution_status(**_partial_kwargs(submitted_count=0))
    assert result["reconciled_to_target_state"] is False


def test_executed_status_is_passthrough():
    result = compute_final_execution_status(
        raw_execution_status="EXECUTED",
        raw_operator_execution_status="executed",
        execution_outcome=None,
        posttrade_recon_status="OK_RECONCILED",
        submitted_count=10,
    )
    assert result["reconciled_to_target_state"] is False
    assert result["final_operator_execution_status"] == "executed"


# --------------------------------------------------------------------------- #
# Scenario 5 — filled count reflects final observed fills, not submit snapshot.
# --------------------------------------------------------------------------- #
def test_scenario5_filled_count_prefers_final_observed_over_submit_snapshot():
    # Submit-time broker_responses all still ACCEPTED (the stale-snapshot bug),
    # but the post-trade re-poll recorded 10 fills on paper_summary.
    paper_summary = {
        "orders_filled_count": 10,
        "alpaca_submission_summary": {"submit_success": 10},
    }
    execution_results = {
        "orders_submitted_count": 10,
        "broker_responses": [{"status": "ACCEPTED"} for _ in range(10)],
    }
    contract = compute_trade_count_contract(
        daily_snapshot={},
        paper_summary=paper_summary,
        execution_payload={"trades": [], "executable_trades_count": 0},
        execution_results=execution_results,
    )
    assert contract["orders_filled_count"] == 10
    assert contract["sources"]["orders_filled_count"] == "paper_summary.orders_filled_count"


def test_scenario5_explicit_execution_results_filled_count_wins():
    # An explicit execution_results count still takes precedence over paper_summary.
    contract = compute_trade_count_contract(
        daily_snapshot={},
        paper_summary={"orders_filled_count": 10},
        execution_payload={"trades": []},
        execution_results={"orders_filled_count": 7},
    )
    assert contract["orders_filled_count"] == 7
    assert contract["sources"]["orders_filled_count"] == "execution_results.orders_filled_count"


def test_scenario5_falls_back_to_broker_responses_when_no_final_count():
    contract = compute_trade_count_contract(
        daily_snapshot={},
        paper_summary={},
        execution_payload={"trades": []},
        execution_results={
            "broker_responses": [
                {"status": "FILLED"},
                {"status": "FILLED"},
                {"status": "ACCEPTED"},
            ]
        },
    )
    assert contract["orders_filled_count"] == 2
    assert contract["sources"]["orders_filled_count"] == "execution_results.broker_responses[].status"

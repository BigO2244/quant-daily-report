from __future__ import annotations

import pytest

from core.failure_semantics import (
    FAILURE_POLICIES,
    EscalationPolicy,
    FailureClass,
    RetryPolicy,
    TerminalOutcome,
    build_authorized_no_trade,
    build_submission_unknown,
    build_system_failure,
    failure_policy_catalog,
)


def test_required_failure_taxonomy_is_complete_and_typed() -> None:
    assert {member.value for member in FailureClass} == {
        "DATA_FAILURE",
        "SIGNAL_FAILURE",
        "PRECOMPUTE_FAILURE",
        "STATE_FAILURE",
        "REGIME_FAILURE",
        "PORTFOLIO_CONSTRUCTION_FAILURE",
        "RISK_FAILURE",
        "AUTHORIZATION_FAILURE",
        "PLAN_INTEGRITY_FAILURE",
        "EXECUTION_FAILURE",
        "BROKER_FAILURE",
        "RECONCILIATION_FAILURE",
        "REPORTING_FAILURE",
    }
    assert set(FAILURE_POLICIES) == set(FailureClass)
    for failure_class, policy in FAILURE_POLICIES.items():
        assert policy.failure_class is failure_class
        assert isinstance(policy.retry_policy, RetryPolicy)
        assert isinstance(policy.fail_closed, bool)
        assert isinstance(policy.escalation, EscalationPolicy)
        assert policy.recovery_procedure
        assert policy.forbidden_fallbacks


def test_exposure_changing_failures_are_fail_closed() -> None:
    non_exposure_reporting = {FailureClass.REPORTING_FAILURE}
    for failure_class, policy in FAILURE_POLICIES.items():
        if failure_class not in non_exposure_reporting:
            assert policy.fail_closed is True
    assert FAILURE_POLICIES[FailureClass.REPORTING_FAILURE].fail_closed is False
    assert "silent_success" in FAILURE_POLICIES[FailureClass.REPORTING_FAILURE].forbidden_fallbacks


def test_authorized_no_trade_requires_hash_authority_and_zero_orders() -> None:
    state = build_authorized_no_trade(
        trade_date="2026-08-12",
        reason_code="authorized_targets_already_satisfied",
        plan_id="plan:2026-08-12",
        plan_hash="abc123",
        authorization_id="authorization:2026-08-12",
        plan_hash_validated=True,
        authorization_validated=True,
        orders_requested=0,
    )
    assert state.terminal_outcome is TerminalOutcome.AUTHORIZED_NO_TRADE
    assert state.orders_requested == 0
    assert state.to_dict()["terminal_outcome"] == "AUTHORIZED_NO_TRADE"

    with pytest.raises(ValueError, match="validated plan hash"):
        build_authorized_no_trade(
            trade_date="2026-08-12",
            reason_code="no_candidates",
            plan_id="p",
            plan_hash="h",
            authorization_id="a",
            plan_hash_validated=False,
            authorization_validated=True,
            orders_requested=0,
        )
    with pytest.raises(ValueError, match="zero requested"):
        build_authorized_no_trade(
            trade_date="2026-08-12",
            reason_code="no_candidates",
            plan_id="p",
            plan_hash="h",
            authorization_id="a",
            plan_hash_validated=True,
            authorization_validated=True,
            orders_requested=1,
        )


def test_submission_unknown_is_nonretryable_fail_closed_and_requires_reference() -> None:
    state = build_submission_unknown(
        trade_date="2026-08-12",
        reason_code="broker_timeout_after_submit",
        operation="submit_order",
        order_references=["client-order-123"],
    )
    assert state.terminal_outcome is TerminalOutcome.SUBMISSION_UNKNOWN
    assert state.failure_class is FailureClass.BROKER_FAILURE
    assert state.fail_closed is True
    assert state.retryable is False
    assert state.orders_may_be_resubmitted is False
    assert state.escalation is EscalationPolicy.IMMEDIATE_OPERATOR

    with pytest.raises(ValueError, match="stable order reference"):
        build_submission_unknown(
            trade_date="2026-08-12",
            reason_code="timeout",
            operation="submit_order",
            order_references=[],
        )


def test_system_failure_retry_is_only_permitted_before_financial_mutation() -> None:
    pre_mutation = build_system_failure(
        failure_class=FailureClass.PRECOMPUTE_FAILURE,
        reason_code="precompute_bundle_missing",
        before_financial_mutation=True,
    )
    after_mutation = build_system_failure(
        failure_class=FailureClass.PRECOMPUTE_FAILURE,
        reason_code="late_failure",
        before_financial_mutation=False,
    )
    ambiguous_broker = build_system_failure(
        failure_class=FailureClass.BROKER_FAILURE,
        reason_code="broker_read_timeout",
        before_financial_mutation=True,
    )
    assert pre_mutation.retryable is True
    assert pre_mutation.orders_may_be_resubmitted is True
    assert after_mutation.retryable is False
    assert after_mutation.orders_may_be_resubmitted is False
    assert ambiguous_broker.retryable is True
    assert ambiguous_broker.orders_may_be_resubmitted is False


def test_serializable_catalog_contains_policy_and_recovery_fields() -> None:
    catalog = failure_policy_catalog()
    assert set(catalog) == {member.value for member in FailureClass}
    broker = catalog["BROKER_FAILURE"]
    assert broker["retry_policy"] == "READ_ONLY_BACKOFF"
    assert broker["fail_closed"] is True
    assert broker["escalation"] == "IMMEDIATE_OPERATOR"
    assert broker["recovery_procedure"]
    assert "automatic_resubmission_after_ambiguous_broker_result" in broker["forbidden_fallbacks"]


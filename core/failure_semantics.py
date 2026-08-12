"""Canonical failure and terminal-outcome semantics for Caerus.

This module is deliberately independent of the execution runtime.  It provides
one typed vocabulary for artifacts, health/reporting consumers, and future
runtime integration without changing order-routing behavior.

Two terminal states are intentionally first-class:

``AUTHORIZED_NO_TRADE``
    A hash-validated, authorized decision requested zero orders.  This is a
    successful decision outcome, not a system failure.

``SUBMISSION_UNKNOWN``
    A broker mutation may have occurred, but durable broker truth is not yet
    available.  It is fail-closed, requires immediate escalation, and forbids
    automatic resubmission.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "caerus.failure_semantics.v1"


class FailureClass(str, Enum):
    DATA_FAILURE = "DATA_FAILURE"
    SIGNAL_FAILURE = "SIGNAL_FAILURE"
    PRECOMPUTE_FAILURE = "PRECOMPUTE_FAILURE"
    STATE_FAILURE = "STATE_FAILURE"
    REGIME_FAILURE = "REGIME_FAILURE"
    PORTFOLIO_CONSTRUCTION_FAILURE = "PORTFOLIO_CONSTRUCTION_FAILURE"
    RISK_FAILURE = "RISK_FAILURE"
    AUTHORIZATION_FAILURE = "AUTHORIZATION_FAILURE"
    PLAN_INTEGRITY_FAILURE = "PLAN_INTEGRITY_FAILURE"
    EXECUTION_FAILURE = "EXECUTION_FAILURE"
    BROKER_FAILURE = "BROKER_FAILURE"
    RECONCILIATION_FAILURE = "RECONCILIATION_FAILURE"
    REPORTING_FAILURE = "REPORTING_FAILURE"


class RetryPolicy(str, Enum):
    """Permitted retry shape; never implies that a retry must occur."""

    NEVER = "NEVER"
    BOUNDED_PRE_MUTATION = "BOUNDED_PRE_MUTATION"
    READ_ONLY_BACKOFF = "READ_ONLY_BACKOFF"
    OPERATOR_RECOVERY_ONLY = "OPERATOR_RECOVERY_ONLY"


class EscalationPolicy(str, Enum):
    IMMEDIATE_OPERATOR = "IMMEDIATE_OPERATOR"
    OPERATOR_ALERT = "OPERATOR_ALERT"
    REPORTING_ALERT = "REPORTING_ALERT"


class TerminalOutcome(str, Enum):
    RECONCILED_SUCCESS = "RECONCILED_SUCCESS"
    AUTHORIZED_NO_TRADE = "AUTHORIZED_NO_TRADE"
    SUBMISSION_UNKNOWN = "SUBMISSION_UNKNOWN"
    SYSTEM_FAILURE = "SYSTEM_FAILURE"


@dataclass(frozen=True)
class FailurePolicy:
    failure_class: FailureClass
    retry_policy: RetryPolicy
    fail_closed: bool
    escalation: EscalationPolicy
    allowable_fallbacks: tuple[str, ...]
    forbidden_fallbacks: tuple[str, ...]
    recovery_procedure: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["failure_class"] = self.failure_class.value
        payload["retry_policy"] = self.retry_policy.value
        payload["escalation"] = self.escalation.value
        payload["allowable_fallbacks"] = list(self.allowable_fallbacks)
        payload["forbidden_fallbacks"] = list(self.forbidden_fallbacks)
        return payload


_NO_STALE_INTENT = (
    "stale_or_previous_day_investment_intent",
    "alternate_target_or_plan",
    "unapproved_strategy_or_sleeve_substitution",
)
_NO_EXPOSURE_MUTATION = (
    "new_order_submission",
    "automatic_resubmission_after_ambiguous_broker_result",
)


def _policy(
    failure_class: FailureClass,
    retry_policy: RetryPolicy,
    escalation: EscalationPolicy,
    recovery_procedure: str,
    *,
    fail_closed: bool = True,
    allowable_fallbacks: tuple[str, ...] = (),
    forbidden_fallbacks: tuple[str, ...] = _NO_STALE_INTENT,
) -> FailurePolicy:
    return FailurePolicy(
        failure_class=failure_class,
        retry_policy=retry_policy,
        fail_closed=fail_closed,
        escalation=escalation,
        allowable_fallbacks=allowable_fallbacks,
        forbidden_fallbacks=forbidden_fallbacks,
        recovery_procedure=recovery_procedure,
    )


FAILURE_POLICIES: Mapping[FailureClass, FailurePolicy] = MappingProxyType(
    {
        FailureClass.DATA_FAILURE: _policy(
            FailureClass.DATA_FAILURE,
            RetryPolicy.BOUNDED_PRE_MUTATION,
            EscalationPolicy.OPERATOR_ALERT,
            "Refresh the governed data source, revalidate freshness and lineage, then rerun before mutation.",
            allowable_fallbacks=("explicitly_governed_equivalent_data_source",),
        ),
        FailureClass.SIGNAL_FAILURE: _policy(
            FailureClass.SIGNAL_FAILURE,
            RetryPolicy.BOUNDED_PRE_MUTATION,
            EscalationPolicy.OPERATOR_ALERT,
            "Regenerate signals from the same governed inputs and require deterministic validation.",
        ),
        FailureClass.PRECOMPUTE_FAILURE: _policy(
            FailureClass.PRECOMPUTE_FAILURE,
            RetryPolicy.BOUNDED_PRE_MUTATION,
            EscalationPolicy.OPERATOR_ALERT,
            "Run bounded self-heal precompute and revalidate the complete same-day bundle.",
            allowable_fallbacks=("same_day_bounded_precompute_self_heal",),
        ),
        FailureClass.STATE_FAILURE: _policy(
            FailureClass.STATE_FAILURE,
            RetryPolicy.READ_ONLY_BACKOFF,
            EscalationPolicy.IMMEDIATE_OPERATOR,
            "Re-read the authoritative state store; require freshness, identity, and consistency before continuing.",
        ),
        FailureClass.REGIME_FAILURE: _policy(
            FailureClass.REGIME_FAILURE,
            RetryPolicy.BOUNDED_PRE_MUTATION,
            EscalationPolicy.OPERATOR_ALERT,
            "Recompute regime state from governed data; do not infer or reuse an unvalidated regime.",
        ),
        FailureClass.PORTFOLIO_CONSTRUCTION_FAILURE: _policy(
            FailureClass.PORTFOLIO_CONSTRUCTION_FAILURE,
            RetryPolicy.BOUNDED_PRE_MUTATION,
            EscalationPolicy.IMMEDIATE_OPERATOR,
            "Rebuild from the same authorized decision and prove constraints; do not manufacture substitute intent.",
        ),
        FailureClass.RISK_FAILURE: _policy(
            FailureClass.RISK_FAILURE,
            RetryPolicy.BOUNDED_PRE_MUTATION,
            EscalationPolicy.IMMEDIATE_OPERATOR,
            "Re-evaluate risk from authoritative state and require a signed/hashed approval before execution.",
        ),
        FailureClass.AUTHORIZATION_FAILURE: _policy(
            FailureClass.AUTHORIZATION_FAILURE,
            RetryPolicy.NEVER,
            EscalationPolicy.IMMEDIATE_OPERATOR,
            "Obtain a new valid authorization through the governed authority path.",
        ),
        FailureClass.PLAN_INTEGRITY_FAILURE: _policy(
            FailureClass.PLAN_INTEGRITY_FAILURE,
            RetryPolicy.NEVER,
            EscalationPolicy.IMMEDIATE_OPERATOR,
            "Preserve all artifacts and obtain a newly validated immutable plan; never repair a plan in place.",
        ),
        FailureClass.EXECUTION_FAILURE: _policy(
            FailureClass.EXECUTION_FAILURE,
            RetryPolicy.OPERATOR_RECOVERY_ONLY,
            EscalationPolicy.IMMEDIATE_OPERATOR,
            "Establish whether any broker mutation occurred, reconcile, then use supervised recovery if needed.",
            forbidden_fallbacks=_NO_STALE_INTENT + _NO_EXPOSURE_MUTATION,
        ),
        FailureClass.BROKER_FAILURE: _policy(
            FailureClass.BROKER_FAILURE,
            RetryPolicy.READ_ONLY_BACKOFF,
            EscalationPolicy.IMMEDIATE_OPERATOR,
            "Retry read-only broker queries with bounds; if mutation is ambiguous, enter SUBMISSION_UNKNOWN.",
            allowable_fallbacks=("bounded_read_only_broker_refresh",),
            forbidden_fallbacks=_NO_STALE_INTENT + _NO_EXPOSURE_MUTATION,
        ),
        FailureClass.RECONCILIATION_FAILURE: _policy(
            FailureClass.RECONCILIATION_FAILURE,
            RetryPolicy.READ_ONLY_BACKOFF,
            EscalationPolicy.IMMEDIATE_OPERATOR,
            "Refresh broker truth without new submissions and resolve every order, position, cash, and NAV delta.",
            allowable_fallbacks=("bounded_read_only_broker_refresh",),
            forbidden_fallbacks=_NO_STALE_INTENT + _NO_EXPOSURE_MUTATION,
        ),
        FailureClass.REPORTING_FAILURE: _policy(
            FailureClass.REPORTING_FAILURE,
            RetryPolicy.BOUNDED_PRE_MUTATION,
            EscalationPolicy.REPORTING_ALERT,
            "Preserve canonical economic artifacts, retry report publication, and visibly mark reporting degraded.",
            fail_closed=False,
            allowable_fallbacks=("canonical_structured_artifacts", "operator_visible_degraded_report"),
            forbidden_fallbacks=("invented_or_stale_economic_values", "silent_success"),
        ),
    }
)


@dataclass(frozen=True)
class FailureState:
    failure_class: FailureClass
    reason_code: str
    terminal_outcome: TerminalOutcome
    retryable: bool
    fail_closed: bool
    escalation: EscalationPolicy
    orders_may_be_resubmitted: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "failure_class": self.failure_class.value,
            "reason_code": self.reason_code,
            "terminal_outcome": self.terminal_outcome.value,
            "retryable": self.retryable,
            "fail_closed": self.fail_closed,
            "escalation": self.escalation.value,
            "orders_may_be_resubmitted": self.orders_may_be_resubmitted,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class AuthorizedNoTrade:
    trade_date: str
    reason_code: str
    plan_id: str
    plan_hash: str
    authorization_id: str
    orders_requested: int = 0
    terminal_outcome: TerminalOutcome = TerminalOutcome.AUTHORIZED_NO_TRADE

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema_version"] = SCHEMA_VERSION
        payload["terminal_outcome"] = self.terminal_outcome.value
        return payload


@dataclass(frozen=True)
class SubmissionUnknown:
    trade_date: str
    reason_code: str
    operation: str
    order_references: tuple[str, ...]
    detail: str = ""
    failure_class: FailureClass = FailureClass.BROKER_FAILURE
    terminal_outcome: TerminalOutcome = TerminalOutcome.SUBMISSION_UNKNOWN
    retryable: bool = False
    fail_closed: bool = True
    escalation: EscalationPolicy = EscalationPolicy.IMMEDIATE_OPERATOR
    orders_may_be_resubmitted: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema_version"] = SCHEMA_VERSION
        payload["failure_class"] = self.failure_class.value
        payload["terminal_outcome"] = self.terminal_outcome.value
        payload["escalation"] = self.escalation.value
        payload["order_references"] = list(self.order_references)
        return payload


def get_failure_policy(failure_class: FailureClass | str) -> FailurePolicy:
    return FAILURE_POLICIES[FailureClass(failure_class)]


def build_system_failure(
    *,
    failure_class: FailureClass | str,
    reason_code: str,
    detail: str = "",
    before_financial_mutation: bool,
) -> FailureState:
    policy = get_failure_policy(failure_class)
    normalized_reason = str(reason_code or "").strip()
    if not normalized_reason:
        raise ValueError("reason_code is required")
    retryable = before_financial_mutation and policy.retry_policy in {
        RetryPolicy.BOUNDED_PRE_MUTATION,
        RetryPolicy.READ_ONLY_BACKOFF,
    }
    return FailureState(
        failure_class=policy.failure_class,
        reason_code=normalized_reason,
        terminal_outcome=TerminalOutcome.SYSTEM_FAILURE,
        retryable=retryable,
        fail_closed=policy.fail_closed,
        escalation=policy.escalation,
        # READ_ONLY_BACKOFF permits another observation, never another broker
        # mutation.  Pre-mutation pipeline repair may proceed to its first
        # submission only after the failure is cleared.
        orders_may_be_resubmitted=(
            retryable and policy.retry_policy is RetryPolicy.BOUNDED_PRE_MUTATION
        ),
        detail=str(detail or ""),
    )


def build_authorized_no_trade(
    *,
    trade_date: str,
    reason_code: str,
    plan_id: str,
    plan_hash: str,
    authorization_id: str,
    plan_hash_validated: bool,
    authorization_validated: bool,
    orders_requested: int,
) -> AuthorizedNoTrade:
    """Build an intentional no-trade outcome only from validated authority."""

    required = {
        "trade_date": trade_date,
        "reason_code": reason_code,
        "plan_id": plan_id,
        "plan_hash": plan_hash,
        "authorization_id": authorization_id,
    }
    missing = [name for name, value in required.items() if not str(value or "").strip()]
    if missing:
        raise ValueError(f"AUTHORIZED_NO_TRADE missing required fields: {', '.join(missing)}")
    if not plan_hash_validated or not authorization_validated:
        raise ValueError("AUTHORIZED_NO_TRADE requires validated plan hash and authorization")
    if int(orders_requested) != 0:
        raise ValueError("AUTHORIZED_NO_TRADE requires exactly zero requested orders")
    return AuthorizedNoTrade(
        trade_date=str(trade_date),
        reason_code=str(reason_code),
        plan_id=str(plan_id),
        plan_hash=str(plan_hash),
        authorization_id=str(authorization_id),
    )


def build_submission_unknown(
    *,
    trade_date: str,
    reason_code: str,
    operation: str,
    order_references: Sequence[str],
    detail: str = "",
) -> SubmissionUnknown:
    """Represent an ambiguous broker mutation without permitting resubmission."""

    references = tuple(str(value).strip() for value in order_references if str(value).strip())
    if not str(trade_date or "").strip() or not str(reason_code or "").strip():
        raise ValueError("trade_date and reason_code are required")
    if not str(operation or "").strip():
        raise ValueError("operation is required")
    if not references:
        raise ValueError("SUBMISSION_UNKNOWN requires at least one stable order reference")
    return SubmissionUnknown(
        trade_date=str(trade_date),
        reason_code=str(reason_code),
        operation=str(operation),
        order_references=references,
        detail=str(detail or ""),
    )


def failure_policy_catalog() -> dict[str, dict[str, Any]]:
    """Return the complete serializable policy catalog for artifact writers."""

    return {
        failure_class.value: FAILURE_POLICIES[failure_class].to_dict()
        for failure_class in FailureClass
    }

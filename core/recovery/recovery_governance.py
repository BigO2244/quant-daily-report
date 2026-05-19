from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from core.recovery.interrupted_state import ExecutionLifecycleState
from core.recovery.state_transitions import (
    OPERATOR_REQUIRED_STATES,
    RESUMABLE_STATES,
    TERMINAL_STATES,
)


class GovernanceClassification(str, Enum):
    SAFE_SIMULATION_ONLY = "SAFE_SIMULATION_ONLY"
    OPERATOR_APPROVAL_REQUIRED = "OPERATOR_APPROVAL_REQUIRED"
    RECOVERY_PROHIBITED = "RECOVERY_PROHIBITED"
    RECOVERY_ALLOWED = "RECOVERY_ALLOWED"
    TERMINALIZED = "TERMINALIZED"
    NON_RESUMABLE = "NON_RESUMABLE"


@dataclass(frozen=True)
class GovernanceDecision:
    classification: GovernanceClassification
    legal: bool
    operator_approval_required: bool
    replay_prohibited: bool
    certification_required: bool
    reasons: list[str]

    def to_artifact(self) -> dict[str, Any]:
        return {
            "classification": self.classification.value,
            "legal": self.legal,
            "operator_approval_required": self.operator_approval_required,
            "replay_prohibited": self.replay_prohibited,
            "certification_required": self.certification_required,
            "reasons": self.reasons,
        }


def classify_recovery_governance(
    *,
    lifecycle_state: ExecutionLifecycleState,
    validation_ok: bool,
    risk_level: str,
    dry_run: bool = True,
    recovery_delta_count: int = 0,
    duplicate_order_risk: bool = False,
    open_orders_count: int = 0,
) -> GovernanceDecision:
    reasons: list[str] = []
    risk = str(risk_level or "UNKNOWN").upper()

    if lifecycle_state in TERMINAL_STATES:
        return GovernanceDecision(
            GovernanceClassification.TERMINALIZED,
            legal=False,
            operator_approval_required=False,
            replay_prohibited=True,
            certification_required=False,
            reasons=["terminal_state_blocks_mutation"],
        )
    if duplicate_order_risk:
        return GovernanceDecision(
            GovernanceClassification.RECOVERY_PROHIBITED,
            legal=False,
            operator_approval_required=True,
            replay_prohibited=True,
            certification_required=True,
            reasons=["duplicate_recovery_order_risk"],
        )
    if open_orders_count > 0:
        return GovernanceDecision(
            GovernanceClassification.RECOVERY_PROHIBITED,
            legal=False,
            operator_approval_required=True,
            replay_prohibited=True,
            certification_required=True,
            reasons=["open_orders_present"],
        )
    if not validation_ok:
        return GovernanceDecision(
            GovernanceClassification.RECOVERY_PROHIBITED,
            legal=False,
            operator_approval_required=True,
            replay_prohibited=True,
            certification_required=True,
            reasons=["validation_failed"],
        )
    if lifecycle_state not in RESUMABLE_STATES and recovery_delta_count > 0:
        return GovernanceDecision(
            GovernanceClassification.NON_RESUMABLE,
            legal=False,
            operator_approval_required=True,
            replay_prohibited=True,
            certification_required=True,
            reasons=["state_not_resumable"],
        )
    if dry_run:
        return GovernanceDecision(
            GovernanceClassification.SAFE_SIMULATION_ONLY,
            legal=True,
            operator_approval_required=lifecycle_state in OPERATOR_REQUIRED_STATES,
            replay_prohibited=True,
            certification_required=True,
            reasons=["dry_run_no_broker_mutation"],
        )
    if risk in {"CRITICAL", "HIGH"}:
        reasons.append(f"risk_level_{risk.lower()}")
    if lifecycle_state in OPERATOR_REQUIRED_STATES:
        reasons.append("operator_required_state")
    if reasons:
        return GovernanceDecision(
            GovernanceClassification.OPERATOR_APPROVAL_REQUIRED,
            legal=True,
            operator_approval_required=True,
            replay_prohibited=True,
            certification_required=True,
            reasons=reasons,
        )
    return GovernanceDecision(
        GovernanceClassification.RECOVERY_ALLOWED,
        legal=True,
        operator_approval_required=False,
        replay_prohibited=True,
        certification_required=True,
        reasons=["governance_safe_path"],
    )


def build_governance_report(
    *,
    lifecycle_state: ExecutionLifecycleState,
    validation: dict[str, Any],
    risk_report: dict[str, Any],
    recovery_delta_count: int,
    dry_run: bool = True,
) -> dict[str, Any]:
    duplicate_risk = bool(
        (risk_report.get("dimensions") or {})
        .get("duplicate_order_risk", {})
        .get("duplicate_order_risk")
    )
    open_orders_count = int(
        (risk_report.get("dimensions") or {})
        .get("open_orders", {})
        .get("open_orders_count")
        or 0
    )
    decision = classify_recovery_governance(
        lifecycle_state=lifecycle_state,
        validation_ok=bool(validation.get("ok")),
        risk_level=str(risk_report.get("overall_risk") or "UNKNOWN"),
        dry_run=dry_run,
        recovery_delta_count=recovery_delta_count,
        duplicate_order_risk=duplicate_risk,
        open_orders_count=open_orders_count,
    )
    return {
        **decision.to_artifact(),
        "lifecycle_state": lifecycle_state.value,
        "risk_level": risk_report.get("overall_risk"),
        "dry_run": dry_run,
        "governance_safe_recovery_pathway": [
            "fixture_replay",
            "dry_run_simulation",
            "operator_review",
            "explicit_approval",
            "separate_supervised_recovery_event",
            "post_recovery_reconciliation",
        ],
    }


from __future__ import annotations

from typing import Any

from core.recovery.drift_analysis import analyze_portfolio_drift
from core.recovery.eventual_settlement import SettlementAssessment
from core.recovery.interrupted_state import BrokerState
from core.recovery.recovery_delta import RecoveryDeltaOrder
from core.recovery.recovery_validator import RecoveryValidationResult


RISK_ORDER = ("LOW", "MODERATE", "HIGH", "CRITICAL")


def _max_level(levels: list[str]) -> str:
    if not levels:
        return "LOW"
    return max(levels, key=lambda level: RISK_ORDER.index(level))


def score_recovery_risk(
    *,
    broker_state: BrokerState,
    recovery_delta: list[RecoveryDeltaOrder],
    validation: RecoveryValidationResult,
    drift_analysis: dict[str, Any],
    settlement: SettlementAssessment,
    artifact_completeness: dict[str, bool] | None = None,
    duplicate_order_risk: bool = False,
    stale_broker_state: bool = False,
) -> dict[str, Any]:
    artifact_completeness = dict(artifact_completeness or {})
    dimensions: dict[str, dict[str, Any]] = {}

    dimensions["unresolved_fills"] = {
        "level": "HIGH" if settlement.status == "PENDING_TERMINALITY" else "LOW",
        "status": settlement.status,
    }
    dimensions["stale_broker_state"] = {
        "level": "HIGH" if stale_broker_state else "LOW",
        "stale": stale_broker_state,
    }
    dimensions["open_orders"] = {
        "level": "CRITICAL" if broker_state.open_orders_count > 0 else "LOW",
        "open_orders_count": broker_state.open_orders_count,
    }
    dimensions["duplicate_order_risk"] = {
        "level": "CRITICAL" if duplicate_order_risk else "LOW",
        "duplicate_order_risk": duplicate_order_risk,
    }
    dimensions["stale_execution_locks"] = {
        "level": "MODERATE"
        if "stale_execution_lock_present_do_not_reuse" in validation.warnings
        else "LOW",
        "warnings": validation.warnings,
    }
    dimensions["reconciliation_confidence"] = {
        "level": {
            "HIGH": "LOW",
            "MODERATE": "MODERATE",
            "LOW": "HIGH",
        }.get(settlement.confidence, "HIGH"),
        "confidence": settlement.confidence,
    }
    missing_count = int(drift_analysis.get("missing_position_count") or 0)
    dimensions["portfolio_drift_severity"] = {
        "level": "HIGH" if missing_count >= 6 else ("MODERATE" if missing_count else "LOW"),
        "missing_position_count": missing_count,
    }
    discontinuity = drift_analysis.get("exposure_discontinuity_pct")
    discontinuity_value = float(discontinuity or 0.0)
    dimensions["exposure_discontinuity"] = {
        "level": "HIGH"
        if discontinuity_value >= 0.25
        else ("MODERATE" if discontinuity_value >= 0.10 else "LOW"),
        "exposure_discontinuity_pct": discontinuity,
    }
    missing_artifacts = sorted(
        name for name, present in artifact_completeness.items() if not bool(present)
    )
    dimensions["artifact_completeness"] = {
        "level": "HIGH" if missing_artifacts else "LOW",
        "missing_artifacts": missing_artifacts,
    }
    if validation.failures:
        dimensions["candidate_validation"] = {
            "level": "CRITICAL",
            "failures": validation.failures,
        }
    else:
        dimensions["candidate_validation"] = {
            "level": "LOW",
            "failures": [],
        }

    overall = _max_level([item["level"] for item in dimensions.values()])
    return {
        "overall_risk": overall,
        "dimensions": dimensions,
        "recovery_order_count": len(recovery_delta),
        "operator_required": overall in {"MODERATE", "HIGH", "CRITICAL"},
    }


def score_simple_position_risk(
    *,
    current_positions: dict[str, float],
    target_positions: dict[str, float],
    broker_state: BrokerState,
    recovery_delta: list[RecoveryDeltaOrder],
    validation: RecoveryValidationResult,
    settlement: SettlementAssessment,
) -> dict[str, Any]:
    drift = analyze_portfolio_drift(
        current_positions=current_positions,
        target_positions=target_positions,
        current_cash=broker_state.cash,
        current_equity=broker_state.equity,
    )
    return score_recovery_risk(
        broker_state=broker_state,
        recovery_delta=recovery_delta,
        validation=validation,
        drift_analysis=drift,
        settlement=settlement,
    )


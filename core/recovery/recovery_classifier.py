from __future__ import annotations

from dataclasses import dataclass, field

from core.recovery.interrupted_state import ExecutionLifecycleState, InterruptedRunSnapshot
from core.recovery.recovery_delta import RecoveryDeltaOrder, position_drift_rows


@dataclass(frozen=True)
class ClassificationResult:
    state: ExecutionLifecycleState
    reasons: list[str] = field(default_factory=list)
    recovery_candidate: bool = False


def classify_interrupted_run(
    snapshot: InterruptedRunSnapshot,
    *,
    recovery_delta: list[RecoveryDeltaOrder] | None = None,
) -> ClassificationResult:
    reasons: list[str] = []
    status = (snapshot.execution_status or "").strip().upper()
    outcome = (snapshot.execution_outcome or "").strip().lower()
    halt_reason = (snapshot.halt_reason or "").strip().lower()
    post_recon = (snapshot.posttrade_reconciliation_status or "").strip().upper()
    delta = list(recovery_delta or [])

    if snapshot.submitted_count <= 0 and status in {"PLANNED", "NO_ACTION", "EXECUTED"}:
        return ClassificationResult(ExecutionLifecycleState.NORMAL_EXECUTION, ["no_partial_submission"])

    if snapshot.submitted_count > 0 and status in {"HALTED", "FAILED", "FAILED_PRE_EXECUTION"}:
        reasons.append("submitted_orders_before_terminal_failure")
        if "sell_phase_timeout" in halt_reason or "timeout_waiting_for_sell_completion" in halt_reason:
            return ClassificationResult(
                ExecutionLifecycleState.SELL_PHASE_TIMEOUT,
                reasons + ["sell_phase_timeout"],
                recovery_candidate=False,
            )
        if outcome == "post_submit_artifact_failure" or "posttrade_state_capture_failed" in halt_reason:
            if post_recon not in {"PASS", "OK_RECONCILED"}:
                return ClassificationResult(
                    ExecutionLifecycleState.SETTLEMENT_PENDING,
                    reasons + ["posttrade_reconciliation_not_authoritative"],
                    recovery_candidate=False,
                )
            if delta:
                return ClassificationResult(
                    ExecutionLifecycleState.RECOVERY_CANDIDATE,
                    reasons + ["posttrade_reconciled_with_recovery_delta"],
                    recovery_candidate=True,
                )
            return ClassificationResult(
                ExecutionLifecycleState.RECOVERY_RECONCILED,
                reasons + ["posttrade_reconciled_without_remaining_delta"],
                recovery_candidate=False,
            )
        return ClassificationResult(
            ExecutionLifecycleState.PARTIAL_EXECUTION,
            reasons + ["partial_execution_unclassified"],
            recovery_candidate=False,
        )

    if delta:
        return ClassificationResult(
            ExecutionLifecycleState.RECOVERY_CANDIDATE,
            ["position_drift_detected"],
            recovery_candidate=True,
        )

    return ClassificationResult(ExecutionLifecycleState.NORMAL_EXECUTION, ["positions_normalized"])


def summarize_position_drift(
    *,
    current_positions: dict[str, float],
    target_positions: dict[str, float],
) -> dict[str, object]:
    rows = position_drift_rows(
        current_positions=current_positions,
        target_positions=target_positions,
    )
    mismatches = [row for row in rows if row["classification"] != "MATCH"]
    return {
        "rows": rows,
        "mismatch_count": len(mismatches),
        "max_abs_qty_delta": max((abs(float(row["delta_qty"])) for row in rows), default=0.0),
    }


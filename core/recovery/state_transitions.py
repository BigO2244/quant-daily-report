from __future__ import annotations

from dataclasses import dataclass

from core.recovery.interrupted_state import ExecutionLifecycleState


ALLOWED_TRANSITIONS: dict[ExecutionLifecycleState, set[ExecutionLifecycleState]] = {
    ExecutionLifecycleState.NORMAL_EXECUTION: {
        ExecutionLifecycleState.PARTIAL_EXECUTION,
        ExecutionLifecycleState.SELL_PHASE_TIMEOUT,
    },
    ExecutionLifecycleState.PARTIAL_EXECUTION: {
        ExecutionLifecycleState.SETTLEMENT_PENDING,
        ExecutionLifecycleState.RECOVERY_CANDIDATE,
    },
    ExecutionLifecycleState.SELL_PHASE_TIMEOUT: {
        ExecutionLifecycleState.SETTLEMENT_PENDING,
    },
    ExecutionLifecycleState.SETTLEMENT_PENDING: {
        ExecutionLifecycleState.RECOVERY_CANDIDATE,
        ExecutionLifecycleState.RECOVERY_RECONCILED,
    },
    ExecutionLifecycleState.RECOVERY_CANDIDATE: {
        ExecutionLifecycleState.RECOVERY_SIMULATED,
        ExecutionLifecycleState.RECOVERY_APPROVED,
    },
    ExecutionLifecycleState.RECOVERY_SIMULATED: {
        ExecutionLifecycleState.RECOVERY_APPROVED,
        ExecutionLifecycleState.SETTLEMENT_PENDING,
    },
    ExecutionLifecycleState.RECOVERY_APPROVED: {
        ExecutionLifecycleState.RECOVERY_EXECUTED,
    },
    ExecutionLifecycleState.RECOVERY_EXECUTED: {
        ExecutionLifecycleState.RECOVERY_RECONCILED,
        ExecutionLifecycleState.SETTLEMENT_PENDING,
    },
    ExecutionLifecycleState.RECOVERY_RECONCILED: set(),
}

TERMINAL_STATES = {ExecutionLifecycleState.RECOVERY_RECONCILED}
RESUMABLE_STATES = {
    ExecutionLifecycleState.SETTLEMENT_PENDING,
    ExecutionLifecycleState.RECOVERY_CANDIDATE,
    ExecutionLifecycleState.RECOVERY_SIMULATED,
    ExecutionLifecycleState.RECOVERY_APPROVED,
}
OPERATOR_REQUIRED_STATES = {
    ExecutionLifecycleState.RECOVERY_CANDIDATE,
    ExecutionLifecycleState.RECOVERY_SIMULATED,
    ExecutionLifecycleState.RECOVERY_APPROVED,
}


@dataclass(frozen=True)
class TransitionStep:
    from_state: ExecutionLifecycleState
    to_state: ExecutionLifecycleState
    allowed: bool
    reason: str

    def to_artifact(self) -> dict[str, str | bool]:
        return {
            "from_state": self.from_state.value,
            "to_state": self.to_state.value,
            "allowed": self.allowed,
            "reason": self.reason,
        }


def validate_transition(
    from_state: ExecutionLifecycleState,
    to_state: ExecutionLifecycleState,
) -> TransitionStep:
    if from_state in TERMINAL_STATES:
        return TransitionStep(from_state, to_state, False, "post_terminal_mutation_rejected")
    if to_state == ExecutionLifecycleState.RECOVERY_EXECUTED and from_state != ExecutionLifecycleState.RECOVERY_APPROVED:
        return TransitionStep(from_state, to_state, False, "silent_replay_or_unapproved_execution_rejected")
    if to_state in ALLOWED_TRANSITIONS.get(from_state, set()):
        return TransitionStep(from_state, to_state, True, "allowed_transition")
    return TransitionStep(from_state, to_state, False, "illegal_transition")


def validate_transition_path(states: list[ExecutionLifecycleState]) -> dict[str, object]:
    steps = [
        validate_transition(from_state, to_state)
        for from_state, to_state in zip(states, states[1:])
    ]
    return {
        "ok": all(step.allowed for step in steps),
        "steps": [step.to_artifact() for step in steps],
        "terminal_state": states[-1].value if states else None,
        "resumable": bool(states and states[-1] in RESUMABLE_STATES),
        "operator_required": bool(states and states[-1] in OPERATOR_REQUIRED_STATES),
    }


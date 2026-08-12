from pathlib import Path

import pytest

from core.orchestrator_state import (
    STAGES,
    OrchestratorStateError,
    append_orchestrator_transition,
    load_orchestrator_state,
)


def test_hash_chained_state_machine_is_strict_and_idempotent(tmp_path: Path):
    kwargs = {"trade_date": "2026-08-12", "plan_id": "plan:test"}
    first = append_orchestrator_transition(
        tmp_path, **kwargs, stage="OBSERVE", status="PASS",
        recorded_at="2026-08-12T13:35:00Z", artifact_refs=("broker.json",),
    )
    replay = append_orchestrator_transition(
        tmp_path, **kwargs, stage="OBSERVE", status="PASS",
        recorded_at="different-is-ignored", artifact_refs=("broker.json",),
    )
    assert replay.content_hash == first.content_hash
    with pytest.raises(OrchestratorStateError, match="conflicting"):
        append_orchestrator_transition(
            tmp_path, **kwargs, stage="OBSERVE", status="FAILED",
            recorded_at="x", artifact_refs=("broker.json",),
        )
    with pytest.raises(OrchestratorStateError, match="illegal"):
        append_orchestrator_transition(
            tmp_path, **kwargs, stage="PRECOMPUTE", status="PASS", recorded_at="x"
        )
    for stage in STAGES[1:]:
        append_orchestrator_transition(
            tmp_path, **kwargs, stage=stage, status="PASS", recorded_at=f"time-{stage}"
        )
    state = load_orchestrator_state(tmp_path, **kwargs)
    assert [item.stage for item in state] == list(STAGES)
    assert all(item.content_hash for item in state)


def test_failed_transition_is_terminal(tmp_path: Path):
    kwargs = {"trade_date": "2026-08-12", "plan_id": "plan:failed"}
    append_orchestrator_transition(
        tmp_path, **kwargs, stage="OBSERVE", status="FAILED", recorded_at="time"
    )
    with pytest.raises(OrchestratorStateError, match="terminal"):
        append_orchestrator_transition(
            tmp_path, **kwargs, stage="RESEARCH", status="PASS", recorded_at="later"
        )

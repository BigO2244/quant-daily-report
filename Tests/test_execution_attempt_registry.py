from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.execution_attempt_registry import (
    AttemptRecord,
    IncidentEvent,
    SelectionStatus,
    append_attempt,
    append_attempt_and_update_selection,
    append_incident_event,
    read_attempts,
    select_from_registry,
    write_selection_pointer,
)
from core.failure_semantics import FailureClass, TerminalOutcome


def _attempt(
    *,
    attempt_id: str,
    sequence: int,
    outcome: TerminalOutcome,
    failure_class: FailureClass | None = None,
    submitted_count: int = 0,
    filled_count: int = 0,
    resolves: tuple[str, ...] = (),
) -> AttemptRecord:
    return AttemptRecord(
        attempt_id=attempt_id,
        trade_date="2026-08-12",
        run_id=f"run-{sequence}",
        lane="paper",
        sequence=sequence,
        terminal_outcome=outcome,
        recorded_at=f"2026-08-12T13:{sequence:02d}:00Z",
        run_root=f"outputs/paper_lane/runs/run-{sequence}",
        submitted_count=submitted_count,
        filled_count=filled_count,
        failure_class=failure_class,
        reason_code="fixture_reason" if failure_class else None,
        resolves_attempt_ids=resolves,
    )


def test_failed_attempt_is_preserved_when_later_retry_succeeds(tmp_path) -> None:
    root = tmp_path / "attempt_registry"
    failed = _attempt(
        attempt_id="attempt-1",
        sequence=1,
        outcome=TerminalOutcome.SYSTEM_FAILURE,
        failure_class=FailureClass.EXECUTION_FAILURE,
    )
    successful = _attempt(
        attempt_id="attempt-2",
        sequence=2,
        outcome=TerminalOutcome.RECONCILED_SUCCESS,
        submitted_count=1,
        filled_count=1,
    )

    first_path = append_attempt(root, failed)
    first_bytes = first_path.read_bytes()
    append_attempt(root, successful)

    records = read_attempts(root, trade_date="2026-08-12")
    selection = select_from_registry(
        root,
        trade_date="2026-08-12",
        generated_at="2026-08-12T14:00:00Z",
    )

    assert first_path.read_bytes() == first_bytes
    assert [record.attempt_id for record in records] == ["attempt-1", "attempt-2"]
    assert selection.status is SelectionStatus.RESOLVED
    assert selection.selected_attempt_id == "attempt-2"
    assert selection.attempt_count == 2
    assert len(selection.attempt_hashes) == 2


def test_append_only_attempt_and_incident_files_cannot_be_overwritten(tmp_path) -> None:
    root = tmp_path / "attempt_registry"
    attempt = _attempt(
        attempt_id="attempt-1",
        sequence=1,
        outcome=TerminalOutcome.SYSTEM_FAILURE,
        failure_class=FailureClass.EXECUTION_FAILURE,
    )
    append_attempt(root, attempt)
    with pytest.raises(FileExistsError, match="append-only"):
        append_attempt(root, attempt)

    event = IncidentEvent(
        event_id="detected-1",
        incident_id="incident-20260812",
        trade_date="2026-08-12",
        event_type="DETECTED",
        recorded_at="2026-08-12T13:35:10Z",
        failure_class=FailureClass.EXECUTION_FAILURE,
        reason_code="paper_lane_dry_run_failed",
        attempt_id="attempt-1",
        evidence_artifacts=("logs/execute_2026-08-12.log",),
    )
    path = append_incident_event(root, event)
    assert json.loads(path.read_text(encoding="utf-8"))["content_hash"]
    with pytest.raises(FileExistsError, match="append-only"):
        append_incident_event(root, event)


def test_unresolved_submission_unknown_blocks_later_success_pointer(tmp_path) -> None:
    root = tmp_path / "attempt_registry"
    append_attempt(
        root,
        _attempt(
            attempt_id="ambiguous-1",
            sequence=1,
            outcome=TerminalOutcome.SUBMISSION_UNKNOWN,
            failure_class=FailureClass.BROKER_FAILURE,
            submitted_count=1,
        ),
    )
    append_attempt(
        root,
        _attempt(
            attempt_id="unsafe-later-success",
            sequence=2,
            outcome=TerminalOutcome.RECONCILED_SUCCESS,
            submitted_count=1,
            filled_count=1,
        ),
    )
    selection = select_from_registry(root, trade_date="2026-08-12")
    assert selection.status is SelectionStatus.BLOCKED_SUBMISSION_UNKNOWN
    assert selection.selected_attempt_id == "ambiguous-1"
    assert selection.unresolved_submission_attempt_ids == ("ambiguous-1",)


def test_explicit_lookup_reconciliation_can_resolve_unknown_attempt(tmp_path) -> None:
    root = tmp_path / "attempt_registry"
    append_attempt(
        root,
        _attempt(
            attempt_id="ambiguous-1",
            sequence=1,
            outcome=TerminalOutcome.SUBMISSION_UNKNOWN,
            failure_class=FailureClass.BROKER_FAILURE,
            submitted_count=1,
        ),
    )
    append_attempt(
        root,
        _attempt(
            attempt_id="lookup-reconciliation",
            sequence=2,
            outcome=TerminalOutcome.RECONCILED_SUCCESS,
            submitted_count=1,
            filled_count=1,
            resolves=("ambiguous-1",),
        ),
    )
    selection = select_from_registry(root, trade_date="2026-08-12")
    assert selection.status is SelectionStatus.RESOLVED
    assert selection.selected_attempt_id == "lookup-reconciliation"
    assert not selection.unresolved_submission_attempt_ids

    pointer_path = write_selection_pointer(root, selection)
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    assert pointer["selected_attempt_id"] == "lookup-reconciliation"
    assert pointer["attempt_count"] == 2
    assert len(pointer["attempt_hashes"]) == 2


def test_tampered_attempt_is_rejected_by_reader(tmp_path) -> None:
    root = tmp_path / "attempt_registry"
    path = append_attempt(
        root,
        _attempt(
            attempt_id="attempt-1",
            sequence=1,
            outcome=TerminalOutcome.SYSTEM_FAILURE,
            failure_class=FailureClass.EXECUTION_FAILURE,
        ),
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["reason_code"] = "tampered"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="content hash mismatch"):
        read_attempts(root, trade_date="2026-08-12")


def test_two_writers_serialize_append_selection_and_pointer(tmp_path) -> None:
    root = tmp_path / "attempt_registry"
    barrier = threading.Barrier(2)

    def write(attempt_id: str):
        barrier.wait(timeout=5)

        def build(prior: tuple[AttemptRecord, ...]) -> AttemptRecord:
            sequence = len(prior) + 1
            return AttemptRecord(
                attempt_id=attempt_id,
                trade_date="2026-08-12",
                run_id=f"run-{attempt_id}",
                lane="paper",
                sequence=sequence,
                terminal_outcome=TerminalOutcome.RECONCILED_SUCCESS,
                recorded_at=f"2026-08-12T13:3{sequence}:00Z",
                run_root=f"outputs/paper_lane/runs/{attempt_id}",
                submitted_count=1,
                filled_count=1,
            )

        return append_attempt_and_update_selection(
            root,
            trade_date="2026-08-12",
            build_record=build,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            future.result(timeout=10)
            for future in (
                pool.submit(write, "writer-a"),
                pool.submit(write, "writer-b"),
            )
        ]

    records = read_attempts(root, trade_date="2026-08-12")
    pointer = json.loads(
        (root / "2026-08-12" / "selection.json").read_text(encoding="utf-8")
    )
    assert [row.sequence for row in records] == [1, 2]
    assert {row.attempt_id for row in records} == {"writer-a", "writer-b"}
    assert pointer["attempt_count"] == 2
    assert pointer["selected_attempt_id"] == records[-1].attempt_id
    assert pointer["selected_attempt_hash"] == records[-1].content_hash
    assert {selection.attempt_count for _path, selection, _pointer in results} == {1, 2}

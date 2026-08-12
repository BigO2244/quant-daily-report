"""Append-only execution-attempt and incident-event preservation primitives.

Attempt/event files are immutable and content-hashed.  A mutable selection
pointer may reference them, but it never replaces their history.  The selector
also refuses to hide an unresolved ``SUBMISSION_UNKNOWN`` behind a later run.

This module does not call the broker or alter the execution scheduler.
"""

from __future__ import annotations

import hashlib
import fcntl
import json
import os
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from tempfile import NamedTemporaryFile
from contextlib import contextmanager
from typing import Any, Callable, Iterable, Mapping, Sequence

from core.failure_semantics import FailureClass, TerminalOutcome


ATTEMPT_SCHEMA_VERSION = "caerus.execution_attempt.v1"
INCIDENT_SCHEMA_VERSION = "caerus.execution_incident_event.v1"
SELECTION_SCHEMA_VERSION = "caerus.execution_attempt_selection.v1"

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_TRADE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class SelectionStatus(str, Enum):
    RESOLVED = "RESOLVED"
    FAILED = "FAILED"
    BLOCKED_SUBMISSION_UNKNOWN = "BLOCKED_SUBMISSION_UNKNOWN"
    NO_ATTEMPTS = "NO_ATTEMPTS"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_id(label: str, value: str) -> str:
    normalized = str(value or "").strip()
    if not _SAFE_ID.fullmatch(normalized) or ".." in normalized:
        raise ValueError(f"invalid {label}: {value!r}")
    return normalized


def _validate_trade_date(value: str) -> str:
    normalized = str(value or "").strip()
    if not _TRADE_DATE.fullmatch(normalized):
        raise ValueError(f"invalid trade_date: {value!r}")
    return normalized


def _canonical_json(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _content_hash(payload: Mapping[str, Any]) -> str:
    unhashed = dict(payload)
    unhashed.pop("content_hash", None)
    return hashlib.sha256(_canonical_json(unhashed)).hexdigest()


def _write_exclusive_json(path: Path, payload: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        raise FileExistsError(f"append-only artifact already exists: {path}") from None
    directory_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return path


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)
    directory_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return path


@contextmanager
def _date_registry_lock(registry_root: Path | str, trade_date: str):
    date_root = Path(registry_root) / _validate_trade_date(trade_date)
    date_root.mkdir(parents=True, exist_ok=True)
    lock_path = date_root / ".attempt_registry.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@dataclass(frozen=True)
class AttemptRecord:
    attempt_id: str
    trade_date: str
    run_id: str
    lane: str
    sequence: int
    terminal_outcome: TerminalOutcome
    recorded_at: str
    run_root: str
    submitted_count: int = 0
    filled_count: int = 0
    failure_class: FailureClass | None = None
    reason_code: str | None = None
    source_artifacts: tuple[str, ...] = ()
    resolves_attempt_ids: tuple[str, ...] = ()
    incident_id: str | None = None
    plan_id: str | None = None
    client_order_ids: tuple[str, ...] = ()
    content_hash: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        _validate_id("attempt_id", self.attempt_id)
        _validate_trade_date(self.trade_date)
        _validate_id("run_id", self.run_id)
        _validate_id("lane", self.lane)
        if int(self.sequence) < 1:
            raise ValueError("sequence must be >= 1")
        if int(self.submitted_count) < 0 or int(self.filled_count) < 0:
            raise ValueError("submission/fill counts must be non-negative")
        if int(self.filled_count) > int(self.submitted_count):
            raise ValueError("filled_count cannot exceed submitted_count")
        if self.terminal_outcome is TerminalOutcome.AUTHORIZED_NO_TRADE and self.submitted_count:
            raise ValueError("AUTHORIZED_NO_TRADE cannot contain submitted orders")
        if self.terminal_outcome is TerminalOutcome.SYSTEM_FAILURE and self.failure_class is None:
            raise ValueError("SYSTEM_FAILURE requires failure_class")
        if self.terminal_outcome is TerminalOutcome.SUBMISSION_UNKNOWN:
            if self.failure_class not in {
                FailureClass.EXECUTION_FAILURE,
                FailureClass.BROKER_FAILURE,
                FailureClass.RECONCILIATION_FAILURE,
            }:
                raise ValueError("SUBMISSION_UNKNOWN requires execution, broker, or reconciliation failure")
        for resolved in self.resolves_attempt_ids:
            _validate_id("resolved attempt_id", resolved)
        if self.incident_id is not None:
            _validate_id("incident_id", self.incident_id)
        if self.plan_id is not None:
            _validate_id("plan_id", self.plan_id)
        for client_order_id in self.client_order_ids:
            _validate_id("client_order_id", client_order_id)

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema_version"] = ATTEMPT_SCHEMA_VERSION
        payload["terminal_outcome"] = self.terminal_outcome.value
        payload["failure_class"] = self.failure_class.value if self.failure_class else None
        payload["source_artifacts"] = list(self.source_artifacts)
        payload["resolves_attempt_ids"] = list(self.resolves_attempt_ids)
        payload["client_order_ids"] = list(self.client_order_ids)
        if not include_hash:
            payload.pop("content_hash", None)
        return payload

    def with_content_hash(self) -> "AttemptRecord":
        return replace(self, content_hash=_content_hash(self.to_dict(include_hash=False)))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], *, verify_hash: bool = True) -> "AttemptRecord":
        if payload.get("schema_version") != ATTEMPT_SCHEMA_VERSION:
            raise ValueError("unsupported attempt schema")
        record = cls(
            attempt_id=str(payload.get("attempt_id") or ""),
            trade_date=str(payload.get("trade_date") or ""),
            run_id=str(payload.get("run_id") or ""),
            lane=str(payload.get("lane") or ""),
            sequence=int(payload.get("sequence") or 0),
            terminal_outcome=TerminalOutcome(str(payload.get("terminal_outcome") or "")),
            recorded_at=str(payload.get("recorded_at") or ""),
            run_root=str(payload.get("run_root") or ""),
            submitted_count=int(payload.get("submitted_count") or 0),
            filled_count=int(payload.get("filled_count") or 0),
            failure_class=(
                FailureClass(str(payload["failure_class"]))
                if payload.get("failure_class")
                else None
            ),
            reason_code=(str(payload["reason_code"]) if payload.get("reason_code") else None),
            source_artifacts=tuple(str(value) for value in (payload.get("source_artifacts") or [])),
            resolves_attempt_ids=tuple(str(value) for value in (payload.get("resolves_attempt_ids") or [])),
            incident_id=(str(payload["incident_id"]) if payload.get("incident_id") else None),
            plan_id=(str(payload["plan_id"]) if payload.get("plan_id") else None),
            client_order_ids=tuple(
                str(value) for value in (payload.get("client_order_ids") or [])
            ),
            content_hash=str(payload.get("content_hash") or ""),
        )
        expected = _content_hash(record.to_dict(include_hash=False))
        if verify_hash and record.content_hash != expected:
            raise ValueError(f"attempt content hash mismatch: {record.attempt_id}")
        return record


@dataclass(frozen=True)
class IncidentEvent:
    event_id: str
    incident_id: str
    trade_date: str
    event_type: str
    recorded_at: str
    failure_class: FailureClass
    reason_code: str
    attempt_id: str | None = None
    evidence_artifacts: tuple[str, ...] = ()
    detail: str = ""
    content_hash: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        _validate_id("event_id", self.event_id)
        _validate_id("incident_id", self.incident_id)
        _validate_trade_date(self.trade_date)
        _validate_id("event_type", self.event_type)
        if self.attempt_id is not None:
            _validate_id("attempt_id", self.attempt_id)
        if not str(self.reason_code or "").strip():
            raise ValueError("reason_code is required")

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema_version"] = INCIDENT_SCHEMA_VERSION
        payload["failure_class"] = self.failure_class.value
        payload["evidence_artifacts"] = list(self.evidence_artifacts)
        if not include_hash:
            payload.pop("content_hash", None)
        return payload

    def with_content_hash(self) -> "IncidentEvent":
        return replace(self, content_hash=_content_hash(self.to_dict(include_hash=False)))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], *, verify_hash: bool = True) -> "IncidentEvent":
        if payload.get("schema_version") != INCIDENT_SCHEMA_VERSION:
            raise ValueError("unsupported incident-event schema")
        event = cls(
            event_id=str(payload.get("event_id") or ""),
            incident_id=str(payload.get("incident_id") or ""),
            trade_date=str(payload.get("trade_date") or ""),
            event_type=str(payload.get("event_type") or ""),
            recorded_at=str(payload.get("recorded_at") or ""),
            failure_class=FailureClass(str(payload.get("failure_class") or "")),
            reason_code=str(payload.get("reason_code") or ""),
            attempt_id=(str(payload["attempt_id"]) if payload.get("attempt_id") else None),
            evidence_artifacts=tuple(str(value) for value in (payload.get("evidence_artifacts") or [])),
            detail=str(payload.get("detail") or ""),
            content_hash=str(payload.get("content_hash") or ""),
        )
        expected = _content_hash(event.to_dict(include_hash=False))
        if verify_hash and event.content_hash != expected:
            raise ValueError(f"incident content hash mismatch: {event.event_id}")
        return event


@dataclass(frozen=True)
class AttemptSelection:
    trade_date: str
    status: SelectionStatus
    selected_attempt_id: str | None
    selected_attempt_hash: str | None
    unresolved_submission_attempt_ids: tuple[str, ...]
    attempt_count: int
    attempt_hashes: tuple[str, ...]
    generated_at: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema_version"] = SELECTION_SCHEMA_VERSION
        payload["status"] = self.status.value
        payload["unresolved_submission_attempt_ids"] = list(self.unresolved_submission_attempt_ids)
        payload["attempt_hashes"] = list(self.attempt_hashes)
        return payload


def attempt_path(registry_root: Path | str, *, trade_date: str, attempt_id: str) -> Path:
    return (
        Path(registry_root)
        / _validate_trade_date(trade_date)
        / "attempts"
        / f"{_validate_id('attempt_id', attempt_id)}.json"
    )


def incident_event_path(
    registry_root: Path | str,
    *,
    trade_date: str,
    incident_id: str,
    event_id: str,
) -> Path:
    return (
        Path(registry_root)
        / _validate_trade_date(trade_date)
        / "incidents"
        / _validate_id("incident_id", incident_id)
        / f"{_validate_id('event_id', event_id)}.json"
    )


def append_attempt(registry_root: Path | str, record: AttemptRecord) -> Path:
    hashed = record.with_content_hash()
    return _write_exclusive_json(
        attempt_path(registry_root, trade_date=hashed.trade_date, attempt_id=hashed.attempt_id),
        hashed.to_dict(),
    )


def append_incident_event(registry_root: Path | str, event: IncidentEvent) -> Path:
    hashed = event.with_content_hash()
    return _write_exclusive_json(
        incident_event_path(
            registry_root,
            trade_date=hashed.trade_date,
            incident_id=hashed.incident_id,
            event_id=hashed.event_id,
        ),
        hashed.to_dict(),
    )


def read_attempts(registry_root: Path | str, *, trade_date: str) -> list[AttemptRecord]:
    directory = Path(registry_root) / _validate_trade_date(trade_date) / "attempts"
    if not directory.exists():
        return []
    records = [
        AttemptRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(directory.glob("*.json"))
    ]
    seen_sequences: set[int] = set()
    seen_attempts: set[str] = set()
    for record in records:
        if record.trade_date != trade_date:
            raise ValueError(f"attempt trade_date mismatch: {record.attempt_id}")
        if record.sequence in seen_sequences:
            raise ValueError(f"duplicate attempt sequence: {record.sequence}")
        if record.attempt_id in seen_attempts:
            raise ValueError(f"duplicate attempt_id: {record.attempt_id}")
        seen_sequences.add(record.sequence)
        seen_attempts.add(record.attempt_id)
    return sorted(records, key=lambda record: (record.sequence, record.recorded_at, record.attempt_id))


def select_canonical_attempt(
    attempts: Iterable[AttemptRecord],
    *,
    trade_date: str,
    generated_at: str | None = None,
) -> AttemptSelection:
    records = sorted(
        list(attempts),
        key=lambda record: (record.sequence, record.recorded_at, record.attempt_id),
    )
    normalized_date = _validate_trade_date(trade_date)
    if any(record.trade_date != normalized_date for record in records):
        raise ValueError("all attempts must match selection trade_date")
    sequences = [record.sequence for record in records]
    if len(sequences) != len(set(sequences)):
        raise ValueError("attempt sequences must be unique")

    hashes = tuple(
        (record.content_hash or record.with_content_hash().content_hash)
        for record in records
    )
    if not records:
        return AttemptSelection(
            trade_date=normalized_date,
            status=SelectionStatus.NO_ATTEMPTS,
            selected_attempt_id=None,
            selected_attempt_hash=None,
            unresolved_submission_attempt_ids=(),
            attempt_count=0,
            attempt_hashes=(),
            generated_at=generated_at or _utc_now(),
            reason="no_attempts_recorded",
        )

    resolved_ids = {
        resolved_id
        for record in records
        for resolved_id in record.resolves_attempt_ids
        if record.terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
    }
    unresolved = tuple(
        record.attempt_id
        for record in records
        if record.terminal_outcome is TerminalOutcome.SUBMISSION_UNKNOWN
        and record.attempt_id not in resolved_ids
    )
    if unresolved:
        selected = next(record for record in reversed(records) if record.attempt_id in unresolved)
        status = SelectionStatus.BLOCKED_SUBMISSION_UNKNOWN
        reason = "unresolved_broker_mutation_blocks_success_selection"
    else:
        selected = records[-1]
        if selected.terminal_outcome in {
            TerminalOutcome.RECONCILED_SUCCESS,
            TerminalOutcome.AUTHORIZED_NO_TRADE,
        }:
            status = SelectionStatus.RESOLVED
            reason = "latest_terminal_attempt_is_resolved"
        else:
            status = SelectionStatus.FAILED
            reason = "latest_terminal_attempt_is_failure"

    selected_hash = selected.content_hash or selected.with_content_hash().content_hash
    return AttemptSelection(
        trade_date=normalized_date,
        status=status,
        selected_attempt_id=selected.attempt_id,
        selected_attempt_hash=selected_hash,
        unresolved_submission_attempt_ids=unresolved,
        attempt_count=len(records),
        attempt_hashes=hashes,
        generated_at=generated_at or _utc_now(),
        reason=reason,
    )


def select_from_registry(
    registry_root: Path | str,
    *,
    trade_date: str,
    generated_at: str | None = None,
) -> AttemptSelection:
    return select_canonical_attempt(
        read_attempts(registry_root, trade_date=trade_date),
        trade_date=trade_date,
        generated_at=generated_at,
    )


def write_selection_pointer(
    registry_root: Path | str,
    selection: AttemptSelection,
) -> Path:
    """Atomically write a mutable pointer that references immutable attempts."""

    path = Path(registry_root) / _validate_trade_date(selection.trade_date) / "selection.json"
    return _atomic_write_json(path, selection.to_dict())


def append_attempt_and_update_selection(
    registry_root: Path | str,
    *,
    trade_date: str,
    build_record: Callable[[tuple[AttemptRecord, ...]], AttemptRecord],
    generated_at: str | None = None,
) -> tuple[Path, AttemptSelection, Path]:
    """Atomically coordinate read → append → select → pointer for one date.

    The callback executes while the date flock is held and therefore owns the
    next immutable sequence number. This is the only safe API for writers that
    also publish the mutable canonical selection pointer.
    """

    normalized_date = _validate_trade_date(trade_date)
    with _date_registry_lock(registry_root, normalized_date):
        prior = tuple(read_attempts(registry_root, trade_date=normalized_date))
        record = build_record(prior)
        if record.trade_date != normalized_date:
            raise ValueError("attempt transaction record trade_date mismatch")
        expected_sequence = len(prior) + 1
        if record.sequence != expected_sequence:
            raise ValueError(
                "attempt transaction record sequence must equal "
                f"{expected_sequence}"
            )
        attempt_artifact = append_attempt(registry_root, record)
        attempts = [*prior, AttemptRecord.from_dict(
            json.loads(attempt_artifact.read_text(encoding="utf-8"))
        )]
        selection = select_canonical_attempt(
            attempts,
            trade_date=normalized_date,
            generated_at=generated_at,
        )
        pointer = write_selection_pointer(registry_root, selection)
        return attempt_artifact, selection, pointer

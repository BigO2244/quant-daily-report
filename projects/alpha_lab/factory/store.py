"""Append-only, hash-chained JSONL persistence for research evidence."""

from __future__ import annotations

import fcntl
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from .canonical import (
    canonical_hash,
    canonical_json,
    format_datetime,
    parse_datetime,
    require_non_empty,
    require_sha256,
)
from .contracts import Observation, _require_aware
from .errors import ContractValidationError, EventStoreIntegrityError, ResearchBoundaryError


_FORBIDDEN_PATH_PARTS = frozenset(
    {
        "broker",
        "brokers",
        "cron",
        "deploy",
        "execution",
        "production",
        "runtime",
    }
)


@dataclass(frozen=True)
class EventRecord:
    event_id: str
    event_type: str
    occurred_at: datetime
    recorded_at: datetime
    payload: Mapping[str, Any]
    payload_hash: str
    previous_event_hash: Optional[str]
    event_hash: str
    schema_version: str = "caerus_alpha_lab_event_v1"

    def __post_init__(self) -> None:
        require_non_empty(self.event_id, "event_id")
        require_non_empty(self.event_type, "event_type")
        _require_aware(self.occurred_at, "occurred_at")
        _require_aware(self.recorded_at, "recorded_at")
        require_sha256(self.payload_hash, "payload_hash")
        require_sha256(self.event_hash, "event_hash")
        if self.previous_event_hash is not None:
            require_sha256(self.previous_event_hash, "previous_event_hash")
        if canonical_hash(self.payload) != self.payload_hash:
            raise ContractValidationError("event payload_hash does not match payload")
        if canonical_hash(self.unsigned_dict()) != self.event_hash:
            raise ContractValidationError("event_hash does not match event envelope")

    def unsigned_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "occurred_at": format_datetime(self.occurred_at),
            "recorded_at": format_datetime(self.recorded_at),
            "payload": self.payload,
            "payload_hash": self.payload_hash,
            "previous_event_hash": self.previous_event_hash,
        }

    def to_dict(self) -> Dict[str, Any]:
        result = self.unsigned_dict()
        result["event_hash"] = self.event_hash
        return result


class AppendOnlyJSONLEventStore:
    """Persist research events by append only and verify the complete hash chain."""

    def __init__(self, path: Path, *, research_root: Path) -> None:
        self.research_root = Path(research_root).expanduser().resolve()
        self.path = Path(path).expanduser().resolve()
        try:
            relative = self.path.relative_to(self.research_root)
        except ValueError as exc:
            raise ResearchBoundaryError("event store must be inside research_root") from exc
        parts = {part.lower() for part in self.path.parts}
        if parts.intersection(_FORBIDDEN_PATH_PARTS):
            raise ResearchBoundaryError("event store path crosses a forbidden runtime boundary")
        if not relative.parts or relative == Path("."):
            raise ResearchBoundaryError("event store path must name a file inside research_root")
        if not self.research_root.is_dir():
            raise ResearchBoundaryError("research_root must already exist as a directory")
        if self.path.parent != self.research_root and not self.path.parent.is_dir():
            raise ResearchBoundaryError("event store parent directory must already exist")

    def append(
        self,
        *,
        event_id: str,
        event_type: str,
        occurred_at: datetime,
        recorded_at: datetime,
        payload: Mapping[str, Any],
        validate_existing: Optional[Callable[[List[EventRecord]], None]] = None,
    ) -> EventRecord:
        require_non_empty(event_id, "event_id")
        require_non_empty(event_type, "event_type")
        _require_aware(occurred_at, "occurred_at")
        _require_aware(recorded_at, "recorded_at")
        if not isinstance(payload, Mapping):
            raise ContractValidationError("event payload must be a mapping")

        descriptor = os.open(str(self.path), os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            with os.fdopen(descriptor, "r+", encoding="utf-8", closefd=True) as stream:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                records = self._read_stream(stream)
                if validate_existing is not None:
                    validate_existing(records)
                if any(record.event_id == event_id for record in records):
                    raise EventStoreIntegrityError("duplicate event_id: {}".format(event_id))
                previous_hash = records[-1].event_hash if records else None
                unsigned = {
                    "schema_version": "caerus_alpha_lab_event_v1",
                    "event_id": event_id,
                    "event_type": event_type,
                    "occurred_at": format_datetime(occurred_at),
                    "recorded_at": format_datetime(recorded_at),
                    "payload": payload,
                    "payload_hash": canonical_hash(payload),
                    "previous_event_hash": previous_hash,
                }
                record = EventRecord(
                    event_id=event_id,
                    event_type=event_type,
                    occurred_at=occurred_at,
                    recorded_at=recorded_at,
                    payload=payload,
                    payload_hash=unsigned["payload_hash"],
                    previous_event_hash=previous_hash,
                    event_hash=canonical_hash(unsigned),
                )
                stream.seek(0, os.SEEK_END)
                stream.write(canonical_json(record.to_dict()) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
                return record
        except Exception:
            # os.fdopen owns and closes descriptor after successful construction.
            raise

    def append_observation(
        self,
        *,
        event_id: str,
        observation: Observation,
        decision_timestamp: datetime,
        recorded_at: datetime
    ) -> EventRecord:
        observation.require_consumable_at(decision_timestamp)
        return self.append(
            event_id=event_id,
            event_type="observation_consumed",
            occurred_at=decision_timestamp,
            recorded_at=recorded_at,
            payload=observation.to_dict(),
        )

    def read_all(self) -> List[EventRecord]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_SH)
            return self._read_stream(stream)

    def _read_stream(self, stream: Any) -> List[EventRecord]:
        stream.seek(0)
        text = stream.read()
        if text and not text.endswith("\n"):
            raise EventStoreIntegrityError("event store ends with a partial JSONL record")
        records = []
        previous_hash = None
        event_ids = set()
        for line_number, line in enumerate(text.splitlines(), start=1):
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise EventStoreIntegrityError(
                    "invalid JSON at event-store line {}".format(line_number)
                ) from exc
            try:
                record = EventRecord(
                    event_id=raw["event_id"],
                    event_type=raw["event_type"],
                    occurred_at=parse_datetime(raw["occurred_at"]),
                    recorded_at=parse_datetime(raw["recorded_at"]),
                    payload=raw["payload"],
                    payload_hash=raw["payload_hash"],
                    previous_event_hash=raw["previous_event_hash"],
                    event_hash=raw["event_hash"],
                    schema_version=raw["schema_version"],
                )
            except (KeyError, ContractValidationError) as exc:
                raise EventStoreIntegrityError(
                    "invalid event contract at line {}".format(line_number)
                ) from exc
            if record.previous_event_hash != previous_hash:
                raise EventStoreIntegrityError(
                    "broken event hash chain at line {}".format(line_number)
                )
            if record.event_id in event_ids:
                raise EventStoreIntegrityError(
                    "duplicate event_id at line {}".format(line_number)
                )
            records.append(record)
            event_ids.add(record.event_id)
            previous_hash = record.event_hash
        return records

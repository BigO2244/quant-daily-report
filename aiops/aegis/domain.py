"""Stable identifiers, lifecycle rules, and non-executing domain definitions."""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any


class MissionState(str, Enum):
    DRAFT = "DRAFT"
    PLANNED = "PLANNED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    RUNNING = "RUNNING"
    BLOCKED = "BLOCKED"
    VERIFYING = "VERIFYING"
    DECISION_REQUIRED = "DECISION_REQUIRED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TaskState(str, Enum):
    DRAFT = "DRAFT"
    READY = "READY"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    RUNNING = "RUNNING"
    BLOCKED = "BLOCKED"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


MISSION_TRANSITIONS = {
    MissionState.DRAFT: {MissionState.PLANNED, MissionState.CANCELLED},
    MissionState.PLANNED: {MissionState.APPROVAL_REQUIRED, MissionState.CANCELLED},
    MissionState.APPROVAL_REQUIRED: {MissionState.RUNNING, MissionState.CANCELLED},
    MissionState.RUNNING: {MissionState.BLOCKED, MissionState.VERIFYING, MissionState.FAILED},
    MissionState.BLOCKED: {MissionState.RUNNING, MissionState.CANCELLED, MissionState.FAILED},
    MissionState.VERIFYING: {MissionState.DECISION_REQUIRED, MissionState.COMPLETED, MissionState.FAILED},
    MissionState.DECISION_REQUIRED: {MissionState.RUNNING, MissionState.COMPLETED, MissionState.CANCELLED},
    MissionState.COMPLETED: set(), MissionState.FAILED: set(), MissionState.CANCELLED: set(),
}
TASK_TRANSITIONS = {
    TaskState.DRAFT: {TaskState.READY, TaskState.CANCELLED},
    TaskState.READY: {TaskState.APPROVAL_REQUIRED, TaskState.RUNNING, TaskState.CANCELLED},
    TaskState.APPROVAL_REQUIRED: {TaskState.READY, TaskState.RUNNING, TaskState.CANCELLED},
    TaskState.RUNNING: {TaskState.BLOCKED, TaskState.VERIFYING, TaskState.FAILED},
    TaskState.BLOCKED: {TaskState.READY, TaskState.CANCELLED, TaskState.FAILED},
    TaskState.VERIFYING: {TaskState.COMPLETED, TaskState.FAILED},
    TaskState.COMPLETED: set(), TaskState.FAILED: set(), TaskState.CANCELLED: set(),
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def stable_id(prefix: str, value: Any) -> str:
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"


def validate_transition(current: str, target: str, transitions: dict[Enum, set[Enum]]) -> None:
    try:
        state_type = type(next(iter(transitions)))
        current_state = state_type(current)
        requested = state_type(target)
        allowed = transitions[current_state]
    except ValueError as exc:
        raise ValueError(f"Unknown lifecycle state: {current!r} -> {target!r}") from exc
    if requested not in allowed:
        raise ValueError(f"Invalid lifecycle transition: {current} -> {target}")

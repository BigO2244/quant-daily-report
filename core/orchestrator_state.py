"""Append-only, hash-chained Caerus orchestration state transitions."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


STAGES = (
    "OBSERVE",
    "RESEARCH",
    "PRECOMPUTE",
    "DECIDE",
    "AUTHORIZE",
    "EXECUTE",
    "VERIFY",
    "RECONCILE",
    "LEARN",
)
SCHEMA_VERSION = "caerus.orchestrator_transition.v1"
_SAFE = re.compile(r"^[A-Za-z0-9_.:-]+$")


class OrchestratorStateError(RuntimeError):
    pass


def _hash(payload: Mapping[str, Any]) -> str:
    unhashed = dict(payload)
    unhashed.pop("content_hash", None)
    return hashlib.sha256(
        json.dumps(unhashed, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _safe(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or not _SAFE.fullmatch(normalized) or ".." in normalized:
        raise OrchestratorStateError(f"invalid {label}")
    return normalized


@dataclass(frozen=True)
class OrchestratorTransition:
    plan_id: str
    trade_date: str
    stage: str
    stage_index: int
    status: str
    recorded_at: str
    artifact_refs: tuple[str, ...]
    previous_hash: str | None
    content_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "plan_id": self.plan_id,
            "trade_date": self.trade_date,
            "stage": self.stage,
            "stage_index": self.stage_index,
            "status": self.status,
            "recorded_at": self.recorded_at,
            "artifact_refs": list(self.artifact_refs),
            "previous_hash": self.previous_hash,
            "content_hash": self.content_hash,
        }


def _directory(root: Path | str, trade_date: str, plan_id: str) -> Path:
    _safe(trade_date, "trade_date")
    safe_plan = _safe(plan_id, "plan_id").replace(":", "_")
    return Path(root) / trade_date / safe_plan


def load_orchestrator_state(
    root: Path | str, *, trade_date: str, plan_id: str
) -> list[OrchestratorTransition]:
    directory = _directory(root, trade_date, plan_id)
    transitions: list[OrchestratorTransition] = []
    previous_hash: str | None = None
    for expected_index, path in enumerate(sorted(directory.glob("*.json"))):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise OrchestratorStateError("unsupported orchestrator transition schema")
        if int(payload.get("stage_index", -1)) != expected_index:
            raise OrchestratorStateError("orchestrator stage sequence gap")
        if payload.get("stage") != STAGES[expected_index]:
            raise OrchestratorStateError("illegal orchestrator stage order")
        if payload.get("previous_hash") != previous_hash:
            raise OrchestratorStateError("orchestrator hash chain mismatch")
        if payload.get("content_hash") != _hash(payload):
            raise OrchestratorStateError("orchestrator transition hash mismatch")
        transition = OrchestratorTransition(
            plan_id=str(payload["plan_id"]),
            trade_date=str(payload["trade_date"]),
            stage=str(payload["stage"]),
            stage_index=int(payload["stage_index"]),
            status=str(payload["status"]),
            recorded_at=str(payload["recorded_at"]),
            artifact_refs=tuple(str(value) for value in payload.get("artifact_refs") or ()),
            previous_hash=(str(payload["previous_hash"]) if payload.get("previous_hash") else None),
            content_hash=str(payload["content_hash"]),
        )
        if transition.plan_id != plan_id or transition.trade_date != trade_date:
            raise OrchestratorStateError("orchestrator transition identity mismatch")
        transitions.append(transition)
        previous_hash = transition.content_hash
    return transitions


def append_orchestrator_transition(
    root: Path | str,
    *,
    trade_date: str,
    plan_id: str,
    stage: str,
    status: str,
    recorded_at: str,
    artifact_refs: tuple[str, ...] = (),
) -> OrchestratorTransition:
    stage = str(stage).strip().upper()
    status = str(status).strip().upper()
    if stage not in STAGES or status not in {"PASS", "FAILED", "NO_ACTION"}:
        raise OrchestratorStateError("invalid orchestrator stage/status")
    prior = load_orchestrator_state(root, trade_date=trade_date, plan_id=plan_id)
    expected_index = len(prior)
    if expected_index >= len(STAGES) or STAGES[expected_index] != stage:
        if any(item.stage == stage for item in prior):
            existing = next(item for item in prior if item.stage == stage)
            if existing.status == status and existing.artifact_refs == tuple(artifact_refs):
                return existing
            raise OrchestratorStateError("conflicting orchestrator stage replay")
        raise OrchestratorStateError("illegal orchestrator transition")
    if prior and prior[-1].status == "FAILED":
        raise OrchestratorStateError("failed orchestrator transition is terminal")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "plan_id": plan_id,
        "trade_date": trade_date,
        "stage": stage,
        "stage_index": expected_index,
        "status": status,
        "recorded_at": recorded_at,
        "artifact_refs": list(artifact_refs),
        "previous_hash": prior[-1].content_hash if prior else None,
    }
    payload["content_hash"] = _hash(payload)
    directory = _directory(root, trade_date, plan_id)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{expected_index:02d}_{stage.lower()}.json"
    try:
        with target.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise OrchestratorStateError("orchestrator transition race/conflict") from exc
    directory_fd = os.open(str(directory), os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return load_orchestrator_state(root, trade_date=trade_date, plan_id=plan_id)[-1]

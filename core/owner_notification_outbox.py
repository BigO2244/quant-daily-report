"""Hash-bound owner notification outbox with no delivery capability.

The outbox is a durable handoff boundary, not a sender.  It accepts only an
explicit redacted destination binding and immutable advisory items.  The
library has no network client and no delivery function; persistence itself is
also off by default and requires a literal ``write_enabled=True``.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from authority.lane_exact_plan import canonical_json


OWNER_NOTIFICATION_BINDING_SCHEMA = "caerus.owner_notification_binding.v1"
OWNER_NOTIFICATION_ITEM_SCHEMA = "caerus.owner_notification_outbox_item.v1"
OWNER_NOTIFICATION_PERSISTENCE_SCHEMA = "caerus.owner_notification_outbox_persistence.v1"

_SHA = frozenset("0123456789abcdef")
_BINDING_FIELDS = frozenset(
    {
        "schema_version", "binding_id", "owner_label", "channel_class",
        "destination_reference_hash", "allowed_event_types", "send_enabled",
        "send_authority", "secrets_persisted", "content_hash",
    }
)
_ITEM_FIELDS = frozenset(
    {
        "schema_version", "notification_id", "created_at", "binding_hash",
        "event_type", "severity", "subject", "message", "source_artifact_schema",
        "source_artifact_hash", "required_external_owner_action", "delivery_status",
        "send_requested", "send_performed", "external_call_performed",
        "execution_authority", "activation_authority", "approval_authority",
        "content_hash",
    }
)
_PERSISTENCE_FIELDS = frozenset(
    {
        "schema_version", "status", "path_hash", "write_enabled", "existing_count",
        "requested_count", "appended_count", "final_count", "item_hashes",
        "outbox_hash", "send_performed", "external_call_performed",
        "execution_authority", "activation_authority", "approval_authority",
        "content_hash",
    }
)


class OwnerNotificationOutboxError(ValueError):
    """Raised when an advisory owner notification is unsafe or mutable."""


def _hash(payload: Mapping[str, Any]) -> str:
    body = copy.deepcopy(dict(payload))
    body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def _seal(payload: dict[str, Any]) -> dict[str, Any]:
    payload["content_hash"] = _hash(payload)
    return payload


def _sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in _SHA for c in value):
        raise OwnerNotificationOutboxError(f"{label} must be a lowercase SHA-256 hash")
    return value


def _text(value: Any, *, label: str, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum:
        raise OwnerNotificationOutboxError(f"{label} must be a bounded non-blank string")
    lowered = value.lower()
    forbidden = (
        "apca-api-key-id", "apca-api-secret-key", "alpaca_api_key_id",
        "alpaca_api_secret_key", "account_number", "private_key",
    )
    if any(marker in lowered for marker in forbidden):
        raise OwnerNotificationOutboxError(f"{label} contains prohibited secret/account material")
    return value


def _strict_json_object(line: str, *, label: str) -> dict[str, Any]:
    def pairs(rows: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in rows:
            if key in out:
                raise OwnerNotificationOutboxError(f"{label} has duplicate key {key}")
            out[key] = value
        return out

    def reject_constant(value: str) -> None:
        raise OwnerNotificationOutboxError(f"{label} has non-finite number {value}")

    try:
        value = json.loads(line, object_pairs_hook=pairs, parse_constant=reject_constant)
    except (json.JSONDecodeError, TypeError) as exc:
        raise OwnerNotificationOutboxError(f"{label} is not strict JSON") from exc
    if not isinstance(value, dict):
        raise OwnerNotificationOutboxError(f"{label} must be a JSON object")
    return value


def build_owner_notification_binding(
    *, owner_label: str, channel_class: str, destination_reference_hash: str,
    allowed_event_types: Sequence[str],
) -> dict[str, Any]:
    events = sorted(set(allowed_event_types))
    if not events or list(allowed_event_types) != events:
        raise OwnerNotificationOutboxError("allowed_event_types must be sorted and unique")
    for event in events:
        _text(event, label="event_type", maximum=96)
    owner = _text(owner_label, label="owner_label", maximum=96)
    channel = _text(channel_class, label="channel_class", maximum=48)
    destination_hash = _sha(destination_reference_hash, label="destination_reference_hash")
    seed = hashlib.sha256(
        canonical_json([owner, channel, destination_hash, events]).encode("utf-8")
    ).hexdigest()
    return validate_owner_notification_binding(
        _seal(
            {
                "schema_version": OWNER_NOTIFICATION_BINDING_SCHEMA,
                "binding_id": f"owner-notify:{seed[:24]}",
                "owner_label": owner,
                "channel_class": channel,
                "destination_reference_hash": destination_hash,
                "allowed_event_types": events,
                "send_enabled": False,
                "send_authority": False,
                "secrets_persisted": False,
            }
        )
    )


def validate_owner_notification_binding(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or set(payload) != _BINDING_FIELDS:
        raise OwnerNotificationOutboxError("owner notification binding fields are invalid")
    if payload.get("schema_version") != OWNER_NOTIFICATION_BINDING_SCHEMA:
        raise OwnerNotificationOutboxError("unsupported owner notification binding")
    _text(payload.get("binding_id"), label="binding_id", maximum=96)
    _text(payload.get("owner_label"), label="owner_label", maximum=96)
    _text(payload.get("channel_class"), label="channel_class", maximum=48)
    _sha(payload.get("destination_reference_hash"), label="destination_reference_hash")
    events = payload.get("allowed_event_types")
    if not isinstance(events, list) or not events or events != sorted(set(events)):
        raise OwnerNotificationOutboxError("allowed_event_types must be sorted and unique")
    for event in events:
        _text(event, label="event_type", maximum=96)
    for field in ("send_enabled", "send_authority", "secrets_persisted"):
        if payload.get(field) is not False:
            raise OwnerNotificationOutboxError(f"notification binding {field} must remain false")
    if _sha(payload.get("content_hash"), label="content_hash") != _hash(payload):
        raise OwnerNotificationOutboxError("notification binding content_hash mismatch")
    return copy.deepcopy(dict(payload))


def build_owner_notification_item(
    *, binding: Mapping[str, Any], created_at: str, event_type: str, severity: str,
    subject: str, message: str, source_artifact_schema: str,
    source_artifact_hash: str, required_external_owner_action: str,
) -> dict[str, Any]:
    checked = validate_owner_notification_binding(binding)
    event = _text(event_type, label="event_type", maximum=96)
    if event not in checked["allowed_event_types"]:
        raise OwnerNotificationOutboxError("event_type is not allowed by binding")
    body = {
        "schema_version": OWNER_NOTIFICATION_ITEM_SCHEMA,
        "notification_id": "pending",
        "created_at": _text(created_at, label="created_at", maximum=64),
        "binding_hash": checked["content_hash"],
        "event_type": event,
        "severity": _text(severity, label="severity", maximum=32),
        "subject": _text(subject, label="subject", maximum=240),
        "message": _text(message, label="message", maximum=4096),
        "source_artifact_schema": _text(
            source_artifact_schema, label="source_artifact_schema", maximum=128
        ),
        "source_artifact_hash": _sha(source_artifact_hash, label="source_artifact_hash"),
        "required_external_owner_action": _text(
            required_external_owner_action,
            label="required_external_owner_action",
            maximum=192,
        ),
        "delivery_status": "PENDING_SEND_DISABLED",
        "send_requested": False,
        "send_performed": False,
        "external_call_performed": False,
        "execution_authority": False,
        "activation_authority": False,
        "approval_authority": False,
    }
    identity = hashlib.sha256(
        canonical_json(
            [checked["content_hash"], event, body["source_artifact_hash"], body["required_external_owner_action"]]
        ).encode("utf-8")
    ).hexdigest()
    body["notification_id"] = f"owner-notification:{identity[:32]}"
    return validate_owner_notification_item(_seal(body), binding=checked)


def validate_owner_notification_item(
    payload: Mapping[str, Any], *, binding: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or set(payload) != _ITEM_FIELDS:
        raise OwnerNotificationOutboxError("owner notification item fields are invalid")
    if payload.get("schema_version") != OWNER_NOTIFICATION_ITEM_SCHEMA:
        raise OwnerNotificationOutboxError("unsupported owner notification item")
    for field, maximum in (
        ("notification_id", 128), ("created_at", 64), ("event_type", 96),
        ("severity", 32), ("subject", 240), ("message", 4096),
        ("source_artifact_schema", 128), ("required_external_owner_action", 192),
    ):
        _text(payload.get(field), label=field, maximum=maximum)
    _sha(payload.get("binding_hash"), label="binding_hash")
    _sha(payload.get("source_artifact_hash"), label="source_artifact_hash")
    if payload.get("delivery_status") != "PENDING_SEND_DISABLED":
        raise OwnerNotificationOutboxError("notification delivery must remain disabled")
    for field in (
        "send_requested", "send_performed", "external_call_performed",
        "execution_authority", "activation_authority", "approval_authority",
    ):
        if payload.get(field) is not False:
            raise OwnerNotificationOutboxError(f"notification {field} must remain false")
    if binding is not None:
        checked = validate_owner_notification_binding(binding)
        if payload["binding_hash"] != checked["content_hash"]:
            raise OwnerNotificationOutboxError("notification binding hash mismatch")
        if payload["event_type"] not in checked["allowed_event_types"]:
            raise OwnerNotificationOutboxError("notification event is not allowed")
    if _sha(payload.get("content_hash"), label="content_hash") != _hash(payload):
        raise OwnerNotificationOutboxError("notification content_hash mismatch")
    return copy.deepcopy(dict(payload))


def _read_outbox(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            raise OwnerNotificationOutboxError(f"outbox line {index} is blank")
        rows.append(validate_owner_notification_item(_strict_json_object(line, label=f"outbox line {index}")))
    seen: dict[str, str] = {}
    for row in rows:
        prior = seen.get(row["notification_id"])
        if prior is not None:
            raise OwnerNotificationOutboxError("existing outbox contains duplicate notification_id")
        seen[row["notification_id"]] = row["content_hash"]
    return rows


def _outbox_hash(rows: Sequence[Mapping[str, Any]]) -> str:
    return hashlib.sha256(
        "".join(canonical_json(row) + "\n" for row in rows).encode("utf-8")
    ).hexdigest()


def persist_owner_notification_outbox(
    *, path: Path | str, items: Sequence[Mapping[str, Any]], write_enabled: bool = False,
) -> dict[str, Any]:
    """Validate an idempotent append; write atomically only with literal opt-in."""

    if type(write_enabled) is not bool:
        raise OwnerNotificationOutboxError("write_enabled must be a literal boolean")
    target = Path(path)
    existing = _read_outbox(target)
    checked = [validate_owner_notification_item(item) for item in items]
    if not checked:
        raise OwnerNotificationOutboxError("at least one notification item is required")
    if [row["notification_id"] for row in checked] != sorted(
        set(row["notification_id"] for row in checked)
    ):
        raise OwnerNotificationOutboxError("requested notifications must be sorted and unique")
    by_id = {row["notification_id"]: row for row in existing}
    append: list[dict[str, Any]] = []
    for row in checked:
        prior = by_id.get(row["notification_id"])
        if prior is not None and prior["content_hash"] != row["content_hash"]:
            raise OwnerNotificationOutboxError("notification_id conflicts with immutable existing item")
        if prior is None:
            append.append(row)
    final = existing + append
    if write_enabled and append:
        target.parent.mkdir(parents=True, exist_ok=True)
        lock = target.with_name(target.name + ".lock")
        try:
            lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise OwnerNotificationOutboxError("outbox is locked by another writer") from exc
        os.close(lock_fd)
        temp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=target.parent,
                prefix=target.name + ".", suffix=".tmp", delete=False,
            ) as handle:
                temp_name = handle.name
                for row in final:
                    handle.write(canonical_json(row) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, target)
            temp_name = None
            if _read_outbox(target) != final:
                raise OwnerNotificationOutboxError("outbox read-back differs after atomic write")
        finally:
            if temp_name:
                Path(temp_name).unlink(missing_ok=True)
            lock.unlink(missing_ok=True)
    result = _seal(
        {
            "schema_version": OWNER_NOTIFICATION_PERSISTENCE_SCHEMA,
            "status": (
                "DRY_RUN_NO_WRITE" if not write_enabled
                else "PERSISTED" if append else "IDEMPOTENT_ALREADY_PRESENT"
            ),
            "path_hash": hashlib.sha256(str(target.resolve()).encode("utf-8")).hexdigest(),
            "write_enabled": write_enabled,
            "existing_count": len(existing),
            "requested_count": len(checked),
            "appended_count": len(append) if write_enabled else 0,
            "final_count": len(final) if write_enabled else len(existing),
            "item_hashes": [row["content_hash"] for row in checked],
            "outbox_hash": _outbox_hash(final if write_enabled else existing),
            "send_performed": False,
            "external_call_performed": False,
            "execution_authority": False,
            "activation_authority": False,
            "approval_authority": False,
        }
    )
    return validate_owner_notification_persistence(result)


def validate_owner_notification_persistence(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or set(payload) != _PERSISTENCE_FIELDS:
        raise OwnerNotificationOutboxError("notification persistence fields are invalid")
    if payload.get("schema_version") != OWNER_NOTIFICATION_PERSISTENCE_SCHEMA:
        raise OwnerNotificationOutboxError("unsupported notification persistence result")
    if payload.get("status") not in {"DRY_RUN_NO_WRITE", "PERSISTED", "IDEMPOTENT_ALREADY_PRESENT"}:
        raise OwnerNotificationOutboxError("notification persistence status is invalid")
    for field in ("path_hash", "outbox_hash", "content_hash"):
        _sha(payload.get(field), label=field)
    for field in ("existing_count", "requested_count", "appended_count", "final_count"):
        if type(payload.get(field)) is not int or payload[field] < 0:
            raise OwnerNotificationOutboxError(f"{field} must be a non-negative integer")
    hashes = payload.get("item_hashes")
    if not isinstance(hashes, list):
        raise OwnerNotificationOutboxError("item_hashes must be an array")
    for value in hashes:
        _sha(value, label="item_hash")
    if type(payload.get("write_enabled")) is not bool:
        raise OwnerNotificationOutboxError("write_enabled must be boolean")
    for field in (
        "send_performed", "external_call_performed", "execution_authority",
        "activation_authority", "approval_authority",
    ):
        if payload.get(field) is not False:
            raise OwnerNotificationOutboxError(f"persistence {field} must remain false")
    if payload["status"] == "DRY_RUN_NO_WRITE" and payload["write_enabled"] is not False:
        raise OwnerNotificationOutboxError("dry-run result cannot claim writes enabled")
    if _hash(payload) != payload["content_hash"]:
        raise OwnerNotificationOutboxError("notification persistence content_hash mismatch")
    return copy.deepcopy(dict(payload))


__all__ = [
    "OWNER_NOTIFICATION_BINDING_SCHEMA", "OWNER_NOTIFICATION_ITEM_SCHEMA",
    "OWNER_NOTIFICATION_PERSISTENCE_SCHEMA", "OwnerNotificationOutboxError",
    "build_owner_notification_binding", "validate_owner_notification_binding",
    "build_owner_notification_item", "validate_owner_notification_item",
    "persist_owner_notification_outbox", "validate_owner_notification_persistence",
]

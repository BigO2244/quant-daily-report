import copy
import hashlib

import pytest

from core.owner_notification_outbox import (
    OwnerNotificationOutboxError,
    build_owner_notification_binding,
    build_owner_notification_item,
    persist_owner_notification_outbox,
    validate_owner_notification_item,
)


H = hashlib.sha256(b"source").hexdigest()


def _binding():
    return build_owner_notification_binding(
        owner_label="CAERUS_OWNER",
        channel_class="REDACTED_EXTERNAL_DESTINATION",
        destination_reference_hash=hashlib.sha256(b"owner-destination").hexdigest(),
        allowed_event_types=["LANE_DEPRECATION_READY", "LANE_PROMOTION_READY"],
    )


def _item():
    return build_owner_notification_item(
        binding=_binding(),
        created_at="2026-08-19T02:00:00+00:00",
        event_type="LANE_PROMOTION_READY",
        severity="OWNER_REVIEW",
        subject="Generic Live candidate is ready for review",
        message="Review the sealed candidate and choose Approve or Reject externally.",
        source_artifact_schema="caerus.generic_live_candidate_preflight.v1",
        source_artifact_hash=H,
        required_external_owner_action="APPROVE_OR_REJECT_CANDIDATE_EXTERNALLY",
    )


def test_outbox_defaults_to_no_write_and_has_no_send_capability(tmp_path):
    target = tmp_path / "owner-outbox.jsonl"
    result = persist_owner_notification_outbox(path=target, items=[_item()])
    assert result["status"] == "DRY_RUN_NO_WRITE"
    assert result["write_enabled"] is False
    assert result["send_performed"] is False
    assert result["external_call_performed"] is False
    assert not target.exists()


def test_explicit_persistence_is_atomic_and_idempotent(tmp_path):
    target = tmp_path / "owner-outbox.jsonl"
    first = persist_owner_notification_outbox(path=target, items=[_item()], write_enabled=True)
    before = target.read_bytes()
    second = persist_owner_notification_outbox(path=target, items=[_item()], write_enabled=True)
    assert first["status"] == "PERSISTED"
    assert first["appended_count"] == 1
    assert second["status"] == "IDEMPOTENT_ALREADY_PRESENT"
    assert second["appended_count"] == 0
    assert target.read_bytes() == before
    assert target.stat().st_mode & 0o777 == 0o600


def test_tamper_and_conflicting_identity_fail_closed(tmp_path):
    item = _item()
    tampered = copy.deepcopy(item)
    tampered["send_requested"] = True
    with pytest.raises(OwnerNotificationOutboxError):
        validate_owner_notification_item(tampered)

    target = tmp_path / "owner-outbox.jsonl"
    persist_owner_notification_outbox(path=target, items=[item], write_enabled=True)
    conflict = copy.deepcopy(item)
    conflict["message"] = "Different immutable economics-free advisory text."
    conflict["content_hash"] = hashlib.sha256(b"wrong").hexdigest()
    with pytest.raises(OwnerNotificationOutboxError):
        persist_owner_notification_outbox(path=target, items=[conflict], write_enabled=True)


def test_secret_markers_are_rejected():
    with pytest.raises(OwnerNotificationOutboxError, match="prohibited"):
        build_owner_notification_item(
            binding=_binding(),
            created_at="2026-08-19T02:00:00+00:00",
            event_type="LANE_PROMOTION_READY",
            severity="OWNER_REVIEW",
            subject="Review",
            message="ALPACA_API_SECRET_KEY must never be copied here",
            source_artifact_schema="caerus.generic_live_candidate_preflight.v1",
            source_artifact_hash=H,
            required_external_owner_action="REVIEW_EXTERNALLY",
        )


def test_existing_duplicate_keys_and_nonfinite_values_fail_closed(tmp_path):
    target = tmp_path / "owner-outbox.jsonl"
    target.write_text('{"schema_version":"x","schema_version":"y"}\n')
    with pytest.raises(OwnerNotificationOutboxError, match="duplicate key"):
        persist_owner_notification_outbox(path=target, items=[_item()])
    target.write_text('{"value":NaN}\n')
    with pytest.raises(OwnerNotificationOutboxError, match="non-finite"):
        persist_owner_notification_outbox(path=target, items=[_item()])

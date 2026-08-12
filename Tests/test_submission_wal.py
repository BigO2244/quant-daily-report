from __future__ import annotations

import json

import pytest

import core.submission_wal as wal
from core.submission_wal import (
    OrderIntent,
    ResolutionState,
    WalPersistenceError,
    append_resolution,
    new_resolution,
    prepare_order_intent,
    read_resolutions,
    unresolved_intent_requires_lookup,
)


def _intent(**overrides) -> OrderIntent:
    values = {
        "trade_date": "2026-08-12",
        "plan_id": "plan:2026-08-12",
        "plan_hash": "plan-hash-123",
        "attempt_id": "attempt-1",
        "order_id": "order:sell:1:stable",
        "client_order_id": "qd:stable:STX:SELL",
        "symbol": "STX",
        "side": "SELL",
        "quantity": 1,
        "order_type": "market",
        "created_at": "2026-08-12T15:28:09Z",
        "expected_price": 818.47,
        "notional": 818.47,
        "sleeve": "caerus_orion",
    }
    values.update(overrides)
    return OrderIntent(**values)


def test_first_prewrite_is_durable_and_exact_replay_cannot_resubmit(tmp_path) -> None:
    root = tmp_path / "wal"
    first = prepare_order_intent(root, _intent())
    replay = prepare_order_intent(
        root,
        _intent(
            attempt_id="restart-attempt-2",
            created_at="2026-08-12T15:29:00Z",
        ),
    )

    assert first.created is True
    assert first.broker_submission_allowed is True
    assert first.intent.content_hash
    assert replay.created is False
    assert replay.broker_submission_allowed is False
    assert replay.recovery_lookup_required is True
    assert replay.intent.content_hash == first.intent.content_hash
    assert replay.intent.created_at == "2026-08-12T15:28:09Z"
    assert replay.intent.attempt_id == "attempt-1"
    assert first.path.read_bytes() == replay.path.read_bytes()


def test_crash_after_broker_acceptance_before_response_restarts_without_duplicate(tmp_path) -> None:
    root = tmp_path / "wal"
    broker_calls: list[str] = []
    broker_orders = {"qd:stable:STX:SELL": "broker-order-1"}

    first = prepare_order_intent(root, _intent())
    if first.broker_submission_allowed:
        broker_calls.append(first.intent.client_order_id)
        # Simulated crash here: broker accepted, no resolution event persisted.

    restarted = prepare_order_intent(root, _intent())
    if restarted.broker_submission_allowed:  # pragma: no cover - safety assertion
        broker_calls.append(restarted.intent.client_order_id)

    assert broker_calls == ["qd:stable:STX:SELL"]
    assert unresolved_intent_requires_lookup(
        root,
        trade_date="2026-08-12",
        client_order_id="qd:stable:STX:SELL",
    )

    recovered = new_resolution(
        resolution_id="lookup-1",
        intent=restarted.intent,
        state=ResolutionState.RECOVERED_BY_LOOKUP,
        broker_order_id=broker_orders[restarted.intent.client_order_id],
        recorded_at="2026-08-12T15:28:20Z",
    )
    append_resolution(root, recovered)
    assert not unresolved_intent_requires_lookup(
        root,
        trade_date="2026-08-12",
        client_order_id="qd:stable:STX:SELL",
    )
    assert read_resolutions(
        root,
        trade_date="2026-08-12",
        client_order_id="qd:stable:STX:SELL",
    )[0].state is ResolutionState.RECOVERED_BY_LOOKUP


def test_same_client_order_id_cannot_be_rebound_to_different_order(tmp_path) -> None:
    root = tmp_path / "wal"
    prepare_order_intent(root, _intent())
    with pytest.raises(WalPersistenceError, match="different immutable order intent"):
        prepare_order_intent(root, _intent(quantity=2))


def test_prewrite_failure_is_fail_closed(tmp_path, monkeypatch) -> None:
    def fail_write(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(wal, "_write_exclusive_json", fail_write)
    with pytest.raises(WalPersistenceError, match="unable to persist pre-submit"):
        prepare_order_intent(tmp_path / "wal", _intent())


def test_resolution_requires_prior_intent_and_matching_hash(tmp_path) -> None:
    root = tmp_path / "wal"
    intent = _intent().with_content_hash()
    event = new_resolution(
        resolution_id="submit-1",
        intent=intent,
        state=ResolutionState.SUBMITTED,
        broker_order_id="broker-order-1",
    )
    with pytest.raises(WalPersistenceError, match="without a durable WAL intent"):
        append_resolution(root, event)

    prepared = prepare_order_intent(root, _intent())
    bad = new_resolution(
        resolution_id="submit-2",
        intent=_intent(plan_hash="other-hash").with_content_hash(),
        state=ResolutionState.SUBMITTED,
        broker_order_id="broker-order-1",
    )
    with pytest.raises(WalPersistenceError, match="does not match"):
        append_resolution(root, bad)

    path = append_resolution(
        root,
        new_resolution(
            resolution_id="submit-3",
            intent=prepared.intent,
            state=ResolutionState.SUBMITTED,
            broker_order_id="broker-order-1",
        ),
    )
    assert json.loads(path.read_text(encoding="utf-8"))["content_hash"]
    before = path.read_bytes()
    same_event = read_resolutions(
        root,
        trade_date="2026-08-12",
        client_order_id="qd:stable:STX:SELL",
    )[0]
    assert append_resolution(root, same_event) == path
    assert path.read_bytes() == before


def test_submission_unknown_requires_lookup_and_never_grants_resubmit(tmp_path) -> None:
    root = tmp_path / "wal"
    prepared = prepare_order_intent(root, _intent())
    append_resolution(
        root,
        new_resolution(
            resolution_id="unknown-1",
            intent=prepared.intent,
            state=ResolutionState.SUBMISSION_UNKNOWN,
            detail="broker timeout after request bytes were sent",
        ),
    )
    replay = prepare_order_intent(root, _intent())
    assert replay.broker_submission_allowed is False
    assert replay.recovery_lookup_required is True
    assert unresolved_intent_requires_lookup(
        root,
        trade_date="2026-08-12",
        client_order_id="qd:stable:STX:SELL",
    )

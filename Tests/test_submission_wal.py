from __future__ import annotations

import json

import pytest

from authority.exact_plan import compute_starting_state_hash
import core.submission_wal as wal
from core.submission_wal import (
    BrokerOrderEvidence,
    OrderIntent,
    ResolutionState,
    WalPersistenceError,
    append_resolution,
    broker_observation_detail,
    new_resolution,
    prepare_order_intent,
    prior_intent_has_terminal_broker_evidence,
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


@pytest.mark.parametrize(
    ("broker_status", "terminal"),
    [
        ("accepted", False),
        ("new", False),
        ("pending_new", False),
        ("partially_filled", False),
        ("filled", False),
        ("rejected", True),
        ("canceled", True),
        ("expired", True),
    ],
)
def test_prior_epoch_terminality_requires_terminal_broker_status(
    tmp_path,
    broker_status: str,
    terminal: bool,
) -> None:
    root = tmp_path / "wal"
    prepared = prepare_order_intent(root, _intent())
    append_resolution(
        root,
        new_resolution(
            resolution_id="submitted-1",
            intent=prepared.intent,
            state=ResolutionState.SUBMITTED,
            broker_order_id="broker-order-1",
        ),
    )

    assert prior_intent_has_terminal_broker_evidence(
        root,
        trade_date="2026-08-12",
        client_order_id=prepared.intent.client_order_id,
        lookup_by_client_order_id=lambda client_id: {
            "id": "broker-order-1",
            "client_order_id": client_id,
            "symbol": "STX",
            "side": "SELL",
            "qty": "1",
            "status": broker_status,
            "filled_avg_price": "818.47",
            "filled_qty": (
                "1"
                if broker_status == "filled"
                else ("0.4" if broker_status == "partially_filled" else "0")
            ),
        },
    ) is terminal


def test_durable_zero_fill_rejection_requires_matching_broker_truth(tmp_path) -> None:
    root = tmp_path / "wal"
    prepared = prepare_order_intent(root, _intent())
    append_resolution(
        root,
        new_resolution(
            resolution_id="rejected-1",
            intent=prepared.intent,
            state=ResolutionState.REJECTED,
            detail="broker rejected before acceptance",
        ),
    )

    assert prior_intent_has_terminal_broker_evidence(
        root,
        trade_date="2026-08-12",
        client_order_id=prepared.intent.client_order_id,
        lookup_by_client_order_id=lambda client_id: {
            "id": "broker-order-rejected",
            "client_order_id": client_id,
            "symbol": "STX",
            "side": "SELL",
            "qty": "1",
            "filled_qty": "0",
            "status": "rejected",
        },
    )


def test_filled_prior_epoch_requires_reconciled_matching_account_state(tmp_path) -> None:
    root = tmp_path / "wal"
    prepared = prepare_order_intent(root, _intent())
    append_resolution(
        root,
        new_resolution(
            resolution_id="submitted-1",
            intent=prepared.intent,
            state=ResolutionState.SUBMITTED,
            broker_order_id="broker-order-1",
        ),
    )
    broker_row = lambda client_id: {
        "id": "broker-order-1",
        "client_order_id": client_id,
        "symbol": "STX",
        "side": "SELL",
        "qty": "1",
        "filled_qty": "1",
        "filled_avg_price": "818.47",
        "status": "filled",
    }
    starting_state_hash = compute_starting_state_hash(
        [{"symbol": "STX", "quantity": 1.0}],
        1000.0,
    )
    final_positions = []
    final_cash = 1818.47
    final_state_hash = compute_starting_state_hash(final_positions, final_cash)
    assert not prior_intent_has_terminal_broker_evidence(
        root,
        trade_date="2026-08-12",
        client_order_id=prepared.intent.client_order_id,
        lookup_by_client_order_id=broker_row,
        current_state_hash=final_state_hash,
    )
    append_resolution(
        root,
        new_resolution(
            resolution_id="reconciled-1",
            intent=prepared.intent,
            state=ResolutionState.ECONOMICALLY_RECONCILED,
            broker_order_id="broker-order-1",
            detail=json.dumps(
                {
                    "schema_version": (
                        "caerus.submission_wal_economic_reconciliation.v1"
                    ),
                    "plan_id": prepared.intent.plan_id,
                    "plan_hash": prepared.intent.plan_hash,
                    "starting_state_hash": starting_state_hash,
                    "final_state_hash": final_state_hash,
                    "reconciled_at": "2026-08-12T15:30:00Z",
                    "paper_drill_epoch": "",
                    "final_positions": final_positions,
                    "final_cash": final_cash,
                    "broker_fills": [
                        {
                            "client_order_id": prepared.intent.client_order_id,
                            "broker_order_id": "broker-order-1",
                            "symbol": "STX",
                            "side": "SELL",
                            "order_quantity": 1.0,
                            "filled_quantity": 1.0,
                            "fill_price": 818.47,
                            "status": "filled",
                        }
                    ],
                    "reconciliation_status": "CLEAN",
                },
                sort_keys=True,
            ),
        ),
    )
    assert prior_intent_has_terminal_broker_evidence(
        root,
        trade_date="2026-08-12",
        client_order_id=prepared.intent.client_order_id,
        lookup_by_client_order_id=broker_row,
        current_state_hash=final_state_hash,
    )
    assert not prior_intent_has_terminal_broker_evidence(
        root,
        trade_date="2026-08-12",
        client_order_id=prepared.intent.client_order_id,
        lookup_by_client_order_id=broker_row,
        current_state_hash="lagging-pre-fill-state",
    )


def test_prior_epoch_lookup_identity_mismatch_is_integrity_failure(tmp_path) -> None:
    root = tmp_path / "wal"
    prepared = prepare_order_intent(root, _intent())
    append_resolution(
        root,
        new_resolution(
            resolution_id="submitted-1",
            intent=prepared.intent,
            state=ResolutionState.SUBMITTED,
            broker_order_id="broker-order-1",
        ),
    )

    with pytest.raises(WalPersistenceError, match="mismatched order identity"):
        prior_intent_has_terminal_broker_evidence(
            root,
            trade_date="2026-08-12",
            client_order_id=prepared.intent.client_order_id,
            lookup_by_client_order_id=lambda _client_id: {
                "id": "broker-order-1",
                "client_order_id": "different-client-id",
                "symbol": "STX",
                "side": "SELL",
                "qty": "1",
                "filled_qty": "1",
                "status": "filled",
            },
        )


@pytest.mark.parametrize("filled_qty", ["0", "0.4", "1.1"])
def test_filled_status_with_impossible_quantity_fails_closed(
    tmp_path,
    filled_qty: str,
) -> None:
    root = tmp_path / "wal"
    prepared = prepare_order_intent(root, _intent())
    append_resolution(
        root,
        new_resolution(
            resolution_id="submitted-impossible-fill",
            intent=prepared.intent,
            state=ResolutionState.SUBMITTED,
            broker_order_id="broker-order-1",
        ),
    )
    with pytest.raises(WalPersistenceError, match="filled.*quantity"):
        prior_intent_has_terminal_broker_evidence(
            root,
            trade_date="2026-08-12",
            client_order_id=prepared.intent.client_order_id,
            lookup_by_client_order_id=lambda client_id: {
                "id": "broker-order-1",
                "client_order_id": client_id,
                "symbol": "STX",
                "side": "SELL",
                "qty": "1",
                "filled_qty": filled_qty,
                "status": "filled",
            },
        )


def test_durable_partial_fill_observation_cannot_regress_to_zero(tmp_path) -> None:
    root = tmp_path / "wal"
    prepared = prepare_order_intent(root, _intent())
    partial = BrokerOrderEvidence(
        broker_order_id="broker-order-1",
        status="partially_filled",
        filled_quantity=0.4,
        fill_price=818.47,
        terminal=False,
    )
    append_resolution(
        root,
        new_resolution(
            resolution_id="broker-observed-partial",
            intent=prepared.intent,
            state=ResolutionState.BROKER_OBSERVED,
            broker_order_id=partial.broker_order_id,
            detail=broker_observation_detail(prepared.intent, partial),
        ),
    )

    with pytest.raises(WalPersistenceError, match="filled quantity regressed"):
        prior_intent_has_terminal_broker_evidence(
            root,
            trade_date="2026-08-12",
            client_order_id=prepared.intent.client_order_id,
            lookup_by_client_order_id=lambda client_id: {
                "id": "broker-order-1",
                "client_order_id": client_id,
                "symbol": "STX",
                "side": "SELL",
                "qty": "1",
                "filled_qty": "0",
                "status": "canceled",
            },
        )


def test_legacy_partial_fill_marker_cannot_regress_to_zero(tmp_path) -> None:
    root = tmp_path / "wal"
    prepared = prepare_order_intent(root, _intent())
    append_resolution(
        root,
        new_resolution(
            resolution_id="legacy-partial",
            intent=prepared.intent,
            state=ResolutionState.SUBMITTED,
            broker_order_id="broker-order-1",
            detail="partially_filled",
        ),
    )

    with pytest.raises(WalPersistenceError, match="legacy WAL evidence"):
        prior_intent_has_terminal_broker_evidence(
            root,
            trade_date="2026-08-12",
            client_order_id=prepared.intent.client_order_id,
            lookup_by_client_order_id=lambda client_id: {
                "id": "broker-order-1",
                "client_order_id": client_id,
                "symbol": "STX",
                "side": "SELL",
                "qty": "1",
                "filled_qty": "0",
                "status": "canceled",
            },
        )


@pytest.mark.parametrize(
    ("side", "limit_price", "fill_price", "valid"),
    [
        ("BUY", 100.0, 100.0, True),
        ("BUY", 100.0, 99.0, True),
        ("BUY", 100.0, 101.0, False),
        ("SELL", 100.0, 100.0, True),
        ("SELL", 100.0, 101.0, True),
        ("SELL", 100.0, 99.0, False),
    ],
)
def test_limit_fill_price_must_respect_durable_limit(
    tmp_path,
    side: str,
    limit_price: float,
    fill_price: float,
    valid: bool,
) -> None:
    root = tmp_path / "wal"
    prepared = prepare_order_intent(
        root,
        _intent(side=side, order_type="limit", limit_price=limit_price),
    )
    observed = {
        "id": "broker-order-limit",
        "client_order_id": prepared.intent.client_order_id,
        "symbol": "STX",
        "side": side,
        "qty": "1",
        "filled_qty": "1",
        "filled_avg_price": str(fill_price),
        "status": "filled",
    }
    if valid:
        assert not prior_intent_has_terminal_broker_evidence(
            root,
            trade_date="2026-08-12",
            client_order_id=prepared.intent.client_order_id,
            lookup_by_client_order_id=lambda _client_id: observed,
        )
    else:
        with pytest.raises(WalPersistenceError, match="violates durable limit"):
            prior_intent_has_terminal_broker_evidence(
                root,
                trade_date="2026-08-12",
                client_order_id=prepared.intent.client_order_id,
                lookup_by_client_order_id=lambda _client_id: observed,
            )

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.regime_authority import decide_regime_authority
from core.regime_state_store import (
    RegimeStateConflictError,
    RegimeStateCorruptionError,
    commit_prepared_regime_authority,
    load_regime_history,
    persist_regime_authority,
    prepare_regime_authority,
)


def _persist(
    root: Path,
    run_id: str,
    *,
    observed: str = "LOW",
    confidence: float = 0.9,
    acute: bool = False,
    account_id: str = "paper-account",
    minimum_dwell_bars: int = 3,
    confirmation_bars: int = 2,
):
    return persist_regime_authority(
        root,
        account_scope="PAPER",
        account_id=account_id,
        sleeve_id="caerus_orion",
        authorization_run_id=run_id,
        trade_date="2026-08-12",
        recorded_at=f"2026-08-12T13:35:{int(run_id[-1]) if run_id[-1].isdigit() else 0:02d}Z",
        observed_state=observed,
        confidence=confidence,
        acute_risk=acute,
        risk_package_id=f"risk:{run_id}",
        risk_package_hash=(run_id.encode().hex() + "0" * 64)[:64],
        market_state_id=f"market:{run_id}",
        minimum_dwell_bars=minimum_dwell_bars,
        confirmation_bars=confirmation_bars,
    )


def test_normal_regime_transition_requires_dwell_confirmation_and_confidence():
    pending = decide_regime_authority(
        previous_state="LOW", observed_state="ELEVATED", confidence=0.9,
        bars_in_state=4, consecutive_observations=2,
    )
    assert pending.action == "PERSIST"
    assert pending.effective_state == "LOW"
    confirmed = decide_regime_authority(
        previous_state="LOW", observed_state="ELEVATED", confidence=0.9,
        bars_in_state=5, consecutive_observations=2,
    )
    assert confirmed.action == "CONFIRMED_TRANSITION"
    assert confirmed.effective_state == "ELEVATED"


def test_low_confidence_transition_persists_prior_allocation():
    result = decide_regime_authority(
        previous_state="NORMAL", observed_state="ELEVATED", confidence=0.2,
        bars_in_state=20, consecutive_observations=5,
    )
    assert result.action == "PERSIST"
    assert result.reason_code == "regime_confidence_below_threshold"


def test_acute_risk_bypasses_hysteresis_and_vetoes_buys():
    result = decide_regime_authority(
        previous_state="LOW", observed_state="CRISIS", confidence=1.0,
        bars_in_state=0, consecutive_observations=1, acute_risk=True,
    )
    assert result.action == "EMERGENCY_RISK_RESPONSE"
    assert result.effective_state == "EMERGENCY_RISK_OFF"
    assert result.risk_veto_buys is True


def test_invalid_regime_confidence_fails_closed():
    with pytest.raises(ValueError):
        decide_regime_authority(
            previous_state="LOW", observed_state="HIGH", confidence=float("nan"),
            bars_in_state=5, consecutive_observations=2,
        )


def test_append_only_state_bootstraps_once_and_computes_hysteresis_across_runs(
    tmp_path: Path,
):
    bootstrap = _persist(tmp_path, "run-1")
    pending = _persist(tmp_path, "run-2", observed="ELEVATED")
    transitioned = _persist(tmp_path, "run-3", observed="ELEVATED")

    assert bootstrap.event.action == "BOOTSTRAP"
    assert bootstrap.event.previous_event_hash is None
    assert pending.event.action == "PERSIST"
    assert pending.event.consecutive_observations == 1
    assert transitioned.event.action == "CONFIRMED_TRANSITION"
    assert transitioned.event.effective_state == "ELEVATED"
    assert transitioned.event.consecutive_observations == 2
    assert transitioned.event.bars_in_effective_state == 1
    assert transitioned.event.previous_event_hash == pending.event.content_hash
    history = load_regime_history(
        tmp_path,
        account_scope="PAPER",
        account_id="paper-account",
        sleeve_id="caerus_orion",
    )
    assert [event.sequence for event in history] == [1, 2, 3]


def test_same_authorization_run_is_idempotent_but_conflicting_reuse_fails(
    tmp_path: Path,
):
    first = _persist(tmp_path, "run-1")
    repeated = persist_regime_authority(
        tmp_path,
        account_scope="PAPER",
        account_id="paper-account",
        sleeve_id="caerus_orion",
        authorization_run_id="run-1",
        trade_date="2026-08-12",
        recorded_at="2026-08-12T14:00:00Z",
        observed_state="LOW",
        confidence=0.9,
        acute_risk=False,
        risk_package_id="risk:run-1",
        risk_package_hash=("run-1".encode().hex() + "0" * 64)[:64],
        market_state_id="market:run-1",
        minimum_dwell_bars=3,
        confirmation_bars=2,
    )

    assert first.created is True
    assert repeated.created is False
    assert repeated.event.content_hash == first.event.content_hash
    assert len(list(first.event_path.parent.glob("*.json"))) == 1
    with pytest.raises(RegimeStateConflictError, match="conflicting payload"):
        _persist(tmp_path, "run-1", observed="ELEVATED")


def test_same_governed_observation_is_idempotent_across_different_run_ids(
    tmp_path: Path,
):
    first = _persist(tmp_path, "run-1")
    duplicate = persist_regime_authority(
        tmp_path,
        account_scope="PAPER",
        account_id="paper-account",
        sleeve_id="caerus_orion",
        authorization_run_id="retry-with-new-run-id",
        trade_date="2026-08-12",
        recorded_at="2026-08-12T14:00:00Z",
        observed_state="LOW",
        confidence=0.9,
        acute_risk=False,
        risk_package_id="risk:run-1",
        risk_package_hash=("run-1".encode().hex() + "0" * 64)[:64],
        market_state_id="market:run-1",
        minimum_dwell_bars=3,
        confirmation_bars=2,
    )

    assert duplicate.created is False
    assert duplicate.committed is True
    assert duplicate.event.content_hash == first.event.content_hash
    assert duplicate.event.authorization_run_id == "run-1"
    assert len(list(first.event_path.parent.glob("*.json"))) == 1


def test_same_source_bar_key_with_conflicting_payload_fails_closed(tmp_path: Path):
    first = _persist(tmp_path, "run-1")
    with pytest.raises(RegimeStateConflictError, match="conflicting payload"):
        persist_regime_authority(
            tmp_path,
            account_scope="PAPER",
            account_id="paper-account",
            sleeve_id="caerus_orion",
            authorization_run_id="new-retry-run",
            trade_date="2026-08-12",
            recorded_at="2026-08-12T14:00:00Z",
            observed_state="ELEVATED",
            confidence=0.7,
            acute_risk=False,
            risk_package_id="risk:run-1",
            risk_package_hash=("run-1".encode().hex() + "0" * 64)[:64],
            market_state_id="market:run-1",
            minimum_dwell_bars=3,
            confirmation_bars=2,
        )
    assert len(list(first.event_path.parent.glob("*.json"))) == 1


def test_stale_prepared_conflict_cannot_bypass_observation_identity(tmp_path: Path):
    committed = _persist(tmp_path / "committed", "run-1")
    conflicting = prepare_regime_authority(
        tmp_path / "separate-preparation",
        account_scope="PAPER",
        account_id="paper-account",
        sleeve_id="caerus_orion",
        authorization_run_id="conflicting-run",
        trade_date="2026-08-12",
        recorded_at="2026-08-12T14:00:00Z",
        observed_state="ELEVATED",
        confidence=0.7,
        acute_risk=False,
        risk_package_id="risk:run-1",
        risk_package_hash=("run-1".encode().hex() + "0" * 64)[:64],
        market_state_id="market:run-1",
        minimum_dwell_bars=3,
        confirmation_bars=2,
    )
    with pytest.raises(RegimeStateConflictError, match="conflicting payload"):
        commit_prepared_regime_authority(tmp_path / "committed", conflicting)
    assert len(list(committed.event_path.parent.glob("*.json"))) == 1


def test_prepared_observation_does_not_advance_until_explicit_commit(tmp_path: Path):
    prepared = prepare_regime_authority(
        tmp_path,
        account_scope="PAPER",
        account_id="paper-account",
        sleeve_id="caerus_orion",
        authorization_run_id="run-1",
        trade_date="2026-08-12",
        recorded_at="2026-08-12T13:35:01Z",
        observed_state="LOW",
        confidence=0.9,
        acute_risk=False,
        risk_package_id="risk:run-1",
        risk_package_hash=("run-1".encode().hex() + "0" * 64)[:64],
        market_state_id="market:run-1",
        minimum_dwell_bars=3,
        confirmation_bars=2,
    )
    assert prepared.committed is False
    assert prepared.event_path.exists() is False
    assert load_regime_history(
        tmp_path,
        account_scope="PAPER",
        account_id="paper-account",
        sleeve_id="caerus_orion",
    ) == []

    committed = commit_prepared_regime_authority(tmp_path, prepared)
    assert committed.created is True
    assert committed.committed is True
    assert committed.event_path.exists()


def test_corrupt_regime_history_fails_closed(tmp_path: Path):
    persisted = _persist(tmp_path, "run-1")
    payload = json.loads(persisted.event_path.read_text())
    payload["effective_state"] = "TAMPERED"
    persisted.event_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RegimeStateCorruptionError, match="content hash mismatch"):
        _persist(tmp_path, "run-2")


def test_emergency_veto_persists_until_hysteresis_confirms_exit(tmp_path: Path):
    _persist(tmp_path, "run-1", observed="LOW")
    emergency = _persist(tmp_path, "run-2", observed="CRISIS", acute=True)
    still_vetoed = _persist(tmp_path, "run-3", observed="LOW", acute=False)
    recovered = _persist(tmp_path, "run-4", observed="LOW", acute=False)

    assert emergency.event.effective_state == "EMERGENCY_RISK_OFF"
    assert emergency.event.risk_veto_buys is True
    assert still_vetoed.event.action == "PERSIST"
    assert still_vetoed.event.risk_veto_buys is True
    assert recovered.event.action == "CONFIRMED_TRANSITION"
    assert recovered.event.effective_state == "LOW"
    assert recovered.event.risk_veto_buys is False


def test_regime_state_isolated_by_paper_account(tmp_path: Path):
    first = _persist(tmp_path, "run-1", account_id="paper-account-a")
    second = _persist(tmp_path, "run-2", account_id="paper-account-b")

    assert first.event.sequence == second.event.sequence == 1
    assert first.event_path.parent != second.event_path.parent


def test_append_fsyncs_event_and_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import core.regime_state_store as store

    calls: list[int] = []
    real_fsync = store.os.fsync

    def tracking_fsync(fd: int) -> None:
        calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(store.os, "fsync", tracking_fsync)
    _persist(tmp_path, "run-1")
    assert len(calls) >= 2

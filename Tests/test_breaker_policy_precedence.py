from __future__ import annotations

from engine.breaker import get_exposure_multiplier


def test_full_policy_ignores_state_when_override_disabled(monkeypatch):
    monkeypatch.setenv("BREAKER_STATE_CAN_OVERRIDE", "0")
    mult = get_exposure_multiplier(
        "FULL",
        {"mode": "partial", "partial_exposure": 0.5, "exposure_multiplier": 0.5},
    )
    assert mult == 1.0


def test_full_policy_allows_state_override_when_enabled(monkeypatch):
    monkeypatch.setenv("BREAKER_STATE_CAN_OVERRIDE", "1")
    mult = get_exposure_multiplier(
        "FULL",
        {"mode": "partial", "partial_exposure": 0.5, "exposure_multiplier": 0.5},
    )
    assert mult == 0.5


def test_partial_policy_default_multiplier(monkeypatch):
    monkeypatch.setenv("BREAKER_STATE_CAN_OVERRIDE", "0")
    mult = get_exposure_multiplier("PARTIAL", {"mode": "lock"})
    assert mult == 0.5


def test_lock_policy_default_multiplier(monkeypatch):
    monkeypatch.setenv("BREAKER_STATE_CAN_OVERRIDE", "0")
    mult = get_exposure_multiplier("LOCK", {"mode": "off", "exposure_multiplier": 1.0})
    assert mult == 0.0

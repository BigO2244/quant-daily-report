from __future__ import annotations

from engine.breaker import get_breaker_config, get_exposure_multiplier


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


def test_no_trigger_defaults_to_off_full_exposure(monkeypatch):
    """No BREAKER_MODE env var → mode='off', exposure=1.0."""
    monkeypatch.delenv("BREAKER_MODE", raising=False)
    cfg = get_breaker_config()
    assert cfg["mode"] == "off"
    assert cfg["exposure_multiplier"] == 1.0
    assert cfg["source"] == "default"


def test_explicit_partial_override_kept(monkeypatch):
    """BREAKER_MODE=partial → mode='partial', exposure=0.5."""
    monkeypatch.setenv("BREAKER_MODE", "partial")
    monkeypatch.setenv("BREAKER_PARTIAL_EXPOSURE", "0.5")
    cfg = get_breaker_config()
    assert cfg["mode"] == "partial"
    assert cfg["exposure_multiplier"] == 0.5
    assert cfg["source"] == "env"


def test_explicit_lock_override_kept(monkeypatch):
    """BREAKER_MODE=lock → mode='lock', exposure=0.0."""
    monkeypatch.setenv("BREAKER_MODE", "lock")
    cfg = get_breaker_config()
    assert cfg["mode"] == "lock"
    assert cfg["exposure_multiplier"] == 0.0
    assert cfg["source"] == "env"


def test_invalid_breaker_mode_falls_back_to_off(monkeypatch):
    """Invalid BREAKER_MODE value → fallback to off/full exposure."""
    monkeypatch.setenv("BREAKER_MODE", "halted")
    cfg = get_breaker_config()
    assert cfg["mode"] == "off"
    assert cfg["exposure_multiplier"] == 1.0
    assert cfg["source"] == "default"
    assert "invalid" in cfg["reason"].lower()


def test_breaker_config_has_source_and_reason(monkeypatch):
    """get_breaker_config always returns source and reason fields."""
    monkeypatch.delenv("BREAKER_MODE", raising=False)
    cfg = get_breaker_config()
    assert "source" in cfg
    assert "reason" in cfg
    assert isinstance(cfg["source"], str)
    assert isinstance(cfg["reason"], str)

"""
Tests — Alpha Stack Config Loading
====================================
Verify config loads, feature flags default to false, and sections are present.
"""

import pytest
import sys
import os

# Ensure the repo root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


def test_config_loads():
    """Config file loads without errors."""
    from alpha_stack._config_loader import load_alpha_stack_config
    cfg = load_alpha_stack_config(reload=True)
    assert isinstance(cfg, dict), "Config should return a dict"
    assert len(cfg) > 0, "Config should not be empty"


def test_unvalidated_flags_remain_off():
    """Flags for sleeves/overlays not yet validated must remain off (safety gate)."""
    from alpha_stack._config_loader import load_alpha_stack_config
    cfg = load_alpha_stack_config(reload=True)
    flags = cfg.get("feature_flags", {})

    # These flags control unvalidated or unsupported features.
    # They must stay False until explicitly promoted after shadow validation.
    gated_flags = [
        "ENABLE_MEAN_REVERSION",   # shadow validation not yet complete
        "ENABLE_VALUE_SLEEVE",     # requires PIT-safe fundamentals
    ]
    for flag in gated_flags:
        assert flag in flags, f"Safety-gated flag {flag} must be present in config"
        assert flags[flag] is False, (
            f"Flag {flag} must remain False until explicitly promoted after shadow validation"
        )

    # Core flags are intentionally enabled for shadow paper trading.
    active_flags = [
        "ENABLE_ALPHA_STACK",
        "ENABLE_ALPHA_STACK_SHADOW",
        "ENABLE_QUALITY_SLEEVE",
        "ENABLE_OPTIONS_OVERLAY",
    ]
    for flag in active_flags:
        assert flag in flags, f"Active flag {flag} must be present in config"


def test_config_has_required_sections():
    """Config must have all required top-level sections."""
    from alpha_stack._config_loader import load_alpha_stack_config
    cfg = load_alpha_stack_config(reload=True)
    required = ["feature_flags", "universe", "datastore", "regime", "sleeves", "allocator", "research"]
    for section in required:
        assert section in cfg, f"Config must have section: {section}"


def test_allocator_base_weights_sum_to_one():
    """All base weight vectors in allocator config should sum to ~1.0."""
    from alpha_stack._config_loader import get_section
    alloc_cfg = get_section("allocator") or {}
    base_weights = alloc_cfg.get("base_weights", {})
    for trend_state, weights in base_weights.items():
        total = sum(weights.values())
        assert abs(total - 1.0) < 0.02, (
            f"Base weights for {trend_state} sum to {total:.3f}, expected ~1.0"
        )


def test_get_flag_returns_default_when_missing():
    """get_flag() must return provided default for unknown flags."""
    from alpha_stack._config_loader import get_flag
    assert get_flag("NONEXISTENT_FLAG_XYZ", default=False) is False
    assert get_flag("NONEXISTENT_FLAG_XYZ", default=True) is True

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


def test_all_feature_flags_default_false():
    """All feature flags must default to False for production safety."""
    from alpha_stack._config_loader import load_alpha_stack_config, get_flag
    cfg = load_alpha_stack_config(reload=True)
    flags = cfg.get("feature_flags", {})

    critical_flags = [
        "ENABLE_ALPHA_STACK",
        "ENABLE_ALPHA_STACK_SHADOW",
        "ENABLE_MEAN_REVERSION",
        "ENABLE_VALUE_SLEEVE",
        "ENABLE_QUALITY_SLEEVE",
    ]
    for flag in critical_flags:
        assert flag in flags, f"Flag {flag} must be present in config"
        assert flags[flag] is False, f"Flag {flag} MUST default to False (production safety)"


def test_is_enabled_returns_false_by_default():
    """alpha_stack.is_enabled() must return False with default config."""
    from alpha_stack import is_enabled
    assert is_enabled() is False


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

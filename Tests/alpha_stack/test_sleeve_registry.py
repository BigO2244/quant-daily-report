"""
Tests — Sleeve Registry & Disabled Sleeves
=============================================
Verify the registry loads all sleeves, active_sleeves() respects feature flags,
and disabled sleeves return SleeveOutput(active=False).
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


class TestSleeveRegistry:
    """SleeveRegistry correctness tests."""

    def test_all_sleeves_registered(self):
        """All four v1 sleeves must be registered."""
        from alpha_stack.sleeves.registry import SleeveRegistry, _SLEEVE_REGISTRY
        expected = {"trend", "value", "quality", "mean_reversion"}
        assert expected == set(_SLEEVE_REGISTRY.keys())

    def test_get_returns_sleeve_instance(self):
        """get() must return a SleeveBase subclass instance."""
        from alpha_stack.sleeves.registry import SleeveRegistry
        from alpha_stack.sleeves.base import SleeveBase
        registry = SleeveRegistry()
        trend = registry.get("trend")
        assert trend is not None
        assert isinstance(trend, SleeveBase)

    def test_get_returns_none_for_unknown(self):
        """get() must return None for unregistered names."""
        from alpha_stack.sleeves.registry import SleeveRegistry
        registry = SleeveRegistry()
        assert registry.get("nonexistent_sleeve") is None

    def test_sleeve_names(self):
        """sleeve_names() returns list of registered names."""
        from alpha_stack.sleeves.registry import SleeveRegistry
        registry = SleeveRegistry()
        names = registry.sleeve_names()
        assert "trend" in names
        assert "value" in names
        assert "quality" in names
        assert "mean_reversion" in names

    def test_active_sleeves_with_flags_off(self):
        """With all flags off, only trend sleeve should be active."""
        from alpha_stack.sleeves.registry import SleeveRegistry
        # All flags are false by default in config
        registry = SleeveRegistry()
        active_names = registry.active_sleeve_names()
        # trend has no blocking flag (only master ENABLE_ALPHA_STACK)
        assert "trend" in active_names
        # value, quality, mean_reversion should be excluded
        assert "value" not in active_names
        assert "quality" not in active_names
        assert "mean_reversion" not in active_names

    def test_trend_sleeve_name(self):
        """TrendSleeve.name must return 'trend'."""
        from alpha_stack.sleeves.trend import TrendSleeve
        sleeve = TrendSleeve()
        assert sleeve.name == "trend"

    def test_value_sleeve_run_returns_disabled(self):
        """ValueSleeve.run() returns active=False when flag is off."""
        import pandas as pd
        from alpha_stack.sleeves.value import ValueSleeve
        sleeve = ValueSleeve()
        ctx = _make_ctx()
        out = sleeve.run(pd.DataFrame(), ctx, as_of_date="2024-01-15")
        assert out.active is False
        assert "ENABLE_VALUE_SLEEVE" in out.reason

    def test_quality_sleeve_run_returns_disabled(self):
        """QualitySleeve.run() returns active=False when flag is off."""
        import pandas as pd
        from alpha_stack.sleeves.quality import QualitySleeve
        sleeve = QualitySleeve()
        ctx = _make_ctx()
        out = sleeve.run(pd.DataFrame(), ctx, as_of_date="2024-01-15")
        assert out.active is False

    def test_mean_reversion_run_returns_disabled(self):
        """MeanReversionSleeve.run() returns active=False when flag is off."""
        import pandas as pd
        from alpha_stack.sleeves.mean_reversion import MeanReversionSleeve
        sleeve = MeanReversionSleeve()
        ctx = _make_ctx()
        out = sleeve.run(pd.DataFrame(), ctx, as_of_date="2024-01-15")
        assert out.active is False

    def test_required_features_non_empty(self):
        """All sleeves must declare non-empty required_features()."""
        from alpha_stack.sleeves.registry import SleeveRegistry
        registry = SleeveRegistry()
        for name in registry.sleeve_names():
            sleeve = registry.get(name)
            feats = sleeve.required_features()
            assert isinstance(feats, list) and len(feats) > 0, \
                f"Sleeve {name} must declare required_features"


def _make_ctx():
    """Helper: make a neutral RegimeContext."""
    from alpha_stack.regime.context import RegimeContext
    from alpha_stack.regime.state_machine import TrendState, VolatilityState, BreadthState, MacroState
    return RegimeContext(
        trend_state=TrendState.NEUTRAL,
        vol_state=VolatilityState.NORMAL,
        breadth_state=BreadthState.MIXED,
        macro_state=MacroState.NEUTRAL,
        as_of_date="2024-01-15",
    )

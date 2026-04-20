"""
Tests — Regime State Machine & Hysteresis
============================================
Verify state transitions, hysteresis, and RegimeContext construction.
All tests use synthetic inputs — no network calls.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


# ------------------------------------------------------------------ #
# State machine classifiers                                            #
# ------------------------------------------------------------------ #

class TestTrendClassifier:
    """classify_trend() produces correct states for known inputs."""

    def test_strong_up(self):
        from alpha_stack.regime.state_machine import classify_trend, TrendState
        assert classify_trend(0.05, 0.02) == TrendState.STRONG_UP

    def test_weak_up(self):
        from alpha_stack.regime.state_machine import classify_trend, TrendState
        assert classify_trend(0.01, 0.005) == TrendState.WEAK_UP

    def test_neutral(self):
        from alpha_stack.regime.state_machine import classify_trend, TrendState
        assert classify_trend(-0.01, 0.0) == TrendState.NEUTRAL

    def test_weak_down(self):
        from alpha_stack.regime.state_machine import classify_trend, TrendState
        assert classify_trend(-0.03, -0.005) == TrendState.WEAK_DOWN

    def test_strong_down(self):
        from alpha_stack.regime.state_machine import classify_trend, TrendState
        assert classify_trend(-0.08, -0.03) == TrendState.STRONG_DOWN

    def test_none_inputs_default_to_neutral(self):
        from alpha_stack.regime.state_machine import classify_trend, TrendState
        assert classify_trend(None, None) == TrendState.NEUTRAL


class TestVolatilityClassifier:
    """classify_volatility() produces correct states."""

    def test_calm(self):
        from alpha_stack.regime.state_machine import classify_volatility, VolatilityState
        assert classify_volatility(12.0) == VolatilityState.CALM

    def test_normal(self):
        from alpha_stack.regime.state_machine import classify_volatility, VolatilityState
        assert classify_volatility(18.0) == VolatilityState.NORMAL

    def test_elevated(self):
        from alpha_stack.regime.state_machine import classify_volatility, VolatilityState
        assert classify_volatility(25.0) == VolatilityState.ELEVATED

    def test_crisis(self):
        from alpha_stack.regime.state_machine import classify_volatility, VolatilityState
        assert classify_volatility(35.0) == VolatilityState.CRISIS

    def test_none_defaults_to_normal(self):
        from alpha_stack.regime.state_machine import classify_volatility, VolatilityState
        assert classify_volatility(None) == VolatilityState.NORMAL

    def test_boundary_calm_normal_at_16(self):
        from alpha_stack.regime.state_machine import classify_volatility, VolatilityState
        assert classify_volatility(15.99) == VolatilityState.CALM
        assert classify_volatility(16.0) == VolatilityState.NORMAL

    def test_boundary_normal_elevated_at_22(self):
        from alpha_stack.regime.state_machine import classify_volatility, VolatilityState
        assert classify_volatility(21.99) == VolatilityState.NORMAL
        assert classify_volatility(22.0) == VolatilityState.ELEVATED

    def test_boundary_elevated_crisis_at_30(self):
        from alpha_stack.regime.state_machine import classify_volatility, VolatilityState
        assert classify_volatility(29.99) == VolatilityState.ELEVATED
        assert classify_volatility(30.0) == VolatilityState.CRISIS


class TestBreadthClassifier:
    """classify_breadth() produces correct states."""

    def test_healthy(self):
        from alpha_stack.regime.state_machine import classify_breadth, BreadthState
        assert classify_breadth(75.0) == BreadthState.HEALTHY

    def test_mixed(self):
        from alpha_stack.regime.state_machine import classify_breadth, BreadthState
        assert classify_breadth(55.0) == BreadthState.MIXED

    def test_deteriorating(self):
        from alpha_stack.regime.state_machine import classify_breadth, BreadthState
        assert classify_breadth(38.0) == BreadthState.DETERIORATING

    def test_washed_out(self):
        from alpha_stack.regime.state_machine import classify_breadth, BreadthState
        assert classify_breadth(20.0) == BreadthState.WASHED_OUT

    def test_none_defaults_to_mixed(self):
        from alpha_stack.regime.state_machine import classify_breadth, BreadthState
        assert classify_breadth(None) == BreadthState.MIXED

    def test_boundary_deteriorating_washed_out_at_30(self):
        from alpha_stack.regime.state_machine import classify_breadth, BreadthState
        assert classify_breadth(30.1) == BreadthState.DETERIORATING
        assert classify_breadth(30.0) == BreadthState.DETERIORATING  # inclusive: >= 30 → DETERIORATING
        assert classify_breadth(29.99) == BreadthState.WASHED_OUT

    def test_boundary_mixed_deteriorating_at_45(self):
        from alpha_stack.regime.state_machine import classify_breadth, BreadthState
        assert classify_breadth(44.99) == BreadthState.DETERIORATING
        assert classify_breadth(45.0) == BreadthState.MIXED

    def test_boundary_healthy_mixed_at_65(self):
        from alpha_stack.regime.state_machine import classify_breadth, BreadthState
        assert classify_breadth(64.99) == BreadthState.MIXED
        assert classify_breadth(65.0) == BreadthState.HEALTHY


# ------------------------------------------------------------------ #
# Hysteresis controller                                                #
# ------------------------------------------------------------------ #

class TestHysteresisController:
    """HysteresisController enforces dwell time and confirmation logic."""

    def _make_controller(self, initial, min_dwell=5, confirm=2):
        from alpha_stack.regime.state_machine import TrendState
        from alpha_stack.regime.hysteresis import HysteresisController
        return HysteresisController(
            initial_state=initial,
            min_dwell_days=min_dwell,
            confirmation_bars=confirm,
            max_state_jump=1,
            dimension_name="trend_test",
        )

    def test_no_change_within_dwell_period(self):
        """State must not change if dwell time not met."""
        from alpha_stack.regime.state_machine import TrendState
        ctrl = self._make_controller(TrendState.WEAK_UP, min_dwell=5, confirm=2)
        # Send STRONG_UP signal — but dwell time not met yet
        for _ in range(4):
            changed = ctrl.update(TrendState.STRONG_UP)
            assert ctrl.confirmed_state == TrendState.WEAK_UP
            assert not changed

    def test_transition_after_dwell_and_confirmation(self):
        """State changes after dwell + confirmation windows."""
        from alpha_stack.regime.state_machine import TrendState
        ctrl = self._make_controller(TrendState.NEUTRAL, min_dwell=5, confirm=2)
        # Send NEUTRAL for 5 bars (dwell satisfied)
        for _ in range(5):
            ctrl.update(TrendState.NEUTRAL)
        assert ctrl.confirmed_state == TrendState.NEUTRAL

        # Now send WEAK_UP signal for 2 bars (confirmation)
        ctrl.update(TrendState.WEAK_UP)  # bar 1 of confirmation
        assert ctrl.confirmed_state == TrendState.NEUTRAL  # not yet
        changed = ctrl.update(TrendState.WEAK_UP)           # bar 2 → should confirm
        assert changed, "Should have confirmed transition"
        assert ctrl.confirmed_state == TrendState.WEAK_UP

    def test_confirmation_reset_on_signal_change(self):
        """Pending confirmation resets if confirmed-state signal is seen mid-confirmation."""
        from alpha_stack.regime.state_machine import TrendState
        ctrl = self._make_controller(TrendState.NEUTRAL, min_dwell=5, confirm=3)
        for _ in range(5):
            ctrl.update(TrendState.NEUTRAL)

        ctrl.update(TrendState.WEAK_UP)   # start confirmation for WEAK_UP, count=1
        ctrl.update(TrendState.NEUTRAL)   # back to confirmed state — resets pending
        ctrl.update(TrendState.WEAK_UP)   # restart WEAK_UP confirmation, count=1
        # Still at NEUTRAL (only 1 bar of WEAK_UP since last reset; need 3)
        assert ctrl.confirmed_state == TrendState.NEUTRAL

    def test_max_state_jump_enforced(self):
        """Max state jump of 1 must be enforced."""
        from alpha_stack.regime.state_machine import TrendState
        ctrl = self._make_controller(TrendState.STRONG_UP, min_dwell=5, confirm=2)
        # Try to jump from STRONG_UP (4) directly to STRONG_DOWN (0) — dist=4
        for _ in range(5):
            ctrl.update(TrendState.STRONG_UP)
        # Should be clipped to adjacent state
        for _ in range(2):
            ctrl.update(TrendState.STRONG_DOWN)  # jump of 4 — should be clipped
        # Confirmed state should be WEAK_UP (adjacent to STRONG_UP going down)
        assert ctrl.confirmed_state.numeric() == TrendState.STRONG_UP.numeric() - 1

    def test_crisis_bypass_volatility(self):
        """Crisis vol must bypass dwell time."""
        from alpha_stack.regime.state_machine import VolatilityState
        from alpha_stack.regime.hysteresis import HysteresisController
        ctrl = HysteresisController(
            VolatilityState.NORMAL, min_dwell_days=5, confirmation_bars=2,
            max_state_jump=1, dimension_name="volatility",
        )
        # Immediately send CRISIS with is_crisis=True — should bypass dwell
        changed = ctrl.update(VolatilityState.CRISIS, is_crisis=True)
        assert changed, "Crisis bypass should force immediate transition"
        assert ctrl.confirmed_state == VolatilityState.CRISIS

    def test_transition_history_records_changes(self):
        """Transition history must record confirmed state changes."""
        from alpha_stack.regime.state_machine import TrendState
        ctrl = self._make_controller(TrendState.NEUTRAL, min_dwell=1, confirm=1)
        for _ in range(2):
            ctrl.update(TrendState.WEAK_UP)
        history = ctrl.transition_history()
        assert len(history) >= 1
        assert history[0]["from"] is not None
        assert history[0]["to"] is not None


# ------------------------------------------------------------------ #
# RegimeContext                                                         #
# ------------------------------------------------------------------ #

class TestRegimeContext:
    """RegimeContext properties and serialisation."""

    def _make_ctx(self, trend="neutral", vol="normal", breadth="mixed", macro="neutral"):
        from alpha_stack.regime.context import RegimeContext
        from alpha_stack.regime.state_machine import TrendState, VolatilityState, BreadthState, MacroState
        return RegimeContext(
            trend_state=TrendState(trend),
            vol_state=VolatilityState(vol),
            breadth_state=BreadthState(breadth),
            macro_state=MacroState(macro),
            as_of_date="2024-01-15",
        )

    def test_to_dict_has_all_fields(self):
        ctx = self._make_ctx()
        d = ctx.to_dict()
        for key in ["trend_state", "vol_state", "breadth_state", "macro_state", "as_of_date"]:
            assert key in d

    def test_is_risk_off_crisis(self):
        ctx = self._make_ctx(vol="crisis")
        assert ctx.is_risk_off is True

    def test_is_risk_off_false_normal(self):
        ctx = self._make_ctx(trend="strong_up", vol="calm", breadth="healthy")
        assert ctx.is_risk_off is False

    def test_allows_mean_reversion_gate(self):
        ctx = self._make_ctx(trend="neutral", vol="calm")
        assert ctx.allows_mean_reversion is True

        ctx2 = self._make_ctx(trend="strong_down", vol="crisis")
        assert ctx2.allows_mean_reversion is False

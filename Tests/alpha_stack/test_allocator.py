"""
Tests — AlphaStackAllocator
============================
Verify the regime-aware allocator produces correct outputs with synthetic data.
No network calls — all data is synthetic.
"""

import pytest
import numpy as np
import pandas as pd
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


def _make_ctx(trend="neutral", vol="normal", breadth="mixed", macro="neutral"):
    from alpha_stack.regime.context import RegimeContext
    from alpha_stack.regime.state_machine import TrendState, VolatilityState, BreadthState, MacroState
    return RegimeContext(
        trend_state=TrendState(trend),
        vol_state=VolatilityState(vol),
        breadth_state=BreadthState(breadth),
        macro_state=MacroState(macro),
        as_of_date="2024-06-01",
    )


def _make_sleeve_output(name, n_candidates=5, active=True):
    """Create a synthetic SleeveOutput."""
    from alpha_stack.sleeves.base import SleeveOutput, HoldState
    if not active or n_candidates == 0:
        return SleeveOutput(
            sleeve_name=name,
            as_of_date="2024-06-01",
            active=active,
            candidates=pd.DataFrame(),
            scores=pd.DataFrame(),
            reason="disabled" if not active else "no_candidates",
        )
    tickers = [f"{name.upper()[:3]}{i:02d}" for i in range(n_candidates)]
    candidates = pd.DataFrame({
        "ticker": tickers,
        "score": np.linspace(80, 60, n_candidates),
        "provisional_weight": [1.0 / n_candidates] * n_candidates,
        "hold_state": [HoldState.ENTER.value] * n_candidates,
        "sector": ["Technology"] * n_candidates,
    })
    return SleeveOutput(
        sleeve_name=name,
        as_of_date="2024-06-01",
        active=True,
        candidates=candidates,
        scores=candidates[["ticker", "score"]],
        reason="scored",
    )


class TestAllocatorBaseWeights:
    def test_strong_up_favors_trend(self):
        """In strong_up, trend budget should be largest."""
        from alpha_stack.portfolio.allocator import AlphaStackAllocator
        alloc = AlphaStackAllocator()
        budgets = alloc._base_budgets(__import__('alpha_stack.regime.state_machine', fromlist=['TrendState']).TrendState.STRONG_UP)
        assert budgets["trend"] >= budgets["quality"], "trend < quality in strong_up"
        assert budgets["trend"] >= budgets["value"], "trend < value in strong_up"

    def test_strong_down_favors_quality(self):
        """In strong_down, quality budget should be largest."""
        from alpha_stack.portfolio.allocator import AlphaStackAllocator
        from alpha_stack.regime.state_machine import TrendState
        alloc = AlphaStackAllocator()
        budgets = alloc._base_budgets(TrendState.STRONG_DOWN)
        assert budgets["quality"] >= budgets["trend"], "quality < trend in strong_down"

    def test_base_budgets_sum_to_one(self):
        """Base budgets (ex-cash) should sum to approximately 1.0."""
        from alpha_stack.portfolio.allocator import AlphaStackAllocator
        from alpha_stack.regime.state_machine import TrendState
        alloc = AlphaStackAllocator()
        for state in TrendState:
            budgets = alloc._base_budgets(state)
            total = sum(v for k, v in budgets.items() if k != "cash")
            assert abs(total - 1.0) < 0.01, \
                f"Base budgets for {state.value} sum to {total:.4f}, not 1.0"

    def test_all_base_weights_non_negative(self):
        """No sleeve should have a negative base budget."""
        from alpha_stack.portfolio.allocator import AlphaStackAllocator
        from alpha_stack.regime.state_machine import TrendState
        alloc = AlphaStackAllocator()
        for state in TrendState:
            budgets = alloc._base_budgets(state)
            for k, v in budgets.items():
                assert v >= 0, f"Negative budget {v} for {k} in {state.value}"


class TestAllocatorVolModifiers:
    def test_crisis_vol_reduces_trend_budget(self):
        """CRISIS vol should cut trend budget vs NORMAL."""
        from alpha_stack.portfolio.allocator import AlphaStackAllocator
        from alpha_stack.regime.state_machine import TrendState, VolatilityState
        alloc = AlphaStackAllocator()
        base = alloc._base_budgets(TrendState.NEUTRAL)
        normal_budgets = alloc._apply_vol_modifiers(dict(base), VolatilityState.NORMAL, [])
        crisis_budgets = alloc._apply_vol_modifiers(dict(base), VolatilityState.CRISIS, [])
        assert crisis_budgets.get("trend", 0) < normal_budgets.get("trend", 0), \
            "Crisis did not reduce trend budget"

    def test_crisis_vol_zeroes_mean_reversion(self):
        """CRISIS vol should eliminate mean_reversion budget."""
        from alpha_stack.portfolio.allocator import AlphaStackAllocator
        from alpha_stack.regime.state_machine import TrendState, VolatilityState
        alloc = AlphaStackAllocator()
        base = alloc._base_budgets(TrendState.NEUTRAL)
        crisis_budgets = alloc._apply_vol_modifiers(dict(base), VolatilityState.CRISIS, [])
        assert crisis_budgets.get("mean_reversion", 0) == 0.0, \
            "Mean reversion not zeroed in crisis"

    def test_elevated_vol_reduces_mean_reversion(self):
        """ELEVATED vol should reduce (but not zero) mean_reversion."""
        from alpha_stack.portfolio.allocator import AlphaStackAllocator
        from alpha_stack.regime.state_machine import TrendState, VolatilityState
        alloc = AlphaStackAllocator()
        base = alloc._base_budgets(TrendState.NEUTRAL)
        original_mr = base.get("mean_reversion", 0)
        if original_mr == 0:
            pytest.skip("No mean_reversion budget in neutral base weights")
        elevated_budgets = alloc._apply_vol_modifiers(dict(base), VolatilityState.ELEVATED, [])
        assert elevated_budgets.get("mean_reversion", 0) < original_mr, \
            "Elevated vol did not reduce mean_reversion"
        assert elevated_budgets.get("mean_reversion", 0) > 0, \
            "Elevated vol should not zero mean_reversion"


class TestAllocatorBreadthModifiers:
    def test_healthy_breadth_increases_trend(self):
        """HEALTHY breadth should increase trend budget."""
        from alpha_stack.portfolio.allocator import AlphaStackAllocator
        from alpha_stack.regime.state_machine import TrendState, BreadthState
        alloc = AlphaStackAllocator()
        base = alloc._base_budgets(TrendState.NEUTRAL)
        mixed = alloc._apply_breadth_modifiers(dict(base), BreadthState.MIXED, [])
        healthy = alloc._apply_breadth_modifiers(dict(base), BreadthState.HEALTHY, [])
        assert healthy.get("trend", 0) >= mixed.get("trend", 0), \
            "Healthy breadth did not increase trend"

    def test_deteriorating_breadth_increases_quality(self):
        """DETERIORATING breadth should increase quality budget."""
        from alpha_stack.portfolio.allocator import AlphaStackAllocator
        from alpha_stack.regime.state_machine import TrendState, BreadthState
        alloc = AlphaStackAllocator()
        base = alloc._base_budgets(TrendState.NEUTRAL)
        mixed = alloc._apply_breadth_modifiers(dict(base), BreadthState.MIXED, [])
        detr = alloc._apply_breadth_modifiers(dict(base), BreadthState.DETERIORATING, [])
        assert detr.get("quality", 0) >= mixed.get("quality", 0), \
            "Deteriorating breadth did not increase quality"


class TestAllocatorDrawdownBreaker:
    def test_hard_drawdown_moves_to_cash(self):
        """Hard drawdown should move all budget to cash."""
        from alpha_stack.portfolio.allocator import AlphaStackAllocator
        alloc = AlphaStackAllocator()
        budgets = {"trend": 0.35, "value": 0.25, "quality": 0.30, "mean_reversion": 0.10}
        hard_dd = alloc._constraints.drawdown_hard
        result, note = alloc._apply_drawdown_breaker(budgets, hard_dd + 0.01)
        assert result == {"cash": 1.0}, f"Hard breaker did not move all to cash: {result}"
        assert "HARD" in note.upper()

    def test_soft_drawdown_reduces_sleeves(self):
        """Soft drawdown should reduce all sleeves by ~50%."""
        from alpha_stack.portfolio.allocator import AlphaStackAllocator
        alloc = AlphaStackAllocator()
        budgets = {"trend": 0.35, "value": 0.25, "quality": 0.30}
        soft_dd = alloc._constraints.drawdown_soft
        original_trend = budgets["trend"]
        result, note = alloc._apply_drawdown_breaker(budgets, soft_dd + 0.01)
        assert result.get("trend", 0) < original_trend, "Soft breaker did not reduce trend"
        assert result.get("cash", 0) > 0, "Soft breaker did not add cash"

    def test_no_drawdown_no_change(self):
        """No drawdown should return budgets unchanged."""
        from alpha_stack.portfolio.allocator import AlphaStackAllocator
        alloc = AlphaStackAllocator()
        budgets = {"trend": 0.35, "value": 0.25, "quality": 0.30}
        result, note = alloc._apply_drawdown_breaker(budgets, 0.0)
        assert result == budgets
        assert note == ""


class TestAllocatorFullAllocation:
    def test_allocate_returns_allocation_result(self):
        """allocate() must return an AllocationResult."""
        from alpha_stack.portfolio.allocator import AlphaStackAllocator, AllocationResult
        alloc = AlphaStackAllocator()
        sleeve_outputs = {"trend": _make_sleeve_output("trend", n_candidates=5)}
        ctx = _make_ctx()
        result = alloc.allocate(sleeve_outputs, ctx)
        assert isinstance(result, AllocationResult)

    def test_gross_exposure_bounded(self):
        """Gross exposure must not exceed max_gross_exposure."""
        from alpha_stack.portfolio.allocator import AlphaStackAllocator
        alloc = AlphaStackAllocator()
        sleeve_outputs = {"trend": _make_sleeve_output("trend", n_candidates=10)}
        ctx = _make_ctx()
        result = alloc.allocate(sleeve_outputs, ctx)
        max_exp = alloc._constraints.max_gross_exposure
        assert result.gross_exposure <= max_exp + 1e-9, \
            f"Gross exposure {result.gross_exposure:.4f} exceeds {max_exp}"

    def test_cash_weight_non_negative(self):
        """Cash weight must never be negative."""
        from alpha_stack.portfolio.allocator import AlphaStackAllocator
        alloc = AlphaStackAllocator()
        sleeve_outputs = {"trend": _make_sleeve_output("trend", n_candidates=10)}
        ctx = _make_ctx()
        result = alloc.allocate(sleeve_outputs, ctx)
        assert result.cash_weight >= 0.0, f"Negative cash weight: {result.cash_weight}"

    def test_inactive_sleeve_releases_to_cash(self):
        """An inactive sleeve's budget should NOT appear in target_book."""
        from alpha_stack.portfolio.allocator import AlphaStackAllocator
        alloc = AlphaStackAllocator()
        sleeve_outputs = {
            "trend": _make_sleeve_output("trend", active=False),
        }
        ctx = _make_ctx()
        result = alloc.allocate(sleeve_outputs, ctx)
        if not result.target_book.empty:
            assert "trend" not in result.target_book["sleeve"].values or \
                result.target_book[result.target_book["sleeve"] == "trend"]["weight"].sum() == 0

    def test_multi_sleeve_weights_bounded(self):
        """Multi-sleeve target book must have all weights >= 0."""
        from alpha_stack.portfolio.allocator import AlphaStackAllocator
        alloc = AlphaStackAllocator()
        sleeve_outputs = {
            "trend": _make_sleeve_output("trend", n_candidates=5),
            "quality": _make_sleeve_output("quality", n_candidates=5),
        }
        ctx = _make_ctx()
        result = alloc.allocate(sleeve_outputs, ctx)
        if not result.target_book.empty and "weight" in result.target_book.columns:
            assert (result.target_book["weight"] >= -1e-9).all(), \
                "Negative weights in target_book"

    def test_notes_populated(self):
        """Allocation result must contain notes about regime decisions."""
        from alpha_stack.portfolio.allocator import AlphaStackAllocator
        alloc = AlphaStackAllocator()
        sleeve_outputs = {"trend": _make_sleeve_output("trend", n_candidates=5)}
        ctx = _make_ctx()
        result = alloc.allocate(sleeve_outputs, ctx)
        assert isinstance(result.notes, list)
        assert len(result.notes) > 0, "Expected at least one note"

    def test_crisis_vol_reduces_gross_exposure(self):
        """CRISIS vol should reduce gross exposure vs NORMAL vol."""
        from alpha_stack.portfolio.allocator import AlphaStackAllocator
        alloc_normal = AlphaStackAllocator()
        alloc_crisis = AlphaStackAllocator()
        sleeve_outputs = {"trend": _make_sleeve_output("trend", n_candidates=10)}

        ctx_normal = _make_ctx(vol="normal")
        ctx_crisis = _make_ctx(vol="crisis")

        # Reset prev_budgets to avoid smoothing effects
        result_normal = alloc_normal.allocate(sleeve_outputs, ctx_normal)
        result_crisis = alloc_crisis.allocate(sleeve_outputs, ctx_crisis)

        assert result_crisis.gross_exposure <= result_normal.gross_exposure + 1e-6, \
            f"Crisis ({result_crisis.gross_exposure:.3f}) >= normal ({result_normal.gross_exposure:.3f})"


class TestNormaliseBudgets:
    def test_normalise_caps_at_max_exposure(self):
        """_normalise_budgets must not allow non-cash > max_exposure."""
        from alpha_stack.portfolio.allocator import _normalise_budgets
        budgets = {"trend": 0.60, "value": 0.40, "quality": 0.40}
        result = _normalise_budgets(budgets, max_exposure=0.95)
        non_cash = sum(v for k, v in result.items() if k != "cash")
        assert non_cash <= 0.95 + 1e-9, f"Non-cash {non_cash:.4f} > 0.95"

    def test_normalise_preserves_ratios(self):
        """After normalisation, sleeve ratios should be preserved."""
        from alpha_stack.portfolio.allocator import _normalise_budgets
        budgets = {"trend": 0.60, "value": 0.40}
        result = _normalise_budgets(budgets, max_exposure=0.50)
        ratio = result["trend"] / result["value"]
        assert abs(ratio - 1.5) < 0.01, f"Ratio {ratio:.3f} != 1.5"

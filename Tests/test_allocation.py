#!/usr/bin/env python3
"""
Acceptance Tests for Dynamic Portfolio Allocation
=================================================
Validates the allocation logic against the spec requirements:
1. Only trend active → trend allocation = 100%
2. Trend + valuation active, strength=1 → 50/50 split
3. No sleeves active → CASH = 100%
4. Portfolio weights (including CASH) sum to 1.00 daily
"""
import sys
sys.path.insert(0, '/home/claude')

from core.portfolio_alloc import (
    PortfolioAllocator,
    create_sleeve_output,
    validate_allocation_result,
    WEIGHT_TOLERANCE,
    CASH_TICKER,
)


def test_trend_only_100pct():
    """Test: Only trend active → trend allocation = 100%"""
    trend = create_sleeve_output(
        [{"ticker": "AAPL", "target_weight": 0.5}, {"ticker": "MSFT", "target_weight": 0.5}],
        "sleeve_trend", strength=1.0
    )
    val = create_sleeve_output([], "sleeve_2", strength=0.0)
    
    allocator = PortfolioAllocator()
    result = allocator.allocate([trend, val])
    
    trend_alloc = result.sleeve_allocations.get("sleeve_trend", 0)
    val_alloc = result.sleeve_allocations.get("sleeve_2", 0)
    
    assert abs(trend_alloc - 1.0) < WEIGHT_TOLERANCE, f"Trend should be 100%, got {trend_alloc:.1%}"
    assert abs(val_alloc - 0.0) < WEIGHT_TOLERANCE, f"Valuation should be 0%, got {val_alloc:.1%}"
    print("✓ PASS: Only trend active → 100% trend allocation")


def test_both_active_equal_strength():
    """Test: Trend + valuation active, strength=1 → 50/50 split"""
    trend = create_sleeve_output(
        [{"ticker": "AAPL", "target_weight": 1.0}],
        "sleeve_trend", strength=1.0
    )
    val = create_sleeve_output(
        [{"ticker": "IBM", "target_weight": 1.0}],
        "sleeve_2", strength=1.0
    )
    
    allocator = PortfolioAllocator()
    result = allocator.allocate([trend, val])
    
    trend_alloc = result.sleeve_allocations.get("sleeve_trend", 0)
    val_alloc = result.sleeve_allocations.get("sleeve_2", 0)
    
    assert abs(trend_alloc - 0.5) < WEIGHT_TOLERANCE, f"Trend should be 50%, got {trend_alloc:.1%}"
    assert abs(val_alloc - 0.5) < WEIGHT_TOLERANCE, f"Valuation should be 50%, got {val_alloc:.1%}"
    print("✓ PASS: Both active with equal strength → 50/50 split")


def test_no_sleeves_100_cash():
    """Test: No sleeves active → CASH = 100%"""
    trend = create_sleeve_output([], "sleeve_trend", strength=0.0)
    val = create_sleeve_output([], "sleeve_2", strength=0.0)
    
    allocator = PortfolioAllocator()
    result = allocator.allocate([trend, val])
    
    cash_weight = result.cash_weight
    
    assert abs(cash_weight - 1.0) < WEIGHT_TOLERANCE, f"CASH should be 100%, got {cash_weight:.1%}"
    print("✓ PASS: No sleeves active → 100% CASH")


def test_weights_sum_to_1():
    """Test: Portfolio weights (including CASH) sum to 1.00"""
    # Test various scenarios
    scenarios = [
        # (trend_positions, val_positions, trend_strength, val_strength)
        ([{"ticker": "AAPL", "target_weight": 1.0}], [], 1.0, 0.0),
        ([], [{"ticker": "IBM", "target_weight": 1.0}], 0.0, 1.0),
        ([{"ticker": "AAPL", "target_weight": 0.5}], [{"ticker": "IBM", "target_weight": 0.5}], 1.0, 1.0),
        ([{"ticker": "AAPL", "target_weight": 0.3}], [{"ticker": "IBM", "target_weight": 0.7}], 0.8, 0.2),
        ([], [], 0.0, 0.0),  # All cash
    ]
    
    allocator = PortfolioAllocator()
    
    for i, (trend_pos, val_pos, trend_str, val_str) in enumerate(scenarios):
        trend = create_sleeve_output(trend_pos, "sleeve_trend", strength=trend_str)
        val = create_sleeve_output(val_pos, "sleeve_2", strength=val_str)
        result = allocator.allocate([trend, val])
        
        total = result.total_weight
        assert abs(total - 1.0) < WEIGHT_TOLERANCE, f"Scenario {i+1}: weights should sum to 1.0, got {total:.6f}"
    
    print("✓ PASS: Portfolio weights sum to 1.00 in all scenarios")


def test_position_cap_no_renormalize():
    """Test: Position caps don't renormalize - excess goes to CASH"""
    # Create a single large position that exceeds max position size (10%)
    trend = create_sleeve_output(
        [{"ticker": "AAPL", "target_weight": 1.0}],  # Will try to be 100% of portfolio
        "sleeve_trend", strength=1.0
    )
    val = create_sleeve_output([], "sleeve_2", strength=0.0)
    
    allocator = PortfolioAllocator(max_position_pct=0.10)  # 10% cap
    result = allocator.allocate([trend, val])
    
    # AAPL should be capped at 10%
    aapl_weight = result.combined_weights[result.combined_weights["ticker"] == "AAPL"]["target_weight"].iloc[0]
    assert abs(aapl_weight - 0.10) < WEIGHT_TOLERANCE, f"AAPL should be capped at 10%, got {aapl_weight:.1%}"
    
    # Remaining 90% should be CASH
    cash_weight = result.cash_weight
    assert abs(cash_weight - 0.90) < WEIGHT_TOLERANCE, f"CASH should be 90%, got {cash_weight:.1%}"
    
    # Should have recorded the skip
    assert len(result.skipped_trades) > 0, "Should have recorded skipped trade for cap hit"
    
    print("✓ PASS: Position cap excess goes to CASH (no renormalization)")


def test_validation_function():
    """Test: validate_allocation_result catches errors"""
    trend = create_sleeve_output(
        [{"ticker": "AAPL", "target_weight": 0.5}, {"ticker": "MSFT", "target_weight": 0.5}],
        "sleeve_trend", strength=1.0
    )
    val = create_sleeve_output([], "sleeve_2", strength=0.0)
    
    allocator = PortfolioAllocator()
    result = allocator.allocate([trend, val])
    
    errors = validate_allocation_result(result)
    assert len(errors) == 0, f"Valid allocation should have no errors, got: {errors}"
    
    print("✓ PASS: Validation function works correctly")


def test_strength_proportional_allocation():
    """Test: Different strengths produce proportional allocation"""
    trend = create_sleeve_output(
        [{"ticker": "AAPL", "target_weight": 1.0}],
        "sleeve_trend", strength=0.75  # 75% strength
    )
    val = create_sleeve_output(
        [{"ticker": "IBM", "target_weight": 1.0}],
        "sleeve_2", strength=0.25  # 25% strength
    )
    
    allocator = PortfolioAllocator()
    result = allocator.allocate([trend, val])
    
    trend_alloc = result.sleeve_allocations.get("sleeve_trend", 0)
    val_alloc = result.sleeve_allocations.get("sleeve_2", 0)
    
    # 0.75 / (0.75 + 0.25) = 0.75, 0.25 / (0.75 + 0.25) = 0.25
    assert abs(trend_alloc - 0.75) < WEIGHT_TOLERANCE, f"Trend should be 75%, got {trend_alloc:.1%}"
    assert abs(val_alloc - 0.25) < WEIGHT_TOLERANCE, f"Valuation should be 25%, got {val_alloc:.1%}"
    
    print("✓ PASS: Strength-proportional allocation works correctly")


def main():
    print("=" * 60)
    print("Running Acceptance Tests for Dynamic Portfolio Allocation")
    print("=" * 60)
    print()
    
    tests = [
        test_trend_only_100pct,
        test_both_active_equal_strength,
        test_no_sleeves_100_cash,
        test_weights_sum_to_1,
        test_position_cap_no_renormalize,
        test_validation_function,
        test_strength_proportional_allocation,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"✗ FAIL: {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"✗ ERROR: {test.__name__}: {e}")
            failed += 1
    
    print()
    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

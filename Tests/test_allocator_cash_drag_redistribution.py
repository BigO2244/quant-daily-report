"""Tests for the 2026-04-11 cash-drag fix in core/portfolio_alloc.py.

When a sleeve's budget exceeds the total weight it can absorb at
max_position_pct (n_positions * cap), the excess used to be silently
lost to the cash bucket after the capping step. The allocator now
redistributes that excess to sleeves that still have headroom, and
only leaves residual-as-cash when no recipient has any capacity left.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.portfolio_alloc import (  # noqa: E402
    PortfolioAllocator,
    WEIGHT_TOLERANCE,
    create_sleeve_output,
)


def _mk_sleeve(name: str, tickers: list[str], strength: float = 1.0):
    """Build a SleeveOutput with equal-weight positions."""
    n = len(tickers) or 1
    eq = 1.0 / n
    positions = [{"ticker": t, "target_weight": eq} for t in tickers]
    return create_sleeve_output(positions, name, strength=strength)


class CashDragRedistributionTest(unittest.TestCase):
    def test_uncappable_sleeve_budget_spills_to_other_sleeves(self) -> None:
        # Trend has 2 positions (cap = 2 * 20% = 40%) but a huge budget.
        # Quality has 10 positions (cap = 10 * 20% = 200%, far more than it needs).
        # Without the fix, trend would cap at 40% and the rest (60%) would
        # go straight to cash. With the fix, trend's 60% excess is routed
        # to quality.
        # Strengths are clamped to [0, 1] in SleeveMetadata, so we pick
        # 0.75 / 0.25 to simulate "trend asked for 75% of the book but
        # only has 2 names".
        trend = _mk_sleeve("sleeve_trend", ["AAA", "BBB"], strength=0.75)
        quality = _mk_sleeve(
            "sleeve_quality",
            [f"Q{i}" for i in range(10)],
            strength=0.25,
        )

        allocator = PortfolioAllocator(
            max_position_pct=0.20,
            min_gross_exposure=0.95,
            cash_last_resort=True,
        )
        result = allocator.allocate([trend, quality])

        combined = result.combined_weights
        non_cash = combined[combined["ticker"] != "CASH"]
        gross = float(non_cash["target_weight"].sum())
        cash = float(
            combined.loc[combined["ticker"] == "CASH", "target_weight"].sum()
        )

        # Portfolio should be (near-)fully deployed — budget redistribution
        # fills the gap instead of defaulting to cash.
        self.assertGreaterEqual(gross, 0.95 - WEIGHT_TOLERANCE)
        self.assertLessEqual(cash, 0.05 + WEIGHT_TOLERANCE)

        # Trend should be capped at its physical capacity (2 * 20% = 40%).
        trend_non_cash = combined[
            combined["sleeve_name"].fillna("").str.contains("sleeve_trend")
            & (combined["ticker"] != "CASH")
        ]
        self.assertLessEqual(
            float(trend_non_cash["target_weight"].abs().sum()),
            0.40 + WEIGHT_TOLERANCE,
        )

        # Quality should have picked up the slack.
        quality_rows = combined[
            combined["sleeve_name"].fillna("").str.contains("sleeve_quality")
        ]
        self.assertGreater(float(quality_rows["target_weight"].sum()), 0.50)

        # A redistribution audit row should be present for trend.
        redistributed = [
            t for t in result.skipped_trades
            if t.get("action") == "SLEEVE_BUDGET_REDISTRIBUTED"
        ]
        self.assertTrue(
            any("sleeve_trend" in str(t.get("ticker", "")) for t in redistributed),
            f"Expected a redistribution entry for sleeve_trend, got {redistributed}",
        )

    def test_requested_sleeve_allocations_preserved(self) -> None:
        # The returned sleeve_allocations field should still reflect the
        # REQUESTED split (for reporting) not the internal realized split.
        trend = _mk_sleeve("sleeve_trend", ["AAA", "BBB"], strength=0.75)
        quality = _mk_sleeve(
            "sleeve_quality", [f"Q{i}" for i in range(10)], strength=0.25
        )

        allocator = PortfolioAllocator(
            max_position_pct=0.20,
            min_gross_exposure=0.95,
            cash_last_resort=True,
        )
        result = allocator.allocate([trend, quality])

        # requested: trend strength 3.0 / total 4.0 = 0.75
        self.assertAlmostEqual(
            result.sleeve_allocations.get("sleeve_trend", 0.0), 0.75, places=4
        )
        self.assertAlmostEqual(
            result.sleeve_allocations.get("sleeve_quality", 0.0), 0.25, places=4
        )

    def test_no_capacity_leaves_residual_as_cash(self) -> None:
        # Both sleeves are at their cap — no recipient with headroom — so
        # the excess legitimately becomes cash.
        trend = _mk_sleeve("sleeve_trend", ["AAA"], strength=1.0)
        quality = _mk_sleeve("sleeve_quality", ["QQQ"], strength=1.0)

        allocator = PortfolioAllocator(
            max_position_pct=0.10,
            min_gross_exposure=0.90,
            cash_last_resort=True,
        )
        result = allocator.allocate([trend, quality])

        combined = result.combined_weights
        gross_non_cash = float(
            combined.loc[combined["ticker"] != "CASH", "target_weight"].sum()
        )
        cash = float(
            combined.loc[combined["ticker"] == "CASH", "target_weight"].sum()
        )
        self.assertAlmostEqual(gross_non_cash + cash, 1.0, places=4)
        # 2 positions * 10% cap = 20% max book → 80% cash.
        self.assertLessEqual(gross_non_cash, 0.20 + WEIGHT_TOLERANCE)
        self.assertGreaterEqual(cash, 0.80 - WEIGHT_TOLERANCE)


if __name__ == "__main__":
    unittest.main()

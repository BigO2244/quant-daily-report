"""
Alpha Stack — Portfolio Constraints
======================================
Hard portfolio-level constraints applied after sleeve budget allocation.

Constraints enforced:
    max_gross_exposure  — 95% max long (5% min cash)
    max_position_pct    — 10% per-name hard cap
    max_sector_pct      — 30% per-sector cap (placeholder)
    min_cash            — 5% minimum cash buffer
    turnover_smoothing  — 5% max daily change per sleeve budget

These constraints are applied on top of sleeve-level soft constraints.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Optional

import pandas as pd

from alpha_stack._config_loader import get_section

logger = logging.getLogger(__name__)


@dataclass
class PortfolioConstraints:
    """
    Portfolio-level hard constraints.

    All values can be loaded from alpha_stack.yaml or overridden at construction.
    """
    max_gross_exposure: float = 0.95
    min_cash: float = 0.05
    max_position_pct: float = 0.10
    max_sector_pct: float = 0.30        # Placeholder — requires sector mapping
    max_daily_sleeve_change: float = 0.05  # Turnover smoothing
    drawdown_soft: float = 0.10
    drawdown_hard: float = 0.15

    @classmethod
    def from_config(cls) -> "PortfolioConstraints":
        """Load constraints from alpha_stack.yaml."""
        cfg = (get_section("allocator") or {}).get("constraints", {})
        return cls(
            max_gross_exposure=float(cfg.get("max_gross_exposure", 0.95)),
            min_cash=float(cfg.get("min_cash", 0.05)),
            max_position_pct=float(cfg.get("max_position_pct", 0.10)),
            max_sector_pct=float(cfg.get("max_sector_pct", 0.30)),
            max_daily_sleeve_change=float(cfg.get("max_daily_sleeve_change", 0.05)),
            drawdown_soft=float(cfg.get("drawdown_soft", 0.10)),
            drawdown_hard=float(cfg.get("drawdown_hard", 0.15)),
        )

    def apply_position_caps(
        self,
        weights: pd.Series,
        sector_map: Optional[Dict[str, str]] = None,
    ) -> pd.Series:
        """
        Apply per-name and per-sector position caps.

        Parameters
        ----------
        weights : Series
            {ticker: weight} target weights (summing to <= 1).
        sector_map : dict, optional
            {ticker: sector} for sector constraint.

        Returns
        -------
        Series
            Weights with caps applied and renormalised.
        """
        w = weights.copy().clip(lower=0)

        # Per-name cap
        w = w.clip(upper=self.max_position_pct)

        # Per-sector cap (if sector map available)
        if sector_map:
            w = self._apply_sector_cap(w, sector_map)

        # Gross exposure cap
        total = w.sum()
        if total > self.max_gross_exposure:
            w = w * (self.max_gross_exposure / total)

        return w

    def _apply_sector_cap(
        self,
        weights: pd.Series,
        sector_map: Dict[str, str],
    ) -> pd.Series:
        """Reduce sector weights that exceed max_sector_pct."""
        w = weights.copy()
        sector_totals: Dict[str, float] = {}
        for ticker, sector in sector_map.items():
            if ticker in w.index:
                sector_totals[sector] = sector_totals.get(sector, 0) + w[ticker]

        for sector, total in sector_totals.items():
            if total > self.max_sector_pct:
                scale = self.max_sector_pct / total
                for ticker, sec in sector_map.items():
                    if sec == sector and ticker in w.index:
                        w[ticker] *= scale
                logger.debug(
                    "[CONSTRAINTS] Sector cap applied: %s %.1f%% → %.1f%%",
                    sector, total * 100, self.max_sector_pct * 100,
                )

        return w

    def smooth_sleeve_budgets(
        self,
        current_budgets: Dict[str, float],
        target_budgets: Dict[str, float],
        is_crisis: bool = False,
    ) -> Dict[str, float]:
        """
        Apply turnover smoothing: limit daily change per sleeve budget.

        Parameters
        ----------
        current_budgets : dict
            Current sleeve budgets {name: fraction}.
        target_budgets : dict
            Newly computed target budgets.
        is_crisis : bool
            If True, skip smoothing and apply immediately.

        Returns
        -------
        dict
            Smoothed target budgets.
        """
        if is_crisis:
            return target_budgets

        smoothed = {}
        cap = self.max_daily_sleeve_change

        for name, target in target_budgets.items():
            current = current_budgets.get(name, target)
            delta = target - current
            if abs(delta) > cap:
                # Phase the change
                new_val = current + (cap if delta > 0 else -cap)
                logger.debug(
                    "[CONSTRAINTS] Smoothing %s budget: %.3f → %.3f (target=%.3f, capped)",
                    name, current, new_val, target,
                )
                smoothed[name] = new_val
            else:
                smoothed[name] = target

        # Renormalise
        total = sum(smoothed.values())
        if total > 0:
            smoothed = {k: v / total for k, v in smoothed.items()}

        return smoothed

"""
Alpha Stack — Allocator v1 (Regime-Based)
==========================================
Combines sleeve outputs into a final target book using regime-based
sleeve budget mapping, volatility/breadth/macro modifiers, turnover
smoothing, and portfolio-level hard constraints.

Architecture:
    1. Compute base sleeve budgets from trend_state.
    2. Apply volatility modifiers.
    3. Apply breadth modifiers.
    4. Apply macro modifiers.
    5. Normalise to max_gross_exposure.
    6. Apply turnover smoothing.
    7. Scale each sleeve's candidate weights by the sleeve budget.
    8. Apply portfolio-level position caps (per-name, per-sector).
    9. Return AllocationResult.

IMPORTANT: This allocator is for Alpha Stack research/shadow only.
           It does NOT interact with the production execution path.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

from alpha_stack.regime.context import RegimeContext
from alpha_stack.regime.state_machine import (
    TrendState, VolatilityState, BreadthState, MacroState
)
from alpha_stack.sleeves.base import SleeveOutput
from alpha_stack.portfolio.constraints import PortfolioConstraints
from alpha_stack._config_loader import get_section

logger = logging.getLogger(__name__)


# ================================================================== #
# AllocationResult                                                     #
# ================================================================== #

@dataclass
class AllocationResult:
    """
    Output of the allocator for a single date.

    Fields
    ------
    as_of_date : str
    target_book : DataFrame
        Per-ticker target weights with columns:
        [ticker, weight, sleeve, score, sector, hold_state]
    sleeve_budgets : dict
        Final sleeve budget allocations {name: fraction}.
    cash_weight : float
        Residual cash weight (1 - sum(target_book.weight)).
    regime_context : RegimeContext
    notes : list
        Explanatory notes on any constraint actions.
    meta : dict
        Additional diagnostics.
    """
    as_of_date:     str
    target_book:    pd.DataFrame = field(default_factory=pd.DataFrame)
    sleeve_budgets: Dict[str, float] = field(default_factory=dict)
    cash_weight:    float = 1.0
    regime_context: Optional[RegimeContext] = None
    notes:          List[str] = field(default_factory=list)
    meta:           Dict = field(default_factory=dict)

    @property
    def gross_exposure(self) -> float:
        if self.target_book.empty or "weight" not in self.target_book.columns:
            return 0.0
        return float(self.target_book["weight"].sum())


# ================================================================== #
# AlphaStackAllocator                                                  #
# ================================================================== #

class AlphaStackAllocator:
    """
    Regime-aware sleeve budget allocator for Alpha Stack v1.

    Parameters
    ----------
    constraints : PortfolioConstraints, optional
        Portfolio-level hard constraints. Loads from config if None.
    config_overrides : dict, optional
        Override alpha_stack.yaml allocator settings.
    """

    def __init__(
        self,
        constraints: Optional[PortfolioConstraints] = None,
        config_overrides: Optional[dict] = None,
    ) -> None:
        self._constraints = constraints or PortfolioConstraints.from_config()
        cfg_base = get_section("allocator") or {}
        self._cfg = {**cfg_base, **(config_overrides or {})}
        self._prev_budgets: Dict[str, float] = {}   # for smoothing

    # ------------------------------------------------------------------ #
    # Main entry point                                                     #
    # ------------------------------------------------------------------ #

    def allocate(
        self,
        sleeve_outputs: Dict[str, SleeveOutput],
        regime_context: RegimeContext,
        sector_map: Optional[Dict[str, str]] = None,
        current_dd: float = 0.0,
    ) -> AllocationResult:
        """
        Compute final target book from sleeve outputs and regime context.

        Parameters
        ----------
        sleeve_outputs : dict
            {sleeve_name: SleeveOutput} from all active sleeves.
        regime_context : RegimeContext
        sector_map : dict, optional
            {ticker: sector} for sector constraints.
        current_dd : float
            Current drawdown from peak (0 = no drawdown, 0.10 = 10% DD).

        Returns
        -------
        AllocationResult
        """
        notes = []
        date_str = regime_context.as_of_date

        # 1. Base budgets from trend state
        budgets = self._base_budgets(regime_context.trend_state)
        notes.append(f"Base budgets from trend_state={regime_context.trend_state.value}")

        # 2. Volatility modifiers
        budgets = self._apply_vol_modifiers(budgets, regime_context.vol_state, notes)

        # 3. Breadth modifiers
        budgets = self._apply_breadth_modifiers(budgets, regime_context.breadth_state, notes)

        # 4. Macro modifiers
        budgets = self._apply_macro_modifiers(budgets, regime_context.macro_state, notes)

        # 5. Zero out disabled sleeves
        for name in list(budgets.keys()):
            if name not in sleeve_outputs or not sleeve_outputs[name].active:
                released = budgets.pop(name, 0.0)
                if released > 0:
                    budgets["cash"] = budgets.get("cash", 0.0) + released
                    notes.append(f"Sleeve {name} inactive; budget released to cash.")

        # 6. Normalise
        budgets = _normalise_budgets(budgets, self._constraints.max_gross_exposure)

        # 7. Drawdown circuit breaker
        budgets, dd_note = self._apply_drawdown_breaker(budgets, current_dd)
        if dd_note:
            notes.append(dd_note)

        # 8. Turnover smoothing (skip if prev_budgets empty = first run)
        is_crisis = regime_context.vol_state == VolatilityState.CRISIS
        if self._prev_budgets:
            budgets_non_cash = {k: v for k, v in budgets.items() if k != "cash"}
            prev_non_cash = {k: v for k, v in self._prev_budgets.items() if k != "cash"}
            smoothed = self._constraints.smooth_sleeve_budgets(
                prev_non_cash, budgets_non_cash, is_crisis
            )
            if "cash" in budgets:
                smoothed["cash"] = budgets["cash"]
            budgets = smoothed

        self._prev_budgets = dict(budgets)

        # 9. Scale each sleeve's weights by its budget and merge into target book
        target_book = self._merge_sleeve_outputs(sleeve_outputs, budgets)

        # 10. Apply portfolio-level position caps
        if not target_book.empty and "weight" in target_book.columns:
            raw_weights = target_book.set_index("ticker")["weight"]
            capped = self._constraints.apply_position_caps(raw_weights, sector_map)
            target_book["weight"] = target_book["ticker"].map(capped).fillna(0.0)

        cash_weight = max(
            1.0 - target_book["weight"].sum() if not target_book.empty else 1.0,
            self._constraints.min_cash,
        )

        result = AllocationResult(
            as_of_date=date_str,
            target_book=target_book,
            sleeve_budgets={k: v for k, v in budgets.items() if k != "cash"},
            cash_weight=round(cash_weight, 6),
            regime_context=regime_context,
            notes=notes,
            meta={"drawdown": current_dd, "is_crisis": is_crisis},
        )

        logger.info(
            "[ALLOCATOR] %s | gross=%.1f%% cash=%.1f%% | %s",
            date_str,
            result.gross_exposure * 100,
            cash_weight * 100,
            regime_context,
        )
        return result

    # ------------------------------------------------------------------ #
    # Budget computation                                                   #
    # ------------------------------------------------------------------ #

    def _base_budgets(self, trend_state: TrendState) -> Dict[str, float]:
        """Return base sleeve budgets for a trend state."""
        cfg = self._cfg.get("base_weights", {})
        key = trend_state.value
        defaults = {
            "strong_up":   {"trend": 0.55, "value": 0.20, "quality": 0.15, "mean_reversion": 0.10},
            "weak_up":     {"trend": 0.45, "value": 0.25, "quality": 0.20, "mean_reversion": 0.10},
            "neutral":     {"trend": 0.35, "value": 0.25, "quality": 0.30, "mean_reversion": 0.10},
            "weak_down":   {"trend": 0.20, "value": 0.20, "quality": 0.50, "mean_reversion": 0.10},
            "strong_down": {"trend": 0.10, "value": 0.15, "quality": 0.65, "mean_reversion": 0.10},
        }
        return dict(cfg.get(key, defaults.get(key, defaults["neutral"])))

    def _apply_vol_modifiers(
        self,
        budgets: Dict[str, float],
        vol_state: VolatilityState,
        notes: List[str],
    ) -> Dict[str, float]:
        """Apply volatility modifiers to sleeve budgets."""
        b = dict(budgets)
        cfg = self._cfg.get("vol_modifiers", {})

        if vol_state == VolatilityState.ELEVATED:
            # Cut MR by 50%; redistribute to quality
            mr_mult = float(cfg.get("elevated", {}).get("mean_reversion_mult", 0.5))
            if "mean_reversion" in b:
                released = b["mean_reversion"] * (1 - mr_mult)
                b["mean_reversion"] *= mr_mult
                b["quality"] = b.get("quality", 0) + released
                notes.append(f"Vol=elevated: MR reduced by 50%, {released:.3f} added to quality.")

        elif vol_state == VolatilityState.CRISIS:
            # Zero MR; reduce trend 30%; move released to cash
            crisis_cfg = cfg.get("crisis", {})
            mr_release = b.pop("mean_reversion", 0.0)
            trend_reduction = float(crisis_cfg.get("trend_reduction", 0.30))
            trend_release = b.get("trend", 0) * trend_reduction
            b["trend"] = b.get("trend", 0) * (1 - trend_reduction)
            cash_add = mr_release + trend_release
            b["cash"] = b.get("cash", 0.0) + cash_add
            notes.append(
                f"Vol=CRISIS: MR=0, trend -30%, {cash_add:.3f} moved to cash."
            )

        return b

    def _apply_breadth_modifiers(
        self,
        budgets: Dict[str, float],
        breadth_state: BreadthState,
        notes: List[str],
    ) -> Dict[str, float]:
        """Apply breadth modifiers."""
        b = dict(budgets)
        cfg = self._cfg.get("breadth_modifiers", {})

        if breadth_state == BreadthState.HEALTHY:
            add = float(cfg.get("healthy", {}).get("trend_add", 0.05))
            # Fund from value/quality pro-rata
            available = b.get("value", 0) + b.get("quality", 0)
            if available > 0 and add > 0:
                scale = min(add, available) / available
                b["value"] = b.get("value", 0) * (1 - scale)
                b["quality"] = b.get("quality", 0) * (1 - scale)
                b["trend"] = b.get("trend", 0) + min(add, available)
                notes.append(f"Breadth=healthy: +{add:.2f} to trend.")

        elif breadth_state == BreadthState.DETERIORATING:
            sub = float(cfg.get("deteriorating", {}).get("trend_subtract", 0.05))
            add = float(cfg.get("deteriorating", {}).get("quality_add", 0.05))
            released = min(sub, b.get("trend", 0))
            b["trend"] = max(0, b.get("trend", 0) - released)
            b["quality"] = b.get("quality", 0) + add
            notes.append(f"Breadth=deteriorating: trend -{released:.3f}, quality +{add:.3f}.")

        elif breadth_state == BreadthState.WASHED_OUT:
            # No new trend entries — record in meta but don't change weights here
            # (Sleeves handle this via hold_state = trim/exit)
            notes.append("Breadth=washed_out: no new trend entries (hold/trim only).")

        return b

    def _apply_macro_modifiers(
        self,
        budgets: Dict[str, float],
        macro_state: MacroState,
        notes: List[str],
    ) -> Dict[str, float]:
        """Apply macro modifiers."""
        b = dict(budgets)
        cfg = self._cfg.get("macro_modifiers", {})

        if macro_state == MacroState.SUPPORTIVE:
            add = float(cfg.get("supportive", {}).get("value_add", 0.05))
            cash_sub = float(cfg.get("supportive", {}).get("cash_reserve_subtract", 0.05))
            b["value"] = b.get("value", 0) + add
            b["cash"] = max(0, b.get("cash", 0) - cash_sub)
            notes.append(f"Macro=supportive: +{add:.2f} value.")

        elif macro_state == MacroState.RESTRICTIVE:
            add = float(cfg.get("restrictive", {}).get("cash_reserve_add", 0.05))
            sub = float(cfg.get("restrictive", {}).get("value_subtract", 0.05))
            b["cash"] = b.get("cash", 0) + add
            b["value"] = max(0, b.get("value", 0) - sub)
            notes.append(f"Macro=restrictive: +{add:.2f} cash, -{sub:.2f} value.")

        return b

    def _apply_drawdown_breaker(
        self,
        budgets: Dict[str, float],
        current_dd: float,
    ) -> tuple:
        """Apply drawdown circuit breaker (placeholder)."""
        note = ""
        soft = self._constraints.drawdown_soft
        hard = self._constraints.drawdown_hard

        if current_dd >= hard:
            # Hard mode: move all non-cash to cash
            note = f"HARD DRAWDOWN BREAKER ({current_dd:.1%}): portfolio moved to cash."
            budgets = {"cash": 1.0}
        elif current_dd >= soft:
            # Soft mode: reduce all sleeves by 50%
            note = f"Soft drawdown breaker ({current_dd:.1%}): reducing all sleeves by 50%."
            new_b = {}
            for k, v in budgets.items():
                if k == "cash":
                    new_b["cash"] = v
                else:
                    new_b[k] = v * 0.5
                    new_b["cash"] = new_b.get("cash", 0) + v * 0.5
            budgets = new_b

        return budgets, note

    # ------------------------------------------------------------------ #
    # Target book assembly                                                 #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _merge_sleeve_outputs(
        outputs: Dict[str, SleeveOutput],
        budgets: Dict[str, float],
    ) -> pd.DataFrame:
        """Merge sleeve candidate outputs, scaled by sleeve budgets."""
        frames = []
        for sleeve_name, output in outputs.items():
            if not output.active or not output.has_candidates:
                continue
            budget = budgets.get(sleeve_name, 0.0)
            if budget <= 0:
                continue

            df = output.candidates.copy()
            if "provisional_weight" not in df.columns:
                continue

            # Scale provisional weights by sleeve budget
            # provisional_weight already sums to ≤ risk_budget (passed as 1.0 from sleeve)
            prov_sum = df["provisional_weight"].sum()
            if prov_sum > 0:
                df["weight"] = df["provisional_weight"] / prov_sum * budget
            else:
                df["weight"] = 0.0

            df["sleeve"] = sleeve_name
            frames.append(df)

        if not frames:
            return pd.DataFrame(columns=["ticker", "weight", "sleeve"])

        merged = pd.concat(frames, ignore_index=True)

        # If same ticker appears in multiple sleeves, sum their weights
        if "ticker" in merged.columns:
            agg = merged.groupby("ticker").agg(
                weight=("weight", "sum"),
                sleeve=("sleeve", "first"),
                score=("score", "max") if "score" in merged.columns else ("sleeve", "first"),
                hold_state=("hold_state", "first") if "hold_state" in merged.columns else ("sleeve", "first"),
            ).reset_index()
            return agg

        return merged


def _normalise_budgets(
    budgets: Dict[str, float],
    max_exposure: float,
) -> Dict[str, float]:
    """Normalise sleeve budgets so non-cash total <= max_exposure."""
    non_cash = {k: v for k, v in budgets.items() if k != "cash"}
    total = sum(non_cash.values())
    if total > max_exposure:
        scale = max_exposure / total
        non_cash = {k: v * scale for k, v in non_cash.items()}
    result = dict(non_cash)
    result["cash"] = budgets.get("cash", 0.0)
    return result

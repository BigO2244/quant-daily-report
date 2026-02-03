# core/portfolio_alloc.py
"""
Dynamic Portfolio Allocation Module
===================================
Implements dynamic portfolio allocation across sleeves based on signal activity.
Does NOT modify any sleeve signal logic, thresholds, indicators, or valuation rules.

Allocation Logic:
1. Each sleeve exposes: positions_df (ticker, target_weight, reason, signal_strength)
   and meta (sleeve_name, is_active, strength 0-1, notes)
2. Dynamic allocation: alloc_i = strength_i / sum(strength over active sleeves)
3. Portfolio constraints applied WITHOUT renormalization - excess becomes CASH
4. Final output: combined weights + CASH row, sum = 1.00
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# =============================================================================
# CONFIGURATION
# =============================================================================
DEFAULT_PORTFOLIO_BASE_EQUITY = 10_000.0
DEFAULT_SLEEVE_WEIGHTS = {"sleeve_trend": 0.80, "sleeve_2": 0.20}
DEFAULT_MAX_POSITION_PCT = 0.10
DEFAULT_MAX_SLEEVE_EXPOSURE = 1.00
DEFAULT_MAX_TURNOVER = 0.50
CASH_TICKER = "CASH"
WEIGHT_TOLERANCE = 1e-8

# =============================================================================
# DATA STRUCTURES
# =============================================================================
@dataclass
class SleeveMetadata:
    """Metadata for a sleeve's current state."""
    sleeve_name: str
    is_active: bool = False
    strength: float = 1.0
    notes: str = ""
    
    def __post_init__(self):
        self.strength = max(0.0, min(1.0, float(self.strength)))


@dataclass
class SleeveOutput:
    """Standardized output from a sleeve for portfolio allocation."""
    positions_df: pd.DataFrame
    meta: SleeveMetadata
    
    def __post_init__(self):
        if self.positions_df is None:
            self.positions_df = pd.DataFrame(columns=["ticker", "target_weight", "reason", "signal_strength"])
        for col in ["ticker", "target_weight"]:
            if col not in self.positions_df.columns:
                self.positions_df[col] = [] if col == "ticker" else 0.0
        if "reason" not in self.positions_df.columns:
            self.positions_df["reason"] = ""
        if "signal_strength" not in self.positions_df.columns:
            self.positions_df["signal_strength"] = 1.0


@dataclass
class AllocationResult:
    """Result of portfolio allocation across sleeves."""
    combined_weights: pd.DataFrame
    sleeve_allocations: dict[str, float]
    skipped_trades: list[dict] = field(default_factory=list)
    exit_log: list[dict] = field(default_factory=list)
    
    @property
    def total_weight(self) -> float:
        if self.combined_weights.empty:
            return 0.0
        return float(self.combined_weights["target_weight"].sum())
    
    @property
    def cash_weight(self) -> float:
        if self.combined_weights.empty:
            return 1.0
        cash_row = self.combined_weights[self.combined_weights["ticker"] == CASH_TICKER]
        return float(cash_row["target_weight"].iloc[0]) if not cash_row.empty else 0.0


# =============================================================================
# SLEEVE OUTPUT HELPERS
# =============================================================================
def create_sleeve_output(
    positions: list[dict] | pd.DataFrame | None,
    sleeve_name: str,
    strength: float = 1.0,
    notes: str = "",
) -> SleeveOutput:
    """Factory function to create a SleeveOutput from various input formats."""
    if positions is None:
        df = pd.DataFrame(columns=["ticker", "target_weight", "reason", "signal_strength"])
    elif isinstance(positions, pd.DataFrame):
        df = positions.copy()
    else:
        df = pd.DataFrame(positions)
    
    if not df.empty and "target_weight" in df.columns:
        weight_sum = df["target_weight"].sum()
        if weight_sum > 0:
            df["target_weight"] = df["target_weight"] / weight_sum
    
    is_active = not df.empty and (df.get("target_weight", pd.Series([0])).abs().sum() > WEIGHT_TOLERANCE)
    meta = SleeveMetadata(sleeve_name=sleeve_name, is_active=is_active, strength=strength, notes=notes)
    return SleeveOutput(positions_df=df, meta=meta)


# =============================================================================
# DYNAMIC ALLOCATION ENGINE
# =============================================================================
class PortfolioAllocator:
    """Dynamic portfolio allocator that combines sleeve outputs."""
    
    def __init__(
        self,
        base_equity: float = DEFAULT_PORTFOLIO_BASE_EQUITY,
        max_position_pct: float = DEFAULT_MAX_POSITION_PCT,
        max_sleeve_exposure: float = DEFAULT_MAX_SLEEVE_EXPOSURE,
        max_turnover: Optional[float] = None,
    ):
        self.base_equity = base_equity
        self.max_position_pct = max_position_pct
        self.max_sleeve_exposure = max_sleeve_exposure
        self.max_turnover = max_turnover
        self._skipped_trades: list[dict] = []
        self._exit_log: list[dict] = []
    
    def allocate(
        self,
        sleeve_outputs: list[SleeveOutput],
        previous_weights: Optional[pd.DataFrame] = None,
    ) -> AllocationResult:
        """Perform dynamic allocation across sleeves."""
        self._skipped_trades = []
        self._exit_log = []
        
        sleeve_allocations = self._compute_sleeve_allocations(sleeve_outputs)
        combined = self._combine_sleeve_weights(sleeve_outputs, sleeve_allocations)
        combined = self._apply_position_constraints(combined)
        
        if previous_weights is not None and self.max_turnover is not None:
            combined = self._apply_turnover_constraints(combined, previous_weights)
        
        combined = self._add_cash_allocation(combined)
        
        total = combined["target_weight"].sum()
        if abs(total - 1.0) > WEIGHT_TOLERANCE:
            logger.warning("[WARN] Portfolio weights sum to %s, expected 1.0", f"{total:.6f}")
        
        return AllocationResult(
            combined_weights=combined,
            sleeve_allocations=sleeve_allocations,
            skipped_trades=self._skipped_trades,
            exit_log=self._exit_log,
        )
    
    def _compute_sleeve_allocations(self, sleeve_outputs: list[SleeveOutput]) -> dict[str, float]:
        """Compute allocation percentage for each sleeve based on activity and strength."""
        active_sleeves = []
        for s in sleeve_outputs:
            df = s.positions_df
            has_weights = (
                df is not None
                and not df.empty
                and "target_weight" in df.columns
                and float(df["target_weight"].abs().sum()) > WEIGHT_TOLERANCE
        )
            s.meta.is_active = bool(has_weights)   # keep report consistent
            if s.meta.is_active:
                active_sleeves.append(s)

        
        if not active_sleeves:
            return {s.meta.sleeve_name: 0.0 for s in sleeve_outputs}
        
        total_strength = sum(s.meta.strength for s in active_sleeves)
        
        if total_strength <= 0:
            equal_weight = 1.0 / len(active_sleeves)
            return {s.meta.sleeve_name: equal_weight if s.meta.is_active else 0.0 for s in sleeve_outputs}
        
        return {
            s.meta.sleeve_name: (s.meta.strength / total_strength) if s.meta.is_active else 0.0
            for s in sleeve_outputs
        }
    
    def _combine_sleeve_weights(
        self,
        sleeve_outputs: list[SleeveOutput],
        sleeve_allocations: dict[str, float],
    ) -> pd.DataFrame:
        """Combine sleeve weights, scaling each by its allocation percentage."""
        combined_rows = []
        
        for sleeve in sleeve_outputs:
            alloc_pct = sleeve_allocations.get(sleeve.meta.sleeve_name, 0.0)
            if alloc_pct <= 0 or sleeve.positions_df is None or sleeve.positions_df.empty:
                continue

            
            for _, row in sleeve.positions_df.iterrows():
                scaled_weight = float(row["target_weight"]) * alloc_pct
                combined_rows.append({
                    "ticker": row["ticker"],
                    "target_weight": scaled_weight,
                    "sleeve_name": sleeve.meta.sleeve_name,
                    "reason": row.get("reason", ""),
                    "signal_strength": row.get("signal_strength", 1.0),
                })
        
        if not combined_rows:
            return pd.DataFrame(columns=["ticker", "target_weight", "sleeve_name", "reason", "signal_strength"])
        
        df = pd.DataFrame(combined_rows)
        return df.groupby("ticker", as_index=False).agg({
            "target_weight": "sum",
            "sleeve_name": lambda x: ", ".join(sorted(set(x))),
            "reason": "first",
            "signal_strength": "mean",
        })
    
    def _apply_position_constraints(self, combined: pd.DataFrame) -> pd.DataFrame:
        """Apply max position size constraint. Does NOT renormalize after capping."""
        if combined.empty:
            return combined

        df = combined.copy()

        # Use integer-position assignment to avoid Pylance/pandas index typing issues
        weight_col_idx = df.columns.get_loc("target_weight")  # type: ignore

        for i, row in enumerate(df.itertuples(index=False)):
            orig = float(getattr(row, "target_weight"))
            if orig > self.max_position_pct:
                excess = orig - self.max_position_pct
                df.iat[i, weight_col_idx] = self.max_position_pct  # type: ignore
        
                self._skipped_trades.append({
                    "ticker": getattr(row, "ticker"),
                    "action": "WEIGHT_CAPPED",
                    "original_weight": orig,
                    "capped_weight": self.max_position_pct,
                    "excess": excess,
                    "reason": f"Max position {self.max_position_pct:.1%} exceeded",
                })
        return df

    
    def _apply_turnover_constraints(self, combined: pd.DataFrame, previous_weights: pd.DataFrame) -> pd.DataFrame:
        """Apply maximum turnover constraint."""
        if combined.empty or previous_weights.empty or self.max_turnover is None:
            return combined

        df = combined.copy()
        prev = previous_weights[["ticker", "target_weight"]].copy().rename(columns={"target_weight": "prev_weight"})
        df = df.merge(prev, on="ticker", how="outer")

        df["target_weight"] = df["target_weight"].fillna(0.0)
        df["prev_weight"] = df["prev_weight"].fillna(0.0)

        df["weight_change"] = (df["target_weight"] - df["prev_weight"]).abs()
        total_turnover = float(df["weight_change"].sum()) / 2.0

        if total_turnover > self.max_turnover:
            scale_factor = self.max_turnover / total_turnover

            # Integer column positions for .iat assignments
            weight_col_idx = df.columns.get_loc("target_weight")  # type: ignore
            ticker_col_idx = df.columns.get_loc("ticker")  # type: ignore

            for i, row in enumerate(df.itertuples(index=False)):
                target_w = float(getattr(row, "target_weight"))
                prev_w = float(getattr(row, "prev_weight"))

                change = target_w - prev_w
                new_weight = prev_w + change * scale_factor

                if abs(new_weight - target_w) > WEIGHT_TOLERANCE:
                    self._skipped_trades.append({
                        "ticker": df.iat[i, ticker_col_idx],  # type: ignore
                        "action": "TURNOVER_LIMITED",
                        "original_weight": target_w,
                        "limited_weight": new_weight,
                        "reason": f"Turnover limit {self.max_turnover:.1%} exceeded",
                    })

                df.iat[i, weight_col_idx] = new_weight  # type: ignore

        df = df[df["target_weight"].abs() > WEIGHT_TOLERANCE].copy()
        return df.drop(columns=["prev_weight", "weight_change"], errors="ignore")

    
    def _add_cash_allocation(self, combined: pd.DataFrame) -> pd.DataFrame:
        """Add explicit CASH row for remaining allocation."""
        current_total = combined["target_weight"].sum() if not combined.empty else 0.0
        cash_weight = max(0.0, 1.0 - current_total)
        
        if cash_weight > WEIGHT_TOLERANCE:
            cash_row = pd.DataFrame([{
                "ticker": CASH_TICKER,
                "target_weight": cash_weight,
                "sleeve_name": "CASH",
                "reason": "Unallocated capital",
                "signal_strength": 0.0,
            }])
            combined = pd.concat([combined, cash_row], ignore_index=True)
        return combined


# =============================================================================
# LEGACY FUNCTIONS (backward compatibility)
# =============================================================================
def scale_sleeve_daily(
    daily_df: pd.DataFrame,
    sleeve_name: str,
    base_equity: float = DEFAULT_PORTFOLIO_BASE_EQUITY,
    weights: dict[str, float] | None = None,
    equity_col: str = "equity",
) -> pd.DataFrame:
    """Scale a sleeve's daily equity series to its allocated capital (legacy)."""
    if weights is None:
        weights = DEFAULT_SLEEVE_WEIGHTS
    if daily_df.empty:
        return daily_df.copy()

    w = float(weights.get(sleeve_name, 0.0))
    alloc_capital = base_equity * w
    df = daily_df.copy()
    start_equity = float(df[equity_col].iloc[0])

    if start_equity <= 0:
        raise ValueError(f"{sleeve_name}: start equity must be > 0, got {start_equity}")

    df["alloc_weight"] = w
    df["alloc_capital"] = alloc_capital
    df["alloc_equity"] = alloc_capital * (df[equity_col] / start_equity)
    df["alloc_day_pnl"] = df["alloc_equity"].diff().fillna(0.0)
    df["alloc_day_return"] = df["alloc_equity"].pct_change().fillna(0.0)
    return df


def combine_portfolio_daily(sleeve_daily_scaled: dict[str, pd.DataFrame], date_col: str = "date") -> pd.DataFrame:
    """Combine scaled sleeve frames into a single portfolio daily series (legacy)."""
    frames = []
    for name, df in sleeve_daily_scaled.items():
        if df.empty:
            continue
        tmp = df[[date_col, "alloc_equity", "alloc_day_pnl"]].copy()
        tmp = tmp.rename(columns={"alloc_equity": f"{name}_alloc_equity", "alloc_day_pnl": f"{name}_alloc_day_pnl"})
        frames.append(tmp)

    if not frames:
        return pd.DataFrame(columns=[date_col, "portfolio_equity", "portfolio_day_pnl", "portfolio_day_return"])

    out = frames[0]
    for f in frames[1:]:
        out = out.merge(f, on=date_col, how="outer")

    out = out.sort_values(date_col).reset_index(drop=True)
    equity_cols = [c for c in out.columns if c.endswith("_alloc_equity")]
    pnl_cols = [c for c in out.columns if c.endswith("_alloc_day_pnl")]

    out["portfolio_equity"] = out[equity_cols].sum(axis=1, skipna=True)
    out["portfolio_day_pnl"] = out[pnl_cols].sum(axis=1, skipna=True)
    out["portfolio_day_return"] = out["portfolio_equity"].pct_change().fillna(0.0)
    return out


# =============================================================================
# REPORTING HELPERS
# =============================================================================
def compute_dynamic_allocation(
    sleeve_outputs: list[SleeveOutput],
    base_equity: float = DEFAULT_PORTFOLIO_BASE_EQUITY,
    max_position_pct: float = DEFAULT_MAX_POSITION_PCT,
    previous_weights: Optional[pd.DataFrame] = None,
) -> AllocationResult:
    """Convenience function to compute dynamic allocation."""
    allocator = PortfolioAllocator(base_equity=base_equity, max_position_pct=max_position_pct)
    return allocator.allocate(sleeve_outputs, previous_weights)


def allocation_summary_df(result: AllocationResult) -> pd.DataFrame:
    """Create a summary DataFrame of sleeve allocations for reporting."""
    rows = []
    for sleeve_name, alloc_pct in result.sleeve_allocations.items():
        rows.append({
            "Sleeve": sleeve_name,
            "Active": "Yes" if alloc_pct > 0 else "No",
            "Allocated %": f"{alloc_pct:.1%}",
        })
    rows.append({
        "Sleeve": "CASH",
        "Active": "Yes" if result.cash_weight > WEIGHT_TOLERANCE else "No",
        "Allocated %": f"{result.cash_weight:.1%}",
    })
    return pd.DataFrame(rows)


def holdings_snapshot_df(result: AllocationResult) -> pd.DataFrame:
    """Create a holdings snapshot DataFrame for reporting."""
    if result.combined_weights.empty:
        return pd.DataFrame(columns=["Ticker", "Weight %", "Sleeve"])
    df = result.combined_weights.copy()
    df = df.rename(columns={"ticker": "Ticker", "target_weight": "Weight %", "sleeve_name": "Sleeve"})
    df["Weight %"] = df["Weight %"].apply(lambda x: f"{x:.2%}")
    return df[["Ticker", "Weight %", "Sleeve"]].sort_values("Ticker", key=lambda x: x != CASH_TICKER)


def trade_blotter_df(result: AllocationResult, previous_weights: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Create a trade blotter showing BUY/SELL/HOLD actions."""
    rows = []
    current = result.combined_weights.set_index("ticker")["target_weight"].to_dict()
    prev = previous_weights.set_index("ticker")["target_weight"].to_dict() if previous_weights is not None and not previous_weights.empty else {}
    
    for ticker in sorted(set(current.keys()) | set(prev.keys())):
        curr_w, prev_w = current.get(ticker, 0.0), prev.get(ticker, 0.0)
        change = curr_w - prev_w
        action = "HOLD" if abs(change) < WEIGHT_TOLERANCE else ("BUY" if change > 0 else "SELL")
        skipped = next((s for s in result.skipped_trades if s["ticker"] == ticker), None)
        rows.append({
            "Ticker": ticker, "Action": action, "Prev Weight": f"{prev_w:.2%}",
            "New Weight": f"{curr_w:.2%}", "Change": f"{change:+.2%}",
            "Skipped": skipped["reason"] if skipped else "",
        })
    return pd.DataFrame(rows)


def validate_allocation_result(result: AllocationResult) -> list[str]:
    """Validate an allocation result and return any issues found."""
    errors = []
    if abs(result.total_weight - 1.0) > WEIGHT_TOLERANCE:
        errors.append(f"Total weights sum to {result.total_weight:.6f}, expected 1.0")
    if not result.combined_weights.empty:
        neg = result.combined_weights[result.combined_weights["target_weight"] < -WEIGHT_TOLERANCE]
        if not neg.empty:
            errors.append(f"Negative weights found: {neg['ticker'].tolist()}")
    if sum(result.sleeve_allocations.values()) > 1.0 + WEIGHT_TOLERANCE:
        errors.append(f"Sleeve allocations exceed 1.0")
    return errors


def test_allocation_scenarios() -> dict[str, bool]:
    """Run acceptance test scenarios for the allocator."""
    results = {}
    allocator = PortfolioAllocator()
    
    # Scenario 1: Only trend active -> 100%
    trend_only = create_sleeve_output([{"ticker": "AAPL", "target_weight": 0.5}, {"ticker": "MSFT", "target_weight": 0.5}], "sleeve_trend", 1.0)
    val_inactive = create_sleeve_output([], "sleeve_2", 0.0)
    result = allocator.allocate([trend_only, val_inactive])
    results["trend_only_100pct"] = abs(result.sleeve_allocations.get("sleeve_trend", 0) - 1.0) < WEIGHT_TOLERANCE
    
    # Scenario 2: Both active, equal strength -> 50/50
    trend_active = create_sleeve_output([{"ticker": "AAPL", "target_weight": 1.0}], "sleeve_trend", 1.0)
    val_active = create_sleeve_output([{"ticker": "IBM", "target_weight": 1.0}], "sleeve_2", 1.0)
    result = allocator.allocate([trend_active, val_active])
    results["both_active_50_50"] = (
        abs(result.sleeve_allocations.get("sleeve_trend", 0) - 0.5) < WEIGHT_TOLERANCE and
        abs(result.sleeve_allocations.get("sleeve_2", 0) - 0.5) < WEIGHT_TOLERANCE
    )
    
    # Scenario 3: No sleeves active -> 100% CASH
    result = allocator.allocate([create_sleeve_output([], "sleeve_trend", 0.0), create_sleeve_output([], "sleeve_2", 0.0)])
    results["no_sleeves_100_cash"] = abs(result.cash_weight - 1.0) < WEIGHT_TOLERANCE
    
    # Scenario 4: Weights sum to 1.0
    result = allocator.allocate([trend_active, val_active])
    results["weights_sum_to_1"] = abs(result.total_weight - 1.0) < WEIGHT_TOLERANCE
    
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logger.info("Running allocation acceptance tests...")
    for scenario, passed in test_allocation_scenarios().items():
        logger.info("  %s: %s", "✓ PASS" if passed else "✗ FAIL", scenario)

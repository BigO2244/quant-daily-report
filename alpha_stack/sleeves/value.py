"""
Alpha Stack — Value Sleeve
=============================
Multi-metric value composite sleeve using PIT-safe fundamentals from SEC EDGAR.

STATUS: ACTIVE (ENABLE_VALUE_SLEEVE=true in alpha_stack.yaml)
    Value sleeve is now enabled with PIT-safe fundamentals.

Signals:
    earnings_yield      — net_income (TTM) / market_cap
    fcf_yield           — (operating_cf - capex, TTM) / market_cap
    book_to_price       — stockholders_equity / market_cap
    shareholder_yield   — ⚠️ UNAVAILABLE (requires dividend/buyback data not in SEC EDGAR)

Composite (cross-sectional z-scores):
    S_value = 0.35*z(EY) + 0.25*z(FCFY) + 0.20*z(B/P)
              (shareholder_yield weight redistributed pro-rata to other factors)

Entry: score >= 75
Hold:  score >= 60
Exit:  score < 50

PIT safety:
    - All fundamentals are fetched from SEC EDGAR using `filed` date (not period-end)
    - Includes realistic filing lags (40-90 days post period-end)
    - Price data is sourced from PricesDataStore (as-of-date safe)

Limitation in v1:
    - Shareholder yield unavailable (no dividend/buyback data in SEC EDGAR for all companies)
    - Coverage limited to companies with SEC filings (public US companies)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import pandas as pd

from alpha_stack.sleeves.base import SleeveBase, SleeveOutput, HoldState
from alpha_stack._config_loader import get_flag, get_section

logger = logging.getLogger(__name__)

_ENABLED_FLAG = "ENABLE_VALUE_SLEEVE"

_DEFAULTS = {
    "w_earnings_yield": 0.40,      # Increased weight (was 0.35)
    "w_fcf_yield": 0.30,           # Increased weight (was 0.25)
    "w_book_to_price": 0.30,       # Increased weight (was 0.20)
    "w_shareholder_yield": 0.00,   # Unavailable; disabled
    "enter_score": 75.0,
    "hold_score": 60.0,
    "exit_score": 50.0,
    "top_n": 15,
    "position_cap": 0.10,
    "min_position": 0.02,
    "min_price": 5.0,
}


class ValueSleeve(SleeveBase):
    """
    Value sleeve — now enabled with PIT-safe SEC EDGAR fundamentals.
    """

    def __init__(self, config: Optional[dict] = None) -> None:
        yaml_cfg = (get_section("sleeves") or {}).get("value", {})
        merged = {**_DEFAULTS, **yaml_cfg, **(config or {})}
        super().__init__(merged)
        self._last_diagnostics: Dict[str, Any] = {}

    @property
    def name(self) -> str:
        return "value"

    def required_features(self) -> List[str]:
        return [
            "ticker", "close", "sector",
            "earnings_yield", "fcf_yield", "book_to_price",
        ]

    @staticmethod
    def _is_enabled() -> bool:
        """Check if Value sleeve is enabled via flag."""
        return get_flag(_ENABLED_FLAG, False)

    def run(self, data, regime_context, risk_budget=1.0, as_of_date=None):
        """
        Override run to check feature flag first.
        If flag is off, return active=False immediately.
        """
        if not self._is_enabled():
            date_str = as_of_date or (
                str(data["as_of_date"].iloc[0])[:10]
                if "as_of_date" in data.columns and len(data) > 0
                else "unknown"
            )
            return SleeveOutput(
                sleeve_name=self.name,
                as_of_date=date_str,
                active=False,
                reason=f"{_ENABLED_FLAG} is false",
            )
        # Flag is on: proceed with normal pipeline
        return super().run(data, regime_context, risk_budget, as_of_date)

    def eligibility_filter(self, data: pd.DataFrame) -> pd.DataFrame:
        """Apply hard eligibility gates (price, value factors available)."""
        if not get_flag(_ENABLED_FLAG, False):
            return pd.DataFrame(columns=data.columns)

        df = data.copy()
        n_before = len(df)

        if "close" in df.columns:
            df = df[df["close"] >= self._cfg["min_price"]]

        # Require at least one value factor to be non-null
        # (shareholder_yield is NOT included as it's unavailable in SEC EDGAR)
        factor_cols = ["earnings_yield", "fcf_yield", "book_to_price"]
        available = [c for c in factor_cols if c in df.columns]
        if available:
            df = df[df[available].notna().any(axis=1)]

        self._last_diagnostics["n_before_eligibility"] = n_before
        self._last_diagnostics["n_eligible"] = len(df)
        return df.reset_index(drop=True)

    def score_universe(self, data: pd.DataFrame) -> pd.DataFrame:
        """Compute cross-sectional value score."""
        df = data.copy()
        if not get_flag(_ENABLED_FLAG, False) or data.empty:
            df["score"] = 0.0
            return df

        # Cross-sectional z-scores (no shareholder_yield; others adjusted)
        factor_weights = {
            "earnings_yield": self._cfg["w_earnings_yield"],
            "fcf_yield": self._cfg["w_fcf_yield"],
            "book_to_price": self._cfg["w_book_to_price"],
            # shareholder_yield: 0.0 (unavailable)
        }

        composite = pd.Series(0.0, index=df.index)
        for col, weight in factor_weights.items():
            if col in df.columns:
                if "sector" in df.columns:
                    z = df.groupby("sector")[col].transform(_zscore_safe)
                else:
                    z = _zscore_safe(df[col])
                composite += weight * z.fillna(0)

        df["raw_composite"] = composite
        df["score"] = composite.rank(pct=True) * 100
        return df

    def select_candidates(self, scores: pd.DataFrame) -> pd.DataFrame:
        """Apply entry/hold/exit thresholds."""
        df = scores.copy()
        if df.empty:
            return df

        enter = float(self._cfg["enter_score"])
        hold  = float(self._cfg["hold_score"])
        top_n = int(self._cfg["top_n"])

        df["hold_state"] = df["score"].apply(
            lambda s: HoldState.ENTER.value if s >= enter
                      else (HoldState.HOLD.value if s >= hold
                            else HoldState.EXIT.value)
        )
        candidates = df[df["score"] >= hold].copy()
        candidates["candidate_flag"] = True
        self._last_diagnostics["n_candidates"] = len(candidates)
        return candidates.sort_values("score", ascending=False).head(top_n).reset_index(drop=True)

    def target_weights(self, candidates, regime_context, risk_budget=1.0):
        """Assign equal weights within candidates, with position caps."""
        df = candidates.copy()
        n = len(df)
        if n == 0:
            df["provisional_weight"] = pd.Series(dtype=float)
            return df

        # Equal weight for value sleeve (spec: equal weight within top-decile)
        w = pd.Series(1.0 / n, index=df.index)
        capped = self._iterative_cap_weights(
            w, cap=float(self._cfg["position_cap"]),
            floor=float(self._cfg["min_position"])
        )
        df["provisional_weight"] = (capped * risk_budget).round(6)
        return df

    def diagnostics(self) -> Dict[str, Any]:
        """Return diagnostic information."""
        return dict(self._last_diagnostics)


def _zscore_safe(s: pd.Series) -> pd.Series:
    """Safe z-score: returns 0 if std is 0."""
    mu, sigma = s.mean(), s.std()
    if sigma == 0 or pd.isna(sigma):
        return pd.Series(0.0, index=s.index)
    return (s - mu) / sigma

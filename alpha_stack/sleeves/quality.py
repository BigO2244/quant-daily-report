"""
Alpha Stack — Quality Sleeve
================================
Profitability and balance-sheet quality composite.

⚠️  STATUS: CONFIG-DISABLED (ENABLE_QUALITY_SLEEVE=false in alpha_stack.yaml)
    Interface implemented; scoring returns empty until PIT fundamentals wired.
    See alpha_stack/datastore/fundamentals.py for enablement TODO.

Signals (when enabled):
    roe              — Return on Equity
    roic             — Return on Invested Capital
    net_leverage     — Net Debt / EBITDA (lower = better)
    margin_volatility — 3yr gross margin std (lower = better)
    accrual_ratio    — Operating accruals / NOA (lower = better)

Composite:
    S_quality = 0.30*z(ROE) + 0.25*z(ROIC)
              - 0.20*z(NetLev) - 0.15*z(MarginVol) - 0.10*z(Accruals)

Entry: score >= 70
Hold:  score >= 55
Exit:  score < 45
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import pandas as pd

from alpha_stack.sleeves.base import SleeveBase, SleeveOutput, HoldState
from alpha_stack._config_loader import get_flag, get_section

logger = logging.getLogger(__name__)

_ENABLED_FLAG = "ENABLE_QUALITY_SLEEVE"

_DEFAULTS = {
    "w_roe": 0.30,
    "w_roic": 0.25,
    "w_net_leverage": 0.20,
    "w_margin_vol": 0.15,
    "w_accruals": 0.10,
    "enter_score": 70.0,
    "hold_score": 55.0,
    "exit_score": 45.0,
    "top_n": 15,
    "position_cap": 0.10,
    "min_position": 0.02,
    "min_price": 5.0,
}


class QualitySleeve(SleeveBase):
    """Quality sleeve — disabled until PIT-safe fundamentals are wired."""

    def __init__(self, config: Optional[dict] = None) -> None:
        yaml_cfg = (get_section("sleeves") or {}).get("quality", {})
        merged = {**_DEFAULTS, **yaml_cfg, **(config or {})}
        super().__init__(merged)
        self._last_diagnostics: Dict[str, Any] = {}

    @property
    def name(self) -> str:
        return "quality"

    def required_features(self) -> List[str]:
        return [
            "ticker", "close", "roe", "roic",
            "net_leverage", "margin_volatility", "accrual_ratio",
        ]

    def eligibility_filter(self, data: pd.DataFrame) -> pd.DataFrame:
        if not get_flag(_ENABLED_FLAG, False):
            return pd.DataFrame(columns=data.columns)
        df = data.copy()
        if "close" in df.columns:
            df = df[df["close"] >= self._cfg["min_price"]]
        return df.reset_index(drop=True)

    def score_universe(self, data: pd.DataFrame) -> pd.DataFrame:
        df = data.copy()
        if not get_flag(_ENABLED_FLAG, False) or data.empty:
            df["score"] = 0.0
            return df

        composite = pd.Series(0.0, index=df.index)
        positive_factors = [("roe", "w_roe"), ("roic", "w_roic")]
        negative_factors = [("net_leverage", "w_net_leverage"),
                            ("margin_volatility", "w_margin_vol"),
                            ("accrual_ratio", "w_accruals")]

        for col, w_key in positive_factors:
            if col in df.columns:
                composite += float(self._cfg[w_key]) * _zscore_safe(df[col]).fillna(0)

        for col, w_key in negative_factors:
            if col in df.columns:
                composite -= float(self._cfg[w_key]) * _zscore_safe(df[col]).fillna(0)

        df["raw_composite"] = composite
        df["score"] = composite.rank(pct=True) * 100
        return df

    def select_candidates(self, scores: pd.DataFrame) -> pd.DataFrame:
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
        return candidates.sort_values("score", ascending=False).head(top_n).reset_index(drop=True)

    def target_weights(self, candidates, regime_context, risk_budget=1.0):
        df = candidates.copy()
        n = len(df)
        if n == 0:
            df["provisional_weight"] = pd.Series(dtype=float)
            return df

        # Equal weight with optional +25% overweight for top quintile
        base = pd.Series(1.0 / n, index=df.index)
        if "score" in df.columns and n >= 5:
            q80 = df["score"].quantile(0.80)
            top = df["score"] >= q80
            base[top] *= 1.25
            base = base / base.sum()

        capped = self._iterative_cap_weights(
            base, cap=float(self._cfg["position_cap"]),
            floor=float(self._cfg["min_position"])
        )
        df["provisional_weight"] = (capped * risk_budget).round(6)
        return df

    def run(self, data, regime_context, risk_budget=1.0, as_of_date=None):
        if not get_flag(_ENABLED_FLAG, False):
            date_str = as_of_date or "unknown"
            return SleeveOutput(
                sleeve_name=self.name,
                as_of_date=date_str,
                active=False,
                reason="ENABLE_QUALITY_SLEEVE=false — PIT fundamentals not yet wired",
            )
        return super().run(data, regime_context, risk_budget, as_of_date)

    def diagnostics(self):
        return dict(self._last_diagnostics)


def _zscore_safe(s: pd.Series) -> pd.Series:
    mu, sigma = s.mean(), s.std()
    if sigma == 0 or pd.isna(sigma):
        return pd.Series(0.0, index=s.index)
    return (s - mu) / sigma

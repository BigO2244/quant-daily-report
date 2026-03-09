"""
Alpha Stack — Trend / Momentum Sleeve
========================================
Implements the Trend sleeve per sleeve_specifications.md.

Signals:
    r12_1  — 12-1 momentum (252d, skip 21d)
    r6_1   — 6-1 momentum  (126d, skip 21d)
    r3_1   — 3-1 momentum  ( 63d, skip 21d)
    trend_flag — 1 if EMA50 > EMA200

Composite:
    S_raw = 0.45*z(r12_1) + 0.30*z(r6_1) + 0.15*z(r3_1) + 0.10*trend_flag
    S_adj = S_raw / max(ATR20_pct, 0.01)   (volatility adjustment)
    Score = percentile_rank(S_adj) * 100   → [0, 100]

Entry:  score >= 70 AND EMA50 > EMA200
Hold:   score >= 55
Exit:   score < 45 OR EMA50 <= EMA200

Sizing: inverse volatility with floor/ceiling.

This sleeve is purely price-based — no fundamentals required.
PIT-safe by construction.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from alpha_stack.sleeves.base import SleeveBase, SleeveOutput, HoldState
from alpha_stack._config_loader import get_section

logger = logging.getLogger(__name__)

# Default parameters (overridden by config)
_DEFAULTS = {
    "w_r12_1": 0.45,
    "w_r6_1": 0.30,
    "w_r3_1": 0.15,
    "w_trend_flag": 0.10,
    "enter_score": 70.0,
    "hold_score": 55.0,
    "exit_score": 45.0,
    "vol_floor": 0.10,
    "vol_cap": 0.60,
    "top_n": 15,
    "position_cap": 0.10,
    "min_position": 0.02,
    "min_price": 5.0,
    "min_adv_shares": 100_000,
}


class TrendSleeve(SleeveBase):
    """
    Trend / Momentum sleeve for Alpha Stack.

    Parameters
    ----------
    config : dict, optional
        Override defaults from alpha_stack.yaml.
    """

    def __init__(self, config: Optional[dict] = None) -> None:
        # Merge YAML config + explicit overrides
        yaml_cfg = (get_section("sleeves") or {}).get("trend", {})
        merged = {**_DEFAULTS, **yaml_cfg, **(config or {})}
        super().__init__(merged)
        self._last_diagnostics: Dict[str, Any] = {}

    @property
    def name(self) -> str:
        return "trend"

    def required_features(self) -> List[str]:
        return [
            "ticker", "close", "ema50", "ema200",
            "trend_flag", "r12_1", "r6_1", "r3_1",
            "z_r12_1", "z_r6_1", "z_r3_1",
            "atr20_pct", "realized_vol_20d",
        ]

    # ------------------------------------------------------------------ #
    # Core pipeline                                                        #
    # ------------------------------------------------------------------ #

    def eligibility_filter(self, data: pd.DataFrame) -> pd.DataFrame:
        """Apply hard eligibility gates (price, volume, data completeness)."""
        df = data.copy()
        n_before = len(df)
        gate_failures: Dict[str, int] = {}

        # Price gate
        if "close" in df.columns:
            mask = df["close"] >= self._cfg["min_price"]
            failed = (~mask).sum()
            if failed:
                gate_failures["low_price"] = int(failed)
            df = df[mask]

        # Volume gate (if available)
        if "volume_sma" in df.columns:
            mask = df["volume_sma"] >= self._cfg["min_adv_shares"]
            failed = (~mask).sum()
            if failed:
                gate_failures["low_volume"] = int(failed)
            df = df[mask]

        # Require valid close and at least one momentum signal
        for col in ["close", "r12_1"]:
            if col in df.columns:
                mask = df[col].notna() & (df[col] != 0)
                failed = (~mask).sum()
                if failed:
                    gate_failures[f"missing_{col}"] = int(failed)
                df = df[mask]

        self._last_diagnostics["n_before_eligibility"] = n_before
        self._last_diagnostics["n_eligible"] = len(df)
        self._last_diagnostics["gate_failures"] = gate_failures

        logger.debug(
            "[TREND] Eligibility: %d → %d (gates: %s)",
            n_before, len(df), gate_failures,
        )
        return df.reset_index(drop=True)

    def score_universe(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Compute raw trend score and normalise to [0, 100] percentile rank.
        """
        df = data.copy()
        n = len(df)

        if n == 0:
            df["score"] = pd.Series(dtype=float)
            return df

        w_r12 = float(self._cfg["w_r12_1"])
        w_r6  = float(self._cfg["w_r6_1"])
        w_r3  = float(self._cfg["w_r3_1"])
        w_tf  = float(self._cfg["w_trend_flag"])

        # Use pre-computed z-scores if available, else compute inline
        z_r12 = df.get("z_r12_1", _zscore(df.get("r12_1", pd.Series([0.0] * n))))
        z_r6  = df.get("z_r6_1",  _zscore(df.get("r6_1",  pd.Series([0.0] * n))))
        z_r3  = df.get("z_r3_1",  _zscore(df.get("r3_1",  pd.Series([0.0] * n))))
        tf    = df.get("trend_flag", pd.Series([0.0] * n))

        raw_score = w_r12 * z_r12 + w_r6 * z_r6 + w_r3 * z_r3 + w_tf * tf

        # Volatility adjustment: S_adj = S_raw / max(ATR20_pct, 0.01)
        atr_pct = df.get("atr20_pct", pd.Series([0.02] * n, index=df.index))
        if hasattr(atr_pct, "fillna"):
            atr_pct = atr_pct.fillna(0.02)
        atr_adj = atr_pct.clip(lower=0.01)
        adj_score = raw_score / atr_adj

        # Percentile rank → [0, 100]
        df["score"] = adj_score.rank(pct=True) * 100

        logger.debug("[TREND] Scored %d tickers; score range [%.1f, %.1f]",
                     n, df["score"].min(), df["score"].max())
        return df

    def select_candidates(self, scores: pd.DataFrame) -> pd.DataFrame:
        """Apply entry threshold and select top-N, enforcing trend flag for entry."""
        df = scores.copy()

        enter_thresh = float(self._cfg["enter_score"])
        hold_thresh  = float(self._cfg["hold_score"])
        exit_thresh  = float(self._cfg["exit_score"])
        top_n        = int(self._cfg["top_n"])

        # Assign hold_state
        def _hold_state(row: pd.Series) -> str:
            score = row.get("score", 0)
            tf = row.get("trend_flag", 0)
            if score >= enter_thresh and tf >= 1:
                return HoldState.ENTER.value
            elif score >= hold_thresh:
                return HoldState.HOLD.value
            elif score >= exit_thresh:
                return HoldState.TRIM.value
            else:
                return HoldState.EXIT.value

        df["hold_state"] = df.apply(_hold_state, axis=1)

        # Candidates: ENTER or HOLD states, score >= enter_thresh, trend_flag required for new entries
        candidate_mask = (
            (df["hold_state"].isin([HoldState.ENTER.value, HoldState.HOLD.value]))
            & (df["score"] >= hold_thresh)
        )
        candidates = df[candidate_mask].copy()
        candidates["candidate_flag"] = True

        # Sort by score descending, take top-N
        candidates = candidates.sort_values("score", ascending=False).head(top_n)

        logger.debug("[TREND] Candidates: %d (from %d eligible)", len(candidates), len(df))
        self._last_diagnostics["n_candidates"] = len(candidates)
        return candidates.reset_index(drop=True)

    def target_weights(
        self,
        candidates: pd.DataFrame,
        regime_context,
        risk_budget: float = 1.0,
    ) -> pd.DataFrame:
        """
        Assign inverse-volatility weights, capped at position_cap.

        Sizing: w_i ∝ 1 / clip(vol_20d, vol_floor, vol_cap)
        """
        df = candidates.copy()
        n = len(df)

        if n == 0:
            df["provisional_weight"] = pd.Series(dtype=float)
            return df

        pos_cap = float(self._cfg["position_cap"])
        vol_floor = float(self._cfg["vol_floor"])
        vol_cap_val = float(self._cfg["vol_cap"])
        min_pos = float(self._cfg["min_position"])

        # Get realized vol; fallback to ATR proxy
        if "realized_vol_20d" in df.columns and df["realized_vol_20d"].notna().any():
            vol = df["realized_vol_20d"].fillna(0.25)
        elif "atr20_pct" in df.columns:
            vol = df["atr20_pct"].fillna(0.02) * np.sqrt(252)  # rough annualise
        else:
            vol = pd.Series([0.20] * n, index=df.index)

        vol = vol.clip(lower=vol_floor, upper=vol_cap_val)
        raw_w = 1.0 / vol
        raw_w = raw_w / raw_w.sum()

        # Apply iterative cap
        capped = self._iterative_cap_weights(raw_w, cap=pos_cap, floor=min_pos)

        # Scale by risk_budget
        df["provisional_weight"] = (capped * risk_budget).round(6)

        logger.debug(
            "[TREND] Weights assigned. Total=%.3f, range=[%.3f, %.3f]",
            df["provisional_weight"].sum(),
            df["provisional_weight"].min(),
            df["provisional_weight"].max(),
        )
        return df

    def diagnostics(self) -> Dict[str, Any]:
        return dict(self._last_diagnostics)


# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

def _zscore(s: pd.Series) -> pd.Series:
    mu, sigma = s.mean(), s.std()
    if sigma == 0 or pd.isna(sigma):
        return pd.Series(0.0, index=s.index)
    return (s - mu) / sigma

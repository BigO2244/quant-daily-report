"""
Alpha Stack — Mean Reversion Sleeve
======================================
Short-term dislocation capture with strict regime gate.

STATUS: CONFIG-DISABLED (ENABLE_MEAN_REVERSION=false by default).
        The sleeve is implemented but off unless explicitly enabled.

Regime gate (required — no override):
    Enabled ONLY when:
      - trend_state in {weak_up, neutral}
      - vol_state   in {calm, normal}
    Disabled immediately on regime gate failure (no dwell time required).

Signals:
    RSI(2)          — Short-term RSI; high RSI = overbought, low = oversold candidate
    Bollinger z-score — (Close - MA20) / (2 * std20); negative = stretched below
    5-day reversal   — 5-day return, negated (lower = stronger MR candidate)
    Volume shock     — Volume spike on a down day (positive signal)

Composite:
    S_mr = -0.35*z(RSI2) - 0.30*z(BB_z) - 0.20*z(r_5d) + 0.15*z(VolShock)
    High score = stronger mean-reversion candidate.

Entry: score >= 80 AND regime gate ON
Hold:  score >= 65 AND regime gate ON
Exit:  score < 55 OR regime gate OFF
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from alpha_stack.sleeves.base import SleeveBase, SleeveOutput, HoldState
from alpha_stack.regime.state_machine import TrendState, VolatilityState
from alpha_stack._config_loader import get_flag, get_section

logger = logging.getLogger(__name__)

_ENABLED_FLAG = "ENABLE_MEAN_REVERSION"

_DEFAULTS = {
    "w_rsi2": 0.35,
    "w_bb_z": 0.30,
    "w_r_5d": 0.20,
    "w_volume_shock": 0.15,
    "enter_score": 80.0,
    "hold_score": 65.0,
    "exit_score": 55.0,
    "top_n": 5,
    "position_cap": 0.05,   # Smaller cap for MR sleeve
    "min_position": 0.01,
    "min_price": 5.0,
    "allowed_trend_states": ["weak_up", "neutral"],
    "allowed_vol_states": ["calm", "normal"],
    "rsi_period": 2,
    "bb_period": 20,
    "reversal_days": 5,
    "volume_lookback": 20,
}


class MeanReversionSleeve(SleeveBase):
    """
    Mean reversion sleeve — disabled by default.
    """

    def __init__(self, config: Optional[dict] = None) -> None:
        yaml_cfg = (get_section("sleeves") or {}).get("mean_reversion", {})
        merged = {**_DEFAULTS, **yaml_cfg, **(config or {})}
        super().__init__(merged)
        self._last_diagnostics: Dict[str, Any] = {}

    @property
    def name(self) -> str:
        return "mean_reversion"

    def required_features(self) -> List[str]:
        return ["ticker", "close", "volume", "high", "low"]

    def _regime_gate_ok(self, regime_context) -> bool:
        """Return True if regime gate is satisfied for MR activation."""
        if regime_context is None:
            return False
        allowed_trend = self._cfg.get("allowed_trend_states", ["weak_up", "neutral"])
        allowed_vol   = self._cfg.get("allowed_vol_states", ["calm", "normal"])
        trend_ok = str(regime_context.trend_state.value) in allowed_trend
        vol_ok   = str(regime_context.vol_state.value) in allowed_vol
        return trend_ok and vol_ok

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

        n = len(df)

        # Compute signals inline (prices already filtered to as_of_date upstream)
        # RSI(2): use last 2 days of price moves
        # Bollinger z-score: (close - MA20) / (2 * std20)
        # 5-day reversal
        # Volume shock: today's volume / avg_volume on down day

        # These signals require per-ticker history, not cross-sectional snapshot.
        # In this architecture, the feature data passed to score_universe should
        # already contain precomputed signal columns. If not, fallback to neutral.

        rsi2 = df.get("rsi_2", pd.Series([50.0] * n, index=df.index))
        bb_z = df.get("bb_zscore", pd.Series([0.0] * n, index=df.index))
        r_5d = df.get("r_5d", pd.Series([0.0] * n, index=df.index))
        vol_shock = df.get("volume_shock", pd.Series([1.0] * n, index=df.index))

        w_rsi  = float(self._cfg["w_rsi2"])
        w_bb   = float(self._cfg["w_bb_z"])
        w_r5   = float(self._cfg["w_r_5d"])
        w_vs   = float(self._cfg["w_volume_shock"])

        # Negate directional factors so high score = strong MR candidate (oversold)
        composite = (
            -w_rsi  * _zscore_safe(rsi2)   # lower RSI = more oversold = higher score
            - w_bb  * _zscore_safe(bb_z)   # more negative z = more stretched = higher score
            - w_r5  * _zscore_safe(r_5d)   # more negative 5d = more oversold = higher score
            + w_vs  * _zscore_safe(vol_shock)  # volume spike = attention = higher score
        )

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
        # Small equal weights for MR sleeve
        w = pd.Series(1.0 / n, index=df.index)
        capped = self._iterative_cap_weights(
            w, cap=float(self._cfg["position_cap"]),
            floor=float(self._cfg["min_position"])
        )
        df["provisional_weight"] = (capped * risk_budget).round(6)
        return df

    def run(self, data, regime_context, risk_budget=1.0, as_of_date=None):
        """Override to check both feature flag and regime gate."""
        date_str = as_of_date or "unknown"

        if not get_flag(_ENABLED_FLAG, False):
            return SleeveOutput(
                sleeve_name=self.name,
                as_of_date=date_str,
                active=False,
                reason="ENABLE_MEAN_REVERSION=false",
            )

        if not self._regime_gate_ok(regime_context):
            logger.debug("[MR] Regime gate not satisfied; sleeve inactive.")
            return SleeveOutput(
                sleeve_name=self.name,
                as_of_date=date_str,
                active=False,
                reason=(
                    f"Regime gate not satisfied: "
                    f"trend={getattr(regime_context, 'trend_state', '?')}, "
                    f"vol={getattr(regime_context, 'vol_state', '?')}"
                ),
            )

        return super().run(data, regime_context, risk_budget, as_of_date)

    def diagnostics(self):
        return dict(self._last_diagnostics)


def _zscore_safe(s: pd.Series) -> pd.Series:
    mu, sigma = s.mean(), s.std()
    if sigma == 0 or pd.isna(sigma):
        return pd.Series(0.0, index=s.index)
    return (s - mu) / sigma


# ------------------------------------------------------------------ #
# Signal computation helpers (called externally to pre-compute cols)  #
# ------------------------------------------------------------------ #

def compute_mr_signals(prices_df: pd.DataFrame, as_of_date=None) -> pd.DataFrame:
    """
    Compute RSI(2), Bollinger z-score, 5-day reversal, and volume shock
    for each ticker in a long-format price DataFrame.

    Parameters
    ----------
    prices_df : DataFrame
        Long-format OHLCV with [date, ticker, open, high, low, close, volume].
    as_of_date : date or str, optional

    Returns
    -------
    DataFrame with one row per ticker and columns:
        ticker, rsi_2, bb_zscore, r_5d, volume_shock
    """
    if isinstance(as_of_date, str):
        as_of_date = pd.Timestamp(as_of_date)

    prices_df = prices_df.copy()
    prices_df["date"] = pd.to_datetime(prices_df["date"])
    if as_of_date is not None:
        prices_df = prices_df[prices_df["date"] <= as_of_date]
    prices_df = prices_df.sort_values(["ticker", "date"])

    records = []
    for ticker, group in prices_df.groupby("ticker"):
        if len(group) < 22:
            continue
        close = group["close"].values
        volume = group["volume"].values if "volume" in group.columns else np.ones(len(group))

        # RSI(2)
        rsi2 = _rsi(close, period=2)

        # Bollinger z-score
        ma20 = np.mean(close[-20:])
        std20 = np.std(close[-20:], ddof=1)
        bb_z = (close[-1] - ma20) / (std20 * 2) if std20 > 0 else 0.0

        # 5-day reversal
        r_5d = float(close[-1] / close[-6] - 1) if len(close) >= 6 else 0.0

        # Volume shock: today vs 20-day avg, only meaningful if negative day
        avg_vol = np.mean(volume[-21:-1]) if len(volume) >= 21 else volume[-1]
        vol_shock = float(volume[-1] / max(avg_vol, 1))
        # Only count as MR signal if it's a down day
        if len(close) >= 2 and close[-1] >= close[-2]:
            vol_shock = 1.0  # neutral on up days

        records.append({
            "ticker": ticker,
            "rsi_2": rsi2,
            "bb_zscore": bb_z,
            "r_5d": r_5d,
            "volume_shock": vol_shock,
        })

    if not records:
        return pd.DataFrame(columns=["ticker", "rsi_2", "bb_zscore", "r_5d", "volume_shock"])
    return pd.DataFrame(records)


def _rsi(closes: np.ndarray, period: int = 2) -> float:
    """Compute RSI for the last bar."""
    if len(closes) < period + 1:
        return 50.0
    diffs = np.diff(closes[-(period + 1):])
    gains = np.where(diffs > 0, diffs, 0.0)
    losses = np.where(diffs < 0, -diffs, 0.0)
    avg_gain = np.mean(gains)
    avg_loss = np.mean(losses)
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1 + rs))

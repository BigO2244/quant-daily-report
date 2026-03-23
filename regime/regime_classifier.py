"""
regime_classifier.py
--------------------
The regime state machine.

Takes the indicator table from regime_indicators.build_indicators()
and produces a daily regime classification across four dimensions,
plus a composite regime label used by the sleeve weight allocator.

All thresholds come from regime_config.py — nothing is hardcoded here.

Output columns (from classify):
    trend_state        : strong_up | weak_up | neutral | weak_down | strong_down
    volatility_state   : calm | normal | elevated | crisis
    breadth_state      : healthy | mixed | deteriorating | washed_out
    macro_state        : risk_on | neutral | risk_off | stress
    composite_regime   : risk_on_trending | neutral_mixed | risk_off_defensive |
                         high_volatility | breadth_washout

Design principle:
    All classification is performed on the *_ewm columns (EWM-smoothed values),
    not on raw indicators. This is the primary hysteresis mechanism.
    Raw columns are retained in the indicators table for diagnostics only.
"""

import logging
import numpy as np
import pandas as pd

from regime.regime_config import TREND, VOLATILITY, BREADTH, MACRO

logger = logging.getLogger(__name__)

# Valid state labels — used for validation and documentation
TREND_STATES      = ["strong_up", "weak_up", "neutral", "weak_down", "strong_down"]
VOLATILITY_STATES = ["calm", "normal", "elevated", "crisis"]
BREADTH_STATES    = ["healthy", "mixed", "deteriorating", "washed_out"]
MACRO_STATES      = ["risk_on", "neutral", "risk_off", "stress"]
COMPOSITE_REGIMES = [
    "risk_on_trending",
    "neutral_mixed",
    "risk_off_defensive",
    "high_volatility",
    "breadth_washout",
]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def classify(indicators: pd.DataFrame) -> pd.DataFrame:
    """
    Classify regime states for each trading day.

    Parameters
    ----------
    indicators : output of regime_indicators.build_indicators()
                 Must contain *_ewm columns for all four dimensions.

    Returns
    -------
    DataFrame indexed by date with columns:
        trend_state, volatility_state, breadth_state, macro_state,
        composite_regime
    """
    _validate_columns(indicators)

    out = pd.DataFrame(index=indicators.index)
    out["trend_state"]      = _classify_trend(indicators)
    out["volatility_state"] = _classify_volatility(indicators)
    out["breadth_state"]    = _classify_breadth(indicators)
    out["macro_state"]      = _classify_macro(indicators)
    out["composite_regime"] = _classify_composite(out)

    _log_state_summary(out)
    return out


# ---------------------------------------------------------------------------
# Dimension classifiers
# Each returns a pd.Series of string state labels indexed by date.
# All inputs are EWM-smoothed columns.
# ---------------------------------------------------------------------------

def _classify_trend(ind: pd.DataFrame) -> pd.Series:
    """
    Five states based on SPY position relative to its MAs and 63-day return.
    Uses EWM-smoothed values of all three inputs.
    """
    vs50   = ind["spy_vs_50d_ewm"]
    vs200  = ind["spy_vs_200d_ewm"]
    ma_rel = ind["ma50_vs_ma200_ewm"]
    ret63  = ind["spy_ret_63d_ewm"]

    neutral_band = TREND["neutral_band_pct"]
    strong_up_r  = TREND["strong_up_return_min"]
    strong_dn_r  = TREND["strong_down_return_max"]

    def _state(row):
        v50, v200, marel, r63 = row
        if pd.isna(v50) or pd.isna(v200) or pd.isna(marel) or pd.isna(r63):
            return "neutral"  # insufficient history → default to neutral

        # strong_up: above both MAs, MA50 > MA200, strong recent return
        if v50 > 0 and marel > 0 and r63 >= strong_up_r:
            return "strong_up"

        # strong_down: below both MAs, MA50 < MA200, strong recent decline
        if v50 < 0 and marel < 0 and r63 <= strong_dn_r:
            return "strong_down"

        # neutral: SPY within ±neutral_band of 200d MA
        if abs(v200) <= neutral_band:
            return "neutral"

        # weak_up / weak_down based on 200d MA position
        if v200 > neutral_band:
            return "weak_up"
        return "weak_down"

    df_in = pd.concat([vs50, vs200, ma_rel, ret63], axis=1)
    return df_in.apply(_state, axis=1).rename("trend_state")


def _classify_volatility(ind: pd.DataFrame) -> pd.Series:
    """
    Four states driven by VIX level, VIX vs its own MA, and spike detection.
    Spike detection uses raw vix_spike_10d (not EWM) — we want to catch actual spikes.
    """
    vix_ewm      = ind["vix_ewm"]
    vix_ma_ratio = ind["vix_vs_ma63_ewm"]
    vix_spike    = ind["vix_spike_10d"]  # intentionally raw

    calm_max    = VOLATILITY["calm_max"]
    normal_max  = VOLATILITY["normal_max"]
    elevated_max= VOLATILITY["elevated_max"]
    ma_ratio    = VOLATILITY["elevated_ma_ratio"]
    spike_pct   = VOLATILITY["crisis_spike_pct"]

    def _state(row):
        vix, vma_ratio, spike = row
        if pd.isna(vix):
            return "normal"

        # Crisis: absolute VIX > 28 OR spike > 40% in 10 days
        if vix > elevated_max:
            return "crisis"
        if pd.notna(spike) and spike > spike_pct:
            return "crisis"

        # Elevated: VIX 20-28 OR VIX significantly above its own MA
        if vix > normal_max:
            return "elevated"
        if pd.notna(vma_ratio) and vma_ratio > (ma_ratio - 1):
            return "elevated"

        # Calm: VIX < 15 AND below its MA
        if vix < calm_max and (pd.isna(vma_ratio) or vma_ratio <= 0):
            return "calm"

        return "normal"

    df_in = pd.concat([vix_ewm, vix_ma_ratio, vix_spike], axis=1)
    return df_in.apply(_state, axis=1).rename("volatility_state")


def _classify_breadth(ind: pd.DataFrame) -> pd.Series:
    """
    Four states based on % of universe stocks above their 200-day MA.
    """
    pct_ewm = ind["pct_above_200d_ewm"]

    healthy_min      = BREADTH["healthy_min"]
    mixed_min        = BREADTH["mixed_min"]
    deteriorating_min= BREADTH["deteriorating_min"]

    def _state(pct):
        if pd.isna(pct):
            return "mixed"
        if pct >= healthy_min:
            return "healthy"
        if pct >= mixed_min:
            return "mixed"
        if pct >= deteriorating_min:
            return "deteriorating"
        return "washed_out"

    return pct_ewm.map(_state).rename("breadth_state")


def _classify_macro(ind: pd.DataFrame) -> pd.Series:
    """
    Four states based on yield curve slope and HY credit spreads.
    Both signals must agree to flip from neutral — disagreement = neutral.
    """
    curve_ewm = ind["yield_curve_2s10s_ewm"]
    hy_ewm    = ind["hy_oas_ewm"]

    curve_steep   = MACRO["curve_steep_min"]
    curve_inverted= MACRO["curve_inverted_max"]
    curve_deep    = MACRO["curve_deep_invert"]
    hy_tight      = MACRO["hy_tight_max"]
    hy_normal     = MACRO["hy_normal_max"]
    hy_wide       = MACRO["hy_wide_max"]

    def _curve_signal(curve):
        if pd.isna(curve):
            return "neutral"
        if curve >= curve_steep:
            return "risk_on"
        if curve <= curve_deep:
            return "stress"
        if curve < curve_inverted:
            return "risk_off"
        return "neutral"

    def _hy_signal(hy):
        if pd.isna(hy):
            return "neutral"
        if hy < hy_tight:
            return "risk_on"
        if hy > hy_wide:
            return "stress"
        if hy > hy_normal:
            return "risk_off"
        return "neutral"

    def _state(row):
        curve, hy = row
        cs = _curve_signal(curve)
        hs = _hy_signal(hy)

        # Unanimous signals → use that signal
        if cs == hs:
            return cs
        # Either signal showing stress → stress
        if cs == "stress" or hs == "stress":
            return "stress"
        # Risk-off agreement even if one is neutral
        if cs == "risk_off" or hs == "risk_off":
            return "risk_off"
        # Risk-on only if both agree
        if cs == "risk_on" and hs == "risk_on":
            return "risk_on"
        return "neutral"

    df_in = pd.concat([curve_ewm, hy_ewm], axis=1)
    return df_in.apply(_state, axis=1).rename("macro_state")


# ---------------------------------------------------------------------------
# Composite regime
# Combines the four dimension states into a single regime label.
# Priority order: high_volatility > breadth_washout > risk_off_defensive >
#                 risk_on_trending > neutral_mixed
# ---------------------------------------------------------------------------

def _classify_composite(states: pd.DataFrame) -> pd.Series:
    """
    Map four dimension states to a single composite regime label.

    Priority rules (applied top to bottom — first match wins):
    1. high_volatility   : volatility = crisis
    2. breadth_washout   : breadth = washed_out
    3. risk_off_defensive: macro = stress
                           OR trend in {weak_down, strong_down} AND macro != risk_on
                           OR vol = elevated AND trend in {weak_down, strong_down}
                           OR vol = elevated AND trend = neutral AND breadth in {mixed, deteriorating}
                              AND macro in {neutral, risk_off, stress}
    4. risk_on_trending  : trend in {strong_up, weak_up} AND macro in {risk_on, neutral}
                           AND volatility in {calm, normal} AND breadth in {healthy, mixed}
    5. neutral_mixed     : everything else
    """
    def _composite(row):
        trend = row["trend_state"]
        vol   = row["volatility_state"]
        breadth = row["breadth_state"]
        macro = row["macro_state"]

        # Rule 1: crisis volatility overrides everything
        if vol == "crisis":
            return "high_volatility"

        # Rule 2: washed-out breadth signals potential mean-reversion opportunity
        if breadth == "washed_out":
            return "breadth_washout"

        # Rule 3: macro stress, or any downtrend where macro isn't actively supportive
        if macro == "stress":
            return "risk_off_defensive"
        # Any downtrend with non-risk_on macro = risk-off (incl. weak_down + neutral)
        # Catches gradual deterioration (2018 Q4) where macro lags the trend turn
        if trend in ("weak_down", "strong_down") and macro != "risk_on":
            return "risk_off_defensive"
        # Elevated volatility + downtrend = risk-off even when macro is risk_on
        # Catches high-vol sell-offs (2011 Euro Crisis post-spike)
        if vol == "elevated" and trend in ("weak_down", "strong_down"):
            return "risk_off_defensive"
        # Slow deterioration: trend has not fully rolled into weak_down yet, but
        # elevated vol + weakening breadth + non-supportive macro is already defensive.
        # This is the key 2018 Q4 pattern.
        if (
            vol == "elevated"
            and trend == "neutral"
            and breadth in ("mixed", "deteriorating")
            and macro in ("neutral", "risk_off", "stress")
        ):
            return "risk_off_defensive"

        # Rule 4: constructive conditions on all dimensions
        trend_ok   = trend in ("strong_up", "weak_up")
        macro_ok   = macro in ("risk_on", "neutral")
        vol_ok     = vol in ("calm", "normal")
        breadth_ok = breadth in ("healthy", "mixed")
        if trend_ok and macro_ok and vol_ok and breadth_ok:
            return "risk_on_trending"

        return "neutral_mixed"

    return states.apply(_composite, axis=1).rename("composite_regime")


# ---------------------------------------------------------------------------
# Validation and diagnostics
# ---------------------------------------------------------------------------

def _validate_columns(ind: pd.DataFrame) -> None:
    required_ewm = [
        "spy_vs_50d_ewm", "spy_vs_200d_ewm", "ma50_vs_ma200_ewm", "spy_ret_63d_ewm",
        "vix_ewm", "vix_vs_ma63_ewm", "vix_spike_10d",
        "pct_above_200d_ewm",
        "yield_curve_2s10s_ewm", "hy_oas_ewm",
    ]
    missing = [c for c in required_ewm if c not in ind.columns]
    if missing:
        raise ValueError(
            f"Indicator table missing required columns: {missing}\n"
            f"Available: {list(ind.columns)}"
        )


def _log_state_summary(out: pd.DataFrame) -> None:
    total = len(out)
    if total == 0:
        return
    logger.info("Regime classification complete: %d trading days", total)
    for col in ["trend_state", "volatility_state", "breadth_state", "macro_state", "composite_regime"]:
        counts = out[col].value_counts()
        pcts   = (counts / total * 100).round(1)
        summary = ", ".join(f"{s}={pcts.get(s, 0)}%" for s in counts.index)
        logger.info("  %-22s %s", col + ":", summary)

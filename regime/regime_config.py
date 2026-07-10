"""
regime_config.py
----------------
All regime classifier parameters in one place.
Tune spans and thresholds here; never hardcode them in logic modules.

EWM spans are in trading days (1 day = 1 observation).
Rule of thumb: span=10 ~ 2 weeks, span=20 ~ 1 month.
"""

# ---------------------------------------------------------------------------
# EWM smoothing spans (trading days)
# Applied to raw indicator values before threshold comparison.
# Higher span = more lag, more stability. Lower = more responsive, more noise.
# ---------------------------------------------------------------------------
EWM_SPANS = {
    "trend":      5,   # SPY MA distances and momentum
    "volatility": 5,    # VIX reacts fast; don't over-smooth
    "breadth":    15,   # Breadth metrics are inherently noisy
    "macro":      20,   # Macro indicators move slowly; smooth aggressively
}

# ---------------------------------------------------------------------------
# Trend dimension thresholds
# Computed on SPY price vs its moving averages + 63-day return.
# ---------------------------------------------------------------------------
TREND = {
    # strong_up: SPY > 50d AND 50d > 200d AND 63d return > threshold
    "strong_up_return_min":   0.05,   # +5% over 63 trading days

    # neutral band: SPY within this % of 200d MA in either direction
    "neutral_band_pct":       0.02,   # ±2%

    # strong_down: SPY < 50d AND 50d < 200d AND 63d return < threshold
    "strong_down_return_max": -0.05,  # -5% over 63 trading days
}

# ---------------------------------------------------------------------------
# Volatility dimension thresholds (VIX levels)
# ---------------------------------------------------------------------------
VOLATILITY = {
    "calm_max":           15.0,   # VIX < 15 = calm
    "normal_max":         23.0,   # VIX 15-23 = normal  (raised from 20 — post-crisis 20-23 VIX is normal, not elevated)
    "elevated_max":       40.0,   # VIX 23-40 = elevated (raised from 28 — reserve "crisis" for acute spikes like COVID/GFC; 2011 VIX EWM peaked ~39)
    # VIX > 35 = crisis (no explicit threshold needed; it's the else case)

    # Secondary signal: VIX vs its own 63-day MA
    "elevated_ma_ratio":  1.20,   # VIX > 63d MA by 20% triggers elevated (even if raw < 28)

    # Crisis spike trigger: VIX up >40% in 10 days
    "crisis_spike_days":  10,
    "crisis_spike_pct":   0.40,
}

# ---------------------------------------------------------------------------
# Breadth dimension thresholds
# % of universe stocks above their 200-day MA
# ---------------------------------------------------------------------------
BREADTH = {
    "healthy_min":        0.60,   # > 60% above 200d = healthy
    "mixed_min":          0.40,   # 40-60% = mixed
    "deteriorating_min":  0.25,   # 25-40% = deteriorating
    # < 25% = washed_out (potential mean-reversion signal)
}

# ---------------------------------------------------------------------------
# Macro dimension thresholds
# Yield curve slope (2s10s) and HY credit spreads
# ---------------------------------------------------------------------------
MACRO = {
    # Yield curve: T10Y2Y from FRED (10Y minus 2Y, in percentage points)
    "curve_steep_min":    1.00,   # > 100bps = steep (risk-on)
    "curve_flat_min":     0.00,   # 0-100bps = flat
    "curve_inverted_max": 0.00,   # < 0 = inverted (risk-off signal)
    "curve_deep_invert":  -0.50,  # < -50bps = deeply inverted

    # HY OAS: BAMLH0A0HYM2 from FRED (option-adjusted spread, in bps)
    "hy_tight_max":       350,    # < 350bps = tight / risk-on
    "hy_normal_max":      500,    # 350-500bps = normal
    "hy_wide_max":        700,    # 500-700bps = wide / risk-off
    # > 700bps = stress
}

# ---------------------------------------------------------------------------
# Composite regime → sleeve weight mapping
# Rows = composite regime label. Columns = sleeve allocation %.
# Must sum to 100 per row.
# These are starting hypotheses — validate before trusting.
# ---------------------------------------------------------------------------
REGIME_WEIGHTS = {
    #                        Trend  Value  Quality  MeanRev  Cash
    "risk_on_trending":     (55,    18,    15,      10,      2),
    "neutral_mixed":        (38,    24,    20,      13,      5),
    "risk_off_defensive":   (15,    28,    32,      10,      15),
    "high_volatility":      (10,    20,    28,      7,       35),
    "breadth_washout":      (20,    15,    20,      40,      5),
}

# Column order for the weight tuples above
WEIGHT_COLS = ["trend", "value", "quality", "mean_reversion", "cash"]

# ---------------------------------------------------------------------------
# Weight transition smoothing
# Prevent large turnover spikes when regime shifts.
# blend_alpha: each day, target = alpha * new_target + (1-alpha) * prior_weight
# Converges to new regime in ~1/alpha days (alpha=0.20 → ~5 days)
# ---------------------------------------------------------------------------
TRANSITION = {
    "blend_alpha":              0.20,   # daily blend toward new regime weights
    "min_rebalance_threshold":  0.03,   # ignore weight changes < 3% of portfolio
}

# ---------------------------------------------------------------------------
# FRED series IDs
# ---------------------------------------------------------------------------
FRED_SERIES = {
    "yield_curve_2s10s":  "T10Y2Y",         # 10Y minus 2Y Treasury spread
    "hy_oas":             "BAMLH0A0HYM2",   # HY OAS (bps)
}

# ---------------------------------------------------------------------------
# yfinance tickers used by regime layer
# ---------------------------------------------------------------------------
REGIME_TICKERS = {
    "spy":   "SPY",
    "vix":   "^VIX",
}

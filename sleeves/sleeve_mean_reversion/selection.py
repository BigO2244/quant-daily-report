"""
sleeves/sleeve_mean_reversion/selection.py
-------------------------------------------
Cross-sectional mean-reversion selection for the Mean Reversion sleeve.

Thesis:
    Stocks that have become technically oversold relative to their own
    recent price history tend to revert. This sleeve fades short-term
    dislocations within the universe — not secular downtrends.

    Key safety filter: only select mean-reversion candidates that are
    still above their 200-day MA (avoid catching falling knives).

Signals:
  - RSI(14)         — cross-sectional rank of oversold degree
  - Price Z-score   — standard deviations below 20d / 60d SMA
  - Bollinger %B    — position below lower Bollinger band
  - 1-month reversal— recent losers (inverted)

Composite score = weighted rank of all four signals.
Highest scores = most oversold stocks relative to their own history.

Position sizing: inverse-vol weighted (higher-vol names get smaller sizes
because mean-reversion trades carry more gap risk).

Output format mirrors sleeve_1/selection.py so portfolio_alloc.py works unchanged.

Breadth gate:
    When market breadth is unhealthy (breadth_state in {deteriorating,
    washed_out} OR composite_regime in {risk_off_defensive, high_volatility,
    breadth_washout}), mean-reversion signals are suppressed by halving the
    number of candidates returned.  Oversold stocks in weak-breadth regimes
    can continue falling; the gate reduces but does not eliminate exposure so
    the regime allocator retains final authority over sleeve weight.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional

import numpy as np
import pandas as pd

from core.portfolio_alloc import SleeveOutput, create_sleeve_output
from sleeves.sleeve_mean_reversion.indicators import (
    avg_volume,
    bollinger_pct_b,
    price_zscore,
    realized_volatility,
    rsi,
    short_term_reversal,
)

logger = logging.getLogger(__name__)

SLEEVE_NAME = "sleeve_mean_reversion"

# ── Configuration ──────────────────────────────────────────────────────
TOP_N_DEFAULT = 10

# RSI oversold gate: only consider stocks with RSI below this level
RSI_OVERSOLD_MAX = 45.0      # liberal threshold — we score, not hard-cut below 30

# Z-score gate: only consider stocks with recent dislocation
ZSCORE_MAX = 0.5             # must be within 0.5 std of or below the 20d SMA

# Uptrend gate: do not enter mean-reversion on stocks below 200d SMA
# This prevents catching secular downtrends disguised as oversold
REQUIRE_ABOVE_200D = True

# Liquidity gates — consistent with other sleeves
GATE_MIN_PRICE      = 5.0
GATE_MIN_AVG_VOLUME = 100_000

# Composite score weights (must sum to 1.0)
W_RSI        = 0.30   # RSI — classic oversold measure
W_ZSCORE_20D = 0.30   # 20d price z-score — short dislocation
W_ZSCORE_60D = 0.20   # 60d price z-score — medium dislocation
W_BB_PCTB    = 0.10   # Bollinger %B — relative band position
W_STR        = 0.10   # 1-month reversal — raw return signal

assert abs(W_RSI + W_ZSCORE_20D + W_ZSCORE_60D + W_BB_PCTB + W_STR - 1.0) < 1e-6

# Inverse-vol position sizing caps
IVOL_FLOOR        = 0.05
IVOL_CAP          = 1.50
MAX_SINGLE_WEIGHT = 0.35    # tighter cap than momentum — MR carries more gap risk
MIN_SINGLE_WEIGHT = 0.02

# ── Breadth gate ────────────────────────────────────────────────────────
# breadth_state values that indicate unhealthy market internals.
# "deteriorating" and "washed_out" both increase false-positive risk for MR
# because oversold names can remain oversold (or become more oversold) when
# a majority of stocks are already in downtrends.
UNHEALTHY_BREADTH_STATES = frozenset({"deteriorating", "washed_out"})

# Composite regimes that imply unhealthy breadth even if breadth_state is
# not individually available (provides a fallback check path).
UNHEALTHY_COMPOSITE_REGIMES = frozenset({
    "risk_off_defensive",
    "high_volatility",
    "breadth_washout",
})

# When the gate fires, reduce top_n to this fraction of the requested value.
# 0.5 = take only the top half — tightest signals only, not a hard block.
BREADTH_GATE_TOP_N_FRACTION = 0.5


# ── Breadth gate helper ─────────────────────────────────────────────────

def _breadth_gate_is_active(breadth_gate: Optional[Dict]) -> bool:
    """
    Return True if the supplied regime state indicates unhealthy breadth.

    Parameters
    ----------
    breadth_gate : dict containing regime state keys (breadth_state,
                   composite_regime), typically regime_result.today from
                   the regime engine.  None → gate is inactive.

    Logic:
        1. If breadth_state is in UNHEALTHY_BREADTH_STATES → active.
        2. Else if composite_regime is in UNHEALTHY_COMPOSITE_REGIMES → active.
        3. Otherwise → inactive.

    Returns False (gate inactive) on any missing / unexpected input so
    the gate never accidentally suppresses output due to data absence.
    """
    if not breadth_gate or not isinstance(breadth_gate, dict):
        return False

    breadth_state = breadth_gate.get("breadth_state")
    if breadth_state in UNHEALTHY_BREADTH_STATES:
        return True

    composite = breadth_gate.get("composite_regime")
    if composite in UNHEALTHY_COMPOSITE_REGIMES:
        return True

    return False


# ── Signal builder ─────────────────────────────────────────────────────

def build_mean_reversion_signals(prices: pd.DataFrame) -> pd.DataFrame:
    """
    Compute all Mean Reversion sleeve signals from raw OHLCV prices.

    Parameters
    ----------
    prices : Long-form DataFrame with columns: date, ticker, open, high, low,
             close, volume.

    Returns
    -------
    DataFrame with all indicator columns appended.
    Requires at least 60 trading days of history per ticker for full signal coverage.
    """
    df = prices.copy()
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values(['ticker', 'date'])

    df = rsi(df)
    df = price_zscore(df)
    df = bollinger_pct_b(df)
    df = short_term_reversal(df)
    df = realized_volatility(df)
    df = avg_volume(df)

    # 200-day SMA for uptrend gate
    df['sma_200'] = df.groupby('ticker')['close'].transform(
        lambda x: x.rolling(200, min_periods=100).mean()
    )
    df['above_200d'] = df['close'] > df['sma_200']

    return df


# ── Selection ─────────────────────────────────────────────────────────

def select_and_weight(
    signals:      pd.DataFrame,
    asof_date:    Optional[str] = None,
    top_n:        int = TOP_N_DEFAULT,
    breadth_gate: Optional[Dict] = None,
) -> pd.DataFrame:
    """
    Select the most oversold names and compute inverse-vol target weights.

    Parameters
    ----------
    signals      : Output of build_mean_reversion_signals().
    asof_date    : Snapshot date. Defaults to the most recent date in signals.
    top_n        : Maximum number of positions to hold.
    breadth_gate : Optional regime-state dict (regime_result.today) used to
                   fire the healthy-breadth gate.  When breadth is unhealthy
                   (breadth_state in {deteriorating, washed_out} OR composite
                   regime in {risk_off_defensive, high_volatility,
                   breadth_washout}), top_n is reduced to the top 50% of
                   candidates.  Pass None to disable the gate entirely.

    Returns
    -------
    DataFrame with columns:
        ticker, target_weight, sleeve, score, rank, reason,
        realized_vol (if available), sector (if available)
    """
    if signals is None or signals.empty:
        return pd.DataFrame()

    if asof_date is None:
        asof_date = signals['date'].max()
    asof_date = pd.to_datetime(asof_date)

    snap = signals[signals['date'] == asof_date].copy()
    if snap.empty:
        logger.warning("[MR_SELECT] No signals for date %s", asof_date)
        return pd.DataFrame()

    # ── Uptrend gate ────────────────────────────────────────────────
    eligible = snap.copy()
    if REQUIRE_ABOVE_200D and 'above_200d' in eligible.columns:
        n_before = len(eligible)
        eligible = eligible[eligible['above_200d'].fillna(False)]
        logger.debug(
            "[MR_SELECT] Uptrend gate: %d → %d tickers", n_before, len(eligible)
        )

    # ── Price & liquidity gates ─────────────────────────────────────
    if 'close' in eligible.columns:
        eligible = eligible[eligible['close'].fillna(0) >= GATE_MIN_PRICE]

    if 'avg_volume_20d' in eligible.columns:
        eligible = eligible[eligible['avg_volume_20d'].fillna(0) >= GATE_MIN_AVG_VOLUME]

    # ── Require some degree of oversold-ness ────────────────────────
    if 'rsi_14' in eligible.columns:
        eligible = eligible[eligible['rsi_14'].fillna(50) <= RSI_OVERSOLD_MAX]

    if 'zscore_20d' in eligible.columns:
        eligible = eligible[eligible['zscore_20d'].fillna(0) <= ZSCORE_MAX]

    if eligible.empty:
        logger.info("[MR_SELECT] No tickers pass mean-reversion gates on %s", asof_date.date())
        return pd.DataFrame()

    # ── Cross-sectional scoring (lower = more oversold = better rank) ─
    def pct_rank_lower_better(col: str) -> pd.Series:
        """Rank so that lower values get higher scores (more oversold = better)."""
        if col in eligible.columns:
            return eligible[col].rank(pct=True, na_option='top', ascending=True) * 100
        return pd.Series(50.0, index=eligible.index)

    rsi_rank    = pct_rank_lower_better('rsi_14')
    z20_rank    = pct_rank_lower_better('zscore_20d')
    z60_rank    = pct_rank_lower_better('zscore_60d')
    bb_rank     = pct_rank_lower_better('bb_pct_b')
    str_rank    = pct_rank_lower_better('ret_21d_rev')

    eligible['score'] = (
        W_RSI        * rsi_rank
        + W_ZSCORE_20D * z20_rank
        + W_ZSCORE_60D * z60_rank
        + W_BB_PCTB    * bb_rank
        + W_STR        * str_rank
    )

    # ── Breadth gate ─────────────────────────────────────────────────
    gate_active = _breadth_gate_is_active(breadth_gate)
    effective_top_n = top_n
    if gate_active:
        effective_top_n = max(1, int(top_n * BREADTH_GATE_TOP_N_FRACTION))
        logger.info(
            "[MR_SELECT] Breadth gate ACTIVE (breadth=%s composite=%s): "
            "top_n %d → %d",
            (breadth_gate or {}).get("breadth_state", "unknown"),
            (breadth_gate or {}).get("composite_regime", "unknown"),
            top_n,
            effective_top_n,
        )

    # ── Select top N ────────────────────────────────────────────────
    selected = eligible.nlargest(effective_top_n, 'score').copy()
    selected = selected.reset_index(drop=True)
    selected['rank'] = range(1, len(selected) + 1)

    if selected.empty:
        return pd.DataFrame()

    # ── Inverse-vol weighting ────────────────────────────────────────
    if 'realized_vol' in selected.columns and selected['realized_vol'].notna().any():
        inv_vol = 1.0 / selected['realized_vol'].fillna(0.20).clip(
            lower=IVOL_FLOOR, upper=IVOL_CAP
        )
    else:
        inv_vol = pd.Series(1.0, index=selected.index)

    raw_weights = inv_vol / inv_vol.sum()
    raw_weights = raw_weights.clip(upper=MAX_SINGLE_WEIGHT)
    raw_weights = raw_weights / raw_weights.sum()
    raw_weights = raw_weights.where(raw_weights >= MIN_SINGLE_WEIGHT, other=0.0)
    if raw_weights.sum() > 0:
        raw_weights = raw_weights / raw_weights.sum()

    selected['target_weight'] = raw_weights.values
    selected = selected[selected['target_weight'] > 0]

    # ── Output columns ───────────────────────────────────────────────
    selected['sleeve'] = SLEEVE_NAME
    selected['reason'] = 'mean_reversion_selection'
    selected['breadth_gate_active'] = gate_active

    out_cols = ['ticker', 'target_weight', 'sleeve', 'score', 'rank', 'reason',
                'breadth_gate_active']
    for opt in ['sector', 'realized_vol', 'rsi_14', 'zscore_20d', 'bb_pct_b']:
        if opt in selected.columns:
            out_cols.append(opt)

    logger.info(
        "[MR_SELECT] Selected %d positions on %s (top: %s, RSI=%.1f, breadth_gate=%s)",
        len(selected),
        asof_date.date(),
        selected.iloc[0]['ticker'] if len(selected) > 0 else '—',
        selected.iloc[0].get('rsi_14', float('nan')),
        gate_active,
    )
    return selected[out_cols].reset_index(drop=True)


# ── SleeveOutput builder ──────────────────────────────────────────────

def build_mean_reversion_sleeve_output(
    prices:         pd.DataFrame,
    top_n:          int = TOP_N_DEFAULT,
    base_strength:  float = 1.0,
    breadth_gate:   Optional[Dict] = None,
) -> SleeveOutput:
    """
    Build a SleeveOutput for the Mean Reversion sleeve.

    Parameters
    ----------
    prices        : Raw OHLCV DataFrame from download_prices().
    top_n         : Maximum positions.
    base_strength : Base sleeve strength for dynamic portfolio allocation.
    breadth_gate  : Optional regime-state dict (regime_result.today).
                    When breadth is unhealthy, candidate count is halved and
                    'breadth_gate_active: True' is recorded in the output notes.
                    Pass None (default) to run without the gate.

    Returns
    -------
    SleeveOutput compatible with PortfolioAllocator.
    """
    if prices is None or prices.empty:
        return create_sleeve_output([], SLEEVE_NAME, 0.0, 'No price data')

    try:
        signals = build_mean_reversion_signals(prices)
    except Exception as exc:
        logger.warning("[MR] Signal build failed: %s", exc)
        return create_sleeve_output([], SLEEVE_NAME, 0.0, f'Signal build error: {exc}')

    targets = select_and_weight(signals, top_n=top_n, breadth_gate=breadth_gate)
    if targets.empty:
        return create_sleeve_output([], SLEEVE_NAME, 0.0, 'No oversold stocks pass gates')

    gate_active = bool(targets.iloc[0].get('breadth_gate_active', False))

    positions = [
        {
            'ticker':          str(row['ticker']).upper(),
            'target_weight':   float(row['target_weight']),
            'reason':          'mean_reversion_selection',
            'signal_strength': float(np.clip((100.0 - row.get('score', 50.0)) / 100.0, 0.0, 1.0)),
        }
        for _, row in targets.iterrows()
    ]

    n_pos      = len(positions)
    top_ticker = positions[0]['ticker'] if positions else '—'
    rsi_val    = targets.iloc[0].get('rsi_14', float('nan')) if len(targets) > 0 else float('nan')
    gate_note  = "; breadth_gate_active=True (top_n halved)" if gate_active else ""
    notes = (
        f"Active: {n_pos} positions (top: {top_ticker}, RSI={rsi_val:.1f}), "
        f"RSI + z-score + Bollinger %B, inverse-vol weighted{gate_note}"
    )
    return create_sleeve_output(positions, SLEEVE_NAME, base_strength, notes)

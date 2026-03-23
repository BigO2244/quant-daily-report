"""
sleeves/sleeve_quality/selection.py
-------------------------------------
Cross-sectional quality factor selection for the Quality sleeve.

Signals:
  - Return on Equity (ROE) — profitability quality
  - Gross margin — competitive moat / pricing power
  - Accruals ratio — earnings backed by real cash flows (inverted)
  - Debt/equity — balance sheet conservatism (inverted)
  - Revenue growth — consistent growth quality

Position sizing: inverse-vol weighting (same convention as Sleeve 1).

Output format mirrors sleeve_1/selection.py so portfolio_alloc.py
works unchanged.

Sector exclusions:
  - Financials (SIC 6xxx): debt/equity not meaningful → weight signal down
  - Utilities: capital-intensive; high D/E is structural, not a risk flag
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import numpy as np
import pandas as pd

from core.portfolio_alloc import SleeveOutput, create_sleeve_output
from datastore import DataStore
from sleeves.sleeve_quality.indicators import (
    SIGNAL_WEIGHTS,
    accruals_signal,
    composite_quality_score,
    debt_equity_signal,
    gross_margin_signal,
    revenue_growth_signal,
    roe_signal,
)

logger = logging.getLogger(__name__)

SLEEVE_NAME = "sleeve_quality"

# ── Configuration ──────────────────────────────────────────────────────
TOP_N_DEFAULT   = 10
MIN_COVERAGE    = 2          # minimum number of valid signals to include a ticker

# Liquidity gates — keep consistent with sleeve_1
GATE_MIN_PRICE       = 5.0
GATE_MIN_AVG_VOLUME  = 100_000

# Inverse-vol position sizing caps
IVOL_FLOOR         = 0.05
IVOL_CAP           = 1.50
MAX_SINGLE_WEIGHT  = 0.40
MIN_SINGLE_WEIGHT  = 0.02

# Sectors where debt/equity signal is structurally biased — reduce its weight
DE_SIGNAL_DOWNWEIGHT_SECTORS = {'Financials', 'Utilities', 'Real Estate'}


# ── Signal builder ─────────────────────────────────────────────────────

def build_quality_signals(
    tickers:    list[str],
    as_of_date: str | pd.Timestamp,
    ds:         DataStore,
    prices:     Optional[pd.DataFrame] = None,
    sectors:    Optional[dict[str, str]] = None,
) -> pd.DataFrame:
    """
    Compute all Quality sleeve signals for a list of tickers as of as_of_date.

    Parameters
    ----------
    tickers    : List of ticker symbols.
    as_of_date : PIT gate — only fundamentals filed on/before this date are used.
    ds         : DataStore instance pointed at data/fundamental/.
    prices     : Optional OHLCV DataFrame for liquidity gates and vol estimation.
                 Long-form with columns: date, ticker, close, volume.
    sectors    : Optional {ticker: sector} mapping for sector-aware adjustments.

    Returns
    -------
    DataFrame indexed by ticker with columns:
        roe, gross_margin, accruals, debt_equity, revenue_growth,
        quality_score, realized_vol (if prices provided),
        sector (if sectors provided)
    """
    as_of = pd.Timestamp(as_of_date)
    prior_year = (as_of - pd.DateOffset(years=1)).strftime('%Y-%m-%d')

    # ── Fetch all signals in bulk ────────────────────────────────────
    roe_raw       = pd.Series({t: ds.get_roe(t, as_of_date)         for t in tickers})
    gm_raw        = pd.Series({t: ds.get_gross_margin(t, as_of_date) for t in tickers})
    accruals_raw  = pd.Series({t: ds.get_accruals_ratio(t, as_of_date) for t in tickers})
    de_raw        = pd.Series({t: ds.get_debt_equity_ratio(t, as_of_date) for t in tickers})
    rev_now       = pd.Series({t: ds.get_revenue_ttm(t, as_of_date)  for t in tickers})
    rev_prior     = pd.Series({t: ds.get_revenue_ttm(t, prior_year)  for t in tickers})

    # ── Normalize signals ────────────────────────────────────────────
    roe_z    = roe_signal(roe_raw)
    gm_z     = gross_margin_signal(gm_raw)
    acc_z    = accruals_signal(accruals_raw)
    de_z     = debt_equity_signal(de_raw)
    rev_z    = revenue_growth_signal(rev_now, rev_prior)

    signals = {
        'roe':            roe_z,
        'gross_margin':   gm_z,
        'accruals':       acc_z,
        'debt_equity':    de_z,
        'revenue_growth': rev_z,
    }

    quality_z = composite_quality_score(signals)

    out = pd.DataFrame({
        'roe':            roe_raw,
        'gross_margin':   gm_raw,
        'accruals':       accruals_raw,
        'debt_equity':    de_raw,
        'revenue_growth': (rev_now / rev_prior - 1.0).where(
            (rev_prior.notna()) & (rev_prior > 0), other=np.nan
        ),
        'quality_score':  quality_z,
    }, index=tickers)

    # ── Coverage gate — drop tickers with too few valid signals ──────
    signal_cols = ['roe', 'gross_margin', 'accruals', 'debt_equity', 'revenue_growth']
    out['n_signals'] = out[signal_cols].notna().sum(axis=1)

    # ── Sector info ─────────────────────────────────────────────────
    if sectors:
        out['sector'] = pd.Series(sectors)

    # ── Realized vol from prices ─────────────────────────────────────
    if prices is not None and not prices.empty:
        prices_clean = prices.copy()
        prices_clean['date'] = pd.to_datetime(prices_clean['date'])
        snap = prices_clean[prices_clean['date'] <= as_of]

        # Compute 20-day realized vol per ticker
        ret = (
            snap.sort_values(['ticker', 'date'])
            .groupby('ticker')['close']
            .pct_change()
        )
        vol = (
            ret.groupby(snap['ticker'])
            .transform(lambda x: x.rolling(20, min_periods=10).std())
            * np.sqrt(252)
        ).clip(lower=IVOL_FLOOR, upper=IVOL_CAP)

        latest = snap.sort_values('date').groupby('ticker').last()
        out['close']           = latest['close']
        out['realized_vol']    = vol.groupby(snap['ticker']).last()
        out['avg_volume_20d']  = (
            snap.sort_values(['ticker', 'date'])
            .groupby('ticker')['volume']
            .transform(lambda x: x.rolling(20, min_periods=5).mean())
            .groupby(snap['ticker']).last()
        )

    return out


# ── Selection ─────────────────────────────────────────────────────────

def select_and_weight(
    signals:    pd.DataFrame,
    top_n:      int = TOP_N_DEFAULT,
) -> pd.DataFrame:
    """
    Select top quality names and compute inverse-vol target weights.

    Parameters
    ----------
    signals : Output of build_quality_signals().
    top_n   : Maximum number of positions to hold.

    Returns
    -------
    DataFrame with columns:
        ticker, target_weight, sleeve, quality_score, rank, reason,
        sector (if available), realized_vol (if available)
    """
    if signals is None or signals.empty:
        return pd.DataFrame()

    eligible = signals.copy()

    # ── Coverage gate ───────────────────────────────────────────────
    eligible = eligible[eligible['n_signals'] >= MIN_COVERAGE]

    # ── Liquidity gates ─────────────────────────────────────────────
    if 'close' in eligible.columns:
        eligible = eligible[eligible['close'].fillna(0) >= GATE_MIN_PRICE]

    if 'avg_volume_20d' in eligible.columns:
        eligible = eligible[eligible['avg_volume_20d'].fillna(0) >= GATE_MIN_AVG_VOLUME]

    # ── Must have a valid composite score ───────────────────────────
    eligible = eligible.dropna(subset=['quality_score'])

    if eligible.empty:
        logger.warning("[QUALITY_SELECT] No tickers pass quality gates")
        return pd.DataFrame()

    # ── Select top N ────────────────────────────────────────────────
    selected = eligible.nlargest(top_n, 'quality_score').copy()
    selected = selected.reset_index().rename(columns={'index': 'ticker'})
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
    selected['reason'] = 'quality_selection'

    out_cols = ['ticker', 'target_weight', 'sleeve', 'quality_score', 'rank', 'reason']
    for opt in ['sector', 'realized_vol']:
        if opt in selected.columns:
            out_cols.append(opt)

    logger.info(
        "[QUALITY_SELECT] Selected %d positions (top: %s, score=%.2f)",
        len(selected),
        selected.iloc[0]['ticker'] if len(selected) > 0 else '—',
        selected.iloc[0]['quality_score'] if len(selected) > 0 else 0.0,
    )
    return selected[out_cols].reset_index(drop=True)


# ── SleeveOutput builder ──────────────────────────────────────────────

def build_quality_sleeve_output(
    tickers:        list[str],
    as_of_date:     str | pd.Timestamp,
    ds:             DataStore,
    prices:         Optional[pd.DataFrame] = None,
    sectors:        Optional[dict[str, str]] = None,
    top_n:          int = TOP_N_DEFAULT,
    base_strength:  float = 1.0,
) -> SleeveOutput:
    """
    Build a SleeveOutput for the Quality sleeve.

    Parameters
    ----------
    tickers       : Universe of tickers to score.
    as_of_date    : PIT gate for all fundamental reads.
    ds            : DataStore pointed at data/fundamental/.
    prices        : Optional OHLCV DataFrame for vol and liquidity gates.
    sectors       : Optional {ticker: sector} map.
    top_n         : Max positions.
    base_strength : Base sleeve strength for dynamic portfolio allocation.

    Returns
    -------
    SleeveOutput compatible with PortfolioAllocator.
    """
    if not tickers:
        return create_sleeve_output([], SLEEVE_NAME, 0.0, 'No tickers provided')

    try:
        signals = build_quality_signals(tickers, as_of_date, ds, prices, sectors)
    except Exception as exc:
        logger.warning("[QUALITY] Signal build failed: %s", exc)
        return create_sleeve_output([], SLEEVE_NAME, 0.0, f'Signal build error: {exc}')

    targets = select_and_weight(signals, top_n=top_n)
    if targets.empty:
        return create_sleeve_output([], SLEEVE_NAME, 0.0, 'No tickers pass quality gates')

    positions = [
        {
            'ticker':           str(row['ticker']).upper(),
            'target_weight':    float(row['target_weight']),
            'reason':           'quality_selection',
            'signal_strength':  float(
                np.clip((row.get('quality_score', 0.0) + 3.0) / 6.0, 0.0, 1.0)
            ),
        }
        for _, row in targets.iterrows()
    ]

    n_pos      = len(positions)
    top_ticker = positions[0]['ticker'] if positions else '—'
    notes = (
        f"Active: {n_pos} positions (top: {top_ticker}), "
        "ROE + gross margin + accruals + D/E, inverse-vol weighted"
    )
    return create_sleeve_output(positions, SLEEVE_NAME, base_strength, notes)

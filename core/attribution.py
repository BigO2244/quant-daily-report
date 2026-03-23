"""
core/attribution.py
--------------------
Signal attribution metrics for research sleeves.

Public interface:
    compute_attribution(signals_df, prices_df, sleeve_name) -> dict

Metrics computed:
  IC (Information Coefficient):
    Spearman rank correlation between signal scores and forward returns
    at horizons: 1, 5, 10, 20 trading days.

  ICIR (Information Coefficient Information Ratio):
    IC.mean() / IC.std() — measures consistency of predictive power.

  Sharpe ratio:
    Annualized Sharpe of the sleeve's daily returns (from signal-weighted
    portfolio).

  Factor decay curve:
    List of IC values at [1, 5, 10, 20] day horizons — shows how quickly
    the signal loses predictive power.

  Turnover rate:
    Average fraction of the portfolio (by weight) changing per rebalance
    period. Computed as mean absolute weight change between periods.

HTML rendering:
    build_attribution_html(attribution_dict, sleeve_name) -> str
    Returns a collapsible <details> block for embedding in daily reports.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Horizons for IC calculation (trading days)
IC_HORIZONS = [1, 5, 10, 20]

# Minimum number of signal-date observations required to compute IC
MIN_IC_OBSERVATIONS = 20


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------

def compute_attribution(
    signals_df: pd.DataFrame,
    prices_df: pd.DataFrame,
    sleeve_name: str,
) -> dict:
    """
    Compute attribution metrics for a sleeve's signals.

    Parameters
    ----------
    signals_df : DataFrame
        Must contain: date, ticker, score (or signal).
        The 'score' column is the raw signal used to rank stocks.
        If 'score' is missing, falls back to 'final_signal', then 'mom_12_1'.
    prices_df : DataFrame
        Long-form OHLCV with columns: date, ticker, close.
    sleeve_name : str
        Label for logging and output.

    Returns
    -------
    dict with keys:
        sleeve_name      : str
        ic_1, ic_5, ic_10, ic_20 : float or None
        icir             : float or None
        sharpe           : float or None
        decay_curve      : list[float]   [IC at 1, 5, 10, 20 days]
        turnover         : float or None
        n_signal_dates   : int
        error            : str or None   (set on failure; other keys will be None)
    """
    result: dict = {
        "sleeve_name": sleeve_name,
        "ic_1": None,
        "ic_5": None,
        "ic_10": None,
        "ic_20": None,
        "icir": None,
        "sharpe": None,
        "decay_curve": [],
        "turnover": None,
        "n_signal_dates": 0,
        "error": None,
    }

    if signals_df is None or signals_df.empty:
        result["error"] = "No signals data"
        return result
    if prices_df is None or prices_df.empty:
        result["error"] = "No prices data"
        return result

    try:
        # ── Resolve signal column ──────────────────────────────────────────
        signal_col = _resolve_signal_col(signals_df)
        if signal_col is None:
            result["error"] = "No usable signal column (need score, final_signal, or mom_12_1)"
            return result

        sig = signals_df[["date", "ticker", signal_col]].copy()
        sig["date"] = pd.to_datetime(sig["date"])
        sig = sig.dropna(subset=[signal_col])

        # ── Build forward returns ─────────────────────────────────────────
        px = prices_df[["date", "ticker", "close"]].copy()
        px["date"] = pd.to_datetime(px["date"])
        px = px.sort_values(["ticker", "date"])

        fwd_returns = _build_forward_returns(px, IC_HORIZONS)

        # ── Merge signals with forward returns ────────────────────────────
        merged = sig.merge(fwd_returns, on=["date", "ticker"], how="inner")
        signal_dates = merged["date"].nunique()
        result["n_signal_dates"] = signal_dates

        if signal_dates < MIN_IC_OBSERVATIONS:
            result["error"] = (
                f"Insufficient data: {signal_dates} signal dates "
                f"(need ≥ {MIN_IC_OBSERVATIONS})"
            )
            return result

        # ── IC at each horizon ────────────────────────────────────────────
        ic_values: dict[int, float] = {}
        ic_series: dict[int, pd.Series] = {}

        for h in IC_HORIZONS:
            col = f"fwd_{h}d"
            if col not in merged.columns:
                continue
            # IC per date: Spearman corr between signal and fwd return
            _h_col = col
            _s_col = signal_col
            ic_per_date = (
                merged.dropna(subset=[_s_col, _h_col])
                .groupby("date")[[_s_col, _h_col]]
                .apply(lambda g: _spearman_corr(g[_s_col], g[_h_col]),
                       include_groups=False)
                .dropna()
            )
            ic_series[h] = ic_per_date
            if len(ic_per_date) >= MIN_IC_OBSERVATIONS:
                ic_values[h] = float(ic_per_date.mean())

        result["ic_1"]  = ic_values.get(1)
        result["ic_5"]  = ic_values.get(5)
        result["ic_10"] = ic_values.get(10)
        result["ic_20"] = ic_values.get(20)
        result["decay_curve"] = [ic_values.get(h) for h in IC_HORIZONS]

        # ── ICIR (use 5-day horizon as primary) ──────────────────────────
        primary_h = 5
        if primary_h in ic_series and len(ic_series[primary_h]) >= MIN_IC_OBSERVATIONS:
            ic_s = ic_series[primary_h]
            ic_std = float(ic_s.std())
            if ic_std > 1e-9:
                result["icir"] = float(ic_s.mean() / ic_std)

        # ── Sleeve Sharpe ─────────────────────────────────────────────────
        result["sharpe"] = _compute_sleeve_sharpe(merged, signal_col)

        # ── Turnover ─────────────────────────────────────────────────────
        result["turnover"] = _compute_turnover(merged, signal_col)

    except Exception as exc:
        logger.warning("[ATTRIBUTION][%s] Computation failed: %s", sleeve_name, exc)
        result["error"] = str(exc)

    return result


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

def build_attribution_html(attr: dict, sleeve_name: str) -> str:
    """
    Build a collapsible HTML <details> block for embedding in the daily report.

    Parameters
    ----------
    attr : dict
        Output of compute_attribution().
    sleeve_name : str
        Display label.

    Returns
    -------
    str : HTML fragment.
    """
    if not attr or attr.get("error") and not attr.get("ic_1"):
        error_msg = attr.get("error", "No data") if attr else "No attribution data"
        return (
            f'<details style="margin:8px 0">'
            f'<summary style="cursor:pointer;font-weight:bold">'
            f'Attribution — {sleeve_name}</summary>'
            f'<p style="color:#6c757d;font-style:italic;padding:8px">{error_msg}</p>'
            f'</details>'
        )

    def _fmt(v, fmt=".3f"):
        if v is None:
            return "—"
        try:
            return f"{float(v):{fmt}}"
        except Exception:
            return "—"

    decay = attr.get("decay_curve") or []
    decay_cells = "".join(
        f"<td style='padding:4px 8px;text-align:right'>{_fmt(v)}</td>"
        for v in decay
    )

    error_note = ""
    if attr.get("error"):
        error_note = (
            f'<p style="color:#dc3545;font-size:11px;margin:4px 0">'
            f'Partial error: {attr["error"]}</p>'
        )

    html = f"""
<details style="margin:8px 0;border:1px solid #dee2e6;border-radius:4px">
  <summary style="cursor:pointer;font-weight:bold;padding:8px 12px;
                  background:#f8f9fa;border-radius:4px">
    Attribution — {sleeve_name}
    &nbsp;<span style="font-weight:normal;color:#6c757d;font-size:12px">
      IC₅={_fmt(attr.get('ic_5'))} &nbsp; ICIR={_fmt(attr.get('icir'))}
      &nbsp; Sharpe={_fmt(attr.get('sharpe'))} &nbsp;
      Turnover={_fmt(attr.get('turnover'), '.1%') if attr.get('turnover') is not None else '—'}
    </span>
  </summary>
  <div style="padding:12px">
    {error_note}
    <table style="border-collapse:collapse;width:100%;margin-bottom:12px">
      <thead>
        <tr style="border-bottom:2px solid #dee2e6">
          <th style="padding:4px 8px;text-align:left">Metric</th>
          <th style="padding:4px 8px;text-align:right">Value</th>
        </tr>
      </thead>
      <tbody>
        <tr><td style="padding:4px 8px">IC (1d)</td>
            <td style="padding:4px 8px;text-align:right">{_fmt(attr.get('ic_1'))}</td></tr>
        <tr><td style="padding:4px 8px">IC (5d)</td>
            <td style="padding:4px 8px;text-align:right">{_fmt(attr.get('ic_5'))}</td></tr>
        <tr><td style="padding:4px 8px">IC (10d)</td>
            <td style="padding:4px 8px;text-align:right">{_fmt(attr.get('ic_10'))}</td></tr>
        <tr><td style="padding:4px 8px">IC (20d)</td>
            <td style="padding:4px 8px;text-align:right">{_fmt(attr.get('ic_20'))}</td></tr>
        <tr><td style="padding:4px 8px">ICIR</td>
            <td style="padding:4px 8px;text-align:right">{_fmt(attr.get('icir'))}</td></tr>
        <tr><td style="padding:4px 8px">Sharpe (ann.)</td>
            <td style="padding:4px 8px;text-align:right">{_fmt(attr.get('sharpe'))}</td></tr>
        <tr><td style="padding:4px 8px">Turnover (avg)</td>
            <td style="padding:4px 8px;text-align:right">
              {_fmt(attr.get('turnover'), '.1%') if attr.get('turnover') is not None else '—'}
            </td></tr>
        <tr><td style="padding:4px 8px">Signal dates used</td>
            <td style="padding:4px 8px;text-align:right">{attr.get('n_signal_dates', 0)}</td></tr>
      </tbody>
    </table>
    <h4 style="margin:0 0 6px 0;font-size:13px">Factor Decay Curve (IC by horizon)</h4>
    <table style="border-collapse:collapse">
      <thead>
        <tr>
          {''.join(f"<th style='padding:4px 8px;text-align:right'>{h}d</th>" for h in IC_HORIZONS)}
        </tr>
      </thead>
      <tbody>
        <tr>{decay_cells}</tr>
      </tbody>
    </table>
  </div>
</details>
""".strip()
    return html


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_signal_col(df: pd.DataFrame) -> Optional[str]:
    """Find the best available signal column."""
    for col in ("score", "final_signal", "mom_12_1", "momentum_score_v2", "factor_score"):
        if col in df.columns:
            return col
    return None


def _build_forward_returns(prices: pd.DataFrame, horizons: list[int]) -> pd.DataFrame:
    """
    Build forward returns at multiple horizons.

    For each ticker on each date, fwd_Nd = close_{t+N} / close_t - 1.
    """
    px = prices.sort_values(["ticker", "date"]).copy()
    for h in horizons:
        px[f"fwd_{h}d"] = (
            px.groupby("ticker")["close"]
            .shift(-h)
            .div(px["close"].replace(0, np.nan))
            - 1.0
        )
    return px[["date", "ticker"] + [f"fwd_{h}d" for h in horizons]]


def _spearman_corr(x: pd.Series, y: pd.Series) -> Optional[float]:
    """
    Compute Spearman rank correlation without scipy.
    Uses pandas rank() + pearson correlation on ranks.
    """
    if len(x) < 3:
        return None
    combined = pd.DataFrame({"x": x, "y": y}).dropna()
    if len(combined) < 3:
        return None
    rx = combined["x"].rank()
    ry = combined["y"].rank()
    n = len(rx)
    # Pearson on ranks = Spearman
    rx_c = rx - rx.mean()
    ry_c = ry - ry.mean()
    denom = np.sqrt((rx_c ** 2).sum() * (ry_c ** 2).sum())
    if denom < 1e-9:
        return None
    return float((rx_c * ry_c).sum() / denom)


def _compute_sleeve_sharpe(
    merged: pd.DataFrame,
    signal_col: str,
    horizon: int = 1,
) -> Optional[float]:
    """
    Estimate sleeve Sharpe from signal-weighted daily returns.

    On each date, weight each stock proportionally to its signal rank,
    compute the weighted portfolio return, then annualize.
    """
    fwd_col = f"fwd_{horizon}d"
    if fwd_col not in merged.columns:
        return None

    daily_port_returns = (
        merged.dropna(subset=[signal_col, fwd_col])
        .assign(rank=lambda df: df.groupby("date")[signal_col].rank(pct=True))
        .assign(weight=lambda df: df["rank"] / df.groupby("date")["rank"].transform("sum"))
        .groupby("date")[["weight", fwd_col]]
        .apply(lambda g: (g["weight"] * g[fwd_col]).sum(), include_groups=False)
    )

    if len(daily_port_returns) < 20:
        return None

    mean_ret = float(daily_port_returns.mean())
    std_ret = float(daily_port_returns.std())
    if std_ret < 1e-9:
        return None
    return float(mean_ret / std_ret * np.sqrt(252))


def _compute_turnover(
    merged: pd.DataFrame,
    signal_col: str,
    top_n: int = 10,
) -> Optional[float]:
    """
    Estimate average portfolio turnover across signal dates.

    On each date, the selected portfolio = top N stocks by signal score.
    Turnover = fraction of portfolio changing between consecutive dates.
    """
    dates = sorted(merged["date"].unique())
    if len(dates) < 2:
        return None

    turnovers = []
    prev_set: set = set()

    for d in dates:
        snap = merged[merged["date"] == d].dropna(subset=[signal_col])
        snap = snap.nlargest(top_n, signal_col)
        curr_set = set(snap["ticker"].str.upper())

        if prev_set:
            # Turnover = stocks that changed (entered or exited) / portfolio size
            changed = len(prev_set.symmetric_difference(curr_set))
            portfolio_size = max(len(prev_set), len(curr_set), 1)
            turnovers.append(changed / portfolio_size)

        prev_set = curr_set

    return float(np.mean(turnovers)) if turnovers else None

"""
Regime-Aware Backtest Matrix — Alpha Stack
==========================================
Runs all 4 configurations (Trend-only, Value-only, Combined 50/50,
Combined Allocator) across all specified windows from 2008 through today.

Usage:
    cd quant-daily-report-main
    python scripts/regime_aware_backtest_matrix.py

Output (outputs/regime_matrix/):
    master_results.csv            — all windows × configs × metrics
    equity_curves/                — per-window equity curve CSVs
    regime_history.csv            — full regime classification history
    DECISION_MEMO.md              — window-by-window analysis
    CONCLUSION_REPORT.md          — final recommendation
"""

from __future__ import annotations

import logging
import os
import sys
import warnings
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("regime_matrix")

# ── path setup ───────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── constants ────────────────────────────────────────────────────────────────
DATA_START        = "2007-01-01"   # 1 year of warmup before 2008
BACKTEST_START    = "2008-01-01"
BACKTEST_END      = "2026-03-07"   # last full trading day
TODAY             = pd.Timestamp(BACKTEST_END)
INITIAL_EQUITY    = 100_000.0
COMMISSION_BPS    = 5.0            # round-trip 5bps
SLIPPAGE_BPS      = 5.0            # one-way 5bps
RISK_FREE         = 0.04           # 4% annual
TOP_N_TREND       = 20             # names selected by trend
TOP_N_VALUE       = 30             # names selected by value
REBAL_RULE        = "ME"           # monthly rebalance for both sleeves
SPY_TICKER        = "SPY"
VIX_TICKER        = "^VIX"
TLT_TICKER        = "TLT"

OUTPUT_DIR = ROOT / "outputs" / "regime_matrix"
CACHE_DIR  = ROOT / "alpha_stack_cache" / "prices"

# ── window definitions ────────────────────────────────────────────────────────
FIXED_WINDOWS = [
    ("full_period",    "2008-01-01", "2026-03-07"),
    ("regime_gfc",     "2008-01-01", "2010-12-31"),
    ("regime_bull1",   "2011-01-01", "2015-12-31"),
    ("regime_bull2",   "2016-01-01", "2020-12-31"),
    ("regime_covid",   "2019-01-01", "2022-12-31"),
    ("regime_recent",  "2021-01-01", "2026-03-07"),
    ("last_3yr",       "2023-03-07", "2026-03-07"),
    ("last_5yr",       "2021-03-07", "2026-03-07"),
]

CONFIGS = ["trend_only", "value_only", "combined_static", "combined_allocator"]


# ═══════════════════════════════════════════════════════════════════════════════
# DATA LAYER
# ═══════════════════════════════════════════════════════════════════════════════

def download_prices_yf(tickers: List[str], start: str, end: str) -> pd.DataFrame:
    """Download prices for multiple tickers and return wide DataFrame (date × ticker)."""
    import yfinance as yf
    logger.info("Downloading %d tickers via yfinance (%s → %s)", len(tickers), start, end)
    raw = yf.download(
        tickers,
        start=start,
        end=end,
        progress=False,
        auto_adjust=True,
        group_by="ticker",
    )
    if raw.empty:
        return pd.DataFrame()

    # Flatten to wide Close prices (date × ticker)
    if isinstance(raw.columns, pd.MultiIndex):
        close = raw.xs("Close", axis=1, level=1) if "Close" in raw.columns.get_level_values(1) else raw.xs("close", axis=1, level=1)
    else:
        close = raw[["Close"]] if "Close" in raw.columns else raw[["close"]]
        close.columns = tickers[:1]

    close.index = pd.to_datetime(close.index).normalize()
    close = close.sort_index()
    close = close.dropna(how="all")
    return close


def load_or_download_prices(tickers: List[str], start: str, end: str, force_refresh: bool = False) -> pd.DataFrame:
    """Cache-aware price download. Returns wide close DataFrame (date × ticker)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"_matrix_prices_{start[:4]}_{end[:4]}.parquet"

    if cache_file.exists() and not force_refresh:
        logger.info("Loading prices from cache: %s", cache_file.name)
        df = pd.read_parquet(cache_file)
        df.index = pd.to_datetime(df.index).normalize()
        # Check if all tickers are present
        missing = [t for t in tickers if t not in df.columns]
        if not missing:
            return df
        logger.info("Cache missing %d tickers — refreshing", len(missing))

    df = download_prices_yf(tickers, start, end)
    if not df.empty:
        df.to_parquet(cache_file)
    return df


# ═══════════════════════════════════════════════════════════════════════════════
# REGIME ENGINE (self-contained)
# ═══════════════════════════════════════════════════════════════════════════════

def compute_regime_history(prices_wide: pd.DataFrame, universe_tickers: List[str]) -> pd.DataFrame:
    """
    Compute daily regime states using SPY trend, VIX, and breadth.

    Returns DataFrame with columns:
        date, trend_state, vol_state, breadth_state, vix, spy_t1, spy_t2, pct_above_200
    """
    logger.info("Computing regime history...")
    spy = prices_wide.get(SPY_TICKER, pd.Series(dtype=float))
    vix = prices_wide.get(VIX_TICKER, pd.Series(dtype=float))

    if spy.empty:
        logger.warning("SPY data not available — regime will default to NEUTRAL")
        spy = pd.Series(100.0, index=prices_wide.index)

    # SPY trend indicators
    spy_ema50  = spy.ewm(span=50,  adjust=False).mean()
    spy_ema200 = spy.ewm(span=200, adjust=False).mean()
    spy_t1 = (spy / spy_ema200 - 1).fillna(0)        # (Close/EMA200) - 1
    spy_t2 = (spy_ema50 / spy_ema200 - 1).fillna(0)   # (EMA50/EMA200) - 1

    # Breadth: % universe above 200-DMA
    universe_px = prices_wide[[t for t in universe_tickers if t in prices_wide.columns]]
    pct_above_200 = pd.Series(index=prices_wide.index, dtype=float)
    for dt in prices_wide.index:
        subset = universe_px.loc[:dt]
        if len(subset) < 50:
            pct_above_200[dt] = np.nan
            continue
        ema200_vals = subset.ewm(span=200, adjust=False).mean().iloc[-1]
        latest_px   = subset.iloc[-1]
        valid = (latest_px > 0) & (ema200_vals > 0)
        if valid.sum() == 0:
            pct_above_200[dt] = np.nan
        else:
            pct_above_200[dt] = (latest_px[valid] > ema200_vals[valid]).mean()

    pct_above_200 = pct_above_200.ffill()

    def classify_trend(t1, t2):
        if t1 >= 0.03 and t2 >= 0.01:   return "strong_up"
        if t1 >= 0.00 and t2 >= 0.00:   return "weak_up"
        if t1 >= -0.02:                  return "neutral"
        if t1 >= -0.05 or t2 >= -0.01:  return "weak_down"
        return "strong_down"

    def classify_vol(v):
        if pd.isna(v):     return "normal"
        if v <= 16:        return "calm"
        if v <= 22:        return "normal"
        if v <= 30:        return "elevated"
        return "crisis"

    def classify_breadth(b):
        if pd.isna(b):    return "mixed"
        if b >= 0.65:     return "healthy"
        if b >= 0.45:     return "mixed"
        if b >= 0.30:     return "deteriorating"
        return "washed_out"

    records = []
    for dt in prices_wide.index:
        t1  = float(spy_t1.get(dt, 0))
        t2  = float(spy_t2.get(dt, 0))
        v   = float(vix.get(dt, np.nan)) if VIX_TICKER in prices_wide.columns else np.nan
        b   = float(pct_above_200.get(dt, np.nan))
        records.append({
            "date":          dt,
            "trend_state":   classify_trend(t1, t2),
            "vol_state":     classify_vol(v),
            "breadth_state": classify_breadth(b),
            "vix":           v,
            "spy_t1":        round(t1, 4),
            "spy_t2":        round(t2, 4),
            "pct_above_200": round(b, 4) if not pd.isna(b) else np.nan,
        })

    regime_df = pd.DataFrame(records).set_index("date")
    logger.info("Regime history computed: %d days", len(regime_df))
    return regime_df


def allocator_budgets(trend_state: str, vol_state: str, breadth_state: str, macro_state: str = "neutral") -> Tuple[float, float]:
    """
    Compute Trend and Value budget fractions based on regime.
    Redistributes Quality + MR budgets proportionally to Trend + Value
    since those sleeves are not yet active.

    Returns (trend_budget, value_budget) summing to ~0.95
    """
    # Base allocator budgets from alpha_stack.yaml (trend + value portions only)
    base = {
        "strong_up":   (0.55, 0.20),
        "weak_up":     (0.45, 0.25),
        "neutral":     (0.35, 0.25),
        "weak_down":   (0.20, 0.20),
        "strong_down": (0.10, 0.15),
    }
    bt, bv = base.get(trend_state, (0.35, 0.25))

    # Quality + MR budgets from base (not available) redistributed pro-rata
    quality_mr = 1.0 - bt - bv  # leftover from quality + MR
    if bt + bv > 0:
        scale = (bt + bv + quality_mr) / (bt + bv)
        bt_scaled = bt * scale
        bv_scaled = bv * scale
    else:
        bt_scaled, bv_scaled = 0.5, 0.5

    # Vol modifiers
    if vol_state == "elevated":
        # Reduce trend by 10%, add to value
        released = bt_scaled * 0.10
        bt_scaled -= released
        bv_scaled += released
    elif vol_state == "crisis":
        # Reduce trend by 30%; move to cash (model as reducing both)
        crisis_release = bt_scaled * 0.30
        bt_scaled -= crisis_release
        # cash portion: shrink overall exposure
        bt_scaled *= 0.80
        bv_scaled *= 0.80

    # Breadth modifiers
    if breadth_state == "healthy":
        shift = min(0.05, bv_scaled * 0.20)
        bt_scaled += shift
        bv_scaled -= shift
    elif breadth_state == "deteriorating":
        shift = min(0.05, bt_scaled * 0.15)
        bt_scaled -= shift
        bv_scaled += shift
    elif breadth_state == "washed_out":
        # Hold existing positions, no new trend entries; reduce trend budget
        bt_scaled *= 0.70

    # Macro modifiers
    if macro_state == "supportive":
        bv_scaled = min(bv_scaled + 0.05, bv_scaled * 1.25)
    elif macro_state == "restrictive":
        bv_scaled = max(bv_scaled - 0.05, bv_scaled * 0.75)

    # Normalise to 0.95 gross exposure
    total = bt_scaled + bv_scaled
    if total > 0:
        bt_scaled = bt_scaled / total * 0.95
        bv_scaled = bv_scaled / total * 0.95

    return round(bt_scaled, 4), round(bv_scaled, 4)


# ═══════════════════════════════════════════════════════════════════════════════
# SIGNAL GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

def build_trend_weights(
    prices_wide: pd.DataFrame,
    rebal_dates: pd.DatetimeIndex,
    universe_tickers: List[str],
    top_n: int = TOP_N_TREND,
) -> pd.DataFrame:
    """
    Build target weight DataFrame (date × ticker) for Trend sleeve.

    Signal: cross-sectional rank of composite momentum score.
    Sizing: equal weight within top N.
    """
    from alpha_stack.features.trend import compute_trend_features, compute_raw_trend_score, normalise_to_percentile

    # Long-format prices for the features module
    valid_tickers = [t for t in universe_tickers if t in prices_wide.columns]
    long_px = prices_wide[valid_tickers].stack().reset_index()
    long_px.columns = ["date", "ticker", "close"]
    long_px["date"] = pd.to_datetime(long_px["date"])

    all_weights = {}
    for rd in rebal_dates:
        try:
            feats = compute_trend_features(long_px, as_of_date=rd)
            if feats.empty:
                all_weights[rd] = {}
                continue

            raw_score = compute_raw_trend_score(feats)
            feats["score"] = normalise_to_percentile(raw_score)

            # Entry filter: score >= 60 AND trend_flag = 1 (EMA50 > EMA200)
            candidates = feats[
                (feats["score"] >= 60) & (feats.get("trend_flag", 1) >= 1)
            ].nlargest(top_n, "score")

            if candidates.empty:
                # Fallback: top N regardless of filter (hold mode)
                candidates = feats.nlargest(top_n, "score")

            w = 1.0 / len(candidates)
            all_weights[rd] = {row["ticker"]: w for _, row in candidates.iterrows()}
        except Exception as e:
            logger.debug("[TREND_WEIGHTS] Error on %s: %s", rd.date(), e)
            all_weights[rd] = {}

    tw = pd.DataFrame(all_weights).T.fillna(0.0)
    tw.index = pd.to_datetime(tw.index)
    # Reindex to all price dates and forward-fill
    tw = tw.reindex(prices_wide.index).ffill().fillna(0.0)
    return tw[[c for c in tw.columns if c in valid_tickers]]


def build_value_weights(
    rebal_dates: pd.DatetimeIndex,
    all_dates: pd.DatetimeIndex,
    universe_tickers: List[str],
    sector_map: Dict[str, str],
    prices_wide: pd.DataFrame,
    top_n: int = TOP_N_VALUE,
) -> Tuple[pd.DataFrame, pd.Series]:
    """
    Build target weight DataFrame for Value sleeve using SEC EDGAR fundamentals.

    Returns (weights_df, coverage_series) where coverage_series has the
    fraction of tickers with valid fundamentals per rebalance date.
    """
    from alpha_stack.datastore.prices import PricesDataStore
    from alpha_stack.datastore.fundamentals import FundamentalsDataStore
    from alpha_stack.features.value import compute_value_features

    prices_store = PricesDataStore()
    fundamentals_store = FundamentalsDataStore(prices_datastore=prices_store)

    all_weights = {}
    coverage_by_date = {}
    valid_tickers = [t for t in universe_tickers if t in prices_wide.columns]

    logger.info("[VALUE] Building weights across %d rebalance dates...", len(rebal_dates))
    for i, rd in enumerate(rebal_dates):
        if i % 12 == 0:
            logger.info("[VALUE] Rebalance %d/%d (%s)", i+1, len(rebal_dates), rd.date())
        try:
            feats = compute_value_features(
                fundamentals_store,
                valid_tickers,
                as_of_date=str(rd.date()),
                sector_map=sector_map,
            )
            n_valid = feats.dropna(subset=["earnings_yield", "fcf_yield", "book_to_price"], how="all").shape[0]
            coverage_by_date[rd] = n_valid / len(valid_tickers) if valid_tickers else 0

            if feats.empty or n_valid == 0:
                all_weights[rd] = {}
                continue

            # Composite value score
            feats = feats.dropna(subset=["z_ey", "z_fcfy", "z_bp"], how="all")
            feats["z_ey"]   = feats["z_ey"].fillna(0)
            feats["z_fcfy"] = feats["z_fcfy"].fillna(0)
            feats["z_bp"]   = feats["z_bp"].fillna(0)
            feats["value_score"] = (
                0.40 * feats["z_ey"]
                + 0.30 * feats["z_fcfy"]
                + 0.30 * feats["z_bp"]
            )

            # Select top-N by value score
            top = feats.nlargest(top_n, "value_score")
            if top.empty:
                all_weights[rd] = {}
                continue

            w = 1.0 / len(top)
            all_weights[rd] = {row["ticker"]: w for _, row in top.iterrows()}
        except Exception as e:
            logger.warning("[VALUE_WEIGHTS] Error on %s: %s", rd.date(), e)
            all_weights[rd] = {}
            coverage_by_date[rd] = 0.0

    # Build weight matrix
    tw = pd.DataFrame(all_weights).T.fillna(0.0)
    tw.index = pd.to_datetime(tw.index)
    tw = tw.reindex(all_dates).ffill().fillna(0.0)

    coverage = pd.Series(coverage_by_date)
    coverage.index = pd.to_datetime(coverage.index)

    return tw[[c for c in tw.columns if c in valid_tickers]], coverage


# ═══════════════════════════════════════════════════════════════════════════════
# METRICS
# ═══════════════════════════════════════════════════════════════════════════════

def compute_full_metrics(
    result: dict,
    window_name: str,
    config: str,
    start: str,
    end: str,
    sector_map: Dict[str, str],
    regime_df: Optional[pd.DataFrame] = None,
    value_coverage: Optional[pd.Series] = None,
    trend_bkf: Optional[pd.DataFrame] = None,
    value_bkf: Optional[pd.DataFrame] = None,
) -> dict:
    """Compute all required metrics from a backtest result dict."""
    eq = result["equity_curve"].set_index("date")["equity"]
    eq.index = pd.to_datetime(eq.index)

    start_ts = pd.Timestamp(start)
    end_ts   = pd.Timestamp(end)
    eq = eq[(eq.index >= start_ts) & (eq.index <= end_ts)]

    if len(eq) < 5:
        return {"window": window_name, "config": config, "start": start, "end": end, "error": "insufficient data"}

    daily_rets = eq.pct_change().dropna()
    n_days  = len(eq)
    n_years = max(n_days / 252, 0.01)

    gross_total  = eq.iloc[-1] / eq.iloc[0] - 1
    cagr         = (1 + gross_total) ** (1 / n_years) - 1
    annual_vol   = daily_rets.std() * np.sqrt(252)
    excess       = daily_rets - RISK_FREE / 252
    sharpe       = (excess.mean() / daily_rets.std() * np.sqrt(252)) if daily_rets.std() > 1e-10 else 0

    down_rets    = daily_rets[daily_rets < (RISK_FREE / 252)]
    down_vol     = down_rets.std() * np.sqrt(252) if len(down_rets) > 1 else annual_vol
    sortino      = ((cagr - RISK_FREE) / down_vol) if down_vol > 1e-10 else 0

    cum          = (1 + daily_rets).cumprod()
    rolling_max  = cum.cummax()
    dd_series    = (cum - rolling_max) / rolling_max
    max_dd       = float(dd_series.min())

    # Gross vs net (gross ignores transaction costs — approximate via cagr + cost estimate)
    avg_daily_to = result["stats"].get("avg_daily_turnover", 0)
    annual_to    = avg_daily_to * 252
    cost_drag    = annual_to * (COMMISSION_BPS + SLIPPAGE_BPS) / 1e4
    net_cagr     = cagr  # Already net in backtest_engine
    gross_cagr   = cagr + cost_drag  # Approximate gross

    # Turnover
    turnover_annual = annual_to

    # Average holdings
    weights_df = result.get("weights", pd.DataFrame())
    if not weights_df.empty:
        w_slice = weights_df[(weights_df.index >= start_ts) & (weights_df.index <= end_ts)]
        avg_holdings = (w_slice.abs() > 0.001).sum(axis=1).mean()
    else:
        avg_holdings = np.nan

    # Sector concentration (HHI on average sector weights)
    sector_conc = np.nan
    if not weights_df.empty and sector_map:
        w_slice = weights_df[(weights_df.index >= start_ts) & (weights_df.index <= end_ts)]
        avg_weights = w_slice.mean()
        sector_weights = {}
        for tkr, wt in avg_weights.items():
            sec = sector_map.get(tkr, "Unknown")
            sector_weights[sec] = sector_weights.get(sec, 0) + abs(wt)
        total_w = sum(sector_weights.values())
        if total_w > 0:
            shares = {k: v/total_w for k, v in sector_weights.items()}
            sector_conc = sum(v**2 for v in shares.values())  # HHI

    # Sleeve correlation (only for combined configs)
    sleeve_corr = np.nan
    if config in ("combined_static", "combined_allocator") and trend_bkf is not None and value_bkf is not None:
        t_eq = trend_bkf.set_index("date")["equity"]
        v_eq = value_bkf.set_index("date")["equity"]
        t_eq.index = pd.to_datetime(t_eq.index)
        v_eq.index = pd.to_datetime(v_eq.index)
        t_ret = t_eq.pct_change().dropna()
        v_ret = v_eq.pct_change().dropna()
        aligned = pd.concat([t_ret, v_ret], axis=1).dropna()
        if len(aligned) > 20:
            sleeve_corr = float(aligned.corr().iloc[0, 1])

    # Factor / data coverage
    factor_coverage = np.nan
    if value_coverage is not None and config in ("value_only", "combined_static", "combined_allocator"):
        cov_slice = value_coverage[(value_coverage.index >= start_ts) & (value_coverage.index <= end_ts)]
        if not cov_slice.empty:
            factor_coverage = float(cov_slice.mean())

    # Allocator behaviour
    alloc_drift = np.nan
    if config == "combined_allocator" and regime_df is not None:
        reg_slice = regime_df[(regime_df.index >= start_ts) & (regime_df.index <= end_ts)]
        if not reg_slice.empty:
            state_counts = reg_slice["trend_state"].value_counts(normalize=True)
            alloc_drift = float(state_counts.get("neutral", 0) + state_counts.get("weak_down", 0) + state_counts.get("strong_down", 0))

    # Max DD date
    if not dd_series.empty:
        max_dd_date = str(dd_series.idxmin().date()) if not dd_series.isna().all() else "N/A"
    else:
        max_dd_date = "N/A"

    return {
        "window":             window_name,
        "config":             config,
        "start":              start,
        "end":                end,
        "n_years":            round(n_years, 2),
        "n_days":             n_days,
        "cagr":               round(cagr * 100, 2),
        "gross_cagr":         round(gross_cagr * 100, 2),
        "net_cagr":           round(net_cagr * 100, 2),
        "annual_vol":         round(annual_vol * 100, 2),
        "sharpe":             round(sharpe, 3),
        "sortino":            round(sortino, 3),
        "max_dd":             round(max_dd * 100, 2),
        "max_dd_date":        max_dd_date,
        "total_return":       round(gross_total * 100, 2),
        "turnover_annual":    round(turnover_annual * 100, 2),
        "avg_holdings":       round(avg_holdings, 1) if not pd.isna(avg_holdings) else np.nan,
        "sector_hhi":         round(sector_conc, 4) if not pd.isna(sector_conc) else np.nan,
        "sleeve_correlation": round(sleeve_corr, 4) if not pd.isna(sleeve_corr) else np.nan,
        "allocator_defensive_frac": round(alloc_drift, 3) if not pd.isna(alloc_drift) else np.nan,
        "value_data_coverage": round(factor_coverage * 100, 1) if not pd.isna(factor_coverage) else np.nan,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# ROLLING WINDOWS
# ═══════════════════════════════════════════════════════════════════════════════

def generate_rolling_windows(prices_index: pd.DatetimeIndex, window_years: int, step_months: int = 6) -> List[Tuple[str, str, str]]:
    """Generate rolling windows of `window_years` years stepping every `step_months` months."""
    windows = []
    start_year = 2008
    end_limit  = TODAY

    current = pd.Timestamp(f"{start_year}-01-01")
    while True:
        window_end = current + pd.DateOffset(years=window_years) - pd.DateOffset(days=1)
        if window_end > end_limit:
            break
        label = f"roll_{window_years}y_{current.year}q{(current.month-1)//3+1}"
        windows.append((label, str(current.date()), str(window_end.date())))
        current += pd.DateOffset(months=step_months)

    return windows


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════════

class RegimeAwareBacktestMatrix:

    def __init__(self, force_refresh: bool = False):
        self.force_refresh = force_refresh
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUTPUT_DIR / "equity_curves").mkdir(exist_ok=True)

        # Load universe
        universe_df = pd.read_csv(ROOT / "data" / "universe.csv")
        self.universe_tickers = universe_df["ticker"].tolist()
        self.sector_map = dict(zip(universe_df["ticker"], universe_df.get("sector", pd.Series(dtype=str)).fillna("Unknown")))

        logger.info("Universe: %d tickers", len(self.universe_tickers))

    def run(self):
        logger.info("=" * 70)
        logger.info("REGIME-AWARE BACKTEST MATRIX — ALPHA STACK")
        logger.info("=" * 70)

        # ── Step 1: Download all prices ─────────────────────────────────────
        all_tickers = self.universe_tickers + [SPY_TICKER, VIX_TICKER, TLT_TICKER]
        prices_wide = load_or_download_prices(
            all_tickers, DATA_START, BACKTEST_END,
            force_refresh=self.force_refresh,
        )
        if prices_wide.empty:
            logger.error("No price data retrieved. Aborting.")
            return

        logger.info("Prices shape: %s", prices_wide.shape)

        # ── Step 2: Compute regime history ──────────────────────────────────
        regime_df = compute_regime_history(prices_wide, self.universe_tickers)
        regime_df.to_csv(OUTPUT_DIR / "regime_history.csv")
        logger.info("Regime history saved.")

        # ── Step 3: Build rebalance dates for full period ───────────────────
        full_px = prices_wide[prices_wide.index >= pd.Timestamp(BACKTEST_START)]
        all_dates = full_px.index
        dummy = pd.Series(1, index=all_dates)
        rebal_dates = dummy.resample("ME").last().dropna().index

        # ── Step 4: Pre-build weight matrices for full period ───────────────
        logger.info("Building TREND weight matrix (%d rebalances)...", len(rebal_dates))
        trend_weights_full = build_trend_weights(
            prices_wide, rebal_dates, self.universe_tickers
        )
        trend_weights_full = trend_weights_full.reindex(all_dates).ffill().fillna(0.0)

        logger.info("Building VALUE weight matrix (%d rebalances)...", len(rebal_dates))
        value_weights_full, value_coverage = build_value_weights(
            rebal_dates, all_dates, self.universe_tickers, self.sector_map, prices_wide
        )
        value_weights_full = value_weights_full.reindex(all_dates).ffill().fillna(0.0)

        # ── Step 5: Build combined weight matrices ──────────────────────────
        logger.info("Building COMBINED weight matrices...")

        # Align columns
        t_cols = set(trend_weights_full.columns)
        v_cols = set(value_weights_full.columns)
        all_cols = sorted(t_cols | v_cols)

        tw_full = trend_weights_full.reindex(columns=all_cols, fill_value=0.0)
        vw_full = value_weights_full.reindex(columns=all_cols, fill_value=0.0)

        # Static 50/50
        static_weights_full = (tw_full * 0.50 + vw_full * 0.50)

        # Allocator-based: regime-aware budget each month
        alloc_weights_full = self._build_allocator_weights(
            tw_full, vw_full, regime_df, all_dates, rebal_dates, all_cols
        )

        logger.info("Weight matrices built.")

        # SPY prices for benchmark
        spy_prices = prices_wide.get(SPY_TICKER, pd.Series(dtype=float)).reindex(all_dates)

        # ── Step 6: Define all windows ──────────────────────────────────────
        rolling_3y = generate_rolling_windows(all_dates, window_years=3, step_months=6)
        rolling_5y = generate_rolling_windows(all_dates, window_years=5, step_months=12)
        all_windows = FIXED_WINDOWS + rolling_3y + rolling_5y

        logger.info("Total windows to test: %d", len(all_windows))
        logger.info("Total cells (windows × configs): %d", len(all_windows) * len(CONFIGS))

        # ── Step 7: Run all cells ───────────────────────────────────────────
        from engine.backtest_engine import run_backtest

        all_results_metrics = []
        equity_curves_by_window: Dict[str, Dict[str, pd.DataFrame]] = {}

        px_universe = prices_wide[[t for t in all_cols if t in prices_wide.columns]]

        config_weights = {
            "trend_only":          tw_full,
            "value_only":          vw_full,
            "combined_static":     static_weights_full,
            "combined_allocator":  alloc_weights_full,
        }

        for win_idx, (win_name, win_start, win_end) in enumerate(all_windows):
            ws = pd.Timestamp(win_start)
            we = pd.Timestamp(win_end)

            # Slice prices for this window
            px_slice = px_universe[(px_universe.index >= ws) & (px_universe.index <= we)]
            spy_slice = spy_prices[(spy_prices.index >= ws) & (spy_prices.index <= we)]

            if len(px_slice) < 60:
                logger.warning("[WINDOW] %s too short (%d days) — skipping", win_name, len(px_slice))
                continue

            logger.info(
                "[WINDOW %d/%d] %-30s (%s → %s, %d days)",
                win_idx + 1, len(all_windows), win_name, win_start, win_end, len(px_slice)
            )

            equity_curves_by_window[win_name] = {}
            trend_result = None
            value_result = None

            for config in CONFIGS:
                # Slice weights
                w_slice = config_weights[config].reindex(px_slice.index).ffill().fillna(0.0)
                # Keep only columns present in px_slice
                common = sorted(set(w_slice.columns) & set(px_slice.columns))
                if not common:
                    continue
                w_slice = w_slice[common]
                px_for_run = px_slice[common].ffill()

                try:
                    bt_result = run_backtest(
                        target_weights=w_slice,
                        prices=px_for_run,
                        initial_equity=INITIAL_EQUITY,
                        commission_bps=COMMISSION_BPS,
                        slippage_bps=SLIPPAGE_BPS,
                        rebal_rule=REBAL_RULE,
                        benchmark_prices=spy_slice if not spy_slice.empty else None,
                    )
                except Exception as e:
                    logger.error("[RUN] %s/%s failed: %s", win_name, config, e)
                    continue

                # Store equity curve
                ec = bt_result["equity_curve"]
                equity_curves_by_window[win_name][config] = ec

                # Track individual sleeve results for sleeve correlation
                if config == "trend_only":
                    trend_result = bt_result
                elif config == "value_only":
                    value_result = bt_result

                # Compute metrics
                reg_slice = regime_df[(regime_df.index >= ws) & (regime_df.index <= we)]
                vc_slice  = value_coverage[(value_coverage.index >= ws) & (value_coverage.index <= we)] if value_coverage is not None else None

                metrics = compute_full_metrics(
                    result=bt_result,
                    window_name=win_name,
                    config=config,
                    start=win_start,
                    end=win_end,
                    sector_map=self.sector_map,
                    regime_df=reg_slice,
                    value_coverage=vc_slice,
                    trend_bkf=trend_result["equity_curve"] if trend_result else None,
                    value_bkf=value_result["equity_curve"] if value_result else None,
                )
                all_results_metrics.append(metrics)

            # Save equity curves for this window
            if equity_curves_by_window[win_name]:
                ec_merged = None
                for cfg, ec_df in equity_curves_by_window[win_name].items():
                    ec_df2 = ec_df.rename(columns={"equity": cfg}).set_index("date")
                    if ec_merged is None:
                        ec_merged = ec_df2
                    else:
                        ec_merged = ec_merged.join(ec_df2, how="outer")
                if ec_merged is not None:
                    ec_merged.to_csv(OUTPUT_DIR / "equity_curves" / f"{win_name}.csv")

        # ── Step 8: Save master results ─────────────────────────────────────
        logger.info("Saving master results table...")
        master_df = pd.DataFrame(all_results_metrics)
        master_df.to_csv(OUTPUT_DIR / "master_results.csv", index=False)
        logger.info("master_results.csv saved: %d rows", len(master_df))

        # ── Step 9: Generate reports ────────────────────────────────────────
        logger.info("Generating reports...")
        self._generate_decision_memo(master_df, regime_df, value_coverage)
        self._generate_conclusion_report(master_df, regime_df)

        logger.info("=" * 70)
        logger.info("BACKTEST MATRIX COMPLETE")
        logger.info("Output directory: %s", OUTPUT_DIR)
        logger.info("=" * 70)
        return master_df

    # ────────────────────────────────────────────────────────────────────────
    # Allocator weight builder
    # ────────────────────────────────────────────────────────────────────────

    def _build_allocator_weights(
        self,
        tw: pd.DataFrame,
        vw: pd.DataFrame,
        regime_df: pd.DataFrame,
        all_dates: pd.DatetimeIndex,
        rebal_dates: pd.DatetimeIndex,
        all_cols: List[str],
    ) -> pd.DataFrame:
        """Build allocator weight DataFrame by adjusting T/V split on rebalance dates."""
        alloc_w = pd.DataFrame(0.0, index=all_dates, columns=all_cols)

        prev_budget = (0.475, 0.475)  # default 50/50

        for rd in rebal_dates:
            if rd not in regime_df.index:
                # Find nearest date
                candidates = regime_df.index[regime_df.index <= rd]
                if candidates.empty:
                    continue
                rd_reg = candidates[-1]
            else:
                rd_reg = rd

            reg_row = regime_df.loc[rd_reg]
            t_bud, v_bud = allocator_budgets(
                trend_state   = reg_row.get("trend_state", "neutral"),
                vol_state     = reg_row.get("vol_state", "normal"),
                breadth_state = reg_row.get("breadth_state", "mixed"),
            )

            # Smooth budgets (max 10% change per rebalance)
            pt, pv = prev_budget
            t_bud = np.clip(t_bud, pt - 0.10, pt + 0.10)
            v_bud = np.clip(v_bud, pv - 0.10, pv + 0.10)
            prev_budget = (t_bud, v_bud)

            # Weights on this rebalance date
            if rd in tw.index and rd in vw.index:
                t_row = tw.loc[rd].reindex(all_cols, fill_value=0.0)
                v_row = vw.loc[rd].reindex(all_cols, fill_value=0.0)

                # Scale each sleeve's weights by its budget
                t_sum = t_row.sum()
                v_sum = v_row.sum()
                t_scaled = t_row / t_sum * t_bud if t_sum > 0 else t_row * 0
                v_scaled = v_row / v_sum * v_bud if v_sum > 0 else v_row * 0

                alloc_w.loc[rd] = (t_scaled + v_scaled).values

        # Forward-fill between rebalances
        alloc_w = alloc_w.replace(0.0, np.nan)
        # Only ffill non-rebalance rows that are all-NaN
        rebal_set = set(rebal_dates)
        for i, dt in enumerate(all_dates):
            if dt not in rebal_set and i > 0:
                alloc_w.loc[dt] = alloc_w.iloc[i-1]
        alloc_w = alloc_w.fillna(0.0)

        return alloc_w

    # ────────────────────────────────────────────────────────────────────────
    # Decision Memo
    # ────────────────────────────────────────────────────────────────────────

    def _generate_decision_memo(
        self,
        master_df: pd.DataFrame,
        regime_df: pd.DataFrame,
        value_coverage: Optional[pd.Series],
    ):
        lines = []
        lines.append("# Alpha Stack — Window-by-Window Decision Memo\n")
        lines.append(f"_Generated: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')} | Model update test_\n\n")

        # Regime summary
        lines.append("## Regime Distribution (Full Period)\n")
        if not regime_df.empty:
            for dim in ["trend_state", "vol_state", "breadth_state"]:
                if dim in regime_df.columns:
                    dist = regime_df[dim].value_counts(normalize=True).sort_values(ascending=False)
                    lines.append(f"**{dim.replace('_',' ').title()}**: " + " | ".join(f"{k}: {v:.1%}" for k, v in dist.items()) + "\n\n")

        # Value data coverage
        if value_coverage is not None and not value_coverage.empty:
            early = value_coverage[value_coverage.index <= "2012-12-31"].mean()
            recent = value_coverage[value_coverage.index >= "2015-01-01"].mean()
            lines.append(f"**Value Data Coverage** — Pre-2013: {early:.1%} | Post-2015: {recent:.1%}\n\n")
            if early < 0.40:
                lines.append("> ⚠️  **Sparse EDGAR coverage pre-2013** — Value sleeve results in early windows have reduced statistical reliability. Weight trend-only results more heavily for 2008–2012 windows.\n\n")

        # Fixed windows table
        lines.append("## Fixed Window Results\n\n")
        fixed_names = [w[0] for w in FIXED_WINDOWS]
        fixed_df = master_df[master_df["window"].isin(fixed_names)].copy()

        if not fixed_df.empty:
            lines.append("| Window | Config | CAGR | Sharpe | Sortino | MaxDD | Vol | Turnover | Avg Holdings | Value Coverage |\n")
            lines.append("|--------|--------|------|--------|---------|-------|-----|----------|--------------|----------------|\n")
            for _, row in fixed_df.sort_values(["window", "config"]).iterrows():
                lines.append(
                    f"| {row['window']} | {row['config']} "
                    f"| {row.get('cagr','N/A')}% "
                    f"| {row.get('sharpe','N/A')} "
                    f"| {row.get('sortino','N/A')} "
                    f"| {row.get('max_dd','N/A')}% "
                    f"| {row.get('annual_vol','N/A')}% "
                    f"| {row.get('turnover_annual','N/A')}% "
                    f"| {row.get('avg_holdings','N/A')} "
                    f"| {row.get('value_data_coverage','N/A')}% |\n"
                )

        # Per-window analysis
        lines.append("\n## Window-by-Window Analysis\n\n")
        for win_name, win_start, win_end in FIXED_WINDOWS:
            w_df = master_df[master_df["window"] == win_name]
            if w_df.empty:
                continue
            lines.append(f"### {win_name.replace('_',' ').title()} ({win_start} → {win_end})\n\n")

            # Best config by Sharpe
            valid = w_df.dropna(subset=["sharpe"])
            if not valid.empty:
                best = valid.loc[valid["sharpe"].idxmax()]
                lines.append(f"**Best Config**: {best['config']} (Sharpe: {best['sharpe']}, CAGR: {best['cagr']}%)\n\n")

                # Value-adds check
                trend_row = w_df[w_df["config"] == "trend_only"]
                value_row = w_df[w_df["config"] == "value_only"]
                combined_row = w_df[w_df["config"] == "combined_static"]
                alloc_row = w_df[w_df["config"] == "combined_allocator"]

                if not trend_row.empty and not combined_row.empty:
                    t_s = float(trend_row["sharpe"].iloc[0])
                    c_s = float(combined_row["sharpe"].iloc[0])
                    delta = c_s - t_s
                    lines.append(f"**Value Add** (Combined vs Trend-only Sharpe delta): {delta:+.3f}\n")
                    if delta > 0.05:
                        lines.append("✅ Value adds meaningful diversification in this window.\n\n")
                    elif delta < -0.05:
                        lines.append("❌ Value hurts performance in this window — monitor factor decay.\n\n")
                    else:
                        lines.append("➡️  Value is roughly neutral — minimal diversification benefit.\n\n")

                if not alloc_row.empty and not combined_row.empty:
                    a_s = float(alloc_row["sharpe"].iloc[0])
                    c_s = float(combined_row["sharpe"].iloc[0])
                    alloc_delta = a_s - c_s
                    lines.append(f"**Allocator Benefit** (Allocator vs Static Sharpe delta): {alloc_delta:+.3f}\n")
                    if alloc_delta > 0.03:
                        lines.append("✅ Allocator improves risk-adjusted returns.\n\n")
                    elif alloc_delta < -0.03:
                        lines.append("⚠️  Allocator underperforms static in this window.\n\n")
                    else:
                        lines.append("➡️  Allocator roughly neutral vs static.\n\n")

        # Rolling window summary
        lines.append("## Rolling Window Summary\n\n")
        for yr in [3, 5]:
            roll_df = master_df[master_df["window"].str.startswith(f"roll_{yr}y_")].copy()
            if roll_df.empty:
                continue
            lines.append(f"### Rolling {yr}-Year Windows\n\n")
            for config in CONFIGS:
                c_df = roll_df[roll_df["config"] == config].dropna(subset=["sharpe"])
                if c_df.empty:
                    continue
                avg_s = c_df["sharpe"].mean()
                min_s = c_df["sharpe"].min()
                max_s = c_df["sharpe"].max()
                pct_pos = (c_df["cagr"] > 0).mean()
                lines.append(f"**{config}** — Avg Sharpe: {avg_s:.3f} | Min: {min_s:.3f} | Max: {max_s:.3f} | % Positive CAGR: {pct_pos:.1%}\n\n")

        memo_path = OUTPUT_DIR / "DECISION_MEMO.md"
        with open(memo_path, "w") as f:
            f.writelines(lines)
        logger.info("Decision memo saved: %s", memo_path)

    # ────────────────────────────────────────────────────────────────────────
    # Conclusion Report
    # ────────────────────────────────────────────────────────────────────────

    def _generate_conclusion_report(self, master_df: pd.DataFrame, regime_df: pd.DataFrame):
        lines = []
        lines.append("# Alpha Stack — Regime-Aware Backtest: Conclusion Report\n")
        lines.append(f"_Model update test | {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}_\n\n")

        # Full period summary table
        fp_df = master_df[master_df["window"] == "full_period"].copy()
        lines.append("## Full-Period Summary (2008–2026)\n\n")
        if not fp_df.empty:
            lines.append("| Config | CAGR | Gross CAGR | Sharpe | Sortino | MaxDD | Vol | Turnover | Avg Holdings | Sector HHI | Sleeve Corr |\n")
            lines.append("|--------|------|------------|--------|---------|-------|-----|----------|--------------|------------|-------------|\n")
            for _, row in fp_df.sort_values("sharpe", ascending=False).iterrows():
                lines.append(
                    f"| {row['config']} "
                    f"| {row.get('cagr','N/A')}% "
                    f"| {row.get('gross_cagr','N/A')}% "
                    f"| {row.get('sharpe','N/A')} "
                    f"| {row.get('sortino','N/A')} "
                    f"| {row.get('max_dd','N/A')}% "
                    f"| {row.get('annual_vol','N/A')}% "
                    f"| {row.get('turnover_annual','N/A')}% "
                    f"| {row.get('avg_holdings','N/A')} "
                    f"| {row.get('sector_hhi','N/A')} "
                    f"| {row.get('sleeve_correlation','N/A')} |\n"
                )

        # Value adds value?
        lines.append("\n## Does Value Add Value Across Regimes?\n\n")
        fixed_df = master_df[master_df["window"].isin([w[0] for w in FIXED_WINDOWS])].copy()

        trend_sharpes = fixed_df[fixed_df["config"] == "trend_only"].set_index("window")["sharpe"]
        comb_sharpes  = fixed_df[fixed_df["config"] == "combined_static"].set_index("window")["sharpe"]

        if not trend_sharpes.empty and not comb_sharpes.empty:
            common_wins = trend_sharpes.index.intersection(comb_sharpes.index)
            deltas = (comb_sharpes[common_wins] - trend_sharpes[common_wins]).dropna()
            n_pos = (deltas > 0.03).sum()
            n_neg = (deltas < -0.03).sum()
            n_neutral = len(deltas) - n_pos - n_neg
            lines.append(f"Across {len(deltas)} fixed windows:\n\n")
            lines.append(f"- Value adds (Sharpe Δ > 0.03): **{n_pos}** windows\n")
            lines.append(f"- Value hurts (Sharpe Δ < -0.03): **{n_neg}** windows\n")
            lines.append(f"- Value neutral: **{n_neutral}** windows\n\n")

            if n_pos > n_neg + 1:
                value_verdict = "✅ **Value DOES add value** across most regimes tested."
            elif n_neg > n_pos + 1:
                value_verdict = "❌ **Value does NOT consistently add value** in the tested windows — further hardening required."
            else:
                value_verdict = "➡️ **Value is regime-dependent** — broadly neutral with meaningful dispersion."
            lines.append(value_verdict + "\n\n")

        # Allocator adds value?
        lines.append("## Does the Allocator Improve Outcomes?\n\n")
        static_sharpes = fixed_df[fixed_df["config"] == "combined_static"].set_index("window")["sharpe"]
        alloc_sharpes  = fixed_df[fixed_df["config"] == "combined_allocator"].set_index("window")["sharpe"]

        if not static_sharpes.empty and not alloc_sharpes.empty:
            common_wins = static_sharpes.index.intersection(alloc_sharpes.index)
            a_deltas = (alloc_sharpes[common_wins] - static_sharpes[common_wins]).dropna()
            n_pos_a = (a_deltas > 0.02).sum()
            n_neg_a = (a_deltas < -0.02).sum()
            lines.append(f"Across {len(a_deltas)} fixed windows:\n\n")
            lines.append(f"- Allocator helps (Sharpe Δ > 0.02): **{n_pos_a}** windows\n")
            lines.append(f"- Allocator hurts (Sharpe Δ < -0.02): **{n_neg_a}** windows\n\n")

            if n_pos_a > n_neg_a:
                alloc_verdict = "✅ **Allocator improves outcomes** in the majority of tested windows."
            elif n_neg_a > n_pos_a:
                alloc_verdict = "⚠️ **Allocator underperforms static** — regime thresholds may need re-calibration."
            else:
                alloc_verdict = "➡️ **Allocator is broadly neutral** — benefits are regime-specific."
            lines.append(alloc_verdict + "\n\n")

        # Additional suggested tests
        lines.append("## Additional Suggested Tests\n\n")
        lines.append("Beyond the mandatory matrix, consider:\n\n")
        lines.append("1. **Factor decay analysis** — IC (Information Coefficient) at 1m, 3m, 6m, 12m horizons to measure signal persistence\n")
        lines.append("2. **Transaction cost sensitivity** — Re-run at 0bps, 10bps, 25bps, 50bps to find breakeven turnover threshold\n")
        lines.append("3. **Universe survivorship stress-test** — Re-run with point-in-time S&P 500 constituents (current universe has survivorship bias)\n")
        lines.append("4. **Liquidity filtering robustness** — Restrict to $5B+ market cap or top-150 by liquidity; compare vs full 201-ticker run\n")
        lines.append("5. **Sector-neutralized Value** — Force equal sector weights to isolate stock-selection from sector-rotation effects\n")
        lines.append("6. **Regime classification sensitivity** — Test hysteresis thresholds ±20% to confirm allocator stability\n")
        lines.append("7. **Out-of-sample 2023-2026** — Isolate the post-update period and compare daily NAV vs shadow pre-update baseline\n")
        lines.append("8. **Bootstrap confidence intervals** — Block-bootstrap on annual returns to get Sharpe CI for key configs\n\n")

        # Explicit recommendation
        lines.append("## Explicit Recommendation\n\n")

        # Compute recommendation dynamically
        fp_sharpes = {}
        for config in CONFIGS:
            row = fp_df[fp_df["config"] == config]
            if not row.empty:
                fp_sharpes[config] = float(row["sharpe"].iloc[0])

        trend_s = fp_sharpes.get("trend_only", 0)
        combined_s = fp_sharpes.get("combined_static", 0)
        alloc_s = fp_sharpes.get("combined_allocator", 0)

        lines.append(f"**Full-period Sharpe** — Trend: {trend_s:.3f} | Combined: {combined_s:.3f} | Allocator: {alloc_s:.3f}\n\n")

        if combined_s > trend_s + 0.05 and alloc_s >= combined_s - 0.02:
            rec = (
                "### ✅ PROCEED — Promote Alpha Stack to Shadow Live with Quality as Next Development Target\n\n"
                "The Value sleeve adds meaningful diversification benefit (Sharpe improvement > 0.05) and the "
                "allocator is at least as good as static blending. The combination is ready for shadow "
                "live deployment. Begin Quality sleeve development in parallel.\n\n"
                "**Next steps**: Enable `ENABLE_ALPHA_STACK_SHADOW=true`, monitor daily NAV divergence "
                "from production for 30 trading days, then gate on live promotion."
            )
        elif combined_s < trend_s - 0.05:
            rec = (
                "### 🔧 HARDEN VALUE — Do Not Promote Until Value Sleeve Is Improved\n\n"
                "Value sleeve is currently diluting the Trend sleeve's risk-adjusted performance. "
                "Recommend: (1) investigate factor coverage gaps in pre-2013 data, (2) add "
                "shareholder yield via a supplementary data source, (3) implement sector-neutral "
                "weighting, (4) reduce Value's initial portfolio weight from 20% to 10%. "
                "Re-test before promotion.\n\n"
                "**Keep Alpha Stack in RESEARCH MODE until next iteration.**"
            )
        else:
            rec = (
                "### ➡️ CONTINUE RESEARCH — Value Neutral, Validate for 60 More Trading Days\n\n"
                "Value sleeve shows mixed results — neither clearly additive nor clearly dilutive. "
                "The model is not yet ready for production promotion. Recommended path: "
                "(1) Complete factor decay analysis (IC test), (2) resolve data coverage gaps, "
                "(3) run 60-day paper trade comparison vs. Trend-only baseline, "
                "(4) revisit recommendation with a full quarter of additional data.\n\n"
                "**Alpha Stack remains in RESEARCH MODE.**"
            )

        lines.append(rec + "\n")

        report_path = OUTPUT_DIR / "CONCLUSION_REPORT.md"
        with open(report_path, "w") as f:
            f.writelines(lines)
        logger.info("Conclusion report saved: %s", report_path)


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Alpha Stack Regime-Aware Backtest Matrix")
    parser.add_argument("--refresh", action="store_true", help="Force re-download all price data")
    args = parser.parse_args()

    matrix = RegimeAwareBacktestMatrix(force_refresh=args.refresh)
    results = matrix.run()

    if results is not None and not results.empty:
        print("\n" + "=" * 70)
        print("SUMMARY — Full Period Results")
        print("=" * 70)
        fp = results[results["window"] == "full_period"][["config", "cagr", "sharpe", "sortino", "max_dd", "annual_vol", "turnover_annual"]]
        print(fp.to_string(index=False))

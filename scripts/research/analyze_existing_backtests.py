"""
Regime-Aware Window Analysis — Using Existing Backtest Data
============================================================
Analyzes the existing sleeve1 (Trend) and alpha-variant (Combined/Allocator)
timeseries across all required regime windows and rolling windows.

Works WITHOUT internet access — uses pre-existing project CSV data.

Run this for immediate results; run regime_aware_backtest_matrix.py on your
local machine (with yfinance/EDGAR access) for the Value-only and
Combined-50/50 columns.
"""

from __future__ import annotations
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("regime_analysis")

ROOT   = Path(__file__).resolve().parent.parent
OUTDIR = ROOT / "outputs" / "regime_matrix"
OUTDIR.mkdir(parents=True, exist_ok=True)

RISK_FREE = 0.04

# ── window definitions (matching regime_aware_backtest_matrix.py) ─────────────
FIXED_WINDOWS = [
    ("full_period",    "2009-01-02", "2025-12-31"),
    ("regime_gfc",     "2009-01-02", "2010-12-31"),
    ("regime_bull1",   "2011-01-01", "2015-12-31"),
    ("regime_bull2",   "2016-01-01", "2020-12-31"),
    ("regime_covid",   "2019-01-01", "2022-12-31"),
    ("regime_recent",  "2021-01-01", "2025-12-31"),
    ("last_3yr",       "2023-01-01", "2025-12-31"),
    ("last_5yr",       "2021-01-01", "2025-12-31"),
]

TODAY_TS = pd.Timestamp("2025-12-31")


def generate_rolling_windows(start_year: int, window_years: int, step_months: int = 6) -> list:
    windows = []
    current = pd.Timestamp(f"{start_year}-01-01")
    limit   = pd.Timestamp("2026-01-01")
    while True:
        end = current + pd.DateOffset(years=window_years) - pd.DateOffset(days=1)
        if end > limit:
            break
        lbl = f"roll_{window_years}y_{current.year}q{(current.month-1)//3+1}"
        windows.append((lbl, str(current.date()), str(end.date())))
        current += pd.DateOffset(months=step_months)
    return windows


def compute_metrics(nav: pd.Series, name: str, window: str, config: str, start: str, end: str) -> dict:
    """Compute full metric set from a NAV series (normalised to 1.0 at start)."""
    nav = nav.dropna()
    ws, we = pd.Timestamp(start), pd.Timestamp(end)
    nav = nav[(nav.index >= ws) & (nav.index <= we)].copy()
    if len(nav) < 20:
        return {}

    # Re-normalise to 1.0 at first date of this window
    nav = nav / nav.iloc[0]

    daily_rets = nav.pct_change().dropna()
    n_days     = len(nav)
    n_years    = max(n_days / 252, 0.01)

    total_ret = float(nav.iloc[-1] - 1)
    cagr      = float((1 + total_ret) ** (1 / n_years) - 1)
    ann_vol   = float(daily_rets.std() * np.sqrt(252))
    excess    = daily_rets - RISK_FREE / 252
    sharpe    = float(excess.mean() / daily_rets.std() * np.sqrt(252)) if daily_rets.std() > 1e-10 else 0.0

    down   = daily_rets[daily_rets < RISK_FREE / 252]
    d_vol  = float(down.std() * np.sqrt(252)) if len(down) > 1 else ann_vol
    sortino = float((cagr - RISK_FREE) / d_vol) if d_vol > 1e-10 else 0.0

    cum     = (1 + daily_rets).cumprod()
    rolling = cum.cummax()
    dd_s    = (cum - rolling) / rolling
    max_dd  = float(dd_s.min())
    max_dd_date = str(dd_s.idxmin().date()) if not dd_s.isna().all() else "N/A"

    return {
        "window": window,
        "config": config,
        "start": start,
        "end": end,
        "n_years": round(n_years, 2),
        "n_days": n_days,
        "total_return":  round(total_ret * 100, 2),
        "cagr":          round(cagr * 100, 2),
        "annual_vol":    round(ann_vol * 100, 2),
        "sharpe":        round(sharpe, 3),
        "sortino":       round(sortino, 3),
        "max_dd":        round(max_dd * 100, 2),
        "max_dd_date":   max_dd_date,
        "calmar":        round(cagr / abs(max_dd), 3) if max_dd < -0.001 else None,
        "win_rate":      round((daily_rets > 0).mean() * 100, 1),
    }


def classify_regime_from_nav(spy_nav: pd.Series) -> pd.DataFrame:
    """Simple regime classifier from SPY NAV series."""
    spy_ema50  = spy_nav.ewm(span=50,  adjust=False).mean()
    spy_ema200 = spy_nav.ewm(span=200, adjust=False).mean()
    spy_t1 = (spy_nav / spy_ema200 - 1).fillna(0)
    spy_t2 = (spy_ema50 / spy_ema200 - 1).fillna(0)

    def _trend(t1, t2):
        if t1 >= 0.03 and t2 >= 0.01:   return "strong_up"
        if t1 >= 0.00 and t2 >= 0.00:   return "weak_up"
        if t1 >= -0.02:                  return "neutral"
        if t1 >= -0.05:                  return "weak_down"
        return "strong_down"

    records = []
    for dt in spy_nav.index:
        records.append({
            "date":        dt,
            "trend_state": _trend(float(spy_t1.get(dt, 0)), float(spy_t2.get(dt, 0))),
            "spy_t1":      round(float(spy_t1.get(dt, 0)), 4),
            "spy_t2":      round(float(spy_t2.get(dt, 0)), 4),
        })
    return pd.DataFrame(records).set_index("date")


def sleeve_correlation(nav_a: pd.Series, nav_b: pd.Series, start: str, end: str) -> float:
    ws, we = pd.Timestamp(start), pd.Timestamp(end)
    ret_a = nav_a[(nav_a.index >= ws) & (nav_a.index <= we)].pct_change().dropna()
    ret_b = nav_b[(nav_b.index >= ws) & (nav_b.index <= we)].pct_change().dropna()
    aligned = pd.concat([ret_a, ret_b], axis=1).dropna()
    if len(aligned) < 20:
        return np.nan
    return float(aligned.corr().iloc[0, 1])


def compute_allocator_regime_stats(regime_df: pd.DataFrame, start: str, end: str) -> dict:
    ws, we = pd.Timestamp(start), pd.Timestamp(end)
    sl = regime_df[(regime_df.index >= ws) & (regime_df.index <= we)]
    if sl.empty:
        return {}
    counts = sl["trend_state"].value_counts(normalize=True)
    return {
        "pct_strong_up":   round(float(counts.get("strong_up", 0)) * 100, 1),
        "pct_weak_up":     round(float(counts.get("weak_up", 0)) * 100, 1),
        "pct_neutral":     round(float(counts.get("neutral", 0)) * 100, 1),
        "pct_weak_down":   round(float(counts.get("weak_down", 0)) * 100, 1),
        "pct_strong_down": round(float(counts.get("strong_down", 0)) * 100, 1),
        "defensive_frac":  round(float(counts.get("weak_down", 0) + counts.get("strong_down", 0)) * 100, 1),
    }


def compute_spy_excess(nav: pd.Series, spy: pd.Series, start: str, end: str) -> float:
    """CAGR of nav minus CAGR of spy over the window."""
    ws, we = pd.Timestamp(start), pd.Timestamp(end)
    n = nav[(nav.index >= ws) & (nav.index <= we)].dropna()
    s = spy[(spy.index >= ws) & (spy.index <= we)].dropna()
    if len(n) < 20 or len(s) < 20:
        return np.nan
    n = n / n.iloc[0];  s = s / s.iloc[0]
    nyrs = len(n) / 252
    nc = (n.iloc[-1]) ** (1/nyrs) - 1
    sc = (s.iloc[-1]) ** (1/nyrs) - 1
    return round((nc - sc) * 100, 2)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    # ── Load data ────────────────────────────────────────────────────────────
    res_dir = ROOT / "outputs" / "research"
    trend_ts_path = res_dir / "sleeve1_backtest_2009_2025_timeseries.csv"
    alpha_ts_path = res_dir / "sleeve1_alpha_variant_timeseries.csv"
    alpha_sum_path = res_dir / "sleeve1_alpha_variant_summary.csv"
    rw3y_path = res_dir / "random_windows_3y_full.csv"
    rw5y_path = res_dir / "sleeve1_alpha_random_windows_5y.csv"

    if not trend_ts_path.exists():
        logger.error("Missing: %s", trend_ts_path)
        sys.exit(1)

    logger.info("Loading Trend sleeve timeseries...")
    trend_ts = pd.read_csv(trend_ts_path, parse_dates=["date"])
    trend_ts = trend_ts.set_index("date").sort_index()
    trend_nav = trend_ts["portfolio_nav"].dropna()
    spy_nav   = trend_ts["spy_nav"].dropna()

    logger.info("Loading Alpha Variant (Combined Allocator proxy) timeseries...")
    alpha_ts  = pd.read_csv(alpha_ts_path, parse_dates=["date"]).set_index("date").sort_index()
    alloc_nav = alpha_ts["net_nav"].dropna()            # net of costs
    alloc_nav_cb = alpha_ts["net_nav_cb"].dropna()     # net + circuit breaker
    alloc_gross  = alpha_ts["gross_nav"].dropna()

    logger.info("Trend NAV: %s → %s (%d days)", trend_nav.index[0].date(), trend_nav.index[-1].date(), len(trend_nav))
    logger.info("Alloc NAV: %s → %s (%d days)", alloc_nav.index[0].date(), alloc_nav.index[-1].date(), len(alloc_nav))

    # ── Regime classification from SPY ───────────────────────────────────────
    logger.info("Classifying regimes from SPY NAV...")
    regime_df = classify_regime_from_nav(spy_nav)
    regime_df.to_csv(OUTDIR / "regime_history.csv")

    # ── Build all windows ────────────────────────────────────────────────────
    rolling_3y = generate_rolling_windows(2009, 3, step_months=6)
    rolling_5y = generate_rolling_windows(2009, 5, step_months=12)
    all_windows = FIXED_WINDOWS + rolling_3y + rolling_5y
    logger.info("Total windows: %d", len(all_windows))

    # ── Run analysis ─────────────────────────────────────────────────────────
    all_metrics = []
    equity_curves = {}

    # Data sources for each config:
    #   trend_only          → trend_nav (Sleeve 1 production NAV)
    #   combined_allocator  → alloc_nav (Alpha Variant net NAV)
    #   combined_allocator_cb → alloc_nav_cb (Alpha Variant + circuit breaker)
    #   value_only          → NOT AVAILABLE (requires EDGAR backtest)
    #   combined_static     → NOT AVAILABLE (requires EDGAR backtest)
    #   spy_benchmark       → spy_nav

    configs = {
        "trend_only":           trend_nav,
        "combined_allocator":   alloc_nav,
        "combined_alloc_cb":    alloc_nav_cb,
        "spy_benchmark":        spy_nav,
    }

    for win_name, win_start, win_end in all_windows:
        equity_curves[win_name] = {}

        for config_name, nav in configs.items():
            m = compute_metrics(nav, config_name, win_name, config_name, win_start, win_end)
            if not m:
                continue

            # Add SPY excess return
            m["spy_excess_cagr"] = compute_spy_excess(nav, spy_nav, win_start, win_end)

            # Add regime stats for allocator configs
            if config_name.startswith("combined"):
                reg_stats = compute_allocator_regime_stats(regime_df, win_start, win_end)
                m.update(reg_stats)

            # Sleeve correlation (trend vs allocator)
            if config_name == "combined_allocator":
                m["sleeve_correlation_trend_alloc"] = sleeve_correlation(trend_nav, alloc_nav, win_start, win_end)

            all_metrics.append(m)

        # Equity curve for this window
        ws, we = pd.Timestamp(win_start), pd.Timestamp(win_end)
        ec_df = pd.DataFrame({
            "trend_only":          (trend_nav / trend_nav.iloc[0] if not trend_nav.empty else pd.Series(dtype=float)).get(trend_nav.index[(trend_nav.index >= ws) & (trend_nav.index <= we)]),
        })
        frames = {}
        for cname, nav in configs.items():
            slice_ = nav[(nav.index >= ws) & (nav.index <= we)]
            if not slice_.empty:
                frames[cname] = (slice_ / slice_.iloc[0]).rename(cname)
        if frames:
            ec = pd.concat(frames.values(), axis=1).sort_index()
            ec.to_csv(OUTDIR / "equity_curves" / f"{win_name}.csv")

    # ── Save master results ───────────────────────────────────────────────────
    master_df = pd.DataFrame(all_metrics)
    master_df.to_csv(OUTDIR / "master_results.csv", index=False)
    logger.info("master_results.csv: %d rows", len(master_df))

    # ── Full-period summary ───────────────────────────────────────────────────
    fp = master_df[master_df["window"] == "full_period"].copy()
    logger.info("\n%s", "=" * 70)
    logger.info("FULL PERIOD SUMMARY (2009–2025)")
    logger.info("%s", "=" * 70)
    for _, row in fp.sort_values("sharpe", ascending=False).iterrows():
        logger.info("%-30s | CAGR: %5.1f%% | Sharpe: %5.3f | Sortino: %5.3f | MaxDD: %5.1f%% | Vol: %5.1f%%",
                    row["config"], row["cagr"], row["sharpe"], row["sortino"], row["max_dd"], row["annual_vol"])

    # ── Also load existing random windows data ────────────────────────────────
    existing_rw = {}
    if rw3y_path.exists():
        existing_rw["trend_3y"] = pd.read_csv(rw3y_path)
    if rw5y_path.exists():
        existing_rw["alpha_5y"] = pd.read_csv(rw5y_path)

    # ── Generate reports ──────────────────────────────────────────────────────
    generate_decision_memo(master_df, regime_df, existing_rw)
    generate_conclusion_report(master_df, regime_df, existing_rw)

    logger.info("\nOutput directory: %s", OUTDIR)
    return master_df


def generate_decision_memo(master_df, regime_df, existing_rw):
    lines = []
    lines.append("# Alpha Stack — Regime-Aware Backtest: Window-by-Window Decision Memo\n\n")
    lines.append(f"_Run date: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')} | Model update validation_\n\n")
    lines.append("> **Data scope**: Trend-only = Sleeve 1 production NAV (2009–2025). Combined Allocator = Alpha Variant net NAV (2009–2025). Value-only and Combined 50/50 require full EDGAR backtest — run `regime_aware_backtest_matrix.py` locally for those columns.\n\n")

    # Regime distribution
    lines.append("## Regime Distribution — Full Period (2009–2025)\n\n")
    full_regime = regime_df
    ts_counts = full_regime["trend_state"].value_counts(normalize=True).sort_values(ascending=False)
    lines.append("| Trend State | % of Days |\n|---|---|\n")
    for state, pct in ts_counts.items():
        lines.append(f"| {state} | {pct:.1%} |\n")
    lines.append("\n")

    # Fixed windows
    lines.append("## Fixed Window Results\n\n")
    fixed_names = [w[0] for w in FIXED_WINDOWS]
    fixed_df = master_df[master_df["window"].isin(fixed_names)].copy()

    lines.append("| Window | Config | CAGR | SPY Excess | Sharpe | Sortino | MaxDD | Vol | Win Rate |\n")
    lines.append("|--------|--------|------|------------|--------|---------|-------|-----|----------|\n")
    for _, row in fixed_df.sort_values(["window","config"]).iterrows():
        excess_str = f"{row.get('spy_excess_cagr', float('nan')):+.1f}%" if pd.notna(row.get('spy_excess_cagr')) else "N/A"
        lines.append(
            f"| {row['window']} | {row['config']} "
            f"| {row.get('cagr','N/A')}% | {excess_str} "
            f"| {row.get('sharpe','N/A')} | {row.get('sortino','N/A')} "
            f"| {row.get('max_dd','N/A')}% | {row.get('annual_vol','N/A')}% "
            f"| {row.get('win_rate','N/A')}% |\n"
        )
    lines.append("\n")

    # Per-window analysis
    lines.append("## Window-by-Window Analysis\n\n")
    for win_name, win_start, win_end in FIXED_WINDOWS:
        w_df = master_df[master_df["window"] == win_name]
        if w_df.empty:
            continue

        lines.append(f"### {win_name.replace('_',' ').title()} ({win_start} → {win_end})\n\n")

        reg_stats = compute_allocator_regime_stats(
            classify_regime_from_nav(
                spy_nav_series := master_df[master_df["config"]=="spy_benchmark"][["window"]].copy()  # placeholder
            ) if False else pd.DataFrame({"trend_state": []}, index=pd.DatetimeIndex([])),
            win_start, win_end
        )

        # Get regime stats from full regime_df
        ws, we = pd.Timestamp(win_start), pd.Timestamp(win_end)
        reg_slice = regime_df[(regime_df.index >= ws) & (regime_df.index <= we)]
        if not reg_slice.empty:
            rc = reg_slice["trend_state"].value_counts(normalize=True)
            dom_regime = rc.idxmax()
            lines.append(f"**Dominant Regime**: {dom_regime} ({rc.max():.0%} of days)\n\n")

        # Compare trend vs allocator
        tr = w_df[w_df["config"] == "trend_only"]
        al = w_df[w_df["config"] == "combined_allocator"]
        al_cb = w_df[w_df["config"] == "combined_alloc_cb"]

        if not tr.empty:
            lines.append(f"**Trend-Only**: CAGR {tr['cagr'].iloc[0]}% | Sharpe {tr['sharpe'].iloc[0]} | MaxDD {tr['max_dd'].iloc[0]}%\n\n")
        if not al.empty:
            lines.append(f"**Combined Allocator**: CAGR {al['cagr'].iloc[0]}% | Sharpe {al['sharpe'].iloc[0]} | MaxDD {al['max_dd'].iloc[0]}%\n\n")
            if not tr.empty:
                sharpe_delta = float(al['sharpe'].iloc[0]) - float(tr['sharpe'].iloc[0])
                cagr_delta   = float(al['cagr'].iloc[0])  - float(tr['cagr'].iloc[0])
                lines.append(f"**Alpha Variant Improvement** — Sharpe Δ: {sharpe_delta:+.3f} | CAGR Δ: {cagr_delta:+.1f}%\n\n")
                if sharpe_delta > 0.10:
                    lines.append("✅ Alpha Stack model significantly outperforms Trend-only in this window.\n\n")
                elif sharpe_delta < -0.10:
                    lines.append("⚠️ Alpha Stack underperforms Trend-only — regime-specific factor decay possible.\n\n")
                else:
                    lines.append("➡️ Alpha Stack broadly in line with Trend-only — marginal improvement.\n\n")
        if not al_cb.empty and not al.empty:
            cb_delta = float(al_cb['sharpe'].iloc[0]) - float(al['sharpe'].iloc[0])
            lines.append(f"**Circuit Breaker Effect** — Sharpe Δ: {cb_delta:+.3f} (CB vs no-CB)\n\n")

    # Rolling window summary
    lines.append("## Rolling Window Summary\n\n")
    for yr in [3, 5]:
        roll_df = master_df[master_df["window"].str.startswith(f"roll_{yr}y_")].copy()
        if roll_df.empty:
            continue
        lines.append(f"### Rolling {yr}-Year Windows\n\n")
        lines.append("| Config | Avg Sharpe | Min Sharpe | Max Sharpe | Avg CAGR | % Positive CAGR | Avg MaxDD |\n")
        lines.append("|--------|------------|------------|------------|----------|-----------------|----------|\n")
        for config in ["trend_only", "combined_allocator", "combined_alloc_cb", "spy_benchmark"]:
            c_df = roll_df[roll_df["config"] == config].dropna(subset=["sharpe"])
            if c_df.empty:
                continue
            lines.append(
                f"| {config} "
                f"| {c_df['sharpe'].mean():.3f} "
                f"| {c_df['sharpe'].min():.3f} "
                f"| {c_df['sharpe'].max():.3f} "
                f"| {c_df['cagr'].mean():.1f}% "
                f"| {(c_df['cagr'] > 0).mean():.0%} "
                f"| {c_df['max_dd'].mean():.1f}% |\n"
            )
        lines.append("\n")

    # Existing random windows appendix
    if "trend_3y" in existing_rw:
        rw = existing_rw["trend_3y"]
        lines.append("## Appendix: Existing Random Window Analysis (Trend Strategy)\n\n")
        lines.append(f"Based on {len(rw)} random 3-year windows:\n\n")
        for policy in rw["policy"].unique() if "policy" in rw.columns else ["all"]:
            sub = rw[rw["policy"] == policy] if "policy" in rw.columns else rw
            lines.append(f"**Policy: {policy}** — "
                         f"Median CAGR: {sub.get('median_cagr', sub.get('cagr', pd.Series())).median()*100 if 'median_cagr' in sub.columns else sub['cagr'].median():.1f}% | "
                         f"Median MaxDD: {sub.get('median_max_drawdown', sub.get('max_drawdown', pd.Series())).median()*100 if 'median_max_drawdown' in sub.columns else sub['max_drawdown'].median():.1f}%\n\n")

    memo_path = OUTDIR / "DECISION_MEMO.md"
    with open(memo_path, "w") as f:
        f.writelines(lines)
    logger.info("Decision memo saved: %s", memo_path)


def generate_conclusion_report(master_df, regime_df, existing_rw):
    lines = []
    lines.append("# Alpha Stack — Regime-Aware Backtest: Conclusion Report\n\n")
    lines.append(f"_Run: {pd.Timestamp.now().strftime('%Y-%m-%d')} | Testing model update significance_\n\n")

    lines.append("## Data Scope\n\n")
    lines.append("| Config | Data Source | Period | Status |\n|--------|-------------|--------|--------|\n")
    lines.append("| trend_only | Sleeve 1 production backtest | 2009–2025 | ✅ Available |\n")
    lines.append("| combined_allocator | Alpha Variant (net NAV) | 2009–2025 | ✅ Available |\n")
    lines.append("| combined_alloc_cb | Alpha Variant + circuit breaker | 2009–2025 | ✅ Available |\n")
    lines.append("| value_only | EDGAR fundamentals backtest | 2009–present | ⏳ Run locally |\n")
    lines.append("| combined_static | 50/50 blend | 2009–present | ⏳ Run locally |\n")
    lines.append("| spy_benchmark | SPY buy-and-hold | 2009–2025 | ✅ Available |\n\n")

    # Full period table
    fp = master_df[master_df["window"] == "full_period"].copy()
    lines.append("## Full Period Summary (2009–2025)\n\n")
    if not fp.empty:
        lines.append("| Config | CAGR | SPY Excess | Sharpe | Sortino | MaxDD | Vol | Win Rate |\n")
        lines.append("|--------|------|------------|--------|---------|-------|-----|----------|\n")
        for _, row in fp.sort_values("sharpe", ascending=False).iterrows():
            excess_str = f"{row.get('spy_excess_cagr', float('nan')):+.1f}%" if pd.notna(row.get('spy_excess_cagr')) else "N/A"
            lines.append(
                f"| **{row['config']}** | {row.get('cagr','N/A')}% | {excess_str} "
                f"| {row.get('sharpe','N/A')} | {row.get('sortino','N/A')} "
                f"| {row.get('max_dd','N/A')}% | {row.get('annual_vol','N/A')}% "
                f"| {row.get('win_rate','N/A')}% |\n"
            )
    lines.append("\n")

    # Compare across fixed windows
    fixed_names = [w[0] for w in FIXED_WINDOWS]
    fixed_df = master_df[master_df["window"].isin(fixed_names)].copy()
    trend_sharpes = fixed_df[fixed_df["config"] == "trend_only"].set_index("window")["sharpe"]
    alloc_sharpes = fixed_df[fixed_df["config"] == "combined_allocator"].set_index("window")["sharpe"]

    lines.append("## Does the Alpha Stack Model Outperform Trend-Only?\n\n")
    if not trend_sharpes.empty and not alloc_sharpes.empty:
        common = trend_sharpes.index.intersection(alloc_sharpes.index)
        deltas = (alloc_sharpes[common] - trend_sharpes[common]).dropna()
        n_pos = (deltas > 0.05).sum()
        n_neg = (deltas < -0.05).sum()
        n_neu = len(deltas) - n_pos - n_neg

        lines.append(f"Across {len(deltas)} fixed windows — Alpha Variant vs Trend-Only Sharpe delta:\n\n")
        lines.append("| Window | Trend Sharpe | Allocator Sharpe | Delta | Signal |\n|--------|-------------|-----------------|-------|--------|\n")
        for win in common:
            ts = float(trend_sharpes.get(win, np.nan))
            als = float(alloc_sharpes.get(win, np.nan))
            if pd.isna(ts) or pd.isna(als):
                continue
            delta = als - ts
            signal = "✅ Alpha+" if delta > 0.05 else ("⚠️ Regressed" if delta < -0.05 else "➡️ Neutral")
            lines.append(f"| {win} | {ts:.3f} | {als:.3f} | {delta:+.3f} | {signal} |\n")
        lines.append(f"\n**Result**: Outperforms in {n_pos} windows | Underperforms in {n_neg} | Neutral in {n_neu}\n\n")

        if n_pos >= n_neg + 2:
            verdict = "✅ **POSITIVE** — Alpha Stack model update shows improvement over Trend-only baseline in the majority of regime windows."
        elif n_neg >= n_pos + 2:
            verdict = "⚠️ **CONCERNING** — Alpha Stack model update underperforms Trend-only in majority of windows. Investigate before promoting."
        else:
            verdict = "➡️ **MIXED** — Alpha Stack model update shows regime-dependent performance. Sharpe improvement not yet consistent across all windows."
        lines.append(verdict + "\n\n")

    # Rolling window robustness
    lines.append("## Rolling Window Robustness\n\n")
    for yr in [3, 5]:
        roll_df = master_df[master_df["window"].str.startswith(f"roll_{yr}y_")].copy()
        if roll_df.empty:
            continue
        t_df = roll_df[roll_df["config"] == "trend_only"].dropna(subset=["sharpe"])
        a_df = roll_df[roll_df["config"] == "combined_allocator"].dropna(subset=["sharpe"])
        lines.append(f"**{yr}-Year Rolling Windows**:\n\n")
        if not t_df.empty:
            lines.append(f"- Trend-only: Avg Sharpe {t_df['sharpe'].mean():.3f} | % Positive CAGR {(t_df['cagr'] > 0).mean():.0%} | Avg MaxDD {t_df['max_dd'].mean():.1f}%\n")
        if not a_df.empty:
            lines.append(f"- Combined Allocator: Avg Sharpe {a_df['sharpe'].mean():.3f} | % Positive CAGR {(a_df['cagr'] > 0).mean():.0%} | Avg MaxDD {a_df['max_dd'].mean():.1f}%\n")
        lines.append("\n")

    # Regime-conditioned analysis
    lines.append("## Regime-Conditioned Performance\n\n")
    fixed_df2 = master_df[master_df["window"].isin(fixed_names) & (master_df["config"] == "combined_allocator")].copy()
    if not fixed_df2.empty and "defensive_frac" in fixed_df2.columns:
        lines.append("| Window | Defensive Regime % | Allocator CAGR | Allocator Sharpe |\n|--------|-------------------|----------------|------------------|\n")
        for _, row in fixed_df2.sort_values("window").iterrows():
            lines.append(f"| {row['window']} | {row.get('defensive_frac', 'N/A')}% | {row.get('cagr','N/A')}% | {row.get('sharpe','N/A')} |\n")
        lines.append("\n")

    # Recommended next steps
    lines.append("## Additional Tests Recommended\n\n")
    lines.append("1. **Value-only standalone** — Run `regime_aware_backtest_matrix.py` locally for EDGAR-backed Value backtest. Critical to isolate Value's contribution before making promotion decision.\n\n")
    lines.append("2. **Factor decay IC** — Compute Information Coefficient at 1m/3m/6m/12m lags to confirm signal quality hasn't degraded post-update.\n\n")
    lines.append("3. **Transaction cost sensitivity** — Re-run at 0/10/25/50bps to find cost breakeven for the turnover profile.\n\n")
    lines.append("4. **Survivorship bias audit** — Current universe is point-in-time 2026. Validate pre-2015 windows use historical constituents or flag survivorship risk explicitly.\n\n")
    lines.append("5. **Out-of-sample 2024–2026** — Isolate the last 15 months and compare to production shadow NAV to check for in-sample fitting.\n\n")
    lines.append("6. **Bootstrap CIs** — Block-bootstrap on annual returns (block=12mo) to generate 90% CI for Sharpe in each window.\n\n")
    lines.append("7. **Drawdown period analysis** — For 2022 (max drawdown period), check whether the allocator correctly shifted defensive vs. Trend-only, and whether the circuit breaker triggered.\n\n")

    # Explicit recommendation
    lines.append("## Explicit Recommendation\n\n")
    fp_t = master_df[(master_df["window"]=="full_period") & (master_df["config"]=="trend_only")]
    fp_a = master_df[(master_df["window"]=="full_period") & (master_df["config"]=="combined_allocator")]
    fp_cb = master_df[(master_df["window"]=="full_period") & (master_df["config"]=="combined_alloc_cb")]

    t_sharpe  = float(fp_t["sharpe"].iloc[0])  if not fp_t.empty  else 0
    a_sharpe  = float(fp_a["sharpe"].iloc[0])  if not fp_a.empty  else 0
    cb_sharpe = float(fp_cb["sharpe"].iloc[0]) if not fp_cb.empty else 0

    t_cagr  = float(fp_t["cagr"].iloc[0])  if not fp_t.empty  else 0
    a_cagr  = float(fp_a["cagr"].iloc[0])  if not fp_a.empty  else 0

    lines.append(f"**Full-period Sharpe** — Trend-only: {t_sharpe:.3f} | Combined Allocator: {a_sharpe:.3f} | CB-enabled: {cb_sharpe:.3f}\n\n")
    lines.append(f"**Full-period CAGR** — Trend-only: {t_cagr:.1f}% | Combined Allocator: {a_cagr:.1f}%\n\n")

    if a_sharpe > t_sharpe + 0.10 and a_cagr > t_cagr:
        rec = (
            "### ✅ PROCEED — Promote Alpha Stack to Shadow Live\n\n"
            "The Alpha Variant (Combined Allocator) shows material Sharpe improvement (>0.10) over Trend-only "
            "with higher CAGR across the full period. Pending Value-only standalone verification, the model "
            "update is directionally sound.\n\n"
            "**Recommended path**:\n"
            "1. Enable `ENABLE_ALPHA_STACK_SHADOW=true` in production config\n"
            "2. Run `regime_aware_backtest_matrix.py` locally to complete Value-only and 50/50 columns\n"
            "3. Monitor shadow NAV daily for 30 trading days vs Trend-only baseline\n"
            "4. Begin Quality sleeve development in parallel\n"
        )
    elif a_sharpe < t_sharpe - 0.10:
        rec = (
            "### 🔧 HARDEN — Do Not Promote Until Root Cause Is Identified\n\n"
            "The Alpha Variant underperforms Trend-only on a risk-adjusted basis. This is unexpected "
            "if the update added diversifying signal. Investigate:\n\n"
            "1. Check Value sleeve scoring for look-ahead bias in recent data\n"
            "2. Verify EDGAR PIT filter is correct for the most recent filing dates\n"
            "3. Review allocator budget computation for recent regime transitions\n"
            "4. Confirm no unintended interaction between Trend stop-losses and Value rebalance timing\n\n"
            "**Keep Alpha Stack in RESEARCH MODE.**\n"
        )
    else:
        rec = (
            "### ➡️ CONTINUE RESEARCH — Validate Value Sleeve Before Promoting\n\n"
            "The Alpha Variant shows improvement over Trend-only but below the 0.10 Sharpe threshold "
            "for confident promotion. Value sleeve data (EDGAR backtest) is required before drawing "
            "final conclusions.\n\n"
            "**Recommended path**:\n"
            "1. Run `regime_aware_backtest_matrix.py` locally for Value-only and 50/50 columns\n"
            "2. If Value-only Sharpe > 0.30 and Combined > Trend-only Sharpe + 0.05 → promote\n"
            "3. If Value-only Sharpe < 0.20 → harden Value before promoting\n"
            "4. Run 30-day paper trade shadow before any live promotion\n"
        )

    lines.append(rec)

    rpt_path = OUTDIR / "CONCLUSION_REPORT.md"
    with open(rpt_path, "w") as f:
        f.writelines(lines)
    logger.info("Conclusion report saved: %s", rpt_path)


if __name__ == "__main__":
    main()

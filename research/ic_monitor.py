"""
research/ic_monitor.py
======================
Rolling Information Coefficient (IC) monitor for Sleeve 1 signals.

The IC is the Pearson correlation between the composite signal on day T
and the actual 1-day forward return on day T+1.  A persistently positive
IC (>0) means the signal has genuine predictive power.  IC drifting to
zero or negative for 20+ consecutive days is a leading indicator of edge
decay — trigger a parameter review before drawdowns accumulate.

Outputs
-------
outputs/ic_monitor/ic_daily.csv        — one row per computed IC day
outputs/ic_monitor/ic_rolling_60d.csv  — 60-day rolling mean IC series
outputs/ic_monitor/ic_summary.json     — latest IC stats (for alerts)

Usage
-----
# Standalone (reads from signals/ dir):
    python -m research.ic_monitor --signals-dir signals/ --prices-lookback 90

# Called from daily_quant_report.py:
    from research.ic_monitor import compute_and_log_ic
    compute_and_log_ic(report_date="2026-03-03")
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

OUTPUT_DIR   = Path("outputs/ic_monitor")
SIGNALS_DIR  = Path("signals")
IC_DAILY_CSV = OUTPUT_DIR / "ic_daily.csv"
IC_ROLL_CSV  = OUTPUT_DIR / "ic_rolling_60d.csv"
IC_SUMMARY   = OUTPUT_DIR / "ic_summary.json"

# Alert thresholds
IC_WARN_THRESHOLD        = 0.0    # IC below this for 20+ days → warning
IC_WARN_CONSECUTIVE_DAYS = 20
IC_ROLLING_WARN          = 0.03   # 60d rolling IC below this → caution


# ── Helpers ────────────────────────────────────────────────────────────────

def _load_signal_json(signal_date: str) -> pd.DataFrame | None:
    """Load a signal snapshot JSON from the signals/ directory."""
    path = SIGNALS_DIR / f"{signal_date}.json"
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        # Accept both list-of-dicts and {tickers: [...]} formats
        if isinstance(raw, list):
            return pd.DataFrame(raw)
        if isinstance(raw, dict) and "signals" in raw:
            return pd.DataFrame(raw["signals"])
        if isinstance(raw, dict):
            # Try to flatten top-level ticker → signal_value mapping
            rows = [{"ticker": k, "final_signal": v} for k, v in raw.items()
                    if isinstance(v, (int, float))]
            return pd.DataFrame(rows) if rows else None
    except Exception as exc:
        logger.debug("[IC_MONITOR] Could not parse %s: %s", path, exc)
    return None


def _get_forward_return(tickers: list[str], signal_date: str) -> pd.Series | None:
    """
    Download 2 days of prices for the given tickers around signal_date
    and return the 1-day forward return (close-to-close: signal_date → next day).
    """
    try:
        from core.quant_report import download_prices  # lazy import
        sig_dt = pd.Timestamp(signal_date)
        # Fetch a 5-day window to guarantee at least 2 trading days
        start = (sig_dt - pd.Timedelta(days=3)).strftime("%Y-%m-%d")
        end   = (sig_dt + pd.Timedelta(days=5)).strftime("%Y-%m-%d")
        prices = download_prices(
            tickers,
            start=start,
            end=end,
            interval="1d",
        )
        if prices is None or prices.empty:
            return None
        pivot = prices.pivot(index="date", columns="ticker", values="close").sort_index()
        pivot.index = pd.to_datetime(pivot.index)
        dates_after = pivot.index[pivot.index >= sig_dt]
        if len(dates_after) < 2:
            return None
        ret = pivot.loc[dates_after[1]] / pivot.loc[dates_after[0]] - 1.0
        return ret.dropna()
    except Exception as exc:
        logger.debug("[IC_MONITOR] Price fetch failed for %s: %s", signal_date, exc)
        return None


def _compute_ic(signals_df: pd.DataFrame, fwd_ret: pd.Series) -> float | None:
    """
    Compute Pearson IC between final_signal and forward return.
    Returns None if insufficient overlap.
    """
    if "final_signal" not in signals_df.columns:
        # Try common column aliases
        for col in ("signal", "score", "composite_score", "momentum_score_v2"):
            if col in signals_df.columns:
                signals_df = signals_df.rename(columns={col: "final_signal"})
                break
        else:
            return None

    if "ticker" not in signals_df.columns:
        return None

    merged = (
        signals_df[["ticker", "final_signal"]]
        .dropna()
        .set_index("ticker")
        .join(fwd_ret.rename("fwd_ret"), how="inner")
    )
    if len(merged) < 3:
        return None

    ic = merged["final_signal"].corr(merged["fwd_ret"])
    return float(ic) if not np.isnan(ic) else None


def _load_existing_ic() -> pd.DataFrame:
    """Load the existing daily IC CSV, or return empty DataFrame."""
    if IC_DAILY_CSV.exists():
        try:
            return pd.read_csv(IC_DAILY_CSV, parse_dates=["date"])
        except Exception:
            pass
    return pd.DataFrame(columns=["date", "ic", "n_pairs"])


def _append_ic_row(date_str: str, ic_val: float, n_pairs: int) -> pd.DataFrame:
    """Append a new IC observation, deduplicating on date."""
    existing = _load_existing_ic()
    new_row = pd.DataFrame([{"date": pd.Timestamp(date_str), "ic": ic_val, "n_pairs": n_pairs}])
    combined = pd.concat([existing, new_row], ignore_index=True)
    combined = combined.drop_duplicates(subset=["date"], keep="last").sort_values("date")
    return combined


def _compute_rolling_ic(ic_df: pd.DataFrame, window: int = 60) -> pd.DataFrame:
    """Compute rolling mean IC over a `window`-day period."""
    df = ic_df.copy().set_index("date").sort_index()
    df["rolling_ic"] = df["ic"].rolling(window, min_periods=10).mean()
    return df.reset_index()


def _build_summary(ic_df: pd.DataFrame) -> dict:
    """Produce a JSON-serialisable summary dict for alerting."""
    if ic_df.empty:
        return {"status": "no_data"}

    df = ic_df.sort_values("date")
    recent_30 = df.tail(30)
    recent_60 = df.tail(60)

    # Consecutive days IC < 0
    consecutive_negative = 0
    for ic_val in df["ic"].iloc[::-1]:
        if ic_val < IC_WARN_THRESHOLD:
            consecutive_negative += 1
        else:
            break

    rolling_mean_60d = float(recent_60["ic"].mean()) if len(recent_60) >= 10 else None
    rolling_mean_30d = float(recent_30["ic"].mean()) if len(recent_30) >= 5 else None

    status = "OK"
    alerts = []
    if consecutive_negative >= IC_WARN_CONSECUTIVE_DAYS:
        status = "WARN"
        alerts.append(
            f"IC has been <= 0 for {consecutive_negative} consecutive days — edge decay suspected"
        )
    if rolling_mean_60d is not None and rolling_mean_60d < IC_ROLLING_WARN:
        status = "CAUTION" if status == "OK" else status
        alerts.append(
            f"60d rolling IC ({rolling_mean_60d:.4f}) below caution threshold ({IC_ROLLING_WARN})"
        )

    return {
        "status": status,
        "alerts": alerts,
        "latest_ic": float(df["ic"].iloc[-1]),
        "latest_date": str(df["date"].iloc[-1].date()),
        "rolling_mean_60d": rolling_mean_60d,
        "rolling_mean_30d": rolling_mean_30d,
        "pct_positive_ic": float((df["ic"] > 0).mean()),
        "consecutive_negative_days": consecutive_negative,
        "total_observations": len(df),
    }


# ── Public API ─────────────────────────────────────────────────────────────

def compute_and_log_ic(
    report_date: str | None = None,
    signal_date: str | None = None,
) -> dict:
    """
    Compute the IC for one day and append it to the rolling CSV.

    Parameters
    ----------
    report_date : str, optional
        The date for which to compute IC (format YYYY-MM-DD).
        Defaults to yesterday (the last date a signal would have been generated).
    signal_date : str, optional
        Override: date of the signal file to load (defaults to report_date - 1 trading day).

    Returns
    -------
    dict  — the updated summary dict (also written to ic_summary.json)
    """
    if report_date is None:
        report_date = date.today().strftime("%Y-%m-%d")

    # Signal was generated on the previous trading day
    if signal_date is None:
        sig_dt = pd.bdate_range(end=report_date, periods=2)[0]
        signal_date = sig_dt.strftime("%Y-%m-%d")

    logger.info("[IC_MONITOR] Computing IC: signal_date=%s fwd_date=%s", signal_date, report_date)

    signals_df = _load_signal_json(signal_date)
    if signals_df is None or signals_df.empty:
        logger.info("[IC_MONITOR] No signal file found for %s — skipping", signal_date)
        return {"status": "skipped", "reason": f"no signal file for {signal_date}"}

    tickers = signals_df["ticker"].dropna().unique().tolist() if "ticker" in signals_df.columns else []
    if not tickers:
        logger.info("[IC_MONITOR] Signal file has no tickers — skipping")
        return {"status": "skipped", "reason": "no tickers in signal file"}

    fwd_ret = _get_forward_return(tickers, signal_date)
    if fwd_ret is None or fwd_ret.empty:
        logger.info("[IC_MONITOR] Could not fetch forward returns for %s — skipping", signal_date)
        return {"status": "skipped", "reason": "forward return fetch failed"}

    ic_val = _compute_ic(signals_df, fwd_ret)
    if ic_val is None:
        logger.info("[IC_MONITOR] IC computation returned None — skipping")
        return {"status": "skipped", "reason": "ic computation failed (insufficient overlap)"}

    n_pairs = len(
        signals_df[["ticker", "final_signal"]].dropna()
        .set_index("ticker")
        .join(fwd_ret.rename("fwd_ret"), how="inner")
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ic_df = _append_ic_row(signal_date, ic_val, n_pairs)
    ic_df.to_csv(IC_DAILY_CSV, index=False)

    rolling_df = _compute_rolling_ic(ic_df)
    rolling_df.to_csv(IC_ROLL_CSV, index=False)

    summary = _build_summary(ic_df)
    IC_SUMMARY.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    logger.info(
        "[IC_MONITOR] IC=%.4f | 60d_mean=%.4f | status=%s | alerts=%s",
        ic_val,
        summary.get("rolling_mean_60d") or float("nan"),
        summary["status"],
        summary.get("alerts", []),
    )
    if summary["status"] != "OK":
        for alert in summary.get("alerts", []):
            logger.warning("[IC_MONITOR] ALERT: %s", alert)

    return summary


# ── CLI entrypoint ─────────────────────────────────────────────────────────

def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="IC monitor — compute rolling signal IC")
    p.add_argument("--report-date", default=None, help="Date to compute IC for (YYYY-MM-DD)")
    p.add_argument("--signal-date", default=None, help="Override signal file date (YYYY-MM-DD)")
    p.add_argument("--signals-dir", default="signals", help="Directory containing signal JSON files")
    return p.parse_args(argv)


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parse_args(argv)
    global SIGNALS_DIR
    SIGNALS_DIR = Path(args.signals_dir)
    result = compute_and_log_ic(
        report_date=args.report_date,
        signal_date=args.signal_date,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import numpy as np
import pandas as pd


def _fail(message: str) -> None:
    print(f"[VERIFY][ERROR] {message}")
    raise SystemExit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify required audit bundle outputs.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--start", required=True, help="Expected start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="Expected end date YYYY-MM-DD")
    parser.add_argument("--audit-root", default="outputs/audit")
    parser.add_argument("--min-dates", type=int, default=200)
    return parser.parse_args()


def _assert_exists(path: Path) -> None:
    if not path.exists():
        _fail(f"Missing required file: {path}")


def _load_csv(path: Path, name: str) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except Exception as exc:
        _fail(f"Unable to read {name} at {path}: {exc}")
        raise


def _date_bounds_for_window(start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp, int]:
    trading_days = pd.bdate_range(start=start, end=end)
    if len(trading_days) == 0:
        _fail("No business days in requested verification window.")
    return trading_days.min(), trading_days.max(), len(trading_days)


def main() -> None:
    args = parse_args()

    start = pd.Timestamp(args.start).normalize()
    end = pd.Timestamp(args.end).normalize()
    if end < start:
        _fail(f"end ({end.date()}) is before start ({start.date()})")

    run_dir = Path(args.audit_root) / args.run_id
    if not run_dir.exists():
        _fail(f"Missing run directory: {run_dir}")

    required_files = [
        run_dir / "audit.xlsx",
        run_dir / "trades.csv",
        run_dir / "holdings_daily.csv",
        run_dir / "portfolio_daily.csv",
    ]
    for path in required_files:
        _assert_exists(path)

    holdings = _load_csv(run_dir / "holdings_daily.csv", "holdings_daily.csv")
    portfolio = _load_csv(run_dir / "portfolio_daily.csv", "portfolio_daily.csv")
    trades = _load_csv(run_dir / "trades.csv", "trades.csv")

    if "ticker" not in holdings.columns:
        _fail("holdings_daily.csv missing required column: ticker")
    if "date" not in holdings.columns:
        _fail("holdings_daily.csv missing required column: date")

    h_dates = pd.to_datetime(holdings["date"], errors="coerce").dropna().sort_values()
    if h_dates.empty:
        _fail("holdings_daily.csv has no parseable dates")

    first_trading_day, last_trading_day, n_trading_days = _date_bounds_for_window(start, end)
    if h_dates.min() > first_trading_day:
        _fail(
            f"holdings_daily.csv starts too late: {h_dates.min().date()} > expected {first_trading_day.date()}"
        )
    if h_dates.max() < last_trading_day:
        _fail(
            f"holdings_daily.csv ends too early: {h_dates.max().date()} < expected {last_trading_day.date()}"
        )
    unique_h_dates = int(h_dates.dt.normalize().nunique())
    min_dates_required = min(int(args.min_dates), n_trading_days)
    if unique_h_dates < min_dates_required:
        _fail(
            f"holdings_daily.csv has too few unique dates: {unique_h_dates} < {min_dates_required}"
        )

    if "total_equity" not in portfolio.columns:
        _fail("portfolio_daily.csv missing required column: total_equity")
    if "date" not in portfolio.columns:
        _fail("portfolio_daily.csv missing required column: date")

    p_dates = pd.to_datetime(portfolio["date"], errors="coerce").dropna().sort_values()
    if p_dates.empty:
        _fail("portfolio_daily.csv has no parseable dates")
    unique_p_dates = int(p_dates.dt.normalize().nunique())
    if unique_p_dates < min_dates_required:
        _fail(
            f"portfolio_daily.csv has too few unique dates: {unique_p_dates} < {min_dates_required}"
        )

    eq = pd.to_numeric(portfolio["total_equity"], errors="coerce")
    if not np.isfinite(eq).all():
        _fail("portfolio_daily.csv total_equity contains non-finite values")
    if (eq <= 0).any():
        _fail("portfolio_daily.csv total_equity must be > 0 for all rows")

    print(
        "[VERIFY] ok "
        f"run_id={args.run_id} "
        f"holdings_rows={len(holdings)} holdings_dates={unique_h_dates} "
        f"portfolio_rows={len(portfolio)} portfolio_dates={unique_p_dates} "
        f"trades_rows={len(trades)}"
    )


if __name__ == "__main__":
    main()

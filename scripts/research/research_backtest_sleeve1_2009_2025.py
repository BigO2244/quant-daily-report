#!/usr/bin/env python3
"""Deterministic research backtest for Sleeve 1 over 2009-2025 with regime buckets."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import struct
import zlib
import numpy as np
import pandas as pd
import yfinance as yf

from sleeves.sleeve_1 import backtest as sleeve1


FULL_WINDOW = ("full_2009_2025", "2009-01-01", "2025-12-31")
BUCKETS = [
    ("bucket_2009_2012", "2009-01-01", "2012-12-31"),
    ("bucket_2013_2016", "2013-01-01", "2016-12-31"),
    ("bucket_2017_2019", "2017-01-01", "2019-12-31"),
    ("bucket_2020_2021", "2020-01-01", "2021-12-31"),
    ("bucket_2022", "2022-01-01", "2022-12-31"),
    ("bucket_2023_2025", "2023-01-01", "2025-12-31"),
]
FAST_WINDOW = ("fast_2023_2024", "2023-01-01", "2024-12-31")


@dataclass(frozen=True)
class Window:
    name: str
    start: pd.Timestamp
    end: pd.Timestamp


def _synthetic_prices(tickers: list[str], start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    dates = pd.bdate_range(start=start, end=end)
    rows = []
    for i, ticker in enumerate(tickers):
        base = 50 + (i % 30) * 3
        phase = (i % 17) / 17.0
        for j, d in enumerate(dates):
            drift = 0.0002 + (i % 7) * 0.00003
            wave = 0.01 * np.sin((j / 23.0) + phase)
            close = base * (1 + drift) ** j * (1 + wave)
            open_ = close * (1 - 0.0015)
            high = close * 1.004
            low = close * 0.996
            vol = int(1_000_000 + (i % 20) * 50_000 + (j % 10) * 3_000)
            rows.append({"date": d, "ticker": ticker, "open": open_, "high": high, "low": low, "close": close, "volume": vol})
    return pd.DataFrame(rows)


def _to_window(items: Iterable[tuple[str, str, str]]) -> list[Window]:
    return [Window(name=n, start=pd.Timestamp(s), end=pd.Timestamp(e)) for n, s, e in items]


def _annualized_stats(daily_returns: pd.Series) -> tuple[float, float, float]:
    if daily_returns.empty:
        return np.nan, np.nan, np.nan
    vol = daily_returns.std(ddof=0) * np.sqrt(252)
    sharpe = (daily_returns.mean() * 252 / vol) if vol and not np.isnan(vol) else np.nan
    years = len(daily_returns) / 252.0
    total_return = (1 + daily_returns).prod() - 1
    cagr = (1 + total_return) ** (1 / years) - 1 if years > 0 else np.nan
    return total_return, cagr, vol, sharpe


def _max_drawdown(nav: pd.Series) -> float:
    if nav.empty:
        return np.nan
    peak = nav.cummax()
    dd = nav / peak - 1
    return float(dd.min())


def _load_spy_returns(start: pd.Timestamp, end: pd.Timestamp, synthetic: bool = False) -> pd.DataFrame:
    if synthetic:
        dates = pd.bdate_range(start=start, end=end)
        ret = 0.00025 + 0.008 * np.sin(np.arange(len(dates)) / 18.0)
        nav = np.cumprod(1 + ret)
        return pd.DataFrame({"date": dates, "spy_ret": ret, "spy_nav": nav})

    spy = yf.download(
        "SPY",
        start=start.strftime("%Y-%m-%d"),
        end=(end + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
        interval="1d",
        progress=False,
        auto_adjust=False,
        threads=False,
        timeout=30,
    )
    if spy is None or spy.empty:
        raise RuntimeError("Unable to download SPY prices.")
    if isinstance(spy.columns, pd.MultiIndex):
        spy.columns = [c[0] for c in spy.columns]
    spy = spy.reset_index().rename(columns={"Date": "date", "Close": "close", "date": "date", "close": "close"})
    spy["date"] = pd.to_datetime(spy["date"])
    spy = spy[(spy["date"] >= start) & (spy["date"] <= end)].copy()
    spy = spy.sort_values("date")
    spy["spy_ret"] = spy["close"].pct_change().fillna(0.0)
    spy["spy_nav"] = (1 + spy["spy_ret"]).cumprod()
    return spy[["date", "spy_ret", "spy_nav"]]


def _build_holdings_count(dates: pd.Series, trades_df: pd.DataFrame) -> pd.Series:
    counts = pd.Series(0, index=pd.to_datetime(dates), dtype=float)
    if trades_df is None or trades_df.empty:
        return counts
    td = trades_df.copy()
    td["entry_date"] = pd.to_datetime(td["entry_date"])
    td["exit_date"] = pd.to_datetime(td["exit_date"])
    for _, row in td.iterrows():
        mask = (counts.index >= row["entry_date"]) & (counts.index <= row["exit_date"])
        counts.loc[mask] += 1
    return counts


def run_research(start: pd.Timestamp, end: pd.Timestamp, include_buckets: bool, synthetic: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    original_download_prices = sleeve1.download_prices

    def _download_prices_full_history(tickers, period="1y", interval="1d"):
        if synthetic:
            return _synthetic_prices(list(tickers), start=start, end=end)
        prices = original_download_prices(tickers=tickers, period="max", interval=interval)
        return prices[(prices["date"] >= start) & (prices["date"] <= end)].copy()

    sleeve1.download_prices = _download_prices_full_history
    try:
        signals = sleeve1.prepare_data()
    finally:
        sleeve1.download_prices = original_download_prices

    signals = signals[(signals["date"] >= start) & (signals["date"] <= end)].copy()
    if signals.empty:
        raise RuntimeError("No sleeve 1 signals were generated for requested period.")

    windows = [Window("full_period", start, end)]
    if include_buckets:
        windows.extend(_to_window(BUCKETS))

    spy_full = _load_spy_returns(start, end, synthetic=synthetic)
    summary_rows: list[dict] = []
    full_ts: pd.DataFrame | None = None

    for w in windows:
        win_signals = signals[(signals["date"] >= w.start) & (signals["date"] <= w.end)].copy()
        if win_signals.empty:
            continue

        equity_df, trades_df = sleeve1.backtest(win_signals)
        equity_df = equity_df.sort_values("date").copy()
        equity_df["date"] = pd.to_datetime(equity_df["date"])

        port_returns = equity_df["equity"].pct_change().fillna(0.0)
        port_nav = equity_df["equity"] / float(equity_df["equity"].iloc[0])
        p_total, p_cagr, p_vol, p_sharpe = _annualized_stats(port_returns)
        p_mdd = _max_drawdown(port_nav)

        spy_win = spy_full[(spy_full["date"] >= w.start) & (spy_full["date"] <= w.end)].copy()
        s_total, s_cagr, s_vol, s_sharpe = _annualized_stats(spy_win["spy_ret"])
        s_mdd = _max_drawdown(spy_win["spy_nav"])

        holdings = _build_holdings_count(equity_df["date"], trades_df)
        avg_holdings = float(holdings.mean()) if not holdings.empty else np.nan

        summary_rows.append(
            {
                "window_name": w.name,
                "start_date": w.start.date().isoformat(),
                "end_date": w.end.date().isoformat(),
                "port_total_return": p_total,
                "port_cagr": p_cagr,
                "port_vol": p_vol,
                "port_sharpe": p_sharpe,
                "port_max_dd": p_mdd,
                "spy_total_return": s_total,
                "spy_cagr": s_cagr,
                "spy_vol": s_vol,
                "spy_sharpe": s_sharpe,
                "spy_max_dd": s_mdd,
                "avg_turnover": np.nan,
                "avg_holdings": avg_holdings,
            }
        )

        if w.name == "full_period":
            ts = equity_df[["date"]].copy()
            ts["portfolio_nav"] = port_nav.values
            ts = ts.merge(spy_win[["date", "spy_nav"]], on="date", how="left")
            ts["spy_nav"] = ts["spy_nav"].ffill().bfill()
            ts["window_name"] = w.name
            full_ts = ts

    if not summary_rows or full_ts is None:
        raise RuntimeError("Backtest did not produce required outputs.")

    return pd.DataFrame(summary_rows), full_ts




def _write_png_rgb(path: Path, image: np.ndarray) -> None:
    height, width, channels = image.shape
    if channels != 3:
        raise ValueError("Expected RGB image")
    raw = b"".join(b"\x00" + image[y].tobytes() for y in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    compressed = zlib.compress(raw, level=9)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", compressed) + chunk(b"IEND", b"")
    path.write_bytes(png)


def _write_equity_curve_png(ts_df: pd.DataFrame, path: Path) -> None:
    width, height = 1200, 600
    img = np.full((height, width, 3), 255, dtype=np.uint8)

    margin_left, margin_right = 70, 30
    margin_top, margin_bottom = 30, 50
    x0, x1 = margin_left, width - margin_right - 1
    y0, y1 = margin_top, height - margin_bottom - 1

    # axes
    img[y1, x0:x1 + 1] = 0
    img[y0:y1 + 1, x0] = 0

    nav = ts_df[["portfolio_nav", "spy_nav"]].copy().replace([np.inf, -np.inf], np.nan).dropna()
    if nav.empty:
        _write_png_rgb(path, img)
        return

    y_min = float(nav.min().min())
    y_max = float(nav.max().max())
    if y_max <= y_min:
        y_max = y_min + 1e-9

    n = len(ts_df)
    if n <= 1:
        _write_png_rgb(path, img)
        return

    def to_xy(idx: int, value: float) -> tuple[int, int]:
        x = int(x0 + (idx / (n - 1)) * (x1 - x0))
        y = int(y1 - ((value - y_min) / (y_max - y_min)) * (y1 - y0))
        return x, max(y0, min(y1, y))

    def draw_line(points: list[tuple[int, int]], color: tuple[int, int, int]) -> None:
        for (ax, ay), (bx, by) in zip(points[:-1], points[1:]):
            steps = max(abs(bx - ax), abs(by - ay), 1)
            for i in range(steps + 1):
                x = int(ax + (bx - ax) * (i / steps))
                y = int(ay + (by - ay) * (i / steps))
                img[y, x] = np.array(color, dtype=np.uint8)

    port_points = [to_xy(i, float(v)) for i, v in enumerate(ts_df["portfolio_nav"].ffill().fillna(1.0).tolist())]
    spy_points = [to_xy(i, float(v)) for i, v in enumerate(ts_df["spy_nav"].ffill().fillna(1.0).tolist())]
    draw_line(spy_points, (40, 100, 240))
    draw_line(port_points, (220, 50, 32))

    _write_png_rgb(path, img)

def save_outputs(summary_df: pd.DataFrame, ts_df: pd.DataFrame, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "sleeve1_backtest_2009_2025_summary.csv"
    ts_path = output_dir / "sleeve1_backtest_2009_2025_timeseries.csv"
    png_path = output_dir / "sleeve1_backtest_equity_curve.png"

    summary_df.to_csv(summary_path, index=False)
    ts_df.to_csv(ts_path, index=False)

    _write_equity_curve_png(ts_df, png_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=FULL_WINDOW[1])
    parser.add_argument("--end", default=FULL_WINDOW[2])
    parser.add_argument("--fast-mode", action="store_true", help="Run short deterministic test window and skip buckets")
    parser.add_argument("--output-dir", default="outputs/research")
    parser.add_argument("--synthetic", action="store_true", help="Use deterministic synthetic data (for offline tests)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.fast_mode:
        start = pd.Timestamp(FAST_WINDOW[1])
        end = pd.Timestamp(FAST_WINDOW[2])
        include_buckets = False
    else:
        start = pd.Timestamp(args.start)
        end = pd.Timestamp(args.end)
        include_buckets = (start == pd.Timestamp(FULL_WINDOW[1]) and end == pd.Timestamp(FULL_WINDOW[2]))

    summary_df, ts_df = run_research(start=start, end=end, include_buckets=include_buckets, synthetic=args.synthetic)
    save_outputs(summary_df, ts_df, Path(args.output_dir))


if __name__ == "__main__":
    main()

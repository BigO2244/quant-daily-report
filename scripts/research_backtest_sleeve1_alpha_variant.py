#!/usr/bin/env python3
"""Sleeve 1 alpha-variant research backtest (Top 5, monthly, fully invested)."""

from __future__ import annotations

import argparse
import struct
import sys
import zlib
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sleeves.sleeve_1 import backtest as sleeve1


def _annualized_stats(daily_returns: pd.Series) -> tuple[float, float, float, float]:
    if daily_returns.empty:
        return np.nan, np.nan, np.nan, np.nan
    total_return = float((1.0 + daily_returns).prod() - 1.0)
    years = len(daily_returns) / 252.0
    cagr = float((1.0 + total_return) ** (1.0 / years) - 1.0) if years > 0 else np.nan
    vol = float(daily_returns.std(ddof=0) * np.sqrt(252.0))
    sharpe = float((daily_returns.mean() * 252.0) / vol) if vol > 0 else np.nan
    return total_return, cagr, vol, sharpe


def _max_drawdown(nav: pd.Series) -> float:
    if nav.empty:
        return np.nan
    dd = nav / nav.cummax() - 1.0
    return float(dd.min())


def _beta_vs_spy(port_ret: pd.Series, spy_ret: pd.Series) -> float:
    joined = pd.concat([port_ret.rename("p"), spy_ret.rename("s")], axis=1).dropna()
    if len(joined) < 2:
        return np.nan
    var_spy = float(joined["s"].var(ddof=0))
    if var_spy == 0.0:
        return np.nan
    cov = float(np.cov(joined["p"], joined["s"], ddof=0)[0, 1])
    return cov / var_spy


def _load_spy(start: pd.Timestamp, end: pd.Timestamp, synthetic: bool = False) -> pd.DataFrame:
    if synthetic:
        dates = pd.bdate_range(start=start, end=end)
        rets = 0.0002 + 0.0075 * np.sin(np.arange(len(dates)) / 19.0)
        nav = np.cumprod(1 + rets)
        return pd.DataFrame({"date": dates, "spy_ret": rets, "spy_nav": nav})

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
    spy = spy[(spy["date"] >= start) & (spy["date"] <= end)].sort_values("date")
    spy["spy_ret"] = spy["close"].pct_change().fillna(0.0)
    spy["spy_nav"] = (1.0 + spy["spy_ret"]).cumprod()
    return spy[["date", "spy_ret", "spy_nav"]]


def _synthetic_prices(tickers: list[str], start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    dates = pd.bdate_range(start=start, end=end)
    rows: list[dict] = []
    for i, ticker in enumerate(tickers):
        base = 40 + (i % 40) * 2
        phase = (i % 13) / 13.0
        for j, d in enumerate(dates):
            drift = 0.00025 + (i % 5) * 0.00004
            wave = 0.009 * np.sin((j / 21.0) + phase)
            close = base * (1 + drift) ** j * (1 + wave)
            rows.append(
                {
                    "date": d,
                    "ticker": ticker,
                    "open": close * 0.999,
                    "high": close * 1.003,
                    "low": close * 0.997,
                    "close": close,
                    "volume": int(900_000 + (i % 15) * 40_000 + (j % 9) * 5_000),
                }
            )
    return pd.DataFrame(rows)


def run_backtest(
    start: pd.Timestamp,
    end: pd.Timestamp,
    synthetic: bool = False,
    apply_costs: bool = False,
    cost_bps: float = 25.0,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    original_download = sleeve1.download_prices

    def _download_prices_full_history(tickers, period="1y", interval="1d"):
        if synthetic:
            return _synthetic_prices(list(tickers), start=start, end=end)
        prices = original_download(tickers=tickers, period="max", interval=interval)
        return prices[(prices["date"] >= start) & (prices["date"] <= end)].copy()

    sleeve1.download_prices = _download_prices_full_history
    try:
        signals = sleeve1.prepare_data()
    finally:
        sleeve1.download_prices = original_download

    signals = signals[(signals["date"] >= start) & (signals["date"] <= end)].copy()
    if signals.empty:
        raise RuntimeError("No Sleeve 1 signals in requested window.")

    signals["date"] = pd.to_datetime(signals["date"])
    prices = (
        signals.pivot_table(index="date", columns="ticker", values="close", aggfunc="last")
        .sort_index()
        .ffill()
    )
    rets = prices.pct_change().fillna(0.0)

    ranking = signals[["date", "ticker", "final_signal"]].copy().dropna(subset=["final_signal"])
    ranking = ranking.sort_values(["date", "final_signal"], ascending=[True, False])

    dates = prices.index
    rebalance_dates = set(dates.to_series().groupby(dates.to_period("M")).head(1).tolist())

    weights = pd.Series(0.0, index=prices.columns)
    pending_weights: pd.Series | None = None
    ts_rows: list[dict] = []
    gross_nav = 1.0
    net_nav = 1.0
    turnover_events: list[float] = []
    total_cost_drag = 0.0
    cost_rate = cost_bps / 10000.0

    for i, d in enumerate(dates):
        turnover = 0.0
        cost_t = 0.0
        if pending_weights is not None:
            turnover = float((pending_weights - weights).abs().sum() / 2.0)
            turnover_events.append(turnover)
            cost_t = turnover * cost_rate if apply_costs else 0.0
            total_cost_drag += cost_t
            weights = pending_weights
            pending_weights = None
        if i == 0 and weights.sum() == 0.0:
            rebalance_dates.add(d)

        gross_ret = float((weights * rets.loc[d].fillna(0.0)).sum()) if weights.sum() > 0 else 0.0
        net_ret = gross_ret - cost_t
        gross_nav *= (1.0 + gross_ret)
        net_nav *= (1.0 + net_ret)
        ts_rows.append(
            {
                "date": d,
                "gross_portfolio_ret": gross_ret,
                "net_portfolio_ret": net_ret,
                "gross_nav": gross_nav,
                "net_nav": net_nav,
                "turnover": turnover,
                "cost": cost_t,
                "n_holdings": int((weights > 0).sum()),
            }
        )

        if d in rebalance_dates:
            day_rank = ranking[ranking["date"] == d].head(5)
            selected = [t for t in day_rank["ticker"].tolist() if t in prices.columns]
            if len(selected) < 5:
                # fill from available universe ordered by score to maintain full investment
                extra = ranking[ranking["date"] == d]["ticker"].tolist()
                for t in extra:
                    if t in prices.columns and t not in selected:
                        selected.append(t)
                    if len(selected) == 5:
                        break
            if len(selected) < 5:
                raise RuntimeError(f"Unable to select 5 names on {d.date()}")
            pending_weights = pd.Series(0.0, index=prices.columns)
            pending_weights.loc[selected[:5]] = 0.20

    ts_df = pd.DataFrame(ts_rows)
    spy = _load_spy(start=start, end=end, synthetic=synthetic)
    ts_df = ts_df.merge(spy[["date", "spy_ret", "spy_nav"]], on="date", how="left")
    ts_df["spy_ret"] = ts_df["spy_ret"].fillna(0.0)
    ts_df["spy_nav"] = ts_df["spy_nav"].ffill().bfill()

    gross_total, gross_cagr, gross_vol, gross_sharpe = _annualized_stats(ts_df["gross_portfolio_ret"])
    net_total, net_cagr, net_vol, net_sharpe = _annualized_stats(ts_df["net_portfolio_ret"])
    s_total, s_cagr, s_vol, s_sharpe = _annualized_stats(ts_df["spy_ret"])
    avg_turnover = float(np.mean(turnover_events)) if turnover_events else 0.0

    summary = pd.DataFrame(
        [
            {
                "start_date": start.date().isoformat(),
                "end_date": end.date().isoformat(),
                "gross_total_return": gross_total,
                "gross_cagr": gross_cagr,
                "gross_vol": gross_vol,
                "gross_sharpe": gross_sharpe,
                "gross_max_drawdown": _max_drawdown(ts_df["gross_nav"]),
                "gross_beta_vs_spy": _beta_vs_spy(ts_df["gross_portfolio_ret"], ts_df["spy_ret"]),
                "net_total_return": net_total,
                "net_cagr": net_cagr,
                "net_vol": net_vol,
                "net_sharpe": net_sharpe,
                "net_max_drawdown": _max_drawdown(ts_df["net_nav"]),
                "net_beta_vs_spy": _beta_vs_spy(ts_df["net_portfolio_ret"], ts_df["spy_ret"]),
                "avg_turnover": avg_turnover,
                "cost_bps": float(cost_bps),
                "total_cost_drag": float(total_cost_drag),
                "spy_total_return": s_total,
                "spy_cagr": s_cagr,
                "spy_vol": s_vol,
                "spy_sharpe": s_sharpe,
                "spy_max_drawdown": _max_drawdown(ts_df["spy_nav"]),
            }
        ]
    )

    return summary, ts_df


def _write_png_rgb(path: Path, image: np.ndarray) -> None:
    h, w, c = image.shape
    if c != 3:
        raise ValueError("Expected RGB image")
    raw = b"".join(b"\x00" + image[y].tobytes() for y in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return len(data).to_bytes(4, "big") + tag + data + (zlib.crc32(tag + data) & 0xFFFFFFFF).to_bytes(4, "big")

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, level=9)) + chunk(b"IEND", b"")
    path.write_bytes(png)


def _write_equity_curve_png(ts_df: pd.DataFrame, path: Path) -> None:
    width, height = 1200, 600
    img = np.full((height, width, 3), 255, dtype=np.uint8)
    x0, x1, y0, y1 = 70, width - 31, 30, height - 51
    img[y1, x0 : x1 + 1] = 0
    img[y0 : y1 + 1, x0] = 0

    nav = ts_df[["net_nav", "spy_nav"]].replace([np.inf, -np.inf], np.nan).dropna()
    if nav.empty or len(ts_df) <= 1:
        _write_png_rgb(path, img)
        return

    y_min, y_max = float(nav.min().min()), float(nav.max().max())
    if y_max <= y_min:
        y_max = y_min + 1e-9

    def to_xy(idx: int, value: float) -> tuple[int, int]:
        x = int(x0 + (idx / (len(ts_df) - 1)) * (x1 - x0))
        y = int(y1 - ((value - y_min) / (y_max - y_min)) * (y1 - y0))
        return x, max(y0, min(y1, y))

    def draw(points: list[tuple[int, int]], color: tuple[int, int, int]) -> None:
        for (ax, ay), (bx, by) in zip(points[:-1], points[1:]):
            steps = max(abs(bx - ax), abs(by - ay), 1)
            for i in range(steps + 1):
                x = int(ax + (bx - ax) * (i / steps))
                y = int(ay + (by - ay) * (i / steps))
                img[y, x] = np.array(color, dtype=np.uint8)

    draw([to_xy(i, float(v)) for i, v in enumerate(ts_df["spy_nav"].ffill().fillna(1.0))], (40, 100, 240))
    draw([to_xy(i, float(v)) for i, v in enumerate(ts_df["gross_nav"].ffill().fillna(1.0))], (180, 180, 180))
    draw([to_xy(i, float(v)) for i, v in enumerate(ts_df["net_nav"].ffill().fillna(1.0))], (220, 50, 32))
    _write_png_rgb(path, img)


def save_outputs(summary: pd.DataFrame, ts_df: pd.DataFrame, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary.to_csv(output_dir / "sleeve1_alpha_variant_summary.csv", index=False)
    ts_df[["date", "gross_nav", "net_nav", "spy_nav"]].to_csv(
        output_dir / "sleeve1_alpha_variant_timeseries.csv", index=False
    )
    _write_equity_curve_png(ts_df, output_dir / "sleeve1_alpha_variant_equity_curve.png")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2009-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--output-dir", default="outputs/research")
    parser.add_argument("--synthetic", action="store_true")
    parser.add_argument("--cost-bps", type=float, default=25.0)
    parser.add_argument("--apply-costs", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    start = pd.Timestamp(args.start)
    end = pd.Timestamp(args.end)
    summary, ts_df = run_backtest(
        start=start,
        end=end,
        synthetic=args.synthetic,
        apply_costs=args.apply_costs,
        cost_bps=args.cost_bps,
    )
    save_outputs(summary=summary, ts_df=ts_df, output_dir=Path(args.output_dir))


if __name__ == "__main__":
    main()

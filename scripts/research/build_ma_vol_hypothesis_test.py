from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sleeves.sleeve_1.indicators import (  # noqa: E402
    atr_normalized_momentum,
    dual_ma_filter,
    momentum_12_1,
    realized_volatility as sleeve1_realized_volatility,
    sector_relative_momentum,
)
from sleeves.sleeve_trend.indicators import compute_trend_indicators  # noqa: E402
from sleeves.sleeve_trend.selection import (  # noqa: E402
    GATE_ABOVE_200_EMA,
    GATE_ADX_MIN,
    GATE_EMA_FAST_ABOVE_SLOW,
    GATE_MIN_AVG_VOLUME,
    GATE_MIN_PRICE,
)


HORIZONS = (5, 10, 21)
STRATEGY_HORIZON = 1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate whether trailing MA and volatility signals add real predictive power."
    )
    parser.add_argument("--repo-root", default=".", help="Repository root.")
    parser.add_argument("--start-date", default="2022-01-01", help="Analysis start date (YYYY-MM-DD).")
    parser.add_argument(
        "--end-date",
        default=str(date.today()),
        help="Analysis end date inclusive (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--warmup-days",
        type=int,
        default=420,
        help="Calendar warmup days fetched before start-date for long MAs and momentum.",
    )
    parser.add_argument(
        "--output-dir",
        default="outputs/research/ma_vol_hypothesis",
        help="Directory for summary artifacts.",
    )
    parser.add_argument(
        "--price-cache",
        default="outputs/research/ma_vol_hypothesis/price_panel.parquet",
        help="Parquet cache path for downloaded prices.",
    )
    parser.add_argument(
        "--price-panel",
        default="",
        help="Optional local CSV/parquet price panel with columns date,ticker,open,high,low,close,volume,sector.",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Ignore existing cache and redownload prices.",
    )
    parser.add_argument(
        "--max-tickers",
        type=int,
        default=0,
        help="Optional cap for universe size during debugging/tests.",
    )
    return parser.parse_args()


def _read_universe(path: Path, max_tickers: int = 0) -> pd.DataFrame:
    frame = pd.read_csv(path)
    frame = frame.rename(columns={column: column.strip().lower() for column in frame.columns})
    frame = frame[["ticker", "sector"]].dropna(subset=["ticker"])
    frame["ticker"] = frame["ticker"].astype(str).str.strip().str.upper()
    frame["sector"] = frame["sector"].fillna("Unknown").astype(str).str.strip()
    frame = frame.drop_duplicates(subset=["ticker"], keep="first")
    if max_tickers > 0:
        frame = frame.head(max_tickers).copy()
    return frame.reset_index(drop=True)


def _chunked(values: list[str], size: int) -> list[list[str]]:
    return [values[index:index + size] for index in range(0, len(values), size)]


def _stack_yfinance_download(raw: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame(columns=["date", "ticker", "open", "high", "low", "close", "volume"])

    if isinstance(raw.columns, pd.MultiIndex):
        panel = raw.stack(level=1).reset_index()
        panel = panel.rename(columns={"Date": "date", "Ticker": "ticker"})
        panel.columns = [str(column).strip().lower() for column in panel.columns]
    else:
        panel = raw.reset_index()
        panel = panel.rename(columns={"Date": "date"})
        panel.columns = [str(column).strip().lower() for column in panel.columns]
        panel["ticker"] = tickers[0]

    required = {"date", "ticker", "open", "high", "low", "close", "volume"}
    missing = required.difference(panel.columns)
    if missing:
        raise ValueError(f"Downloaded panel missing columns: {sorted(missing)}")

    panel["date"] = pd.to_datetime(panel["date"]).dt.tz_localize(None)
    panel["ticker"] = panel["ticker"].astype(str).str.upper()
    panel["volume"] = pd.to_numeric(panel["volume"], errors="coerce").fillna(0.0)
    for column in ["open", "high", "low", "close"]:
        panel[column] = pd.to_numeric(panel[column], errors="coerce")
    panel = panel.dropna(subset=["close"]).reset_index(drop=True)
    return panel[["date", "ticker", "open", "high", "low", "close", "volume"]]


def _download_prices(
    universe: pd.DataFrame,
    start_date: str,
    end_date: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    tickers = universe["ticker"].dropna().astype(str).str.upper().tolist()
    all_frames: list[pd.DataFrame] = []
    failures: list[str] = []
    for batch in _chunked(tickers, 50):
        raw = yf.download(
            batch,
            start=start_date,
            end=end_date,
            auto_adjust=True,
            progress=False,
            threads=True,
        )
        frame = _stack_yfinance_download(raw, batch)
        if frame.empty:
            failures.extend(batch)
            continue
        downloaded = set(frame["ticker"].unique())
        failures.extend([ticker for ticker in batch if ticker not in downloaded])
        all_frames.append(frame)

    if not all_frames:
        raise RuntimeError("No universe price history was downloaded.")

    prices = pd.concat(all_frames, ignore_index=True)
    prices = prices.merge(universe, on="ticker", how="left")
    prices["sector"] = prices["sector"].fillna("Unknown")

    spy_raw = yf.download(
        ["SPY"],
        start=start_date,
        end=end_date,
        auto_adjust=True,
        progress=False,
        threads=False,
    )
    spy = _stack_yfinance_download(spy_raw, ["SPY"])
    spy["sector"] = "Benchmark"
    return prices, spy


def _load_price_panel(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        frame = pd.read_csv(path)
    else:
        frame = pd.read_parquet(path)
    frame.columns = [str(column).strip().lower() for column in frame.columns]
    required = {"date", "ticker", "open", "high", "low", "close", "volume"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Price panel missing columns: {sorted(missing)}")
    frame["date"] = pd.to_datetime(frame["date"]).dt.tz_localize(None)
    frame["ticker"] = frame["ticker"].astype(str).str.upper()
    frame["sector"] = frame.get("sector", pd.Series(index=frame.index, dtype="object")).fillna("Unknown")
    return frame


def _load_or_download_prices(
    *,
    universe: pd.DataFrame,
    start_date: str,
    end_date: str,
    price_panel_path: Path | None,
    price_cache_path: Path,
    force_refresh: bool,
) -> tuple[pd.DataFrame, pd.DataFrame, str, list[str]]:
    if price_panel_path is not None:
        panel = _load_price_panel(price_panel_path)
        spy = panel[panel["ticker"] == "SPY"].copy()
        prices = panel[panel["ticker"] != "SPY"].copy()
        return prices, spy, f"local_panel:{price_panel_path}", []

    if price_cache_path.exists() and not force_refresh:
        cached = _load_price_panel(price_cache_path)
        spy = cached[cached["ticker"] == "SPY"].copy()
        prices = cached[cached["ticker"] != "SPY"].copy()
        return prices, spy, f"cache:{price_cache_path}", []

    prices, spy = _download_prices(universe, start_date=start_date, end_date=end_date)
    cached = pd.concat([prices, spy], ignore_index=True)
    price_cache_path.parent.mkdir(parents=True, exist_ok=True)
    cached.to_parquet(price_cache_path, index=False)
    expected = set(universe["ticker"].unique())
    downloaded = set(prices["ticker"].unique())
    failures = sorted(expected.difference(downloaded))
    return prices, spy, "yfinance", failures


def _compute_indicator_panel(prices: pd.DataFrame) -> pd.DataFrame:
    prices = prices.copy().sort_values(["ticker", "date"]).reset_index(drop=True)

    sleeve1 = momentum_12_1(prices)
    sleeve1 = dual_ma_filter(sleeve1)
    sleeve1 = atr_normalized_momentum(sleeve1)
    sleeve1 = sector_relative_momentum(sleeve1)
    sleeve1 = sleeve1_realized_volatility(sleeve1)

    trend_frames: list[pd.DataFrame] = []
    for ticker, frame in prices.groupby("ticker", sort=False):
        trend_frame = compute_trend_indicators(frame.sort_values("date").copy())
        trend_frame["ticker"] = str(ticker)
        trend_frames.append(trend_frame)
    trend = pd.concat(trend_frames, ignore_index=True) if trend_frames else pd.DataFrame()
    trend = trend[
        [
            "date",
            "ticker",
            "ema_fast",
            "ema_slow",
            "ema_trend",
            "adx",
            "plus_di",
            "minus_di",
            "volume_sma",
            "volume_ratio",
            "golden_cross",
            "death_cross",
            "above_trend",
            "below_trend",
            "daily_return",
        ]
    ]

    panel = sleeve1.merge(trend, on=["date", "ticker"], how="left")
    panel = panel.sort_values(["ticker", "date"]).reset_index(drop=True)

    panel["trend_gate_pass"] = True
    panel["liquidity_pass"] = (panel["close"] >= GATE_MIN_PRICE) & (
        panel["volume_sma"].fillna(0) >= GATE_MIN_AVG_VOLUME
    )
    panel["trend_gate_pass"] &= panel["liquidity_pass"]
    panel["fail_liquidity"] = ~panel["liquidity_pass"]

    panel["above_trend_gate"] = panel["above_trend"].fillna(False)
    panel["ema_alignment_pass"] = (panel["ema_fast"] > panel["ema_slow"]).fillna(False)
    panel["adx_pass"] = panel["adx"].fillna(0) >= GATE_ADX_MIN

    if GATE_ABOVE_200_EMA:
        panel["trend_gate_pass"] &= panel["above_trend_gate"]
    if GATE_EMA_FAST_ABOVE_SLOW:
        panel["trend_gate_pass"] &= panel["ema_alignment_pass"]
    panel["trend_gate_pass"] &= panel["adx_pass"]

    panel["fail_above_trend"] = ~panel["above_trend_gate"]
    panel["fail_ema_alignment"] = ~panel["ema_alignment_pass"]
    panel["fail_adx"] = ~panel["adx_pass"]
    panel["fail_ma_uptrend"] = ~panel["ma_uptrend"].fillna(False)
    panel["fail_above_200d"] = ~panel["above_200d"].fillna(False)
    panel["fail_above_50d"] = ~panel["above_50d"].fillna(False)
    panel["fail_sma_alignment"] = ~(
        (panel["sma_50"] > panel["sma_200"]).fillna(False)
    )

    ticker_group = panel.groupby("ticker", group_keys=False)
    prev_above_200 = ticker_group["above_200d"].shift(1).fillna(False)
    prev_ma_uptrend = ticker_group["ma_uptrend"].shift(1).fillna(False)
    panel["cross_above_200d"] = panel["above_200d"].fillna(False) & ~prev_above_200
    panel["cross_below_200d"] = ~panel["above_200d"].fillna(False) & prev_above_200
    panel["enter_ma_uptrend"] = panel["ma_uptrend"].fillna(False) & ~prev_ma_uptrend
    panel["exit_ma_uptrend"] = ~panel["ma_uptrend"].fillna(False) & prev_ma_uptrend

    panel["next_day_return"] = ticker_group["close"].shift(-1) / panel["close"] - 1.0
    for horizon in HORIZONS:
        panel[f"fwd_{horizon}d"] = ticker_group["close"].shift(-horizon) / panel["close"] - 1.0

    valid_vol = panel["realized_vol"].notna()
    vol_rank = panel[valid_vol].groupby("date")["realized_vol"].rank(pct=True, method="average")
    panel["vol_bucket"] = "unknown"
    panel.loc[vol_rank.index[vol_rank <= (1.0 / 3.0)], "vol_bucket"] = "low"
    panel.loc[vol_rank.index[(vol_rank > (1.0 / 3.0)) & (vol_rank <= (2.0 / 3.0))], "vol_bucket"] = "mid"
    panel.loc[vol_rank.index[vol_rank > (2.0 / 3.0)], "vol_bucket"] = "high"

    if "fwd_21d" in panel.columns:
        rank_21d = panel.groupby("date")["fwd_21d"].rank(pct=True, method="average")
        panel["fwd_21d_pct_rank"] = rank_21d

    return panel


def _state_stats(sample: pd.DataFrame, value_column: str) -> dict[str, Any]:
    values = pd.to_numeric(sample[value_column], errors="coerce").dropna()
    if values.empty:
        return {
            "observations": 0,
            "mean_return": None,
            "median_return": None,
            "hit_rate": None,
        }
    return {
        "observations": int(values.shape[0]),
        "mean_return": float(values.mean()),
        "median_return": float(values.median()),
        "hit_rate": float((values > 0).mean()),
    }


def _summarize_boolean_state(panel: pd.DataFrame, state_column: str) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for horizon in HORIZONS:
        column = f"fwd_{horizon}d"
        state_true = panel[panel[state_column].fillna(False)]
        state_false = panel[~panel[state_column].fillna(False)]
        true_stats = _state_stats(state_true, column)
        false_stats = _state_stats(state_false, column)
        delta = None
        if true_stats["mean_return"] is not None and false_stats["mean_return"] is not None:
            delta = true_stats["mean_return"] - false_stats["mean_return"]
        summary[f"{horizon}d"] = {
            "true": true_stats,
            "false": false_stats,
            "mean_return_delta": delta,
        }
    return summary


def _summarize_bucket_state(panel: pd.DataFrame, bucket_column: str) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for horizon in HORIZONS:
        column = f"fwd_{horizon}d"
        horizon_summary: dict[str, Any] = {}
        for bucket in ["low", "mid", "high"]:
            horizon_summary[bucket] = _state_stats(panel[panel[bucket_column] == bucket], column)
        if (
            horizon_summary["low"]["mean_return"] is not None
            and horizon_summary["high"]["mean_return"] is not None
        ):
            horizon_summary["low_minus_high_mean_return"] = (
                horizon_summary["low"]["mean_return"] - horizon_summary["high"]["mean_return"]
            )
        else:
            horizon_summary["low_minus_high_mean_return"] = None
        summary[f"{horizon}d"] = horizon_summary
    return summary


def _summarize_events(panel: pd.DataFrame, event_column: str) -> dict[str, Any]:
    events = panel[panel[event_column].fillna(False)]
    summary: dict[str, Any] = {
        "event_count": int(events.shape[0]),
    }
    for horizon in HORIZONS:
        summary[f"{horizon}d"] = _state_stats(events, f"fwd_{horizon}d")
    return summary


def _safe_corr(lhs: pd.Series, rhs: pd.Series) -> float | None:
    data = pd.concat([lhs, rhs], axis=1).dropna()
    if len(data) < 5:
        return None
    value = data.iloc[:, 0].corr(data.iloc[:, 1])
    return float(value) if pd.notna(value) else None


def _strategy_metrics(portfolio_returns: pd.Series, benchmark_returns: pd.Series, holdings: pd.Series) -> dict[str, Any]:
    aligned = pd.concat(
        [
            portfolio_returns.rename("portfolio_return"),
            benchmark_returns.rename("benchmark_return"),
            holdings.rename("holdings"),
        ],
        axis=1,
    ).fillna({"portfolio_return": 0.0, "benchmark_return": 0.0, "holdings": 0})
    if aligned.empty:
        return {
            "days": 0,
            "total_return": None,
            "cagr": None,
            "annual_volatility": None,
            "sharpe": None,
            "max_drawdown": None,
            "avg_holdings": None,
            "benchmark_total_return": None,
            "excess_total_return": None,
            "information_ratio": None,
        }
    port = aligned["portfolio_return"].astype(float)
    bench = aligned["benchmark_return"].astype(float)
    total_return = float((1.0 + port).prod() - 1.0)
    benchmark_total_return = float((1.0 + bench).prod() - 1.0)
    annual_vol = float(port.std(ddof=1) * math.sqrt(252)) if len(port) > 1 else None
    sharpe = None
    if len(port) > 1 and port.std(ddof=1) > 0:
        sharpe = float(port.mean() / port.std(ddof=1) * math.sqrt(252))
    equity = (1.0 + port).cumprod()
    drawdown = (equity / equity.cummax() - 1.0).min()
    years = len(port) / 252.0 if len(port) > 0 else 0
    cagr = None
    if years > 0 and (1.0 + total_return) > 0:
        cagr = float((1.0 + total_return) ** (1.0 / years) - 1.0)
    excess = port - bench
    info_ratio = None
    if len(excess) > 1 and excess.std(ddof=1) > 0:
        info_ratio = float(excess.mean() / excess.std(ddof=1) * math.sqrt(252))
    return {
        "days": int(len(port)),
        "total_return": total_return,
        "cagr": cagr,
        "annual_volatility": annual_vol,
        "sharpe": sharpe,
        "max_drawdown": float(drawdown) if pd.notna(drawdown) else None,
        "avg_holdings": float(aligned["holdings"].mean()),
        "benchmark_total_return": benchmark_total_return,
        "excess_total_return": float(total_return - benchmark_total_return),
        "information_ratio": info_ratio,
    }


def _build_strategy(panel: pd.DataFrame, benchmark: pd.DataFrame, name: str, mask: pd.Series) -> dict[str, Any]:
    dates = pd.Index(sorted(panel["date"].unique()))
    strategy = panel[mask & panel["next_day_return"].notna()].copy()
    daily = strategy.groupby("date").agg(
        portfolio_return=("next_day_return", "mean"),
        holdings=("ticker", "nunique"),
    )
    daily = daily.reindex(dates, fill_value=0.0)
    benchmark_series = (
        benchmark.dropna(subset=["next_day_return"])
        .set_index("date")["next_day_return"]
        .reindex(dates, fill_value=0.0)
    )
    metrics = _strategy_metrics(daily["portfolio_return"], benchmark_series, daily["holdings"])
    metrics["name"] = name
    return metrics


def _format_reason_counts(counter: Counter[str], limit: int = 5) -> list[dict[str, Any]]:
    return [{"reason": reason, "count": int(count)} for reason, count in counter.most_common(limit)]


def _collect_reason_counts(frame: pd.DataFrame, reason_columns: list[str]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for column in reason_columns:
        label = column.replace("fail_", "").replace("_", " ")
        counter[label] += int(frame[column].fillna(False).sum())
    return counter


def _diagnostic_examples(frame: pd.DataFrame, columns: list[str], limit: int = 20) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    trimmed = frame.sort_values("fwd_21d", ascending=False).head(limit).copy()
    payload: list[dict[str, Any]] = []
    for _, row in trimmed.iterrows():
        item: dict[str, Any] = {}
        for column in columns:
            value = row.get(column)
            if isinstance(value, (pd.Timestamp, datetime)):
                item[column] = str(pd.Timestamp(value).date())
            elif isinstance(value, (np.floating, float)):
                item[column] = float(value)
            elif isinstance(value, (np.integer, int)):
                item[column] = int(value)
            elif pd.isna(value):
                item[column] = None
            else:
                item[column] = value
        payload.append(item)
    return payload


def _rows_for_csv(frame: pd.DataFrame, mapping: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    for outer_key, outer_value in mapping.items():
        if not isinstance(outer_value, dict):
            continue
        for inner_key, inner_value in outer_value.items():
            if not isinstance(inner_value, dict):
                continue
            row: dict[str, Any] = {"dimension": outer_key, "bucket": inner_key}
            row.update(inner_value)
            rows.append(row)


def _render_report(summary: dict[str, Any]) -> str:
    ma_21d = summary["ma_signal_test"]["ma_uptrend"]["21d"]
    trend_21d = summary["ma_signal_test"]["trend_gate_pass"]["21d"]
    vol_21d = summary["volatility_test"]["within_ma_uptrend"]["21d"]
    golden = summary["event_test"]["golden_cross"]["21d"]
    death = summary["event_test"]["death_cross"]["21d"]
    best_strategy = summary["strategy_comparison"][0] if summary["strategy_comparison"] else {}

    lines = [
        "# MA / Volatility Hypothesis Test",
        "",
        f"- Analysis window: `{summary['analysis_start']}` to `{summary['analysis_end']}`",
        f"- Universe size tested: `{summary['ticker_count']}` tickers",
        f"- Data source: `{summary['data_source']}`",
        f"- Observation count: `{summary['observation_count']}`",
        "",
        "## Headline Read",
        "",
        (
            f"- `ma_uptrend` 21-day mean return delta: "
            f"`{_fmt_pct(ma_21d['mean_return_delta'])}` "
            f"(pass `{_fmt_pct(ma_21d['true']['mean_return'])}` vs fail `{_fmt_pct(ma_21d['false']['mean_return'])}`)"
        ),
        (
            f"- `trend_gate_pass` 21-day mean return delta: "
            f"`{_fmt_pct(trend_21d['mean_return_delta'])}` "
            f"(pass `{_fmt_pct(trend_21d['true']['mean_return'])}` vs fail `{_fmt_pct(trend_21d['false']['mean_return'])}`)"
        ),
        (
            f"- Within MA-uptrend names, low-vol minus high-vol 21-day mean return: "
            f"`{_fmt_pct(vol_21d['low_minus_high_mean_return'])}`"
        ),
        (
            f"- Golden cross 21-day mean return: `{_fmt_pct(golden['mean_return'])}` "
            f"across `{golden['observations']}` events"
        ),
        (
            f"- Death cross 21-day mean return: `{_fmt_pct(death['mean_return'])}` "
            f"across `{death['observations']}` events"
        ),
        "",
        "## Strategy Read-Through",
        "",
        (
            f"- Best simple MA/vol strategy in this test: `{best_strategy.get('name', 'n/a')}` "
            f"with total return `{_fmt_pct(best_strategy.get('total_return'))}`, "
            f"SPY-relative excess `{_fmt_pct(best_strategy.get('excess_total_return'))}`, "
            f"Sharpe `{_fmt_num(best_strategy.get('sharpe'))}`"
        ),
        "",
        "## Missed-Winner Diagnostics",
        "",
        (
            f"- MA-filter false negatives: `{summary['missed_winner_diagnostics']['ma_false_negative_count']}` "
            f"top-decile 21-day winners blocked by the SMA filter"
        ),
        (
            f"- Trend-gate false negatives: `{summary['missed_winner_diagnostics']['trend_false_negative_count']}` "
            f"top-decile 21-day winners blocked by the trend gate"
        ),
    ]

    top_reasons = summary["missed_winner_diagnostics"]["trend_false_negative_reasons"]
    if top_reasons:
        reason_text = ", ".join(f"{item['reason']} ({item['count']})" for item in top_reasons[:5])
        lines.extend(["", f"- Most common rejection reasons on missed winners: {reason_text}"])

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            (
                "- If the MA-gated cohort is better than the ungated cohort, the MA filter is doing real selection work. "
                "If not, it is mostly delaying or blocking upside."
            ),
            (
                "- If low-vol names outperform only in drawdown control but not in forward-return terms, realized volatility "
                "should stay in sizing, not in directional ranking."
            ),
            (
                "- This test uses the current universe, so it has survivorship bias. Use it to rank hypotheses, not as a final production claim."
            ),
        ]
    )
    return "\n".join(lines) + "\n"


def _fmt_pct(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    return f"{float(value):.2%}"


def _fmt_num(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    return f"{float(value):.2f}"


def _build_summary(
    panel: pd.DataFrame,
    benchmark: pd.DataFrame,
    *,
    analysis_start: str,
    analysis_end: str,
    data_source: str,
    failed_downloads: list[str],
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    sample = panel[
        (panel["date"] >= pd.Timestamp(analysis_start))
        & (panel["date"] <= pd.Timestamp(analysis_end))
        & panel["close"].notna()
    ].copy()
    benchmark_sample = benchmark[
        (benchmark["date"] >= pd.Timestamp(analysis_start))
        & (benchmark["date"] <= pd.Timestamp(analysis_end))
    ].copy()
    benchmark_sample["next_day_return"] = (
        benchmark_sample["close"].shift(-1) / benchmark_sample["close"] - 1.0
    )

    ma_signal_test = {
        "above_200d": _summarize_boolean_state(sample, "above_200d"),
        "ma_uptrend": _summarize_boolean_state(sample, "ma_uptrend"),
        "trend_gate_pass": _summarize_boolean_state(sample, "trend_gate_pass"),
        "above_trend_gate": _summarize_boolean_state(sample, "above_trend_gate"),
        "ema_alignment_pass": _summarize_boolean_state(sample, "ema_alignment_pass"),
        "adx_pass": _summarize_boolean_state(sample, "adx_pass"),
    }

    vol_sample = sample[sample["vol_bucket"].isin(["low", "mid", "high"])].copy()
    volatility_test = {
        "all_universe": _summarize_bucket_state(vol_sample, "vol_bucket"),
        "within_ma_uptrend": _summarize_bucket_state(
            vol_sample[vol_sample["ma_uptrend"].fillna(False)],
            "vol_bucket",
        ),
        "within_trend_gate": _summarize_bucket_state(
            vol_sample[vol_sample["trend_gate_pass"].fillna(False)],
            "vol_bucket",
        ),
        "correlation_realized_vol_vs_fwd_21d_all": _safe_corr(sample["realized_vol"], sample["fwd_21d"]),
        "correlation_realized_vol_vs_fwd_21d_within_ma_uptrend": _safe_corr(
            sample.loc[sample["ma_uptrend"].fillna(False), "realized_vol"],
            sample.loc[sample["ma_uptrend"].fillna(False), "fwd_21d"],
        ),
        "correlation_realized_vol_vs_fwd_21d_within_trend_gate": _safe_corr(
            sample.loc[sample["trend_gate_pass"].fillna(False), "realized_vol"],
            sample.loc[sample["trend_gate_pass"].fillna(False), "fwd_21d"],
        ),
    }

    event_test = {
        "golden_cross": _summarize_events(sample, "golden_cross"),
        "death_cross": _summarize_events(sample, "death_cross"),
        "cross_above_200d": _summarize_events(sample, "cross_above_200d"),
        "cross_below_200d": _summarize_events(sample, "cross_below_200d"),
        "enter_ma_uptrend": _summarize_events(sample, "enter_ma_uptrend"),
        "exit_ma_uptrend": _summarize_events(sample, "exit_ma_uptrend"),
    }

    miss_reason_columns = [
        "fail_liquidity",
        "fail_above_trend",
        "fail_ema_alignment",
        "fail_adx",
        "fail_above_200d",
        "fail_above_50d",
        "fail_sma_alignment",
    ]
    future_ranked = sample.dropna(subset=["fwd_21d_pct_rank"]).copy()
    ma_false_negatives = future_ranked[
        (~future_ranked["ma_uptrend"].fillna(False)) & (future_ranked["fwd_21d_pct_rank"] >= 0.90)
    ].copy()
    trend_false_negatives = future_ranked[
        (~future_ranked["trend_gate_pass"].fillna(False)) & (future_ranked["fwd_21d_pct_rank"] >= 0.90)
    ].copy()
    trend_false_positives = future_ranked[
        (future_ranked["trend_gate_pass"].fillna(False)) & (future_ranked["fwd_21d_pct_rank"] <= 0.10)
    ].copy()

    missed_winner_diagnostics = {
        "ma_false_negative_count": int(ma_false_negatives.shape[0]),
        "trend_false_negative_count": int(trend_false_negatives.shape[0]),
        "trend_false_positive_count": int(trend_false_positives.shape[0]),
        "ma_false_negative_reasons": _format_reason_counts(
            _collect_reason_counts(ma_false_negatives, ["fail_above_200d", "fail_above_50d", "fail_sma_alignment"])
        ),
        "trend_false_negative_reasons": _format_reason_counts(
            _collect_reason_counts(trend_false_negatives, miss_reason_columns)
        ),
        "trend_false_positive_examples": _diagnostic_examples(
            trend_false_positives.sort_values("fwd_21d", ascending=True),
            [
                "date",
                "ticker",
                "sector",
                "fwd_21d",
                "realized_vol",
                "adx",
                "ema_fast",
                "ema_slow",
            ],
        ),
        "ma_false_negative_examples": _diagnostic_examples(
            ma_false_negatives,
            [
                "date",
                "ticker",
                "sector",
                "fwd_21d",
                "realized_vol",
                "above_200d",
                "above_50d",
                "sma_50",
                "sma_200",
            ],
        ),
        "trend_false_negative_examples": _diagnostic_examples(
            trend_false_negatives,
            [
                "date",
                "ticker",
                "sector",
                "fwd_21d",
                "realized_vol",
                "adx",
                "above_trend_gate",
                "ema_alignment_pass",
                "liquidity_pass",
            ],
        ),
    }

    strategy_rows = [
        _build_strategy(sample, benchmark_sample, "universe_equal_weight", sample["close"].notna()),
        _build_strategy(sample, benchmark_sample, "ma_uptrend_equal_weight", sample["ma_uptrend"].fillna(False)),
        _build_strategy(sample, benchmark_sample, "trend_gate_equal_weight", sample["trend_gate_pass"].fillna(False)),
        _build_strategy(
            sample,
            benchmark_sample,
            "ma_uptrend_low_vol",
            sample["ma_uptrend"].fillna(False) & sample["vol_bucket"].eq("low"),
        ),
        _build_strategy(
            sample,
            benchmark_sample,
            "trend_gate_low_vol",
            sample["trend_gate_pass"].fillna(False) & sample["vol_bucket"].eq("low"),
        ),
        _build_strategy(
            sample,
            benchmark_sample,
            "ma_uptrend_high_vol",
            sample["ma_uptrend"].fillna(False) & sample["vol_bucket"].eq("high"),
        ),
    ]
    strategy_rows = sorted(
        strategy_rows,
        key=lambda row: (
            row.get("excess_total_return") is not None,
            row.get("excess_total_return") or -999.0,
        ),
        reverse=True,
    )
    strategy_frame = pd.DataFrame(strategy_rows)

    recommendations: list[str] = []
    ma_delta = ma_signal_test["ma_uptrend"]["21d"]["mean_return_delta"]
    trend_delta = ma_signal_test["trend_gate_pass"]["21d"]["mean_return_delta"]
    low_minus_high = volatility_test["within_ma_uptrend"]["21d"]["low_minus_high_mean_return"]
    golden_mean = event_test["golden_cross"]["21d"]["mean_return"]
    death_mean = event_test["death_cross"]["21d"]["mean_return"]
    if ma_delta is not None:
        if ma_delta <= 0:
            recommendations.append("The SMA uptrend filter does not improve 21-day expectancy; test a looser or ranking-based trend filter.")
        else:
            recommendations.append("The SMA uptrend filter improves 21-day expectancy; keep it, but measure how many high-return names it still blocks.")
    if trend_delta is not None:
        if trend_delta <= 0:
            recommendations.append("The full trend gate is probably too strict; inspect ADX and trend-filter rejections first.")
        else:
            recommendations.append("The full trend gate adds expectancy on average; focus on its false negatives instead of discarding it wholesale.")
    if low_minus_high is not None:
        if low_minus_high <= 0:
            recommendations.append("Lower realized volatility is not adding directional alpha inside MA-uptrend names; keep vol in sizing, not in score.")
        else:
            recommendations.append("Lower realized volatility appears additive within MA-uptrend names; retain it as a secondary ranking or weighting input.")
    if golden_mean is not None and death_mean is not None and golden_mean <= death_mean:
        recommendations.append("Cross events look lagged; prefer state filters over waiting for crossovers as primary entries.")
    if trend_false_negatives.shape[0] > 0:
        top_reason = missed_winner_diagnostics["trend_false_negative_reasons"][:1]
        if top_reason:
            recommendations.append(
                f"Most missed winners were rejected for `{top_reason[0]['reason']}`; that gate is the first candidate for threshold review."
            )

    summary = {
        "as_of_date": analysis_end,
        "analysis_start": analysis_start,
        "analysis_end": analysis_end,
        "data_source": data_source,
        "failed_downloads": failed_downloads,
        "ticker_count": int(sample["ticker"].nunique()),
        "observation_count": int(sample.shape[0]),
        "ma_signal_test": ma_signal_test,
        "volatility_test": volatility_test,
        "event_test": event_test,
        "missed_winner_diagnostics": missed_winner_diagnostics,
        "strategy_comparison": strategy_rows,
        "recommendations": recommendations,
    }

    state_rows: list[dict[str, Any]] = []
    for signal_name, signal_summary in ma_signal_test.items():
        for horizon, horizon_summary in signal_summary.items():
            for state_name in ["true", "false"]:
                row = {"signal": signal_name, "horizon": horizon, "state": state_name}
                row.update(horizon_summary[state_name])
                row["mean_return_delta"] = horizon_summary["mean_return_delta"]
                state_rows.append(row)
    state_frame = pd.DataFrame(state_rows)

    vol_rows: list[dict[str, Any]] = []
    for scope_name, scope_summary in volatility_test.items():
        if not isinstance(scope_summary, dict):
            continue
        for horizon, horizon_summary in scope_summary.items():
            if not isinstance(horizon_summary, dict):
                continue
            for bucket in ["low", "mid", "high"]:
                if bucket not in horizon_summary:
                    continue
                row = {"scope": scope_name, "horizon": horizon, "bucket": bucket}
                row.update(horizon_summary[bucket])
                row["low_minus_high_mean_return"] = horizon_summary.get("low_minus_high_mean_return")
                vol_rows.append(row)
    vol_frame = pd.DataFrame(vol_rows)

    diagnostic_rows: list[dict[str, Any]] = []
    for example in summary["missed_winner_diagnostics"]["trend_false_negative_examples"]:
        diagnostic_rows.append({"bucket": "trend_false_negative", **example})
    for example in summary["missed_winner_diagnostics"]["ma_false_negative_examples"]:
        diagnostic_rows.append({"bucket": "ma_false_negative", **example})
    for example in summary["missed_winner_diagnostics"]["trend_false_positive_examples"]:
        diagnostic_rows.append({"bucket": "trend_false_positive", **example})
    diagnostic_examples = pd.DataFrame(diagnostic_rows)
    return summary, state_frame, vol_frame, strategy_frame, diagnostic_examples


def main() -> None:
    args = _parse_args()
    repo_root = Path(args.repo_root).resolve()
    output_dir = (repo_root / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    universe = _read_universe(repo_root / "data" / "universe.csv", max_tickers=args.max_tickers)
    analysis_start = pd.Timestamp(args.start_date)
    analysis_end = pd.Timestamp(args.end_date)
    fetch_start = (analysis_start - pd.Timedelta(days=args.warmup_days)).strftime("%Y-%m-%d")
    fetch_end = (analysis_end + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    price_panel_path = Path(args.price_panel).resolve() if args.price_panel else None
    price_cache_path = (repo_root / args.price_cache).resolve()

    prices, benchmark, data_source, failed_downloads = _load_or_download_prices(
        universe=universe,
        start_date=fetch_start,
        end_date=fetch_end,
        price_panel_path=price_panel_path,
        price_cache_path=price_cache_path,
        force_refresh=args.force_refresh,
    )

    indicator_panel = _compute_indicator_panel(prices)
    summary, state_frame, vol_frame, strategy_frame, diagnostic_frame = _build_summary(
        indicator_panel,
        benchmark,
        analysis_start=args.start_date,
        analysis_end=args.end_date,
        data_source=data_source,
        failed_downloads=failed_downloads,
    )

    summary_path = output_dir / "summary.json"
    report_path = output_dir / "report.md"
    state_path = output_dir / "state_summary.csv"
    vol_path = output_dir / "volatility_summary.csv"
    strategy_path = output_dir / "strategy_comparison.csv"
    examples_path = output_dir / "diagnostic_examples.csv"

    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    state_frame.to_csv(state_path, index=False)
    vol_frame.to_csv(vol_path, index=False)
    strategy_frame.to_csv(strategy_path, index=False)
    diagnostic_frame.to_csv(examples_path, index=False)


if __name__ == "__main__":
    main()

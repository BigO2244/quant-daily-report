from __future__ import annotations

import json
import math
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from datastore import DEBT_TAGS, EQUITY_TAGS, REVENUE_TAGS, DataStore
from sleeves.sleeve_mean_reversion.selection import (
    GATE_MIN_AVG_VOLUME as MR_MIN_AVG_VOLUME,
    GATE_MIN_PRICE as MR_MIN_PRICE,
    REQUIRE_ABOVE_200D,
    RSI_OVERSOLD_MAX,
    TOP_N_DEFAULT as MEANREV_TOP_N,
    W_BB_PCTB,
    W_RSI,
    W_STR,
    W_ZSCORE_20D,
    W_ZSCORE_60D,
    ZSCORE_MAX,
    build_mean_reversion_signals,
    select_and_weight as meanrev_select_and_weight,
)
from sleeves.sleeve_quality.indicators import (
    accruals_signal,
    composite_quality_score,
    debt_equity_signal,
    gross_margin_signal,
    roe_signal,
)
from sleeves.sleeve_quality.selection import (
    GATE_MIN_AVG_VOLUME as QUALITY_MIN_AVG_VOLUME,
    GATE_MIN_PRICE as QUALITY_MIN_PRICE,
    MIN_COVERAGE as QUALITY_MIN_COVERAGE,
    TOP_N_DEFAULT as QUALITY_TOP_N,
    select_and_weight as quality_select_and_weight,
)

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning)

UNIVERSE_PATH = ROOT / "data" / "universe.csv"
FUNDAMENTAL_PATH = ROOT / "data" / "fundamental"
RESULTS_PATH = ROOT / "research" / "backtest_results_quality_meanrev.json"
PLOT_PATH = ROOT / "research" / "backtest_equity_curves_quality_meanrev.png"

PRICE_YEARS = 3
DOWNLOAD_CHUNK_SIZE = 40
IC_FORWARD_DAYS = 5
QUALITY_REBALANCE_DAYS = 21
QUALITY_WARMUP_DAYS = 40
MEANREV_WARMUP_DAYS = 200
MIN_IC_CROSS_SECTION = 10
LOOKAHEAD_IC_THRESHOLD = 0.15
QUALITY_ENHANCED_MIN_SIGNAL_COVERAGE = 3
QUALITY_ENHANCED_MIN_SECTOR_PEERS = 4
QUALITY_ENHANCED_N_QUARTERS = 8
QUALITY_ENHANCED_REVENUE_GROWTH_CLIP = (-0.75, 1.50)
QUALITY_ENHANCED_GROSS_MARGIN_CLIP = (-0.25, 0.90)
QUALITY_ENHANCED_DEBT_EQUITY_CLIP = (0.0, 10.0)
QUALITY_ENHANCED_WEIGHTS = {
    "sector_roe": 0.30,
    "gross_margin_stability": 0.20,
    "accruals": 0.20,
    "debt_equity_trend": 0.15,
    "revenue_growth_consistency": 0.15,
}


def load_universe() -> tuple[list[str], dict[str, str]]:
    df = pd.read_csv(UNIVERSE_PATH)
    df["ticker"] = df["ticker"].astype(str).str.upper().str.strip()
    df = df[df["ticker"] != ""].drop_duplicates(subset=["ticker"])
    sectors = {
        str(row["ticker"]): str(row.get("sector") or "Unknown")
        for _, row in df.iterrows()
    }
    return df["ticker"].tolist(), sectors


def _chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _extract_ticker_frame(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        if ticker not in raw.columns.get_level_values(0):
            return pd.DataFrame()
        frame = raw[ticker].copy()
    else:
        frame = raw.copy()
    if frame.empty:
        return pd.DataFrame()
    frame = frame.dropna(how="all")
    if frame.empty:
        return pd.DataFrame()
    frame = frame.reset_index()
    frame.columns = [str(col).lower() for col in frame.columns]
    required = {"date", "open", "high", "low", "close", "volume"}
    if not required.issubset(frame.columns):
        return pd.DataFrame()
    frame["ticker"] = ticker
    return frame[["date", "ticker", "open", "high", "low", "close", "volume"]]


def download_prices(tickers: list[str]) -> pd.DataFrame:
    all_tickers = sorted(set(tickers + ["SPY"]))
    frames: list[pd.DataFrame] = []
    missing: list[str] = []
    print(f"Downloading {PRICE_YEARS} years of price history for {len(all_tickers)} tickers via yfinance...")
    for chunk in _chunked(all_tickers, DOWNLOAD_CHUNK_SIZE):
        raw = yf.download(
            tickers=chunk,
            period=f"{PRICE_YEARS}y",
            interval="1d",
            auto_adjust=True,
            progress=False,
            group_by="ticker",
            threads=True,
        )
        for ticker in chunk:
            frame = _extract_ticker_frame(raw, ticker)
            if frame.empty:
                missing.append(ticker)
                continue
            frames.append(frame)
    if not frames:
        raise RuntimeError(
            "yfinance returned no usable price data. "
            "If sandbox DNS is blocked, rerun this script with network-enabled escalation."
        )
    prices = pd.concat(frames, ignore_index=True)
    prices["date"] = pd.to_datetime(prices["date"])
    prices = prices.drop_duplicates(subset=["date", "ticker"]).sort_values(["ticker", "date"]).reset_index(drop=True)
    if missing:
        print(f"Missing or empty yfinance downloads: {', '.join(sorted(set(missing))[:20])}")
    return prices


def build_price_features(prices: pd.DataFrame) -> pd.DataFrame:
    df = prices.copy().sort_values(["ticker", "date"])
    df["daily_ret"] = df.groupby("ticker")["close"].pct_change()
    df["realized_vol_20"] = (
        df.groupby("ticker")["daily_ret"]
        .transform(lambda x: x.rolling(20, min_periods=10).std())
        * math.sqrt(252.0)
    ).clip(lower=0.05, upper=1.50)
    df["avg_volume_20d"] = df.groupby("ticker")["volume"].transform(
        lambda x: x.rolling(20, min_periods=5).mean()
    )
    return df


def zscore_cross_section(series: pd.Series) -> pd.Series:
    clean = series.dropna()
    if len(clean) < 3:
        return pd.Series(np.nan, index=series.index)
    mean = clean.mean()
    std = clean.std()
    if pd.isna(std) or std < 1e-10:
        return pd.Series(0.0, index=series.index)
    return (series - mean) / std


def group_relative_zscore(
    series: pd.Series,
    groups: pd.Series,
    *,
    min_group_size: int = QUALITY_ENHANCED_MIN_SECTOR_PEERS,
) -> pd.Series:
    df = pd.DataFrame({"value": series, "group": groups}, index=series.index)
    global_scores = zscore_cross_section(series)
    out = pd.Series(np.nan, index=series.index, dtype=float)

    for _, group_frame in df.groupby("group", dropna=False):
        out.loc[group_frame.index] = zscore_cross_section(group_frame["value"])

    counts = df.groupby("group", dropna=False)["value"].transform(lambda x: x.notna().sum())
    return out.where(counts >= min_group_size, global_scores)


def weighted_composite_score(signals: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    total_weight = 0.0
    weighted_sum = pd.Series(0.0, index=signals.index, dtype=float)
    available_weight = pd.Series(0.0, index=signals.index, dtype=float)

    for column, weight in weights.items():
        series = signals[column]
        weighted_sum = weighted_sum.add(series.fillna(0.0) * weight, fill_value=0.0)
        available_weight = available_weight.add(series.notna().astype(float) * weight, fill_value=0.0)
        total_weight += weight

    score = weighted_sum / available_weight.replace(0.0, np.nan)
    return score.where(available_weight >= max(total_weight * 0.40, 1e-9))


def linear_slope(series: pd.Series) -> float | None:
    clean = series.dropna()
    if len(clean) < 3:
        return None
    x = np.arange(len(clean), dtype=float)
    slope, _ = np.polyfit(x, clean.to_numpy(dtype=float), 1)
    return float(slope)


def _prepare_period_series(
    df: pd.DataFrame,
    *,
    tag: str,
    cutoff: pd.Timestamp,
    forms: set[str],
    single_quarter_only: bool,
) -> pd.DataFrame:
    subset = df[
        (df["tag"] == tag)
        & (df["filed_date"] <= cutoff)
        & (df["form"].isin(forms))
    ].copy()
    if subset.empty:
        return pd.DataFrame(columns=["period_end", "value"])

    subset = subset.dropna(subset=["period_end", "value"])
    if subset.empty:
        return pd.DataFrame(columns=["period_end", "value"])

    if single_quarter_only and "period_start" in subset.columns:
        span = (subset["period_end"] - subset["period_start"]).dt.days
        subset = subset[span.between(60, 120)]
    if subset.empty:
        return pd.DataFrame(columns=["period_end", "value"])

    subset = (
        subset.sort_values("filed_date")
        .drop_duplicates(subset=["period_end"], keep="last")
        .sort_values("period_end")
    )
    return subset[["period_end", "value"]]


def quarterly_series_from_tags(
    df: pd.DataFrame | None,
    *,
    tags: list[str],
    cutoff: pd.Timestamp,
    n_quarters: int = QUALITY_ENHANCED_N_QUARTERS,
    flow_metric: bool = True,
) -> pd.Series:
    if df is None or df.empty:
        return pd.Series(dtype=float)

    merged: pd.DataFrame | None = None
    forms = {"10-Q"} if flow_metric else {"10-Q", "10-K"}
    for tag in tags:
        part = _prepare_period_series(
            df,
            tag=tag,
            cutoff=cutoff,
            forms=forms,
            single_quarter_only=flow_metric,
        )
        if part.empty:
            continue
        part = part.rename(columns={"value": tag}).set_index("period_end")
        merged = part if merged is None else merged.join(part, how="outer")

    if merged is None or merged.empty:
        return pd.Series(dtype=float)

    values = pd.Series(np.nan, index=merged.index, dtype=float)
    for tag in tags:
        if tag in merged.columns:
            values = values.fillna(pd.to_numeric(merged[tag], errors="coerce"))

    return values.dropna().sort_index().tail(n_quarters)


def compute_quality_enhanced_snapshot(
    *,
    tickers: list[str],
    as_of_date: pd.Timestamp,
    ds: DataStore,
    price_features: pd.DataFrame,
    sectors: dict[str, str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    as_of_str = as_of_date.strftime("%Y-%m-%d")
    cutoff = pd.Timestamp(as_of_date)
    rows: list[dict[str, object]] = []

    for ticker in tickers:
        sector = sectors.get(ticker, "Unknown")
        raw_df = ds._load(ticker)

        roe_raw = ds.get_roe(ticker, as_of_str)
        accruals_raw = ds.get_accruals_ratio(ticker, as_of_str)

        revenue_q = quarterly_series_from_tags(
            raw_df,
            tags=REVENUE_TAGS,
            cutoff=cutoff,
            flow_metric=True,
        )
        gross_profit_q = quarterly_series_from_tags(
            raw_df,
            tags=["GrossProfit"],
            cutoff=cutoff,
            flow_metric=True,
        )
        if not revenue_q.empty and not gross_profit_q.empty:
            quarterly_margin = (gross_profit_q / revenue_q.replace(0.0, np.nan)).dropna()
            quarterly_margin = quarterly_margin.clip(*QUALITY_ENHANCED_GROSS_MARGIN_CLIP)
        else:
            quarterly_margin = pd.Series(dtype=float)

        debt_q = quarterly_series_from_tags(
            raw_df,
            tags=DEBT_TAGS,
            cutoff=cutoff,
            flow_metric=False,
        )
        equity_q = quarterly_series_from_tags(
            raw_df,
            tags=EQUITY_TAGS,
            cutoff=cutoff,
            flow_metric=False,
        )
        if not debt_q.empty and not equity_q.empty:
            debt_equity_q = (debt_q / equity_q.replace(0.0, np.nan)).dropna()
            debt_equity_q = debt_equity_q.clip(*QUALITY_ENHANCED_DEBT_EQUITY_CLIP)
        else:
            debt_equity_q = pd.Series(dtype=float)

        revenue_yoy = (revenue_q / revenue_q.shift(4) - 1.0).dropna()
        revenue_yoy = revenue_yoy.clip(*QUALITY_ENHANCED_REVENUE_GROWTH_CLIP)

        gm_stability_raw = None
        if len(quarterly_margin) >= 4:
            gm_stability_raw = float(quarterly_margin.mean() - quarterly_margin.std(ddof=0))

        debt_trend_raw = None
        debt_slope = linear_slope(debt_equity_q)
        if debt_slope is not None:
            debt_trend_raw = float(-debt_slope)

        revenue_consistency_raw = None
        if len(revenue_yoy) >= 3:
            revenue_consistency_raw = float(revenue_yoy.mean() - revenue_yoy.std(ddof=0))

        rows.append(
            {
                "ticker": ticker,
                "sector": sector,
                "roe": roe_raw,
                "accruals": accruals_raw,
                "gross_margin_stability": gm_stability_raw,
                "debt_equity_trend": debt_trend_raw,
                "revenue_growth_consistency": revenue_consistency_raw,
            }
        )

    snapshot = pd.DataFrame(rows).set_index("ticker")

    price_snap = price_features[price_features["date"] == as_of_date].copy()
    if not price_snap.empty:
        price_snap = price_snap.set_index("ticker")
        snapshot["close"] = price_snap["close"]
        snapshot["realized_vol"] = price_snap["realized_vol_20"]
        snapshot["avg_volume_20d"] = price_snap["avg_volume_20d"]

    signal_df = pd.DataFrame(index=snapshot.index)
    signal_df["sector_roe"] = group_relative_zscore(
        snapshot["roe"].clip(lower=-0.5, upper=2.0),
        snapshot["sector"],
    )
    signal_df["gross_margin_stability"] = zscore_cross_section(snapshot["gross_margin_stability"])
    signal_df["accruals"] = accruals_signal(snapshot["accruals"])
    signal_df["debt_equity_trend"] = zscore_cross_section(snapshot["debt_equity_trend"])
    signal_df["revenue_growth_consistency"] = zscore_cross_section(snapshot["revenue_growth_consistency"])

    snapshot["n_signals"] = signal_df.notna().sum(axis=1)
    snapshot["quality_score"] = weighted_composite_score(signal_df, QUALITY_ENHANCED_WEIGHTS)
    signal_df["quality_score"] = snapshot["quality_score"]
    return snapshot, signal_df


def pct_rank_lower_better(series: pd.Series) -> pd.Series:
    return series.rank(pct=True, na_option="top", ascending=True) * 100.0


def compute_quality_snapshot(
    *,
    tickers: list[str],
    as_of_date: pd.Timestamp,
    ds: DataStore,
    price_features: pd.DataFrame,
    sectors: dict[str, str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    as_of_str = as_of_date.strftime("%Y-%m-%d")
    prior_year_str = (as_of_date - pd.DateOffset(years=1)).strftime("%Y-%m-%d")

    roe_raw = pd.Series({ticker: ds.get_roe(ticker, as_of_str) for ticker in tickers})
    gross_margin_raw = pd.Series({ticker: ds.get_gross_margin(ticker, as_of_str) for ticker in tickers})
    accruals_raw = pd.Series({ticker: ds.get_accruals_ratio(ticker, as_of_str) for ticker in tickers})
    debt_equity_raw = pd.Series({ticker: ds.get_debt_equity_ratio(ticker, as_of_str) for ticker in tickers})
    revenue_now = pd.Series({ticker: ds.get_revenue_ttm(ticker, as_of_str) for ticker in tickers})
    revenue_prior = pd.Series({ticker: ds.get_revenue_ttm(ticker, prior_year_str) for ticker in tickers})

    revenue_growth_raw = pd.Series(np.nan, index=tickers, dtype=float)
    valid_growth = revenue_prior.notna() & (revenue_prior > 0) & revenue_now.notna()
    revenue_growth_raw.loc[valid_growth] = (revenue_now.loc[valid_growth] / revenue_prior.loc[valid_growth]) - 1.0

    snapshot = pd.DataFrame(
        {
            "roe": roe_raw,
            "gross_margin": gross_margin_raw,
            "accruals": accruals_raw,
            "debt_equity": debt_equity_raw,
            "revenue_growth": revenue_growth_raw,
        },
        index=tickers,
    )
    snapshot["n_signals"] = snapshot[["roe", "gross_margin", "accruals", "debt_equity", "revenue_growth"]].notna().sum(axis=1)
    snapshot["sector"] = pd.Series(sectors)

    price_snap = price_features[price_features["date"] == as_of_date].copy()
    if not price_snap.empty:
        price_snap = price_snap.set_index("ticker")
        snapshot["close"] = price_snap["close"]
        snapshot["realized_vol"] = price_snap["realized_vol_20"]
        snapshot["avg_volume_20d"] = price_snap["avg_volume_20d"]

    score_df = pd.DataFrame(index=snapshot.index)
    score_df["roe"] = roe_signal(snapshot["roe"])
    score_df["gross_margin"] = gross_margin_signal(snapshot["gross_margin"])
    score_df["accruals"] = accruals_signal(snapshot["accruals"])
    score_df["debt_equity"] = debt_equity_signal(snapshot["debt_equity"])
    score_df["revenue_growth"] = zscore_cross_section(snapshot["revenue_growth"].clip(lower=-0.5, upper=2.0))
    snapshot["quality_score"] = composite_quality_score(score_df.to_dict(orient="series"))
    score_df["quality_score"] = snapshot["quality_score"]
    return snapshot, score_df


def apply_quality_ic_gates(snapshot: pd.DataFrame, score_df: pd.DataFrame) -> pd.DataFrame:
    eligible = snapshot.copy()
    min_coverage = QUALITY_MIN_COVERAGE
    if {"sector_roe", "gross_margin_stability", "debt_equity_trend", "revenue_growth_consistency"}.intersection(score_df.columns):
        min_coverage = QUALITY_ENHANCED_MIN_SIGNAL_COVERAGE
    eligible = eligible[eligible["n_signals"] >= min_coverage]
    if "close" in eligible.columns:
        eligible = eligible[eligible["close"].fillna(0.0) >= QUALITY_MIN_PRICE]
    if "avg_volume_20d" in eligible.columns:
        eligible = eligible[eligible["avg_volume_20d"].fillna(0.0) >= QUALITY_MIN_AVG_VOLUME]
    eligible = eligible.dropna(subset=["quality_score"])
    if eligible.empty:
        return pd.DataFrame()
    return score_df.loc[eligible.index].copy()


def compute_meanrev_signal_scores(signal_panel: pd.DataFrame, as_of_date: pd.Timestamp) -> pd.DataFrame:
    snap = signal_panel[signal_panel["date"] == as_of_date].copy()
    return compute_meanrev_signal_scores_from_snapshot(snap)


def compute_meanrev_signal_scores_from_snapshot(snap: pd.DataFrame) -> pd.DataFrame:
    if snap.empty:
        return pd.DataFrame()

    eligible = snap.copy()
    if REQUIRE_ABOVE_200D and "above_200d" in eligible.columns:
        eligible = eligible[eligible["above_200d"].fillna(False)]
    if "close" in eligible.columns:
        eligible = eligible[eligible["close"].fillna(0.0) >= MR_MIN_PRICE]
    if "avg_volume_20d" in eligible.columns:
        eligible = eligible[eligible["avg_volume_20d"].fillna(0.0) >= MR_MIN_AVG_VOLUME]
    if "rsi_14" in eligible.columns:
        eligible = eligible[eligible["rsi_14"].fillna(50.0) <= RSI_OVERSOLD_MAX]
    if "zscore_20d" in eligible.columns:
        eligible = eligible[eligible["zscore_20d"].fillna(0.0) <= ZSCORE_MAX]
    if eligible.empty:
        return pd.DataFrame()

    scores = pd.DataFrame(index=eligible["ticker"].astype(str))
    scores["rsi_14"] = pct_rank_lower_better(eligible["rsi_14"])
    scores["zscore_20d"] = pct_rank_lower_better(eligible["zscore_20d"])
    scores["zscore_60d"] = pct_rank_lower_better(eligible["zscore_60d"])
    scores["bb_pct_b"] = pct_rank_lower_better(eligible["bb_pct_b"])
    scores["ret_21d_rev"] = pct_rank_lower_better(eligible["ret_21d_rev"])
    scores["score"] = (
        W_RSI * scores["rsi_14"]
        + W_ZSCORE_20D * scores["zscore_20d"]
        + W_ZSCORE_60D * scores["zscore_60d"]
        + W_BB_PCTB * scores["bb_pct_b"]
        + W_STR * scores["ret_21d_rev"]
    )
    return scores


def meanrev_weights_from_snapshot(snap: pd.DataFrame) -> dict[str, float]:
    if snap.empty:
        return {}
    eligible = snap.copy()
    if REQUIRE_ABOVE_200D and "above_200d" in eligible.columns:
        eligible = eligible[eligible["above_200d"].fillna(False)]
    if "close" in eligible.columns:
        eligible = eligible[eligible["close"].fillna(0.0) >= MR_MIN_PRICE]
    if "avg_volume_20d" in eligible.columns:
        eligible = eligible[eligible["avg_volume_20d"].fillna(0.0) >= MR_MIN_AVG_VOLUME]
    if "rsi_14" in eligible.columns:
        eligible = eligible[eligible["rsi_14"].fillna(50.0) <= RSI_OVERSOLD_MAX]
    if "zscore_20d" in eligible.columns:
        eligible = eligible[eligible["zscore_20d"].fillna(0.0) <= ZSCORE_MAX]
    if eligible.empty:
        return {}

    scores = compute_meanrev_signal_scores_from_snapshot(eligible)
    if scores.empty:
        return {}
    eligible = eligible.set_index("ticker")
    eligible["score"] = scores["score"]
    selected = eligible.nlargest(MEANREV_TOP_N, "score").copy().reset_index()
    if selected.empty:
        return {}

    if "realized_vol" in selected.columns and selected["realized_vol"].notna().any():
        inv_vol = 1.0 / selected["realized_vol"].fillna(0.20).clip(lower=0.05, upper=1.50)
    else:
        inv_vol = pd.Series(1.0, index=selected.index)

    raw_weights = inv_vol / inv_vol.sum()
    raw_weights = raw_weights.clip(upper=0.35)
    raw_weights = raw_weights / raw_weights.sum()
    raw_weights = raw_weights.where(raw_weights >= 0.02, other=0.0)
    if raw_weights.sum() <= 0:
        return {}
    raw_weights = raw_weights / raw_weights.sum()
    selected["target_weight"] = raw_weights.values
    return {
        str(row["ticker"]).upper(): float(row["target_weight"])
        for _, row in selected[selected["target_weight"] > 0.0].iterrows()
    }


def forward_returns(close_wide: pd.DataFrame, as_of_date: pd.Timestamp, horizon: int = IC_FORWARD_DAYS) -> pd.Series:
    dates = list(close_wide.index)
    date_to_idx = {date: idx for idx, date in enumerate(dates)}
    idx = date_to_idx.get(as_of_date)
    if idx is None or idx + horizon >= len(dates):
        return pd.Series(dtype=float)
    future_date = dates[idx + horizon]
    current = close_wide.loc[as_of_date]
    future = close_wide.loc[future_date]
    return (future / current.clip(lower=0.01)) - 1.0


def spearman_corr(left: pd.Series, right: pd.Series) -> float | None:
    valid = left.dropna().index.intersection(right.dropna().index)
    if len(valid) < MIN_IC_CROSS_SECTION:
        return None
    left_rank = left.loc[valid].rank()
    right_rank = right.loc[valid].rank()
    corr = left_rank.corr(right_rank)
    return float(corr) if pd.notna(corr) else None


def record_signal_ics(
    *,
    ic_records: list[dict[str, object]],
    signal_scores: pd.DataFrame,
    close_wide: pd.DataFrame,
    as_of_date: pd.Timestamp,
) -> None:
    if signal_scores.empty:
        return
    fwd = forward_returns(close_wide, as_of_date)
    if fwd.empty:
        return
    for column in signal_scores.columns:
        series = signal_scores[column].dropna()
        valid = series.index.intersection(fwd.dropna().index)
        if len(valid) < MIN_IC_CROSS_SECTION:
            continue
        ic = spearman_corr(series.loc[valid], fwd.loc[valid])
        if ic is not None:
            ic_records.append(
                {
                    "date": as_of_date.strftime("%Y-%m-%d"),
                    "signal": column,
                    "ic": ic,
                }
            )


def update_open_trades(
    *,
    open_trades: dict[str, dict[str, object]],
    previous_weights: dict[str, float],
    new_weights: dict[str, float],
    effective_date: pd.Timestamp,
    close_wide: pd.DataFrame,
    date_to_idx: dict[pd.Timestamp, int],
    completed_trades: list[dict[str, object]],
) -> None:
    prev_tickers = {ticker for ticker, weight in previous_weights.items() if weight > 0.0}
    new_tickers = {ticker for ticker, weight in new_weights.items() if weight > 0.0}

    exiting = prev_tickers - new_tickers
    entering = new_tickers - prev_tickers

    for ticker in sorted(exiting):
        trade = open_trades.pop(ticker, None)
        if trade is None:
            continue
        exit_price = close_wide.at[effective_date, ticker] if ticker in close_wide.columns else np.nan
        entry_price = trade.get("entry_price")
        entry_date = trade.get("entry_date")
        entry_idx = date_to_idx.get(entry_date)
        exit_idx = date_to_idx.get(effective_date)
        completed_trades.append(
            {
                "ticker": ticker,
                "entry_date": entry_date.strftime("%Y-%m-%d") if isinstance(entry_date, pd.Timestamp) else None,
                "exit_date": effective_date.strftime("%Y-%m-%d"),
                "entry_price": float(entry_price) if pd.notna(entry_price) else None,
                "exit_price": float(exit_price) if pd.notna(exit_price) else None,
                "trade_return": float((exit_price / entry_price) - 1.0)
                if pd.notna(exit_price) and pd.notna(entry_price) and entry_price > 0
                else None,
                "holding_period_days": int(exit_idx - entry_idx) if entry_idx is not None and exit_idx is not None else None,
            }
        )

    for ticker in sorted(entering):
        entry_price = close_wide.at[effective_date, ticker] if ticker in close_wide.columns else np.nan
        open_trades[ticker] = {
            "entry_date": effective_date,
            "entry_price": float(entry_price) if pd.notna(entry_price) else None,
        }


def close_remaining_trades(
    *,
    open_trades: dict[str, dict[str, object]],
    final_date: pd.Timestamp,
    close_wide: pd.DataFrame,
    date_to_idx: dict[pd.Timestamp, int],
    completed_trades: list[dict[str, object]],
) -> None:
    update_open_trades(
        open_trades=open_trades,
        previous_weights={ticker: 1.0 for ticker in open_trades},
        new_weights={},
        effective_date=final_date,
        close_wide=close_wide,
        date_to_idx=date_to_idx,
        completed_trades=completed_trades,
    )


def run_rebalance_backtest(
    *,
    name: str,
    close_wide: pd.DataFrame,
    evaluation_dates: list[pd.Timestamp],
    weight_builder,
    score_builder,
) -> dict[str, object]:
    dates = list(close_wide.index)
    date_to_idx = {date: idx for idx, date in enumerate(dates)}
    ic_records: list[dict[str, object]] = []

    planned_weights: dict[pd.Timestamp, dict[str, float]] = {}
    total_dates = len(evaluation_dates)
    for idx, as_of_date in enumerate(evaluation_dates, start=1):
        if idx == 1 or idx == total_dates or idx % 25 == 0:
            print(f"[{name}] evaluating {idx}/{total_dates}: {as_of_date.date()}")
        signal_scores = score_builder(as_of_date)
        record_signal_ics(ic_records=ic_records, signal_scores=signal_scores, close_wide=close_wide, as_of_date=as_of_date)
        planned_weights[as_of_date] = weight_builder(as_of_date)

    effective_weights: dict[pd.Timestamp, dict[str, float]] = {}
    for as_of_date, weights in planned_weights.items():
        idx = date_to_idx.get(as_of_date)
        if idx is None or idx + 1 >= len(dates):
            continue
        effective_weights[dates[idx + 1]] = weights

    if not effective_weights:
        raise RuntimeError(f"{name}: no effective rebalance dates produced any weights")

    effective_dates = sorted(effective_weights)
    start_date = effective_dates[0]
    start_idx = date_to_idx[start_date]

    nav = 1.0
    nav_series = {start_date: nav}
    open_trades: dict[str, dict[str, object]] = {}
    completed_trades: list[dict[str, object]] = []
    active_weights = effective_weights[start_date]

    update_open_trades(
        open_trades=open_trades,
        previous_weights={},
        new_weights=active_weights,
        effective_date=start_date,
        close_wide=close_wide,
        date_to_idx=date_to_idx,
        completed_trades=completed_trades,
    )

    for idx in range(start_idx + 1, len(dates)):
        prev_date = dates[idx - 1]
        current_date = dates[idx]
        port_ret = 0.0
        for ticker, weight in active_weights.items():
            if ticker not in close_wide.columns or weight == 0.0:
                continue
            prev_px = close_wide.at[prev_date, ticker]
            curr_px = close_wide.at[current_date, ticker]
            if pd.notna(prev_px) and pd.notna(curr_px) and prev_px > 0:
                port_ret += float(weight) * ((curr_px / prev_px) - 1.0)
        nav *= 1.0 + port_ret
        nav_series[current_date] = nav

        if current_date in effective_weights and current_date != start_date:
            new_weights = effective_weights[current_date]
            update_open_trades(
                open_trades=open_trades,
                previous_weights=active_weights,
                new_weights=new_weights,
                effective_date=current_date,
                close_wide=close_wide,
                date_to_idx=date_to_idx,
                completed_trades=completed_trades,
            )
            active_weights = new_weights

    final_date = dates[-1]
    if open_trades:
        close_remaining_trades(
            open_trades=open_trades,
            final_date=final_date,
            close_wide=close_wide,
            date_to_idx=date_to_idx,
            completed_trades=completed_trades,
        )

    return {
        "equity_curve": pd.Series(nav_series).sort_index(),
        "trades": completed_trades,
        "ic_records": ic_records,
        "start_date": start_date.strftime("%Y-%m-%d"),
        "end_date": final_date.strftime("%Y-%m-%d"),
    }


def annualized_return(equity_curve: pd.Series) -> float | None:
    if equity_curve.empty or len(equity_curve) < 2:
        return None
    years = (equity_curve.index[-1] - equity_curve.index[0]).days / 365.25
    if years <= 0:
        return None
    return float((equity_curve.iloc[-1] / equity_curve.iloc[0]) ** (1.0 / years) - 1.0)


def sharpe_ratio(equity_curve: pd.Series) -> float | None:
    if equity_curve.empty or len(equity_curve) < 10:
        return None
    returns = equity_curve.pct_change().dropna()
    if returns.empty:
        return None
    std = returns.std()
    if pd.isna(std) or std < 1e-10:
        return None
    return float((returns.mean() / std) * math.sqrt(252.0))


def max_drawdown(equity_curve: pd.Series) -> float | None:
    if equity_curve.empty:
        return None
    running_max = equity_curve.cummax()
    drawdown = (equity_curve / running_max) - 1.0
    return float(drawdown.min())


def mean_ic_by_signal(ic_records: list[dict[str, object]]) -> dict[str, float | None]:
    if not ic_records:
        return {}
    df = pd.DataFrame(ic_records)
    out: dict[str, float | None] = {}
    for signal, group in df.groupby("signal"):
        values = pd.to_numeric(group["ic"], errors="coerce").dropna()
        out[str(signal)] = float(values.mean()) if not values.empty else None
    return out


def win_rate(trades: list[dict[str, object]]) -> float | None:
    closed = [trade for trade in trades if trade.get("trade_return") is not None]
    if not closed:
        return None
    wins = sum(1 for trade in closed if float(trade["trade_return"]) > 0.0)
    return float(wins / len(closed))


def average_holding_period(trades: list[dict[str, object]]) -> float | None:
    periods = [trade.get("holding_period_days") for trade in trades if trade.get("holding_period_days") is not None]
    if not periods:
        return None
    return float(np.mean(periods))


def suspicious_ic_flags(mean_ics: dict[str, float | None]) -> list[dict[str, object]]:
    flags: list[dict[str, object]] = []
    for signal, value in sorted(mean_ics.items()):
        if value is None:
            continue
        if abs(value) > LOOKAHEAD_IC_THRESHOLD:
            flags.append(
                {
                    "signal": signal,
                    "mean_ic": float(value),
                    "reason": f"|IC| > {LOOKAHEAD_IC_THRESHOLD:.2f}",
                }
            )
    return flags


def spy_equity_curve(close_wide: pd.DataFrame, start_date: pd.Timestamp) -> pd.Series:
    if "SPY" not in close_wide.columns:
        return pd.Series(dtype=float)
    spy = close_wide["SPY"].dropna()
    spy = spy[spy.index >= start_date]
    if spy.empty:
        return pd.Series(dtype=float)
    returns = spy.pct_change().fillna(0.0)
    return (1.0 + returns).cumprod()


def plot_equity_curves(
    quality_current_curve: pd.Series,
    quality_enhanced_curve: pd.Series,
    meanrev_curve: pd.Series,
    spy_curve: pd.Series,
) -> None:
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(
        quality_current_curve.index,
        quality_current_curve.values,
        label="Quality Current Code",
        color="steelblue",
        linewidth=1.8,
    )
    ax.plot(
        quality_enhanced_curve.index,
        quality_enhanced_curve.values,
        label="Quality Enhanced Research",
        color="darkorange",
        linewidth=1.8,
    )
    ax.plot(meanrev_curve.index, meanrev_curve.values, label="Mean Reversion", color="seagreen", linewidth=1.8)
    ax.plot(spy_curve.index, spy_curve.values, label="SPY", color="gray", linestyle="--", linewidth=1.5)
    ax.set_title("Research Sleeve Backtests vs SPY")
    ax.set_xlabel("Date")
    ax.set_ylabel("Normalized Equity")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper left")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    plt.xticks(rotation=30)
    plt.tight_layout()
    plt.savefig(PLOT_PATH, dpi=150)
    plt.close(fig)


def format_metric(value: float | None, pct: bool = False) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "n/a"
    return f"{value:.2%}" if pct else f"{value:.3f}"


def print_summary(results: dict[str, object]) -> None:
    quality_current = results["quality_current"]
    quality_enhanced = results["quality_enhanced"]
    meanrev = results["mean_reversion"]
    spy = results["spy"]
    print("\n" + "=" * 84)
    print(f"{'Metric':<24} {'Q Current':>14} {'Q Enhanced':>14} {'MeanRev':>14} {'SPY':>14}")
    print("-" * 84)
    print(f"{'Annualized Return':<24} {format_metric(quality_current['annualized_return'], pct=True):>14} {format_metric(quality_enhanced['annualized_return'], pct=True):>14} {format_metric(meanrev['annualized_return'], pct=True):>14} {format_metric(spy['annualized_return'], pct=True):>14}")
    print(f"{'Sharpe Ratio':<24} {format_metric(quality_current['sharpe_ratio']):>14} {format_metric(quality_enhanced['sharpe_ratio']):>14} {format_metric(meanrev['sharpe_ratio']):>14} {format_metric(spy['sharpe_ratio']):>14}")
    print(f"{'Max Drawdown':<24} {format_metric(quality_current['max_drawdown'], pct=True):>14} {format_metric(quality_enhanced['max_drawdown'], pct=True):>14} {format_metric(meanrev['max_drawdown'], pct=True):>14} {format_metric(spy['max_drawdown'], pct=True):>14}")
    print(f"{'Composite IC':<24} {format_metric(quality_current['ic_summary'].get('quality_score')):>14} {format_metric(quality_enhanced['ic_summary'].get('quality_score')):>14} {format_metric(meanrev['ic_summary'].get('score')):>14} {'n/a':>14}")
    print(f"{'Win Rate':<24} {format_metric(quality_current['win_rate'], pct=True):>14} {format_metric(quality_enhanced['win_rate'], pct=True):>14} {format_metric(meanrev['win_rate'], pct=True):>14} {'n/a':>14}")
    print(f"{'Avg Holding Period':<24} {format_metric(quality_current['avg_holding_period_days']):>14} {format_metric(quality_enhanced['avg_holding_period_days']):>14} {format_metric(meanrev['avg_holding_period_days']):>14} {'n/a':>14}")
    print("=" * 84)

    for sleeve_name in ("quality_current", "quality_enhanced", "mean_reversion"):
        flags = results[sleeve_name]["lookahead_bias_flags"]
        if flags:
            print(f"[LOOKAHEAD][{sleeve_name}] flagged signals:")
            for flag in flags:
                print(f"  - {flag['signal']}: mean_ic={flag['mean_ic']:.4f} ({flag['reason']})")
        else:
            print(f"[LOOKAHEAD][{sleeve_name}] no signals exceeded |IC| > {LOOKAHEAD_IC_THRESHOLD:.2f}")

    print("\nImplementation notes:")
    print("  - Quality Current tracks the actual implemented PIT DataStore factors in sleeves/sleeve_quality.")
    print("  - Quality Enhanced adds sector-relative ROE, gross-margin stability, debt/equity trend, and revenue-growth consistency.")
    print("  - Mean reversion uses the actual implemented RSI / price z-score / Bollinger / reversal logic.")
    print("  - The code does not currently implement the requested healthy-breadth gate for mean reversion.")
    print("  - Weights activate on the next trading day; returns are measured close-to-close as a research approximation.")


def main() -> int:
    tickers, sectors = load_universe()
    ds = DataStore(FUNDAMENTAL_PATH)

    prices = download_prices(tickers)
    if prices.empty:
        raise RuntimeError("No price data returned by yfinance.")
    price_features = build_price_features(prices)
    close_wide = prices.pivot_table(index="date", columns="ticker", values="close").sort_index()
    close_wide = close_wide.loc[:, ~close_wide.columns.duplicated()]
    dates = list(close_wide.index)
    if len(dates) < max(MEANREV_WARMUP_DAYS, QUALITY_WARMUP_DAYS) + IC_FORWARD_DAYS + 2:
        raise RuntimeError("Insufficient trading history for the requested backtest.")

    quality_eval_dates = dates[QUALITY_WARMUP_DAYS : len(dates) - IC_FORWARD_DAYS : QUALITY_REBALANCE_DAYS]
    meanrev_eval_dates = dates[MEANREV_WARMUP_DAYS : len(dates) - IC_FORWARD_DAYS]

    meanrev_universe_prices = prices[prices["ticker"] != "SPY"].copy()
    meanrev_panel = build_mean_reversion_signals(meanrev_universe_prices)
    meanrev_by_date = {date: group.copy() for date, group in meanrev_panel.groupby("date")}
    quality_current_snapshot_cache: dict[pd.Timestamp, tuple[pd.DataFrame, pd.DataFrame]] = {}
    quality_enhanced_snapshot_cache: dict[pd.Timestamp, tuple[pd.DataFrame, pd.DataFrame]] = {}

    def quality_current_weight_builder(as_of_date: pd.Timestamp) -> dict[str, float]:
        if as_of_date not in quality_current_snapshot_cache:
            quality_current_snapshot_cache[as_of_date] = compute_quality_snapshot(
                tickers=tickers,
                as_of_date=as_of_date,
                ds=ds,
                price_features=price_features,
                sectors=sectors,
            )
        snapshot, _ = quality_current_snapshot_cache[as_of_date]
        targets = quality_select_and_weight(snapshot, top_n=QUALITY_TOP_N)
        if targets.empty:
            return {}
        return {
            str(row["ticker"]).upper(): float(row["target_weight"])
            for _, row in targets.iterrows()
        }

    def quality_current_score_builder(as_of_date: pd.Timestamp) -> pd.DataFrame:
        if as_of_date not in quality_current_snapshot_cache:
            quality_current_snapshot_cache[as_of_date] = compute_quality_snapshot(
                tickers=tickers,
                as_of_date=as_of_date,
                ds=ds,
                price_features=price_features,
                sectors=sectors,
            )
        snapshot, score_df = quality_current_snapshot_cache[as_of_date]
        return apply_quality_ic_gates(snapshot, score_df)

    def quality_enhanced_weight_builder(as_of_date: pd.Timestamp) -> dict[str, float]:
        if as_of_date not in quality_enhanced_snapshot_cache:
            quality_enhanced_snapshot_cache[as_of_date] = compute_quality_enhanced_snapshot(
                tickers=tickers,
                as_of_date=as_of_date,
                ds=ds,
                price_features=price_features,
                sectors=sectors,
            )
        snapshot, _ = quality_enhanced_snapshot_cache[as_of_date]
        targets = quality_select_and_weight(snapshot, top_n=QUALITY_TOP_N)
        if targets.empty:
            return {}
        return {
            str(row["ticker"]).upper(): float(row["target_weight"])
            for _, row in targets.iterrows()
        }

    def quality_enhanced_score_builder(as_of_date: pd.Timestamp) -> pd.DataFrame:
        if as_of_date not in quality_enhanced_snapshot_cache:
            quality_enhanced_snapshot_cache[as_of_date] = compute_quality_enhanced_snapshot(
                tickers=tickers,
                as_of_date=as_of_date,
                ds=ds,
                price_features=price_features,
                sectors=sectors,
            )
        snapshot, score_df = quality_enhanced_snapshot_cache[as_of_date]
        return apply_quality_ic_gates(snapshot, score_df)

    def meanrev_weight_builder(as_of_date: pd.Timestamp) -> dict[str, float]:
        snap = meanrev_by_date.get(as_of_date)
        if snap is None:
            return {}
        return meanrev_weights_from_snapshot(snap)

    def meanrev_score_builder(as_of_date: pd.Timestamp) -> pd.DataFrame:
        snap = meanrev_by_date.get(as_of_date)
        if snap is None:
            return pd.DataFrame()
        return compute_meanrev_signal_scores_from_snapshot(snap)

    print(f"Universe size: {len(tickers)}")
    print(f"Price history: {prices['ticker'].nunique()} tickers, {prices['date'].min().date()} to {prices['date'].max().date()}")
    print(f"Quality evaluation dates: {len(quality_eval_dates)}")
    print(f"Mean reversion evaluation dates: {len(meanrev_eval_dates)}")

    quality_current_result = run_rebalance_backtest(
        name="quality_current",
        close_wide=close_wide.drop(columns=["SPY"], errors="ignore"),
        evaluation_dates=quality_eval_dates,
        weight_builder=quality_current_weight_builder,
        score_builder=quality_current_score_builder,
    )
    quality_enhanced_result = run_rebalance_backtest(
        name="quality_enhanced",
        close_wide=close_wide.drop(columns=["SPY"], errors="ignore"),
        evaluation_dates=quality_eval_dates,
        weight_builder=quality_enhanced_weight_builder,
        score_builder=quality_enhanced_score_builder,
    )
    meanrev_result = run_rebalance_backtest(
        name="mean_reversion",
        close_wide=close_wide.drop(columns=["SPY"], errors="ignore"),
        evaluation_dates=meanrev_eval_dates,
        weight_builder=meanrev_weight_builder,
        score_builder=meanrev_score_builder,
    )

    quality_current_curve = quality_current_result["equity_curve"]
    quality_enhanced_curve = quality_enhanced_result["equity_curve"]
    meanrev_curve = meanrev_result["equity_curve"]
    spy_start = min(quality_current_curve.index.min(), quality_enhanced_curve.index.min(), meanrev_curve.index.min())
    spy_curve = spy_equity_curve(close_wide, spy_start)
    plot_equity_curves(quality_current_curve, quality_enhanced_curve, meanrev_curve, spy_curve)

    quality_current_ic_summary = mean_ic_by_signal(quality_current_result["ic_records"])
    quality_enhanced_ic_summary = mean_ic_by_signal(quality_enhanced_result["ic_records"])
    meanrev_ic_summary = mean_ic_by_signal(meanrev_result["ic_records"])

    results = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "price_history_years": PRICE_YEARS,
        "implementation_notes": {
            "quality_current": [
                "Backtest uses the actual implemented PIT DataStore factors in sleeves/sleeve_quality.",
                "Monthly rebalances are used because quality signals move on filing cadence, not daily.",
            ],
            "quality_enhanced": [
                "Research variant adds sector-relative ROE plus stability/trend factors using PIT EDGAR history only.",
                "Gross-margin stability is modeled as mean quarterly gross margin minus quarterly margin volatility.",
                "Revenue-growth consistency is modeled as mean quarterly YoY growth minus its volatility.",
                "Debt/equity trend rewards deleveraging via a falling multi-quarter debt/equity slope.",
            ],
            "mean_reversion": [
                "Backtest uses the actual implemented mean-reversion panel in sleeves/sleeve_mean_reversion.",
                "Current code does not implement a healthy-breadth regime gate.",
                "Current code uses 20d and 60d price z-scores, not a 20-day return z-score versus a 252-day distribution.",
            ],
            "execution_assumption": "Weights activate on the next trading day and daily PnL is measured close-to-close.",
        },
        "quality_current": {
            "start_date": quality_current_result["start_date"],
            "end_date": quality_current_result["end_date"],
            "annualized_return": annualized_return(quality_current_curve),
            "sharpe_ratio": sharpe_ratio(quality_current_curve),
            "max_drawdown": max_drawdown(quality_current_curve),
            "win_rate": win_rate(quality_current_result["trades"]),
            "avg_holding_period_days": average_holding_period(quality_current_result["trades"]),
            "ic_summary": quality_current_ic_summary,
            "lookahead_bias_flags": suspicious_ic_flags(quality_current_ic_summary),
            "trade_count": len(quality_current_result["trades"]),
        },
        "quality_enhanced": {
            "start_date": quality_enhanced_result["start_date"],
            "end_date": quality_enhanced_result["end_date"],
            "annualized_return": annualized_return(quality_enhanced_curve),
            "sharpe_ratio": sharpe_ratio(quality_enhanced_curve),
            "max_drawdown": max_drawdown(quality_enhanced_curve),
            "win_rate": win_rate(quality_enhanced_result["trades"]),
            "avg_holding_period_days": average_holding_period(quality_enhanced_result["trades"]),
            "ic_summary": quality_enhanced_ic_summary,
            "lookahead_bias_flags": suspicious_ic_flags(quality_enhanced_ic_summary),
            "trade_count": len(quality_enhanced_result["trades"]),
        },
        "mean_reversion": {
            "start_date": meanrev_result["start_date"],
            "end_date": meanrev_result["end_date"],
            "annualized_return": annualized_return(meanrev_curve),
            "sharpe_ratio": sharpe_ratio(meanrev_curve),
            "max_drawdown": max_drawdown(meanrev_curve),
            "win_rate": win_rate(meanrev_result["trades"]),
            "avg_holding_period_days": average_holding_period(meanrev_result["trades"]),
            "ic_summary": meanrev_ic_summary,
            "lookahead_bias_flags": suspicious_ic_flags(meanrev_ic_summary),
            "trade_count": len(meanrev_result["trades"]),
        },
        "spy": {
            "start_date": spy_curve.index.min().strftime("%Y-%m-%d") if not spy_curve.empty else None,
            "end_date": spy_curve.index.max().strftime("%Y-%m-%d") if not spy_curve.empty else None,
            "annualized_return": annualized_return(spy_curve),
            "sharpe_ratio": sharpe_ratio(spy_curve),
            "max_drawdown": max_drawdown(spy_curve),
        },
        "artifacts": {
            "results_json": str(RESULTS_PATH.relative_to(ROOT)),
            "equity_plot": str(PLOT_PATH.relative_to(ROOT)),
        },
    }

    RESULTS_PATH.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print_summary(results)
    print(f"\nSaved results to {RESULTS_PATH}")
    print(f"Saved plot to {PLOT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

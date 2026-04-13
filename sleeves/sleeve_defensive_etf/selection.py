from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from core.portfolio_alloc import SleeveOutput, create_sleeve_output

logger = logging.getLogger(__name__)

SLEEVE_NAME = "sleeve_defensive_etf"
DEFENSIVE_ETF_TICKERS = ["SGOV", "SHY", "IEF", "TLT"]
ACTIVE_COMPOSITE_REGIMES = {"risk_off_defensive", "high_volatility", "breadth_washout"}
ACTIVE_VOL_STATES = {"elevated", "crisis"}
ACTIVE_MACRO_STATES = {"risk_off", "stress"}

BASE_TEMPLATES: dict[str, dict[str, float]] = {
    "risk_off_defensive": {"SGOV": 0.30, "SHY": 0.20, "IEF": 0.35, "TLT": 0.15},
    "high_volatility": {"SGOV": 0.50, "SHY": 0.20, "IEF": 0.20, "TLT": 0.10},
    "breadth_washout": {"SGOV": 0.25, "SHY": 0.15, "IEF": 0.35, "TLT": 0.25},
    "fallback_defensive": {"SGOV": 0.40, "SHY": 0.20, "IEF": 0.25, "TLT": 0.15},
}


def _normalize_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _is_active_regime(regime_summary: dict[str, Any] | None) -> bool:
    regime_summary = dict(regime_summary or {})
    composite = _normalize_text(regime_summary.get("composite_regime"))
    volatility = _normalize_text(regime_summary.get("volatility_state"))
    macro = _normalize_text(regime_summary.get("macro_state"))
    return (
        composite in ACTIVE_COMPOSITE_REGIMES
        or volatility in ACTIVE_VOL_STATES
        or macro in ACTIVE_MACRO_STATES
    )


def build_defensive_etf_signal_frame(prices: pd.DataFrame | None) -> pd.DataFrame:
    if prices is None or prices.empty:
        return pd.DataFrame(columns=["ticker", "close", "return_20d", "realized_vol_20d", "avg_volume_20d"])

    df = prices.copy()
    if "ticker" not in df.columns or "date" not in df.columns or "close" not in df.columns:
        return pd.DataFrame(columns=["ticker", "close", "return_20d", "realized_vol_20d", "avg_volume_20d"])

    df["ticker"] = df["ticker"].astype(str).str.upper()
    df = df[df["ticker"].isin(DEFENSIVE_ETF_TICKERS)].copy()
    if df.empty:
        return pd.DataFrame(columns=["ticker", "close", "return_20d", "realized_vol_20d", "avg_volume_20d"])

    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["ticker", "date"])
    grouped = df.groupby("ticker", group_keys=False)
    df["return_20d"] = grouped["close"].pct_change(20)
    daily_return = grouped["close"].pct_change()
    df["realized_vol_20d"] = (
        daily_return.groupby(df["ticker"]).transform(lambda x: x.rolling(20, min_periods=10).std()) * np.sqrt(252)
    )
    if "volume" in df.columns:
        df["avg_volume_20d"] = grouped["volume"].transform(lambda x: x.rolling(20, min_periods=5).mean())
    else:
        df["avg_volume_20d"] = np.nan
    latest = grouped.tail(1).copy()
    return latest[["ticker", "close", "return_20d", "realized_vol_20d", "avg_volume_20d"]].reset_index(drop=True)


def _base_template(regime_summary: dict[str, Any] | None) -> dict[str, float]:
    composite = _normalize_text((regime_summary or {}).get("composite_regime"))
    template = BASE_TEMPLATES.get(composite) or BASE_TEMPLATES["fallback_defensive"]
    return {ticker: float(weight) for ticker, weight in template.items()}


def _tilt_weights_for_signal_frame(
    base_weights: dict[str, float],
    signal_frame: pd.DataFrame,
    regime_summary: dict[str, Any] | None,
) -> dict[str, float]:
    weights = dict(base_weights)
    if signal_frame.empty:
        return weights

    metrics = signal_frame.set_index("ticker").to_dict(orient="index")
    composite = _normalize_text((regime_summary or {}).get("composite_regime"))
    volatility = _normalize_text((regime_summary or {}).get("volatility_state"))

    for ticker in list(weights.keys()):
        metric = metrics.get(ticker, {})
        ret20 = float(metric.get("return_20d") or 0.0)
        vol20 = float(metric.get("realized_vol_20d") or 0.0)
        scale = 1.0
        if ret20 > 0:
            scale += min(0.20, ret20 * 2.0)
        elif ret20 < 0:
            scale -= min(0.25, abs(ret20) * 2.5)
        if ticker in {"IEF", "TLT"} and vol20 > 0.18:
            scale -= min(0.15, (vol20 - 0.18) * 0.75)
        if composite == "high_volatility" and ticker == "SGOV":
            scale += 0.15
        if composite == "breadth_washout" and ticker == "TLT" and ret20 > 0:
            scale += 0.10
        if volatility == "crisis" and ticker == "TLT" and ret20 < 0:
            scale -= 0.10
        weights[ticker] = max(0.0, float(weights[ticker]) * max(0.50, scale))

    if weights.get("TLT", 0.0) > 0 and float(metrics.get("TLT", {}).get("return_20d") or 0.0) < 0:
        transfer = weights["TLT"] * 0.35
        weights["TLT"] -= transfer
        weights["IEF"] = weights.get("IEF", 0.0) + transfer * 0.60
        weights["SGOV"] = weights.get("SGOV", 0.0) + transfer * 0.40

    return weights


def _normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    positive = {ticker: max(0.0, float(weight)) for ticker, weight in weights.items() if max(0.0, float(weight)) > 0}
    total = float(sum(positive.values()))
    if total <= 0:
        return {}
    return {ticker: weight / total for ticker, weight in positive.items()}


def build_defensive_etf_sleeve_output(
    *,
    prices: pd.DataFrame | None,
    regime_summary: dict[str, Any] | None,
    base_strength: float = 1.0,
) -> SleeveOutput:
    if not _is_active_regime(regime_summary):
        return create_sleeve_output([], SLEEVE_NAME, 0.0, "Inactive outside defensive regimes")

    signal_frame = build_defensive_etf_signal_frame(prices)
    if signal_frame.empty:
        return create_sleeve_output([], SLEEVE_NAME, 0.0, "No defensive ETF price data")

    available = set(signal_frame["ticker"].astype(str).str.upper())
    template = {ticker: weight for ticker, weight in _base_template(regime_summary).items() if ticker in available}
    if not template:
        return create_sleeve_output([], SLEEVE_NAME, 0.0, "No defensive ETFs available")

    tilted = _tilt_weights_for_signal_frame(template, signal_frame, regime_summary)
    final_weights = _normalize_weights({ticker: weight for ticker, weight in tilted.items() if ticker in available})
    if not final_weights:
        return create_sleeve_output([], SLEEVE_NAME, 0.0, "No valid defensive ETF weights")

    composite = _normalize_text((regime_summary or {}).get("composite_regime")) or "defensive"
    metrics = signal_frame.set_index("ticker").to_dict(orient="index")
    positions = []
    for rank, (ticker, weight) in enumerate(sorted(final_weights.items(), key=lambda item: (-item[1], item[0])), start=1):
        metric = metrics.get(ticker, {})
        ret20 = float(metric.get("return_20d") or 0.0)
        signal_strength = max(0.25, min(1.0, 0.50 + ret20 * 3.0))
        positions.append(
            {
                "ticker": ticker,
                "target_weight": float(weight),
                "reason": f"defensive_etf_{composite}",
                "signal_strength": float(signal_strength),
                "rank": rank,
            }
        )

    notes = (
        f"Active defensive ETF sleeve for {composite}: "
        + ", ".join(f"{ticker} {weight:.0%}" for ticker, weight in sorted(final_weights.items(), key=lambda item: (-item[1], item[0])))
    )
    logger.info("[DEFENSIVE_ETF] %s", notes)
    return create_sleeve_output(positions, SLEEVE_NAME, base_strength, notes)


def build_defensive_etf_equity_curve(
    *,
    prices: pd.DataFrame | None,
    sleeve_output: SleeveOutput | None,
    base_equity: float = 10_000.0,
) -> pd.DataFrame:
    if (
        prices is None
        or prices.empty
        or sleeve_output is None
        or sleeve_output.positions_df is None
        or sleeve_output.positions_df.empty
    ):
        return pd.DataFrame(columns=["date", "equity"])

    weights_df = sleeve_output.positions_df.copy()
    if "ticker" not in weights_df.columns or "target_weight" not in weights_df.columns:
        return pd.DataFrame(columns=["date", "equity"])
    weights = (
        weights_df.assign(ticker=weights_df["ticker"].astype(str).str.upper())
        .groupby("ticker", as_index=True)["target_weight"]
        .sum()
    )
    weights = weights[weights > 0]
    if weights.empty:
        return pd.DataFrame(columns=["date", "equity"])

    df = prices.copy()
    df["ticker"] = df["ticker"].astype(str).str.upper()
    df = df[df["ticker"].isin(weights.index)].copy()
    if df.empty:
        return pd.DataFrame(columns=["date", "equity"])

    pivot = (
        df.assign(date=pd.to_datetime(df["date"]))
        .pivot_table(index="date", columns="ticker", values="close", aggfunc="last")
        .sort_index()
        .ffill()
    )
    if pivot.empty:
        return pd.DataFrame(columns=["date", "equity"])
    daily_returns = pivot.pct_change(fill_method=None).fillna(0.0)
    aligned_weights = weights.reindex(daily_returns.columns).fillna(0.0)
    total = float(aligned_weights.sum())
    if total <= 0:
        return pd.DataFrame(columns=["date", "equity"])
    aligned_weights = aligned_weights / total
    portfolio_returns = daily_returns.mul(aligned_weights, axis=1).sum(axis=1)
    equity = float(base_equity) * (1.0 + portfolio_returns).cumprod()
    return pd.DataFrame({"date": equity.index, "equity": equity.values})

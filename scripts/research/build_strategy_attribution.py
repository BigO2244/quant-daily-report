#!/usr/bin/env python3
"""Build research-grade attribution artifacts for Caerus strategies.

The command is additive and read-only. It uses existing Shadow candidate
snapshots, the persisted price panel, and the Shadow NAV chain to explain where
reported strategy behavior appears to come from. Current-position contribution
is explicitly labelled as exposure attribution because historical position
weights are not fully persisted for every NAV date.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import math
import sys
from pathlib import Path
from typing import Any

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


STRATEGY_SLUGS = ("caerus_polaris", "caerus_orion", "caerus_lyra")
MODEL_SLUGS = (*STRATEGY_SLUGS, "spy_benchmark")
DISPLAY_NAMES = {
    "caerus_polaris": "Polaris",
    "caerus_orion": "Orion",
    "caerus_lyra": "Lyra",
    "spy_benchmark": "SPY",
}
WINDOWS = (1, 7, 21, 63)
DEFAULT_COST_BPS = 10.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build Caerus strategy attribution artifacts.")
    parser.add_argument("--repo-root", default=str(_REPO_ROOT))
    parser.add_argument("--trade-date", default=None)
    parser.add_argument("--price-cache-path", default="outputs/research/flow_detection_v1/price_panel.parquet")
    parser.add_argument("--shadow-root", default="outputs/shadow_candidates")
    parser.add_argument("--output-root", default="outputs/attribution")
    parser.add_argument("--lookback-days", type=int, default=252)
    return parser


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _round(value: float | None, digits: int = 10) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), digits)


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _sha256_payload(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_json_bytes(payload)).hexdigest()


def _sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_sector_map(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        return {}
    with io.StringIO("\n".join(lines)) as handle:
        return {
            str(row.get("ticker") or "").upper(): str(row.get("sector") or "Unknown")
            for row in csv.DictReader(handle)
            if row.get("ticker")
        }


def _date_dirs(root: Path) -> list[str]:
    dates: list[str] = []
    for child in root.iterdir() if root.exists() else []:
        if not child.is_dir():
            continue
        try:
            dt.date.fromisoformat(child.name)
        except ValueError:
            continue
        dates.append(child.name)
    return sorted(dates)


def _latest_snapshot_date(shadow_root: Path) -> str | None:
    candidates = []
    for date in _date_dirs(shadow_root):
        if all((shadow_root / date / f"{slug}.json").exists() for slug in STRATEGY_SLUGS):
            candidates.append(date)
    return candidates[-1] if candidates else None


def _load_strategy_snapshots(shadow_root: Path, trade_date: str) -> dict[str, dict[str, Any]]:
    snapshots = {}
    for slug in STRATEGY_SLUGS:
        payload = _read_json(shadow_root / trade_date / f"{slug}.json")
        if payload:
            snapshots[slug] = payload
    return snapshots


def _load_price_panel(path: Path, *, through_date: str) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["date", "ticker", "close", "volume", "sector"])
    panel = pd.read_parquet(path)
    if panel.empty:
        return panel
    panel = panel.copy()
    panel["date"] = pd.to_datetime(panel["date"])
    panel["ticker"] = panel["ticker"].astype(str).str.upper()
    panel = panel[panel["date"] <= pd.Timestamp(through_date)].sort_values(["ticker", "date"])
    return panel


def _returns_matrix(panel: pd.DataFrame) -> pd.DataFrame:
    if panel.empty:
        return pd.DataFrame()
    prices = panel.pivot(index="date", columns="ticker", values="close").sort_index()
    return prices.pct_change()


def _price_matrix(panel: pd.DataFrame) -> pd.DataFrame:
    if panel.empty:
        return pd.DataFrame()
    return panel.pivot(index="date", columns="ticker", values="close").sort_index()


def _latest_common_date(returns: pd.DataFrame, tickers: list[str], trade_date: str) -> pd.Timestamp | None:
    if returns.empty:
        return None
    eligible = returns.loc[returns.index <= pd.Timestamp(trade_date), [ticker for ticker in tickers if ticker in returns.columns]].dropna(how="all")
    if eligible.empty:
        return None
    return pd.Timestamp(eligible.index[-1])


def _weighted_return(row: pd.Series, weights: pd.Series) -> float:
    aligned = row.reindex(weights.index).fillna(0.0)
    return float(aligned.mul(weights).sum())


def _portfolio_returns(returns: pd.DataFrame, weights: pd.Series, *, end_date: str, lookback: int) -> pd.Series:
    if returns.empty or weights.empty:
        return pd.Series(dtype=float)
    cols = [ticker for ticker in weights.index if ticker in returns.columns]
    if not cols:
        return pd.Series(dtype=float)
    frame = returns.loc[returns.index <= pd.Timestamp(end_date), cols].tail(lookback).fillna(0.0)
    if frame.empty:
        return pd.Series(dtype=float)
    aligned_weights = weights.reindex(cols).fillna(0.0)
    return frame.mul(aligned_weights, axis=1).sum(axis=1)


def _window_ticker_returns(returns: pd.DataFrame, tickers: list[str], *, end_date: str, window: int) -> dict[str, float]:
    cols = [ticker for ticker in tickers if ticker in returns.columns]
    if not cols:
        return {}
    frame = returns.loc[returns.index <= pd.Timestamp(end_date), cols].tail(window).fillna(0.0)
    if frame.empty:
        return {}
    compounded = (1.0 + frame).prod() - 1.0
    return {str(ticker): float(value) for ticker, value in compounded.items()}


def _hhi(weights: pd.Series) -> float:
    return float(weights.fillna(0.0).pow(2).sum()) if not weights.empty else 0.0


def _max_drawdown_window(portfolio_returns: pd.Series) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    if portfolio_returns.empty:
        return None, None
    nav = (1.0 + portfolio_returns.fillna(0.0)).cumprod()
    peaks = nav.cummax()
    drawdown = nav / peaks - 1.0
    trough = drawdown.idxmin()
    peak = nav.loc[:trough].idxmax()
    return pd.Timestamp(peak), pd.Timestamp(trough)


def build_position_attribution(
    *,
    snapshots: dict[str, dict[str, Any]],
    returns: pd.DataFrame,
    trade_date: str,
    sector_map: dict[str, str],
    lookback_days: int,
) -> dict[str, Any]:
    strategies: dict[str, Any] = {}
    for slug, snapshot in snapshots.items():
        weights = pd.Series(snapshot.get("target_weights") or {}, dtype=float)
        weights.index = weights.index.astype(str).str.upper()
        tickers = weights.index.tolist()
        latest_date = _latest_common_date(returns, tickers, trade_date)
        end_date = latest_date.strftime("%Y-%m-%d") if latest_date is not None else trade_date
        window_payloads: dict[str, Any] = {}
        rolling_series: list[dict[str, Any]] = []
        for window in WINDOWS:
            ticker_returns = _window_ticker_returns(returns, tickers, end_date=end_date, window=window)
            rows = []
            portfolio_return = sum(float(weights.get(ticker, 0.0)) * ret for ticker, ret in ticker_returns.items())
            equal_weight = 1.0 / len(tickers) if tickers else 0.0
            equal_return = sum(equal_weight * ret for ret in ticker_returns.values())
            for ticker in tickers:
                ret = ticker_returns.get(ticker)
                if ret is None:
                    continue
                contribution = float(weights[ticker]) * ret
                rows.append(
                    {
                        "ticker": ticker,
                        "sector": sector_map.get(ticker, "Unknown"),
                        "weight": _round(float(weights[ticker]), 8),
                        "return": _round(ret),
                        "contribution": _round(contribution),
                        "contribution_pct_of_portfolio_return": _round(contribution / portfolio_return, 8) if abs(portfolio_return) > 1e-12 else None,
                        "equal_weight_contribution": _round(equal_weight * ret),
                        "sizing_contribution": _round((float(weights[ticker]) - equal_weight) * ret),
                    }
                )
            rows_abs = sorted(rows, key=lambda item: abs(item["contribution"] or 0.0), reverse=True)
            window_payloads[f"{window}d"] = {
                "status": "OK" if rows else "NO_POSITION_RETURN_DATA",
                "methodology": "current_book_trailing_exposure",
                "end_date": end_date,
                "portfolio_return": _round(portfolio_return),
                "equal_weight_return": _round(equal_return),
                "sizing_impact": _round(portfolio_return - equal_return),
                "top_contributors": sorted(rows, key=lambda item: item["contribution"] or 0.0, reverse=True)[:10],
                "top_detractors": sorted(rows, key=lambda item: item["contribution"] or 0.0)[:10],
                "largest_absolute_contributors": rows_abs[:10],
                "positions": rows,
            }
        trailing = returns.loc[returns.index <= pd.Timestamp(end_date), tickers].tail(min(lookback_days, 63)).fillna(0.0) if tickers else pd.DataFrame()
        if not trailing.empty:
            contribution_frame = trailing.mul(weights.reindex(trailing.columns).fillna(0.0), axis=1)
            cumulative = contribution_frame.cumsum()
            for date, row in cumulative.iterrows():
                rolling_series.append(
                    {
                        "date": pd.Timestamp(date).strftime("%Y-%m-%d"),
                        "cumulative_contribution": {ticker: _round(float(row[ticker])) for ticker in contribution_frame.columns},
                    }
                )
        portfolio_daily = _portfolio_returns(returns, weights, end_date=end_date, lookback=min(lookback_days, 63))
        peak, trough = _max_drawdown_window(portfolio_daily)
        drawdown_rows: list[dict[str, Any]] = []
        if peak is not None and trough is not None and not trailing.empty:
            dd_frame = trailing.loc[(trailing.index > peak) & (trailing.index <= trough)]
            dd_contrib = dd_frame.mul(weights.reindex(dd_frame.columns).fillna(0.0), axis=1).sum()
            drawdown_rows = [
                {"ticker": str(ticker), "contribution_to_drawdown": _round(float(value)), "sector": sector_map.get(str(ticker), "Unknown")}
                for ticker, value in dd_contrib.sort_values().items()
            ]
        top3_weight = float(weights.sort_values(ascending=False).head(3).sum()) if not weights.empty else 0.0
        top3_contribution = sum(item["contribution"] or 0.0 for item in window_payloads.get("21d", {}).get("top_contributors", [])[:3])
        strategies[slug] = {
            "strategy_name": snapshot.get("strategy_name") or DISPLAY_NAMES.get(slug, slug),
            "trade_date": trade_date,
            "data_through_date": end_date,
            "attribution_convention": "current_book_trailing_exposure_not_historical_realized_positions",
            "windows": window_payloads,
            "rolling_cumulative_contribution": rolling_series,
            "drawdown_contribution": {
                "status": "OK" if drawdown_rows else "NO_DRAWDOWN_WINDOW",
                "peak_date": peak.strftime("%Y-%m-%d") if peak is not None else None,
                "trough_date": trough.strftime("%Y-%m-%d") if trough is not None else None,
                "top_drawdown_contributors": drawdown_rows[:10],
            },
            "concentration_impact": {
                "holdings_count": int(len(weights)),
                "max_weight": _round(float(weights.max()) if not weights.empty else 0.0, 8),
                "top3_weight": _round(top3_weight, 8),
                "hhi": _round(_hhi(weights), 8),
                "top3_contribution_21d": _round(top3_contribution),
                "top3_contribution_share_21d": _round(top3_contribution / (window_payloads.get("21d", {}).get("portfolio_return") or 0.0), 8)
                if abs(window_payloads.get("21d", {}).get("portfolio_return") or 0.0) > 1e-12
                else None,
            },
            "turnover_impact": {
                "expected_turnover": _round(_as_float(snapshot.get("expected_turnover"))),
                "transaction_cost_bps": _round(_as_float((snapshot.get("performance_summary") or {}).get("transaction_cost_bps")) or DEFAULT_COST_BPS),
                "estimated_one_day_cost_drag": _round((_as_float(snapshot.get("expected_turnover")) or 0.0) * ((_as_float((snapshot.get("performance_summary") or {}).get("transaction_cost_bps")) or DEFAULT_COST_BPS) / 10000.0)),
            },
        }
    return {
        "schema_version": "caerus_position_attribution_v1",
        "trade_date": trade_date,
        "strategies": strategies,
    }


def _regression_beta(y: pd.Series, x: pd.Series) -> tuple[float | None, float | None]:
    joined = pd.concat([y.rename("y"), x.rename("x")], axis=1).dropna()
    if len(joined) < 20:
        return None, None
    variance = float(joined["x"].var(ddof=1))
    if abs(variance) < 1e-12:
        return None, None
    beta = float(joined["y"].cov(joined["x"]) / variance)
    corr = float(joined["y"].corr(joined["x"]))
    return beta, corr


def _momentum_scores(prices: pd.DataFrame, tickers: list[str], end_date: str) -> dict[str, float]:
    eligible = prices.loc[prices.index <= pd.Timestamp(end_date), [ticker for ticker in tickers if ticker in prices.columns]]
    out: dict[str, float] = {}
    for ticker in eligible.columns:
        series = eligible[ticker].dropna()
        if len(series) >= 253:
            out[str(ticker)] = float(series.iloc[-21] / series.iloc[-252] - 1.0)
    return out


def build_factor_exposure(
    *,
    snapshots: dict[str, dict[str, Any]],
    panel: pd.DataFrame,
    returns: pd.DataFrame,
    prices: pd.DataFrame,
    trade_date: str,
    sector_map: dict[str, str],
    lookback_days: int,
) -> dict[str, Any]:
    strategies: dict[str, Any] = {}
    spy = returns["SPY"] if "SPY" in returns.columns else pd.Series(dtype=float)
    avg_dollar_volume = pd.Series(dtype=float)
    if not panel.empty:
        tmp = panel.copy()
        tmp["dollar_volume"] = tmp["close"] * tmp["volume"]
        avg_dollar_volume = tmp[tmp["date"] <= pd.Timestamp(trade_date)].groupby("ticker")["dollar_volume"].tail(63).groupby(tmp["ticker"]).mean()
    universe_adv = avg_dollar_volume.dropna().rank(pct=True)
    for slug, snapshot in snapshots.items():
        weights = pd.Series(snapshot.get("target_weights") or {}, dtype=float)
        weights.index = weights.index.astype(str).str.upper()
        tickers = weights.index.tolist()
        end = _latest_common_date(returns, tickers, trade_date)
        end_date = end.strftime("%Y-%m-%d") if end is not None else trade_date
        daily = _portfolio_returns(returns, weights, end_date=end_date, lookback=lookback_days)
        beta, corr = _regression_beta(daily, spy.reindex(daily.index) if not spy.empty else spy)
        vols = returns.loc[returns.index <= pd.Timestamp(end_date), [ticker for ticker in tickers if ticker in returns.columns]].tail(20).std(ddof=1) * (252.0 ** 0.5)
        momentum = _momentum_scores(prices, tickers, end_date)
        sector_weights: dict[str, float] = {}
        for ticker, weight in weights.items():
            sector = sector_map.get(str(ticker), "Unknown")
            sector_weights[sector] = sector_weights.get(sector, 0.0) + float(weight)
        max_sector = max(sector_weights.values()) if sector_weights else 0.0
        liquidity_scores = [float(universe_adv.get(ticker)) for ticker in tickers if ticker in universe_adv.index and pd.notna(universe_adv.get(ticker))]
        weighted_liquidity = sum(float(weights[ticker]) * float(universe_adv.get(ticker, 0.0)) for ticker in tickers if ticker in universe_adv.index)
        top3_weight = float(weights.sort_values(ascending=False).head(3).sum()) if not weights.empty else 0.0
        hidden_factor_flags = []
        if beta is not None and beta > 1.2:
            hidden_factor_flags.append("high_market_beta")
        if max_sector >= 0.60:
            hidden_factor_flags.append("sector_concentration")
        if top3_weight >= 0.60:
            hidden_factor_flags.append("position_concentration")
        strategies[slug] = {
            "strategy_name": snapshot.get("strategy_name") or DISPLAY_NAMES.get(slug, slug),
            "data_through_date": end_date,
            "market_beta": _round(beta),
            "market_correlation": _round(corr),
            "realized_volatility_ann_current_book": _round(float(daily.std(ddof=1) * (252.0 ** 0.5)) if len(daily) >= 2 else None),
            "momentum_exposure": {
                "weighted_12_1_momentum": _round(sum(float(weights[t]) * momentum.get(t, 0.0) for t in tickers)),
                "by_ticker": {ticker: _round(value) for ticker, value in momentum.items()},
            },
            "volatility_exposure": {
                "weighted_20d_ann_vol": _round(sum(float(weights[ticker]) * float(vols.get(ticker, 0.0)) for ticker in tickers)),
                "by_ticker": {str(ticker): _round(float(value)) for ticker, value in vols.dropna().items()},
            },
            "sector_exposure": {
                "weights": {sector: _round(weight, 8) for sector, weight in sorted(sector_weights.items())},
                "max_sector_weight": _round(max_sector, 8),
                "sector_hhi": _round(sum(weight * weight for weight in sector_weights.values()), 8),
            },
            "market_cap_tilt_proxy": {
                "status": "LIQUIDITY_PROXY_ONLY",
                "weighted_avg_dollar_volume_percentile": _round(weighted_liquidity, 8) if liquidity_scores else None,
                "coverage": len(liquidity_scores),
            },
            "growth_value_tilt": {"status": "UNAVAILABLE", "reason": "No point-in-time value/growth fundamentals in attribution inputs."},
            "quality_profitability_tilt": {"status": "UNAVAILABLE", "reason": "No point-in-time ROE/ROIC/profitability panel in attribution inputs."},
            "hidden_factor_flags": hidden_factor_flags,
            "selection_alpha_interpretation": "PARTIAL: factor proxy exposure estimated; residual selection alpha requires historical holdings and factor returns.",
        }
    return {"schema_version": "caerus_factor_exposure_v1", "trade_date": trade_date, "strategies": strategies}


def _load_nav_history(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    frame = pd.read_csv(path)
    if frame.empty:
        return frame
    frame["date"] = pd.to_datetime(frame["date"])
    return frame.sort_values("date")


def _classify_regimes(panel: pd.DataFrame, trade_dates: pd.Series) -> pd.DataFrame:
    if panel.empty:
        return pd.DataFrame()
    prices = _price_matrix(panel)
    if "SPY" not in prices.columns:
        return pd.DataFrame()
    spy = prices["SPY"].dropna().to_frame("spy_close")
    spy["spy_return"] = spy["spy_close"].pct_change()
    spy["ema200"] = spy["spy_close"].ewm(span=200, adjust=False).mean()
    spy["vol20"] = spy["spy_return"].rolling(20).std() * (252.0 ** 0.5)
    spy["trend63"] = spy["spy_close"] / spy["spy_close"].shift(63) - 1.0
    all_prices = prices.drop(columns=[], errors="ignore")
    breadth = all_prices.gt(all_prices.rolling(200).mean()).mean(axis=1)
    spy["breadth_above_200d"] = breadth
    median_vol = spy["vol20"].rolling(252, min_periods=60).median()
    spy["risk_regime"] = spy.apply(lambda row: "risk_on" if row["spy_close"] >= row["ema200"] else "risk_off", axis=1)
    spy["volatility_regime"] = ["high_vol" if pd.notna(v) and pd.notna(m) and v > m else "normal_vol" for v, m in zip(spy["vol20"], median_vol)]
    spy["trend_regime"] = ["trending" if pd.notna(ret) and pd.notna(vol) and abs(ret) > vol / math.sqrt(4) else "choppy" for ret, vol in zip(spy["trend63"], spy["vol20"])]
    spy["breadth_regime"] = ["broad" if pd.notna(v) and v >= 0.55 else "narrow" for v in spy["breadth_above_200d"]]
    spy = spy.reset_index().rename(columns={"date": "date"})
    if "date" not in spy.columns:
        spy = spy.rename(columns={spy.columns[0]: "date"})
    spy["date"] = pd.to_datetime(spy["date"])
    return spy[spy["date"].isin(pd.to_datetime(trade_dates))]


def _period_stats(frame: pd.DataFrame, strategy: str) -> dict[str, Any]:
    returns = frame[strategy].dropna()
    spy = frame["spy_benchmark"].dropna() if "spy_benchmark" in frame.columns else pd.Series(dtype=float)
    if returns.empty:
        return {"valid_days": 0, "cumulative_return": None, "avg_daily_return": None, "hit_rate": None, "excess_vs_spy": None}
    cumulative = float((1.0 + returns).prod() - 1.0)
    spy_cumulative = float((1.0 + spy.reindex(returns.index).fillna(0.0)).prod() - 1.0) if not spy.empty else None
    return {
        "valid_days": int(len(returns)),
        "cumulative_return": _round(cumulative),
        "avg_daily_return": _round(float(returns.mean())),
        "hit_rate": _round(float((returns > 0).mean()), 8),
        "worst_daily_return": _round(float(returns.min())),
        "excess_vs_spy": _round(cumulative - spy_cumulative) if spy_cumulative is not None else None,
    }


def build_regime_analysis(*, repo_root: Path, panel: pd.DataFrame, trade_date: str) -> dict[str, Any]:
    nav = _load_nav_history(repo_root / "outputs" / "shadow_candidates" / "performance" / "shadow_nav_series.csv")
    if nav.empty:
        return {"schema_version": "caerus_regime_attribution_v1", "trade_date": trade_date, "status": "NO_NAV_HISTORY", "strategies": {}}
    nav = nav[nav["date"] <= pd.Timestamp(trade_date)].copy()
    returns = nav[["date", *MODEL_SLUGS]].copy()
    for slug in MODEL_SLUGS:
        returns[slug] = returns[slug].pct_change()
    regimes = _classify_regimes(panel, returns["date"])
    if regimes.empty:
        return {"schema_version": "caerus_regime_attribution_v1", "trade_date": trade_date, "status": "NO_REGIME_DATA", "strategies": {}}
    frame = returns.merge(regimes, on="date", how="inner").dropna(subset=["spy_benchmark"], how="all")
    strategies: dict[str, Any] = {}
    for slug in STRATEGY_SLUGS:
        by_bucket: dict[str, Any] = {}
        for dimension in ("risk_regime", "volatility_regime", "trend_regime", "breadth_regime"):
            by_bucket[dimension] = {
                str(bucket): _period_stats(group, slug)
                for bucket, group in frame.groupby(dimension)
            }
        best_risk = max(by_bucket["risk_regime"].items(), key=lambda item: item[1].get("avg_daily_return") or -999.0)[0] if by_bucket["risk_regime"] else None
        worst_risk = min(by_bucket["risk_regime"].items(), key=lambda item: item[1].get("avg_daily_return") or 999.0)[0] if by_bucket["risk_regime"] else None
        strategies[slug] = {
            "strategy_name": DISPLAY_NAMES[slug],
            "status": "OK",
            "performance_by_regime": by_bucket,
            "interpretation": {
                "best_risk_regime": best_risk,
                "worst_risk_regime": worst_risk,
                "regime_dependency_note": "Use as descriptive attribution; formal causal regime alpha requires historical holdings and out-of-sample tests.",
            },
        }
    return {"schema_version": "caerus_regime_attribution_v1", "trade_date": trade_date, "status": "OK", "strategies": strategies}


def _nav_dates_from_csv(path: Path) -> tuple[str | None, str | None, int]:
    if not path.exists():
        return None, None, 0
    try:
        frame = pd.read_csv(path, usecols=["date"])
    except Exception:
        return None, None, 0
    if frame.empty:
        return None, None, 0
    dates = pd.to_datetime(frame["date"], errors="coerce").dropna().sort_values()
    if dates.empty:
        return None, None, 0
    return dates.iloc[0].strftime("%Y-%m-%d"), dates.iloc[-1].strftime("%Y-%m-%d"), int(len(dates))


def build_nav_surface_registry(*, repo_root: Path, trade_date: str) -> dict[str, Any]:
    shadow_root = repo_root / "outputs" / "shadow_candidates"
    nav_path = shadow_root / "performance" / "shadow_nav_series.csv"
    backtest_start, backtest_end, backtest_rows = _nav_dates_from_csv(nav_path)
    shadow_perf = _read_json(shadow_root / trade_date / "shadow_performance.json") or {}
    latest_shadow = _read_json(shadow_root / "latest" / "shadow_evaluation.json") or {}
    broker_snapshot = _read_json(repo_root / "outputs" / "broker" / "broker_snapshot_latest.json") or {}
    broker_nav = (
        _as_float(broker_snapshot.get("equity"))
        or _as_float((broker_snapshot.get("account") or {}).get("equity"))
        or _as_float((broker_snapshot.get("account_snapshot") or {}).get("equity"))
    )
    surfaces = {
        "research_backtest_nav": {
            "nav_surface_type": "RESEARCH_BACKTEST_NAV",
            "confidence_level": "PARTIAL_CONFIDENCE",
            "execution_realism": "MODEL_CLOSE_WITH_SYNTHETIC_COSTS",
            "point_in_time_validity": "RESEARCH_PANEL_DEPENDENT",
            "source_path": str(nav_path.relative_to(repo_root)),
            "start_date": backtest_start,
            "end_date": backtest_end,
            "row_count": backtest_rows,
            "governance_note": "Use for research context only; do not blend with broker-authoritative live NAV.",
        },
        "operational_shadow_nav": {
            "nav_surface_type": "OPERATIONAL_SHADOW_NAV",
            "confidence_level": "LOW" if shadow_perf.get("data_status") == "NO_DATA" or shadow_perf.get("status") in {"NO_PRIOR", "BROKEN_CHAIN"} else "PARTIAL_CONFIDENCE",
            "execution_realism": "MODEL_PORTFOLIO_NO_BROKER_FILLS",
            "point_in_time_validity": "TIMING_ASSUMPTION_REVIEW_REQUIRED",
            "source_path": str((shadow_root / trade_date / "shadow_performance.json").relative_to(repo_root)),
            "trade_date": trade_date,
            "chain_status": shadow_perf.get("status"),
            "data_status": shadow_perf.get("data_status"),
            "return_convention": shadow_perf.get("return_convention"),
            "governance_note": "Shadow NAV is model-only and must be labelled separately from live broker NAV.",
        },
        "live_broker_paper_nav": {
            "nav_surface_type": "LIVE_BROKER_PAPER_NAV",
            "confidence_level": "BROKER_AUTHORITATIVE" if broker_nav is not None else "UNAVAILABLE",
            "execution_realism": "BROKER_PAPER_FILLS_AND_ACCOUNT_STATE",
            "point_in_time_validity": "BROKER_TIMESTAMP_DEPENDENT",
            "source_path": "outputs/broker/broker_snapshot_latest.json",
            "equity": _round(broker_nav),
            "as_of": broker_snapshot.get("as_of") or broker_snapshot.get("timestamp"),
            "governance_note": "Use as the authoritative live paper NAV surface when available.",
        },
    }
    validation_checks = []
    for key, surface in surfaces.items():
        required = ("nav_surface_type", "confidence_level", "execution_realism", "point_in_time_validity", "source_path")
        missing = [field for field in required if surface.get(field) in (None, "")]
        source_path = repo_root / str(surface.get("source_path") or "")
        source_exists = source_path.exists()
        if key == "live_broker_paper_nav" and surface.get("confidence_level") == "UNAVAILABLE":
            source_exists = False
        validation_checks.append(
            {
                "surface": key,
                "passed": not missing and (source_exists or key == "live_broker_paper_nav"),
                "missing_metadata": missing,
                "source_exists": source_exists,
                "source_path": surface.get("source_path"),
            }
        )
    downgrade_rules = [
        {
            "condition": "operational shadow status is NO_PRIOR, BROKEN_CHAIN, or NO_DATA",
            "surface": "operational_shadow_nav",
            "downgrade_to": "LOW",
            "active": surfaces["operational_shadow_nav"]["confidence_level"] == "LOW",
        },
        {
            "condition": "broker snapshot missing or equity unavailable",
            "surface": "live_broker_paper_nav",
            "downgrade_to": "UNAVAILABLE",
            "active": surfaces["live_broker_paper_nav"]["confidence_level"] == "UNAVAILABLE",
        },
        {
            "condition": "research backtest uses model close and synthetic costs",
            "surface": "research_backtest_nav",
            "downgrade_to": "PARTIAL_CONFIDENCE",
            "active": True,
        },
    ]
    return {
        "schema_version": "caerus_nav_surface_registry_v1",
        "trade_date": trade_date,
        "separation_policy": "Never blend research backtest, operational shadow, and live broker NAV into one performance narrative.",
        "provenance_policy": "Every performance number must carry surface, confidence, execution realism, and point-in-time validity metadata.",
        "surfaces": surfaces,
        "lineage_validation": {
            "status": "OK" if all(check["passed"] for check in validation_checks) else "REVIEW_REQUIRED",
            "checks": validation_checks,
        },
        "confidence_downgrade_rules": downgrade_rules,
        "blend_prevention": {
            "status": "ENFORCED_BY_METADATA",
            "rule": "Consumers must group or compare performance only after displaying nav_surface_type and confidence_level.",
        },
        "latest_shadow_evaluation_trade_date": latest_shadow.get("trade_date"),
    }


def _strategy_exposures(snapshot: dict[str, Any], sector_map: dict[str, str]) -> dict[str, Any]:
    weights = pd.Series(snapshot.get("target_weights") or {}, dtype=float)
    weights.index = weights.index.astype(str).str.upper()
    sector_weights: dict[str, float] = {}
    for ticker, weight in weights.items():
        sector = sector_map.get(str(ticker), "Unknown")
        sector_weights[sector] = sector_weights.get(sector, 0.0) + float(weight)
    return {
        "holdings_count": int(len(weights)),
        "gross_weight": _round(float(weights.abs().sum()) if not weights.empty else 0.0, 8),
        "max_weight": _round(float(weights.max()) if not weights.empty else 0.0, 8),
        "top3_weight": _round(float(weights.sort_values(ascending=False).head(3).sum()) if not weights.empty else 0.0, 8),
        "hhi": _round(_hhi(weights), 8),
        "sector_weights": {sector: _round(weight, 8) for sector, weight in sorted(sector_weights.items())},
    }


def build_portfolio_history_payloads(
    *,
    snapshots: dict[str, dict[str, Any]],
    sector_map: dict[str, str],
    trade_date: str,
) -> dict[str, Any]:
    generated_at = f"{trade_date}T00:00:00Z"
    holdings: dict[str, Any] = {}
    weights: dict[str, Any] = {}
    exposures: dict[str, Any] = {}
    deltas: dict[str, Any] = {}
    for slug, snapshot in snapshots.items():
        strategy_holdings = []
        for item in snapshot.get("holdings") or []:
            ticker = str(item.get("ticker") or "").upper()
            strategy_holdings.append(
                {
                    **item,
                    "ticker": ticker,
                    "sector": sector_map.get(ticker, "Unknown"),
                }
            )
        holdings[slug] = {
            "strategy_name": snapshot.get("strategy_name") or DISPLAY_NAMES.get(slug, slug),
            "holdings": strategy_holdings,
        }
        weights[slug] = {
            "strategy_name": snapshot.get("strategy_name") or DISPLAY_NAMES.get(slug, slug),
            "target_weights": {str(ticker).upper(): _round(float(weight), 8) for ticker, weight in (snapshot.get("target_weights") or {}).items()},
        }
        exposures[slug] = _strategy_exposures(snapshot, sector_map)
        deltas[slug] = {
            "strategy_name": snapshot.get("strategy_name") or DISPLAY_NAMES.get(slug, slug),
            "expected_turnover": _round(_as_float(snapshot.get("expected_turnover"))),
            "estimated_holding_period_days": _round(_as_float(snapshot.get("estimated_holding_period_days"))),
            "source_delta_status": "CURRENT_SNAPSHOT_ONLY",
            "note": "Historical immutable daily snapshots enable realized deltas after at least two persisted dates.",
        }
    common = {
        "schema_version": "caerus_daily_portfolio_history_v1",
        "trade_date": trade_date,
        "generated_at": generated_at,
        "immutability_policy": "Do not overwrite if existing content differs.",
        "source": "shadow_candidate_target_snapshots",
    }
    return {
        "holdings_snapshot.json": {**common, "strategies": holdings},
        "weights_snapshot.json": {**common, "strategies": weights},
        "exposures_snapshot.json": {**common, "strategies": exposures},
        "rebalance_delta.json": {**common, "strategies": deltas},
    }


def _write_json_immutable(path: Path, payload: dict[str, Any]) -> tuple[Path, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = _json_bytes(payload).decode("utf-8")
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if existing != rendered:
            return path, "IMMUTABLE_CONFLICT_EXISTING_DIFFERENT"
        return path, "EXISTS_IDENTICAL"
    path.write_text(rendered, encoding="utf-8")
    return path, "WRITTEN"


def validate_portfolio_history_manifest(manifest: dict[str, Any], *, repo_root: Path) -> dict[str, Any]:
    checks = []
    for filename, meta in (manifest.get("files") or {}).items():
        path = repo_root / str(meta.get("path") or "")
        expected = meta.get("sha256")
        actual = _sha256_file(path)
        checks.append(
            {
                "file": filename,
                "path": meta.get("path"),
                "exists": path.exists(),
                "expected_sha256": expected,
                "actual_sha256": actual,
                "passed": path.exists() and expected == actual,
            }
        )
    return {
        "status": "OK" if checks and all(check["passed"] for check in checks) else "FAIL",
        "checks": checks,
    }


def build_exposure_intelligence(
    *,
    trade_date: str,
    summary: dict[str, Any],
    factors: dict[str, Any],
    contribution: dict[str, Any],
    concentration: dict[str, Any],
) -> dict[str, Any]:
    exposure_summary: dict[str, Any] = {
        "schema_version": "caerus_exposure_summary_v1",
        "trade_date": trade_date,
        "strategies": {},
    }
    risk_flags: dict[str, Any] = {
        "schema_version": "caerus_factor_risk_flags_v1",
        "trade_date": trade_date,
        "strategies": {},
    }
    monitor: dict[str, Any] = {
        "schema_version": "caerus_concentration_monitor_v1",
        "trade_date": trade_date,
        "thresholds": {
            "top3_contribution_share_high": 0.75,
            "top3_weight_high": 0.60,
            "max_sector_weight_high": 0.60,
            "market_beta_high": 1.20,
            "turnover_high": 0.50,
        },
        "strategies": {},
    }
    for slug in STRATEGY_SLUGS:
        factor = ((factors.get("strategies") or {}).get(slug) or {})
        contrib = ((contribution.get("strategies") or {}).get(slug) or {})
        summary_item = ((summary.get("strategies") or {}).get(slug) or {})
        flags = list(factor.get("hidden_factor_flags") or [])
        top3_share = (((contrib.get("concentration_impact") or {}).get("top3_contribution_share_21d")))
        top3_weight = (contrib.get("concentration_impact") or {}).get("top3_weight")
        max_sector = ((factor.get("sector_exposure") or {}).get("max_sector_weight"))
        beta = factor.get("market_beta")
        turnover = (contrib.get("turnover_impact") or {}).get("expected_turnover")
        if top3_share is not None and top3_share >= 0.75 and "contribution_concentration" not in flags:
            flags.append("contribution_concentration")
        alerts = []
        if beta is not None and beta >= monitor["thresholds"]["market_beta_high"]:
            alerts.append({"type": "HIGH_BETA", "value": beta, "threshold": monitor["thresholds"]["market_beta_high"]})
        if max_sector is not None and max_sector >= monitor["thresholds"]["max_sector_weight_high"]:
            alerts.append({"type": "SECTOR_CONCENTRATION", "value": max_sector, "threshold": monitor["thresholds"]["max_sector_weight_high"]})
        if top3_share is not None and top3_share >= monitor["thresholds"]["top3_contribution_share_high"]:
            alerts.append({"type": "CONTRIBUTION_CONCENTRATION", "value": top3_share, "threshold": monitor["thresholds"]["top3_contribution_share_high"]})
        if top3_weight is not None and top3_weight >= monitor["thresholds"]["top3_weight_high"]:
            alerts.append({"type": "POSITION_CONCENTRATION", "value": top3_weight, "threshold": monitor["thresholds"]["top3_weight_high"]})
        if turnover is not None and turnover >= monitor["thresholds"]["turnover_high"]:
            alerts.append({"type": "TURNOVER_SPIKE", "value": turnover, "threshold": monitor["thresholds"]["turnover_high"]})
        exposure_summary["strategies"][slug] = {
            "strategy_name": DISPLAY_NAMES[slug],
            "market_beta": beta,
            "market_correlation": factor.get("market_correlation"),
            "sector_exposure": factor.get("sector_exposure"),
            "momentum_exposure": factor.get("momentum_exposure"),
            "volatility_exposure": factor.get("volatility_exposure"),
            "turnover": turnover,
            "liquidity_proxy": factor.get("market_cap_tilt_proxy"),
            "position_crowding": {
                "top3_weight": (contrib.get("concentration_impact") or {}).get("top3_weight"),
                "top3_contribution_share_21d": top3_share,
            },
            "primary_contributor": summary_item.get("primary_21d_return_source"),
            "primary_detractor": summary_item.get("primary_21d_detractor"),
        }
        risk_flags["strategies"][slug] = {
            "strategy_name": DISPLAY_NAMES[slug],
            "flags": flags,
            "alerts": alerts,
            "severity": "HIGH" if len(alerts) >= 3 or "contribution_concentration" in flags or "position_concentration" in flags else "MEDIUM" if flags or alerts else "LOW",
            "interpretation": "Hidden risk flags are descriptive and should trigger review, not automatic strategy changes.",
        }
        conc_payload = ((concentration.get("strategies") or {}).get(slug) or {})
        monitor["strategies"][slug] = {
            **conc_payload,
            "strategy_name": DISPLAY_NAMES[slug],
            "alerts": alerts,
            "status": "ALERT" if alerts else "OK",
        }
    return {
        "exposure_summary.json": exposure_summary,
        "factor_risk_flags.json": risk_flags,
        "concentration_monitor.json": monitor,
    }


def _rolling_exposure_rows(summary: dict[str, Any], factors: dict[str, Any], concentration: dict[str, Any], trade_date: str) -> list[dict[str, Any]]:
    rows = []
    for slug in STRATEGY_SLUGS:
        item = ((summary.get("strategies") or {}).get(slug) or {})
        factor = ((factors.get("strategies") or {}).get(slug) or {})
        conc = ((concentration.get("strategies") or {}).get(slug) or {})
        rows.append(
            {
                "date": trade_date,
                "strategy": slug,
                "market_beta": factor.get("market_beta"),
                "max_sector_weight": item.get("max_sector_weight"),
                "top3_weight": conc.get("top3_weight"),
                "top3_contribution_share_21d": conc.get("top3_contribution_share_21d"),
                "portfolio_21d_return_current_book": item.get("portfolio_21d_return_current_book"),
            }
        )
    return rows


def _write_rolling_exposure_history(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = pd.read_csv(path) if path.exists() else pd.DataFrame()
    incoming = pd.DataFrame(rows)
    if existing.empty:
        combined = incoming
    else:
        combined = pd.concat([existing, incoming], ignore_index=True)
        combined = combined.drop_duplicates(subset=["date", "strategy"], keep="last")
    combined = combined.sort_values(["date", "strategy"])
    combined.to_csv(path, index=False)
    return path


def _regime_transition_summary(performance: dict[str, Any], slug: str) -> dict[str, Any]:
    risk = performance.get("risk_regime") or {}
    vol = performance.get("volatility_regime") or {}
    trend = performance.get("trend_regime") or {}
    breadth = performance.get("breadth_regime") or {}

    def avg(bucket: dict[str, Any], key: str) -> float | None:
        return _as_float((bucket.get(key) or {}).get("avg_daily_return"))

    pairs = {
        "risk_on_to_risk_off_sensitivity": (avg(risk, "risk_off"), avg(risk, "risk_on")),
        "normal_to_high_vol_sensitivity": (avg(vol, "high_vol"), avg(vol, "normal_vol")),
        "trending_to_choppy_sensitivity": (avg(trend, "choppy"), avg(trend, "trending")),
        "broad_to_narrow_breadth_sensitivity": (avg(breadth, "narrow"), avg(breadth, "broad")),
    }
    sensitivities = {
        name: _round(left - right) if left is not None and right is not None else None
        for name, (left, right) in pairs.items()
    }
    observed = [abs(value) for value in sensitivities.values() if value is not None]
    fragility_score = sum(observed) / len(observed) if observed else None
    return {
        "strategy_name": DISPLAY_NAMES.get(slug, slug),
        "status": "DESCRIPTIVE_PROXY",
        "transition_sensitivity": sensitivities,
        "regime_persistence_score": _round(1.0 / (1.0 + fragility_score * 1000.0), 8) if fragility_score is not None else None,
        "fragility_score": _round(fragility_score),
        "interpretation": "Proxy based on regime-bucket average return differences; true transition event attribution requires daily regime state history persisted with holdings.",
    }


def build_exposure_drift_summary(path: Path, current_rows: list[dict[str, Any]], *, trade_date: str) -> dict[str, Any]:
    history = pd.read_csv(path) if path.exists() else pd.DataFrame()
    current = pd.DataFrame(current_rows)
    strategies = {}
    for _, row in current.iterrows():
        slug = str(row["strategy"])
        prior = history[(history.get("strategy") == slug) & (history.get("date") < trade_date)] if not history.empty and "strategy" in history.columns and "date" in history.columns else pd.DataFrame()
        prior_tail = prior.sort_values("date").tail(5) if not prior.empty else pd.DataFrame()
        metrics = {}
        for field in ("market_beta", "max_sector_weight", "top3_contribution_share_21d", "portfolio_21d_return_current_book"):
            current_value = _as_float(row.get(field))
            prior_avg = _as_float(prior_tail[field].mean()) if not prior_tail.empty and field in prior_tail.columns else None
            metrics[field] = {
                "current": _round(current_value),
                "prior_5_observation_avg": _round(prior_avg),
                "delta": _round(current_value - prior_avg) if current_value is not None and prior_avg is not None else None,
            }
        strategies[slug] = {
            "strategy_name": DISPLAY_NAMES.get(slug, slug),
            "history_observations": int(len(prior)),
            "metrics": metrics,
            "status": "BASELINE_ONLY" if prior.empty else "OK",
        }
    return {
        "schema_version": "caerus_exposure_drift_summary_v1",
        "trade_date": trade_date,
        "strategies": strategies,
    }


def build_exposure_markdown(exposure: dict[str, Any], risk_flags: dict[str, Any], drift: dict[str, Any]) -> str:
    lines = [
        "# Exposure Intelligence Summary",
        "",
        f"- Trade date: `{exposure.get('trade_date')}`",
        "",
        "| Strategy | Beta | Max Sector | Top-3 Contribution Share | Severity | Alerts | Drift Status |",
        "|---|---:|---:|---:|---|---|---|",
    ]
    for slug in STRATEGY_SLUGS:
        item = ((exposure.get("strategies") or {}).get(slug) or {})
        flags = ((risk_flags.get("strategies") or {}).get(slug) or {})
        drift_item = ((drift.get("strategies") or {}).get(slug) or {})
        alerts = ", ".join(alert.get("type", "") for alert in flags.get("alerts") or []) or "None"
        lines.append(
            "| "
            + " | ".join(
                [
                    DISPLAY_NAMES[slug],
                    "N/A" if item.get("market_beta") is None else f"{item['market_beta']:.2f}",
                    "N/A" if ((item.get("sector_exposure") or {}).get("max_sector_weight")) is None else f"{(item.get('sector_exposure') or {}).get('max_sector_weight'):.0%}",
                    "N/A" if ((item.get("position_crowding") or {}).get("top3_contribution_share_21d")) is None else f"{(item.get('position_crowding') or {}).get('top3_contribution_share_21d'):.0%}",
                    str(flags.get("severity") or "N/A"),
                    alerts,
                    str(drift_item.get("status") or "N/A"),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "- Alerts are observability flags, not trading instructions.",
            "- BASELINE_ONLY drift status means there is not yet enough persisted exposure history to assess drift.",
        ]
    )
    return "\n".join(lines) + "\n"


def build_regime_hardening_artifacts(regimes: dict[str, Any], contribution: dict[str, Any], factors: dict[str, Any], trade_date: str) -> dict[str, Any]:
    fragility: dict[str, Any] = {
        "schema_version": "caerus_regime_fragility_report_v1",
        "trade_date": trade_date,
        "strategies": {},
    }
    matrix: dict[str, Any] = {
        "schema_version": "caerus_regime_exposure_matrix_v1",
        "trade_date": trade_date,
        "strategies": {},
    }
    attribution_by_regime: dict[str, Any] = {
        "schema_version": "caerus_attribution_by_regime_v1",
        "trade_date": trade_date,
        "strategies": {},
    }
    transition_analysis: dict[str, Any] = {
        "schema_version": "caerus_regime_transition_analysis_v1",
        "trade_date": trade_date,
        "strategies": {},
    }
    for slug in STRATEGY_SLUGS:
        regime_payload = ((regimes.get("strategies") or {}).get(slug) or {})
        performance = regime_payload.get("performance_by_regime") or {}
        flags = []
        risk = performance.get("risk_regime") or {}
        risk_on = ((risk.get("risk_on") or {}).get("avg_daily_return"))
        risk_off = ((risk.get("risk_off") or {}).get("avg_daily_return"))
        if risk_on is not None and risk_off is not None and abs(risk_off - risk_on) > 0.001:
            flags.append("risk_regime_dependency")
        vol = performance.get("volatility_regime") or {}
        high_vol = ((vol.get("high_vol") or {}).get("avg_daily_return"))
        normal_vol = ((vol.get("normal_vol") or {}).get("avg_daily_return"))
        if high_vol is not None and normal_vol is not None and abs(high_vol - normal_vol) > 0.001:
            flags.append("volatility_regime_dependency")
        beta = (((factors.get("strategies") or {}).get(slug) or {}).get("market_beta"))
        top3_share = (((contribution.get("strategies") or {}).get(slug) or {}).get("concentration_impact") or {}).get("top3_contribution_share_21d")
        max_sector = ((((factors.get("strategies") or {}).get(slug) or {}).get("sector_exposure") or {}).get("max_sector_weight"))
        if beta is not None and beta >= 1.5 and "beta_amplification" not in flags:
            flags.append("beta_amplification")
        if top3_share is not None and top3_share >= 0.75 and "concentration_amplification" not in flags:
            flags.append("concentration_amplification")
        fragility["strategies"][slug] = {
            "strategy_name": DISPLAY_NAMES[slug],
            "flags": flags,
            "best_risk_regime": ((regime_payload.get("interpretation") or {}).get("best_risk_regime")),
            "worst_risk_regime": ((regime_payload.get("interpretation") or {}).get("worst_risk_regime")),
            "beta_amplification": {"market_beta": beta, "threshold": 1.5, "active": beta is not None and beta >= 1.5},
            "concentration_amplification": {"top3_contribution_share_21d": top3_share, "max_sector_weight": max_sector, "active": (top3_share is not None and top3_share >= 0.75) or (max_sector is not None and max_sector >= 0.60)},
            "classification": "FRAGILE_REVIEW" if flags else "NO_MAJOR_FRAGILITY_FLAG",
        }
        matrix["strategies"][slug] = {
            "strategy_name": DISPLAY_NAMES[slug],
            "regime_performance": performance,
            "current_factor_exposure": ((factors.get("strategies") or {}).get(slug) or {}),
        }
        attribution_by_regime["strategies"][slug] = {
            "strategy_name": DISPLAY_NAMES[slug],
            "regime_performance": performance,
            "current_book_concentration": ((contribution.get("strategies") or {}).get(slug) or {}).get("concentration_impact"),
            "note": "Links regime behavior with current concentration; full historical attribution by regime requires daily historical weights.",
        }
        transition_analysis["strategies"][slug] = _regime_transition_summary(performance, slug)
    return {
        "regime_performance_breakdown.json": regimes,
        "regime_fragility_report.json": fragility,
        "regime_exposure_matrix.json": matrix,
        "attribution_by_regime.json": attribution_by_regime,
        "regime_transition_analysis.json": transition_analysis,
    }


def build_track_b_governance_recommendations(*, trade_date: str) -> dict[str, Any]:
    return {
        "schema_version": "caerus_track_b_governance_recommendations_v1",
        "trade_date": trade_date,
        "policy": "Do not silently alter current accounting semantics; design, compare, validate, and roll out through FR governance.",
        "recommendations": [
            {
                "fr_id": "FR-024",
                "title": "NAV surface registry and performance provenance enforcement",
                "track": "A",
                "status": "PROMOTION_READY",
                "blast_radius": "LOW",
                "dependencies": ["FR-015"],
                "safe_for_immediate_deployment": True,
                "friday_governance_required": False,
                "rollback": "Stop publishing registry artifacts and ignore generated outputs.",
            },
            {
                "fr_id": "FR-025",
                "title": "Immutable daily shadow holdings and weights history",
                "track": "A",
                "status": "PROMOTION_READY",
                "blast_radius": "MEDIUM",
                "dependencies": ["FR-024"],
                "safe_for_immediate_deployment": True,
                "friday_governance_required": False,
                "rollback": "Stop writing new portfolio_history snapshots; preserve existing immutable evidence.",
            },
            {
                "fr_id": "FR-026",
                "title": "Exposure intelligence and concentration risk observability",
                "track": "A",
                "status": "PROMOTION_READY",
                "blast_radius": "LOW",
                "dependencies": ["FR-024", "FR-025"],
                "safe_for_immediate_deployment": True,
                "friday_governance_required": False,
                "rollback": "Remove report integration and ignore exposure artifacts.",
            },
            {
                "fr_id": "FR-027",
                "title": "Regime decomposition and fragility reporting",
                "track": "A",
                "status": "PROMOTION_READY",
                "blast_radius": "LOW",
                "dependencies": ["FR-026"],
                "safe_for_immediate_deployment": True,
                "friday_governance_required": False,
                "rollback": "Stop publishing regime hardening artifacts; no strategy behavior changes.",
            },
            {
                "fr_id": "FR-028",
                "title": "Shadow execution timing semantics correction candidate",
                "track": "B",
                "status": "BACKLOG",
                "blast_radius": "HIGH",
                "dependencies": ["FR-024", "FR-025", "FR-026", "FR-027"],
                "safe_for_immediate_deployment": False,
                "friday_governance_required": True,
                "before_after_methodology": "Compare current same-day target-weight returns against prior-day weights applied to next-session returns without migrating historical chains.",
                "required_metadata": ["signal_as_of_timestamp", "price_as_of_timestamp", "execution_assumption", "execution_surface"],
                "rollback": "Keep current published chain unchanged; disable candidate comparison reader; preserve comparison artifacts.",
            },
            {
                "fr_id": "FR-029",
                "title": "Promotion governance hardening for provenance, exposure, and timing confidence",
                "track": "B",
                "status": "BACKLOG",
                "blast_radius": "MEDIUM",
                "dependencies": ["FR-028"],
                "safe_for_immediate_deployment": False,
                "friday_governance_required": True,
                "rollback": "Revert promotion-readiness checks to existing scorecard criteria.",
            },
        ],
    }


def build_decision_attribution(snapshots: dict[str, dict[str, Any]], *, trade_date: str) -> dict[str, Any]:
    strategies: dict[str, Any] = {}
    for slug, snapshot in snapshots.items():
        rank_table = snapshot.get("rank_table") or []
        selected = [row for row in rank_table if row.get("is_selected")]
        unselected = [row for row in rank_table if not row.get("is_selected")]
        selected_scores = [_as_float(row.get("momentum_score")) for row in selected]
        unselected_scores = [_as_float(row.get("momentum_score")) for row in unselected]
        strategies[slug] = {
            "strategy_name": snapshot.get("strategy_name") or DISPLAY_NAMES.get(slug, slug),
            "status": "FOUNDATIONAL",
            "signal_sources": ["momentum_score", "momentum_rank"],
            "selected_count": len(selected),
            "rank_table_coverage": len(rank_table),
            "selected_avg_momentum_score": _round(sum(v for v in selected_scores if v is not None) / len([v for v in selected_scores if v is not None])) if any(v is not None for v in selected_scores) else None,
            "unselected_top15_avg_momentum_score": _round(sum(v for v in unselected_scores if v is not None) / len([v for v in unselected_scores if v is not None])) if any(v is not None for v in unselected_scores) else None,
            "top_selected": selected[:10],
            "top_unselected_candidates": unselected[:10],
            "limitations": [
                "No feature-level decomposition beyond persisted momentum score/rank.",
                "No SHAP/ML feature importance; intentionally out of scope for Phase 1.",
            ],
        }
    return {"schema_version": "caerus_decision_attribution_v1", "trade_date": trade_date, "strategies": strategies}


def build_summary(
    *,
    trade_date: str,
    contribution: dict[str, Any],
    factors: dict[str, Any],
    regimes: dict[str, Any],
    decisions: dict[str, Any],
) -> dict[str, Any]:
    strategies: dict[str, Any] = {}
    for slug in STRATEGY_SLUGS:
        contrib_21 = (((contribution.get("strategies") or {}).get(slug) or {}).get("windows") or {}).get("21d") or {}
        factor = ((factors.get("strategies") or {}).get(slug) or {})
        regime = ((regimes.get("strategies") or {}).get(slug) or {})
        top = contrib_21.get("top_contributors") or []
        detractors = contrib_21.get("top_detractors") or []
        strategies[slug] = {
            "strategy_name": DISPLAY_NAMES[slug],
            "primary_21d_return_source": top[0] if top else None,
            "primary_21d_detractor": detractors[0] if detractors else None,
            "portfolio_21d_return_current_book": contrib_21.get("portfolio_return"),
            "top3_contribution_share_21d": (((contribution.get("strategies") or {}).get(slug) or {}).get("concentration_impact") or {}).get("top3_contribution_share_21d"),
            "hidden_factor_flags": factor.get("hidden_factor_flags") or [],
            "market_beta": factor.get("market_beta"),
            "max_sector_weight": ((factor.get("sector_exposure") or {}).get("max_sector_weight")),
            "best_risk_regime": ((regime.get("interpretation") or {}).get("best_risk_regime")),
            "worst_risk_regime": ((regime.get("interpretation") or {}).get("worst_risk_regime")),
            "decision_attribution_status": (((decisions.get("strategies") or {}).get(slug) or {}).get("status")),
        }
    return {
        "schema_version": "caerus_attribution_summary_v1",
        "trade_date": trade_date,
        "classification": "RESEARCH_GRADE_PHASE_1_PARTIAL",
        "methodology_note": "Position attribution uses current-book trailing exposure; regime attribution uses persisted Shadow NAV history.",
        "strategies": strategies,
        "cio_questions": {
            "where_returns_come_from": "See contribution_report.json top contributors/detractors and rolling contribution series.",
            "are_gains_concentrated": "See concentration_analysis.json and top3 contribution shares.",
            "are_challengers_momentum_beta": "See factor_exposure.json market beta, momentum exposure, and sector concentration flags.",
            "which_regimes_help_or_hurt": "See regime_analysis.json risk/volatility/trend/breadth buckets.",
            "durable_or_regime_dependent": "Phase 1 provides descriptive evidence; durability requires longer clean chain and historical holdings.",
        },
    }


def build_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Caerus Strategy Attribution Summary",
        "",
        "## Executive Summary",
        f"- Trade date: `{summary['trade_date']}`",
        f"- Classification: `{summary['classification']}`",
        f"- Methodology: {summary['methodology_note']}",
        "",
        "## Strategy Readout",
        "| Strategy | 21d Current-Book Return | Top Contributor | Top Detractor | Beta | Hidden Factor Flags | Best Risk Regime |",
        "|---|---:|---|---|---:|---|---|",
    ]
    for slug in STRATEGY_SLUGS:
        item = summary["strategies"][slug]
        top = item.get("primary_21d_return_source") or {}
        bad = item.get("primary_21d_detractor") or {}
        flags = ", ".join(item.get("hidden_factor_flags") or []) or "None"
        lines.append(
            "| "
            + " | ".join(
                [
                    DISPLAY_NAMES[slug],
                    "N/A" if item.get("portfolio_21d_return_current_book") is None else f"{item['portfolio_21d_return_current_book']:+.2%}",
                    str(top.get("ticker") or "N/A"),
                    str(bad.get("ticker") or "N/A"),
                    "N/A" if item.get("market_beta") is None else f"{item['market_beta']:.2f}",
                    flags,
                    str(item.get("best_risk_regime") or "N/A"),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## CIO Interpretation Guidance",
            "- Treat current-book contribution as exposure attribution, not a full historical holdings audit.",
            "- A high top-3 contribution share means outperformance may be concentration amplification rather than broad selection skill.",
            "- High beta, high momentum exposure, or single-sector dominance should be interpreted as hidden factor dependence until residual alpha is isolated.",
            "- Regime results are descriptive and should guide promotion questions, not mechanically approve a strategy.",
            "",
            "## Limitations",
            "- Full position-realized attribution requires daily historical holdings/weights for each strategy.",
            "- Value, growth, market-cap, and profitability tilts are proxy-only or unavailable without point-in-time fundamentals.",
            "- Current price panel is daily close based; intraday execution quality and fill realism are outside this framework.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_outputs(output_dir: Path, payloads: dict[str, Any], markdown: str) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "contribution_report.json": payloads["contribution_report"],
        "factor_exposure.json": payloads["factor_exposure"],
        "regime_analysis.json": payloads["regime_analysis"],
        "concentration_analysis.json": payloads["concentration_analysis"],
        "decision_attribution.json": payloads["decision_attribution"],
        "attribution_summary.json": payloads["attribution_summary"],
        "nav_surface_registry.json": payloads["nav_surface_registry"],
        "surface_metadata.json": payloads["surface_metadata"],
        "broker_authoritative_nav.json": payloads["broker_authoritative_nav"],
        "exposure_summary.json": payloads["exposure_summary.json"],
        "factor_risk_flags.json": payloads["factor_risk_flags.json"],
        "concentration_monitor.json": payloads["concentration_monitor.json"],
        "regime_performance_breakdown.json": payloads["regime_performance_breakdown.json"],
        "regime_fragility_report.json": payloads["regime_fragility_report.json"],
        "regime_exposure_matrix.json": payloads["regime_exposure_matrix.json"],
        "attribution_by_regime.json": payloads["attribution_by_regime.json"],
        "regime_transition_analysis.json": payloads["regime_transition_analysis.json"],
        "exposure_drift_summary.json": payloads["exposure_drift_summary"],
        "track_b_governance_recommendations.json": payloads["track_b_governance_recommendations"],
    }
    written: list[Path] = []
    for name, payload in files.items():
        path = output_dir / name
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        written.append(path)
    md_path = output_dir / "attribution_summary.md"
    md_path.write_text(markdown, encoding="utf-8")
    written.append(md_path)
    exposure_md = output_dir / "exposure_intelligence_summary.md"
    exposure_md.write_text(payloads["exposure_intelligence_markdown"], encoding="utf-8")
    written.append(exposure_md)
    return written


def run_attribution(
    *,
    repo_root: Path,
    trade_date: str | None,
    price_cache_path: Path,
    shadow_root: Path,
    output_root: Path,
    lookback_days: int,
) -> tuple[dict[str, Any], list[Path]]:
    effective_trade_date = trade_date or _latest_snapshot_date(shadow_root)
    if not effective_trade_date:
        raise SystemExit("No dated shadow strategy snapshots available for attribution.")
    snapshots = _load_strategy_snapshots(shadow_root, effective_trade_date)
    missing = [slug for slug in STRATEGY_SLUGS if slug not in snapshots]
    if missing:
        raise SystemExit(f"Missing strategy snapshots for {effective_trade_date}: {missing}")
    panel = _load_price_panel(price_cache_path, through_date=effective_trade_date)
    returns = _returns_matrix(panel)
    prices = _price_matrix(panel)
    sector_map = _load_sector_map(repo_root / "data" / "universe.csv")
    contribution = build_position_attribution(
        snapshots=snapshots,
        returns=returns,
        trade_date=effective_trade_date,
        sector_map=sector_map,
        lookback_days=lookback_days,
    )
    factors = build_factor_exposure(
        snapshots=snapshots,
        panel=panel,
        returns=returns,
        prices=prices,
        trade_date=effective_trade_date,
        sector_map=sector_map,
        lookback_days=lookback_days,
    )
    regimes = build_regime_analysis(repo_root=repo_root, panel=panel, trade_date=effective_trade_date)
    decisions = build_decision_attribution(snapshots, trade_date=effective_trade_date)
    concentration = {
        "schema_version": "caerus_concentration_analysis_v1",
        "trade_date": effective_trade_date,
        "strategies": {
            slug: (contribution["strategies"][slug].get("concentration_impact") or {})
            for slug in STRATEGY_SLUGS
        },
    }
    summary = build_summary(
        trade_date=effective_trade_date,
        contribution=contribution,
        factors=factors,
        regimes=regimes,
        decisions=decisions,
    )
    nav_surface_registry = build_nav_surface_registry(repo_root=repo_root, trade_date=effective_trade_date)
    exposure_artifacts = build_exposure_intelligence(
        trade_date=effective_trade_date,
        summary=summary,
        factors=factors,
        contribution=contribution,
        concentration=concentration,
    )
    regime_artifacts = build_regime_hardening_artifacts(
        regimes=regimes,
        contribution=contribution,
        factors=factors,
        trade_date=effective_trade_date,
    )
    track_b_governance = build_track_b_governance_recommendations(trade_date=effective_trade_date)
    payloads = {
        "contribution_report": contribution,
        "factor_exposure": factors,
        "regime_analysis": regimes,
        "concentration_analysis": concentration,
        "decision_attribution": decisions,
        "attribution_summary": summary,
        "nav_surface_registry": nav_surface_registry,
        "surface_metadata": {
            "schema_version": "caerus_surface_metadata_v1",
            "trade_date": effective_trade_date,
            "surfaces": nav_surface_registry["surfaces"],
            "dashboard_policy": "Display surface type, confidence, execution realism, and point-in-time validity beside every performance number.",
        },
        "broker_authoritative_nav": nav_surface_registry["surfaces"]["live_broker_paper_nav"],
        "track_b_governance_recommendations": track_b_governance,
    }
    payloads.update(exposure_artifacts)
    payloads.update(regime_artifacts)
    output_dir = output_root / effective_trade_date
    markdown = build_markdown(summary)
    rolling_rows = _rolling_exposure_rows(summary, factors, concentration, effective_trade_date)
    drift_summary = build_exposure_drift_summary(
        output_root / "rolling_exposure_history.csv",
        rolling_rows,
        trade_date=effective_trade_date,
    )
    payloads["exposure_drift_summary"] = drift_summary
    payloads["exposure_intelligence_markdown"] = build_exposure_markdown(
        payloads["exposure_summary.json"],
        payloads["factor_risk_flags.json"],
        drift_summary,
    )
    written = write_outputs(output_dir, payloads, markdown)
    rolling_path = _write_rolling_exposure_history(
        output_root / "rolling_exposure_history.csv",
        rolling_rows,
    )
    written.append(rolling_path)
    history_payloads = build_portfolio_history_payloads(
        snapshots=snapshots,
        sector_map=sector_map,
        trade_date=effective_trade_date,
    )
    history_dir = repo_root / "outputs" / "portfolio_history" / effective_trade_date
    history_manifest = {
        "schema_version": "caerus_portfolio_history_manifest_v1",
        "trade_date": effective_trade_date,
        "files": {},
        "immutability_policy": "Existing files with different content are not overwritten.",
        "integrity_algorithm": "sha256",
    }
    for filename, payload in history_payloads.items():
        path, status = _write_json_immutable(history_dir / filename, payload)
        history_manifest["files"][filename] = {
            "path": str(path.relative_to(repo_root)),
            "write_status": status,
            "sha256": _sha256_payload(payload),
            "schema_version": payload.get("schema_version"),
            "snapshot_complete": set(STRATEGY_SLUGS).issubset(set((payload.get("strategies") or {}).keys())),
        }
        written.append(path)
    history_manifest["validation"] = validate_portfolio_history_manifest(history_manifest, repo_root=repo_root)
    manifest_path, manifest_status = _write_json_immutable(history_dir / "manifest.json", history_manifest)
    written.append(manifest_path)
    if manifest_status == "IMMUTABLE_CONFLICT_EXISTING_DIFFERENT":
        integrity_sidecar = {
            "schema_version": "caerus_portfolio_history_integrity_sidecar_v1",
            "trade_date": effective_trade_date,
            "status": "MANIFEST_IMMUTABLE_CONFLICT_EXISTING_DIFFERENT",
            "reason": "Existing manifest was preserved; checksum validation emitted as additive sidecar.",
            "intended_manifest": history_manifest,
        }
        sidecar_path = history_dir / "manifest_integrity.json"
        sidecar_path.write_text(json.dumps(integrity_sidecar, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(sidecar_path)
    return summary, written


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    price_cache_path = (repo_root / args.price_cache_path).resolve() if not Path(args.price_cache_path).is_absolute() else Path(args.price_cache_path)
    shadow_root = (repo_root / args.shadow_root).resolve() if not Path(args.shadow_root).is_absolute() else Path(args.shadow_root)
    output_root = (repo_root / args.output_root).resolve() if not Path(args.output_root).is_absolute() else Path(args.output_root)
    summary, written = run_attribution(
        repo_root=repo_root,
        trade_date=args.trade_date,
        price_cache_path=price_cache_path,
        shadow_root=shadow_root,
        output_root=output_root,
        lookback_days=args.lookback_days,
    )
    print(f"[ATTRIBUTION] trade_date={summary['trade_date']} classification={summary['classification']}")
    for path in written:
        print(f"[ATTRIBUTION] wrote {path.relative_to(repo_root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

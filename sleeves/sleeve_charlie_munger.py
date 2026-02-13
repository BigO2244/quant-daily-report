from __future__ import annotations

import datetime as dt
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from engine.backtest_engine import run_backtest as engine_run_backtest

logger = logging.getLogger(__name__)

_CACHE_ROOT = Path("outputs/cache/charlie_munger")


@dataclass
class CharlieMungerConfig:
    enabled: bool = True
    universe: str = "sp500"
    entry_band: float = 0.05
    use_cross_above: bool = False
    ma_weeks: int = 200
    rebalance_freq: str = "QE"
    target_holdings: int = 15
    min_holdings: int = 10
    max_weight_per_name: float = 0.10
    weighting: str = "equal"
    rebalance_threshold: float = 0.01
    benchmark: str = "SPY"
    allow_missing_fundamentals: bool = True
    quality_min_score: float = 70.0
    quality_exit_min_score: float = 60.0
    quality_consecutive_periods: int = 2
    market_cap_min: float = 10_000_000_000
    roic_min: float = 0.12
    roe_min: float = 0.15
    fcf_positive_years_min: int = 8
    net_debt_to_ebitda_max: float = 2.5


DEFAULT_CONFIG = CharlieMungerConfig()


def normalize_rebalance_freq(freq: str | None) -> str:
    """Map config rebalance frequency aliases to pandas-supported rules."""
    raw = str(freq or "").strip().upper()
    if raw in {"Q", "QE", "Q-DEC", "QE-DEC"} or raw.startswith("Q"):
        return "Q"
    if raw in {"M", "ME"} or raw.startswith("M"):
        return "M"
    return "M"


def load_config() -> CharlieMungerConfig:
    cfg = CharlieMungerConfig()
    cfg_file = Path("sleeves/charlie_munger_config.json")
    if cfg_file.exists():
        try:
            payload = json.loads(cfg_file.read_text())
            raw = payload.get("charlie_munger", payload)
            quality = raw.get("quality", {}) if isinstance(raw, dict) else {}
            for key in [
                "enabled", "universe", "entry_band", "use_cross_above", "ma_weeks",
                "rebalance_freq", "target_holdings", "min_holdings", "max_weight_per_name",
                "weighting", "rebalance_threshold", "benchmark", "allow_missing_fundamentals",
            ]:
                if key in raw:
                    setattr(cfg, key, raw[key])
            if "min_score" in quality:
                cfg.quality_min_score = quality["min_score"]
            if "exit_min_score" in quality:
                cfg.quality_exit_min_score = quality["exit_min_score"]
            if "consecutive_periods" in quality:
                cfg.quality_consecutive_periods = quality["consecutive_periods"]
            if "market_cap_min" in quality:
                cfg.market_cap_min = quality["market_cap_min"]
            if "roic_min" in quality:
                cfg.roic_min = quality["roic_min"]
            if "roe_min" in quality:
                cfg.roe_min = quality["roe_min"]
            if "fcf_positive_years_min" in quality:
                cfg.fcf_positive_years_min = quality["fcf_positive_years_min"]
            if "net_debt_to_ebitda_max" in quality:
                cfg.net_debt_to_ebitda_max = quality["net_debt_to_ebitda_max"]
        except Exception as exc:
            logger.warning("[CHARLIE] Failed to load config file: %s", exc)

    cfg.entry_band = float(os.getenv("CM_ENTRY_BAND", cfg.entry_band))
    cfg.use_cross_above = os.getenv("CM_USE_CROSS_ABOVE", "0").lower() in {"1", "true", "yes"}
    cfg.target_holdings = int(os.getenv("CM_TARGET_HOLDINGS", cfg.target_holdings))
    cfg.min_holdings = int(os.getenv("CM_MIN_HOLDINGS", cfg.min_holdings))
    cfg.weighting = os.getenv("CM_WEIGHTING", cfg.weighting)
    cfg.rebalance_freq = os.getenv("CM_REBALANCE_FREQ", cfg.rebalance_freq)
    cfg.allow_missing_fundamentals = os.getenv("CM_ALLOW_MISSING_FUNDAMENTALS", "1").lower() in {"1", "true", "yes"}
    return cfg


def compute_200w_sma(weekly_close: pd.Series, window: int = 200) -> pd.Series:
    return weekly_close.rolling(window=window, min_periods=window).mean()


def is_entry_signal(
    price_to_ma: float,
    prev_price_to_ma: float | None,
    entry_band: float,
    use_cross_above: bool,
) -> bool:
    near_ma = abs(price_to_ma - 1.0) <= entry_band
    if use_cross_above and prev_price_to_ma is not None:
        return (price_to_ma >= 1.0) and (prev_price_to_ma < 1.0)
    return near_ma


def score_quality(fundamentals: dict, cfg: CharlieMungerConfig = DEFAULT_CONFIG) -> tuple[float | None, dict]:
    if not fundamentals:
        return None, {"missing": True}

    score = 0.0
    comps = {}

    market_cap = fundamentals.get("market_cap")
    mc_pass = market_cap is not None and market_cap >= cfg.market_cap_min
    comps["market_cap"] = {"value": market_cap, "pass": mc_pass, "weight": 25}
    score += 25 if mc_pass else 0

    roic = fundamentals.get("roic")
    roe = fundamentals.get("roe")
    q_profit = (roic is not None and roic >= cfg.roic_min) or (roe is not None and roe >= cfg.roe_min)
    comps["profitability"] = {"roic": roic, "roe": roe, "pass": q_profit, "weight": 25}
    score += 25 if q_profit else 0

    fcf_positive_years = fundamentals.get("fcf_positive_years")
    fcf_pass = fcf_positive_years is not None and fcf_positive_years >= cfg.fcf_positive_years_min
    comps["fcf_consistency"] = {"value": fcf_positive_years, "pass": fcf_pass, "weight": 25}
    score += 25 if fcf_pass else 0

    nde = fundamentals.get("net_debt_to_ebitda")
    nde_pass = nde is not None and nde <= cfg.net_debt_to_ebitda_max
    comps["leverage"] = {"value": nde, "pass": nde_pass, "weight": 25}
    score += 25 if nde_pass else 0

    comps["final"] = score
    return score, comps


def _fetch_sp500_universe() -> list[str]:
    _CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    cache_path = _CACHE_ROOT / "sp500_universe.csv"
    if cache_path.exists():
        return sorted(pd.read_csv(cache_path)["ticker"].astype(str).str.upper().tolist())

    try:
        table = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")[0]
        tickers = sorted(table["Symbol"].astype(str).str.upper().str.replace(".", "-", regex=False).tolist())
        pd.DataFrame({"ticker": tickers}).to_csv(cache_path, index=False)
        return tickers
    except Exception:
        logger.warning("[CHARLIE] Failed to fetch S&P 500 constituents; falling back to data/universe.csv")
        if os.path.exists("data/universe.csv"):
            return sorted(pd.read_csv("data/universe.csv")["ticker"].astype(str).str.upper().tolist())
        return []


def _fetch_fundamentals(ticker: str) -> dict:
    _CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    cache_path = _CACHE_ROOT / f"fund_{ticker}.json"
    if cache_path.exists():
        age_days = (dt.datetime.utcnow() - dt.datetime.utcfromtimestamp(cache_path.stat().st_mtime)).days
        if age_days <= 30:
            try:
                return json.loads(cache_path.read_text())
            except Exception:
                pass

    info = {}
    try:
        tk = yf.Ticker(ticker)
        i = tk.info or {}
        info = {
            "market_cap": i.get("marketCap"),
            "roic": i.get("returnOnCapital"),
            "roe": i.get("returnOnEquity"),
            "net_debt_to_ebitda": i.get("netDebtToEbitda"),
            "fcf_positive_years": 10 if i.get("freeCashflow") and i.get("freeCashflow") > 0 else 0,
        }
    except Exception:
        info = {}

    cache_path.write_text(json.dumps(info))
    return info


def _download_prices(tickers: list[str], period: str = "15y") -> pd.DataFrame:
    px = yf.download(
        tickers=tickers,
        period=period,
        interval="1d",
        group_by="ticker",
        auto_adjust=False,
        progress=False,
        threads=False,
    )
    if px is None or px.empty:
        return pd.DataFrame()

    rows = []
    for t in tickers:
        try:
            tdf = px[t].reset_index().rename(columns={"Date": "date", "Adj Close": "adj_close", "Close": "close"})
            tdf["ticker"] = t
            rows.append(tdf[["date", "ticker", "close", "adj_close"]])
        except Exception:
            continue

    if not rows:
        return pd.DataFrame()
    out = pd.concat(rows, ignore_index=True)
    out["date"] = pd.to_datetime(out["date"])
    return out


def _daily_to_weekly(prices: pd.DataFrame) -> pd.DataFrame:
    if prices.empty:
        return prices
    data = prices.copy()
    data["px"] = pd.to_numeric(data.get("adj_close", data.get("close")), errors="coerce")
    weekly = (
        data.sort_values(["ticker", "date"])
        .set_index("date")
        .groupby("ticker")["px"]
        .resample("W-FRI")
        .last()
        .dropna()
        .reset_index()
    )
    return weekly.rename(columns={"px": "close"})


def _compute_stats(series: pd.Series) -> dict:
    if series.empty:
        return {"cumulative_return": None, "max_drawdown": None}
    cum = (1 + series).cumprod()
    peak = cum.cummax()
    dd = (cum / peak) - 1.0
    return {
        "cumulative_return": float(cum.iloc[-1] - 1.0),
        "max_drawdown": float(dd.min()),
    }


def run_backtest_with_details(period: str = "15y", interval: str = "1d") -> dict:
    del interval
    cfg = load_config()
    if not cfg.enabled:
        return {"equity_df": pd.DataFrame(), "trades_df": pd.DataFrame(), "signals": {"selected": []}}

    universe = _fetch_sp500_universe()
    tickers = universe[: max(cfg.target_holdings * 8, 80)]
    prices = _download_prices(sorted(set(tickers + [cfg.benchmark])), period=period)
    if prices.empty:
        return {"equity_df": pd.DataFrame(), "trades_df": pd.DataFrame(), "signals": {"selected": []}}

    weekly = _daily_to_weekly(prices[prices["ticker"].isin(tickers)])
    asof = weekly["date"].max()
    candidates = []

    for t in tickers:
        ts = weekly[weekly["ticker"] == t].sort_values("date")
        if len(ts) < cfg.ma_weeks:
            continue
        ts["ma"] = compute_200w_sma(ts["close"], cfg.ma_weeks)
        ts = ts.dropna(subset=["ma"])
        if ts.empty:
            continue
        last = ts.iloc[-1]
        prev = ts.iloc[-2] if len(ts) > 1 else None
        p2m = float(last["close"] / last["ma"]) if last["ma"] else np.nan
        prev_p2m = float(prev["close"] / prev["ma"]) if prev is not None and prev["ma"] else None
        if not np.isfinite(p2m):
            continue
        if not is_entry_signal(p2m, prev_p2m, cfg.entry_band, cfg.use_cross_above):
            continue

        fundamentals = _fetch_fundamentals(t)
        q_score, q_debug = score_quality(fundamentals, cfg)
        quality_ok = (q_score is not None and q_score >= cfg.quality_min_score) or (
            q_score is None and cfg.allow_missing_fundamentals
        )
        if not quality_ok:
            continue
        candidates.append(
            {
                "ticker": t,
                "quality_score": q_score if q_score is not None else 0.0,
                "price": float(last["close"]),
                "ma_200w": float(last["ma"]),
                "price_to_ma": p2m,
                "quality_debug": q_debug,
            }
        )

    candidates = sorted(candidates, key=lambda x: (-x["quality_score"], abs(x["price_to_ma"] - 1.0)))
    selected = candidates[: cfg.target_holdings]
    if len(selected) < cfg.min_holdings:
        selected = candidates[: cfg.min_holdings]

    if cfg.weighting == "quality" and selected:
        q_sum = sum(max(1.0, s["quality_score"]) for s in selected)
        for s in selected:
            s["target_weight"] = min(cfg.max_weight_per_name, max(1.0, s["quality_score"]) / q_sum)
    elif selected:
        w = min(cfg.max_weight_per_name, 1.0 / len(selected))
        for s in selected:
            s["target_weight"] = w

    if selected:
        w_sum = sum(s["target_weight"] for s in selected)
        for s in selected:
            s["target_weight"] = s["target_weight"] / w_sum

    idx = pd.DatetimeIndex(sorted(weekly["date"].unique()))
    target_w = pd.DataFrame(0.0, index=idx, columns=[s["ticker"] for s in selected])
    if not target_w.empty:
        target_w.loc[idx >= asof, :] = [s["target_weight"] for s in selected]

    # quarterly/monthly rebalancing approximation
    rebal_rule = normalize_rebalance_freq(cfg.rebalance_freq)
    px_wide = weekly.pivot(index="date", columns="ticker", values="close")
    px_wide = px_wide[target_w.columns] if not target_w.empty else pd.DataFrame(index=idx)

    bt = engine_run_backtest(
        target_weights=target_w,
        prices=px_wide,
        initial_equity=10_000.0,
        commission_bps=5.0,
        slippage_bps=5.0,
        rebal_rule=rebal_rule,
    ) if not target_w.empty else {"equity_curve": pd.DataFrame(), "trades": pd.DataFrame(), "weights": pd.DataFrame()}

    bench_prices = weekly[weekly["ticker"] == cfg.benchmark].sort_values("date")
    bench_rets = bench_prices["close"].pct_change().fillna(0.0)
    bench_stats = _compute_stats(bench_rets)

    eq = bt.get("equity_curve", pd.DataFrame())
    if not eq.empty:
        eq = eq.copy()
        eq["sleeve_return"] = eq["equity"].pct_change().fillna(0.0)
        sleeve_stats = _compute_stats(eq["sleeve_return"])
    else:
        sleeve_stats = {"cumulative_return": None, "max_drawdown": None}

    signals = build_signals(asof, selected, candidates, len(tickers), cfg)
    return {
        "equity_df": bt.get("equity_curve", pd.DataFrame()),
        "trades_df": bt.get("trades", pd.DataFrame()),
        "weights_df": bt.get("weights", pd.DataFrame()),
        "target_weights": target_w,
        "asof": asof,
        "signals": signals,
        "benchmark": {"ticker": cfg.benchmark, **bench_stats},
        "sleeve_stats": sleeve_stats,
    }


def build_signals(as_of_date: pd.Timestamp, selected: list[dict], candidates: list[dict], universe_size: int, cfg: CharlieMungerConfig) -> dict:
    selected_payload = []
    for row in selected:
        selected_payload.append(
            {
                "ticker": row["ticker"],
                "action": "BUY",
                "target_weight": row["target_weight"],
                "quality_score": row["quality_score"],
                "price": row["price"],
                "ma_200w": row["ma_200w"],
                "price_to_ma": row["price_to_ma"],
                "reason": "NEAR_200W_MA",
            }
        )

    return {
        "sleeve": "charlie_munger",
        "as_of": pd.to_datetime(as_of_date).strftime("%Y-%m-%d") if as_of_date is not None else None,
        "universe_size": int(universe_size),
        "selected": selected_payload,
        "hold": [],
        "sell": [],
        "meta": {
            "params": cfg.__dict__,
            "notes": "Long-only quality accumulation near 200-week MA",
            "near_ma_candidates": len(candidates),
            "debug_top50": candidates[:50],
        },
    }

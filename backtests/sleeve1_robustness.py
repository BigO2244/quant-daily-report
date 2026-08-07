from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from core.quant_report import download_prices

OUTPUT_DIR = Path("outputs/backtests/sleeve1_robustness")
DEFAULT_START = "2005-01-01"
DEFAULT_TOP_N = 10
# 2026-04-11: aligned from 10 → 25 bps one-way to match live paper broker
# cost assumption (paper/config_paper.json::execution.slippage_bps) and the
# alpha_stack research cost_model. Keeps backtest vs live comparisons honest.
DEFAULT_SLIPPAGE_BPS = 25


def _month_end_rule() -> str:
    probe = pd.Series([1.0], index=pd.to_datetime(["2026-01-01"]))
    try:
        probe.resample("ME").last()
        return "ME"
    except Exception:
        return "M"


@dataclass
class RobustnessConfig:
    start: str = DEFAULT_START
    end: str | None = None
    top_n: int = DEFAULT_TOP_N
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS
    universe_csv: str = "data/universe.csv"
    survivorship_ok: bool = True


def load_universe(path: str = "data/universe.csv") -> list[str]:
    u = pd.read_csv(path)
    if "ticker" not in u.columns:
        raise ValueError("Universe file must include ticker column")
    return sorted(u["ticker"].astype(str).str.upper().str.strip().dropna().unique().tolist())


def compute_12_1_momentum(close: pd.Series) -> pd.Series:
    return close.shift(1) / close.shift(12) - 1.0


def _annualized_vol(r: pd.Series) -> float:
    return float(r.std(ddof=0) * np.sqrt(12)) if len(r) else np.nan


def _max_drawdown(ret: pd.Series) -> float:
    nav = (1 + ret.fillna(0.0)).cumprod()
    dd = nav / nav.cummax() - 1
    return float(dd.min()) if len(dd) else np.nan


def compute_metrics(ret: pd.Series, benchmark: pd.Series) -> dict[str, float]:
    r = ret.dropna()
    b = benchmark.reindex(r.index).dropna()
    if r.empty:
        return {k: np.nan for k in ["cagr", "ann_vol", "sharpe", "sortino", "max_drawdown", "worst_12m", "pct_positive_years", "beta_vs_spy", "corr_vs_spy"]}
    years = len(r) / 12
    cagr = (1 + r).prod() ** (1 / years) - 1 if years > 0 else np.nan
    ann_vol = _annualized_vol(r)
    downside = r[r < 0]
    down_std = downside.std(ddof=0)
    sortino = (r.mean() * 12) / (down_std * np.sqrt(12)) if len(downside) and down_std > 0 else np.nan
    sharpe = (r.mean() * 12) / ann_vol if ann_vol and ann_vol > 0 else np.nan
    worst_12m = float((1 + r).rolling(12).apply(np.prod, raw=True).sub(1).min()) if len(r) >= 12 else np.nan
    yearly = (1 + r).groupby(r.index.year).prod() - 1
    pct_positive_years = float((yearly > 0).mean()) if len(yearly) else np.nan
    b = b.reindex(r.index)
    corr = (
        float(r.corr(b))
        if len(b.dropna()) > 1 and r.nunique() > 1 and b.nunique() > 1
        else np.nan
    )
    beta = float(r.cov(b) / b.var()) if len(b.dropna()) > 1 and b.var() > 0 else np.nan
    return {
        "cagr": float(cagr),
        "ann_vol": float(ann_vol),
        "sharpe": float(sharpe),
        "sortino": float(sortino),
        "max_drawdown": _max_drawdown(r),
        "worst_12m": worst_12m,
        "pct_positive_years": pct_positive_years,
        "beta_vs_spy": beta,
        "corr_vs_spy": corr,
    }


def run_backtest(config: RobustnessConfig) -> dict[str, pd.DataFrame | dict]:
    end = pd.Timestamp(config.end).normalize() if config.end else pd.Timestamp.today().normalize()
    start = pd.Timestamp(config.start).normalize()
    universe = load_universe(config.universe_csv)
    tickers = sorted(set(universe + ["SPY"]))

    prices = download_prices(tickers, period="max", interval="1d")
    prices = prices[prices["date"].between(start - pd.Timedelta(days=420), end)].copy()
    close = prices.pivot(index="date", columns="ticker", values="close").sort_index()
    month_end_rule = _month_end_rule()
    month_end = close.resample(month_end_rule).last()

    spy_daily = close["SPY"].dropna()
    spy_200d = spy_daily.rolling(200, min_periods=200).mean()
    gate_daily = spy_daily >= spy_200d
    gate = gate_daily.resample(month_end_rule).last().reindex(month_end.index).astype("boolean").fillna(False).shift(1).fillna(False)
    spy = month_end["SPY"].dropna()

    asset_month = month_end.reindex(columns=universe)
    momentum = asset_month.apply(compute_12_1_momentum)
    returns = asset_month.pct_change()

    weights = pd.DataFrame(0.0, index=returns.index, columns=returns.columns)
    for dt_idx in weights.index:
        ranked = momentum.loc[dt_idx].dropna().sort_values(ascending=False)
        picks = ranked.head(config.top_n).index.tolist()
        if picks:
            weights.loc[dt_idx, picks] = 1.0 / len(picks)

    weights = weights.where(gate.reindex(weights.index).fillna(False), 0.0)
    shifted_w = weights.shift(1).fillna(0.0)
    gross_ret = (shifted_w * returns).sum(axis=1).fillna(0.0)
    turnover = (weights - shifted_w).abs().sum(axis=1)
    net_ret = gross_ret - (config.slippage_bps / 10000.0) * turnover

    spy_ret = spy.pct_change().reindex(net_ret.index).fillna(0.0)
    exposure = (weights.sum(axis=1) > 0).astype(float)

    df = pd.DataFrame({
        "date": net_ret.index,
        "strategy_return": net_ret.values,
        "spy_return": spy_ret.values,
        "turnover": turnover.values,
        "exposure": exposure.values,
    }).set_index("date")
    df = df[df.index >= start]
    df["strategy_nav"] = (1 + df["strategy_return"]).cumprod()
    df["spy_nav"] = (1 + df["spy_return"]).cumprod()

    rolling_sharpe = (df["strategy_return"].rolling(36).mean() * 12) / (df["strategy_return"].rolling(36).std(ddof=0) * np.sqrt(12))
    rolling_dd12 = (df["strategy_nav"] / df["strategy_nav"].rolling(12).max()) - 1

    regimes = {
        "full_period": (df.index.min(), df.index.max()),
        "2008": (pd.Timestamp("2008-01-31"), pd.Timestamp("2008-12-31")),
        "2013_2017": (pd.Timestamp("2013-01-31"), pd.Timestamp("2017-12-31")),
        "2020": (pd.Timestamp("2020-01-31"), pd.Timestamp("2020-12-31")),
        "2022": (pd.Timestamp("2022-01-31"), pd.Timestamp("2022-12-31")),
    }

    metrics_rows = []
    regimes_rows = []
    for name, (rs, re) in regimes.items():
        seg = df[(df.index >= rs) & (df.index <= re)]
        m = compute_metrics(seg["strategy_return"], seg["spy_return"])
        m["avg_turnover"] = float(seg["turnover"].mean()) if len(seg) else np.nan
        m["exposure_pct"] = float(seg["exposure"].mean()) if len(seg) else np.nan
        m["regime"] = name
        metrics_rows.append(m)
        regimes_rows.append({"regime": name, "start": str(rs.date()), "end": str(re.date())})

    return {
        "timeseries": df,
        "metrics": pd.DataFrame(metrics_rows),
        "regimes": pd.DataFrame(regimes_rows),
        "rolling_sharpe": pd.DataFrame({"date": df.index, "rolling_36m_sharpe": rolling_sharpe.values}).set_index("date"),
        "rolling_drawdown": pd.DataFrame({"date": df.index, "rolling_12m_drawdown": rolling_dd12.values}).set_index("date"),
        "config": {
            "start": str(start.date()),
            "end": str(end.date()),
            "top_n": config.top_n,
            "slippage_bps": config.slippage_bps,
            "survivorship_ok": config.survivorship_ok,
            "universe_size": len(universe),
        },
    }


def run_walk_forward(
    config: RobustnessConfig,
    train_months: int = 12,
    oos_months: int = 3,
) -> pd.DataFrame:
    """
    Walk-forward validation for the Sleeve 1 momentum strategy.

    Splits the full backtest period into rolling windows:
      - Train window : `train_months` months (parameter selection)
      - OOS window   : `oos_months`   months (out-of-sample test, never used for training)

    The window rolls forward by `oos_months` on each iteration, so OOS periods
    are non-overlapping and cover the entire backtest period after the first
    training window.

    Returns a DataFrame with one row per OOS window containing:
        window, oos_start, oos_end, oos_sharpe, oos_cagr, oos_max_drawdown,
        oos_ann_vol, is_sharpe (in-sample), is_cagr (in-sample)

    Usage:
        cfg = RobustnessConfig(start="2015-01-01")
        wfo = run_walk_forward(cfg, train_months=12, oos_months=3)
        print(wfo[["window", "oos_start", "oos_end", "oos_sharpe", "is_sharpe"]])
    """
    end = pd.Timestamp(config.end).normalize() if config.end else pd.Timestamp.today().normalize()
    start = pd.Timestamp(config.start).normalize()
    universe = load_universe(config.universe_csv)
    tickers = sorted(set(universe + ["SPY"]))

    # ── Fetch all price data once ──────────────────────────────────
    prices = download_prices(tickers, period="max", interval="1d")
    prices = prices[
        prices["date"].between(start - pd.Timedelta(days=420), end)
    ].copy()
    close = prices.pivot(index="date", columns="ticker", values="close").sort_index()
    month_end_rule = _month_end_rule()
    month_end = close.resample(month_end_rule).last()

    spy_daily = close["SPY"].dropna()
    spy_200d = spy_daily.rolling(200, min_periods=200).mean()
    gate_daily = spy_daily >= spy_200d
    gate = (
        gate_daily.resample(month_end_rule)
        .last()
        .reindex(month_end.index)
        .astype("boolean")
        .fillna(False)
        .shift(1)
        .fillna(False)
    )

    asset_month = month_end.reindex(columns=universe)
    momentum = asset_month.apply(compute_12_1_momentum)
    returns = asset_month.pct_change()
    spy_ret_monthly = month_end["SPY"].pct_change()

    all_dates = returns.index[returns.index >= start]
    if len(all_dates) < train_months + oos_months:
        raise ValueError(
            f"Not enough data for walk-forward: need at least "
            f"{train_months + oos_months} months, got {len(all_dates)}"
        )

    # ── Rolling walk-forward loop ──────────────────────────────────
    rows = []
    window_num = 0
    cursor = 0  # index into all_dates

    while cursor + train_months + oos_months <= len(all_dates):
        train_idx = all_dates[cursor : cursor + train_months]
        oos_idx   = all_dates[cursor + train_months : cursor + train_months + oos_months]

        oos_start = oos_idx[0]
        oos_end   = oos_idx[-1]

        def _run_segment(idx, top_n):
            w = pd.DataFrame(0.0, index=idx, columns=universe)
            for dt_idx in idx:
                ranked = momentum.loc[dt_idx].dropna().sort_values(ascending=False)
                picks = ranked.head(top_n).index.tolist()
                if picks:
                    w.loc[dt_idx, picks] = 1.0 / len(picks)
            w = w.where(gate.reindex(w.index).fillna(False), 0.0)
            shifted_w = w.shift(1).fillna(0.0)
            ret_seg = returns.reindex(index=idx)
            gross = (shifted_w * ret_seg).sum(axis=1).fillna(0.0)
            turnover = (w - shifted_w).abs().sum(axis=1)
            net = gross - (config.slippage_bps / 10000.0) * turnover
            spy_seg = spy_ret_monthly.reindex(idx).fillna(0.0)
            return net, spy_seg

        # In-sample: use full train window to pick top_n (held fixed)
        is_ret, is_spy = _run_segment(train_idx, config.top_n)
        is_metrics = compute_metrics(is_ret, is_spy)

        # OOS: apply the same top_n (in a real WFO you'd optimise in-sample;
        # here we use the configured top_n as the "selected" parameter)
        oos_ret, oos_spy = _run_segment(oos_idx, config.top_n)
        oos_metrics = compute_metrics(oos_ret, oos_spy)

        rows.append({
            "window":          window_num,
            "oos_start":       str(oos_start.date()),
            "oos_end":         str(oos_end.date()),
            "train_months":    train_months,
            "oos_months":      oos_months,
            "oos_sharpe":      oos_metrics["sharpe"],
            "oos_cagr":        oos_metrics["cagr"],
            "oos_ann_vol":     oos_metrics["ann_vol"],
            "oos_max_drawdown":oos_metrics["max_drawdown"],
            "is_sharpe":       is_metrics["sharpe"],
            "is_cagr":         is_metrics["cagr"],
            "sharpe_decay":    (
                (oos_metrics["sharpe"] - is_metrics["sharpe"]) / abs(is_metrics["sharpe"])
                if is_metrics["sharpe"] and not np.isnan(is_metrics["sharpe"])
                else np.nan
            ),
        })

        cursor += oos_months
        window_num += 1

    wfo_df = pd.DataFrame(rows)

    # ── Summary stats ──────────────────────────────────────────────
    if not wfo_df.empty:
        mean_oos = wfo_df["oos_sharpe"].mean()
        mean_is  = wfo_df["is_sharpe"].mean()
        pct_positive = (wfo_df["oos_sharpe"] > 0).mean()
        print(
            f"\n[WFO] {len(wfo_df)} windows | "
            f"Mean OOS Sharpe: {mean_oos:.3f} | "
            f"Mean IS Sharpe: {mean_is:.3f} | "
            f"OOS Sharpe > 0: {pct_positive:.0%}"
        )
        if not np.isnan(mean_oos) and not np.isnan(mean_is) and mean_is != 0:
            decay = (mean_oos - mean_is) / abs(mean_is)
            print(f"[WFO] Mean Sharpe decay IS→OOS: {decay:+.1%}")

    return wfo_df


def write_outputs(result: dict[str, pd.DataFrame | dict]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = result["timeseries"]
    metrics = result["metrics"]
    regimes = result["regimes"]
    rolling_sharpe = result["rolling_sharpe"]
    rolling_drawdown = result["rolling_drawdown"]
    config = result["config"]

    metrics.to_csv(OUTPUT_DIR / "metrics.csv", index=False)
    regimes.to_csv(OUTPUT_DIR / "regimes.csv", index=False)
    ts[["strategy_nav", "strategy_return"]].to_csv(OUTPUT_DIR / "strategy_nav_monthly.csv")
    ts[["spy_nav", "spy_return"]].to_csv(OUTPUT_DIR / "spy_nav_monthly.csv")
    rolling_sharpe.to_csv(OUTPUT_DIR / "rolling_36m_sharpe.csv")
    rolling_drawdown.to_csv(OUTPUT_DIR / "rolling_12m_drawdown.csv")

    summary = {"config": config, "full_period": metrics.loc[metrics["regime"] == "full_period"].iloc[0].to_dict()}
    (OUTPUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    limitation = "Survivorship bias warning: survivorship_ok=True uses present-day universe membership."
    report = [
        "# Sleeve 1 Robustness Backtest",
        "",
        f"**{limitation}**",
        "",
        "## Configuration",
        f"- Start: {config['start']}",
        f"- End: {config['end']}",
        f"- Top N: {config['top_n']}",
        f"- Slippage (bps): {config['slippage_bps']}",
        f"- Universe size: {config['universe_size']}",
        "",
        "## Known limitations",
        "- Survivorship bias remains by design for current testing mode.",
        "- Monthly close-to-close approximation; fills modeled via turnover slippage.",
        "",
        "## Full-period metrics",
        metrics.loc[metrics["regime"] == "full_period"].to_csv(index=False),
    ]
    (OUTPUT_DIR / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sleeve 1 robustness backtest")
    p.add_argument("--start", default=DEFAULT_START)
    p.add_argument("--end", default=None)
    p.add_argument("--top_n", type=int, default=DEFAULT_TOP_N)
    p.add_argument("--slippage_bps", type=float, default=DEFAULT_SLIPPAGE_BPS)
    p.add_argument("--survivorship_ok", type=int, default=1)
    p.add_argument(
        "--wfo",
        action="store_true",
        default=False,
        help="Run walk-forward validation in addition to the standard backtest.",
    )
    p.add_argument(
        "--wfo_train_months",
        type=int,
        default=12,
        help="Training window length in months for walk-forward (default: 12).",
    )
    p.add_argument(
        "--wfo_oos_months",
        type=int,
        default=3,
        help="OOS window length in months for walk-forward (default: 3).",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    cfg = RobustnessConfig(
        start=args.start,
        end=args.end,
        top_n=args.top_n,
        slippage_bps=args.slippage_bps,
        survivorship_ok=bool(args.survivorship_ok),
    )
    result = run_backtest(cfg)
    write_outputs(result)

    if args.wfo:
        print("\n[WFO] Starting walk-forward validation...")
        wfo_df = run_walk_forward(
            cfg,
            train_months=args.wfo_train_months,
            oos_months=args.wfo_oos_months,
        )
        wfo_path = OUTPUT_DIR / "wfo_results.csv"
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        wfo_df.to_csv(wfo_path, index=False)
        print(f"[WFO] Results written to {wfo_path}")


if __name__ == "__main__":
    main()

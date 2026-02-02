# paper/paper_broker.py
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from typing import Dict, List, Tuple

import pandas as pd
import yfinance as yf


@dataclass
class PaperConfig:
    initial_equity: float
    benchmark_ticker: str
    slippage_bps: float
    allow_fractional: bool
    min_trade_dollars: float
    cash_buffer_bps: float = 0.0  # keep small cash buffer to avoid negative cash


def load_config(path: str) -> PaperConfig:
    with open(path, "r") as f:
        cfg = json.load(f)

    execution = cfg.get("execution", {})
    constraints = cfg.get("constraints", {})

    return PaperConfig(
        initial_equity=float(cfg["initial_equity"]),
        benchmark_ticker=str(cfg["benchmark_ticker"]),
        slippage_bps=float(execution.get("slippage_bps", 0.0)),
        allow_fractional=bool(constraints.get("allow_fractional_shares", True)),
        min_trade_dollars=float(constraints.get("min_trade_dollars", 5.0)),
        cash_buffer_bps=float(constraints.get("cash_buffer_bps", 0.0)),
    )


def _ensure_parent_dir(filepath: str) -> None:
    parent = os.path.dirname(filepath)
    if parent:
        os.makedirs(parent, exist_ok=True)


def append_csv(df: pd.DataFrame, path: str) -> None:
    _ensure_parent_dir(path)
    header = not os.path.exists(path) or os.path.getsize(path) == 0
    df.to_csv(path, mode="a", header=header, index=False)


def read_latest_holdings_from_ledger(ledger_path: str) -> Tuple[pd.DataFrame, float, float, str]:
    """
    Returns:
      holdings_df: columns [ticker, sleeve, shares]
      cash: float
      total_equity: float
      last_date: str (YYYY-MM-DD)
    """
    if not os.path.exists(ledger_path) or os.path.getsize(ledger_path) == 0:
        return pd.DataFrame(columns=["ticker", "sleeve", "shares"]), 0.0, 0.0, ""

    led = pd.read_csv(ledger_path)
    if led.empty:
        return pd.DataFrame(columns=["ticker", "sleeve", "shares"]), 0.0, 0.0, ""

    last_date = str(led["date"].max())
    last = led[led["date"] == last_date].copy()

    cash = float(last["cash"].iloc[0])
    total_equity = float(last["total_equity"].iloc[0])

    holdings = last[["ticker", "sleeve", "shares"]].copy()
    holdings["shares"] = holdings["shares"].astype(float)

    return holdings, cash, total_equity, last_date


def load_targets(signals_path: str) -> pd.DataFrame:
    with open(signals_path, "r") as f:
        obj = json.load(f)

    df = pd.DataFrame(obj)
    if df.empty:
        raise ValueError(f"Signals file is empty: {signals_path}")

    if "sleeve" not in df.columns:
        df["sleeve"] = "core"

    df["ticker"] = df["ticker"].astype(str).str.upper().str.strip()
    df["target_weight"] = df["target_weight"].astype(float)

    wsum = float(df["target_weight"].sum())
    if wsum <= 0:
        raise ValueError("Signals target_weight sum <= 0")
    df["target_weight"] = df["target_weight"] / wsum

    return df[["ticker", "sleeve", "target_weight"]]


def fetch_open_prices_yfinance(tickers: List[str], run_date: str) -> pd.Series:
    """
    Fetch the 'Open' price for each ticker for run_date (YYYY-MM-DD).
    Uses per-run yfinance tz cache folder. Uses threads=False and retries per-ticker.
    """
    if not tickers:
        return pd.Series(dtype=float)

    start = pd.Timestamp(run_date)
    end = start + pd.Timedelta(days=1)  # yfinance end is exclusive

    # Avoid tz-cache sqlite lock issues
    yf.set_tz_cache_location(tempfile.mkdtemp(prefix="yf_tz_cache_"))

    px = yf.download(
        tickers=tickers,
        start=start.strftime("%Y-%m-%d"),
        end=end.strftime("%Y-%m-%d"),
        interval="1d",
        group_by="column",
        auto_adjust=False,
        progress=False,
        threads=False,
    )

    if px is None or len(px) == 0:
        raise RuntimeError(f"No price data returned from yfinance for {run_date}")

    # Build a Series of opens
    if isinstance(px.columns, pd.MultiIndex):
        opens = {}
        for t in tickers:
            if ("Open", t) in px.columns and len(px[("Open", t)]) > 0:
                opens[t] = float(px[("Open", t)].iloc[0])
        s = pd.Series(opens, dtype=float)
    else:
        if "Open" not in px.columns:
            raise RuntimeError("Open column not found in yfinance response.")
        s = pd.Series({tickers[0]: float(px["Open"].iloc[0])}, dtype=float)

    # Retry missing tickers one by one
    missing_pre = [t for t in tickers if t not in s.index or pd.isna(s.loc[t])]
    if missing_pre:
        retries = {}
        for t in missing_pre:
            try:
                yf.set_tz_cache_location(tempfile.mkdtemp(prefix="yf_tz_cache_"))
                px1 = yf.download(
                    tickers=[t],
                    start=start.strftime("%Y-%m-%d"),
                    end=end.strftime("%Y-%m-%d"),
                    interval="1d",
                    group_by="column",
                    auto_adjust=False,
                    progress=False,
                    threads=False,
                )
                if px1 is not None and len(px1) > 0 and "Open" in px1.columns:
                    retries[t] = float(px1["Open"].iloc[0])
            except Exception:
                continue
        if retries:
            s = pd.concat([s, pd.Series(retries, dtype=float)])

    # Drop any still-missing tickers (degrade gracefully)
    missing = [t for t in tickers if t not in s.index or pd.isna(s.loc[t])]
    if missing:
        print(f"[PAPER][WARN] Missing open prices on {run_date}: {missing} — dropping from execution.")
        s = s.drop(labels=[t for t in missing if t in s.index], errors="ignore")

    return s


def apply_slippage(price: float, side: str, slippage_bps: float) -> Tuple[float, float]:
    bps = slippage_bps / 10000.0
    if side.upper() == "BUY":
        slipped = price * (1.0 + bps)
        cost = slipped - price
    else:
        slipped = price * (1.0 - bps)
        cost = price - slipped
    return slipped, cost


def build_rebalance_trades(
    holdings: pd.DataFrame,
    targets: pd.DataFrame,
    prices: pd.Series,
    total_equity: float,
    cfg: PaperConfig,
) -> pd.DataFrame:
    h = holdings.set_index("ticker")["shares"].to_dict()

    targets = targets.copy()

    # Leave cash buffer to avoid negative cash due to slippage
    cash_buffer = 1.0 - (cfg.cash_buffer_bps / 10000.0)
    cash_buffer = max(0.0, min(1.0, cash_buffer))
    targets["target_dollars"] = targets["target_weight"] * total_equity * cash_buffer

    trades: List[Dict[str, object]] = []

    # Buy/sell to reach targets
    for _, row in targets.iterrows():
        tkr = row["ticker"]
        if tkr not in prices.index:
            continue
        px = float(prices.loc[tkr])
        if px <= 0:
            continue

        target_shares = row["target_dollars"] / px
        if not cfg.allow_fractional:
            target_shares = int(target_shares)

        current_shares = float(h.get(tkr, 0.0))
        delta = target_shares - current_shares
        trade_notional = abs(delta) * px

        if trade_notional < cfg.min_trade_dollars or abs(delta) < 1e-12:
            continue

        side = "BUY" if delta > 0 else "SELL"
        slipped_px, slip_cost_per_share = apply_slippage(px, side, cfg.slippage_bps)

        trades.append(
            {
                "ticker": tkr,
                "side": side,
                "shares": float(abs(delta)),
                "price": float(slipped_px),
                "slippage_cost": float(slip_cost_per_share * abs(delta)),
                "notional": float(abs(delta) * slipped_px),
                "reason": "rebalance_to_target",
            }
        )

    # Liquidate positions not in targets
    target_set = set(targets["ticker"].tolist())
    for tkr, sh in h.items():
        if tkr not in target_set and abs(sh) > 1e-12:
            if tkr not in prices.index:
                continue
            px = float(prices.loc[tkr])
            slipped_px, slip_cost_per_share = apply_slippage(px, "SELL", cfg.slippage_bps)
            trades.append(
                {
                    "ticker": tkr,
                    "side": "SELL",
                    "shares": float(abs(sh)),
                    "price": float(slipped_px),
                    "slippage_cost": float(slip_cost_per_share * abs(sh)),
                    "notional": float(abs(sh) * slipped_px),
                    "reason": "removed_from_targets",
                }
            )

    return pd.DataFrame(trades)


def apply_trades_to_holdings(
    holdings: pd.DataFrame,
    targets: pd.DataFrame,
    trades: pd.DataFrame,
    starting_cash: float,
) -> Tuple[pd.DataFrame, float]:
    sleeve_map_from_targets = targets.set_index("ticker")["sleeve"].to_dict()

    shares_map: Dict[str, float] = {}
    sleeve_map: Dict[str, str] = {}

    if not holdings.empty:
        for _, r in holdings.iterrows():
            tkr = str(r["ticker"])
            shares_map[tkr] = float(r["shares"])
            sleeve_map[tkr] = str(r.get("sleeve", "core"))

    cash = float(starting_cash)

    if trades is not None and not trades.empty:
        for _, tr in trades.iterrows():
            tkr = str(tr["ticker"])
            side = str(tr["side"]).upper()
            sh = float(tr["shares"])
            px = float(tr["price"])
            notional = sh * px

            if side == "BUY":
                shares_map[tkr] = shares_map.get(tkr, 0.0) + sh
                cash -= notional
            else:
                shares_map[tkr] = shares_map.get(tkr, 0.0) - sh
                cash += notional

            if tkr in sleeve_map_from_targets:
                sleeve_map[tkr] = sleeve_map_from_targets[tkr]

    out = (
        pd.DataFrame(
            [
                {"ticker": k, "sleeve": sleeve_map.get(k, "core"), "shares": v}
                for k, v in shares_map.items()
                if abs(v) > 1e-12
            ]
        )
        .sort_values("ticker")
        .reset_index(drop=True)
    )

    return out, cash


def mark_to_market(holdings: pd.DataFrame, prices: pd.Series) -> pd.DataFrame:
    h = holdings.copy()
    h["price"] = h["ticker"].apply(lambda x: float(prices.loc[x]) if x in prices.index else float("nan"))
    h = h.dropna(subset=["price"]).copy()
    h["market_value"] = h["shares"] * h["price"]
    return h


def run_paper_day(
    run_date: str,
    signals_path: str,
    ledger_path: str,
    trades_path: str,
    config_path: str,
    force: bool = False,
) -> Dict[str, object]:
    cfg = load_config(config_path)

    holdings_prev, cash_prev, equity_prev, last_date = read_latest_holdings_from_ledger(ledger_path)

    # Guardrail: prevent accidental double execution
    if last_date == run_date and not force:
        raise RuntimeError(f"Ledger already contains run_date={run_date}. Refusing to run twice.")

    # Bootstrap day 1
    if last_date == "":
        cash_prev = cfg.initial_equity
        equity_prev = cfg.initial_equity

    targets = load_targets(signals_path)

    # Universe for pricing: targets + benchmark + existing holdings
    tickers = sorted(set(targets["ticker"].tolist() + [cfg.benchmark_ticker]))
    if not holdings_prev.empty:
        tickers = sorted(set(tickers + holdings_prev["ticker"].tolist()))

    prices_open = fetch_open_prices_yfinance(tickers, run_date=run_date)

    # If any tickers missing prices, drop them from targets and renormalize
    priced = set(prices_open.index.tolist())
    targets = targets[targets["ticker"].isin(priced)].copy()
    wsum = float(targets["target_weight"].sum())
    if wsum <= 0:
        raise RuntimeError("After dropping missing-priced tickers, no targets remain.")
    targets["target_weight"] = targets["target_weight"] / wsum

    trades = build_rebalance_trades(
        holdings=holdings_prev,
        targets=targets,
        prices=prices_open,
        total_equity=equity_prev,
        cfg=cfg,
    )

    if trades is None or trades.empty:
        trades_out = pd.DataFrame(columns=["date", "ticker", "side", "shares", "price", "slippage_cost", "notional", "reason"])
    else:
        trades_out = trades.copy()
        trades_out.insert(0, "date", run_date)

    append_csv(trades_out, trades_path)

    holdings_new, cash_new = apply_trades_to_holdings(
        holdings=holdings_prev,
        targets=targets,
        trades=trades,
        starting_cash=cash_prev,
    )

    m2m = mark_to_market(holdings_new, prices_open)
    total_mv = float(m2m["market_value"].sum()) if not m2m.empty else 0.0
    total_equity = float(cash_new + total_mv)

    ledger_day = m2m[["ticker", "sleeve", "shares", "price", "market_value"]].copy()
    ledger_day.insert(0, "date", run_date)
    ledger_day["cash"] = cash_new
    ledger_day["total_equity"] = total_equity
    append_csv(ledger_day, ledger_path)

    executed_trades = int(len(trades_out)) if trades_out is not None else 0
    turnover = float(trades_out["notional"].sum()) if executed_trades and "notional" in trades_out.columns else 0.0

    return {
        "date": run_date,
        "total_equity": total_equity,
        "cash": cash_new,
        "num_trades": executed_trades,
        "turnover_notional": turnover,
        "benchmark": cfg.benchmark_ticker,
    }

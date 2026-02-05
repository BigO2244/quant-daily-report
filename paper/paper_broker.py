# paper/paper_broker.py
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd
import yfinance as yf

from paper.trading_calendar import market_session_status
from paper.trading_calendar import prev_trading_day

logger = logging.getLogger(__name__)


@dataclass
class PaperConfig:
    initial_equity: float
    benchmark_ticker: str
    slippage_bps: float
    allow_fractional: bool
    min_trade_dollars: float
    cash_buffer_bps: float = 0.0
    trading_mode: str = "paper"
    portfolio_id: str = "main"
    strategy_version: str = "v1"
    market_cutoff_time_et: str = "15:45"
    reconciliation_abs_tolerance_dollars: float = 1.0
    reconciliation_bps_tolerance: float = 1.0
    max_turnover_pct: float = 0.30
    max_trades_per_day: int = 10
    max_position_change_pct: float = 0.15
    risk_action: str = "hard_stop"
    halt_on_data_error: bool = True
    require_benchmark_price: bool = True
    cash_target_weight_default: float = 0.0


def load_config(path: str) -> PaperConfig:
    with open(path, "r") as f:
        cfg = json.load(f)

    execution = cfg.get("execution", {})
    constraints = cfg.get("constraints", {})
    mode_cfg = cfg.get("mode", {})
    safety_cfg = cfg.get("safety", {})
    risk_cfg = cfg.get("risk", {})

    trading_mode = os.getenv("TRADING_MODE", str(mode_cfg.get("trading_mode", "paper"))).strip().lower()

    return PaperConfig(
        initial_equity=float(cfg["initial_equity"]),
        benchmark_ticker=str(cfg["benchmark_ticker"]),
        slippage_bps=float(execution.get("slippage_bps", 0.0)),
        allow_fractional=bool(constraints.get("allow_fractional_shares", True)),
        min_trade_dollars=float(constraints.get("min_trade_dollars", 5.0)),
        cash_buffer_bps=float(constraints.get("cash_buffer_bps", 0.0)),
        trading_mode=trading_mode,
        portfolio_id=str(mode_cfg.get("portfolio_id", "main")),
        strategy_version=str(mode_cfg.get("strategy_version", "v1")),
        market_cutoff_time_et=str(safety_cfg.get("market_cutoff_time_et", "15:45")),
        reconciliation_abs_tolerance_dollars=float(safety_cfg.get("reconciliation_abs_tolerance_dollars", 1.0)),
        reconciliation_bps_tolerance=float(safety_cfg.get("reconciliation_bps_tolerance", 1.0)),
        max_turnover_pct=float(risk_cfg.get("max_turnover_pct", 0.30)),
        max_trades_per_day=int(risk_cfg.get("max_trades_per_day", 10)),
        max_position_change_pct=float(risk_cfg.get("max_position_change_pct", 0.15)),
        risk_action=str(risk_cfg.get("action", "hard_stop")).strip().lower(),
        halt_on_data_error=bool(safety_cfg.get("halt_on_data_error", True)),
        require_benchmark_price=bool(safety_cfg.get("require_benchmark_price", True)),
        cash_target_weight_default=float(
            constraints.get("cash_target_weight", constraints.get("target_cash_weight", 0.0))
        ),
    )


def _ensure_parent_dir(filepath: str) -> None:
    parent = os.path.dirname(filepath)
    if parent:
        os.makedirs(parent, exist_ok=True)


def append_csv(df: pd.DataFrame, path: str) -> None:
    _ensure_parent_dir(path)
    header = not os.path.exists(path) or os.path.getsize(path) == 0
    df.to_csv(path, mode="a", header=header, index=False)


def _read_csv(path: str) -> pd.DataFrame:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return pd.DataFrame()
    return pd.read_csv(path)


def read_latest_holdings_from_ledger(
    ledger_path: str,
) -> Tuple[pd.DataFrame, float, float, str]:
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


def load_targets(
    signals_path: str,
    cash_target_weight_default: float = 0.0,
) -> Tuple[pd.DataFrame, float, str | None, dict]:
    with open(signals_path, "r") as f:
        obj = json.load(f)

    snapshot_date = None
    meta = {}
    payload_cash_target_weight = None
    if isinstance(obj, dict):
        snapshot_date = obj.get("snapshot_date")
        meta = obj.get("meta") or {}
        if obj.get("cash_target_weight") is not None:
            payload_cash_target_weight = float(obj.get("cash_target_weight"))
        rows = obj.get("signals")
    else:
        rows = obj

    df = pd.DataFrame(rows)
    if df.empty:
        raise ValueError(f"Signals file is empty: {signals_path}")

    if "sleeve" not in df.columns:
        df["sleeve"] = "core"

    df["ticker"] = df["ticker"].astype(str).str.upper().str.strip()
    df["target_weight"] = df["target_weight"].astype(float)

    cash_rows = df[df["ticker"] == "CASH"].copy()
    if not cash_rows.empty:
        target_cash_weight = float(cash_rows["target_weight"].iloc[-1])
    elif payload_cash_target_weight is not None:
        target_cash_weight = payload_cash_target_weight
    else:
        target_cash_weight = float(cash_target_weight_default)
    target_cash_weight = max(0.0, min(1.0, target_cash_weight))

    non_cash = df[df["ticker"] != "CASH"].copy()
    non_cash_sum = float(non_cash["target_weight"].sum())
    if non_cash_sum <= 0:
        raise ValueError("Signals non-cash target_weight sum <= 0")

    investable_weight_target = 1.0 - target_cash_weight
    if abs(non_cash_sum - investable_weight_target) <= 1e-6:
        pass
    elif abs(non_cash_sum - 1.0) <= 1e-6:
        non_cash["target_weight"] = non_cash["target_weight"] * investable_weight_target
    elif non_cash_sum > 0:
        non_cash["target_weight"] = (
            non_cash["target_weight"] / non_cash_sum * investable_weight_target
        )

    return (
        non_cash[["ticker", "sleeve", "target_weight"]],
        target_cash_weight,
        snapshot_date,
        meta,
    )


def fetch_open_prices_yfinance(tickers: List[str], run_date: str) -> pd.DataFrame:
    if not tickers:
        return pd.DataFrame(columns=["ticker", "open", "price_date"])

    start = pd.Timestamp(run_date)
    end = start + pd.Timedelta(days=1)

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

    rows: List[Dict[str, object]] = []
    if isinstance(px.columns, pd.MultiIndex):
        for t in tickers:
            col = ("Open", t)
            if col in px.columns and len(px[col]) > 0:
                rows.append({"ticker": t, "open": float(px[col].iloc[0]), "price_date": str(px.index[0].date())})
    else:
        if "Open" not in px.columns:
            raise RuntimeError("Open column not found in yfinance response.")
        rows.append({"ticker": tickers[0], "open": float(px["Open"].iloc[0]), "price_date": str(px.index[0].date())})

    df = pd.DataFrame(rows)
    missing_pre = [t for t in tickers if t not in set(df["ticker"].tolist())]
    if missing_pre:
        retries = []
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
                    retries.append({"ticker": t, "open": float(px1["Open"].iloc[0]), "price_date": str(px1.index[0].date())})
            except Exception:
                continue
        if retries:
            df = pd.concat([df, pd.DataFrame(retries)], ignore_index=True)

    if df.empty:
        raise RuntimeError(f"No usable open prices for run_date={run_date}")
    return df.drop_duplicates(subset=["ticker"], keep="last")


def fetch_prev_closes_yfinance(tickers: List[str], asof_date: str) -> pd.DataFrame:
    if not tickers:
        return pd.DataFrame(columns=["ticker", "prev_close", "price_date"])

    start = pd.Timestamp(asof_date)
    end = start + pd.Timedelta(days=1)

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
        return pd.DataFrame(columns=["ticker", "prev_close", "price_date"])

    rows: List[Dict[str, object]] = []
    if isinstance(px.columns, pd.MultiIndex):
        for t in tickers:
            col = ("Close", t)
            if col in px.columns and len(px[col]) > 0:
                rows.append(
                    {
                        "ticker": t,
                        "prev_close": float(px[col].iloc[0]),
                        "price_date": str(px.index[0].date()),
                    }
                )
    else:
        if "Close" in px.columns:
            rows.append(
                {
                    "ticker": tickers[0],
                    "prev_close": float(px["Close"].iloc[0]),
                    "price_date": str(px.index[0].date()),
                }
            )

    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["ticker", "prev_close", "price_date"])
    return df.drop_duplicates(subset=["ticker"], keep="last")


def validate_open_window(
    trade_date: str,
    signals_meta: dict,
    prices_open: pd.Series,
    prev_closes: pd.Series,
    weights: pd.DataFrame,
    signals_path: str,
) -> tuple[bool, list[str], dict]:
    cutoff_date = prev_trading_day(trade_date)
    reasons: list[str] = []
    meta = signals_meta or {}

    asof_date = meta.get("asof_date")
    meta_trade_date = meta.get("trade_date")
    file_trade_date = Path(signals_path).stem
    file_trade_date_is_iso = False
    try:
        pd.Timestamp(file_trade_date)
        file_trade_date_is_iso = True
    except Exception:
        file_trade_date_is_iso = False

    if not asof_date:
        reasons.append("missing_asof_date")
    elif str(asof_date) > cutoff_date:
        reasons.append(f"asof_after_cutoff asof={asof_date} cutoff={cutoff_date}")

    if file_trade_date_is_iso and file_trade_date != trade_date:
        reasons.append(
            f"signal_file_trade_date_mismatch file_trade_date={file_trade_date} trade_date={trade_date}"
        )
    if str(meta_trade_date or "") != trade_date:
        reasons.append(
            f"meta_trade_date_mismatch meta_trade_date={meta_trade_date} trade_date={trade_date}"
        )

    required_tickers = sorted(
        t for t in weights[weights["target_weight"].abs() > 0]["ticker"].astype(str).tolist()
    )
    missing_opens = sorted(
        t
        for t in required_tickers
        if t not in prices_open.index or not pd.notna(prices_open.get(t))
    )
    missing_prev_closes = sorted(
        t
        for t in required_tickers
        if t not in prev_closes.index or not pd.notna(prev_closes.get(t))
    )
    if missing_opens:
        reasons.append(f"missing_open_prices:{','.join(missing_opens)}")
    if missing_prev_closes:
        reasons.append(f"missing_prev_closes:{','.join(missing_prev_closes)}")

    ok = len(reasons) == 0
    details = {
        "trade_date": trade_date,
        "signals_path": signals_path,
        "asof_date": asof_date,
        "cutoff_date": cutoff_date,
        "missing_opens": missing_opens,
        "missing_prev_closes": missing_prev_closes,
        "result": "PASS" if ok else "FAIL",
        "reasons": reasons,
    }
    return ok, reasons, details


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
    starting_cash: float,
    target_cash_weight: float,
    cfg: PaperConfig,
) -> Tuple[pd.DataFrame, Dict[str, object]]:
    h = holdings.set_index("ticker")["shares"].to_dict()

    targets = targets.copy().sort_values(["target_weight", "ticker"], ascending=[False, True])

    cash_buffer = 1.0 - (cfg.cash_buffer_bps / 10000.0)
    cash_buffer = max(0.0, min(1.0, cash_buffer))
    target_investable_dollars = total_equity * (1.0 - target_cash_weight) * cash_buffer
    targets["target_dollars"] = targets["target_weight"] * total_equity * cash_buffer

    trades: List[Dict[str, object]] = []
    buy_candidates: List[Dict[str, object]] = []

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

        if side == "SELL":
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
        else:
            buy_candidates.append(
                {
                    "ticker": tkr,
                    "desired_shares": float(abs(delta)),
                    "price": float(slipped_px),
                    "slippage_cost_per_share": float(slip_cost_per_share),
                    "target_weight": float(row["target_weight"]),
                }
            )

    target_set = set(targets["ticker"].tolist())
    for tkr, sh in h.items():
        if tkr not in target_set and abs(sh) > 1e-12:
            if tkr not in prices.index:
                continue
            px = float(prices.loc[tkr])
            slipped_px, slip_cost_per_share = apply_slippage(
                px, "SELL", cfg.slippage_bps
            )
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

    cash_after_sells = float(starting_cash)
    for tr in trades:
        if tr["side"] == "SELL":
            cash_after_sells += float(tr["notional"])

    buy_candidates = sorted(
        buy_candidates,
        key=lambda x: (-float(x["target_weight"]), str(x["ticker"])),
    )

    scaled_tickers: List[str] = []
    invested_buys = 0.0
    for buy in buy_candidates:
        tkr = str(buy["ticker"])
        desired = float(buy["desired_shares"])
        px = float(buy["price"])

        if px <= 0:
            continue

        cash_remaining = cash_after_sells - invested_buys
        if cfg.allow_fractional:
            max_affordable = max(0.0, cash_remaining / px)
        else:
            max_affordable = max(0.0, int(cash_remaining // px))
        exec_shares = min(desired, max_affordable)

        if exec_shares <= 1e-12:
            if desired > 1e-12:
                scaled_tickers.append(tkr)
            continue

        exec_notional = exec_shares * px
        if exec_notional < cfg.min_trade_dollars:
            continue
        if exec_shares + 1e-12 < desired:
            scaled_tickers.append(tkr)

        invested_buys += exec_notional
        trades.append(
            {
                "ticker": tkr,
                "side": "BUY",
                "shares": float(exec_shares),
                "price": float(px),
                "slippage_cost": float(buy["slippage_cost_per_share"] * exec_shares),
                "notional": float(exec_notional),
                "reason": "rebalance_to_target" if exec_shares >= desired else "cash_limited",
            }
        )

    overspend_prevented = len(scaled_tickers) > 0
    return pd.DataFrame(trades), {
        "target_cash_weight": float(target_cash_weight),
        "target_investable_dollars": float(target_investable_dollars),
        "scaled_tickers": sorted(set(scaled_tickers)),
        "overspend_prevented": overspend_prevented,
    }


def apply_risk_guards(
    trades: pd.DataFrame,
    equity: float,
    cfg: PaperConfig,
) -> Tuple[pd.DataFrame, List[str], bool]:
    if trades is None or trades.empty:
        return trades, [], False

    blocked: List[str] = []
    hard_stop = False
    out = trades.copy()

    max_turnover_notional = equity * cfg.max_turnover_pct
    current_turnover = float(out["notional"].sum())
    if current_turnover > max_turnover_notional + 1e-9:
        msg = f"max_turnover_pct exceeded ({current_turnover:.2f} > {max_turnover_notional:.2f})"
        if cfg.risk_action == "hard_stop":
            return pd.DataFrame(columns=trades.columns), [msg], True
        out = out.sort_values("notional", ascending=False).copy()
        running = 0.0
        kept_rows = []
        for _, row in out.iterrows():
            n = float(row["notional"])
            if running + n <= max_turnover_notional + 1e-9:
                kept_rows.append(row.to_dict())
                running += n
            else:
                blocked.append(f"{msg}; clipped ticker={row['ticker']}")
        out = pd.DataFrame(kept_rows, columns=trades.columns)

    if len(out) > cfg.max_trades_per_day:
        msg = f"max_trades_per_day exceeded ({len(out)} > {cfg.max_trades_per_day})"
        if cfg.risk_action == "hard_stop":
            return pd.DataFrame(columns=trades.columns), [msg], True
        out = out.sort_values("notional", ascending=False).head(cfg.max_trades_per_day).copy()
        blocked.append(msg)

    if out.empty:
        return out, blocked, hard_stop

    per_trade_limit = equity * cfg.max_position_change_pct
    if per_trade_limit >= 0:
        clipped_rows = []
        clipped_any = False
        for _, row in out.iterrows():
            r = row.to_dict()
            n = float(r["notional"])
            if n <= per_trade_limit + 1e-9:
                clipped_rows.append(r)
                continue
            msg = f"max_position_change_pct exceeded ticker={r['ticker']} ({n:.2f} > {per_trade_limit:.2f})"
            if cfg.risk_action == "hard_stop":
                return pd.DataFrame(columns=trades.columns), [msg], True
            ratio = per_trade_limit / n if n > 0 else 0.0
            r["shares"] = float(r["shares"]) * ratio
            r["notional"] = float(r["notional"]) * ratio
            r["slippage_cost"] = float(r["slippage_cost"]) * ratio
            r["reason"] = f"{r['reason']}_risk_clipped"
            clipped_rows.append(r)
            blocked.append(msg)
            clipped_any = True
        if clipped_any:
            out = pd.DataFrame(clipped_rows, columns=trades.columns)

    return out, blocked, hard_stop


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

    out = pd.DataFrame(
        [
            {"ticker": k, "sleeve": sleeve_map.get(k, "core"), "shares": v}
            for k, v in shares_map.items()
            if abs(v) > 1e-12
        ]
    )
    if out.empty:
        out = pd.DataFrame(columns=["ticker", "sleeve", "shares"])
    else:
        out = out.sort_values("ticker").reset_index(drop=True)

    return out, cash


def mark_to_market(holdings: pd.DataFrame, prices: pd.Series) -> pd.DataFrame:
    h = holdings.copy()
    h["price"] = h["ticker"].apply(
        lambda x: float(prices.loc[x]) if x in prices.index else float("nan")
    )
    h = h.dropna(subset=["price"]).copy()
    h["market_value"] = h["shares"] * h["price"]
    return h


def _run_id(run_date: str, cfg: PaperConfig) -> str:
    return f"{run_date}:{cfg.portfolio_id}:{cfg.strategy_version}"


def _build_shadow_orders(trades: pd.DataFrame, run_id: str) -> List[Dict[str, object]]:
    orders: List[Dict[str, object]] = []
    if trades is None or trades.empty:
        return orders
    for _, tr in trades.iterrows():
        ticker = str(tr["ticker"])
        side = str(tr["side"]).upper()
        order_id = f"{run_id}:{ticker}:{side}"
        orders.append(
            {
                "order_id": order_id,
                "ticker": ticker,
                "side": side,
                "quantity": float(tr["shares"]),
                "order_type": "MKT",
                "time_in_force": "DAY",
                "notional": float(tr["notional"]),
                "reason": str(tr.get("reason", "rebalance")),
            }
        )
    return orders


def _filter_idempotent_orders(orders: List[Dict[str, object]], sent_ledger_path: str) -> Tuple[List[Dict[str, object]], List[str]]:
    sent = _read_csv(sent_ledger_path)
    existing = set(sent["order_id"].astype(str).tolist()) if not sent.empty else set()
    out: List[Dict[str, object]] = []
    skipped: List[str] = []
    for o in orders:
        if o["order_id"] in existing:
            logger.info("[ORDER][IDEMPOTENT] skipped order_id=%s", o["order_id"])
            skipped.append(o["order_id"])
            continue
        out.append(o)
    return out, skipped


def _persist_sent_orders(orders: List[Dict[str, object]], sent_ledger_path: str, run_date: str, run_id: str) -> None:
    if not orders:
        return
    rows = [{"date": run_date, "run_id": run_id, "order_id": o["order_id"], "ticker": o["ticker"], "side": o["side"]} for o in orders]
    append_csv(pd.DataFrame(rows), sent_ledger_path)


def reset_orders_sent_ledger_for_date(sent_ledger_path: str, trade_date: str) -> int:
    path = Path(sent_ledger_path)
    if not path.exists() or path.stat().st_size == 0:
        logger.info("[ORDER][LEDGER_RESET] path=%s date=%s removed=%d", sent_ledger_path, trade_date, 0)
        return 0

    sent = pd.read_csv(path)
    if sent.empty:
        logger.info("[ORDER][LEDGER_RESET] path=%s date=%s removed=%d", sent_ledger_path, trade_date, 0)
        return 0

    if "date" not in sent.columns:
        logger.info("[ORDER][LEDGER_RESET] path=%s date=%s removed=%d", sent_ledger_path, trade_date, 0)
        return 0

    mask = sent["date"].astype(str) == str(trade_date)
    removed = int(mask.sum())
    kept = sent.loc[~mask].copy()
    _ensure_parent_dir(sent_ledger_path)
    kept.to_csv(path, index=False)
    logger.info("[ORDER][LEDGER_RESET] path=%s date=%s removed=%d", sent_ledger_path, trade_date, removed)
    return removed

def _write_shadow_orders(run_date: str, orders: List[Dict[str, object]]) -> str:
    out_path = Path("outputs") / "shadow_orders" / f"{run_date}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(orders, f, indent=2)
        f.write("\n")
    return str(out_path)


def _broker_reconciliation(
    model_cash: float,
    model_positions: pd.DataFrame,
    model_equity: float,
    broker_cash: float,
    broker_positions: pd.DataFrame,
    broker_equity: float,
    cfg: PaperConfig,
) -> Dict[str, object]:
    model_map = {str(r["ticker"]): float(r["shares"]) for _, r in model_positions.iterrows()} if not model_positions.empty else {}
    broker_map = {str(r["ticker"]): float(r["shares"]) for _, r in broker_positions.iterrows()} if not broker_positions.empty else {}
    tickers = sorted(set(model_map.keys()) | set(broker_map.keys()))
    deltas = [{"ticker": t, "model_shares": model_map.get(t, 0.0), "broker_shares": broker_map.get(t, 0.0), "delta_shares": model_map.get(t, 0.0)-broker_map.get(t, 0.0)} for t in tickers]

    equity_tol = max(cfg.reconciliation_abs_tolerance_dollars, model_equity * (cfg.reconciliation_bps_tolerance / 10000.0))
    cash_delta = model_cash - broker_cash
    equity_delta = model_equity - broker_equity
    ok = abs(equity_delta) <= equity_tol
    logger.info("[BROKER][RECON] model_equity=%.2f broker_equity=%.2f delta=%.2f", model_equity, broker_equity, equity_delta)

    return {
        "status": "PASS" if ok else "FAIL",
        "cash_delta": cash_delta,
        "equity_delta": equity_delta,
        "equity_tolerance": equity_tol,
        "position_deltas": deltas,
    }


def _current_et(now_utc: dt.datetime | None = None) -> dt.datetime:
    if now_utc is None:
        now_utc = dt.datetime.now(dt.timezone.utc)
    return now_utc.astimezone(dt.timezone(dt.timedelta(hours=-5)))


def run_paper_day(
    run_date: str,
    signals_path: str,
    ledger_path: str,
    trades_path: str,
    config_path: str,
    force: bool = False,
    now_et: dt.datetime | None = None,
    constraints: Dict[str, float] | None = None,
) -> Dict[str, object]:
    cfg = load_config(config_path)

    mode = cfg.trading_mode
    if mode == "live":
        raise RuntimeError("TRADING_MODE=live is not implemented. Refusing to proceed.")
    if mode not in {"paper", "shadow"}:
        raise RuntimeError(f"Unsupported TRADING_MODE={mode}")

    holdings_prev, cash_prev, equity_prev, last_date = read_latest_holdings_from_ledger(ledger_path)

    if last_date == run_date and not force and mode == "paper":
        raise RuntimeError(
            f"Ledger already contains run_date={run_date}. Refusing to run twice."
        )

    if last_date == "":
        cash_prev = cfg.initial_equity
        equity_prev = cfg.initial_equity

    if now_et is None:
        now_et = _current_et()

    mkt = market_session_status(run_date=run_date, now_et=now_et, cutoff_time_et=cfg.market_cutoff_time_et)

    runtime_constraints = constraints or {}
    cash_target_weight_default = float(
        runtime_constraints.get("cash_target_weight", cfg.cash_target_weight_default)
    )

    targets, target_cash_weight, snapshot_date, signals_meta = load_targets(
        signals_path,
        cash_target_weight_default=cash_target_weight_default,
    )
    if snapshot_date and snapshot_date != run_date:
        raise RuntimeError(f"[HALT] signal_date_mismatch snapshot_date={snapshot_date} execution_date={run_date}")

    tickers = sorted(set(targets["ticker"].tolist() + [cfg.benchmark_ticker]))
    if not holdings_prev.empty:
        tickers = sorted(set(tickers + holdings_prev["ticker"].tolist()))

    prices_df = fetch_open_prices_yfinance(tickers, run_date=run_date)
    stale_rows = prices_df[prices_df["price_date"].astype(str) < run_date]
    if not stale_rows.empty:
        last_px_date = str(stale_rows["price_date"].min())
        raise RuntimeError(f"[HALT] stale_prices detected (last_price_date={last_px_date})")

    prices_open = prices_df.set_index("ticker")["open"].astype(float)
    if cfg.require_benchmark_price and cfg.benchmark_ticker not in prices_open.index:
        raise RuntimeError(f"[HALT] benchmark_missing ticker={cfg.benchmark_ticker}")

    prev_close_df = fetch_prev_closes_yfinance(
        sorted(targets["ticker"].astype(str).unique().tolist()),
        asof_date=prev_trading_day(run_date),
    )
    prev_closes = (
        prev_close_df.set_index("ticker")["prev_close"].astype(float)
        if not prev_close_df.empty
        else pd.Series(dtype=float)
    )

    validation_ok, validation_reasons, validation_details = validate_open_window(
        trade_date=run_date,
        signals_meta=signals_meta,
        prices_open=prices_open,
        prev_closes=prev_closes,
        weights=targets,
        signals_path=signals_path,
    )
    if validation_ok:
        logger.info(
            "[PAPER][VALIDATION] PASS trade_date=%s signals=%s asof=%s cutoff=%s missing_opens=%d missing_prev_closes=%d",
            run_date,
            signals_path,
            validation_details.get("asof_date"),
            validation_details.get("cutoff_date"),
            len(validation_details.get("missing_opens", [])),
            len(validation_details.get("missing_prev_closes", [])),
        )
    else:
        logger.error(
            "[PAPER][VALIDATION] FAIL trade_date=%s signals=%s asof=%s cutoff=%s reasons=\"%s\"",
            run_date,
            signals_path,
            validation_details.get("asof_date"),
            validation_details.get("cutoff_date"),
            "; ".join(validation_reasons),
        )
        blocked_reasons = [f"validation:{reason}" for reason in validation_reasons]
        trades = pd.DataFrame(columns=["ticker", "side", "shares", "price", "slippage_cost", "notional", "reason"])
        trade_meta = {
            "target_cash_weight": float(target_cash_weight),
            "target_investable_dollars": float(equity_prev * max(0.0, 1.0 - target_cash_weight)),
            "scaled_tickers": [],
            "overspend_prevented": False,
        }
        prices_open = prices_open.iloc[0:0]

    if not validation_ok:
        targets = targets.iloc[0:0].copy()

    investable_weight = max(0.0, 1.0 - target_cash_weight)
    if validation_ok:
        priced = set(prices_open.index.tolist())
        targets = targets[targets["ticker"].isin(priced)].copy()
        wsum = float(targets["target_weight"].sum())
        if wsum <= 0:
            raise RuntimeError("After dropping missing-priced tickers, no targets remain.")
        targets["target_weight"] = targets["target_weight"] / wsum * investable_weight

    blocked_reasons: List[str] = [] if validation_ok else [f"validation:{reason}" for reason in validation_reasons]
    if validation_ok and mode == "shadow" and not mkt.is_open_now:
        logger.info("NO TRADES — MARKET CLOSED (%s)", mkt.reason)
        trades = pd.DataFrame(columns=["ticker", "side", "shares", "price", "slippage_cost", "notional", "reason"])
        trade_meta = {
            "target_cash_weight": float(target_cash_weight),
            "target_investable_dollars": float(equity_prev * investable_weight),
            "scaled_tickers": [],
            "overspend_prevented": False,
        }
        blocked_reasons.append(f"market_guard:{mkt.reason}")
    elif validation_ok:
        trades, trade_meta = build_rebalance_trades(
            holdings=holdings_prev,
            targets=targets,
            prices=prices_open,
            total_equity=equity_prev,
            starting_cash=cash_prev,
            target_cash_weight=target_cash_weight,
            cfg=cfg,
        )

    logger.info(
        "[SHADOW] cash_target=%.2f%% investable=$%.2f equity=$%.2f",
        100.0 * float(target_cash_weight),
        float(trade_meta.get("target_investable_dollars", equity_prev * investable_weight)),
        float(equity_prev),
    )

    if validation_ok:
        trades, risk_blocked, hard_stop = apply_risk_guards(trades, equity_prev, cfg)
        blocked_reasons.extend(risk_blocked)
        if hard_stop:
            logger.error("[HALT] risk guard hard stop: %s", "; ".join(risk_blocked))

    trades_out = trades.copy() if trades is not None else pd.DataFrame()
    if trades_out.empty:
        trades_out = pd.DataFrame(columns=["date", "ticker", "side", "shares", "price", "slippage_cost", "notional", "reason"])
    else:
        trades_out.insert(0, "date", run_date)

    append_csv(trades_out, trades_path)

    if validation_ok:
        holdings_new, cash_new = apply_trades_to_holdings(
            holdings=holdings_prev,
            targets=targets,
            trades=trades,
            starting_cash=cash_prev,
        )
        m2m = mark_to_market(holdings_new, prices_open)
        total_mv = float(m2m["market_value"].sum()) if not m2m.empty else 0.0
        total_equity = float(cash_new + total_mv)
        achieved_cash_weight = (cash_new / total_equity) if total_equity > 0 else 0.0
    else:
        holdings_new = holdings_prev.copy()
        cash_new = float(cash_prev)
        total_equity = float(equity_prev)
        achieved_cash_weight = (cash_new / total_equity) if total_equity > 0 else 0.0
        m2m = pd.DataFrame(columns=["ticker", "sleeve", "shares", "price", "market_value"])
        total_mv = 0.0
    invested_dollars = total_mv
    investable_dollars = float(trade_meta.get("target_investable_dollars", equity_prev * investable_weight))
    target_cash_dollars = float(equity_prev * target_cash_weight)

    achieved_weights = {}
    if total_equity > 0 and not m2m.empty:
        achieved_weights = {
            str(r["ticker"]): float(r["market_value"]) / total_equity
            for _, r in m2m.iterrows()
        }
    target_weights = {
        str(r["ticker"]): float(r["target_weight"]) for _, r in targets.iterrows()
    }
    all_tickers = sorted(set(target_weights.keys()) | set(achieved_weights.keys()))
    position_recon = []
    for tkr in all_tickers:
        tw = float(target_weights.get(tkr, 0.0))
        aw = float(achieved_weights.get(tkr, 0.0))
        position_recon.append(
            {
                "ticker": tkr,
                "target_weight": tw,
                "achieved_weight": aw,
                "delta_weight": aw - tw,
                "cash_limited": tkr in set(trade_meta.get("scaled_tickers", [])),
            }
        )

    if validation_ok:
        ledger_day = m2m[["ticker", "sleeve", "shares", "price", "market_value"]].copy()
        ledger_day.insert(0, "date", run_date)
        ledger_day["cash"] = cash_new
        ledger_day["total_equity"] = total_equity
        append_csv(ledger_day, ledger_path)

    run_id = _run_id(run_date, cfg)
    shadow_orders_path = None
    idempotent_skips: List[str] = []
    orders: List[Dict[str, object]] = []
    sent_ledger_path = "outputs/shadow_orders/orders_sent.csv"

    if mode == "shadow":
        orders = _build_shadow_orders(trades, run_id)
        orders, idempotent_skips = _filter_idempotent_orders(orders, sent_ledger_path)
        _persist_sent_orders(orders, sent_ledger_path, run_date, run_id)
        shadow_orders_path = _write_shadow_orders(run_date, orders)

    broker_state = {
        "cash": cash_new,
        "positions": holdings_new,
        "equity": total_equity,
    }
    if validation_ok:
        recon = _broker_reconciliation(
            model_cash=cash_new,
            model_positions=holdings_new,
            model_equity=total_equity,
            broker_cash=float(broker_state["cash"]),
            broker_positions=broker_state["positions"],
            broker_equity=float(broker_state["equity"]),
            cfg=cfg,
        )
        if recon["status"] != "PASS":
            raise RuntimeError(
                f"Broker reconciliation failed equity_delta={recon['equity_delta']:.2f} tol={recon['equity_tolerance']:.2f}"
            )
    else:
        recon = {
            "status": "SKIP",
            "cash_delta": 0.0,
            "equity_delta": 0.0,
            "equity_tolerance": 0.0,
            "position_deltas": [],
        }

    executed_trades = int(len(trades_out)) if trades_out is not None else 0
    turnover = (
        float(trades_out["notional"].sum())
        if executed_trades and "notional" in trades_out.columns
        else 0.0
    )

    logger.info(
        "[SHADOW] mode=%s market=%s orders=%d blocked=%d recon=%s",
        mode,
        "OPEN" if mkt.is_open_now else "CLOSED",
        len(orders) if mode == "shadow" else executed_trades,
        len(blocked_reasons) + len(idempotent_skips),
        recon["status"],
    )

    return {
        "date": run_date,
        "trading_mode": mode,
        "market_status": "OPEN" if mkt.is_open_now else "CLOSED",
        "market_reason": mkt.reason,
        "total_equity": total_equity,
        "sizing_equity": float(equity_prev),
        "cash": cash_new,
        "num_trades": executed_trades,
        "turnover_notional": turnover,
        "benchmark": cfg.benchmark_ticker,
        "target_cash_weight": float(target_cash_weight),
        "achieved_cash_weight": float(achieved_cash_weight),
        "investable_dollars": float(investable_dollars),
        "invested_dollars": float(invested_dollars),
        "target_cash_dollars": float(target_cash_dollars),
        "cash_dollars": float(cash_new),
        "overspend_prevented": bool(trade_meta.get("overspend_prevented")),
        "scaled_tickers": list(trade_meta.get("scaled_tickers", [])),
        "position_reconciliation": position_recon,
        "blocked_reasons": blocked_reasons,
        "open_window_validation": validation_details,
        "idempotent_skips": idempotent_skips,
        "shadow_orders": orders,
        "shadow_orders_path": shadow_orders_path,
        "run_id": run_id,
        "broker_reconciliation": recon,
    }

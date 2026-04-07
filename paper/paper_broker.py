# paper/paper_broker.py
from __future__ import annotations

import datetime as dt
import json
import logging
import math
import os
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from brokers.alpaca_snapshot import summarize_pretrade_broker_policy
from brokers.alpaca_broker import (
    AlpacaBroker,
    AlpacaSubmissionRejectError,
    CASH_REBALANCE_INCOMPLETE,
    EXECUTION_OUTCOME_POST_SUBMIT_ARTIFACT_FAILURE,
    EXECUTION_OUTCOME_PARTIAL_BROKER_ABORT,
    alpaca_client_order_id,
    broker_reject_policy_outcome,
    classify_alpaca_broker_reject,
    json_safe_primitive,
)
from reconciliation import write_post_execution_drift_report
from paper.run_manager import safe_write_text
from paper.trading_calendar import market_session_status
from paper.trading_calendar import prev_trading_day
from paper.reporting_consistency import compute_exposure
from core.trading_mode import canonical_trading_mode, legacy_shadow_mode_requested
from core.universe_v4 import is_allowed_etf_symbol

logger = logging.getLogger(__name__)

SENT_LEDGER_COLUMNS = [
    "date",
    "run_id",
    "order_id",
    "ticker",
    "side",
    "client_order_id",
    "alpaca_order_id",
    "status",
]

CAPITAL_RESERVE_MIN_CASH = 500.0
CAPITAL_RESERVE_EQUITY_PCT = 0.01
CAPITAL_SELL_PROCEEDS_HAIRCUT = 0.95
ALPACA_SELL_PHASE_TIMEOUT_SECONDS = 90.0
ALPACA_SELL_PHASE_POLL_INTERVAL_SECONDS = 3.0


def _load_yfinance():
    """Lazy import so non-price-fetching paths and CLI help avoid yfinance startup."""
    import yfinance as yf

    return yf


def _is_truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _format_reason_counts(counts: Counter[str]) -> str:
    if not counts:
        return "none"
    return ",".join(f"{k}:{int(v)}" for k, v in sorted(counts.items()))


def _default_reserve_cash_policy() -> Dict[str, float | None]:
    return {
        "min_cash_dollars": float(CAPITAL_RESERVE_MIN_CASH),
        "equity_reserve_pct": float(CAPITAL_RESERVE_EQUITY_PCT),
        "sell_proceeds_haircut": float(CAPITAL_SELL_PROCEEDS_HAIRCUT),
        "reserve_cash": None,
        "available_for_buys": None,
    }


def _default_capital_budget_meta() -> Dict[str, object]:
    return {
        "broker_cash_at_planning": None,
        "broker_equity_at_planning": None,
        "broker_buying_power_at_planning": None,
        "reserve_cash_policy": _default_reserve_cash_policy(),
        "expected_sell_proceeds": 0.0,
        "expected_sell_proceeds_conservative": 0.0,
        "requested_buy_notional": 0.0,
        "allowed_buy_notional": 0.0,
        "capital_constraint_triggered": False,
        "clipped_or_deferred_buys_count": 0,
    }


def _run_output_root() -> Path:
    base = str(os.getenv("RUN_OUTPUT_ROOT", "")).strip()
    return Path(base) if base else Path("outputs")


def _allow_overwrite() -> bool:
    return _is_truthy(os.getenv("ALLOW_OVERWRITE"))


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
    max_position_pct: float = 0.10
    risk_action: str = "hard_stop"
    halt_on_data_error: bool = True
    require_benchmark_price: bool = True
    cash_target_weight_default: float = 0.0
    sent_ledger_path: str = "outputs/orders_sent/orders_sent.csv"


def load_config(path: str) -> PaperConfig:
    with open(path, "r") as f:
        cfg = json.load(f)

    execution = cfg.get("execution", {})
    constraints = cfg.get("constraints", {})
    mode_cfg = cfg.get("mode", {})
    safety_cfg = cfg.get("safety", {})
    risk_cfg = cfg.get("risk", {})

    trading_mode = canonical_trading_mode(
        os.getenv("TRADING_MODE", str(mode_cfg.get("trading_mode", "paper"))).strip().lower(),
        field_name="TRADING_MODE",
    )

    initial_equity = float(
        os.getenv("PAPER_START_CASH", cfg.get("initial_equity", 10000.0))
    )

    return PaperConfig(
        initial_equity=initial_equity,
        benchmark_ticker=str(cfg["benchmark_ticker"]),
        slippage_bps=float(execution.get("slippage_bps", 0.0)),
        allow_fractional=bool(constraints.get("allow_fractional_shares", True)),
        min_trade_dollars=float(constraints.get("min_trade_dollars", 100.0)),
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
        max_position_pct=float(risk_cfg.get("max_position_pct", 0.10)),
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
) -> Tuple[pd.DataFrame, float, str | None, str | None]:
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

    banned = [t for t in df["ticker"].tolist() if t != "CASH" and not is_allowed_etf_symbol(t)]
    if banned:
        raise ValueError(f"Disallowed leveraged/inverse ETF symbols in signals: {sorted(set(banned))}")

    cash_rows = df[df["ticker"] == "CASH"].copy()
    if not cash_rows.empty:
        target_cash_weight = float(cash_rows["target_weight"].iloc[-1])
    elif payload_cash_target_weight is not None:
        target_cash_weight = payload_cash_target_weight
    else:
        target_cash_weight = float(cash_target_weight_default)
    target_cash_weight = max(0.0, min(1.0, target_cash_weight))

    non_cash = df[df["ticker"] != "CASH"].copy()
    non_cash = non_cash[non_cash["target_weight"].abs() > 1e-12].copy()
    non_cash_sum = float(non_cash["target_weight"].sum())
    investable_weight_target = 1.0 - target_cash_weight
    if non_cash_sum <= 0:
        if investable_weight_target <= 1e-9:
            return (
                non_cash[["ticker", "sleeve", "target_weight"]],
                target_cash_weight,
                snapshot_date,
                meta.get("asof_date"),
            )
        raise ValueError("Signals non-cash target_weight sum <= 0")

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
        meta.get("asof_date"),
    )


def fetch_open_prices_yfinance(tickers: List[str], run_date: str) -> pd.DataFrame:
    if not tickers:
        return pd.DataFrame(columns=["ticker", "open", "price_date"])

    start = pd.Timestamp(run_date)
    end = start + pd.Timedelta(days=1)

    yf = _load_yfinance()
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

    yf = _load_yfinance()
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
    planning_mode: bool = False,
) -> tuple[bool, list[str], dict]:
    cutoff_date = prev_trading_day(trade_date)
    global_reasons: list[str] = []
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
        global_reasons.append("missing_asof_date")
    elif str(asof_date) > cutoff_date:
        global_reasons.append(f"asof_after_cutoff asof={asof_date} cutoff={cutoff_date}")

    if file_trade_date_is_iso and file_trade_date != trade_date:
        global_reasons.append(
            f"signal_file_trade_date_mismatch file_trade_date={file_trade_date} trade_date={trade_date}"
        )
    if str(meta_trade_date or "") != trade_date:
        global_reasons.append(
            f"meta_trade_date_mismatch meta_trade_date={meta_trade_date} trade_date={trade_date}"
        )

    required_tickers = sorted(
        t for t in weights[weights["target_weight"].abs() > 0]["ticker"].astype(str).tolist()
    )
    blocked_tickers: Dict[str, List[str]] = {}
    ticker_validation: Dict[str, Dict[str, object]] = {}
    missing_opens: List[str] = []
    missing_prev_closes: List[str] = []
    for ticker in required_tickers:
        ticker_reasons: List[str] = []
        if (not planning_mode) and (ticker not in prices_open.index or not pd.notna(prices_open.get(ticker))):
            missing_opens.append(ticker)
            ticker_reasons.append("missing_open_prices")
        if ticker not in prev_closes.index or not pd.notna(prev_closes.get(ticker)):
            missing_prev_closes.append(ticker)
            ticker_reasons.append("missing_prev_closes")
        ticker_validation[ticker] = {
            "pass": len(ticker_reasons) == 0,
            "reasons": ticker_reasons,
        }
        if ticker_reasons:
            blocked_tickers[ticker] = ticker_reasons

    reasons = list(global_reasons)
    if missing_opens:
        reasons.append(f"missing_open_prices:{','.join(sorted(missing_opens))}")
    if missing_prev_closes:
        reasons.append(f"missing_prev_closes:{','.join(sorted(missing_prev_closes))}")

    hard_fail = len(global_reasons) > 0
    ok = not hard_fail
    details = {
        "trade_date": trade_date,
        "signals_path": signals_path,
        "asof_date": asof_date,
        "cutoff_date": cutoff_date,
        "hard_fail": hard_fail,
        "global_reasons": global_reasons,
        "missing_opens": sorted(missing_opens),
        "missing_prev_closes": sorted(missing_prev_closes),
        "blocked_tickers": blocked_tickers,
        "ticker_validation": ticker_validation,
        "result": "FAIL" if hard_fail else "PASS",
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
    def _round_toward_zero(shares: float) -> float:
        if shares > 0:
            return float(math.floor(shares))
        if shares < 0:
            return float(math.ceil(shares))
        return 0.0

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
            target_shares = _round_toward_zero(float(target_shares))

        current_shares = float(h.get(tkr, 0.0))
        raw_delta = float(target_shares) - current_shares
        delta = raw_delta if cfg.allow_fractional else _round_toward_zero(raw_delta)

        if abs(delta) < 1e-12:
            continue
        if not cfg.allow_fractional and abs(delta) < 1.0:
            continue

        side = "BUY" if delta > 0 else "SELL"
        slipped_px, slip_cost_per_share = apply_slippage(px, side, cfg.slippage_bps)
        trade_notional = abs(delta) * slipped_px
        if trade_notional < cfg.min_trade_dollars:
            continue

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
                    "target_dollars": float(row["target_dollars"]),
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
        if not cfg.allow_fractional:
            exec_shares = _round_toward_zero(float(exec_shares))

        if exec_shares <= 1e-12:
            if desired > 1e-12:
                scaled_tickers.append(tkr)
            continue

        if not cfg.allow_fractional and exec_shares < 1.0:
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
    sweep_added_shares = 0
    sweep_iterations = 0
    sweep_tickers: set[str] = set()

    # Whole-share cash sweep: allocate leftover investable dollars by adding +1 share
    # to the most underfilled target names (deterministic ranking).
    if (not cfg.allow_fractional) and buy_candidates:
        shares_after: Dict[str, float] = {
            str(ticker): float(shares) for ticker, shares in h.items()
        }
        buy_trade_index: Dict[str, int] = {}
        for idx, tr in enumerate(trades):
            tkr = str(tr.get("ticker", ""))
            side = str(tr.get("side", "")).upper()
            sh = float(tr.get("shares", 0.0))
            if side == "BUY":
                shares_after[tkr] = float(shares_after.get(tkr, 0.0) + sh)
                buy_trade_index[tkr] = idx
            elif side == "SELL":
                shares_after[tkr] = max(0.0, float(shares_after.get(tkr, 0.0) - sh))

        target_meta: Dict[str, Dict[str, float]] = {
            str(c["ticker"]): {
                "price": float(c["price"]),
                "target_dollars": float(c["target_dollars"]),
                "target_weight": float(c["target_weight"]),
                "slippage_cost_per_share": float(c["slippage_cost_per_share"]),
            }
            for c in buy_candidates
        }

        max_iterations = 500
        for _ in range(max_iterations):
            remaining_target = max(0.0, float(target_investable_dollars) - float(invested_buys))
            remaining_cash = max(0.0, float(cash_after_sells) - float(invested_buys))
            remaining = min(remaining_target, remaining_cash)
            if remaining <= 1e-9:
                break

            ranked: List[tuple[float, float, str]] = []
            min_add_price = None
            for tkr, meta in sorted(target_meta.items()):
                px = float(meta["price"])
                if px <= 0:
                    continue
                current_shares = max(0.0, float(shares_after.get(tkr, 0.0)))
                current_dollars = current_shares * px
                gap = float(meta["target_dollars"]) - current_dollars
                if gap <= 1e-9:
                    continue
                if remaining + 1e-9 < px:
                    continue
                # New BUY orders must still satisfy minimum notional.
                if tkr not in buy_trade_index and px + 1e-9 < float(cfg.min_trade_dollars):
                    continue
                min_add_price = px if min_add_price is None else min(min_add_price, px)
                ranked.append((gap, float(meta["target_weight"]), tkr))

            if not ranked:
                break
            if remaining + 1e-9 < float(cfg.min_trade_dollars):
                break
            if min_add_price is not None and remaining + 1e-9 < float(min_add_price):
                break

            ranked.sort(key=lambda x: (-x[0], -x[1], x[2]))
            _, _, best_ticker = ranked[0]
            best_meta = target_meta[best_ticker]
            px = float(best_meta["price"])

            if best_ticker in buy_trade_index:
                idx = buy_trade_index[best_ticker]
                trades[idx]["shares"] = float(trades[idx]["shares"]) + 1.0
                trades[idx]["notional"] = float(trades[idx]["notional"]) + px
                trades[idx]["slippage_cost"] = float(trades[idx]["slippage_cost"]) + float(
                    best_meta["slippage_cost_per_share"]
                )
            else:
                trades.append(
                    {
                        "ticker": best_ticker,
                        "side": "BUY",
                        "shares": 1.0,
                        "price": float(px),
                        "slippage_cost": float(best_meta["slippage_cost_per_share"]),
                        "notional": float(px),
                        "reason": "cash_sweep",
                    }
                )
                buy_trade_index[best_ticker] = len(trades) - 1

            shares_after[best_ticker] = float(shares_after.get(best_ticker, 0.0) + 1.0)
            invested_buys = float(invested_buys + px)
            sweep_added_shares += 1
            sweep_iterations += 1
            sweep_tickers.add(best_ticker)

    final_remaining_target = max(0.0, float(target_investable_dollars) - float(invested_buys))
    final_remaining_cash = max(0.0, float(cash_after_sells) - float(invested_buys))
    cash_sweep_remaining_dollars = float(min(final_remaining_target, final_remaining_cash))

    if sweep_added_shares > 0:
        logger.info(
            "[CASH_SWEEP] added_shares=%d tickers=%d residual_cash=%.2f",
            sweep_added_shares,
            len(sweep_tickers),
            cash_sweep_remaining_dollars,
        )

    return pd.DataFrame(trades), {
        "target_cash_weight": float(target_cash_weight),
        "target_investable_dollars": float(target_investable_dollars),
        "scaled_tickers": sorted(set(scaled_tickers)),
        "overspend_prevented": overspend_prevented,
        "cash_sweep_added_shares": int(sweep_added_shares),
        "cash_sweep_iterations": int(sweep_iterations),
        "cash_sweep_tickers": sorted(sweep_tickers),
        "cash_sweep_remaining_dollars": float(cash_sweep_remaining_dollars),
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
    side_series = out["side"].astype(str).str.upper() if "side" in out.columns else pd.Series("", index=out.index)
    buy_mask = side_series.eq("BUY")
    buy_turnover = float(out.loc[buy_mask, "notional"].astype(float).sum()) if "notional" in out.columns else 0.0
    current_turnover = float(out["notional"].sum())
    sell_turnover = float(current_turnover - buy_turnover)
    risk_meta = {
        "turnover_requested": float(buy_turnover),
        "turnover_requested_buys": float(buy_turnover),
        "turnover_requested_sells": float(sell_turnover),
        "turnover_requested_total": float(current_turnover),
        "turnover_cap": float(max_turnover_notional),
        "turnover_scaled": False,
        "turnover_scale": 1.0,
        "turnover_cap_scope": "buys_only",
    }
    if cfg.max_turnover_pct > 0 and buy_turnover > max_turnover_notional + 1e-9:
        scale = max_turnover_notional / buy_turnover if buy_turnover > 0 else 0.0
        scale = max(0.0, min(1.0, float(scale)))
        logger.warning(
            "[RISK] buy turnover cap hit; scaling BUY orders only (requested=%.2f cap=%.2f scale=%.4f sells_exempt=%.2f)",
            buy_turnover,
            max_turnover_notional,
            scale,
            sell_turnover,
        )
        out.loc[buy_mask, "shares"] = out.loc[buy_mask, "shares"].astype(float) * scale
        if "notional" in out.columns:
            out.loc[buy_mask, "notional"] = out.loc[buy_mask, "notional"].astype(float) * scale
        if "slippage_cost" in out.columns:
            out.loc[buy_mask, "slippage_cost"] = out.loc[buy_mask, "slippage_cost"].astype(float) * scale

        scaled_rows: List[Dict[str, object]] = []
        for _, row in out.iterrows():
            r = row.to_dict()
            shares = float(r.get("shares", 0.0))
            price = float(r.get("price", 0.0))

            if cfg.allow_fractional:
                rounded_shares = abs(shares)
            else:
                rounded_shares = float(math.floor(abs(shares)))
                if rounded_shares < 1.0:
                    continue

            notional = rounded_shares * abs(price)
            if notional < float(cfg.min_trade_dollars):
                continue

            r["shares"] = rounded_shares
            r["notional"] = float(notional)
            if "slippage_cost" in r:
                prior_slippage = float(r.get("slippage_cost", 0.0))
                prior_shares = abs(float(row.get("shares", 0.0)))
                if prior_shares > 0:
                    r["slippage_cost"] = float(prior_slippage * (rounded_shares / prior_shares))
                else:
                    r["slippage_cost"] = 0.0
            scaled_rows.append(r)

        out = pd.DataFrame(scaled_rows, columns=trades.columns) if scaled_rows else pd.DataFrame(columns=trades.columns)
        risk_meta["turnover_scaled"] = True
        risk_meta["turnover_scale"] = float(scale)

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

    out.attrs["risk_meta"] = risk_meta
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
        shares = abs(float(tr.get("shares", 0.0)))
        notional = abs(float(tr.get("notional", 0.0)))
        if shares < 1.0 or notional < 1.0:
            continue
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


def _normalize_precomputed_trade_plan(
    precomputed_trade_plan: List[Dict[str, object]] | None,
) -> pd.DataFrame:
    cols = ["ticker", "side", "shares", "price", "slippage_cost", "notional", "reason"]
    rows: List[Dict[str, object]] = []
    for item in precomputed_trade_plan or []:
        if not isinstance(item, dict):
            continue
        ticker = str(item.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        side_raw = str(item.get("side") or "").strip().upper()
        side = "SELL" if side_raw in {"SELL", "CLOSE", "REDUCE"} else "BUY"
        try:
            shares = float(item.get("shares", item.get("quantity")) or 0.0)
        except Exception:
            shares = 0.0
        try:
            price = float(
                item.get("price")
                or item.get("entry_price")
                or 0.0
            )
        except Exception:
            price = 0.0
        try:
            notional = float(item.get("notional") or 0.0)
        except Exception:
            notional = 0.0
        if price <= 0.0 and shares > 0.0 and notional > 0.0:
            price = abs(float(notional)) / abs(float(shares))
        rows.append(
            {
                "ticker": ticker,
                "side": side,
                "shares": abs(float(shares)),
                "price": float(price),
                "slippage_cost": float(item.get("slippage_cost") or 0.0),
                "notional": abs(float(notional)) if notional else abs(float(shares) * float(price)),
                "reason": str(item.get("reason") or item.get("notes") or "precomputed_execution_payload"),
            }
        )
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows).reindex(columns=cols)


def _normalize_and_filter_executable_trades(
    trades: pd.DataFrame,
    cfg: PaperConfig,
) -> Tuple[pd.DataFrame, Dict[str, int]]:
    cols = ["ticker", "side", "shares", "price", "slippage_cost", "notional", "reason"]
    if trades is None or trades.empty:
        return pd.DataFrame(columns=cols), {
            "raw": 0,
            "rounded": 0,
            "dropped_zero_shares": 0,
            "dropped_min_notional": 0,
            "kept": 0,
        }

    raw = int(len(trades))
    rounded_rows: List[Dict[str, object]] = []
    dropped_zero_shares = 0
    dropped_min_notional = 0

    for _, row in trades.iterrows():
        rounded = row.to_dict()
        shares = abs(float(rounded.get("shares", 0.0)))
        price = float(rounded.get("price", 0.0))
        rounded_shares = float(math.floor(shares))

        if rounded_shares < 1.0:
            dropped_zero_shares += 1
            continue

        notional = abs(rounded_shares * price)
        if notional < float(cfg.min_trade_dollars):
            dropped_min_notional += 1
            continue

        rounded["shares"] = rounded_shares
        rounded["notional"] = float(notional)
        rounded_rows.append(rounded)

    out = pd.DataFrame(rounded_rows)
    if out.empty:
        out = pd.DataFrame(columns=cols)
    else:
        out = out.reindex(columns=cols)

    stats = {
        "raw": raw,
        "rounded": raw,
        "dropped_zero_shares": dropped_zero_shares,
        "dropped_min_notional": dropped_min_notional,
        "kept": int(len(out)),
    }
    logger.info(
        "[EXECUTION_FILTER] raw=%d rounded=%d dropped_zero=%d dropped_min_notional=%d kept=%d",
        stats["raw"],
        stats["rounded"],
        stats["dropped_zero_shares"],
        stats["dropped_min_notional"],
        stats["kept"],
    )
    return out, stats


def _filter_idempotent_orders(orders: List[Dict[str, object]], sent_ledger_path: str) -> Tuple[List[Dict[str, object]], List[str]]:
    sent = _read_csv(sent_ledger_path)
    existing = (
        set(sent["order_id"].astype(str).tolist())
        if (not sent.empty and "order_id" in sent.columns)
        else set()
    )
    out: List[Dict[str, object]] = []
    skipped: List[str] = []
    for o in orders:
        if o["order_id"] in existing:
            logger.info("[ORDER][IDEMPOTENT] skipped order_id=%s", o["order_id"])
            skipped.append(o["order_id"])
            continue
        out.append(o)
    return out, skipped


def _same_day_submission_lock(sent_ledger_path: str, trade_date: str) -> Tuple[int, List[str]]:
    sent = _read_csv(sent_ledger_path)
    if sent.empty:
        return 0, []

    date_col = "date" if "date" in sent.columns else ("trade_date" if "trade_date" in sent.columns else None)
    if date_col is None:
        return 0, []

    matching = sent[sent[date_col].astype(str) == str(trade_date)].copy()
    if matching.empty:
        return 0, []

    order_count = (
        int(len(set(matching["order_id"].astype(str).str.strip()) - {""}))
        if "order_id" in matching.columns
        else int(len(matching))
    )
    prior_run_ids = (
        sorted({str(value).strip() for value in matching["run_id"].tolist() if str(value).strip()})
        if "run_id" in matching.columns
        else []
    )
    return order_count, prior_run_ids


def _coerce_float(value: object, default: float | None = 0.0) -> float | None:
    try:
        return float(value)
    except Exception:
        if default is None:
            return None
        return float(default)


def _persist_sent_orders(
    orders: List[Dict[str, object]],
    sent_ledger_path: str,
    run_date: str,
    run_id: str,
    order_metadata: Dict[str, Dict[str, object]] | None = None,
) -> None:
    if not orders:
        return
    order_metadata = order_metadata or {}
    rows = []
    for o in orders:
        internal_order_id = str(o.get("order_id", ""))
        meta = order_metadata.get(internal_order_id, {})
        rows.append(
            {
                "date": run_date,
                "run_id": run_id,
                "order_id": internal_order_id,
                "ticker": str(o.get("ticker", "")),
                "side": str(o.get("side", "")).upper(),
                "client_order_id": str(meta.get("client_order_id") or ""),
                "alpaca_order_id": str(meta.get("alpaca_order_id") or ""),
                "status": str(meta.get("status") or ""),
            }
        )

    incoming = pd.DataFrame(rows)
    existing = _read_csv(sent_ledger_path)

    if existing.empty:
        combined = incoming.copy()
    else:
        combined = pd.concat([existing, incoming], ignore_index=True, sort=False)

    for col in SENT_LEDGER_COLUMNS:
        if col not in combined.columns:
            combined[col] = ""
    combined = combined[SENT_LEDGER_COLUMNS + [c for c in combined.columns if c not in SENT_LEDGER_COLUMNS]]
    if "order_id" in combined.columns:
        combined = combined.drop_duplicates(subset=["order_id"], keep="first")
    sort_cols = [c for c in ("date", "order_id") if c in combined.columns]
    if sort_cols:
        combined = combined.sort_values(sort_cols).reset_index(drop=True)
    _ensure_parent_dir(sent_ledger_path)
    combined.to_csv(sent_ledger_path, index=False)


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
    root = _run_output_root()
    out_path = root / "broker" / f"shadow_orders_{run_date}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    safe_write_text(
        out_path,
        json.dumps(orders, indent=2) + "\n",
        allow_overwrite=_allow_overwrite(),
    )
    return str(out_path)


def _write_intended_orders_artifact(
    run_date: str,
    run_id: str,
    execution_trades: "pd.DataFrame",
    execution_enabled: bool,
    block_reasons: List[str],
    capital_budget: Dict[str, object] | None = None,
) -> str:
    """Persist intended orders before submission so run outcome is always diagnosable.

    Written regardless of whether execution proceeds.  Allows operators to
    distinguish:
      - orders_intended_count=0                    => strategy generated no trades
      - orders_intended_count>0, execution_enabled=False => blocked by execution gate
      - orders_intended_count>0, execution_enabled=True  => orders submitted
    """
    root = _run_output_root()
    out_path = root / "broker" / f"intended_orders_{run_date}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    orders_list: List[Dict[str, object]] = []
    if execution_trades is not None and not execution_trades.empty:
        for _, row in execution_trades.iterrows():
            orders_list.append({
                "ticker": str(row.get("ticker", "")),
                "side": str(row.get("side", "")),
                "shares": float(row["shares"]) if row.get("shares") is not None else None,
                "price": float(row["price"]) if row.get("price") is not None else None,
                "notional": float(row["notional"]) if row.get("notional") is not None else None,
                "reason": str(row["reason"]) if row.get("reason") is not None else None,
            })
    payload = {
        "report_date": run_date,
        "run_id": run_id,
        "orders_intended_count": len(orders_list),
        "orders_intended": orders_list,
        "execution_blocked": not execution_enabled,
        "block_reasons": list(block_reasons),
        "execution_enabled": execution_enabled,
        # reconcile_report is null here; the recon_execution_blocked_*.json
        # artifact (written by reconciliation.py) covers the case where
        # recon blocked before run_paper_day was called.
        "reconcile_report": None,
        "capital_budget": dict(capital_budget or _default_capital_budget_meta()),
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    logger.info(
        "[INTENDED_ORDERS] orders=%d execution_enabled=%s path=%s",
        len(orders_list),
        execution_enabled,
        out_path,
    )
    return str(out_path)


def _write_alpaca_orders(run_date: str, submissions: List[Dict[str, object]]) -> str:
    root = _run_output_root()
    out_path = root / "broker" / f"orders_{run_date}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cols = [
        "trade_date",
        "order_id",
        "client_order_id",
        "alpaca_order_id",
        "ticker",
        "side",
        "quantity",
        "status",
        "submitted_at",
        "mode",
    ]
    frame = pd.DataFrame(submissions or [])
    if frame.empty:
        frame = pd.DataFrame(columns=cols)
    else:
        for c in cols:
            if c not in frame.columns:
                frame[c] = ""
        frame = frame[cols + [c for c in frame.columns if c not in cols]]
    safe_write_text(
        out_path,
        frame.to_csv(index=False),
        allow_overwrite=_allow_overwrite(),
    )
    return str(out_path)


def _write_broker_account_snapshot(
    filename: str,
    run_date: str,
    account: Dict[str, object] | None,
) -> str:
    root = _run_output_root()
    out_path = root / "broker" / filename
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "trade_date": str(run_date),
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "account": dict(account or {}),
        "cash": _coerce_float((account or {}).get("cash"), None),
        "equity": _coerce_float(
            (account or {}).get("equity") or (account or {}).get("portfolio_value"),
            None,
        ),
        "buying_power": _coerce_float((account or {}).get("buying_power"), None),
        "status": str((account or {}).get("status") or ""),
    }
    safe_write_text(
        out_path,
        json.dumps(json_safe_primitive(payload), indent=2) + "\n",
        allow_overwrite=True,
    )
    return str(out_path)


def _write_broker_positions_snapshot(
    filename: str,
    run_date: str,
    positions: List[Dict[str, object]] | None,
) -> str:
    root = _run_output_root()
    out_path = root / "broker" / filename
    out_path.parent.mkdir(parents=True, exist_ok=True)
    normalized = _positions_to_quantity_map(positions or [])
    payload = {
        "trade_date": str(run_date),
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "positions_count": int(len(positions or [])),
        "normalized_positions": normalized,
        "positions": list(positions or []),
    }
    safe_write_text(
        out_path,
        json.dumps(json_safe_primitive(payload), indent=2) + "\n",
        allow_overwrite=True,
    )
    return str(out_path)


def _split_orders_for_execution(
    orders: List[Dict[str, object]],
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    sell_orders: List[Dict[str, object]] = []
    buy_orders: List[Dict[str, object]] = []
    for order in orders or []:
        side = str(order.get("side", "")).upper()
        if side in {"SELL", "CLOSE", "REDUCE"}:
            sell_orders.append(order)
        else:
            buy_orders.append(order)
    return sell_orders, buy_orders


def _order_notional(order: Dict[str, object]) -> float:
    notional = _coerce_float(order.get("notional"), 0.0)
    if notional > 0:
        return float(abs(notional))
    qty = abs(_coerce_float(order.get("quantity"), 0.0))
    price = abs(_coerce_float(order.get("price"), 0.0))
    return float(qty * price)


def _positions_to_quantity_map(positions: List[Dict[str, object]]) -> Dict[str, float]:
    normalized: Dict[str, float] = {}
    for pos in positions or []:
        symbol = str(pos.get("symbol") or "").upper().strip()
        if not symbol:
            continue
        normalized[symbol] = abs(_coerce_float(pos.get("qty"), 0.0) or 0.0)
    return normalized


def _holdings_to_quantity_map(holdings: pd.DataFrame) -> Dict[str, float]:
    if holdings is None or holdings.empty:
        return {}
    normalized: Dict[str, float] = {}
    for _, row in holdings.iterrows():
        symbol = str(row.get("ticker") or "").upper().strip()
        if not symbol:
            continue
        normalized[symbol] = normalized.get(symbol, 0.0) + abs(_coerce_float(row.get("shares"), 0.0) or 0.0)
    return normalized


def _expected_positions_after_orders(
    starting_positions: Dict[str, float],
    orders: List[Dict[str, object]],
) -> Dict[str, float]:
    expected = {str(k): float(v) for k, v in (starting_positions or {}).items()}
    for order in orders or []:
        symbol = str(order.get("ticker") or "").upper().strip()
        if not symbol:
            continue
        qty = abs(_coerce_float(order.get("quantity"), 0.0) or 0.0)
        side = str(order.get("side") or "").upper()
        current = expected.get(symbol, 0.0)
        if side in {"SELL", "CLOSE", "REDUCE"}:
            new_qty = max(0.0, current - qty)
        else:
            new_qty = current + qty
        if new_qty <= 1e-12:
            expected.pop(symbol, None)
        else:
            expected[symbol] = float(new_qty)
    return expected


def _resolve_filled_buy_orders(
    alpaca: AlpacaBroker,
    submitted_orders: List[Dict[str, object]],
) -> List[Dict[str, object]]:
    buy_orders_submitted = 0
    buy_orders_filled = 0
    orders_for_recon: List[Dict[str, object]] = []
    try:
        for order in submitted_orders or []:
            side = str(order.get("side") or "").upper()
            if side in {"SELL", "CLOSE", "REDUCE"}:
                orders_for_recon.append(order)
                continue
            buy_orders_submitted += 1
            alpaca_order_id = str(order.get("alpaca_order_id") or "").strip()
            current_order: Dict[str, object] | None = None
            if alpaca_order_id:
                current_order = alpaca.get_order(alpaca_order_id)
            else:
                internal_order_id = str(order.get("order_id") or "").strip()
                if not internal_order_id:
                    raise RuntimeError("missing internal order_id for submitted buy order")
                current_order = alpaca.find_order_by_client_id(
                    alpaca_client_order_id(internal_order_id)
                )
            if not current_order:
                raise RuntimeError(
                    f"missing current Alpaca status for buy order {str(order.get('order_id') or '').strip() or str(order.get('ticker') or '').strip() or 'unknown'}"
                )
            status = str(current_order.get("status") or "").upper()
            if status == "FILLED":
                orders_for_recon.append(order)
                buy_orders_filled += 1
        if buy_orders_submitted > 0:
            logger.info(
                "[POSTTRADE_RECON] buy_orders_submitted=%d buy_orders_filled=%d buy_orders_pending=%d (excluded from drift check)",
                buy_orders_submitted,
                buy_orders_filled,
                buy_orders_submitted - buy_orders_filled,
            )
        return orders_for_recon
    except Exception as exc:
        logger.warning(
            "[POSTTRADE_RECON] buy fill resolution failed; using full submitted order set: %s",
            exc,
        )
        return list(submitted_orders or [])


def _sell_phase_timeout_seconds() -> float:
    raw = _coerce_float(os.getenv("ALPACA_SELL_PHASE_TIMEOUT_SECONDS"), None)
    if raw is None:
        return float(ALPACA_SELL_PHASE_TIMEOUT_SECONDS)
    return max(0.0, float(raw))


def _sell_phase_poll_interval_seconds() -> float:
    raw = _coerce_float(os.getenv("ALPACA_SELL_PHASE_POLL_INTERVAL_SECONDS"), None)
    if raw is None:
        return float(ALPACA_SELL_PHASE_POLL_INTERVAL_SECONDS)
    return max(0.0, float(raw))


def _positions_match_expected_after_sells(
    actual_positions: Dict[str, float],
    expected_positions: Dict[str, float],
    tracked_symbols: List[str],
    *,
    tolerance: float = 1e-6,
) -> bool:
    for symbol in tracked_symbols:
        actual_qty = float(actual_positions.get(symbol, 0.0) or 0.0)
        expected_qty = float(expected_positions.get(symbol, 0.0) or 0.0)
        if actual_qty > expected_qty + float(tolerance):
            return False
    return True


def _wait_for_alpaca_sell_phase_completion(
    *,
    alpaca: AlpacaBroker,
    sell_orders: List[Dict[str, object]],
    submission_metadata: Dict[str, Dict[str, object]],
    starting_positions: Dict[str, float],
) -> Dict[str, object]:
    timeout_seconds = _sell_phase_timeout_seconds()
    poll_interval_seconds = _sell_phase_poll_interval_seconds()
    tracked_symbols = sorted(
        {
            str(order.get("ticker") or "").upper().strip()
            for order in sell_orders or []
            if str(order.get("ticker") or "").strip()
        }
    )
    expected_positions = _expected_positions_after_orders(starting_positions, sell_orders)

    def _snapshot_state() -> tuple[Dict[str, object], List[Dict[str, object]], Dict[str, str]]:
        account = alpaca.get_account()
        positions = alpaca.get_positions()
        observed_statuses: Dict[str, str] = {}
        for order in sell_orders or []:
            internal_order_id = str(order.get("order_id") or "")
            client_order_id = str(
                ((submission_metadata.get(internal_order_id) or {}).get("client_order_id")) or ""
            ).strip()
            status = str(
                ((submission_metadata.get(internal_order_id) or {}).get("status")) or "UNKNOWN"
            ).upper()
            if client_order_id:
                current = alpaca.find_order_by_client_id(client_order_id) or {}
                if current:
                    status = str(current.get("status") or status or "UNKNOWN").upper()
            if internal_order_id:
                observed_statuses[internal_order_id] = status
        return account, positions, observed_statuses

    if not sell_orders:
        account, positions, observed_statuses = _snapshot_state()
        return {
            "status": "NO_SELLS",
            "completion_reason": "no_sell_orders",
            "polls": 1,
            "account": account,
            "positions": positions,
            "observed_statuses": observed_statuses,
        }

    logger.info(
        "[ALPACA][SELL_PHASE] started tracked_orders=%d tracked_symbols=%s timeout=%.1fs poll_interval=%.1fs",
        len(sell_orders),
        ",".join(tracked_symbols) or "none",
        float(timeout_seconds),
        float(poll_interval_seconds),
    )

    terminal_failure_statuses = {"CANCELED", "CANCELLED", "EXPIRED", "REJECTED", "SUSPENDED"}
    deadline = time.monotonic() + float(timeout_seconds)
    polls = 0
    last_account: Dict[str, object] | None = None
    last_positions: List[Dict[str, object]] = []
    last_observed_statuses: Dict[str, str] = {}

    while True:
        polls += 1
        try:
            account, positions, observed_statuses = _snapshot_state()
        except Exception as exc:
            logger.warning("[ALPACA][SELL_PHASE] refresh_failed polls=%d error=%s", polls, exc)
            return {
                "status": "REFRESH_FAILED",
                "completion_reason": f"refresh_failed:{exc}",
                "polls": polls,
                "account": last_account,
                "positions": last_positions,
                "observed_statuses": last_observed_statuses,
            }

        last_account = account
        last_positions = positions
        last_observed_statuses = observed_statuses
        actual_positions = _positions_to_quantity_map(positions)
        positions_confirmed = _positions_match_expected_after_sells(
            actual_positions,
            expected_positions,
            tracked_symbols,
        )
        terminal_failures = sorted(
            {
                status
                for status in observed_statuses.values()
                if str(status).upper() in terminal_failure_statuses
            }
        )
        all_filled = bool(observed_statuses) and all(
            str(status).upper() == "FILLED" for status in observed_statuses.values()
        )

        if positions_confirmed or all_filled:
            completion_reason = "positions_confirmed" if positions_confirmed else "orders_filled"
            logger.info(
                "[ALPACA][SELL_PHASE] completed polls=%d reason=%s observed_statuses=%s",
                polls,
                completion_reason,
                json.dumps(observed_statuses, sort_keys=True),
            )
            return {
                "status": "COMPLETED",
                "completion_reason": completion_reason,
                "polls": polls,
                "account": account,
                "positions": positions,
                "observed_statuses": observed_statuses,
            }

        if terminal_failures:
            completion_reason = f"terminal_nonfill:{','.join(terminal_failures)}"
            logger.warning(
                "[ALPACA][SELL_PHASE] failed polls=%d reason=%s observed_statuses=%s",
                polls,
                completion_reason,
                json.dumps(observed_statuses, sort_keys=True),
            )
            return {
                "status": "FAILED",
                "completion_reason": completion_reason,
                "polls": polls,
                "account": account,
                "positions": positions,
                "observed_statuses": observed_statuses,
            }

        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            logger.warning(
                "[ALPACA][SELL_PHASE] timeout polls=%d tracked_symbols=%s observed_statuses=%s",
                polls,
                ",".join(tracked_symbols) or "none",
                json.dumps(observed_statuses, sort_keys=True),
            )
            return {
                "status": "TIMEOUT",
                "completion_reason": "timeout_waiting_for_sell_completion",
                "polls": polls,
                "account": account,
                "positions": positions,
                "observed_statuses": observed_statuses,
            }

        logger.info(
            "[ALPACA][SELL_PHASE] waiting polls=%d remaining=%.1fs observed_statuses=%s",
            polls,
            float(remaining_seconds),
            json.dumps(observed_statuses, sort_keys=True),
        )
        if poll_interval_seconds > 0:
            time.sleep(min(float(poll_interval_seconds), float(remaining_seconds)))


def _build_capital_budget(
    *,
    broker_cash: object,
    broker_equity: object,
    broker_buying_power: object,
    expected_sell_proceeds: float,
    requested_buy_notional: float,
) -> Dict[str, object]:
    cash_value = _coerce_float(broker_cash, None)
    equity_value = _coerce_float(broker_equity, None)
    buying_power_value = _coerce_float(broker_buying_power, None)
    reserve_cash_floor = max(
        float(CAPITAL_RESERVE_MIN_CASH),
        max(0.0, float(equity_value or 0.0)) * float(CAPITAL_RESERVE_EQUITY_PCT),
    )
    reserve_cash_cap = max(0.0, float(equity_value or 0.0)) * 0.05
    reserve_cash = (
        min(reserve_cash_floor, reserve_cash_cap)
        if reserve_cash_cap > 0.0
        else reserve_cash_floor
    )
    expected_sell_proceeds_value = max(0.0, float(expected_sell_proceeds or 0.0))
    expected_sell_proceeds_conservative = float(
        expected_sell_proceeds_value * float(CAPITAL_SELL_PROCEEDS_HAIRCUT)
    )
    requested_buy_notional_value = max(0.0, float(requested_buy_notional or 0.0))
    available_for_buys = max(
        0.0,
        float(cash_value or 0.0) + expected_sell_proceeds_conservative - reserve_cash,
    )
    allowed_buy_notional = min(requested_buy_notional_value, available_for_buys)
    return {
        "broker_cash_at_planning": cash_value,
        "broker_equity_at_planning": equity_value,
        "broker_buying_power_at_planning": buying_power_value,
        "reserve_cash_policy": {
            "min_cash_dollars": float(CAPITAL_RESERVE_MIN_CASH),
            "equity_reserve_pct": float(CAPITAL_RESERVE_EQUITY_PCT),
            "sell_proceeds_haircut": float(CAPITAL_SELL_PROCEEDS_HAIRCUT),
            "reserve_cash": float(reserve_cash),
            "available_for_buys": float(available_for_buys),
        },
        "expected_sell_proceeds": float(expected_sell_proceeds_value),
        "expected_sell_proceeds_conservative": float(expected_sell_proceeds_conservative),
        "requested_buy_notional": float(requested_buy_notional_value),
        "allowed_buy_notional": float(allowed_buy_notional),
        "capital_constraint_triggered": bool(
            requested_buy_notional_value > allowed_buy_notional + 1e-9
        ),
        "clipped_or_deferred_buys_count": 0,
    }


def _apply_capital_budget_to_trades(
    trades: pd.DataFrame,
    cfg: PaperConfig,
    capital_budget: Dict[str, object],
) -> Tuple[pd.DataFrame, Dict[str, object]]:
    if trades is None or trades.empty:
        out = dict(capital_budget)
        out["allowed_buy_notional"] = 0.0
        out["clipped_or_deferred_buys_count"] = 0
        return pd.DataFrame(columns=trades.columns if trades is not None else []), out

    cols = list(trades.columns)
    available_for_buys = float(
        ((capital_budget.get("reserve_cash_policy") or {}).get("available_for_buys")) or 0.0
    )
    spent = 0.0
    clipped_or_deferred = 0
    kept_rows: List[Dict[str, object]] = []

    for _, row in trades.iterrows():
        row_dict = row.to_dict()
        side = str(row_dict.get("side", "")).upper()
        if side != "BUY":
            kept_rows.append(row_dict)
            continue

        requested_shares = abs(float(row_dict.get("shares", 0.0) or 0.0))
        price = abs(float(row_dict.get("price", 0.0) or 0.0))
        requested_notional = abs(float(row_dict.get("notional", 0.0) or 0.0))
        remaining_budget = max(0.0, available_for_buys - spent)

        if requested_notional <= remaining_budget + 1e-9:
            kept_rows.append(row_dict)
            spent += requested_notional
            continue

        clipped_or_deferred += 1
        if remaining_budget <= 1e-9 or price <= 0 or requested_shares <= 1e-12:
            continue

        if cfg.allow_fractional:
            allowed_shares = min(requested_shares, remaining_budget / price)
        else:
            allowed_shares = min(requested_shares, float(math.floor(remaining_budget / price)))
            if allowed_shares > 0:
                allowed_shares = float(math.floor(allowed_shares))

        allowed_notional = allowed_shares * price
        if allowed_shares <= 1e-12 or allowed_notional + 1e-9 < float(cfg.min_trade_dollars):
            continue

        scale = allowed_shares / requested_shares if requested_shares > 0 else 0.0
        row_dict["shares"] = float(allowed_shares)
        row_dict["notional"] = float(allowed_notional)
        if row_dict.get("slippage_cost") is not None:
            row_dict["slippage_cost"] = float(float(row_dict.get("slippage_cost") or 0.0) * scale)
        row_dict["reason"] = (
            f"{row_dict.get('reason')}_capital_clipped"
            if allowed_shares + 1e-9 < requested_shares
            else row_dict.get("reason")
        )
        kept_rows.append(row_dict)
        spent += allowed_notional

    out = dict(capital_budget)
    out["allowed_buy_notional"] = float(spent)
    constraint_triggered = bool(
        float(out.get("requested_buy_notional") or 0.0) > spent + 1e-9
    )
    out["capital_constraint_triggered"] = constraint_triggered
    out["clipped_or_deferred_buys_count"] = int(clipped_or_deferred)

    # Detect when capital constraint drops ALL buy orders to zero.
    buy_rows = [r for r in kept_rows if str(r.get("side", "")).upper() == "BUY"]
    requested_had_buys = any(
        str(r.get("side", "")).upper() == "BUY"
        for _, r in trades.iterrows()
    ) if trades is not None and not trades.empty else False
    all_buys_clipped = bool(constraint_triggered and requested_had_buys and not buy_rows)
    out["capital_constrained_no_trades"] = all_buys_clipped
    if all_buys_clipped:
        logger.warning(
            "[CAPITAL_BUDGET][WARN] all orders clipped to zero — retry eligible"
        )

    frame = pd.DataFrame(kept_rows, columns=cols) if kept_rows else pd.DataFrame(columns=cols)
    return frame, out


def _compute_buy_budget(account: Dict[str, object], cfg: PaperConfig) -> float:
    del cfg
    capital_budget = _build_capital_budget(
        broker_cash=account.get("cash"),
        broker_equity=account.get("equity") or account.get("portfolio_value"),
        broker_buying_power=account.get("buying_power"),
        expected_sell_proceeds=0.0,
        requested_buy_notional=float("inf"),
    )
    return float(((capital_budget.get("reserve_cash_policy") or {}).get("available_for_buys")) or 0.0)


def _apply_buy_budget(
    buy_orders: List[Dict[str, object]],
    buy_budget: float,
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    kept: List[Dict[str, object]] = []
    skipped: List[Dict[str, object]] = []
    spent = 0.0
    for order in buy_orders or []:
        notional = _order_notional(order)
        if spent + notional <= float(buy_budget) + 1e-9:
            kept.append(order)
            spent += notional
        else:
            skipped.append(order)
    return kept, skipped


def _default_pdt_pretrade_meta() -> Dict[str, object]:
    return {
        "broker_pdt_risk_status": "UNKNOWN",
        "broker_pdt_daytrade_count": None,
        "broker_pdt_daytrading_buying_power": None,
        "broker_pdt_flags": [],
        "broker_pdt_warning_message": None,
        "deferred_orders_count": 0,
        "deferred_symbols": [],
    }


def _same_day_sent_symbols(sent_ledger_path: str, trade_date: str, side: str) -> set[str]:
    sent = _read_csv(sent_ledger_path)
    if sent.empty or "date" not in sent.columns or "side" not in sent.columns or "ticker" not in sent.columns:
        return set()

    mask = (
        sent["date"].astype(str) == str(trade_date)
    ) & (
        sent["side"].astype(str).str.upper() == str(side).upper()
    )
    if not mask.any():
        return set()
    return {
        str(ticker).upper().strip()
        for ticker in sent.loc[mask, "ticker"].tolist()
        if str(ticker).strip()
    }


def _apply_pdt_pretrade_guard(
    trades: pd.DataFrame,
    *,
    planning_account_snapshot: Dict[str, object] | None,
    sent_ledger_path: str,
    run_date: str,
) -> Tuple[pd.DataFrame, Dict[str, object], List[str]]:
    if planning_account_snapshot is None:
        return trades, _default_pdt_pretrade_meta(), []

    policy = summarize_pretrade_broker_policy({"account": planning_account_snapshot})
    meta = {
        **_default_pdt_pretrade_meta(),
        "broker_pdt_risk_status": policy.get("broker_pdt_risk_status") or "UNKNOWN",
        "broker_pdt_daytrade_count": policy.get("broker_pdt_daytrade_count"),
        "broker_pdt_daytrading_buying_power": policy.get("broker_pdt_daytrading_buying_power"),
        "broker_pdt_flags": list(policy.get("broker_pdt_flags") or []),
        "broker_pdt_warning_message": policy.get("broker_pdt_warning_message"),
    }

    if trades is None or trades.empty:
        return trades, meta, []

    strong_risk = bool(
        meta["broker_pdt_risk_status"] == "WARN"
        and (
            "pattern_day_trader:true" in meta["broker_pdt_flags"]
            or "daytrade_count_high" in meta["broker_pdt_flags"]
            or "daytrading_buying_power_non_positive" in meta["broker_pdt_flags"]
        )
    )
    if not strong_risk:
        return trades, meta, []

    side_series = (
        trades["side"].astype(str).str.upper()
        if "side" in trades.columns
        else pd.Series([""] * len(trades), index=trades.index, dtype=str)
    )
    ticker_series = (
        trades["ticker"].astype(str).str.upper().str.strip()
        if "ticker" in trades.columns
        else pd.Series([""] * len(trades), index=trades.index, dtype=str)
    )
    sell_mask = side_series.isin({"SELL", "CLOSE", "REDUCE"})
    if not sell_mask.any():
        return trades, meta, []

    clear_block_condition = bool(
        "pattern_day_trader:true" in meta["broker_pdt_flags"]
        and "daytrading_buying_power_non_positive" in meta["broker_pdt_flags"]
    )
    same_day_buy_symbols = _same_day_sent_symbols(sent_ledger_path, run_date, "BUY")
    risky_sell_mask = sell_mask.copy()
    block_reason_suffix = "broker_pdt_zero_daytrading_buying_power"
    if not clear_block_condition:
        if not same_day_buy_symbols:
            return trades, meta, []
        risky_sell_mask = sell_mask & ticker_series.isin(same_day_buy_symbols)
        block_reason_suffix = "local_same_day_buy"

    deferred_symbols = sorted(set(ticker_series.loc[risky_sell_mask].tolist()))
    if not deferred_symbols:
        return trades, meta, []

    filtered = trades.loc[~risky_sell_mask].copy()
    deferred_count = int(risky_sell_mask.sum())
    meta["broker_pdt_risk_status"] = "BLOCKED"
    meta["deferred_orders_count"] = deferred_count
    meta["deferred_symbols"] = deferred_symbols
    flags = list(meta["broker_pdt_flags"])
    flags.append("pre_submit_local_same_day_sell_block")
    meta["broker_pdt_flags"] = sorted(set(flags))
    base_message = str(meta.get("broker_pdt_warning_message") or "PDT risk signaled by broker account fields")
    if clear_block_condition:
        meta["broker_pdt_warning_message"] = (
            f"{base_message}; deferred planned sells before submit because Alpaca reported PDT status with non-positive daytrading_buying_power: {','.join(deferred_symbols)}"
        )
    else:
        meta["broker_pdt_warning_message"] = (
            f"{base_message}; deferred likely same-day sells before submit: {','.join(deferred_symbols)}"
        )
    blocked_reasons = [
        f"pdt_pretrade_block:{symbol}:{block_reason_suffix}"
        for symbol in deferred_symbols
    ]
    return filtered, meta, blocked_reasons


def _submit_alpaca_orders(
    *,
    alpaca: AlpacaBroker,
    orders: List[Dict[str, object]],
    run_date: str,
    alpaca_submissions: List[Dict[str, object]],
    submission_metadata: Dict[str, Dict[str, object]],
    idempotent_skips: List[str],
    idempotent_drop_reasons: Counter[str],
    alpaca_submission_summary: Dict[str, object],
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]], int, int, int]:
    submitted_orders: List[Dict[str, object]] = []
    remote_existing_orders: List[Dict[str, object]] = []
    submit_attempts = 0
    submit_success = 0
    submit_failed = 0

    for order in orders:
        internal_order_id = str(order.get("order_id", ""))
        ticker = str(order.get("ticker", "")).upper()
        side = str(order.get("side", "")).upper()
        quantity = abs(_coerce_float(order.get("quantity"), 0.0))
        order_type = str(order.get("order_type", "MKT")).upper()
        tif = str(order.get("time_in_force", "DAY")).lower()
        if quantity <= 0:
            continue

        client_order_id = alpaca_client_order_id(internal_order_id)
        existing_remote = alpaca.find_order_by_client_id(client_order_id)
        if existing_remote:
            idempotent_skips.append(internal_order_id)
            idempotent_drop_reasons["remote_existing"] += 1
            remote_existing_orders.append(order)
            alpaca_submission_summary["remote_existing_orders"] = int(
                alpaca_submission_summary.get("remote_existing_orders", 0)
            ) + 1
            submission_metadata[internal_order_id] = {
                "client_order_id": client_order_id,
                "alpaca_order_id": str(existing_remote.get("id") or ""),
                "status": str(existing_remote.get("status") or "EXISTING_REMOTE"),
            }
            alpaca_submissions.append(
                {
                    "trade_date": run_date,
                    "order_id": internal_order_id,
                    "client_order_id": client_order_id,
                    "alpaca_order_id": str(existing_remote.get("id") or ""),
                    "ticker": ticker,
                    "side": side,
                    "quantity": float(quantity),
                    "status": str(existing_remote.get("status") or "EXISTING_REMOTE"),
                    "submitted_at": str(existing_remote.get("submitted_at") or ""),
                    "mode": "alpaca",
                }
            )
            logger.info(
                "[ALPACA][IDEMPOTENT] skipped remote-existing order_id=%s client_order_id=%s",
                internal_order_id,
                client_order_id,
            )
            continue

        submit_attempts += 1
        try:
            if order_type == "MKT":
                submitted = alpaca.submit_market_order(
                    symbol=ticker,
                    qty=float(quantity),
                    side=side,
                    client_order_id=client_order_id,
                    tif=tif,
                )
            else:
                submitted = alpaca.submit_limit_order(
                    symbol=ticker,
                    qty=float(quantity),
                    side=side,
                    limit_price=_coerce_float(order.get("notional"), 0.0) / max(float(quantity), 1.0),
                    client_order_id=client_order_id,
                    tif=tif,
                )
        except Exception as exc:
            submit_failed += 1
            reject = classify_alpaca_broker_reject(exc)
            raise AlpacaSubmissionRejectError(
                classification=str(reject.get("classification") or ""),
                broker_message=str(reject.get("message") or ""),
                raw_message=str(reject.get("raw_message") or reject.get("message") or exc),
                broker_code=reject.get("code"),
                order_id=internal_order_id,
                symbol=ticker,
                side=side,
                quantity=float(quantity),
                attempted_submissions=submit_attempts,
                successful_submissions=submit_success,
                failed_submissions=submit_failed,
                submitted_orders=[dict(item) for item in alpaca_submissions],
            ) from exc
        submit_success += 1

        alpaca_order_id = str(submitted.get("id") or "")
        status = str(submitted.get("status") or "SUBMITTED")
        submitted_at = str(submitted.get("submitted_at") or "")
        submission_metadata[internal_order_id] = {
            "client_order_id": client_order_id,
            "alpaca_order_id": alpaca_order_id,
            "status": status,
        }
        alpaca_submissions.append(
            {
                "trade_date": run_date,
                "order_id": internal_order_id,
                "client_order_id": client_order_id,
                "alpaca_order_id": alpaca_order_id,
                "ticker": ticker,
                "side": side,
                "quantity": float(quantity),
                "status": status,
                "submitted_at": submitted_at,
                "mode": "alpaca",
            }
        )
        submitted_orders.append(order)
        logger.info(
            "[ALPACA][SUBMIT] order_id=%s client_order_id=%s alpaca_order_id=%s status=%s",
            internal_order_id,
            client_order_id,
            alpaca_order_id,
            status,
        )

    return submitted_orders, remote_existing_orders, submit_attempts, submit_success, submit_failed


def _capture_alpaca_posttrade_state(
    *,
    alpaca: AlpacaBroker,
    run_date: str,
    holdings_prev: pd.DataFrame,
    submitted_orders: List[Dict[str, object]],
    cfg: "PaperConfig",
    raise_on_failure: bool,
) -> Dict[str, object]:
    try:
        alpaca_account_snapshot = json_safe_primitive(alpaca.get_account())
        alpaca_positions_snapshot = json_safe_primitive(alpaca.get_positions())
    except Exception as exc:
        if raise_on_failure:
            raise RuntimeError(f"Failed to refresh Alpaca account/positions: {exc}") from exc
        logger.warning("[ALPACA][POSTTRADE] refresh failed after broker abort: %s", exc)
        return {}

    posttrade_account_snapshot_path = _write_broker_account_snapshot(
        "posttrade_account_snapshot.json",
        run_date,
        alpaca_account_snapshot,
    )
    posttrade_positions_snapshot_path = _write_broker_positions_snapshot(
        "posttrade_positions.json",
        run_date,
        alpaca_positions_snapshot,
    )
    orders_for_recon = _resolve_filled_buy_orders(alpaca, submitted_orders)
    expected_positions = _expected_positions_after_orders(
        _holdings_to_quantity_map(holdings_prev),
        orders_for_recon,
    )
    actual_positions = _positions_to_quantity_map(alpaca_positions_snapshot)
    posttrade_recon = write_post_execution_drift_report(
        run_date=run_date,
        expected_positions=expected_positions,
        actual_positions=actual_positions,
        actual_cash=_coerce_float(
            alpaca_account_snapshot.get("cash"),
            None,
        ),
        actual_equity=_coerce_float(
            alpaca_account_snapshot.get("equity") or alpaca_account_snapshot.get("portfolio_value"),
            None,
        ),
    )
    return {
        "alpaca_account_snapshot": alpaca_account_snapshot,
        "alpaca_positions_snapshot": alpaca_positions_snapshot,
        "posttrade_account_snapshot_path": posttrade_account_snapshot_path,
        "posttrade_positions_snapshot_path": posttrade_positions_snapshot_path,
        "posttrade_recon_path": str(posttrade_recon.get("report_path") or ""),
        "posttrade_recon_status": str(posttrade_recon.get("drift_status") or ""),
        "posttrade_repair_suggestions": list(posttrade_recon.get("repair_suggestions") or []),
        "posttrade_affected_symbols": list(posttrade_recon.get("affected_symbols") or []),
        "posttrade_duplicate_fill_suspicions_count": int(
            posttrade_recon.get("duplicate_fill_suspicions_count") or 0
        ),
    }


def _alpaca_positions_to_holdings(
    positions: List[Dict[str, object]],
    sleeve: str,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    holdings_rows: List[Dict[str, object]] = []
    m2m_rows: List[Dict[str, object]] = []
    for pos in positions or []:
        ticker = str(pos.get("symbol") or "").upper().strip()
        if not ticker:
            continue
        qty = _coerce_float(pos.get("qty"), 0.0)
        shares = abs(qty)
        if shares <= 0:
            continue
        current_price = _coerce_float(pos.get("current_price"), 0.0)
        market_value = _coerce_float(pos.get("market_value"), shares * current_price)
        holdings_rows.append(
            {
                "ticker": ticker,
                "sleeve": sleeve,
                "shares": float(shares),
            }
        )
        m2m_rows.append(
            {
                "ticker": ticker,
                "sleeve": sleeve,
                "shares": float(shares),
                "price": float(current_price),
                "market_value": float(market_value),
            }
        )
    holdings = pd.DataFrame(holdings_rows)
    if holdings.empty:
        holdings = pd.DataFrame(columns=["ticker", "sleeve", "shares"])
    m2m = pd.DataFrame(m2m_rows)
    if m2m.empty:
        m2m = pd.DataFrame(columns=["ticker", "sleeve", "shares", "price", "market_value"])
    return holdings, m2m


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
        "model_equity": float(model_equity),
        "broker_equity": float(broker_equity),
        "broker_minus_model_equity_delta": float(broker_equity - model_equity),
        "cash_delta": cash_delta,
        "equity_delta": equity_delta,
        "equity_tolerance": equity_tol,
        "position_deltas": deltas,
    }


def _current_et(now_utc: dt.datetime | None = None) -> dt.datetime:
    if now_utc is None:
        now_utc = dt.datetime.now(dt.timezone.utc)
    return now_utc.astimezone(ZoneInfo("America/New_York"))


def _is_weekend_et(now_et: dt.datetime) -> bool:
    """Check if current time is Saturday or Sunday in ET timezone."""
    weekday = now_et.weekday()  # Monday = 0, Sunday = 6
    return weekday in (5, 6)  # Saturday = 5, Sunday = 6


def _get_peak_equity_from_ledger(ledger_path: str) -> float | None:
    """
    Scan the full ledger CSV and return the maximum total_equity observed.
    Returns None if the ledger is missing or has no equity data.
    Used by the circuit-breaker check in _risk_controls_preflight().
    """
    try:
        if not os.path.exists(ledger_path) or os.path.getsize(ledger_path) == 0:
            return None
        led = pd.read_csv(ledger_path)
        if led.empty or "total_equity" not in led.columns:
            return None
        peak = pd.to_numeric(led["total_equity"], errors="coerce").dropna().max()
        return float(peak) if not math.isnan(peak) else None
    except Exception:
        return None


def _risk_controls_preflight(
    execution_trades: pd.DataFrame,
    equity: float,
    ledger_path: str,
) -> Dict[str, object]:
    """
    Run portfolio risk controls before order submission.

    Checks:
      1. Circuit breaker — if portfolio drawdown from peak ≥ threshold, block ALL new buys.
      2. Position caps   — if any single BUY order would exceed max_position_pct of equity,
                           scale it down.

    Parameters
    ----------
    execution_trades : DataFrame with [ticker, side, shares, price, notional, reason]
    equity           : Current portfolio equity (used for weight computation and drawdown base)
    ledger_path      : Path to the CSV ledger for peak-equity lookup

    Returns
    -------
    dict with keys:
        blocked         : bool  — True if circuit breaker fired (suppress all buys)
        block_reason    : str or None
        trimmed_tickers : list[str]  — tickers whose BUY notional was scaled down
        drawdown        : float  — current drawdown from peak (negative means loss)
        risk_summary    : dict  — full RiskResult exposure report
    """
    result: Dict[str, object] = {
        "blocked": False,
        "block_reason": None,
        "trimmed_tickers": [],
        "drawdown": 0.0,
        "risk_summary": {},
    }

    if equity <= 0:
        return result

    try:
        from core.risk_controls import RiskControls

        rc = RiskControls(max_position_pct=float(cfg.max_position_pct))

        # ── Circuit breaker: compare current equity to historical peak ──
        peak_equity = _get_peak_equity_from_ledger(ledger_path)
        if peak_equity is None or peak_equity <= 0:
            peak_equity = equity  # no history yet → 0% drawdown

        drawdown = (equity - peak_equity) / peak_equity  # negative if below peak
        result["drawdown"] = round(drawdown, 6)

        if abs(drawdown) >= rc.circuit_breaker_pct:
            result["blocked"] = True
            result["block_reason"] = (
                f"risk_controls_circuit_breaker:drawdown={drawdown:.1%}_exceeds_threshold={rc.circuit_breaker_pct:.1%}"
            )
            logger.warning(
                "[RISK_CONTROLS] CIRCUIT BREAKER FIRED — drawdown=%.1f%% >= threshold=%.1f%%. Blocking all new buys.",
                drawdown * 100,
                rc.circuit_breaker_pct * 100,
            )
            return result

        # ── Position cap check on BUY orders ──────────────────────────
        if execution_trades is None or execution_trades.empty:
            return result

        buy_mask = execution_trades["side"].astype(str).str.upper() == "BUY"
        buy_trades = execution_trades[buy_mask].copy()
        if buy_trades.empty:
            return result

        trimmed: List[str] = []
        for idx, row in buy_trades.iterrows():
            notional = float(row.get("notional") or 0.0)
            weight = notional / equity
            if weight > rc.max_position_pct:
                old_notional = notional
                execution_trades.loc[idx, "notional"] = rc.max_position_pct * equity
                price = float(row.get("price") or 0.0)
                if price > 0:
                    new_shares = math.floor((rc.max_position_pct * equity) / price)
                    execution_trades.loc[idx, "shares"] = float(new_shares)
                    execution_trades.loc[idx, "notional"] = new_shares * price
                ticker = str(row.get("ticker", ""))
                trimmed.append(ticker)
                logger.warning(
                    "[RISK_CONTROLS] Position cap triggered: %s notional=%.2f (%.1f%% of equity) trimmed to max=%.1f%%",
                    ticker,
                    old_notional,
                    weight * 100,
                    rc.max_position_pct * 100,
                )

        result["trimmed_tickers"] = trimmed

    except Exception as exc:
        logger.warning("[RISK_CONTROLS] Preflight check failed (non-blocking): %s", exc)

    return result


def run_paper_day(
    run_date: str,
    signals_path: str,
    ledger_path: str,
    trades_path: str,
    config_path: str,
    force: bool = False,
    now_et: dt.datetime | None = None,
    constraints: Dict[str, float] | None = None,
    plan_only: bool = False,
    precomputed_trade_plan: List[Dict[str, object]] | None = None,
) -> Dict[str, object]:
    cfg = load_config(config_path)

    # Mode resolution: env MODE overrides config, then falls back to config.
    # Legacy aliases are normalized so the runtime only executes in paper/live.
    cfg_mode = canonical_trading_mode(cfg.trading_mode, field_name="config trading_mode")
    env_mode = str(os.getenv("MODE", "")).strip().lower()
    env_trading_mode = str(os.getenv("TRADING_MODE", "")).strip().lower()
    mode = canonical_trading_mode(
        env_mode if env_mode else cfg_mode,
        field_name="MODE" if env_mode else "config trading_mode",
    )
    trading_mode_env = canonical_trading_mode(
        env_trading_mode if env_trading_mode else mode,
        field_name="TRADING_MODE" if env_trading_mode else "config trading_mode",
    )
    if mode != trading_mode_env:
        raise RuntimeError(
            f"[INVARIANT] MODE={mode} and TRADING_MODE={trading_mode_env} must resolve to the same canonical mode"
        )
    if mode == "live":
        raise RuntimeError("TRADING_MODE=live is not implemented. Refusing to proceed.")
    legacy_shadow_requested = legacy_shadow_mode_requested(cfg.trading_mode, env_mode, env_trading_mode)
    paper_execution_requested = mode == "paper"
    planning_account_snapshot: Dict[str, object] | None = None

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
    elif now_et.tzinfo is None:
        raise ValueError("now_et must be timezone-aware")

    now_et = now_et.astimezone(ZoneInfo("America/New_York"))
    plan_only = bool(
        plan_only
        or legacy_shadow_requested
        or str(os.getenv("PLAN_ONLY", "")).strip().lower() in {"1", "true", "yes", "y"}
    )
    if legacy_shadow_requested:
        logger.warning(
            "[MODE] legacy shadow alias detected; forcing plan_only under canonical paper mode"
        )

    # Weekend pause — skip order submission on Sat/Sun
    # Early return BEFORE loading targets/signals to avoid failures on empty signals
    is_weekend = _is_weekend_et(now_et)
    if is_weekend and not force:
        logger.info(
            "[WEEKEND_PAUSE] Skipping order submission — current day is %s (ET)",
            now_et.strftime("%A")
        )
        return {
            "execution_enabled": False,
            "execution_status": "SKIPPED_WEEKEND",
            "mode": mode,
            "trading_mode": mode,
            "run_date": run_date,
            "orders": [],
            "turnover": 0.0,
            "trades": pd.DataFrame(),
        }

    # Sync holdings and valuation from the paper broker for weekday runs.
    # In canonical paper mode, broker account equity is the authoritative valuation base
    # for position sizing — it reflects live market prices.  Canonical/ledger
    # equity is stale (recorded at prior execution) and is preserved only as an
    # audit field in the recon report.
    if paper_execution_requested and not is_weekend:
        try:
            alpaca = AlpacaBroker.from_env()
            # Broker equity authority: in paper mode pull live account equity so
            # target-dollar sizing uses current portfolio value, not a stale snapshot.
            if paper_execution_requested:
                try:
                    _acct = alpaca.get_account() or {}
                    planning_account_snapshot = dict(_acct)
                    _live_equity = _coerce_float(_acct.get("equity") or _acct.get("portfolio_value"))
                    _live_cash = _coerce_float(_acct.get("cash"))
                    if _live_equity is not None and _live_equity > 0:
                        logger.info(
                            "[HOLDINGS_SYNC] Live broker equity=%.2f cash=%s "
                            "replaces ledger equity=%.2f for portfolio sizing",
                            _live_equity,
                            _live_cash,
                            equity_prev,
                        )
                        equity_prev = _live_equity
                        if _live_cash is not None:
                            cash_prev = _live_cash
                except Exception as _acct_exc:
                    logger.warning(
                        "[HOLDINGS_SYNC] Failed to fetch live broker equity: %s "
                        "— continuing with ledger equity=%.2f",
                        _acct_exc,
                        equity_prev,
                    )
            broker_positions = alpaca.get_positions()
            if broker_positions:
                broker_holdings_df, _ = _alpaca_positions_to_holdings(broker_positions, "main")
                if not broker_holdings_df.empty:
                    logger.info(
                        "[HOLDINGS_SYNC] Synced %d positions from Alpaca broker",
                        len(broker_holdings_df)
                    )
                    # Replace holdings_prev with broker positions for diff engine
                    holdings_prev = broker_holdings_df[["ticker", "shares"]].copy()
        except Exception as exc:
            logger.warning(
                "[HOLDINGS_SYNC] Failed to sync from Alpaca broker: %s (continuing with ledger holdings)",
                exc
            )

    mkt = market_session_status(run_date=run_date, now_et=now_et, cutoff_time_et=cfg.market_cutoff_time_et)
    market_allow = bool(mkt.is_open_now)
    logger.info(
        "[MARKET_GUARD] now=%s tz=%s trade_date=%s calendar=%s classification=%s allow=%s session_open_et=%s session_close_et=%s next_open_et=%s",
        now_et.isoformat(),
        str(now_et.tzinfo),
        run_date,
        mkt.calendar_name,
        mkt.reason,
        market_allow,
        mkt.session_open_et.isoformat() if mkt.session_open_et else "n/a",
        mkt.session_close_et.isoformat() if mkt.session_close_et else "n/a",
        mkt.next_open_et.isoformat() if mkt.next_open_et else "n/a",
    )

    runtime_constraints = constraints or {}
    use_precomputed_trade_plan = bool(precomputed_trade_plan)
    cash_target_weight_default = float(
        runtime_constraints.get("cash_target_weight", cfg.cash_target_weight_default)
    )
    exit_only = _is_truthy(runtime_constraints.get("exit_only")) or _is_truthy(
        os.getenv("EXIT_ONLY")
    )

    breaker_mode = str(os.getenv("BREAKER_MODE", "off")).strip().lower()
    breaker_mode_source = "env" if os.getenv("BREAKER_MODE") is not None else "default"
    try:
        with open(signals_path, "r", encoding="utf-8") as f:
            signal_obj = json.load(f)
        if isinstance(signal_obj, dict):
            signal_breaker = signal_obj.get("breaker") or {}
            signal_breaker_mode = signal_breaker.get("mode")
            if signal_breaker_mode is not None:
                breaker_mode = str(signal_breaker_mode).strip().lower()
                breaker_mode_source = "signals"
    except Exception:
        pass
    if breaker_mode not in {"off", "partial", "lock"}:
        breaker_mode = "off"
        breaker_mode_source = "default"

    targets, target_cash_weight, snapshot_date, asof_date = load_targets(
        signals_path,
        cash_target_weight_default=cash_target_weight_default,
    )
    investable_multiplier = max(0.0, 1.0 - float(target_cash_weight))
    breaker_suppresses_trading = bool(breaker_mode == "lock" and investable_multiplier <= 1e-9)
    logger.info(
        "[BREAKER] mode=%s source=%s target_cash_weight=%.6f investable_multiplier=%.6f suppresses_trading=%s",
        breaker_mode,
        breaker_mode_source,
        float(target_cash_weight),
        float(investable_multiplier),
        breaker_suppresses_trading,
    )

    if exit_only:
        logger.info("[SCHEDULE] Explicit exit-only mode enabled run_date=%s", run_date)
        if not holdings_prev.empty:
            held = set(holdings_prev["ticker"].astype(str).str.upper())
            targets = targets[targets["ticker"].astype(str).str.upper().isin(held)].copy()
        else:
            targets = targets.iloc[0:0].copy()
        # Preserve model/snapshot cash target (e.g., breaker overlays) in exit-only mode.
        target_cash_weight = max(0.0, min(1.0, float(target_cash_weight)))
    if snapshot_date and snapshot_date != run_date:
        raise RuntimeError(f"[HALT] signal_date_mismatch snapshot_date={snapshot_date} execution_date={run_date}")

    planning_mode = bool(plan_only or (not mkt.is_open_now))

    blocked = False
    blocked_reasons: List[str] = []

    prices_open = pd.Series(dtype=float)
    prev_closes = pd.Series(dtype=float)
    pricing_source = "OPEN"
    pricing_asof = run_date
    pricing_series = pd.Series(dtype=float)
    tickers = sorted(set(targets["ticker"].tolist() + [cfg.benchmark_ticker]))
    if not holdings_prev.empty:
        tickers = sorted(set(tickers + holdings_prev["ticker"].tolist()))

    if not planning_mode:
        prices_df = fetch_open_prices_yfinance(tickers, run_date=run_date)
        stale_rows = prices_df[prices_df["price_date"].astype(str) < run_date]
        if not stale_rows.empty:
            last_px_date = str(stale_rows["price_date"].min())
            raise RuntimeError(f"[HALT] stale_prices detected (last_price_date={last_px_date})")
        prices_open = prices_df.set_index("ticker")["open"].astype(float)
        if cfg.require_benchmark_price and cfg.benchmark_ticker not in prices_open.index:
            raise RuntimeError(f"[HALT] benchmark_missing ticker={cfg.benchmark_ticker}")

    prev_close_asof = prev_trading_day(run_date)
    prev_close_df = fetch_prev_closes_yfinance(tickers, asof_date=prev_close_asof)
    prev_closes = (
        prev_close_df.set_index("ticker")["prev_close"].astype(float)
        if not prev_close_df.empty
        else pd.Series(dtype=float)
    )
    if planning_mode:
        pricing_source = "PREV_CLOSE"
        pricing_series = prev_closes
        pricing_asof = str(prev_close_df["price_date"].max()) if not prev_close_df.empty else prev_close_asof
    else:
        pricing_source = "OPEN"
        pricing_series = prices_open
        pricing_asof = run_date

    validation_ok, validation_reasons, validation_details = validate_open_window(
        trade_date=run_date,
        signals_meta={"trade_date": snapshot_date, "asof_date": asof_date},
        prices_open=prices_open,
        prev_closes=prev_closes,
        weights=targets,
        signals_path=signals_path,
        planning_mode=planning_mode,
    )
    blocked_tickers = {
        str(ticker): list(reasons)
        for ticker, reasons in (validation_details.get("blocked_tickers") or {}).items()
    }

    if not validation_ok:
        blocked = True
        logger.error(
            '[PAPER][VALIDATION] FAIL trade_date=%s signals=%s asof=%s cutoff=%s reasons="%s"',
            run_date,
            signals_path,
            validation_details.get("asof_date"),
            validation_details.get("cutoff_date"),
            "; ".join(validation_reasons),
        )
        blocked_reasons.extend([f"validation:{reason}" for reason in validation_reasons])
    else:
        if blocked_tickers:
            logger.warning(
                '[PAPER][VALIDATION] PARTIAL trade_date=%s signals=%s asof=%s cutoff=%s blocked_tickers="%s"',
                run_date,
                signals_path,
                validation_details.get("asof_date"),
                validation_details.get("cutoff_date"),
                "; ".join(
                    f"{ticker}:{','.join(reasons)}" for ticker, reasons in sorted(blocked_tickers.items())
                ),
            )
            blocked_reasons.extend(
                [f"validation:{reason}:{ticker}" for ticker, reasons in sorted(blocked_tickers.items()) for reason in reasons]
            )
        else:
            logger.info(
                "[PAPER][VALIDATION] PASS trade_date=%s signals=%s asof=%s cutoff=%s",
                run_date,
                signals_path,
                validation_details.get("asof_date"),
                validation_details.get("cutoff_date"),
            )

    investable_weight = max(0.0, 1.0 - target_cash_weight)
    if not blocked:
        priced = set(pricing_series.index.tolist())
        targets = targets[targets["ticker"].isin(priced)].copy()
        if blocked_tickers:
            targets = targets[~targets["ticker"].astype(str).isin(set(blocked_tickers.keys()))].copy()
        wsum = float(targets["target_weight"].sum())
        if investable_weight <= 1e-12:
            targets = targets.iloc[0:0].copy()
        else:
            if wsum <= 0:
                raise RuntimeError("After dropping missing-priced/blocked tickers, no targets remain.")
            targets["target_weight"] = targets["target_weight"] / wsum * investable_weight

    market_closed_or_not_session = not bool(mkt.is_open_now)
    if market_closed_or_not_session:
        logger.info("HALT — MARKET CLOSED (%s)", mkt.reason)
        blocked_reasons.append(f"market_guard:{mkt.reason}")
        logger.info(
            "[PAPER][VALIDATION] FAIL trade_date=%s reason=%s",
            run_date,
            f"market_closed_or_not_session:{mkt.reason}",
        )
    else:
        logger.info(
            "[PAPER][VALIDATION] PASS trade_date=%s reason=%s",
            run_date,
            "market_open",
        )

    trade_meta = {
        "target_cash_weight": float(target_cash_weight),
        "target_investable_dollars": float(equity_prev * investable_weight),
        "scaled_tickers": [],
        "overspend_prevented": False,
    }
    if not blocked:
        if use_precomputed_trade_plan:
            trades = _normalize_precomputed_trade_plan(precomputed_trade_plan)
            trade_meta = {
                "target_cash_weight": float(target_cash_weight),
                "target_investable_dollars": float(equity_prev * investable_weight),
                "scaled_tickers": [],
                "overspend_prevented": False,
                "precomputed_trade_plan_used": True,
            }
            logger.info(
                "[PRECOMPUTE_EXECUTION] using explicit precomputed trade plan orders=%d",
                int(len(trades)),
            )
        else:
            trades, trade_meta = build_rebalance_trades(
                holdings=holdings_prev,
                targets=targets,
                prices=pricing_series,
                total_equity=equity_prev,
                starting_cash=cash_prev,
                target_cash_weight=target_cash_weight,
                cfg=cfg,
            )
    else:
        trades = pd.DataFrame(columns=["ticker", "side", "shares", "price", "slippage_cost", "notional", "reason"])

    logger.info(
        "[PAPER] cash_target=%.2f%% investable=$%.2f equity=$%.2f",
        100.0 * float(target_cash_weight),
        float(trade_meta.get("target_investable_dollars", equity_prev * investable_weight)),
        float(equity_prev),
    )

    risk_meta = {
        "turnover_requested": float(trades["notional"].sum()) if trades is not None and not trades.empty else 0.0,
        "turnover_cap": float(equity_prev * cfg.max_turnover_pct) if cfg.max_turnover_pct > 0 else float("inf"),
        "turnover_scaled": False,
        "turnover_scale": 1.0,
    }
    if not blocked:
        if use_precomputed_trade_plan:
            logger.info(
                "[PRECOMPUTE_EXECUTION] bypassing risk guard mutation for explicit precomputed trade plan"
            )
        else:
            trades, risk_blocked, hard_stop = apply_risk_guards(trades, equity_prev, cfg)
            risk_meta.update((trades.attrs or {}).get("risk_meta", {}))
            blocked_reasons.extend(risk_blocked)
            if hard_stop:
                logger.error("[HALT] risk guard hard stop: %s", "; ".join(risk_blocked))
                logger.info(
                    "[PAPER][VALIDATION] FAIL trade_date=%s reason=%s",
                    run_date,
                    "risk_guard_hard_stop",
                )
                blocked = True
                trades = pd.DataFrame(columns=["ticker", "side", "shares", "price", "slippage_cost", "notional", "reason"])

    if breaker_mode == "lock" and trades is not None and not trades.empty and not use_precomputed_trade_plan:
        side_series = (
            trades["side"].astype(str).str.upper()
            if "side" in trades.columns
            else pd.Series([""] * len(trades), index=trades.index, dtype=str)
        )
        sell_mask = side_series.isin({"SELL", "CLOSE", "REDUCE"})
        dropped_non_sell = int((~sell_mask).sum())
        trades = trades.loc[sell_mask].copy()
        logger.info(
            "[BREAKER] lock mode sells-only filter applied dropped_non_sell=%d kept=%d",
            dropped_non_sell,
            int(len(trades)),
        )
        if dropped_non_sell > 0:
            blocked_reasons.append(f"breaker_lock_filtered_non_sell:{dropped_non_sell}")

    capital_budget_meta = _default_capital_budget_meta()
    pdt_pretrade_meta = _default_pdt_pretrade_meta()
    if paper_execution_requested and planning_account_snapshot is not None and not use_precomputed_trade_plan:
        trades, pdt_pretrade_meta, pdt_blocked_reasons = _apply_pdt_pretrade_guard(
            trades,
            planning_account_snapshot=planning_account_snapshot,
            sent_ledger_path=cfg.sent_ledger_path,
            run_date=run_date,
        )
        if pdt_blocked_reasons:
            blocked_reasons.extend(pdt_blocked_reasons)
            logger.warning(
                "[PDT_PREFLIGHT] status=%s deferred_orders=%d deferred_symbols=%s message=%s",
                pdt_pretrade_meta.get("broker_pdt_risk_status"),
                int(pdt_pretrade_meta.get("deferred_orders_count") or 0),
                ",".join(list(pdt_pretrade_meta.get("deferred_symbols") or [])) or "none",
                pdt_pretrade_meta.get("broker_pdt_warning_message") or "none",
            )
        elif pdt_pretrade_meta.get("broker_pdt_warning_message"):
            logger.warning(
                "[PDT_PREFLIGHT] status=%s message=%s",
                pdt_pretrade_meta.get("broker_pdt_risk_status"),
                pdt_pretrade_meta.get("broker_pdt_warning_message"),
            )

    if paper_execution_requested and planning_account_snapshot is not None and not use_precomputed_trade_plan:
        requested_buy_notional = (
            float(
                trades.loc[
                    trades["side"].astype(str).str.upper() == "BUY",
                    "notional",
                ].astype(float).sum()
            )
            if trades is not None and not trades.empty and "side" in trades.columns and "notional" in trades.columns
            else 0.0
        )
        expected_sell_proceeds = (
            float(
                trades.loc[
                    trades["side"].astype(str).str.upper().isin({"SELL", "CLOSE", "REDUCE"}),
                    "notional",
                ].astype(float).sum()
            )
            if trades is not None and not trades.empty and "side" in trades.columns and "notional" in trades.columns
            else 0.0
        )
        capital_budget_meta = _build_capital_budget(
            broker_cash=planning_account_snapshot.get("cash"),
            broker_equity=planning_account_snapshot.get("equity") or planning_account_snapshot.get("portfolio_value"),
            broker_buying_power=planning_account_snapshot.get("buying_power"),
            expected_sell_proceeds=expected_sell_proceeds,
            requested_buy_notional=requested_buy_notional,
        )
        trades, capital_budget_meta = _apply_capital_budget_to_trades(
            trades,
            cfg,
            capital_budget_meta,
        )
        reserve_policy = capital_budget_meta.get("reserve_cash_policy") or {}
        log_level = logging.WARNING if capital_budget_meta.get("capital_constraint_triggered") else logging.INFO
        logger.log(
            log_level,
            "[CAPITAL_BUDGET] broker_cash=%s equity=%s buying_power=%s reserve_cash=%.2f expected_sells=%.2f expected_sells_conservative=%.2f requested_buys=%.2f allowed_buys=%.2f clipped_or_deferred=%d triggered=%s",
            capital_budget_meta.get("broker_cash_at_planning"),
            capital_budget_meta.get("broker_equity_at_planning"),
            capital_budget_meta.get("broker_buying_power_at_planning"),
            float(reserve_policy.get("reserve_cash") or 0.0),
            float(capital_budget_meta.get("expected_sell_proceeds") or 0.0),
            float(capital_budget_meta.get("expected_sell_proceeds_conservative") or 0.0),
            float(capital_budget_meta.get("requested_buy_notional") or 0.0),
            float(capital_budget_meta.get("allowed_buy_notional") or 0.0),
            int(capital_budget_meta.get("clipped_or_deferred_buys_count") or 0),
            bool(capital_budget_meta.get("capital_constraint_triggered")),
        )

    executable_trades, execution_filter_stats = _normalize_and_filter_executable_trades(trades, cfg)
    execution_trades = executable_trades.copy()
    trade_plan_trades = execution_trades.copy()
    filter_drop_reasons: Counter[str] = Counter()
    if int(execution_filter_stats.get("dropped_zero_shares", 0)) > 0:
        filter_drop_reasons["zero_shares"] = int(execution_filter_stats.get("dropped_zero_shares", 0))
    if int(execution_filter_stats.get("dropped_min_notional", 0)) > 0:
        filter_drop_reasons["min_notional"] = int(execution_filter_stats.get("dropped_min_notional", 0))

    # ── Risk controls preflight ────────────────────────────────────
    # Runs circuit-breaker and per-position cap checks before any
    # order is submitted.  Non-blocking on error (warnings only).
    # Circuit-breaker: blocks ALL new buys if drawdown ≥ threshold.
    # Position caps: scales down any single BUY that exceeds max_pct.
    _rc_equity = float(
        (planning_account_snapshot or {}).get("equity")
        or (planning_account_snapshot or {}).get("portfolio_value")
        or equity_prev
        or 0.0
    )
    if use_precomputed_trade_plan:
        _rc_result = {"drawdown": 0.0, "blocked": False, "trimmed_tickers": []}
        logger.info(
            "[PRECOMPUTE_EXECUTION] bypassing risk-controls preflight mutation for explicit precomputed trade plan"
        )
    else:
        _rc_result = _risk_controls_preflight(execution_trades, _rc_equity, ledger_path)
        if _rc_result.get("blocked"):
            blocked = True
            _cb_reason = str(_rc_result.get("block_reason") or "risk_controls_circuit_breaker")
            blocked_reasons.append(_cb_reason)
            execution_enabled = False
            logger.warning("[RISK_CONTROLS] Execution blocked by circuit breaker: %s", _cb_reason)
        elif _rc_result.get("trimmed_tickers"):
            logger.info(
                "[RISK_CONTROLS] Position caps applied to: %s",
                ", ".join(str(t) for t in _rc_result["trimmed_tickers"]),
            )
        logger.info(
            "[RISK_CONTROLS] drawdown=%.2f%% blocked=%s trimmed=%d",
            float(_rc_result.get("drawdown", 0.0)) * 100,
            bool(_rc_result.get("blocked")),
            len(_rc_result.get("trimmed_tickers") or []),
        )
    # ─────────────────────────────────────────────────────────────

    run_id = _run_id(run_date, cfg)
    shadow_orders_path = None
    alpaca_orders_path = None
    alpaca_submissions: List[Dict[str, object]] = []
    alpaca_submission_summary: Dict[str, object] = {
        "initial_intended_orders": 0,
        "post_local_idempotent_orders": 0,
        "submitted_orders": 0,
        "remote_existing_orders": 0,
    }
    idempotent_drop_reasons: Counter[str] = Counter()
    submit_attempts = 0
    submit_success = 0
    submit_failed = 0
    idempotent_skips: List[str] = []
    orders: List[Dict[str, object]] = []
    sent_ledger_path: str = cfg.sent_ledger_path
    alpaca_account_snapshot: Dict[str, object] | None = None
    alpaca_positions_snapshot: List[Dict[str, object]] = []
    postsell_account_snapshot: Dict[str, object] | None = None
    postsell_account_snapshot_path: str | None = None
    postsell_cash_confirmed: float | None = None
    postsell_buying_power_confirmed: float | None = None
    sell_phase_status: str | None = None
    sell_phase_completion_reason: str | None = None
    sell_phase_observed_statuses: Dict[str, str] = {}
    sell_phase_polls: int = 0
    buy_budget_computed: float | None = None
    budget_skipped_orders: List[Dict[str, object]] = []
    posttrade_account_snapshot_path: str | None = None
    posttrade_positions_snapshot_path: str | None = None
    posttrade_recon_path: str | None = None
    posttrade_recon_status: str | None = None
    posttrade_repair_suggestions: List[str] = []
    posttrade_affected_symbols: List[str] = []
    posttrade_duplicate_fill_suspicions_count: int = 0
    execution_outcome: str | None = None
    execution_reason: str | None = None
    execution_halt_reason: str | None = None
    cash_rebalance_status: str | None = None
    broker_reject_status: str | None = None
    broker_reject_message: str | None = None
    artifact_failure_stage: str | None = None
    artifact_failure_message: str | None = None
    halt_remaining_buys = False
    execution_submitted_symbols: List[str] = []
    execution_enabled = bool(
        paper_execution_requested and mkt.is_open_now and not plan_only and not blocked and not is_weekend
    )
    logger.info(
        "[EXEC_GATE] mode=%s allow=%s market_open=%s plan_only=%s blocked=%s is_weekend=%s reason=%s",
        mode,
        execution_enabled,
        bool(mkt.is_open_now),
        bool(plan_only),
        bool(blocked),
        bool(is_weekend),
        mkt.reason,
    )

    # Persist intended orders artifact before any submission attempt.
    # Written unconditionally so post-run diagnosis can always distinguish:
    #   orders_intended_count=0                      => strategy had no trades
    #   orders_intended_count>0 + execution_blocked  => gate prevented submission
    #   orders_intended_count>0 + execution_enabled  => orders were submitted
    try:
        _write_intended_orders_artifact(
            run_date=run_date,
            run_id=run_id,
            execution_trades=execution_trades,
            execution_enabled=execution_enabled,
            block_reasons=list(blocked_reasons),
            capital_budget=capital_budget_meta,
        )
    except Exception as _art_exc:
        logger.warning("[INTENDED_ORDERS] Failed to write artifact: %s", _art_exc)

    if execution_enabled:
        initial_orders = _build_shadow_orders(execution_trades, run_id)
        alpaca_submission_summary["initial_intended_orders"] = int(len(initial_orders))
        same_day_locked_orders = 0
        same_day_prior_run_ids: List[str] = []
        allow_partial_buy_continuation = _is_truthy(
            os.getenv("ALLOW_PARTIAL_BUY_CONTINUATION")
        )
        if paper_execution_requested:
            same_day_locked_orders, same_day_prior_run_ids = _same_day_submission_lock(
                sent_ledger_path,
                run_date,
            )
        if same_day_locked_orders > 0 and not allow_partial_buy_continuation:
            blocked = True
            execution_enabled = False
            lock_reason = (
                f"same_day_submission_lock:{run_date}:recorded_orders={same_day_locked_orders}"
            )
            if same_day_prior_run_ids:
                lock_reason = f"{lock_reason}:prior_runs={','.join(same_day_prior_run_ids)}"
            blocked_reasons.append(lock_reason)
            alpaca_submission_summary["same_day_submission_lock"] = True
            alpaca_submission_summary["same_day_recorded_orders"] = int(same_day_locked_orders)
            alpaca_submission_summary["same_day_prior_run_ids"] = list(same_day_prior_run_ids)
            orders = []
            orders_for_execution = []
            logger.warning(
                "[ALPACA][LOCK] refusing same-day duplicate submission trade_date=%s recorded_orders=%d prior_runs=%s ledger=%s",
                run_date,
                int(same_day_locked_orders),
                ",".join(same_day_prior_run_ids) if same_day_prior_run_ids else "unknown",
                sent_ledger_path,
            )
        else:
            if same_day_locked_orders > 0 and allow_partial_buy_continuation:
                alpaca_submission_summary["same_day_submission_lock_bypassed"] = True
                logger.warning(
                    "[ALPACA][CONTINUATION] bypassing same-day submission lock trade_date=%s recorded_orders=%d prior_runs=%s",
                    run_date,
                    int(same_day_locked_orders),
                    ",".join(same_day_prior_run_ids) if same_day_prior_run_ids else "unknown",
                )
            orders, local_idempotent_skips = _filter_idempotent_orders(initial_orders, sent_ledger_path)
            idempotent_skips.extend(local_idempotent_skips)
            if local_idempotent_skips:
                idempotent_drop_reasons["local_ledger"] += int(len(local_idempotent_skips))
            alpaca_submission_summary["local_idempotent_skips"] = int(len(local_idempotent_skips))
            alpaca_submission_summary["post_local_idempotent_orders"] = int(len(orders))
            submission_metadata: Dict[str, Dict[str, object]] = {}
            orders_for_execution = list(orders)

        if paper_execution_requested and execution_enabled:
            alpaca = AlpacaBroker.from_env()
            broker_cls = alpaca.__class__
            logger.info(
                "[BROKER] selected=%s.%s mode=%s env_mode=%s env_trading_mode=%s",
                broker_cls.__module__,
                broker_cls.__name__,
                mode,
                env_mode or "n/a",
                env_trading_mode or "n/a",
            )
            if paper_execution_requested and not isinstance(alpaca, AlpacaBroker):
                raise RuntimeError(
                    f"[INVARIANT] Expected AlpacaBroker in paper mode, got {broker_cls.__module__}.{broker_cls.__name__}"
                )
            submitted_orders: List[Dict[str, object]] = []
            remote_existing_orders: List[Dict[str, object]] = []
            sell_orders, buy_orders = _split_orders_for_execution(orders)
            orders_by_id = {
                str(order.get("order_id") or ""): dict(order)
                for order in orders
                if str(order.get("order_id") or "")
            }
            logger.info(
                "[ALPACA][PHASE] sell_phase_orders=%d buy_phase_orders=%d",
                len(sell_orders),
                len(buy_orders),
            )
            try:
                phase_submitted, phase_existing, phase_attempts, phase_success, phase_failed = _submit_alpaca_orders(
                    alpaca=alpaca,
                    orders=sell_orders,
                    run_date=run_date,
                    alpaca_submissions=alpaca_submissions,
                    submission_metadata=submission_metadata,
                    idempotent_skips=idempotent_skips,
                    idempotent_drop_reasons=idempotent_drop_reasons,
                    alpaca_submission_summary=alpaca_submission_summary,
                )
                submitted_orders.extend(phase_submitted)
                remote_existing_orders.extend(phase_existing)
                submit_attempts += phase_attempts
                submit_success += phase_success
                submit_failed += phase_failed
                alpaca_submission_summary["sell_phase_submitted"] = int(len(phase_submitted))
                sell_phase_result = _wait_for_alpaca_sell_phase_completion(
                    alpaca=alpaca,
                    sell_orders=sell_orders,
                    submission_metadata=submission_metadata,
                    starting_positions=_holdings_to_quantity_map(holdings_prev),
                )
                sell_phase_status = str(sell_phase_result.get("status") or "UNKNOWN")
                sell_phase_completion_reason = str(
                    sell_phase_result.get("completion_reason") or "unknown"
                )
                sell_phase_observed_statuses = {
                    str(k): str(v)
                    for k, v in dict(sell_phase_result.get("observed_statuses") or {}).items()
                }
                sell_phase_polls = int(sell_phase_result.get("polls") or 0)
                alpaca_submission_summary["sell_phase_status"] = sell_phase_status
                alpaca_submission_summary["sell_phase_completion_reason"] = sell_phase_completion_reason
                alpaca_submission_summary["sell_phase_polls"] = sell_phase_polls
                alpaca_submission_summary["sell_phase_observed_statuses"] = dict(
                    sell_phase_observed_statuses
                )

                postsell_account_snapshot = dict(
                    json_safe_primitive(sell_phase_result.get("account") or {})
                )
                if postsell_account_snapshot:
                    postsell_cash_confirmed = _coerce_float(
                        postsell_account_snapshot.get("cash"),
                        None,
                    )
                    postsell_buying_power_confirmed = _coerce_float(
                        postsell_account_snapshot.get("buying_power"),
                        postsell_cash_confirmed,
                    )
                    try:
                        postsell_account_snapshot_path = _write_broker_account_snapshot(
                            "postsell_account_snapshot.json",
                            run_date,
                            postsell_account_snapshot,
                        )
                        buy_budget_computed = _compute_buy_budget(postsell_account_snapshot, cfg)
                        alpaca_submission_summary["postsell_cash_confirmed"] = postsell_cash_confirmed
                        alpaca_submission_summary["buy_budget_computed"] = buy_budget_computed
                        logger.info(
                            "[ALPACA][POSTSELL] status=%s reason=%s snapshot=%s cash=%s buying_power=%s buy_budget=%.2f",
                            sell_phase_status,
                            sell_phase_completion_reason,
                            postsell_account_snapshot_path,
                            postsell_cash_confirmed,
                            postsell_buying_power_confirmed,
                            float(buy_budget_computed or 0.0),
                        )
                    except Exception as exc:
                        execution_outcome = EXECUTION_OUTCOME_POST_SUBMIT_ARTIFACT_FAILURE
                        execution_reason = "post_sell_account_snapshot_write_failed"
                        execution_halt_reason = (
                            f"{EXECUTION_OUTCOME_POST_SUBMIT_ARTIFACT_FAILURE}:"
                            "post_sell_account_snapshot_write_failed:"
                            f"{CASH_REBALANCE_INCOMPLETE}"
                        )
                        cash_rebalance_status = CASH_REBALANCE_INCOMPLETE
                        artifact_failure_stage = "post_sell_account_snapshot"
                        artifact_failure_message = str(exc)
                        halt_remaining_buys = True
                        budget_skipped_orders = list(buy_orders)
                        buy_budget_computed = 0.0
                        alpaca_submission_summary["postsell_cash_confirmed"] = postsell_cash_confirmed
                        alpaca_submission_summary["buy_budget_computed"] = buy_budget_computed
                        alpaca_submission_summary["artifact_failure_stage"] = artifact_failure_stage
                        alpaca_submission_summary["artifact_failure_message"] = artifact_failure_message
                        alpaca_submission_summary["execution_outcome"] = execution_outcome
                        alpaca_submission_summary["execution_reason"] = execution_reason
                        alpaca_submission_summary["cash_rebalance_status"] = cash_rebalance_status
                        alpaca_submission_summary["halt_remaining_buys"] = halt_remaining_buys
                        alpaca_submission_summary["buy_phase_block_reason"] = execution_reason
                        blocked_reasons.append(execution_halt_reason)
                        logger.error(
                            "[ALPACA][ARTIFACT_ABORT] outcome=%s reason=%s submitted=%d stage=%s message=%s",
                            execution_outcome,
                            execution_reason,
                            int(len(submitted_orders)),
                            artifact_failure_stage,
                            artifact_failure_message,
                        )
                else:
                    buy_budget_computed = 0.0
                    alpaca_submission_summary["postsell_cash_confirmed"] = postsell_cash_confirmed
                    alpaca_submission_summary["buy_budget_computed"] = buy_budget_computed

                buy_phase_allowed = bool(
                    execution_outcome is None and sell_phase_status in {"COMPLETED", "NO_SELLS"}
                )
                buy_phase_block_reason = execution_reason if execution_outcome else None
                if execution_outcome == EXECUTION_OUTCOME_POST_SUBMIT_ARTIFACT_FAILURE:
                    budget_skipped_orders = list(buy_orders)
                    logger.warning(
                        "[ALPACA][BUY_PHASE] blocked reason=%s planned_orders=%d",
                        buy_phase_block_reason,
                        len(buy_orders),
                    )
                elif not buy_phase_allowed:
                    buy_phase_block_reason = f"sell_phase_{str(sell_phase_status).lower()}"
                    budget_skipped_orders = list(buy_orders)
                    logger.warning(
                        "[ALPACA][BUY_PHASE] blocked reason=%s planned_orders=%d",
                        buy_phase_block_reason,
                        len(buy_orders),
                    )
                else:
                    if use_precomputed_trade_plan:
                        buy_orders_budgeted = list(buy_orders)
                        budget_skipped_orders = []
                        alpaca_submission_summary["exact_plan_buy_budget_bypassed"] = True
                        logger.info(
                            "[PRECOMPUTE_EXECUTION] exact plan preserving planned buy orders=%d despite refreshed_cash=%s computed_buy_budget=%.2f",
                            len(buy_orders_budgeted),
                            postsell_cash_confirmed,
                            float(buy_budget_computed or 0.0),
                        )
                    else:
                        buy_orders_budgeted, budget_skipped_orders = _apply_buy_budget(
                            buy_orders,
                            float(buy_budget_computed or 0.0),
                        )
                        if buy_orders and not buy_orders_budgeted:
                            buy_phase_block_reason = "post_sell_cash_below_reserve"
                        if budget_skipped_orders:
                            logger.info(
                                "[ALPACA][BUY_BUDGET] skipped_orders=%d buy_budget=%.2f",
                                len(budget_skipped_orders),
                                float(buy_budget_computed or 0.0),
                            )
                        if buy_phase_block_reason:
                            logger.warning(
                                "[ALPACA][BUY_PHASE] blocked reason=%s refreshed_cash=%s planned_orders=%d",
                                buy_phase_block_reason,
                                postsell_cash_confirmed,
                                len(buy_orders),
                            )
                        else:
                            logger.info(
                                "[ALPACA][BUY_PHASE] allowed refreshed_cash=%s buy_budget=%.2f planned_orders=%d",
                                postsell_cash_confirmed,
                                float(buy_budget_computed or 0.0),
                                len(buy_orders_budgeted),
                            )
                if not buy_phase_allowed:
                    buy_orders_budgeted = []
                alpaca_submission_summary["budget_skipped_orders"] = int(len(budget_skipped_orders))
                alpaca_submission_summary["buy_phase_planned"] = int(len(buy_orders_budgeted))
                if buy_phase_block_reason:
                    alpaca_submission_summary["buy_phase_block_reason"] = str(buy_phase_block_reason)

                phase_submitted, phase_existing, phase_attempts, phase_success, phase_failed = _submit_alpaca_orders(
                    alpaca=alpaca,
                    orders=buy_orders_budgeted,
                    run_date=run_date,
                    alpaca_submissions=alpaca_submissions,
                    submission_metadata=submission_metadata,
                    idempotent_skips=idempotent_skips,
                    idempotent_drop_reasons=idempotent_drop_reasons,
                    alpaca_submission_summary=alpaca_submission_summary,
                )
                submitted_orders.extend(phase_submitted)
                remote_existing_orders.extend(phase_existing)
                submit_attempts += phase_attempts
                submit_success += phase_success
                submit_failed += phase_failed
                alpaca_submission_summary["buy_phase_submitted"] = int(len(phase_submitted))
            except AlpacaSubmissionRejectError as exc:
                submit_attempts += int(getattr(exc, "attempted_submissions", 0) or 0)
                submit_success += int(getattr(exc, "successful_submissions", 0) or 0)
                submit_failed += int(getattr(exc, "failed_submissions", 0) or 0)
                policy = broker_reject_policy_outcome(
                    str(exc.classification or ""),
                    successful_submissions=submit_success,
                )
                execution_outcome = str(policy.get("execution_outcome") or EXECUTION_OUTCOME_PARTIAL_BROKER_ABORT)
                execution_reason = str(policy.get("execution_reason") or "").strip() or str(exc.classification or "").lower()
                execution_halt_reason = str(policy.get("halt_reason") or "")
                cash_rebalance_status = str(policy.get("cash_rebalance_status") or "") or None
                broker_reject_status = str(exc.classification or "")
                broker_reject_message = str(exc.broker_message or exc.raw_message or exc)
                halt_remaining_buys = bool(policy.get("halt_remaining_buys"))
                submitted_orders = [
                    dict(orders_by_id[order_id])
                    for order_id in [str(item.get("order_id") or "") for item in alpaca_submissions]
                    if order_id in orders_by_id
                ]
                orders_for_execution = list(submitted_orders)
                orders = list(submitted_orders)
                budget_skipped_orders = list(buy_orders)
                if halt_remaining_buys:
                    alpaca_submission_summary["buy_phase_block_reason"] = execution_reason
                alpaca_submission_summary["broker_reject_status"] = broker_reject_status
                alpaca_submission_summary["broker_reject_message"] = broker_reject_message
                alpaca_submission_summary["execution_outcome"] = execution_outcome
                alpaca_submission_summary["execution_reason"] = execution_reason
                alpaca_submission_summary["cash_rebalance_status"] = cash_rebalance_status
                alpaca_submission_summary["halt_remaining_buys"] = halt_remaining_buys
                blocked_reasons.append(execution_halt_reason)
                logger.error(
                    "[ALPACA][ABORT] outcome=%s reason=%s submitted=%d rejected_symbol=%s rejected_side=%s message=%s",
                    execution_outcome,
                    execution_reason,
                    int(len(submitted_orders)),
                    exc.symbol or "unknown",
                    exc.side or "unknown",
                    broker_reject_message,
                )

            orders_for_execution = list(submitted_orders)
            alpaca_submission_summary["submitted_orders"] = int(len(submitted_orders))

            persist_orders = submitted_orders + remote_existing_orders
            if persist_orders:
                _persist_sent_orders(
                    persist_orders,
                    sent_ledger_path,
                    run_date,
                    run_id,
                    order_metadata=submission_metadata,
                )

            try:
                posttrade_state = _capture_alpaca_posttrade_state(
                    alpaca=alpaca,
                    run_date=run_date,
                    holdings_prev=holdings_prev,
                    submitted_orders=submitted_orders,
                    cfg=cfg,
                    raise_on_failure=execution_outcome is None,
                )
            except Exception as exc:
                if submitted_orders:
                    execution_outcome = (
                        execution_outcome
                        or EXECUTION_OUTCOME_POST_SUBMIT_ARTIFACT_FAILURE
                    )
                    execution_reason = execution_reason or "posttrade_state_capture_failed"
                    execution_halt_reason = execution_halt_reason or (
                        f"{EXECUTION_OUTCOME_POST_SUBMIT_ARTIFACT_FAILURE}:"
                        "posttrade_state_capture_failed"
                    )
                    artifact_failure_stage = artifact_failure_stage or "posttrade_state_capture"
                    artifact_failure_message = str(exc)
                    alpaca_submission_summary["artifact_failure_stage"] = artifact_failure_stage
                    alpaca_submission_summary["artifact_failure_message"] = artifact_failure_message
                    alpaca_submission_summary["execution_outcome"] = execution_outcome
                    alpaca_submission_summary["execution_reason"] = execution_reason
                    logger.error(
                        "[ALPACA][ARTIFACT_ABORT] outcome=%s reason=%s submitted=%d stage=%s message=%s",
                        execution_outcome,
                        execution_reason,
                        int(len(submitted_orders)),
                        artifact_failure_stage,
                        artifact_failure_message,
                    )
                    posttrade_state = {}
                else:
                    raise
            if posttrade_state:
                alpaca_account_snapshot = dict(posttrade_state.get("alpaca_account_snapshot") or {})
                alpaca_positions_snapshot = list(posttrade_state.get("alpaca_positions_snapshot") or [])
                posttrade_account_snapshot_path = str(
                    posttrade_state.get("posttrade_account_snapshot_path") or ""
                ) or None
                posttrade_positions_snapshot_path = str(
                    posttrade_state.get("posttrade_positions_snapshot_path") or ""
                ) or None
                posttrade_recon_path = str(posttrade_state.get("posttrade_recon_path") or "") or None
                posttrade_recon_status = str(posttrade_state.get("posttrade_recon_status") or "") or None
                posttrade_repair_suggestions = list(posttrade_state.get("posttrade_repair_suggestions") or [])
                posttrade_affected_symbols = list(posttrade_state.get("posttrade_affected_symbols") or [])
                posttrade_duplicate_fill_suspicions_count = int(
                    posttrade_state.get("posttrade_duplicate_fill_suspicions_count") or 0
                )

            if (
                execution_outcome in {
                    EXECUTION_OUTCOME_PARTIAL_BROKER_ABORT,
                    EXECUTION_OUTCOME_POST_SUBMIT_ARTIFACT_FAILURE,
                }
                and cash_rebalance_status == CASH_REBALANCE_INCOMPLETE
                and CASH_REBALANCE_INCOMPLETE not in posttrade_repair_suggestions
            ):
                posttrade_repair_suggestions.append(CASH_REBALANCE_INCOMPLETE)

            execution_submitted_symbols = sorted(
                {
                    str(item.get("ticker") or "").upper()
                    for item in alpaca_submissions
                    if str(item.get("ticker") or "").strip()
                }
            )
            alpaca_orders_path = _write_alpaca_orders(run_date, alpaca_submissions)
            orders = submitted_orders
        elif execution_enabled:
            alpaca_submission_summary["submitted_orders"] = int(len(orders))
            _persist_sent_orders(orders, sent_ledger_path, run_date, run_id)

        shadow_orders_path = _write_shadow_orders(run_date, orders)
        alpaca_submission_summary["idempotent_skips_total"] = int(len(idempotent_skips))

        allowed_order_ids = {
            str(order.get("order_id"))
            for order in orders_for_execution
            if order.get("order_id")
        }
        if not execution_trades.empty:
            kept_rows: List[Dict[str, object]] = []
            for _, tr in execution_trades.iterrows():
                ticker = str(tr.get("ticker", ""))
                side = str(tr.get("side", "")).upper()
                order_id = f"{run_id}:{ticker}:{side}"
                if order_id in allowed_order_ids:
                    kept_rows.append(tr.to_dict())
            execution_trades = (
                pd.DataFrame(kept_rows).reindex(columns=execution_trades.columns)
                if kept_rows
                else execution_trades.iloc[0:0].copy()
            )
            dropped_idempotent = int(len(executable_trades) - len(execution_trades))
            if dropped_idempotent > 0:
                execution_filter_stats["idempotent_dropped"] = dropped_idempotent
                execution_filter_stats["kept_after_idempotent"] = int(len(execution_trades))
                if int(sum(idempotent_drop_reasons.values())) < dropped_idempotent:
                    idempotent_drop_reasons["other"] += int(
                        dropped_idempotent - int(sum(idempotent_drop_reasons.values()))
                    )
                logger.info(
                    "[ORDER][IDEMPOTENT] filtered_executable_trades=%d kept_after_idempotent=%d",
                    dropped_idempotent,
                    int(len(execution_trades)),
                )

    trades_raw = int(execution_filter_stats.get("raw", 0))
    trades_after_filter = int(execution_filter_stats.get("kept", len(executable_trades)))
    trades_after_idempotency = int(len(execution_trades))
    idempotent_dropped = max(0, trades_after_filter - trades_after_idempotency)
    known_idempotent_drops = int(sum(idempotent_drop_reasons.values()))
    if idempotent_dropped > known_idempotent_drops:
        idempotent_drop_reasons["other"] += int(idempotent_dropped - known_idempotent_drops)
    exec_trace = {
        "trades_raw": trades_raw,
        "trades_after_filter": trades_after_filter,
        "trades_after_idempotency": trades_after_idempotency,
        "filter_drop_reasons": dict(filter_drop_reasons),
        "idempotent_drop_reasons": dict(idempotent_drop_reasons),
        "idempotent_dropped": int(idempotent_dropped),
        "submit_attempts": int(submit_attempts),
        "submit_success": int(submit_success),
        "submit_failed": int(submit_failed),
        "execution_enabled": bool(execution_enabled),
    }
    alpaca_submission_summary["submit_attempts"] = int(submit_attempts)
    alpaca_submission_summary["submit_success"] = int(submit_success)
    alpaca_submission_summary["submit_failed"] = int(submit_failed)
    logger.info(
        "[EXEC_TRACE] trades_raw=%d trades_after_filter=%d filter_drop_reasons=%s trades_after_idempotency=%d idempotent_dropped=%d idempotent_reasons=%s submit_attempts=%d submit_success=%d submit_failed=%d execution_enabled=%s",
        trades_raw,
        trades_after_filter,
        _format_reason_counts(filter_drop_reasons),
        trades_after_idempotency,
        int(idempotent_dropped),
        _format_reason_counts(idempotent_drop_reasons),
        int(submit_attempts),
        int(submit_success),
        int(submit_failed),
        bool(execution_enabled),
    )
    if paper_execution_requested and execution_enabled:
        if trades_after_idempotency > 0 and submit_attempts == 0:
            raise RuntimeError(
                "[INVARIANT] PAPER mode had executable trades but 0 submit attempts"
            )
        if submit_attempts > 0 and submit_success == 0:
            raise RuntimeError(
                "[INVARIANT] PAPER mode attempted submits but 0 success"
            )

    trades_out = execution_trades.copy()
    if trades_out.empty:
        trades_out = pd.DataFrame(columns=["date", "ticker", "side", "shares", "price", "slippage_cost", "notional", "reason"])
    else:
        trades_out.insert(0, "date", run_date)

    if not blocked and not planning_mode:
        append_csv(trades_out, trades_path)

    trade_plan = (
        trade_plan_trades[["ticker", "side", "shares", "price", "slippage_cost", "notional", "reason"]]
        .assign(quantity=lambda df: df["shares"])
        .to_dict("records")
        if trade_plan_trades is not None and not trade_plan_trades.empty
        else []
    )

    if not blocked and not planning_mode:
        if paper_execution_requested:
            if alpaca_account_snapshot is None:
                alpaca = AlpacaBroker.from_env()
                alpaca_account_snapshot = alpaca.get_account()
                alpaca_positions_snapshot = alpaca.get_positions()
            holdings_new, m2m = _alpaca_positions_to_holdings(
                positions=alpaca_positions_snapshot,
                sleeve=cfg.portfolio_id,
            )
            total_mv = float(m2m["market_value"].sum()) if not m2m.empty else 0.0
            cash_new = _coerce_float(
                alpaca_account_snapshot.get("cash"),
                cash_prev,
            )
            total_equity = _coerce_float(
                alpaca_account_snapshot.get("equity")
                or alpaca_account_snapshot.get("portfolio_value"),
                cash_new + total_mv,
            )
            if total_equity <= 0:
                total_equity = float(cash_new + total_mv)
            achieved_cash_weight = (cash_new / total_equity) if total_equity > 0 else 0.0
        else:
            holdings_new, cash_new = apply_trades_to_holdings(
                holdings=holdings_prev,
                targets=targets,
                trades=execution_trades,
                starting_cash=cash_prev,
            )
            m2m = mark_to_market(holdings_new, pricing_series)
            total_mv = float(m2m["market_value"].sum()) if not m2m.empty else 0.0
            total_equity = float(cash_new + total_mv)
            achieved_cash_weight = (cash_new / total_equity) if total_equity > 0 else 0.0
    else:
        holdings_new = holdings_prev.copy()
        cash_new = float(cash_prev)
        if holdings_new is not None and not holdings_new.empty and pricing_series is not None and not pricing_series.empty:
            m2m = mark_to_market(holdings_new, pricing_series)
            total_mv = float(m2m["market_value"].sum()) if not m2m.empty else 0.0
            total_equity = float(cash_new + total_mv)
            if total_equity <= 0:
                total_equity = float(equity_prev)
        else:
            m2m = pd.DataFrame(columns=["ticker", "sleeve", "shares", "price", "market_value"])
            total_mv = 0.0
            total_equity = float(equity_prev)
        achieved_cash_weight = (cash_new / total_equity) if total_equity > 0 else 0.0
    invested_dollars = total_mv
    investable_dollars = float(trade_meta.get("target_investable_dollars", equity_prev * investable_weight))
    target_cash_dollars = float(equity_prev * target_cash_weight)

    achieved_weights = {}
    if total_equity > 0 and not m2m.empty:
        achieved_weights = {
            str(r["ticker"]): float(r["market_value"]) / total_equity
            for _, r in m2m.iterrows()
        }
    exposure = compute_exposure(
        achieved_weights,
        leverage_enabled=bool((m2m["shares"] < 0).any()) if not m2m.empty and "shares" in m2m.columns else False,
        enforce_bounds=True,
    )
    gross_exposure = float(exposure["gross_exposure"])
    net_exposure = float(exposure["net_exposure"])
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

    if not blocked and not planning_mode:
        ledger_day = m2m[["ticker", "sleeve", "shares", "price", "market_value"]].copy()
        ledger_day.insert(0, "date", run_date)
        ledger_day["cash"] = cash_new
        ledger_day["total_equity"] = total_equity
        append_csv(ledger_day, ledger_path)

    broker_state = {
        "cash": cash_new,
        "positions": holdings_new,
        "equity": total_equity,
    }
    if not blocked:
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
            "model_equity": float(total_equity),
            "broker_equity": float(total_equity),
            "broker_minus_model_equity_delta": 0.0,
            "cash_delta": 0.0,
            "equity_delta": 0.0,
            "equity_tolerance": 0.0,
            "position_deltas": [],
        }

    executed_trades = int(len(trades_out)) if (trades_out is not None and not planning_mode) else 0
    turnover = (
        float(trades_out["notional"].sum())
        if executed_trades and "notional" in trades_out.columns
        else 0.0
    )
    turnover_denominator = float(equity_prev) if float(equity_prev) > 0 else float(cfg.initial_equity)
    turnover_pct = (turnover / turnover_denominator) if turnover_denominator > 0 else 0.0

    logger.info(
        "[PAPER] mode=%s market=%s orders=%d blocked=%d recon=%s",
        mode,
        "OPEN" if mkt.is_open_now else "CLOSED",
        len(orders),
        len(blocked_reasons) + len(idempotent_skips),
        recon["status"],
    )

    return {
        "date": run_date,
        "trading_mode": mode,
        "market_status": "OPEN" if mkt.is_open_now else "CLOSED",
        "market_guard": {
            "status": "OPEN" if mkt.is_open_now else "CLOSED",
            "reason": mkt.reason,
            "is_open_now": bool(mkt.is_open_now),
            "is_trading_session": bool(mkt.is_open_now),
            "is_trading_day": bool(mkt.is_trading_day),
        },
        "market_reason": mkt.reason,
        "planned_for": mkt.next_open_et.isoformat() if mkt.next_open_et else None,
        "plan_only": plan_only,
        "pricing_source": pricing_source,
        "pricing_asof": pricing_asof,
        "total_equity": total_equity,
        "sizing_equity": float(equity_prev),
        "cash": cash_new,
        "num_trades": executed_trades,
        "turnover_notional": turnover,
        "turnover_pct": float(turnover_pct),
        "benchmark": cfg.benchmark_ticker,
        "min_trade_dollars": float(cfg.min_trade_dollars),
        "target_cash_weight": float(target_cash_weight),
        "achieved_cash_weight": float(achieved_cash_weight),
        "gross_exposure": float(gross_exposure),
        "net_exposure": float(net_exposure),
        "investable_dollars": float(investable_dollars),
        "invested_dollars": float(invested_dollars),
        "target_cash_dollars": float(target_cash_dollars),
        "cash_dollars": float(cash_new),
        "overspend_prevented": bool(trade_meta.get("overspend_prevented")),
        "scaled_tickers": list(trade_meta.get("scaled_tickers", [])),
        "position_reconciliation": position_recon,
        "blocked_reasons": blocked_reasons,
        "blocked_tickers": blocked_tickers,
        "execution_status": (
            "SKIPPED_WEEKEND" if is_weekend
            else "HALTED" if (blocked or execution_halt_reason)
            else "PLANNED" if (plan_only or not mkt.is_open_now)
            else "READY"
        ),
        "halt_reason": execution_halt_reason,
        "execution_outcome": execution_outcome,
        "execution_reason": execution_reason,
        "cash_rebalance_status": cash_rebalance_status,
        "broker_reject_status": broker_reject_status,
        "broker_reject_message": broker_reject_message,
        "artifact_failure_stage": artifact_failure_stage,
        "artifact_failure_message": artifact_failure_message,
        "halt_remaining_buys": bool(halt_remaining_buys),
        "execution_trades": (
            execution_trades[["ticker", "side", "shares", "price", "notional", "reason"]].to_dict("records")
            if execution_trades is not None and not execution_trades.empty
            else []
        ),
        "trade_plan": trade_plan,
        "execution_filter": execution_filter_stats,
        "exec_trace": exec_trace,
        "execution_enabled": bool(execution_enabled),
        "precomputed_trade_plan_used": bool(use_precomputed_trade_plan),
        "risk_meta": risk_meta,
        "capital_budget": capital_budget_meta,
        "pdt_pretrade": pdt_pretrade_meta,
        "open_window_validation": validation_details,
        "idempotent_skips": idempotent_skips,
        "orders": orders,
        "shadow_orders": orders,
        "shadow_orders_path": shadow_orders_path,
        "alpaca_submissions": alpaca_submissions,
        "alpaca_orders_path": alpaca_orders_path,
        "alpaca_submission_summary": alpaca_submission_summary,
        "execution_submitted_symbols": execution_submitted_symbols,
        "alpaca_account_snapshot": alpaca_account_snapshot,
        "alpaca_positions_snapshot": alpaca_positions_snapshot,
        "postsell_account_snapshot": postsell_account_snapshot,
        "postsell_account_snapshot_path": postsell_account_snapshot_path,
        "postsell_cash_confirmed": postsell_cash_confirmed,
        "postsell_buying_power_confirmed": postsell_buying_power_confirmed,
        "sell_phase_status": sell_phase_status,
        "sell_phase_completion_reason": sell_phase_completion_reason,
        "sell_phase_observed_statuses": sell_phase_observed_statuses,
        "sell_phase_polls": sell_phase_polls,
        "buy_budget_computed": buy_budget_computed,
        "budget_skipped_orders": budget_skipped_orders,
        "posttrade_account_snapshot_path": posttrade_account_snapshot_path,
        "posttrade_positions_snapshot_path": posttrade_positions_snapshot_path,
        "posttrade_recon_path": posttrade_recon_path,
        "posttrade_recon_status": posttrade_recon_status,
        "posttrade_repair_suggestions": posttrade_repair_suggestions,
        "posttrade_affected_symbols": posttrade_affected_symbols,
        "posttrade_duplicate_fill_suspicions_count": posttrade_duplicate_fill_suspicions_count,
        "run_id": run_id,
        "broker_reconciliation": recon,
        "breaker": {
            "mode": breaker_mode,
            "mode_source": breaker_mode_source,
        },
        "exit_only": bool(exit_only),
    }

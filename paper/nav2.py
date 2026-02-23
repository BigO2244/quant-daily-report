from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable

import pandas as pd

from paper.ledger2 import LEDGER2_PATH, load_ledger
from paper.mark_to_market import update_nav_timeseries
from paper.paper_broker import fetch_prev_closes_yfinance
from paper.reporting_consistency import compute_nav

logger = logging.getLogger(__name__)


def _default_get_price_fn(ticker: str, asof_date: str) -> float | None:
    px = fetch_prev_closes_yfinance([ticker], asof_date=asof_date)
    if px.empty:
        return None
    return float(px.iloc[0]["prev_close"])


def _resolve_starting_cash() -> float:
    cfg_path = Path("paper/config_paper.json")
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8")) or {}
            for key in ("starting_equity", "starting_cash", "initial_equity"):
                if key in cfg:
                    return float(cfg[key])
        except Exception:
            pass

    start_cash = 10000.0
    logger.warning("[NAV2] starting cash not configured; defaulting to 10000.0")
    marker = Path("outputs/ledger/cash_start.json")
    marker.parent.mkdir(parents=True, exist_ok=True)
    if not marker.exists():
        marker.write_text(json.dumps({"starting_cash": start_cash}, indent=2) + "\n", encoding="utf-8")
    return start_cash


def _compute_portfolio_state(ledger_df: pd.DataFrame, asof_date: str, starting_cash: float) -> tuple[float, dict[str, dict[str, float]]]:
    cash = float(starting_cash)
    holdings: dict[str, dict[str, float]] = {}

    if ledger_df.empty:
        return cash, holdings

    usable = ledger_df[ledger_df["trade_date"].astype(str) <= str(asof_date)].copy()
    if usable.empty:
        return cash, holdings

    if all(col in usable.columns for col in ("trade_date", "order_id")):
        before = len(usable)
        dedupe_sort_cols = ["trade_date"]
        if "timestamp_et" in usable.columns:
            dedupe_sort_cols.append("timestamp_et")
        usable = (
            usable.sort_values(dedupe_sort_cols, na_position="last")
            .drop_duplicates(subset=["trade_date", "order_id"], keep="last")
            .reset_index(drop=True)
        )
        if len(usable) != before:
            logger.info(
                "[NAV2] deduped usable ledger rows removed=%d key=(trade_date,order_id) asof=%s",
                before - len(usable),
                asof_date,
            )

    for col in ("quantity", "fill_price", "notional", "fees"):
        if col not in usable.columns:
            usable[col] = pd.NA
        usable[col] = pd.to_numeric(usable[col], errors="coerce")

    bad_fees = int(usable["fees"].isna().sum())
    if bad_fees > 0:
        logger.warning(
            "[NAV2] coercing non-numeric fees to 0.0 count=%d asof=%s",
            bad_fees,
            asof_date,
        )
    usable["fees"] = usable["fees"].fillna(0.0)
    usable["quantity"] = usable["quantity"].fillna(0.0)
    usable["fill_price"] = usable["fill_price"].fillna(0.0)
    usable["notional"] = usable["notional"].fillna(0.0)

    sort_cols = ["trade_date"]
    if "timestamp_et" in usable.columns:
        sort_cols.append("timestamp_et")

    for _, row in usable.sort_values(sort_cols, na_position="last").iterrows():
        ticker = str(row.get("ticker") or "").upper()
        if not ticker:
            continue
        side = str(row.get("side") or "").upper()
        qty = float(row.get("quantity") or 0.0)
        px = float(row.get("fill_price") or 0.0)
        fees = float(row.get("fees") or 0.0)
        if qty <= 0 or px <= 0:
            continue

        pos = holdings.setdefault(ticker, {"shares": 0.0, "avg_cost": 0.0, "realized_pnl": 0.0})
        if side == "BUY":
            new_shares = pos["shares"] + qty
            pos["avg_cost"] = ((pos["shares"] * pos["avg_cost"]) + (qty * px)) / new_shares if new_shares else 0.0
            pos["shares"] = new_shares
            cash -= (qty * px) + fees
        elif side == "SELL":
            sell_qty = min(qty, pos["shares"])
            pos["realized_pnl"] += sell_qty * (px - pos["avg_cost"])
            pos["shares"] = max(0.0, pos["shares"] - sell_qty)
            cash += (sell_qty * px) - fees

    return cash, holdings


def update_nav(
    asof_date: str,
    trade_date: str,
    get_price_fn: Callable[[str, str], float | None] | None,
    source: str,
    run_id: str,
    ledger_path: str = LEDGER2_PATH,
) -> dict:
    perf_dir = Path("outputs/perf")
    perf_dir.mkdir(parents=True, exist_ok=True)

    starting_cash = _resolve_starting_cash()
    ledger_df = load_ledger(ledger_path)
    cash, holdings = _compute_portfolio_state(ledger_df, asof_date, starting_cash)

    resolved_price_fn = get_price_fn or _default_get_price_fn
    holdings_rows = []
    price_map: dict[str, float] = {}
    missing_prices: list[str] = []

    for ticker, pos in sorted(holdings.items()):
        shares = float(pos["shares"])
        if shares <= 0:
            continue
        mtm_price = resolved_price_fn(ticker, asof_date)
        if mtm_price is None:
            missing_prices.append(ticker)
            continue
        mtm_price = float(mtm_price)
        price_map[ticker] = mtm_price
        holdings_rows.append(
            {
                "ticker": ticker,
                "shares": shares,
                "avg_cost": float(pos["avg_cost"]),
                "realized_pnl": float(pos["realized_pnl"]),
                "sleeve": "main",
            }
        )

    holdings_df = pd.DataFrame(holdings_rows)
    mtm, nav = compute_nav(ledger=holdings_df, prices=price_map, cash=float(cash))
    realized_map = {
        str(r["ticker"]).upper(): float(r.get("realized_pnl", 0.0))
        for _, r in holdings_df.iterrows()
    }
    holdings_out = pd.DataFrame(
        columns=[
            "date",
            "ticker",
            "shares",
            "avg_cost",
            "mtm_price",
            "market_value",
            "unrealized_pnl",
            "realized_pnl",
        ]
    )
    if not mtm.empty:
        holdings_out = mtm.rename(columns={"price": "mtm_price"})[
            ["ticker", "shares", "avg_cost", "mtm_price", "market_value", "unrealized_pnl"]
        ].copy()
        holdings_out["realized_pnl"] = (
            holdings_out["ticker"].astype(str).str.upper().map(realized_map).fillna(0.0)
        )
        holdings_out.insert(0, "date", str(asof_date))

    equity = float(nav.get("equity", 0.0))
    market_value = float(nav.get("totals", {}).get("market_value", 0.0))

    nav_payload = {
        "date": str(asof_date),
        "trade_date": str(trade_date),
        "source": str(source).upper(),
        "run_id": str(run_id),
        "equity": equity,
        "cash": float(cash),
        "gross_exposure": float(nav.get("gross_exposure", 0.0)),
        "net_exposure": float(nav.get("net_exposure", 0.0)),
        "market_value": float(market_value),
        "missing_prices": sorted(set(missing_prices)),
    }

    nav_path = perf_dir / f"nav_{asof_date}.json"
    nav_path.write_text(json.dumps(nav_payload, indent=2) + "\n", encoding="utf-8")

    holdings_path = perf_dir / f"holdings_mtm_{asof_date}.csv"
    holdings_out.to_csv(holdings_path, index=False)

    nav_ts_path = update_nav_timeseries(
        asof_date=asof_date,
        nav=nav_payload,
        ledger=ledger_df,
    )

    return {
        "nav_path": str(nav_path),
        "nav_timeseries_path": nav_ts_path,
        "holdings_mtm_path": str(holdings_path),
        "equity": equity,
        "cash": float(cash),
        "missing_prices": sorted(set(missing_prices)),
    }


# backward-compatible adapter

def update_nav_outputs(
    asof_date: str,
    ledger_path: str = LEDGER2_PATH,
    nav_path_tpl: str = "outputs/perf/nav_{asof}.json",
    nav_timeseries_path: str = "outputs/perf/nav_timeseries.csv",
) -> dict:
    _ = nav_path_tpl, nav_timeseries_path
    return update_nav(asof_date=asof_date, trade_date=asof_date, get_price_fn=None, source="SHADOW", run_id="legacy", ledger_path=ledger_path)

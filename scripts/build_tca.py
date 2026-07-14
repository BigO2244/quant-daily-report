#!/usr/bin/env python3
"""Transaction Cost Analysis (TCA): decompose the shadow-vs-realized gap into
named, additive, reconciling cost buckets.

Reads the broker-truth ledger (outputs/ledger/<account>/, built by
build_broker_truth_ledger.py) and the intended target book
(outputs/operational_drag/<latest>/intended_nav_timeseries.csv), plus the
model's per-order decision prices (outputs/runs/*/broker/intended_orders_*.json).
Market data (daily closes + 1-minute arrival bars) is pulled READ-ONLY from
Alpaca's data API and cached under outputs/tca/cache/.

Daily reconciling identity, in return space (day t, equity-weighted):

  gap(t) = r_real(t) - r_intended_series(t)
         = CASH_DRAG          (k-1) * r_int  — deployment scaling vs intended
         + NON_FILL           intended names under-held, no reject evidence
         + REJECTED           intended names under-held, order rejected/canceled
         + OVERHOLD           names held that the target book does not want
         + OTHER_SELECTION    residual over/underweights of intended names
         + EXEC_TRADING       fills vs same-day close (execution timing)
         + FEES               regulatory fees
         + DIVIDENDS          cash dividends received (helps, not a cost)
         + PRICE_MARKING      intended book marked at broker closes vs the
                              intended engine's own price marks (measurement)
         + RESIDUAL           forced closure (rounding, missing prices)

Buckets sum to gap(t) EXACTLY by construction; RESIDUAL is reported, never
hidden. Per-order implementation shortfall (DELAY = decision->arrival,
SLIPPAGE = arrival->fill) is reported in order_tca.csv; arrival uses the
1-minute bar OPEN at/immediately before submission (never post-fill data).

Writes: outputs/tca/<account>/{daily_decomposition.csv, order_tca.csv,
tca_summary.json} and outputs/tca/TCA_REPORT.md. Read-only; re-runnable.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import math
import sys
import time
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

import re

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
LEDGER_ROOT = REPO_ROOT / "outputs" / "ledger"
TCA_ROOT = REPO_ROOT / "outputs" / "tca"
ET = ZoneInfo("America/New_York")
OCC_OPTION_SYMBOL = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")

sys.path.insert(0, str(REPO_ROOT / "scripts"))
from build_broker_truth_ledger import (  # noqa: E402
    ACCOUNT_ENV_FILES,
    parse_env_file,
    parse_iso,
    read_csv_rows,
    read_jsonl,
    atomic_write,
)

DATA_BASE = "https://data.alpaca.markets"

EXTERNAL_FLOW_TYPES = {"CSD", "CSW", "TRANS", "ACATC", "ACATS", "JNLC", "JNLS"}

BUCKETS = [
    "cash_drag",
    "non_fill",
    "rejected",
    "overhold",
    "other_selection",
    "exec_trading",
    "fees",
    "dividends",
    "price_marking",
    "residual",
]

BUCKET_NOTES = {
    "cash_drag": ("the book we INTENDED but didn't get", "STRUCTURAL (cash-account settled-funds staging; margin would remove most)"),
    "non_fill": ("the book we INTENDED but didn't get", "FIXABLE (min-trade floors, sizing, order coverage)"),
    "rejected": ("the book we INTENDED but didn't get", "FIXABLE (validation gates / whitelist)"),
    "overhold": ("the book we INTENDED but didn't get", "FIXABLE (sells that did not happen)"),
    "other_selection": ("the book we INTENDED but didn't get", "FIXABLE (weight tracking precision)"),
    "exec_trading": ("cost of the trades we DID", "FIXABLE (order type/timing; see order_tca delay vs slippage)"),
    "fees": ("cost of the trades we DID", "STRUCTURAL (regulatory; scales with turnover)"),
    "dividends": ("accounting", "N/A (income, not a cost)"),
    "price_marking": ("measurement", "N/A (price-source difference, not a real leak)"),
    "residual": ("measurement", "N/A (closure term; should stay small)"),
}


def log(msg: str) -> None:
    print(f"[tca] {msg}", flush=True)


# --------------------------------------------------------------------------
# Market data (read-only, cached)
# --------------------------------------------------------------------------

class MarketData:
    def __init__(self, creds: dict, cache_dir: Path):
        self.h = {
            "APCA-API-KEY-ID": creds["ALPACA_API_KEY_ID"],
            "APCA-API-SECRET-KEY": creds["ALPACA_API_SECRET_KEY"],
        }
        self.cache_dir = cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)

    def _get(self, path: str, params: dict) -> dict:
        for attempt in range(5):
            resp = requests.get(f"{DATA_BASE}{path}", headers=self.h, params=params, timeout=60)
            if resp.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()

    def daily_closes(self, symbols: list, start: str, end: str) -> dict:
        """{symbol: {date: close}} via stock and option daily bars."""
        cache = self.cache_dir / f"daily_closes_{start}_{end}.json"
        if cache.exists():
            data = json.loads(cache.read_text())
            if data.get("_schema") == 3 and set(symbols) <= set(data.get("_symbols", [])):
                return data["closes"]
        option_symbols = sorted(symbol for symbol in symbols if OCC_OPTION_SYMBOL.match(symbol))
        stock_symbols = sorted(set(symbols) - set(option_symbols))
        alias_path = REPO_ROOT / "data" / "security_master" / "manual_aliases.json"
        aliases = {}
        if alias_path.exists():
            aliases = (json.loads(alias_path.read_text()).get("aliases") or {})
        provider_to_requested: dict[str, list[str]] = defaultdict(list)
        for symbol in stock_symbols:
            provider_to_requested[aliases.get(symbol, symbol)].append(symbol)
        provider_symbols = sorted(provider_to_requested)
        closes: dict[str, dict] = defaultdict(dict)
        for i in range(0, len(provider_symbols), 100):
            chunk = provider_symbols[i : i + 100]
            token = None
            while True:
                params = {
                    "symbols": ",".join(chunk),
                    "timeframe": "1Day",
                    "start": start,
                    "end": end,
                    "feed": "iex",
                    "limit": 10000,
                }
                if token:
                    params["page_token"] = token
                data = self._get("/v2/stocks/bars", params)
                for provider_symbol, bars in (data.get("bars") or {}).items():
                    for requested_symbol in provider_to_requested.get(provider_symbol, [provider_symbol]):
                        for b in bars:
                            d = parse_iso(b["t"]).astimezone(ET).date().isoformat()
                            closes[requested_symbol][d] = b["c"]
                token = data.get("next_page_token")
                if not token:
                    break
        for i in range(0, len(option_symbols), 100):
            chunk = option_symbols[i : i + 100]
            token = None
            while True:
                params = {
                    "symbols": ",".join(chunk),
                    "timeframe": "1Day",
                    "start": start,
                    "end": end,
                    "limit": 10000,
                }
                if token:
                    params["page_token"] = token
                data = self._get("/v1beta1/options/bars", params)
                for symbol, bars in (data.get("bars") or {}).items():
                    for b in bars:
                        day = parse_iso(b["t"]).astimezone(ET).date().isoformat()
                        closes[symbol][day] = b["c"]
                token = data.get("next_page_token")
                if not token:
                    break
        out = {s: dict(v) for s, v in closes.items()}
        atomic_write(cache, json.dumps({"_schema": 3, "_symbols": symbols, "closes": out}))
        return out

    def minute_bars_window(self, date: str, symbols: list, start_utc: str, end_utc: str) -> dict:
        """{symbol: [bars]} for a submission window on one date (cached)."""
        cache = self.cache_dir / f"arrival_{date}.json"
        if cache.exists():
            data = json.loads(cache.read_text())
            if data.get("_schema") == 2 and set(symbols) <= set(data.get("_symbols", [])):
                return data["bars"]
        option_symbols = sorted(symbol for symbol in symbols if OCC_OPTION_SYMBOL.match(symbol))
        stock_symbols = sorted(set(symbols) - set(option_symbols))
        allbars: dict[str, list] = defaultdict(list)
        for i in range(0, len(stock_symbols), 100):
            chunk = stock_symbols[i : i + 100]
            token = None
            while True:
                params = {
                    "symbols": ",".join(chunk),
                    "timeframe": "1Min",
                    "start": start_utc,
                    "end": end_utc,
                    "feed": "iex",
                    "limit": 10000,
                }
                if token:
                    params["page_token"] = token
                data = self._get("/v2/stocks/bars", params)
                for sym, bars in (data.get("bars") or {}).items():
                    allbars[sym].extend(bars)
                token = data.get("next_page_token")
                if not token:
                    break
        for i in range(0, len(option_symbols), 100):
            chunk = option_symbols[i : i + 100]
            token = None
            while True:
                params = {
                    "symbols": ",".join(chunk),
                    "timeframe": "1Min",
                    "start": start_utc,
                    "end": end_utc,
                    "limit": 10000,
                }
                if token:
                    params["page_token"] = token
                data = self._get("/v1beta1/options/bars", params)
                for sym, values in (data.get("bars") or {}).items():
                    allbars[sym].extend(values)
                token = data.get("next_page_token")
                if not token:
                    break
        out = {s: v for s, v in allbars.items()}
        atomic_write(cache, json.dumps({"_schema": 2, "_symbols": symbols, "bars": out}))
        return out

    def quote_before(self, symbol: str, submitted_at: dt.datetime, cache_key: str):
        """Last historical quote at/before submission without bulk quote pulls.

        A multi-symbol, multi-minute quote request can return millions of ticks.
        One descending, one-symbol request capped at one quote is bounded and is
        exactly the observation TCA needs.
        """
        cache = self.cache_dir / "arrival_quotes" / f"{cache_key}.json"
        if cache.exists():
            data = json.loads(cache.read_text())
            return data.get("mid"), parse_iso(data["timestamp"]) if data.get("timestamp") else None
        legacy = cache.with_name(f"{cache_key.removeprefix('v2_')}.json")
        if legacy.exists():
            data = json.loads(legacy.read_text())
            if data.get("mid") is not None and data.get("timestamp"):
                atomic_write(cache, json.dumps(data))
                return data["mid"], parse_iso(data["timestamp"])
        start = submitted_at - dt.timedelta(minutes=5)
        if OCC_OPTION_SYMBOL.match(symbol):
            return None, None  # Alpaca exposes historical option bars/trades, not historical quotes.
        endpoint = "/v2/stocks/quotes"
        payload = self._get(
            endpoint,
            {
                "symbols": symbol,
                "start": start.isoformat().replace("+00:00", "Z"),
                "end": submitted_at.isoformat().replace("+00:00", "Z"),
                "limit": 1,
                "sort": "desc",
                "feed": "iex",
            },
        )
        mid, quote_ts = quote_mid_at_or_before((payload.get("quotes") or {}).get(symbol, []), submitted_at)
        atomic_write(
            cache,
            json.dumps({"symbol": symbol, "submitted_at": submitted_at.isoformat(), "mid": mid,
                        "timestamp": quote_ts.isoformat() if quote_ts else None}),
        )
        return mid, quote_ts


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------

def latest_intended_series() -> tuple[Path, list]:
    drag_root = REPO_ROOT / "outputs" / "operational_drag"
    runs = sorted(d for d in drag_root.iterdir() if d.is_dir()) if drag_root.exists() else []
    for run in reversed(runs):
        ts = run / "intended_nav_timeseries.csv"
        if ts.exists():
            return ts, read_csv_rows(ts)
    raise SystemExit("no intended_nav_timeseries.csv found under outputs/operational_drag/")


def load_intended_orders() -> dict:
    """Decision prices indexed by both decision run id and date.

    Orders can be submitted the next morning from a prior-day plan, and a later
    blocked rerun can coexist with the run that actually generated the order.
    The client order id embeds the run id, so preserve every run rather than
    collapsing to an arbitrary latest file first.
    """
    paths = list(sorted((REPO_ROOT / "outputs" / "runs").glob("*/broker/intended_orders_*.json")))
    paths += list(sorted((REPO_ROOT / "outputs" / "broker").glob("intended_orders_*.json")))
    by_date: dict[str, dict] = {}
    by_run: dict[str, dict] = {}
    for p in paths:
        d = p.stem.replace("intended_orders_", "")
        try:
            doc = json.loads(p.read_text())
        except Exception:
            continue
        recs = {}
        for o in doc.get("orders_intended") or []:
            try:
                px = float(o.get("price") or 0)
            except (TypeError, ValueError):
                px = 0.0
            if math.isfinite(px) and px > 0:
                recs[(o.get("ticker"), (o.get("side") or "").lower())] = px
        entry = {
            "prices": recs,
            "path": str(p.relative_to(REPO_ROOT)),
            "blocked": doc.get("execution_blocked"),
            "date": d,
        }
        by_date[d] = entry
        if doc.get("run_id"):
            by_run[doc["run_id"]] = entry
    return {"by_date": by_date, "by_run": by_run}


def dedupe_orders(orders: list) -> list:
    """Latest record per order id."""
    latest: dict[str, dict] = {}
    for o in orders:
        cur = latest.get(o["id"])
        if cur is None or (o.get("updated_at") or "") >= (cur.get("updated_at") or ""):
            latest[o["id"]] = o
    return sorted(latest.values(), key=lambda o: o.get("submitted_at") or "")


def quote_mid_at_or_before(quotes: list[dict], timestamp: dt.datetime, max_age_minutes: int = 5):
    """Return (mid, quote timestamp) without using post-arrival information."""
    best = None
    for quote in quotes:
        quote_ts = parse_iso(quote["t"])
        bid, ask = float(quote.get("bp") or 0), float(quote.get("ap") or 0)
        if bid <= 0 or ask <= 0 or ask < bid or quote_ts > timestamp:
            continue
        if best is None or quote_ts > best[0]:
            best = (quote_ts, (bid + ask) / 2.0)
    if best is None or timestamp - best[0] > dt.timedelta(minutes=max_age_minutes):
        return None, None
    return best[1], best[0]


def decision_price_for_order(order: dict, trade_date: str, intended_orders: dict):
    """Resolve the exact-side price from the generating run, then trade date."""
    symbol, side = order["symbol"], (order.get("side") or "").lower()
    client_id = order.get("client_order_id") or ""
    run_entry = next(
        (
            entry
            for run_id, entry in intended_orders.get("by_run", {}).items()
            if client_id.startswith(run_id)
        ),
        None,
    )
    run_price = (run_entry or {}).get("prices", {}).get((symbol, side))
    date_price = (
        intended_orders.get("by_date", {}).get(trade_date, {}).get("prices", {}).get((symbol, side))
    )
    if run_price:
        return run_price, "intended_orders_run_id_exact_side"
    if date_price:
        return date_price, "intended_orders_trade_date_exact_side"
    return None, "missing"


# --------------------------------------------------------------------------
# Order-level TCA (implementation shortfall: delay + slippage)
# --------------------------------------------------------------------------

def build_order_tca(account: str, md: MarketData, intended_orders: dict) -> list:
    accdir = LEDGER_ROOT / account
    orders = dedupe_orders(read_jsonl(accdir / "orders.jsonl"))
    fills_by_order: dict[str, list[dict]] = defaultdict(list)
    for fill in read_csv_rows(accdir / "fills.csv"):
        if fill.get("order_id"):
            fills_by_order[fill["order_id"]].append(fill)
    rows = []
    # group orders by ET trade date for windowed minute-bar pulls
    by_date: dict[str, list] = defaultdict(list)
    for o in orders:
        if not o.get("submitted_at"):
            continue
        d = parse_iso(o["submitted_at"]).astimezone(ET).date().isoformat()
        by_date[d].append(o)

    all_dates = sorted(by_date)
    all_symbols = sorted({o["symbol"] for o in orders if not OCC_OPTION_SYMBOL.match(o["symbol"])})
    decision_closes = {}
    if all_dates:
        start = (dt.date.fromisoformat(all_dates[0]) - dt.timedelta(days=10)).isoformat()
        decision_closes = md.daily_closes(all_symbols, start, all_dates[-1])

    for d, day_orders in sorted(by_date.items()):
        subs = [parse_iso(o["submitted_at"]) for o in day_orders]
        start = (min(subs) - dt.timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        end = (max(subs) + dt.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        occ = OCC_OPTION_SYMBOL
        symbols = sorted({o["symbol"] for o in day_orders})
        try:
            bars = md.minute_bars_window(d, symbols, start, end) if symbols else {}
        except Exception as exc:
            log(f"{account}: minute bars unavailable for {d} ({exc}) — daily fallback")
            bars = {}
        for o in day_orders:
            sym, side = o["symbol"], (o.get("side") or "").lower()
            sign = 1 if side == "buy" else -1
            sub_ts = parse_iso(o["submitted_at"])
            decision, decision_src = decision_price_for_order(o, d, intended_orders)
            if decision is None:
                prior_dates = [day for day in decision_closes.get(sym, {}) if day < d]
                if not prior_dates and occ.match(sym):
                    option_start = (dt.date.fromisoformat(d) - dt.timedelta(days=10)).isoformat()
                    option_closes = md.daily_closes([sym], option_start, d)
                    decision_closes[sym] = option_closes.get(sym, {})
                    prior_dates = [day for day in decision_closes.get(sym, {}) if day < d]
                if prior_dates:
                    prior_day = max(prior_dates)
                    decision = decision_closes[sym][prior_day]
                    decision_src = f"FALLBACK_alpaca_prior_close@{prior_day}_missing_model_artifact"
            # Primary arrival is the last valid bid/ask midpoint at or before
            # submission. A minute-bar open is an explicitly labeled fallback.
            arrival = quote_ts = None
            try:
                arrival, quote_ts = md.quote_before(sym, sub_ts, f"v2_{account}_{o['id']}")
            except Exception as exc:
                log(f"{account}: quote unavailable for {o['id']} {sym} ({exc}) — bar fallback")
            arrival_src = (
                f"quote_mid@{quote_ts.astimezone(ET).strftime('%H:%M:%S')}ET"
                if quote_ts is not None
                else "missing"
            )
            if arrival is None:
                best = None
                for b in bars.get(sym, []):
                    bt = parse_iso(b["t"])
                    if bt <= sub_ts and (best is None or bt > best[0]):
                        best = (bt, b)
                if best is not None and (sub_ts - best[0]) <= dt.timedelta(minutes=5):
                    arrival = float(best[1]["o"])
                    arrival_src = f"FALLBACK_1min_bar_open@{best[0].astimezone(ET).strftime('%H:%M')}ET"
            if arrival is None:
                fallback_dates = [day for day in decision_closes.get(sym, {}) if day < d]
                if fallback_dates:
                    fallback_day = max(fallback_dates)
                    arrival = float(decision_closes[sym][fallback_day])
                    arrival_src = f"FALLBACK_alpaca_prior_daily_close@{fallback_day}"
            order_fills = fills_by_order.get(o["id"], [])
            filled_qty = sum(float(fill.get("qty") or 0) for fill in order_fills)
            fill_price_notional = sum(
                float(fill.get("qty") or 0) * float(fill.get("price") or 0)
                for fill in order_fills
            )
            fill_notional = sum(
                float(fill.get("notional") or 0)
                for fill in order_fills
            )
            fill_px = fill_price_notional / filled_qty if filled_qty else None
            qty = float(o.get("qty") or 0) or None
            delay_bps = slippage_bps = is_bps = None
            if decision and arrival:
                delay_bps = sign * (arrival - decision) / decision * 1e4
            if arrival and fill_px:
                slippage_bps = sign * (fill_px - arrival) / arrival * 1e4
            if decision and fill_px:
                is_bps = sign * (fill_px - decision) / decision * 1e4
            notional = fill_notional
            rows.append(
                {
                    "trade_date_et": d,
                    "order_id": o["id"],
                    "client_order_id": o.get("client_order_id"),
                    "symbol": sym,
                    "side": side,
                    "status": o.get("status"),
                    "order_type": o.get("type"),
                    "submitted_at": o.get("submitted_at"),
                    "filled_at": o.get("filled_at"),
                    "qty": qty,
                    "filled_qty": filled_qty,
                    "fill_count": len(order_fills),
                    "fill_activity_ids": ";".join(fill["activity_id"] for fill in order_fills),
                    "decision_price": decision,
                    "decision_source": decision_src,
                    "arrival_price": arrival,
                    "arrival_source": arrival_src,
                    "fill_price": fill_px,
                    "fill_price_source": "alpaca_fill_activities_vwap" if order_fills else "missing",
                    "filled_notional": round(notional, 2),
                    "delay_bps": round(delay_bps, 2) if delay_bps is not None else None,
                    "slippage_bps": round(slippage_bps, 2) if slippage_bps is not None else None,
                    "is_vs_decision_bps": round(is_bps, 2) if is_bps is not None else None,
                    "delay_cost_usd": round(delay_bps / 1e4 * notional, 4) if delay_bps is not None else None,
                    "slippage_cost_usd": round(slippage_bps / 1e4 * notional, 4) if slippage_bps is not None else None,
                }
            )
    return rows


# --------------------------------------------------------------------------
# Daily reconciling decomposition
# --------------------------------------------------------------------------

def build_daily_decomposition(account: str, md: MarketData, intended_rows: list, order_rows: list) -> tuple[list, dict]:
    accdir = LEDGER_ROOT / account
    nav = read_csv_rows(accdir / "daily_nav.csv")
    fills = read_csv_rows(accdir / "fills.csv")
    acts = read_jsonl(accdir / "activities.jsonl")

    nav = [r for r in nav if float(r["equity"] or 0) > 0]
    nav_dates = [r["date"] for r in nav]
    equity = {r["date"]: float(r["equity"]) for r in nav}

    intended = {}
    for r in intended_rows:
        try:
            e = float(r["intended_equity_value"] or 0)
        except ValueError:
            continue
        if e > 0:
            intended[r["date"]] = {
                "equity": e,
                "cash": float(r["intended_cash"] or 0),
                "positions": json.loads(r["intended_positions"] or "[]"),
            }

    common = [d for d in nav_dates if d in intended]
    if len(common) < 2:
        raise SystemExit(f"{account}: insufficient overlap between ledger NAV and intended series")

    # Fills cumulated by ET date -> BOD holdings
    fills_by_date: dict[str, list] = defaultdict(list)
    for f in fills:
        fills_by_date[f["trade_date_et"]].append(f)

    # fees / dividends / external flows by date
    fees_by_date: dict[str, float] = defaultdict(float)
    div_by_date: dict[str, float] = defaultdict(float)
    flow_dates = set()
    for a in acts:
        d = a.get("date") or (a.get("transaction_time") or "")[:10]
        t = a.get("activity_type")
        if t == "FEE":
            fees_by_date[d] += float(a.get("net_amount") or 0)
        elif t in ("DIV", "DIVCGL", "DIVCGS", "DIVNRA", "DIVROC"):
            div_by_date[d] += float(a.get("net_amount") or 0)
        elif t in EXTERNAL_FLOW_TYPES:
            flow_dates.add(d)

    # Symbols: everything ever held + everything in the intended books.
    # OCC option symbols (gated overlay; 2 net-zero round trips) cannot be
    # priced from the stock-bars endpoint — their effect lands in `residual`
    # on their trade dates and is flagged.
    occ = OCC_OPTION_SYMBOL
    all_syms = {f["symbol"] for f in fills} | {
        p["symbol"] for d in common for p in intended[d]["positions"]
    }
    option_syms = {s for s in all_syms if occ.match(s)}
    symbols = sorted(all_syms)
    closes = md.daily_closes(symbols, common[0], common[-1])
    log(f"{account}: daily closes for {len(symbols)} symbols {common[0]}..{common[-1]}")

    # order evidence for cause classification: per (date, symbol) worst status
    order_status: dict[tuple, set] = defaultdict(set)
    for o in order_rows:
        order_status[(o["trade_date_et"], o["symbol"])].add(o["status"] or "unknown")

    def holdings_through(date: str, running: dict, done_dates: list) -> dict:
        # running holdings updated incrementally by caller
        return running

    daily = []
    flags: list[str] = []
    running_qty: dict[str, float] = defaultdict(float)
    fill_dates_sorted = sorted(fills_by_date)
    fd_idx = 0

    for i in range(1, len(common)):
        d0, d1 = common[i - 1], common[i]
        # advance holdings: apply fills with trade_date <= d0
        while fd_idx < len(fill_dates_sorted) and fill_dates_sorted[fd_idx] <= d0:
            for f in fills_by_date[fill_dates_sorted[fd_idx]]:
                sgn = 1 if f["side"] == "buy" else -1
                running_qty[f["symbol"]] += sgn * float(f["qty"])
            fd_idx += 1

        E0, E1 = equity[d0], equity[d1]
        if d1 in flow_dates or d0 in flow_dates:
            flags.append(f"{d1}: external flow day — decomposition skipped")
            continue
        r_real = E1 / E0 - 1
        r_int_series = intended[d1]["equity"] / intended[d0]["equity"] - 1
        gap = r_real - r_int_series

        def close(sym, d, fallback=None):
            c = closes.get(sym, {}).get(d)
            return c if c is not None else fallback

        # returns per symbol d0->d1 (broker closes)
        def sym_ret(sym):
            c0, c1 = close(sym, d0), close(sym, d1)
            if c0 and c1:
                return c1 / c0 - 1
            return None

        # actual BOD weights
        w_act: dict[str, float] = {}
        for sym, q in running_qty.items():
            if abs(q) < 1e-9:
                continue
            c0 = close(sym, d0)
            if c0 is None:
                flags.append(f"{d1}: no close for held {sym} @ {d0}")
                continue
            multiplier = 100.0 if sym in option_syms else 1.0
            w_act[sym] = q * c0 * multiplier / E0

        # intended BOD weights (book recorded at d0)
        ib = intended[d0]
        w_int: dict[str, float] = {}
        for p in ib["positions"]:
            w_int[p["symbol"]] = float(p["market_value"]) / ib["equity"]

        gross_act = sum(w_act.values())
        gross_int = sum(w_int.values())

        # returns
        r_int_bm = 0.0
        missing_int = []
        for sym, w in w_int.items():
            r = sym_ret(sym)
            if r is None:
                missing_int.append(sym)
                continue
            r_int_bm += w * r
        r_hold = 0.0
        for sym, w in w_act.items():
            r = sym_ret(sym)
            if r is None:
                flags.append(f"{d1}: no return for held {sym}")
                continue
            r_hold += w * r
        if missing_int:
            flags.append(f"{d1}: intended symbols missing returns {missing_int[:5]}")

        k = gross_act / gross_int if gross_int > 1e-9 else 1.0
        cash_drag = (k - 1) * r_int_bm

        non_fill = rejected = overhold = other_sel = 0.0
        for sym in set(w_act) | set(w_int):
            r = sym_ret(sym)
            if r is None:
                continue
            eps = w_act.get(sym, 0.0) - k * w_int.get(sym, 0.0)
            if abs(eps) < 1e-12:
                continue
            contrib = eps * r
            if sym in w_int and eps < 0:
                statuses = order_status.get((d0, sym), set()) | order_status.get((d1, sym), set())
                if statuses & {"rejected", "canceled", "expired"}:
                    rejected += contrib
                else:
                    non_fill += contrib
            elif sym not in w_int and eps > 0:
                overhold += contrib
            else:
                other_sel += contrib

        # execution vs same-day close for fills on d1
        exec_trading = 0.0
        for f in fills_by_date.get(d1, []):
            c1 = close(f["symbol"], d1)
            if c1 is None:
                flags.append(f"{d1}: no close for filled {f['symbol']}")
                continue
            sgn = 1 if f["side"] == "buy" else -1
            multiplier = 100.0 if f["symbol"] in option_syms else 1.0
            exec_trading += sgn * float(f["qty"]) * multiplier * (c1 - float(f["price"])) / E0

        fees = fees_by_date.get(d1, 0.0) / E0
        divs = div_by_date.get(d1, 0.0) / E0
        price_marking = r_int_bm - r_int_series

        explained = (
            cash_drag + non_fill + rejected + overhold + other_sel
            + exec_trading + fees + divs + price_marking
        )
        # identity: gap = (r_hold - r_int_bm) + (r_real - r_hold) + (r_int_bm - r_int_series)
        residual = gap - explained

        daily.append(
            {
                "date": d1,
                "r_real": r_real,
                "r_intended_series": r_int_series,
                "gap": gap,
                "cash_drag": cash_drag,
                "non_fill": non_fill,
                "rejected": rejected,
                "overhold": overhold,
                "other_selection": other_sel,
                "exec_trading": exec_trading,
                "fees": fees,
                "dividends": divs,
                "price_marking": price_marking,
                "residual": residual,
                "k_deployment": k,
                "gross_act": gross_act,
                "gross_int": gross_int,
                "equity_bod": E0,
            }
        )
    return daily, {"flags": flags, "overlap": (common[0], common[-1]), "days": len(daily)}


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def summarize(account: str, daily: list, order_rows: list, meta: dict, intended_src: str) -> dict:
    tot = {b: sum(r[b] for r in daily) for b in BUCKETS}
    tot_gap = sum(r["gap"] for r in daily)
    # geometric cross-check
    prod_real = math.prod(1 + r["r_real"] for r in daily)
    prod_int = math.prod(1 + r["r_intended_series"] for r in daily)

    filled = [o for o in order_rows if o["filled_qty"]]
    delay_usd = sum(o["delay_cost_usd"] or 0 for o in filled)
    slip_usd = sum(o["slippage_cost_usd"] or 0 for o in filled)
    covered = [o for o in filled if o["delay_bps"] is not None]
    arrival_fallback = [o for o in filled if o["arrival_price"] is None]
    arrival_proxy = [o for o in filled if str(o.get("arrival_source") or "").startswith("FALLBACK_")]
    decision_missing = [o for o in filled if o["decision_price"] is None]
    decision_proxy = [o for o in filled if str(o.get("decision_source") or "").startswith("FALLBACK_")]
    math_reconciles = abs(sum(tot.values()) - tot_gap) < 1e-9
    residual_abs = sum(abs(r["residual"]) for r in daily)
    residual_net_bps = abs(tot["residual"]) * 1e4
    non_residual_abs = sum(abs(r[b]) for r in daily for b in BUCKETS if b != "residual")
    residual_share = residual_abs / max(residual_abs + non_residual_abs, 1e-12)
    # A forced closure term can make any decomposition add up. Treat the
    # attribution as validated only when residual is economically immaterial.
    attribution_validated = (
        math_reconciles and residual_share <= 0.10 and residual_net_bps <= 10.0
    )

    # biggest daily leaks per bucket
    leaks = []
    for b in BUCKETS:
        if b in ("dividends", "price_marking", "residual"):
            continue
        worst = sorted(daily, key=lambda r: r[b])[:3]
        for w in worst:
            if w[b] < -0.0005:  # > 5 bps day cost
                leaks.append({"bucket": b, "date": w["date"], "cost_bps_day": round(w[b] * 1e4, 1)})
    leaks.sort(key=lambda x: x["cost_bps_day"])

    return {
        "account": account,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "intended_series_source": intended_src,
        "window": {"start": meta["overlap"][0], "end": meta["overlap"][1], "days": meta["days"]},
        "gap_total_arithmetic_sum_daily": round(tot_gap, 6),
        "gap_geometric_cum": round((prod_real - 1) - (prod_int - 1), 6),
        "compounding_note": "buckets reconcile the arithmetic sum of daily gaps; the geometric cumulative gap differs by cross-compounding",
        "buckets_sum": round(sum(tot.values()), 6),
        "reconciles": math_reconciles,
        "attribution_validated": attribution_validated,
        "validation": {
            "mathematical_identity": math_reconciles,
            "absolute_residual_bps": round(residual_abs * 1e4, 1),
            "absolute_net_residual_bps": round(residual_net_bps, 1),
            "residual_share_of_absolute_attribution": round(residual_share, 6),
            "maximum_residual_share": 0.10,
            "maximum_absolute_net_residual_bps": 10.0,
            "note": "Mathematical closure alone is not validation because residual is a forced closure bucket.",
        },
        "buckets": {b: {"total_return_contrib": round(tot[b], 6), "bps": round(tot[b] * 1e4, 1),
                        "group": BUCKET_NOTES[b][0], "fixability": BUCKET_NOTES[b][1]} for b in BUCKETS},
        "order_level": {
            "orders": len(order_rows),
            "filled_orders": len(filled),
            "delay_covered_orders": len(covered),
            "arrival_missing_orders": len(arrival_fallback),
            "arrival_proxy_orders": len(arrival_proxy),
            "decision_price_missing_orders": len(decision_missing),
            "decision_price_proxy_orders": len(decision_proxy),
            "delay_cost_usd_total": round(delay_usd, 2),
            "slippage_cost_usd_total": round(slip_usd, 2),
            "note": "delay/slippage use exact-side intended prices, historical quote midpoints at/before submission, and exact Alpaca fill-activity VWAP; proxy/missing arrivals are explicit",
        },
        "biggest_daily_leaks": leaks[:10],
        "data_flags_count": len(meta["flags"]),
        "data_flags_sample": meta["flags"][:15],
    }


def write_outputs(account: str, daily: list, order_rows: list, summary: dict) -> None:
    outdir = TCA_ROOT / account
    outdir.mkdir(parents=True, exist_ok=True)

    cols = ["date", "r_real", "r_intended_series", "gap"] + BUCKETS + [
        "k_deployment", "gross_act", "gross_int", "equity_bod"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for r in daily:
        w.writerow({c: (f"{r[c]:.8f}" if isinstance(r[c], float) else r[c]) for c in cols})
    atomic_write(outdir / "daily_decomposition.csv", buf.getvalue())

    if order_rows:
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=list(order_rows[0].keys()))
        w.writeheader()
        w.writerows(order_rows)
        atomic_write(outdir / "order_tca.csv", buf.getvalue())

    atomic_write(outdir / "tca_summary.json", json.dumps(summary, indent=2, sort_keys=True))


def render_report(summaries: list) -> str:
    L = ["# Transaction Cost Analysis — shadow vs realized, fully decomposed", ""]
    for s in summaries:
        L += [
            f"## Account: {s['account']}",
            "",
            f"Window **{s['window']['start']} → {s['window']['end']}** "
            f"({s['window']['days']} decomposed days). Intended book: `{s['intended_series_source']}`.",
            "",
            f"Total gap (sum of daily realized − intended): **{s['gap_total_arithmetic_sum_daily']*1e4:+.1f} bps**"
            f" (geometric cumulative: {s['gap_geometric_cum']*1e4:+.1f} bps). "
            f"Buckets sum to {s['buckets_sum']*1e4:+.1f} bps — mathematical identity: "
            f"**{s['reconciles']}**; attribution validated: **{s['attribution_validated']}**.",
            f"Absolute residual is {s['validation']['absolute_residual_bps']:.1f} bps "
            f"({s['validation']['residual_share_of_absolute_attribution']:.1%} of absolute attribution; "
            f"limit {s['validation']['maximum_residual_share']:.0%}); net residual is "
            f"{s['validation']['absolute_net_residual_bps']:.1f} bps "
            f"(limit {s['validation']['maximum_absolute_net_residual_bps']:.1f}).",
            "",
            "| Bucket | bps | Group | Fixability |",
            "|---|---|---|---|",
        ]
        for b in BUCKETS:
            info = s["buckets"][b]
            L.append(f"| {b} | {info['bps']:+.1f} | {info['group']} | {info['fixability']} |")
        ol = s["order_level"]
        L += [
            "",
            f"**Trades we DID** (implementation shortfall, positive = cost): "
            f"{ol['filled_orders']} filled orders; delay ${ol['delay_cost_usd_total']:+,.2f}, "
            f"slippage ${ol['slippage_cost_usd_total']:+,.2f} "
            f"({ol['delay_covered_orders']} orders with full decision+arrival coverage; "
            f"{ol['arrival_proxy_orders']} used an explicitly flagged bar proxy; "
            f"{ol['arrival_missing_orders']} lacked arrival data; "
            f"{ol.get('decision_price_proxy_orders', 0)} used a flagged prior-close decision proxy; "
            f"{ol.get('decision_price_missing_orders', 0)} lacked a decision price).",
            "",
            "**Biggest daily leaks:**",
            "",
        ]
        for leak in s["biggest_daily_leaks"]:
            L.append(f"- {leak['date']} {leak['bucket']}: {leak['cost_bps_day']:+.1f} bps")
        L += ["", f"Data flags: {s['data_flags_count']} (see tca_summary.json).", ""]
    L += [
        "## Caveats",
        "",
        "- Arrival prices use the last valid IEX bid/ask midpoint at or before submission. "
        "A 1-minute bar-open proxy is used only when quotes are unavailable and is explicitly flagged.",
        "- `price_marking` is a measurement bucket: the intended engine marks its book at its "
        "own plan prices; valuing the same book at broker closes differs. It is not a trading leak.",
        "- Days with external cash flows are excluded from the decomposition and flagged.",
        "- Buckets reconcile exactly to each day's gap by construction; `residual` is the "
        "explicit closure term and is reported, never hidden.",
        "",
    ]
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--account", choices=["paper", "live", "both"], default="both")
    args = ap.parse_args()
    accounts = ["paper", "live"] if args.account == "both" else [args.account]

    creds = parse_env_file(ACCOUNT_ENV_FILES["paper"])  # market data works on paper keys
    md = MarketData(creds, TCA_ROOT / "cache")

    src_path, intended_rows = latest_intended_series()
    intended_orders = load_intended_orders()
    log(
        f"intended series: {src_path.relative_to(REPO_ROOT)}; intended orders for "
        f"{len(intended_orders['by_date'])} dates / {len(intended_orders['by_run'])} runs"
    )

    summaries = []
    ok = True
    for account in accounts:
        order_rows = build_order_tca(account, md, intended_orders)
        log(f"{account}: order TCA rows={len(order_rows)}")
        try:
            daily, meta = build_daily_decomposition(account, md, intended_rows, order_rows)
        except SystemExit as exc:
            log(f"{account}: {exc}")
            continue
        log(f"{account}: decomposed {meta['days']} days; flags={len(meta['flags'])}")
        summary = summarize(account, daily, order_rows, meta, str(src_path.relative_to(REPO_ROOT)))
        write_outputs(account, daily, order_rows, summary)
        summaries.append(summary)
        ok = ok and summary["attribution_validated"]
    if summaries:
        atomic_write(TCA_ROOT / "TCA_REPORT.md", render_report(summaries))
        log(f"wrote {TCA_ROOT / 'TCA_REPORT.md'}; reconciles={ok}")
    return 0 if ok and summaries else 1


if __name__ == "__main__":
    sys.exit(main())

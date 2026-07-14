#!/usr/bin/env python3
"""Build the durable, append-only broker-truth ledger from Alpaca.

Pulls, for one or both accounts (paper lane and live pilot, each with its own
credentials file):
  - portfolio history (daily equity/NAV series, back to account inception)
  - account activities (every fill, dividend, fee, transfer; paginated)
  - orders (status=all, paginated; needed downstream for TCA arrival prices)
  - current positions + account snapshot

READ-ONLY against Alpaca: only GET requests are issued. Never places, cancels,
or modifies orders. Does not read or write any trading gate.

Durable store layout (append-only; reruns are idempotent):
  outputs/ledger/<account>/
    activities.jsonl        one line per activity, deduped by activity id
    orders.jsonl            one line per (order id, updated_at), last-wins on read
    daily_nav.csv           one row per trading day from Alpaca portfolio history;
                            existing rows are NEVER rewritten (restatements are
                            flagged in manifest.json, not applied)
    fills.csv               normalized per-fill view derived from activities.jsonl
    positions/positions_<date>.json   point-in-time snapshot at pull time
    account_snapshots.jsonl one line per pull
    manifest.json           watermarks, gap flags, reconciliation results

Usage:
  python3 scripts/build_broker_truth_ledger.py --account both
  python3 scripts/build_broker_truth_ledger.py --account live --verbose
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
LEDGER_ROOT = REPO_ROOT / "outputs" / "ledger"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

ACCOUNT_ENV_FILES = {
    "paper": Path.home() / ".caerus" / "alpaca.env",
    "live": Path.home() / ".caerus" / "live_pilot.env",
}

# Activity types that are EXTERNAL capital flows (not P&L): used by the
# performance report to flow-adjust daily returns.
EXTERNAL_FLOW_TYPES = {"CSD", "CSW", "TRANS", "ACATC", "ACATS", "JNLC", "JNLS"}

NAV_COLUMNS = [
    "date",
    "equity",
    "profit_loss",
    "profit_loss_pct",
    "base_value",
    "source",
    "pulled_at_utc",
]

FILL_COLUMNS = [
    "activity_id",
    "transaction_time_utc",
    "trade_date_et",
    "symbol",
    "side",
    "qty",
    "price",
    "multiplier",
    "notional",
    "order_id",
    "fill_type",
    "cum_qty",
    "leaves_qty",
]

DAILY_STATE_SCHEMA = "caerus_broker_truth_daily_state_v1"
OCC_OPTION_SYMBOL = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")


def log(msg: str) -> None:
    print(f"[broker_ledger] {msg}", flush=True)


def parse_env_file(path: Path) -> dict:
    """Parse KEY=VALUE / export KEY=VALUE lines. Values never logged."""
    creds = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip().strip("'\"")
            creds[key] = val
    return creds


class AlpacaReadOnly:
    """Read-only Alpaca client: GET requests only, by construction."""

    def __init__(self, creds: dict, timeout: int = 30):
        self.base = creds.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets").rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "APCA-API-KEY-ID": creds["ALPACA_API_KEY_ID"],
                "APCA-API-SECRET-KEY": creds["ALPACA_API_SECRET_KEY"],
            }
        )
        self.timeout = timeout

    def get(self, path: str, params: dict | None = None):
        url = f"{self.base}{path}"
        resp = self.session.get(url, params=params or {}, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def account(self) -> dict:
        return self.get("/v2/account")

    def positions(self) -> list:
        return self.get("/v2/positions")

    def portfolio_history(self, inception_date: dt.date) -> dict:
        """Daily portfolio history from account inception to today.

        Alpaca documents 1D history as available since account creation.  Use
        explicit start/end bounds so the requested coverage is auditable and
        does not depend on a relative ``period`` being interpreted as expected.
        """
        return self.get(
            "/v2/account/portfolio/history",
            {
                "start": inception_date.isoformat(),
                "end": dt.date.today().isoformat(),
                "timeframe": "1D",
                "intraday_reporting": "market_hours",
                "pnl_reset": "no_reset",
                "cashflow_types": "ALL",
            },
        )

    def daily_closes(self, symbols: list[str], start: str, end: str) -> dict[str, dict[str, float]]:
        """Return Alpaca stock/option daily closes, keyed by requested symbol."""
        if not symbols:
            return {}
        option_symbols = sorted(symbol for symbol in symbols if OCC_OPTION_SYMBOL.match(symbol))
        stock_symbols = sorted(set(symbols) - set(option_symbols))
        alias_path = REPO_ROOT / "data" / "security_master" / "manual_aliases.json"
        aliases = {}
        if alias_path.exists():
            payload = json.loads(alias_path.read_text())
            aliases = payload.get("aliases") or payload.get("ticker_aliases") or {}
        provider_to_requested: dict[str, list[str]] = defaultdict(list)
        for symbol in stock_symbols:
            provider_to_requested[aliases.get(symbol, symbol)].append(symbol)

        out: dict[str, dict[str, float]] = defaultdict(dict)
        data_base = "https://data.alpaca.markets"
        provider_symbols = sorted(provider_to_requested)
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
                resp = self.session.get(
                    f"{data_base}/v2/stocks/bars", params=params, timeout=self.timeout
                )
                resp.raise_for_status()
                payload = resp.json()
                for provider_symbol, bars in (payload.get("bars") or {}).items():
                    for requested_symbol in provider_to_requested.get(provider_symbol, [provider_symbol]):
                        for bar in bars:
                            day = parse_iso(bar["t"]).astimezone(
                                __import__("zoneinfo").ZoneInfo("America/New_York")
                            ).date().isoformat()
                            out[requested_symbol][day] = float(bar["c"])
                token = payload.get("next_page_token")
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
                resp = self.session.get(
                    f"{data_base}/v1beta1/options/bars", params=params, timeout=self.timeout
                )
                resp.raise_for_status()
                payload = resp.json()
                for symbol, bars in (payload.get("bars") or {}).items():
                    for bar in bars:
                        day = parse_iso(bar["t"]).astimezone(
                            __import__("zoneinfo").ZoneInfo("America/New_York")
                        ).date().isoformat()
                        out[symbol][day] = float(bar["c"])
                token = payload.get("next_page_token")
                if not token:
                    break
        return {symbol: dict(values) for symbol, values in out.items()}

    def activities_all(self, after_iso: str | None = None) -> list:
        """All account activities ascending, following page_token pagination."""
        out = []
        params = {"direction": "asc", "page_size": 100}
        if after_iso:
            params["after"] = after_iso
        page_token = None
        while True:
            if page_token:
                params["page_token"] = page_token
            batch = self.get("/v2/account/activities", params)
            if not batch:
                break
            out.extend(batch)
            if len(batch) < 100:
                break
            page_token = batch[-1]["id"]
        return out

    def orders_all(self, after_iso: str | None = None) -> list:
        """All orders (status=all) ascending by submitted_at, paginated."""
        out = []
        seen = set()
        after = after_iso
        while True:
            params = {"status": "all", "limit": 500, "direction": "asc", "nested": "false"}
            if after:
                params["after"] = after
            batch = self.get("/v2/orders", params)
            fresh = [o for o in batch if o["id"] not in seen]
            for o in fresh:
                seen.add(o["id"])
            out.extend(fresh)
            if len(batch) < 500 or not fresh:
                break
            after = batch[-1]["submitted_at"]
        return out


# --------------------------------------------------------------------------
# Append-only store helpers
# --------------------------------------------------------------------------

def read_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def append_jsonl(path: Path, rows: list) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def append_revisioned_daily_state(path: Path, rows: list[dict], recorded_at: str) -> int:
    """Append only new or changed daily-state facts.

    A correction never destroys the prior observation: it receives the next
    revision for that date. An identical rerun appends nothing.
    """
    existing = read_jsonl(path)
    latest: dict[str, dict] = {}
    for row in existing:
        current = latest.get(row["date"])
        if current is None or int(row.get("revision", 1)) > int(current.get("revision", 1)):
            latest[row["date"]] = row
    additions = []
    for row in rows:
        canonical = json.dumps(row, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        prior = latest.get(row["date"])
        if prior and prior.get("content_sha256") == digest:
            continue
        additions.append(
            {
                **row,
                "revision": int(prior.get("revision", 0)) + 1 if prior else 1,
                "recorded_at_utc": recorded_at,
                "content_sha256": digest,
            }
        )
    append_jsonl(path, additions)
    return len(additions)


def read_csv_rows(path: Path) -> list:
    if not path.exists():
        return []
    with path.open() as fh:
        return list(csv.DictReader(fh))


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(iso_ts: str) -> dt.datetime:
    """ISO parse tolerant of Z suffix and non-6-digit fractional seconds
    (Python 3.10's fromisoformat rejects e.g. '.52469+00:00')."""
    s = iso_ts.replace("Z", "+00:00")
    m = re.match(r"^(.*?)\.(\d+)(\+\d{2}:\d{2}|-\d{2}:\d{2})?$", s)
    if m:
        frac = (m.group(2) + "000000")[:6]
        s = f"{m.group(1)}.{frac}{m.group(3) or ''}"
    return dt.datetime.fromisoformat(s)


def et_trade_date(iso_ts: str) -> str:
    """Trade date in America/New_York for an ISO timestamp."""
    from zoneinfo import ZoneInfo

    return parse_iso(iso_ts).astimezone(ZoneInfo("America/New_York")).date().isoformat()


def missing_trading_dates(first: dt.date, last: dt.date, have: set[str]) -> list[str]:
    """Missing exchange sessions, excluding weekends and market holidays."""
    try:
        from paper.trading_calendar import is_trading_day
    except Exception:
        is_trading_day = lambda value: dt.date.fromisoformat(value).weekday() < 5  # type: ignore
    missing = []
    cursor = first
    while cursor < last:
        value = cursor.isoformat()
        if is_trading_day(value) and value not in have:
            missing.append(value)
        cursor += dt.timedelta(days=1)
    return missing


def build_daily_states(nav_rows: list[dict], fills: list[dict], closes: dict) -> tuple[list[dict], list[str]]:
    """Reconstruct EOD quantities from fills and mark them with Alpaca closes.

    Alpaca exposes historical NAV and activities, but not historical position
    snapshots. Quantities are therefore the exact cumulative fill ledger (and
    are independently reconciled to the current positions endpoint); market
    value is quantity times Alpaca's EOD close. Cash is the balancing component
    of broker NAV and marked positions. Incomplete valuation is explicit.
    """
    by_date: dict[str, list[dict]] = defaultdict(list)
    for fill in fills:
        by_date[fill["trade_date_et"]].append(fill)
    dates = sorted(by_date)
    idx = 0
    qty: dict[str, float] = defaultdict(float)
    states = []
    flags = []
    for nav in sorted(nav_rows, key=lambda row: row["date"]):
        day = nav["date"]
        while idx < len(dates) and dates[idx] <= day:
            for fill in by_date[dates[idx]]:
                sign = 1.0 if fill.get("side") == "buy" else -1.0
                qty[fill["symbol"]] += sign * float(fill.get("qty") or 0)
            idx += 1
        positions = []
        missing = []
        market_value = 0.0
        for symbol in sorted(qty):
            amount = qty[symbol]
            if abs(amount) < 1e-9:
                continue
            close = (closes.get(symbol) or {}).get(day)
            if close is None:
                missing.append(symbol)
                positions.append(
                    {"symbol": symbol, "qty": round(amount, 9), "close": None,
                     "multiplier": 100.0 if OCC_OPTION_SYMBOL.match(symbol) else 1.0,
                     "market_value": None}
                )
                continue
            multiplier = 100.0 if OCC_OPTION_SYMBOL.match(symbol) else 1.0
            value = amount * float(close) * multiplier
            market_value += value
            positions.append(
                {
                    "symbol": symbol,
                    "qty": round(amount, 9),
                    "close": round(float(close), 8),
                    "multiplier": multiplier,
                    "market_value": round(value, 6),
                }
            )
        equity = float(nav["equity"])
        complete = not missing
        if missing:
            flags.append(f"DAILY_STATE_UNPRICED date={day} symbols={','.join(missing)}")
        states.append(
            {
                "schema_version": DAILY_STATE_SCHEMA,
                "date": day,
                "equity": round(equity, 6),
                "cash": round(equity - market_value, 6) if complete else None,
                "positions_market_value": round(market_value, 6) if complete else None,
                "positions": positions,
                "valuation_complete": complete,
                "quantity_source": "alpaca_fill_activities_cumulative",
                "price_source": "alpaca_iex_daily_close",
                "cash_source": "alpaca_portfolio_history_equity_minus_marked_positions",
            }
        )
    return states, flags


# --------------------------------------------------------------------------
# Per-account build
# --------------------------------------------------------------------------

def build_account_ledger(account: str, env_file: Path, verbose: bool = False, rebuild_nav: bool = False) -> dict:
    creds = parse_env_file(env_file)
    missing = [k for k in ("ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY") if not creds.get(k)]
    if missing:
        raise SystemExit(f"{env_file}: missing {missing}")
    client = AlpacaReadOnly(creds)
    outdir = LEDGER_ROOT / account
    outdir.mkdir(parents=True, exist_ok=True)
    pulled_at = utc_now_iso()

    manifest_path = outdir / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    flags: list[str] = []

    # ---- account snapshot -------------------------------------------------
    acct = client.account()
    inception = parse_iso(acct["created_at"]).date()
    snap = {
        "pulled_at_utc": pulled_at,
        "equity": acct.get("equity"),
        "cash": acct.get("cash"),
        "long_market_value": acct.get("long_market_value"),
        "short_market_value": acct.get("short_market_value"),
        "buying_power": acct.get("buying_power"),
        "status": acct.get("status"),
        "created_at": acct.get("created_at"),
        "account_number_last4": str(acct.get("account_number", ""))[-4:],
    }
    append_jsonl(outdir / "account_snapshots.jsonl", [snap])

    # ---- activities (append-only, dedupe by id) ---------------------------
    existing_acts = read_jsonl(outdir / "activities.jsonl")
    seen_ids = {a["id"] for a in existing_acts}
    # Re-pull with a 7-day overlap before the watermark so late-posted
    # activities are never missed; dedupe keeps it idempotent.
    after = None
    if existing_acts:
        last_dt = max(a.get("transaction_time") or a.get("date") or "" for a in existing_acts)
        if last_dt:
            after_date = dt.datetime.fromisoformat(last_dt.split("T")[0])
            after = (after_date - dt.timedelta(days=7)).strftime("%Y-%m-%d")
    fetched = client.activities_all(after_iso=after)
    new_acts = [a for a in fetched if a["id"] not in seen_ids]
    append_jsonl(outdir / "activities.jsonl", new_acts)
    all_acts = existing_acts + new_acts
    log(f"{account}: activities total={len(all_acts)} new={len(new_acts)}")

    # ---- orders (append-only, dedupe by (id, updated_at)) -----------------
    existing_orders = read_jsonl(outdir / "orders.jsonl")
    seen_orders = {(o["id"], o.get("updated_at")) for o in existing_orders}
    order_after = None
    if existing_orders:
        last_sub = max((o.get("submitted_at") or "") for o in existing_orders)
        if last_sub:
            sub_date = parse_iso(last_sub)
            order_after = (sub_date - dt.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    fetched_orders = client.orders_all(after_iso=order_after)
    new_orders = [o for o in fetched_orders if (o["id"], o.get("updated_at")) not in seen_orders]
    append_jsonl(outdir / "orders.jsonl", new_orders)
    log(f"{account}: orders total={len(existing_orders) + len(new_orders)} new={len(new_orders)}")

    # ---- fills.csv: deterministic derived view of FILL activities ---------
    fills = []
    for a in all_acts:
        if a.get("activity_type") != "FILL":
            continue
        qty = float(a.get("qty") or 0)
        price = float(a.get("price") or 0)
        multiplier = 100.0 if OCC_OPTION_SYMBOL.match(a.get("symbol") or "") else 1.0
        fills.append(
            {
                "activity_id": a["id"],
                "transaction_time_utc": a.get("transaction_time"),
                "trade_date_et": et_trade_date(a["transaction_time"]),
                "symbol": a.get("symbol"),
                "side": a.get("side"),
                "qty": qty,
                "price": price,
                "multiplier": multiplier,
                "notional": round(qty * price * multiplier, 6),
                "order_id": a.get("order_id"),
                "fill_type": a.get("type"),
                "cum_qty": a.get("cum_qty"),
                "leaves_qty": a.get("leaves_qty"),
            }
        )
    fills.sort(key=lambda r: (r["transaction_time_utc"] or "", r["activity_id"]))
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=FILL_COLUMNS)
    writer.writeheader()
    writer.writerows(fills)
    atomic_write(outdir / "fills.csv", buf.getvalue())
    log(f"{account}: fills={len(fills)}")

    # ---- daily NAV from portfolio history (append-only) -------------------
    from zoneinfo import ZoneInfo

    ph = client.portfolio_history(inception)
    timestamps = ph.get("timestamp") or []
    fresh_nav: dict[str, dict] = {}
    for i, ts in enumerate(timestamps):
        # Bar timestamps must be read in ET: Friday EOD bars land after
        # midnight UTC and would otherwise be dated Saturday.
        d = dt.datetime.fromtimestamp(ts, ZoneInfo("America/New_York")).date()
        if d < inception:
            continue  # Alpaca pads the window before account creation
        equity = ph["equity"][i]
        if equity is None:
            continue
        fresh_nav[d.isoformat()] = {
            "date": d.isoformat(),
            "equity": equity,
            "profit_loss": (ph.get("profit_loss") or [None] * len(timestamps))[i],
            "profit_loss_pct": (ph.get("profit_loss_pct") or [None] * len(timestamps))[i],
            "base_value": ph.get("base_value"),
            "source": "alpaca_portfolio_history",
            "pulled_at_utc": pulled_at,
        }

    nav_path = outdir / "daily_nav.csv"
    if rebuild_nav and nav_path.exists():
        backup = nav_path.with_suffix(f".csv.pre_rebuild.{pulled_at.replace(':', '')}")
        nav_path.rename(backup)
        flags.append(f"NAV_REBUILT prior file kept at {backup.name}")
        log(f"{account}: --rebuild-nav — prior daily_nav.csv kept at {backup.name}")
    existing_nav = read_csv_rows(nav_path)
    existing_dates = {r["date"] for r in existing_nav}
    today_iso = dt.date.today().isoformat()

    # Append-only guarantee: never rewrite a stored past row. If Alpaca now
    # reports a different equity for a stored past date, flag it.
    nav_restatements = []
    for r in existing_nav:
        if r["date"] in fresh_nav and r["date"] < today_iso:
            old_eq = float(r["equity"])
            new_eq = float(fresh_nav[r["date"]]["equity"])
            if abs(old_eq - new_eq) > 0.01:
                nav_restatements.append(
                    {"date": r["date"], "stored_equity": old_eq, "alpaca_equity": new_eq}
                )
                flags.append(
                    f"RESTATEMENT date={r['date']} stored={old_eq} alpaca_now={new_eq}"
                )

    # Today's row is provisional (intraday equity) — re-write it on each pull
    # until the day is over; past rows are frozen.
    new_rows = [v for k, v in sorted(fresh_nav.items()) if k not in existing_dates]
    kept = [r for r in existing_nav if not (r["date"] == today_iso and today_iso in fresh_nav)]
    if today_iso in fresh_nav and today_iso in existing_dates:
        new_rows = [r for r in new_rows if r["date"] != today_iso] + [fresh_nav[today_iso]]
    merged = kept + new_rows
    merged.sort(key=lambda r: r["date"])
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=NAV_COLUMNS)
    writer.writeheader()
    for r in merged:
        writer.writerow({k: r.get(k, "") for k in NAV_COLUMNS})
    atomic_write(nav_path, buf.getvalue())
    log(f"{account}: daily_nav rows={len(merged)} new={len(new_rows)}")

    # Gap detection: actual exchange sessions only (weekends and market
    # holidays are expected absences, not coverage failures).
    have = {r["date"] for r in merged}
    missing_days = missing_trading_dates(inception, dt.date.today(), have)
    if missing_days:
        flags.append(f"NAV_GAPS n={len(missing_days)} days={missing_days[:10]}")

    # ---- historical EOD positions + cash ----------------------------------
    # Historical position snapshots are not an Alpaca trading endpoint. Build
    # the exact quantity path from broker fills, mark with Alpaca EOD prices,
    # and balance to the broker's own NAV. The append-only source preserves
    # revisions; daily_state_latest.json is only a convenient materialization.
    symbols = sorted({f["symbol"] for f in fills})
    closes = client.daily_closes(
        symbols,
        merged[0]["date"] if merged else inception.isoformat(),
        merged[-1]["date"] if merged else today_iso,
    )
    daily_states, state_flags = build_daily_states(merged, fills, closes)
    flags.extend(state_flags)
    state_appended = append_revisioned_daily_state(
        outdir / "daily_state.jsonl", daily_states, pulled_at
    )
    atomic_write(
        outdir / "daily_state_latest.json",
        json.dumps({"schema_version": DAILY_STATE_SCHEMA, "days": daily_states}, indent=2, sort_keys=True),
    )
    log(f"{account}: daily_state rows={len(daily_states)} appended_revisions={state_appended}")

    # ---- positions snapshot ------------------------------------------------
    positions = client.positions()
    pos_dir = outdir / "positions"
    pos_dir.mkdir(exist_ok=True)
    position_payload = json.dumps(
        {"pulled_at_utc": pulled_at, "positions": positions}, indent=2, sort_keys=True
    )
    pull_token = pulled_at.replace(":", "").replace("-", "")
    atomic_write(
        pos_dir / f"positions_{pull_token}.json",
        position_payload,
    )
    atomic_write(outdir / "positions_latest.json", position_payload)

    # ---- reconciliation ----------------------------------------------------
    recon = reconcile(account, acct, positions, merged, all_acts)
    recon["checks"]["stored_nav_vs_fresh_portfolio_history"] = {
        "overlap_rows": len(existing_dates & set(fresh_nav)),
        "tolerance_dollars": 0.01,
        "restatements": nav_restatements,
        "pass": not nav_restatements,
    }
    recon["pass"] = all(check["pass"] for check in recon["checks"].values())

    manifest.update(
        {
            "account": account,
            "inception_date": inception.isoformat(),
            "last_pull_utc": pulled_at,
            "activities_count": len(all_acts),
            "orders_count": len(existing_orders) + len(new_orders),
            "fills_count": len(fills),
            "nav_rows": len(merged),
            "nav_first_date": merged[0]["date"] if merged else None,
            "nav_last_date": merged[-1]["date"] if merged else None,
            "portfolio_history_request": {
                "start": inception.isoformat(),
                "end": today_iso,
                "timeframe": "1D",
                "pnl_reset": "no_reset",
                "cashflow_types": "ALL",
            },
            "missing_trading_dates": missing_days,
            "daily_state_rows": len(daily_states),
            "daily_state_complete_rows": sum(1 for row in daily_states if row["valuation_complete"]),
            "daily_state_appended_revisions": state_appended,
            "nav_history_reconciliation": {
                "overlap_rows": len(existing_dates & set(fresh_nav)),
                "tolerance_dollars": 0.01,
                "restatements": nav_restatements,
                "pass": not nav_restatements,
            },
            "flags": flags,
            "reconciliation": recon,
        }
    )
    atomic_write(manifest_path, json.dumps(manifest, indent=2, sort_keys=True))
    status = "OK" if recon["pass"] else "RECON_FAIL"
    log(f"{account}: {status} — {json.dumps(recon['checks'])}")
    return manifest


def reconcile(account, acct, positions, nav_rows, activities) -> dict:
    """Cross-checks between independent Alpaca endpoints."""
    checks = {}

    # 1. Current/previous-close equity vs last portfolio-history point. An
    # intraday account equity should not be compared to the prior close with a
    # permissive tolerance when Alpaca exposes last_equity directly.
    eq_acct = float(acct["equity"])
    eq_nav = float(nav_rows[-1]["equity"]) if nav_rows else None
    if eq_nav is not None:
        nav_date = nav_rows[-1]["date"]
        today_et = dt.datetime.now(__import__("zoneinfo").ZoneInfo("America/New_York")).date().isoformat()
        reference_name = "equity" if nav_date == today_et else "last_equity"
        reference_equity = float(acct.get(reference_name) or eq_acct)
        diff_pct = abs(reference_equity - eq_nav) / max(abs(reference_equity), 1e-9)
        checks["equity_vs_portfolio_history"] = {
            "account_equity": eq_acct,
            "reference_field": reference_name,
            "reference_equity": reference_equity,
            "nav_last_equity": eq_nav,
            "nav_last_date": nav_date,
            "diff_pct": round(diff_pct, 6),
            "pass": diff_pct <= 0.0001,
        }

    # 2. equity == cash + market value of positions (internal consistency).
    mv = sum(float(p.get("market_value") or 0) for p in positions)
    cash = float(acct["cash"])
    implied = cash + mv
    diff_pct = abs(eq_acct - implied) / max(eq_acct, 1e-9)
    checks["equity_vs_cash_plus_positions"] = {
        "equity": eq_acct,
        "cash": cash,
        "positions_mv": round(mv, 2),
        "diff_pct": round(diff_pct, 6),
        "pass": diff_pct < 0.005,
    }

    # 3. Position quantities derived from the full fill history vs broker
    #    positions endpoint (catches missing fills / pagination gaps).
    derived: dict[str, float] = {}
    for a in activities:
        if a.get("activity_type") != "FILL":
            continue
        q = float(a.get("qty") or 0)
        sign = 1 if a.get("side") == "buy" else -1
        derived[a["symbol"]] = derived.get(a["symbol"], 0.0) + sign * q
    broker_qty = {p["symbol"]: float(p["qty"]) for p in positions}
    mismatches = []
    for sym in sorted(set(derived) | set(broker_qty)):
        dq = round(derived.get(sym, 0.0), 6)
        bq = round(broker_qty.get(sym, 0.0), 6)
        if abs(dq - bq) > 1e-4:
            mismatches.append({"symbol": sym, "derived_from_fills": dq, "broker": bq})
    checks["positions_vs_fill_history"] = {
        "symbols_checked": len(set(derived) | set(broker_qty)),
        "mismatches": mismatches,
        "pass": not mismatches,
    }

    return {"pass": all(c["pass"] for c in checks.values()), "checks": checks}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--account", choices=["paper", "live", "both"], default="both")
    ap.add_argument("--env-file", help="override credentials env file (single account only)")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument(
        "--rebuild-nav",
        action="store_true",
        help="re-derive daily_nav.csv from a fresh pull (prior file is kept as a backup)",
    )
    args = ap.parse_args()

    accounts = ["paper", "live"] if args.account == "both" else [args.account]
    if args.env_file and len(accounts) > 1:
        ap.error("--env-file requires a single --account")

    rc = 0
    for account in accounts:
        env_file = Path(args.env_file) if args.env_file else ACCOUNT_ENV_FILES[account]
        try:
            manifest = build_account_ledger(
                account, env_file, verbose=args.verbose, rebuild_nav=args.rebuild_nav
            )
            if not manifest["reconciliation"]["pass"]:
                rc = 1
        except Exception as exc:  # keep one account's failure from hiding the other
            log(f"{account}: FAILED — {exc}")
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())

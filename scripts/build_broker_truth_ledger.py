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
import io
import json
import os
import re
import sys
import tempfile
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
LEDGER_ROOT = REPO_ROOT / "outputs" / "ledger"

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
    "notional",
    "order_id",
    "fill_type",
    "cum_qty",
    "leaves_qty",
]


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
        """Daily portfolio history from account inception to today."""
        days = (dt.date.today() - inception_date).days
        # Pick the smallest canonical period that covers inception.
        if days <= 30:
            period = "1M"
        elif days <= 90:
            period = "3M"
        elif days <= 180:
            period = "6M"
        elif days <= 365:
            period = "1A"
        else:
            years = days // 365 + 1
            period = f"{years}A"
        return self.get(
            "/v2/account/portfolio/history",
            {"period": period, "timeframe": "1D"},
        )

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
        fills.append(
            {
                "activity_id": a["id"],
                "transaction_time_utc": a.get("transaction_time"),
                "trade_date_et": et_trade_date(a["transaction_time"]),
                "symbol": a.get("symbol"),
                "side": a.get("side"),
                "qty": qty,
                "price": price,
                "notional": round(qty * price, 6),
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
    for r in existing_nav:
        if r["date"] in fresh_nav and r["date"] < today_iso:
            old_eq = float(r["equity"])
            new_eq = float(fresh_nav[r["date"]]["equity"])
            if abs(old_eq - new_eq) > 0.01:
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

    # Gap detection: weekdays with no NAV row between inception and today.
    have = {r["date"] for r in merged}
    d = inception
    missing_days = []
    while d < dt.date.today():
        if d.weekday() < 5 and d.isoformat() not in have:
            missing_days.append(d.isoformat())
        d += dt.timedelta(days=1)
    if missing_days:
        flags.append(f"NAV_GAPS n={len(missing_days)} days={missing_days[:10]}")

    # ---- positions snapshot ------------------------------------------------
    positions = client.positions()
    pos_dir = outdir / "positions"
    pos_dir.mkdir(exist_ok=True)
    atomic_write(
        pos_dir / f"positions_{today_iso}.json",
        json.dumps({"pulled_at_utc": pulled_at, "positions": positions}, indent=2, sort_keys=True),
    )

    # ---- reconciliation ----------------------------------------------------
    recon = reconcile(account, acct, positions, merged, all_acts)

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

    # 1. Current equity (account endpoint) vs last portfolio-history point.
    eq_acct = float(acct["equity"])
    eq_nav = float(nav_rows[-1]["equity"]) if nav_rows else None
    if eq_nav is not None:
        diff_pct = abs(eq_acct - eq_nav) / max(eq_acct, 1e-9)
        checks["equity_vs_portfolio_history"] = {
            "account_equity": eq_acct,
            "nav_last_equity": eq_nav,
            "nav_last_date": nav_rows[-1]["date"],
            "diff_pct": round(diff_pct, 6),
            # Same-day intraday pulls can drift a little; 2% tolerance.
            "pass": diff_pct < 0.02,
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

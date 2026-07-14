#!/usr/bin/env python3
"""Realized-performance report from the broker-truth ledger.

Reads outputs/ledger/<account>/ (built by build_broker_truth_ledger.py — pure
Alpaca broker truth) and computes REALIZED performance per account:
  - flow-adjusted daily returns (external transfers are not P&L)
  - cumulative + annualized return, vol, Sharpe, max drawdown
  - turnover from actual fills
  - shadow-vs-realized gap over the full overlap with the shadow model NAV

Writes:
  outputs/ledger/<account>/performance.json
  outputs/ledger/realized_performance_report.md

No network access: reads only the durable ledger + local shadow artifacts.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LEDGER_ROOT = REPO_ROOT / "outputs" / "ledger"

# External capital flows (not P&L). Matches build_broker_truth_ledger.py.
EXTERNAL_FLOW_TYPES = {"CSD", "CSW", "TRANS", "ACATC", "ACATS", "JNLC", "JNLS"}

TRADING_DAYS = 252


def read_csv_rows(path: Path) -> list:
    if not path.exists():
        return []
    with path.open() as fh:
        return list(csv.DictReader(fh))


def read_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def flows_by_date(activities: list) -> dict:
    """Net external capital flow per ET date (deposits +, withdrawals -)."""
    flows: dict[str, float] = {}
    for a in activities:
        if a.get("activity_type") not in EXTERNAL_FLOW_TYPES:
            continue
        d = (a.get("date") or (a.get("transaction_time") or "")[:10])
        amt = float(a.get("net_amount") or 0)
        if d:
            flows[d] = flows.get(d, 0.0) + amt
    return flows


def daily_returns(nav_rows: list, flows: dict) -> list:
    """Flow-adjusted daily returns.

    r_t = (E_t - F_t - E_{t-1}) / E_{t-1}, flows treated as start-of-day.
    The series starts at the first day with nonzero equity (pre-funding days
    carry no information).
    """
    rows = [r for r in nav_rows if float(r["equity"] or 0) != 0.0]
    out = []
    for prev, cur in zip(rows, rows[1:]):
        e0, e1 = float(prev["equity"]), float(cur["equity"])
        f = flows.get(cur["date"], 0.0)
        if e0 <= 0:
            continue
        out.append({"date": cur["date"], "ret": (e1 - f - e0) / e0, "equity": e1, "flow": f})
    return out


def perf_metrics(rets: list, fills: list) -> dict:
    if not rets:
        return {"error": "no return observations"}
    r = [x["ret"] for x in rets]
    n = len(r)
    # Chain-linked TWR index
    index = [1.0]
    for x in r:
        index.append(index[-1] * (1 + x))
    cum_ret = index[-1] - 1
    years = n / TRADING_DAYS
    ann_ret = (index[-1]) ** (1 / years) - 1 if years > 0 else None
    mean = sum(r) / n
    var = sum((x - mean) ** 2 for x in r) / (n - 1) if n > 1 else 0.0
    vol_d = math.sqrt(var)
    ann_vol = vol_d * math.sqrt(TRADING_DAYS)
    sharpe = (mean / vol_d * math.sqrt(TRADING_DAYS)) if vol_d > 0 else None

    # Max drawdown on the TWR index
    peak, max_dd, dd_start, dd_trough = index[0], 0.0, None, None
    peak_i = 0
    for i, v in enumerate(index):
        if v > peak:
            peak, peak_i = v, i
        dd = v / peak - 1
        if dd < max_dd:
            max_dd = dd
            dd_start = rets[peak_i - 1]["date"] if peak_i > 0 else rets[0]["date"]
            dd_trough = rets[i - 1]["date"] if i > 0 else rets[0]["date"]

    # Turnover from actual fills over the return window
    first_d, last_d = rets[0]["date"], rets[-1]["date"]
    window_fills = [f for f in fills if first_d <= f["trade_date_et"] <= last_d]
    traded = sum(abs(float(f["notional"])) for f in window_fills)
    avg_equity = sum(x["equity"] for x in rets) / n
    ann_turnover = (traded / 2) / avg_equity / years if years > 0 and avg_equity > 0 else None

    return {
        "start_date": first_d,
        "end_date": last_d,
        "obs_days": n,
        "cumulative_return": round(cum_ret, 6),
        "annualized_return": round(ann_ret, 6) if ann_ret is not None else None,
        "annualized_vol": round(ann_vol, 6),
        "sharpe_rf0": round(sharpe, 4) if sharpe is not None else None,
        "max_drawdown": round(max_dd, 6),
        "max_drawdown_peak_date": dd_start,
        "max_drawdown_trough_date": dd_trough,
        "gross_traded_notional": round(traded, 2),
        "fill_count": len(window_fills),
        "avg_equity": round(avg_equity, 2),
        "annualized_turnover_two_sided_over_2": round(ann_turnover, 4)
        if ann_turnover is not None
        else None,
    }


def load_shadow_series(path: Path, date_col: str, value_col: str) -> dict:
    """date -> shadow NAV value."""
    out = {}
    for r in read_csv_rows(path):
        d, v = r.get(date_col), r.get(value_col)
        if d and v not in (None, ""):
            out[d] = float(v)
    return out


def shadow_vs_realized(rets: list, shadow: dict) -> dict:
    """Cumulative gap between realized TWR and the shadow book over the
    full overlap of dates present in both series.

    Both series are turned into indices and SAMPLED at the common dates, so a
    date missing from one series is bridged identically by both (no skipped
    returns on one side only).
    """
    # Realized TWR index at every realized date
    real_index = {}
    idx = 1.0
    for x in rets:
        idx *= 1 + x["ret"]
        real_index[x["date"]] = idx
    common = [d for d in (x["date"] for x in rets) if d in shadow]
    if len(common) < 2:
        return {"error": "insufficient overlap", "overlap_days": len(common)}
    d0 = common[0]
    r0, s0 = real_index[d0], shadow[d0]
    series = []
    daily_gaps = []
    prev_r, prev_s = 1.0, 1.0
    real_idx = shadow_idx = 1.0
    for d in common[1:]:
        real_idx = real_index[d] / r0
        shadow_idx = shadow[d] / s0
        daily_gaps.append((real_idx / prev_r - 1) - (shadow_idx / prev_s - 1))
        prev_r, prev_s = real_idx, shadow_idx
        series.append(
            {
                "date": d,
                "realized_index": round(real_idx, 6),
                "shadow_index": round(shadow_idx, 6),
                "cum_gap": round(real_idx - shadow_idx, 6),
            }
        )
    n = len(daily_gaps)
    mean_gap = sum(daily_gaps) / n
    years = n / TRADING_DAYS
    return {
        "overlap_start": common[0],
        "overlap_end": common[-1],
        "overlap_days": n,
        "realized_cum_return": round(real_idx - 1, 6),
        "shadow_cum_return": round(shadow_idx - 1, 6),
        "cum_gap_realized_minus_shadow": round((real_idx - 1) - (shadow_idx - 1), 6),
        "mean_daily_gap": round(mean_gap, 8),
        "annualized_gap": round(mean_gap * TRADING_DAYS, 6),
        "gap_annualized_from_cum": round(
            ((real_idx / shadow_idx) ** (1 / years) - 1), 6
        )
        if years > 0
        else None,
        "series_tail": series[-5:],
        "series": series,
    }


def default_shadow_specs() -> list[dict]:
    """The two available model-book series to compare realized NAV against.

    - intended_target_book: the operational-drag engine's frictionless NAV of
      the recorded daily target books (what the model actually decided).
      Latest run directory wins.
    - shadow_polaris: the shadow scorecard NAV for the baseline strategy
      (caveat: the live lane tracks precompute signals, not Polaris directly).
    """
    specs = []
    drag_root = REPO_ROOT / "outputs" / "operational_drag"
    if drag_root.exists():
        runs = sorted(d for d in drag_root.iterdir() if d.is_dir())
        for run in reversed(runs):
            ts = run / "intended_nav_timeseries.csv"
            if ts.exists():
                specs.append(
                    {
                        "name": "intended_target_book",
                        "path": ts,
                        "date_col": "date",
                        "value_col": "intended_equity_value",
                    }
                )
                break
    shadow_csv = REPO_ROOT / "outputs" / "shadow_candidates" / "performance" / "shadow_nav_series.csv"
    if shadow_csv.exists():
        specs.append(
            {
                "name": "shadow_polaris",
                "path": shadow_csv,
                "date_col": "date",
                "value_col": "caerus_polaris",
            }
        )
    return specs


def account_report(account: str, shadow_specs: list[dict]) -> dict:
    accdir = LEDGER_ROOT / account
    nav_rows = read_csv_rows(accdir / "daily_nav.csv")
    acts = read_jsonl(accdir / "activities.jsonl")
    fills = read_csv_rows(accdir / "fills.csv")
    if not nav_rows:
        return {"account": account, "error": "no ledger NAV — run build_broker_truth_ledger.py"}

    flows = flows_by_date(acts)
    rets = daily_returns(nav_rows, flows)
    metrics = perf_metrics(rets, fills)

    result = {
        "account": account,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "external_flows": {k: round(v, 2) for k, v in sorted(flows.items())},
        "realized": metrics,
        "shadow_vs_realized": {},
        "_shadow_series_full": {},
    }
    for spec in shadow_specs:
        shadow = load_shadow_series(spec["path"], spec["date_col"], spec["value_col"])
        gap = shadow_vs_realized(rets, shadow)
        result["shadow_vs_realized"][spec["name"]] = {
            k: v for k, v in gap.items() if k != "series"
        }
        result["shadow_vs_realized"][spec["name"]]["source"] = str(spec["path"].relative_to(REPO_ROOT))
        if gap.get("series"):
            result["_shadow_series_full"][spec["name"]] = gap["series"]
    return result


def render_markdown(reports: list) -> str:
    lines = [
        "# Realized Performance — Broker Truth Ledger",
        "",
        f"Generated {dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} from Alpaca "
        "portfolio history, activities, and fills (read-only pulls; see outputs/ledger/).",
        "",
    ]
    for rep in reports:
        acct = rep["account"]
        lines.append(f"## Account: {acct}")
        if "error" in rep:
            lines.append(f"**ERROR:** {rep['error']}")
            continue
        m = rep["realized"]
        flows = rep["external_flows"]
        lines += [
            "",
            f"Window: **{m['start_date']} → {m['end_date']}** ({m['obs_days']} trading days). "
            f"External flows: {json.dumps(flows) if flows else 'none'}.",
            "",
            "| Metric | Value |",
            "|---|---|",
            f"| Cumulative return (TWR, flow-adjusted) | {m['cumulative_return']:.2%} |",
            f"| Annualized return | {m['annualized_return']:.2%} |",
            f"| Annualized volatility | {m['annualized_vol']:.2%} |",
            f"| Sharpe (rf=0) | {m['sharpe_rf0']} |",
            f"| Max drawdown | {m['max_drawdown']:.2%} ({m['max_drawdown_peak_date']} → {m['max_drawdown_trough_date']}) |",
            f"| Gross traded notional | ${m['gross_traded_notional']:,.0f} ({m['fill_count']} fills) |",
            f"| Annualized turnover (two-sided/2) | {m['annualized_turnover_two_sided_over_2']} |",
            "",
        ]
        for name, gap in (rep.get("shadow_vs_realized") or {}).items():
            lines.append(f"### Shadow vs realized — {name}")
            if "error" in gap:
                lines.append(f"_{gap['error']} (source: {gap.get('source')})_")
            else:
                lines += [
                    "",
                    f"Source: `{gap['source']}`. Overlap: **{gap['overlap_start']} → "
                    f"{gap['overlap_end']}** ({gap['overlap_days']} days — full available overlap).",
                    "",
                    "| | Realized | Shadow | Gap (real − shadow) |",
                    "|---|---|---|---|",
                    f"| Cumulative return | {gap['realized_cum_return']:.2%} | "
                    f"{gap['shadow_cum_return']:.2%} | {gap['cum_gap_realized_minus_shadow']:+.2%} |",
                    "",
                    f"Mean daily gap {gap['mean_daily_gap']:+.5%} → annualized "
                    f"{gap['annualized_gap']:+.2%}.",
                    "",
                ]
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--shadow-csv", help="extra shadow NAV csv to compare against")
    ap.add_argument("--shadow-date-col", default="date")
    ap.add_argument("--shadow-value-col", default="equity")
    args = ap.parse_args()

    shadow_specs = default_shadow_specs()
    if args.shadow_csv:
        shadow_specs.append(
            {
                "name": Path(args.shadow_csv).stem,
                "path": Path(args.shadow_csv),
                "date_col": args.shadow_date_col,
                "value_col": args.shadow_value_col,
            }
        )

    reports = []
    for account in ("paper", "live"):
        # The shadow book models the paper lane's capital; the live pilot is a
        # small real-money tracker of the same targets — realized metrics only.
        rep = account_report(account, shadow_specs if account == "paper" else [])
        series_by_name = rep.pop("_shadow_series_full", {})
        out = LEDGER_ROOT / account / "performance.json"
        out.write_text(json.dumps(rep, indent=2, sort_keys=True))
        for name, series in series_by_name.items():
            (LEDGER_ROOT / account / f"shadow_vs_realized_series_{name}.csv").write_text(
                "date,realized_index,shadow_index,cum_gap\n"
                + "\n".join(
                    f"{r['date']},{r['realized_index']},{r['shadow_index']},{r['cum_gap']}"
                    for r in series
                )
                + "\n"
            )
        reports.append(rep)
        print(f"[ledger_report] {account}: {json.dumps(rep.get('realized', rep), default=str)[:300]}")

    (LEDGER_ROOT / "realized_performance_report.md").write_text(render_markdown(reports))
    print(f"[ledger_report] wrote {LEDGER_ROOT / 'realized_performance_report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

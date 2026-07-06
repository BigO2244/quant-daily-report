#!/usr/bin/env python3
"""
Morning Report — 5-second CIO console summary.

Reads outputs/trading_day_summary.json and prints a clean, formatted
snapshot of yesterday's trading run.

Usage:
    python scripts/morning_report.py
    python scripts/morning_report.py --path /custom/path/trading_day_summary.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_DEFAULT_SUMMARY = _REPO_ROOT / "outputs" / "trading_day_summary.json"

_SEP = "=" * 49


def _fmt_currency(val: float | None) -> str:
    if val is None:
        return "N/A"
    return f"${val:,.0f}"


def _fmt_pct(val: float | None, precision: int = 2) -> str:
    if val is None:
        return "N/A"
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.{precision}f}%"


def _yn(val: bool | None) -> str:
    if val is None:
        return "UNKNOWN"
    return "YES" if val else "NO"


def _source_warning(summary: dict, expected_date: str | None = None) -> str | None:
    trade_date_raw = str(summary.get("trade_date") or "").strip()
    if not trade_date_raw:
        return "summary artifact has no trade_date"
    expected_raw = str(expected_date or dt.date.today().isoformat()).strip()
    try:
        trade_date = dt.date.fromisoformat(trade_date_raw)
        expected = dt.date.fromisoformat(expected_raw)
    except ValueError:
        return None
    age_days = (expected - trade_date).days
    if age_days > 3:
        return (
            f"summary trade_date {trade_date.isoformat()} is {age_days} days older "
            f"than report date {expected.isoformat()}"
        )
    return None


def print_morning_report(summary: dict, expected_date: str | None = None) -> None:
    exec_s = summary.get("execution_summary") or {}
    port_s = summary.get("portfolio_state") or {}
    email_s = summary.get("email_summary") or {}
    dash_s = summary.get("dashboard") or {}
    bench_s = summary.get("benchmark") or {}
    warning = _source_warning(summary, expected_date)

    # Derive portfolio total (market value + cash)
    mv = port_s.get("portfolio_market_value")
    cash = port_s.get("cash_after")
    total_value: float | None = None
    if mv is not None and cash is not None:
        total_value = mv + cash
    elif mv is not None:
        total_value = mv

    print()
    print(_SEP)
    print("  QUANT SYSTEM — MORNING REPORT")
    print(_SEP)
    print()
    print(f"  Run ID:      {summary.get('run_id', 'N/A')}")
    print(f"  Trade Date:  {summary.get('trade_date', 'N/A')}")
    print(f"  Generated:   {summary.get('generated_at', 'N/A')}")
    if warning:
        print(f"  Source Warning: {warning}")
    print()

    print("  EXECUTION")
    print(f"  {'Trades Executed:':<24} {_yn(exec_s.get('executed'))}")
    print(f"  {'Status:':<24} {exec_s.get('status', 'N/A')}")
    print(f"  {'Orders Submitted:':<24} {exec_s.get('orders_submitted', 0)}")
    print(f"  {'Orders Accepted:':<24} {exec_s.get('orders_accepted', 0)}")
    print(f"  {'Duplicate (Replay):':<24} {exec_s.get('duplicate_orders', 0)}")
    print(f"  {'Buy Orders:':<24} {exec_s.get('buy_orders', 0)}")
    print(f"  {'Sell Orders:':<24} {exec_s.get('sell_orders', 0)}")
    print()

    print("  PORTFOLIO")
    print(f"  {'Cash Balance:':<24} {_fmt_currency(port_s.get('cash_after'))}")
    print(f"  {'Positions:':<24} {port_s.get('positions_count', 'N/A')}")
    print(f"  {'Equity (invested):':<24} {_fmt_currency(mv)}")
    print(f"  {'Total Portfolio Value:':<24} {_fmt_currency(total_value)}")
    print()

    print("  EMAIL SYSTEM")
    print(f"  {'Emails Sent:':<24} {email_s.get('emails_sent', 'N/A')}")
    print(f"  {'Expected:':<24} {email_s.get('expected', 3)}")
    print(f"  {'Status:':<24} {email_s.get('status', 'N/A')}")
    print(f"  {'Confirmation Sent:':<24} {_yn(email_s.get('confirmation_sent'))}")
    print()

    print("  DASHBOARD")
    print(f"  {'Generated:':<24} {_yn(dash_s.get('generated'))}")
    if dash_s.get("path"):
        print(f"  {'Path:':<24} {dash_s['path']}")
    print()

    print("  PERFORMANCE VS SPY")
    print(f"  {'Portfolio Return:':<24} {_fmt_pct(bench_s.get('portfolio_return_pct'))}")
    print(f"  {'SPY Return:':<24} {_fmt_pct(bench_s.get('spy_return_pct'))}")
    print(f"  {'Alpha vs SPY:':<24} {_fmt_pct(bench_s.get('performance_vs_spy_pct'))}")
    print(f"  {'Portfolio Value:':<24} {_fmt_currency(bench_s.get('portfolio_value'))}")
    print(f"  {'SPY Close:':<24} {_fmt_currency(bench_s.get('spy_value'))}")
    print()
    print(_SEP)
    print()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Print the morning CIO trading-day summary report."
    )
    parser.add_argument(
        "--path",
        type=Path,
        default=_DEFAULT_SUMMARY,
        help=f"Path to trading_day_summary.json (default: {_DEFAULT_SUMMARY})",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    path = args.path

    if not path.exists():
        print(f"\n  [ERROR] Summary file not found: {path}", file=sys.stderr)
        print("  Run the daily pipeline first or check outputs/trading_day_summary.json\n",
              file=sys.stderr)
        return 1

    try:
        summary = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"\n  [ERROR] Could not parse {path}: {exc}\n", file=sys.stderr)
        return 1

    print_morning_report(summary, expected_date=os.environ.get("REPORT_DATE"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

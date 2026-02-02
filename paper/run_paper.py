# paper/run_paper.py
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from paper.paper_broker import run_paper_day
from paper.trading_calendar import next_trading_day


def main() -> None:
    parser = argparse.ArgumentParser(description="Run paper trading execution for a given signal date.")
    parser.add_argument("signal_date", help="Signal date (YYYY-MM-DD). Executes next business day open.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="DEV ONLY: allow re-running the same trade date (bypasses ledger date guard).",
    )
    args = parser.parse_args()

    signal_date = args.signal_date
    trade_date = next_trading_day(signal_date)

    signals_path = Path("signals") / f"{signal_date}.json"
    if not signals_path.exists():
        raise FileNotFoundError(f"Missing signals file: {signals_path}")

    print(f"[PAPER] Signal date: {signal_date}")
    print(f"[PAPER] Trade (execution) date: {trade_date}")
    print(f"[PAPER] Signals file: {signals_path}")

    # Guard: avoid trying to fetch bars for a future date (Yahoo daily bars won't exist yet)
    today_str = dt.date.today().strftime("%Y-%m-%d")
    if trade_date > today_str:
        raise RuntimeError(
            f"Trade date {trade_date} is in the future vs today {today_str}. "
            "Run this on/after the trade date (or test with a past signal date)."
        )

    result = run_paper_day(
        run_date=trade_date,
        signals_path=str(signals_path),
        ledger_path="paper/ledger.csv",
        trades_path="paper/trades.csv",
        config_path="paper/config_paper.json",
        force=args.force,
    )

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

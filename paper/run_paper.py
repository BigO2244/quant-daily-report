# paper/run_paper.py
from __future__ import annotations

import sys
import json
from pathlib import Path

from paper.paper_broker import run_paper_day
from paper.trading_calendar import next_trading_day


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 paper/run_paper.py <signal_date YYYY-MM-DD>")
        sys.exit(1)

    signal_date = sys.argv[1]
    trade_date = next_trading_day(signal_date)
    import datetime as dt

    today_str = dt.date.today().strftime("%Y-%m-%d")
    if trade_date > today_str:
        raise RuntimeError(
            f"Trade date {trade_date} is in the future vs today {today_str}. "
            "Run this on/after the trade date (or test with a past signal date)."
    )


    today_str = dt.date.today().strftime("%Y-%m-%d")
    if trade_date > today_str:
        raise RuntimeError(
            f"Trade date {trade_date} is in the future vs today {today_str}. "
            "Run this on/after the trade date (or test with a past signal date)."
    )


    signals_path = Path("signals") / f"{signal_date}.json"
    if not signals_path.exists():
        raise FileNotFoundError(f"Missing signals file: {signals_path}")

    print(f"[PAPER] Signal date: {signal_date}")
    print(f"[PAPER] Trade (execution) date: {trade_date}")
    print(f"[PAPER] Signals file: {signals_path}")

    result = run_paper_day(
        run_date=trade_date,
        signals_path=str(signals_path),
        ledger_path="paper/ledger.csv",
        trades_path="paper/trades.csv",
        config_path="paper/config_paper.json"
    )

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

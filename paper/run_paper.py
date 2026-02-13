# paper/run_paper.py
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
from pathlib import Path

from paper.paper_broker import run_paper_day
from paper.state_paths import ensure_paper_state_files
from paper.trading_calendar import next_trading_day

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(
        description="Run paper trading execution for a given signal date."
    )
    parser.add_argument(
        "signal_date", help="Signal date (YYYY-MM-DD). Executes next business day open."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="DEV ONLY: allow re-running the same trade date (bypasses ledger date guard).",
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Generate a plan only; do not generate/send orders even during open market hours.",
    )
    args = parser.parse_args()

    signal_date = args.signal_date
    trade_date = next_trading_day(signal_date)

    signals_path = Path("signals") / f"{signal_date}.json"
    if not signals_path.exists():
        raise FileNotFoundError(f"Missing signals file: {signals_path}")

    logger.info("[PAPER] Signal date: %s", signal_date)
    logger.info("[PAPER] Trade (execution) date: %s", trade_date)
    logger.info("[PAPER] Signals file: %s", signals_path)

    # Guard: avoid trying to fetch bars for a future date (Yahoo daily bars won't exist yet)
    today_str = dt.date.today().strftime("%Y-%m-%d")
    if trade_date > today_str:
        raise RuntimeError(
            f"Trade date {trade_date} is in the future vs today {today_str}. "
            "Run this on/after the trade date (or test with a past signal date)."
        )

    ledger_path, trades_path = ensure_paper_state_files()

    result = run_paper_day(
        run_date=trade_date,
        signals_path=str(signals_path),
        ledger_path=ledger_path,
        trades_path=trades_path,
        config_path="paper/config_paper.json",
        force=args.force,
        plan_only=args.plan_only,
    )

    logger.info("%s", json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

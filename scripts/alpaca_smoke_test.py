#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from brokers.alpaca_broker import AlpacaBroker


def _missing_env_vars() -> list[str]:
    required = ["ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY"]
    return [k for k in required if not str(os.getenv(k, "")).strip()]


def _print_setup_help() -> None:
    print(
        "\nSet Alpaca paper credentials, then retry:\n"
        "  export ALPACA_API_KEY_ID='YOUR_KEY'\n"
        "  export ALPACA_API_SECRET_KEY='YOUR_SECRET'\n"
        "  export ALPACA_PAPER=1\n\n"
        "Run either command:\n"
        "  python3 alpaca_smoke_test.py\n"
        "  python3 scripts/alpaca_smoke_test.py\n",
        file=sys.stderr,
    )


def main() -> int:
    missing = _missing_env_vars()
    if missing:
        print(
            "[ALPACA][SMOKE][FAIL] Missing required env vars: "
            + ", ".join(missing),
            file=sys.stderr,
        )
        _print_setup_help()
        return 2
    try:
        broker = AlpacaBroker.from_env()
        account = broker.get_account()
        positions = broker.get_positions()
    except Exception as exc:
        print(f"[ALPACA][SMOKE][FAIL] {exc}", file=sys.stderr)
        _print_setup_help()
        return 1

    print("[ALPACA][SMOKE][OK]")
    print(
        json.dumps(
            {
                "paper": broker.paper,
                "account": {
                    "id": account.get("id"),
                    "status": account.get("status"),
                    "cash": account.get("cash"),
                    "equity": account.get("equity"),
                    "buying_power": account.get("buying_power"),
                },
                "positions_count": len(positions),
                "positions_preview": [
                    {
                        "symbol": p.get("symbol"),
                        "qty": p.get("qty"),
                        "market_value": p.get("market_value"),
                    }
                    for p in positions[:10]
                ],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.options_smoke_session import build_options_smoke_session
from core.execution_authority_policy import require_options_capital_disabled


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a safe options smoke session (open one pair or close next session).")
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--asof-date", default=None)
    parser.add_argument("--state-root", default="outputs/options_execution")
    parser.add_argument("--policy-path", default="config/options_smoke_session_policy.json")
    parser.add_argument("--submit", action="store_true", help="Actually submit the paper session orders.")
    args = parser.parse_args(argv)
    if args.submit:
        require_options_capital_disabled(
            mutation_path="scripts.options_smoke_session"
        )

    api_key = os.environ.get("ALPACA_API_KEY_ID") or os.environ.get("ALPACA_KEY_ID")
    api_secret = os.environ.get("ALPACA_API_SECRET_KEY") or os.environ.get("ALPACA_SECRET_KEY")
    base_url = (os.environ.get("ALPACA_BASE_URL") or "https://paper-api.alpaca.markets").rstrip("/")
    if not api_key or not api_secret:
        raise RuntimeError("Missing Alpaca API credentials in environment")

    headers = {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": api_secret,
        "Content-Type": "application/json",
    }

    def _get(path: str) -> dict:
        response = requests.get(f"{base_url}{path}", headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()

    def _post(path: str, payload: dict) -> dict:
        response = requests.post(
            f"{base_url}{path}",
            headers=headers,
            data=json.dumps(payload),
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    class PaperBroker:
        def get_account(self) -> dict:
            return _get("/v2/account")

        def get_positions(self) -> list[dict]:
            return _get("/v2/positions")

        def submit_option_market_order(self, symbol: str, qty: float, side: str, client_order_id: str, tif: str = "day"):
            payload = {
                "symbol": symbol,
                "qty": qty,
                "side": side,
                "type": "market",
                "time_in_force": tif,
                "client_order_id": client_order_id,
            }
            return _post("/v2/orders", payload)

    broker = PaperBroker()
    account = broker.get_account()
    positions = broker.get_positions()
    review = build_options_smoke_session(
        trade_date=args.trade_date,
        asof_date=args.asof_date,
        broker=broker,
        account=account,
        positions=positions,
        policy_path=args.policy_path,
        state_root=args.state_root,
        allow_submission=bool(args.submit),
    )
    if args.submit and review.get("submitted_count", 0) > 0:
        review["submitted_orders"] = [
            dict(order) for order in review.get("submitted_orders") or []
        ]
    print(json.dumps(review, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

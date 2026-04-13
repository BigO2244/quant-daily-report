#!/usr/bin/env python3
from __future__ import annotations

import datetime
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.export_alpaca_broker_snapshot import fetch_snapshot_inputs


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


def _canonical_alpaca_base() -> str:
    base = (os.getenv("ALPACA_BASE_URL") or "https://paper-api.alpaca.markets").strip().rstrip("/")
    if base.endswith("/v2"):
        base = base[:-3]
    return base


def _probe_account_endpoint(url: str, key: str, secret: str) -> tuple[int, str]:
    req = urllib.request.Request(
        url,
        headers={
            "APCA-API-KEY-ID": key,
            "APCA-API-SECRET-KEY": secret,
        },
    )
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return int(getattr(resp, "status", 200)), body[:200]
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = str(exc)
        return int(exc.code), body[:200]


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

    key = str(os.getenv("ALPACA_API_KEY_ID", "")).strip()
    secret = str(os.getenv("ALPACA_API_SECRET_KEY", "")).strip()
    paper_raw = str(os.getenv("ALPACA_PAPER", "")).strip() or "unset"
    base = _canonical_alpaca_base()
    url = f"{base}/v2/account"
    print(
        f"[ALPACA][SMOKE] url={url} key_set={bool(key)} secret_set={bool(secret)} paper={paper_raw}"
    )
    try:
        status_code, body_prefix = _probe_account_endpoint(url=url, key=key, secret=secret)
    except Exception as exc:
        print(f"[ALPACA][SMOKE][FAIL] probe_exception={exc!r}", file=sys.stderr)
        return 1
    if status_code == 404:
        print(
            f"[ALPACA][SMOKE][FAIL] status=404 url={url} body={body_prefix}",
            file=sys.stderr,
        )
        return 1
    if status_code >= 400:
        print(
            f"[ALPACA][SMOKE][FAIL] status={status_code} url={url} body={body_prefix}",
            file=sys.stderr,
        )
        return 1

    try:
        account, positions, _, _, _, source_mode = fetch_snapshot_inputs(
            report_date=_resolve_trade_date(),
            order_limit=25,
        )
    except Exception as exc:
        print(f"[ALPACA][SMOKE][FAIL] {exc}", file=sys.stderr)
        _print_setup_help()
        return 1

    print("[ALPACA][SMOKE][OK]")
    print(
        json.dumps(
            {
                "source_mode": source_mode,
                "paper": paper_raw,
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


def _resolve_trade_date() -> str:
    try:
        from zoneinfo import ZoneInfo

        return datetime.datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        return datetime.datetime.utcnow().date().isoformat()


if __name__ == "__main__":
    raise SystemExit(main())

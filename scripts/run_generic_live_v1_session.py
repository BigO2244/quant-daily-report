#!/usr/bin/env python3
"""Thin generic Live v1 runner; default is validation-only/no-write."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
from pathlib import Path

from brokers.alpaca_broker import AlpacaBroker
from core.generic_live_v1_submission import (
    execute_generic_live_v1_session,
    rearm_generic_live_v1_session,
)


def _read(path: Path) -> dict:
    def no_duplicates(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate JSON key: {key}")
            value[key] = item
        return value

    def no_constant(value):
        raise ValueError(f"non-finite JSON constant: {value}")

    payload = json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=no_duplicates,
        parse_constant=no_constant,
    )
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain an object")
    return payload


def _require_exact_env(preflight: dict, *, submit: bool) -> None:
    expected = {
        "CAERUS_GENERIC_LIVE_ACCOUNT_ID_HASH": preflight["account_id_hash"],
        "CAERUS_GENERIC_LIVE_CAPITAL_CEILING_USD": "460",
        "CAERUS_GENERIC_LIVE_MINIMUM_TRADE_USD": "100",
        "CAERUS_GENERIC_LIVE_MAX_ORDERS": "1",
        "CAERUS_GENERIC_LIVE_MAXIMUM_GROSS_FRACTION": "0.95",
        "CAERUS_GENERIC_LIVE_EFFECTIVE_SESSION": preflight["effective_session"],
        "CAERUS_GENERIC_LIVE_ADAPTER_CONTRACT": "CAERUS_GENERIC_LANE_V4",
        "CAERUS_GENERIC_LIVE_ELIGIBLE_SLEEVE": "caerus_lyra",
        "CAERUS_GENERIC_LIVE_OWNER_DECISION_HASH": preflight["owner_decision_hash"],
        "CAERUS_GENERIC_LIVE_PREFLIGHT_HASH": preflight["content_hash"],
    }
    mismatches = [key for key, value in expected.items() if os.environ.get(key) != value]
    if mismatches:
        raise RuntimeError("generic Live v1 environment mismatch: " + ",".join(sorted(mismatches)))
    if os.environ.get("CAERUS_GENERIC_PAPER_CUTOVER", "0") != "0":
        raise RuntimeError("generic PAPER cutover must remain disabled")
    if os.environ.get("CAERUS_LEGACY_LIVE_EXECUTOR_ENABLED", "0") != "0":
        raise RuntimeError("legacy Live executor must remain disabled")
    if submit:
        for key in (
            "CAERUS_GENERIC_LIVE_OWNER_APPROVED",
            "CAERUS_GENERIC_LIVE_SUBMIT_APPROVED",
            "CAERUS_GENERIC_LIVE_SCHEDULE_ENABLED",
        ):
            if os.environ.get(key) != "1":
                raise RuntimeError(f"generic Live submission gate is not approved: {key}")
        if os.environ.get("ALPACA_PAPER") != "0" or os.environ.get("ALPACA_BASE_URL", "").rstrip("/") != "https://api.alpaca.markets":
            raise RuntimeError("generic Live submission requires canonical Alpaca Live environment")
        expected_deployed = os.environ.get("CAERUS_GENERIC_LIVE_DEPLOYED_SHA", "")
        observed_deployed = subprocess.run(
            ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
        ).stdout.strip()
        if expected_deployed != observed_deployed or preflight["deployed_sha"] != observed_deployed:
            raise RuntimeError("generic Live deployed SHA pin mismatch")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--exact-plan", type=Path, required=True)
    parser.add_argument("--executed-at", required=True)
    parser.add_argument("--wal-directory", type=Path)
    parser.add_argument("--session-gate-path", type=Path)
    parser.add_argument("--result-path", type=Path)
    parser.add_argument("--submit-exact-session", action="store_true")
    args = parser.parse_args()
    preflight = _read(args.preflight)
    plan = _read(args.exact_plan)
    try:
        if os.environ.get("CAERUS_GENERIC_LIVE_PLAN_HASH") != plan.get("content_hash"):
            raise RuntimeError("generic Live exact plan environment pin mismatch")
        _require_exact_env(preflight, submit=args.submit_exact_session)
        broker = AlpacaBroker.from_env() if args.submit_exact_session else None
        result = execute_generic_live_v1_session(
            activation_preflight=preflight, exact_plan=plan,
            executed_at=args.executed_at, submit_enabled=args.submit_exact_session,
            broker=broker, wal_directory=args.wal_directory,
            rearm_state_path=args.session_gate_path,
            result_path=args.result_path,
        )
    except Exception:
        if args.submit_exact_session and args.session_gate_path is not None:
            armed = False
            try:
                armed = json.loads(args.session_gate_path.read_text()).get("status") == "ARMED"
            except Exception:
                pass
            if not armed:
                rearm_generic_live_v1_session(
                    state_path=args.session_gate_path,
                    preflight_hash=str(preflight.get("content_hash") or ""),
                    plan_hash=str(plan.get("content_hash") or ""),
                    rearmed_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                    trigger="PREFLIGHT_BREAK",
                )
        raise
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

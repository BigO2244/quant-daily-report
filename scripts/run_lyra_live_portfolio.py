#!/usr/bin/env python3
"""Build and optionally execute the exact owner-approved Lyra Live basket."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from brokers.alpaca_broker import AlpacaBroker  # noqa: E402
from core.lyra_live_execution import _write_exclusive, execute_portfolio_plan  # noqa: E402
from core.lyra_live_portfolio import (  # noqa: E402
    build_portfolio_plan,
    validate_owner_decision,
    validate_plan,
    validate_target_source,
)


ET = ZoneInfo("America/New_York")


def _read_json(path: Path) -> dict:
    if not path.is_absolute() or path.is_symlink():
        raise RuntimeError("governed JSON path must be absolute and non-symlink")
    return json.loads(path.read_text(encoding="utf-8"))


def _require_runtime(*, decision: dict, submit: bool) -> None:
    if os.environ.get("CAERUS_LYRA_LIVE_OWNER_DECISION_HASH") != decision["content_hash"]:
        raise RuntimeError("Lyra Live runtime owner pin differs")
    if submit:
        expected = {
            "CAERUS_LYRA_LIVE_ENABLED": "1",
            "CAERUS_LYRA_LIVE_SUBMIT_APPROVED": "1",
            "ALPACA_PAPER": "0",
            "ALPACA_BASE_URL": "https://api.alpaca.markets",
        }
        mismatches = [key for key, value in expected.items() if os.environ.get(key, "").rstrip("/") != value]
        if mismatches:
            raise RuntimeError("Lyra Live runtime gate differs: " + ",".join(sorted(mismatches)))


def run(
    *, mode: str, execution_session: str, target_source_path: Path,
    owner_decision_path: Path, state_root: Path, submit: bool,
    now: dt.datetime | None = None,
) -> dict:
    observed = now or dt.datetime.now(dt.timezone.utc)
    if observed.tzinfo is None:
        raise RuntimeError("runtime observation needs a timezone")
    owner = validate_owner_decision(_read_json(owner_decision_path))
    _require_runtime(decision=owner, submit=submit)
    session_root = state_root / execution_session
    completed_path = session_root / "result.json"
    if completed_path.exists():
        completed = _read_json(completed_path)
        if (
            completed.get("execution_session") != execution_session
            or completed.get("owner_decision_hash") != owner["content_hash"]
            or completed.get("status") != "COMPLETE"
        ):
            raise RuntimeError("existing Lyra Live result is not a valid completed session")
        return {"plan": _read_json(session_root / "plan.json"), "execution": completed}
    raw_target = target_source_path.read_bytes()
    target = validate_target_source(raw_target, mode=mode, execution_session=execution_session)
    broker = AlpacaBroker.from_env()
    if broker.paper or str(broker.base_url).rstrip("/") != "https://api.alpaca.markets":
        raise RuntimeError("Lyra Live factual read requires canonical Alpaca Live")
    account = broker.get_account()
    if (
        not str(account.get("status") or "").upper().endswith("ACTIVE")
        or account.get("trading_blocked") is True
        or account.get("account_blocked") is True
    ):
        raise RuntimeError("Lyra Live broker account is not active/unblocked")
    positions = broker.get_positions()
    open_orders = broker.list_orders(status="open", limit=100)
    symbols = sorted(set(target["weights"]) | {str(row.get("symbol") or "").upper() for row in positions})
    assets = {symbol: broker.get_asset(symbol) for symbol in target["weights"]}
    latest = broker.get_latest_trades(symbols)
    prices = {symbol: float(latest[symbol]["price"]) for symbol in symbols}
    deployed_repo = Path(os.environ.get("CAERUS_LYRA_LIVE_DEPLOYED_REPO", str(ROOT))).resolve()
    if submit and deployed_repo != ROOT.resolve():
        raise RuntimeError("Lyra Live executable and deployed repository pins differ")
    deployed_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=deployed_repo, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    planned_at = (
        observed.isoformat(timespec="seconds") if observed.date().isoformat() == execution_session
        else f"{execution_session}T09:35:00-04:00"
    )
    plan_path = session_root / "plan.json"
    if plan_path.exists():
        plan = validate_plan(_read_json(plan_path), owner_decision=owner)
        if (
            plan["mode"] != mode
            or plan["execution_session"] != execution_session
            or plan["target_source_hash"] != target["source_hash"]
            or plan["account_id_hash"] != str(account.get("id_hash") or "")
            or plan["deployed_sha"] != deployed_sha
        ):
            raise RuntimeError("persisted Lyra Live recovery plan lineage differs")
    else:
        plan = build_portfolio_plan(
            owner_decision=owner, raw_target_source=raw_target, mode=mode,
            execution_session=execution_session, planned_at=planned_at,
            account_id_hash=str(account.get("id_hash") or ""),
            equity_usd=float(account["equity"]), cash_usd=float(account["cash"]),
            buying_power_usd=float(account["buying_power"]), positions=positions,
            open_orders=open_orders, assets=assets, latest_prices=prices,
            deployed_sha=deployed_sha,
        )
        _write_exclusive(plan_path, plan)
    if submit:
        calendar = broker.get_market_session_calendar(execution_session)
        local = observed.astimezone(ET)
        opened = dt.datetime.fromisoformat(str(calendar["session_open_et"]))
        closed = dt.datetime.fromisoformat(str(calendar["session_close_et"]))
        if not (opened <= local < closed):
            raise RuntimeError("Lyra Live submission is outside the broker session")
        for symbol in symbols:
            stamp = dt.datetime.fromisoformat(str(latest[symbol]["timestamp"]).replace("Z", "+00:00"))
            if abs((observed - stamp.astimezone(observed.tzinfo)).total_seconds()) > 120:
                raise RuntimeError(f"{symbol} latest trade is stale")
    result = execute_portfolio_plan(
        owner_decision=owner, plan=plan, broker=broker,
        state_root=state_root, executed_at=observed.isoformat(timespec="seconds"),
        submit_enabled=submit,
    )
    return {"plan": plan, "execution": result}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("initialization", "recurring"), required=True)
    parser.add_argument("--execution-session", required=True)
    parser.add_argument("--target-source", type=Path, required=True)
    parser.add_argument("--owner-decision", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--dry-run", action="store_true")
    action.add_argument("--submit", action="store_true")
    args = parser.parse_args()
    try:
        result = run(
            mode=args.mode, execution_session=args.execution_session,
            target_source_path=args.target_source.resolve(),
            owner_decision_path=args.owner_decision.resolve(),
            state_root=args.state_root.resolve(), submit=args.submit,
        )
    except Exception as exc:
        print(json.dumps({
            "status": "BLOCKED_NO_SUBMIT" if not args.submit else "BLOCKED",
            "reason": str(exc), "broker_write_performed": False,
        }, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result["execution"]["status"] in {"DRY_RUN_READY", "COMPLETE"} else 2


if __name__ == "__main__":
    raise SystemExit(main())

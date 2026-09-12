#!/usr/bin/env python3
"""Build and optionally execute the exact owner-approved Lyra Live basket."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
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
    content_hash,
    validate_owner_decision,
    validate_plan,
    validate_target_source,
)


ET = ZoneInfo("America/New_York")


def _blocked_reason_code(message: str) -> str:
    if "target effective date differs" in message:
        return "target_effective_date_stale_or_mismatched"
    if "target identity differs" in message:
        return "target_strategy_or_variant_mismatch"
    if "runtime gate differs" in message or "runtime owner pin differs" in message:
        return "runtime_authority_mismatch"
    if "broker account is not active/unblocked" in message:
        return "broker_account_unavailable"
    if "latest trade is stale" in message:
        return "market_data_stale"
    return "prebroker_or_execution_guard_blocked"


def persist_blocked_attempt(
    *, state_root: Path, execution_session: str, mode: str,
    target_source_path: Path, owner_decision_path: Path, submit: bool,
    observed_at: str, error: Exception,
) -> dict:
    target_sha = None
    try:
        target_sha = hashlib.sha256(target_source_path.read_bytes()).hexdigest()
    except OSError:
        pass
    session_root = state_root / execution_session
    receipt_exists = any(session_root.glob("receipt-*.json"))
    mutation_exists = any(session_root.glob("mutation-*.json"))
    if not submit:
        broker_write: bool | None = False
        broker_write_status = "PROVEN_NONE_DRY_RUN"
    elif receipt_exists:
        broker_write = True
        broker_write_status = "PROVEN_WRITE"
    elif mutation_exists:
        broker_write = None
        broker_write_status = "UNPROVEN_CHECK_BROKER_BY_CLIENT_ORDER_ID"
    else:
        broker_write = False
        broker_write_status = "PROVEN_NONE_PREMUTATION"
    body = {
        "schema_version": "caerus.lyra_live_blocked_attempt.v1",
        "execution_session": execution_session,
        "mode": mode,
        "observed_at": observed_at,
        "status": "BLOCKED",
        "reason_code": _blocked_reason_code(str(error)),
        "reason": str(error),
        "submit_requested": bool(submit),
        "broker_write_performed": broker_write,
        "broker_write_status": broker_write_status,
        "target_source_path": str(target_source_path),
        "target_source_sha256": target_sha,
        "owner_decision_path": str(owner_decision_path),
    }
    body["content_hash"] = content_hash(body)
    path = (
        state_root / execution_session / "blocked_attempts"
        / f"{body['content_hash']}.json"
    ).resolve()
    _write_exclusive(path, body)
    return {**body, "artifact_path": str(path)}


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
    capture_started_at = dt.datetime.now(dt.timezone.utc).isoformat()
    account = broker.get_account()
    if (
        str(account.get("status") or "").upper().split('.')[-1] != "ACTIVE"
        or account.get("trading_blocked") is True
        or account.get("account_blocked") is True
    ):
        raise RuntimeError("Lyra Live broker account is not active/unblocked")
    positions = broker.get_positions()
    open_orders = broker.list_orders(status="open", limit=100)
    symbols = sorted(set(target["weights"]) | {str(row.get("symbol") or "").upper() for row in positions})
    assets = {symbol: broker.get_asset(symbol) for symbol in target["weights"]}
    latest = broker.get_latest_trades(symbols)
    capture_completed_at = dt.datetime.now(dt.timezone.utc).isoformat()
    prices = {symbol: float(latest[symbol]["price"]) for symbol in symbols}
    deployed_repo = Path(os.environ.get("CAERUS_LYRA_LIVE_DEPLOYED_REPO", str(ROOT))).resolve()
    if submit and deployed_repo != ROOT.resolve():
        raise RuntimeError("Lyra Live executable and deployed repository pins differ")
    deployed_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=deployed_repo, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    planned_at = dt.datetime.now(dt.timezone.utc).isoformat()
    snapshot = {
        "schema_version": "caerus.lyra_broker_snapshot.v1",
        "captured_at": capture_completed_at, "capture_started_at": capture_started_at,
        "capture_completed_at": capture_completed_at,
        "source": "ALPACA_LIVE_GET", "execution_session": execution_session,
        "account": {key: account.get(key) for key in
                    ("id_hash", "equity", "cash", "buying_power", "status", "trading_blocked", "account_blocked")},
        "positions": positions, "open_orders": open_orders,
        "latest_trades": {symbol: {'price': latest[symbol].get('price'),
                                    'timestamp': latest[symbol].get('timestamp')} for symbol in symbols},
    }
    snapshot["content_hash"] = content_hash(snapshot)
    _write_exclusive(session_root / ("broker_pretrade_snapshot-" + snapshot["content_hash"] + ".json"), snapshot)
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
            equity_usd=account["equity"], cash_usd=account["cash"],
            buying_power_usd=account["buying_power"], positions=positions,
            open_orders=open_orders, assets=assets, latest_prices=prices,
            deployed_sha=deployed_sha,
            broker_snapshot=snapshot,
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
    if submit:
        from core.paper_live_parity import require_pretrade_parity
        require_pretrade_parity(repo_root=ROOT, trade_date=execution_session,
                               lane="live", plan_hash=plan["content_hash"])
    result = execute_portfolio_plan(
        owner_decision=owner, plan=plan, broker=broker,
        state_root=state_root, executed_at=(now or dt.datetime.now(dt.timezone.utc)).isoformat(),
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
    observed = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    try:
        result = run(
            mode=args.mode, execution_session=args.execution_session,
            target_source_path=args.target_source.resolve(),
            owner_decision_path=args.owner_decision.resolve(),
            state_root=args.state_root.resolve(), submit=args.submit,
        )
    except Exception as exc:
        blocked = {
            "status": "BLOCKED_NO_SUBMIT" if not args.submit else "BLOCKED",
            "reason": str(exc), "broker_write_performed": None,
        }
        try:
            artifact = persist_blocked_attempt(
                state_root=args.state_root.resolve(),
                execution_session=args.execution_session,
                mode=args.mode,
                target_source_path=args.target_source.resolve(),
                owner_decision_path=args.owner_decision.resolve(),
                submit=args.submit,
                observed_at=observed,
                error=exc,
            )
            blocked.update(
                {
                    "reason_code": artifact["reason_code"],
                    "blocked_attempt_path": artifact["artifact_path"],
                    "blocked_attempt_hash": artifact["content_hash"],
                    "broker_write_performed": artifact["broker_write_performed"],
                    "broker_write_status": artifact["broker_write_status"],
                }
            )
        except Exception as persistence_exc:
            blocked["blocked_attempt_persistence_error"] = str(persistence_exc)
        print(json.dumps(blocked, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result["execution"]["status"] in {"DRY_RUN_READY", "COMPLETE"} else 2


if __name__ == "__main__":
    raise SystemExit(main())

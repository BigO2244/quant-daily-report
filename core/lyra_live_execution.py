"""Durable, idempotent execution boundary for an exact Lyra Live batch."""

from __future__ import annotations

import copy
import datetime as dt
import fcntl
import hashlib
import json
import os
import stat
import time
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from brokers.alpaca_broker import _LYRA_LIVE_PORTFOLIO_CAPABILITY
from core.lyra_live_portfolio import (
    LyraLivePortfolioError,
    canonical_json,
    content_hash,
    validate_owner_decision,
    validate_plan,
)


RESULT_SCHEMA = "caerus.lyra_live_execution_result.v1"
TERMINAL = {"filled", "canceled", "expired", "rejected", "done_for_day", "replaced"}


class LyraLiveExecutionError(RuntimeError):
    pass


def _write_exclusive(path: Path, payload: Mapping[str, Any]) -> None:
    if not path.is_absolute() or path.is_symlink() or path.parent.is_symlink():
        raise LyraLiveExecutionError("execution artifact path is unsafe")
    if not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    encoded = (canonical_json(payload) + "\n").encode()
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        try:
            existing = json.loads(path.read_text())
        except Exception as exc:
            raise LyraLiveExecutionError("existing execution artifact is unreadable") from exc
        if existing != payload:
            raise LyraLiveExecutionError("immutable execution artifact collision")
        return
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _safe_order(order: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if order is None:
        return None
    return {
        "id": str(order.get("id") or ""),
        "client_order_id": str(order.get("client_order_id") or ""),
        "symbol": str(order.get("symbol") or "").upper(),
        "side": str(order.get("side") or "").upper().split(".")[-1],
        "status": str(order.get("status") or "").lower().split(".")[-1],
        "qty": str(order.get("qty") or order.get("quantity") or ""),
        "notional": str(order.get("notional") or ""),
        "filled_qty": str(order.get("filled_qty") or order.get("filled_quantity") or ""),
        "filled_avg_price": str(order.get("filled_avg_price") or ""),
    }


def _mutation_context(plan: Mapping[str, Any], order: Mapping[str, Any]) -> dict[str, Any]:
    body = {
        "schema_version": "caerus.lyra_live_mutation_context.v1",
        "action": "SUBMIT", "execution_session": plan["execution_session"],
        "mode": plan["mode"], "owner_decision_hash": plan["owner_decision_hash"],
        "target_source_hash": plan["target_source_hash"], "plan_hash": plan["content_hash"],
        "account_id_hash": plan["account_id_hash"], "deployed_sha": plan["deployed_sha"],
        "order_index": order["order_index"], "maximum_orders": plan["maximum_orders"],
        "client_order_id": order["client_order_id"], "symbol": order["symbol"],
        "side": order["side"], "quantity": order["quantity"], "notional": order["notional"],
        "order_type": "market", "time_in_force": "day", "extended_hours": False,
        "fractional_shares": True,
        "factual_equity_usd": plan["factual_equity_usd"],
        "factual_cash_usd": plan["factual_cash_usd"],
        "factual_buying_power_usd": plan["factual_buying_power_usd"],
        "maximum_gross_usd": plan["maximum_gross_usd"],
        "required_cash_reserve_usd": plan["required_cash_reserve_usd"],
        "maximum_buy_notional_usd": plan["maximum_buy_notional_usd"],
        "total_buy_notional_usd": plan["total_buy_notional_usd"],
        "projected_gross_usd": plan["projected_gross_usd"],
    }
    body["content_hash"] = hashlib.sha256(canonical_json(body).encode()).hexdigest()
    body["capability_signature"] = _LYRA_LIVE_PORTFOLIO_CAPABILITY.sign(body["content_hash"])
    return body


def _status(order: Mapping[str, Any]) -> str:
    return str(order.get("status") or "").strip().lower().split(".")[-1]


def _poll_terminal(broker: Any, order: Mapping[str, Any], *, timeout_seconds: int = 45) -> Mapping[str, Any]:
    broker_id = str(order.get("id") or "")
    if not broker_id:
        raise LyraLiveExecutionError("broker receipt lacks order id")
    deadline = time.monotonic() + timeout_seconds
    observed = order
    while _status(observed) not in TERMINAL and time.monotonic() < deadline:
        time.sleep(1)
        observed = broker.get_order(broker_id) or observed
    if _status(observed) not in TERMINAL:
        raise LyraLiveExecutionError("broker order did not reach a terminal state")
    if _status(observed) != "filled":
        raise LyraLiveExecutionError(f"broker order terminal status is {_status(observed)}")
    return observed


def execute_portfolio_plan(
    *, owner_decision: Mapping[str, Any], plan: Mapping[str, Any], broker: Any,
    state_root: Path | str, executed_at: str, submit_enabled: bool,
) -> dict[str, Any]:
    """Persist intent first, then submit/recover every exact order once."""

    owner = validate_owner_decision(owner_decision)
    checked = validate_plan(plan, owner_decision=owner)
    try:
        executed = dt.datetime.fromisoformat(executed_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LyraLiveExecutionError("execution time is invalid") from exc
    if executed.tzinfo is None:
        raise LyraLiveExecutionError("execution time needs a timezone")
    root = Path(state_root)
    if not root.is_absolute() or root.is_symlink():
        raise LyraLiveExecutionError("state root must be an absolute non-symlink")
    session_root = root / checked["execution_session"]
    session_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(session_root, 0o700)
    lock_path = session_root / ".execution.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(descriptor)
        raise LyraLiveExecutionError("Lyra Live session is already claimed") from exc
    try:
        intent = {
            "schema_version": "caerus.lyra_live_submission_intent.v1",
            "execution_session": checked["execution_session"],
            "owner_decision_hash": owner["content_hash"],
            "plan_hash": checked["content_hash"],
            "order_client_ids": [order["client_order_id"] for order in checked["orders"]],
            "persisted_at": executed_at, "broker_write_performed": False,
        }
        intent["content_hash"] = content_hash(intent)
        _write_exclusive(session_root / "intent.json", intent)
        if not submit_enabled:
            body = {
                "schema_version": RESULT_SCHEMA, "execution_session": checked["execution_session"],
                "mode": checked["mode"], "status": "DRY_RUN_READY",
                "executed_at": executed_at, "owner_decision_hash": owner["content_hash"],
                "plan_hash": checked["content_hash"], "intent_hash": intent["content_hash"],
                "submitted_orders": [], "broker_write_performed": False,
                "posttrade_reconciliation": None,
            }
            body["content_hash"] = content_hash(body)
            return body
        if executed.date().isoformat() != checked["execution_session"]:
            raise LyraLiveExecutionError("submission is outside the exact execution session")
        local = executed.astimezone(ZoneInfo("America/New_York"))
        if not (dt.time(9, 35) <= local.time() < dt.time(9, 50)):
            raise LyraLiveExecutionError("submission is outside the 09:35-09:50 ET window")
        receipts: list[dict[str, Any]] = []
        for order in checked["orders"]:
            context = _mutation_context(checked, order)
            _write_exclusive(
                session_root / f"mutation-{order['order_index']:02d}.json", context,
            )
            recovered = broker.find_order_by_client_id(order["client_order_id"])
            if recovered is None:
                recovered = broker.submit_lyra_live_portfolio_market_order(
                    symbol=order["symbol"], side=order["side"],
                    client_order_id=order["client_order_id"], qty=order["quantity"],
                    notional=order["notional"], mutation_context=context,
                    _lyra_live_portfolio_capability=_LYRA_LIVE_PORTFOLIO_CAPABILITY,
                )
            terminal = _poll_terminal(broker, recovered)
            safe = _safe_order(terminal)
            receipt = {
                "schema_version": "caerus.lyra_live_order_receipt.v1",
                "execution_session": checked["execution_session"],
                "plan_hash": checked["content_hash"], "order_index": order["order_index"],
                "mutation_context_hash": context["content_hash"], "broker_order": safe,
                "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            }
            receipt["content_hash"] = content_hash(receipt)
            _write_exclusive(session_root / f"receipt-{order['order_index']:02d}.json", receipt)
            receipts.append(receipt)
        account = broker.get_account()
        positions = broker.get_positions()
        symbols = sorted(checked["target_weights"])
        latest = broker.get_latest_trades(symbols)
        prices = {symbol: float(latest[symbol]["price"]) for symbol in symbols}
        equity = float(account["equity"])
        post = {str(row.get("symbol") or "").upper(): float(row.get("qty") or 0) for row in positions}
        target_values = {
            symbol: equity * 0.95 * float(weight)
            for symbol, weight in checked["target_weights"].items()
        }
        actual_values = {symbol: post.get(symbol, 0.0) * prices[symbol] for symbol in symbols}
        errors = {symbol: actual_values[symbol] - target_values[symbol] for symbol in symbols}
        tolerance = max(2.0, equity * 0.01)
        unexpected = sorted(symbol for symbol, qty in post.items() if qty > 0 and symbol not in symbols)
        cash = float(account["cash"])
        reconciliation = {
            "target_values_usd": target_values, "actual_values_usd": actual_values,
            "value_errors_usd": errors, "tolerance_usd": tolerance,
            "cash_usd": cash, "minimum_cash_reserve_usd": equity * 0.05,
            "unexpected_positions": unexpected,
            "status": "ALIGNED" if (
                not unexpected
                and all(abs(error) <= tolerance for error in errors.values())
                and cash + 0.01 >= equity * 0.05
            ) else "NOT_ALIGNED",
        }
        body = {
            "schema_version": RESULT_SCHEMA, "execution_session": checked["execution_session"],
            "mode": checked["mode"],
            "status": "COMPLETE" if reconciliation["status"] == "ALIGNED" else "BLOCKED_RECONCILIATION",
            "executed_at": executed_at, "owner_decision_hash": owner["content_hash"],
            "plan_hash": checked["content_hash"], "intent_hash": intent["content_hash"],
            "submitted_orders": receipts, "broker_write_performed": bool(receipts),
            "posttrade_reconciliation": reconciliation,
        }
        body["content_hash"] = content_hash(body)
        _write_exclusive(session_root / "result.json", body)
        return body
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


__all__ = ["LyraLiveExecutionError", "execute_portfolio_plan"]

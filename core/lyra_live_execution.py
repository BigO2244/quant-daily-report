"""Durable, idempotent execution boundary for an exact Lyra Live batch."""

from __future__ import annotations

import copy
import datetime as dt
import fcntl
import hashlib
import json
import math
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
    validate_broker_snapshot,
    _finite,
    _timestamp,
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
        "max_live_capital_usd": plan["max_live_capital_usd"],
        "sizing_basis_usd": plan["sizing_basis_usd"],
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


def _posttrade_reconciliation(plan, snapshot, expected_positions):
    """Reconcile actual account/position truth independently of quote-mark targets."""
    result = {'status': 'NOT_ALIGNED', 'reasons': [], 'snapshot_hash': snapshot['content_hash']}
    try:
        if snapshot['capture_errors']:
            raise LyraLiveExecutionError('posttrade capture contains invalid or missing data')
        started = _timestamp(snapshot['capture_started_at'], label='posttrade capture start')
        completed = _timestamp(snapshot['capture_completed_at'], label='posttrade capture end')
        if (not 0 <= (completed-started).total_seconds() <= 120
                or completed.astimezone(ZoneInfo('America/New_York')).date().isoformat() != plan['execution_session']):
            raise LyraLiveExecutionError('posttrade snapshot stale or outside session')
        account = snapshot['account']
        if account.get('id_hash') != plan['account_id_hash']:
            raise LyraLiveExecutionError('posttrade broker account identity differs from exact plan')
        equity = _finite(account.get('equity'), label='posttrade equity', positive=True)
        cash = _finite(account.get('cash'), label='posttrade cash')
        positions = snapshot['positions']
        if not isinstance(positions, list):
            raise LyraLiveExecutionError('posttrade positions missing')
        quantities, market_values = {}, {}
        for row in positions:
            symbol = str(row.get('symbol') or '').upper()
            if not symbol or symbol in quantities:
                raise LyraLiveExecutionError('posttrade positions duplicate or missing symbol')
            quantities[symbol] = _finite(row.get('qty'), label=symbol+' posttrade quantity')
            market_values[symbol] = _finite(row.get('market_value'), label=symbol+' broker market value')
        deltas = {s: quantities.get(s, 0)-expected_positions.get(s, 0)
                  for s in sorted(set(quantities)|set(expected_positions))
                  if abs(quantities.get(s, 0)-expected_positions.get(s, 0)) > 1e-8}
        broker_position_value = sum(market_values.values())
        nav_delta = equity-(cash+broker_position_value)
        # Same cent-level account NAV tolerance as canonical economic reconciliation.
        nav_abs = .01
        symbols = sorted(set(quantities)|set(plan['target_weights']))
        prices = {}
        for symbol in symbols:
            quote = snapshot['latest_trades'][symbol]
            stamped = _timestamp(quote.get('timestamp'), label=symbol+' posttrade quote timestamp')
            if not 0 <= (completed-stamped).total_seconds() <= 120:
                raise LyraLiveExecutionError(symbol+' posttrade quote stale')
            prices[symbol] = _finite(quote.get('price'), label=symbol+' posttrade price', positive=True)
        target_values = {symbol: plan['sizing_basis_usd']*.95*float(weight)
                         for symbol, weight in plan['target_weights'].items()}
        actual_values = {symbol: quantities.get(symbol, 0)*prices[symbol] for symbol in target_values}
        errors = {symbol: actual_values[symbol]-target_values[symbol] for symbol in target_values}
        tolerance = max(2., equity*.01)
        reserve = max(plan['required_cash_reserve_usd'], equity*.05)
        unexpected = sorted(s for s, qty in quantities.items() if qty > 0 and s not in target_values)
        result.update(target_values_usd=target_values, actual_values_usd=actual_values,
            value_errors_usd=errors, tolerance_usd=tolerance, cash_usd=cash,
            minimum_cash_reserve_usd=reserve, unexpected_positions=unexpected,
            expected_positions=expected_positions, actual_positions=quantities, quantity_deltas=deltas,
            account_nav_reconciliation={'alpaca_equity_usd': equity, 'alpaca_cash_usd': cash,
                'alpaca_position_market_value_usd': broker_position_value, 'delta_usd': nav_delta,
                'tolerance_usd': nav_abs},
            quote_marked_position_value_usd=sum(quantities[s]*prices[s] for s in quantities))
        if deltas: result['reasons'].append('confirmed_fill_quantity_mismatch')
        if abs(nav_delta) > nav_abs: result['reasons'].append('alpaca_cash_positions_NAV_mismatch')
        if unexpected: result['reasons'].append('unexpected_positions')
        if any(abs(error) > tolerance for error in errors.values()): result['reasons'].append('target_value_mismatch')
        if cash+.01 < reserve: result['reasons'].append('cash_reserve_mismatch')
        if not result['reasons']: result['status'] = 'ALIGNED'
    except (LyraLivePortfolioError, LyraLiveExecutionError, KeyError, TypeError, ValueError, AttributeError) as exc:
        result['reasons'].append(str(exc))
    return result


def execute_portfolio_plan(
    *, owner_decision: Mapping[str, Any], plan: Mapping[str, Any], broker: Any,
    state_root: Path | str, executed_at: str, submit_enabled: bool,
) -> dict[str, Any]:
    """Persist intent first, then submit/recover every exact order once."""

    owner = validate_owner_decision(owner_decision)
    checked = validate_plan(plan, owner_decision=owner)
    execution_started = time.monotonic()
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
        expected_positions = dict(checked["starting_positions"])
        for order in checked["orders"]:
            context = _mutation_context(checked, order)
            _write_exclusive(
                session_root / f"mutation-{order['order_index']:02d}.json", context,
            )
            recovered = broker.find_order_by_client_id(order["client_order_id"])
            if recovered is None:
                validation_time = executed + dt.timedelta(seconds=time.monotonic()-execution_started)
                validate_broker_snapshot(checked['broker_pretrade_snapshot'],
                    account_id_hash=checked['account_id_hash'], execution_session=checked['execution_session'],
                    as_of=validation_time.isoformat())
                fresh = broker.get_account()
                if fresh.get('id_hash') != checked['account_id_hash']:
                    raise LyraLiveExecutionError('broker account identity differs from exact plan')
                if (str(fresh.get('status') or '').upper().split('.')[-1] != 'ACTIVE'
                        or fresh.get('trading_blocked') is True or fresh.get('account_blocked') is True):
                    raise LyraLiveExecutionError('broker account is not active/unblocked')
                actual_positions = {str(p["symbol"]).upper(): _finite(p.get("qty", p.get("quantity")), label='broker quantity')
                                    for p in broker.get_positions()}
                if any(not math.isfinite(q) or q < 0 for q in actual_positions.values()) or any(
                    abs(actual_positions.get(s, 0) - expected_positions.get(s, 0)) > 1e-8
                    for s in set(actual_positions) | set(expected_positions)
                ):
                    raise LyraLiveExecutionError("broker positions changed outside the exact plan; reconciliation required")
                fresh_cash = _finite(fresh.get('cash'), label='broker cash')
                fresh_equity = _finite(fresh.get('equity'), label='broker equity', positive=True)
                if not math.isfinite(fresh_equity) or fresh_equity <= 0 or not math.isfinite(fresh_cash) or fresh_cash < 0:
                    raise LyraLiveExecutionError("broker cash/NAV is invalid")
                if not receipts and (abs(fresh_equity - checked["factual_equity_usd"]) > .01 or abs(fresh_cash - checked["factual_cash_usd"]) > .01):
                    raise LyraLiveExecutionError("saved plan broker NAV/cash is stale; replan required")
                if checked["maximum_gross_usd"] > fresh_equity * .95 + .01:
                    raise LyraLiveExecutionError("current broker NAV cannot support saved plan; replan required")
                if order["side"] == "BUY":
                    # Rebudget against confirmed broker cash after completed sells.
                    if not all(math.isfinite(x) and x >= 0 for x in (fresh_cash, fresh_equity)):
                        raise LyraLiveExecutionError("broker cash/NAV is invalid")
                    reserve = max(checked["required_cash_reserve_usd"], fresh_equity * .05)
                    if fresh_cash - reserve + .01 < float(order["notional"]):
                        raise LyraLiveExecutionError("confirmed broker cash cannot fund exact buy; replan required")
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
            quantity = float(safe["filled_qty"])
            if not math.isfinite(quantity) or quantity < 0:
                raise LyraLiveExecutionError("invalid broker fill quantity")
            expected_positions[order["symbol"]] = expected_positions.get(order["symbol"], 0) + quantity * (1 if order["side"] == "BUY" else -1)
        capture_started = dt.datetime.now(dt.timezone.utc).isoformat()
        account = broker.get_account()
        positions = broker.get_positions()
        symbols = sorted(set(checked['target_weights']) | {
            str(row.get('symbol') or '').upper() for row in positions if isinstance(row, Mapping)
        }) if isinstance(positions, list) else sorted(checked['target_weights'])
        capture_errors = []
        try:
            latest = broker.get_latest_trades(symbols)
        except Exception as exc:
            latest = {}
            capture_errors.append('posttrade_quote_capture_failed:'+type(exc).__name__)
        capture_completed = dt.datetime.now(dt.timezone.utc).isoformat()
        def evidence_value(value, path):
            if isinstance(value, float) and not math.isfinite(value):
                capture_errors.append('nonfinite_numeric_value:'+path)
                return str(value)
            if isinstance(value, Mapping):
                return {k:evidence_value(v, path+'.'+str(k)) for k,v in value.items()}
            if isinstance(value, list):
                return [evidence_value(v, path+'.'+str(i)) for i,v in enumerate(value)]
            return value
        snapshot = {
            'schema_version': 'caerus.lyra_broker_snapshot.v1', 'execution_session': checked['execution_session'],
            'captured_at': capture_completed, 'capture_started_at': capture_started,
            'capture_completed_at': capture_completed, 'source': 'ALPACA_LIVE_GET',
            'plan_hash': checked['content_hash'], 'account_id_hash': checked['account_id_hash'],
            'account': evidence_value({key:account.get(key) for key in
                ('id_hash','equity','cash','buying_power','status')}, 'account'),
            'positions': evidence_value(positions, 'positions'),
            'fills': [r['broker_order'] for r in receipts],
            'latest_trades': evidence_value(latest, 'latest_trades'),
            'capture_errors': capture_errors,
        }
        snapshot['content_hash'] = content_hash(snapshot)
        _write_exclusive(session_root/'broker_posttrade_snapshot.json', snapshot)
        reconciliation = _posttrade_reconciliation(checked, snapshot, expected_positions)
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

"""Durable, idempotent submission boundary for owner-approved generic Live v1.

Dry-run is the default and performs no writes or broker calls.  Explicit
submission requires a READY session preflight bound to the same exact v4 plan.
The immutable intent is persisted before broker mutation, recovery checks the
stable client id before any retry, and every terminal path re-arms the generic
session state.  This module never imports the legacy Live executor.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import math
import os
import fcntl
import stat
from pathlib import Path
from typing import Any, Mapping, Protocol

from authority.lane_exact_plan import canonical_json, validate_lane_exact_execution_plan
from brokers.alpaca_broker import _GENERIC_LIVE_V4_CAPABILITY
from core.generic_live_v1_activation import validate_generic_live_v1_activation_preflight
from core.accounting_journal import validate_accounting_journal
from core.lane_performance import validate_lane_performance
from core.lane_reconciliation import validate_lane_reconciliation
from core.lane_truth_status import validate_dashboard_performance_surfaces
from core.generic_live_v1_ops import reject_sensitive_payload


GENERIC_LIVE_V1_SUBMISSION_RESULT_SCHEMA = "caerus.generic_live_v1_submission_result.v1"
GENERIC_LIVE_V1_REARM_SCHEMA = "caerus.generic_live_v1_rearm.v1"
GENERIC_LIVE_V1_POSTTRADE_RESULT_SCHEMA = "caerus.generic_live_v1_posttrade_result.v1"


class GenericLiveV1SubmissionError(RuntimeError):
    """Raised before or during an explicitly authorized generic submission."""


class GenericLiveV1Broker(Protocol):
    def get_account(self) -> Mapping[str, Any]: ...
    def get_positions(self) -> list[Mapping[str, Any]]: ...
    def list_orders(self, status: str = "open", limit: int = 100) -> list[Mapping[str, Any]]: ...
    def get_market_session_calendar(self, trade_date: str) -> Mapping[str, Any]: ...
    def get_asset(self, symbol: str) -> Mapping[str, Any] | None: ...
    def find_order_by_client_id(self, client_id: str) -> Mapping[str, Any] | None: ...

    def submit_generic_live_v4_market_order(self, **kwargs: Any) -> Mapping[str, Any]: ...


def _hash(payload: Mapping[str, Any]) -> str:
    body = copy.deepcopy(dict(payload))
    body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def _timestamp(value: str) -> tuple[str, dt.datetime]:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise GenericLiveV1SubmissionError("executed_at must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise GenericLiveV1SubmissionError("executed_at must include a timezone")
    return str(value), parsed


def _stable_client_id(plan_hash: str, order_id: str) -> str:
    seed = hashlib.sha256(f"{plan_hash}:{order_id}".encode("utf-8")).hexdigest()
    return f"caerus-v4-{seed[:32]}"


def _write_exclusive(path: Path, payload: Mapping[str, Any]) -> None:
    if not path.is_absolute() or path.is_symlink() or path.parent.is_symlink():
        raise GenericLiveV1SubmissionError("immutable artifact path must be absolute and non-symlink")
    reject_sensitive_payload(payload)
    if not path.parent.exists():
        path.parent.mkdir(parents=False, exist_ok=False, mode=0o700)
    if stat.S_IMODE(os.lstat(path.parent).st_mode) != 0o700:
        raise GenericLiveV1SubmissionError("immutable artifact directory must have mode 0700")
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise GenericLiveV1SubmissionError(f"existing immutable artifact is unreadable: {path}") from exc
        if existing != payload:
            raise GenericLiveV1SubmissionError(f"immutable artifact collision: {path}")
        return
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _atomic_rearm(path: Path, payload: Mapping[str, Any]) -> None:
    if not path.is_absolute() or path.is_symlink() or path.parent.is_symlink():
        raise GenericLiveV1SubmissionError("rearm state path must be absolute and non-symlink")
    reject_sensitive_payload(payload)
    if not path.parent.exists():
        path.parent.mkdir(parents=False, exist_ok=False, mode=0o700)
    if stat.S_IMODE(os.lstat(path.parent).st_mode) != 0o700:
        raise GenericLiveV1SubmissionError("rearm state directory must have mode 0700")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _require_session_disarm(
    path: Path, *, preflight_hash: str, plan_hash: str, effective_session: str
) -> None:
    if not path.is_absolute() or path.is_symlink():
        raise GenericLiveV1SubmissionError("generic session disarm path must be absolute and non-symlink")
    try:
        if stat.S_IMODE(os.lstat(path).st_mode) != 0o600:
            raise GenericLiveV1SubmissionError("generic session disarm state must have mode 0600")
    except FileNotFoundError:
        pass
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise GenericLiveV1SubmissionError("generic session disarm state is absent or unreadable") from exc
    expected_fields = {
        "schema_version", "status", "effective_session", "preflight_hash",
        "plan_hash", "legacy_executor_enabled", "paper_cutover_enabled", "content_hash",
    }
    if not isinstance(payload, Mapping) or set(payload) != expected_fields:
        raise GenericLiveV1SubmissionError("generic session disarm state fields are invalid")
    if payload.get("schema_version") != "caerus.generic_live_v1_session_gate.v1" or payload.get("status") != "DISARMED_FOR_EXACT_SESSION":
        raise GenericLiveV1SubmissionError("generic session gate is not disarmed for exact session")
    if (
        payload.get("effective_session") != effective_session
        or payload.get("preflight_hash") != preflight_hash
        or payload.get("plan_hash") != plan_hash
    ):
        raise GenericLiveV1SubmissionError("generic session gate lineage differs")
    if payload.get("legacy_executor_enabled") is not False or payload.get("paper_cutover_enabled") is not False:
        raise GenericLiveV1SubmissionError("generic session gate would enable a forbidden path")
    if payload.get("content_hash") != _hash(payload):
        raise GenericLiveV1SubmissionError("generic session gate content_hash mismatch")


def _rearm(*, preflight_hash: str, plan_hash: str, executed_at: str, trigger: str) -> dict[str, Any]:
    body = {
        "schema_version": GENERIC_LIVE_V1_REARM_SCHEMA,
        "status": "ARMED",
        "trigger": trigger,
        "rearmed_at": executed_at,
        "preflight_hash": preflight_hash,
        "plan_hash": plan_hash,
        "legacy_executor_enabled": False,
        "generic_submission_enabled": False,
        "paper_cutover_enabled": False,
    }
    body["content_hash"] = _hash(body)
    return body


def rearm_generic_live_v1_session(
    *, state_path: Path | str, preflight_hash: str, plan_hash: str,
    rearmed_at: str, trigger: str,
) -> dict[str, Any]:
    if trigger not in {
        "PREFLIGHT_BREAK", "SUBMISSION_BREAK", "ORDER_BREAK",
        "RECONCILIATION_BREAK", "ACCOUNTING_BREAK", "REPORTING_BREAK",
        "SESSION_COMPLETE",
    }:
        raise GenericLiveV1SubmissionError("generic Live v1 rearm trigger is invalid")
    if any(
        not isinstance(value, str) or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
        for value in (preflight_hash, plan_hash)
    ):
        raise GenericLiveV1SubmissionError("generic Live v1 rearm hashes are invalid")
    _timestamp(rearmed_at)
    payload = _rearm(
        preflight_hash=preflight_hash, plan_hash=plan_hash,
        executed_at=rearmed_at, trigger=trigger,
    )
    _atomic_rearm(Path(state_path), payload)
    return payload


def ensure_generic_live_v1_rearmed_after_failure(
    *, state_path: Path | str, preflight_hash: str | None,
    plan_hash: str | None, rearmed_at: str,
) -> dict[str, Any]:
    """Best-effort emergency rearm for unreadable or pre-validation inputs.

    If a typed ARMED state already exists it is preserved, including its more
    specific trigger.  Missing lineage is represented by the all-zero digest;
    that sentinel can never match a submission preflight or plan.
    """

    path = Path(state_path)
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        current = {}
    expected_fields = {
        "schema_version", "status", "trigger", "rearmed_at", "preflight_hash",
        "plan_hash", "legacy_executor_enabled", "generic_submission_enabled",
        "paper_cutover_enabled", "content_hash",
    }
    if (
        isinstance(current, Mapping)
        and set(current) == expected_fields
        and current.get("schema_version") == GENERIC_LIVE_V1_REARM_SCHEMA
        and current.get("status") == "ARMED"
        and current.get("legacy_executor_enabled") is False
        and current.get("generic_submission_enabled") is False
        and current.get("paper_cutover_enabled") is False
    ):
        try:
            if current.get("content_hash") == _hash(current):
                return copy.deepcopy(dict(current))
        except Exception:
            pass
    zero = "0" * 64
    def valid_hash(value: Any) -> bool:
        return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)

    for key, supplied in (("preflight_hash", preflight_hash), ("plan_hash", plan_hash)):
        if valid_hash(supplied):
            continue
        recovered = current.get(key) if isinstance(current, Mapping) else None
        if valid_hash(recovered):
            if key == "preflight_hash":
                preflight_hash = recovered
            else:
                plan_hash = recovered
    return rearm_generic_live_v1_session(
        state_path=path,
        preflight_hash=preflight_hash if valid_hash(preflight_hash) else zero,
        plan_hash=plan_hash if valid_hash(plan_hash) else zero,
        rearmed_at=rearmed_at,
        trigger="PREFLIGHT_BREAK",
    )


def _safe_broker_order(order: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "broker_order_id": str(order.get("id") or ""),
        "broker_client_order_id": str(order.get("client_order_id") or ""),
        "broker_status": str(order.get("status") or ""),
        "symbol": str(order.get("symbol") or ""),
        "side": str(order.get("side") or ""),
        "quantity": str(order.get("qty") or order.get("quantity") or ""),
    }


def _acquire_session_lock(path: Path) -> int:
    if not path.is_absolute() or path.is_symlink() or path.parent.is_symlink():
        raise GenericLiveV1SubmissionError("session lock path must be absolute and non-symlink")
    if not path.parent.exists():
        path.parent.mkdir(parents=False, exist_ok=False, mode=0o700)
    if stat.S_IMODE(os.lstat(path.parent).st_mode) != 0o700:
        raise GenericLiveV1SubmissionError("session lock directory must have mode 0700")
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o600:
        os.close(descriptor)
        raise GenericLiveV1SubmissionError("session lock file must have mode 0600")
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(descriptor)
        raise GenericLiveV1SubmissionError("generic Live v1 session is already claimed") from exc
    return descriptor


def _fresh_broker_preflight(
    *, broker: GenericLiveV1Broker, plan: Mapping[str, Any], preflight: Mapping[str, Any],
    executed: dt.datetime,
) -> None:
    account = broker.get_account()
    if account.get("id_hash") != preflight["account_id_hash"]:
        raise GenericLiveV1SubmissionError("fresh broker account pin mismatch")
    if str(account.get("status") or "").upper() != "ACTIVE" or account.get("trading_blocked") is True or account.get("account_blocked") is True:
        raise GenericLiveV1SubmissionError("fresh broker account is not active/unblocked")
    try:
        equity = float(account["equity"])
        cash = float(account["cash"])
        buying_power = float(account["buying_power"])
    except (KeyError, TypeError, ValueError) as exc:
        raise GenericLiveV1SubmissionError("fresh broker equity/cash is invalid") from exc
    if not all(math.isfinite(value) for value in (equity, cash, buying_power)) or equity <= 0 or cash < 0 or buying_power < 0:
        raise GenericLiveV1SubmissionError("fresh broker equity/cash/buying power is non-finite or negative")
    if abs(cash - float(plan["starting_cash"])) > 0.01 or abs(equity - float(plan["starting_equity"])) > 0.01:
        raise GenericLiveV1SubmissionError("fresh broker cash/equity differs from exact plan snapshot")
    positions = {
        str(row.get("symbol") or "").upper(): float(row.get("qty") or row.get("quantity") or 0.0)
        for row in broker.get_positions()
    }
    expected_positions = {
        str(row["symbol"]): float(row["quantity"]) for row in plan["starting_positions"]
    }
    if positions != expected_positions:
        raise GenericLiveV1SubmissionError("fresh broker positions differ from exact plan snapshot")
    if broker.list_orders(status="open", limit=100):
        raise GenericLiveV1SubmissionError("fresh broker open orders are present")
    orders = [*plan["sell_orders"], *plan["buy_orders"]]
    if orders:
        order = orders[0]
        asset = broker.get_asset(str(order["symbol"]))
        if not isinstance(asset, Mapping) or asset.get("tradable") is not True or str(asset.get("status") or "").lower().split(".")[-1] != "active":
            raise GenericLiveV1SubmissionError("fresh broker asset is not active/tradable")
        if order["side"] == "BUY" and buying_power + 0.01 < float(order["notional"]):
            raise GenericLiveV1SubmissionError("fresh broker buying power is below exact order notional")
    calendar = broker.get_market_session_calendar(plan["trade_date"])
    try:
        opened = dt.datetime.fromisoformat(str(calendar["session_open_et"]).replace("Z", "+00:00"))
        closed = dt.datetime.fromisoformat(str(calendar["session_close_et"]).replace("Z", "+00:00"))
    except (KeyError, ValueError) as exc:
        raise GenericLiveV1SubmissionError("fresh broker market calendar is invalid") from exc
    if not (opened <= executed.astimezone(opened.tzinfo) < closed):
        raise GenericLiveV1SubmissionError("generic Live v1 submission is outside market session")


def _validate_recovered_order(
    order: Mapping[str, Any], *, client_id: str, symbol: str, side: str, quantity: float,
) -> str:
    if str(order.get("client_order_id") or "") != client_id:
        raise GenericLiveV1SubmissionError("recovered broker order client id mismatch")
    if str(order.get("symbol") or "").upper() != symbol.upper():
        raise GenericLiveV1SubmissionError("recovered broker order symbol mismatch")
    recovered_side = str(order.get("side") or "").upper().split(".")[-1]
    if recovered_side != side.upper():
        raise GenericLiveV1SubmissionError("recovered broker order side mismatch")
    try:
        recovered_quantity = float(order.get("qty") or order.get("quantity"))
    except (TypeError, ValueError) as exc:
        raise GenericLiveV1SubmissionError("recovered broker order quantity is invalid") from exc
    if abs(recovered_quantity - quantity) > 1e-9:
        raise GenericLiveV1SubmissionError("recovered broker order quantity mismatch")
    return str(order.get("status") or "").strip().lower().split(".")[-1]


def _validate_existing_receipt(
    path: Path, *, intent_hash: str, client_id: str, broker_order: Mapping[str, Any]
) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise GenericLiveV1SubmissionError("existing broker receipt is unreadable") from exc
    if not isinstance(payload, Mapping) or payload.get("schema_version") != "caerus.generic_live_v1_broker_receipt.v1":
        raise GenericLiveV1SubmissionError("existing broker receipt schema differs")
    if payload.get("content_hash") != _hash(payload) or payload.get("intent_hash") != intent_hash:
        raise GenericLiveV1SubmissionError("existing broker receipt hash/intent differs")
    recorded = payload.get("broker_order")
    if not isinstance(recorded, Mapping) or recorded.get("broker_client_order_id") != client_id:
        raise GenericLiveV1SubmissionError("existing broker receipt client id differs")
    if recorded.get("broker_order_id") != str(broker_order.get("id") or ""):
        raise GenericLiveV1SubmissionError("existing broker receipt order id differs")


def _execute_generic_live_v1_session(
    *,
    activation_preflight: Mapping[str, Any],
    exact_plan: Mapping[str, Any],
    executed_at: str,
    submit_enabled: bool = False,
    broker: GenericLiveV1Broker | None = None,
    wal_directory: Path | str | None = None,
    rearm_state_path: Path | str | None = None,
    result_path: Path | str | None = None,
) -> dict[str, Any]:
    """Validate/dry-run or execute one exact session, then always re-arm."""

    if type(submit_enabled) is not bool:
        raise GenericLiveV1SubmissionError("submit_enabled must be a literal boolean")
    preflight = validate_generic_live_v1_activation_preflight(activation_preflight)
    failures = validate_lane_exact_execution_plan(exact_plan, as_of=executed_at)
    if failures:
        raise GenericLiveV1SubmissionError("exact plan is invalid: " + ",".join(failures))
    executed_raw, executed = _timestamp(executed_at)
    if exact_plan["content_hash"] != preflight.get("exact_plan_hash"):
        raise GenericLiveV1SubmissionError("activation preflight is not bound to exact plan")
    if exact_plan["trade_date"] != preflight["effective_session"] or executed.date().isoformat() != preflight["effective_session"]:
        raise GenericLiveV1SubmissionError("submission session differs from owner effective session")
    if exact_plan["lane_id"] != "generic-live-v1" or exact_plan["lane_kind"] != "LIVE":
        raise GenericLiveV1SubmissionError("exact plan is not generic-live-v1 LIVE")
    orders = [*exact_plan["sell_orders"], *exact_plan["buy_orders"]]
    if len(orders) > 1:
        raise GenericLiveV1SubmissionError("owner-approved Live v1 permits at most one order")
    effective_capital = min(460.0, float(preflight["observed_equity_usd"]))
    if float(exact_plan["deployable_capital"]) > effective_capital + 0.01:
        raise GenericLiveV1SubmissionError("exact plan exceeds effective account capital ceiling")
    marks = {str(row["symbol"]): float(row["price"]) for row in exact_plan["price_marks"]}
    gross = sum(
        max(float(row["quantity"]), 0.0) * marks.get(str(row["symbol"]), math.inf)
        for row in exact_plan["expected_posttrade_positions"]
    )
    if not math.isfinite(gross) or gross > effective_capital * 0.95 + 0.01:
        raise GenericLiveV1SubmissionError("exact plan exceeds owner-approved 95% gross ceiling")
    if float(exact_plan["expected_posttrade_cash"]) + 0.01 < effective_capital * 0.05:
        raise GenericLiveV1SubmissionError("exact plan does not preserve owner-approved 5% cash")

    base = {
        "schema_version": GENERIC_LIVE_V1_SUBMISSION_RESULT_SCHEMA,
        "result_id": "pending",
        "executed_at": executed_raw,
        "effective_session": preflight["effective_session"],
        "lane_id": "generic-live-v1",
        "sleeve_id": "caerus_lyra",
        "account_id_hash": preflight["account_id_hash"],
        "preflight_hash": preflight["content_hash"],
        "owner_decision_hash": preflight["owner_decision_hash"],
        "plan_id": exact_plan["plan_id"],
        "plan_hash": exact_plan["content_hash"],
        "submission_requested": submit_enabled,
        "legacy_executor_reachable": False,
        "paper_cutover_performed": False,
        "configuration_mutated": False,
        "schedule_mutated": False,
    }
    if not submit_enabled:
        body = {
            **base,
            "status": "VALIDATED_NO_WRITE",
            "reason_codes": ["EXPLICIT_SUBMISSION_NOT_ENABLED"],
            "client_order_id": _stable_client_id(exact_plan["content_hash"], orders[0]["order_id"]) if orders else None,
            "intent_hash": None,
            "broker_order": None,
            "broker_lookup_performed": False,
            "broker_submission_performed": False,
            "wal_written": False,
            "rearm_written": False,
            "result_written": False,
            "generic_kill_switch_state": "UNCHANGED",
        }
        seed = _hash(body)
        body["result_id"] = f"generic-live-v1-result:{preflight['effective_session']}:{seed[:24]}"
        body["content_hash"] = _hash(body)
        return body

    if preflight["status"] != "READY_TO_DISARM_FOR_SESSION":
        raise GenericLiveV1SubmissionError("blocked preflight cannot submit")
    if broker is None or wal_directory is None or rearm_state_path is None or result_path is None:
        raise GenericLiveV1SubmissionError("submission requires explicit broker, WAL, rearm, and result paths")
    wal_root = Path(wal_directory)
    rearm_path = Path(rearm_state_path)
    session_lock = _acquire_session_lock(
        wal_root / f"session-{preflight['effective_session']}.lock"
    )
    rearm_payload = _rearm(
        preflight_hash=preflight["content_hash"], plan_hash=exact_plan["content_hash"],
        executed_at=executed_raw, trigger="SESSION_COMPLETE",
    )
    failure_trigger = "PREFLIGHT_BREAK"
    try:
        _require_session_disarm(
            rearm_path, preflight_hash=preflight["content_hash"],
            plan_hash=exact_plan["content_hash"], effective_session=preflight["effective_session"],
        )
        _fresh_broker_preflight(
            broker=broker, plan=exact_plan, preflight=preflight, executed=executed
        )
        if not orders:
            _atomic_rearm(rearm_path, rearm_payload)
            broker_order = None
            intent_hash = None
            client_id = None
            lookup = False
            submitted = False
            status = "NO_TRADE_REARMED"
            reasons = ["EXACT_PLAN_HAS_NO_ORDERS"]
        else:
            order = orders[0]
            quantity = float(order["quantity"])
            notional = float(order["notional"])
            if not math.isfinite(quantity) or quantity <= 0 or abs(quantity - round(quantity)) > 1e-9:
                raise GenericLiveV1SubmissionError("submission order is not whole-share")
            if not math.isfinite(notional) or notional < 100.0 or notional > 437.0:
                raise GenericLiveV1SubmissionError("submission order breaches $100-$437 bound")
            client_id = _stable_client_id(exact_plan["content_hash"], order["order_id"])
            intent = {
                "schema_version": "caerus.generic_live_v1_wal_intent.v1",
                "created_at": executed_raw,
                "effective_session": preflight["effective_session"],
                "account_id_hash": preflight["account_id_hash"],
                "preflight_hash": preflight["content_hash"],
                "plan_hash": exact_plan["content_hash"],
                "order_id": order["order_id"],
                "client_order_id": client_id,
                "symbol": order["symbol"],
                "side": order["side"],
                "quantity": quantity,
                "estimated_notional": notional,
            }
            intent["content_hash"] = _hash(intent)
            intent_hash = intent["content_hash"]
            intent_path = wal_root / f"intent-{client_id}.json"
            _write_exclusive(intent_path, intent)
            failure_trigger = "SUBMISSION_BREAK"
            existing = broker.find_order_by_client_id(client_id)
            lookup = True
            if existing is None:
                existing = broker.submit_generic_live_v4_market_order(
                    symbol=order["symbol"], qty=quantity, side=order["side"],
                    client_order_id=client_id, estimated_notional=notional, tif="day",
                    _generic_live_v4_capability=_GENERIC_LIVE_V4_CAPABILITY,
                )
                submitted = True
                broker_status = _validate_recovered_order(
                    existing, client_id=client_id, symbol=order["symbol"],
                    side=order["side"], quantity=quantity,
                )
                accepted_statuses = {
                    "accepted", "new", "pending_new", "filled",
                    "accepted_for_bidding", "held",
                }
                if broker_status not in accepted_statuses:
                    failure_trigger = "ORDER_BREAK"
                    raise GenericLiveV1SubmissionError(
                        f"broker order returned terminal/unknown status: {broker_status or 'missing'}"
                    )
                status = "SUBMITTED_REARMED"
                reasons = ["EXACT_V4_ORDER_SUBMITTED"]
            else:
                broker_status = _validate_recovered_order(
                    existing, client_id=client_id, symbol=order["symbol"],
                    side=order["side"], quantity=quantity,
                )
                if broker_status not in {
                    "accepted", "new", "pending_new", "filled",
                    "accepted_for_bidding", "held",
                }:
                    failure_trigger = "ORDER_BREAK"
                    raise GenericLiveV1SubmissionError(
                        f"recovered broker order has terminal/partial/unknown status: {broker_status or 'missing'}"
                    )
                submitted = False
                status = "RECOVERED_EXISTING_REARMED"
                reasons = ["EXISTING_CLIENT_ORDER_RECOVERED_NO_RESUBMIT"]
            broker_order = _safe_broker_order(existing)
            receipt = {
                "schema_version": "caerus.generic_live_v1_broker_receipt.v1",
                "recorded_at": executed_raw,
                "intent_hash": intent_hash,
                "broker_order": broker_order,
                "submission_performed": submitted,
            }
            receipt["content_hash"] = _hash(receipt)
            receipt_path = wal_root / f"receipt-{client_id}.json"
            if not receipt_path.exists():
                _write_exclusive(receipt_path, receipt)
            else:
                _validate_existing_receipt(
                    receipt_path, intent_hash=intent_hash,
                    client_id=client_id, broker_order=existing,
                )
            _atomic_rearm(rearm_path, rearm_payload)
    except Exception:
        failure_rearm = _rearm(
            preflight_hash=preflight["content_hash"], plan_hash=exact_plan["content_hash"],
            executed_at=executed_raw, trigger=failure_trigger,
        )
        try:
            _atomic_rearm(rearm_path, failure_rearm)
        finally:
            fcntl.flock(session_lock, fcntl.LOCK_UN)
            os.close(session_lock)
        raise

    fcntl.flock(session_lock, fcntl.LOCK_UN)
    os.close(session_lock)

    body = {
        **base,
        "status": status,
        "reason_codes": reasons,
        "client_order_id": client_id,
        "intent_hash": intent_hash,
        "broker_order": broker_order,
        "broker_lookup_performed": lookup,
        "broker_submission_performed": submitted,
        "wal_written": bool(orders),
        "rearm_written": True,
        "result_written": True,
        "generic_kill_switch_state": "ARMED",
    }
    seed = _hash(body)
    body["result_id"] = f"generic-live-v1-result:{preflight['effective_session']}:{seed[:24]}"
    body["content_hash"] = _hash(body)
    _write_exclusive(Path(result_path), body)
    return body


def execute_generic_live_v1_session(
    *,
    activation_preflight: Mapping[str, Any],
    exact_plan: Mapping[str, Any],
    executed_at: str,
    submit_enabled: bool = False,
    broker: GenericLiveV1Broker | None = None,
    wal_directory: Path | str | None = None,
    rearm_state_path: Path | str | None = None,
    result_path: Path | str | None = None,
) -> dict[str, Any]:
    """Run the session and guarantee an emergency rearm on submit failures."""

    try:
        return _execute_generic_live_v1_session(
            activation_preflight=activation_preflight,
            exact_plan=exact_plan,
            executed_at=executed_at,
            submit_enabled=submit_enabled,
            broker=broker,
            wal_directory=wal_directory,
            rearm_state_path=rearm_state_path,
            result_path=result_path,
        )
    except BaseException:
        if submit_enabled is True and rearm_state_path is not None:
            try:
                ensure_generic_live_v1_rearmed_after_failure(
                    state_path=rearm_state_path,
                    preflight_hash=(activation_preflight.get("content_hash") if isinstance(activation_preflight, Mapping) else None),
                    plan_hash=(exact_plan.get("content_hash") if isinstance(exact_plan, Mapping) else None),
                    rearmed_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                )
            except BaseException as rearm_error:
                raise GenericLiveV1SubmissionError(
                    "generic Live v1 failed and emergency rearm persistence also failed"
                ) from rearm_error
        raise


def _validate_order_lifecycle(
    payload: Mapping[str, Any], *, submission_result: Mapping[str, Any]
) -> dict[str, Any]:
    fields = {
        "schema_version", "status", "observed_at", "submission_result_hash",
        "plan_hash", "account_id_hash", "lane_id", "broker_order_id",
        "filled_quantity", "content_hash",
    }
    if not isinstance(payload, Mapping) or set(payload) != fields:
        raise GenericLiveV1SubmissionError("order lifecycle artifact fields are invalid")
    if payload.get("schema_version") != "caerus.generic_live_v1_order_lifecycle.v1":
        raise GenericLiveV1SubmissionError("order lifecycle schema differs")
    _timestamp(str(payload["observed_at"]))
    if payload.get("content_hash") != _hash(payload):
        raise GenericLiveV1SubmissionError("order lifecycle content_hash mismatch")
    if payload.get("submission_result_hash") != submission_result["content_hash"] or payload.get("plan_hash") != submission_result["plan_hash"]:
        raise GenericLiveV1SubmissionError("order lifecycle submission/plan lineage differs")
    if payload.get("account_id_hash") != submission_result["account_id_hash"] or payload.get("lane_id") != "generic-live-v1":
        raise GenericLiveV1SubmissionError("order lifecycle account/lane scope differs")
    if payload.get("status") not in {"FILLED", "NO_TRADE"}:
        raise GenericLiveV1SubmissionError("order lifecycle is not terminal green")
    if payload["status"] == "FILLED":
        if not payload.get("broker_order_id") or float(payload.get("filled_quantity") or 0.0) <= 0.0:
            raise GenericLiveV1SubmissionError("filled order lifecycle evidence is incomplete")
    elif payload.get("broker_order_id") is not None or float(payload.get("filled_quantity") or 0.0) != 0.0:
        raise GenericLiveV1SubmissionError("NO_TRADE lifecycle cannot carry a fill")
    return copy.deepcopy(dict(payload))


def finalize_generic_live_v1_posttrade(
    *, submission_result: Mapping[str, Any], exact_plan: Mapping[str, Any],
    order_lifecycle: Mapping[str, Any], reconciliation: Mapping[str, Any],
    journal_entries: list[Mapping[str, Any]], performance: Mapping[str, Any],
    dashboard_projection: Mapping[str, Any], finalized_at: str,
    rearm_state_path: Path | str, result_path: Path | str,
) -> dict[str, Any]:
    """Validate factual typed artifacts; arbitrary booleans/hashes are rejected."""

    if not isinstance(submission_result, Mapping) or submission_result.get("schema_version") != GENERIC_LIVE_V1_SUBMISSION_RESULT_SCHEMA or submission_result.get("content_hash") != _hash(submission_result):
        raise GenericLiveV1SubmissionError("posttrade submission result is invalid")
    if exact_plan.get("content_hash") != submission_result.get("plan_hash") or validate_lane_exact_execution_plan(exact_plan):
        raise GenericLiveV1SubmissionError("posttrade exact plan lineage is invalid")
    finalized_raw, _ = _timestamp(finalized_at)
    trigger = "SESSION_COMPLETE"
    try:
        order = _validate_order_lifecycle(order_lifecycle, submission_result=submission_result)
    except Exception:
        trigger = "ORDER_BREAK"
        raise
    finally:
        if trigger != "SESSION_COMPLETE":
            rearm_generic_live_v1_session(
                state_path=rearm_state_path, preflight_hash=submission_result["preflight_hash"],
                plan_hash=submission_result["plan_hash"], rearmed_at=finalized_raw, trigger=trigger,
            )
    try:
        reconciled = validate_lane_reconciliation(reconciliation, exact_plan=exact_plan)
        if reconciled["status"] != "PASS" or reconciled["accounting_ready"] is not True:
            raise GenericLiveV1SubmissionError("reconciliation is not PASS/accounting-ready")
    except Exception:
        rearm_generic_live_v1_session(
            state_path=rearm_state_path, preflight_hash=submission_result["preflight_hash"],
            plan_hash=submission_result["plan_hash"], rearmed_at=finalized_raw,
            trigger="RECONCILIATION_BREAK",
        )
        raise
    try:
        journal = validate_accounting_journal(journal_entries)
        if reconciled["reconciled_fills"] and not journal:
            raise GenericLiveV1SubmissionError("filled reconciliation has no accounting journal")
        if any(
            row["source_hash"] != reconciled["content_hash"]
            or row["account_id_hash"] != submission_result["account_id_hash"]
            or row["lane_id"] != "generic-live-v1"
            for row in journal
        ):
            raise GenericLiveV1SubmissionError("accounting journal lineage differs")
    except Exception:
        rearm_generic_live_v1_session(
            state_path=rearm_state_path, preflight_hash=submission_result["preflight_hash"],
            plan_hash=submission_result["plan_hash"], rearmed_at=finalized_raw,
            trigger="ACCOUNTING_BREAK",
        )
        raise
    try:
        perf = validate_lane_performance(performance)
        dashboard = validate_dashboard_performance_surfaces(dashboard_projection)
        if (
            perf["lane_id"] != "generic-live-v1"
            or perf["account_id_hash"] != submission_result["account_id_hash"]
            or perf["lane_kind"] != "LIVE"
            or not any("generic-live-v1" in row["lane_ids"] for row in dashboard["performance_surfaces"])
        ):
            raise GenericLiveV1SubmissionError("performance/dashboard scope differs")
    except Exception:
        rearm_generic_live_v1_session(
            state_path=rearm_state_path, preflight_hash=submission_result["preflight_hash"],
            plan_hash=submission_result["plan_hash"], rearmed_at=finalized_raw,
            trigger="REPORTING_BREAK",
        )
        raise
    rearm = rearm_generic_live_v1_session(
        state_path=rearm_state_path, preflight_hash=submission_result["preflight_hash"],
        plan_hash=submission_result["plan_hash"], rearmed_at=finalized_raw,
        trigger="SESSION_COMPLETE",
    )
    evidence_hashes = sorted([
        order["content_hash"], reconciled["content_hash"],
        *[row["record_hash"] for row in journal], perf["content_hash"], dashboard["content_hash"],
    ])
    body = {
        "schema_version": GENERIC_LIVE_V1_POSTTRADE_RESULT_SCHEMA,
        "finalized_at": finalized_raw, "status": "GREEN_REARMED",
        "reason_codes": ["ALL_TYPED_POSTTRADE_ARTIFACTS_GREEN"],
        "submission_result_hash": submission_result["content_hash"],
        "preflight_hash": submission_result["preflight_hash"],
        "plan_hash": submission_result["plan_hash"],
        "evidence_hashes": evidence_hashes, "rearm_hash": rearm["content_hash"],
        "generic_kill_switch_state": "ARMED", "legacy_executor_enabled": False,
        "paper_cutover_enabled": False, "broker_submission_allowed": False,
    }
    body["content_hash"] = _hash(body)
    _write_exclusive(Path(result_path), body)
    return body


__all__ = [
    "GENERIC_LIVE_V1_SUBMISSION_RESULT_SCHEMA", "GenericLiveV1SubmissionError",
    "execute_generic_live_v1_session", "finalize_generic_live_v1_posttrade",
    "ensure_generic_live_v1_rearmed_after_failure", "rearm_generic_live_v1_session",
]

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
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from authority.lane_exact_plan import canonical_json, validate_lane_exact_execution_plan
from brokers.alpaca_broker import _GENERIC_LIVE_V4_CAPABILITY
from core.generic_live_v1_activation import (
    validate_generic_live_v1_activation_preflight,
    validate_generic_live_v1_lyra_plan_chain,
)
from core.generic_live_v1_capital import build_generic_live_v1_capital_proof
from core.accounting_journal import validate_accounting_journal
from core.lane_performance import validate_lane_performance
from core.lane_reconciliation import validate_lane_reconciliation
from core.lane_oms import build_lane_oms_intents
from core.lane_truth_status import validate_dashboard_performance_surfaces
from core.generic_live_v1_ops import reject_sensitive_payload


GENERIC_LIVE_V1_SUBMISSION_RESULT_SCHEMA = "caerus.generic_live_v1_submission_result.v1"
GENERIC_LIVE_V1_REARM_SCHEMA = "caerus.generic_live_v1_rearm.v1"
GENERIC_LIVE_V1_POSTTRADE_RESULT_SCHEMA = "caerus.generic_live_v1_posttrade_result.v1"
GENERIC_LIVE_V1_ORDER_LIFECYCLE_SCHEMA = "caerus.generic_live_v1_order_lifecycle.v1"


class GenericLiveV1SubmissionError(RuntimeError):
    """Raised before or during an explicitly authorized generic submission."""


class GenericLiveV1Broker(Protocol):
    def get_account(self) -> Mapping[str, Any]: ...
    def get_positions(self) -> list[Mapping[str, Any]]: ...
    def list_orders(self, status: str = "open", limit: int = 100) -> list[Mapping[str, Any]]: ...
    def get_market_session_calendar(self, trade_date: str) -> Mapping[str, Any]: ...
    def get_asset(self, symbol: str) -> Mapping[str, Any] | None: ...
    def find_order_by_client_id(self, client_id: str) -> Mapping[str, Any] | None: ...

    def submit_generic_live_v4_limit_order(self, **kwargs: Any) -> Mapping[str, Any]: ...
    def cancel_generic_live_v4_order(self, **kwargs: Any) -> None: ...
    def get_order(self, order_id: str) -> Mapping[str, Any] | None: ...


def _hash(payload: Mapping[str, Any]) -> str:
    body = copy.deepcopy(dict(payload))
    body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def _validate_submission_result(payload: Mapping[str, Any]) -> dict[str, Any]:
    if (
        not isinstance(payload, Mapping)
        or payload.get("schema_version") != GENERIC_LIVE_V1_SUBMISSION_RESULT_SCHEMA
        or payload.get("content_hash") != _hash(payload)
    ):
        raise GenericLiveV1SubmissionError("generic Live v1 submission result is invalid")
    seed_body = copy.deepcopy(dict(payload))
    seed_body.pop("content_hash", None)
    seed_body["result_id"] = "pending"
    expected_id = (
        f"generic-live-v1-result:{payload.get('effective_session')}:"
        f"{_hash(seed_body)[:24]}"
    )
    if payload.get("result_id") != expected_id:
        raise GenericLiveV1SubmissionError("generic Live v1 submission result identity differs")
    observation_hashes = payload.get("broker_observation_hashes")
    if not isinstance(observation_hashes, list) or len(observation_hashes) != len(set(observation_hashes)) or any(
        not isinstance(value, str) or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
        for value in observation_hashes
    ):
        raise GenericLiveV1SubmissionError("submission broker observation hashes are invalid")
    cancellation_hash = payload.get("cancellation_evidence_hash")
    if cancellation_hash is not None and (
        not isinstance(cancellation_hash, str) or len(cancellation_hash) != 64
        or any(character not in "0123456789abcdef" for character in cancellation_hash)
    ):
        raise GenericLiveV1SubmissionError("submission cancellation evidence hash is invalid")
    if bool(payload.get("cancel_performed", False)) is not bool(cancellation_hash):
        raise GenericLiveV1SubmissionError("submission cancellation evidence binding differs")
    return copy.deepcopy(dict(payload))


def validate_generic_live_v1_submission_result(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Public strict validator for persisted submission results."""

    return _validate_submission_result(payload)


def _timestamp(value: str) -> tuple[str, dt.datetime]:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise GenericLiveV1SubmissionError("executed_at must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise GenericLiveV1SubmissionError("executed_at must include a timezone")
    return str(value), parsed


def _mutation_context(
    *, preflight: Mapping[str, Any], plan: Mapping[str, Any], order: Mapping[str, Any],
    capital_proof: Mapping[str, Any],
) -> dict[str, Any]:
    body = {
        "schema_version": "caerus.generic_live_v1_mutation_context.v1",
        "action": "SUBMIT",
        "effective_session": preflight["effective_session"],
        "owner_decision_hash": preflight["owner_decision_hash"],
        "preflight_hash": preflight["content_hash"],
        "plan_hash": plan["content_hash"],
        "execution_policy_hash": plan["execution_policy_hash"],
        "account_id_hash": preflight["account_id_hash"],
        "deployed_sha": preflight["deployed_sha"],
        "order_id": order["order_id"],
        "client_order_id": order["client_order_id"],
        "symbol": order["symbol"],
        "side": order["side"],
        "quantity": float(order["quantity"]),
        "order_type": "limit",
        "time_in_force": "day",
        "extended_hours": False,
        "allow_fractional_shares": False,
        "quantity_precision": 0,
        "limit_price": float(order["enforcement_price"]),
        "max_fee_usd": 0.01,
        "maximum_gross_usd": capital_proof["dynamic_gross_cap_usd"],
        "capital_proof_hash": capital_proof["content_hash"],
        "fresh_equity_usd": capital_proof["fresh_equity_usd"],
        "fresh_cash_usd": capital_proof["fresh_cash_usd"],
        "effective_capital_usd": capital_proof["effective_capital_usd"],
        "dynamic_gross_cap_usd": capital_proof["dynamic_gross_cap_usd"],
        "required_cash_reserve_usd": capital_proof["required_cash_reserve_usd"],
        "worst_case_posttrade_gross_usd": capital_proof["worst_case_posttrade_gross_usd"],
        "worst_case_posttrade_cash_usd": capital_proof["worst_case_posttrade_cash_usd"],
        "capital_gross_limit_pass": capital_proof["gross_limit_pass"],
        "capital_cash_reserve_pass": capital_proof["cash_reserve_pass"],
        "starting_symbol_quantity": capital_proof["starting_symbol_quantity"],
        "starting_other_gross_usd": capital_proof["starting_other_gross_usd"],
        "gross_valuation_price": capital_proof["gross_valuation_price"],
        "expected_posttrade_symbol_quantity": capital_proof["expected_posttrade_symbol_quantity"],
    }
    body["content_hash"] = _hash(body)
    body["capability_signature"] = _GENERIC_LIVE_V4_CAPABILITY.sign(body["content_hash"])
    return body


def _cancellation_context(
    *, submission_context: Mapping[str, Any], broker_order_id: str
) -> dict[str, Any]:
    body = {
        key: copy.deepcopy(value)
        for key, value in submission_context.items()
        if key not in {"content_hash", "capability_signature"}
    }
    body["schema_version"] = "caerus.generic_live_v1_cancellation_context.v1"
    body["action"] = "CANCEL"
    body["broker_order_id"] = str(broker_order_id)
    body["content_hash"] = _hash(body)
    body["capability_signature"] = _GENERIC_LIVE_V4_CAPABILITY.sign(body["content_hash"])
    return body


def _validate_owner_execution_policy(plan: Mapping[str, Any]) -> None:
    if plan.get("broker_environment") != "alpaca_live":
        raise GenericLiveV1SubmissionError("exact plan broker environment is not Alpaca Live")
    constraints = plan["constraints"]
    required = {
        "order_type": "limit", "time_in_force": "day",
        "allow_extended_hours": False, "allow_fractional_shares": False,
        "quantity_precision": 0, "minimum_order_notional_usd": 100.0,
        "maximum_order_notional_usd": 437.0,
        "maximum_total_buy_notional_usd": 437.0, "maximum_orders": 1,
        "price_precision": 4, "max_adverse_slippage_bps": 25.0,
    }
    if any(constraints.get(key) != value for key, value in required.items()):
        raise GenericLiveV1SubmissionError("exact plan execution policy differs from Live v1")
    for order in [*plan["sell_orders"], *plan["buy_orders"]]:
        if order["order_type"] != "limit" or order["time_in_force"] != "day" or order["extended_hours"] is not False:
            raise GenericLiveV1SubmissionError("exact order is not DAY limit/no-extended")
        if float(order["quantity"]) * float(order["enforcement_price"]) + 0.01 > 437.0 + 1e-9:
            raise GenericLiveV1SubmissionError("exact limit plus maximum fee breaches $437")


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
        "broker_status": str(order.get("status") or "").strip().lower().split(".")[-1],
        "symbol": str(order.get("symbol") or "").strip().upper(),
        "side": str(order.get("side") or "").strip().upper().split(".")[-1],
        "quantity": str(order.get("qty") or order.get("quantity") or ""),
    }


def _order_status_observation(
    *, sequence: int, phase: str, order: Mapping[str, Any], status_override: str | None = None,
) -> dict[str, Any]:
    safe = _safe_broker_order(order)
    if status_override is not None:
        safe["broker_status"] = status_override
    body = {
        "sequence": sequence,
        "phase": phase,
        "broker_order": safe,
        "source_hash": hashlib.sha256(canonical_json(safe).encode("utf-8")).hexdigest(),
    }
    body["content_hash"] = _hash(body)
    return body


def _validate_order_status_observations(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list) or not payload:
        raise GenericLiveV1SubmissionError("broker receipt lacks order status observations")
    checked: list[dict[str, Any]] = []
    for index, raw in enumerate(payload):
        if not isinstance(raw, Mapping) or set(raw) != {
            "sequence", "phase", "broker_order", "source_hash", "content_hash",
        }:
            raise GenericLiveV1SubmissionError("order status observation fields are invalid")
        if raw["sequence"] != index or not isinstance(raw["phase"], str) or not raw["phase"]:
            raise GenericLiveV1SubmissionError("order status observation sequence/phase differs")
        safe = raw["broker_order"]
        if not isinstance(safe, Mapping) or set(safe) != {
            "broker_order_id", "broker_client_order_id", "broker_status",
            "symbol", "side", "quantity",
        } or any(not isinstance(value, str) for value in safe.values()):
            raise GenericLiveV1SubmissionError("order status observation is not safely redacted")
        source = hashlib.sha256(canonical_json(safe).encode("utf-8")).hexdigest()
        if raw["source_hash"] != source or raw["content_hash"] != _hash(raw):
            raise GenericLiveV1SubmissionError("order status observation hash differs")
        checked.append(copy.deepcopy(dict(raw)))
    return checked


def _safe_broker_observation(kind: str, value: Any) -> Any:
    """Normalize broker reads without credentials or raw account identifiers."""

    if kind == "account" and isinstance(value, Mapping):
        return {
            "account_id_hash": str(value.get("id_hash") or ""),
            "status": str(value.get("status") or ""),
            "trading_blocked": value.get("trading_blocked"),
            "account_blocked": value.get("account_blocked"),
            "equity": str(value.get("equity") or ""),
            "cash": str(value.get("cash") or ""),
            "buying_power": str(value.get("buying_power") or ""),
        }
    if kind in {"positions", "open_orders"} and isinstance(value, list):
        if kind == "positions":
            return [
                {"symbol": str(row.get("symbol") or "").upper(), "quantity": str(row.get("qty") or row.get("quantity") or "")}
                for row in value if isinstance(row, Mapping)
            ]
        return [_safe_broker_order(row) for row in value if isinstance(row, Mapping)]
    if kind == "asset" and isinstance(value, Mapping):
        return {
            "symbol": str(value.get("symbol") or "").upper(),
            "status": str(value.get("status") or ""),
            "tradable": value.get("tradable"),
        }
    if kind == "calendar" and isinstance(value, Mapping):
        return {
            "trade_date": str(value.get("trade_date") or ""),
            "session_open_et": str(value.get("session_open_et") or ""),
            "session_close_et": str(value.get("session_close_et") or ""),
        }
    if kind in {"client_order_lookup", "submission_response", "order_poll", "cancellation_poll"}:
        return None if value is None else _safe_broker_order(value)
    if kind == "cancellation_request" and isinstance(value, Mapping):
        return {
            "broker_order_id": str(value.get("broker_order_id") or ""),
            "cancellation_context_hash": str(value.get("content_hash") or ""),
        }
    raise GenericLiveV1SubmissionError(f"unsupported broker observation kind: {kind}")


def _persist_broker_observation(
    *, wal_root: Path, effective_session: str, sequence: int,
    kind: str, value: Any, recorded_at: str,
) -> dict[str, Any]:
    body = {
        "schema_version": "caerus.generic_live_v1_broker_observation.v1",
        "effective_session": effective_session,
        "sequence": sequence,
        "kind": kind,
        "recorded_at": recorded_at,
        "observation": _safe_broker_observation(kind, value),
    }
    body["content_hash"] = _hash(body)
    _write_exclusive(
        wal_root / f"observation-{sequence:03d}-{kind}-{body['content_hash'][:16]}.json", body
    )
    return body


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
    executed: dt.datetime, observe: Callable[[str, Any], Mapping[str, Any]],
) -> dict[str, Any]:
    account = broker.get_account()
    observe("account", account)
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
    position_rows = broker.get_positions()
    observe("positions", position_rows)
    positions = {
        str(row.get("symbol") or "").upper(): float(row.get("qty") or row.get("quantity") or 0.0)
        for row in position_rows
    }
    expected_positions = {
        str(row["symbol"]): float(row["quantity"]) for row in plan["starting_positions"]
    }
    if positions != expected_positions:
        raise GenericLiveV1SubmissionError("fresh broker positions differ from exact plan snapshot")
    open_orders = broker.list_orders(status="open", limit=100)
    observe("open_orders", open_orders)
    if open_orders:
        raise GenericLiveV1SubmissionError("fresh broker open orders are present")
    orders = [*plan["sell_orders"], *plan["buy_orders"]]
    if orders:
        order = orders[0]
        asset = broker.get_asset(str(order["symbol"]))
        observe("asset", asset)
        if not isinstance(asset, Mapping) or asset.get("tradable") is not True or str(asset.get("status") or "").lower().split(".")[-1] != "active":
            raise GenericLiveV1SubmissionError("fresh broker asset is not active/tradable")
        required_buying_power = (
            float(order["quantity"]) * float(order["enforcement_price"]) + 0.01
        )
        if order["side"] == "BUY" and buying_power + 1e-9 < required_buying_power:
            raise GenericLiveV1SubmissionError("fresh broker buying power is below exact order notional")
    calendar = broker.get_market_session_calendar(plan["trade_date"])
    observe("calendar", calendar)
    try:
        opened = dt.datetime.fromisoformat(str(calendar["session_open_et"]).replace("Z", "+00:00"))
        closed = dt.datetime.fromisoformat(str(calendar["session_close_et"]).replace("Z", "+00:00"))
    except (KeyError, ValueError) as exc:
        raise GenericLiveV1SubmissionError("fresh broker market calendar is invalid") from exc
    if not (opened <= executed.astimezone(opened.tzinfo) < closed):
        raise GenericLiveV1SubmissionError("generic Live v1 submission is outside market session")
    proof = build_generic_live_v1_capital_proof(
        exact_plan=plan, fresh_equity_usd=equity, fresh_cash_usd=cash,
    )
    if proof["gross_limit_pass"] is not True or proof["cash_reserve_pass"] is not True or proof["long_only_pass"] is not True:
        raise GenericLiveV1SubmissionError("fresh worst-case capital proof is not green")
    return proof


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
    path: Path, *, intent_hash: str, client_id: str, broker_order: Mapping[str, Any],
    mutation_context_hash: str,
) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise GenericLiveV1SubmissionError("existing broker receipt is unreadable") from exc
    if not isinstance(payload, Mapping) or payload.get("schema_version") != "caerus.generic_live_v1_broker_receipt.v1":
        raise GenericLiveV1SubmissionError("existing broker receipt schema differs")
    if payload.get("content_hash") != _hash(payload) or payload.get("intent_hash") != intent_hash:
        raise GenericLiveV1SubmissionError("existing broker receipt hash/intent differs")
    if payload.get("mutation_context_hash") != mutation_context_hash:
        raise GenericLiveV1SubmissionError("existing broker receipt mutation context differs")
    _validate_order_status_observations(payload.get("order_status_observations"))
    cancellation_hash = payload.get("cancellation_context_hash")
    if (
        (cancellation_hash is not None and (
            not isinstance(cancellation_hash, str)
            or len(cancellation_hash) != 64
            or any(character not in "0123456789abcdef" for character in cancellation_hash)
        ))
        or (payload.get("cancel_performed") is True) is not (cancellation_hash is not None)
    ):
        raise GenericLiveV1SubmissionError("existing broker receipt cancellation lineage differs")
    recorded = payload.get("broker_order")
    if not isinstance(recorded, Mapping) or recorded.get("broker_client_order_id") != client_id:
        raise GenericLiveV1SubmissionError("existing broker receipt client id differs")
    if recorded.get("broker_order_id") != str(broker_order.get("id") or ""):
        raise GenericLiveV1SubmissionError("existing broker receipt order id differs")
    return copy.deepcopy(dict(payload))


def _execute_generic_live_v1_session(
    *,
    activation_preflight: Mapping[str, Any],
    exact_plan: Mapping[str, Any],
    lyra_decision: Mapping[str, Any] | None = None,
    executed_at: str,
    submit_enabled: bool = False,
    broker: GenericLiveV1Broker | None = None,
    wal_directory: Path | str | None = None,
    rearm_state_path: Path | str | None = None,
    result_path: Path | str | None = None,
    poll_attempts: int = 3,
    poll_interval_seconds: float = 1.0,
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
    _validate_owner_execution_policy(exact_plan)
    if submit_enabled:
        if not isinstance(lyra_decision, Mapping):
            raise GenericLiveV1SubmissionError(
                "submission requires the exact protected Lyra v2 decision"
            )
        if lyra_decision.get("content_hash") != preflight.get("lyra_decision_hash"):
            raise GenericLiveV1SubmissionError("submission Lyra decision pin differs")
        validate_generic_live_v1_lyra_plan_chain(
            lyra_decision=lyra_decision, exact_plan=exact_plan,
            effective_session=preflight["effective_session"],
            account_id_hash=preflight["account_id_hash"],
            fresh_equity_usd=float(preflight["observed_equity_usd"]),
            fresh_cash_usd=float(exact_plan["starting_cash"]),
        )
    if type(poll_attempts) is not int or poll_attempts < 1 or poll_attempts > 10:
        raise GenericLiveV1SubmissionError("poll_attempts must be in [1, 10]")
    if not math.isfinite(float(poll_interval_seconds)) or not 0.0 <= float(poll_interval_seconds) <= 5.0:
        raise GenericLiveV1SubmissionError("poll_interval_seconds must be in [0, 5]")
    effective_capital = min(460.0, float(preflight["observed_equity_usd"]))
    if float(exact_plan["deployable_capital"]) > effective_capital + 0.01:
        raise GenericLiveV1SubmissionError("exact plan exceeds effective account capital ceiling")
    validation_capital = build_generic_live_v1_capital_proof(
        exact_plan=exact_plan, fresh_equity_usd=float(preflight["observed_equity_usd"]),
        fresh_cash_usd=float(exact_plan["starting_cash"]),
    )
    if validation_capital["gross_limit_pass"] is not True:
        raise GenericLiveV1SubmissionError("exact plan exceeds owner-approved dynamic 95% gross ceiling")
    if validation_capital["cash_reserve_pass"] is not True:
        raise GenericLiveV1SubmissionError("exact plan does not preserve dynamic owner-approved 5% cash")

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
            "exact_order_id": orders[0]["order_id"] if orders else None,
            "client_order_id": orders[0]["client_order_id"] if orders else None,
            "intent_hash": None,
            "mutation_context_hash": None,
            "cancellation_context_hash": None,
            "receipt_hash": None,
            "broker_order": None,
            "broker_lookup_performed": False,
            "broker_submission_performed": False,
            "broker_observation_hashes": [],
            "cancellation_evidence_hash": None,
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
    broker_observation_hashes: list[str] = []
    observation_sequence = 0

    def observe(kind: str, value: Any) -> Mapping[str, Any]:
        nonlocal observation_sequence
        observation_sequence += 1
        artifact = _persist_broker_observation(
            wal_root=wal_root,
            effective_session=preflight["effective_session"],
            sequence=observation_sequence,
            kind=kind,
            value=value,
            recorded_at=executed_raw,
        )
        broker_observation_hashes.append(artifact["content_hash"])
        return artifact

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
        capital_proof = _fresh_broker_preflight(
            broker=broker, plan=exact_plan, preflight=preflight, executed=executed,
            observe=observe,
        )
        if not orders:
            _atomic_rearm(rearm_path, rearm_payload)
            broker_order = None
            intent_hash = None
            mutation_context_hash = None
            cancellation_context_hash = None
            receipt_hash = None
            cancellation_evidence_hash = None
            client_id = None
            lookup = False
            submitted = False
            status = "NO_TRADE_REARMED"
            reasons = ["EXACT_PLAN_HAS_NO_ORDERS"]
        else:
            order = orders[0]
            quantity = float(order["quantity"])
            notional = float(order["quantity"]) * float(order["enforcement_price"]) + 0.01
            if not math.isfinite(quantity) or quantity <= 0 or abs(quantity - round(quantity)) > 1e-9:
                raise GenericLiveV1SubmissionError("submission order is not whole-share")
            if not math.isfinite(notional) or notional < 100.0 or notional > 437.0:
                raise GenericLiveV1SubmissionError("submission order breaches $100-$437 bound")
            client_id = str(order["client_order_id"])
            intents = build_lane_oms_intents(exact_plan)
            if len(intents) != 1 or intents[0]["order_id"] != order["order_id"]:
                raise GenericLiveV1SubmissionError("exact plan did not produce one exact OMS intent")
            intent = intents[0]
            intent_hash = intent["content_hash"]
            intent_path = wal_root / f"intent-{client_id}.json"
            _write_exclusive(intent_path, intent)
            failure_trigger = "SUBMISSION_BREAK"
            existing = broker.find_order_by_client_id(client_id)
            observe("client_order_lookup", existing)
            lookup = True
            if existing is None:
                context = _mutation_context(
                    preflight=preflight, plan=exact_plan, order=order,
                    capital_proof=capital_proof,
                )
                existing = broker.submit_generic_live_v4_limit_order(
                    symbol=order["symbol"], qty=quantity, side=order["side"],
                    client_order_id=client_id, limit_price=float(order["enforcement_price"]),
                    max_fee_usd=0.01, mutation_context=context, tif="day",
                    _generic_live_v4_capability=_GENERIC_LIVE_V4_CAPABILITY,
                )
                observe("submission_response", existing)
                submitted = True
            else:
                submitted = False
                context = _mutation_context(
                    preflight=preflight, plan=exact_plan, order=order,
                    capital_proof=capital_proof,
                )
            mutation_context_hash = context["content_hash"]
            broker_status = _validate_recovered_order(
                existing, client_id=client_id, symbol=order["symbol"],
                side=order["side"], quantity=quantity,
            )
            observations = [
                _order_status_observation(
                    sequence=0, phase="RECOVERED" if not submitted else "SUBMITTED",
                    order=existing,
                )
            ]
            failure_trigger = "ORDER_BREAK"
            broker_order_id = str(existing.get("id") or "")
            open_statuses = {"accepted", "new", "pending_new", "accepted_for_bidding", "held"}
            canceling_statuses = {"pending_cancel"}
            terminal_break_statuses = {"canceled", "cancelled", "rejected", "expired"}
            unresolved_reason = None
            for attempt in range(poll_attempts):
                if broker_status == "filled" or broker_status in terminal_break_statuses or broker_status == "partially_filled":
                    break
                if broker_status not in open_statuses:
                    unresolved_reason = f"UNRESOLVED_BROKER_STATUS:{broker_status or 'missing'}"
                    break
                if attempt + 1 < poll_attempts and poll_interval_seconds:
                    time.sleep(float(poll_interval_seconds))
                refreshed = broker.get_order(broker_order_id)
                observe("order_poll", refreshed)
                if refreshed is None:
                    unresolved_reason = "BROKER_ORDER_MISSING_DURING_POLL"
                    observations.append(
                        _order_status_observation(
                            sequence=len(observations), phase="POLL",
                            order=existing, status_override="missing_after_poll",
                        )
                    )
                    break
                existing = refreshed
                broker_status = _validate_recovered_order(
                    existing, client_id=client_id, symbol=order["symbol"],
                    side=order["side"], quantity=quantity,
                )
                observations.append(
                    _order_status_observation(
                        sequence=len(observations), phase="POLL", order=existing,
                    )
                )
            cancel_performed = False
            cancellation_context_hash = None
            cancellation_evidence_hash = None
            if unresolved_reason is None and (
                broker_status in open_statuses or broker_status == "partially_filled"
            ):
                cancel_context = _cancellation_context(
                    submission_context=context, broker_order_id=broker_order_id
                )
                cancellation_context_hash = cancel_context["content_hash"]
                observe("cancellation_request", {
                    "broker_order_id": broker_order_id,
                    "content_hash": cancel_context["content_hash"],
                })
                cancellation_terminal = False
                cancellation_operation_failed = False
                try:
                    broker.cancel_generic_live_v4_order(
                        broker_order_id=broker_order_id, mutation_context=cancel_context,
                        _generic_live_v4_capability=_GENERIC_LIVE_V4_CAPABILITY,
                    )
                    cancel_performed = True
                    for cancel_attempt in range(poll_attempts):
                        refreshed = broker.get_order(broker_order_id)
                        observe("cancellation_poll", refreshed)
                        if refreshed is None:
                            unresolved_reason = "BROKER_ORDER_MISSING_AFTER_CANCEL"
                            observations.append(
                                _order_status_observation(
                                    sequence=len(observations), phase="CANCEL_POLL",
                                    order=existing, status_override="missing_after_cancel",
                                )
                            )
                            break
                        existing = refreshed
                        broker_status = _validate_recovered_order(
                            existing, client_id=client_id, symbol=order["symbol"],
                            side=order["side"], quantity=quantity,
                        )
                        observations.append(
                            _order_status_observation(
                                sequence=len(observations), phase="CANCEL_POLL", order=existing,
                            )
                        )
                        if broker_status in terminal_break_statuses | {"filled"}:
                            cancellation_terminal = True
                            break
                        if broker_status not in open_statuses | canceling_statuses | {"partially_filled"}:
                            unresolved_reason = f"UNRESOLVED_POST_CANCEL_STATUS:{broker_status or 'missing'}"
                            break
                        if cancel_attempt + 1 < poll_attempts and poll_interval_seconds:
                            time.sleep(float(poll_interval_seconds))
                except BaseException:
                    cancellation_operation_failed = True
                    unresolved_reason = "CANCELLATION_OPERATION_FAILED"
                if not cancellation_terminal and unresolved_reason is None:
                    unresolved_reason = f"CANCEL_NOT_TERMINAL:{broker_status}"
                cancellation = {
                    "schema_version": "caerus.generic_live_v1_cancellation_evidence.v1",
                    "effective_session": preflight["effective_session"],
                    "broker_order_id": broker_order_id,
                    "cancellation_context_hash": cancel_context["content_hash"],
                    "status": "TERMINAL" if cancellation_terminal else "UNRESOLVED",
                    "operation_failed": cancellation_operation_failed,
                    "terminal_broker_status": broker_status if cancellation_terminal else None,
                    "broker_observation_hashes": list(broker_observation_hashes),
                    "recorded_at": executed_raw,
                }
                cancellation["content_hash"] = _hash(cancellation)
                _write_exclusive(
                    wal_root / f"cancellation-{client_id}-{cancellation['content_hash'][:16]}.json",
                    cancellation,
                )
                cancellation_evidence_hash = cancellation["content_hash"]
            filled_quantity = float(existing.get("filled_qty") or (quantity if broker_status == "filled" else 0.0))
            if unresolved_reason is not None:
                status = "UNRESOLVED_ORDER_REARMED"
                reasons = [unresolved_reason]
            elif broker_status == "filled" and abs(filled_quantity - quantity) <= 1e-9:
                status = "FILLED_REARMED" if submitted else "RECOVERED_FILLED_REARMED"
                reasons = ["EXACT_V4_ORDER_FILLED"] if submitted else ["EXISTING_FILLED_ORDER_RECOVERED_NO_RESUBMIT"]
            elif broker_status in terminal_break_statuses or broker_status == "partially_filled":
                failure_trigger = "ORDER_BREAK"
                status = "ORDER_BREAK_REARMED"
                reasons = ["PARTIAL_OR_TERMINAL_ORDER_REQUIRES_RECONCILIATION"]
            else:
                failure_trigger = "ORDER_BREAK"
                raise GenericLiveV1SubmissionError("broker order did not reach a safe terminal state")
            broker_order = _safe_broker_order(existing)
            receipt = {
                "schema_version": "caerus.generic_live_v1_broker_receipt.v1",
                "recorded_at": executed_raw,
                "intent_hash": intent_hash,
                "mutation_context_hash": mutation_context_hash,
                "cancellation_context_hash": cancellation_context_hash,
                "broker_observation_hashes": list(broker_observation_hashes),
                "cancellation_evidence_hash": cancellation_evidence_hash,
                "broker_order": broker_order,
                "order_status_observations": observations,
                "submission_performed": submitted,
                "cancel_performed": cancel_performed,
            }
            receipt["content_hash"] = _hash(receipt)
            receipt_hash = receipt["content_hash"]
            receipt_path = wal_root / f"receipt-{client_id}-{receipt_hash}.json"
            _write_exclusive(receipt_path, receipt)
            terminal_rearm = _rearm(
                preflight_hash=preflight["content_hash"], plan_hash=exact_plan["content_hash"],
                executed_at=executed_raw,
                trigger=(
                    "ORDER_BREAK"
                    if status in {"ORDER_BREAK_REARMED", "UNRESOLVED_ORDER_REARMED"}
                    else "SESSION_COMPLETE"
                ),
            )
            _atomic_rearm(rearm_path, terminal_rearm)
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
        "exact_order_id": orders[0]["order_id"] if orders else None,
        "client_order_id": client_id,
        "intent_hash": intent_hash,
        "mutation_context_hash": mutation_context_hash,
        "cancellation_context_hash": cancellation_context_hash,
        "receipt_hash": receipt_hash,
        "broker_order": broker_order,
        "broker_lookup_performed": lookup,
        "broker_submission_performed": submitted,
        "broker_observation_hashes": broker_observation_hashes,
        "cancellation_evidence_hash": cancellation_evidence_hash,
        "order_lifecycle_status": broker_status if orders else "no_trade",
        "filled_quantity": filled_quantity if orders else 0.0,
        "cancel_performed": cancel_performed if orders else False,
        "order_status_observations": observations if orders else [],
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


def seal_generic_live_v1_order_lifecycle(
    *, submission_result: Mapping[str, Any], observed_at: str,
    broker_order_evidence_hash: str | None,
    broker_fill_evidence_hashes: list[str],
) -> dict[str, Any]:
    """Seal exact terminal broker evidence already captured by submission/recovery."""

    submission_result = _validate_submission_result(submission_result)
    observed_raw, _ = _timestamp(observed_at)
    if not isinstance(broker_fill_evidence_hashes, list) or any(
        not isinstance(value, str) or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
        for value in broker_fill_evidence_hashes
    ) or len(set(broker_fill_evidence_hashes)) != len(broker_fill_evidence_hashes):
        raise GenericLiveV1SubmissionError("order lifecycle fill evidence hashes are invalid")
    submission_status = submission_result.get("status")
    broker = submission_result.get("broker_order")
    if submission_status == "NO_TRADE_REARMED":
        lifecycle_status = "NO_TRADE"
        broker_order_id = symbol = side = None
        planned_quantity = filled_quantity = 0.0
    elif submission_status in {
        "FILLED_REARMED", "RECOVERED_FILLED_REARMED", "ORDER_BREAK_REARMED",
        "UNRESOLVED_ORDER_REARMED",
    } and isinstance(broker, Mapping):
        broker_status = str(broker.get("broker_status") or "").lower()
        filled_quantity = float(submission_result.get("filled_quantity") or 0.0)
        planned_quantity = float(broker.get("quantity") or 0.0)
        if submission_status == "UNRESOLVED_ORDER_REARMED":
            lifecycle_status = "UNRESOLVED"
        elif submission_status in {"FILLED_REARMED", "RECOVERED_FILLED_REARMED"}:
            lifecycle_status = "FILLED"
        elif broker_status in {"rejected"}:
            lifecycle_status = "REJECTED"
        elif broker_status in {"expired"}:
            lifecycle_status = "EXPIRED"
        elif filled_quantity > 0.0:
            lifecycle_status = "PARTIAL_CANCELED"
        else:
            lifecycle_status = "CANCELED"
        broker_order_id = str(broker.get("broker_order_id") or "")
        symbol = str(broker.get("symbol") or "").upper()
        side = str(broker.get("side") or "").upper()
    else:
        raise GenericLiveV1SubmissionError("dry-run/nonterminal submission cannot become posttrade evidence")
    if (filled_quantity > 0.0) is not bool(broker_fill_evidence_hashes):
        raise GenericLiveV1SubmissionError("fill quantity and broker fill evidence differ")
    if lifecycle_status == "NO_TRADE":
        if broker_order_evidence_hash is not None:
            raise GenericLiveV1SubmissionError("no-trade lifecycle cannot carry broker order evidence")
    elif (
        not isinstance(broker_order_evidence_hash, str)
        or len(broker_order_evidence_hash) != 64
        or any(character not in "0123456789abcdef" for character in broker_order_evidence_hash)
    ):
        raise GenericLiveV1SubmissionError("broker order evidence hash is invalid")
    body = {
        "schema_version": GENERIC_LIVE_V1_ORDER_LIFECYCLE_SCHEMA,
        "lifecycle_id": "pending",
        "status": lifecycle_status,
        "observed_at": observed_raw,
        "submission_result_hash": submission_result["content_hash"],
        "preflight_hash": submission_result["preflight_hash"],
        "plan_hash": submission_result["plan_hash"],
        "account_id_hash": submission_result["account_id_hash"],
        "lane_id": "generic-live-v1",
        "exact_order_id": submission_result.get("exact_order_id"),
        "client_order_id": submission_result.get("client_order_id"),
        "broker_order_id": broker_order_id,
        "broker_order_evidence_hash": broker_order_evidence_hash,
        "receipt_hash": submission_result.get("receipt_hash"),
        "mutation_context_hash": submission_result.get("mutation_context_hash"),
        "cancellation_context_hash": submission_result.get("cancellation_context_hash"),
        "symbol": symbol,
        "side": side,
        "planned_quantity": planned_quantity,
        "filled_quantity": filled_quantity,
        "broker_fill_evidence_hashes": sorted(broker_fill_evidence_hashes),
        "cancel_performed": bool(submission_result.get("cancel_performed", False)),
    }
    seed = _hash(body)
    body["lifecycle_id"] = f"generic-live-v1-order:{submission_result['effective_session']}:{seed[:24]}"
    body["content_hash"] = _hash(body)
    return _validate_order_lifecycle(body, submission_result=submission_result)
def execute_generic_live_v1_session(
    *,
    activation_preflight: Mapping[str, Any],
    exact_plan: Mapping[str, Any],
    lyra_decision: Mapping[str, Any] | None = None,
    executed_at: str,
    submit_enabled: bool = False,
    broker: GenericLiveV1Broker | None = None,
    wal_directory: Path | str | None = None,
    rearm_state_path: Path | str | None = None,
    result_path: Path | str | None = None,
    poll_attempts: int = 3,
    poll_interval_seconds: float = 1.0,
) -> dict[str, Any]:
    """Run the session and guarantee an emergency rearm on submit failures."""

    try:
        return _execute_generic_live_v1_session(
            activation_preflight=activation_preflight,
            exact_plan=exact_plan,
            lyra_decision=lyra_decision,
            executed_at=executed_at,
            submit_enabled=submit_enabled,
            broker=broker,
            wal_directory=wal_directory,
            rearm_state_path=rearm_state_path,
            result_path=result_path,
            poll_attempts=poll_attempts,
            poll_interval_seconds=poll_interval_seconds,
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
        "schema_version", "lifecycle_id", "status", "observed_at",
        "submission_result_hash", "preflight_hash", "plan_hash",
        "account_id_hash", "lane_id", "exact_order_id", "client_order_id",
        "broker_order_id", "receipt_hash", "mutation_context_hash",
        "cancellation_context_hash", "symbol",
        "broker_order_evidence_hash",
        "side", "planned_quantity", "filled_quantity",
        "broker_fill_evidence_hashes", "cancel_performed", "content_hash",
    }
    if not isinstance(payload, Mapping) or set(payload) != fields:
        raise GenericLiveV1SubmissionError("order lifecycle artifact fields are invalid")
    if payload.get("schema_version") != GENERIC_LIVE_V1_ORDER_LIFECYCLE_SCHEMA:
        raise GenericLiveV1SubmissionError("order lifecycle schema differs")
    _timestamp(str(payload["observed_at"]))
    if payload.get("content_hash") != _hash(payload):
        raise GenericLiveV1SubmissionError("order lifecycle content_hash mismatch")
    identity_body = copy.deepcopy(dict(payload))
    identity_body.pop("content_hash", None)
    identity_body["lifecycle_id"] = "pending"
    expected_id = (
        f"generic-live-v1-order:{submission_result['effective_session']}:"
        f"{_hash(identity_body)[:24]}"
    )
    if payload.get("lifecycle_id") != expected_id:
        raise GenericLiveV1SubmissionError("order lifecycle identity differs")
    if (
        payload.get("submission_result_hash") != submission_result["content_hash"]
        or payload.get("preflight_hash") != submission_result["preflight_hash"]
        or payload.get("plan_hash") != submission_result["plan_hash"]
    ):
        raise GenericLiveV1SubmissionError("order lifecycle submission/plan lineage differs")
    if payload.get("account_id_hash") != submission_result["account_id_hash"] or payload.get("lane_id") != "generic-live-v1":
        raise GenericLiveV1SubmissionError("order lifecycle account/lane scope differs")
    if payload.get("status") not in {
        "FILLED", "PARTIAL_CANCELED", "REJECTED", "CANCELED", "EXPIRED",
        "UNRESOLVED", "NO_TRADE",
    }:
        raise GenericLiveV1SubmissionError("order lifecycle is not a known terminal state")
    hashes = payload.get("broker_fill_evidence_hashes")
    if not isinstance(hashes, list) or hashes != sorted(set(hashes)) or any(
        not isinstance(value, str) or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
        for value in hashes
    ):
        raise GenericLiveV1SubmissionError("order lifecycle fill hashes are invalid")
    if payload["status"] == "FILLED":
        if (
            submission_result.get("status") not in {"FILLED_REARMED", "RECOVERED_FILLED_REARMED"}
            or not payload.get("broker_order_id")
            or float(payload.get("filled_quantity") or 0.0) <= 0.0
            or float(payload["filled_quantity"]) != float(payload["planned_quantity"])
        ):
            raise GenericLiveV1SubmissionError("filled order lifecycle evidence is incomplete")
    elif payload["status"] == "NO_TRADE":
        if submission_result.get("status") != "NO_TRADE_REARMED" or any(
            payload.get(field) is not None
            for field in (
                "exact_order_id", "client_order_id", "broker_order_id",
                "broker_order_evidence_hash", "receipt_hash",
                "mutation_context_hash", "cancellation_context_hash", "symbol", "side",
            )
        ) or float(payload.get("filled_quantity") or 0.0) != 0.0:
            raise GenericLiveV1SubmissionError("NO_TRADE lifecycle cannot carry an order/fill")
    elif payload["status"] == "UNRESOLVED":
        if submission_result.get("status") != "UNRESOLVED_ORDER_REARMED":
            raise GenericLiveV1SubmissionError("unresolved lifecycle differs from submission result")
    elif submission_result.get("status") != "ORDER_BREAK_REARMED":
        raise GenericLiveV1SubmissionError("order-break lifecycle differs from submission result")
    if (float(payload.get("filled_quantity") or 0.0) > 0.0) is not bool(hashes):
        raise GenericLiveV1SubmissionError("order lifecycle quantity/fill lineage differs")
    broker = submission_result.get("broker_order")
    if payload["status"] != "NO_TRADE":
        _validate_order_status_observations(
            submission_result.get("order_status_observations")
        )
        expected = {
            "exact_order_id": submission_result.get("exact_order_id"),
            "client_order_id": submission_result.get("client_order_id"),
            "broker_order_id": broker.get("broker_order_id") if isinstance(broker, Mapping) else None,
            "receipt_hash": submission_result.get("receipt_hash"),
            "mutation_context_hash": submission_result.get("mutation_context_hash"),
            "cancellation_context_hash": submission_result.get("cancellation_context_hash"),
            "symbol": broker.get("symbol") if isinstance(broker, Mapping) else None,
            "side": broker.get("side") if isinstance(broker, Mapping) else None,
            "planned_quantity": float(broker.get("quantity") or 0.0) if isinstance(broker, Mapping) else 0.0,
            "filled_quantity": float(submission_result.get("filled_quantity") or 0.0),
            "cancel_performed": bool(submission_result.get("cancel_performed", False)),
        }
        if any(payload.get(field) != value for field, value in expected.items()):
            raise GenericLiveV1SubmissionError("order lifecycle exact submission lineage differs")
        cancellation_hash = payload.get("cancellation_context_hash")
        if cancellation_hash is not None and (
            not isinstance(cancellation_hash, str)
            or len(cancellation_hash) != 64
            or any(character not in "0123456789abcdef" for character in cancellation_hash)
        ):
            raise GenericLiveV1SubmissionError("order lifecycle cancellation context hash is invalid")
        if payload["cancel_performed"] is True and cancellation_hash is None:
            raise GenericLiveV1SubmissionError("performed cancellation lacks signed context lineage")
        broker_status = str(broker.get("broker_status") or "").lower()
        allowed_by_status = {
            "FILLED": {"filled"},
            "PARTIAL_CANCELED": {"canceled", "cancelled", "partially_filled"},
            "REJECTED": {"rejected"},
            "CANCELED": {"canceled", "cancelled"},
            "EXPIRED": {"expired"},
            "UNRESOLVED": {
                "accepted", "new", "pending_new", "accepted_for_bidding", "held",
                "pending_cancel", "partially_filled", "calculated", "stopped",
                "suspended", "unknown", "",
            },
        }
        if broker_status not in allowed_by_status[payload["status"]]:
            raise GenericLiveV1SubmissionError("order lifecycle status differs from broker receipt")
        order_evidence_hash = payload.get("broker_order_evidence_hash")
        if not isinstance(order_evidence_hash, str) or len(order_evidence_hash) != 64 or any(
            character not in "0123456789abcdef" for character in order_evidence_hash
        ):
            raise GenericLiveV1SubmissionError("order lifecycle broker order evidence hash is invalid")
        filled = float(payload["filled_quantity"])
        planned = float(payload["planned_quantity"])
        if payload["status"] == "PARTIAL_CANCELED" and not (0.0 < filled < planned):
            raise GenericLiveV1SubmissionError("partial lifecycle quantity is not partial")
        if payload["status"] in {"REJECTED", "CANCELED", "EXPIRED"} and filled != 0.0:
            raise GenericLiveV1SubmissionError("zero-fill terminal lifecycle carries a fill")
    return copy.deepcopy(dict(payload))


def validate_generic_live_v1_order_lifecycle(
    payload: Mapping[str, Any], *, submission_result: Mapping[str, Any],
) -> dict[str, Any]:
    """Public strict validator for submission-bound order lifecycle evidence."""

    checked_submission = _validate_submission_result(submission_result)
    return _validate_order_lifecycle(payload, submission_result=checked_submission)


def validate_generic_live_v1_order_reconciliation_causality(
    *, submission_result: Mapping[str, Any], exact_plan: Mapping[str, Any],
    order_lifecycle: Mapping[str, Any], reconciliation: Mapping[str, Any],
) -> dict[str, Any]:
    """Prove the terminal order outcome against exact broker reconciliation."""

    submission = _validate_submission_result(submission_result)
    if (
        exact_plan.get("content_hash") != submission.get("plan_hash")
        or validate_lane_exact_execution_plan(exact_plan)
    ):
        raise GenericLiveV1SubmissionError(
            "posttrade exact plan lineage is invalid"
        )
    order = _validate_order_lifecycle(
        order_lifecycle, submission_result=submission,
    )
    reconciled = validate_lane_reconciliation(reconciliation, exact_plan=exact_plan)
    if (
        reconciled["account_id_hash"] != submission["account_id_hash"]
        or reconciled["lane_id"] != "generic-live-v1"
        or reconciled["plan_hash"] != submission["plan_hash"]
    ):
        raise GenericLiveV1SubmissionError(
            "reconciliation scope differs from submission"
        )
    if order["status"] == "FILLED" and (
        reconciled["status"] != "PASS" or reconciled["accounting_ready"] is not True
    ):
        raise GenericLiveV1SubmissionError(
            "filled order is not PASS/accounting-ready"
        )
    if order["status"] == "PARTIAL_CANCELED" and (
        reconciled["status"] != "PARTIAL"
        or reconciled["accounting_ready"] is not True
    ):
        raise GenericLiveV1SubmissionError(
            "partial order is not PARTIAL/accounting-ready"
        )
    if order["status"] in {"REJECTED", "CANCELED", "EXPIRED"} and (
        reconciled["status"] not in {"REJECTED", "PARTIAL"}
        or reconciled["accounting_ready"] is not False
        or reconciled["reconciled_fills"]
    ):
        raise GenericLiveV1SubmissionError(
            "zero-fill order break is not exactly reconciled"
        )
    if order["status"] == "UNRESOLVED" and (
        reconciled["status"] != "UNRESOLVED"
        or reconciled["accounting_ready"] is not False
    ):
        raise GenericLiveV1SubmissionError(
            "unresolved order is not fail-closed reconciled"
        )
    if order["status"] == "NO_TRADE" and (
        reconciled["status"] != "PASS" or reconciled["reconciled_fills"]
    ):
        raise GenericLiveV1SubmissionError(
            "no-trade session is not exactly reconciled"
        )
    summaries = reconciled["broker_orders"]
    if order["status"] == "NO_TRADE":
        if summaries or reconciled["broker_fills"]:
            raise GenericLiveV1SubmissionError(
                "no-trade reconciliation carries broker activity"
            )
    else:
        if len(summaries) != 1:
            raise GenericLiveV1SubmissionError(
                "reconciliation does not contain exact broker order"
            )
        summary = summaries[0]
        if (
            summary["order_id"] != order["exact_order_id"]
            or summary["broker_order_id"] != order["broker_order_id"]
            or abs(
                float(summary["filled_quantity"])
                - float(order["filled_quantity"])
            ) > 1e-9
            or sorted(reconciled["source_hashes"]["broker_fills"])
            != order["broker_fill_evidence_hashes"]
            or reconciled["source_hashes"]["broker_orders"]
            != [order["broker_order_evidence_hash"]]
            or reconciled["source_hashes"]["wal_intents"]
            != [submission["intent_hash"]]
        ):
            raise GenericLiveV1SubmissionError(
                "reconciliation order/fill causality differs"
            )
    return reconciled


def finalize_generic_live_v1_posttrade(
    *, submission_result: Mapping[str, Any], exact_plan: Mapping[str, Any],
    order_lifecycle: Mapping[str, Any], reconciliation: Mapping[str, Any],
    journal_entries: list[Mapping[str, Any]], performance: Mapping[str, Any] | None,
    dashboard_projection: Mapping[str, Any], finalized_at: str,
    rearm_state_path: Path | str, result_path: Path | str,
    rollback_handler: Callable[[str], Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate factual typed artifacts; arbitrary booleans/hashes are rejected."""

    submission_result = _validate_submission_result(submission_result)
    if submission_result.get("status") not in {
        "FILLED_REARMED", "RECOVERED_FILLED_REARMED", "ORDER_BREAK_REARMED",
        "UNRESOLVED_ORDER_REARMED", "NO_TRADE_REARMED",
    }:
        raise GenericLiveV1SubmissionError("dry-run/nonterminal result cannot enter posttrade")
    if exact_plan.get("content_hash") != submission_result.get("plan_hash") or validate_lane_exact_execution_plan(exact_plan):
        raise GenericLiveV1SubmissionError("posttrade exact plan lineage is invalid")
    finalized_raw, _ = _timestamp(finalized_at)

    def rollback_break(trigger: str) -> Mapping[str, Any]:
        evidence = rollback_handler(trigger)
        if (
            not isinstance(evidence, Mapping)
            or evidence.get("status") != "ROLLED_BACK_ARMED"
            or evidence.get("trigger") != trigger
            or evidence.get("paper_bytes_unchanged") is not True
            or evidence.get("cron_exact_line_removed") is not True
            or not evidence.get("rearm_hash")
            or evidence.get("config_action") not in {
                "RESTORED_BACKUP", "REMOVED_NO_PRIOR_CONFIG", "ALREADY_ABSENT"
            }
        ):
            raise GenericLiveV1SubmissionError(
                f"{trigger} rollback evidence is incomplete"
            )
        return evidence

    try:
        order = _validate_order_lifecycle(order_lifecycle, submission_result=submission_result)
    except Exception:
        try:
            rollback_break("ORDER_BREAK")
        finally:
            raise
    order_green = order["status"] in {"FILLED", "NO_TRADE"}
    try:
        reconciled = validate_lane_reconciliation(reconciliation, exact_plan=exact_plan)
        if (
            reconciled["account_id_hash"] != submission_result["account_id_hash"]
            or reconciled["lane_id"] != "generic-live-v1"
            or reconciled["plan_hash"] != submission_result["plan_hash"]
        ):
            raise GenericLiveV1SubmissionError("reconciliation scope differs from submission")
        if order["status"] == "FILLED" and (
            reconciled["status"] != "PASS" or reconciled["accounting_ready"] is not True
        ):
            raise GenericLiveV1SubmissionError("filled order is not PASS/accounting-ready")
        if order["status"] == "PARTIAL_CANCELED" and (
            reconciled["status"] != "PARTIAL" or reconciled["accounting_ready"] is not True
        ):
            raise GenericLiveV1SubmissionError("partial order is not PARTIAL/accounting-ready")
        if order["status"] in {"REJECTED", "CANCELED", "EXPIRED"} and (
            reconciled["status"] not in {"REJECTED", "PARTIAL"}
            or reconciled["accounting_ready"] is not False
            or reconciled["reconciled_fills"]
        ):
            raise GenericLiveV1SubmissionError("zero-fill order break is not exactly reconciled")
        if order["status"] == "UNRESOLVED" and (
            reconciled["status"] != "UNRESOLVED"
            or reconciled["accounting_ready"] is not False
        ):
            raise GenericLiveV1SubmissionError("unresolved order is not fail-closed reconciled")
        if order["status"] == "NO_TRADE" and (
            reconciled["status"] != "PASS" or reconciled["reconciled_fills"]
        ):
            raise GenericLiveV1SubmissionError("no-trade session is not exactly reconciled")
        summaries = reconciled["broker_orders"]
        if order["status"] == "NO_TRADE":
            if summaries or reconciled["broker_fills"]:
                raise GenericLiveV1SubmissionError("no-trade reconciliation carries broker activity")
        else:
            if len(summaries) != 1:
                raise GenericLiveV1SubmissionError("reconciliation does not contain exact broker order")
            summary = summaries[0]
            if (
                summary["order_id"] != order["exact_order_id"]
                or summary["broker_order_id"] != order["broker_order_id"]
                or abs(float(summary["filled_quantity"]) - float(order["filled_quantity"])) > 1e-9
                or sorted(reconciled["source_hashes"]["broker_fills"])
                != order["broker_fill_evidence_hashes"]
                or reconciled["source_hashes"]["broker_orders"]
                != [order["broker_order_evidence_hash"]]
                or reconciled["source_hashes"]["wal_intents"]
                != [submission_result["intent_hash"]]
            ):
                raise GenericLiveV1SubmissionError("reconciliation order/fill causality differs")
    except Exception:
        rollback_break("RECONCILIATION_BREAK")
        raise
    try:
        journal = validate_accounting_journal(journal_entries)
        if reconciled["reconciled_fills"] and not journal:
            raise GenericLiveV1SubmissionError("filled reconciliation has no accounting journal")
        session_journal = [
            row for row in journal if row["source_hash"] == reconciled["content_hash"]
        ]
        if reconciled["reconciled_fills"] and not session_journal:
            raise GenericLiveV1SubmissionError("filled reconciliation has no session journal entries")
        if not reconciled["reconciled_fills"] and session_journal:
            raise GenericLiveV1SubmissionError("zero-fill reconciliation created session economics")
        if any(
            row["account_id_hash"] != submission_result["account_id_hash"]
            or row["lane_id"] != "generic-live-v1"
            for row in journal
        ):
            raise GenericLiveV1SubmissionError("accounting journal lineage differs")
        reconciled_fill_ids = {row["fill_id"] for row in reconciled["reconciled_fills"]}
        if {row["fill_id"] for row in session_journal} != reconciled_fill_ids:
            raise GenericLiveV1SubmissionError("session journal fill coverage differs")
    except Exception:
        rollback_break("ACCOUNTING_BREAK")
        raise
    try:
        dashboard = validate_dashboard_performance_surfaces(dashboard_projection)
        live_surfaces = [
            row for row in dashboard["performance_surfaces"]
            if row["lane_id"] == "generic-live-v1"
        ]
        if not live_surfaces:
            raise GenericLiveV1SubmissionError("dashboard omits generic Live surface")
        if order_green:
            if performance is None:
                raise GenericLiveV1SubmissionError("green order lacks factual performance")
            perf = validate_lane_performance(performance)
            if (
                perf["lane_id"] != "generic-live-v1"
                or perf["account_id_hash"] != submission_result["account_id_hash"]
                or perf["lane_kind"] != "LIVE"
                or any(row["claim_status"] != "AVAILABLE" for row in live_surfaces)
            ):
                raise GenericLiveV1SubmissionError("performance/dashboard scope differs")
        else:
            perf = None
            if performance is not None or any(
                row["claim_status"] != "SUPPRESSED" or not row["blocker_codes"]
                for row in live_surfaces
            ):
                raise GenericLiveV1SubmissionError(
                    "order break must publish only blocked/suppressed truth"
                )
    except Exception:
        rollback_break("REPORTING_BREAK")
        raise
    final_trigger = "SESSION_COMPLETE" if order_green else "ORDER_BREAK"
    if order_green:
        rearm = rearm_generic_live_v1_session(
            state_path=rearm_state_path, preflight_hash=submission_result["preflight_hash"],
            plan_hash=submission_result["plan_hash"], rearmed_at=finalized_raw,
            trigger=final_trigger,
        )
    else:
        rollback = rollback_break("ORDER_BREAK")
        rearm = {"content_hash": rollback["rearm_hash"]}
    evidence_hashes = sorted([
        order["content_hash"], reconciled["content_hash"],
        *[row["record_hash"] for row in journal],
        *([] if perf is None else [perf["content_hash"]]), dashboard["content_hash"],
    ])
    body = {
        "schema_version": GENERIC_LIVE_V1_POSTTRADE_RESULT_SCHEMA,
        "finalized_at": finalized_raw,
        "status": "GREEN_REARMED" if order_green else "ROLLBACK_REQUIRED_REARMED",
        "reason_codes": [
            "ALL_TYPED_POSTTRADE_ARTIFACTS_GREEN" if order_green
            else "TERMINAL_ORDER_BREAK_RECONCILED_ACCOUNTED_AND_REPORTED"
        ],
        "submission_result_hash": submission_result["content_hash"],
        "preflight_hash": submission_result["preflight_hash"],
        "plan_hash": submission_result["plan_hash"],
        "evidence_hashes": evidence_hashes, "rearm_hash": rearm["content_hash"],
        "generic_kill_switch_state": "ARMED", "legacy_executor_enabled": False,
        "paper_cutover_enabled": False, "broker_submission_allowed": False,
        "rollback_required": not order_green,
    }
    body["content_hash"] = _hash(body)
    _write_exclusive(Path(result_path), body)
    return body


__all__ = [
    "GENERIC_LIVE_V1_ORDER_LIFECYCLE_SCHEMA",
    "GENERIC_LIVE_V1_SUBMISSION_RESULT_SCHEMA", "GenericLiveV1SubmissionError",
    "execute_generic_live_v1_session", "finalize_generic_live_v1_posttrade",
    "ensure_generic_live_v1_rearmed_after_failure", "rearm_generic_live_v1_session",
    "seal_generic_live_v1_order_lifecycle",
    "validate_generic_live_v1_order_lifecycle",
    "validate_generic_live_v1_order_reconciliation_causality",
    "validate_generic_live_v1_submission_result",
]

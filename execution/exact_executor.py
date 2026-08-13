"""Dumb, restart-safe executor for ``caerus.execution_plan.v3``.

This module contains no portfolio construction, target interpretation, sizing,
rebudgeting, or artifact fallback.  It validates one authorized exact plan,
writes each immutable order intent before broker mutation, and submits exactly
the sealed sell rows followed by the sealed buy rows.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import math
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

from authority.contracts import AuthorityContractError
from authority.exact_plan import (
    ExactExecutionPlan,
    compute_starting_state_hash,
    exact_execution_plan_from_dict,
)
from core.failure_semantics import FailureClass, TerminalOutcome
from core.live_pilot_guardrails import (
    resolve_dynamic_cap,
    validate_live_pilot_asset,
    validate_live_pilot_submission_guardrails,
)
from core.paper_drill_epoch import claim_namespace, plan_drill_epoch, scoped_wal_root
from core.submission_wal import (
    OrderIntent,
    ResolutionState,
    WalPersistenceError,
    append_resolution,
    intent_path,
    new_resolution,
    prepare_order_intent,
    unresolved_intent_requires_lookup,
)


_PLAN_CLAIM_SCHEMA_VERSION = "caerus.exact_execution_plan_claim.v1"
_ACCOUNT_AUTHORITY_ROOT_ENV = "CAERUS_EXACT_ACCOUNT_AUTHORITY_ROOT"
_DEFAULT_ACCOUNT_AUTHORITY_ROOT = (
    Path(__file__).resolve().parents[1]
    / "outputs"
    / "paper_lane"
    / "exact_account_authority"
)


class PlanClaimError(RuntimeError):
    """The immutable date/account execution claim is missing or conflicts."""


class ExecutionMutexError(RuntimeError):
    """The date-wide OS execution mutex could not be acquired or released."""


class ExactOrderSuppressionReason(str, Enum):
    """Typed reason an immutable requested order was not broker-submitted."""

    PRE_SUBMIT_VALIDATION_BLOCKED = "PRE_SUBMIT_VALIDATION_BLOCKED"
    DRY_RUN_VALIDATION_ONLY = "DRY_RUN_VALIDATION_ONLY"
    PRIOR_ORDER_UNRESOLVED = "PRIOR_ORDER_UNRESOLVED"
    PRIOR_ORDER_REJECTED = "PRIOR_ORDER_REJECTED"
    PRIOR_SUBMISSION_UNKNOWN = "PRIOR_SUBMISSION_UNKNOWN"
    PRIOR_RECONCILIATION_FAILURE = "PRIOR_RECONCILIATION_FAILURE"
    EXECUTION_HALTED = "EXECUTION_HALTED"


def _canonical_json(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _claim_hash(payload: Mapping[str, Any]) -> str:
    unhashed = dict(payload)
    unhashed.pop("content_hash", None)
    return hashlib.sha256(_canonical_json(unhashed)).hexdigest()


def _broker_account_id_hash(account: Mapping[str, Any]) -> str | None:
    account_id = str(
        account.get("id") or account.get("account_id") or ""
    ).strip()
    if not account_id:
        return None
    return hashlib.sha256(account_id.encode("utf-8")).hexdigest()


def _account_authority_root(env: Mapping[str, str]) -> Path:
    configured = str(env.get(_ACCOUNT_AUTHORITY_ROOT_ENV) or "").strip()
    if not configured:
        return _DEFAULT_ACCOUNT_AUTHORITY_ROOT
    root = Path(configured).expanduser()
    return root if root.is_absolute() else Path(__file__).resolve().parents[1] / root


def _account_date_root(
    authority_root: Path | str,
    *,
    trade_date: str,
    account_id_hash: str,
) -> Path:
    return Path(authority_root) / account_id_hash / trade_date


@contextmanager
def _date_execution_lock(
    authority_root: Path | str,
    trade_date: str,
    account_id_hash: str,
):
    """Serialize one PAPER account/date independent of caller WAL location."""

    handle = None
    try:
        date_root = _account_date_root(
            authority_root,
            trade_date=trade_date,
            account_id_hash=account_id_hash,
        )
        date_root.mkdir(parents=True, exist_ok=True)
        lock_path = date_root / ".exact_execution.lock"
        handle = lock_path.open("a+", encoding="utf-8")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    except OSError as exc:
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass
        raise ExecutionMutexError(f"exact_execution_mutex_failed:{exc}") from exc
    try:
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
        except OSError as exc:
            raise ExecutionMutexError(f"exact_execution_mutex_failed:{exc}") from exc


def _ensure_plan_claim(
    authority_root: Path | str,
    *,
    plan: ExactExecutionPlan,
    account: Mapping[str, Any],
    wal_root: Path | str,
    create: bool = True,
) -> Path | None:
    """Persist one immutable plan identity per trade-date/PAPER account.

    The caller holds ``_date_execution_lock``.  The exclusive create and fsync
    make a crash after this point recoverable: a restart may resume the same
    plan, while a different plan can never obtain submission authority.
    """

    current_account_id_hash = _broker_account_id_hash(account)
    if not current_account_id_hash:
        raise PlanClaimError("plan_claim_account_identity_unavailable")
    if current_account_id_hash != plan.account_id_hash:
        raise PlanClaimError("exact_plan_account_identity_mismatch")
    account_scope = "PAPER"
    epoch = plan_drill_epoch(plan)
    claim_path = (
        _account_date_root(
            authority_root,
            trade_date=plan.trade_date,
            account_id_hash=plan.account_id_hash,
        )
        / claim_namespace(epoch)
        / "plan_claim.json"
    )
    canonical_wal_root = str(Path(wal_root).expanduser().resolve())
    identity = {
        "schema_version": _PLAN_CLAIM_SCHEMA_VERSION,
        "trade_date": plan.trade_date,
        "account_scope": account_scope,
        "account_id_hash": plan.account_id_hash,
        "plan_id": plan.plan_id,
        "plan_hash": plan.content_hash,
        "submission_wal_root": canonical_wal_root,
    }
    if claim_path.exists():
        try:
            existing = json.loads(claim_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise PlanClaimError(f"plan_claim_integrity_failed:{exc}") from exc
        if not isinstance(existing, Mapping):
            raise PlanClaimError("plan_claim_integrity_failed:not_an_object")
        if existing.get("content_hash") != _claim_hash(existing):
            raise PlanClaimError("plan_claim_integrity_failed:content_hash_mismatch")
        if any(existing.get(key) != value for key, value in identity.items()):
            raise PlanClaimError("plan_claim_conflicts_with_authorized_plan")
        return claim_path

    if not create:
        return None

    payload = {
        **identity,
        "claimed_at": _now(),
    }
    payload["content_hash"] = _claim_hash(payload)
    try:
        claim_path.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
        with claim_path.open("x", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        # Defensive even though the date-wide OS lock should make this
        # unreachable for cooperating executor processes.
        return _ensure_plan_claim(
            authority_root,
            plan=plan,
            account=account,
            wal_root=wal_root,
            create=create,
        )
    except OSError as exc:
        raise PlanClaimError(f"plan_claim_persistence_failed:{exc}") from exc
    try:
        directory_fd = os.open(str(claim_path.parent), os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        raise PlanClaimError(f"plan_claim_persistence_failed:{exc}") from exc
    return claim_path


@dataclass(frozen=True)
class ExactExecutionOutcome:
    plan_id_received: str
    plan_hash_received: str
    plan_hash_validated: bool
    authorization_validated: bool
    terminal_outcome: TerminalOutcome
    status: str
    reason_code: str
    orders_requested: tuple[Mapping[str, Any], ...]
    orders_submitted: tuple[Mapping[str, Any], ...]
    orders_filled: tuple[Mapping[str, Any], ...]
    orders_rejected: tuple[Mapping[str, Any], ...]
    orders_suppressed: tuple[Mapping[str, Any], ...]
    final_positions: tuple[Mapping[str, Any], ...]
    final_cash: float | None
    reconciliation_status: str
    failure_class: FailureClass | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "caerus.exact_execution_result.v1",
            "plan_id_received": self.plan_id_received,
            "plan_hash_received": self.plan_hash_received,
            "plan_hash_validated": self.plan_hash_validated,
            "authorization_validated": self.authorization_validated,
            "terminal_outcome": self.terminal_outcome.value,
            "status": self.status,
            "reason_code": self.reason_code,
            "orders_requested": [dict(row) for row in self.orders_requested],
            "orders_requested_count": len(self.orders_requested),
            "orders_submitted": [dict(row) for row in self.orders_submitted],
            "orders_submitted_count": len(self.orders_submitted),
            "orders_filled": [dict(row) for row in self.orders_filled],
            "orders_filled_count": len(self.orders_filled),
            "orders_rejected": [dict(row) for row in self.orders_rejected],
            "orders_rejected_count": len(self.orders_rejected),
            "orders_suppressed": [dict(row) for row in self.orders_suppressed],
            "orders_suppressed_count": len(self.orders_suppressed),
            "final_positions": [dict(row) for row in self.final_positions],
            "final_cash": self.final_cash,
            "reconciliation_status": self.reconciliation_status,
            "failure_class": self.failure_class.value if self.failure_class else None,
        }


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _positions(raw: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(raw, list):
        raise RuntimeError("broker positions response is not a list")
    rows: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise RuntimeError("broker position row is malformed")
        symbol = str(item.get("symbol") or item.get("ticker") or "").strip().upper()
        quantity = _finite(item.get("quantity", item.get("qty", item.get("shares"))))
        if not symbol or quantity is None or quantity < 0:
            raise RuntimeError("broker position row lacks valid symbol/quantity")
        if quantity > 1e-12:
            rows.append({"symbol": symbol, "quantity": quantity})
    return tuple(sorted(rows, key=lambda row: str(row["symbol"])))


def _snapshot(broker: Any) -> tuple[tuple[dict[str, Any], ...], float, Mapping[str, Any]]:
    account = broker.get_account()
    if not isinstance(account, Mapping):
        raise RuntimeError("broker account response is not an object")
    cash = _finite(account.get("cash"))
    if cash is None or cash < 0:
        raise RuntimeError("broker cash is unavailable")
    return _positions(broker.get_positions()), cash, dict(account)


def _best_effort_snapshot(broker: Any) -> tuple[tuple[dict[str, Any], ...], float | None]:
    """Preserve an outcome after mutation even when broker reads are degraded."""
    try:
        positions, cash, _account = _snapshot(broker)
        return positions, cash
    except Exception:
        return (), None


def _status(row: Mapping[str, Any]) -> str:
    # alpaca-py serializes enum values as ``OrderStatus.FILLED`` in the
    # adapter's mapping.  Treat the enum-qualified and plain wire forms as the
    # same terminal state.
    text = str(row.get("status") or "").strip().lower()
    return text.rsplit(".", 1)[-1]


def _is_filled(row: Mapping[str, Any]) -> bool:
    return _status(row) == "filled"


def _is_rejected(row: Mapping[str, Any]) -> bool:
    return _status(row) in {"rejected", "canceled", "cancelled", "expired", "failed"}


def _filled_quantity(row: Mapping[str, Any]) -> float:
    quantity = _finite(row.get("filled_qty") or row.get("filled_quantity"))
    if quantity is not None and quantity > 0:
        return quantity
    if _is_filled(row):
        return float(_finite(row.get("quantity") or row.get("qty")) or 0.0)
    return 0.0


def _has_fill(row: Mapping[str, Any]) -> bool:
    """True for both complete and economically material partial fills."""

    return _filled_quantity(row) > 0


def _notional(order: Mapping[str, Any]) -> float | None:
    direct = _finite(order.get("notional"))
    if direct is not None and direct > 0:
        return direct
    quantity = _finite(order.get("quantity"))
    price = _finite(
        order.get("limit_price")
        or order.get("expected_price")
        or order.get("price")
    )
    if quantity is None or price is None or quantity <= 0 or price <= 0:
        return None
    return quantity * price


def _broker_lookup(broker: Any, client_order_id: str) -> Mapping[str, Any] | None:
    lookup = getattr(broker, "find_order_by_client_id", None)
    if not callable(lookup):
        raise RuntimeError("broker lacks stable client-order-id lookup")
    result = lookup(client_order_id)
    if result is None:
        return None
    if not isinstance(result, Mapping):
        raise RuntimeError("broker client-order-id lookup returned malformed data")
    return dict(result)


def _append_resolution(
    wal_root: Path | str,
    *,
    intent: OrderIntent,
    state: ResolutionState,
    broker_order_id: str | None = None,
    detail: str = "",
) -> None:
    append_resolution(
        wal_root,
        new_resolution(
            resolution_id=f"resolution:{uuid.uuid4().hex}",
            intent=intent,
            state=state,
            broker_order_id=broker_order_id,
            detail=detail,
        ),
    )


def _submit_one(
    *,
    plan: ExactExecutionPlan,
    order: Mapping[str, Any],
    broker: Any,
    env: Mapping[str, str],
    wal_root: Path | str,
    attempt_id: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Return broker row or an explicit ambiguity reason. Never resubmit WAL rows."""
    order_type = str(order["order_type"]).lower()
    intent = OrderIntent(
        trade_date=plan.trade_date,
        plan_id=plan.plan_id,
        plan_hash=plan.content_hash,
        attempt_id=attempt_id,
        order_id=str(order["order_id"]),
        client_order_id=str(order["client_order_id"]),
        symbol=str(order["symbol"]),
        side=str(order["side"]),
        quantity=float(order["quantity"]),
        order_type=order_type,
        created_at=_now(),
        limit_price=_finite(order.get("limit_price")),
        stop_price=_finite(order.get("stop_price")),
        expected_price=_finite(order.get("expected_price") or order.get("price")),
        notional=_notional(order),
        sleeve=str(order.get("sleeve") or "caerus_orion"),
        time_in_force=str(order["time_in_force"]),
    )
    prepared = prepare_order_intent(wal_root, intent)
    durable = prepared.intent
    if not prepared.broker_submission_allowed:
        try:
            recovered = _broker_lookup(broker, durable.client_order_id)
        except Exception as exc:
            _append_resolution(
                wal_root,
                intent=durable,
                state=ResolutionState.SUBMISSION_UNKNOWN,
                detail=f"restart lookup failed: {exc}",
            )
            return None, f"submission_unknown:{durable.client_order_id}:lookup_failed"
        if recovered is None:
            _append_resolution(
                wal_root,
                intent=durable,
                state=ResolutionState.SUBMISSION_UNKNOWN,
                detail="restart lookup found no broker order; automatic resubmission forbidden",
            )
            return None, f"submission_unknown:{durable.client_order_id}:not_found"
        broker_id = str(recovered.get("id") or recovered.get("order_id") or "").strip()
        if not broker_id:
            return None, f"submission_unknown:{durable.client_order_id}:missing_broker_id"
        _append_resolution(
            wal_root,
            intent=durable,
            state=ResolutionState.RECOVERED_BY_LOOKUP,
            broker_order_id=broker_id,
        )
        # Broker evidence may use enum-qualified strings or alternate quantity
        # keys.  It must never overwrite the immutable exact order identity and
        # economics carried by ``order``.
        return {**dict(recovered), **dict(order), "recovered_by_client_order_id": True}, None

    estimate = _notional(order)
    validate_live_pilot_submission_guardrails(
        broker_paper=bool(getattr(broker, "paper", False)),
        base_url=str(getattr(broker, "base_url", "") or ""),
        env=env,
        order_notional=estimate,
    )
    kwargs = {
        "symbol": str(order["symbol"]),
        "qty": float(order["quantity"]),
        "side": str(order["side"]),
        "client_order_id": str(order["client_order_id"]),
        "tif": str(order["time_in_force"]),
    }
    broker_row: dict[str, Any] | None = None
    try:
        from brokers.alpaca_broker import AlpacaBroker, _EXACT_EXECUTION_CAPABILITY

        if isinstance(broker, AlpacaBroker):
            kwargs["_execution_capability"] = _EXACT_EXECUTION_CAPABILITY
    except ImportError:
        pass
    try:
        if order_type == "market":
            result = broker.submit_market_order(**kwargs, estimated_notional=estimate)
        elif order_type == "limit":
            limit_price = _finite(order.get("limit_price"))
            if limit_price is None or limit_price <= 0:
                raise RuntimeError("exact limit order lacks positive limit_price")
            result = broker.submit_limit_order(
                **kwargs,
                limit_price=limit_price,
                extended_hours=bool(order.get("extended_hours", False)),
            )
        else:
            raise RuntimeError(f"exact executor does not support order_type={order_type}")
        if not isinstance(result, Mapping):
            raise RuntimeError("broker submission returned malformed data")
        broker_row = dict(result)
        broker_id = str(broker_row.get("id") or broker_row.get("order_id") or "").strip()
        if not broker_id:
            raise RuntimeError("broker submission response lacks order id")
        state = ResolutionState.REJECTED if _is_rejected(broker_row) else ResolutionState.SUBMITTED
        _append_resolution(
            wal_root,
            intent=durable,
            state=state,
            broker_order_id=broker_id if state is ResolutionState.SUBMITTED else None,
            detail=str(broker_row.get("status") or ""),
        )
        return {**broker_row, **dict(order)}, None
    except WalPersistenceError:
        # Broker may already have mutated. A missing durable resolution is always
        # ambiguous and forbids any additional order submission.
        evidence = (
            {**broker_row, **dict(order)} if broker_row is not None else None
        )
        return evidence, f"submission_unknown:{durable.client_order_id}:wal_resolution_failed"
    except Exception as exc:
        try:
            recovered = _broker_lookup(broker, durable.client_order_id)
        except Exception as lookup_exc:
            recovered = None
            detail = f"submit error={exc}; lookup error={lookup_exc}"
        else:
            detail = f"submit error={exc}; lookup did not find order"
        if recovered is not None:
            broker_id = str(recovered.get("id") or recovered.get("order_id") or "").strip()
            if broker_id:
                try:
                    _append_resolution(
                        wal_root,
                        intent=durable,
                        state=ResolutionState.RECOVERED_BY_LOOKUP,
                        broker_order_id=broker_id,
                        detail=f"recovered after submit exception: {exc}",
                    )
                except WalPersistenceError:
                    return (
                        {**dict(recovered), **dict(order), "recovered_by_client_order_id": True},
                        f"submission_unknown:{durable.client_order_id}:wal_resolution_failed",
                    )
                return {**dict(recovered), **dict(order), "recovered_by_client_order_id": True}, None
        try:
            _append_resolution(
                wal_root,
                intent=durable,
                state=ResolutionState.SUBMISSION_UNKNOWN,
                detail=detail,
            )
        except WalPersistenceError:
            return None, f"submission_unknown:{durable.client_order_id}:wal_resolution_failed"
        return None, f"submission_unknown:{durable.client_order_id}"


def _refresh_terminal(
    broker: Any,
    row: Mapping[str, Any],
    *,
    attempts: int,
    delay_seconds: float,
) -> dict[str, Any]:
    current = dict(row)
    order_id = str(current.get("id") or current.get("order_id") or "").strip()
    for index in range(max(1, attempts)):
        if _is_filled(current) or _is_rejected(current) or not order_id:
            break
        if index and delay_seconds > 0:
            time.sleep(delay_seconds)
        refreshed = broker.get_order(order_id)
        if isinstance(refreshed, Mapping):
            current.update(dict(refreshed))
    return current


def _suppression_reason(
    *,
    terminal: TerminalOutcome,
    status: str,
    reconciliation: str,
) -> ExactOrderSuppressionReason:
    if status == "DRY_RUN":
        return ExactOrderSuppressionReason.DRY_RUN_VALIDATION_ONLY
    if terminal is TerminalOutcome.SUBMISSION_UNKNOWN:
        return ExactOrderSuppressionReason.PRIOR_SUBMISSION_UNKNOWN
    if status == "ORDER_REJECTED":
        return ExactOrderSuppressionReason.PRIOR_ORDER_REJECTED
    if status in {"SUBMITTED_UNFILLED", "SELL_PHASE_UNRESOLVED"}:
        return ExactOrderSuppressionReason.PRIOR_ORDER_UNRESOLVED
    if reconciliation == "FAILED_PRE_SUBMIT" or status == "BLOCKED":
        return ExactOrderSuppressionReason.PRE_SUBMIT_VALIDATION_BLOCKED
    if reconciliation == "FAILED_RECONCILIATION":
        return ExactOrderSuppressionReason.PRIOR_RECONCILIATION_FAILURE
    return ExactOrderSuppressionReason.EXECUTION_HALTED


def _outcome(
    plan: ExactExecutionPlan,
    *,
    terminal: TerminalOutcome,
    status: str,
    reason: str,
    submitted: Sequence[Mapping[str, Any]] = (),
    filled: Sequence[Mapping[str, Any]] = (),
    rejected: Sequence[Mapping[str, Any]] = (),
    final_positions: Sequence[Mapping[str, Any]] = (),
    final_cash: float | None = None,
    reconciliation: str = "NOT_RUN",
    failure_class: FailureClass | None = None,
) -> ExactExecutionOutcome:
    submitted_client_ids = {
        str(row.get("client_order_id") or "").strip()
        for row in submitted
        if str(row.get("client_order_id") or "").strip()
    }
    submitted_order_ids = {
        str(row.get("order_id") or "").strip()
        for row in submitted
        if str(row.get("order_id") or "").strip()
    }
    suppression_reason = _suppression_reason(
        terminal=terminal,
        status=status,
        reconciliation=reconciliation,
    )
    suppressed = []
    for order in plan.orders:
        if (
            str(order.get("client_order_id") or "") in submitted_client_ids
            or str(order.get("order_id") or "") in submitted_order_ids
        ):
            continue
        suppressed.append(
            {
                **dict(order),
                "suppression": {
                    "schema_version": "caerus.exact_order_suppression.v1",
                    "reason_type": "ExactOrderSuppressionReason",
                    "reason_code": suppression_reason.value,
                    "blocking_status": status,
                    "blocking_reason_code": reason,
                },
            }
        )
    return ExactExecutionOutcome(
        plan_id_received=plan.plan_id,
        plan_hash_received=plan.content_hash,
        plan_hash_validated=True,
        authorization_validated=True,
        terminal_outcome=terminal,
        status=status,
        reason_code=reason,
        orders_requested=tuple(dict(row) for row in plan.orders),
        orders_submitted=tuple(dict(row) for row in submitted),
        orders_filled=tuple(dict(row) for row in filled),
        orders_rejected=tuple(dict(row) for row in rejected),
        orders_suppressed=tuple(suppressed),
        final_positions=tuple(dict(row) for row in final_positions),
        final_cash=final_cash,
        reconciliation_status=reconciliation,
        failure_class=failure_class,
    )


def _execute_exact_plan_locked(
    *,
    plan_payload: Mapping[str, Any],
    broker: Any,
    env: Mapping[str, str],
    wal_root: Path | str,
    attempt_id: str,
    dry_run: bool,
) -> ExactExecutionOutcome:
    """Validate and execute exactly one v3 plan, or fail closed without mutation."""
    plan = exact_execution_plan_from_dict(plan_payload, expected_account_scope="PAPER")
    epoch = plan_drill_epoch(plan)
    base_wal_root = Path(wal_root)
    wal_root = scoped_wal_root(base_wal_root, epoch)
    authority_root = _account_authority_root(env)
    if not bool(getattr(broker, "paper", False)):
        raise AuthorityContractError("exact v3 execution is PAPER-only")

    # Epoch isolation never hides submission uncertainty.  A new epoch may
    # proceed only when every intent in every other same-date namespace has a
    # terminal durable resolution.  The current epoch is handled by the normal
    # idempotent lookup-recovery path below.
    if epoch:
        try:
            foreign_unresolved: list[str] = []
            for candidate in base_wal_root.glob(
                f"**/{plan.trade_date}/intents/*.json"
            ):
                candidate_root = candidate.parents[2]
                if candidate_root.resolve() == Path(wal_root).resolve():
                    continue
                intent = OrderIntent.from_dict(
                    json.loads(candidate.read_text(encoding="utf-8"))
                )
                if unresolved_intent_requires_lookup(
                    candidate_root,
                    trade_date=plan.trade_date,
                    client_order_id=intent.client_order_id,
                ):
                    foreign_unresolved.append(intent.client_order_id)
            if foreign_unresolved:
                return _outcome(
                    plan,
                    terminal=TerminalOutcome.SYSTEM_FAILURE,
                    status="BLOCKED",
                    reason="prior_epoch_submission_unresolved",
                    reconciliation="FAILED_PRE_SUBMIT",
                    failure_class=FailureClass.STATE_FAILURE,
                )
        except Exception as exc:
            return _outcome(
                plan,
                terminal=TerminalOutcome.SYSTEM_FAILURE,
                status="BLOCKED",
                reason=f"prior_epoch_wal_integrity_failed:{exc}",
                reconciliation="FAILED_PRE_SUBMIT",
                failure_class=FailureClass.STATE_FAILURE,
            )

    max_age_raw = env.get("CAERUS_EXACT_MAX_PLAN_AGE_SECONDS", "900")
    try:
        max_age = float(max_age_raw)
        created = dt.datetime.fromisoformat(plan.created_at.replace("Z", "+00:00"))
        age = (dt.datetime.now(dt.timezone.utc) - created.astimezone(dt.timezone.utc)).total_seconds()
    except (TypeError, ValueError) as exc:
        raise AuthorityContractError("invalid exact-plan freshness policy") from exc
    if max_age <= 0 or age < -60.0:
        return _outcome(
            plan,
            terminal=TerminalOutcome.SYSTEM_FAILURE,
            status="BLOCKED",
            reason="stale_or_future_exact_execution_plan",
            reconciliation="FAILED_PRE_SUBMIT",
            failure_class=FailureClass.AUTHORIZATION_FAILURE,
        )
    # The executor is the final idempotency boundary. Any same-date WAL must
    # belong to this exact plan even when a caller bypasses the authorizer CLI.
    wal_intent_dir = Path(wal_root) / plan.trade_date / "intents"
    durable_intents = []
    if wal_intent_dir.exists():
        try:
            durable_intents = [
                OrderIntent.from_dict(json.loads(path.read_text(encoding="utf-8")))
                for path in sorted(wal_intent_dir.glob("*.json"))
            ]
        except Exception as exc:
            return _outcome(
                plan,
                terminal=TerminalOutcome.SYSTEM_FAILURE,
                status="BLOCKED",
                reason=f"submission_wal_integrity_failed:{exc}",
                reconciliation="FAILED_PRE_SUBMIT",
                failure_class=FailureClass.STATE_FAILURE,
            )
    wal_plans = {(intent.plan_id, intent.plan_hash) for intent in durable_intents}
    if wal_plans and wal_plans != {(plan.plan_id, plan.content_hash)}:
        return _outcome(
            plan,
            terminal=TerminalOutcome.SYSTEM_FAILURE,
            status="BLOCKED",
            reason="foreign_or_mixed_submission_wal_plan",
            reconciliation="FAILED_PRE_SUBMIT",
            failure_class=FailureClass.AUTHORIZATION_FAILURE,
        )
    orders_by_client = {str(order["client_order_id"]): order for order in plan.orders}
    if durable_intents:
        if any(intent.client_order_id not in orders_by_client for intent in durable_intents):
            return _outcome(
                plan,
                terminal=TerminalOutcome.SYSTEM_FAILURE,
                status="BLOCKED",
                reason="submission_wal_contains_unsealed_order",
                reconciliation="FAILED_PRE_SUBMIT",
                failure_class=FailureClass.AUTHORIZATION_FAILURE,
            )
        for intent in durable_intents:
            order = orders_by_client[intent.client_order_id]
            if (
                intent.order_id != str(order["order_id"])
                or intent.symbol != str(order["symbol"])
                or intent.side != str(order["side"])
                or abs(float(intent.quantity) - float(order["quantity"])) > 1e-12
                or intent.order_type != str(order["order_type"])
                or intent.time_in_force != str(order["time_in_force"])
            ):
                return _outcome(
                    plan,
                    terminal=TerminalOutcome.SYSTEM_FAILURE,
                    status="BLOCKED",
                    reason="submission_wal_order_identity_mismatch",
                    reconciliation="FAILED_PRE_SUBMIT",
                    failure_class=FailureClass.AUTHORIZATION_FAILURE,
                )

    # A restart with durable intents enters lookup recovery. A completely fresh
    # attempt must still be current and match the state against which Decision
    # authorized. An expired plan may resolve all of its already-durable intents,
    # but may never create another intent or submit another order.
    recovering = bool(durable_intents)
    expired_recovery = age > max_age and recovering
    if age > max_age and not recovering:
        return _outcome(
            plan,
            terminal=TerminalOutcome.SYSTEM_FAILURE,
            status="BLOCKED",
            reason="stale_or_future_exact_execution_plan",
            reconciliation="FAILED_PRE_SUBMIT",
            failure_class=FailureClass.AUTHORIZATION_FAILURE,
        )

    recovered_evidence: list[dict[str, Any]] = []
    if recovering:
        # Resolve durable broker identities before any gate used to protect new
        # submissions. A later snapshot/cap/asset failure must not erase proof
        # of an already accepted mutation.
        for intent in durable_intents:
            order = orders_by_client[intent.client_order_id]
            try:
                observed = _broker_lookup(broker, intent.client_order_id)
            except Exception as exc:
                return _outcome(
                    plan,
                    terminal=TerminalOutcome.SUBMISSION_UNKNOWN,
                    status="SUBMISSION_UNKNOWN",
                    reason=f"recovery_lookup_failed:{intent.client_order_id}:{exc}",
                    submitted=recovered_evidence,
                    filled=[row for row in recovered_evidence if _has_fill(row)],
                    reconciliation="SUBMISSION_UNKNOWN",
                    failure_class=FailureClass.BROKER_FAILURE,
                )
            if observed is None:
                return _outcome(
                    plan,
                    terminal=TerminalOutcome.SUBMISSION_UNKNOWN,
                    status="SUBMISSION_UNKNOWN",
                    reason=f"recovery_order_not_found:{intent.client_order_id}",
                    submitted=recovered_evidence,
                    filled=[row for row in recovered_evidence if _has_fill(row)],
                    reconciliation="SUBMISSION_UNKNOWN",
                    failure_class=FailureClass.BROKER_FAILURE,
                )
            recovered_row = {
                **dict(observed),
                **dict(order),
                "recovered_by_client_order_id": True,
            }
            recovered_evidence.append(recovered_row)
            broker_id = str(
                observed.get("id") or observed.get("order_id") or ""
            ).strip()
            if not broker_id:
                return _outcome(
                    plan,
                    terminal=TerminalOutcome.SUBMISSION_UNKNOWN,
                    status="SUBMISSION_UNKNOWN",
                    reason=f"recovery_order_missing_broker_id:{intent.client_order_id}",
                    submitted=recovered_evidence,
                    filled=[row for row in recovered_evidence if _has_fill(row)],
                    reconciliation="SUBMISSION_UNKNOWN",
                    failure_class=FailureClass.BROKER_FAILURE,
                )
    try:
        positions, cash, current_account = _snapshot(broker)
    except Exception as exc:
        if recovering:
            return _outcome(
                plan,
                terminal=TerminalOutcome.SYSTEM_FAILURE,
                status="FAILED_RECONCILIATION",
                reason=f"recovery_post_submit_broker_snapshot_failed:{exc}",
                submitted=recovered_evidence,
                filled=[row for row in recovered_evidence if _has_fill(row)],
                reconciliation="FAILED_RECONCILIATION",
                failure_class=FailureClass.BROKER_FAILURE,
            )
        raise
    current_account_id_hash = _broker_account_id_hash(current_account)
    if not current_account_id_hash:
        return _outcome(
            plan,
            terminal=TerminalOutcome.SYSTEM_FAILURE,
            status="BLOCKED",
            reason="broker_account_identity_unavailable",
            submitted=recovered_evidence,
            filled=[row for row in recovered_evidence if _has_fill(row)],
            final_positions=positions,
            final_cash=cash,
            reconciliation="FAILED_PRE_SUBMIT",
            failure_class=FailureClass.AUTHORIZATION_FAILURE,
        )
    if current_account_id_hash != plan.account_id_hash:
        return _outcome(
            plan,
            terminal=TerminalOutcome.SYSTEM_FAILURE,
            status="BLOCKED",
            reason="exact_plan_account_identity_mismatch",
            submitted=recovered_evidence,
            filled=[row for row in recovered_evidence if _has_fill(row)],
            final_positions=positions,
            final_cash=cash,
            reconciliation="FAILED_PRE_SUBMIT",
            failure_class=FailureClass.AUTHORIZATION_FAILURE,
        )
    # Validate an existing canonical account/date claim before mutable broker
    # state can obscure the real authority conflict. A fresh valid plan has no
    # claim yet and continues through all pre-submit gates before creating one.
    try:
        _ensure_plan_claim(
            authority_root,
            plan=plan,
            account=current_account,
            wal_root=wal_root,
            create=False,
        )
    except PlanClaimError as exc:
        return _outcome(
            plan,
            terminal=TerminalOutcome.SYSTEM_FAILURE,
            status="BLOCKED",
            reason=str(exc),
            submitted=recovered_evidence,
            filled=[row for row in recovered_evidence if _has_fill(row)],
            final_positions=positions,
            final_cash=cash,
            reconciliation="FAILED_PRE_SUBMIT",
            failure_class=FailureClass.AUTHORIZATION_FAILURE,
        )
    # Broker lookup is read-only.  Persist recovery observations only after the
    # broker account hash has matched the account sealed into the exact plan.
    for intent, recovered_row in zip(
        durable_intents, recovered_evidence, strict=True
    ):
        broker_id = str(
            recovered_row.get("id") or recovered_row.get("order_id") or ""
        ).strip()
        try:
            _append_resolution(
                wal_root,
                intent=intent,
                state=(
                    ResolutionState.REJECTED
                    if _is_rejected(recovered_row)
                    else ResolutionState.RECOVERED_BY_LOOKUP
                ),
                broker_order_id=(
                    None if _is_rejected(recovered_row) else broker_id
                ),
                detail=f"restart lookup observed status={_status(recovered_row)}",
            )
        except WalPersistenceError:
            return _outcome(
                plan,
                terminal=TerminalOutcome.SUBMISSION_UNKNOWN,
                status="SUBMISSION_UNKNOWN",
                reason=f"recovery_wal_resolution_failed:{intent.client_order_id}",
                submitted=recovered_evidence,
                filled=[row for row in recovered_evidence if _has_fill(row)],
                final_positions=positions,
                final_cash=cash,
                reconciliation="SUBMISSION_UNKNOWN",
                failure_class=FailureClass.BROKER_FAILURE,
            )
    current_nav = _finite(
        current_account.get("portfolio_value") or current_account.get("equity")
    )
    runtime_cap, _runtime_cap_source = resolve_dynamic_cap(current_nav, env)
    aggregate_buy_notional = sum(
        float(_notional(order) or 0.0)
        for order in plan.buy_orders
        if not intent_path(
            wal_root,
            trade_date=plan.trade_date,
            client_order_id=str(order["client_order_id"]),
        ).exists()
    )
    if not recovering:
        actual_state_hash = compute_starting_state_hash(positions, cash)
        if actual_state_hash != plan.starting_state_hash:
            return _outcome(
                plan,
                terminal=TerminalOutcome.SYSTEM_FAILURE,
                status="BLOCKED",
                reason="starting_broker_state_mismatch",
                final_positions=positions,
                final_cash=cash,
                reconciliation="FAILED_PRE_SUBMIT",
                failure_class=FailureClass.STATE_FAILURE,
            )
    else:
        # A retry may continue only from the broker state mechanically explained
        # by the stable WAL orders. Resolve every prior intent before creating a
        # new one; unexplained cash/position drift blocks the remainder.
        expected_quantities = {
            str(row["symbol"]): float(row["quantity"])
            for row in plan.starting_positions
        }
        expected_cash = float(plan.starting_cash)
        for observed_row in recovered_evidence:
            order = orders_by_client[str(observed_row["client_order_id"])]
            filled_quantity = _filled_quantity(observed_row)
            if filled_quantity > 0:
                fill_price = _finite(
                    observed_row.get("filled_avg_price")
                    or observed_row.get("fill_price")
                )
                if fill_price is None or fill_price <= 0:
                    return _outcome(
                        plan,
                        terminal=TerminalOutcome.SUBMISSION_UNKNOWN,
                        status="SUBMISSION_UNKNOWN",
                        reason=f"recovery_fill_economics_missing:{order['client_order_id']}",
                        submitted=recovered_evidence,
                        filled=[row for row in recovered_evidence if _has_fill(row)],
                        final_positions=positions,
                        final_cash=cash,
                        reconciliation="SUBMISSION_UNKNOWN",
                        failure_class=FailureClass.BROKER_FAILURE,
                    )
                symbol = str(order["symbol"])
                if str(order["side"]) == "SELL":
                    expected_quantities[symbol] = expected_quantities.get(symbol, 0.0) - filled_quantity
                    expected_cash += filled_quantity * fill_price
                else:
                    expected_quantities[symbol] = expected_quantities.get(symbol, 0.0) + filled_quantity
                    expected_cash -= filled_quantity * fill_price
        expected_quantities = {
            symbol: quantity
            for symbol, quantity in expected_quantities.items()
            if quantity > 1e-8
        }
        actual_quantities = {
            str(row["symbol"]): float(row["quantity"])
            for row in positions
        }
        cash_tolerance = float(
            _finite(plan.constraints.get("cash_reconciliation_tolerance_usd"))
            or 0.01
        )
        if (
            expected_quantities != actual_quantities
            or abs(expected_cash - cash) > cash_tolerance + 1e-9
        ):
            return _outcome(
                plan,
                terminal=TerminalOutcome.SYSTEM_FAILURE,
                status="BLOCKED",
                reason="unexplained_broker_state_drift_during_recovery",
                submitted=recovered_evidence,
                filled=[row for row in recovered_evidence if _has_fill(row)],
                final_positions=positions,
                final_cash=cash,
                reconciliation="FAILED_PRE_SUBMIT",
                failure_class=FailureClass.STATE_FAILURE,
            )
        if expired_recovery and any(
            not intent_path(
                wal_root,
                trade_date=plan.trade_date,
                client_order_id=str(order["client_order_id"]),
            ).exists()
            for order in plan.orders
        ):
            return _outcome(
                plan,
                terminal=TerminalOutcome.SYSTEM_FAILURE,
                status="BLOCKED",
                reason="stale_recovery_resolved_existing_intents_new_submissions_forbidden",
                submitted=recovered_evidence,
                filled=[row for row in recovered_evidence if _has_fill(row)],
                final_positions=positions,
                final_cash=cash,
                reconciliation="FAILED_PRE_SUBMIT",
                failure_class=FailureClass.AUTHORIZATION_FAILURE,
            )

        # Recovery must resolve every prior stable-ID order before any new WAL
        # intent can be created.  Preserve partial fills as economic evidence,
        # but never advance to another order (especially the BUY phase) while
        # a prior order is open, canceled, rejected, expired, or otherwise not
        # completely filled.
        recovered_fills = [row for row in recovered_evidence if _has_fill(row)]
        recovered_rejections = [row for row in recovered_evidence if _is_rejected(row)]
        if recovered_rejections:
            rejected_row = recovered_rejections[0]
            rejected_order = orders_by_client[str(rejected_row["client_order_id"])]
            return _outcome(
                plan,
                terminal=TerminalOutcome.SYSTEM_FAILURE,
                status="ORDER_REJECTED",
                reason=f"exact_order_rejected:{rejected_order['order_id']}",
                submitted=recovered_evidence,
                filled=recovered_fills,
                rejected=recovered_rejections,
                final_positions=positions,
                final_cash=cash,
                reconciliation="FAILED_RECONCILIATION",
                failure_class=FailureClass.BROKER_FAILURE,
            )
        unresolved_recovered = [
            row for row in recovered_evidence if not _is_filled(row)
        ]
        if unresolved_recovered:
            unresolved_row = unresolved_recovered[0]
            unresolved_order = orders_by_client[str(unresolved_row["client_order_id"])]
            return _outcome(
                plan,
                terminal=TerminalOutcome.SYSTEM_FAILURE,
                status="SUBMITTED_UNFILLED",
                reason=f"exact_order_not_terminal:{unresolved_order['order_id']}",
                submitted=recovered_evidence,
                filled=recovered_fills,
                final_positions=positions,
                final_cash=cash,
                reconciliation="UNRESOLVED",
                failure_class=FailureClass.RECONCILIATION_FAILURE,
            )

    has_new_intents = any(
        not intent_path(
            wal_root,
            trade_date=plan.trade_date,
            client_order_id=str(order["client_order_id"]),
        ).exists()
        for order in plan.orders
    )
    if has_new_intents and (
        runtime_cap is None or aggregate_buy_notional > float(runtime_cap) + 1e-9
    ):
        return _outcome(
            plan,
            terminal=TerminalOutcome.SYSTEM_FAILURE,
            status="BLOCKED",
            reason="runtime_dynamic_cap_below_authorized_buy_notional",
            submitted=recovered_evidence if recovering else (),
            filled=(
                [row for row in recovered_evidence if _has_fill(row)]
                if recovering
                else ()
            ),
            final_positions=positions,
            final_cash=cash,
            reconciliation="FAILED_PRE_SUBMIT",
            failure_class=FailureClass.RISK_FAILURE,
        )

    # Validate the entire immutable batch before the first WAL or broker mutation.
    inventory = {str(row["symbol"]): float(row["quantity"]) for row in positions}
    for order in plan.orders:
        order_has_wal = intent_path(
            wal_root,
            trade_date=plan.trade_date,
            client_order_id=str(order["client_order_id"]),
        ).exists()
        if order_has_wal:
            continue
        asset = broker.get_asset(str(order["symbol"]))
        asset_error = validate_live_pilot_asset(asset, str(order["symbol"]))
        if asset_error:
            return _outcome(
                plan,
                terminal=TerminalOutcome.SYSTEM_FAILURE,
                status="BLOCKED",
                reason=f"exact_asset_validation_failed:{asset_error}",
                submitted=recovered_evidence if recovering else (),
                filled=(
                    [row for row in recovered_evidence if _has_fill(row)]
                    if recovering
                    else ()
                ),
                final_positions=positions,
                final_cash=cash,
                reconciliation="FAILED_PRE_SUBMIT",
                failure_class=FailureClass.EXECUTION_FAILURE,
            )
        if (
            not order_has_wal
            and str(order["side"]) == "SELL"
            and inventory.get(str(order["symbol"]), 0.0) + 1e-9 < float(order["quantity"])
        ):
            return _outcome(
                plan,
                terminal=TerminalOutcome.SYSTEM_FAILURE,
                status="BLOCKED",
                reason=f"exact_sell_inventory_changed:{order['symbol']}",
                submitted=recovered_evidence if recovering else (),
                filled=(
                    [row for row in recovered_evidence if _has_fill(row)]
                    if recovering
                    else ()
                ),
                final_positions=positions,
                final_cash=cash,
                reconciliation="FAILED_PRE_SUBMIT",
                failure_class=FailureClass.STATE_FAILURE,
            )
        if str(order["order_type"]).lower() not in {"market", "limit"}:
            return _outcome(
                plan,
                terminal=TerminalOutcome.SYSTEM_FAILURE,
                status="BLOCKED",
                reason=f"unsupported_exact_order_type:{order['order_type']}",
                submitted=recovered_evidence if recovering else (),
                filled=(
                    [row for row in recovered_evidence if _has_fill(row)]
                    if recovering
                    else ()
                ),
                final_positions=positions,
                final_cash=cash,
                reconciliation="FAILED_PRE_SUBMIT",
                failure_class=FailureClass.EXECUTION_FAILURE,
            )
        try:
            validate_live_pilot_submission_guardrails(
                broker_paper=bool(getattr(broker, "paper", False)),
                base_url=str(getattr(broker, "base_url", "") or ""),
                env=env,
                order_notional=_notional(order),
            )
        except Exception as exc:
            return _outcome(
                plan,
                terminal=TerminalOutcome.SYSTEM_FAILURE,
                status="BLOCKED",
                reason=f"exact_batch_guardrail_failed:{order['order_id']}:{exc}",
                submitted=recovered_evidence if recovering else (),
                filled=(
                    [row for row in recovered_evidence if _has_fill(row)]
                    if recovering
                    else ()
                ),
                final_positions=positions,
                final_cash=cash,
                reconciliation="FAILED_PRE_SUBMIT",
                failure_class=FailureClass.EXECUTION_FAILURE,
            )

    if has_new_intents:
        open_orders = broker.list_orders(status="open", limit=100)
        if not isinstance(open_orders, list):
            raise RuntimeError("broker open-order response is not a list")
        planned_clients = {str(order["client_order_id"]) for order in plan.orders}
        planned_symbols = {str(order["symbol"]) for order in plan.orders}
        conflicts = [
            row
            for row in open_orders
            if isinstance(row, Mapping)
            and str(row.get("symbol") or "").strip().upper() in planned_symbols
            and str(row.get("client_order_id") or "") not in planned_clients
        ]
        if conflicts:
            return _outcome(
                plan,
                terminal=TerminalOutcome.SYSTEM_FAILURE,
                status="BLOCKED",
                reason="conflicting_open_order",
                submitted=recovered_evidence,
                filled=[row for row in recovered_evidence if _has_fill(row)],
                final_positions=positions,
                final_cash=cash,
                reconciliation="FAILED_PRE_SUBMIT",
                failure_class=FailureClass.STATE_FAILURE,
            )

    if not dry_run:
        try:
            _ensure_plan_claim(
                authority_root,
                plan=plan,
                account=current_account,
                wal_root=wal_root,
            )
        except PlanClaimError as exc:
            return _outcome(
                plan,
                terminal=TerminalOutcome.SYSTEM_FAILURE,
                status="BLOCKED",
                reason=str(exc),
                submitted=recovered_evidence,
                filled=[row for row in recovered_evidence if _has_fill(row)],
                final_positions=positions,
                final_cash=cash,
                reconciliation="FAILED_PRE_SUBMIT",
                failure_class=FailureClass.AUTHORIZATION_FAILURE,
            )

    if not plan.orders:
        return _outcome(
            plan,
            terminal=TerminalOutcome.AUTHORIZED_NO_TRADE,
            status="AUTHORIZED_NO_TRADE",
            reason="authorized_exact_plan_contains_zero_orders",
            final_positions=positions,
            final_cash=cash,
            reconciliation="NOT_APPLICABLE_NO_TRADE",
        )
    if dry_run:
        return _outcome(
            plan,
            terminal=TerminalOutcome.RECONCILED_SUCCESS,
            status="DRY_RUN",
            reason="exact_plan_validated_no_submission",
            submitted=recovered_evidence if recovering else (),
            filled=(
                [row for row in recovered_evidence if _has_fill(row)]
                if recovering
                else ()
            ),
            final_positions=positions,
            final_cash=cash,
            reconciliation="DRY_RUN_NO_SUBMISSION",
        )

    attempts = max(1, int(float(env.get("CAERUS_EXACT_FILL_REFRESH_ATTEMPTS") or 5)))
    delay = max(0.0, float(env.get("CAERUS_EXACT_FILL_REFRESH_DELAY_SECONDS") or 0.2))
    submitted: list[dict[str, Any]] = []
    filled: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for phase, orders in (("SELL", plan.sell_orders), ("BUY", plan.buy_orders)):
        if phase == "BUY" and len(filled) < len(plan.sell_orders):
            failure_positions, failure_cash = _best_effort_snapshot(broker)
            return _outcome(
                plan,
                terminal=TerminalOutcome.SYSTEM_FAILURE,
                status="SELL_PHASE_UNRESOLVED",
                reason="exact_sell_phase_not_fully_filled_buys_not_submitted",
                submitted=submitted,
                filled=filled,
                rejected=rejected,
                final_positions=failure_positions,
                final_cash=failure_cash,
                reconciliation="UNRESOLVED",
                failure_class=FailureClass.EXECUTION_FAILURE,
            )
        for order in orders:
            broker_row, ambiguity = _submit_one(
                plan=plan,
                order=order,
                broker=broker,
                env=env,
                wal_root=wal_root,
                attempt_id=attempt_id,
            )
            if ambiguity:
                failure_positions, failure_cash = _best_effort_snapshot(broker)
                ambiguity_submitted = list(submitted)
                ambiguity_filled = list(filled)
                if broker_row is not None:
                    ambiguity_submitted.append(broker_row)
                    if _has_fill(broker_row):
                        ambiguity_filled.append(broker_row)
                return _outcome(
                    plan,
                    terminal=TerminalOutcome.SUBMISSION_UNKNOWN,
                    status="SUBMISSION_UNKNOWN",
                    reason=ambiguity,
                    submitted=ambiguity_submitted,
                    filled=ambiguity_filled,
                    rejected=rejected,
                    final_positions=failure_positions,
                    final_cash=failure_cash,
                    reconciliation="SUBMISSION_UNKNOWN",
                    failure_class=FailureClass.BROKER_FAILURE,
                )
            assert broker_row is not None
            submitted.append(broker_row)
            try:
                refreshed = _refresh_terminal(
                    broker, broker_row, attempts=attempts, delay_seconds=delay
                )
                broker_row = {**refreshed, **dict(order)}
                submitted[-1] = broker_row
            except Exception as exc:
                failure_positions, failure_cash = _best_effort_snapshot(broker)
                status_fills = list(filled)
                if _has_fill(submitted[-1]):
                    status_fills.append(submitted[-1])
                return _outcome(
                    plan,
                    terminal=TerminalOutcome.SYSTEM_FAILURE,
                    status="FAILED_RECONCILIATION",
                    reason=f"post_submit_broker_status_refresh_failed:{order['order_id']}:{exc}",
                    submitted=submitted,
                    filled=status_fills,
                    rejected=rejected,
                    final_positions=failure_positions,
                    final_cash=failure_cash,
                    reconciliation="FAILED_RECONCILIATION",
                    failure_class=FailureClass.BROKER_FAILURE,
                )
            if _has_fill(broker_row):
                filled.append(broker_row)
            if _is_rejected(broker_row):
                rejected.append(broker_row)
                failure_positions, failure_cash = _best_effort_snapshot(broker)
                return _outcome(
                    plan,
                    terminal=TerminalOutcome.SYSTEM_FAILURE,
                    status="ORDER_REJECTED",
                    reason=f"exact_order_rejected:{order['order_id']}",
                    submitted=submitted,
                    filled=filled,
                    rejected=rejected,
                    final_positions=failure_positions,
                    final_cash=failure_cash,
                    reconciliation="FAILED_RECONCILIATION",
                    failure_class=FailureClass.BROKER_FAILURE,
                )
            elif not _is_filled(broker_row):
                # Known open order: do not call it success and do not progress to
                # another exposure-changing phase.
                failure_positions, failure_cash = _best_effort_snapshot(broker)
                return _outcome(
                    plan,
                    terminal=TerminalOutcome.SYSTEM_FAILURE,
                    status="SUBMITTED_UNFILLED",
                    reason=f"exact_order_not_terminal:{order['order_id']}",
                    submitted=submitted,
                    filled=filled,
                    rejected=rejected,
                    final_positions=failure_positions,
                    final_cash=failure_cash,
                    reconciliation="UNRESOLVED",
                    failure_class=FailureClass.RECONCILIATION_FAILURE,
                )

    try:
        final_positions, final_cash, _final_account = _snapshot(broker)
    except Exception as exc:
        failure_positions, failure_cash = _best_effort_snapshot(broker)
        return _outcome(
            plan,
            terminal=TerminalOutcome.SYSTEM_FAILURE,
            status="FAILED_RECONCILIATION",
            reason=f"post_submit_broker_snapshot_failed:{exc}",
            submitted=submitted,
            filled=filled,
            rejected=rejected,
            final_positions=failure_positions,
            final_cash=failure_cash,
            reconciliation="FAILED_RECONCILIATION",
            failure_class=FailureClass.BROKER_FAILURE,
        )
    expected_positions_hash = compute_starting_state_hash(
        plan.expected_posttrade_positions, 0.0
    )
    actual_positions_hash = compute_starting_state_hash(final_positions, 0.0)
    # Market orders are authorized and sealed at a fresh reference price, but
    # broker cash settles at the actual fill price.  Reconcile cash from the
    # terminal fill evidence rather than treating favorable/adverse slippage as
    # unexplained state drift.  Position equality remains exact below.
    fill_adjusted_cash = float(plan.starting_cash)
    fill_economics_complete = len(filled) == len(plan.orders)
    seen_fill_ids: set[str] = set()
    for fill in filled:
        client_order_id = str(fill.get("client_order_id") or "").strip()
        quantity = _filled_quantity(fill)
        fill_price = _finite(fill.get("filled_avg_price") or fill.get("fill_price"))
        order = orders_by_client.get(client_order_id)
        if (
            not client_order_id
            or client_order_id in seen_fill_ids
            or order is None
            or quantity <= 0
            or fill_price is None
            or fill_price <= 0
        ):
            fill_economics_complete = False
            continue
        seen_fill_ids.add(client_order_id)
        notional = quantity * fill_price
        fill_adjusted_cash += notional if str(order["side"]) == "SELL" else -notional
    fill_economics_complete = (
        fill_economics_complete and len(seen_fill_ids) == len(plan.orders)
    )
    cash_tolerance = _finite(plan.constraints.get("cash_reconciliation_tolerance_usd"))
    if cash_tolerance is None or cash_tolerance < 0:
        cash_tolerance = 0.01
    if (
        not fill_economics_complete
        or expected_positions_hash != actual_positions_hash
        or abs(final_cash - fill_adjusted_cash) > cash_tolerance + 1e-9
    ):
        return _outcome(
            plan,
            terminal=TerminalOutcome.SYSTEM_FAILURE,
            status="FAILED_RECONCILIATION",
            reason="exact_posttrade_state_mismatch",
            submitted=submitted,
            filled=filled,
            rejected=rejected,
            final_positions=final_positions,
            final_cash=final_cash,
            reconciliation="FAILED_RECONCILIATION",
            failure_class=FailureClass.RECONCILIATION_FAILURE,
        )
    return _outcome(
        plan,
        terminal=TerminalOutcome.RECONCILED_SUCCESS,
        status="RECONCILED_SUCCESS",
        reason="exact_plan_submitted_filled_and_reconciled",
        submitted=submitted,
        filled=filled,
        rejected=rejected,
        final_positions=final_positions,
        final_cash=final_cash,
        reconciliation="CLEAN",
    )


def execute_exact_plan(
    *,
    plan_payload: Mapping[str, Any],
    broker: Any,
    env: Mapping[str, str],
    wal_root: Path | str,
    attempt_id: str,
    dry_run: bool,
) -> ExactExecutionOutcome:
    """Execute under a date-wide OS mutex and an immutable account claim.

    The mutex closes the pre-WAL race where two distinct plans could both see
    an empty date directory.  It is intentionally held for the full execution
    call.  The durable plan claim, written immediately before the first possible
    WAL/broker mutation, survives process crashes and permits only same-plan
    stable-ID recovery.
    """

    plan = exact_execution_plan_from_dict(
        plan_payload, expected_account_scope="PAPER"
    )
    if not bool(getattr(broker, "paper", False)):
        raise AuthorityContractError("exact v3 execution is PAPER-only")
    try:
        authority_root = _account_authority_root(env)
        with _date_execution_lock(
            authority_root,
            plan.trade_date,
            plan.account_id_hash,
        ):
            return _execute_exact_plan_locked(
                plan_payload=plan_payload,
                broker=broker,
                env=env,
                wal_root=wal_root,
                attempt_id=attempt_id,
                dry_run=dry_run,
            )
    except ExecutionMutexError as exc:
        return _outcome(
            plan,
            terminal=TerminalOutcome.SYSTEM_FAILURE,
            status="BLOCKED",
            reason=str(exc),
            reconciliation="FAILED_PRE_SUBMIT",
            failure_class=FailureClass.STATE_FAILURE,
        )

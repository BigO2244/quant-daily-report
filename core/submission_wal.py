"""Durable append-only write-ahead log for exact broker order submissions.

The required call order is:

1. ``prepare_order_intent`` persists and fsyncs one immutable intent.
2. Only when ``broker_submission_allowed`` is true may the caller invoke the
   broker.
3. ``append_resolution`` records the immutable observed outcome.

If a process restarts after the broker accepted an order but before a response
was persisted, replay returns the prior intent with submission disallowed.
The caller must look up the stable ``client_order_id`` and record
``RECOVERED_BY_LOOKUP`` or ``SUBMISSION_UNKNOWN``; it must not resubmit.

This module does not invoke a broker or alter execution behavior by itself.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from authority.exact_plan import compute_starting_state_hash


INTENT_SCHEMA_VERSION = "caerus.submission_wal_intent.v1"
RESOLUTION_SCHEMA_VERSION = "caerus.submission_wal_resolution.v1"

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_TRADE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PAPER_DRILL_EPOCH = re.compile(
    r"^(\d{4}-\d{2}-\d{2})T([01]\d|2[0-3])([0-5]\d)ET$"
)


class WalPersistenceError(RuntimeError):
    """Raised when the durable pre-submit write cannot be proven."""


class ResolutionState(str, Enum):
    SUBMITTED = "SUBMITTED"
    RECOVERED_BY_LOOKUP = "RECOVERED_BY_LOOKUP"
    BROKER_OBSERVED = "BROKER_OBSERVED"
    REJECTED = "REJECTED"
    SUBMISSION_UNKNOWN = "SUBMISSION_UNKNOWN"
    ECONOMICALLY_RECONCILED = "ECONOMICALLY_RECONCILED"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_id(label: str, value: str) -> str:
    normalized = str(value or "").strip()
    if not _SAFE_ID.fullmatch(normalized) or ".." in normalized:
        raise ValueError(f"invalid {label}: {value!r}")
    return normalized


def _date(value: str) -> str:
    normalized = str(value or "").strip()
    if not _TRADE_DATE.fullmatch(normalized):
        raise ValueError(f"invalid trade_date: {value!r}")
    return normalized


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _canonical_json(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _content_hash(payload: Mapping[str, Any]) -> str:
    unhashed = dict(payload)
    unhashed.pop("content_hash", None)
    return hashlib.sha256(_canonical_json(unhashed)).hexdigest()


def _intent_identity(intent: "OrderIntent") -> bytes:
    """Stable order identity; attempt/time metadata may change on restart."""

    payload = intent.to_dict(include_hash=False)
    payload.pop("created_at", None)
    payload.pop("attempt_id", None)
    return _canonical_json(payload)


def _write_exclusive_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    with path.open("x", encoding="utf-8") as handle:
        handle.write(serialized)
        handle.flush()
        os.fsync(handle.fileno())
    directory_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


@dataclass(frozen=True)
class OrderIntent:
    trade_date: str
    plan_id: str
    plan_hash: str
    attempt_id: str
    order_id: str
    client_order_id: str
    symbol: str
    side: str
    quantity: float
    order_type: str
    created_at: str
    limit_price: float | None = None
    stop_price: float | None = None
    expected_price: float | None = None
    notional: float | None = None
    time_in_force: str = "day"
    extended_hours: bool = False
    account_scope: str = "PAPER"
    sleeve: str = ""
    starting_state_hash: str = ""
    paper_drill_epoch: str = ""
    content_hash: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        _date(self.trade_date)
        for label, value in {
            "plan_id": self.plan_id,
            "attempt_id": self.attempt_id,
            "order_id": self.order_id,
            "client_order_id": self.client_order_id,
        }.items():
            _safe_id(label, value)
        if not str(self.plan_hash or "").strip():
            raise ValueError("plan_hash is required")
        if not str(self.symbol or "").strip():
            raise ValueError("symbol is required")
        if str(self.side or "").strip().upper() not in {"BUY", "SELL"}:
            raise ValueError(f"invalid side: {self.side!r}")
        if not _finite(self.quantity) or float(self.quantity) <= 0:
            raise ValueError("quantity must be finite and positive")
        if not str(self.order_type or "").strip():
            raise ValueError("order_type is required")
        for label, value in {
            "limit_price": self.limit_price,
            "stop_price": self.stop_price,
            "expected_price": self.expected_price,
            "notional": self.notional,
        }.items():
            if value is not None and (not _finite(value) or float(value) < 0):
                raise ValueError(f"{label} must be finite and non-negative")
        if str(self.account_scope or "").strip().upper() != "PAPER":
            raise ValueError("submission WAL order intent is PAPER-only")
        if self.starting_state_hash and not _SHA256.fullmatch(
            str(self.starting_state_hash)
        ):
            raise ValueError("starting_state_hash must be a SHA-256 digest")
        if self.paper_drill_epoch:
            match = _PAPER_DRILL_EPOCH.fullmatch(str(self.paper_drill_epoch))
            if match is None or match.group(1) != self.trade_date:
                raise ValueError("paper_drill_epoch must match the intent trade date")

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema_version"] = INTENT_SCHEMA_VERSION
        payload["symbol"] = self.symbol.strip().upper()
        payload["side"] = self.side.strip().upper()
        # Normalize numeric representation before hashing so 1 and 1.0 have
        # the same immutable identity across JSON round trips.
        payload["quantity"] = float(self.quantity)
        payload["limit_price"] = (
            float(self.limit_price) if self.limit_price is not None else None
        )
        payload["stop_price"] = (
            float(self.stop_price) if self.stop_price is not None else None
        )
        payload["expected_price"] = (
            float(self.expected_price) if self.expected_price is not None else None
        )
        payload["notional"] = float(self.notional) if self.notional is not None else None
        payload["account_scope"] = self.account_scope.strip().upper()
        # These lineage fields were added without changing the v1 envelope.
        # Omit them for legacy intents so their existing content hashes remain
        # verifiable after deployment.
        if not self.starting_state_hash:
            payload.pop("starting_state_hash", None)
        if not self.paper_drill_epoch:
            payload.pop("paper_drill_epoch", None)
        if not include_hash:
            payload.pop("content_hash", None)
        return payload

    def with_content_hash(self) -> "OrderIntent":
        return replace(self, content_hash=_content_hash(self.to_dict(include_hash=False)))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], *, verify_hash: bool = True) -> "OrderIntent":
        if payload.get("schema_version") != INTENT_SCHEMA_VERSION:
            raise ValueError("unsupported WAL intent schema")
        intent = cls(
            trade_date=str(payload.get("trade_date") or ""),
            plan_id=str(payload.get("plan_id") or ""),
            plan_hash=str(payload.get("plan_hash") or ""),
            attempt_id=str(payload.get("attempt_id") or ""),
            order_id=str(payload.get("order_id") or ""),
            client_order_id=str(payload.get("client_order_id") or ""),
            symbol=str(payload.get("symbol") or ""),
            side=str(payload.get("side") or ""),
            quantity=float(payload.get("quantity") or 0),
            order_type=str(payload.get("order_type") or ""),
            created_at=str(payload.get("created_at") or ""),
            limit_price=(float(payload["limit_price"]) if payload.get("limit_price") is not None else None),
            stop_price=(float(payload["stop_price"]) if payload.get("stop_price") is not None else None),
            expected_price=(
                float(payload["expected_price"])
                if payload.get("expected_price") is not None
                else None
            ),
            notional=(float(payload["notional"]) if payload.get("notional") is not None else None),
            time_in_force=str(payload.get("time_in_force") or "day"),
            extended_hours=bool(payload.get("extended_hours", False)),
            account_scope=str(payload.get("account_scope") or "PAPER"),
            sleeve=str(payload.get("sleeve") or ""),
            starting_state_hash=str(payload.get("starting_state_hash") or ""),
            paper_drill_epoch=str(payload.get("paper_drill_epoch") or ""),
            content_hash=str(payload.get("content_hash") or ""),
        )
        expected = _content_hash(intent.to_dict(include_hash=False))
        if verify_hash and intent.content_hash != expected:
            raise ValueError(f"WAL intent content hash mismatch: {intent.client_order_id}")
        return intent


@dataclass(frozen=True)
class IntentPreparation:
    intent: OrderIntent
    path: Path
    created: bool
    broker_submission_allowed: bool
    recovery_lookup_required: bool


@dataclass(frozen=True)
class ResolutionEvent:
    resolution_id: str
    trade_date: str
    client_order_id: str
    intent_hash: str
    state: ResolutionState
    recorded_at: str
    broker_order_id: str | None = None
    detail: str = ""
    content_hash: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        _safe_id("resolution_id", self.resolution_id)
        _date(self.trade_date)
        _safe_id("client_order_id", self.client_order_id)
        if not str(self.intent_hash or "").strip():
            raise ValueError("intent_hash is required")
        if self.state in {
            ResolutionState.SUBMITTED,
            ResolutionState.RECOVERED_BY_LOOKUP,
            ResolutionState.BROKER_OBSERVED,
            ResolutionState.ECONOMICALLY_RECONCILED,
        }:
            if not str(self.broker_order_id or "").strip():
                raise ValueError(f"{self.state.value} requires broker_order_id")

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema_version"] = RESOLUTION_SCHEMA_VERSION
        payload["state"] = self.state.value
        if not include_hash:
            payload.pop("content_hash", None)
        return payload

    def with_content_hash(self) -> "ResolutionEvent":
        return replace(self, content_hash=_content_hash(self.to_dict(include_hash=False)))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], *, verify_hash: bool = True) -> "ResolutionEvent":
        if payload.get("schema_version") != RESOLUTION_SCHEMA_VERSION:
            raise ValueError("unsupported WAL resolution schema")
        event = cls(
            resolution_id=str(payload.get("resolution_id") or ""),
            trade_date=str(payload.get("trade_date") or ""),
            client_order_id=str(payload.get("client_order_id") or ""),
            intent_hash=str(payload.get("intent_hash") or ""),
            state=ResolutionState(str(payload.get("state") or "")),
            recorded_at=str(payload.get("recorded_at") or ""),
            broker_order_id=(str(payload["broker_order_id"]) if payload.get("broker_order_id") else None),
            detail=str(payload.get("detail") or ""),
            content_hash=str(payload.get("content_hash") or ""),
        )
        expected = _content_hash(event.to_dict(include_hash=False))
        if verify_hash and event.content_hash != expected:
            raise ValueError(f"WAL resolution content hash mismatch: {event.resolution_id}")
        return event


def intent_path(wal_root: Path | str, *, trade_date: str, client_order_id: str) -> Path:
    return (
        Path(wal_root)
        / _date(trade_date)
        / "intents"
        / f"{_safe_id('client_order_id', client_order_id)}.json"
    )


def resolution_path(
    wal_root: Path | str,
    *,
    trade_date: str,
    client_order_id: str,
    resolution_id: str,
) -> Path:
    return (
        Path(wal_root)
        / _date(trade_date)
        / "resolutions"
        / _safe_id("client_order_id", client_order_id)
        / f"{_safe_id('resolution_id', resolution_id)}.json"
    )


def prepare_order_intent(wal_root: Path | str, intent: OrderIntent) -> IntentPreparation:
    """Durably prewrite intent or return the identical prior intent on replay.

    Any persistence error raises ``WalPersistenceError`` before a caller can
    receive permission to invoke the broker.
    """

    hashed = intent.with_content_hash()
    path = intent_path(
        wal_root,
        trade_date=hashed.trade_date,
        client_order_id=hashed.client_order_id,
    )
    if path.exists():
        try:
            prior = OrderIntent.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:
            raise WalPersistenceError(
                f"existing WAL intent is unreadable or corrupt: {exc}"
            ) from exc
        if _intent_identity(prior) != _intent_identity(hashed):
            raise WalPersistenceError(
                "stable client_order_id is already bound to a different immutable order intent"
            )
        recovery_required = unresolved_intent_requires_lookup(
            wal_root,
            trade_date=prior.trade_date,
            client_order_id=prior.client_order_id,
        )
        return IntentPreparation(
            intent=prior,
            path=path,
            created=False,
            broker_submission_allowed=False,
            recovery_lookup_required=recovery_required,
        )
    try:
        _write_exclusive_json(path, hashed.to_dict())
    except FileExistsError:
        # Another process won the race.  Re-enter the replay path and verify
        # exact identity; never permit both processes to submit.
        return prepare_order_intent(wal_root, hashed)
    except Exception as exc:
        raise WalPersistenceError(f"unable to persist pre-submit WAL intent: {exc}") from exc
    return IntentPreparation(
        intent=hashed,
        path=path,
        created=True,
        broker_submission_allowed=True,
        recovery_lookup_required=False,
    )


def append_resolution(wal_root: Path | str, event: ResolutionEvent) -> Path:
    """Append one immutable broker-observation event for a persisted intent."""

    path = intent_path(
        wal_root,
        trade_date=event.trade_date,
        client_order_id=event.client_order_id,
    )
    if not path.exists():
        raise WalPersistenceError("cannot resolve an order without a durable WAL intent")
    intent = OrderIntent.from_dict(json.loads(path.read_text(encoding="utf-8")))
    if intent.content_hash != event.intent_hash:
        raise WalPersistenceError("resolution intent_hash does not match durable WAL intent")
    hashed = event.with_content_hash()
    target = resolution_path(
        wal_root,
        trade_date=hashed.trade_date,
        client_order_id=hashed.client_order_id,
        resolution_id=hashed.resolution_id,
    )
    if target.exists():
        try:
            prior = ResolutionEvent.from_dict(json.loads(target.read_text(encoding="utf-8")))
        except Exception as exc:
            raise WalPersistenceError(
                f"existing WAL resolution is unreadable or corrupt: {exc}"
            ) from exc
        if prior.content_hash != hashed.content_hash:
            raise WalPersistenceError(
                "resolution_id is already bound to a different immutable event"
            )
        return target
    try:
        _write_exclusive_json(target, hashed.to_dict())
    except FileExistsError:
        # Resolve a concurrent writer through the immutable replay path.
        return append_resolution(wal_root, hashed)
    except Exception as exc:
        raise WalPersistenceError(f"unable to persist WAL resolution: {exc}") from exc
    return target


def read_resolutions(
    wal_root: Path | str,
    *,
    trade_date: str,
    client_order_id: str,
) -> list[ResolutionEvent]:
    directory = (
        Path(wal_root)
        / _date(trade_date)
        / "resolutions"
        / _safe_id("client_order_id", client_order_id)
    )
    if not directory.exists():
        return []
    events = [
        ResolutionEvent.from_dict(json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(directory.glob("*.json"))
    ]
    intent = OrderIntent.from_dict(
        json.loads(
            intent_path(
                wal_root,
                trade_date=trade_date,
                client_order_id=client_order_id,
            ).read_text(encoding="utf-8")
        )
    )
    if any(event.intent_hash != intent.content_hash for event in events):
        raise WalPersistenceError("WAL resolution lineage mismatch")
    return sorted(events, key=lambda event: (event.recorded_at, event.resolution_id))


def unresolved_intent_requires_lookup(
    wal_root: Path | str,
    *,
    trade_date: str,
    client_order_id: str,
) -> bool:
    """Return true when restart must query broker rather than resubmit."""

    path = intent_path(wal_root, trade_date=trade_date, client_order_id=client_order_id)
    if not path.exists():
        return False
    events = read_resolutions(
        wal_root,
        trade_date=trade_date,
        client_order_id=client_order_id,
    )
    if not events:
        return True
    return events[-1].state is ResolutionState.SUBMISSION_UNKNOWN


_TERMINAL_BROKER_ORDER_STATUSES = {
    "filled",
    "rejected",
    "canceled",
    "cancelled",
    "expired",
    "failed",
}


def _enum_tail(value: Any) -> str:
    return str(value or "").strip().lower().rsplit(".", 1)[-1]


@dataclass(frozen=True)
class BrokerOrderEvidence:
    """Identity-checked broker truth for one immutable WAL intent."""

    broker_order_id: str
    status: str
    filled_quantity: float
    fill_price: float | None
    terminal: bool


@dataclass(frozen=True)
class EconomicReconciliationProof:
    """Validated account-state transition sealed into WAL resolution detail."""

    plan_id: str
    plan_hash: str
    starting_state_hash: str
    final_state_hash: str
    reconciled_at: str
    final_positions: tuple[Mapping[str, Any], ...]
    final_cash: float
    paper_drill_epoch: str
    reconciliation_status: str
    broker_fills: tuple[Mapping[str, Any], ...]


_BROKER_OBSERVATION_SCHEMA = "caerus.submission_wal_broker_observation.v1"


def broker_observation_detail(
    intent: OrderIntent,
    evidence: BrokerOrderEvidence,
) -> str:
    """Return the canonical append-only broker observation payload."""

    return json.dumps(
        {
            "schema_version": _BROKER_OBSERVATION_SCHEMA,
            **canonical_broker_fill_evidence(intent, evidence),
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def durable_broker_observations(
    intent: OrderIntent,
    events: Sequence[ResolutionEvent],
) -> tuple[BrokerOrderEvidence, ...]:
    """Validate the immutable sequence of broker fill observations.

    A broker may advance a partial fill and its VWAP, but it may never erase a
    quantity already observed, change identity, or regress from a terminal
    state.  These durable observations protect against a later stale broker
    lookup combined with a lagging account snapshot.
    """

    observations: list[BrokerOrderEvidence] = []
    for event in events:
        if event.state is not ResolutionState.BROKER_OBSERVED:
            continue
        try:
            payload = json.loads(event.detail)
        except (TypeError, json.JSONDecodeError) as exc:
            raise WalPersistenceError(
                "broker observation WAL detail is malformed"
            ) from exc
        if not isinstance(payload, Mapping):
            raise WalPersistenceError("broker observation WAL detail is malformed")
        if payload.get("schema_version") != _BROKER_OBSERVATION_SCHEMA:
            raise WalPersistenceError("broker observation WAL schema is invalid")
        canonical_client_id = str(payload.get("client_order_id") or "").strip()
        canonical_broker_id = str(payload.get("broker_order_id") or "").strip()
        canonical_symbol = str(payload.get("symbol") or "").strip().upper()
        canonical_side = str(payload.get("side") or "").strip().upper()
        canonical_status = _enum_tail(payload.get("status"))
        try:
            order_quantity = float(payload.get("order_quantity"))
            filled_quantity = float(payload.get("filled_quantity"))
        except (TypeError, ValueError) as exc:
            raise WalPersistenceError(
                "broker observation WAL economics are malformed"
            ) from exc
        raw_fill_price = payload.get("fill_price")
        fill_price: float | None = None
        if raw_fill_price is not None:
            try:
                fill_price = float(raw_fill_price)
            except (TypeError, ValueError) as exc:
                raise WalPersistenceError(
                    "broker observation WAL economics are malformed"
                ) from exc
        terminal = canonical_status in _TERMINAL_BROKER_ORDER_STATUSES
        if (
            canonical_client_id != intent.client_order_id
            or canonical_broker_id != str(event.broker_order_id or "").strip()
            or canonical_symbol != intent.symbol.strip().upper()
            or canonical_side != intent.side.strip().upper()
            or not math.isfinite(order_quantity)
            or abs(order_quantity - float(intent.quantity)) > 1e-9
            or not canonical_status
            or not math.isfinite(filled_quantity)
            or filled_quantity < 0
            or filled_quantity > float(intent.quantity) + 1e-9
            or (canonical_status == "filled" and abs(
                filled_quantity - float(intent.quantity)
            ) > 1e-9)
            or (
                filled_quantity > 1e-12
                and (
                    fill_price is None
                    or not math.isfinite(fill_price)
                    or fill_price <= 0
                )
            )
            or (filled_quantity <= 1e-12 and fill_price is not None)
        ):
            raise WalPersistenceError(
                "broker observation WAL identity or economics are invalid"
            )
        if filled_quantity > 1e-12 and intent.order_type.strip().lower() == "limit":
            limit_price = float(intent.limit_price or 0.0)
            price_tolerance = max(1e-9, abs(limit_price) * 1e-12)
            if (
                not math.isfinite(limit_price)
                or limit_price <= 0
                or (
                    canonical_side == "BUY"
                    and float(fill_price) > limit_price + price_tolerance
                )
                or (
                    canonical_side == "SELL"
                    and float(fill_price) < limit_price - price_tolerance
                )
            ):
                raise WalPersistenceError(
                    "broker observation WAL violates durable limit order"
                )
        current = BrokerOrderEvidence(
            broker_order_id=canonical_broker_id,
            status=canonical_status,
            filled_quantity=filled_quantity,
            fill_price=fill_price,
            terminal=terminal,
        )
        if observations:
            prior = observations[-1]
            quantity_tolerance = 1e-9
            if current.broker_order_id != prior.broker_order_id:
                raise WalPersistenceError(
                    "broker observation WAL order id changed"
                )
            if current.filled_quantity + quantity_tolerance < prior.filled_quantity:
                raise WalPersistenceError(
                    "broker observation WAL filled quantity regressed"
                )
            if prior.terminal and current != prior:
                raise WalPersistenceError(
                    "broker observation WAL regressed after terminal status"
                )
            if (
                abs(current.filled_quantity - prior.filled_quantity)
                <= quantity_tolerance
                and current.filled_quantity > 1e-12
            ):
                price_tolerance = max(
                    1e-9,
                    abs(float(prior.fill_price or 0.0)) * 1e-12,
                )
                if abs(float(current.fill_price) - float(prior.fill_price)) > price_tolerance:
                    raise WalPersistenceError(
                        "broker observation WAL fill price changed without quantity"
                    )
            elif current.filled_quantity > prior.filled_quantity + quantity_tolerance:
                prior_notional = prior.filled_quantity * float(prior.fill_price or 0.0)
                current_notional = current.filled_quantity * float(current.fill_price or 0.0)
                incremental_quantity = current.filled_quantity - prior.filled_quantity
                incremental_price = (current_notional - prior_notional) / incremental_quantity
                if not math.isfinite(incremental_price) or incremental_price <= 0:
                    raise WalPersistenceError(
                        "broker observation WAL incremental fill economics are invalid"
                    )
        observations.append(current)
    return tuple(observations)


def validate_broker_order_evidence(
    intent: OrderIntent,
    observed: Mapping[str, Any],
    *,
    resolution_events: Sequence[ResolutionEvent] = (),
) -> BrokerOrderEvidence:
    """Validate stable broker identity and fill economics against an intent.

    Broker-owned fields are checked before any caller overlays the immutable
    exact-order fields.  A terminal ``filled`` status is never inferred from
    status text alone: the broker must report the exact authorized quantity and
    a finite positive fill price.
    """

    if not isinstance(observed, Mapping):
        raise WalPersistenceError(
            "broker client-order-id lookup returned malformed data"
        )
    observed_client_id = str(observed.get("client_order_id") or "").strip()
    if observed_client_id != intent.client_order_id:
        raise WalPersistenceError(
            "broker client-order-id lookup returned mismatched order identity"
        )
    observed_broker_id = str(
        observed.get("id") or observed.get("order_id") or ""
    ).strip()
    if not observed_broker_id:
        raise WalPersistenceError(
            "broker client-order-id lookup returned no broker order id"
        )
    durable_broker_ids = {
        str(event.broker_order_id or "").strip()
        for event in resolution_events
        if str(event.broker_order_id or "").strip()
    }
    if len(durable_broker_ids) > 1 or (
        durable_broker_ids and observed_broker_id not in durable_broker_ids
    ):
        raise WalPersistenceError(
            "broker lookup order id conflicts with durable WAL resolution"
        )

    observed_symbol = str(observed.get("symbol") or "").strip().upper()
    observed_side = _enum_tail(observed.get("side")).upper()
    raw_quantity = observed.get("qty", observed.get("quantity"))
    try:
        observed_quantity = float(raw_quantity)
    except (TypeError, ValueError) as exc:
        raise WalPersistenceError(
            "broker lookup order quantity is missing or invalid"
        ) from exc
    if (
        observed_symbol != intent.symbol.strip().upper()
        or observed_side != intent.side.strip().upper()
        or not math.isfinite(observed_quantity)
        or abs(observed_quantity - float(intent.quantity)) > 1e-9
    ):
        raise WalPersistenceError(
            "broker lookup order economics conflict with durable WAL intent"
        )

    status = _enum_tail(observed.get("status"))
    if not status:
        raise WalPersistenceError("broker lookup order status is missing")
    if "filled_qty" in observed:
        filled_raw = observed.get("filled_qty")
    elif "filled_quantity" in observed:
        filled_raw = observed.get("filled_quantity")
    else:
        filled_raw = None
    if filled_raw in (None, ""):
        raise WalPersistenceError("broker lookup filled quantity is missing")
    try:
        filled_quantity = float(filled_raw)
    except (TypeError, ValueError) as exc:
        raise WalPersistenceError("broker lookup filled quantity is invalid") from exc
    if not math.isfinite(filled_quantity) or filled_quantity < 0:
        raise WalPersistenceError("broker lookup filled quantity is invalid")
    if filled_quantity > float(intent.quantity) + 1e-9:
        raise WalPersistenceError(
            "broker lookup filled quantity exceeds durable WAL intent"
        )
    if status == "filled" and abs(
        filled_quantity - float(intent.quantity)
    ) > 1e-9:
        raise WalPersistenceError(
            "broker filled status conflicts with durable WAL quantity"
        )

    fill_price: float | None = None
    if filled_quantity > 1e-12:
        raw_fill_price = observed.get("filled_avg_price")
        if raw_fill_price in (None, ""):
            raw_fill_price = observed.get("fill_price")
        try:
            fill_price = float(raw_fill_price)
        except (TypeError, ValueError) as exc:
            raise WalPersistenceError(
                "broker filled order price is missing or invalid"
            ) from exc
        if not math.isfinite(fill_price) or fill_price <= 0:
            raise WalPersistenceError(
                "broker filled order price is missing or invalid"
            )
        if intent.order_type.strip().lower() == "limit":
            limit_price = float(intent.limit_price or 0.0)
            if not math.isfinite(limit_price) or limit_price <= 0:
                raise WalPersistenceError(
                    "durable limit order lacks a valid limit price"
                )
            price_tolerance = max(1e-9, abs(limit_price) * 1e-12)
            if (
                intent.side.strip().upper() == "BUY"
                and fill_price > limit_price + price_tolerance
            ) or (
                intent.side.strip().upper() == "SELL"
                and fill_price < limit_price - price_tolerance
            ):
                raise WalPersistenceError(
                    "broker fill price violates durable limit order"
                )
    durable_observations = durable_broker_observations(intent, resolution_events)
    legacy_material_fill_statuses = {
        _enum_tail(event.detail)
        for event in resolution_events
        if event.state in {
            ResolutionState.SUBMITTED,
            ResolutionState.RECOVERED_BY_LOOKUP,
        }
        and _enum_tail(event.detail) in {"filled", "partially_filled"}
    }
    if (
        not durable_observations
        and legacy_material_fill_statuses
        and filled_quantity <= 1e-12
    ):
        raise WalPersistenceError(
            "broker lookup erased a material fill recorded by legacy WAL evidence"
        )
    if (
        not durable_observations
        and "filled" in legacy_material_fill_statuses
        and abs(filled_quantity - float(intent.quantity)) > 1e-9
    ):
        raise WalPersistenceError(
            "broker lookup conflicts with legacy filled WAL evidence"
        )
    if durable_observations:
        prior = durable_observations[-1]
        quantity_tolerance = 1e-9
        if observed_broker_id != prior.broker_order_id:
            raise WalPersistenceError(
                "broker lookup order id conflicts with durable broker observation"
            )
        if filled_quantity + quantity_tolerance < prior.filled_quantity:
            raise WalPersistenceError(
                "broker lookup filled quantity regressed below durable observation"
            )
        if prior.terminal and (
            status != prior.status
            or abs(filled_quantity - prior.filled_quantity) > quantity_tolerance
            or fill_price != prior.fill_price
        ):
            raise WalPersistenceError(
                "broker lookup regressed after durable terminal observation"
            )
        if (
            abs(filled_quantity - prior.filled_quantity) <= quantity_tolerance
            and filled_quantity > 1e-12
        ):
            price_tolerance = max(
                1e-9,
                abs(float(prior.fill_price or 0.0)) * 1e-12,
            )
            if abs(float(fill_price) - float(prior.fill_price)) > price_tolerance:
                raise WalPersistenceError(
                    "broker lookup fill price conflicts with durable observation"
                )
    result = BrokerOrderEvidence(
        broker_order_id=observed_broker_id,
        status=status,
        filled_quantity=filled_quantity,
        fill_price=fill_price,
        terminal=status in _TERMINAL_BROKER_ORDER_STATUSES,
    )
    proof = economic_reconciliation_proof(intent, resolution_events)
    if proof is not None:
        sealed_fill = next(
            (
                row
                for row in proof.broker_fills
                if row["client_order_id"] == intent.client_order_id
            ),
            None,
        )
        if sealed_fill != canonical_broker_fill_evidence(intent, result):
            raise WalPersistenceError(
                "broker lookup fill evidence conflicts with durable economic reconciliation"
            )
    return result


def canonical_broker_fill_evidence(
    intent: OrderIntent,
    evidence: BrokerOrderEvidence,
) -> dict[str, Any]:
    return {
        "client_order_id": intent.client_order_id,
        "broker_order_id": evidence.broker_order_id,
        "symbol": intent.symbol.strip().upper(),
        "side": intent.side.strip().upper(),
        "order_quantity": float(intent.quantity),
        "filled_quantity": float(evidence.filled_quantity),
        "fill_price": (
            float(evidence.fill_price)
            if evidence.fill_price is not None
            else None
        ),
        "status": evidence.status,
    }


def economic_reconciliation_proof(
    intent: OrderIntent,
    events: Sequence[ResolutionEvent],
) -> EconomicReconciliationProof | None:
    """Return a fully validated latest economic reconciliation, if present."""

    reconciled_indexes = [
        index
        for index, event in enumerate(events)
        if event.state is ResolutionState.ECONOMICALLY_RECONCILED
    ]
    if not reconciled_indexes:
        return None
    latest_index = reconciled_indexes[-1]
    # A later stable-ID recovery observation does not undo an already proven
    # account-state transition. Ambiguous or contradictory later resolution
    # states do invalidate the proof and require operator resolution.
    if any(
        event.state
        not in {
            ResolutionState.RECOVERED_BY_LOOKUP,
            ResolutionState.BROKER_OBSERVED,
            ResolutionState.ECONOMICALLY_RECONCILED,
        }
        for event in events[latest_index + 1 :]
    ):
        return None
    latest = events[latest_index]
    try:
        payload = json.loads(latest.detail)
    except (TypeError, json.JSONDecodeError) as exc:
        raise WalPersistenceError(
            "economic reconciliation WAL detail is malformed"
        ) from exc
    if not isinstance(payload, Mapping):
        raise WalPersistenceError("economic reconciliation WAL detail is malformed")
    starting_state_hash = str(payload.get("starting_state_hash") or "")
    final_state_hash = str(payload.get("final_state_hash") or "")
    reconciled_at = str(payload.get("reconciled_at") or "")
    raw_positions = payload.get("final_positions")
    raw_cash = payload.get("final_cash")
    raw_broker_fills = payload.get("broker_fills")
    if (
        payload.get("schema_version")
        != "caerus.submission_wal_economic_reconciliation.v1"
        or str(payload.get("plan_id") or "") != intent.plan_id
        or str(payload.get("plan_hash") or "") != intent.plan_hash
        or not _SHA256.fullmatch(starting_state_hash)
        or not _SHA256.fullmatch(final_state_hash)
        or not isinstance(raw_positions, list)
        or not isinstance(raw_broker_fills, list)
        or not _finite(raw_cash)
        or float(raw_cash) < 0
    ):
        raise WalPersistenceError(
            "economic reconciliation WAL lineage is invalid"
        )
    if intent.starting_state_hash and starting_state_hash != intent.starting_state_hash:
        raise WalPersistenceError(
            "economic reconciliation WAL starting state conflicts with intent"
        )
    paper_drill_epoch = str(payload.get("paper_drill_epoch") or "")
    if paper_drill_epoch != str(intent.paper_drill_epoch or ""):
        raise WalPersistenceError(
            "economic reconciliation WAL epoch conflicts with intent"
        )
    try:
        parsed_at = datetime.fromisoformat(reconciled_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WalPersistenceError(
            "economic reconciliation WAL timestamp is invalid"
        ) from exc
    if parsed_at.tzinfo is None:
        raise WalPersistenceError(
            "economic reconciliation WAL timestamp is invalid"
        )
    try:
        canonical_positions = tuple(dict(row) for row in raw_positions)
        recomputed = compute_starting_state_hash(
            canonical_positions,
            float(raw_cash),
        )
    except Exception as exc:
        raise WalPersistenceError(
            "economic reconciliation WAL final state is malformed"
        ) from exc
    if recomputed != final_state_hash:
        raise WalPersistenceError(
            "economic reconciliation WAL final state hash mismatch"
        )
    reconciliation_status = str(
        payload.get("reconciliation_status") or ""
    ).strip()
    if reconciliation_status not in {
        "CLEAN",
        "TERMINAL_FAILURE_STATE_RECONCILED",
    }:
        raise WalPersistenceError(
            "economic reconciliation WAL status is invalid"
        )
    broker_fills: list[dict[str, Any]] = []
    seen_client_ids: set[str] = set()
    for raw_fill in raw_broker_fills:
        if not isinstance(raw_fill, Mapping):
            raise WalPersistenceError(
                "economic reconciliation WAL broker fill is malformed"
            )
        client_order_id = str(raw_fill.get("client_order_id") or "").strip()
        broker_order_id = str(raw_fill.get("broker_order_id") or "").strip()
        symbol = str(raw_fill.get("symbol") or "").strip().upper()
        side = str(raw_fill.get("side") or "").strip().upper()
        status = _enum_tail(raw_fill.get("status"))
        try:
            order_quantity = float(raw_fill.get("order_quantity"))
            filled_quantity = float(raw_fill.get("filled_quantity"))
        except (TypeError, ValueError) as exc:
            raise WalPersistenceError(
                "economic reconciliation WAL broker fill is malformed"
            ) from exc
        raw_fill_price = raw_fill.get("fill_price")
        fill_price = None
        if raw_fill_price is not None:
            try:
                fill_price = float(raw_fill_price)
            except (TypeError, ValueError) as exc:
                raise WalPersistenceError(
                    "economic reconciliation WAL broker fill is malformed"
                ) from exc
        if (
            not client_order_id
            or client_order_id in seen_client_ids
            or not broker_order_id
            or not symbol
            or side not in {"BUY", "SELL"}
            or status not in _TERMINAL_BROKER_ORDER_STATUSES
            or not math.isfinite(order_quantity)
            or order_quantity <= 0
            or not math.isfinite(filled_quantity)
            or filled_quantity < 0
            or filled_quantity > order_quantity + 1e-9
            or (
                filled_quantity > 1e-12
                and (
                    fill_price is None
                    or not math.isfinite(fill_price)
                    or fill_price <= 0
                )
            )
            or (filled_quantity <= 1e-12 and fill_price is not None)
        ):
            raise WalPersistenceError(
                "economic reconciliation WAL broker fill is malformed"
            )
        seen_client_ids.add(client_order_id)
        broker_fills.append(
            {
                "client_order_id": client_order_id,
                "broker_order_id": broker_order_id,
                "symbol": symbol,
                "side": side,
                "order_quantity": order_quantity,
                "filled_quantity": filled_quantity,
                "fill_price": fill_price,
                "status": status,
            }
        )
    current_fill = next(
        (
            row
            for row in broker_fills
            if row["client_order_id"] == intent.client_order_id
        ),
        None,
    )
    if (
        current_fill is None
        or current_fill["broker_order_id"] != str(latest.broker_order_id or "")
        or current_fill["symbol"] != intent.symbol.strip().upper()
        or current_fill["side"] != intent.side.strip().upper()
        or abs(current_fill["order_quantity"] - float(intent.quantity)) > 1e-9
    ):
        raise WalPersistenceError(
            "economic reconciliation WAL broker fill lineage is invalid"
        )
    durable_observations = durable_broker_observations(intent, events)
    if durable_observations:
        latest_observation = canonical_broker_fill_evidence(
            intent,
            durable_observations[-1],
        )
        if latest_observation != current_fill:
            raise WalPersistenceError(
                "economic reconciliation WAL conflicts with durable broker observation"
            )
    return EconomicReconciliationProof(
        plan_id=intent.plan_id,
        plan_hash=intent.plan_hash,
        starting_state_hash=starting_state_hash,
        final_state_hash=final_state_hash,
        reconciled_at=reconciled_at,
        final_positions=canonical_positions,
        final_cash=float(raw_cash),
        paper_drill_epoch=paper_drill_epoch,
        reconciliation_status=reconciliation_status,
        broker_fills=tuple(
            sorted(broker_fills, key=lambda row: row["client_order_id"])
        ),
    )


def prior_intent_has_terminal_broker_evidence(
    wal_root: Path | str,
    *,
    trade_date: str,
    client_order_id: str,
    lookup_by_client_order_id: Callable[[str], Mapping[str, Any] | None],
    current_state_hash: str | None = None,
) -> bool:
    """Prove a prior WAL intent can no longer mutate economically.

    ``unresolved_intent_requires_lookup`` answers a narrower replay question:
    whether the *same* immutable intent must be looked up before replay.  A
    durable ``SUBMITTED`` event is sufficient for that purpose, but it is not
    proof that a later drill epoch may submit another economic order.  This
    helper is deliberately stricter.  Except for a durably rejected intent, it
    requires a fresh stable-client-ID broker lookup and accepts only a terminal
    broker status.  Missing, malformed, accepted, pending, or partially filled
    orders remain unresolved and therefore block a new namespace.
    """

    path = intent_path(
        wal_root,
        trade_date=trade_date,
        client_order_id=client_order_id,
    )
    if not path.exists():
        return False
    intent = OrderIntent.from_dict(json.loads(path.read_text(encoding="utf-8")))
    events = read_resolutions(
        wal_root,
        trade_date=trade_date,
        client_order_id=client_order_id,
    )
    if not callable(lookup_by_client_order_id):
        raise WalPersistenceError("broker lacks stable client-order-id lookup")
    observed = lookup_by_client_order_id(intent.client_order_id)
    if observed is None:
        return False
    evidence = validate_broker_order_evidence(
        intent,
        observed,
        resolution_events=events,
    )
    if not evidence.terminal:
        return False
    if evidence.filled_quantity <= 1e-12:
        return True
    proof = economic_reconciliation_proof(intent, events)
    if proof is None:
        return False
    if current_state_hash is None:
        return True
    return str(current_state_hash) == proof.final_state_hash


def _namespace_epoch(base_root: Path, candidate_root: Path) -> str:
    try:
        relative = candidate_root.resolve().relative_to(base_root.resolve())
    except ValueError as exc:
        raise WalPersistenceError(
            "submission WAL namespace is outside its canonical base"
        ) from exc
    if not relative.parts:
        return ""
    if len(relative.parts) == 2 and relative.parts[0] == "epochs":
        epoch = relative.parts[1]
        if _PAPER_DRILL_EPOCH.fullmatch(epoch):
            return epoch
    raise WalPersistenceError("submission WAL namespace is malformed")


def unresolved_foreign_intent_client_ids(
    base_wal_root: Path | str,
    *,
    current_wal_root: Path | str,
    trade_date: str,
    lookup_by_client_order_id: Callable[[str], Mapping[str, Any] | None],
    current_state_hash: str | None = None,
) -> list[str]:
    """Return unresolved intent IDs from every other same-date WAL namespace.

    Lookup outages are conservatively classified as unresolved. Integrity
    failures (bad hashes, mismatched broker identity, malformed evidence) raise
    so callers can distinguish corrupt evidence from an ordinary open order.
    """

    base_root = Path(base_wal_root).expanduser().resolve()
    current_root = Path(current_wal_root).expanduser().resolve()
    current_epoch = _namespace_epoch(base_root, current_root)
    unresolved: set[str] = set()
    plan_groups: dict[
        tuple[str, str, str],
        dict[str, Any],
    ] = {}
    for candidate in base_root.glob(f"**/{_date(trade_date)}/intents/*.json"):
        candidate_root = candidate.parents[2]
        if candidate_root.resolve() == current_root:
            continue
        namespace_epoch = _namespace_epoch(base_root, candidate_root)
        if (
            (not current_epoch and namespace_epoch)
            or (
                current_epoch
                and namespace_epoch
                and namespace_epoch > current_epoch
            )
        ):
            raise WalPersistenceError(
                "paper drill epoch order is not monotonic"
            )
        try:
            intent = OrderIntent.from_dict(
                json.loads(candidate.read_text(encoding="utf-8"))
            )
            if str(intent.paper_drill_epoch or "") != namespace_epoch:
                raise WalPersistenceError(
                    "submission WAL intent epoch conflicts with namespace"
                )
            events = read_resolutions(
                candidate_root,
                trade_date=trade_date,
                client_order_id=intent.client_order_id,
            )
        except WalPersistenceError:
            raise
        except Exception as exc:
            raise WalPersistenceError(
                f"submission WAL intent or resolution integrity failed: {exc}"
            ) from exc
        if not callable(lookup_by_client_order_id):
            raise WalPersistenceError("broker lacks stable client-order-id lookup")
        try:
            observed = lookup_by_client_order_id(intent.client_order_id)
        except Exception:
            unresolved.add(intent.client_order_id)
            continue
        if observed is None:
            unresolved.add(intent.client_order_id)
            continue
        evidence = validate_broker_order_evidence(
            intent,
            observed,
            resolution_events=events,
        )
        if not evidence.terminal:
            unresolved.add(intent.client_order_id)
            continue
        proof = economic_reconciliation_proof(intent, events)
        if evidence.filled_quantity > 1e-12 and proof is None:
            unresolved.add(intent.client_order_id)
            continue
        group_key = (namespace_epoch, intent.plan_id, intent.plan_hash)
        group = plan_groups.setdefault(
            group_key,
            {
                "namespace_epoch": namespace_epoch,
                "candidate_root": candidate_root.resolve(),
                "proof": None,
                "broker_fills": {},
            },
        )
        if group["candidate_root"] != candidate_root.resolve():
            raise WalPersistenceError(
                "submission WAL plan spans multiple namespaces"
            )
        if proof is not None:
            prior = group.get("proof")
            if prior is not None and prior != proof:
                raise WalPersistenceError(
                    "economic reconciliation WAL plan proof is inconsistent"
                )
            group["proof"] = proof
        canonical_fill = canonical_broker_fill_evidence(intent, evidence)
        prior_fill = group["broker_fills"].setdefault(
            intent.client_order_id,
            canonical_fill,
        )
        if prior_fill != canonical_fill:
            raise WalPersistenceError(
                "broker fill evidence is inconsistent within a plan"
            )

    plans_by_namespace: dict[str, tuple[str, str]] = {}
    for namespace_epoch, plan_id, plan_hash in plan_groups:
        plan_identity = (plan_id, plan_hash)
        prior_identity = plans_by_namespace.setdefault(
            namespace_epoch,
            plan_identity,
        )
        if prior_identity != plan_identity:
            raise WalPersistenceError(
                "submission WAL namespace contains multiple plans"
            )
    reconciled_plans = [
        group
        for group in plan_groups.values()
        if group.get("proof") is not None
    ]
    for group in reconciled_plans:
        observed_fills = tuple(
            sorted(
                group["broker_fills"].values(),
                key=lambda row: row["client_order_id"],
            )
        )
        if observed_fills != group["proof"].broker_fills:
            raise WalPersistenceError(
                "broker fill evidence conflicts with economic reconciliation"
            )
    if reconciled_plans:
        ordered = sorted(
            reconciled_plans,
            key=lambda group: (
                0 if not group["namespace_epoch"] else 1,
                group["namespace_epoch"],
            ),
        )
        for previous, following in zip(ordered, ordered[1:]):
            previous_proof = previous["proof"]
            following_proof = following["proof"]
            if (
                following_proof.starting_state_hash
                != previous_proof.final_state_hash
            ):
                raise WalPersistenceError(
                    "economic reconciliation WAL state chain is discontinuous"
                )
        if (
            not current_state_hash
            or ordered[-1]["proof"].final_state_hash != str(current_state_hash)
        ):
            unresolved.add("prior_epoch_reconciled_state_not_observed")
    return sorted(unresolved)


def new_resolution(
    *,
    resolution_id: str,
    intent: OrderIntent,
    state: ResolutionState,
    broker_order_id: str | None = None,
    detail: str = "",
    recorded_at: str | None = None,
) -> ResolutionEvent:
    """Convenience constructor retaining immutable intent lineage."""

    hashed = intent if intent.content_hash else intent.with_content_hash()
    return ResolutionEvent(
        resolution_id=resolution_id,
        trade_date=hashed.trade_date,
        client_order_id=hashed.client_order_id,
        intent_hash=hashed.content_hash,
        state=state,
        recorded_at=recorded_at or _utc_now(),
        broker_order_id=broker_order_id,
        detail=detail,
    )

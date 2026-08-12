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
from typing import Any, Mapping


INTENT_SCHEMA_VERSION = "caerus.submission_wal_intent.v1"
RESOLUTION_SCHEMA_VERSION = "caerus.submission_wal_resolution.v1"

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_TRADE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class WalPersistenceError(RuntimeError):
    """Raised when the durable pre-submit write cannot be proven."""


class ResolutionState(str, Enum):
    SUBMITTED = "SUBMITTED"
    RECOVERED_BY_LOOKUP = "RECOVERED_BY_LOOKUP"
    REJECTED = "REJECTED"
    SUBMISSION_UNKNOWN = "SUBMISSION_UNKNOWN"


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
        if self.state in {ResolutionState.SUBMITTED, ResolutionState.RECOVERED_BY_LOOKUP}:
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

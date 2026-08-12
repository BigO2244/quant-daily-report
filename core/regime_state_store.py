"""Append-only Choice 2 regime authority state.

The store is scoped by account, capital scope, and sleeve.  Every authorization
observation becomes an immutable, content-hashed event linked to its predecessor.
There is no mutable "latest" state: the effective state is reconstructed from the
validated chain, and any missing, malformed, or semantically inconsistent event
fails closed.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from core.regime_authority import RegimeAuthorityDecision, decide_regime_authority


REGIME_EVENT_SCHEMA_VERSION = "caerus.regime_authority_event.v2"
_SAFE_SCOPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
_TRADE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class RegimeStateError(RuntimeError):
    """Base class for fail-closed persistent regime-state errors."""


class RegimeStateCorruptionError(RegimeStateError):
    """Raised when persisted regime history cannot be trusted."""


class RegimeStateConflictError(RegimeStateError):
    """Raised when an authorization run ID is reused with different inputs."""


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


def _safe_scope(label: str, value: str) -> str:
    normalized = str(value or "").strip()
    if not _SAFE_SCOPE.fullmatch(normalized) or ".." in normalized:
        raise ValueError(f"invalid {label}: {value!r}")
    return normalized


def _normalized_state(value: Any) -> str:
    state = str(value or "UNKNOWN").strip().upper()
    return state or "UNKNOWN"


def _confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("regime confidence must be numeric") from exc
    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        raise ValueError("regime confidence must be within [0, 1]")
    return confidence


def _strict_bool(value: Any, *, label: str) -> bool:
    if not isinstance(value, bool):
        raise RegimeStateCorruptionError(f"{label} must be boolean")
    return value


def _account_directory(
    state_root: Path | str,
    *,
    account_scope: str,
    account_id: str,
    sleeve_id: str,
) -> Path:
    scope = _safe_scope("account_scope", account_scope).upper()
    sleeve = _safe_scope("sleeve_id", sleeve_id).lower()
    account = str(account_id or "").strip()
    if not account:
        raise ValueError("account_id is required")
    account_key = hashlib.sha256(account.encode("utf-8")).hexdigest()[:24]
    return Path(state_root) / scope / sleeve / account_key


@dataclass(frozen=True)
class RegimeAuthorityEvent:
    sequence: int
    account_scope: str
    account_id: str
    sleeve_id: str
    observation_id: str
    authorization_run_id: str
    trade_date: str
    recorded_at: str
    observed_state: str
    confidence: float
    acute_risk: bool
    previous_state: str
    effective_state: str
    action: str
    reason_code: str
    decision_bars_in_state: int
    bars_in_effective_state: int
    consecutive_observations: int
    minimum_dwell_bars: int
    confirmation_bars: int
    confidence_threshold: float
    emergency_response: bool
    risk_veto_buys: bool
    risk_package_id: str
    risk_package_hash: str
    market_state_id: str
    previous_event_hash: str | None
    content_hash: str = ""

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema_version"] = REGIME_EVENT_SCHEMA_VERSION
        if not include_hash:
            payload.pop("content_hash", None)
        return payload

    def with_content_hash(self) -> "RegimeAuthorityEvent":
        return replace(self, content_hash=_content_hash(self.to_dict(include_hash=False)))

    def to_decision(self) -> RegimeAuthorityDecision:
        return RegimeAuthorityDecision(
            previous_state=self.previous_state,
            observed_state=self.observed_state,
            effective_state=self.effective_state,
            action=self.action,
            reason_code=self.reason_code,
            confidence=self.confidence,
            bars_in_state=self.decision_bars_in_state,
            consecutive_observations=self.consecutive_observations,
            minimum_dwell_bars=self.minimum_dwell_bars,
            confirmation_bars=self.confirmation_bars,
            emergency_response=self.emergency_response,
            risk_veto_buys=self.risk_veto_buys,
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RegimeAuthorityEvent":
        if payload.get("schema_version") != REGIME_EVENT_SCHEMA_VERSION:
            raise RegimeStateCorruptionError("unsupported regime event schema")
        try:
            event = cls(
                sequence=int(payload.get("sequence") or 0),
                account_scope=str(payload.get("account_scope") or ""),
                account_id=str(payload.get("account_id") or ""),
                sleeve_id=str(payload.get("sleeve_id") or ""),
                observation_id=str(payload.get("observation_id") or ""),
                authorization_run_id=str(payload.get("authorization_run_id") or ""),
                trade_date=str(payload.get("trade_date") or ""),
                recorded_at=str(payload.get("recorded_at") or ""),
                observed_state=_normalized_state(payload.get("observed_state")),
                confidence=_confidence(payload.get("confidence")),
                acute_risk=_strict_bool(payload.get("acute_risk"), label="acute_risk"),
                previous_state=_normalized_state(payload.get("previous_state")),
                effective_state=_normalized_state(payload.get("effective_state")),
                action=str(payload.get("action") or ""),
                reason_code=str(payload.get("reason_code") or ""),
                decision_bars_in_state=int(payload.get("decision_bars_in_state") or 0),
                bars_in_effective_state=int(payload.get("bars_in_effective_state") or 0),
                consecutive_observations=int(payload.get("consecutive_observations") or 0),
                minimum_dwell_bars=int(payload.get("minimum_dwell_bars") or 0),
                confirmation_bars=int(payload.get("confirmation_bars") or 0),
                confidence_threshold=_confidence(payload.get("confidence_threshold")),
                emergency_response=_strict_bool(
                    payload.get("emergency_response"),
                    label="emergency_response",
                ),
                risk_veto_buys=_strict_bool(
                    payload.get("risk_veto_buys"),
                    label="risk_veto_buys",
                ),
                risk_package_id=str(payload.get("risk_package_id") or ""),
                risk_package_hash=str(payload.get("risk_package_hash") or ""),
                market_state_id=str(payload.get("market_state_id") or ""),
                previous_event_hash=(
                    str(payload["previous_event_hash"])
                    if payload.get("previous_event_hash") is not None
                    else None
                ),
                content_hash=str(payload.get("content_hash") or ""),
            )
        except (TypeError, ValueError, OverflowError) as exc:
            raise RegimeStateCorruptionError(f"invalid regime event: {exc}") from exc
        if event.sequence < 1:
            raise RegimeStateCorruptionError("regime event sequence must be positive")
        if (
            not event.observation_id
            or not event.authorization_run_id
            or not event.trade_date
            or not event.recorded_at
        ):
            raise RegimeStateCorruptionError("regime event identity fields are required")
        if not _SHA256.fullmatch(event.observation_id):
            raise RegimeStateCorruptionError("regime event observation_id is invalid")
        if not _TRADE_DATE.fullmatch(event.trade_date):
            raise RegimeStateCorruptionError("regime event trade_date is invalid")
        if not event.risk_package_id or not event.risk_package_hash:
            raise RegimeStateCorruptionError("regime event Risk authority binding is required")
        if not _SHA256.fullmatch(event.risk_package_hash):
            raise RegimeStateCorruptionError("regime event Risk authority hash is invalid")
        if event.content_hash != _content_hash(event.to_dict(include_hash=False)):
            raise RegimeStateCorruptionError("regime event content hash mismatch")
        return event


@dataclass(frozen=True)
class RegimePersistenceResult:
    event: RegimeAuthorityEvent
    event_path: Path
    created: bool
    committed: bool

    def regime_state(self) -> dict[str, Any]:
        payload = self.event.to_decision().to_dict()
        payload.update(
            {
                "state_schema_version": REGIME_EVENT_SCHEMA_VERSION,
                "state_sequence": self.event.sequence,
                "state_event_hash": self.event.content_hash,
                "state_event_path": str(self.event_path),
                "state_observation_id": self.event.observation_id,
                "state_committed_at_evaluation": self.committed,
                "state_commit_required_before_pointer": not self.committed,
                "bars_in_effective_state": self.event.bars_in_effective_state,
                "risk_package_id": self.event.risk_package_id,
                "risk_package_hash": self.event.risk_package_hash,
                "market_state_id": self.event.market_state_id,
                "bootstrap": self.event.sequence == 1,
            }
        )
        return payload


def _event_path(events_dir: Path, event: RegimeAuthorityEvent) -> Path:
    return events_dir / f"{event.sequence:08d}-{event.observation_id[:16]}.json"


def _observation_id(
    *,
    account_scope: str,
    account_id: str,
    sleeve_id: str,
    trade_date: str,
    market_state_id: str,
) -> str:
    return hashlib.sha256(
        _canonical_json(
            {
                "account_scope": str(account_scope).upper(),
                "account_id": str(account_id),
                "sleeve_id": str(sleeve_id).lower(),
                "trade_date": str(trade_date),
                "market_state_id": str(market_state_id),
            }
        )
    ).hexdigest()


def _expected_event(
    previous: RegimeAuthorityEvent | None,
    *,
    account_scope: str,
    account_id: str,
    sleeve_id: str,
    authorization_run_id: str,
    trade_date: str,
    recorded_at: str,
    observed_state: str,
    confidence: float,
    acute_risk: bool,
    minimum_dwell_bars: int,
    confirmation_bars: int,
    confidence_threshold: float,
    risk_package_id: str,
    risk_package_hash: str,
    market_state_id: str,
) -> RegimeAuthorityEvent:
    observed = _normalized_state(observed_state)
    confidence_value = _confidence(confidence)
    if min(int(minimum_dwell_bars), int(confirmation_bars)) <= 0:
        raise ValueError("regime persistence thresholds must be positive")
    if not authorization_run_id or not trade_date or not recorded_at:
        raise ValueError("authorization run, trade date, and recorded_at are required")
    if not _TRADE_DATE.fullmatch(str(trade_date)):
        raise ValueError("trade_date must be YYYY-MM-DD")
    if not risk_package_id or not risk_package_hash:
        raise ValueError("persisted Risk package identity and hash are required")
    if not _SHA256.fullmatch(str(risk_package_hash)):
        raise ValueError("persisted Risk package hash must be lowercase SHA-256")
    normalized_market_state_id = str(market_state_id or f"risk:{risk_package_id}")
    observation_id = _observation_id(
        account_scope=account_scope,
        account_id=account_id,
        sleeve_id=sleeve_id,
        trade_date=trade_date,
        market_state_id=normalized_market_state_id,
    )

    if previous is None:
        if acute_risk:
            decision = decide_regime_authority(
                previous_state=observed,
                observed_state=observed,
                confidence=confidence_value,
                bars_in_state=1,
                consecutive_observations=1,
                acute_risk=True,
                minimum_dwell_bars=minimum_dwell_bars,
                confirmation_bars=confirmation_bars,
                confidence_threshold=confidence_threshold,
            )
        else:
            decision = RegimeAuthorityDecision(
                previous_state=observed,
                observed_state=observed,
                effective_state=observed,
                action="BOOTSTRAP",
                reason_code="regime_state_bootstrap_no_prior_history",
                confidence=confidence_value,
                bars_in_state=1,
                consecutive_observations=1,
                minimum_dwell_bars=int(minimum_dwell_bars),
                confirmation_bars=int(confirmation_bars),
                emergency_response=False,
                risk_veto_buys=observed == "EMERGENCY_RISK_OFF",
            )
        bars_in_effective_state = 1
        sequence = 1
        previous_hash = None
    else:
        decision_bars = previous.bars_in_effective_state + 1
        consecutive = (
            previous.consecutive_observations + 1
            if observed == previous.observed_state
            else 1
        )
        decision = decide_regime_authority(
            previous_state=previous.effective_state,
            observed_state=observed,
            confidence=confidence_value,
            bars_in_state=decision_bars,
            consecutive_observations=consecutive,
            acute_risk=acute_risk,
            minimum_dwell_bars=minimum_dwell_bars,
            confirmation_bars=confirmation_bars,
            confidence_threshold=confidence_threshold,
        )
        bars_in_effective_state = (
            1
            if decision.effective_state != previous.effective_state
            else decision_bars
        )
        sequence = previous.sequence + 1
        previous_hash = previous.content_hash

    return RegimeAuthorityEvent(
        sequence=sequence,
        account_scope=str(account_scope).upper(),
        account_id=str(account_id),
        sleeve_id=str(sleeve_id).lower(),
        observation_id=observation_id,
        authorization_run_id=str(authorization_run_id),
        trade_date=str(trade_date),
        recorded_at=str(recorded_at),
        observed_state=decision.observed_state,
        confidence=decision.confidence,
        acute_risk=bool(acute_risk),
        previous_state=decision.previous_state,
        effective_state=decision.effective_state,
        action=decision.action,
        reason_code=decision.reason_code,
        decision_bars_in_state=decision.bars_in_state,
        bars_in_effective_state=bars_in_effective_state,
        consecutive_observations=decision.consecutive_observations,
        minimum_dwell_bars=decision.minimum_dwell_bars,
        confirmation_bars=decision.confirmation_bars,
        confidence_threshold=float(confidence_threshold),
        emergency_response=decision.emergency_response,
        risk_veto_buys=decision.risk_veto_buys,
        risk_package_id=str(risk_package_id),
        risk_package_hash=str(risk_package_hash),
        market_state_id=normalized_market_state_id,
        previous_event_hash=previous_hash,
    ).with_content_hash()


def _validate_history(
    events: list[RegimeAuthorityEvent],
    *,
    account_scope: str,
    account_id: str,
    sleeve_id: str,
) -> None:
    previous: RegimeAuthorityEvent | None = None
    seen_runs: set[str] = set()
    seen_observations: set[str] = set()
    for event in events:
        if (
            event.account_scope != account_scope.upper()
            or event.account_id != account_id
            or event.sleeve_id != sleeve_id.lower()
        ):
            raise RegimeStateCorruptionError("regime event identity scope mismatch")
        if event.authorization_run_id in seen_runs:
            raise RegimeStateCorruptionError("duplicate regime authorization run ID")
        if event.observation_id in seen_observations:
            raise RegimeStateCorruptionError("duplicate governed regime observation")
        if previous is not None and event.trade_date < previous.trade_date:
            raise RegimeStateCorruptionError("regime event trade dates move backwards")
        expected = _expected_event(
            previous,
            account_scope=event.account_scope,
            account_id=event.account_id,
            sleeve_id=event.sleeve_id,
            authorization_run_id=event.authorization_run_id,
            trade_date=event.trade_date,
            recorded_at=event.recorded_at,
            observed_state=event.observed_state,
            confidence=event.confidence,
            acute_risk=event.acute_risk,
            minimum_dwell_bars=event.minimum_dwell_bars,
            confirmation_bars=event.confirmation_bars,
            confidence_threshold=event.confidence_threshold,
            risk_package_id=event.risk_package_id,
            risk_package_hash=event.risk_package_hash,
            market_state_id=event.market_state_id,
        )
        if event.to_dict() != expected.to_dict():
            raise RegimeStateCorruptionError("regime event semantic chain mismatch")
        seen_runs.add(event.authorization_run_id)
        seen_observations.add(event.observation_id)
        previous = event


def load_regime_history(
    state_root: Path | str,
    *,
    account_scope: str,
    account_id: str,
    sleeve_id: str,
) -> list[RegimeAuthorityEvent]:
    account_dir = _account_directory(
        state_root,
        account_scope=account_scope,
        account_id=account_id,
        sleeve_id=sleeve_id,
    )
    events_dir = account_dir / "events"
    if not events_dir.exists():
        return []
    paths = sorted(events_dir.glob("*.json"))
    events: list[RegimeAuthorityEvent] = []
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise RegimeStateCorruptionError(
                f"regime event is unreadable: {path}: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise RegimeStateCorruptionError(f"regime event is not an object: {path}")
        event = RegimeAuthorityEvent.from_dict(payload)
        if path != _event_path(events_dir, event):
            raise RegimeStateCorruptionError(f"regime event filename mismatch: {path}")
        events.append(event)
    _validate_history(
        events,
        account_scope=account_scope,
        account_id=account_id,
        sleeve_id=sleeve_id,
    )
    return events


def _prepare_result(
    events: list[RegimeAuthorityEvent],
    *,
    events_dir: Path,
    account_scope: str,
    account_id: str,
    sleeve_id: str,
    authorization_run_id: str,
    trade_date: str,
    recorded_at: str,
    observed_state: str,
    confidence: float,
    acute_risk: bool,
    risk_package_id: str,
    risk_package_hash: str,
    market_state_id: str,
    minimum_dwell_bars: int,
    confirmation_bars: int,
    confidence_threshold: float,
) -> RegimePersistenceResult:
    normalized_market_state_id = str(market_state_id or f"risk:{risk_package_id}")
    observation_id = _observation_id(
        account_scope=account_scope,
        account_id=account_id,
        sleeve_id=sleeve_id,
        trade_date=trade_date,
        market_state_id=normalized_market_state_id,
    )
    matching_observation = [
        event for event in events if event.observation_id == observation_id
    ]
    if matching_observation:
        event = matching_observation[0]
        same_payload = (
            event.observed_state == _normalized_state(observed_state)
            and event.confidence == _confidence(confidence)
            and event.acute_risk is bool(acute_risk)
            and event.minimum_dwell_bars == int(minimum_dwell_bars)
            and event.confirmation_bars == int(confirmation_bars)
            and event.confidence_threshold == _confidence(confidence_threshold)
            and event.risk_package_id == str(risk_package_id)
            and event.risk_package_hash == str(risk_package_hash)
        )
        if not same_payload:
            raise RegimeStateConflictError(
                "governed regime observation key reused with conflicting payload"
            )
        return RegimePersistenceResult(
            event=event,
            event_path=_event_path(events_dir, event),
            created=False,
            committed=True,
        )
    if any(event.authorization_run_id == authorization_run_id for event in events):
        raise RegimeStateConflictError(
            "authorization run ID reused with a different governed regime observation"
        )

    previous = events[-1] if events else None
    if previous is not None and trade_date < previous.trade_date:
        raise RegimeStateConflictError("regime authorization trade date moves backwards")
    event = _expected_event(
        previous,
        account_scope=account_scope,
        account_id=account_id,
        sleeve_id=sleeve_id,
        authorization_run_id=authorization_run_id,
        trade_date=trade_date,
        recorded_at=recorded_at,
        observed_state=observed_state,
        confidence=confidence,
        acute_risk=acute_risk,
        minimum_dwell_bars=minimum_dwell_bars,
        confirmation_bars=confirmation_bars,
        confidence_threshold=confidence_threshold,
        risk_package_id=risk_package_id,
        risk_package_hash=risk_package_hash,
        market_state_id=normalized_market_state_id,
    )
    return RegimePersistenceResult(
        event=event,
        event_path=_event_path(events_dir, event),
        created=False,
        committed=False,
    )


def prepare_regime_authority(
    state_root: Path | str,
    *,
    account_scope: str,
    account_id: str,
    sleeve_id: str,
    authorization_run_id: str,
    trade_date: str,
    recorded_at: str,
    observed_state: str,
    confidence: float,
    acute_risk: bool,
    risk_package_id: str,
    risk_package_hash: str,
    market_state_id: str,
    minimum_dwell_bars: int = 5,
    confirmation_bars: int = 2,
    confidence_threshold: float = 0.60,
) -> RegimePersistenceResult:
    """Evaluate a governed observation without advancing committed counters."""

    account_dir = _account_directory(
        state_root,
        account_scope=account_scope,
        account_id=account_id,
        sleeve_id=sleeve_id,
    )
    events_dir = account_dir / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    lock_path = account_dir / ".append.lock"
    with lock_path.open("a+b") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        events = load_regime_history(
            state_root,
            account_scope=account_scope,
            account_id=account_id,
            sleeve_id=sleeve_id,
        )
        return _prepare_result(
            events,
            events_dir=events_dir,
            account_scope=account_scope,
            account_id=account_id,
            sleeve_id=sleeve_id,
            authorization_run_id=authorization_run_id,
            trade_date=trade_date,
            recorded_at=recorded_at,
            observed_state=observed_state,
            confidence=confidence,
            acute_risk=acute_risk,
            minimum_dwell_bars=minimum_dwell_bars,
            confirmation_bars=confirmation_bars,
            confidence_threshold=confidence_threshold,
            risk_package_id=risk_package_id,
            risk_package_hash=risk_package_hash,
            market_state_id=market_state_id,
        )


def _append_event(
    *,
    account_dir: Path,
    events_dir: Path,
    event: RegimeAuthorityEvent,
) -> RegimePersistenceResult:
    path = _event_path(events_dir, event)
    serialized = json.dumps(event.to_dict(), indent=2, sort_keys=True) + "\n"
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise RegimeStateConflictError(
            f"append-only regime event already exists: {path}"
        ) from exc
    for directory in (events_dir, account_dir):
        directory_fd = os.open(str(directory), os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    return RegimePersistenceResult(
        event=event,
        event_path=path,
        created=True,
        committed=True,
    )


def commit_prepared_regime_authority(
    state_root: Path | str,
    prepared: RegimePersistenceResult,
) -> RegimePersistenceResult:
    """Commit a previously prepared event after exact authorization publication."""

    event = RegimeAuthorityEvent.from_dict(prepared.event.to_dict())
    account_dir = _account_directory(
        state_root,
        account_scope=event.account_scope,
        account_id=event.account_id,
        sleeve_id=event.sleeve_id,
    )
    events_dir = account_dir / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    lock_path = account_dir / ".append.lock"
    with lock_path.open("a+b") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        events = load_regime_history(
            state_root,
            account_scope=event.account_scope,
            account_id=event.account_id,
            sleeve_id=event.sleeve_id,
        )
        recomputed = _prepare_result(
            events,
            events_dir=events_dir,
            account_scope=event.account_scope,
            account_id=event.account_id,
            sleeve_id=event.sleeve_id,
            authorization_run_id=event.authorization_run_id,
            trade_date=event.trade_date,
            recorded_at=event.recorded_at,
            observed_state=event.observed_state,
            confidence=event.confidence,
            acute_risk=event.acute_risk,
            risk_package_id=event.risk_package_id,
            risk_package_hash=event.risk_package_hash,
            market_state_id=event.market_state_id,
            minimum_dwell_bars=event.minimum_dwell_bars,
            confirmation_bars=event.confirmation_bars,
            confidence_threshold=event.confidence_threshold,
        )
        if recomputed.committed:
            return recomputed
        if recomputed.event.content_hash != event.content_hash:
            raise RegimeStateConflictError(
                "committed regime history advanced after authorization preparation"
            )
        return _append_event(
            account_dir=account_dir,
            events_dir=events_dir,
            event=event,
        )


def persist_regime_authority(
    state_root: Path | str,
    **kwargs: Any,
) -> RegimePersistenceResult:
    """Prepare and immediately commit an observation.

    This is used by the immediate emergency path and by state-store clients that
    have already established their authorization commit boundary.  Normal Choice
    2 authorizations use ``prepare_regime_authority`` and commit only after their
    immutable handoff has been published.
    """

    prepared = prepare_regime_authority(state_root, **kwargs)
    if prepared.committed:
        return prepared
    return commit_prepared_regime_authority(state_root, prepared)

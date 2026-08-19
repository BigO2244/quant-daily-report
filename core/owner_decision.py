"""Immutable, non-executable owner-decision contract.

``caerus.owner_decision.v1`` records an explicit approval or rejection.  This
module can validate and read that record, but it cannot compile, activate, or
write a deployment policy.  In particular, the mere presence of a valid
decision artifact grants no execution authority.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from core.deployment_policy import (
    DeploymentPolicyError,
    LaneDeploymentPolicy,
    artifact_content_hash,
    canonical_json,
)


OWNER_DECISION_SCHEMA = "caerus.owner_decision.v1"
VALID_OWNER_DECISIONS = frozenset({"APPROVE", "REJECT"})

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")

_TOP_LEVEL_FIELDS = frozenset(
    {
        "schema_version",
        "owner_decision_id",
        "recommendation_id",
        "recommendation_hash",
        "decision",
        "owner",
        "decided_at",
        "effective_session",
        "approved_policy_patch",
        "capital_ceiling",
        "risk_limits",
        "preflight_requirements",
        "rollback_deployment_version",
        "expires_at",
        "content_hash",
    }
)


class OwnerDecisionError(ValueError):
    """Raised when owner-decision integrity or semantics are invalid."""


def _reject_json_constant(value: str) -> None:
    raise OwnerDecisionError(f"non-finite JSON constant is forbidden: {value}")


def _object_without_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OwnerDecisionError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise OwnerDecisionError("JSON object keys must be strings")
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise OwnerDecisionError("JSON object keys must be strings")
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _required_string(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OwnerDecisionError(f"{label} must be a non-empty string")
    return value.strip()


def _safe_id(value: Any, *, label: str) -> str:
    raw = _required_string(value, label=label)
    if not _SAFE_ID.fullmatch(raw) or ".." in raw:
        raise OwnerDecisionError(f"{label} is not a valid identifier")
    return raw


def _sha256(value: Any, *, label: str) -> str:
    raw = _required_string(value, label=label)
    if len(raw) != 64 or any(character not in "0123456789abcdef" for character in raw):
        raise OwnerDecisionError(f"{label} must be a lowercase SHA-256 digest")
    return raw


def _timestamp(value: Any, *, label: str) -> tuple[str, dt.datetime]:
    raw = _required_string(value, label=label)
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OwnerDecisionError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise OwnerDecisionError(f"{label} must include a timezone")
    return raw, parsed


def _date(value: Any, *, label: str) -> tuple[str, dt.date]:
    raw = _required_string(value, label=label)
    try:
        parsed = dt.date.fromisoformat(raw)
    except ValueError as exc:
        raise OwnerDecisionError(f"{label} must be an ISO date") from exc
    return raw, parsed


def _json_object(value: Any, *, label: str, allow_empty: bool) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise OwnerDecisionError(f"{label} must be a JSON object")
    if not allow_empty and not value:
        raise OwnerDecisionError(f"{label} must not be empty")
    try:
        canonical_json(value)
    except DeploymentPolicyError as exc:
        raise OwnerDecisionError(f"{label} is not canonical JSON: {exc}") from exc
    return _freeze_json(value)


def seal_owner_decision_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copied payload with a deterministic hash, without authorizing it."""

    sealed = dict(_thaw_json(payload))
    sealed["content_hash"] = artifact_content_hash(sealed)
    return sealed


@dataclass(frozen=True)
class OwnerDecision:
    owner_decision_id: str
    recommendation_id: str
    recommendation_hash: str
    decision: str
    owner: str
    decided_at: str
    effective_session: str | None
    approved_policy_patch: Mapping[str, Any]
    capital_ceiling: float | None
    risk_limits: Mapping[str, Any]
    preflight_requirements: tuple[str, ...]
    rollback_deployment_version: str | None
    expires_at: str
    content_hash: str

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any], *, verify_hash: bool = True
    ) -> "OwnerDecision":
        if not isinstance(payload, Mapping):
            raise OwnerDecisionError("owner decision must be a JSON object")
        present = set(payload)
        missing = sorted(_TOP_LEVEL_FIELDS - present)
        unknown = sorted(present - _TOP_LEVEL_FIELDS)
        if missing:
            raise OwnerDecisionError(
                f"owner decision missing required fields: {', '.join(missing)}"
            )
        if unknown:
            raise OwnerDecisionError(
                f"owner decision contains unknown fields: {', '.join(unknown)}"
            )
        if payload["schema_version"] != OWNER_DECISION_SCHEMA:
            raise OwnerDecisionError(
                f"unsupported owner decision schema: {payload['schema_version']!r}"
            )
        decision = _required_string(payload["decision"], label="decision")
        if decision not in VALID_OWNER_DECISIONS:
            raise OwnerDecisionError(f"unsupported owner decision: {decision!r}")
        decided_at, decided_timestamp = _timestamp(payload["decided_at"], label="decided_at")
        expires_at, expiry_timestamp = _timestamp(payload["expires_at"], label="expires_at")
        if expiry_timestamp <= decided_timestamp:
            raise OwnerDecisionError("expires_at must be later than decided_at")

        raw_preflight = payload["preflight_requirements"]
        if not isinstance(raw_preflight, list):
            raise OwnerDecisionError("preflight_requirements must be a list")
        preflight = tuple(
            _required_string(item, label="preflight requirement") for item in raw_preflight
        )
        if len(preflight) != len(set(preflight)):
            raise OwnerDecisionError("preflight_requirements contains duplicates")

        patch = _json_object(
            payload["approved_policy_patch"],
            label="approved_policy_patch",
            allow_empty=decision == "REJECT",
        )
        risk_limits = _json_object(
            payload["risk_limits"], label="risk_limits", allow_empty=decision == "REJECT"
        )

        capital_raw = payload["capital_ceiling"]
        capital_ceiling: float | None
        if capital_raw is None:
            capital_ceiling = None
        else:
            if isinstance(capital_raw, bool):
                raise OwnerDecisionError("capital_ceiling must be numeric or null")
            try:
                capital_ceiling = float(capital_raw)
            except (TypeError, ValueError) as exc:
                raise OwnerDecisionError("capital_ceiling must be numeric or null") from exc
            if not math.isfinite(capital_ceiling) or capital_ceiling < 0.0:
                raise OwnerDecisionError("capital_ceiling must be finite and non-negative")

        effective_session: str | None
        rollback: str | None
        if decision == "APPROVE":
            effective_session, effective_date = _date(
                payload["effective_session"], label="effective_session"
            )
            if effective_date < decided_timestamp.date():
                raise OwnerDecisionError("effective_session cannot precede decided_at")
            rollback = _safe_id(
                payload["rollback_deployment_version"],
                label="rollback_deployment_version",
            )
            if capital_ceiling is None:
                raise OwnerDecisionError("APPROVE requires capital_ceiling")
            if not preflight:
                raise OwnerDecisionError("APPROVE requires preflight_requirements")
        else:
            if payload["effective_session"] is not None:
                raise OwnerDecisionError("REJECT must not set effective_session")
            if payload["rollback_deployment_version"] is not None:
                raise OwnerDecisionError("REJECT must not set rollback_deployment_version")
            if patch or risk_limits or preflight or capital_ceiling is not None:
                raise OwnerDecisionError("REJECT must not carry approval terms")
            effective_session = None
            rollback = None

        declared_hash = _sha256(payload["content_hash"], label="content_hash")
        expected_hash = artifact_content_hash(payload)
        if verify_hash and declared_hash != expected_hash:
            raise OwnerDecisionError("owner decision content_hash mismatch")
        return cls(
            owner_decision_id=_safe_id(
                payload["owner_decision_id"], label="owner_decision_id"
            ),
            recommendation_id=_safe_id(
                payload["recommendation_id"], label="recommendation_id"
            ),
            recommendation_hash=_sha256(
                payload["recommendation_hash"], label="recommendation_hash"
            ),
            decision=decision,
            owner=_required_string(payload["owner"], label="owner"),
            decided_at=decided_at,
            effective_session=effective_session,
            approved_policy_patch=patch,
            capital_ceiling=capital_ceiling,
            risk_limits=risk_limits,
            preflight_requirements=preflight,
            rollback_deployment_version=rollback,
            expires_at=expires_at,
            content_hash=declared_hash,
        )

    @property
    def approved(self) -> bool:
        return self.decision == "APPROVE"

    @property
    def execution_authority(self) -> bool:
        """A decision record is never itself execution authority."""

        return False

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        payload = {
            "schema_version": OWNER_DECISION_SCHEMA,
            "owner_decision_id": self.owner_decision_id,
            "recommendation_id": self.recommendation_id,
            "recommendation_hash": self.recommendation_hash,
            "decision": self.decision,
            "owner": self.owner,
            "decided_at": self.decided_at,
            "effective_session": self.effective_session,
            "approved_policy_patch": _thaw_json(self.approved_policy_patch),
            "capital_ceiling": self.capital_ceiling,
            "risk_limits": _thaw_json(self.risk_limits),
            "preflight_requirements": list(self.preflight_requirements),
            "rollback_deployment_version": self.rollback_deployment_version,
            "expires_at": self.expires_at,
        }
        if include_hash:
            payload["content_hash"] = self.content_hash
        return payload


def parse_owner_decision(
    payload: Mapping[str, Any], *, verify_hash: bool = True
) -> OwnerDecision:
    return OwnerDecision.from_dict(payload, verify_hash=verify_hash)


def read_owner_decision(
    path: Path | str, *, verify_hash: bool = True
) -> OwnerDecision:
    source = Path(path)
    try:
        payload = json.loads(
            source.read_text(encoding="utf-8"),
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except OwnerDecisionError:
        raise
    except Exception as exc:
        raise OwnerDecisionError(f"cannot read owner decision {source}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise OwnerDecisionError(f"owner decision {source} must contain a JSON object")
    return parse_owner_decision(payload, verify_hash=verify_hash)


def validate_policy_decision_binding(
    policy: LaneDeploymentPolicy, decision: OwnerDecision
) -> None:
    """Validate the immutable policy/decision linkage without activating it."""

    if not decision.approved:
        raise OwnerDecisionError("deployment policy requires an APPROVE owner decision")
    if policy.owner_decision_id != decision.owner_decision_id:
        raise OwnerDecisionError("deployment policy owner_decision_id mismatch")
    if policy.approved_by != decision.owner:
        raise OwnerDecisionError("deployment policy approved_by does not match decision owner")
    if policy.approved_at != decision.decided_at:
        raise OwnerDecisionError("deployment policy approved_at mismatch")
    if policy.effective_session != decision.effective_session:
        raise OwnerDecisionError("deployment policy effective_session mismatch")
    if policy.rollback_deployment_version != decision.rollback_deployment_version:
        raise OwnerDecisionError("deployment policy rollback_deployment_version mismatch")

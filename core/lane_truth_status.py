"""Deterministic truth-status, daily lane audit, and reporting projections.

This module is a read-only Stage 13 contract.  All inputs are explicit mappings;
it never reads files, registry state, runtime configuration, or broker state.
The lane kind and performance surface are bound by the governed deployment
policy and the validated valuation/performance artifacts.  A sleeve lifecycle
label is never consulted when classifying a return as modeled or factual.

PAPER and LIVE return claims are suppressed unless journal, reconciliation,
valuation, and performance lineage are all green.  Lifecycle recommendations
remain advisory inbox items whose Approve/Reject controls are represented only
as external owner actions.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import math
import re
from typing import Any, Iterable, Mapping, Sequence

from core.accounting_journal import canonical_json
from core.deployment_policy import parse_deployment_policy
from core.lane_performance import (
    LanePerformanceError,
    validate_lane_performance,
)
from core.lane_valuation import LaneValuationError, validate_lane_valuation
from core.lifecycle_recommendation import (
    LifecycleRecommendationError,
    validate_lifecycle_recommendation,
)
from core.owner_decision import OwnerDecisionError, parse_owner_decision


TRUTH_LINEAGE_STATUS_SCHEMA = "caerus.truth_lineage_status.v1"
DAILY_LANE_AUDIT_SCHEMA = "caerus.daily_lane_audit.v1"
ALL_LANE_AUDIT_SCHEMA = "caerus.all_lane_audit.v1"
DASHBOARD_PERFORMANCE_SURFACES_SCHEMA = (
    "caerus.dashboard_performance_surfaces.v1"
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_LINEAGE_TYPES = frozenset({"JOURNAL", "RECONCILIATION"})
_LINEAGE_STATUSES = frozenset(
    {"PASS", "FAIL", "MISSING", "NOT_APPLICABLE_MODELED"}
)
_DEPLOYMENT_STATES = frozenset(
    {
        "ACTIVE",
        "PENDING",
        "SUPERSEDED",
        "ROLLED_BACK",
        "DISABLED",
        "ROLLBACK_READY",
        "UNAVAILABLE",
    }
)
_SURFACE_BY_KIND = {
    "SHADOW": ("MODELED_SHADOW_NAV", "THEORETICAL_MODEL", "MODELED_RETURN"),
    "PAPER": ("FACTUAL_PAPER", "BROKER_RECONCILED", "FACTUAL_RETURN"),
    "LIVE": ("FACTUAL_LIVE", "BROKER_RECONCILED", "FACTUAL_RETURN"),
}
_LABEL_BY_KIND = {
    "SHADOW": "modeled shadow return",
    "PAPER": "realized paper return",
    "LIVE": "realized live return",
}
_LINEAGE_FIELDS = frozenset(
    {
        "schema_version",
        "evidence_type",
        "status",
        "as_of",
        "lane_id",
        "lane_kind",
        "deployment_version",
        "performance_surface",
        "source_hashes",
        "blocker_codes",
        "execution_authority",
        "approval_authority",
        "content_hash",
    }
)
_VERSION_REF_FIELDS = frozenset(
    {"deployment_version", "state", "source_hash"}
)
_DEPLOYMENT_STATE_FIELDS = frozenset({"active", "prior", "rollback"})
_CAPITAL_FIELDS = frozenset(
    {
        "capital_ceiling_usd",
        "effective_deployable_capital_usd",
        "source_hash",
    }
)
_CLAIM_FIELDS = frozenset(
    {
        "claim_id",
        "lane_id",
        "lane_kind",
        "sleeve_id",
        "deployment_version",
        "performance_surface",
        "economic_authority",
        "claim_type",
        "claim_status",
        "permitted_label",
        "inception_date",
        "as_of",
        "return_value",
        "source_hashes",
        "blocker_codes",
    }
)
_INBOX_FIELDS = frozenset(
    {
        "recommendation_id",
        "recommendation_hash",
        "action",
        "sleeve_id",
        "source_lane",
        "destination_lane",
        "generated_at",
        "expires_at",
        "status",
        "owner_decision_id",
        "owner_decision_hash",
        "owner_decision_status",
        "required_external_owner_actions",
        "evidence_hashes",
        "reason_codes",
    }
)
_LANE_AUDIT_FIELDS = frozenset(
    {
        "schema_version",
        "audit_id",
        "audit_date",
        "as_of",
        "status",
        "lane_id",
        "lane_kind",
        "account_id_hash",
        "deployment_version",
        "performance_surface",
        "economic_authority",
        "deployment_state",
        "capital",
        "journal_status",
        "reconciliation_status",
        "return_claims",
        "lifecycle_inbox",
        "blocker_codes",
        "source_hashes",
        "execution_authority",
        "approval_authority",
        "content_hash",
    }
)
_ALL_LANE_FIELDS = frozenset(
    {
        "schema_version",
        "audit_id",
        "audit_date",
        "as_of",
        "status",
        "deployment_version",
        "deployment_policy_hash",
        "lane_audits",
        "lane_audit_hashes",
        "blocker_codes",
        "pending_owner_action_count",
        "source_hashes",
        "execution_authority",
        "approval_authority",
        "content_hash",
    }
)
_DASHBOARD_FIELDS = frozenset(
    {
        "schema_version",
        "projection_id",
        "audit_date",
        "as_of",
        "status",
        "performance_surfaces",
        "lifecycle_inbox",
        "source_audit_hashes",
        "execution_authority",
        "approval_authority",
        "content_hash",
    }
)
_DASHBOARD_SURFACE_FIELDS = frozenset(
    {
        "lane_id",
        "lane_kind",
        "sleeve_id",
        "deployment_version",
        "performance_surface",
        "claim_type",
        "claim_status",
        "label",
        "inception_date",
        "as_of",
        "display_return",
        "reconciliation_status",
        "blocker_codes",
        "source_hashes",
        "active_deployment_version",
        "prior_deployment_version",
        "rollback_deployment_version",
        "capital_ceiling_usd",
        "effective_deployable_capital_usd",
    }
)
_DASHBOARD_INBOX_FIELDS = frozenset((_INBOX_FIELDS - {"status"}) | {"status", "lane_ids"})


class LaneTruthStatusError(ValueError):
    """Raised when a reporting artifact cannot be proven from its inputs."""


def _hash(payload: Mapping[str, Any]) -> str:
    body = copy.deepcopy(dict(payload))
    body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def _strict_fields(
    payload: Mapping[str, Any], expected: frozenset[str], *, label: str
) -> None:
    missing = sorted(expected - set(payload))
    unknown = sorted(set(payload) - expected)
    if missing or unknown:
        raise LaneTruthStatusError(
            f"{label} fields mismatch; missing={missing}, unknown={unknown}"
        )


def _string(value: Any, *, label: str, safe: bool = False) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise LaneTruthStatusError(
            f"{label} must be a non-blank string without surrounding whitespace"
        )
    if safe and (not _SAFE_ID.fullmatch(value) or ".." in value):
        raise LaneTruthStatusError(f"{label} is invalid")
    return value


def _sha(value: Any, *, label: str) -> str:
    result = _string(value, label=label)
    if not _SHA256.fullmatch(result):
        raise LaneTruthStatusError(f"{label} must be a lowercase SHA-256 digest")
    return result


def _timestamp(value: Any, *, label: str) -> tuple[str, dt.datetime]:
    raw = _string(value, label=label)
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LaneTruthStatusError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise LaneTruthStatusError(f"{label} must include a timezone")
    return raw, parsed


def _date(value: Any, *, label: str) -> str:
    raw = _string(value, label=label)
    try:
        dt.date.fromisoformat(raw)
    except ValueError as exc:
        raise LaneTruthStatusError(f"{label} must be an ISO date") from exc
    return raw


def _number(value: Any, *, label: str) -> float:
    if isinstance(value, bool):
        raise LaneTruthStatusError(f"{label} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise LaneTruthStatusError(f"{label} must be numeric") from exc
    if not math.isfinite(result) or result < 0.0:
        raise LaneTruthStatusError(f"{label} must be finite and non-negative")
    return result


def _strings(value: Any, *, label: str) -> list[str]:
    if not isinstance(value, list):
        raise LaneTruthStatusError(f"{label} must be an array")
    result = [_string(row, label=f"{label} item") for row in value]
    if result != sorted(set(result)):
        raise LaneTruthStatusError(f"{label} must be sorted and unique")
    return result


def build_truth_lineage_status(
    *,
    evidence_type: str,
    status: str,
    as_of: str,
    lane_id: str,
    lane_kind: str,
    deployment_version: str,
    performance_surface: str,
    source_hashes: Sequence[str],
    blocker_codes: Sequence[str] = (),
) -> dict[str, Any]:
    """Build a hash-bound journal or reconciliation status input."""

    body = {
        "schema_version": TRUTH_LINEAGE_STATUS_SCHEMA,
        "evidence_type": evidence_type,
        "status": status,
        "as_of": as_of,
        "lane_id": lane_id,
        "lane_kind": lane_kind,
        "deployment_version": deployment_version,
        "performance_surface": performance_surface,
        "source_hashes": sorted(set(source_hashes)),
        "blocker_codes": sorted(set(blocker_codes)),
        "execution_authority": False,
        "approval_authority": False,
    }
    body["content_hash"] = _hash(body)
    return validate_truth_lineage_status(body)


def validate_truth_lineage_status(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise LaneTruthStatusError("truth lineage status must be an object")
    _strict_fields(payload, _LINEAGE_FIELDS, label="truth lineage status")
    if payload["schema_version"] != TRUTH_LINEAGE_STATUS_SCHEMA:
        raise LaneTruthStatusError("unsupported truth lineage status schema")
    evidence_type = _string(payload["evidence_type"], label="evidence_type")
    if evidence_type not in _LINEAGE_TYPES:
        raise LaneTruthStatusError("unsupported evidence_type")
    status = _string(payload["status"], label="status")
    if status not in _LINEAGE_STATUSES:
        raise LaneTruthStatusError("unsupported lineage status")
    _timestamp(payload["as_of"], label="as_of")
    _string(payload["lane_id"], label="lane_id", safe=True)
    lane_kind = _string(payload["lane_kind"], label="lane_kind")
    if lane_kind not in _SURFACE_BY_KIND:
        raise LaneTruthStatusError("unsupported lane_kind")
    _string(payload["deployment_version"], label="deployment_version", safe=True)
    expected_surface = _SURFACE_BY_KIND[lane_kind][0]
    if payload["performance_surface"] != expected_surface:
        raise LaneTruthStatusError("performance_surface does not match lane_kind")
    sources = _strings(payload["source_hashes"], label="source_hashes")
    for index, value in enumerate(sources):
        _sha(value, label=f"source_hashes[{index}]")
    blockers = _strings(payload["blocker_codes"], label="blocker_codes")
    if status == "PASS" and blockers:
        raise LaneTruthStatusError("PASS lineage status cannot have blockers")
    if status in {"FAIL", "MISSING"} and not blockers:
        raise LaneTruthStatusError(f"{status} lineage status requires blockers")
    if status == "NOT_APPLICABLE_MODELED":
        if lane_kind != "SHADOW" or evidence_type != "RECONCILIATION":
            raise LaneTruthStatusError(
                "NOT_APPLICABLE_MODELED is only valid for SHADOW reconciliation"
            )
        if blockers:
            raise LaneTruthStatusError("modeled reconciliation status has no blockers")
    if evidence_type == "JOURNAL" and status == "NOT_APPLICABLE_MODELED":
        raise LaneTruthStatusError("modeled lanes still require a theoretical journal")
    if payload["execution_authority"] is not False:
        raise LaneTruthStatusError("truth lineage cannot grant execution authority")
    if payload["approval_authority"] is not False:
        raise LaneTruthStatusError("truth lineage cannot grant approval authority")
    if _sha(payload["content_hash"], label="content_hash") != _hash(payload):
        raise LaneTruthStatusError("truth lineage content_hash mismatch")
    return json.loads(canonical_json(payload))


def _validate_version_ref(
    payload: Any, *, label: str, expected_version: str
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise LaneTruthStatusError(f"{label} must be an object")
    _strict_fields(payload, _VERSION_REF_FIELDS, label=label)
    version = _string(
        payload["deployment_version"], label=f"{label}.deployment_version", safe=True
    )
    if version != expected_version:
        raise LaneTruthStatusError(f"{label} deployment_version mismatch")
    state = _string(payload["state"], label=f"{label}.state")
    if state not in _DEPLOYMENT_STATES:
        raise LaneTruthStatusError(f"{label}.state is unsupported")
    _sha(payload["source_hash"], label=f"{label}.source_hash")
    return json.loads(canonical_json(payload))


def _capital_ceiling(lane: Mapping[str, Any]) -> float:
    capital = lane.get("capital_policy")
    if not isinstance(capital, Mapping):
        raise LaneTruthStatusError("lane capital_policy is missing")
    keys = (
        "capital_ceiling_usd",
        "owner_approved_ceiling",
        "capital_ceiling",
        "maximum_deployable_capital_usd",
    )
    present = [key for key in keys if capital.get(key) is not None]
    if len(present) != 1:
        raise LaneTruthStatusError(
            "capital_policy must declare exactly one recognized capital ceiling"
        )
    return _number(capital[present[0]], label=f"capital_policy.{present[0]}")


def _bind_lineage(
    payload: Mapping[str, Any],
    *,
    evidence_type: str,
    lane: Mapping[str, Any],
    deployment_version: str,
    as_of: str,
) -> dict[str, Any]:
    row = validate_truth_lineage_status(payload)
    expected = {
        "evidence_type": evidence_type,
        "as_of": as_of,
        "lane_id": lane["lane_id"],
        "lane_kind": lane["lane_kind"],
        "deployment_version": deployment_version,
        "performance_surface": lane["performance_surface"],
    }
    for field, value in expected.items():
        if row[field] != value:
            raise LaneTruthStatusError(f"{evidence_type.lower()} {field} mismatch")
    return row


def _validate_valuation_performance(
    *,
    lane: Mapping[str, Any],
    deployment_version: str,
    as_of: str,
    valuation: Mapping[str, Any] | None,
    performance: Mapping[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    checked_valuation: dict[str, Any] | None = None
    checked_performance: dict[str, Any] | None = None
    if valuation is not None:
        try:
            checked_valuation = validate_lane_valuation(valuation)
        except LaneValuationError as exc:
            raise LaneTruthStatusError(f"lane valuation is invalid: {exc}") from exc
        expected = {
            "lane_id": lane["lane_id"],
            "lane_kind": lane["lane_kind"],
            "account_id_hash": lane["account_id_hash"],
            "deployment_version": deployment_version,
            "performance_surface": lane["performance_surface"],
            "as_of": as_of,
        }
        for field, value in expected.items():
            if checked_valuation[field] != value:
                raise LaneTruthStatusError(f"valuation {field} mismatch")
    if performance is not None:
        try:
            checked_performance = validate_lane_performance(performance)
        except LanePerformanceError as exc:
            raise LaneTruthStatusError(f"lane performance is invalid: {exc}") from exc
        expected = {
            "lane_id": lane["lane_id"],
            "lane_kind": lane["lane_kind"],
            "account_id_hash": lane["account_id_hash"],
            "performance_surface": lane["performance_surface"],
            "latest_as_of": as_of,
        }
        for field, value in expected.items():
            if checked_performance[field] != value:
                raise LaneTruthStatusError(f"performance {field} mismatch")
        segment = next(
            (
                row
                for row in checked_performance["segments"]
                if row["deployment_version"] == deployment_version
            ),
            None,
        )
        if segment is None:
            raise LaneTruthStatusError(
                "performance lacks the active deployment segment"
            )
        if checked_valuation is not None:
            if checked_valuation["content_hash"] not in segment["source_valuation_hashes"]:
                raise LaneTruthStatusError(
                    "performance does not bind the supplied valuation"
                )
    return checked_valuation, checked_performance


def _claims(
    *,
    lane: Mapping[str, Any],
    deployment_version: str,
    as_of: str,
    journal: Mapping[str, Any],
    reconciliation: Mapping[str, Any],
    valuation: Mapping[str, Any] | None,
    performance: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    lane_kind = lane["lane_kind"]
    surface, authority, claim_type = _SURFACE_BY_KIND[lane_kind]
    blockers: list[str] = []
    if journal["status"] != "PASS":
        blockers.extend(journal["blocker_codes"] or ["journal_not_green"])
    if lane_kind in {"PAPER", "LIVE"} and reconciliation["status"] != "PASS":
        blockers.extend(
            reconciliation["blocker_codes"] or ["reconciliation_not_green"]
        )
    if lane_kind == "SHADOW" and reconciliation["status"] != "NOT_APPLICABLE_MODELED":
        blockers.append("shadow_reconciliation_status_invalid")
    if valuation is None:
        blockers.append("valuation_missing")
    elif valuation["proof"]["status"] != "PASS":
        blockers.append("valuation_proof_not_green")
    elif lane_kind in {"PAPER", "LIVE"} and valuation["reconciliation_status"] != "PASS":
        blockers.append("valuation_reconciliation_not_green")
    elif lane_kind == "SHADOW" and valuation["reconciliation_status"] != "MODELED":
        blockers.append("shadow_valuation_not_modeled")
    if performance is None:
        blockers.append("performance_missing")
    elif performance["factual"] is not (lane_kind in {"PAPER", "LIVE"}):
        blockers.append("performance_factuality_mismatch")
    blockers = sorted(set(blockers))

    segment = None
    if performance is not None:
        segment = next(
            (
                row
                for row in performance["segments"]
                if row["deployment_version"] == deployment_version
            ),
            None,
        )
    sources = sorted(
        {
            journal["content_hash"],
            reconciliation["content_hash"],
            *([] if valuation is None else [valuation["content_hash"]]),
            *([] if performance is None else [performance["content_hash"]]),
        }
    )

    def claim(
        *, sleeve_id: str | None, rows: list[Mapping[str, Any]] | None, inception: str | None
    ) -> dict[str, Any]:
        latest = rows[-1] if rows else None
        claim_status = "AVAILABLE" if not blockers and latest is not None else "SUPPRESSED"
        suffix = sleeve_id or "lane"
        return {
            "claim_id": f"return-claim:{lane['lane_id']}:{deployment_version}:{suffix}",
            "lane_id": lane["lane_id"],
            "lane_kind": lane_kind,
            "sleeve_id": sleeve_id,
            "deployment_version": deployment_version,
            "performance_surface": surface,
            "economic_authority": authority,
            "claim_type": claim_type,
            "claim_status": claim_status,
            "permitted_label": _LABEL_BY_KIND[lane_kind],
            "inception_date": inception,
            "as_of": None if latest is None else latest["as_of"],
            "return_value": (
                None if claim_status == "SUPPRESSED" else latest["cumulative_return"]
            ),
            "source_hashes": sources,
            "blocker_codes": (
                sorted(set(blockers + (["return_history_missing"] if latest is None else [])))
                if claim_status == "SUPPRESSED"
                else []
            ),
        }

    if segment is None:
        return [claim(sleeve_id=None, rows=None, inception=None)]
    result = [
        claim(
            sleeve_id=None,
            rows=segment["lane_series"],
            inception=segment["inception_date"],
        )
    ]
    for sleeve in segment["sleeve_series"]:
        result.append(
            claim(
                sleeve_id=sleeve["sleeve_id"],
                rows=sleeve["rows"],
                inception=sleeve["rows"][0]["valuation_date"],
            )
        )
    return result


def _lifecycle_inbox(
    *,
    recommendations: Iterable[Mapping[str, Any]],
    owner_decisions: Iterable[Mapping[str, Any]],
    lane: Mapping[str, Any],
    as_of_time: dt.datetime,
) -> list[dict[str, Any]]:
    decisions: dict[str, Any] = {}
    for raw in owner_decisions:
        try:
            decision = parse_owner_decision(raw)
        except OwnerDecisionError as exc:
            raise LaneTruthStatusError(f"owner decision is invalid: {exc}") from exc
        if decision.recommendation_id in decisions:
            raise LaneTruthStatusError("duplicate owner decision for recommendation")
        if _timestamp(decision.decided_at, label="decided_at")[1] > as_of_time:
            raise LaneTruthStatusError("owner decision occurs after audit as_of")
        decisions[decision.recommendation_id] = decision

    inbox: list[dict[str, Any]] = []
    lane_aliases = {lane["lane_id"].upper(), lane["lane_kind"]}
    seen: set[str] = set()
    for raw in recommendations:
        try:
            recommendation = validate_lifecycle_recommendation(raw)
        except LifecycleRecommendationError as exc:
            raise LaneTruthStatusError(
                f"lifecycle recommendation is invalid: {exc}"
            ) from exc
        if recommendation["recommendation_id"] in seen:
            raise LaneTruthStatusError("duplicate lifecycle recommendation")
        seen.add(recommendation["recommendation_id"])
        if not (
            str(recommendation["source_lane"]).upper() in lane_aliases
            or str(recommendation["destination_lane"]).upper() in lane_aliases
        ):
            raise LaneTruthStatusError(
                "lifecycle recommendation is not explicitly scoped to this lane"
            )
        generated = _timestamp(recommendation["generated_at"], label="generated_at")[1]
        expiry = _timestamp(recommendation["expires_at"], label="expires_at")[1]
        if generated > as_of_time:
            raise LaneTruthStatusError("recommendation occurs after audit as_of")
        decision = decisions.pop(recommendation["recommendation_id"], None)
        if decision is not None:
            if decision.recommendation_hash != recommendation["content_hash"]:
                raise LaneTruthStatusError("owner decision recommendation hash mismatch")
            status = "OWNER_APPROVED" if decision.approved else "OWNER_REJECTED"
            actions: list[str] = []
            decision_id = decision.owner_decision_id
            decision_hash = decision.content_hash
            decision_status = decision.decision
        elif expiry <= as_of_time:
            status = "EXPIRED"
            actions = []
            decision_id = None
            decision_hash = None
            decision_status = "NONE"
        else:
            status = "PENDING_OWNER_DECISION"
            actions = ["APPROVE", "REJECT"]
            decision_id = None
            decision_hash = None
            decision_status = "REQUIRED_EXTERNAL_OWNER_ACTION"
        inbox.append(
            {
                "recommendation_id": recommendation["recommendation_id"],
                "recommendation_hash": recommendation["content_hash"],
                "action": recommendation["action"],
                "sleeve_id": recommendation["sleeve_id"],
                "source_lane": recommendation["source_lane"],
                "destination_lane": recommendation["destination_lane"],
                "generated_at": recommendation["generated_at"],
                "expires_at": recommendation["expires_at"],
                "status": status,
                "owner_decision_id": decision_id,
                "owner_decision_hash": decision_hash,
                "owner_decision_status": decision_status,
                "required_external_owner_actions": actions,
                "evidence_hashes": recommendation["evidence_hashes"],
                "reason_codes": recommendation["reason_codes"],
            }
        )
    if decisions:
        raise LaneTruthStatusError("owner decision lacks a supplied recommendation")
    return sorted(inbox, key=lambda row: row["recommendation_id"])


def build_daily_lane_audit(
    *,
    deployment_policy: Mapping[str, Any],
    known_sleeve_ids: Iterable[str],
    lane_id: str,
    as_of: str,
    deployment_state: Mapping[str, Any],
    capital: Mapping[str, Any],
    journal_status: Mapping[str, Any],
    reconciliation_status: Mapping[str, Any],
    valuation: Mapping[str, Any] | None,
    performance: Mapping[str, Any] | None,
    lifecycle_recommendations: Iterable[Mapping[str, Any]] = (),
    owner_decisions: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Build one enabled-lane audit without granting any operational authority."""

    try:
        policy = parse_deployment_policy(
            deployment_policy, known_sleeve_ids=known_sleeve_ids
        )
    except Exception as exc:
        raise LaneTruthStatusError(f"deployment policy is invalid: {exc}") from exc
    if policy.status != "ACTIVE":
        raise LaneTruthStatusError("daily lane audit requires an ACTIVE deployment policy")
    lanes = [row.to_dict() for row in policy.lanes if row.lane_id == lane_id]
    if len(lanes) != 1 or not lanes[0]["enabled"]:
        raise LaneTruthStatusError("lane_id must identify exactly one enabled lane")
    lane = lanes[0]
    expected_surface = _SURFACE_BY_KIND[lane["lane_kind"]][0]
    if lane["performance_surface"] != expected_surface:
        raise LaneTruthStatusError(
            "governed performance_surface does not match lane_kind"
        )
    as_of_raw, as_of_time = _timestamp(as_of, label="as_of")
    audit_date = as_of_time.date().isoformat()

    if not isinstance(deployment_state, Mapping):
        raise LaneTruthStatusError("deployment_state must be an object")
    _strict_fields(deployment_state, _DEPLOYMENT_STATE_FIELDS, label="deployment_state")
    active = _validate_version_ref(
        deployment_state["active"],
        label="deployment_state.active",
        expected_version=policy.deployment_version,
    )
    prior = _validate_version_ref(
        deployment_state["prior"],
        label="deployment_state.prior",
        expected_version=policy.prior_deployment_version,
    )
    rollback = _validate_version_ref(
        deployment_state["rollback"],
        label="deployment_state.rollback",
        expected_version=policy.rollback_deployment_version,
    )
    if active["state"] != "ACTIVE" or active["source_hash"] != policy.content_hash:
        raise LaneTruthStatusError("active deployment state is not bound to ACTIVE policy")

    if not isinstance(capital, Mapping):
        raise LaneTruthStatusError("capital must be an object")
    _strict_fields(capital, _CAPITAL_FIELDS, label="capital")
    ceiling = _number(capital["capital_ceiling_usd"], label="capital_ceiling_usd")
    effective = _number(
        capital["effective_deployable_capital_usd"],
        label="effective_deployable_capital_usd",
    )
    if ceiling != _capital_ceiling(lane):
        raise LaneTruthStatusError("capital ceiling does not match governed policy")
    if effective > ceiling + 1e-8:
        raise LaneTruthStatusError("effective deployable capital exceeds ceiling")
    capital_source = _sha(capital["source_hash"], label="capital.source_hash")
    capital_row = {
        "capital_ceiling_usd": ceiling,
        "effective_deployable_capital_usd": effective,
        "source_hash": capital_source,
    }

    journal = _bind_lineage(
        journal_status,
        evidence_type="JOURNAL",
        lane=lane,
        deployment_version=policy.deployment_version,
        as_of=as_of_raw,
    )
    reconciliation = _bind_lineage(
        reconciliation_status,
        evidence_type="RECONCILIATION",
        lane=lane,
        deployment_version=policy.deployment_version,
        as_of=as_of_raw,
    )
    checked_valuation, checked_performance = _validate_valuation_performance(
        lane=lane,
        deployment_version=policy.deployment_version,
        as_of=as_of_raw,
        valuation=valuation,
        performance=performance,
    )
    claims = _claims(
        lane=lane,
        deployment_version=policy.deployment_version,
        as_of=as_of_raw,
        journal=journal,
        reconciliation=reconciliation,
        valuation=checked_valuation,
        performance=checked_performance,
    )
    inbox = _lifecycle_inbox(
        recommendations=lifecycle_recommendations,
        owner_decisions=owner_decisions,
        lane=lane,
        as_of_time=as_of_time,
    )
    blockers = sorted(
        {
            blocker
            for claim in claims
            for blocker in claim["blocker_codes"]
        }
    )
    sources = sorted(
        {
            policy.content_hash,
            active["source_hash"],
            prior["source_hash"],
            rollback["source_hash"],
            capital_source,
            journal["content_hash"],
            reconciliation["content_hash"],
            *(row["recommendation_hash"] for row in inbox),
            *(
                row["owner_decision_hash"]
                for row in inbox
                if row["owner_decision_hash"] is not None
            ),
            *(
                []
                if checked_valuation is None
                else [checked_valuation["content_hash"]]
            ),
            *(
                []
                if checked_performance is None
                else [checked_performance["content_hash"]]
            ),
        }
    )
    seed = hashlib.sha256(
        canonical_json(
            {
                "lane_id": lane_id,
                "deployment_version": policy.deployment_version,
                "as_of": as_of_raw,
                "sources": sources,
            }
        ).encode("utf-8")
    ).hexdigest()
    body = {
        "schema_version": DAILY_LANE_AUDIT_SCHEMA,
        "audit_id": f"daily-lane-audit:{lane_id}:{audit_date}:{seed[:24]}",
        "audit_date": audit_date,
        "as_of": as_of_raw,
        "status": "PASS" if not blockers else "BLOCKED",
        "lane_id": lane_id,
        "lane_kind": lane["lane_kind"],
        "account_id_hash": lane["account_id_hash"],
        "deployment_version": policy.deployment_version,
        "performance_surface": lane["performance_surface"],
        "economic_authority": _SURFACE_BY_KIND[lane["lane_kind"]][1],
        "deployment_state": {"active": active, "prior": prior, "rollback": rollback},
        "capital": capital_row,
        "journal_status": journal,
        "reconciliation_status": reconciliation,
        "return_claims": claims,
        "lifecycle_inbox": inbox,
        "blocker_codes": blockers,
        "source_hashes": sources,
        "execution_authority": False,
        "approval_authority": False,
    }
    body["content_hash"] = _hash(body)
    return validate_daily_lane_audit(body)


def validate_daily_lane_audit(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise LaneTruthStatusError("daily lane audit must be an object")
    _strict_fields(payload, _LANE_AUDIT_FIELDS, label="daily lane audit")
    if payload["schema_version"] != DAILY_LANE_AUDIT_SCHEMA:
        raise LaneTruthStatusError("unsupported daily lane audit schema")
    _string(payload["audit_id"], label="audit_id", safe=True)
    _date(payload["audit_date"], label="audit_date")
    _, as_of = _timestamp(payload["as_of"], label="as_of")
    if as_of.date().isoformat() != payload["audit_date"]:
        raise LaneTruthStatusError("audit_date does not match as_of")
    if payload["status"] not in {"PASS", "BLOCKED"}:
        raise LaneTruthStatusError("unsupported daily lane audit status")
    _string(payload["lane_id"], label="lane_id", safe=True)
    lane_kind = _string(payload["lane_kind"], label="lane_kind")
    if lane_kind not in _SURFACE_BY_KIND:
        raise LaneTruthStatusError("unsupported lane_kind")
    _sha(payload["account_id_hash"], label="account_id_hash")
    _string(payload["deployment_version"], label="deployment_version", safe=True)
    surface, authority, claim_type = _SURFACE_BY_KIND[lane_kind]
    if payload["performance_surface"] != surface or payload["economic_authority"] != authority:
        raise LaneTruthStatusError("daily audit surface or authority mismatch")
    if not isinstance(payload["deployment_state"], Mapping):
        raise LaneTruthStatusError("deployment_state must be an object")
    _strict_fields(
        payload["deployment_state"], _DEPLOYMENT_STATE_FIELDS, label="deployment_state"
    )
    for name in ("active", "prior", "rollback"):
        row = payload["deployment_state"][name]
        if not isinstance(row, Mapping):
            raise LaneTruthStatusError(f"deployment_state.{name} must be an object")
        _strict_fields(row, _VERSION_REF_FIELDS, label=f"deployment_state.{name}")
        _string(row["deployment_version"], label=f"{name}.deployment_version", safe=True)
        if row["state"] not in _DEPLOYMENT_STATES:
            raise LaneTruthStatusError(f"deployment_state.{name} is unsupported")
        _sha(row["source_hash"], label=f"deployment_state.{name}.source_hash")
    if payload["deployment_state"]["active"]["deployment_version"] != payload["deployment_version"]:
        raise LaneTruthStatusError("active deployment version mismatch")
    if payload["deployment_state"]["active"]["state"] != "ACTIVE":
        raise LaneTruthStatusError("active deployment state must be ACTIVE")
    if not isinstance(payload["capital"], Mapping):
        raise LaneTruthStatusError("capital must be an object")
    _strict_fields(payload["capital"], _CAPITAL_FIELDS, label="capital")
    ceiling = _number(payload["capital"]["capital_ceiling_usd"], label="capital ceiling")
    effective = _number(
        payload["capital"]["effective_deployable_capital_usd"], label="effective capital"
    )
    if effective > ceiling + 1e-8:
        raise LaneTruthStatusError("effective deployable capital exceeds ceiling")
    _sha(payload["capital"]["source_hash"], label="capital.source_hash")
    journal = validate_truth_lineage_status(payload["journal_status"])
    reconciliation = validate_truth_lineage_status(payload["reconciliation_status"])
    for evidence, expected_type in ((journal, "JOURNAL"), (reconciliation, "RECONCILIATION")):
        for field in ("lane_id", "lane_kind", "deployment_version", "performance_surface", "as_of"):
            if evidence[field] != payload[field]:
                raise LaneTruthStatusError(f"nested lineage {field} mismatch")
        if evidence["evidence_type"] != expected_type:
            raise LaneTruthStatusError("nested lineage evidence type mismatch")
    claims = payload["return_claims"]
    if not isinstance(claims, list) or not claims:
        raise LaneTruthStatusError("return_claims must be a non-empty array")
    claim_ids: set[str] = set()
    for index, claim in enumerate(claims):
        label = f"return_claims[{index}]"
        if not isinstance(claim, Mapping):
            raise LaneTruthStatusError(f"{label} must be an object")
        _strict_fields(claim, _CLAIM_FIELDS, label=label)
        claim_id = _string(claim["claim_id"], label=f"{label}.claim_id", safe=True)
        if claim_id in claim_ids:
            raise LaneTruthStatusError("duplicate return claim")
        claim_ids.add(claim_id)
        for field in ("lane_id", "lane_kind", "deployment_version", "performance_surface", "economic_authority"):
            if claim[field] != payload[field]:
                raise LaneTruthStatusError(f"{label}.{field} mismatch")
        if claim["claim_type"] != claim_type:
            raise LaneTruthStatusError(f"{label}.claim_type mismatch")
        if claim["permitted_label"] != _LABEL_BY_KIND[lane_kind]:
            raise LaneTruthStatusError(f"{label}.permitted_label mismatch")
        if claim["sleeve_id"] is not None:
            _string(claim["sleeve_id"], label=f"{label}.sleeve_id", safe=True)
        blockers = _strings(claim["blocker_codes"], label=f"{label}.blocker_codes")
        sources = _strings(claim["source_hashes"], label=f"{label}.source_hashes")
        for value in sources:
            _sha(value, label=f"{label}.source_hash")
        if claim["claim_status"] == "AVAILABLE":
            if blockers or claim["return_value"] is None or claim["as_of"] is None or claim["inception_date"] is None:
                raise LaneTruthStatusError("AVAILABLE return claim is incomplete")
            if isinstance(claim["return_value"], bool) or not math.isfinite(float(claim["return_value"])):
                raise LaneTruthStatusError("return_value must be finite")
            _, claim_as_of = _timestamp(claim["as_of"], label=f"{label}.as_of")
            inception = _date(claim["inception_date"], label=f"{label}.inception_date")
            if claim_as_of != as_of:
                raise LaneTruthStatusError("AVAILABLE return claim as_of mismatch")
            if inception > payload["audit_date"]:
                raise LaneTruthStatusError("return inception_date is after audit_date")
        elif claim["claim_status"] == "SUPPRESSED":
            if not blockers or claim["return_value"] is not None:
                raise LaneTruthStatusError("SUPPRESSED return must have blockers and no value")
        else:
            raise LaneTruthStatusError("unsupported claim_status")
    inbox = payload["lifecycle_inbox"]
    if not isinstance(inbox, list):
        raise LaneTruthStatusError("lifecycle_inbox must be an array")
    for index, row in enumerate(inbox):
        label = f"lifecycle_inbox[{index}]"
        if not isinstance(row, Mapping):
            raise LaneTruthStatusError(f"{label} must be an object")
        _strict_fields(row, _INBOX_FIELDS, label=label)
        _sha(row["recommendation_hash"], label=f"{label}.recommendation_hash")
        if row["owner_decision_hash"] is not None:
            _sha(row["owner_decision_hash"], label=f"{label}.owner_decision_hash")
        actions = row["required_external_owner_actions"]
        if row["status"] == "PENDING_OWNER_DECISION":
            if actions != ["APPROVE", "REJECT"] or row["owner_decision_id"] is not None:
                raise LaneTruthStatusError("pending recommendation owner actions are invalid")
        elif actions:
            raise LaneTruthStatusError("resolved/expired recommendation cannot have actions")
    blockers = _strings(payload["blocker_codes"], label="blocker_codes")
    observed_blockers = sorted(
        {
            blocker
            for claim in claims
            for blocker in claim["blocker_codes"]
        }
    )
    if blockers != observed_blockers:
        raise LaneTruthStatusError("daily audit blockers do not match return claims")
    available = any(claim["claim_status"] == "AVAILABLE" for claim in claims)
    lineage_green = journal["status"] == "PASS" and (
        reconciliation["status"] == "PASS"
        if lane_kind in {"PAPER", "LIVE"}
        else reconciliation["status"] == "NOT_APPLICABLE_MODELED"
    )
    if available and not lineage_green:
        raise LaneTruthStatusError("available return claim lacks green lineage")
    expected_status = "PASS" if not blockers else "BLOCKED"
    if payload["status"] != expected_status:
        raise LaneTruthStatusError("daily audit status does not match blockers")
    sources = _strings(payload["source_hashes"], label="source_hashes")
    for value in sources:
        _sha(value, label="source_hash")
    required_sources = {
        payload["deployment_state"]["active"]["source_hash"],
        payload["capital"]["source_hash"],
        journal["content_hash"],
        reconciliation["content_hash"],
        *(value for claim in claims for value in claim["source_hashes"]),
        *(row["recommendation_hash"] for row in inbox),
        *(
            row["owner_decision_hash"]
            for row in inbox
            if row["owner_decision_hash"] is not None
        ),
    }
    if not required_sources.issubset(set(sources)):
        raise LaneTruthStatusError("daily audit source_hashes omit required lineage")
    if payload["execution_authority"] is not False or payload["approval_authority"] is not False:
        raise LaneTruthStatusError("daily audit cannot grant authority")
    if _sha(payload["content_hash"], label="content_hash") != _hash(payload):
        raise LaneTruthStatusError("daily lane audit content_hash mismatch")
    return json.loads(canonical_json(payload))


def build_all_lane_audit(
    *,
    deployment_policy: Mapping[str, Any],
    known_sleeve_ids: Iterable[str],
    lane_audits: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Aggregate exactly one audit for every enabled lane in the policy."""

    try:
        policy = parse_deployment_policy(
            deployment_policy, known_sleeve_ids=known_sleeve_ids
        )
    except Exception as exc:
        raise LaneTruthStatusError(f"deployment policy is invalid: {exc}") from exc
    audits = [validate_daily_lane_audit(row) for row in lane_audits]
    expected_ids = sorted(row.lane_id for row in policy.lanes if row.enabled)
    observed_ids = [row["lane_id"] for row in audits]
    if observed_ids != sorted(set(observed_ids)):
        raise LaneTruthStatusError("lane audits must be sorted and unique by lane_id")
    if observed_ids != expected_ids:
        raise LaneTruthStatusError("all-lane audit does not cover every enabled lane")
    if any(row["deployment_version"] != policy.deployment_version for row in audits):
        raise LaneTruthStatusError("lane audit deployment version mismatch")
    as_of_values = {row["as_of"] for row in audits}
    dates = {row["audit_date"] for row in audits}
    if len(as_of_values) != 1 or len(dates) != 1:
        raise LaneTruthStatusError("all lane audits must share one as_of and audit_date")
    as_of = next(iter(as_of_values))
    audit_date = next(iter(dates))
    summaries = [
        {
            "lane_id": row["lane_id"],
            "lane_kind": row["lane_kind"],
            "status": row["status"],
            "deployment_version": row["deployment_version"],
            "performance_surface": row["performance_surface"],
            "blocker_codes": row["blocker_codes"],
            "audit_hash": row["content_hash"],
        }
        for row in audits
    ]
    blockers = sorted(
        f"{row['lane_id']}:{blocker}"
        for row in audits
        for blocker in row["blocker_codes"]
    )
    hashes = [row["content_hash"] for row in audits]
    pending = len(
        {
            item["recommendation_id"]
            for row in audits
            for item in row["lifecycle_inbox"]
            if item["status"] == "PENDING_OWNER_DECISION"
        }
    )
    seed = hashlib.sha256(canonical_json(hashes).encode("utf-8")).hexdigest()
    body = {
        "schema_version": ALL_LANE_AUDIT_SCHEMA,
        "audit_id": f"all-lane-audit:{audit_date}:{seed[:24]}",
        "audit_date": audit_date,
        "as_of": as_of,
        "status": "PASS" if not blockers else "BLOCKED",
        "deployment_version": policy.deployment_version,
        "deployment_policy_hash": policy.content_hash,
        "lane_audits": summaries,
        "lane_audit_hashes": hashes,
        "blocker_codes": blockers,
        "pending_owner_action_count": pending,
        "source_hashes": sorted({policy.content_hash, *hashes}),
        "execution_authority": False,
        "approval_authority": False,
    }
    body["content_hash"] = _hash(body)
    return validate_all_lane_audit(body)


def validate_all_lane_audit(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise LaneTruthStatusError("all-lane audit must be an object")
    _strict_fields(payload, _ALL_LANE_FIELDS, label="all-lane audit")
    if payload["schema_version"] != ALL_LANE_AUDIT_SCHEMA:
        raise LaneTruthStatusError("unsupported all-lane audit schema")
    _string(payload["audit_id"], label="audit_id", safe=True)
    _date(payload["audit_date"], label="audit_date")
    _, aggregate_as_of = _timestamp(payload["as_of"], label="as_of")
    if aggregate_as_of.date().isoformat() != payload["audit_date"]:
        raise LaneTruthStatusError("all-lane audit_date does not match as_of")
    _string(payload["deployment_version"], label="deployment_version", safe=True)
    _sha(payload["deployment_policy_hash"], label="deployment_policy_hash")
    rows = payload["lane_audits"]
    if not isinstance(rows, list) or not rows:
        raise LaneTruthStatusError("lane_audits must be a non-empty array")
    if [row.get("lane_id") for row in rows] != sorted(row.get("lane_id") for row in rows):
        raise LaneTruthStatusError("lane_audits must be sorted by lane_id")
    hashes: list[str] = []
    blockers: list[str] = []
    for index, row in enumerate(rows):
        label = f"lane_audits[{index}]"
        expected = frozenset(
            {"lane_id", "lane_kind", "status", "deployment_version", "performance_surface", "blocker_codes", "audit_hash"}
        )
        if not isinstance(row, Mapping):
            raise LaneTruthStatusError(f"{label} must be an object")
        _strict_fields(row, expected, label=label)
        if row["deployment_version"] != payload["deployment_version"]:
            raise LaneTruthStatusError("lane summary deployment version mismatch")
        _string(row["lane_id"], label=f"{label}.lane_id", safe=True)
        expected_surface = _SURFACE_BY_KIND.get(row["lane_kind"])
        if expected_surface is None or row["performance_surface"] != expected_surface[0]:
            raise LaneTruthStatusError("lane summary performance surface mismatch")
        audit_hash = _sha(row["audit_hash"], label=f"{label}.audit_hash")
        hashes.append(audit_hash)
        row_blockers = _strings(row["blocker_codes"], label=f"{label}.blocker_codes")
        blockers.extend(f"{row['lane_id']}:{value}" for value in row_blockers)
        if row["status"] != ("PASS" if not row_blockers else "BLOCKED"):
            raise LaneTruthStatusError("lane summary status mismatch")
    if payload["lane_audit_hashes"] != hashes:
        raise LaneTruthStatusError("lane_audit_hashes do not match summaries")
    if payload["blocker_codes"] != sorted(blockers):
        raise LaneTruthStatusError("aggregate blockers do not match lane summaries")
    if payload["status"] != ("PASS" if not blockers else "BLOCKED"):
        raise LaneTruthStatusError("all-lane status does not match blockers")
    if not isinstance(payload["pending_owner_action_count"], int) or payload["pending_owner_action_count"] < 0:
        raise LaneTruthStatusError("pending_owner_action_count must be non-negative")
    for value in _strings(payload["source_hashes"], label="source_hashes"):
        _sha(value, label="source_hash")
    if payload["execution_authority"] is not False or payload["approval_authority"] is not False:
        raise LaneTruthStatusError("all-lane audit cannot grant authority")
    if _sha(payload["content_hash"], label="content_hash") != _hash(payload):
        raise LaneTruthStatusError("all-lane audit content_hash mismatch")
    return json.loads(canonical_json(payload))


def build_dashboard_performance_surfaces(
    lane_audits: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Project validated audit claims into display-safe dashboard records."""

    audits = [validate_daily_lane_audit(row) for row in lane_audits]
    if not audits:
        raise LaneTruthStatusError("dashboard projection requires lane audits")
    audits.sort(key=lambda row: row["lane_id"])
    if len({row["lane_id"] for row in audits}) != len(audits):
        raise LaneTruthStatusError("dashboard projection contains duplicate lanes")
    if len({row["as_of"] for row in audits}) != 1:
        raise LaneTruthStatusError("dashboard lane audits must share one as_of")
    surfaces: list[dict[str, Any]] = []
    inbox_by_id: dict[str, dict[str, Any]] = {}
    for audit in audits:
        for claim in audit["return_claims"]:
            surfaces.append(
                {
                    "lane_id": claim["lane_id"],
                    "lane_kind": claim["lane_kind"],
                    "sleeve_id": claim["sleeve_id"],
                    "deployment_version": claim["deployment_version"],
                    "performance_surface": claim["performance_surface"],
                    "claim_type": claim["claim_type"],
                    "claim_status": claim["claim_status"],
                    "label": claim["permitted_label"],
                    "inception_date": claim["inception_date"],
                    "as_of": claim["as_of"],
                    "display_return": claim["return_value"],
                    "reconciliation_status": audit["reconciliation_status"]["status"],
                    "blocker_codes": claim["blocker_codes"],
                    "source_hashes": claim["source_hashes"],
                    "active_deployment_version": audit["deployment_state"]["active"]["deployment_version"],
                    "prior_deployment_version": audit["deployment_state"]["prior"]["deployment_version"],
                    "rollback_deployment_version": audit["deployment_state"]["rollback"]["deployment_version"],
                    "capital_ceiling_usd": audit["capital"]["capital_ceiling_usd"],
                    "effective_deployable_capital_usd": audit["capital"]["effective_deployable_capital_usd"],
                }
            )
        for item in audit["lifecycle_inbox"]:
            recommendation_id = item["recommendation_id"]
            prior = inbox_by_id.get(recommendation_id)
            if prior is None:
                inbox_by_id[recommendation_id] = {
                    "lane_ids": [audit["lane_id"]],
                    **item,
                }
                continue
            comparable = dict(prior)
            comparable.pop("lane_ids")
            if comparable != item:
                raise LaneTruthStatusError(
                    "lifecycle inbox recommendation differs across lane audits"
                )
            prior["lane_ids"] = sorted({*prior["lane_ids"], audit["lane_id"]})
    sources = [row["content_hash"] for row in audits]
    seed = hashlib.sha256(canonical_json(sources).encode("utf-8")).hexdigest()
    body = {
        "schema_version": DASHBOARD_PERFORMANCE_SURFACES_SCHEMA,
        "projection_id": f"dashboard-performance:{audits[0]['audit_date']}:{seed[:24]}",
        "audit_date": audits[0]["audit_date"],
        "as_of": audits[0]["as_of"],
        "status": "PASS" if all(row["status"] == "PASS" for row in audits) else "BLOCKED",
        "performance_surfaces": surfaces,
        "lifecycle_inbox": sorted(
            inbox_by_id.values(), key=lambda row: row["recommendation_id"]
        ),
        "source_audit_hashes": sources,
        "execution_authority": False,
        "approval_authority": False,
    }
    body["content_hash"] = _hash(body)
    return validate_dashboard_performance_surfaces(body)


def validate_dashboard_performance_surfaces(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise LaneTruthStatusError("dashboard performance projection must be an object")
    _strict_fields(payload, _DASHBOARD_FIELDS, label="dashboard projection")
    if payload["schema_version"] != DASHBOARD_PERFORMANCE_SURFACES_SCHEMA:
        raise LaneTruthStatusError("unsupported dashboard projection schema")
    _string(payload["projection_id"], label="projection_id", safe=True)
    _date(payload["audit_date"], label="audit_date")
    _, projection_as_of = _timestamp(payload["as_of"], label="as_of")
    if projection_as_of.date().isoformat() != payload["audit_date"]:
        raise LaneTruthStatusError("dashboard audit_date does not match as_of")
    if payload["status"] not in {"PASS", "BLOCKED"}:
        raise LaneTruthStatusError("unsupported dashboard projection status")
    if not isinstance(payload["performance_surfaces"], list) or not payload["performance_surfaces"]:
        raise LaneTruthStatusError("performance_surfaces must be a non-empty array")
    for index, row in enumerate(payload["performance_surfaces"]):
        if not isinstance(row, Mapping):
            raise LaneTruthStatusError(f"performance_surfaces[{index}] must be an object")
        _strict_fields(
            row,
            _DASHBOARD_SURFACE_FIELDS,
            label=f"performance_surfaces[{index}]",
        )
        if row["claim_status"] == "SUPPRESSED" and row["display_return"] is not None:
            raise LaneTruthStatusError(
                f"performance_surfaces[{index}] exposes a suppressed return"
            )
        if row["claim_status"] == "AVAILABLE" and row["display_return"] is None:
            raise LaneTruthStatusError(
                f"performance_surfaces[{index}] omits an available return"
            )
        expected = _SURFACE_BY_KIND.get(row["lane_kind"])
        if expected is None or row["performance_surface"] != expected[0] or row["claim_type"] != expected[2]:
            raise LaneTruthStatusError(
                f"performance_surfaces[{index}] surface classification mismatch"
            )
        if row["label"] != _LABEL_BY_KIND[row["lane_kind"]]:
            raise LaneTruthStatusError(
                f"performance_surfaces[{index}] return label mismatch"
            )
        if row["as_of"] is not None:
            _, row_as_of = _timestamp(
                row["as_of"], label=f"performance_surfaces[{index}].as_of"
            )
            if row_as_of != projection_as_of:
                raise LaneTruthStatusError("dashboard return as_of mismatch")
        if row["display_return"] is not None and (
            isinstance(row["display_return"], bool)
            or not math.isfinite(float(row["display_return"]))
        ):
            raise LaneTruthStatusError("dashboard display_return must be finite")
        for name in (
            "active_deployment_version",
            "prior_deployment_version",
            "rollback_deployment_version",
        ):
            _string(row[name], label=f"performance_surfaces[{index}].{name}", safe=True)
        ceiling = _number(
            row["capital_ceiling_usd"],
            label=f"performance_surfaces[{index}].capital_ceiling_usd",
        )
        effective = _number(
            row["effective_deployable_capital_usd"],
            label=f"performance_surfaces[{index}].effective_deployable_capital_usd",
        )
        if effective > ceiling + 1e-8:
            raise LaneTruthStatusError("dashboard effective capital exceeds ceiling")
        blockers = _strings(
            row["blocker_codes"], label=f"performance_surfaces[{index}].blocker_codes"
        )
        sources = _strings(
            row["source_hashes"], label=f"performance_surfaces[{index}].source_hashes"
        )
        for value in sources:
            _sha(value, label="dashboard surface source hash")
        if row["claim_status"] == "SUPPRESSED" and not blockers:
            raise LaneTruthStatusError("suppressed dashboard return requires blockers")
        if row["claim_status"] == "AVAILABLE" and blockers:
            raise LaneTruthStatusError("available dashboard return cannot have blockers")
    if not isinstance(payload["lifecycle_inbox"], list):
        raise LaneTruthStatusError("lifecycle_inbox must be an array")
    recommendation_ids: list[str] = []
    for index, row in enumerate(payload["lifecycle_inbox"]):
        if not isinstance(row, Mapping):
            raise LaneTruthStatusError(f"lifecycle_inbox[{index}] must be an object")
        _strict_fields(row, _DASHBOARD_INBOX_FIELDS, label=f"lifecycle_inbox[{index}]")
        recommendation_ids.append(row["recommendation_id"])
        lane_ids = row["lane_ids"]
        if not isinstance(lane_ids, list) or lane_ids != sorted(set(lane_ids)) or not lane_ids:
            raise LaneTruthStatusError("dashboard lifecycle lane_ids must be sorted and unique")
        if row["status"] == "PENDING_OWNER_DECISION":
            if row["required_external_owner_actions"] != ["APPROVE", "REJECT"]:
                raise LaneTruthStatusError("dashboard pending owner actions are invalid")
        elif row["required_external_owner_actions"]:
            raise LaneTruthStatusError("dashboard resolved inbox item cannot have actions")
    if recommendation_ids != sorted(set(recommendation_ids)):
        raise LaneTruthStatusError("dashboard lifecycle inbox must be sorted and unique")
    source_audit_hashes = payload["source_audit_hashes"]
    if not isinstance(source_audit_hashes, list) or not source_audit_hashes:
        raise LaneTruthStatusError("source_audit_hashes must be a non-empty array")
    for value in source_audit_hashes:
        _sha(value, label="source_audit_hash")
    if payload["execution_authority"] is not False or payload["approval_authority"] is not False:
        raise LaneTruthStatusError("dashboard projection cannot grant authority")
    if _sha(payload["content_hash"], label="content_hash") != _hash(payload):
        raise LaneTruthStatusError("dashboard projection content_hash mismatch")
    return json.loads(canonical_json(payload))


__all__ = [
    "ALL_LANE_AUDIT_SCHEMA",
    "DAILY_LANE_AUDIT_SCHEMA",
    "DASHBOARD_PERFORMANCE_SURFACES_SCHEMA",
    "TRUTH_LINEAGE_STATUS_SCHEMA",
    "LaneTruthStatusError",
    "build_all_lane_audit",
    "build_daily_lane_audit",
    "build_dashboard_performance_surfaces",
    "build_truth_lineage_status",
    "validate_all_lane_audit",
    "validate_daily_lane_audit",
    "validate_dashboard_performance_surfaces",
    "validate_truth_lineage_status",
]

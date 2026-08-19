"""Fresh operational evidence required by dynamic-balance Live preflight."""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import re
from typing import Any, Mapping

from authority.lane_exact_plan import canonical_json


SCHEMA = "caerus.generic_live_dynamic_operational_proofs.v1"
_SHA = re.compile(r"^[0-9a-f]{64}$")
_GIT = re.compile(r"^[0-9a-f]{40}$")
_FIELDS = frozenset(
    {
        "schema_version", "generated_at", "deployed_sha", "expected_deployed_sha",
        "account_observation_hash", "positions_observed_at", "positions_source_hash",
        "open_orders_observed_at", "open_orders_source_hash", "open_order_count",
        "asset_observed_at", "asset_source_hash", "asset_status", "asset_tradable",
        "legacy_executor_disabled", "legacy_kill_switch_armed",
        "generic_kill_switch_armed", "generic_schedule_installed",
        "generic_submission_adapter_deployed", "rollback_rearm_proven",
        "order_lifecycle_pipeline_green", "reconciliation_pipeline_green",
        "accounting_pipeline_green", "reporting_pipeline_green",
        "broker_write_performed", "content_hash",
    }
)


class GenericLiveDynamicOperationalProofError(ValueError):
    pass


def _hash(payload: Mapping[str, Any]) -> str:
    body = copy.deepcopy(dict(payload))
    body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode()).hexdigest()


def _time(value: Any, label: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise GenericLiveDynamicOperationalProofError(f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        raise GenericLiveDynamicOperationalProofError(f"{label} needs timezone")
    return parsed


def validate_generic_live_dynamic_operational_proofs(
    payload: Mapping[str, Any], *, as_of: str,
) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or set(payload) != _FIELDS or payload.get("schema_version") != SCHEMA:
        raise GenericLiveDynamicOperationalProofError("operational proof fields are invalid")
    evaluated = _time(as_of, "as_of")
    generated = _time(payload.get("generated_at"), "generated_at")
    if generated > evaluated:
        raise GenericLiveDynamicOperationalProofError("operational proof is future-dated")
    for field in ("positions_observed_at", "open_orders_observed_at", "asset_observed_at"):
        observed = _time(payload.get(field), field)
        age = (evaluated - observed).total_seconds()
        if age < 0 or age >= 120:
            raise GenericLiveDynamicOperationalProofError(f"{field} is not fresher than 120 seconds")
    for field in (
        "account_observation_hash", "positions_source_hash", "open_orders_source_hash",
        "asset_source_hash", "content_hash",
    ):
        if not isinstance(payload.get(field), str) or not _SHA.fullmatch(payload[field]):
            raise GenericLiveDynamicOperationalProofError(f"{field} is invalid")
    for field in ("deployed_sha", "expected_deployed_sha"):
        if not isinstance(payload.get(field), str) or not _GIT.fullmatch(payload[field]):
            raise GenericLiveDynamicOperationalProofError(f"{field} is invalid")
    if payload["deployed_sha"] != payload["expected_deployed_sha"]:
        raise GenericLiveDynamicOperationalProofError("deployed SHA differs")
    if type(payload.get("open_order_count")) is not int or payload["open_order_count"] != 0:
        raise GenericLiveDynamicOperationalProofError("fresh open orders are present")
    if str(payload.get("asset_status")).lower().split(".")[-1] != "active" or payload.get("asset_tradable") is not True:
        raise GenericLiveDynamicOperationalProofError("fresh asset is not active/tradable")
    for field in (
        "legacy_executor_disabled", "legacy_kill_switch_armed",
        "generic_kill_switch_armed", "generic_schedule_installed",
        "generic_submission_adapter_deployed", "rollback_rearm_proven",
        "order_lifecycle_pipeline_green", "reconciliation_pipeline_green",
        "accounting_pipeline_green", "reporting_pipeline_green",
    ):
        if payload.get(field) is not True:
            raise GenericLiveDynamicOperationalProofError(f"{field} is not green")
    if payload.get("broker_write_performed") is not False:
        raise GenericLiveDynamicOperationalProofError("operational proof must be read-only")
    if payload["content_hash"] != _hash(payload):
        raise GenericLiveDynamicOperationalProofError("operational proof content hash differs")
    return copy.deepcopy(dict(payload))


def seal_generic_live_dynamic_operational_proofs(payload: Mapping[str, Any]) -> dict[str, Any]:
    body = copy.deepcopy(dict(payload))
    body["schema_version"] = SCHEMA
    body["content_hash"] = _hash(body)
    return body


__all__ = [
    "SCHEMA", "GenericLiveDynamicOperationalProofError",
    "seal_generic_live_dynamic_operational_proofs",
    "validate_generic_live_dynamic_operational_proofs",
]

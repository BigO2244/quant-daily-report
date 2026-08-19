"""UI-neutral dashboard payload built only from the truth-status projection.

The canonical card builder is preserved for explicit callers. Production
consumption remains off by default through :func:`consume_dashboard_truth`,
which validates the explicit truth artifact in both modes and performs no
dashboard write or external call.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping

from core.accounting_journal import canonical_json
from core.lane_truth_status import validate_dashboard_performance_surfaces


DASHBOARD_TRUTH_PAYLOAD_SCHEMA = "caerus.dashboard_truth_payload.v1"
DASHBOARD_TRUTH_CONSUMPTION_SCHEMA = "caerus.dashboard_truth_consumption.v1"


class DashboardTruthConsumerError(ValueError):
    """Raised when the disabled consumption boundary is malformed."""


def dashboard_truth_payload_hash(payload: Mapping[str, Any]) -> str:
    body = copy.deepcopy(dict(payload))
    body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def build_dashboard_truth_payload(projection: Mapping[str, Any]) -> dict[str, Any]:
    """Project only already-authorized labels and claims into display cards."""

    source = validate_dashboard_performance_surfaces(projection)
    cards = []
    for row in source["performance_surfaces"]:
        cards.append(
            {
                "card_id": f"return:{row['lane_id']}:{row['sleeve_id'] or 'lane'}",
                "lane_id": row["lane_id"],
                "lane_kind": row["lane_kind"],
                "sleeve_id": row["sleeve_id"],
                "deployment_version": row["deployment_version"],
                "performance_surface": row["performance_surface"],
                "label": row["label"],
                "claim_status": row["claim_status"],
                "return_value": row["display_return"],
                "as_of": row["as_of"],
                "blocker_codes": row["blocker_codes"],
                "reconciliation_status": row["reconciliation_status"],
                "capital_ceiling_usd": row["capital_ceiling_usd"],
                "effective_deployable_capital_usd": row["effective_deployable_capital_usd"],
                "source_hashes": row["source_hashes"],
            }
        )
    body = {
        "schema_version": DASHBOARD_TRUTH_PAYLOAD_SCHEMA,
        "status": source["status"],
        "audit_date": source["audit_date"],
        "as_of": source["as_of"],
        "cards": cards,
        "lifecycle_inbox": source["lifecycle_inbox"],
        "truth_projection_id": source["projection_id"],
        "truth_projection_hash": source["content_hash"],
        "source_audit_hashes": source["source_audit_hashes"],
        "fallback_data_used": False,
        "execution_authority": False,
        "approval_authority": False,
    }
    body["content_hash"] = dashboard_truth_payload_hash(body)
    return json.loads(canonical_json(body))


def consume_dashboard_truth(
    *, truth_status_artifact: Mapping[str, Any], consumer_enabled: bool = False,
) -> dict[str, Any]:
    """Validate truth; build cards only with an explicit literal enable flag."""

    if type(consumer_enabled) is not bool:
        raise DashboardTruthConsumerError("consumer_enabled must be a literal boolean")
    source = validate_dashboard_performance_surfaces(truth_status_artifact)
    payload = build_dashboard_truth_payload(source) if consumer_enabled else None
    body = {
        "schema_version": DASHBOARD_TRUTH_CONSUMPTION_SCHEMA,
        "consumption_id": "pending",
        "status": (
            "TRUTH_VALIDATED_NO_CONSUMPTION"
            if not consumer_enabled
            else "TRUTH_CONSUMED_NO_PUBLISH"
        ),
        "consumer_enabled": consumer_enabled,
        "truth_projection_id": source["projection_id"],
        "truth_projection_hash": source["content_hash"],
        "dashboard_truth_payload": payload,
        "fallback_data_used": False,
        "dashboard_write_performed": False,
        "external_call_performed": False,
        "execution_authority": False,
        "activation_authority": False,
        "approval_authority": False,
    }
    seed = dashboard_truth_payload_hash(body)
    body["consumption_id"] = f"dashboard-truth-consumption:{source['content_hash'][:24]}:{seed[:12]}"
    body["content_hash"] = dashboard_truth_payload_hash(body)
    return validate_dashboard_truth_consumption(body)


def validate_dashboard_truth_consumption(payload: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "schema_version", "consumption_id", "status", "consumer_enabled",
        "truth_projection_id", "truth_projection_hash", "dashboard_truth_payload",
        "fallback_data_used", "dashboard_write_performed", "external_call_performed",
        "execution_authority", "activation_authority", "approval_authority", "content_hash",
    }
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise DashboardTruthConsumerError("dashboard truth consumption fields are invalid")
    if payload.get("schema_version") != DASHBOARD_TRUTH_CONSUMPTION_SCHEMA:
        raise DashboardTruthConsumerError("unsupported dashboard truth consumption schema")
    enabled = payload.get("consumer_enabled")
    if type(enabled) is not bool:
        raise DashboardTruthConsumerError("consumer_enabled must be boolean")
    expected_status = "TRUTH_CONSUMED_NO_PUBLISH" if enabled else "TRUTH_VALIDATED_NO_CONSUMPTION"
    if payload.get("status") != expected_status:
        raise DashboardTruthConsumerError("dashboard truth consumption status differs from gate")
    if enabled:
        child = payload.get("dashboard_truth_payload")
        if not isinstance(child, Mapping):
            raise DashboardTruthConsumerError("enabled consumption requires canonical dashboard truth payload")
        if child.get("truth_projection_hash") != payload.get("truth_projection_hash"):
            raise DashboardTruthConsumerError("dashboard payload truth lineage mismatch")
        if child.get("content_hash") != dashboard_truth_payload_hash(child):
            raise DashboardTruthConsumerError("dashboard truth payload content_hash mismatch")
    elif payload.get("dashboard_truth_payload") is not None:
        raise DashboardTruthConsumerError("disabled consumer cannot expose dashboard cards")
    for field in (
        "fallback_data_used", "dashboard_write_performed", "external_call_performed",
        "execution_authority", "activation_authority", "approval_authority",
    ):
        if payload.get(field) is not False:
            raise DashboardTruthConsumerError(f"dashboard consumer {field} must remain false")
    digest = payload.get("truth_projection_hash")
    if not isinstance(digest, str) or len(digest) != 64:
        raise DashboardTruthConsumerError("truth_projection_hash is invalid")
    if payload.get("content_hash") != dashboard_truth_payload_hash(payload):
        raise DashboardTruthConsumerError("dashboard truth consumption content_hash mismatch")
    return json.loads(canonical_json(payload))


__all__ = [
    "DASHBOARD_TRUTH_PAYLOAD_SCHEMA", "DASHBOARD_TRUTH_CONSUMPTION_SCHEMA",
    "DashboardTruthConsumerError", "build_dashboard_truth_payload",
    "dashboard_truth_payload_hash", "consume_dashboard_truth",
    "validate_dashboard_truth_consumption",
]

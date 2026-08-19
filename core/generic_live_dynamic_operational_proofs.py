"""Causal read-only operational evidence for dynamic-balance Live preflight.

There is deliberately no generic ``seal`` helper. A green artifact can only be
built from the exact plan, freshly observed broker objects, and independently
pinned pipeline/runtime evidence.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import re
from typing import Any, Mapping, Sequence

from authority.lane_exact_plan import canonical_json, validate_lane_exact_execution_plan
from core.generic_live_dynamic_account import validate_generic_live_dynamic_account_observation


SCHEMA = "caerus.generic_live_dynamic_operational_proofs.v2"
_SHA = re.compile(r"^[0-9a-f]{64}$")
_GIT = re.compile(r"^[0-9a-f]{40}$")
_PIPELINES = (
    "order_lifecycle", "reconciliation", "accounting", "reporting", "rollback_rearm",
)
_FIELDS = frozenset(
    {
        "schema_version", "generated_at", "plan_hash", "account_id_hash",
        "deployed_sha", "expected_deployed_sha", "account_observation_hash",
        "positions_evidence", "open_orders_evidence", "asset_evidence",
        "runtime_evidence", "pipeline_evidence", "broker_write_performed",
        "execution_authority", "content_hash",
    }
)


class GenericLiveDynamicOperationalProofError(ValueError):
    pass


def _hash(payload: Mapping[str, Any]) -> str:
    body = copy.deepcopy(dict(payload))
    body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode()).hexdigest()


def _source_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _time(value: Any, label: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise GenericLiveDynamicOperationalProofError(f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        raise GenericLiveDynamicOperationalProofError(f"{label} needs timezone")
    return parsed


def _fresh(observed_at: Any, *, generated: dt.datetime, as_of: dt.datetime, label: str) -> None:
    observed = _time(observed_at, label)
    if observed > generated:
        raise GenericLiveDynamicOperationalProofError(f"{label} is later than generated_at")
    age = (as_of - observed).total_seconds()
    if age < 0 or age >= 120:
        raise GenericLiveDynamicOperationalProofError(f"{label} is not fresher than 120 seconds")


def _normalized_positions(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping) or not str(row.get("symbol") or ""):
            raise GenericLiveDynamicOperationalProofError("position evidence is malformed")
        try:
            quantity = float(row["quantity"] if "quantity" in row else row["qty"])
        except (KeyError, TypeError, ValueError) as exc:
            raise GenericLiveDynamicOperationalProofError("position quantity is malformed") from exc
        if quantity < 0:
            raise GenericLiveDynamicOperationalProofError("position evidence is not long-only")
        normalized.append({"symbol": str(row["symbol"]).upper(), "quantity": quantity})
    return sorted(normalized, key=lambda row: row["symbol"])


def validate_generic_live_dynamic_operational_proofs(
    payload: Mapping[str, Any], *, exact_plan: Mapping[str, Any],
    account_observation: Mapping[str, Any], raw_account_response: bytes,
    trusted_pipeline_hashes: Mapping[str, str],
    as_of: str,
) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or set(payload) != _FIELDS or payload.get("schema_version") != SCHEMA:
        raise GenericLiveDynamicOperationalProofError("operational proof fields are invalid")
    failures = validate_lane_exact_execution_plan(exact_plan)
    if failures:
        raise GenericLiveDynamicOperationalProofError("exact plan is invalid: " + ",".join(failures))
    account = validate_generic_live_dynamic_account_observation(
        account_observation, raw_account_response=raw_account_response, as_of=as_of,
    )
    evaluated = _time(as_of, "as_of")
    generated = _time(payload.get("generated_at"), "generated_at")
    if generated > evaluated or (evaluated - generated).total_seconds() >= 120:
        raise GenericLiveDynamicOperationalProofError("operational proof generation is not current")
    if payload.get("plan_hash") != exact_plan.get("content_hash"):
        raise GenericLiveDynamicOperationalProofError("operational proof plan hash differs")
    if payload.get("account_id_hash") != exact_plan.get("account_id_hash") or payload.get("account_id_hash") != account.get("account_id_hash"):
        raise GenericLiveDynamicOperationalProofError("account pin differs")
    if payload.get("account_observation_hash") != account.get("content_hash"):
        raise GenericLiveDynamicOperationalProofError("account observation hash differs")
    for field in ("deployed_sha", "expected_deployed_sha"):
        if not isinstance(payload.get(field), str) or not _GIT.fullmatch(payload[field]):
            raise GenericLiveDynamicOperationalProofError(f"{field} is invalid")
    if payload["deployed_sha"] != payload["expected_deployed_sha"]:
        raise GenericLiveDynamicOperationalProofError("deployed SHA differs")

    positions = payload.get("positions_evidence")
    if not isinstance(positions, Mapping) or set(positions) != {"observed_at", "rows", "source_hash"}:
        raise GenericLiveDynamicOperationalProofError("positions evidence fields are invalid")
    _fresh(positions["observed_at"], generated=generated, as_of=evaluated, label="positions observed_at")
    rows = _normalized_positions(positions["rows"])
    if positions.get("source_hash") != _source_hash(positions["rows"]):
        raise GenericLiveDynamicOperationalProofError("positions source hash differs")
    if rows != _normalized_positions(exact_plan["starting_positions"]):
        raise GenericLiveDynamicOperationalProofError("fresh positions differ from the exact plan")

    open_orders = payload.get("open_orders_evidence")
    if not isinstance(open_orders, Mapping) or set(open_orders) != {"observed_at", "rows", "source_hash"}:
        raise GenericLiveDynamicOperationalProofError("open-orders evidence fields are invalid")
    _fresh(open_orders["observed_at"], generated=generated, as_of=evaluated, label="open orders observed_at")
    if not isinstance(open_orders["rows"], list) or open_orders["rows"]:
        raise GenericLiveDynamicOperationalProofError("fresh open orders are present")
    if open_orders.get("source_hash") != _source_hash(open_orders["rows"]):
        raise GenericLiveDynamicOperationalProofError("open-orders source hash differs")

    orders = [*exact_plan["sell_orders"], *exact_plan["buy_orders"]]
    asset = payload.get("asset_evidence")
    if not isinstance(asset, Mapping) or set(asset) != {"observed_at", "row", "source_hash"}:
        raise GenericLiveDynamicOperationalProofError("asset evidence fields are invalid")
    _fresh(asset["observed_at"], generated=generated, as_of=evaluated, label="asset observed_at")
    if asset.get("source_hash") != _source_hash(asset["row"]):
        raise GenericLiveDynamicOperationalProofError("asset source hash differs")
    if orders:
        row = asset.get("row")
        if not isinstance(row, Mapping):
            raise GenericLiveDynamicOperationalProofError("asset evidence is missing")
        status = str(row.get("status") or "").lower().split(".")[-1]
        if str(row.get("symbol") or "").upper() != str(orders[0]["symbol"]).upper() or status != "active" or row.get("tradable") is not True:
            raise GenericLiveDynamicOperationalProofError("fresh asset does not match the exact order")
    elif asset.get("row") is not None:
        raise GenericLiveDynamicOperationalProofError("NO_TRADE must not invent asset evidence")

    runtime = payload.get("runtime_evidence")
    runtime_fields = {
        "legacy_executor_disabled", "legacy_kill_switch_armed", "generic_kill_switch_armed",
        "generic_schedule_installed", "generic_submission_adapter_deployed",
        "source_hash", "trusted_source_hash",
    }
    if not isinstance(runtime, Mapping) or set(runtime) != runtime_fields:
        raise GenericLiveDynamicOperationalProofError("runtime evidence fields are invalid")
    if runtime.get("source_hash") != runtime.get("trusted_source_hash") or not _SHA.fullmatch(str(runtime.get("source_hash") or "")):
        raise GenericLiveDynamicOperationalProofError("runtime source is not independently pinned")
    for field in runtime_fields - {"source_hash", "trusted_source_hash"}:
        if runtime.get(field) is not True:
            raise GenericLiveDynamicOperationalProofError(f"runtime {field} is not green")

    pipelines = payload.get("pipeline_evidence")
    if not isinstance(pipelines, Mapping) or set(pipelines) != set(_PIPELINES):
        raise GenericLiveDynamicOperationalProofError("pipeline evidence set is invalid")
    if not isinstance(trusted_pipeline_hashes, Mapping) or set(trusted_pipeline_hashes) != set(_PIPELINES):
        raise GenericLiveDynamicOperationalProofError("trusted pipeline evidence set is invalid")
    for name in _PIPELINES:
        evidence = pipelines[name]
        if not isinstance(evidence, Mapping) or set(evidence) != {"schema_version", "status", "account_id_hash", "plan_hash", "content_hash"}:
            raise GenericLiveDynamicOperationalProofError(f"{name} pipeline evidence is malformed")
        if evidence.get("schema_version") != f"caerus.generic_live_{name}_readiness.v1" or evidence.get("status") != "GREEN":
            raise GenericLiveDynamicOperationalProofError(f"{name} pipeline is not green")
        if evidence.get("account_id_hash") != payload["account_id_hash"] or evidence.get("plan_hash") != payload["plan_hash"]:
            raise GenericLiveDynamicOperationalProofError(f"{name} pipeline scope differs")
        expected_hash = _source_hash({k: v for k, v in evidence.items() if k != "content_hash"})
        if evidence.get("content_hash") != expected_hash or evidence["content_hash"] != trusted_pipeline_hashes.get(name):
            raise GenericLiveDynamicOperationalProofError(f"{name} pipeline evidence is not independently pinned")

    if payload.get("broker_write_performed") is not False or payload.get("execution_authority") is not False:
        raise GenericLiveDynamicOperationalProofError("operational proof must be read-only and non-authoritative")
    if payload.get("content_hash") != _hash(payload):
        raise GenericLiveDynamicOperationalProofError("operational proof content hash differs")
    return copy.deepcopy(dict(payload))


def build_generic_live_dynamic_operational_proofs(
    *, generated_at: str, exact_plan: Mapping[str, Any],
    account_observation: Mapping[str, Any], raw_account_response: bytes,
    positions_observed_at: str,
    positions: Sequence[Mapping[str, Any]], open_orders_observed_at: str,
    open_orders: Sequence[Mapping[str, Any]], asset_observed_at: str,
    asset: Mapping[str, Any] | None, deployed_sha: str, expected_deployed_sha: str,
    runtime_evidence: Mapping[str, Any], pipeline_evidence: Mapping[str, Mapping[str, Any]],
    trusted_pipeline_hashes: Mapping[str, str], as_of: str,
) -> dict[str, Any]:
    positions_rows = copy.deepcopy(list(positions))
    open_order_rows = copy.deepcopy(list(open_orders))
    body = {
        "schema_version": SCHEMA,
        "generated_at": generated_at,
        "plan_hash": exact_plan.get("content_hash"),
        "account_id_hash": exact_plan.get("account_id_hash"),
        "deployed_sha": deployed_sha,
        "expected_deployed_sha": expected_deployed_sha,
        "account_observation_hash": account_observation.get("content_hash"),
        "positions_evidence": {"observed_at": positions_observed_at, "rows": positions_rows, "source_hash": _source_hash(positions_rows)},
        "open_orders_evidence": {"observed_at": open_orders_observed_at, "rows": open_order_rows, "source_hash": _source_hash(open_order_rows)},
        "asset_evidence": {"observed_at": asset_observed_at, "row": copy.deepcopy(asset), "source_hash": _source_hash(asset)},
        "runtime_evidence": copy.deepcopy(dict(runtime_evidence)),
        "pipeline_evidence": copy.deepcopy(dict(pipeline_evidence)),
        "broker_write_performed": False,
        "execution_authority": False,
    }
    body["content_hash"] = _hash(body)
    return validate_generic_live_dynamic_operational_proofs(
        body, exact_plan=exact_plan, account_observation=account_observation,
        raw_account_response=raw_account_response,
        trusted_pipeline_hashes=trusted_pipeline_hashes, as_of=as_of,
    )


__all__ = [
    "SCHEMA", "GenericLiveDynamicOperationalProofError",
    "build_generic_live_dynamic_operational_proofs",
    "validate_generic_live_dynamic_operational_proofs",
]

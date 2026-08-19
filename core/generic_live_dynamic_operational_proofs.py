"""Causal read-only operational evidence for dynamic-balance Live preflight.

There is deliberately no generic ``seal`` helper. A green artifact can only be
built from the exact plan, freshly observed broker objects, and independently
pinned pipeline/runtime evidence.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
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
        "deployed_sha", "account_observation_hash",
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


def _strict_json(raw: bytes, *, label: str) -> Any:
    if not isinstance(raw, bytes) or not raw:
        raise GenericLiveDynamicOperationalProofError(f"raw {label} bytes are required")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise GenericLiveDynamicOperationalProofError(f"raw {label} contains duplicate keys")
            result[key] = value
        return result

    try:
        return json.loads(
            raw.decode(), object_pairs_hook=pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                GenericLiveDynamicOperationalProofError(f"raw {label} contains non-finite values")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GenericLiveDynamicOperationalProofError(f"raw {label} is invalid JSON") from exc


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
    raw_positions_response: bytes, raw_open_orders_response: bytes,
    raw_asset_response: bytes, raw_runtime_evidence: bytes,
    trusted_deployed_sha: str, trusted_runtime_hash: str,
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
    if not isinstance(trusted_deployed_sha, str) or not _GIT.fullmatch(trusted_deployed_sha):
        raise GenericLiveDynamicOperationalProofError("trusted deployed SHA is invalid")
    if payload.get("deployed_sha") != trusted_deployed_sha:
        raise GenericLiveDynamicOperationalProofError("deployed SHA differs from protected pin")

    positions = payload.get("positions_evidence")
    if not isinstance(positions, Mapping) or set(positions) != {"observed_at", "rows", "source_hash"}:
        raise GenericLiveDynamicOperationalProofError("positions evidence fields are invalid")
    _fresh(positions["observed_at"], generated=generated, as_of=evaluated, label="positions observed_at")
    raw_positions = _strict_json(raw_positions_response, label="positions response")
    if not isinstance(raw_positions, list):
        raise GenericLiveDynamicOperationalProofError("raw positions response must be an array")
    rows = _normalized_positions(raw_positions)
    if positions.get("rows") != raw_positions or positions.get("source_hash") != hashlib.sha256(raw_positions_response).hexdigest():
        raise GenericLiveDynamicOperationalProofError("positions source hash differs")
    if rows != _normalized_positions(exact_plan["starting_positions"]):
        raise GenericLiveDynamicOperationalProofError("fresh positions differ from the exact plan")

    open_orders = payload.get("open_orders_evidence")
    if not isinstance(open_orders, Mapping) or set(open_orders) != {"observed_at", "rows", "source_hash"}:
        raise GenericLiveDynamicOperationalProofError("open-orders evidence fields are invalid")
    _fresh(open_orders["observed_at"], generated=generated, as_of=evaluated, label="open orders observed_at")
    raw_open_orders = _strict_json(raw_open_orders_response, label="open-orders response")
    if not isinstance(raw_open_orders, list) or raw_open_orders:
        raise GenericLiveDynamicOperationalProofError("fresh open orders are present")
    if open_orders.get("rows") != raw_open_orders or open_orders.get("source_hash") != hashlib.sha256(raw_open_orders_response).hexdigest():
        raise GenericLiveDynamicOperationalProofError("open-orders source hash differs")

    orders = [*exact_plan["sell_orders"], *exact_plan["buy_orders"]]
    asset = payload.get("asset_evidence")
    if not isinstance(asset, Mapping) or set(asset) != {"observed_at", "row", "source_hash"}:
        raise GenericLiveDynamicOperationalProofError("asset evidence fields are invalid")
    _fresh(asset["observed_at"], generated=generated, as_of=evaluated, label="asset observed_at")
    raw_asset = _strict_json(raw_asset_response, label="asset response")
    if asset.get("row") != raw_asset or asset.get("source_hash") != hashlib.sha256(raw_asset_response).hexdigest():
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
        "source_hash",
    }
    if not isinstance(runtime, Mapping) or set(runtime) != runtime_fields:
        raise GenericLiveDynamicOperationalProofError("runtime evidence fields are invalid")
    raw_runtime = _strict_json(raw_runtime_evidence, label="runtime evidence")
    runtime_facts = {field: runtime.get(field) for field in runtime_fields if field != "source_hash"}
    if not isinstance(raw_runtime, Mapping) or dict(raw_runtime) != runtime_facts:
        raise GenericLiveDynamicOperationalProofError("runtime facts differ from protected source")
    actual_runtime_hash = hashlib.sha256(raw_runtime_evidence).hexdigest()
    if runtime.get("source_hash") != actual_runtime_hash or actual_runtime_hash != trusted_runtime_hash:
        raise GenericLiveDynamicOperationalProofError("runtime source is not independently pinned")
    for field in runtime_fields - {"source_hash"}:
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
    positions_observed_at: str, raw_positions_response: bytes,
    open_orders_observed_at: str, raw_open_orders_response: bytes,
    asset_observed_at: str, raw_asset_response: bytes,
    deployed_sha: str, trusted_deployed_sha: str, raw_runtime_evidence: bytes,
    trusted_runtime_hash: str, pipeline_evidence: Mapping[str, Mapping[str, Any]],
    trusted_pipeline_hashes: Mapping[str, str], as_of: str,
) -> dict[str, Any]:
    positions_rows = _strict_json(raw_positions_response, label="positions response")
    open_order_rows = _strict_json(raw_open_orders_response, label="open-orders response")
    asset = _strict_json(raw_asset_response, label="asset response")
    runtime_facts = _strict_json(raw_runtime_evidence, label="runtime evidence")
    if not isinstance(runtime_facts, Mapping):
        raise GenericLiveDynamicOperationalProofError("runtime evidence must be an object")
    body = {
        "schema_version": SCHEMA,
        "generated_at": generated_at,
        "plan_hash": exact_plan.get("content_hash"),
        "account_id_hash": exact_plan.get("account_id_hash"),
        "deployed_sha": deployed_sha,
        "account_observation_hash": account_observation.get("content_hash"),
        "positions_evidence": {"observed_at": positions_observed_at, "rows": positions_rows, "source_hash": hashlib.sha256(raw_positions_response).hexdigest()},
        "open_orders_evidence": {"observed_at": open_orders_observed_at, "rows": open_order_rows, "source_hash": hashlib.sha256(raw_open_orders_response).hexdigest()},
        "asset_evidence": {"observed_at": asset_observed_at, "row": copy.deepcopy(asset), "source_hash": hashlib.sha256(raw_asset_response).hexdigest()},
        "runtime_evidence": {**copy.deepcopy(dict(runtime_facts)), "source_hash": hashlib.sha256(raw_runtime_evidence).hexdigest()},
        "pipeline_evidence": copy.deepcopy(dict(pipeline_evidence)),
        "broker_write_performed": False,
        "execution_authority": False,
    }
    body["content_hash"] = _hash(body)
    return validate_generic_live_dynamic_operational_proofs(
        body, exact_plan=exact_plan, account_observation=account_observation,
        raw_account_response=raw_account_response,
        raw_positions_response=raw_positions_response,
        raw_open_orders_response=raw_open_orders_response,
        raw_asset_response=raw_asset_response,
        raw_runtime_evidence=raw_runtime_evidence,
        trusted_deployed_sha=trusted_deployed_sha,
        trusted_runtime_hash=trusted_runtime_hash,
        trusted_pipeline_hashes=trusted_pipeline_hashes, as_of=as_of,
    )


__all__ = [
    "SCHEMA", "GenericLiveDynamicOperationalProofError",
    "build_generic_live_dynamic_operational_proofs",
    "validate_generic_live_dynamic_operational_proofs",
]

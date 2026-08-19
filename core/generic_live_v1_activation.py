"""Session-bound, fail-closed preflight for the owner-approved Live v1 lane.

The artifact built here is evidence, not execution authority.  It can conclude
``READY_TO_DISARM_FOR_SESSION`` only after validating the immutable owner
decision, the exact Lyra v2 decision, the exact v4 Live plan, and explicit
operational proofs.  Missing inputs are reported as blockers rather than being
inferred from lifecycle state or mutable configuration.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import math
import re
from typing import Any, Mapping

from authority.lane_exact_plan import canonical_json, validate_lane_exact_execution_plan
from core.generic_live_candidate_config import validate_redacted_live_account_observation
from core.owner_decision import OwnerDecision, parse_owner_decision
from core.sleeve_decision import validate_sleeve_decision


GENERIC_LIVE_V1_ACTIVATION_PREFLIGHT_SCHEMA = "caerus.generic_live_v1_activation_preflight.v1"
GENERIC_LIVE_V1_ADAPTER = "CAERUS_GENERIC_LANE_V4"
GENERIC_LIVE_V1_LANE_ID = "generic-live-v1"
GENERIC_LIVE_V1_SLEEVE_ID = "caerus_lyra"
GENERIC_LIVE_V1_REARM_TRIGGERS = frozenset(
    {
        "PREFLIGHT_BREAK", "SUBMISSION_BREAK", "ORDER_BREAK",
        "RECONCILIATION_BREAK", "ACCOUNTING_BREAK", "REPORTING_BREAK",
    }
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_PROOF_FIELDS = frozenset(
    {
        "deployed_sha", "expected_deployed_sha", "legacy_executor_disabled",
        "legacy_kill_switch_armed", "generic_kill_switch_armed",
        "generic_schedule_installed", "generic_submission_adapter_deployed",
        "broker_read_preflight_green", "open_order_count",
        "rollback_rearm_proven", "order_lifecycle_pipeline_green",
        "reconciliation_pipeline_green", "accounting_pipeline_green",
        "reporting_pipeline_green", "source_hashes",
    }
)
_FIELDS = frozenset(
    {
        "schema_version", "preflight_id", "evaluated_at", "status",
        "reason_codes", "effective_session", "owner_decision_id",
        "owner_decision_hash", "account_observation_hash", "account_id_hash",
        "lyra_decision_id", "lyra_decision_hash", "exact_plan_id",
        "exact_plan_hash", "deployed_sha", "expected_deployed_sha",
        "gate_results", "capital_ceiling_usd", "observed_equity_usd",
        "effective_capital_ceiling_usd", "minimum_trade_usd",
        "maximum_orders_per_session", "maximum_gross_fraction",
        "adapter_contract", "eligible_sleeve_id", "legacy_executor_reachable",
        "generic_paper_cutover_allowed", "opportunistic_test_order_allowed",
        "broker_write_performed", "configuration_mutated", "schedule_mutated",
        "kill_switch_mutated", "execution_authority", "activation_authority",
        "approval_authority", "source_hashes", "content_hash",
    }
)


class GenericLiveV1ActivationError(ValueError):
    """Raised when activation evidence is malformed or internally inconsistent."""


def _hash(payload: Mapping[str, Any]) -> str:
    body = copy.deepcopy(dict(payload))
    body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def _timestamp(value: Any, *, label: str) -> tuple[str, dt.datetime]:
    if not isinstance(value, str) or not value or value != value.strip():
        raise GenericLiveV1ActivationError(f"{label} must be a non-blank ISO timestamp")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GenericLiveV1ActivationError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise GenericLiveV1ActivationError(f"{label} must include a timezone")
    return value, parsed


def _sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise GenericLiveV1ActivationError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _git_sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not _GIT_SHA.fullmatch(value):
        raise GenericLiveV1ActivationError(f"{label} must be a full lowercase Git SHA")
    return value


def _bool(value: Any, *, label: str) -> bool:
    if type(value) is not bool:
        raise GenericLiveV1ActivationError(f"{label} must be a literal boolean")
    return value


def _owner_terms(owner: OwnerDecision) -> Mapping[str, Any]:
    if not owner.approved:
        raise GenericLiveV1ActivationError("owner decision is not APPROVE")
    terms = owner.approved_policy_patch
    expected = {
        "adapter_contract": GENERIC_LIVE_V1_ADAPTER,
        "lane_id": GENERIC_LIVE_V1_LANE_ID,
        "lane_kind": "LIVE",
        "decision_schema": "caerus.sleeve_decision.v2",
        "eligible_sleeve_ids": (GENERIC_LIVE_V1_SLEEVE_ID,),
        "selection_rule": "LYRA_ONLY_VALID_NEXT_SESSION_V2_DECISION_AND_APPROVED_V4_PLAN",
        "capital_ceiling_usd": 460.0,
        "minimum_trade_usd": 100.0,
        "maximum_orders_per_session": 1,
        "maximum_gross_fraction": 0.95,
        "whole_share_only": True,
        "long_only": True,
        "leverage_allowed": False,
        "shorting_allowed": False,
        "generic_paper_cutover_allowed": False,
        "legacy_live_executor_allowed": False,
        "opportunistic_test_orders_allowed": False,
    }
    mismatches = [field for field, expected_value in expected.items() if terms.get(field) != expected_value]
    if mismatches:
        raise GenericLiveV1ActivationError(
            "owner decision differs from approved Live v1 terms: " + ",".join(sorted(mismatches))
        )
    if frozenset(terms.get("automatic_rearm_and_rollback_triggers", ())) != GENERIC_LIVE_V1_REARM_TRIGGERS:
        raise GenericLiveV1ActivationError("owner decision automatic rollback triggers differ")
    if owner.capital_ceiling != 460.0:
        raise GenericLiveV1ActivationError("owner decision capital ceiling differs")
    return terms


def _valid_lyra_decision(
    decision: Mapping[str, Any] | None, *, session: str
) -> tuple[bool, list[str]]:
    if decision is None:
        return False, ["LYRA_V2_DECISION_MISSING"]
    failures = validate_sleeve_decision(decision)
    if failures:
        return False, ["LYRA_V2_DECISION_INVALID"]
    blockers: list[str] = []
    if decision.get("sleeve_id") != GENERIC_LIVE_V1_SLEEVE_ID:
        blockers.append("LYRA_V2_DECISION_WRONG_SLEEVE")
    if decision.get("trade_date") != session:
        blockers.append("LYRA_V2_DECISION_WRONG_SESSION")
    if decision.get("outcome") != "RECOMMENDATION" or decision.get("decision_grade") != "READY":
        blockers.append("LYRA_V2_DECISION_NOT_READY_RECOMMENDATION")
    if decision.get("liquidity_status") not in {"PASS", "CONSTRAINED"}:
        blockers.append("LYRA_V2_DECISION_LIQUIDITY_NOT_APPROVED")
    reasons = set(decision.get("reason_codes") or [])
    if reasons & {"NON_DECISION_GRADE_UNIVERSE", "EVALUATION_ONLY"}:
        blockers.append("LYRA_V2_DECISION_HISTORICAL_EVIDENCE_BLOCKER_UNRESOLVED")
    return not blockers, blockers


def _valid_plan(
    plan: Mapping[str, Any] | None,
    *, decision: Mapping[str, Any] | None,
    session: str,
    account_id_hash: str,
    effective_capital: float,
) -> tuple[bool, list[str]]:
    if plan is None:
        return False, ["EXACT_V4_PLAN_MISSING"]
    failures = validate_lane_exact_execution_plan(plan)
    if failures:
        return False, ["EXACT_V4_PLAN_INVALID"]
    blockers: list[str] = []
    if plan.get("lane_kind") != "LIVE" or plan.get("lane_id") != GENERIC_LIVE_V1_LANE_ID:
        blockers.append("EXACT_V4_PLAN_WRONG_LANE")
    if plan.get("trade_date") != session:
        blockers.append("EXACT_V4_PLAN_WRONG_SESSION")
    if plan.get("account_id_hash") != account_id_hash:
        blockers.append("EXACT_V4_PLAN_ACCOUNT_PIN_MISMATCH")
    deployable = float(plan.get("deployable_capital", math.inf))
    if not math.isfinite(deployable) or deployable > effective_capital + 0.01:
        blockers.append("EXACT_V4_PLAN_CAPITAL_CEILING_EXCEEDED")
    orders = [*plan.get("sell_orders", []), *plan.get("buy_orders", [])]
    if len(orders) > 1:
        blockers.append("EXACT_V4_PLAN_ORDER_COUNT_EXCEEDED")
    for order in orders:
        quantity = float(order.get("quantity", math.nan))
        if not math.isfinite(quantity) or quantity <= 0 or abs(quantity - round(quantity)) > 1e-9:
            blockers.append("EXACT_V4_PLAN_NOT_WHOLE_SHARE")
        if float(order.get("notional", 0.0)) + 0.01 < 100.0:
            blockers.append("EXACT_V4_PLAN_BELOW_MINIMUM_TRADE")
        contributions = order.get("sleeve_contributions")
        if not isinstance(contributions, list) or not contributions:
            blockers.append("EXACT_V4_PLAN_SLEEVE_LINEAGE_MISSING")
            continue
        for row in contributions:
            if row.get("sleeve_id") != GENERIC_LIVE_V1_SLEEVE_ID:
                blockers.append("EXACT_V4_PLAN_NON_LYRA_CONTRIBUTION")
            if decision is not None and (
                row.get("decision_id") != decision.get("decision_id")
                or row.get("decision_hash") != decision.get("content_hash")
            ):
                blockers.append("EXACT_V4_PLAN_LYRA_DECISION_LINEAGE_MISMATCH")
    target_contributions = []
    for target in plan.get("approved_target_rows", []):
        contributions = target.get("sleeve_contributions")
        if not isinstance(contributions, list) or not contributions:
            blockers.append("EXACT_V4_PLAN_TARGET_LINEAGE_MISSING")
            continue
        target_contributions.extend(contributions)
    if not target_contributions:
        blockers.append("EXACT_V4_PLAN_TARGET_LINEAGE_MISSING")
    for row in target_contributions:
        if row.get("sleeve_id") != GENERIC_LIVE_V1_SLEEVE_ID:
            blockers.append("EXACT_V4_PLAN_NON_LYRA_TARGET")
        if decision is not None and (
            row.get("decision_id") != decision.get("decision_id")
            or row.get("decision_hash") != decision.get("content_hash")
        ):
            blockers.append("EXACT_V4_PLAN_LYRA_TARGET_LINEAGE_MISMATCH")
    if float(plan.get("expected_posttrade_cash", -1.0)) < -0.01:
        blockers.append("EXACT_V4_PLAN_NEGATIVE_CASH")
    marks = {
        str(row.get("symbol")): float(row.get("price", math.nan))
        for row in plan.get("price_marks", [])
        if isinstance(row, Mapping)
    }
    expected_gross = 0.0
    for position in plan.get("expected_posttrade_positions", []):
        quantity = float(position.get("quantity", -1.0))
        if quantity < 0.0:
            blockers.append("EXACT_V4_PLAN_SHORT_POSITION")
        price = marks.get(str(position.get("symbol")), math.nan)
        if not math.isfinite(price) or price <= 0.0:
            blockers.append("EXACT_V4_PLAN_GROSS_MARK_MISSING")
        else:
            expected_gross += max(quantity, 0.0) * price
    if expected_gross > effective_capital * 0.95 + 0.01:
        blockers.append("EXACT_V4_PLAN_GROSS_LIMIT_EXCEEDED")
    return not blockers, sorted(set(blockers))


def build_generic_live_v1_activation_preflight(
    *,
    owner_decision: Mapping[str, Any],
    live_account_observation: Mapping[str, Any],
    operational_proofs: Mapping[str, Any],
    evaluated_at: str,
    lyra_decision: Mapping[str, Any] | None = None,
    exact_plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build immutable session evidence; never mutate a gate or submit an order."""

    owner = parse_owner_decision(owner_decision)
    terms = _owner_terms(owner)
    observation = validate_redacted_live_account_observation(live_account_observation)
    evaluated_raw, evaluated = _timestamp(evaluated_at, label="evaluated_at")
    _, expires = _timestamp(owner.expires_at, label="owner expires_at")
    if set(operational_proofs) != _PROOF_FIELDS:
        raise GenericLiveV1ActivationError("operational proof fields are invalid")
    deployed_sha = _git_sha(operational_proofs["deployed_sha"], label="deployed_sha")
    expected_sha = _git_sha(operational_proofs["expected_deployed_sha"], label="expected_deployed_sha")
    source_hashes = operational_proofs["source_hashes"]
    if not isinstance(source_hashes, list) or source_hashes != sorted(set(source_hashes)) or not source_hashes:
        raise GenericLiveV1ActivationError("source_hashes must be a non-empty sorted unique list")
    for value in source_hashes:
        _sha(value, label="source_hash")
    open_orders = operational_proofs["open_order_count"]
    if type(open_orders) is not int or open_orders < 0:
        raise GenericLiveV1ActivationError("open_order_count must be a non-negative integer")

    decision_green, decision_blockers = _valid_lyra_decision(
        lyra_decision, session=str(owner.effective_session)
    )
    effective_capital = min(460.0, float(observation["equity"]))
    plan_green, plan_blockers = _valid_plan(
        exact_plan,
        decision=lyra_decision if decision_green else None,
        session=str(owner.effective_session),
        account_id_hash=observation["account_id_hash"],
        effective_capital=effective_capital,
    )
    gate_results = {
        "owner_decision_current": evaluated <= expires,
        "effective_session_current": evaluated.date().isoformat() == owner.effective_session,
        "deployed_sha_match": deployed_sha == expected_sha,
        "legacy_executor_disabled": _bool(operational_proofs["legacy_executor_disabled"], label="legacy_executor_disabled"),
        "legacy_kill_switch_armed": _bool(operational_proofs["legacy_kill_switch_armed"], label="legacy_kill_switch_armed"),
        "generic_kill_switch_armed": _bool(operational_proofs["generic_kill_switch_armed"], label="generic_kill_switch_armed"),
        "generic_schedule_installed": _bool(operational_proofs["generic_schedule_installed"], label="generic_schedule_installed"),
        "generic_submission_adapter_deployed": _bool(operational_proofs["generic_submission_adapter_deployed"], label="generic_submission_adapter_deployed"),
        "broker_account_active": observation["status"].upper() == "ACTIVE" and not observation["trading_blocked"] and not observation["account_blocked"],
        "broker_read_preflight_green": _bool(operational_proofs["broker_read_preflight_green"], label="broker_read_preflight_green"),
        "open_orders_clear": open_orders == 0,
        "lyra_v2_decision_green": decision_green,
        "exact_v4_plan_green": plan_green,
        "rollback_rearm_proven": _bool(operational_proofs["rollback_rearm_proven"], label="rollback_rearm_proven"),
        "order_lifecycle_pipeline_green": _bool(operational_proofs["order_lifecycle_pipeline_green"], label="order_lifecycle_pipeline_green"),
        "reconciliation_pipeline_green": _bool(operational_proofs["reconciliation_pipeline_green"], label="reconciliation_pipeline_green"),
        "accounting_pipeline_green": _bool(operational_proofs["accounting_pipeline_green"], label="accounting_pipeline_green"),
        "reporting_pipeline_green": _bool(operational_proofs["reporting_pipeline_green"], label="reporting_pipeline_green"),
    }
    blocker_by_gate = {
        "owner_decision_current": "OWNER_DECISION_EXPIRED",
        "effective_session_current": "OWNER_EFFECTIVE_SESSION_NOT_CURRENT",
        "deployed_sha_match": "DEPLOYED_SHA_MISMATCH",
        "legacy_executor_disabled": "LEGACY_EXECUTOR_NOT_DISABLED",
        "legacy_kill_switch_armed": "LEGACY_KILL_SWITCH_NOT_ARMED",
        "generic_kill_switch_armed": "GENERIC_KILL_SWITCH_NOT_ARMED",
        "generic_schedule_installed": "GENERIC_SCHEDULE_NOT_INSTALLED",
        "generic_submission_adapter_deployed": "GENERIC_SUBMISSION_ADAPTER_NOT_DEPLOYED",
        "broker_account_active": "BROKER_ACCOUNT_NOT_ACTIVE",
        "broker_read_preflight_green": "BROKER_READ_PREFLIGHT_NOT_GREEN",
        "open_orders_clear": "BROKER_OPEN_ORDERS_PRESENT",
        "rollback_rearm_proven": "ROLLBACK_REARM_NOT_PROVEN",
        "order_lifecycle_pipeline_green": "ORDER_LIFECYCLE_PIPELINE_NOT_GREEN",
        "reconciliation_pipeline_green": "RECONCILIATION_PIPELINE_NOT_GREEN",
        "accounting_pipeline_green": "ACCOUNTING_PIPELINE_NOT_GREEN",
        "reporting_pipeline_green": "REPORTING_PIPELINE_NOT_GREEN",
    }
    blockers = [code for gate, code in blocker_by_gate.items() if not gate_results[gate]]
    blockers.extend(decision_blockers)
    blockers.extend(plan_blockers)
    blockers = sorted(set(blockers))
    body = {
        "schema_version": GENERIC_LIVE_V1_ACTIVATION_PREFLIGHT_SCHEMA,
        "preflight_id": "pending",
        "evaluated_at": evaluated_raw,
        "status": "BLOCKED" if blockers else "READY_TO_DISARM_FOR_SESSION",
        "reason_codes": blockers or ["ALL_OWNER_APPROVED_LIVE_V1_GATES_GREEN"],
        "effective_session": owner.effective_session,
        "owner_decision_id": owner.owner_decision_id,
        "owner_decision_hash": owner.content_hash,
        "account_observation_hash": observation["content_hash"],
        "account_id_hash": observation["account_id_hash"],
        "lyra_decision_id": lyra_decision.get("decision_id") if decision_green and lyra_decision else None,
        "lyra_decision_hash": lyra_decision.get("content_hash") if decision_green and lyra_decision else None,
        "exact_plan_id": exact_plan.get("plan_id") if plan_green and exact_plan else None,
        "exact_plan_hash": exact_plan.get("content_hash") if plan_green and exact_plan else None,
        "deployed_sha": deployed_sha,
        "expected_deployed_sha": expected_sha,
        "gate_results": gate_results,
        "capital_ceiling_usd": 460.0,
        "observed_equity_usd": float(observation["equity"]),
        "effective_capital_ceiling_usd": effective_capital,
        "minimum_trade_usd": 100.0,
        "maximum_orders_per_session": 1,
        "maximum_gross_fraction": 0.95,
        "adapter_contract": terms["adapter_contract"],
        "eligible_sleeve_id": GENERIC_LIVE_V1_SLEEVE_ID,
        "legacy_executor_reachable": False,
        "generic_paper_cutover_allowed": False,
        "opportunistic_test_order_allowed": False,
        "broker_write_performed": False,
        "configuration_mutated": False,
        "schedule_mutated": False,
        "kill_switch_mutated": False,
        "execution_authority": False,
        "activation_authority": False,
        "approval_authority": False,
        "source_hashes": sorted(set([owner.content_hash, observation["content_hash"], *source_hashes])),
    }
    seed = _hash(body)
    body["preflight_id"] = f"generic-live-v1-preflight:{owner.effective_session}:{seed[:24]}"
    body["content_hash"] = _hash(body)
    return validate_generic_live_v1_activation_preflight(body)


def validate_generic_live_v1_activation_preflight(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or set(payload) != _FIELDS:
        raise GenericLiveV1ActivationError("activation preflight fields are invalid")
    if payload.get("schema_version") != GENERIC_LIVE_V1_ACTIVATION_PREFLIGHT_SCHEMA:
        raise GenericLiveV1ActivationError("unsupported activation preflight schema")
    if payload.get("status") not in {"BLOCKED", "READY_TO_DISARM_FOR_SESSION"}:
        raise GenericLiveV1ActivationError("activation preflight status is invalid")
    reasons = payload.get("reason_codes")
    if not isinstance(reasons, list) or not reasons or reasons != sorted(set(reasons)):
        raise GenericLiveV1ActivationError("reason_codes must be sorted and unique")
    gates = payload.get("gate_results")
    if not isinstance(gates, Mapping) or not gates or any(type(value) is not bool for value in gates.values()):
        raise GenericLiveV1ActivationError("gate_results must be a non-empty boolean mapping")
    if payload["status"] == "READY_TO_DISARM_FOR_SESSION":
        if not all(gates.values()) or reasons != ["ALL_OWNER_APPROVED_LIVE_V1_GATES_GREEN"]:
            raise GenericLiveV1ActivationError("ready status requires every gate green")
        if not payload["lyra_decision_hash"] or not payload["exact_plan_hash"]:
            raise GenericLiveV1ActivationError("ready status requires decision and plan bindings")
    elif all(gates.values()):
        raise GenericLiveV1ActivationError("blocked status requires at least one failed gate")
    for field in ("owner_decision_hash", "account_observation_hash", "account_id_hash", "content_hash"):
        _sha(payload.get(field), label=field)
    for field in ("lyra_decision_hash", "exact_plan_hash"):
        if payload.get(field) is not None:
            _sha(payload[field], label=field)
    _git_sha(payload.get("deployed_sha"), label="deployed_sha")
    _git_sha(payload.get("expected_deployed_sha"), label="expected_deployed_sha")
    for field in (
        "legacy_executor_reachable", "generic_paper_cutover_allowed",
        "opportunistic_test_order_allowed", "broker_write_performed",
        "configuration_mutated", "schedule_mutated", "kill_switch_mutated",
        "execution_authority", "activation_authority", "approval_authority",
    ):
        if payload.get(field) is not False:
            raise GenericLiveV1ActivationError(f"preflight safety flag must remain false: {field}")
    if payload.get("adapter_contract") != GENERIC_LIVE_V1_ADAPTER or payload.get("eligible_sleeve_id") != GENERIC_LIVE_V1_SLEEVE_ID:
        raise GenericLiveV1ActivationError("activation scope differs from owner-approved Live v1")
    if payload.get("capital_ceiling_usd") != 460.0 or payload.get("minimum_trade_usd") != 100.0:
        raise GenericLiveV1ActivationError("activation economic terms differ")
    if payload.get("maximum_orders_per_session") != 1 or payload.get("maximum_gross_fraction") != 0.95:
        raise GenericLiveV1ActivationError("activation order/gross terms differ")
    hashes = payload.get("source_hashes")
    if not isinstance(hashes, list) or hashes != sorted(set(hashes)) or not hashes:
        raise GenericLiveV1ActivationError("source_hashes must be sorted and unique")
    for value in hashes:
        _sha(value, label="source_hash")
    if payload["owner_decision_hash"] not in hashes or payload["account_observation_hash"] not in hashes:
        raise GenericLiveV1ActivationError("preflight source hashes omit owner/account evidence")
    if payload["content_hash"] != _hash(payload):
        raise GenericLiveV1ActivationError("activation preflight content_hash mismatch")
    return copy.deepcopy(dict(payload))


__all__ = [
    "GENERIC_LIVE_V1_ACTIVATION_PREFLIGHT_SCHEMA", "GenericLiveV1ActivationError",
    "build_generic_live_v1_activation_preflight", "validate_generic_live_v1_activation_preflight",
]

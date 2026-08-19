"""Independent, non-executable Risk authority for one lane target.

``caerus.lane_risk_package.v1`` consumes a validated
``caerus.lane_target_package.v1`` and records Risk's terminal decision.  Risk
may approve the Decision target, constrain it monotonically, or reject it.  It
cannot create investment intent: retained sleeve contributions keep their
Decision lineage and may only be scaled down in the same proportion as their
symbol.

This module does not read runtime configuration, select a strategy, build an
execution plan, or submit an order.  Every package has
``execution_authority=false``; exact-plan authorization remains downstream.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from core.lane_target_authority import (
    LANE_TARGET_PACKAGE_SCHEMA,
    validate_lane_target_package,
)


LANE_RISK_PACKAGE_SCHEMA = "caerus.lane_risk_package.v1"
RISK_DECISIONS = frozenset({"APPROVE", "CONSTRAIN", "REJECT"})

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_SAFE_LANE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_SAFE_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,14}$")
_TOLERANCE = 1e-10
_REQUIRED_FIELDS = frozenset(
    {
        "schema_version",
        "risk_package_id",
        "trade_date",
        "evaluated_at",
        "authority",
        "decision",
        "reason_codes",
        "constraints",
        "execution_authority",
        "session_id",
        "session_hash",
        "lane_id",
        "lane_kind",
        "deployment_version",
        "capital_basis",
        "allocation_id",
        "allocation_hash",
        "target_package_id",
        "target_package_hash",
        "target_hash",
        "account_id_hash",
        "account_state_hash",
        "lane_policy_hash",
        "allocator_policy_hash",
        "risk_policy_hash",
        "capital_policy_hash",
        "execution_policy_hash",
        "reconciliation_policy_hash",
        "decision_target_cash_weight",
        "decision_target_rows",
        "approved_cash_weight",
        "approved_target_rows",
        "approved_target_hash",
        "source_hashes",
        "content_hash",
    }
)


class LaneRiskAuthorityError(ValueError):
    """Raised when independent Risk authority cannot be proven."""


def _reject_json_constant(value: str) -> None:
    raise LaneRiskAuthorityError(f"non-finite JSON constant is forbidden: {value}")


def _object_without_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LaneRiskAuthorityError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def canonical_json(payload: Any) -> str:
    """Return the sole canonical JSON representation used by this contract."""

    try:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise LaneRiskAuthorityError(
            f"lane risk package is not canonical JSON: {exc}"
        ) from exc


def lane_risk_content_hash(payload: Mapping[str, Any]) -> str:
    """Hash a package body, excluding its self-referential content hash."""

    if not isinstance(payload, Mapping):
        raise LaneRiskAuthorityError("lane risk package must be an object")
    body = copy.deepcopy(dict(payload))
    body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def _hash_json(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _strict_string(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise LaneRiskAuthorityError(
            f"{label} must be a non-blank string without surrounding whitespace"
        )
    return value


def _safe_id(value: Any, *, label: str) -> str:
    result = _strict_string(value, label=label)
    if not _SAFE_ID.fullmatch(result) or ".." in result:
        raise LaneRiskAuthorityError(f"{label} is invalid")
    return result


def _sha256(value: Any, *, label: str) -> str:
    result = _strict_string(value, label=label)
    if not _SHA256.fullmatch(result):
        raise LaneRiskAuthorityError(f"{label} must be a lowercase SHA-256 digest")
    return result


def _weight(value: Any, *, label: str, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise LaneRiskAuthorityError(f"{label} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise LaneRiskAuthorityError(f"{label} must be numeric") from exc
    lower_bound = result > 0.0 if positive else result >= 0.0
    if not math.isfinite(result) or not lower_bound or result > 1.0 + _TOLERANCE:
        qualifier = "(0, 1]" if positive else "[0, 1]"
        raise LaneRiskAuthorityError(f"{label} must be finite and within {qualifier}")
    return result


def _timestamp(value: Any, *, label: str) -> str:
    raw = _strict_string(value, label=label)
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LaneRiskAuthorityError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise LaneRiskAuthorityError(f"{label} must include a timezone")
    return raw


def _trade_date(value: Any) -> str:
    raw = _strict_string(value, label="trade_date")
    try:
        dt.date.fromisoformat(raw)
    except ValueError as exc:
        raise LaneRiskAuthorityError("trade_date must be an ISO date") from exc
    return raw


def _reason_codes(value: Any, *, decision: str) -> list[str]:
    if not isinstance(value, list):
        raise LaneRiskAuthorityError("reason_codes must be an array")
    result = [_safe_id(item, label="reason_code") for item in value]
    if len(result) != len(set(result)):
        raise LaneRiskAuthorityError("reason_codes must not contain duplicates")
    if decision in {"CONSTRAIN", "REJECT"} and not result:
        raise LaneRiskAuthorityError(f"{decision} requires at least one reason code")
    return result


def _target_rows(value: Any, *, allow_empty: bool, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or (not allow_empty and not value):
        qualifier = "an array" if allow_empty else "a non-empty array"
        raise LaneRiskAuthorityError(f"{label} must be {qualifier}")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        row_label = f"{label}[{index}]"
        if not isinstance(raw, Mapping):
            raise LaneRiskAuthorityError(f"{row_label} must be an object")
        row = copy.deepcopy(dict(raw))
        symbol = _strict_string(row.get("symbol"), label=f"{row_label}.symbol")
        if not _SAFE_SYMBOL.fullmatch(symbol) or symbol in seen:
            raise LaneRiskAuthorityError(f"{row_label}.symbol is invalid or duplicated")
        seen.add(symbol)
        if row.get("ticker") != symbol:
            raise LaneRiskAuthorityError(f"{row_label}.ticker must equal symbol")
        row_weight = _weight(
            row.get("target_weight"), label=f"{row_label}.target_weight", positive=True
        )
        contributions = row.get("sleeve_contributions")
        if not isinstance(contributions, list) or not contributions:
            raise LaneRiskAuthorityError(
                f"{row_label}.sleeve_contributions must be a non-empty array"
            )
        contributor_ids: set[str] = set()
        contribution_total = 0.0
        for contribution_index, raw_contribution in enumerate(contributions):
            contribution_label = (
                f"{row_label}.sleeve_contributions[{contribution_index}]"
            )
            if not isinstance(raw_contribution, Mapping):
                raise LaneRiskAuthorityError(f"{contribution_label} must be an object")
            contribution = dict(raw_contribution)
            sleeve_id = _safe_id(
                contribution.get("sleeve_id"), label=f"{contribution_label}.sleeve_id"
            )
            if sleeve_id in contributor_ids:
                raise LaneRiskAuthorityError(
                    f"{row_label} contains duplicate sleeve contributions"
                )
            contributor_ids.add(sleeve_id)
            contribution_total += _weight(
                contribution.get("target_weight"),
                label=f"{contribution_label}.target_weight",
                positive=True,
            )
            _safe_id(
                contribution.get("decision_id"),
                label=f"{contribution_label}.decision_id",
            )
            _sha256(
                contribution.get("decision_hash"),
                label=f"{contribution_label}.decision_hash",
            )
            _weight(
                contribution.get("lane_allocation_weight"),
                label=f"{contribution_label}.lane_allocation_weight",
            )
            _weight(
                contribution.get("sleeve_internal_weight"),
                label=f"{contribution_label}.sleeve_internal_weight",
            )
        if abs(contribution_total - row_weight) > _TOLERANCE:
            raise LaneRiskAuthorityError(
                f"{row_label} contribution weights must sum to target_weight"
            )
        result.append(row)
    return result


def _target_semantics(
    payload: Mapping[str, Any], *, cash_weight: Any, rows: Any
) -> dict[str, Any]:
    return {
        "trade_date": payload.get("trade_date"),
        "session_id": payload.get("session_id"),
        "session_hash": payload.get("session_hash"),
        "lane_id": payload.get("lane_id"),
        "lane_kind": payload.get("lane_kind"),
        "deployment_version": payload.get("deployment_version"),
        "account_id_hash": payload.get("account_id_hash"),
        "allocation_id": payload.get("allocation_id"),
        "allocation_hash": payload.get("allocation_hash"),
        "target_cash_weight": cash_weight,
        "capital_basis": payload.get("capital_basis"),
        "target_rows": rows,
    }


def _approved_target_hash(payload: Mapping[str, Any]) -> str | None:
    if payload.get("decision") == "REJECT":
        return None
    return _hash_json(
        _target_semantics(
            payload,
            cash_weight=payload.get("approved_cash_weight"),
            rows=payload.get("approved_target_rows"),
        )
    )


def _validate_target_input(payload: Mapping[str, Any]) -> None:
    if not isinstance(payload, Mapping):
        raise LaneRiskAuthorityError("lane_target_package must be an object")
    if payload.get("schema_version") != LANE_TARGET_PACKAGE_SCHEMA:
        raise LaneRiskAuthorityError("unsupported lane target package schema")
    try:
        failures = validate_lane_target_package(payload)
    except Exception as exc:
        raise LaneRiskAuthorityError(f"lane target package is invalid: {exc}") from exc
    if failures:
        raise LaneRiskAuthorityError(
            "lane target package is invalid: " + ",".join(failures)
        )
    _target_rows(payload.get("target_rows"), allow_empty=False, label="target_rows")
    cash = _weight(payload.get("target_cash_weight"), label="target_cash_weight")
    gross = sum(float(row["target_weight"]) for row in payload["target_rows"])
    if abs(cash + gross - 1.0) > _TOLERANCE:
        raise LaneRiskAuthorityError("lane target cash plus gross exposure must equal one")


def _prove_monotonic_constraint(
    *,
    decision: str,
    source_cash: float,
    source_rows: list[dict[str, Any]],
    approved_cash: float | None,
    approved_rows: list[dict[str, Any]],
) -> None:
    if decision == "REJECT":
        if approved_cash is not None or approved_rows:
            raise LaneRiskAuthorityError("REJECT must authorize no executable target")
        return

    if approved_cash is None:
        raise LaneRiskAuthorityError(f"{decision} requires approved_cash_weight")
    if decision == "APPROVE":
        if approved_cash != source_cash or approved_rows != source_rows:
            raise LaneRiskAuthorityError("APPROVE must preserve the Decision target exactly")
        return

    if approved_cash + _TOLERANCE < source_cash:
        raise LaneRiskAuthorityError("Risk may not reduce required cash")
    source_by_symbol = {row["symbol"]: row for row in source_rows}
    approved_symbols = [row["symbol"] for row in approved_rows]
    source_order = [row["symbol"] for row in source_rows]
    if any(symbol not in source_by_symbol for symbol in approved_symbols):
        raise LaneRiskAuthorityError("Risk may not add symbols")
    if approved_symbols != [symbol for symbol in source_order if symbol in approved_symbols]:
        raise LaneRiskAuthorityError("Risk must retain canonical target-row order")
    for approved in approved_rows:
        source = source_by_symbol.get(approved["symbol"])
        if source is None:
            raise LaneRiskAuthorityError("Risk may not add symbols")
        source_weight = float(source["target_weight"])
        approved_weight = float(approved["target_weight"])
        if approved_weight > source_weight + _TOLERANCE:
            raise LaneRiskAuthorityError(
                f"Risk may not increase symbol exposure: {approved['symbol']}"
            )
        scale = approved_weight / source_weight
        source_contributions = source["sleeve_contributions"]
        approved_contributions = approved["sleeve_contributions"]
        if len(approved_contributions) != len(source_contributions):
            raise LaneRiskAuthorityError(
                f"Risk may not change sleeve contributions: {approved['symbol']}"
            )
        for source_contribution, approved_contribution in zip(
            source_contributions, approved_contributions
        ):
            for key, source_value in source_contribution.items():
                if key == "target_weight":
                    expected = float(source_value) * scale
                    if abs(float(approved_contribution.get(key, -1.0)) - expected) > _TOLERANCE:
                        raise LaneRiskAuthorityError(
                            "Risk must scale sleeve contributions proportionally: "
                            f"{approved['symbol']}"
                        )
                elif approved_contribution.get(key) != source_value:
                    raise LaneRiskAuthorityError(
                        "Risk may not change sleeve contribution or decision lineage: "
                        f"{approved['symbol']}"
                    )
            if set(approved_contribution) != set(source_contribution):
                raise LaneRiskAuthorityError(
                    f"Risk may not change sleeve contribution fields: {approved['symbol']}"
                )
        for key, source_value in source.items():
            if key in {"target_weight", "sleeve_contributions"}:
                continue
            if approved.get(key) != source_value:
                raise LaneRiskAuthorityError(
                    f"Risk may not change target identity or attribution: {approved['symbol']}"
                )
        if set(approved) != set(source):
            raise LaneRiskAuthorityError(
                f"Risk may not change target fields: {approved['symbol']}"
            )

    source_total = source_cash + sum(float(row["target_weight"]) for row in source_rows)
    approved_total = approved_cash + sum(
        float(row["target_weight"]) for row in approved_rows
    )
    if abs(approved_total - source_total) > _TOLERANCE:
        raise LaneRiskAuthorityError("every exposure reduction must increase cash equally")
    if sum(float(row["target_weight"]) for row in approved_rows) > sum(
        float(row["target_weight"]) for row in source_rows
    ) + _TOLERANCE:
        raise LaneRiskAuthorityError("Risk may not increase gross exposure")


def _validate_or_raise(
    payload: Mapping[str, Any],
    *,
    lane_target_package: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise LaneRiskAuthorityError("lane risk package must be an object")
    if set(payload) != _REQUIRED_FIELDS:
        missing = sorted(_REQUIRED_FIELDS - set(payload))
        unknown = sorted(set(payload) - _REQUIRED_FIELDS)
        details = []
        if missing:
            details.append("missing fields: " + ", ".join(missing))
        if unknown:
            details.append("unknown fields: " + ", ".join(unknown))
        raise LaneRiskAuthorityError("lane risk package " + "; ".join(details))
    if payload["schema_version"] != LANE_RISK_PACKAGE_SCHEMA:
        raise LaneRiskAuthorityError("unsupported lane risk package schema")
    _safe_id(payload["risk_package_id"], label="risk_package_id")
    _trade_date(payload["trade_date"])
    _timestamp(payload["evaluated_at"], label="evaluated_at")
    if payload["authority"] != "RISK":
        raise LaneRiskAuthorityError("authority must be RISK")
    decision = _strict_string(payload["decision"], label="decision")
    if decision not in RISK_DECISIONS:
        raise LaneRiskAuthorityError(f"unsupported Risk decision: {decision}")
    _reason_codes(payload["reason_codes"], decision=decision)
    if not isinstance(payload["constraints"], Mapping):
        raise LaneRiskAuthorityError("constraints must be an object")
    canonical_json(payload["constraints"])
    if payload["execution_authority"] is not False:
        raise LaneRiskAuthorityError("lane risk packages never carry execution authority")

    for field in (
        "session_hash",
        "allocation_hash",
        "target_package_hash",
        "target_hash",
        "account_id_hash",
        "account_state_hash",
        "lane_policy_hash",
        "allocator_policy_hash",
        "risk_policy_hash",
        "capital_policy_hash",
        "execution_policy_hash",
        "reconciliation_policy_hash",
    ):
        _sha256(payload[field], label=field)
    for field in (
        "session_id",
        "deployment_version",
        "capital_basis",
        "allocation_id",
        "target_package_id",
    ):
        _safe_id(payload[field], label=field)
    lane_id = _strict_string(payload["lane_id"], label="lane_id")
    if not _SAFE_LANE.fullmatch(lane_id):
        raise LaneRiskAuthorityError("lane_id is invalid")
    if payload["lane_kind"] not in {"SHADOW", "PAPER", "LIVE"}:
        raise LaneRiskAuthorityError("lane_kind is invalid")

    source_cash = _weight(
        payload["decision_target_cash_weight"], label="decision_target_cash_weight"
    )
    source_rows = _target_rows(
        payload["decision_target_rows"],
        allow_empty=False,
        label="decision_target_rows",
    )
    if decision == "REJECT":
        if payload["approved_cash_weight"] is not None:
            raise LaneRiskAuthorityError("REJECT must have null approved_cash_weight")
        approved_cash = None
    else:
        approved_cash = _weight(
            payload["approved_cash_weight"], label="approved_cash_weight"
        )
    approved_rows = _target_rows(
        payload["approved_target_rows"],
        allow_empty=decision == "REJECT",
        label="approved_target_rows",
    )
    _prove_monotonic_constraint(
        decision=decision,
        source_cash=source_cash,
        source_rows=source_rows,
        approved_cash=approved_cash,
        approved_rows=approved_rows,
    )

    expected_target_hash = _hash_json(
        _target_semantics(
            payload,
            cash_weight=source_cash,
            rows=source_rows,
        )
    )
    if payload["target_hash"] != expected_target_hash:
        raise LaneRiskAuthorityError("Decision target hash mismatch")
    expected_approved_hash = _approved_target_hash(payload)
    if payload["approved_target_hash"] != expected_approved_hash:
        raise LaneRiskAuthorityError("approved target hash mismatch")

    expected_sources = {
        "session": payload["session_hash"],
        "allocation": payload["allocation_hash"],
        "target_package": payload["target_package_hash"],
        "target": payload["target_hash"],
        "account_id": payload["account_id_hash"],
        "account_state": payload["account_state_hash"],
        "lane_policy": payload["lane_policy_hash"],
        "allocator_policy": payload["allocator_policy_hash"],
        "risk_policy": payload["risk_policy_hash"],
        "capital_policy": payload["capital_policy_hash"],
        "execution_policy": payload["execution_policy_hash"],
        "reconciliation_policy": payload["reconciliation_policy_hash"],
    }
    if payload["source_hashes"] != expected_sources:
        raise LaneRiskAuthorityError("source hash bindings are incomplete or inconsistent")
    if payload["content_hash"] != lane_risk_content_hash(payload):
        raise LaneRiskAuthorityError("lane risk package content_hash mismatch")

    if lane_target_package is not None:
        _validate_target_input(lane_target_package)
        bindings = {
            "trade_date": "trade_date",
            "session_id": "session_id",
            "session_hash": "session_hash",
            "lane_id": "lane_id",
            "lane_kind": "lane_kind",
            "deployment_version": "deployment_version",
            "capital_basis": "capital_basis",
            "account_id_hash": "account_id_hash",
            "allocation_id": "allocation_id",
            "allocation_hash": "allocation_hash",
            "target_package_id": "target_package_id",
            "target_hash": "target_hash",
            "lane_policy_hash": "lane_policy_hash",
            "allocator_policy_hash": "allocator_policy_hash",
        }
        for risk_field, target_field in bindings.items():
            if payload[risk_field] != lane_target_package[target_field]:
                raise LaneRiskAuthorityError(f"lane target binding mismatch: {risk_field}")
        if payload["target_package_hash"] != lane_target_package["content_hash"]:
            raise LaneRiskAuthorityError("lane target package hash binding mismatch")
        if payload["decision_target_cash_weight"] != lane_target_package["target_cash_weight"]:
            raise LaneRiskAuthorityError("lane target cash binding mismatch")
        if payload["decision_target_rows"] != lane_target_package["target_rows"]:
            raise LaneRiskAuthorityError("lane target rows binding mismatch")
        policy_bindings = {
            "lane_policy_hash": "lane",
            "allocator_policy_hash": "allocator",
            "risk_policy_hash": "risk",
            "capital_policy_hash": "capital",
            "execution_policy_hash": "execution",
            "reconciliation_policy_hash": "reconciliation",
        }
        for risk_field, policy_name in policy_bindings.items():
            if payload[risk_field] != lane_target_package["policy_hashes"][policy_name]:
                raise LaneRiskAuthorityError(
                    f"lane target policy binding mismatch: {risk_field}"
                )
    return copy.deepcopy(dict(payload))


def validate_lane_risk_package(
    payload: Mapping[str, Any],
    *,
    lane_target_package: Mapping[str, Any] | None = None,
) -> list[str]:
    """Return an empty list when the Risk package and optional binding are valid."""

    try:
        _validate_or_raise(payload, lane_target_package=lane_target_package)
    except LaneRiskAuthorityError as exc:
        return [f"lane_risk:{exc}"]
    return []


def build_lane_risk_package(
    *,
    lane_target_package: Mapping[str, Any],
    account_state_hash: str,
    decision: str,
    reason_codes: Sequence[str] = (),
    constraints: Mapping[str, Any] | None = None,
    approved_cash_weight: float | None = None,
    approved_target_rows: Sequence[Mapping[str, Any]] | None = None,
    evaluated_at: str | None = None,
) -> dict[str, Any]:
    """Build one independent Risk decision bound to an immutable lane target."""

    _validate_target_input(lane_target_package)
    normalized_decision = _strict_string(decision, label="decision").upper()
    if normalized_decision not in RISK_DECISIONS:
        raise LaneRiskAuthorityError(
            f"decision must be one of {sorted(RISK_DECISIONS)}"
        )
    account_hash = _sha256(account_state_hash, label="account_state_hash")
    policy_hashes = lane_target_package.get("policy_hashes")
    if not isinstance(policy_hashes, Mapping):
        raise LaneRiskAuthorityError("lane target policy hashes are required")
    required_policy_names = {
        "lane", "allocator", "risk", "capital", "execution", "reconciliation"
    }
    if set(policy_hashes) != required_policy_names:
        raise LaneRiskAuthorityError("lane target policy hash set is incomplete")
    normalized_policy_hashes = {
        name: _sha256(policy_hashes[name], label=f"{name}_policy_hash")
        for name in sorted(required_policy_names)
    }
    source_cash = lane_target_package["target_cash_weight"]
    source_rows = copy.deepcopy(lane_target_package["target_rows"])

    if normalized_decision == "APPROVE":
        effective_cash = source_cash if approved_cash_weight is None else approved_cash_weight
        effective_rows = (
            source_rows
            if approved_target_rows is None
            else copy.deepcopy(list(approved_target_rows))
        )
    elif normalized_decision == "CONSTRAIN":
        if approved_cash_weight is None or approved_target_rows is None:
            raise LaneRiskAuthorityError(
                "CONSTRAIN requires approved_cash_weight and approved_target_rows"
            )
        effective_cash = approved_cash_weight
        effective_rows = copy.deepcopy(list(approved_target_rows))
    else:
        if approved_cash_weight is not None or approved_target_rows not in (None, []):
            raise LaneRiskAuthorityError("REJECT must authorize no executable target")
        effective_cash = None
        effective_rows = []

    effective_evaluated_at = _timestamp(
        evaluated_at or dt.datetime.now(dt.timezone.utc).isoformat(),
        label="evaluated_at",
    )
    body: dict[str, Any] = {
        "schema_version": LANE_RISK_PACKAGE_SCHEMA,
        "risk_package_id": "pending",
        "trade_date": lane_target_package["trade_date"],
        "evaluated_at": effective_evaluated_at,
        "authority": "RISK",
        "decision": normalized_decision,
        "reason_codes": list(reason_codes),
        "constraints": copy.deepcopy(dict(constraints or {})),
        "execution_authority": False,
        "session_id": lane_target_package["session_id"],
        "session_hash": lane_target_package["session_hash"],
        "lane_id": lane_target_package["lane_id"],
        "lane_kind": lane_target_package["lane_kind"],
        "deployment_version": lane_target_package["deployment_version"],
        "capital_basis": lane_target_package["capital_basis"],
        "allocation_id": lane_target_package["allocation_id"],
        "allocation_hash": lane_target_package["allocation_hash"],
        "target_package_id": lane_target_package["target_package_id"],
        "target_package_hash": lane_target_package["content_hash"],
        "target_hash": lane_target_package["target_hash"],
        "account_id_hash": lane_target_package["account_id_hash"],
        "account_state_hash": account_hash,
        "lane_policy_hash": lane_target_package["lane_policy_hash"],
        "allocator_policy_hash": lane_target_package["allocator_policy_hash"],
        "risk_policy_hash": normalized_policy_hashes["risk"],
        "capital_policy_hash": normalized_policy_hashes["capital"],
        "execution_policy_hash": normalized_policy_hashes["execution"],
        "reconciliation_policy_hash": normalized_policy_hashes["reconciliation"],
        "decision_target_cash_weight": source_cash,
        "decision_target_rows": source_rows,
        "approved_cash_weight": effective_cash,
        "approved_target_rows": effective_rows,
        "approved_target_hash": None,
        "source_hashes": {
            "session": lane_target_package["session_hash"],
            "allocation": lane_target_package["allocation_hash"],
            "target_package": lane_target_package["content_hash"],
            "target": lane_target_package["target_hash"],
            "account_id": lane_target_package["account_id_hash"],
            "account_state": account_hash,
            "lane_policy": lane_target_package["lane_policy_hash"],
            "allocator_policy": lane_target_package["allocator_policy_hash"],
            "risk_policy": normalized_policy_hashes["risk"],
            "capital_policy": normalized_policy_hashes["capital"],
            "execution_policy": normalized_policy_hashes["execution"],
            "reconciliation_policy": normalized_policy_hashes["reconciliation"],
        },
    }
    body["approved_target_hash"] = _approved_target_hash(body)
    identity_seed = _hash_json(
        {
            "target_package_hash": body["target_package_hash"],
            "account_id_hash": body["account_id_hash"],
            "account_state_hash": account_hash,
            "risk_policy_hash": normalized_policy_hashes["risk"],
            "decision": normalized_decision,
            "approved_target_hash": body["approved_target_hash"],
        }
    )
    body["risk_package_id"] = (
        f"lane-risk:{body['lane_id']}:{body['trade_date']}:{identity_seed[:24]}"
    )
    body["content_hash"] = lane_risk_content_hash(body)
    return _validate_or_raise(body, lane_target_package=lane_target_package)


def serialize_lane_risk_package(payload: Mapping[str, Any]) -> str:
    """Validate and serialize one Risk package as canonical JSON plus newline."""

    validated = _validate_or_raise(payload)
    return canonical_json(validated) + "\n"


def read_lane_risk_package(path: Path | str) -> dict[str, Any]:
    """Strictly read and validate one immutable Risk package."""

    artifact_path = Path(path)
    try:
        payload = json.loads(
            artifact_path.read_text(encoding="utf-8"),
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except LaneRiskAuthorityError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise LaneRiskAuthorityError(
            f"cannot read lane risk package {artifact_path}: {exc}"
        ) from exc
    return _validate_or_raise(payload)


def write_lane_risk_package(path: Path | str, payload: Mapping[str, Any]) -> Path:
    """Persist immutable Risk evidence, refusing to overwrite an existing file."""

    artifact_path = Path(path)
    serialized = serialize_lane_risk_package(payload)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with artifact_path.open("x", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise LaneRiskAuthorityError(
            f"lane risk package already exists: {artifact_path}"
        ) from exc
    directory_fd = os.open(str(artifact_path.parent), os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return artifact_path


__all__ = [
    "LANE_RISK_PACKAGE_SCHEMA",
    "RISK_DECISIONS",
    "LaneRiskAuthorityError",
    "build_lane_risk_package",
    "canonical_json",
    "lane_risk_content_hash",
    "read_lane_risk_package",
    "serialize_lane_risk_package",
    "validate_lane_risk_package",
    "write_lane_risk_package",
]

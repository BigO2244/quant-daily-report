"""Lane-neutral standard sleeve-decision v2 contract.

A sleeve describes only its investment recommendation and evidence.  Active
lane membership, account identity, and execution eligibility are deliberately
absent; those are joined later from a governed deployment policy.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import math
import re
from typing import Any, Mapping


SLEEVE_DECISION_SCHEMA = "caerus.sleeve_decision.v2"
SLEEVE_DECISION_BATCH_SCHEMA = "caerus.sleeve_decision_batch.v2"
SLEEVE_OUTCOMES = frozenset(
    {"RECOMMENDATION", "NO_TRADE", "UNAVAILABLE", "FROZEN", "OBSERVATION"}
)
DECISION_GRADES = frozenset({"READY", "OBSERVATION", "INCOMPLETE"})
LIQUIDITY_STATUSES = frozenset({"PASS", "CONSTRAINED", "FAIL", "UNKNOWN"})
_FORBIDDEN_LANE_FIELDS = frozenset(
    {
        "lane_id",
        "lane_kind",
        "deployment_version",
        "account_id",
        "account_id_hash",
        "broker_environment",
        "capital_eligible",
        "execution_eligible",
        "mode",
    }
)
_REQUIRED_FIELDS = frozenset(
    {
        "schema_version",
        "trade_date",
        "session_id",
        "session_hash",
        "sleeve_id",
        "outcome",
        "confidence",
        "forecast_risk",
        "capacity",
        "expected_turnover",
        "liquidity_status",
        "source_method",
        "decision_grade",
        "target_rows",
        "reason_codes",
        "source_artifacts",
        "decision_id",
        "content_hash",
    }
)
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_SAFE_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,14}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class SleeveDecisionError(ValueError):
    """Raised when a standard sleeve decision is invalid or ambiguous."""


def canonical_json(payload: Any) -> str:
    try:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise SleeveDecisionError(f"sleeve decision is not canonical JSON: {exc}") from exc


def content_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def seal_sleeve_decision(payload: Mapping[str, Any]) -> dict[str, Any]:
    body = copy.deepcopy(dict(payload))
    body.pop("content_hash", None)
    body["content_hash"] = content_hash(body)
    return body


def _required_id(value: Any, *, label: str) -> str:
    raw = str(value or "").strip()
    if not _SAFE_ID.fullmatch(raw) or ".." in raw:
        raise SleeveDecisionError(f"{label} is invalid")
    return raw


def _finite(value: Any, *, label: str, maximum: float | None = None) -> float:
    if isinstance(value, bool):
        raise SleeveDecisionError(f"{label} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise SleeveDecisionError(f"{label} must be numeric") from exc
    if not math.isfinite(result) or result < 0.0 or (
        maximum is not None and result > maximum
    ):
        raise SleeveDecisionError(f"{label} is outside its allowed range")
    return result


def validate_sleeve_decision(payload: Mapping[str, Any]) -> list[str]:
    failures: list[str] = []
    if not isinstance(payload, Mapping):
        return ["sleeve_decision:not_object"]
    missing = _REQUIRED_FIELDS - set(payload)
    if missing:
        failures.extend(f"sleeve_decision:missing:{field}" for field in sorted(missing))
    forbidden = _FORBIDDEN_LANE_FIELDS & set(payload)
    failures.extend(f"sleeve_decision:lane_field:{field}" for field in sorted(forbidden))
    if failures:
        return sorted(set(failures))
    if payload.get("schema_version") != SLEEVE_DECISION_SCHEMA:
        failures.append("sleeve_decision:schema")
    try:
        dt.date.fromisoformat(str(payload.get("trade_date") or ""))
    except ValueError:
        failures.append("sleeve_decision:trade_date")
    for field in ("session_id", "sleeve_id", "decision_id", "source_method"):
        try:
            _required_id(payload.get(field), label=field)
        except SleeveDecisionError:
            failures.append(f"sleeve_decision:{field}")
    if not _SHA256.fullmatch(str(payload.get("session_hash") or "")):
        failures.append("sleeve_decision:session_hash")
    outcome = str(payload.get("outcome") or "")
    grade = str(payload.get("decision_grade") or "")
    if outcome not in SLEEVE_OUTCOMES:
        failures.append("sleeve_decision:outcome")
    if grade not in DECISION_GRADES:
        failures.append("sleeve_decision:decision_grade")
    if str(payload.get("liquidity_status") or "") not in LIQUIDITY_STATUSES:
        failures.append("sleeve_decision:liquidity_status")
    try:
        confidence = _finite(payload.get("confidence"), label="confidence", maximum=1.0)
        turnover = _finite(payload.get("expected_turnover"), label="expected_turnover")
        if outcome == "RECOMMENDATION" and (grade != "READY" or confidence <= 0.0):
            failures.append("sleeve_decision:recommendation_grade")
        if turnover > 2.0:
            failures.append("sleeve_decision:expected_turnover")
    except SleeveDecisionError as exc:
        failures.append(f"sleeve_decision:numeric:{exc}")
    for field in ("forecast_risk", "capacity"):
        if not isinstance(payload.get(field), Mapping) or not payload.get(field):
            failures.append(f"sleeve_decision:{field}")
    targets = payload.get("target_rows")
    if not isinstance(targets, list):
        failures.append("sleeve_decision:target_rows")
    else:
        seen: set[str] = set()
        total = 0.0
        for row in targets:
            if not isinstance(row, Mapping):
                failures.append("sleeve_decision:target_row")
                continue
            symbol = str(row.get("symbol") or "")
            if not _SAFE_SYMBOL.fullmatch(symbol) or symbol in seen:
                failures.append("sleeve_decision:target_symbol")
            seen.add(symbol)
            try:
                total += _finite(
                    row.get("target_weight"), label=f"{symbol}.target_weight", maximum=1.0
                )
            except SleeveDecisionError:
                failures.append("sleeve_decision:target_weight")
        if outcome == "RECOMMENDATION":
            if not targets or abs(total - 1.0) > 1e-9:
                failures.append("sleeve_decision:target_total")
        elif targets:
            failures.append("sleeve_decision:nonrecommendation_targets")
    for field in ("reason_codes", "source_artifacts"):
        if not isinstance(payload.get(field), list):
            failures.append(f"sleeve_decision:{field}")
    body = copy.deepcopy(dict(payload))
    declared = str(body.pop("content_hash", ""))
    if not _SHA256.fullmatch(declared) or declared != content_hash(body):
        failures.append("sleeve_decision:content_hash")
    return sorted(set(failures))


def require_valid_sleeve_decision(payload: Mapping[str, Any]) -> dict[str, Any]:
    failures = validate_sleeve_decision(payload)
    if failures:
        raise SleeveDecisionError("invalid sleeve decision: " + ",".join(failures))
    return copy.deepcopy(dict(payload))


def build_sleeve_decision_batch(
    *, decisions: list[Mapping[str, Any]], generated_at: str
) -> dict[str, Any]:
    if not decisions:
        raise SleeveDecisionError("decision batch must not be empty")
    normalized = [require_valid_sleeve_decision(row) for row in decisions]
    sleeve_ids = [str(row["sleeve_id"]) for row in normalized]
    if len(sleeve_ids) != len(set(sleeve_ids)):
        raise SleeveDecisionError("decision batch contains duplicate sleeves")
    lineage = {
        (row["trade_date"], row["session_id"], row["session_hash"])
        for row in normalized
    }
    if len(lineage) != 1:
        raise SleeveDecisionError("decision batch mixes session lineage")
    try:
        parsed = dt.datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SleeveDecisionError("generated_at must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise SleeveDecisionError("generated_at must include a timezone")
    trade_date, session_id, session_hash = next(iter(lineage))
    rows = sorted(normalized, key=lambda row: str(row["sleeve_id"]))
    return {
        "schema_version": SLEEVE_DECISION_BATCH_SCHEMA,
        "trade_date": trade_date,
        "session_id": session_id,
        "session_hash": session_hash,
        "generated_at": generated_at,
        "complete_registry_coverage": True,
        "expected_sleeve_ids": [row["sleeve_id"] for row in rows],
        "decisions": rows,
        "content_hash": content_hash(rows),
    }


__all__ = [
    "SLEEVE_DECISION_BATCH_SCHEMA",
    "SLEEVE_DECISION_SCHEMA",
    "SleeveDecisionError",
    "build_sleeve_decision_batch",
    "require_valid_sleeve_decision",
    "seal_sleeve_decision",
    "validate_sleeve_decision",
]

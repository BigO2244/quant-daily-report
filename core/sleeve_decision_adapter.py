"""Deterministic adapter from legacy evaluation envelopes to decision v2.

The legacy control-plane envelope mixes evaluation evidence with deployment
and capital eligibility.  This adapter deliberately consumes only evaluation
status, opportunity availability, reasons, and immutable envelope evidence.
Risk, capacity, targets, and the meaning of a successful evaluation are
explicit caller inputs.  No lane, account, capital, or execution membership is
inferred here.
"""

from __future__ import annotations

import copy
import datetime as dt
import re
from typing import Any, Mapping, Sequence

from core.sleeve_decision import (
    SLEEVE_DECISION_BATCH_SCHEMA,
    SLEEVE_DECISION_SCHEMA,
    SleeveDecisionError,
    build_sleeve_decision_batch,
    canonical_json,
    content_hash,
    seal_sleeve_decision,
    validate_sleeve_decision,
)


LEGACY_EVALUATION_SCHEMA = "caerus_sleeve_evaluation_v1"
LEGACY_EVALUATION_BATCH_SCHEMA = "caerus_all_sleeve_evaluation_v1"
TERMINAL_EVALUATION_STATUSES = frozenset(
    {"OK", "NO_OPPORTUNITY", "BLOCKED", "FAILED"}
)
_OK_OUTCOMES = frozenset({"RECOMMENDATION", "NO_TRADE", "OBSERVATION"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_REASON = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{0,127}$")
_BATCH_FIELDS = frozenset(
    {
        "schema_version",
        "trade_date",
        "session_id",
        "session_hash",
        "generated_at",
        "complete_registry_coverage",
        "expected_sleeve_ids",
        "decisions",
        "content_hash",
    }
)
_DECISION_FIELDS = frozenset(
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
_PROFILE_REQUIRED = frozenset(
    {
        "ok_outcome",
        "confidence",
        "forecast_risk",
        "capacity",
        "expected_turnover",
        "liquidity_status",
        "source_method",
        "decision_grade",
        "target_rows",
    }
)
_PROFILE_OPTIONAL = frozenset({"reason_codes", "source_artifacts"})
_FORBIDDEN_PROFILE_FIELDS = frozenset(
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


class SleeveDecisionAdapterError(SleeveDecisionError):
    """Raised when legacy evidence cannot be adapted without ambiguity."""


def _iso_timestamp(value: Any, *, label: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise SleeveDecisionAdapterError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise SleeveDecisionAdapterError(f"{label} must include a timezone")
    return parsed


def _expected_ids(values: Sequence[str]) -> list[str]:
    if isinstance(values, (str, bytes)) or not values:
        raise SleeveDecisionAdapterError("expected_sleeve_ids must not be empty")
    normalized = [str(value or "").strip() for value in values]
    if any(not value for value in normalized) or len(normalized) != len(set(normalized)):
        raise SleeveDecisionAdapterError(
            "expected_sleeve_ids contains a blank or duplicate identity"
        )
    return normalized


def _source_artifact(
    *, artifact_type: str, schema_version: str, artifact_hash: str, sleeve_id: str | None = None
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "artifact_type": artifact_type,
        "schema_version": schema_version,
        "content_hash": artifact_hash,
    }
    if sleeve_id is not None:
        row["sleeve_id"] = sleeve_id
    return row


def _normalized_reason_codes(*groups: Any) -> list[str]:
    values: set[str] = set()
    for group in groups:
        if group is None:
            continue
        if not isinstance(group, list):
            raise SleeveDecisionAdapterError("reason_codes must be a list")
        for raw in group:
            value = str(raw or "").strip().upper()
            if not _SAFE_REASON.fullmatch(value):
                raise SleeveDecisionAdapterError("reason_codes contains an invalid value")
            values.add(value)
    return sorted(values)


def _outcome(status: str, profile: Mapping[str, Any]) -> str:
    configured = str(profile.get("ok_outcome") or "").strip().upper()
    if configured not in _OK_OUTCOMES:
        raise SleeveDecisionAdapterError(
            "ok_outcome must be RECOMMENDATION, NO_TRADE, or OBSERVATION"
        )
    if status == "OK":
        return configured
    if status == "NO_OPPORTUNITY":
        return "NO_TRADE"
    return "UNAVAILABLE"


def _validate_profile(
    sleeve_id: str, profile: Any, *, status: str
) -> dict[str, Any]:
    if not isinstance(profile, Mapping):
        raise SleeveDecisionAdapterError(f"decision input for {sleeve_id} is not an object")
    fields = set(profile)
    missing = _PROFILE_REQUIRED - fields
    unknown = fields - _PROFILE_REQUIRED - _PROFILE_OPTIONAL
    forbidden = fields & _FORBIDDEN_PROFILE_FIELDS
    if missing:
        raise SleeveDecisionAdapterError(
            f"decision input for {sleeve_id} is missing {','.join(sorted(missing))}"
        )
    if unknown or forbidden:
        names = sorted(unknown | forbidden)
        raise SleeveDecisionAdapterError(
            f"decision input for {sleeve_id} has unsupported fields: {','.join(names)}"
        )
    result = copy.deepcopy(dict(profile))
    outcome = _outcome(status, result)
    expected_grade = {
        "RECOMMENDATION": "READY",
        "OBSERVATION": "OBSERVATION",
        "NO_TRADE": "INCOMPLETE",
        "UNAVAILABLE": "INCOMPLETE",
    }[outcome]
    if str(result.get("decision_grade") or "") != expected_grade:
        raise SleeveDecisionAdapterError(
            f"decision_grade for {sleeve_id} must be {expected_grade} when outcome is {outcome}"
        )
    targets = result.get("target_rows")
    if not isinstance(targets, list):
        raise SleeveDecisionAdapterError(f"target_rows for {sleeve_id} must be a list")
    if outcome != "RECOMMENDATION" and targets:
        raise SleeveDecisionAdapterError(
            f"non-recommendation input for {sleeve_id} must have empty target_rows"
        )
    sources = result.get("source_artifacts", [])
    if not isinstance(sources, list) or any(not isinstance(row, Mapping) for row in sources):
        raise SleeveDecisionAdapterError(
            f"source_artifacts for {sleeve_id} must be a list of objects"
        )
    # Canonicalization here rejects NaN, infinities, and non-JSON values before
    # any derived identity or content hash is created.
    try:
        canonical_json(result)
    except SleeveDecisionError as exc:
        raise SleeveDecisionAdapterError(str(exc)) from exc
    return result


def _validate_evaluation_batch(
    payload: Mapping[str, Any], *, expected_sleeve_ids: Sequence[str]
) -> tuple[str, list[Mapping[str, Any]]]:
    if not isinstance(payload, Mapping):
        raise SleeveDecisionAdapterError("evaluation_batch must be an object")
    if payload.get("schema_version") != LEGACY_EVALUATION_BATCH_SCHEMA:
        raise SleeveDecisionAdapterError("evaluation_batch schema is unsupported")
    if payload.get("all_non_frozen_evaluated") is not True:
        raise SleeveDecisionAdapterError("evaluation_batch does not claim complete coverage")
    expected = _expected_ids(expected_sleeve_ids)
    if payload.get("expected_non_frozen_sleeve_ids") != expected:
        raise SleeveDecisionAdapterError("evaluation_batch expected sleeve coverage differs")
    trade_date = str(payload.get("trade_date") or "")
    try:
        dt.date.fromisoformat(trade_date)
    except ValueError as exc:
        raise SleeveDecisionAdapterError("evaluation_batch trade_date is invalid") from exc
    generated_at = _iso_timestamp(payload.get("generated_at"), label="evaluation generated_at")
    if generated_at.date() < dt.date.fromisoformat(trade_date):
        raise SleeveDecisionAdapterError("evaluation generated_at precedes trade_date")
    envelopes = payload.get("envelopes")
    if not isinstance(envelopes, list):
        raise SleeveDecisionAdapterError("evaluation_batch envelopes must be a list")
    actual: list[str] = []
    normalized: list[Mapping[str, Any]] = []
    for index, envelope in enumerate(envelopes):
        if not isinstance(envelope, Mapping):
            raise SleeveDecisionAdapterError(f"evaluation envelope {index} is not an object")
        if envelope.get("schema_version") != LEGACY_EVALUATION_SCHEMA:
            raise SleeveDecisionAdapterError(f"evaluation envelope {index} schema differs")
        if str(envelope.get("trade_date") or "") != trade_date:
            raise SleeveDecisionAdapterError(f"evaluation envelope {index} trade_date differs")
        sleeve_id = str(envelope.get("sleeve_id") or "").strip()
        actual.append(sleeve_id)
        evaluation = envelope.get("evaluation")
        if not isinstance(evaluation, Mapping):
            raise SleeveDecisionAdapterError(
                f"evaluation envelope {sleeve_id or index} has no evaluation object"
            )
        status = str(evaluation.get("status") or "").upper()
        if status not in TERMINAL_EVALUATION_STATUSES:
            raise SleeveDecisionAdapterError(
                f"evaluation envelope {sleeve_id or index} is not terminal"
            )
        lifecycle = envelope.get("lifecycle")
        if not isinstance(lifecycle, Mapping) or lifecycle.get("frozen") is not False:
            raise SleeveDecisionAdapterError(
                f"evaluation envelope {sleeve_id or index} is not a non-frozen sleeve"
            )
        opportunity = envelope.get("opportunity")
        if not isinstance(opportunity, Mapping):
            raise SleeveDecisionAdapterError(
                f"evaluation envelope {sleeve_id or index} has no opportunity object"
            )
        if status == "OK" and opportunity.get("available") is not True:
            raise SleeveDecisionAdapterError(
                f"OK evaluation envelope {sleeve_id or index} is not available"
            )
        if status != "OK" and opportunity.get("available") is not False:
            raise SleeveDecisionAdapterError(
                f"non-OK evaluation envelope {sleeve_id or index} claims availability"
            )
        try:
            canonical_json(envelope)
        except SleeveDecisionError as exc:
            raise SleeveDecisionAdapterError(str(exc)) from exc
        normalized.append(envelope)
    if actual != expected or len(actual) != len(set(actual)):
        raise SleeveDecisionAdapterError(
            "evaluation_batch envelopes do not exactly cover expected sleeves"
        )
    return trade_date, normalized


def build_sleeve_decision_v2_batch(
    *,
    evaluation_batch: Mapping[str, Any],
    expected_sleeve_ids: Sequence[str],
    session_id: str,
    session_hash: str,
    generated_at: str,
    decision_inputs: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Adapt one complete legacy evaluation batch to strict decision v2.

    ``decision_inputs`` must contain exactly one profile for every expected
    sleeve.  In particular, ``ok_outcome`` makes the distinction between a
    recommendation and a benchmark observation explicit rather than deriving
    it from legacy role, lane, or eligibility metadata.
    """

    expected = _expected_ids(expected_sleeve_ids)
    trade_date, envelopes = _validate_evaluation_batch(
        evaluation_batch, expected_sleeve_ids=expected
    )
    if not isinstance(decision_inputs, Mapping) or set(decision_inputs) != set(expected):
        raise SleeveDecisionAdapterError(
            "decision_inputs must exactly cover expected_sleeve_ids"
        )
    if not _SHA256.fullmatch(str(session_hash or "")):
        raise SleeveDecisionAdapterError("session_hash is invalid")
    generated = _iso_timestamp(generated_at, label="generated_at")
    evaluated = _iso_timestamp(
        evaluation_batch.get("generated_at"), label="evaluation generated_at"
    )
    if generated < evaluated:
        raise SleeveDecisionAdapterError("generated_at precedes evaluation evidence")

    evaluation_batch_hash = content_hash(evaluation_batch)
    decisions: list[dict[str, Any]] = []
    for envelope in envelopes:
        sleeve_id = str(envelope["sleeve_id"])
        status = str(envelope["evaluation"]["status"]).upper()
        profile = _validate_profile(sleeve_id, decision_inputs[sleeve_id], status=status)
        outcome = _outcome(status, profile)
        sources = [
            _source_artifact(
                artifact_type="sleeve_evaluation_batch",
                schema_version=LEGACY_EVALUATION_BATCH_SCHEMA,
                artifact_hash=evaluation_batch_hash,
            ),
            _source_artifact(
                artifact_type="sleeve_evaluation_envelope",
                schema_version=LEGACY_EVALUATION_SCHEMA,
                artifact_hash=content_hash(envelope),
                sleeve_id=sleeve_id,
            ),
        ]
        sources.extend(copy.deepcopy(profile.get("source_artifacts", [])))
        sources = sorted(sources, key=canonical_json)
        body: dict[str, Any] = {
            "schema_version": SLEEVE_DECISION_SCHEMA,
            "trade_date": trade_date,
            "session_id": session_id,
            "session_hash": session_hash,
            "sleeve_id": sleeve_id,
            "outcome": outcome,
            "confidence": profile["confidence"],
            "forecast_risk": copy.deepcopy(profile["forecast_risk"]),
            "capacity": copy.deepcopy(profile["capacity"]),
            "expected_turnover": profile["expected_turnover"],
            "liquidity_status": profile["liquidity_status"],
            "source_method": profile["source_method"],
            "decision_grade": profile["decision_grade"],
            "target_rows": copy.deepcopy(profile["target_rows"]),
            "reason_codes": _normalized_reason_codes(
                envelope.get("reason_codes"),
                profile.get("reason_codes", []),
                [f"EVALUATION_STATUS_{status}"],
            ),
            "source_artifacts": sources,
        }
        identity_hash = content_hash(body)
        body["decision_id"] = (
            f"sleeve-decision:v2:{trade_date}:{sleeve_id}:{identity_hash[:24]}"
        )
        decision = seal_sleeve_decision(body)
        failures = validate_sleeve_decision(decision)
        if failures:
            raise SleeveDecisionAdapterError(
                f"adapted decision for {sleeve_id} is invalid: {','.join(failures)}"
            )
        decisions.append(decision)

    batch = build_sleeve_decision_batch(decisions=decisions, generated_at=generated_at)
    failures = validate_adapted_sleeve_decision_batch(
        batch,
        expected_sleeve_ids=expected,
        evaluation_batch=evaluation_batch,
        decision_inputs=decision_inputs,
    )
    if failures:
        raise SleeveDecisionAdapterError(
            "adapted decision batch is invalid: " + ",".join(failures)
        )
    return batch


def validate_adapted_sleeve_decision_batch(
    payload: Mapping[str, Any],
    *,
    expected_sleeve_ids: Sequence[str],
    evaluation_batch: Mapping[str, Any] | None = None,
    decision_inputs: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[str]:
    """Validate strict batch shape, external coverage, and legacy hash lineage."""

    failures: list[str] = []
    if not isinstance(payload, Mapping):
        return ["sleeve_decision_adapter:not_object"]
    expected = _expected_ids(expected_sleeve_ids)
    if set(payload) != _BATCH_FIELDS:
        failures.append("sleeve_decision_adapter:batch_fields")
    if payload.get("schema_version") != SLEEVE_DECISION_BATCH_SCHEMA:
        failures.append("sleeve_decision_adapter:schema")
    if payload.get("complete_registry_coverage") is not True:
        failures.append("sleeve_decision_adapter:coverage_claim")
    sorted_expected = sorted(expected)
    if payload.get("expected_sleeve_ids") != sorted_expected:
        failures.append("sleeve_decision_adapter:expected_sleeve_ids")
    try:
        _iso_timestamp(payload.get("generated_at"), label="generated_at")
    except SleeveDecisionAdapterError:
        failures.append("sleeve_decision_adapter:generated_at")
    decisions = payload.get("decisions")
    if not isinstance(decisions, list):
        failures.append("sleeve_decision_adapter:decisions")
        return sorted(set(failures))
    actual: list[str] = []
    session_lineage: set[tuple[Any, Any, Any]] = set()
    for row in decisions:
        if not isinstance(row, Mapping):
            failures.append("sleeve_decision_adapter:decision_object")
            continue
        if set(row) != _DECISION_FIELDS:
            failures.append("sleeve_decision_adapter:decision_fields")
        failures.extend(validate_sleeve_decision(row))
        actual.append(str(row.get("sleeve_id") or ""))
        session_lineage.add(
            (row.get("trade_date"), row.get("session_id"), row.get("session_hash"))
        )
    if actual != sorted_expected or len(actual) != len(set(actual)):
        failures.append("sleeve_decision_adapter:decision_coverage")
    expected_lineage = {
        (payload.get("trade_date"), payload.get("session_id"), payload.get("session_hash"))
    }
    if session_lineage != expected_lineage:
        failures.append("sleeve_decision_adapter:session_lineage")
    if payload.get("content_hash") != content_hash(decisions):
        failures.append("sleeve_decision_adapter:content_hash")

    if evaluation_batch is not None:
        try:
            trade_date, envelopes = _validate_evaluation_batch(
                evaluation_batch, expected_sleeve_ids=expected
            )
        except SleeveDecisionAdapterError:
            failures.append("sleeve_decision_adapter:evaluation_batch")
        else:
            if payload.get("trade_date") != trade_date:
                failures.append("sleeve_decision_adapter:evaluation_trade_date")
            batch_hash = content_hash(evaluation_batch)
            envelope_by_id = {str(row["sleeve_id"]): row for row in envelopes}
            envelope_hashes = {
                str(row["sleeve_id"]): content_hash(row) for row in envelopes
            }
            for row in decisions:
                if not isinstance(row, Mapping):
                    continue
                sleeve_id = str(row.get("sleeve_id") or "")
                sources = row.get("source_artifacts")
                if not isinstance(sources, list):
                    continue
                expected_batch_source = _source_artifact(
                    artifact_type="sleeve_evaluation_batch",
                    schema_version=LEGACY_EVALUATION_BATCH_SCHEMA,
                    artifact_hash=batch_hash,
                )
                expected_envelope_source = _source_artifact(
                    artifact_type="sleeve_evaluation_envelope",
                    schema_version=LEGACY_EVALUATION_SCHEMA,
                    artifact_hash=envelope_hashes.get(sleeve_id, ""),
                    sleeve_id=sleeve_id,
                )
                if sources.count(expected_batch_source) != 1:
                    failures.append(
                        f"sleeve_decision_adapter:{sleeve_id}:batch_lineage"
                    )
                if sources.count(expected_envelope_source) != 1:
                    failures.append(
                        f"sleeve_decision_adapter:{sleeve_id}:envelope_lineage"
                    )
                envelope = envelope_by_id.get(sleeve_id)
                status = str((envelope or {}).get("evaluation", {}).get("status") or "").upper()
                if status in {"BLOCKED", "FAILED"} and row.get("outcome") != "UNAVAILABLE":
                    failures.append(
                        f"sleeve_decision_adapter:{sleeve_id}:evaluation_outcome"
                    )
                if status == "NO_OPPORTUNITY" and row.get("outcome") != "NO_TRADE":
                    failures.append(
                        f"sleeve_decision_adapter:{sleeve_id}:evaluation_outcome"
                    )
                if decision_inputs is None:
                    continue
                if not isinstance(decision_inputs, Mapping) or set(
                    decision_inputs
                ) != set(expected):
                    failures.append("sleeve_decision_adapter:decision_inputs")
                    continue
                try:
                    profile = _validate_profile(
                        sleeve_id, decision_inputs[sleeve_id], status=status
                    )
                except (KeyError, SleeveDecisionAdapterError):
                    failures.append(
                        f"sleeve_decision_adapter:{sleeve_id}:decision_input"
                    )
                    continue
                expected_values = {
                    "outcome": _outcome(status, profile),
                    "confidence": profile["confidence"],
                    "forecast_risk": profile["forecast_risk"],
                    "capacity": profile["capacity"],
                    "expected_turnover": profile["expected_turnover"],
                    "liquidity_status": profile["liquidity_status"],
                    "source_method": profile["source_method"],
                    "decision_grade": profile["decision_grade"],
                    "target_rows": profile["target_rows"],
                    "reason_codes": _normalized_reason_codes(
                        envelope.get("reason_codes"),
                        profile.get("reason_codes", []),
                        [f"EVALUATION_STATUS_{status}"],
                    ),
                }
                if any(row.get(key) != value for key, value in expected_values.items()):
                    failures.append(
                        f"sleeve_decision_adapter:{sleeve_id}:decision_input_binding"
                    )
                expected_sources = [expected_batch_source, expected_envelope_source]
                expected_sources.extend(copy.deepcopy(profile.get("source_artifacts", [])))
                expected_sources = sorted(expected_sources, key=canonical_json)
                if sources != expected_sources:
                    failures.append(
                        f"sleeve_decision_adapter:{sleeve_id}:source_binding"
                    )
                identity_body = {
                    key: row.get(key)
                    for key in _DECISION_FIELDS - {"decision_id", "content_hash"}
                }
                expected_decision_id = (
                    f"sleeve-decision:v2:{row.get('trade_date')}:{sleeve_id}:"
                    f"{content_hash(identity_body)[:24]}"
                )
                if row.get("decision_id") != expected_decision_id:
                    failures.append(
                        f"sleeve_decision_adapter:{sleeve_id}:decision_id"
                    )
    return sorted(set(failures))


__all__ = [
    "LEGACY_EVALUATION_BATCH_SCHEMA",
    "LEGACY_EVALUATION_SCHEMA",
    "SleeveDecisionAdapterError",
    "build_sleeve_decision_v2_batch",
    "validate_adapted_sleeve_decision_batch",
]

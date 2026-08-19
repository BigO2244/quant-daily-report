"""Truth-preserving replay of sealed legacy v1 decisions into v2.

This compatibility boundary is for historical dual-compute evidence only.  It
does not infer lane membership or authority.  Every derived field has one
fixed formula, and legacy facts that were not recorded remain explicitly
labelled as not recorded.
"""

from __future__ import annotations

import copy
from typing import Any, Mapping, Sequence

from core.sleeve_decision import content_hash
from core.sleeve_decision_adapter import (
    SleeveDecisionAdapterError,
    build_sleeve_decision_v2_batch,
)


REPLAY_METHOD = "factual_legacy_v1_replay"


class FactualLegacyDecisionReplayError(SleeveDecisionAdapterError):
    """Raised when sealed legacy evidence cannot be replayed without invention."""


def _legacy_index(batch: Mapping[str, Any], expected: Sequence[str]) -> dict[str, Mapping[str, Any]]:
    if batch.get("schema_version") != "caerus.sleeve_decision_batch.v1":
        raise FactualLegacyDecisionReplayError("legacy decision batch schema differs")
    rows = batch.get("decisions")
    if not isinstance(rows, list) or not rows:
        raise FactualLegacyDecisionReplayError("legacy decision batch has no decisions")
    if batch.get("content_hash") != content_hash(rows):
        raise FactualLegacyDecisionReplayError("legacy decision batch hash mismatch")
    index: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise FactualLegacyDecisionReplayError("legacy decision row is not an object")
        sleeve_id = str(row.get("sleeve_id") or "")
        body = copy.deepcopy(dict(row))
        declared = body.pop("content_hash", None)
        if declared != content_hash(body):
            raise FactualLegacyDecisionReplayError(
                f"legacy decision hash mismatch: {sleeve_id}"
            )
        if sleeve_id in index:
            raise FactualLegacyDecisionReplayError(
                f"duplicate legacy decision sleeve: {sleeve_id}"
            )
        index[sleeve_id] = row
    if set(index) != set(expected):
        raise FactualLegacyDecisionReplayError(
            "legacy decision batch does not exactly cover expected sleeves"
        )
    return index


def _targets(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = row.get("target_rows")
    if not isinstance(raw, list):
        raise FactualLegacyDecisionReplayError("legacy target_rows is not an array")
    targets = [
        {"symbol": item.get("symbol"), "target_weight": item.get("target_weight")}
        for item in raw
        if isinstance(item, Mapping)
    ]
    if len(targets) != len(raw):
        raise FactualLegacyDecisionReplayError("legacy target row is not an object")
    return targets


def _ok_outcome(row: Mapping[str, Any], targets: Sequence[Mapping[str, Any]]) -> str:
    outcome = str(row.get("outcome") or "")
    if outcome == "RECOMMENDATION" and targets:
        return "RECOMMENDATION"
    if outcome == "RECOMMENDATION":
        # A targetless functional allocation diagnostic is observable evidence,
        # not a security recommendation.  No target economics are invented.
        return "OBSERVATION"
    if outcome in {"OBSERVATION", "NO_TRADE"}:
        return outcome
    return "NO_TRADE"


def build_factual_legacy_v1_replay_batch(
    *,
    evaluation_batch: Mapping[str, Any],
    legacy_decision_batch: Mapping[str, Any],
    expected_sleeve_ids: Sequence[str],
    generated_at: str,
    additional_source_artifacts_by_sleeve: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    additional_reason_codes_by_sleeve: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    """Replay one immutable v1 batch through the strict v2 adapter.

    Formulas:

    * recommendation confidence is 1 only for a hash-valid, complete target;
    * forecast risk and capacity remain ``NOT_RECORDED_IN_LEGACY_V1``;
    * expected turnover is the full-from-cash one-way target sum (an explicit
      upper-bound proxy, not historical realized turnover);
    * liquidity remains ``UNKNOWN``;
    * targetless legacy recommendations become non-tradable observations.
    """

    expected = [str(value) for value in expected_sleeve_ids]
    legacy = _legacy_index(legacy_decision_batch, expected)
    extra_sources = additional_source_artifacts_by_sleeve or {}
    extra_reasons = additional_reason_codes_by_sleeve or {}
    if set(extra_sources) - set(expected) or set(extra_reasons) - set(expected):
        raise FactualLegacyDecisionReplayError("replay extensions name an unexpected sleeve")
    profiles: dict[str, dict[str, Any]] = {}
    for sleeve_id in expected:
        source = legacy[sleeve_id]
        targets = _targets(source)
        outcome = _ok_outcome(source, targets)
        recommendation = outcome == "RECOMMENDATION"
        source_hash = str(source["content_hash"])
        reason_codes = [
            "FACTUAL_LEGACY_V1_REPLAY",
            "LIQUIDITY_NOT_RECORDED",
            "PROFILE_GAPS_EXPLICIT",
        ]
        reason_codes.extend(str(value) for value in extra_reasons.get(sleeve_id, ()))
        if recommendation:
            reason_codes.extend(
                [
                    "CONFIDENCE_IS_SEALED_TARGET_COMPLETENESS",
                    "TURNOVER_FULL_FROM_CASH_UPPER_BOUND",
                ]
            )
        elif source.get("outcome") == "RECOMMENDATION" and not targets:
            reason_codes.append("TARGETLESS_DIAGNOSTIC_AS_OBSERVATION")
        profiles[sleeve_id] = {
            "ok_outcome": outcome,
            "confidence": 1.0 if recommendation else 0.0,
            "forecast_risk": {
                "status": "NOT_RECORDED_IN_LEGACY_V1",
                "source_decision_hash": source_hash,
            },
            "capacity": {
                "status": "NOT_RECORDED_IN_LEGACY_V1",
                "source_decision_hash": source_hash,
            },
            "expected_turnover": (
                sum(float(row["target_weight"]) for row in targets)
                if recommendation
                else 0.0
            ),
            "liquidity_status": "UNKNOWN",
            "source_method": REPLAY_METHOD,
            "decision_grade": {
                "RECOMMENDATION": "READY",
                "OBSERVATION": "OBSERVATION",
                "NO_TRADE": "INCOMPLETE",
            }[outcome],
            "target_rows": targets if recommendation else [],
            "reason_codes": reason_codes,
            "source_artifacts": [
                {
                    "artifact_type": "legacy_sleeve_decision",
                    "schema_version": source["schema_version"],
                    "content_hash": source_hash,
                    "sleeve_id": sleeve_id,
                }
            ] + [copy.deepcopy(dict(row)) for row in extra_sources.get(sleeve_id, ())],
        }
    return build_sleeve_decision_v2_batch(
        evaluation_batch=evaluation_batch,
        expected_sleeve_ids=expected,
        session_id=str(legacy_decision_batch.get("session_id") or ""),
        session_hash=str(legacy_decision_batch.get("session_hash") or ""),
        generated_at=generated_at,
        decision_inputs=profiles,
    )


__all__ = [
    "FactualLegacyDecisionReplayError",
    "REPLAY_METHOD",
    "build_factual_legacy_v1_replay_batch",
]

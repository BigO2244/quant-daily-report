from __future__ import annotations

import copy

import pytest

from core.sleeve_decision import content_hash, seal_sleeve_decision
from core.sleeve_decision_adapter import (
    SleeveDecisionAdapterError,
    build_sleeve_decision_v2_batch,
    validate_adapted_sleeve_decision_batch,
)


EXPECTED = ["caerus_polaris", "caerus_orion", "caerus_lyra"]
SESSION_HASH = "1" * 64


def _envelope(sleeve_id: str, status: str, *, available: bool) -> dict:
    return {
        "schema_version": "caerus_sleeve_evaluation_v1",
        "trade_date": "2026-08-18",
        "run_id": "run:test",
        "sleeve_id": sleeve_id,
        "strategy_type": "ignored-by-adapter",
        "role": "ignored-by-adapter",
        "lifecycle": {"status": "shadow", "frozen": False},
        "evaluation": {
            "status": status,
            "runner": "fixture",
            "message": "fixture",
            "evaluated_at": "2026-08-18T20:00:00+00:00",
        },
        "opportunity": {"available": available, "candidate_count": int(available)},
        # These legacy fields are intentionally contradictory.  The adapter
        # must neither copy them nor use them to decide outcome.
        "eligibility": {
            "capital_eligible": sleeve_id == "caerus_polaris",
            "paper_execution_eligible": sleeve_id == "caerus_orion",
            "live_execution_eligible": sleeve_id == "caerus_lyra",
        },
        "reason_codes": [f"{status}_EVIDENCE"],
    }


def _evaluation_batch() -> dict:
    return {
        "schema_version": "caerus_all_sleeve_evaluation_v1",
        "trade_date": "2026-08-18",
        "run_id": "run:test",
        "generated_at": "2026-08-18T20:00:00+00:00",
        "all_non_frozen_evaluated": True,
        "expected_non_frozen_sleeve_ids": EXPECTED,
        "envelopes": [
            _envelope("caerus_polaris", "OK", available=True),
            _envelope("caerus_orion", "OK", available=True),
            _envelope("caerus_lyra", "FAILED", available=False),
        ],
    }


def _profile(*, outcome: str, grade: str, symbol: str | None = None) -> dict:
    return {
        "ok_outcome": outcome,
        "confidence": 0.8 if grade == "READY" else 0.0,
        "forecast_risk": {"annualized_volatility": 0.17},
        "capacity": {"estimated_usd": 100000.0},
        "expected_turnover": 0.2,
        "liquidity_status": "PASS",
        "source_method": "explicit_adapter_fixture",
        "decision_grade": grade,
        "target_rows": ([{"symbol": symbol, "target_weight": 1.0}] if symbol else []),
        "reason_codes": ["EXPLICIT_PROFILE"],
        "source_artifacts": [{"artifact_type": "forecast", "content_hash": "2" * 64}],
    }


def _profiles() -> dict:
    return {
        "caerus_polaris": _profile(outcome="OBSERVATION", grade="OBSERVATION"),
        "caerus_orion": _profile(
            outcome="RECOMMENDATION", grade="READY", symbol="AAPL"
        ),
        "caerus_lyra": _profile(outcome="NO_TRADE", grade="INCOMPLETE"),
    }


def _build() -> tuple[dict, dict]:
    evidence = _evaluation_batch()
    return evidence, build_sleeve_decision_v2_batch(
        evaluation_batch=evidence,
        expected_sleeve_ids=EXPECTED,
        session_id="session:test",
        session_hash=SESSION_HASH,
        generated_at="2026-08-18T20:01:00+00:00",
        decision_inputs=_profiles(),
    )


def test_adapter_emits_strict_v2_with_exact_coverage_and_no_authority_fields() -> None:
    evidence, batch = _build()

    assert batch["expected_sleeve_ids"] == sorted(EXPECTED)
    assert validate_adapted_sleeve_decision_batch(
        batch, expected_sleeve_ids=EXPECTED, evaluation_batch=evidence
    ) == []
    by_id = {row["sleeve_id"]: row for row in batch["decisions"]}
    assert by_id["caerus_polaris"]["outcome"] == "OBSERVATION"
    assert by_id["caerus_orion"]["outcome"] == "RECOMMENDATION"
    assert by_id["caerus_lyra"]["outcome"] == "UNAVAILABLE"
    forbidden = {
        "lane_id",
        "account_id",
        "deployment_version",
        "capital_eligible",
        "execution_eligible",
        "mode",
    }
    assert all(not (forbidden & set(row)) for row in batch["decisions"])
    assert all(
        any(
            source.get("artifact_type") == "sleeve_evaluation_envelope"
            and source.get("sleeve_id") == row["sleeve_id"]
            for source in row["source_artifacts"]
        )
        for row in batch["decisions"]
    )


def test_adapter_is_deterministic_for_identical_explicit_inputs() -> None:
    _, first = _build()
    _, second = _build()
    assert first == second


def test_adapter_rejects_missing_coverage_and_authority_smuggling() -> None:
    evidence = _evaluation_batch()
    profiles = _profiles()
    profiles.pop("caerus_lyra")
    with pytest.raises(SleeveDecisionAdapterError, match="exactly cover"):
        build_sleeve_decision_v2_batch(
            evaluation_batch=evidence,
            expected_sleeve_ids=EXPECTED,
            session_id="session:test",
            session_hash=SESSION_HASH,
            generated_at="2026-08-18T20:01:00+00:00",
            decision_inputs=profiles,
        )

    profiles = _profiles()
    profiles["caerus_orion"]["lane_id"] = "paper"
    with pytest.raises(SleeveDecisionAdapterError, match="unsupported fields: lane_id"):
        build_sleeve_decision_v2_batch(
            evaluation_batch=evidence,
            expected_sleeve_ids=EXPECTED,
            session_id="session:test",
            session_hash=SESSION_HASH,
            generated_at="2026-08-18T20:01:00+00:00",
            decision_inputs=profiles,
        )


def test_adapter_rejects_ambiguous_ok_outcome_and_noncanonical_numbers() -> None:
    evidence = _evaluation_batch()
    profiles = _profiles()
    profiles["caerus_orion"]["ok_outcome"] = "INFER_FROM_LANE"
    with pytest.raises(SleeveDecisionAdapterError, match="ok_outcome"):
        build_sleeve_decision_v2_batch(
            evaluation_batch=evidence,
            expected_sleeve_ids=EXPECTED,
            session_id="session:test",
            session_hash=SESSION_HASH,
            generated_at="2026-08-18T20:01:00+00:00",
            decision_inputs=profiles,
        )

    profiles = _profiles()
    profiles["caerus_orion"]["capacity"] = {"estimated_usd": float("nan")}
    with pytest.raises(SleeveDecisionAdapterError, match="canonical JSON"):
        build_sleeve_decision_v2_batch(
            evaluation_batch=evidence,
            expected_sleeve_ids=EXPECTED,
            session_id="session:test",
            session_hash=SESSION_HASH,
            generated_at="2026-08-18T20:01:00+00:00",
            decision_inputs=profiles,
        )


def test_validator_detects_resealed_envelope_lineage_reassignment() -> None:
    evidence, batch = _build()
    tampered = copy.deepcopy(batch)
    row = next(item for item in tampered["decisions"] if item["sleeve_id"] == "caerus_orion")
    source = next(
        item
        for item in row["source_artifacts"]
        if item.get("artifact_type") == "sleeve_evaluation_envelope"
    )
    source["content_hash"] = "f" * 64
    replacement = seal_sleeve_decision({k: v for k, v in row.items() if k != "content_hash"})
    tampered["decisions"][tampered["decisions"].index(row)] = replacement
    tampered["content_hash"] = content_hash(tampered["decisions"])

    assert "sleeve_decision_adapter:caerus_orion:envelope_lineage" in (
        validate_adapted_sleeve_decision_batch(
            tampered, expected_sleeve_ids=EXPECTED, evaluation_batch=evidence
        )
    )


def test_evidence_order_must_match_declared_registry_coverage() -> None:
    evidence = _evaluation_batch()
    evidence["envelopes"] = list(reversed(evidence["envelopes"]))
    with pytest.raises(SleeveDecisionAdapterError, match="exactly cover"):
        build_sleeve_decision_v2_batch(
            evaluation_batch=evidence,
            expected_sleeve_ids=EXPECTED,
            session_id="session:test",
            session_hash=SESSION_HASH,
            generated_at="2026-08-18T20:01:00+00:00",
            decision_inputs=_profiles(),
        )

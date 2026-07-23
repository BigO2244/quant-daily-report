from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from projects.alpha_lab.factory import (
    AppendOnlyJSONLEventStore,
    ContractValidationError,
    EventStoreIntegrityError,
    ExperimentDesign,
    HypothesisClassification,
    HypothesisManifest,
    Observation,
    ProviderNotReadyError,
    ProviderReadiness,
    ProviderRequirement,
    ProviderStatus,
    ResearchBoundaryError,
    RunManifest,
    RunState,
    canonical_hash,
    canonical_json,
    evaluate_provider_readiness,
    require_provider_ready,
)


UTC = timezone.utc
NOW = datetime(2026, 7, 14, 20, 0, tzinfo=UTC)
HASH = "a" * 64


def test_canonical_json_and_hash_are_order_independent_and_reject_nan():
    left = {"b": [2, 1], "a": {"z": True}}
    right = {"a": {"z": True}, "b": [2, 1]}
    assert canonical_json(left) == canonical_json(right)
    assert canonical_hash(left) == canonical_hash(right)
    with pytest.raises(ContractValidationError, match="NaN"):
        canonical_json({"bad": float("nan")})


def test_observation_is_immutable_hash_checked_and_point_in_time_safe():
    payload = {"revision": 0.12, "analysts": ["a", "b"]}
    observation = Observation.create(
        source_id="vendor.estimates",
        security_id="FIGI:BBG000B9XRY4",
        observed_at=NOW,
        available_at=NOW + timedelta(minutes=2),
        payload=payload,
    )
    payload["revision"] = 0.99
    assert observation.payload["revision"] == 0.12
    with pytest.raises(TypeError):
        observation.payload["revision"] = 1.0
    observation.require_consumable_at(NOW + timedelta(minutes=2))
    with pytest.raises(ContractValidationError, match="unavailable"):
        observation.require_consumable_at(NOW + timedelta(minutes=1))
    with pytest.raises(ContractValidationError, match="payload_hash"):
        Observation(
            source_id="source",
            security_id="security",
            observed_at=NOW,
            available_at=NOW,
            payload={"x": 1},
            payload_hash="0" * 64,
        )


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"source_id": ""}, "source_id"),
        ({"security_id": ""}, "security_id"),
        ({"observed_at": datetime(2026, 1, 1)}, "observed_at"),
    ],
)
def test_observation_required_fields_fail_closed(kwargs, message):
    values = {
        "source_id": "source",
        "security_id": "security",
        "observed_at": NOW,
        "available_at": NOW,
        "payload": {"x": 1},
        "payload_hash": canonical_hash({"x": 1}),
    }
    values.update(kwargs)
    with pytest.raises(ContractValidationError, match=message):
        Observation(**values)


def _design():
    return ExperimentDesign(
        primary_metric="factor_adjusted_net_return",
        benchmark="SPY",
        risk_model="market_size_value_quality_momentum",
        holding_horizon="20_trading_days",
        cost_model="spread_plus_impact_v1",
        challenge_period="2025-01-01/2025-12-31",
        maximum_variants=3,
        pass_criteria=("locked challenge return is positive",),
        kill_criteria=("point-in-time integrity failure",),
    )


def test_hypothesis_and_run_manifest_hashes_are_stable():
    hypothesis = HypothesisManifest(
        hypothesis_id="HYP-2026-002",
        title="Revision drift",
        claim="Positive estimate revisions predict positive residual returns.",
        classification=HypothesisClassification.ALPHA_CANDIDATE,
        frozen_at=NOW,
        data_contract_ids=("analyst_estimates_v1", "pit_universe_v1"),
        design=_design(),
    )
    assert hypothesis.manifest_hash == canonical_hash(hypothesis.to_dict())
    run = RunManifest(
        run_id="20260714T200000Z",
        experiment_id="EXP-2026-0002",
        hypothesis_id=hypothesis.hypothesis_id,
        state=RunState.RUNNING,
        created_at=NOW,
        hypothesis_hash=hypothesis.manifest_hash,
        code_hash=HASH,
        data_snapshot_hash=HASH,
        provider_gate_hash=HASH,
    )
    assert run.manifest_hash == canonical_hash(run.to_dict())


def test_provider_readiness_gate_is_fail_closed():
    requirement = ProviderRequirement(
        provider_id="vendor",
        dataset_id="estimates",
        required_fields=("published_at", "security_id", "estimate"),
    )
    blocked = ProviderReadiness(
        provider_id="vendor",
        dataset_id="estimates",
        status=ProviderStatus.BLOCKED,
        checked_at=NOW,
        fields_available=("security_id", "estimate"),
        historical_point_in_time_verified=False,
        evidence_hash=None,
        blockers=("timestamp audit incomplete",),
    )
    result = evaluate_provider_readiness(requirement, blocked)
    assert result.ready is False
    assert "missing_field:published_at" in result.blockers
    assert "historical_point_in_time_not_verified" in result.blockers
    with pytest.raises(ProviderNotReadyError):
        require_provider_ready(requirement, blocked)

    ready = ProviderReadiness(
        provider_id="vendor",
        dataset_id="estimates",
        status=ProviderStatus.READY,
        checked_at=NOW,
        fields_available=requirement.required_fields,
        historical_point_in_time_verified=True,
        evidence_hash=HASH,
    )
    assert require_provider_ready(requirement, ready).ready is True


def test_append_only_store_hash_chain_and_tamper_detection(tmp_path):
    store = AppendOnlyJSONLEventStore(tmp_path / "events.jsonl", research_root=tmp_path)
    observation = Observation.create(
        source_id="sec.form4",
        security_id="CIK:0000320193",
        observed_at=NOW,
        available_at=NOW + timedelta(minutes=1),
        payload={"transaction_code": "P"},
    )
    first = store.append_observation(
        event_id="obs-1",
        observation=observation,
        decision_timestamp=NOW + timedelta(minutes=1),
        recorded_at=NOW + timedelta(minutes=2),
    )
    second = store.append(
        event_id="run-1",
        event_type="run_started",
        occurred_at=NOW + timedelta(minutes=3),
        recorded_at=NOW + timedelta(minutes=3),
        payload={"run_id": "r1"},
    )
    assert second.previous_event_hash == first.event_hash
    assert [record.event_id for record in store.read_all()] == ["obs-1", "run-1"]
    with pytest.raises(EventStoreIntegrityError, match="duplicate"):
        store.append(
            event_id="run-1",
            event_type="run_started",
            occurred_at=NOW,
            recorded_at=NOW,
            payload={"run_id": "r1"},
        )

    text = store.path.read_text(encoding="utf-8")
    store.path.write_text(text.replace('"transaction_code":"P"', '"transaction_code":"S"'), encoding="utf-8")
    with pytest.raises(EventStoreIntegrityError, match="invalid event contract"):
        store.read_all()


def test_append_observation_rejects_future_data_without_writing(tmp_path):
    store = AppendOnlyJSONLEventStore(tmp_path / "events.jsonl", research_root=tmp_path)
    observation = Observation.create(
        source_id="source",
        security_id="security",
        observed_at=NOW,
        available_at=NOW + timedelta(days=1),
        payload={"value": 1},
    )
    with pytest.raises(ContractValidationError, match="unavailable"):
        store.append_observation(
            event_id="future",
            observation=observation,
            decision_timestamp=NOW,
            recorded_at=NOW,
        )
    assert not store.path.exists()


def test_event_store_rejects_paths_outside_research_root(tmp_path):
    with pytest.raises(ResearchBoundaryError):
        AppendOnlyJSONLEventStore(tmp_path.parent / "elsewhere.jsonl", research_root=tmp_path)
    forbidden = tmp_path / "execution"
    forbidden.mkdir()
    with pytest.raises(ResearchBoundaryError):
        AppendOnlyJSONLEventStore(forbidden / "events.jsonl", research_root=tmp_path)


def test_factory_has_no_runtime_or_order_submission_imports():
    factory_root = Path(__file__).parents[1] / "factory"
    forbidden_modules = {
        "brokers",
        "broker",
        "reconciliation",
        "daily_quant_report",
        "core",
        "scripts",
        "alpha_stack",
    }
    forbidden_calls = {"submit_market_order", "submit_option_market_order", "cancel_order"}
    for source_path in factory_root.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not any(
                    alias.name.split(".")[0] in forbidden_modules for alias in node.names
                ), source_path
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in forbidden_modules, source_path
            if isinstance(node, ast.Call):
                function = node.func
                name = getattr(function, "attr", getattr(function, "id", ""))
                assert name not in forbidden_calls, source_path

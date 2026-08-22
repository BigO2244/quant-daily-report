from __future__ import annotations

import copy
import hashlib
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

from projects.alpha_lab.control_plane.evaluator import (
    EvaluationPhase,
    EvaluatorSpec,
    run_evaluator,
)
from projects.alpha_lab.factory import (
    ContractValidationError,
    EventRecord,
    EventStoreIntegrityError,
    GlobalResearchLedger,
    canonical_hash,
)
from projects.alpha_lab.factory.canonical import format_datetime


SOURCE_TEXT = "def evaluate(packet, phase):\n    return {}\n"
FROZEN_VARIANTS = [
    {
        "variant_id": "primary",
        "variant_definition_hash": hashlib.sha256(b"primary").hexdigest(),
    },
    {
        "variant_id": "placebo",
        "variant_definition_hash": hashlib.sha256(b"placebo").hexdigest(),
    },
]
SEARCH_CENSUS = [
    {
        "search_id": "internal-grid-001",
        "search_definition_hash": hashlib.sha256(b"internal-grid-001").hexdigest(),
    }
]


def _v2_spec_payload(source_sha256: str, **overrides):
    payload = {
        "schema_version": "caerus_alpha_lab_evaluator_spec_v2",
        "hypothesis_id": "HYP-2026-006",
        "family_id": "FAM-2026-006",
        "experiment_id": "EXP-2026-0006",
        "exploratory_wave_id": "WAVE-2026-001",
        "challenge_epoch_id": "CHALLENGE-2026-001",
        "evaluator_id": "synthetic_contract_v2",
        "technique_family": "EVENT_STUDY",
        "module": "projects.alpha_lab.evaluators.synthetic_contract_v2",
        "callable_name": "evaluate",
        "maximum_variants": 2,
        "frozen_variants": copy.deepcopy(FROZEN_VARIANTS),
        "search_census": copy.deepcopy(SEARCH_CENSUS),
        "search_census_hash": canonical_hash(SEARCH_CENSUS),
        "selection_trial_units": len(SEARCH_CENSUS),
        "primary_metric": "residual_return",
        "expected_direction": "GREATER_THAN",
        "null_value": 0.0,
        "economic_hurdle": 0.0,
        "inference_method": "HOLM_BONFERRONI",
        "inference_alpha_or_q": 0.05,
        "resampling_unit": "REBALANCE_DATE_BLOCK",
        "effective_sample_floor": 30,
        "evaluator_code_sha256": source_sha256,
        "data_contract_ids": ["event_tape_v1"],
        "challenge_period": "2025-01-01/2025-12-31",
    }
    payload.update(overrides)
    return {**payload, "spec_hash": canonical_hash(payload)}


def _raw_result():
    return {
        "variant_count": 2,
        "primary_metric_name": "residual_return",
        "orders_submitted": False,
        "search_census": copy.deepcopy(SEARCH_CENSUS),
        "search_census_hash": canonical_hash(SEARCH_CENSUS),
        "selection_trial_units": len(SEARCH_CENSUS),
        "variants": [
            {
                **copy.deepcopy(contract),
                "evidence_verdict": "NEGATIVE",
                "primary_metric_value": -0.01,
                "p_value": None,
                "inference_eligible": False,
                "ineligibility_reasons": ["NO_VALID_P_VALUE"],
                "stress_scenario_pass": False,
                "capacity_and_concentration_pass": False,
                "effective_sample_size": 20,
            }
            for contract in FROZEN_VARIANTS
        ],
    }


def _packet(spec: EvaluatorSpec):
    return {
        "data_gate_status": "READY_FOR_FROZEN_EVALUATOR",
        "hypothesis_id": spec.hypothesis_id,
        "assets": {"event_tape_v1": {}},
    }


def _install_evaluator(monkeypatch, tmp_path: Path, raw):
    source = tmp_path / "synthetic_contract_v2.py"
    source.write_text(SOURCE_TEXT, encoding="utf-8")
    module = types.ModuleType("projects.alpha_lab.evaluators.synthetic_contract_v2")
    module.__file__ = str(source)
    module.evaluate = lambda packet, phase: copy.deepcopy(raw)
    monkeypatch.setattr(
        "projects.alpha_lab.control_plane.evaluator.importlib.import_module",
        lambda name: module,
    )
    return hashlib.sha256(source.read_bytes()).hexdigest()


def test_v2_requires_ordered_variant_and_search_accounting_contracts():
    source_sha = hashlib.sha256(SOURCE_TEXT.encode("utf-8")).hexdigest()
    payload = _v2_spec_payload(source_sha)
    for field_name in (
        "frozen_variants",
        "search_census",
        "search_census_hash",
        "selection_trial_units",
    ):
        incomplete = {key: value for key, value in payload.items() if key != field_name}
        unsigned = {key: value for key, value in incomplete.items() if key != "spec_hash"}
        incomplete["spec_hash"] = canonical_hash(unsigned)
        with pytest.raises(ContractValidationError):
            EvaluatorSpec.from_dict(incomplete)


def test_v2_mechanically_checks_variant_and_search_census_sizes():
    source_sha = hashlib.sha256(SOURCE_TEXT.encode("utf-8")).hexdigest()
    with pytest.raises(ContractValidationError, match="frozen variant census"):
        EvaluatorSpec.from_dict(_v2_spec_payload(source_sha, maximum_variants=3))
    with pytest.raises(ContractValidationError, match="search_census_hash mismatch"):
        EvaluatorSpec.from_dict(
            _v2_spec_payload(source_sha, search_census_hash="0" * 64)
        )
    with pytest.raises(ContractValidationError, match="search census size"):
        EvaluatorSpec.from_dict(
            _v2_spec_payload(source_sha, selection_trial_units=2)
        )


def test_v2_binds_registered_trials_to_exact_ordered_output(monkeypatch, tmp_path):
    raw = _raw_result()
    source_sha = _install_evaluator(monkeypatch, tmp_path, raw)
    spec = EvaluatorSpec.from_dict(_v2_spec_payload(source_sha))
    result = run_evaluator(
        spec=spec,
        input_packet=_packet(spec),
        phase=EvaluationPhase.DISCOVERY,
        registered_trial_ids=("FAM-2026-006-T001", "FAM-2026-006-T002"),
    )
    assert result["registered_trial_contracts"] == [
        {"statistical_trial_id": "FAM-2026-006-T001", **FROZEN_VARIANTS[0]},
        {"statistical_trial_id": "FAM-2026-006-T002", **FROZEN_VARIANTS[1]},
    ]
    assert result["frozen_variant_contract_hash"] == canonical_hash(
        FROZEN_VARIANTS
    )
    assert result["search_census_hash"] == canonical_hash(SEARCH_CENSUS)
    assert result["selection_trial_units"] == len(SEARCH_CENSUS)


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        ("variant_order", "frozen ordered contract"),
        ("variant_definition", "frozen ordered contract"),
        ("search_census", "search census differs"),
        ("selection_units", "mechanical search census"),
    ],
)
def test_v2_rejects_output_contract_tampering(
    monkeypatch, tmp_path, tamper, message
):
    raw = _raw_result()
    if tamper == "variant_order":
        raw["variants"].reverse()
    elif tamper == "variant_definition":
        raw["variants"][0]["variant_definition_hash"] = hashlib.sha256(
            b"substituted"
        ).hexdigest()
    elif tamper == "search_census":
        raw["search_census"] = []
        raw["search_census_hash"] = canonical_hash([])
    elif tamper == "selection_units":
        raw["selection_trial_units"] = 2
    source_sha = _install_evaluator(monkeypatch, tmp_path, raw)
    spec = EvaluatorSpec.from_dict(_v2_spec_payload(source_sha))
    with pytest.raises(ContractValidationError, match=message):
        run_evaluator(
            spec=spec,
            input_packet=_packet(spec),
            phase=EvaluationPhase.DISCOVERY,
            registered_trial_ids=("FAM-2026-006-T001", "FAM-2026-006-T002"),
        )


def test_v1_remains_loadable_but_read_only():
    unsigned = {
        "schema_version": "caerus_alpha_lab_evaluator_spec_v1",
        "hypothesis_id": "HYP-2026-006",
        "evaluator_id": "historical_v1",
        "technique_family": "EVENT_STUDY",
        "module": "projects.alpha_lab.evaluators.historical_v1",
        "callable_name": "evaluate",
        "maximum_variants": 1,
        "primary_metric": "residual_return",
        "data_contract_ids": ["event_tape_v1"],
        "challenge_period": "2025-01-01/2025-12-31",
    }
    spec = EvaluatorSpec.from_dict(
        {**unsigned, "spec_hash": canonical_hash(unsigned)}
    )
    with pytest.raises(ContractValidationError, match="historical evidence"):
        run_evaluator(
            spec=spec,
            input_packet={},
            phase=EvaluationPhase.DISCOVERY,
            registered_trial_ids=("FAM-2026-006-T001",),
        )


def test_self_signed_challenge_event_is_not_canonical_authority():
    input_sha = hashlib.sha256(b"challenge-input").hexdigest()
    now = datetime(2026, 8, 22, 16, 0, tzinfo=timezone.utc)
    trial_ids = ("FAM-2026-006-T001", "FAM-2026-006-T002")
    payload = {
        "schema_version": "caerus_alpha_lab_challenge_access_v1",
        "access_id": "ACCESS-0123456789abcdef",
        "challenge_epoch_id": "CHALLENGE-2026-001",
        "trial_ids": list(trial_ids),
        "input_sha256_by_trial": {item: input_sha for item in trial_ids},
        "accessed_at": format_datetime(now),
        "consumer": "self-signed",
        "purpose": "Attempt to bypass canonical access",
        "single_use": True,
    }
    unsigned_event = {
        "schema_version": "caerus_alpha_lab_event_v1",
        "event_id": "challenge-access:ACCESS-0123456789abcdef",
        "event_type": "challenge_access_started",
        "occurred_at": format_datetime(now),
        "recorded_at": format_datetime(now),
        "payload": payload,
        "payload_hash": canonical_hash(payload),
        "previous_event_hash": None,
    }
    fake_event = EventRecord(
        event_id=unsigned_event["event_id"],
        event_type=unsigned_event["event_type"],
        occurred_at=now,
        recorded_at=now,
        payload=payload,
        payload_hash=unsigned_event["payload_hash"],
        previous_event_hash=None,
        event_hash=canonical_hash(unsigned_event),
    )
    source_sha = hashlib.sha256(SOURCE_TEXT.encode("utf-8")).hexdigest()
    spec = EvaluatorSpec.from_dict(_v2_spec_payload(source_sha))
    with pytest.raises(ContractValidationError, match="canonical ledger access event"):
        run_evaluator(
            spec=spec,
            input_packet=_packet(spec),
            phase=EvaluationPhase.CHALLENGE,
            registered_trial_ids=trial_ids,
            challenge_access_receipt=fake_event,
            challenge_input_sha256=input_sha,
        )


def test_orphan_event_on_hash_valid_ledger_is_not_challenge_authority(tmp_path):
    input_sha = hashlib.sha256(b"challenge-input").hexdigest()
    now = datetime(2026, 8, 22, 16, 0, tzinfo=timezone.utc)
    trial_ids = ("FAM-2026-006-T001", "FAM-2026-006-T002")
    payload = {
        "schema_version": "caerus_alpha_lab_challenge_access_v1",
        "access_id": "ACCESS-fedcba9876543210",
        "challenge_epoch_id": "CHALLENGE-2026-001",
        "trial_ids": list(trial_ids),
        "input_sha256_by_trial": {item: input_sha for item in trial_ids},
        "accessed_at": format_datetime(now),
        "consumer": "orphan",
        "purpose": "Attempt to bypass semantic authority",
        "single_use": True,
    }
    ledger_dir = tmp_path / "ledger"
    ledger_dir.mkdir()
    ledger = GlobalResearchLedger(
        ledger_dir / "research_events.v1.jsonl", research_root=tmp_path
    )
    receipt = ledger.store.append(
        event_id="challenge-access:ACCESS-fedcba9876543210",
        event_type=ledger.HOLDOUT_EVENT,
        occurred_at=now,
        recorded_at=now,
        payload=payload,
    )
    source_sha = hashlib.sha256(SOURCE_TEXT.encode("utf-8")).hexdigest()
    spec = EvaluatorSpec.from_dict(_v2_spec_payload(source_sha))
    with pytest.raises(EventStoreIntegrityError, match="invalid challenge access"):
        run_evaluator(
            spec=spec,
            input_packet=_packet(spec),
            phase=EvaluationPhase.CHALLENGE,
            registered_trial_ids=trial_ids,
            challenge_access_receipt=receipt,
            challenge_ledger=ledger,
            challenge_input_sha256=input_sha,
        )

from __future__ import annotations

import base64
import copy
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from projects.alpha_lab.control_plane.authenticated_ledger import (
    EVENT_ATTESTATION_SCHEMA,
    IDENTITY_BUNDLE_SCHEMA,
    load_identity_bundle,
    strict_load_json_object_bytes,
    strict_load_json_object,
)
from projects.alpha_lab.control_plane.cli import main as control_plane_main
from projects.alpha_lab.control_plane.evaluator import EvaluatorSpec
from projects.alpha_lab.factory import (
    ContractValidationError,
    ExpectedDirection,
    GENESIS_LEDGER_HEAD,
    GlobalResearchLedger,
    HypothesisFamily,
    IdentityKey,
    IdentityRegistry,
    IdentityRegistryHistory,
    IdentityRole,
    IdentityTrustAnchor,
    InferenceTrack,
    MultipleTestingMethod,
    ResearchAttestation,
    ResearchExperiment,
    ResearchPhase,
    ResearchRun,
    ResearchRunClass,
    ResearchWave,
    RegistryRelease,
    TrialOutcome,
    TrialResult,
    canonical_hash,
    canonical_json,
    deterministic_attempt_id,
    deterministic_trial_id,
    event_attestation_context_hash,
    migration_plan_attestation_context_hash,
    typed_event_payload_hash,
)


NOW = datetime(2026, 8, 22, 16, 0, tzinfo=timezone.utc)
FAMILY_ID = "FAM-2026-006"
HYPOTHESIS_ID = "HYP-2026-006"
EXPERIMENT_ID = "EXP-2026-0006"
TRIAL_ID = deterministic_trial_id(FAMILY_ID, 1)


def _sha(value: str | bytes) -> str:
    return hashlib.sha256(
        value if isinstance(value, bytes) else value.encode("utf-8")
    ).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(value) + "\n", encoding="utf-8")


def _key(identity_id: str, key_id: str, roles: tuple[IdentityRole, ...]):
    private = Ed25519PrivateKey.generate()
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    return private, IdentityKey(
        identity_id=identity_id,
        subject_id=identity_id,
        key_id=key_id,
        public_key_pem=public_pem,
        allowed_roles=roles,
        issued_at=NOW - timedelta(days=1),
    )


def _attestation(
    *,
    registry: IdentityRegistry,
    private_key: Ed25519PrivateKey,
    identity: IdentityKey,
    role: IdentityRole,
    artifact_sha256: str,
    ledger_head_hash: str,
    context_sha256: str,
    attested_at: datetime,
) -> ResearchAttestation:
    draft = ResearchAttestation(
        identity_id=identity.identity_id,
        key_id=identity.key_id,
        role=role,
        artifact_sha256=artifact_sha256,
        ledger_head_hash=ledger_head_hash,
        context_sha256=context_sha256,
        attested_at=attested_at,
        signature_b64=base64.b64encode(b"\x00" * 64).decode("ascii"),
        registry_hash=registry.registry_hash,
    )
    signature = private_key.sign(canonical_json(draft.signed_payload()).encode("utf-8"))
    return ResearchAttestation(
        **{
            **draft.__dict__,
            "signature_b64": base64.b64encode(signature).decode("ascii"),
        }
    )


def _registry():
    owner_private, owner = _key(
        "brett.owner", "owner.2026", (IdentityRole.OWNER_RATIFIER,)
    )
    author_private, author = _key(
        "research.author",
        "author.2026",
        (IdentityRole.PREREGISTRATION_AUTHOR,),
    )
    data_private, data = _key(
        "data.certifier", "data.2026", (IdentityRole.DATA_CERTIFIER,)
    )
    _, reviewer = _key(
        "reviewer.independent",
        "review.2026",
        (IdentityRole.INDEPENDENT_REVIEWER,),
    )
    exporter_private, exporter = _key(
        "ledger.exporter", "exporter.2026", (IdentityRole.LEDGER_EXPORTER,)
    )
    unsigned = IdentityRegistry(
        registry_id="caerus.research.identity",
        keys=(owner, author, data, reviewer, exporter),
        issued_at=NOW - timedelta(hours=1),
    )
    root_private = Ed25519PrivateKey.generate()
    anchor = IdentityTrustAnchor(
        anchor_id="caerus.identity.root",
        root_key_id="root.2026",
        root_public_key_pem=root_private.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8"),
        expected_registry_id=unsigned.registry_id,
    )
    release_draft = RegistryRelease(
        registry_id=unsigned.registry_id,
        registry_hash=unsigned.registry_hash,
        version=1,
        released_at=NOW - timedelta(minutes=1),
        root_key_id=anchor.root_key_id,
        signature_b64=base64.b64encode(b"\x00" * 64).decode("ascii"),
    )
    release = RegistryRelease(
        **{
            **release_draft.__dict__,
            "signature_b64": base64.b64encode(
                root_private.sign(
                    canonical_json(release_draft.signed_payload()).encode("utf-8")
                )
            ).decode("ascii"),
        }
    )
    registry = IdentityRegistry(
        registry_id=unsigned.registry_id,
        keys=(owner, author, data, reviewer, exporter),
        issued_at=unsigned.issued_at,
        release=release,
        trust_anchor=anchor,
    )
    return registry, anchor, {
        "owner": (owner_private, owner),
        "author": (author_private, author),
        "data": (data_private, data),
        "exporter": (exporter_private, exporter),
    }


def _spec() -> EvaluatorSpec:
    unsigned = {
        "schema_version": "caerus_alpha_lab_evaluator_spec_v2",
        "hypothesis_id": HYPOTHESIS_ID,
        "family_id": FAMILY_ID,
        "experiment_id": EXPERIMENT_ID,
        "exploratory_wave_id": "WAVE-2026-001",
        "challenge_epoch_id": "CHALLENGE-2026-006",
        "evaluator_id": "authenticated_recovery_v2",
        "technique_family": "EVENT_STUDY",
        "module": "projects.alpha_lab.evaluators.authenticated_recovery_v2",
        "callable_name": "evaluate",
        "maximum_variants": 1,
        "frozen_variants": [
            {
                "variant_id": "primary",
                "variant_definition_hash": _sha("primary"),
            }
        ],
        "search_census": [],
        "search_census_hash": canonical_hash([]),
        "selection_trial_units": 0,
        "primary_metric": "active_return_after_costs",
        "expected_direction": "GREATER_THAN",
        "null_value": 0.0,
        "economic_hurdle": 0.01,
        "inference_method": "HOLM_BONFERRONI",
        "inference_alpha_or_q": 0.05,
        "resampling_unit": "REBALANCE_DATE_BLOCK",
        "effective_sample_floor": 30,
        "evaluator_code_sha256": _sha("evaluator-code"),
        "data_contract_ids": ["panel_v1"],
        "challenge_period": "2025-01-01/2025-12-31",
    }
    return EvaluatorSpec.from_dict({**unsigned, "spec_hash": canonical_hash(unsigned)})


def _seed_legacy_ledger(data_root: Path, spec: EvaluatorSpec) -> GlobalResearchLedger:
    ledger_dir = data_root / "ledger"
    ledger_dir.mkdir(parents=True)
    ledger = GlobalResearchLedger(
        ledger_dir / "research_events.v1.jsonl", research_root=data_root
    )
    ledger.register_wave(
        ResearchWave(
            wave_id="WAVE-2026-001",
            track=InferenceTrack.EXPLORATORY,
            family_ids=(FAMILY_ID,),
            method=MultipleTestingMethod.BENJAMINI_YEKUTIELI,
            alpha_or_q=0.10,
            registered_at=NOW,
            policy_artifact="policy/wave.json",
            policy_sha256=_sha("wave"),
            owner_ratified=True,
        ),
        recorded_at=NOW,
    )
    ledger.register_family(
        HypothesisFamily(
            family_id=FAMILY_ID,
            wave_id="WAVE-2026-001",
            challenge_epoch_id="CHALLENGE-2026-006",
            name="Authenticated recovery family",
            economic_mechanism="A falsifiable mechanism.",
            family_scope_hash=_sha("scope"),
            primary_metric="active_return_after_costs",
            benchmark="frozen benchmark",
            expected_direction=ExpectedDirection.GREATER_THAN,
            null_value=0.0,
            economic_hurdle=0.01,
            primary_variant_id="primary",
            maximum_trial_units=1,
            selection_trial_budget=0,
            within_family_method=MultipleTestingMethod.HOLM_BONFERRONI,
            family_alpha=0.05,
            registered_at=NOW,
            source_artifact="hypotheses/HYP-2026-006.md",
            source_sha256=_sha("hypothesis"),
            owner_ratified=True,
        ),
        recorded_at=NOW,
    )
    ledger.register_experiment(
        ResearchExperiment(
            experiment_id=EXPERIMENT_ID,
            family_id=FAMILY_ID,
            hypothesis_id=HYPOTHESIS_ID,
            parent_experiment_ids=(),
            generated_after_results=False,
            generation_reason="INITIAL",
            frozen_primary_metric="active_return_after_costs",
            registered_at=NOW,
            source_artifact="experiments/EXP-2026-0006.json",
            source_sha256=_sha("experiment"),
            owner_ratified=True,
        ),
        recorded_at=NOW,
    )
    run_sha = _sha("run-1")
    ledger.register_run(
        ResearchRun(
            attempt_id=deterministic_attempt_id(run_sha),
            family_id=FAMILY_ID,
            hypothesis_id=HYPOTHESIS_ID,
            experiment_id=EXPERIMENT_ID,
            run_id="run-1",
            run_class=ResearchRunClass.MODEL_TRIAL,
            phase=ResearchPhase.DISCOVERY,
            occurred_at=NOW,
            source_artifact="runs/run-1.json",
            source_sha256=run_sha,
            statistical_trial_id=TRIAL_ID,
            primary_metric="active_return_after_costs",
            variant_id="primary",
            variant_definition_hash=_sha("primary"),
            consumes_trial_budget=True,
            preregistered=True,
            code_sha256=spec.evaluator_code_sha256,
            data_snapshot_sha256=_sha("input"),
            evaluator_spec_sha256=spec.spec_hash,
            effective_sample_floor=30,
        ),
        recorded_at=NOW,
    )
    return ledger


def _ledger_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _signed_migration_plan(
    *,
    ledger: GlobalResearchLedger,
    registry: IdentityRegistry,
    signer: tuple[Ed25519PrivateKey, IdentityKey],
) -> dict:
    records = ledger.store.read_all()
    descriptors = [
        {
            "event_id": record.event_id,
            "event_type": record.event_type,
            "typed_payload_sha256": typed_event_payload_hash(
                record.event_type, record.payload
            ),
            "record_payload_sha256": record.payload_hash,
            "previous_event_hash": record.previous_event_hash,
            "event_hash": record.event_hash,
            "recorded_at": record.recorded_at.isoformat().replace("+00:00", "Z"),
        }
        for record in records
    ]
    terminal_head = records[-1].event_hash
    plan = {
        "schema_version": "caerus_alpha_lab_migration_event_plan_v2",
        "decision": "RATIFY_GLOBAL_RESEARCH_LEDGER_MIGRATION",
        "owner": "Brett Olson",
        "recorded_at": NOW.isoformat().replace("+00:00", "Z"),
        "source_receipts": {},
        "hypothesis_sources": {},
        "census": {"legacy_event_count": len(records)},
        "family_mappings": {HYPOTHESIS_ID: FAMILY_ID},
        "family_definitions": {},
        "wave_id": "WAVE-2026-001",
        "wave_method": "BENJAMINI_YEKUTIELI",
        "wave_alpha_or_q": 0.10,
        "dependence_contract": None,
        "challenge_epoch_id": "CHALLENGE-2026-006",
        "active_registry_hash": registry.registry_hash,
        "externally_pinned_registry_hash": registry.registry_hash,
        "publication_contract": {"mode": "test_create_only"},
        "ordered_events": descriptors,
        "expected_event_count": len(records),
        "expected_terminal_head": terminal_head,
        "identity_activation_head_hash": terminal_head,
        "expected_ledger_sha256": _ledger_sha256(ledger.store.path),
    }
    identity_material = {
        key: plan[key]
        for key in (
            "schema_version",
            "decision",
            "owner",
            "recorded_at",
            "source_receipts",
            "hypothesis_sources",
            "census",
            "family_mappings",
            "family_definitions",
            "wave_id",
            "wave_method",
            "wave_alpha_or_q",
            "dependence_contract",
            "challenge_epoch_id",
            "active_registry_hash",
            "externally_pinned_registry_hash",
            "publication_contract",
        )
    }
    identity_material["schema_version"] = (
        "caerus_alpha_lab_migration_plan_identity_v1"
    )
    plan["plan_identity_sha256"] = canonical_hash(identity_material)
    owner_private, owner = signer
    attestation = _attestation(
        registry=registry,
        private_key=owner_private,
        identity=owner,
        role=IdentityRole.OWNER_RATIFIER,
        artifact_sha256=canonical_hash(plan),
        ledger_head_hash=GENESIS_LEDGER_HEAD,
        context_sha256=migration_plan_attestation_context_hash(plan),
        attested_at=NOW,
    )
    return {
        "schema_version": "caerus_alpha_lab_signed_migration_plan_v1",
        "plan": plan,
        "owner_attestation": attestation.to_dict(),
    }


def _identity_bundle(
    path: Path,
    *,
    registry: IdentityRegistry,
    anchor: IdentityTrustAnchor,
    signed_plan: dict,
) -> None:
    _write_json(
        path,
        {
            "schema_version": IDENTITY_BUNDLE_SCHEMA,
            "identity_trust_anchor": {
                "schema_version": anchor.schema_version,
                "anchor_id": anchor.anchor_id,
                "root_key_id": anchor.root_key_id,
                "root_public_key_pem": anchor.root_public_key_pem,
                "expected_registry_id": anchor.expected_registry_id,
            },
            "identity_registries": [registry.to_dict()],
            "signed_migration_plan": signed_plan,
        },
    )


def _bundle(data_root: Path, spec: EvaluatorSpec) -> Path:
    retrieved_at = NOW + timedelta(hours=1)
    raw = {
        "variant_count": 1,
        "primary_metric_name": "active_return_after_costs",
        "orders_submitted": False,
        "search_census": [],
        "search_census_hash": spec.search_census_hash,
        "selection_trial_units": 0,
        "variants": [
            {
                "variant_id": "primary",
                "variant_definition_hash": _sha("primary"),
                "evidence_verdict": "NEGATIVE",
                "primary_metric_value": -0.01,
                "p_value": None,
                "inference_eligible": False,
                "ineligibility_reasons": ["NO_VALID_P_VALUE"],
                "stress_scenario_pass": False,
                "capacity_and_concentration_pass": False,
                "effective_sample_size": 20,
            }
        ],
    }
    envelope = {
        "schema_version": "caerus_alpha_lab_evaluator_result_v2",
        "hypothesis_id": HYPOTHESIS_ID,
        "family_id": FAMILY_ID,
        "experiment_id": EXPERIMENT_ID,
        "exploratory_wave_id": "WAVE-2026-001",
        "challenge_epoch_id": "CHALLENGE-2026-006",
        "evaluator_id": spec.evaluator_id,
        "technique_family": "EVENT_STUDY",
        "phase": "DISCOVERY",
        "spec_hash": spec.spec_hash,
        "input_packet_hash": _sha("input"),
        "input_source_sha256": _sha("input"),
        "registered_trial_ids": [TRIAL_ID],
        "registered_trial_contracts": [
            {
                "statistical_trial_id": TRIAL_ID,
                "variant_id": "primary",
                "variant_definition_hash": _sha("primary"),
            }
        ],
        "frozen_variant_contract_hash": canonical_hash(spec.frozen_variant_dicts),
        "search_census_hash": spec.search_census_hash,
        "selection_trial_units": 0,
        "challenge_access_receipt_hash": None,
        "boundary_attestation": {
            "schema_version": "caerus_alpha_lab_evaluator_boundary_v1",
            "source_path": "/frozen/evaluators/authenticated_recovery_v2.py",
            "status": "PASS",
            "findings": [],
            "source_sha256": spec.evaluator_code_sha256,
        },
        "result": raw,
        "promotion_performed": False,
        "trading_behavior_changed": False,
    }
    envelope["result_hash"] = canonical_hash(envelope)
    result_bytes = (canonical_json(envelope) + "\n").encode("utf-8")
    result_sha = hashlib.sha256(result_bytes).hexdigest()
    content_hash = canonical_hash({"result.json": result_sha})
    bundle_id = "{}-{}".format(
        retrieved_at.strftime("%Y%m%dT%H%M%SZ"),
        content_hash[:12],
    )
    bundle_dir = (
        data_root
        / "control_plane"
        / "evaluator_runs"
        / HYPOTHESIS_ID
        / retrieved_at.date().isoformat()
        / bundle_id
    )
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "result.json").write_bytes(result_bytes)
    _write_json(
        bundle_dir / "manifest.json",
        {
            "schema_version": "caerus_alpha_lab_control_plane_bundle_v1",
            "classification": "RESEARCH_ONLY_NON_EXECUTIONAL",
            "bundle_id": bundle_id,
            "retrieved_at": retrieved_at,
            "source_id": "alpha_lab.control_plane",
            "files": [
                {
                    "name": "result.json",
                    "bytes": len(result_bytes),
                    "sha256": result_sha,
                }
            ],
            "credentials_persisted": False,
            "trading_behavior_changed": False,
            "promotion_performed": False,
            "purchase_performed": False,
        },
    )
    return bundle_dir


def _result_attestation_wrapper(
    path: Path,
    *,
    registry: IdentityRegistry,
    signer: tuple[Ed25519PrivateKey, IdentityKey],
    ledger: GlobalResearchLedger,
    bundle_dir: Path,
    recorded_at: datetime,
) -> None:
    result_path = bundle_dir / "result.json"
    result_sha = hashlib.sha256(result_path.read_bytes()).hexdigest()
    result = TrialResult(
        statistical_trial_id=TRIAL_ID,
        outcome=TrialOutcome.NEGATIVE,
        recorded_at=NOW + timedelta(hours=1),
        primary_metric="active_return_after_costs",
        primary_metric_value=-0.01,
        p_value=None,
        inference_eligible=False,
        ineligibility_reasons=("NO_VALID_P_VALUE",),
        stress_scenario_pass=False,
        capacity_and_concentration_pass=False,
        effective_sample_size=20,
        minimum_effective_sample=30,
        source_artifact=str(result_path),
        source_sha256=result_sha,
    )
    payload = result.to_dict()
    payload_sha = typed_event_payload_hash(GlobalResearchLedger.RESULT_EVENT, payload)
    previous_head = ledger.store.read_all()[-1].event_hash
    event_id = "result:{}".format(TRIAL_ID)
    context_sha = event_attestation_context_hash(
        event_id=event_id,
        event_type=GlobalResearchLedger.RESULT_EVENT,
        occurred_at=result.recorded_at,
        recorded_at=recorded_at,
        payload_sha256=payload_sha,
        previous_event_hash=previous_head,
    )
    author_private, author = signer
    attestation = _attestation(
        registry=registry,
        private_key=author_private,
        identity=author,
        role=IdentityRole.PREREGISTRATION_AUTHOR,
        artifact_sha256=payload_sha,
        ledger_head_hash=previous_head,
        context_sha256=context_sha,
        attested_at=recorded_at,
    )
    _write_json(
        path,
        {
            "schema_version": EVENT_ATTESTATION_SCHEMA,
            "event_id": event_id,
            "event_attestation": attestation.to_dict(),
        },
    )


def _repo(tmp_path: Path) -> tuple[Path, Path]:
    repo_root = tmp_path / "repo"
    data_root = repo_root / "outputs" / "research" / "alpha_lab"
    (repo_root / "projects" / "alpha_lab").mkdir(parents=True)
    data_root.mkdir(parents=True)
    _write_json(
        repo_root / "projects" / "alpha_lab" / "gcp_storage_policy.json",
        {
            "gcp": {
                "repository_root": str(repo_root.resolve()),
                "authoritative_data_root": str(data_root.resolve()),
            }
        },
    )
    return repo_root, data_root


def test_strict_json_loader_rejects_duplicate_keys_and_nonfinite_values(tmp_path):
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"a":1,"a":2}\n', encoding="utf-8")
    with pytest.raises(ContractValidationError, match="duplicate JSON key"):
        strict_load_json_object(duplicate)

    nonfinite = tmp_path / "nonfinite.json"
    nonfinite.write_text('{"a":NaN}\n', encoding="utf-8")
    with pytest.raises(ContractValidationError, match="non-finite"):
        strict_load_json_object(nonfinite)


def test_canonical_cli_open_fails_closed_without_identity_bundle(tmp_path):
    repo_root, data_root = _repo(tmp_path)
    with pytest.raises(ContractValidationError, match="identity-bundle"):
        control_plane_main(
            [
                "reconcile-evaluator-bundle",
                "--bundle",
                str(data_root / "missing-bundle"),
                "--spec",
                str(data_root / "missing-spec.json"),
                "--ledger",
                str(data_root / "ledger" / "research_events.v1.jsonl"),
                "--repo-root",
                str(repo_root),
                "--at",
                "2026-08-22T18:00:00Z",
            ]
        )


def test_authenticated_recovery_appends_only_with_detached_event_attestation(
    tmp_path, capsys
):
    repo_root, data_root = _repo(tmp_path)
    spec = _spec()
    ledger = _seed_legacy_ledger(data_root, spec)
    bundle_dir = _bundle(data_root, spec)
    spec_path = data_root / "specs" / "authenticated_recovery.json"
    _write_json(spec_path, spec.to_dict())
    registry, anchor, signers = _registry()
    signed_plan = _signed_migration_plan(
        ledger=ledger, registry=registry, signer=signers["owner"]
    )
    identity_bundle = data_root / "identity" / "bundle.json"
    _identity_bundle(
        identity_bundle,
        registry=registry,
        anchor=anchor,
        signed_plan=signed_plan,
    )
    trust_anchor_path = data_root / "identity" / "root-trust-anchor.json"
    _write_json(trust_anchor_path, anchor.to_dict())

    with pytest.raises(ContractValidationError, match="missing detached event attestation"):
        control_plane_main(
            [
                "reconcile-evaluator-bundle",
                "--bundle",
                str(bundle_dir),
                "--spec",
                str(spec_path),
                "--ledger",
                str(data_root / "ledger" / "research_events.v1.jsonl"),
                "--repo-root",
                str(repo_root),
                "--identity-bundle",
                str(identity_bundle),
                "--identity-trust-anchor",
                str(trust_anchor_path),
                "--identity-registry-pin",
                registry.registry_hash,
                "--at",
                "2026-08-22T18:00:00Z",
            ]
        )

    attestation_path = data_root / "identity" / "result-attestation.json"
    recorded_at = datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc)
    _result_attestation_wrapper(
        attestation_path,
        registry=registry,
        signer=signers["author"],
        ledger=ledger,
        bundle_dir=bundle_dir,
        recorded_at=recorded_at,
    )
    assert (
        control_plane_main(
            [
                "reconcile-evaluator-bundle",
                "--bundle",
                str(bundle_dir),
                "--spec",
                str(spec_path),
                "--ledger",
                str(data_root / "ledger" / "research_events.v1.jsonl"),
                "--repo-root",
                str(repo_root),
                "--identity-bundle",
                str(identity_bundle),
                "--identity-trust-anchor",
                str(trust_anchor_path),
                "--identity-registry-pin",
                registry.registry_hash,
                "--event-attestation",
                str(attestation_path),
                "--at",
                "2026-08-22T18:00:00Z",
            ]
        )
        == 0
    )
    output = strict_load_json_object_bytes(
        capsys.readouterr().out.encode("utf-8"), source="cli output"
    )
    assert output["added_trial_ids"] == [TRIAL_ID]
    records = GlobalResearchLedger(
        data_root / "ledger" / "research_events.v1.jsonl",
        research_root=data_root,
        identity_history=IdentityRegistryHistory(
            registries=(registry,),
            active_registry_hash=registry.registry_hash,
            externally_pinned_registry_hash=registry.registry_hash,
        ),
    ).store.read_all()
    assert records[-1].schema_version == "caerus_alpha_lab_event_v2"
    assert records[-1].event_attestation["identity_id"] == "research.author"


def test_identity_bundle_rejects_substituted_external_root(tmp_path):
    repo_root, data_root = _repo(tmp_path)
    registry, anchor, signers = _registry()
    ledger = _seed_legacy_ledger(data_root, _spec())
    signed_plan = _signed_migration_plan(
        ledger=ledger, registry=registry, signer=signers["owner"]
    )
    bundle = data_root / "identity" / "bundle.json"
    _identity_bundle(bundle, registry=registry, anchor=anchor, signed_plan=signed_plan)
    wrong_private = Ed25519PrivateKey.generate()
    wrong_anchor = IdentityTrustAnchor(
        anchor_id=anchor.anchor_id,
        root_key_id=anchor.root_key_id,
        root_public_key_pem=wrong_private.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8"),
        expected_registry_id=anchor.expected_registry_id,
    )
    wrong_path = repo_root / "protected-wrong-anchor.json"
    _write_json(wrong_path, wrong_anchor.to_dict())
    with pytest.raises(ContractValidationError, match="trust anchor rejects"):
        load_identity_bundle(
            bundle_path=bundle,
            external_registry_pin=registry.registry_hash,
            external_trust_anchor_path=wrong_path,
        )


def test_identity_bundle_rejects_unsigned_schema_extensions(tmp_path):
    repo_root, data_root = _repo(tmp_path)
    registry, anchor, signers = _registry()
    ledger = _seed_legacy_ledger(data_root, _spec())
    signed_plan = _signed_migration_plan(
        ledger=ledger, registry=registry, signer=signers["owner"]
    )
    bundle = data_root / "identity" / "bundle.json"
    _identity_bundle(bundle, registry=registry, anchor=anchor, signed_plan=signed_plan)
    anchor_path = repo_root / "protected-root-anchor.json"
    _write_json(anchor_path, anchor.to_dict())
    raw = strict_load_json_object(bundle)

    changed_registry = copy.deepcopy(raw)
    changed_registry["identity_registries"][0]["unsigned_extension"] = True
    changed_wrapper = copy.deepcopy(raw)
    changed_wrapper["signed_migration_plan"]["unsigned_extension"] = True
    changed_plan = copy.deepcopy(raw)
    changed_plan["signed_migration_plan"]["plan"]["unsigned_extension"] = True
    for index, changed in enumerate(
        (changed_registry, changed_wrapper, changed_plan), start=1
    ):
        changed_path = data_root / "identity" / "changed-{}.json".format(index)
        _write_json(changed_path, changed)
        with pytest.raises(ContractValidationError):
            load_identity_bundle(
                bundle_path=changed_path,
                external_registry_pin=registry.registry_hash,
                external_trust_anchor_path=anchor_path,
            )

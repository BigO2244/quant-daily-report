"""End-to-end public-only ceremony tests with ephemeral test keys."""

from __future__ import annotations

import base64
import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from projects.alpha_lab.control_plane.authenticated_ledger import load_identity_bundle
from projects.alpha_lab.factory import ContractValidationError, IdentityKey, IdentityRole
from projects.alpha_lab.factory import import_research_ledger as importer
from projects.alpha_lab.factory.canonical import canonical_hash, canonical_json
from projects.alpha_lab.factory.ceremony import (
    finalize_attestation_request,
    finalize_migration,
    finalize_projection,
    finalize_publication,
    finalize_registry_release,
    main as ceremony_main,
    migration_definition_from_owner_packet,
    prepare_attestation_request,
    prepare_migration,
    prepare_projection,
    prepare_publication,
    prepare_registry_release,
    verify_attestation_result,
    verify_migration,
    verify_projection,
)
from projects.alpha_lab.factory.import_research_ledger import (
    audit_existing,
    publish_signed_migration_plan,
    verify_signed_publication_authorization,
)
from projects.alpha_lab.factory.research_identity import IdentityRegistry, IdentityTrustAnchor
from projects.alpha_lab.tests.test_import_research_ledger import _fixture


RELEASED_AT = datetime(2026, 8, 22, 15, 59, tzinfo=timezone.utc)
CEREMONY_AT = datetime(2026, 8, 22, 16, 0, tzinfo=timezone.utc)


def _public_pem(private: Ed25519PrivateKey) -> str:
    return private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")


def _identity(identity_id: str, key_id: str, role: IdentityRole):
    private = Ed25519PrivateKey.generate()
    key = IdentityKey(
        identity_id=identity_id,
        subject_id=identity_id,
        key_id=key_id,
        public_key_pem=_public_pem(private),
        allowed_roles=(role,),
        issued_at=RELEASED_AT - timedelta(days=1),
    )
    return private, key


def _history():
    identities = {
        role: _identity(
            role.value.lower().replace("_", "."),
            "{}.2026".format(role.value.lower().replace("_", ".")),
            role,
        )
        for role in IdentityRole
    }
    unsigned = IdentityRegistry(
        registry_id="caerus.research.identity",
        keys=tuple(item[1] for item in identities.values()),
        issued_at=RELEASED_AT - timedelta(minutes=1),
    )
    root = Ed25519PrivateKey.generate()
    anchor = IdentityTrustAnchor(
        anchor_id="caerus.identity.root",
        root_key_id="root.2026",
        root_public_key_pem=_public_pem(root),
        expected_registry_id=unsigned.registry_id,
    )
    request = prepare_registry_release(
        directory=unsigned.directory_dict(),
        trust_anchor=anchor.to_dict(),
        released_at=RELEASED_AT,
    )
    signature = base64.b64encode(
        root.sign(canonical_json(request["signed_payload"]).encode("utf-8"))
    ).decode("ascii")
    finalized = finalize_registry_release(
        request,
        signature_b64=signature,
        external_pin=unsigned.registry_hash,
        trusted_anchor=anchor.to_dict(),
    )
    from projects.alpha_lab.factory.research_identity import IdentityRegistryHistory

    history = IdentityRegistryHistory.from_dict(
        finalized["registry_history"], externally_supplied_pin=unsigned.registry_hash
    )
    return history, identities, root, request, finalized


def _sign(private: Ed25519PrivateKey, request):
    return base64.b64encode(
        private.sign(canonical_json(request["signed_payload"]).encode("utf-8"))
    ).decode("ascii")


def _patch_synthetic_census(monkeypatch):
    monkeypatch.setattr(importer, "EXPECTED_CANONICAL_GATE_COUNT", 1)
    monkeypatch.setattr(
        importer,
        "EXPECTED_CANONICAL_GATE_STATUSES",
        {"READY_FOR_FROZEN_EVALUATOR": 1},
    )
    monkeypatch.setattr(
        importer,
        "EXPECTED_CANONICAL_VARIANTS_BY_HYPOTHESIS",
        {"HYP-2026-006": 2},
    )
    monkeypatch.setattr(importer, "EXPECTED_CANONICAL_ROBUSTNESS_COUNT", 2)
    monkeypatch.setattr(
        importer,
        "EXPECTED_CANONICAL_EXPERIMENT_HYPOTHESES",
        {"HYP-2026-006"},
    )


def _owner_packet(repo_root: Path, inventory):
    packet = json.loads(
        (repo_root / "projects/alpha_lab/templates/MIGRATION_OWNER_SIGNING_PACKET.json")
        .read_text(encoding="utf-8")
    ) if (repo_root / "projects/alpha_lab/templates/MIGRATION_OWNER_SIGNING_PACKET.json").exists() else json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "templates/MIGRATION_OWNER_SIGNING_PACKET.json"
        ).read_text(encoding="utf-8")
    )
    packet["source_receipt_census"].update(
        {
            "data_gate_attempt_count": inventory["data_gate_attempt_count"],
            "data_gate_status_counts": inventory["data_gate_status_counts"],
            "model_trial_count": inventory["model_trial_count"],
            "robustness_record_count": inventory["robustness_record_count"],
            "challenge_read_count": inventory["challenge_read_count"],
            "statistical_trial_count": inventory["statistical_trial_count"],
        }
    )
    return packet


def _migration_fixture(tmp_path, monkeypatch):
    _patch_synthetic_census(monkeypatch)
    repo_root, data_root = _fixture(tmp_path)
    history, identities, _, _, _ = _history()
    inventory = audit_existing(repo_root=repo_root, data_root=data_root)
    owner_private, owner_key = identities[IdentityRole.OWNER_RATIFIER]
    preparation = prepare_migration(
        repo_root=repo_root,
        data_root=data_root,
        owner_packet=_owner_packet(repo_root, inventory),
        recorded_at=CEREMONY_AT,
        identity_history=history,
        owner_identity_id=owner_key.identity_id,
        owner_key_id=owner_key.key_id,
    )
    signed_plan = finalize_migration(
        preparation,
        signature_b64=_sign(owner_private, preparation["attestation_request"]),
        identity_history=history,
    )
    return repo_root, data_root, history, identities, inventory, preparation, signed_plan


def _write_json(path: Path, value) -> None:
    path.write_text(canonical_json(value), encoding="utf-8")


def test_registry_prepare_finalize_history_and_failures():
    history, _, root, request, finalized = _history()
    assert finalized["signature_verified"] is True
    assert finalized["registry_history"] == history.to_dict()
    assert set(history.to_dict()) == {
        "schema_version",
        "active_registry_hash",
        "externally_pinned_registry_hash",
        "trust_anchor",
        "registries",
    }

    prior_registry = history.resolve(history.active_registry_hash)
    next_unsigned = IdentityRegistry(
        registry_id=prior_registry.registry_id,
        keys=prior_registry.keys,
        issued_at=RELEASED_AT + timedelta(minutes=1),
    )
    rotation_request = prepare_registry_release(
        directory=next_unsigned.directory_dict(),
        trust_anchor=request["trust_anchor"],
        released_at=RELEASED_AT + timedelta(minutes=2),
        previous_history=history,
    )
    rotated = finalize_registry_release(
        rotation_request,
        signature_b64=base64.b64encode(
            root.sign(
                canonical_json(rotation_request["signed_payload"]).encode("utf-8")
            )
        ).decode("ascii"),
        external_pin=rotation_request["prospective_registry_hash"],
        trusted_anchor=request["trust_anchor"],
        previous_history=history,
    )
    assert [
        item["release"]["version"]
        for item in rotated["registry_history"]["registries"]
    ] == [1, 2]

    wrong = Ed25519PrivateKey.generate().sign(
        canonical_json(request["signed_payload"]).encode("utf-8")
    )
    with pytest.raises(ContractValidationError, match="signature verification"):
        finalize_registry_release(
            request,
            signature_b64=base64.b64encode(wrong).decode("ascii"),
            external_pin=request["prospective_registry_hash"],
            trusted_anchor=request["trust_anchor"],
        )
    good = base64.b64encode(
        root.sign(canonical_json(request["signed_payload"]).encode("utf-8"))
    ).decode("ascii")
    with pytest.raises(ContractValidationError, match="external pin"):
        finalize_registry_release(
            request,
            signature_b64=good,
            external_pin="0" * 64,
            trusted_anchor=request["trust_anchor"],
        )
    substituted_anchor = dict(request["trust_anchor"])
    substituted_anchor["root_public_key_pem"] = _public_pem(
        Ed25519PrivateKey.generate()
    )
    with pytest.raises(ContractValidationError, match="trust anchor"):
        finalize_registry_release(
            request,
            signature_b64=good,
            external_pin=request["prospective_registry_hash"],
            trusted_anchor=substituted_anchor,
        )

    changed_directory = copy.deepcopy(request["directory"])
    changed_directory["keys"][0]["schema_version"] = "identity_key_extension_v1"
    with pytest.raises(ContractValidationError, match="identity key schema_version"):
        prepare_registry_release(
            directory=changed_directory,
            trust_anchor=request["trust_anchor"],
            released_at=RELEASED_AT,
        )
    changed_directory = copy.deepcopy(request["directory"])
    changed_directory["keys"][0]["unsigned_extension"] = True
    with pytest.raises(ContractValidationError, match="canonical complete form"):
        prepare_registry_release(
            directory=changed_directory,
            trust_anchor=request["trust_anchor"],
            released_at=RELEASED_AT,
        )

    changed_release = copy.deepcopy(request)
    changed_release["signed_payload"]["schema_version"] = "registry_release_extension_v1"
    changed_release["signing_bytes_sha256"] = canonical_hash(
        changed_release["signed_payload"]
    )
    changed_signature = base64.b64encode(
        root.sign(
            canonical_json(changed_release["signed_payload"]).encode("utf-8")
        )
    ).decode("ascii")
    with pytest.raises(ContractValidationError, match="release schema_version"):
        finalize_registry_release(
            changed_release,
            signature_b64=changed_signature,
            external_pin=request["prospective_registry_hash"],
            trusted_anchor=request["trust_anchor"],
        )


def test_event_and_generic_attestation_round_trips_and_reject_context():
    history, identities, _, _, _ = _history()
    private, key = identities[IdentityRole.PREREGISTRATION_AUTHOR]
    draft = {
        "schema_version": "caerus_alpha_lab_event_attestation_draft_v1",
        "event_id": "experiment:EXP-TEST-001",
        "event_type": "research_experiment_registered",
        "occurred_at": "2026-08-22T16:00:00Z",
        "recorded_at": "2026-08-22T16:00:00Z",
        "payload": {"experiment_id": "EXP-TEST-001"},
        "previous_event_hash": "GENESIS",
    }
    request = prepare_attestation_request(
        identity_history=history,
        identity_id=key.identity_id,
        key_id=key.key_id,
        role=IdentityRole.PREREGISTRATION_AUTHOR,
        attested_at=CEREMONY_AT,
        context_kind="event",
        context_artifact=draft,
    )
    result = finalize_attestation_request(
        request,
        signature_b64=_sign(private, request),
        identity_history=history,
    )
    assert result["event_id"] == draft["event_id"]
    assert verify_attestation_result(request, result, identity_history=history) == result
    extra_field = copy.deepcopy(result)
    extra_field["event_attestation"]["unsigned_extension"] = "not signed"
    with pytest.raises(ContractValidationError, match="fields are incomplete or mutable"):
        verify_attestation_result(
            request,
            extra_field,
            identity_history=history,
        )
    wrong_schema = copy.deepcopy(result)
    wrong_schema["event_attestation"]["schema_version"] = "unsigned_extension_v1"
    with pytest.raises(ContractValidationError, match="schema_version"):
        verify_attestation_result(
            request,
            wrong_schema,
            identity_history=history,
        )

    changed = json.loads(json.dumps(request))
    changed["context_artifact"]["payload"]["experiment_id"] = "EXP-TAMPERED"
    with pytest.raises(ContractValidationError, match="reviewed contract"):
        finalize_attestation_request(
            changed,
            signature_b64=_sign(private, request),
            identity_history=history,
        )

    owner_private, owner_key = identities[IdentityRole.OWNER_RATIFIER]
    generic = prepare_attestation_request(
        identity_history=history,
        identity_id=owner_key.identity_id,
        key_id=owner_key.key_id,
        role=IdentityRole.OWNER_RATIFIER,
        attested_at=CEREMONY_AT,
        context_kind="generic",
        artifact_sha256="1" * 64,
        ledger_head_hash="GENESIS",
        context_sha256="2" * 64,
        recorded_at=CEREMONY_AT,
    )
    finalized = finalize_attestation_request(
        generic,
        signature_b64=_sign(owner_private, generic),
        identity_history=history,
    )
    assert finalized["role"] == "OWNER_RATIFIER"


def test_owner_packet_nested_controls_are_exact(tmp_path, monkeypatch):
    _patch_synthetic_census(monkeypatch)
    repo_root, data_root = _fixture(tmp_path)
    inventory = audit_existing(repo_root=repo_root, data_root=data_root)
    packet = _owner_packet(repo_root, inventory)
    mutations = (
        ("registry release placeholder", ("registry_control", "active_registry_release_hash"), "0" * 64),
        ("external pin placeholder", ("registry_control", "externally_pinned_registry_hash"), "0" * 64),
        ("premature pin verification", ("registry_control", "pin_match_verified"), True),
        ("plan hash placeholder", ("deterministic_plan_control", "migration_event_plan_sha256"), "0" * 64),
        ("plan identity placeholder", ("deterministic_plan_control", "plan_identity_sha256"), "0" * 64),
        ("premature event count", ("deterministic_plan_control", "ordered_event_count"), 1),
        ("terminal head placeholder", ("deterministic_plan_control", "expected_terminal_head"), "0" * 64),
        ("activation head placeholder", ("deterministic_plan_control", "identity_activation_head_hash"), "0" * 64),
        ("ledger hash placeholder", ("deterministic_plan_control", "expected_ledger_sha256"), "0" * 64),
        ("premature scratch verification", ("deterministic_plan_control", "scratch_bytes_and_hash_verified"), True),
        ("receipt placeholder", ("source_receipt_census", "source_receipts"), {"forged": "0" * 64}),
        ("canonical path", ("gcp_publication_authorization", "canonical_path"), "/tmp/research_events.v1.jsonl"),
        ("publication mode", ("gcp_publication_authorization", "mode"), "OVERWRITE"),
        ("overwrite permission", ("gcp_publication_authorization", "overwrite_or_repair_allowed"), True),
        ("premature publication authority", ("gcp_publication_authorization", "authorized"), True),
        ("publication signature placeholder", ("gcp_publication_authorization", "signed_publication_authorization_sha256"), "0" * 64),
        ("publication rule", ("gcp_publication_authorization", "authorization_rule"), "migration signature is sufficient"),
    )
    for label, path, replacement in mutations:
        changed = copy.deepcopy(packet)
        changed[path[0]][path[1]] = replacement
        with pytest.raises(ContractValidationError) as context:
            migration_definition_from_owner_packet(
                changed,
                recorded_at=CEREMONY_AT,
                inventory=inventory,
            )
        assert context.value is not None, label

    changed = copy.deepcopy(packet)
    changed["registry_control"]["unexpected"] = False
    with pytest.raises(ContractValidationError, match="registry control"):
        migration_definition_from_owner_packet(
            changed,
            recorded_at=CEREMONY_AT,
            inventory=inventory,
        )


def test_migration_publication_and_projection_full_happy_path(tmp_path, monkeypatch):
    (
        repo_root,
        data_root,
        history,
        identities,
        inventory,
        migration_preparation,
        signed_plan,
    ) = _migration_fixture(tmp_path, monkeypatch)
    verified = verify_migration(
        signed_plan,
        identity_history=history,
        repo_root=repo_root,
        data_root=data_root,
    )
    assert verified["fresh_receipts_verified"] is True
    assert migration_preparation["migration_event_plan"]["publication_contract"][
        "separate_publication_authorization_required"
    ] is True
    assert migration_preparation["migration_event_plan"]["publication_contract"][
        "owner_signature_authorizes_one_publication"
    ] is False

    owner_private, owner_key = identities[IdentityRole.OWNER_RATIFIER]
    publication_preparation = prepare_publication(
        repo_root=repo_root,
        data_root=data_root,
        signed_plan=signed_plan,
        authorized_at=CEREMONY_AT + timedelta(minutes=1),
        identity_history=history,
        owner_identity_id=owner_key.identity_id,
        owner_key_id=owner_key.key_id,
    )
    signed_authorization = finalize_publication(
        publication_preparation,
        signature_b64=_sign(
            owner_private, publication_preparation["attestation_request"]
        ),
        identity_history=history,
        repo_root=repo_root,
        data_root=data_root,
        signed_plan=signed_plan,
    )
    assert verify_signed_publication_authorization(
        signed_authorization,
        repo_root=repo_root,
        inventory=inventory,
        signed_plan=signed_plan,
        identity_history=history,
    )["prior_ledger_head"] == "GENESIS"

    monkeypatch.setattr(importer, "AUTHORITATIVE_REPO_ROOT", repo_root.resolve())
    monkeypatch.setattr(importer, "AUTHORITATIVE_DATA_ROOT", data_root.resolve())
    report = publish_signed_migration_plan(
        repo_root=repo_root,
        data_root=data_root,
        inventory=inventory,
        signed_plan=signed_plan,
        publication_authorization=signed_authorization,
        identity_history=history,
    )
    ledger_path = repo_root / importer.LEDGER_RELATIVE_PATH
    assert report["publication_mode"] == "CREATE_ONLY_ATOMIC_HARD_LINK"
    assert ledger_path.exists()
    with pytest.raises(ContractValidationError, match="already exists"):
        publish_signed_migration_plan(
            repo_root=repo_root,
            data_root=data_root,
            inventory=inventory,
            signed_plan=signed_plan,
            publication_authorization=signed_authorization,
            identity_history=history,
        )

    exporter_private, exporter_key = identities[IdentityRole.LEDGER_EXPORTER]
    projection_preparation = prepare_projection(
        ledger_path=ledger_path,
        research_root=data_root,
        repo_root=repo_root,
        signed_plan=signed_plan,
        identity_history=history,
        exported_at=CEREMONY_AT + timedelta(minutes=2),
        exporter_identity_id=exporter_key.identity_id,
        exporter_key_id=exporter_key.key_id,
    )
    signed_export = finalize_projection(
        projection_preparation,
        signature_b64=_sign(
            exporter_private, projection_preparation["attestation_request"]
        ),
        ledger_path=ledger_path,
        research_root=data_root,
        repo_root=repo_root,
        signed_plan=signed_plan,
        identity_history=history,
    )
    projection_report = verify_projection(
        signed_export,
        ledger_path=ledger_path,
        research_root=data_root,
        repo_root=repo_root,
        signed_plan=signed_plan,
        identity_history=history,
    )
    assert projection_report["authenticated_ledger_replayed"] is True


def test_wrong_receipt_path_plan_signature_and_time_fail_closed(tmp_path, monkeypatch):
    repo_root, data_root, history, identities, inventory, _, signed_plan = _migration_fixture(
        tmp_path, monkeypatch
    )
    owner_private, owner_key = identities[IdentityRole.OWNER_RATIFIER]
    publication_preparation = prepare_publication(
        repo_root=repo_root,
        data_root=data_root,
        signed_plan=signed_plan,
        authorized_at=CEREMONY_AT + timedelta(minutes=1),
        identity_history=history,
        owner_identity_id=owner_key.identity_id,
        owner_key_id=owner_key.key_id,
    )
    signed_authorization = finalize_publication(
        publication_preparation,
        signature_b64=_sign(owner_private, publication_preparation["attestation_request"]),
        identity_history=history,
        repo_root=repo_root,
        data_root=data_root,
        signed_plan=signed_plan,
    )

    bad_path = json.loads(json.dumps(signed_authorization))
    bad_path["authorization"]["canonical_ledger_path"] += ".other"
    with pytest.raises(ContractValidationError, match="fresh plan, receipts, path"):
        verify_signed_publication_authorization(
            bad_path,
            repo_root=repo_root,
            inventory=inventory,
            signed_plan=signed_plan,
            identity_history=history,
        )

    changed_inventory = json.loads(json.dumps(inventory))
    changed_inventory["source_receipts"]["run_manifests"] = "0" * 64
    with pytest.raises(ContractValidationError):
        verify_signed_publication_authorization(
            signed_authorization,
            repo_root=repo_root,
            inventory=changed_inventory,
            signed_plan=signed_plan,
            identity_history=history,
        )

    bad_plan = json.loads(json.dumps(signed_plan))
    bad_plan["owner_attestation"]["signature_b64"] = base64.b64encode(b"x" * 64).decode("ascii")
    with pytest.raises(ContractValidationError, match="owner signature"):
        prepare_publication(
            repo_root=repo_root,
            data_root=data_root,
            signed_plan=bad_plan,
            authorized_at=CEREMONY_AT + timedelta(minutes=1),
            identity_history=history,
            owner_identity_id=owner_key.identity_id,
            owner_key_id=owner_key.key_id,
        )

    with pytest.raises(ContractValidationError, match="predates"):
        prepare_publication(
            repo_root=repo_root,
            data_root=data_root,
            signed_plan=signed_plan,
            authorized_at=CEREMONY_AT - timedelta(seconds=1),
            identity_history=history,
            owner_identity_id=owner_key.identity_id,
            owner_key_id=owner_key.key_id,
        )


def test_registry_and_event_cli_round_trips_create_only(tmp_path):
    history, identities, root, request, _ = _history()
    directory_path = tmp_path / "directory.json"
    anchor_path = tmp_path / "anchor.json"
    _write_json(directory_path, request["directory"])
    _write_json(anchor_path, request["trust_anchor"])
    prepared_dir = tmp_path / "registry-prepare"
    assert ceremony_main(
        [
            "registry",
            "prepare",
            "--directory",
            str(directory_path),
            "--trust-anchor",
            str(anchor_path),
            "--released-at",
            "2026-08-22T15:59:00Z",
            "--output-dir",
            str(prepared_dir),
        ]
    ) == 0
    with pytest.raises(FileExistsError):
        ceremony_main(
            [
                "registry",
                "prepare",
                "--directory",
                str(directory_path),
                "--trust-anchor",
                str(anchor_path),
                "--released-at",
                "2026-08-22T15:59:00Z",
                "--output-dir",
                str(prepared_dir),
            ]
        )
    cli_request = json.loads((prepared_dir / "review_manifest.json").read_text())
    signature_path = tmp_path / "root.sig"
    signature_path.write_bytes(
        root.sign(canonical_json(cli_request["signed_payload"]).encode("utf-8"))
    )
    finalized_dir = tmp_path / "registry-final"
    assert ceremony_main(
        [
            "registry",
            "finalize",
            "--request",
            str(prepared_dir / "review_manifest.json"),
            "--signature",
            str(signature_path),
            "--trust-anchor",
            str(anchor_path),
            "--external-pin",
            request["prospective_registry_hash"],
            "--output-dir",
            str(finalized_dir),
        ]
    ) == 0
    cli_history = json.loads(
        (finalized_dir / "identity_registry_history.json").read_text()
    )
    assert cli_history == history.to_dict()

    history_path = finalized_dir / "identity_registry_history.json"
    _, author_key = identities[IdentityRole.PREREGISTRATION_AUTHOR]
    author_private = identities[IdentityRole.PREREGISTRATION_AUTHOR][0]
    event_path = tmp_path / "event.json"
    _write_json(
        event_path,
        {
            "schema_version": "caerus_alpha_lab_event_attestation_draft_v1",
            "event_id": "event:cli",
            "event_type": "test_event",
            "occurred_at": "2026-08-22T16:00:00Z",
            "recorded_at": "2026-08-22T16:00:00Z",
            "payload": {"ok": True},
            "previous_event_hash": "GENESIS",
        },
    )
    event_prepare = tmp_path / "event-prepare"
    assert ceremony_main(
        [
            "attestation",
            "prepare",
            "--identity-history",
            str(history_path),
            "--identity-trust-anchor",
            str(anchor_path),
            "--external-pin",
            history.active_registry_hash,
            "--identity-id",
            author_key.identity_id,
            "--key-id",
            author_key.key_id,
            "--role",
            "PREREGISTRATION_AUTHOR",
            "--attested-at",
            "2026-08-22T16:00:00Z",
            "--event-draft",
            str(event_path),
            "--output-dir",
            str(event_prepare),
        ]
    ) == 0
    event_request = json.loads((event_prepare / "review_manifest.json").read_text())
    event_signature = tmp_path / "event.sig.b64"
    event_signature.write_text(
        base64.b64encode(
            author_private.sign(
                canonical_json(event_request["signed_payload"]).encode("utf-8")
            )
        ).decode("ascii"),
        encoding="ascii",
    )
    event_output = tmp_path / "event-attestation.json"
    assert ceremony_main(
        [
            "attestation",
            "finalize",
            "--identity-history",
            str(history_path),
            "--identity-trust-anchor",
            str(anchor_path),
            "--external-pin",
            history.active_registry_hash,
            "--request",
            str(event_prepare / "review_manifest.json"),
            "--signature",
            str(event_signature),
            "--output",
            str(event_output),
        ]
    ) == 0
    assert json.loads(event_output.read_text())["schema_version"].endswith(
        "event_attestation_v1"
    )


def test_migration_publication_projection_cli_round_trip(tmp_path, monkeypatch):
    _patch_synthetic_census(monkeypatch)
    repo_root, data_root = _fixture(tmp_path)
    inventory = audit_existing(repo_root=repo_root, data_root=data_root)
    history, identities, _, _, _ = _history()
    history_path = tmp_path / "history.json"
    anchor_path = tmp_path / "anchor.json"
    packet_path = tmp_path / "owner-packet.json"
    _write_json(history_path, history.to_dict())
    _write_json(anchor_path, history.to_dict()["trust_anchor"])
    _write_json(packet_path, _owner_packet(repo_root, inventory))
    owner_private, owner_key = identities[IdentityRole.OWNER_RATIFIER]

    definition_path = tmp_path / "migration-definition.json"
    assert ceremony_main(
        [
            "migration", "definition",
            "--repo-root", str(repo_root),
            "--data-root", str(data_root),
            "--owner-packet", str(packet_path),
            "--recorded-at", "2026-08-22T16:00:00Z",
            "--output", str(definition_path),
        ]
    ) == 0
    assert json.loads(definition_path.read_text())["decision"].startswith("RATIFY_")

    migration_dir = tmp_path / "migration-prepare"
    assert ceremony_main(
        [
            "migration", "prepare",
            "--repo-root", str(repo_root),
            "--data-root", str(data_root),
            "--owner-packet", str(packet_path),
            "--recorded-at", "2026-08-22T16:00:00Z",
            "--identity-history", str(history_path),
            "--identity-trust-anchor", str(anchor_path),
            "--external-pin", history.active_registry_hash,
            "--owner-identity-id", owner_key.identity_id,
            "--owner-key-id", owner_key.key_id,
            "--output-dir", str(migration_dir),
        ]
    ) == 0
    migration_review = json.loads((migration_dir / "review_manifest.json").read_text())
    migration_signature = tmp_path / "migration.sig"
    migration_signature.write_bytes(
        owner_private.sign(
            canonical_json(migration_review["signed_payload"]).encode("utf-8")
        )
    )
    signed_plan_path = tmp_path / "signed-plan.json"
    assert ceremony_main(
        [
            "migration", "finalize",
            "--identity-history", str(history_path),
            "--identity-trust-anchor", str(anchor_path),
            "--external-pin", history.active_registry_hash,
            "--preparation", str(migration_dir / "review_manifest.json"),
            "--signature", str(migration_signature),
            "--output", str(signed_plan_path),
        ]
    ) == 0

    identity_bundle_path = tmp_path / "identity-bundle.json"
    assert ceremony_main(
        [
            "migration", "identity-bundle",
            "--identity-history", str(history_path),
            "--identity-trust-anchor", str(anchor_path),
            "--external-pin", history.active_registry_hash,
            "--signed-plan", str(signed_plan_path),
            "--output", str(identity_bundle_path),
        ]
    ) == 0
    loaded_history, activation = load_identity_bundle(
        bundle_path=identity_bundle_path,
        external_registry_pin=history.active_registry_hash,
        external_trust_anchor_path=anchor_path,
    )
    assert loaded_history.active_registry_hash == history.active_registry_hash
    assert activation.plan_sha256 == canonical_hash(
        json.loads(signed_plan_path.read_text())["plan"]
    )

    publication_dir = tmp_path / "publication-prepare"
    assert ceremony_main(
        [
            "publication", "prepare",
            "--repo-root", str(repo_root),
            "--data-root", str(data_root),
            "--signed-plan", str(signed_plan_path),
            "--authorized-at", "2026-08-22T16:01:00Z",
            "--identity-history", str(history_path),
            "--identity-trust-anchor", str(anchor_path),
            "--external-pin", history.active_registry_hash,
            "--owner-identity-id", owner_key.identity_id,
            "--owner-key-id", owner_key.key_id,
            "--output-dir", str(publication_dir),
        ]
    ) == 0
    publication_review = json.loads(
        (publication_dir / "review_manifest.json").read_text()
    )
    publication_signature = tmp_path / "publication.sig"
    publication_signature.write_bytes(
        owner_private.sign(
            canonical_json(publication_review["signed_payload"]).encode("utf-8")
        )
    )
    authorization_path = tmp_path / "signed-authorization.json"
    assert ceremony_main(
        [
            "publication", "finalize",
            "--repo-root", str(repo_root),
            "--data-root", str(data_root),
            "--signed-plan", str(signed_plan_path),
            "--identity-history", str(history_path),
            "--identity-trust-anchor", str(anchor_path),
            "--external-pin", history.active_registry_hash,
            "--preparation", str(publication_dir / "review_manifest.json"),
            "--signature", str(publication_signature),
            "--output", str(authorization_path),
        ]
    ) == 0

    monkeypatch.setattr(importer, "AUTHORITATIVE_REPO_ROOT", repo_root.resolve())
    monkeypatch.setattr(importer, "AUTHORITATIVE_DATA_ROOT", data_root.resolve())
    assert ceremony_main(
        [
            "publication", "publish",
            "--repo-root", str(repo_root),
            "--data-root", str(data_root),
            "--signed-plan", str(signed_plan_path),
            "--authorization", str(authorization_path),
            "--identity-history", str(history_path),
            "--identity-trust-anchor", str(anchor_path),
            "--external-pin", history.active_registry_hash,
            "--write",
        ]
    ) == 0
    ledger_path = repo_root / importer.LEDGER_RELATIVE_PATH
    assert ledger_path.exists()

    exporter_private, exporter_key = identities[IdentityRole.LEDGER_EXPORTER]
    projection_dir = tmp_path / "projection-prepare"
    ledger_args = [
        "--ledger", str(ledger_path),
        "--research-root", str(data_root),
        "--repo-root", str(repo_root),
        "--signed-migration-plan", str(signed_plan_path),
        "--identity-history", str(history_path),
        "--identity-trust-anchor", str(anchor_path),
        "--external-pin", history.active_registry_hash,
    ]
    assert ceremony_main(
        [
            "projection", "prepare", *ledger_args,
            "--exported-at", "2026-08-22T16:02:00Z",
            "--exporter-identity-id", exporter_key.identity_id,
            "--exporter-key-id", exporter_key.key_id,
            "--output-dir", str(projection_dir),
        ]
    ) == 0
    projection_review = json.loads(
        (projection_dir / "review_manifest.json").read_text()
    )
    projection_signature = tmp_path / "projection.sig"
    projection_signature.write_bytes(
        exporter_private.sign(
            canonical_json(projection_review["signed_payload"]).encode("utf-8")
        )
    )
    signed_export_path = tmp_path / "signed-export.json"
    assert ceremony_main(
        [
            "projection", "finalize", *ledger_args,
            "--preparation", str(projection_dir / "review_manifest.json"),
            "--signature", str(projection_signature),
            "--output", str(signed_export_path),
        ]
    ) == 0
    assert ceremony_main(
        [
            "projection", "verify", *ledger_args,
            "--signed-export", str(signed_export_path),
        ]
    ) == 0

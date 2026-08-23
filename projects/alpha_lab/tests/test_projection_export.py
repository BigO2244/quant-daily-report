"""Focused adversarial tests for the Alpha signed projection boundary."""

from __future__ import annotations

import base64
import hashlib
import multiprocessing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from projects.alpha_lab.factory import (
    ContractValidationError,
    GENESIS_LEDGER_HEAD,
    GlobalResearchLedger,
    IdentityKey,
    IdentityRegistry,
    IdentityRegistryHistory,
    IdentityRole,
    IdentityTrustAnchor,
    RegistryRelease,
    ResearchAttestation,
    build_signed_projection_export,
    build_unsigned_projection_export,
    canonical_json,
    projection_export_attestation_context_hash,
)
from projects.alpha_lab.factory.errors import EventStoreIntegrityError
from projects.alpha_lab.factory.store import AppendOnlyJSONLEventStore


NOW = datetime(2026, 8, 22, 16, 0, tzinfo=timezone.utc)


def _blocked_append(path: str, root: str, started, finished) -> None:
    store = AppendOnlyJSONLEventStore(Path(path), research_root=Path(root))
    started.set()
    store.append(
        event_id="snapshot-append",
        event_type="snapshot-test",
        occurred_at=NOW,
        recorded_at=NOW,
        payload={"sequence": 2},
    )
    finished.set()


def _key(identity_id: str, key_id: str, role: IdentityRole):
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode("utf-8")
    return private, IdentityKey(
        identity_id=identity_id,
        subject_id=identity_id,
        key_id=key_id,
        public_key_pem=public,
        allowed_roles=(role,),
        issued_at=NOW - timedelta(days=1),
    )


def _ledger(tmp_path: Path):
    identities = [
        _key("owner.signer", "owner.2026", IdentityRole.OWNER_RATIFIER),
        _key("author.signer", "author.2026", IdentityRole.PREREGISTRATION_AUTHOR),
        _key("data.signer", "data.2026", IdentityRole.DATA_CERTIFIER),
        _key("review.signer", "review.2026", IdentityRole.INDEPENDENT_REVIEWER),
        _key("export.signer", "export.2026", IdentityRole.LEDGER_EXPORTER),
    ]
    keys = tuple(item[1] for item in identities)
    unsigned = IdentityRegistry(
        registry_id="caerus.research.identity", keys=keys, issued_at=NOW - timedelta(hours=2)
    )
    root = Ed25519PrivateKey.generate()
    anchor = IdentityTrustAnchor(
        anchor_id="caerus.identity.root",
        root_key_id="root.2026",
        root_public_key_pem=root.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode("utf-8"),
        expected_registry_id=unsigned.registry_id,
    )
    draft = RegistryRelease(
        registry_id=unsigned.registry_id,
        registry_hash=unsigned.registry_hash,
        version=1,
        released_at=NOW - timedelta(minutes=1),
        root_key_id=anchor.root_key_id,
        signature_b64=base64.b64encode(b"\x00" * 64).decode("ascii"),
    )
    release = RegistryRelease(
        **{
            **draft.__dict__,
            "signature_b64": base64.b64encode(
                root.sign(canonical_json(draft.signed_payload()).encode("utf-8"))
            ).decode("ascii"),
        }
    )
    registry = IdentityRegistry(
        registry_id=unsigned.registry_id,
        keys=keys,
        issued_at=unsigned.issued_at,
        release=release,
        trust_anchor=anchor,
    )
    history = IdentityRegistryHistory(
        registries=(registry,),
        active_registry_hash=registry.registry_hash,
        externally_pinned_registry_hash=registry.registry_hash,
    )
    root_path = tmp_path / "repo"
    root_path.mkdir()
    ledger = GlobalResearchLedger(
        root_path / "ledger.jsonl", research_root=root_path, identity_history=history
    )
    ledger.store.path.touch()
    return ledger, root_path, registry, identities[-1]


def _attestation(registry, private, key, unsigned):
    draft = ResearchAttestation(
        identity_id=key.identity_id,
        key_id=key.key_id,
        role=IdentityRole.LEDGER_EXPORTER,
        artifact_sha256=hashlib.sha256(canonical_json(unsigned).encode("utf-8")).hexdigest(),
        ledger_head_hash=GENESIS_LEDGER_HEAD,
        context_sha256=projection_export_attestation_context_hash(unsigned),
        attested_at=NOW,
        signature_b64=base64.b64encode(b"\x00" * 64).decode("ascii"),
        registry_hash=registry.registry_hash,
    )
    return ResearchAttestation(
        **{
            **draft.__dict__,
            "signature_b64": base64.b64encode(
                private.sign(canonical_json(draft.signed_payload()).encode("utf-8"))
            ).decode("ascii"),
        }
    )


def test_signed_projection_export_binds_full_unsigned_context(tmp_path):
    ledger, repo_root, registry, (private, key) = _ledger(tmp_path)
    unsigned = build_unsigned_projection_export(
        ledger, repo_root=repo_root, exported_at=NOW
    )
    assert unsigned["classification"] == "LINEAGE_ONLY_NON_DECISION_GRADE"
    signed = build_signed_projection_export(
        ledger,
        repo_root=repo_root,
        attestation=_attestation(registry, private, key, unsigned),
        recorded_at=NOW,
    )
    assert signed["schema_version"] == "caerus_alpha_lab_signed_projection_export_v1"
    assert signed["canonical_event_store"] == "ledger.jsonl"
    assert signed["exported_at"] == "2026-08-22T16:00:00Z"
    assert signed["exporter_identity"]["role"] == "LEDGER_EXPORTER"
    assert unsigned["active_identity_registry"] == signed["active_identity_registry"]
    assert signed["active_identity_registry"]["externally_pinned_registry_hash"] == registry.registry_hash
    assert signed["source_ledger_receipt"]["head_by_event_count"] == {"0": None}
    assert len(signed["projection_export_hash"]) == 64


def test_unsigned_context_and_source_path_or_head_tampering_fail_closed(tmp_path):
    ledger, repo_root, registry, (private, key) = _ledger(tmp_path)
    unsigned = build_unsigned_projection_export(
        ledger, repo_root=repo_root, exported_at=NOW
    )
    unsigned["classification"] = "DECISION_GRADE"
    with pytest.raises(ContractValidationError, match="lineage-only"):
        projection_export_attestation_context_hash(unsigned)

    unsigned = build_unsigned_projection_export(
        ledger, repo_root=repo_root, exported_at=NOW
    )
    bad = _attestation(registry, private, key, unsigned)
    ledger.store.path.write_bytes(b"{\"tampered\":true}\n")
    with pytest.raises(EventStoreIntegrityError):
        build_signed_projection_export(
            ledger, repo_root=repo_root, attestation=bad, recorded_at=NOW
        )


def test_signed_export_rejects_attestation_time_mismatch(tmp_path):
    ledger, repo_root, registry, (private, key) = _ledger(tmp_path)
    unsigned = build_unsigned_projection_export(
        ledger, repo_root=repo_root, exported_at=NOW
    )
    with pytest.raises(ContractValidationError, match="exactly equal"):
        build_signed_projection_export(
            ledger,
            repo_root=repo_root,
            attestation=_attestation(registry, private, key, unsigned),
            recorded_at=NOW + timedelta(seconds=1),
        )


def test_signed_export_rejects_a_missing_event_store_before_signing(tmp_path):
    ledger, repo_root, registry, (private, key) = _ledger(tmp_path)
    unsigned = build_unsigned_projection_export(
        ledger, repo_root=repo_root, exported_at=NOW
    )
    ledger.store.path.unlink()
    with pytest.raises(EventStoreIntegrityError, match="must exist"):
        build_signed_projection_export(
            ledger,
            repo_root=repo_root,
            attestation=_attestation(registry, private, key, unsigned),
            recorded_at=NOW,
        )


def test_shared_snapshot_lock_blocks_append_until_the_receipt_is_complete(tmp_path):
    ledger, repo_root, _, _ = _ledger(tmp_path)
    ledger.store.append(
        event_id="snapshot-seed",
        event_type="snapshot-test",
        occurred_at=NOW,
        recorded_at=NOW,
        payload={"sequence": 1},
    )
    # Use spawn rather than fork: a fork inherits the held descriptor and
    # cannot prove that a separately launched appender blocks on this lock.
    context = multiprocessing.get_context("spawn")
    started = context.Event()
    finished = context.Event()
    with ledger.store.shared_snapshot_lock(require_existing_file=True) as stream:
        append = context.Process(
            target=_blocked_append,
            args=(str(ledger.store.path), str(repo_root), started, finished),
        )
        append.start()
        assert started.wait(2)
        assert not finished.wait(0.1)
        records = ledger.store.read_all()
        stream.seek(0)
        raw = stream.read()
        assert len(records) == 1
        assert raw.count(b"\n") == 1
    assert finished.wait(2)
    append.join(timeout=2)
    assert append.exitcode == 0
    assert len(ledger.store.read_all()) == 2

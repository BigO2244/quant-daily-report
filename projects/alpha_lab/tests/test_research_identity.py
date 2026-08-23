"""Adversarial tests for public-key research-governance attestations."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from projects.alpha_lab.factory import (
    ExpectedDirection,
    GENESIS_LEDGER_HEAD,
    GlobalResearchLedger,
    HypothesisFamily,
    IdentityKey,
    IdentityRegistry,
    IdentityRegistryHistory,
    IdentityRole,
    IdentityStatus,
    IdentityTrustAnchor,
    IndependentResearchReview,
    InferenceTrack,
    MultipleTestingMethod,
    ResearchAttestation,
    ResearchExperiment,
    ResearchWave,
    RegistryRelease,
    canonical_json,
    event_attestation_context_hash,
    typed_event_payload_hash,
)
from projects.alpha_lab.factory.errors import ContractValidationError, EventStoreIntegrityError


NOW = datetime(2026, 8, 22, 16, 0, tzinfo=timezone.utc)


def _sha(value: str | bytes) -> str:
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode("utf-8")).hexdigest()


def _key(identity_id: str, key_id: str, roles: tuple[IdentityRole, ...], *, private=None, subject_id=None, **kwargs):
    private = private or Ed25519PrivateKey.generate()
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    return private, IdentityKey(
        identity_id=identity_id,
        subject_id=subject_id or identity_id,
        key_id=key_id,
        public_key_pem=public_pem,
        allowed_roles=roles,
        issued_at=NOW - timedelta(days=1),
        **kwargs,
    )


def _anchored_registry(keys):
    unsigned_registry = IdentityRegistry(
        registry_id="caerus.research.identity", keys=keys, issued_at=NOW - timedelta(hours=1)
    )
    root_private = Ed25519PrivateKey.generate()
    anchor = IdentityTrustAnchor(
        anchor_id="caerus.identity.root", root_key_id="root.2026",
        root_public_key_pem=root_private.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode("utf-8"),
        expected_registry_id="caerus.research.identity",
    )
    release_draft = RegistryRelease(
        registry_id=unsigned_registry.registry_id, registry_hash=unsigned_registry.registry_hash,
        version=1, released_at=NOW - timedelta(minutes=1), root_key_id=anchor.root_key_id,
        signature_b64=base64.b64encode(b"\x00" * 64).decode("ascii"),
    )
    release = RegistryRelease(
        **{
            **release_draft.__dict__,
            "signature_b64": base64.b64encode(
                root_private.sign(canonical_json(release_draft.signed_payload()).encode("utf-8"))
            ).decode("ascii"),
        }
    )
    return IdentityRegistry(
        registry_id=unsigned_registry.registry_id, keys=keys,
        issued_at=unsigned_registry.issued_at, release=release, trust_anchor=anchor,
    )


def _released_registry(
    keys,
    *,
    version,
    issued_at,
    released_at,
    root_private,
    anchor,
    previous=None,
):
    unsigned = IdentityRegistry(
        registry_id="caerus.research.identity", keys=keys, issued_at=issued_at
    )
    draft = RegistryRelease(
        registry_id=unsigned.registry_id,
        registry_hash=unsigned.registry_hash,
        version=version,
        released_at=released_at,
        root_key_id=anchor.root_key_id,
        signature_b64=base64.b64encode(b"\x00" * 64).decode("ascii"),
        previous_registry_hash=(previous.registry_hash if previous else None),
        previous_release_hash=(previous.release.release_hash if previous else None),
    )
    release = replace(
        draft,
        signature_b64=base64.b64encode(
            root_private.sign(canonical_json(draft.signed_payload()).encode("utf-8"))
        ).decode("ascii"),
    )
    return IdentityRegistry(
        registry_id=unsigned.registry_id,
        keys=keys,
        issued_at=issued_at,
        release=release,
        trust_anchor=anchor,
    )


def _registry(*, author_roles=(IdentityRole.PREREGISTRATION_AUTHOR,)):
    owner_private, owner = _key("brett.owner", "owner.2026", (IdentityRole.OWNER_RATIFIER,))
    author_private, author = _key("research.author", "author.2026", author_roles)
    data_private, data = _key("data.certifier", "data.2026", (IdentityRole.DATA_CERTIFIER,))
    reviewer_private, reviewer = _key("reviewer.independent", "review.2026", (IdentityRole.INDEPENDENT_REVIEWER,))
    exporter_private, exporter = _key("ledger.exporter", "exporter.2026", (IdentityRole.LEDGER_EXPORTER,))
    registry = _anchored_registry((owner, author, data, reviewer, exporter))
    return registry, {
        owner.identity_id: owner_private,
        author.identity_id: author_private,
        data.identity_id: data_private,
        reviewer.identity_id: reviewer_private,
        exporter.identity_id: exporter_private,
    }, {key.identity_id: key for key in (owner, author, data, reviewer, exporter)}


def _history_release_one():
    root_private = Ed25519PrivateKey.generate()
    anchor = IdentityTrustAnchor(
        anchor_id="caerus.identity.root",
        root_key_id="root.2026",
        root_public_key_pem=root_private.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8"),
        expected_registry_id="caerus.research.identity",
    )
    _, owner = _key("brett.owner", "owner.2026", (IdentityRole.OWNER_RATIFIER,))
    _, author = _key(
        "research.author", "author.2026", (IdentityRole.PREREGISTRATION_AUTHOR,)
    )
    _, data = _key("data.certifier", "data.2026", (IdentityRole.DATA_CERTIFIER,))
    _, reviewer = _key(
        "reviewer.independent",
        "review.2026",
        (IdentityRole.INDEPENDENT_REVIEWER,),
    )
    _, exporter = _key("ledger.exporter", "exporter.2026", (IdentityRole.LEDGER_EXPORTER,))
    keys = (owner, author, data, reviewer, exporter)
    first = _released_registry(
        keys,
        version=1,
        issued_at=NOW - timedelta(hours=2),
        released_at=NOW - timedelta(minutes=90),
        root_private=root_private,
        anchor=anchor,
    )
    return root_private, anchor, first, keys


def _attestation(
    registry,
    private,
    key,
    *,
    role,
    artifact_sha256,
    ledger_head_hash,
    context_sha256=None,
    at=NOW,
):
    context_sha256 = context_sha256 or _sha("test-operation-context")
    draft = ResearchAttestation(
        identity_id=key.identity_id,
        key_id=key.key_id,
        role=role,
        artifact_sha256=artifact_sha256,
        ledger_head_hash=ledger_head_hash,
        context_sha256=context_sha256,
        attested_at=at,
        signature_b64=base64.b64encode(b"\x00" * 64).decode("ascii"),
        registry_hash=registry.registry_hash,
    )
    signature = private.sign(canonical_json(draft.signed_payload()).encode("utf-8"))
    return ResearchAttestation(
        **{**draft.__dict__, "signature_b64": base64.b64encode(signature).decode("ascii")}
    )


def _wave():
    return ResearchWave(
        wave_id="WAVE-2026-001",
        track=InferenceTrack.EXPLORATORY,
        family_ids=("FAM-2026-001",),
        method=MultipleTestingMethod.BENJAMINI_YEKUTIELI,
        alpha_or_q=0.10,
        registered_at=NOW,
        policy_artifact="policy.json",
        policy_sha256=_sha("policy"),
        owner_ratified=True,
    )


def test_signature_tampering_wrong_role_and_stale_head_fail_closed():
    registry, private, keys = _registry()
    attestation = _attestation(
        registry,
        private["brett.owner"],
        keys["brett.owner"],
        role=IdentityRole.OWNER_RATIFIER,
        artifact_sha256=_sha("policy"),
        ledger_head_hash=GENESIS_LEDGER_HEAD,
    )
    registry.verify(
        attestation,
        expected_role=IdentityRole.OWNER_RATIFIER,
        artifact_sha256=_sha("policy"),
        ledger_head_hash=GENESIS_LEDGER_HEAD,
        context_sha256=_sha("test-operation-context"),
        recorded_at=NOW,
    )
    with pytest.raises(ContractValidationError, match="artifact hash"):
        registry.verify(
            attestation,
            expected_role=IdentityRole.OWNER_RATIFIER,
            artifact_sha256=_sha("other-policy"),
            ledger_head_hash=GENESIS_LEDGER_HEAD,
            context_sha256=_sha("test-operation-context"),
            recorded_at=NOW,
        )
    with pytest.raises(ContractValidationError, match="role"):
        registry.verify(
            attestation,
            expected_role=IdentityRole.DATA_CERTIFIER,
            artifact_sha256=_sha("policy"),
            ledger_head_hash=GENESIS_LEDGER_HEAD,
            context_sha256=_sha("test-operation-context"),
            recorded_at=NOW,
        )
    with pytest.raises(ContractValidationError, match="stale"):
        registry.verify(
            attestation,
            expected_role=IdentityRole.OWNER_RATIFIER,
            artifact_sha256=_sha("policy"),
            ledger_head_hash=_sha("different-ledger-head"),
            context_sha256=_sha("test-operation-context"),
            recorded_at=NOW,
        )
    bad_signature = ResearchAttestation(
        **{**attestation.__dict__, "signature_b64": base64.b64encode(b"x" * 64).decode("ascii")}
    )
    with pytest.raises(ContractValidationError, match="signature"):
        registry.verify(
            bad_signature,
            expected_role=IdentityRole.OWNER_RATIFIER,
            artifact_sha256=_sha("policy"),
            ledger_head_hash=GENESIS_LEDGER_HEAD,
            context_sha256=_sha("test-operation-context"),
            recorded_at=NOW,
        )


def test_registry_rejects_shared_public_key_and_revoked_key_use():
    private, owner = _key("owner.one", "owner.2026", (IdentityRole.OWNER_RATIFIER,))
    _, shared = _key(
        "owner.two", "owner.2027", (IdentityRole.PREREGISTRATION_AUTHOR,), private=private
    )
    _, data = _key("data.certifier", "data.2026", (IdentityRole.DATA_CERTIFIER,))
    _, reviewer = _key("reviewer.independent", "review.2026", (IdentityRole.INDEPENDENT_REVIEWER,))
    _, exporter = _key("ledger.exporter", "exporter.2026", (IdentityRole.LEDGER_EXPORTER,))
    with pytest.raises(ContractValidationError, match="shared"):
        IdentityRegistry(
            registry_id="caerus.research.identity", keys=(owner, shared, data, reviewer, exporter), issued_at=NOW
        )

    revoked_private, revoked = _key(
        "old.owner", "owner.old", (IdentityRole.OWNER_RATIFIER,),
        status=IdentityStatus.REVOKED,
        revoked_at=NOW - timedelta(minutes=2),
        replacement_key_id="owner.new",
    )
    _, replacement = _key("old.owner", "owner.new", (IdentityRole.OWNER_RATIFIER,))
    _, author = _key("research.author", "author.2026", (IdentityRole.PREREGISTRATION_AUTHOR,))
    registry = _anchored_registry((revoked, replacement, author, data, reviewer, exporter))
    revoked_attestation = _attestation(
        registry, revoked_private, revoked, role=IdentityRole.OWNER_RATIFIER,
        artifact_sha256=_sha("policy"), ledger_head_hash=GENESIS_LEDGER_HEAD, at=NOW + timedelta(minutes=1)
    )
    with pytest.raises(ContractValidationError, match="revoked"):
        registry.verify(
            revoked_attestation, expected_role=IdentityRole.OWNER_RATIFIER,
            artifact_sha256=_sha("policy"), ledger_head_hash=GENESIS_LEDGER_HEAD,
            context_sha256=_sha("test-operation-context"),
            recorded_at=NOW + timedelta(minutes=1),
        )
    retired_private, retired = _key(
        "old.author", "author.old", (IdentityRole.PREREGISTRATION_AUTHOR,),
        status=IdentityStatus.RETIRED,
    )
    _, current_author = _key("old.author", "author.new", (IdentityRole.PREREGISTRATION_AUTHOR,))
    retired_registry = _anchored_registry((replacement, retired, current_author, data, reviewer, exporter))
    retired_attestation = _attestation(
        retired_registry, retired_private, retired, role=IdentityRole.PREREGISTRATION_AUTHOR,
        artifact_sha256=_sha("experiment"), ledger_head_hash=GENESIS_LEDGER_HEAD,
    )
    with pytest.raises(ContractValidationError, match="not active"):
        retired_registry.verify(
            retired_attestation, expected_role=IdentityRole.PREREGISTRATION_AUTHOR,
            artifact_sha256=_sha("experiment"), ledger_head_hash=GENESIS_LEDGER_HEAD, recorded_at=NOW,
            context_sha256=_sha("test-operation-context"),
        )


def test_enrolled_ledger_requires_signed_owner_ratification(tmp_path):
    registry, private, keys = _registry()
    history = IdentityRegistryHistory(
        registries=(registry,),
        active_registry_hash=registry.registry_hash,
        externally_pinned_registry_hash=registry.registry_hash,
    )
    root = tmp_path / "research"
    root.mkdir()
    ledger = GlobalResearchLedger(
        root / "ledger.jsonl", research_root=root, identity_history=history
    )
    with pytest.raises(EventStoreIntegrityError, match="owner_ratifier"):
        ledger.register_wave(_wave(), recorded_at=NOW)
    payload = _wave().to_dict()
    payload_sha256 = typed_event_payload_hash(
        GlobalResearchLedger.WAVE_EVENT, payload
    )
    context_sha256 = event_attestation_context_hash(
        event_id="wave:WAVE-2026-001",
        event_type=GlobalResearchLedger.WAVE_EVENT,
        occurred_at=NOW,
        recorded_at=NOW,
        payload_sha256=payload_sha256,
        previous_event_hash=GENESIS_LEDGER_HEAD,
    )
    attestation = _attestation(
        registry, private["brett.owner"], keys["brett.owner"],
        role=IdentityRole.OWNER_RATIFIER, artifact_sha256=payload_sha256,
        ledger_head_hash=GENESIS_LEDGER_HEAD,
        context_sha256=context_sha256,
    )
    ledger.register_wave(
        _wave(), recorded_at=NOW, event_attestation=attestation.to_dict()
    )
    assert ledger.store.read_all()[0].event_attestation["identity_id"] == "brett.owner"


def test_cross_role_self_certification_is_rejected_at_registry_enrollment():
    with pytest.raises(ContractValidationError, match="prohibited independent-control roles"):
        _registry(author_roles=(IdentityRole.PREREGISTRATION_AUTHOR, IdentityRole.DATA_CERTIFIER))


def test_registry_release_loader_and_external_pin_reject_substitution_or_rollback():
    registry, _, _ = _registry()
    loaded = IdentityRegistry.from_dict(registry.to_dict(), trust_anchor=registry.trust_anchor)
    assert loaded.registry_hash == registry.registry_hash
    history = IdentityRegistryHistory(
        registries=(loaded,), active_registry_hash=loaded.registry_hash,
        externally_pinned_registry_hash=loaded.registry_hash,
    )
    assert history.resolve(loaded.registry_hash) is loaded
    with pytest.raises(ContractValidationError, match="external identity pin"):
        IdentityRegistryHistory(
            registries=(loaded,), active_registry_hash=loaded.registry_hash,
            externally_pinned_registry_hash=_sha("substituted-directory"),
        )


def test_owner_subject_cannot_also_hold_an_independent_role():
    _, owner = _key(
        "brett.owner", "owner.2026", (IdentityRole.OWNER_RATIFIER,),
        subject_id="person.same",
    )
    _, author = _key(
        "research.author", "author.2026", (IdentityRole.PREREGISTRATION_AUTHOR,),
        subject_id="person.same",
    )
    _, data = _key("data.certifier", "data.2026", (IdentityRole.DATA_CERTIFIER,))
    _, reviewer = _key(
        "reviewer.independent", "review.2026", (IdentityRole.INDEPENDENT_REVIEWER,)
    )
    _, exporter = _key("ledger.exporter", "exporter.2026", (IdentityRole.LEDGER_EXPORTER,))
    with pytest.raises(ContractValidationError, match="across its lifetime"):
        IdentityRegistry(
            registry_id="caerus.research.identity",
            keys=(owner, author, data, reviewer, exporter),
            issued_at=NOW,
        )


def test_registry_history_allows_cumulative_same_role_rotation():
    root_private, anchor, first, keys = _history_release_one()
    owner, author, data, reviewer, exporter = keys
    _, replacement = _key(
        "research.author",
        "author.2027",
        (IdentityRole.PREREGISTRATION_AUTHOR,),
        subject_id=author.subject_id,
    )
    replacement = replace(replacement, issued_at=NOW)
    retired = replace(
        author,
        status=IdentityStatus.RETIRED,
        replacement_key_id=replacement.key_id,
    )
    second = _released_registry(
        (owner, retired, replacement, data, reviewer, exporter),
        version=2,
        issued_at=NOW + timedelta(minutes=30),
        released_at=NOW + timedelta(hours=1),
        root_private=root_private,
        anchor=anchor,
        previous=first,
    )
    history = IdentityRegistryHistory(
        registries=(first, second),
        active_registry_hash=second.registry_hash,
        externally_pinned_registry_hash=second.registry_hash,
    )
    assert history.resolve(first.registry_hash) is first
    assert history.resolve(second.registry_hash) is second


def test_registry_history_rejects_key_mutation_removal_and_resurrection():
    root_private, anchor, first, keys = _history_release_one()
    owner, author, data, reviewer, exporter = keys
    _, different_key = _key(
        author.identity_id, author.key_id, author.allowed_roles,
        subject_id=author.subject_id,
    )
    mutated = replace(
        different_key,
        issued_at=author.issued_at,
    )
    mutation_release = _released_registry(
        (owner, mutated, data, reviewer, exporter),
        version=2,
        issued_at=NOW + timedelta(minutes=30),
        released_at=NOW + timedelta(hours=1),
        root_private=root_private,
        anchor=anchor,
        previous=first,
    )
    with pytest.raises(ContractValidationError, match="public key"):
        IdentityRegistryHistory(
            registries=(first, mutation_release),
            active_registry_hash=mutation_release.registry_hash,
            externally_pinned_registry_hash=mutation_release.registry_hash,
        )

    _, replacement_owner = _key(
        "brett.owner.new", "owner.2027", (IdentityRole.OWNER_RATIFIER,),
        subject_id=owner.subject_id,
    )
    replacement_owner = replace(replacement_owner, issued_at=NOW)
    removal_release = _released_registry(
        (replacement_owner, author, data, reviewer, exporter),
        version=2,
        issued_at=NOW + timedelta(minutes=30),
        released_at=NOW + timedelta(hours=1),
        root_private=root_private,
        anchor=anchor,
        previous=first,
    )
    with pytest.raises(ContractValidationError, match="cannot remove"):
        IdentityRegistryHistory(
            registries=(first, removal_release),
            active_registry_hash=removal_release.registry_hash,
            externally_pinned_registry_hash=removal_release.registry_hash,
        )

    _, replacement = _key(
        author.identity_id,
        "author.2027",
        author.allowed_roles,
        subject_id=author.subject_id,
    )
    replacement = replace(replacement, issued_at=NOW)
    retired = replace(
        author,
        status=IdentityStatus.RETIRED,
        replacement_key_id=replacement.key_id,
    )
    retired_first = _released_registry(
        (owner, retired, replacement, data, reviewer, exporter),
        version=1,
        issued_at=NOW + timedelta(minutes=5),
        released_at=NOW + timedelta(minutes=10),
        root_private=root_private,
        anchor=anchor,
    )
    resurrected = replace(retired, status=IdentityStatus.ACTIVE)
    resurrection_release = _released_registry(
        (owner, resurrected, replacement, data, reviewer, exporter),
        version=2,
        issued_at=NOW + timedelta(minutes=30),
        released_at=NOW + timedelta(hours=1),
        root_private=root_private,
        anchor=anchor,
        previous=retired_first,
    )
    with pytest.raises(ContractValidationError, match="resurrected"):
        IdentityRegistryHistory(
            registries=(retired_first, resurrection_release),
            active_registry_hash=resurrection_release.registry_hash,
            externally_pinned_registry_hash=resurrection_release.registry_hash,
        )


def test_registry_history_rejects_incoherent_directory_time():
    root_private, anchor, first, keys = _history_release_one()
    second = _released_registry(
        keys,
        version=2,
        issued_at=NOW - timedelta(minutes=100),
        released_at=NOW,
        root_private=root_private,
        anchor=anchor,
        previous=first,
    )
    with pytest.raises(ContractValidationError, match="predates"):
        IdentityRegistryHistory(
            registries=(first, second),
            active_registry_hash=second.registry_hash,
            externally_pinned_registry_hash=second.registry_hash,
        )


def test_registry_release_time_is_an_exact_attestation_and_event_lower_bound():
    registry, private, keys = _registry()
    assert registry.release is not None
    release_time = registry.release.released_at
    history = IdentityRegistryHistory(
        registries=(registry,),
        active_registry_hash=registry.registry_hash,
        externally_pinned_registry_hash=registry.registry_hash,
    )
    artifact_sha256 = _sha("release-boundary-artifact")
    context_sha256 = _sha("release-boundary-context")

    before = _attestation(
        registry,
        private["brett.owner"],
        keys["brett.owner"],
        role=IdentityRole.OWNER_RATIFIER,
        artifact_sha256=artifact_sha256,
        ledger_head_hash=GENESIS_LEDGER_HEAD,
        context_sha256=context_sha256,
        at=release_time - timedelta(microseconds=1),
    )
    with pytest.raises(ContractValidationError, match="predates the referenced"):
        history.verify(
            before,
            expected_role=IdentityRole.OWNER_RATIFIER,
            artifact_sha256=artifact_sha256,
            ledger_head_hash=GENESIS_LEDGER_HEAD,
            context_sha256=context_sha256,
            recorded_at=release_time,
        )

    at_boundary = _attestation(
        registry,
        private["brett.owner"],
        keys["brett.owner"],
        role=IdentityRole.OWNER_RATIFIER,
        artifact_sha256=artifact_sha256,
        ledger_head_hash=GENESIS_LEDGER_HEAD,
        context_sha256=context_sha256,
        at=release_time,
    )
    history.verify(
        at_boundary,
        expected_role=IdentityRole.OWNER_RATIFIER,
        artifact_sha256=artifact_sha256,
        ledger_head_hash=GENESIS_LEDGER_HEAD,
        context_sha256=context_sha256,
        recorded_at=release_time,
    )

    after_boundary = _attestation(
        registry,
        private["brett.owner"],
        keys["brett.owner"],
        role=IdentityRole.OWNER_RATIFIER,
        artifact_sha256=artifact_sha256,
        ledger_head_hash=GENESIS_LEDGER_HEAD,
        context_sha256=context_sha256,
        at=release_time + timedelta(seconds=1),
    )
    history.verify(
        after_boundary,
        expected_role=IdentityRole.OWNER_RATIFIER,
        artifact_sha256=artifact_sha256,
        ledger_head_hash=GENESIS_LEDGER_HEAD,
        context_sha256=context_sha256,
        recorded_at=release_time + timedelta(seconds=1),
    )

    with pytest.raises(ContractValidationError, match="predates the referenced"):
        history.verify(
            at_boundary,
            expected_role=IdentityRole.OWNER_RATIFIER,
            artifact_sha256=artifact_sha256,
            ledger_head_hash=GENESIS_LEDGER_HEAD,
            context_sha256=context_sha256,
            recorded_at=release_time - timedelta(microseconds=1),
        )
    with pytest.raises(ContractValidationError, match="recording window"):
        history.verify(
            at_boundary,
            expected_role=IdentityRole.OWNER_RATIFIER,
            artifact_sha256=artifact_sha256,
            ledger_head_hash=GENESIS_LEDGER_HEAD,
            context_sha256=context_sha256,
            recorded_at=release_time + timedelta(hours=25),
        )

    owner_key = keys["brett.owner"]
    before_key_issuance = _attestation(
        registry,
        private["brett.owner"],
        owner_key,
        role=IdentityRole.OWNER_RATIFIER,
        artifact_sha256=artifact_sha256,
        ledger_head_hash=GENESIS_LEDGER_HEAD,
        context_sha256=context_sha256,
        at=owner_key.issued_at - timedelta(microseconds=1),
    )
    with pytest.raises(ContractValidationError, match="predates key issuance"):
        registry.verify(
            before_key_issuance,
            expected_role=IdentityRole.OWNER_RATIFIER,
            artifact_sha256=artifact_sha256,
            ledger_head_hash=GENESIS_LEDGER_HEAD,
            context_sha256=context_sha256,
            recorded_at=owner_key.issued_at,
        )

"""Public-only external-signer ceremonies for Alpha Lab research authority.

Every prepare command emits exact canonical Ed25519 signing bytes and a review
manifest. Finalize commands accept only a detached 64-byte signature (raw or
strict base64), immediately verify it against the externally pinned public
history, and create no private key or signing credential.

This module is research-only. Its publisher remains dry-run unless ``--write``
is supplied and the separate owner-signed QS-003 authorization verifies.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from projects.alpha_lab.control_plane.authenticated_ledger import (
    EVENT_ATTESTATION_SCHEMA,
    IDENTITY_BUNDLE_SCHEMA,
    strict_load_json_object,
)

from .canonical import (
    canonical_hash,
    canonical_json,
    format_datetime,
    parse_datetime,
    require_sha256,
)
from .errors import ContractValidationError
from .import_research_ledger import (
    AUTHORITATIVE_REPO_ROOT,
    EXACT_FAMILY_MAPPING,
    LEDGER_RELATIVE_PATH,
    LEGACY_CHALLENGE_EPOCH_ID,
    LEGACY_WAVE_ID,
    OWNER_NORMALIZATION_NOTICE,
    OWNER_NORMALIZED_FAMILY_DEFINITIONS,
    PUBLICATION_AUTHORIZATION_RULE,
    PUBLICATION_AUTHORIZATION_SCHEMA,
    PUBLICATION_MODE,
    SIGNED_PUBLICATION_AUTHORIZATION_SCHEMA,
    REQUIRED_LEGACY_DEFINITION_BLOCKERS,
    UNRESOLVED_HYP_001_PRIMARY,
    UNRESOLVED_HYP_009_PRIMARY,
    UNRESOLVED_HYP_010_BENCHMARK,
    _canonical_census,
    _records_from_plan_and_inventory,
    _validate_canonical_census,
    _validate_migration_definition,
    audit_existing,
    build_migration_event_plan,
    build_publication_authorization,
    publication_authorization_attestation_context_hash,
    publish_signed_migration_plan,
    verify_signed_publication_authorization,
)
from .projection_export import (
    SIGNED_SCHEMA_VERSION,
    UNSIGNED_SCHEMA_VERSION,
    build_signed_projection_export,
    build_unsigned_projection_export,
    projection_export_attestation_context_hash,
)
from .research_identity import (
    GENESIS_LEDGER_HEAD,
    IdentityActivationEvidence,
    IdentityRegistry,
    IdentityRegistryHistory,
    IdentityRole,
    IdentityTrustAnchor,
    RegistryRelease,
    ResearchAttestation,
    event_attestation_context_hash,
    migration_plan_attestation_context_hash,
    typed_event_payload_hash,
)
from .research_ledger import GlobalResearchLedger


ATTESTATION_REQUEST_SCHEMA = "caerus_alpha_lab_attestation_signing_request_v1"
REGISTRY_REQUEST_SCHEMA = "caerus_alpha_lab_registry_release_signing_request_v1"
REGISTRY_FINALIZATION_SCHEMA = "caerus_alpha_lab_registry_release_finalization_v1"
MIGRATION_PREPARATION_SCHEMA = "caerus_alpha_lab_migration_preparation_v1"
PROJECTION_PREPARATION_SCHEMA = "caerus_alpha_lab_projection_preparation_v1"
PUBLICATION_PREPARATION_SCHEMA = "caerus_alpha_lab_publication_preparation_v1"
MIGRATION_PACKET_SCHEMA = "caerus_alpha_lab_migration_owner_signing_packet_v1"
MIGRATION_PLAN_SCHEMA = "caerus_alpha_lab_migration_event_plan_v2"
SIGNED_MIGRATION_PLAN_SCHEMA = "caerus_alpha_lab_signed_migration_plan_v1"

_ZERO_SIGNATURE_B64 = base64.b64encode(b"\x00" * 64).decode("ascii")
_ATTESTATION_REQUEST_FIELDS = {
    "schema_version",
    "context_kind",
    "context_artifact",
    "subject_id",
    "registry_history_sha256",
    "externally_pinned_registry_hash",
    "recorded_at",
    "for_new_event",
    "signed_payload",
    "signing_bytes_sha256",
    "private_key_material_present",
}

_OWNER_PACKET_REGISTRY_CONTROL = {
    "active_registry_release_hash": "REQUIRED_LOWERCASE_SHA256",
    "externally_pinned_registry_hash": "REQUIRED_SAME_LOWERCASE_SHA256",
    "pin_match_verified": False,
}
_OWNER_PACKET_PLAN_CONTROL = {
    "migration_event_plan_sha256": "REQUIRED_LOWERCASE_SHA256",
    "plan_identity_sha256": "REQUIRED_LOWERCASE_SHA256",
    "ordered_event_count": None,
    "expected_terminal_head": "REQUIRED_LOWERCASE_SHA256",
    "identity_activation_head_hash": "REQUIRED_SAME_AS_EXPECTED_TERMINAL_HEAD",
    "expected_ledger_sha256": "REQUIRED_LOWERCASE_SHA256",
    "scratch_bytes_and_hash_verified": False,
}
_OWNER_PACKET_PUBLICATION_CONTROL = {
    "canonical_path": str(AUTHORITATIVE_REPO_ROOT / LEDGER_RELATIVE_PATH),
    "mode": PUBLICATION_MODE,
    "overwrite_or_repair_allowed": False,
    "authorized": False,
    "signed_publication_authorization_sha256": (
        "REQUIRED_AFTER_MIGRATION_PLAN_IS_SIGNED"
    ),
    "authorization_rule": PUBLICATION_AUTHORIZATION_RULE,
}


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return canonical_json(value).encode("utf-8")


def _strict_object(path: Path, *, reject_private: bool = False) -> Dict[str, Any]:
    value = strict_load_json_object(path)
    if reject_private:
        _reject_secret_material(value)
    return value


def _reject_secret_material(value: Any) -> None:
    """Reject credentials while permitting the explicit false safety flag."""

    if isinstance(value, Mapping):
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered == "private_key_material_present" and item is False:
                continue
            if any(marker in lowered for marker in ("private", "secret", "token")):
                raise ContractValidationError(
                    "ceremony material must not contain private or secret fields"
                )
            _reject_secret_material(item)
    elif isinstance(value, list):
        for item in value:
            _reject_secret_material(item)
    elif isinstance(value, str) and "PRIVATE KEY" in value:
        raise ContractValidationError("ceremony material contains private key material")


def _write_create_only(path: Path, value: bytes) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def _emit_json(value: Mapping[str, Any], output: Optional[Path]) -> None:
    payload = _canonical_bytes(value)
    if output is None:
        sys.stdout.write(payload.decode("utf-8") + "\n")
        return
    _write_create_only(output, payload)


def _emit_prepare_bundle(
    *,
    output_dir: Path,
    artifact_name: str,
    artifact: Mapping[str, Any],
    review_manifest: Mapping[str, Any],
) -> None:
    """Create a review bundle create-only, with the review manifest last."""

    destination = output_dir.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    try:
        _write_create_only(destination / artifact_name, _canonical_bytes(artifact))
        signed_payload = review_manifest.get("signed_payload")
        if not isinstance(signed_payload, Mapping):
            raise ContractValidationError("review manifest lacks signing payload")
        _write_create_only(
            destination / "signing_payload.json", _canonical_bytes(signed_payload)
        )
        _write_create_only(
            destination / "review_manifest.json", _canonical_bytes(review_manifest)
        )
        directory_descriptor = os.open(str(destination), os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except Exception:
        shutil.rmtree(destination)
        raise


def _preparation_from_review_manifest(
    raw: Mapping[str, Any], *, expected_schema: str
) -> Mapping[str, Any]:
    """Require the displayed request to equal the nested workflow request."""

    if set(raw) != _ATTESTATION_REQUEST_FIELDS | {"preparation"}:
        raise ContractValidationError("workflow review manifest schema is invalid")
    preparation = raw.get("preparation")
    if not isinstance(preparation, Mapping) or preparation.get("schema_version") != expected_schema:
        raise ContractValidationError("workflow review manifest lacks its preparation")
    nested = preparation.get("attestation_request")
    outer = {key: raw[key] for key in _ATTESTATION_REQUEST_FIELDS}
    if not isinstance(nested, Mapping) or canonical_hash(nested) != canonical_hash(outer):
        raise ContractValidationError("workflow review manifest has divergent signing requests")
    return preparation


def _detached_signature_b64(path: Path) -> str:
    """Accept only a raw 64-byte Ed25519 signature or strict base64 thereof."""

    value = path.expanduser().resolve().read_bytes()
    if len(value) == 64:
        raw = value
    else:
        try:
            text = value.decode("ascii").strip()
            if not text or any(character.isspace() for character in text):
                raise ValueError("embedded whitespace")
            raw = base64.b64decode(text, validate=True)
        except (UnicodeDecodeError, ValueError) as exc:
            raise ContractValidationError(
                "detached signature must be raw 64-byte Ed25519 or strict base64"
            ) from exc
    if len(raw) != 64:
        raise ContractValidationError("detached Ed25519 signature must be exactly 64 bytes")
    return base64.b64encode(raw).decode("ascii")


def load_registry_history(
    path: Path, *, external_pin: str, trust_anchor_path: Path
) -> IdentityRegistryHistory:
    """Load complete public history while requiring its separately supplied pin."""

    raw = _strict_object(path, reject_private=True)
    anchor = IdentityTrustAnchor.from_dict(
        _strict_object(trust_anchor_path, reject_private=True)
    )
    return IdentityRegistryHistory.from_dict(
        raw,
        externally_supplied_pin=external_pin,
        externally_supplied_trust_anchor=anchor,
    )


def prepare_registry_release(
    *,
    directory: Mapping[str, Any],
    trust_anchor: Mapping[str, Any],
    released_at: datetime,
    previous_history: Optional[IdentityRegistryHistory] = None,
) -> Dict[str, Any]:
    """Prepare exact root-signing bytes for one immutable registry release."""

    _reject_secret_material(directory)
    _reject_secret_material(trust_anchor)
    if set(directory) != {"schema_version", "registry_id", "issued_at", "keys"}:
        raise ContractValidationError("unsigned registry directory schema is invalid")
    anchor = IdentityTrustAnchor.from_dict(trust_anchor)
    unsigned = IdentityRegistry.from_dict(directory)
    if unsigned.registry_id != anchor.expected_registry_id:
        raise ContractValidationError("registry directory does not match its trust anchor")
    if previous_history is None:
        version = 1
        previous_registry_hash = None
        previous_release_hash = None
        previous_history_raw = None
    else:
        prior = previous_history.resolve(previous_history.active_registry_hash)
        assert prior.release is not None and prior.trust_anchor is not None
        if canonical_hash(prior.trust_anchor.to_dict()) != canonical_hash(anchor.to_dict()):
            raise ContractValidationError("registry release changes its root trust anchor")
        if prior.registry_id != unsigned.registry_id:
            raise ContractValidationError("registry release changes registry_id")
        version = prior.release.version + 1
        previous_registry_hash = prior.registry_hash
        previous_release_hash = prior.release.release_hash
        previous_history_raw = previous_history.to_dict()
    payload = RegistryRelease.prepare_signed_payload(
        registry_id=unsigned.registry_id,
        registry_hash=unsigned.registry_hash,
        version=version,
        released_at=released_at,
        root_key_id=anchor.root_key_id,
        previous_registry_hash=previous_registry_hash,
        previous_release_hash=previous_release_hash,
    )
    if unsigned.issued_at > released_at:
        raise ContractValidationError("registry directory cannot follow release time")
    signing_bytes = _canonical_bytes(payload)
    return {
        "schema_version": REGISTRY_REQUEST_SCHEMA,
        "operation": "ROOT_SIGN_IDENTITY_REGISTRY_RELEASE",
        "directory": unsigned.directory_dict(),
        "trust_anchor": anchor.to_dict(),
        "previous_history": previous_history_raw,
        "signed_payload": payload,
        "signing_bytes_sha256": _sha256_bytes(signing_bytes),
        "prospective_registry_hash": unsigned.registry_hash,
        "private_key_material_present": False,
    }


def finalize_registry_release(
    request: Mapping[str, Any],
    *,
    signature_b64: str,
    external_pin: str,
    trusted_anchor: Mapping[str, Any],
    previous_history: Optional[IdentityRegistryHistory] = None,
) -> Dict[str, Any]:
    """Finalize, root-verify, and export a complete pinned registry history."""

    expected = {
        "schema_version",
        "operation",
        "directory",
        "trust_anchor",
        "previous_history",
        "signed_payload",
        "signing_bytes_sha256",
        "prospective_registry_hash",
        "private_key_material_present",
    }
    if set(request) != expected or request.get("schema_version") != REGISTRY_REQUEST_SCHEMA:
        raise ContractValidationError("registry signing request schema is invalid")
    if request.get("operation") != "ROOT_SIGN_IDENTITY_REGISTRY_RELEASE" or request.get("private_key_material_present") is not False:
        raise ContractValidationError("registry signing request control fields are invalid")
    directory = request.get("directory")
    anchor_raw = request.get("trust_anchor")
    payload = request.get("signed_payload")
    if not all(isinstance(item, Mapping) for item in (directory, anchor_raw, payload)):
        raise ContractValidationError("registry signing request material is invalid")
    signing_bytes = _canonical_bytes(payload)
    if _sha256_bytes(signing_bytes) != request.get("signing_bytes_sha256"):
        raise ContractValidationError("registry signing bytes differ from reviewed hash")
    anchor = IdentityTrustAnchor.from_dict(anchor_raw)
    external_anchor = IdentityTrustAnchor.from_dict(trusted_anchor)
    if canonical_hash(anchor.to_dict()) != canonical_hash(external_anchor.to_dict()):
        raise ContractValidationError("separately supplied root trust anchor rejects request")
    unsigned = IdentityRegistry.from_dict(directory)
    if unsigned.registry_hash != request.get("prospective_registry_hash") or external_pin != unsigned.registry_hash:
        raise ContractValidationError("external pin does not match reviewed registry directory")
    required_release_fields = {
        "schema_version",
        "registry_id",
        "registry_hash",
        "version",
        "released_at",
        "root_key_id",
        "previous_registry_hash",
        "previous_release_hash",
    }
    if set(payload) != required_release_fields:
        raise ContractValidationError("registry release signing payload is invalid")
    release = RegistryRelease(
        registry_id=str(payload["registry_id"]),
        registry_hash=str(payload["registry_hash"]),
        version=int(payload["version"]),
        released_at=parse_datetime(str(payload["released_at"])),
        root_key_id=str(payload["root_key_id"]),
        signature_b64=signature_b64,
        previous_registry_hash=payload["previous_registry_hash"],
        previous_release_hash=payload["previous_release_hash"],
        schema_version=str(payload["schema_version"]),
    )
    registry = IdentityRegistry(
        registry_id=unsigned.registry_id,
        keys=unsigned.keys,
        issued_at=unsigned.issued_at,
        release=release,
        trust_anchor=anchor,
    )
    previous_raw = request.get("previous_history")
    if previous_raw is None:
        if previous_history is not None:
            raise ContractValidationError("genesis request conflicts with trusted previous history")
        registries = (registry,)
    else:
        if not isinstance(previous_raw, Mapping):
            raise ContractValidationError("registry previous history is invalid")
        if previous_history is None:
            raise ContractValidationError("non-genesis release requires separately supplied previous history")
        if canonical_hash(previous_raw) != canonical_hash(previous_history.to_dict()):
            raise ContractValidationError("registry request changes trusted previous history")
        prior = IdentityRegistryHistory.from_dict(
            previous_raw,
            externally_supplied_pin=previous_history.externally_pinned_registry_hash,
            externally_supplied_trust_anchor=external_anchor,
        )
        registries = prior.registries + (registry,)
    history = IdentityRegistryHistory(
        registries=registries,
        active_registry_hash=registry.registry_hash,
        externally_pinned_registry_hash=external_pin,
    )
    return {
        "schema_version": REGISTRY_FINALIZATION_SCHEMA,
        "signed_registry": registry.to_dict(),
        "registry_history": history.to_dict(),
        "registry_hash": registry.registry_hash,
        "release_hash": release.release_hash,
        "signature_verified": True,
        "private_key_material_present": False,
    }


def export_registry_history(
    *,
    trust_anchor: Mapping[str, Any],
    registries: Sequence[Mapping[str, Any]],
    external_pin: str,
) -> Dict[str, Any]:
    """Validate separate signed releases and export the canonical full history."""

    anchor = IdentityTrustAnchor.from_dict(trust_anchor)
    history = IdentityRegistryHistory(
        registries=tuple(
            IdentityRegistry.from_dict(item, trust_anchor=anchor) for item in registries
        ),
        active_registry_hash=external_pin,
        externally_pinned_registry_hash=external_pin,
    )
    return history.to_dict()


def _event_contract(value: Mapping[str, Any]) -> Dict[str, Any]:
    expected = {
        "schema_version",
        "event_id",
        "event_type",
        "occurred_at",
        "recorded_at",
        "payload",
        "previous_event_hash",
    }
    if set(value) != expected or value.get("schema_version") != "caerus_alpha_lab_event_attestation_draft_v1":
        raise ContractValidationError("event attestation draft schema is invalid")
    payload = value.get("payload")
    if not isinstance(payload, Mapping):
        raise ContractValidationError("event attestation payload must be an object")
    occurred_at = parse_datetime(str(value["occurred_at"]))
    recorded_at = parse_datetime(str(value["recorded_at"]))
    prior = str(value["previous_event_hash"])
    payload_sha = typed_event_payload_hash(str(value["event_type"]), payload)
    return {
        "role": None,
        "artifact_sha256": payload_sha,
        "ledger_head_hash": prior,
        "context_sha256": event_attestation_context_hash(
            event_id=str(value["event_id"]),
            event_type=str(value["event_type"]),
            occurred_at=occurred_at,
            recorded_at=recorded_at,
            payload_sha256=payload_sha,
            previous_event_hash=prior,
        ),
        "recorded_at": recorded_at,
    }


def _context_contract(kind: str, artifact: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if kind == "event":
        if artifact is None:
            raise ContractValidationError("event attestation requires an event draft")
        return _event_contract(artifact)
    if kind == "migration":
        if artifact is None or artifact.get("schema_version") != MIGRATION_PLAN_SCHEMA:
            raise ContractValidationError("migration attestation requires a canonical plan")
        return {
            "role": IdentityRole.OWNER_RATIFIER,
            "artifact_sha256": canonical_hash(artifact),
            "ledger_head_hash": GENESIS_LEDGER_HEAD,
            "context_sha256": migration_plan_attestation_context_hash(artifact),
            "recorded_at": parse_datetime(str(artifact["recorded_at"])),
        }
    if kind == "publication":
        if artifact is None or artifact.get("schema_version") != PUBLICATION_AUTHORIZATION_SCHEMA:
            raise ContractValidationError("publication attestation requires QS-003 payload")
        return {
            "role": IdentityRole.OWNER_RATIFIER,
            "artifact_sha256": canonical_hash(artifact),
            "ledger_head_hash": GENESIS_LEDGER_HEAD,
            "context_sha256": publication_authorization_attestation_context_hash(artifact),
            "recorded_at": parse_datetime(str(artifact["authorized_at"])),
        }
    if kind == "projection":
        if artifact is None or artifact.get("schema_version") != UNSIGNED_SCHEMA_VERSION:
            raise ContractValidationError("projection attestation requires unsigned export")
        receipt = artifact.get("source_ledger_receipt")
        if not isinstance(receipt, Mapping):
            raise ContractValidationError("projection export lacks source receipt")
        return {
            "role": IdentityRole.LEDGER_EXPORTER,
            "artifact_sha256": canonical_hash(artifact),
            "ledger_head_hash": receipt.get("event_chain_head") or GENESIS_LEDGER_HEAD,
            "context_sha256": projection_export_attestation_context_hash(artifact),
            "recorded_at": parse_datetime(str(artifact["exported_at"])),
        }
    if kind != "generic":
        raise ContractValidationError("attestation context kind is invalid")
    return {}


def prepare_attestation_request(
    *,
    identity_history: IdentityRegistryHistory,
    identity_id: str,
    key_id: str,
    role: IdentityRole,
    attested_at: datetime,
    context_kind: str,
    context_artifact: Optional[Mapping[str, Any]] = None,
    artifact_sha256: Optional[str] = None,
    ledger_head_hash: Optional[str] = None,
    context_sha256: Optional[str] = None,
    recorded_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Prepare exact ResearchAttestation signing bytes for an external signer."""

    contract = _context_contract(context_kind, context_artifact)
    expected_role = contract.get("role")
    if expected_role is not None and role is not expected_role:
        raise ContractValidationError("role does not match attestation context")
    if context_kind == "generic":
        if artifact_sha256 is None or ledger_head_hash is None or context_sha256 is None or recorded_at is None:
            raise ContractValidationError("generic attestation requires all immutable hashes and recorded_at")
        require_sha256(artifact_sha256, "artifact_sha256")
        require_sha256(context_sha256, "context_sha256")
        contract = {
            "role": role,
            "artifact_sha256": artifact_sha256,
            "ledger_head_hash": ledger_head_hash,
            "context_sha256": context_sha256,
            "recorded_at": recorded_at,
        }
    parsed_recorded_at = contract["recorded_at"]
    draft = ResearchAttestation(
        identity_id=identity_id,
        key_id=key_id,
        role=role,
        artifact_sha256=str(contract["artifact_sha256"]),
        ledger_head_hash=str(contract["ledger_head_hash"]),
        context_sha256=str(contract["context_sha256"]),
        attested_at=attested_at,
        signature_b64=_ZERO_SIGNATURE_B64,
        registry_hash=identity_history.active_registry_hash,
    )
    subject_id = identity_history.subject_id_for(
        identity_id=identity_id,
        key_id=key_id,
        registry_hash=identity_history.active_registry_hash,
    )
    signing_bytes = _canonical_bytes(draft.signed_payload())
    return {
        "schema_version": ATTESTATION_REQUEST_SCHEMA,
        "context_kind": context_kind,
        "context_artifact": dict(context_artifact) if context_artifact is not None else None,
        "subject_id": subject_id,
        "registry_history_sha256": canonical_hash(identity_history.to_dict()),
        "externally_pinned_registry_hash": identity_history.externally_pinned_registry_hash,
        "recorded_at": format_datetime(parsed_recorded_at),
        "for_new_event": True,
        "signed_payload": draft.signed_payload(),
        "signing_bytes_sha256": _sha256_bytes(signing_bytes),
        "private_key_material_present": False,
    }


def _validate_attestation_request(
    request: Mapping[str, Any], *, identity_history: IdentityRegistryHistory
) -> tuple[ResearchAttestation, Dict[str, Any]]:
    if set(request) != _ATTESTATION_REQUEST_FIELDS or request.get("schema_version") != ATTESTATION_REQUEST_SCHEMA:
        raise ContractValidationError("attestation signing request schema is invalid")
    if request.get("for_new_event") is not True or request.get("private_key_material_present") is not False:
        raise ContractValidationError("attestation signing request control fields are invalid")
    if request.get("externally_pinned_registry_hash") != identity_history.externally_pinned_registry_hash:
        raise ContractValidationError("attestation request external pin is stale")
    if request.get("registry_history_sha256") != canonical_hash(identity_history.to_dict()):
        raise ContractValidationError("attestation request registry history has changed")
    signed_payload = request.get("signed_payload")
    artifact = request.get("context_artifact")
    if not isinstance(signed_payload, Mapping) or (artifact is not None and not isinstance(artifact, Mapping)):
        raise ContractValidationError("attestation request payload is invalid")
    if _sha256_bytes(_canonical_bytes(signed_payload)) != request.get("signing_bytes_sha256"):
        raise ContractValidationError("attestation signing bytes differ from reviewed hash")
    kind = str(request["context_kind"])
    role = IdentityRole(str(signed_payload.get("role")))
    if kind == "generic":
        contract = {
            "role": role,
            "artifact_sha256": str(signed_payload.get("artifact_sha256")),
            "ledger_head_hash": str(signed_payload.get("ledger_head_hash")),
            "context_sha256": str(signed_payload.get("context_sha256")),
            "recorded_at": parse_datetime(str(request["recorded_at"])),
        }
    else:
        contract = _context_contract(kind, artifact)
    expected = {
        "role": role,
        "artifact_sha256": str(contract["artifact_sha256"]),
        "ledger_head_hash": str(contract["ledger_head_hash"]),
        "context_sha256": str(contract["context_sha256"]),
        "recorded_at": parse_datetime(str(request["recorded_at"])),
    }
    if contract.get("role") is not None and role is not contract["role"]:
        raise ContractValidationError("attestation role differs from reviewed context")
    if parse_datetime(str(request["recorded_at"])) != contract["recorded_at"]:
        raise ContractValidationError("attestation recording time differs from reviewed context")
    placeholder = ResearchAttestation.from_dict(
        {**dict(signed_payload), "signature_b64": _ZERO_SIGNATURE_B64}
    )
    if placeholder.signed_payload() != dict(signed_payload):
        raise ContractValidationError("attestation signed payload has extra or implicit fields")
    if (
        placeholder.artifact_sha256 != expected["artifact_sha256"]
        or placeholder.ledger_head_hash != expected["ledger_head_hash"]
        or placeholder.context_sha256 != expected["context_sha256"]
        or placeholder.registry_hash != identity_history.active_registry_hash
        or request.get("subject_id")
        != identity_history.subject_id_for(
            identity_id=placeholder.identity_id,
            key_id=placeholder.key_id,
            registry_hash=placeholder.registry_hash,
        )
    ):
        raise ContractValidationError("attestation signed payload differs from reviewed contract")
    return placeholder, expected


def finalize_attestation_request(
    request: Mapping[str, Any],
    *,
    signature_b64: str,
    identity_history: IdentityRegistryHistory,
) -> Dict[str, Any]:
    """Ingest and immediately public-key verify one detached signature."""

    placeholder, expected = _validate_attestation_request(
        request, identity_history=identity_history
    )
    attestation = ResearchAttestation.from_dict(
        {**placeholder.signed_payload(), "signature_b64": signature_b64}
    )
    identity_history.verify(
        attestation,
        expected_role=expected["role"],
        artifact_sha256=expected["artifact_sha256"],
        ledger_head_hash=expected["ledger_head_hash"],
        context_sha256=expected["context_sha256"],
        recorded_at=expected["recorded_at"],
        for_new_event=True,
    )
    result = attestation.to_dict()
    if request["context_kind"] == "event":
        artifact = request["context_artifact"]
        assert isinstance(artifact, Mapping)
        return {
            "schema_version": EVENT_ATTESTATION_SCHEMA,
            "event_id": artifact["event_id"],
            "event_attestation": result,
        }
    return result


def verify_attestation_result(
    request: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    identity_history: IdentityRegistryHistory,
) -> Dict[str, Any]:
    """Re-verify a finalized generic or event-wrapped attestation."""

    if result.get("schema_version") == EVENT_ATTESTATION_SCHEMA:
        if set(result) != {"schema_version", "event_id", "event_attestation"}:
            raise ContractValidationError("event attestation wrapper is invalid")
        attestation_raw = result.get("event_attestation")
    else:
        attestation_raw = result
    if not isinstance(attestation_raw, Mapping):
        raise ContractValidationError("attestation result is invalid")
    parsed = ResearchAttestation.from_dict(attestation_raw)
    expected = finalize_attestation_request(
        request,
        signature_b64=parsed.signature_b64,
        identity_history=identity_history,
    )
    if canonical_hash(expected) != canonical_hash(result):
        raise ContractValidationError("attestation result differs from reviewed request")
    return dict(result)


def migration_definition_from_owner_packet(
    packet: Mapping[str, Any],
    *,
    recorded_at: datetime,
    inventory: Mapping[str, Any],
) -> Dict[str, Any]:
    """Extract the exact 13-family owner draft into a signable definition."""

    expected_packet_fields = {
        "schema_version",
        "status",
        "owner",
        "decision",
        "recorded_at",
        "private_key_material_present",
        "family_mappings",
        "family_definitions_status",
        "family_definitions",
        "family_merge_policy",
        "unresolved_legacy_definition_blockers",
        "required_unresolved_sentinels",
        "registry_control",
        "deterministic_plan_control",
        "source_receipt_census",
        "gcp_publication_authorization",
        "owner_attestation",
    }
    if set(packet) != expected_packet_fields or packet.get("schema_version") != MIGRATION_PACKET_SCHEMA:
        raise ContractValidationError("migration owner packet schema is invalid")
    if (
        packet.get("status") != "UNSIGNED_NOT_AUTHORIZED"
        or packet.get("owner") != "Brett Olson"
        or packet.get("decision") != "PENDING_PERSONAL_RATIFICATION"
    ):
        raise ContractValidationError("migration owner packet is not the unsigned Brett review packet")
    packet_time = packet.get("recorded_at")
    if packet_time not in {
        "SUPPLY_EXPLICIT_CANONICAL_UTC_AT_CEREMONY_PREPARATION",
        format_datetime(recorded_at),
    }:
        raise ContractValidationError("migration owner packet recorded_at conflicts with ceremony time")
    if packet.get("owner_attestation") is not None or packet.get("private_key_material_present") is not False:
        raise ContractValidationError("migration owner packet contains premature authority material")
    if packet.get("family_merge_policy") != "REJECT_ALL_MERGES":
        raise ContractValidationError("migration owner packet weakens the no-merge policy")
    if packet.get("family_definitions_status") != OWNER_NORMALIZATION_NOTICE:
        raise ContractValidationError("migration owner packet misclassifies normalized family definitions")
    if packet.get("family_mappings") != EXACT_FAMILY_MAPPING or packet.get("family_definitions") != OWNER_NORMALIZED_FAMILY_DEFINITIONS:
        raise ContractValidationError("migration owner packet differs from exact normalized families")
    expected_blockers = {
        hypothesis_id: list(blockers)
        for hypothesis_id, blockers in REQUIRED_LEGACY_DEFINITION_BLOCKERS.items()
        if blockers
    }
    if packet.get("unresolved_legacy_definition_blockers") != expected_blockers:
        raise ContractValidationError("migration owner packet blocker summary is incomplete")
    if packet.get("required_unresolved_sentinels") != {
        "HYP-2026-001.primary_variant_id": UNRESOLVED_HYP_001_PRIMARY,
        "HYP-2026-009.primary_variant_id": UNRESOLVED_HYP_009_PRIMARY,
        "HYP-2026-010.benchmark": UNRESOLVED_HYP_010_BENCHMARK,
    }:
        raise ContractValidationError("migration owner packet unresolved sentinels are incomplete")
    if packet.get("registry_control") != _OWNER_PACKET_REGISTRY_CONTROL:
        raise ContractValidationError("migration owner packet registry control is not exact")
    if packet.get("deterministic_plan_control") != _OWNER_PACKET_PLAN_CONTROL:
        raise ContractValidationError("migration owner packet plan control is not exact")
    if packet.get("gcp_publication_authorization") != _OWNER_PACKET_PUBLICATION_CONTROL:
        raise ContractValidationError(
            "migration owner packet separate publication control is not exact"
        )
    census = packet.get("source_receipt_census")
    if not isinstance(census, Mapping):
        raise ContractValidationError("migration owner packet lacks receipt census")
    expected_census = {
        "source_receipts": "POPULATE_FROM_FRESH_CANONICAL_GCP_AUDIT",
        **_canonical_census(inventory),
    }
    if census != expected_census:
        raise ContractValidationError(
            "migration owner packet census differs from exact fresh evidence controls"
        )
    definition = {
        "decision": "RATIFY_GLOBAL_RESEARCH_LEDGER_MIGRATION",
        "owner": "Brett Olson",
        "recorded_at": format_datetime(recorded_at),
        "family_mappings": dict(packet["family_mappings"]),
        "family_definitions": dict(packet["family_definitions"]),
        "wave_id": LEGACY_WAVE_ID,
        "wave_method": "HOLM_BONFERRONI",
        "wave_alpha_or_q": 0.05,
        "dependence_contract": {
            "assumption": "NO_POSITIVE_DEPENDENCE_CLAIM",
            "artifact_sha256": None,
        },
        "challenge_epoch_id": LEGACY_CHALLENGE_EPOCH_ID,
    }
    return _validate_migration_definition(definition, inventory)


def prepare_migration(
    *,
    repo_root: Path,
    data_root: Path,
    owner_packet: Mapping[str, Any],
    recorded_at: datetime,
    identity_history: IdentityRegistryHistory,
    owner_identity_id: str,
    owner_key_id: str,
) -> Dict[str, Any]:
    """Fresh-audit evidence and prepare the deterministic owner-signed plan."""

    inventory = audit_existing(repo_root=repo_root, data_root=data_root)
    _validate_canonical_census(inventory)
    definition = migration_definition_from_owner_packet(
        owner_packet, recorded_at=recorded_at, inventory=inventory
    )
    plan = build_migration_event_plan(
        inventory=inventory,
        migration_definition=definition,
        active_registry_hash=identity_history.active_registry_hash,
        externally_pinned_registry_hash=identity_history.externally_pinned_registry_hash,
    )
    request = prepare_attestation_request(
        identity_history=identity_history,
        identity_id=owner_identity_id,
        key_id=owner_key_id,
        role=IdentityRole.OWNER_RATIFIER,
        attested_at=recorded_at,
        context_kind="migration",
        context_artifact=plan,
    )
    return {
        "schema_version": MIGRATION_PREPARATION_SCHEMA,
        "migration_definition": definition,
        "migration_event_plan": plan,
        "fresh_inventory_sha256": canonical_hash(inventory),
        "fresh_receipt_set": {
            "source_receipts": inventory["source_receipts"],
            "hypothesis_sources": inventory["hypothesis_sources"],
            "census": _canonical_census(inventory),
        },
        "signed_payload": request["signed_payload"],
        "attestation_request": request,
        "private_key_material_present": False,
    }


def finalize_migration(
    preparation: Mapping[str, Any],
    *,
    signature_b64: str,
    identity_history: IdentityRegistryHistory,
) -> Dict[str, Any]:
    expected_fields = {
        "schema_version",
        "migration_definition",
        "migration_event_plan",
        "fresh_inventory_sha256",
        "fresh_receipt_set",
        "signed_payload",
        "attestation_request",
        "private_key_material_present",
    }
    if (
        set(preparation) != expected_fields
        or preparation.get("schema_version") != MIGRATION_PREPARATION_SCHEMA
        or preparation.get("private_key_material_present") is not False
    ):
        raise ContractValidationError("migration preparation schema is invalid")
    plan = preparation.get("migration_event_plan")
    request = preparation.get("attestation_request")
    definition = preparation.get("migration_definition")
    receipts = preparation.get("fresh_receipt_set")
    if not all(
        isinstance(item, Mapping)
        for item in (plan, request, definition, receipts)
    ):
        raise ContractValidationError("migration preparation is incomplete")
    _validate_migration_definition(definition, {"hypothesis_sources": {}})
    if set(receipts) != {"source_receipts", "hypothesis_sources", "census"}:
        raise ContractValidationError("migration preparation receipt set is invalid")
    definition_fields = (
        "decision",
        "owner",
        "recorded_at",
        "family_mappings",
        "family_definitions",
        "wave_id",
        "wave_method",
        "wave_alpha_or_q",
        "dependence_contract",
        "challenge_epoch_id",
    )
    if (
        plan.get("source_receipts") != receipts["source_receipts"]
        or plan.get("hypothesis_sources") != receipts["hypothesis_sources"]
        or plan.get("census") != receipts["census"]
        or preparation.get("signed_payload") != request.get("signed_payload")
        or any(plan.get(field) != definition.get(field) for field in definition_fields)
    ):
        raise ContractValidationError("migration preparation differs from reviewed definition or receipts")
    if request.get("context_artifact") != plan:
        raise ContractValidationError("migration plan differs from its signing request")
    attestation = finalize_attestation_request(
        request, signature_b64=signature_b64, identity_history=identity_history
    )
    result = {
        "schema_version": SIGNED_MIGRATION_PLAN_SCHEMA,
        "plan": dict(plan),
        "owner_attestation": attestation,
    }
    IdentityActivationEvidence.from_signed_plan(result, identity_history=identity_history)
    return result


def verify_migration(
    signed_plan: Mapping[str, Any],
    *,
    identity_history: IdentityRegistryHistory,
    repo_root: Optional[Path] = None,
    data_root: Optional[Path] = None,
) -> Dict[str, Any]:
    activation = IdentityActivationEvidence.from_signed_plan(
        signed_plan, identity_history=identity_history
    )
    report: Dict[str, Any] = {
        "schema_version": "caerus_alpha_lab_migration_verification_v1",
        "signed_migration_plan_sha256": canonical_hash(signed_plan),
        "migration_plan_sha256": activation.plan_sha256,
        "identity_activation_head_hash": activation.identity_activation_head_hash,
        "owner_signature_verified": True,
        "fresh_receipts_verified": False,
    }
    if (repo_root is None) != (data_root is None):
        raise ContractValidationError("fresh migration verification requires both roots")
    if repo_root is not None and data_root is not None:
        inventory = audit_existing(repo_root=repo_root, data_root=data_root)
        _validate_canonical_census(inventory)
        records, _ = _records_from_plan_and_inventory(
            plan=activation.plan, inventory=inventory
        )
        activation.verify_legacy_records(records)
        report["fresh_inventory_sha256"] = canonical_hash(inventory)
        report["fresh_receipts_verified"] = True
    return report


def build_control_plane_identity_bundle(
    *,
    identity_history: IdentityRegistryHistory,
    signed_plan: Mapping[str, Any],
) -> Dict[str, Any]:
    """Compose the exact authenticated-ledger loader bundle without ad hoc JSON."""

    IdentityActivationEvidence.from_signed_plan(
        signed_plan, identity_history=identity_history
    )
    history = identity_history.to_dict()
    return {
        "schema_version": IDENTITY_BUNDLE_SCHEMA,
        "identity_trust_anchor": history["trust_anchor"],
        "identity_registries": history["registries"],
        "signed_migration_plan": dict(signed_plan),
    }


def prepare_publication(
    *,
    repo_root: Path,
    data_root: Path,
    signed_plan: Mapping[str, Any],
    authorized_at: datetime,
    identity_history: IdentityRegistryHistory,
    owner_identity_id: str,
    owner_key_id: str,
) -> Dict[str, Any]:
    inventory = audit_existing(repo_root=repo_root, data_root=data_root)
    authorization = build_publication_authorization(
        repo_root=repo_root,
        inventory=inventory,
        signed_plan=signed_plan,
        identity_history=identity_history,
        authorized_at=authorized_at,
    )
    request = prepare_attestation_request(
        identity_history=identity_history,
        identity_id=owner_identity_id,
        key_id=owner_key_id,
        role=IdentityRole.OWNER_RATIFIER,
        attested_at=authorized_at,
        context_kind="publication",
        context_artifact=authorization,
    )
    return {
        "schema_version": PUBLICATION_PREPARATION_SCHEMA,
        "publication_authorization": authorization,
        "fresh_inventory_sha256": canonical_hash(inventory),
        "signed_payload": request["signed_payload"],
        "attestation_request": request,
        "private_key_material_present": False,
    }


def finalize_publication(
    preparation: Mapping[str, Any],
    *,
    signature_b64: str,
    identity_history: IdentityRegistryHistory,
    repo_root: Path,
    data_root: Path,
    signed_plan: Mapping[str, Any],
) -> Dict[str, Any]:
    expected_fields = {
        "schema_version",
        "publication_authorization",
        "fresh_inventory_sha256",
        "signed_payload",
        "attestation_request",
        "private_key_material_present",
    }
    if (
        set(preparation) != expected_fields
        or preparation.get("schema_version") != PUBLICATION_PREPARATION_SCHEMA
        or preparation.get("private_key_material_present") is not False
    ):
        raise ContractValidationError("publication preparation schema is invalid")
    authorization = preparation.get("publication_authorization")
    request = preparation.get("attestation_request")
    if not isinstance(authorization, Mapping) or not isinstance(request, Mapping):
        raise ContractValidationError("publication preparation is incomplete")
    if (
        request.get("context_artifact") != authorization
        or preparation.get("signed_payload") != request.get("signed_payload")
    ):
        raise ContractValidationError("publication authorization differs from signing request")
    attestation = finalize_attestation_request(
        request, signature_b64=signature_b64, identity_history=identity_history
    )
    result = {
        "schema_version": SIGNED_PUBLICATION_AUTHORIZATION_SCHEMA,
        "authorization": dict(authorization),
        "owner_attestation": attestation,
    }
    inventory = audit_existing(repo_root=repo_root, data_root=data_root)
    verify_signed_publication_authorization(
        result,
        repo_root=repo_root,
        inventory=inventory,
        signed_plan=signed_plan,
        identity_history=identity_history,
    )
    return result


def _open_authenticated_ledger(
    *,
    ledger_path: Path,
    research_root: Path,
    signed_plan: Mapping[str, Any],
    identity_history: IdentityRegistryHistory,
) -> GlobalResearchLedger:
    activation = IdentityActivationEvidence.from_signed_plan(
        signed_plan, identity_history=identity_history
    )
    ledger = GlobalResearchLedger(
        ledger_path,
        research_root=research_root,
        identity_history=identity_history,
        identity_activation=activation,
    )
    records = ledger.store.read_all()
    activation.verify_legacy_records(records)
    ledger.project()
    return ledger


def prepare_projection(
    *,
    ledger_path: Path,
    research_root: Path,
    repo_root: Path,
    signed_plan: Mapping[str, Any],
    identity_history: IdentityRegistryHistory,
    exported_at: datetime,
    exporter_identity_id: str,
    exporter_key_id: str,
) -> Dict[str, Any]:
    ledger = _open_authenticated_ledger(
        ledger_path=ledger_path,
        research_root=research_root,
        signed_plan=signed_plan,
        identity_history=identity_history,
    )
    unsigned = build_unsigned_projection_export(
        ledger, repo_root=repo_root, exported_at=exported_at
    )
    request = prepare_attestation_request(
        identity_history=identity_history,
        identity_id=exporter_identity_id,
        key_id=exporter_key_id,
        role=IdentityRole.LEDGER_EXPORTER,
        attested_at=exported_at,
        context_kind="projection",
        context_artifact=unsigned,
    )
    return {
        "schema_version": PROJECTION_PREPARATION_SCHEMA,
        "unsigned_projection_export": unsigned,
        "signed_payload": request["signed_payload"],
        "attestation_request": request,
        "private_key_material_present": False,
    }


def finalize_projection(
    preparation: Mapping[str, Any],
    *,
    signature_b64: str,
    ledger_path: Path,
    research_root: Path,
    repo_root: Path,
    signed_plan: Mapping[str, Any],
    identity_history: IdentityRegistryHistory,
) -> Dict[str, Any]:
    expected_fields = {
        "schema_version",
        "unsigned_projection_export",
        "signed_payload",
        "attestation_request",
        "private_key_material_present",
    }
    if (
        set(preparation) != expected_fields
        or preparation.get("schema_version") != PROJECTION_PREPARATION_SCHEMA
        or preparation.get("private_key_material_present") is not False
    ):
        raise ContractValidationError("projection preparation schema is invalid")
    unsigned = preparation.get("unsigned_projection_export")
    request = preparation.get("attestation_request")
    if not isinstance(unsigned, Mapping) or not isinstance(request, Mapping):
        raise ContractValidationError("projection preparation is incomplete")
    if (
        request.get("context_artifact") != unsigned
        or preparation.get("signed_payload") != request.get("signed_payload")
    ):
        raise ContractValidationError("unsigned projection differs from signing request")
    ledger = _open_authenticated_ledger(
        ledger_path=ledger_path,
        research_root=research_root,
        signed_plan=signed_plan,
        identity_history=identity_history,
    )
    exported_at = parse_datetime(str(unsigned["exported_at"]))
    rebuilt = build_unsigned_projection_export(
        ledger, repo_root=repo_root, exported_at=exported_at
    )
    if canonical_hash(rebuilt) != canonical_hash(unsigned):
        raise ContractValidationError("projection source changed after signing preparation")
    attestation = finalize_attestation_request(
        request, signature_b64=signature_b64, identity_history=identity_history
    )
    result = build_signed_projection_export(
        ledger,
        repo_root=repo_root,
        attestation=attestation,
        recorded_at=exported_at,
    )
    return result


def verify_projection(
    signed_export: Mapping[str, Any],
    *,
    ledger_path: Path,
    research_root: Path,
    repo_root: Path,
    signed_plan: Mapping[str, Any],
    identity_history: IdentityRegistryHistory,
) -> Dict[str, Any]:
    if signed_export.get("schema_version") != SIGNED_SCHEMA_VERSION:
        raise ContractValidationError("signed projection export schema is invalid")
    attestation = signed_export.get("exporter_attestation")
    if not isinstance(attestation, Mapping):
        raise ContractValidationError("signed projection export lacks exporter attestation")
    ledger = _open_authenticated_ledger(
        ledger_path=ledger_path,
        research_root=research_root,
        signed_plan=signed_plan,
        identity_history=identity_history,
    )
    rebuilt = build_signed_projection_export(
        ledger,
        repo_root=repo_root,
        attestation=attestation,
        recorded_at=parse_datetime(str(signed_export["exported_at"])),
    )
    if canonical_hash(rebuilt) != canonical_hash(signed_export):
        raise ContractValidationError("signed projection differs from current authenticated ledger")
    return {
        "schema_version": "caerus_alpha_lab_projection_verification_v1",
        "projection_export_hash": rebuilt["projection_export_hash"],
        "exporter_signature_verified": True,
        "authenticated_ledger_replayed": True,
    }


def _history_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--identity-history", type=Path, required=True)
    parser.add_argument("--identity-trust-anchor", type=Path, required=True)
    parser.add_argument("--external-pin", required=True)


def _ledger_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--research-root", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--signed-migration-plan", type=Path, required=True)
    _history_arguments(parser)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    domains = parser.add_subparsers(dest="domain", required=True)

    registry = domains.add_parser("registry", help="prepare/finalize public registry releases")
    registry_commands = registry.add_subparsers(dest="action", required=True)
    command = registry_commands.add_parser("prepare", help="emit exact root signing bytes")
    command.add_argument("--directory", type=Path, required=True)
    command.add_argument("--trust-anchor", type=Path, required=True)
    command.add_argument("--released-at", required=True)
    command.add_argument("--previous-history", type=Path)
    command.add_argument("--previous-external-pin")
    command.add_argument("--output-dir", type=Path, required=True)
    command = registry_commands.add_parser("finalize", help="ingest detached root signature and verify")
    command.add_argument("--request", type=Path, required=True)
    command.add_argument("--signature", type=Path, required=True)
    command.add_argument("--trust-anchor", type=Path, required=True)
    command.add_argument("--external-pin", required=True)
    command.add_argument("--previous-history", type=Path)
    command.add_argument("--previous-external-pin")
    command.add_argument("--output-dir", type=Path, required=True)
    command = registry_commands.add_parser("export-history", help="export complete pinned public history")
    command.add_argument("--trust-anchor", type=Path, required=True)
    command.add_argument("--registry", type=Path, action="append", required=True)
    command.add_argument("--external-pin", required=True)
    command.add_argument("--output", type=Path)
    command = registry_commands.add_parser("verify-history", help="verify complete history and external pin")
    command.add_argument("--history", type=Path, required=True)
    command.add_argument("--trust-anchor", type=Path, required=True)
    command.add_argument("--external-pin", required=True)

    attest = domains.add_parser("attestation", help="generic detached ResearchAttestation ceremony")
    attest_commands = attest.add_subparsers(dest="action", required=True)
    command = attest_commands.add_parser("prepare", help="emit exact attestation signing bytes")
    _history_arguments(command)
    command.add_argument("--identity-id", required=True)
    command.add_argument("--key-id", required=True)
    command.add_argument("--role", choices=[item.value for item in IdentityRole], required=True)
    command.add_argument("--attested-at", required=True)
    context = command.add_mutually_exclusive_group(required=True)
    context.add_argument("--event-draft", type=Path)
    context.add_argument("--generic", action="store_true")
    command.add_argument("--artifact-sha256")
    command.add_argument("--ledger-head")
    command.add_argument("--context-sha256")
    command.add_argument("--recorded-at")
    command.add_argument("--output-dir", type=Path, required=True)
    command = attest_commands.add_parser("finalize", help="ingest and verify detached attestation signature")
    _history_arguments(command)
    command.add_argument("--request", type=Path, required=True)
    command.add_argument("--signature", type=Path, required=True)
    command.add_argument("--output", type=Path)
    command = attest_commands.add_parser("verify", help="re-verify finalized attestation")
    _history_arguments(command)
    command.add_argument("--request", type=Path, required=True)
    command.add_argument("--attestation", type=Path, required=True)

    migration = domains.add_parser("migration", help="prepare/finalize/verify owner migration plan")
    migration_commands = migration.add_subparsers(dest="action", required=True)
    command = migration_commands.add_parser("definition", help="generate and validate the complete owner migration definition")
    command.add_argument("--repo-root", type=Path, required=True)
    command.add_argument("--data-root", type=Path, required=True)
    command.add_argument("--owner-packet", type=Path, required=True)
    command.add_argument("--recorded-at", required=True)
    command.add_argument("--output", type=Path)
    command = migration_commands.add_parser("prepare", help="fresh-audit and emit deterministic migration plan")
    command.add_argument("--repo-root", type=Path, required=True)
    command.add_argument("--data-root", type=Path, required=True)
    command.add_argument("--owner-packet", type=Path, required=True)
    command.add_argument("--recorded-at", required=True)
    _history_arguments(command)
    command.add_argument("--owner-identity-id", required=True)
    command.add_argument("--owner-key-id", required=True)
    command.add_argument("--output-dir", type=Path, required=True)
    command = migration_commands.add_parser("finalize", help="ingest owner signature and verify signed plan")
    _history_arguments(command)
    command.add_argument("--preparation", type=Path, required=True)
    command.add_argument("--signature", type=Path, required=True)
    command.add_argument("--output", type=Path)
    command = migration_commands.add_parser("verify", help="verify signed plan and optionally fresh receipts")
    _history_arguments(command)
    command.add_argument("--signed-plan", type=Path, required=True)
    command.add_argument("--repo-root", type=Path)
    command.add_argument("--data-root", type=Path)
    command = migration_commands.add_parser("identity-bundle", help="compose the exact authenticated control-plane loader bundle")
    _history_arguments(command)
    command.add_argument("--signed-plan", type=Path, required=True)
    command.add_argument("--output", type=Path)

    publication = domains.add_parser("publication", help="separate QS-003 create-only authorization")
    publication_commands = publication.add_subparsers(dest="action", required=True)
    command = publication_commands.add_parser("prepare", help="emit exact owner QS-003 signing bytes")
    command.add_argument("--repo-root", type=Path, required=True)
    command.add_argument("--data-root", type=Path, required=True)
    command.add_argument("--signed-plan", type=Path, required=True)
    command.add_argument("--authorized-at", required=True)
    _history_arguments(command)
    command.add_argument("--owner-identity-id", required=True)
    command.add_argument("--owner-key-id", required=True)
    command.add_argument("--output-dir", type=Path, required=True)
    command = publication_commands.add_parser("finalize", help="ingest and verify owner QS-003 signature")
    command.add_argument("--repo-root", type=Path, required=True)
    command.add_argument("--data-root", type=Path, required=True)
    command.add_argument("--signed-plan", type=Path, required=True)
    _history_arguments(command)
    command.add_argument("--preparation", type=Path, required=True)
    command.add_argument("--signature", type=Path, required=True)
    command.add_argument("--output", type=Path)
    command = publication_commands.add_parser("verify", help="re-verify QS-003 against fresh receipts")
    command.add_argument("--repo-root", type=Path, required=True)
    command.add_argument("--data-root", type=Path, required=True)
    command.add_argument("--signed-plan", type=Path, required=True)
    command.add_argument("--authorization", type=Path, required=True)
    _history_arguments(command)
    command = publication_commands.add_parser("publish", help="dry-run by default; --write requires both owner signatures")
    command.add_argument("--repo-root", type=Path, required=True)
    command.add_argument("--data-root", type=Path, required=True)
    command.add_argument("--signed-plan", type=Path, required=True)
    command.add_argument("--authorization", type=Path, required=True)
    _history_arguments(command)
    command.add_argument("--write", action="store_true")

    projection = domains.add_parser("projection", help="prepare/finalize/verify signed projections")
    projection_commands = projection.add_subparsers(dest="action", required=True)
    command = projection_commands.add_parser("prepare", help="snapshot authenticated ledger and emit exporter bytes")
    _ledger_arguments(command)
    command.add_argument("--exported-at", required=True)
    command.add_argument("--exporter-identity-id", required=True)
    command.add_argument("--exporter-key-id", required=True)
    command.add_argument("--output-dir", type=Path, required=True)
    command = projection_commands.add_parser("finalize", help="reopen ledger, ingest signature, and emit signed export")
    _ledger_arguments(command)
    command.add_argument("--preparation", type=Path, required=True)
    command.add_argument("--signature", type=Path, required=True)
    command.add_argument("--output", type=Path)
    command = projection_commands.add_parser("verify", help="replay ledger and verify final signed export")
    _ledger_arguments(command)
    command.add_argument("--signed-export", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.domain == "registry":
        if args.action == "prepare":
            previous = None
            if args.previous_history is not None:
                if args.previous_external_pin is None:
                    raise ContractValidationError("previous history requires its separately supplied pin")
                previous = load_registry_history(
                    args.previous_history,
                    external_pin=args.previous_external_pin,
                    trust_anchor_path=args.trust_anchor,
                )
            request = prepare_registry_release(
                directory=_strict_object(args.directory, reject_private=True),
                trust_anchor=_strict_object(args.trust_anchor, reject_private=True),
                released_at=parse_datetime(args.released_at),
                previous_history=previous,
            )
            _emit_prepare_bundle(
                output_dir=args.output_dir,
                artifact_name="registry_directory.json",
                artifact=request["directory"],
                review_manifest=request,
            )
            return 0
        if args.action == "finalize":
            previous = None
            if args.previous_history is not None:
                if args.previous_external_pin is None:
                    raise ContractValidationError("previous history requires its separately supplied pin")
                previous = load_registry_history(
                    args.previous_history,
                    external_pin=args.previous_external_pin,
                    trust_anchor_path=args.trust_anchor,
                )
            result = finalize_registry_release(
                _strict_object(args.request, reject_private=True),
                signature_b64=_detached_signature_b64(args.signature),
                external_pin=args.external_pin,
                trusted_anchor=_strict_object(args.trust_anchor, reject_private=True),
                previous_history=previous,
            )
            destination = args.output_dir.expanduser().resolve()
            destination.mkdir(parents=True, exist_ok=False, mode=0o700)
            try:
                _write_create_only(destination / "signed_registry.json", _canonical_bytes(result["signed_registry"]))
                _write_create_only(destination / "identity_registry_history.json", _canonical_bytes(result["registry_history"]))
                _write_create_only(destination / "finalization_receipt.json", _canonical_bytes(result))
            except Exception:
                shutil.rmtree(destination)
                raise
            return 0
        if args.action == "export-history":
            result = export_registry_history(
                trust_anchor=_strict_object(args.trust_anchor, reject_private=True),
                registries=[_strict_object(path, reject_private=True) for path in args.registry],
                external_pin=args.external_pin,
            )
            _emit_json(result, args.output)
            return 0
        history = load_registry_history(
            args.history,
            external_pin=args.external_pin,
            trust_anchor_path=args.trust_anchor,
        )
        _emit_json(
            {
                "schema_version": "caerus_alpha_lab_identity_registry_history_verification_v1",
                "registry_history_sha256": canonical_hash(history.to_dict()),
                "active_registry_hash": history.active_registry_hash,
                "external_pin_verified": True,
            },
            None,
        )
        return 0

    if args.domain == "attestation":
        history = load_registry_history(
            args.identity_history,
            external_pin=args.external_pin,
            trust_anchor_path=args.identity_trust_anchor,
        )
        if args.action == "prepare":
            artifact = _strict_object(args.event_draft) if args.event_draft else None
            request = prepare_attestation_request(
                identity_history=history,
                identity_id=args.identity_id,
                key_id=args.key_id,
                role=IdentityRole(args.role),
                attested_at=parse_datetime(args.attested_at),
                context_kind="event" if artifact is not None else "generic",
                context_artifact=artifact,
                artifact_sha256=args.artifact_sha256,
                ledger_head_hash=args.ledger_head,
                context_sha256=args.context_sha256,
                recorded_at=parse_datetime(args.recorded_at) if args.recorded_at else None,
            )
            _emit_prepare_bundle(
                output_dir=args.output_dir,
                artifact_name="attestation_context.json",
                artifact=artifact or {"schema_version": "caerus_alpha_lab_generic_attestation_context_v1", "artifact_sha256": args.artifact_sha256, "ledger_head_hash": args.ledger_head, "context_sha256": args.context_sha256, "recorded_at": args.recorded_at},
                review_manifest=request,
            )
            return 0
        request = _strict_object(args.request, reject_private=True)
        if args.action == "finalize":
            result = finalize_attestation_request(
                request,
                signature_b64=_detached_signature_b64(args.signature),
                identity_history=history,
            )
            _emit_json(result, args.output)
            return 0
        result = verify_attestation_result(
            request,
            _strict_object(args.attestation, reject_private=True),
            identity_history=history,
        )
        _emit_json(
            {"schema_version": "caerus_alpha_lab_attestation_verification_v1", "attestation_sha256": canonical_hash(result), "signature_verified": True},
            None,
        )
        return 0

    if args.domain == "migration" and args.action == "definition":
        inventory = audit_existing(repo_root=args.repo_root, data_root=args.data_root)
        _validate_canonical_census(inventory)
        definition = migration_definition_from_owner_packet(
            _strict_object(args.owner_packet),
            recorded_at=parse_datetime(args.recorded_at),
            inventory=inventory,
        )
        _emit_json(definition, args.output)
        return 0

    history = load_registry_history(
        args.identity_history,
        external_pin=args.external_pin,
        trust_anchor_path=args.identity_trust_anchor,
    )
    if args.domain == "migration":
        if args.action == "identity-bundle":
            result = build_control_plane_identity_bundle(
                identity_history=history,
                signed_plan=_strict_object(args.signed_plan, reject_private=True),
            )
            _emit_json(result, args.output)
            return 0
        if args.action == "prepare":
            result = prepare_migration(
                repo_root=args.repo_root,
                data_root=args.data_root,
                owner_packet=_strict_object(args.owner_packet),
                recorded_at=parse_datetime(args.recorded_at),
                identity_history=history,
                owner_identity_id=args.owner_identity_id,
                owner_key_id=args.owner_key_id,
            )
            _emit_prepare_bundle(
                output_dir=args.output_dir,
                artifact_name="migration_event_plan.json",
                artifact=result["migration_event_plan"],
                review_manifest={**result["attestation_request"], "preparation": result},
            )
            return 0
        if args.action == "finalize":
            raw = _strict_object(args.preparation, reject_private=True)
            preparation = _preparation_from_review_manifest(
                raw, expected_schema=MIGRATION_PREPARATION_SCHEMA
            )
            result = finalize_migration(
                preparation,
                signature_b64=_detached_signature_b64(args.signature),
                identity_history=history,
            )
            _emit_json(result, args.output)
            return 0
        result = verify_migration(
            _strict_object(args.signed_plan, reject_private=True),
            identity_history=history,
            repo_root=args.repo_root,
            data_root=args.data_root,
        )
        _emit_json(result, None)
        return 0

    if args.domain == "publication":
        signed_plan = _strict_object(args.signed_plan, reject_private=True)
        if args.action == "prepare":
            result = prepare_publication(
                repo_root=args.repo_root,
                data_root=args.data_root,
                signed_plan=signed_plan,
                authorized_at=parse_datetime(args.authorized_at),
                identity_history=history,
                owner_identity_id=args.owner_identity_id,
                owner_key_id=args.owner_key_id,
            )
            _emit_prepare_bundle(
                output_dir=args.output_dir,
                artifact_name="publication_authorization.json",
                artifact=result["publication_authorization"],
                review_manifest={**result["attestation_request"], "preparation": result},
            )
            return 0
        authorization = _strict_object(args.authorization, reject_private=True) if hasattr(args, "authorization") else None
        if args.action == "finalize":
            raw = _strict_object(args.preparation, reject_private=True)
            preparation = _preparation_from_review_manifest(
                raw, expected_schema=PUBLICATION_PREPARATION_SCHEMA
            )
            result = finalize_publication(
                preparation,
                signature_b64=_detached_signature_b64(args.signature),
                identity_history=history,
                repo_root=args.repo_root,
                data_root=args.data_root,
                signed_plan=signed_plan,
            )
            _emit_json(result, args.output)
            return 0
        inventory = audit_existing(repo_root=args.repo_root, data_root=args.data_root)
        assert authorization is not None
        verified = verify_signed_publication_authorization(
            authorization,
            repo_root=args.repo_root,
            inventory=inventory,
            signed_plan=signed_plan,
            identity_history=history,
        )
        if args.action == "verify":
            _emit_json(
                {"schema_version": "caerus_alpha_lab_publication_authorization_verification_v1", "authorization_sha256": canonical_hash(verified), "owner_signature_verified": True, "fresh_receipts_verified": True},
                None,
            )
            return 0
        if not args.write:
            _emit_json(
                {"schema_version": "caerus_alpha_lab_publication_dry_run_v1", "authorization_sha256": canonical_hash(verified), "would_publish": True, "write_performed": False},
                None,
            )
            return 0
        result = publish_signed_migration_plan(
            repo_root=args.repo_root,
            data_root=args.data_root,
            inventory=inventory,
            signed_plan=signed_plan,
            publication_authorization=authorization,
            identity_history=history,
        )
        _emit_json(result, None)
        return 0

    signed_plan = _strict_object(args.signed_migration_plan, reject_private=True)
    if args.domain == "projection":
        if args.action == "prepare":
            result = prepare_projection(
                ledger_path=args.ledger,
                research_root=args.research_root,
                repo_root=args.repo_root,
                signed_plan=signed_plan,
                identity_history=history,
                exported_at=parse_datetime(args.exported_at),
                exporter_identity_id=args.exporter_identity_id,
                exporter_key_id=args.exporter_key_id,
            )
            _emit_prepare_bundle(
                output_dir=args.output_dir,
                artifact_name="unsigned_projection_export.json",
                artifact=result["unsigned_projection_export"],
                review_manifest={**result["attestation_request"], "preparation": result},
            )
            return 0
        if args.action == "finalize":
            raw = _strict_object(args.preparation, reject_private=True)
            preparation = _preparation_from_review_manifest(
                raw, expected_schema=PROJECTION_PREPARATION_SCHEMA
            )
            result = finalize_projection(
                preparation,
                signature_b64=_detached_signature_b64(args.signature),
                ledger_path=args.ledger,
                research_root=args.research_root,
                repo_root=args.repo_root,
                signed_plan=signed_plan,
                identity_history=history,
            )
            _emit_json(result, args.output)
            return 0
        result = verify_projection(
            _strict_object(args.signed_export, reject_private=True),
            ledger_path=args.ledger,
            research_root=args.research_root,
            repo_root=args.repo_root,
            signed_plan=signed_plan,
            identity_history=history,
        )
        _emit_json(result, None)
        return 0
    raise ContractValidationError("unsupported ceremony command")


if __name__ == "__main__":
    raise SystemExit(main())

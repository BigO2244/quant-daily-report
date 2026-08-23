"""Signed, research-only projection exports for the Caerus control boundary.

The exporter verifies a detached public-key attestation supplied by an external
signer.  It never accepts, creates, loads, or persists a private key.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Mapping

from .canonical import (
    canonical_hash,
    canonical_json,
    format_datetime,
    parse_datetime,
    require_sha256,
)
from .contracts import _require_aware
from .errors import ContractValidationError, EventStoreIntegrityError
from .research_identity import (
    GENESIS_LEDGER_HEAD,
    IdentityRegistryHistory,
    IdentityRole,
    ResearchAttestation,
)
from .research_ledger import GlobalResearchLedger


UNSIGNED_SCHEMA_VERSION = "caerus_alpha_lab_unsigned_projection_export_v1"
SIGNED_SCHEMA_VERSION = "caerus_alpha_lab_signed_projection_export_v1"
UNSIGNED_CLASSIFICATION = "LINEAGE_ONLY_NON_DECISION_GRADE"
SIGNED_CLASSIFICATION = "SIGNED_RESEARCH_ONLY_NON_EXECUTIONAL"


def _normalized_repo_relative(path: Path, repo_root: Path) -> str:
    root = Path(repo_root).expanduser().resolve()
    if not root.is_dir():
        raise ContractValidationError("projection export repo_root must be an existing directory")
    try:
        relative = Path(path).expanduser().resolve().relative_to(root)
    except ValueError as exc:
        raise ContractValidationError(
            "canonical_event_store must be inside the supplied repository root"
        ) from exc
    if not relative.parts or relative == Path(".") or ".." in relative.parts:
        raise ContractValidationError("canonical_event_store must be a normalized repo-relative file path")
    return relative.as_posix()


def _active_registry_context(history: IdentityRegistryHistory) -> Dict[str, Any]:
    registry = history.resolve(history.active_registry_hash)
    if registry.release is None or registry.trust_anchor is None:
        raise ContractValidationError("projection export requires an anchored active identity registry")
    return {
        "registry_id": registry.registry_id,
        "registry_hash": registry.registry_hash,
        "active_registry_release": {
            **registry.release.signed_payload(),
            "signature_b64": registry.release.signature_b64,
            "release_hash": registry.release.release_hash,
        },
        "externally_pinned_registry_hash": history.externally_pinned_registry_hash,
        "trust_anchor": {
            "schema_version": registry.trust_anchor.schema_version,
            "anchor_id": registry.trust_anchor.anchor_id,
            "root_key_id": registry.trust_anchor.root_key_id,
            "root_public_key_pem": registry.trust_anchor.root_public_key_pem,
            "expected_registry_id": registry.trust_anchor.expected_registry_id,
        },
    }


def _source_receipt(ledger: GlobalResearchLedger, *, repo_root: Path) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Replay the ledger before deriving a receipt over its exact on-disk bytes."""

    path = ledger.store.path
    # Keep the replay, records, and bytes inside one shared OS-lock interval.
    # Appenders take an exclusive lock, so they cannot splice a new event into
    # this receipt between semantic replay and the byte-level digest.
    with ledger.store.shared_snapshot_lock(require_existing_file=True) as stream:
        projection = ledger.project()
        records = ledger.store.read_all()
        stream.seek(0)
        raw = stream.read()
    head = records[-1].event_hash if records else None
    if projection.get("event_count") != len(records) or projection.get("event_chain_head") != head:
        raise EventStoreIntegrityError("ledger projection disagrees with its replayed event-chain receipt")
    head_by_event_count: Dict[str, Any] = {"0": None}
    for count, record in enumerate(records, start=1):
        head_by_event_count[str(count)] = record.event_hash
    if head_by_event_count[str(len(records))] != head:
        raise EventStoreIntegrityError("source receipt ancestry does not terminate at the replayed head")
    return projection, {
        "schema_version": "caerus_alpha_lab_source_ledger_receipt_v1",
        "canonical_event_store": _normalized_repo_relative(path, repo_root),
        "ledger_bytes_sha256": hashlib.sha256(raw).hexdigest(),
        "event_count": len(records),
        "event_chain_head": head,
        "head_by_event_count": head_by_event_count,
        "latest_event_recorded_at": (
            format_datetime(max(record.recorded_at for record in records)) if records else None
        ),
        "event_chain_replay_verified": True,
        "typed_semantic_replay_verified": True,
        "identity_activation_head_hash": ledger.identity_activation_head_hash,
        # No legacy event is upgraded by this export.  With no activation
        # evidence, the complete ledger remains non-decision-grade.
        "legacy_prefix_nondecision_grade": True,
    }


def build_unsigned_projection_export(
    ledger: GlobalResearchLedger, *, repo_root: Path, exported_at: datetime
) -> Dict[str, Any]:
    """Build a deterministic, explicitly non-decision-grade export context."""

    _require_aware(exported_at, "exported_at")
    if ledger.identity_history is None:
        raise ContractValidationError(
            "projection export requires externally pinned public identity registry history"
        )
    registry_context = _active_registry_context(ledger.identity_history)
    release_at = parse_datetime(
        str(registry_context["active_registry_release"]["released_at"])
    )
    if exported_at < release_at:
        raise ContractValidationError(
            "projection export predates the active identity registry release"
        )
    projection, receipt = _source_receipt(ledger, repo_root=repo_root)
    projection = dict(projection)
    projection["canonical_event_store"] = receipt["canonical_event_store"]
    result = {
        "schema_version": UNSIGNED_SCHEMA_VERSION,
        "classification": UNSIGNED_CLASSIFICATION,
        "exported_at": format_datetime(exported_at),
        "canonical_event_store": receipt["canonical_event_store"],
        "source_ledger_receipt": receipt,
        "projection": projection,
        "projection_sha256": canonical_hash(projection),
        "active_identity_registry": registry_context,
    }
    # Exercise the strict Alpha serializer before a detached signer sees it.
    canonical_json(result)
    return result


def projection_export_attestation_context_hash(
    unsigned_export: Mapping[str, Any]
) -> str:
    """Hash the complete unsigned export, not only its projection payload."""

    if unsigned_export.get("schema_version") != UNSIGNED_SCHEMA_VERSION:
        raise ContractValidationError("projection export attestation requires the canonical unsigned schema")
    if unsigned_export.get("classification") != UNSIGNED_CLASSIFICATION:
        raise ContractValidationError("unsigned projection export must remain lineage-only non-decision-grade")
    try:
        parse_datetime(str(unsigned_export["exported_at"]))
    except (KeyError, TypeError, ValueError, ContractValidationError) as exc:
        raise ContractValidationError("unsigned projection export requires exported_at") from exc
    if not isinstance(unsigned_export.get("active_identity_registry"), Mapping):
        raise ContractValidationError(
            "unsigned projection export must bind active identity registry context"
        )
    return canonical_hash(
        {
            "schema_version": "caerus_alpha_lab_projection_export_attestation_context_v1",
            "unsigned_export": dict(unsigned_export),
        }
    )


def build_signed_projection_export(
    ledger: GlobalResearchLedger,
    *,
    repo_root: Path,
    attestation: ResearchAttestation | Mapping[str, Any],
    recorded_at: datetime,
) -> Dict[str, Any]:
    """Verify one LEDGER_EXPORTER attestation and emit a signed projection.

    ``attestation`` is detached evidence produced by an approved external
    signer.  This function only performs public-key verification.
    """

    if ledger.identity_history is None:
        raise ContractValidationError("signed projection export requires an enrolled identity registry history")
    history = ledger.identity_history
    _require_aware(recorded_at, "recorded_at")
    unsigned_export = build_unsigned_projection_export(
        ledger, repo_root=repo_root, exported_at=recorded_at
    )
    parsed = (
        attestation
        if isinstance(attestation, ResearchAttestation)
        else ResearchAttestation.from_dict(attestation)
    )
    receipt = unsigned_export["source_ledger_receipt"]
    if parsed.attested_at != recorded_at:
        raise ContractValidationError(
            "signed projection recorded_at must exactly equal the exporter attestation time"
        )
    latest_event_recorded_at = receipt["latest_event_recorded_at"]
    if latest_event_recorded_at is not None and recorded_at < parse_datetime(
        str(latest_event_recorded_at)
    ):
        raise ContractValidationError(
            "signed projection cannot predate its source ledger receipt"
        )
    active_release_at = parse_datetime(
        str(
            unsigned_export["active_identity_registry"]["active_registry_release"][
                "released_at"
            ]
        )
    )
    if recorded_at < active_release_at:
        raise ContractValidationError(
            "signed projection predates the active identity registry release"
        )
    ledger_head = receipt["event_chain_head"] or GENESIS_LEDGER_HEAD
    artifact_sha256 = canonical_hash(unsigned_export)
    context_sha256 = projection_export_attestation_context_hash(unsigned_export)
    history.verify(
        parsed,
        expected_role=IdentityRole.LEDGER_EXPORTER,
        artifact_sha256=artifact_sha256,
        ledger_head_hash=ledger_head,
        context_sha256=context_sha256,
        recorded_at=recorded_at,
        for_new_event=True,
    )
    require_sha256(artifact_sha256, "unsigned_export_sha256")
    exporter_identity = {
        "identity_id": parsed.identity_id,
        "key_id": parsed.key_id,
        "subject_id": history.subject_id_for(
            identity_id=parsed.identity_id,
            key_id=parsed.key_id,
            registry_hash=parsed.registry_hash,
        ),
        "role": parsed.role.value,
        "registry_hash": parsed.registry_hash,
    }
    result: Dict[str, Any] = {
        "schema_version": SIGNED_SCHEMA_VERSION,
        "classification": SIGNED_CLASSIFICATION,
        "exported_at": unsigned_export["exported_at"],
        "canonical_event_store": unsigned_export["canonical_event_store"],
        "source_ledger_receipt": receipt,
        "projection": unsigned_export["projection"],
        "projection_sha256": unsigned_export["projection_sha256"],
        "unsigned_export_sha256": artifact_sha256,
        "exporter_identity": exporter_identity,
        "active_identity_registry": unsigned_export["active_identity_registry"],
        "exporter_attestation": parsed.to_dict(),
    }
    result["projection_export_hash"] = canonical_hash(result)
    canonical_json(result)
    return result

"""Authenticated canonical-ledger loading for control-plane entry points."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from projects.alpha_lab.factory.canonical import canonical_json
from projects.alpha_lab.factory.errors import ContractValidationError
from projects.alpha_lab.factory.research_identity import (
    IdentityActivationEvidence,
    IdentityRegistry,
    IdentityRegistryHistory,
    IdentityTrustAnchor,
    ResearchAttestation,
)
from projects.alpha_lab.factory.research_ledger import GlobalResearchLedger


IDENTITY_BUNDLE_SCHEMA = "caerus_alpha_lab_control_plane_identity_bundle_v1"
EVENT_ATTESTATION_SCHEMA = "caerus_alpha_lab_control_plane_event_attestation_v1"


def _strict_load_json_object_text(*, text: str, source: str) -> Dict[str, Any]:
    def reject_duplicates(pairs: Sequence[tuple[str, Any]]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ContractValidationError(
                    "{} contains duplicate JSON key: {}".format(source, key)
                )
            result[key] = item
        return result

    def reject_constant(value: str) -> None:
        raise ContractValidationError(
            "{} contains non-finite JSON number: {}".format(source, value)
        )

    try:
        value = json.loads(
            text,
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise ContractValidationError("{} is not strict JSON".format(source)) from exc
    if not isinstance(value, dict):
        raise ContractValidationError("{} must contain a JSON object".format(source))
    canonical_json(value)
    return value


def strict_load_json_object(path: Path) -> Dict[str, Any]:
    """Load strict JSON object material, rejecting duplicate keys and NaN/Inf."""

    path = path.expanduser().resolve()
    return _strict_load_json_object_text(
        text=path.read_text(encoding="utf-8"),
        source=str(path),
    )


def strict_load_json_object_bytes(value: bytes, *, source: str) -> Dict[str, Any]:
    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractValidationError("{} is not UTF-8 JSON".format(source)) from exc
    return _strict_load_json_object_text(text=text, source=source)


def _reject_private_material(value: Any, *, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in ("private", "secret", "token")):
                raise ContractValidationError(
                    "identity material must not contain private or secret fields"
                )
            _reject_private_material(item, path="{}.{}".format(path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_private_material(item, path="{}[{}]".format(path, index))
    elif isinstance(value, str) and "PRIVATE KEY" in value:
        raise ContractValidationError(
            "identity material must not contain private key material"
        )


def load_identity_bundle(
    *, bundle_path: Path, external_registry_pin: str
) -> tuple[IdentityRegistryHistory, IdentityActivationEvidence]:
    """Load public registry history and verified signed activation evidence."""

    raw = strict_load_json_object(bundle_path)
    _reject_private_material(raw)
    expected_keys = {
        "schema_version",
        "identity_trust_anchor",
        "identity_registries",
        "signed_migration_plan",
    }
    if set(raw) != expected_keys or raw.get("schema_version") != IDENTITY_BUNDLE_SCHEMA:
        raise ContractValidationError("identity bundle schema is invalid")
    anchor_raw = raw["identity_trust_anchor"]
    if not isinstance(anchor_raw, Mapping):
        raise ContractValidationError("identity bundle trust anchor is invalid")
    anchor = IdentityTrustAnchor(
        anchor_id=str(anchor_raw["anchor_id"]),
        root_key_id=str(anchor_raw["root_key_id"]),
        root_public_key_pem=str(anchor_raw["root_public_key_pem"]),
        expected_registry_id=str(anchor_raw["expected_registry_id"]),
        schema_version=str(
            anchor_raw.get(
                "schema_version", "caerus_alpha_lab_identity_trust_anchor_v1"
            )
        ),
    )
    registry_payloads = raw["identity_registries"]
    if not isinstance(registry_payloads, list) or not registry_payloads:
        raise ContractValidationError("identity bundle requires registry history")
    registries = tuple(
        IdentityRegistry.from_dict(item, trust_anchor=anchor)
        for item in registry_payloads
    )
    history = IdentityRegistryHistory(
        registries=registries,
        active_registry_hash=external_registry_pin,
        externally_pinned_registry_hash=external_registry_pin,
    )
    signed_plan = raw["signed_migration_plan"]
    if not isinstance(signed_plan, Mapping):
        raise ContractValidationError("identity bundle signed activation plan is invalid")
    activation = IdentityActivationEvidence.from_signed_plan(
        signed_plan, identity_history=history
    )
    plan = activation.plan
    if (
        plan.get("active_registry_hash") != history.active_registry_hash
        or plan.get("externally_pinned_registry_hash") != external_registry_pin
    ):
        raise ContractValidationError(
            "signed activation plan does not match the externally pinned registry"
        )
    return history, activation


def open_authenticated_global_ledger(
    *,
    ledger_path: Path,
    research_root: Path,
    identity_bundle: Optional[Path],
    identity_registry_pin: Optional[str],
) -> GlobalResearchLedger:
    """Open a canonical ledger only with pinned registry and signed activation."""

    if identity_bundle is None or identity_registry_pin is None:
        raise ContractValidationError(
            "canonical research ledger requires --identity-bundle and "
            "--identity-registry-pin"
        )
    history, activation = load_identity_bundle(
        bundle_path=identity_bundle, external_registry_pin=identity_registry_pin
    )
    ledger = GlobalResearchLedger(
        ledger_path,
        research_root=research_root,
        identity_history=history,
        identity_activation=activation,
    )
    records = ledger.store.read_all()
    activation.verify_legacy_records(records)
    ledger.project()
    return ledger


def load_event_attestations(paths: Sequence[Path]) -> Dict[str, Mapping[str, Any]]:
    """Load detached per-event attestations keyed by exact ledger event_id."""

    attestations: Dict[str, Mapping[str, Any]] = {}
    for path in paths:
        raw = strict_load_json_object(path)
        _reject_private_material(raw)
        expected_keys = {"schema_version", "event_id", "event_attestation"}
        if set(raw) != expected_keys or raw.get("schema_version") != EVENT_ATTESTATION_SCHEMA:
            raise ContractValidationError("event attestation wrapper schema is invalid")
        event_id = str(raw["event_id"])
        if event_id in attestations:
            raise ContractValidationError(
                "duplicate event attestation wrapper for {}".format(event_id)
            )
        event_attestation = raw["event_attestation"]
        if not isinstance(event_attestation, Mapping):
            raise ContractValidationError("event attestation payload is invalid")
        ResearchAttestation.from_dict(event_attestation)
        attestations[event_id] = dict(event_attestation)
    return attestations


def ledger_requires_event_attestation(ledger: GlobalResearchLedger) -> bool:
    return getattr(ledger, "identity_history", None) is not None


def require_event_attestation(
    attestations: Mapping[str, Mapping[str, Any]],
    event_id: str,
    *,
    ledger: GlobalResearchLedger,
) -> Optional[Mapping[str, Any]]:
    if not ledger_requires_event_attestation(ledger):
        return None
    try:
        return attestations[event_id]
    except KeyError as exc:
        raise ContractValidationError(
            "missing detached event attestation for {}".format(event_id)
        ) from exc

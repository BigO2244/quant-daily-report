"""Fail-closed public-key identity attestations for research governance.

Only public identity records and detached signatures belong in research
artifacts.  Private keys are deliberately outside this repository and must be
held in the approved signer or hardware-backed credential service.
"""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Sequence, TYPE_CHECKING

from .canonical import canonical_hash, canonical_json, format_datetime, parse_datetime, require_non_empty, require_sha256
from .contracts import _require_aware
from .errors import ContractValidationError

if TYPE_CHECKING:
    from .store import EventRecord

try:  # Fail closed when the approved asymmetric-verification dependency is absent.
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
except ImportError:  # pragma: no cover - exercised in minimal deployment environments.
    InvalidSignature = Exception  # type: ignore[assignment,misc]
    serialization = None  # type: ignore[assignment]
    Ed25519PublicKey = None  # type: ignore[assignment,misc]


_IDENTITY_ID = re.compile(r"^[a-z][a-z0-9._-]{2,127}$")
_KEY_ID = re.compile(r"^[a-z][a-z0-9._-]{2,127}$")
GENESIS_LEDGER_HEAD = "GENESIS"
MAX_ATTESTATION_AGE = timedelta(hours=24)


class IdentityRole(str, Enum):
    OWNER_RATIFIER = "OWNER_RATIFIER"
    PREREGISTRATION_AUTHOR = "PREREGISTRATION_AUTHOR"
    DATA_CERTIFIER = "DATA_CERTIFIER"
    INDEPENDENT_REVIEWER = "INDEPENDENT_REVIEWER"
    LEDGER_EXPORTER = "LEDGER_EXPORTER"


class IdentityStatus(str, Enum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    RETIRED = "RETIRED"


@dataclass(frozen=True)
class IdentityTrustAnchor:
    """Out-of-band root verifier for immutable registry-release attestations."""

    anchor_id: str
    root_key_id: str
    root_public_key_pem: str
    expected_registry_id: str
    schema_version: str = "caerus_alpha_lab_identity_trust_anchor_v1"

    def __post_init__(self) -> None:
        _require_identifier(self.anchor_id, "anchor_id", _IDENTITY_ID)
        _require_identifier(self.root_key_id, "root_key_id", _KEY_ID)
        _require_identifier(self.expected_registry_id, "expected_registry_id", _IDENTITY_ID)
        if "PRIVATE KEY" in self.root_public_key_pem:
            raise ContractValidationError("trust anchor must contain a public key only")
        _require_crypto()
        try:
            public_key = serialization.load_pem_public_key(self.root_public_key_pem.encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise ContractValidationError("trust-anchor public key PEM is invalid") from exc
        if not isinstance(public_key, Ed25519PublicKey):
            raise ContractValidationError("trust-anchor key must use Ed25519")


@dataclass(frozen=True)
class RegistryRelease:
    """A root-signed versioned release of one exact public identity directory."""

    registry_id: str
    registry_hash: str
    version: int
    released_at: datetime
    root_key_id: str
    signature_b64: str
    previous_registry_hash: Optional[str] = None
    previous_release_hash: Optional[str] = None
    schema_version: str = "caerus_alpha_lab_identity_registry_release_v2"

    def __post_init__(self) -> None:
        _require_identifier(self.registry_id, "registry_id", _IDENTITY_ID)
        require_sha256(self.registry_hash, "registry_hash")
        if not isinstance(self.version, int) or self.version < 1:
            raise ContractValidationError("registry release version must be positive")
        _require_aware(self.released_at, "released_at")
        _require_identifier(self.root_key_id, "root_key_id", _KEY_ID)
        if self.version == 1:
            if self.previous_registry_hash is not None or self.previous_release_hash is not None:
                raise ContractValidationError(
                    "genesis registry release cannot declare a predecessor"
                )
        else:
            if self.previous_registry_hash is None or self.previous_release_hash is None:
                raise ContractValidationError(
                    "non-genesis registry release requires both predecessor hashes"
                )
            require_sha256(self.previous_registry_hash, "previous_registry_hash")
            require_sha256(self.previous_release_hash, "previous_release_hash")
        try:
            if len(base64.b64decode(self.signature_b64, validate=True)) != 64:
                raise ValueError("wrong length")
        except (TypeError, ValueError) as exc:
            raise ContractValidationError("registry release signature is invalid") from exc

    def signed_payload(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "registry_id": self.registry_id,
            "registry_hash": self.registry_hash,
            "version": self.version,
            "released_at": format_datetime(self.released_at),
            "root_key_id": self.root_key_id,
            "previous_registry_hash": self.previous_registry_hash,
            "previous_release_hash": self.previous_release_hash,
        }

    @property
    def release_hash(self) -> str:
        """Stable identity for this signed release, including its signature."""

        return canonical_hash(
            {**self.signed_payload(), "signature_b64": self.signature_b64}
        )


def _require_identifier(value: str, field_name: str, pattern: re.Pattern[str]) -> None:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ContractValidationError("{} is not a path-safe identity identifier".format(field_name))


def _require_ledger_head(value: str) -> None:
    if value == GENESIS_LEDGER_HEAD:
        return
    require_sha256(value, "ledger_head_hash")


def _require_crypto() -> None:
    if Ed25519PublicKey is None or serialization is None:
        raise ContractValidationError(
            "Ed25519 verification support is unavailable; authenticated identity fails closed"
        )


def typed_event_payload_hash(event_type: str, payload: Mapping[str, Any]) -> str:
    """Hash one typed payload before any detached attestation is attached."""

    require_non_empty(event_type, "event_type")
    if not isinstance(payload, Mapping):
        raise ContractValidationError("typed event payload must be a mapping")
    return canonical_hash(
        {
            "schema_version": "caerus_alpha_lab_typed_event_payload_v1",
            "event_type": event_type,
            "payload": payload,
        }
    )


def event_attestation_context_hash(
    *,
    event_id: str,
    event_type: str,
    occurred_at: datetime,
    recorded_at: datetime,
    payload_sha256: str,
    previous_event_hash: str,
) -> str:
    """Bind an attestation to the exact append operation and prior chain head."""

    require_non_empty(event_id, "event_id")
    require_non_empty(event_type, "event_type")
    _require_aware(occurred_at, "occurred_at")
    _require_aware(recorded_at, "recorded_at")
    require_sha256(payload_sha256, "payload_sha256")
    _require_ledger_head(previous_event_hash)
    return canonical_hash(
        {
            "schema_version": "caerus_alpha_lab_event_attestation_context_v1",
            "event_id": event_id,
            "event_type": event_type,
            "occurred_at": format_datetime(occurred_at),
            "recorded_at": format_datetime(recorded_at),
            "typed_payload_sha256": payload_sha256,
            "previous_event_hash": previous_event_hash,
        }
    )


def migration_plan_attestation_context_hash(plan: Mapping[str, Any]) -> str:
    """Bind an owner signature to the migration's immutable append boundary."""

    try:
        plan_identity = str(plan["plan_identity_sha256"])
        recorded_at = parse_datetime(str(plan["recorded_at"]))
        terminal_head = str(plan["identity_activation_head_hash"])
        ledger_sha256 = str(plan["expected_ledger_sha256"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractValidationError("migration plan identity context is incomplete") from exc
    require_sha256(plan_identity, "plan_identity_sha256")
    _require_aware(recorded_at, "recorded_at")
    require_sha256(terminal_head, "identity_activation_head_hash")
    require_sha256(ledger_sha256, "expected_ledger_sha256")
    return canonical_hash(
        {
            "schema_version": "caerus_alpha_lab_migration_attestation_context_v1",
            "plan_identity_sha256": plan_identity,
            "recorded_at": format_datetime(recorded_at),
            "identity_activation_head_hash": terminal_head,
            "expected_ledger_sha256": ledger_sha256,
        }
    )


@dataclass(frozen=True)
class IdentityKey:
    """A public verification key.  It contains no private credential material."""

    identity_id: str
    subject_id: str
    key_id: str
    public_key_pem: str
    allowed_roles: tuple[IdentityRole, ...]
    issued_at: datetime
    status: IdentityStatus = IdentityStatus.ACTIVE
    expires_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    replacement_key_id: Optional[str] = None
    shared_identity: bool = False
    recovery_issued: bool = False
    schema_version: str = "caerus_alpha_lab_identity_key_v1"

    def __post_init__(self) -> None:
        _require_identifier(self.identity_id, "identity_id", _IDENTITY_ID)
        _require_identifier(self.subject_id, "subject_id", _IDENTITY_ID)
        _require_identifier(self.key_id, "key_id", _KEY_ID)
        if not self.allowed_roles or len(self.allowed_roles) != len(set(self.allowed_roles)):
            raise ContractValidationError("allowed_roles must be a non-empty unique set")
        if not all(isinstance(role, IdentityRole) for role in self.allowed_roles):
            raise ContractValidationError("allowed_roles must contain IdentityRole values")
        _require_aware(self.issued_at, "issued_at")
        if not isinstance(self.status, IdentityStatus):
            raise ContractValidationError("identity key status is invalid")
        if self.expires_at is not None:
            _require_aware(self.expires_at, "expires_at")
            if self.expires_at <= self.issued_at:
                raise ContractValidationError("expires_at must follow issued_at")
        if self.revoked_at is not None:
            _require_aware(self.revoked_at, "revoked_at")
            if self.revoked_at < self.issued_at:
                raise ContractValidationError("revoked_at cannot precede issued_at")
        if self.status is IdentityStatus.REVOKED and self.revoked_at is None:
            raise ContractValidationError("revoked key requires revoked_at")
        if self.replacement_key_id is not None:
            _require_identifier(self.replacement_key_id, "replacement_key_id", _KEY_ID)
            if self.replacement_key_id == self.key_id:
                raise ContractValidationError("replacement_key_id must differ from key_id")
        if self.shared_identity:
            raise ContractValidationError("shared identities are forbidden")
        if not isinstance(self.recovery_issued, bool):
            raise ContractValidationError("recovery_issued must be boolean")
        if not isinstance(self.public_key_pem, str) or "PRIVATE KEY" in self.public_key_pem:
            raise ContractValidationError("identity record must contain an Ed25519 public key only")
        _require_crypto()
        try:
            public_key = serialization.load_pem_public_key(self.public_key_pem.encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise ContractValidationError("identity public key PEM is invalid") from exc
        if not isinstance(public_key, Ed25519PublicKey):
            raise ContractValidationError("identity public key must use Ed25519")

    @property
    def public_key_fingerprint(self) -> str:
        _require_crypto()
        public_key = serialization.load_pem_public_key(self.public_key_pem.encode("utf-8"))
        assert isinstance(public_key, Ed25519PublicKey)
        raw = public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return hashlib.sha256(raw).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "identity_id": self.identity_id,
            "subject_id": self.subject_id,
            "key_id": self.key_id,
            "public_key_pem": self.public_key_pem,
            "public_key_fingerprint": self.public_key_fingerprint,
            "allowed_roles": [role.value for role in self.allowed_roles],
            "issued_at": format_datetime(self.issued_at),
            "status": self.status.value,
            "expires_at": format_datetime(self.expires_at) if self.expires_at else None,
            "revoked_at": format_datetime(self.revoked_at) if self.revoked_at else None,
            "replacement_key_id": self.replacement_key_id,
            "shared_identity": False,
            "recovery_issued": self.recovery_issued,
        }


@dataclass(frozen=True)
class ResearchAttestation:
    """Detached Ed25519 signature bound to one immutable artifact and ledger head."""

    identity_id: str
    key_id: str
    role: IdentityRole
    artifact_sha256: str
    ledger_head_hash: str
    context_sha256: str
    attested_at: datetime
    signature_b64: str
    registry_hash: str
    schema_version: str = "caerus_alpha_lab_research_attestation_v1"

    def __post_init__(self) -> None:
        _require_identifier(self.identity_id, "identity_id", _IDENTITY_ID)
        _require_identifier(self.key_id, "key_id", _KEY_ID)
        if not isinstance(self.role, IdentityRole):
            raise ContractValidationError("attestation role is invalid")
        require_sha256(self.artifact_sha256, "artifact_sha256")
        _require_ledger_head(self.ledger_head_hash)
        require_sha256(self.context_sha256, "context_sha256")
        _require_aware(self.attested_at, "attested_at")
        require_sha256(self.registry_hash, "registry_hash")
        try:
            decoded = base64.b64decode(self.signature_b64, validate=True)
        except (TypeError, ValueError) as exc:
            raise ContractValidationError("signature_b64 is invalid") from exc
        if len(decoded) != 64:
            raise ContractValidationError("Ed25519 signature must be 64 bytes")

    def signed_payload(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "identity_id": self.identity_id,
            "key_id": self.key_id,
            "role": self.role.value,
            "artifact_sha256": self.artifact_sha256,
            "ledger_head_hash": self.ledger_head_hash,
            "context_sha256": self.context_sha256,
            "attested_at": format_datetime(self.attested_at),
            "registry_hash": self.registry_hash,
        }

    def to_dict(self) -> Dict[str, Any]:
        payload = self.signed_payload()
        payload["signature_b64"] = self.signature_b64
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ResearchAttestation":
        return cls(
            identity_id=str(value["identity_id"]),
            key_id=str(value["key_id"]),
            role=IdentityRole(value["role"]),
            artifact_sha256=str(value["artifact_sha256"]),
            ledger_head_hash=str(value["ledger_head_hash"]),
            context_sha256=str(value["context_sha256"]),
            attested_at=parse_datetime(str(value["attested_at"])),
            signature_b64=str(value["signature_b64"]),
            registry_hash=str(value["registry_hash"]),
            schema_version=str(value.get("schema_version", "caerus_alpha_lab_research_attestation_v1")),
        )


class IdentityRegistry:
    """Versioned public-key directory with rotation, revocation, and audit checks."""

    def __init__(
        self,
        *,
        registry_id: str,
        keys: Sequence[IdentityKey],
        issued_at: datetime,
        release: Optional[RegistryRelease] = None,
        trust_anchor: Optional[IdentityTrustAnchor] = None,
    ) -> None:
        _require_identifier(registry_id, "registry_id", _IDENTITY_ID)
        _require_aware(issued_at, "issued_at")
        if not keys:
            raise ContractValidationError("identity registry cannot be empty")
        self.registry_id = registry_id
        self.issued_at = issued_at
        self._keys = tuple(keys)
        self.release = release
        self.trust_anchor = trust_anchor
        ids = [(key.identity_id, key.key_id) for key in keys]
        if len(ids) != len(set(ids)):
            raise ContractValidationError("identity registry has duplicate identity/key IDs")
        key_ids = [key.key_id for key in keys]
        if len(key_ids) != len(set(key_ids)):
            raise ContractValidationError("key_id must identify exactly one credential")
        fingerprints = [key.public_key_fingerprint for key in keys]
        if len(fingerprints) != len(set(fingerprints)):
            raise ContractValidationError("one public key cannot be shared across identities")
        separated_roles = set(IdentityRole)
        roles_by_subject: Dict[str, set[IdentityRole]] = {}
        for key in keys:
            roles_by_subject.setdefault(key.subject_id, set()).update(key.allowed_roles)
        for subject_id, roles in roles_by_subject.items():
            if len(roles.intersection(separated_roles)) > 1:
                raise ContractValidationError(
                    "subject {} combines prohibited independent-control roles "
                    "across its lifetime".format(subject_id)
                )
        for key in keys:
            if key.replacement_key_id is not None and not any(
                candidate.identity_id == key.identity_id
                and candidate.key_id == key.replacement_key_id
                for candidate in keys
            ):
                raise ContractValidationError("rotation replacement key is not registered")
        active_by_role = {role: 0 for role in IdentityRole}
        for key in keys:
            if key.status is IdentityStatus.ACTIVE:
                for role in key.allowed_roles:
                    active_by_role[role] += 1
        if not all(active_by_role.values()):
            raise ContractValidationError("every attestation role needs an active identity key")
        if (release is None) != (trust_anchor is None):
            raise ContractValidationError("registry release and trust anchor must be supplied together")
        if release is not None and trust_anchor is not None:
            if issued_at > release.released_at:
                raise ContractValidationError(
                    "registry issued_at cannot follow its signed release"
                )
            if any(key.issued_at > issued_at for key in keys):
                raise ContractValidationError(
                    "registry cannot contain a key issued after the directory"
                )
            if any(
                key.revoked_at is not None and key.revoked_at > release.released_at
                for key in keys
            ):
                raise ContractValidationError(
                    "registry release cannot publish a future revocation"
                )
            if release.registry_id != self.registry_id or trust_anchor.expected_registry_id != self.registry_id:
                raise ContractValidationError("registry release does not match the configured trust anchor")
            if release.root_key_id != trust_anchor.root_key_id:
                raise ContractValidationError("registry release uses an unexpected trust-anchor key")
            if release.registry_hash != self.registry_hash:
                raise ContractValidationError("registry release hash does not match the public identity directory")
            root = serialization.load_pem_public_key(trust_anchor.root_public_key_pem.encode("utf-8"))
            assert isinstance(root, Ed25519PublicKey)
            try:
                root.verify(
                    base64.b64decode(release.signature_b64, validate=True),
                    canonical_json(release.signed_payload()).encode("utf-8"),
                )
            except InvalidSignature as exc:
                raise ContractValidationError("registry release signature verification failed") from exc

    def directory_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": "caerus_alpha_lab_identity_registry_v1",
            "registry_id": self.registry_id,
            "issued_at": format_datetime(self.issued_at),
            "keys": [key.to_dict() for key in sorted(self._keys, key=lambda item: (item.identity_id, item.key_id))],
        }

    def to_dict(self) -> Dict[str, Any]:
        result = self.directory_dict()
        if self.release is not None:
            result["release"] = {
                **self.release.signed_payload(),
                "signature_b64": self.release.signature_b64,
            }
        return result

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any], *, trust_anchor: Optional[IdentityTrustAnchor] = None
    ) -> "IdentityRegistry":
        if value.get("schema_version") != "caerus_alpha_lab_identity_registry_v1":
            raise ContractValidationError("identity registry schema_version is invalid")
        try:
            keys = []
            for raw in value["keys"]:
                key = IdentityKey(
                    identity_id=str(raw["identity_id"]),
                    subject_id=str(raw["subject_id"]),
                    key_id=str(raw["key_id"]),
                    public_key_pem=str(raw["public_key_pem"]),
                    allowed_roles=tuple(IdentityRole(role) for role in raw["allowed_roles"]),
                    issued_at=parse_datetime(str(raw["issued_at"])),
                    status=IdentityStatus(raw.get("status", "ACTIVE")),
                    expires_at=(
                        parse_datetime(str(raw["expires_at"]))
                        if raw.get("expires_at") is not None
                        else None
                    ),
                    revoked_at=(
                        parse_datetime(str(raw["revoked_at"]))
                        if raw.get("revoked_at") is not None
                        else None
                    ),
                    replacement_key_id=raw.get("replacement_key_id"),
                    shared_identity=bool(raw.get("shared_identity", False)),
                    recovery_issued=bool(raw.get("recovery_issued", False)),
                    schema_version=str(raw.get("schema_version", "caerus_alpha_lab_identity_key_v1")),
                )
                declared = raw.get("public_key_fingerprint")
                if declared != key.public_key_fingerprint:
                    raise ContractValidationError("identity public-key fingerprint mismatch")
                keys.append(key)
            release_raw = value.get("release")
            release = (
                RegistryRelease(
                    registry_id=str(release_raw["registry_id"]),
                    registry_hash=str(release_raw["registry_hash"]),
                    version=int(release_raw["version"]),
                    released_at=parse_datetime(str(release_raw["released_at"])),
                    root_key_id=str(release_raw["root_key_id"]),
                    signature_b64=str(release_raw["signature_b64"]),
                    previous_registry_hash=release_raw.get("previous_registry_hash"),
                    previous_release_hash=release_raw.get("previous_release_hash"),
                    schema_version=str(release_raw.get("schema_version", "caerus_alpha_lab_identity_registry_release_v2")),
                )
                if release_raw is not None
                else None
            )
            return cls(
                registry_id=str(value["registry_id"]),
                keys=keys,
                issued_at=parse_datetime(str(value["issued_at"])),
                release=release,
                trust_anchor=trust_anchor,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ContractValidationError("identity registry payload is invalid") from exc

    @property
    def registry_hash(self) -> str:
        return canonical_hash(self.directory_dict())

    @property
    def is_anchored(self) -> bool:
        return self.release is not None and self.trust_anchor is not None

    def audit(self, *, observed_at: datetime) -> Dict[str, Any]:
        _require_aware(observed_at, "observed_at")
        expiring = sorted(
            key.key_id
            for key in self._keys
            if key.status is IdentityStatus.ACTIVE
            and key.expires_at is not None
            and key.expires_at - observed_at <= timedelta(days=30)
        )
        return {
            "registry_id": self.registry_id,
            "registry_hash": self.registry_hash,
            "observed_at": format_datetime(observed_at),
            "active_key_count": sum(key.status is IdentityStatus.ACTIVE for key in self._keys),
            "revoked_key_count": sum(key.status is IdentityStatus.REVOKED for key in self._keys),
            "recovery_issued_key_count": sum(key.recovery_issued for key in self._keys),
            "expiring_key_ids": expiring,
            "private_keys_persisted": False,
        }

    def subject_id_for(self, *, identity_id: str, key_id: str) -> str:
        key = next(
            (item for item in self._keys if item.identity_id == identity_id and item.key_id == key_id),
            None,
        )
        if key is None:
            raise ContractValidationError("identity key is not in the authenticated identity registry")
        return key.subject_id

    def verify(
        self,
        attestation: ResearchAttestation,
        *,
        expected_role: IdentityRole,
        artifact_sha256: str,
        ledger_head_hash: str,
        context_sha256: str,
        recorded_at: datetime,
    ) -> None:
        """Verify role, registry snapshot, timestamp, lifecycle, and signature."""

        _require_aware(recorded_at, "recorded_at")
        require_sha256(artifact_sha256, "artifact_sha256")
        _require_ledger_head(ledger_head_hash)
        require_sha256(context_sha256, "context_sha256")
        if not self.is_anchored:
            raise ContractValidationError("identity registry lacks an authenticated trust anchor")
        if attestation.registry_hash != self.registry_hash:
            raise ContractValidationError("attestation does not bind this identity registry version")
        if attestation.role is not expected_role:
            raise ContractValidationError("attestation role does not match the required control")
        if attestation.artifact_sha256 != artifact_sha256:
            raise ContractValidationError("attestation artifact hash does not match immutable evidence")
        if attestation.ledger_head_hash != ledger_head_hash:
            raise ContractValidationError("attestation ledger head is stale or mismatched")
        if attestation.context_sha256 != context_sha256:
            raise ContractValidationError("attestation context does not match the signed operation")
        if attestation.attested_at > recorded_at or recorded_at - attestation.attested_at > MAX_ATTESTATION_AGE:
            raise ContractValidationError("attestation timestamp is outside the accepted recording window")
        key = next(
            (item for item in self._keys if item.identity_id == attestation.identity_id and item.key_id == attestation.key_id),
            None,
        )
        if key is None:
            raise ContractValidationError("attestation key is not in the authenticated identity registry")
        if key.status is IdentityStatus.REVOKED:
            raise ContractValidationError("attestation key is revoked")
        if key.status is not IdentityStatus.ACTIVE:
            raise ContractValidationError("attestation key is not active")
        if expected_role not in key.allowed_roles:
            raise ContractValidationError("identity key is not authorized for the attestation role")
        if attestation.attested_at < key.issued_at:
            raise ContractValidationError("attestation predates key issuance")
        if key.expires_at is not None and attestation.attested_at >= key.expires_at:
            raise ContractValidationError("attestation uses an expired identity key")
        if key.revoked_at is not None and attestation.attested_at >= key.revoked_at:
            raise ContractValidationError("attestation uses a revoked identity key")
        _require_crypto()
        public_key = serialization.load_pem_public_key(key.public_key_pem.encode("utf-8"))
        assert isinstance(public_key, Ed25519PublicKey)
        try:
            public_key.verify(
                base64.b64decode(attestation.signature_b64, validate=True),
                canonical_json(attestation.signed_payload()).encode("utf-8"),
            )
        except InvalidSignature as exc:
            raise ContractValidationError("attestation signature verification failed") from exc


class IdentityRegistryHistory:
    """Immutable, anchored registry-version resolver with explicit anti-rollback pin."""

    def __init__(
        self,
        *,
        registries: Sequence[IdentityRegistry],
        active_registry_hash: str,
        externally_pinned_registry_hash: str,
    ) -> None:
        if not registries:
            raise ContractValidationError("identity registry history cannot be empty")
        require_sha256(active_registry_hash, "active_registry_hash")
        require_sha256(externally_pinned_registry_hash, "externally_pinned_registry_hash")
        self._registries = {registry.registry_hash: registry for registry in registries}
        if len(self._registries) != len(registries):
            raise ContractValidationError("identity registry history has duplicate versions")
        if active_registry_hash not in self._registries:
            raise ContractValidationError("active identity registry hash is not in history")
        if externally_pinned_registry_hash != active_registry_hash:
            raise ContractValidationError("external identity pin rejects a registry rollback or substitution")
        active = self._registries[active_registry_hash]
        if not all(registry.is_anchored for registry in registries):
            raise ContractValidationError("every historical identity registry must be trust anchored")
        assert active.release is not None and active.trust_anchor is not None
        ordered = sorted(registries, key=lambda item: item.release.version if item.release else -1)
        versions = [registry.release.version for registry in ordered if registry.release]
        if len(versions) != len(set(versions)):
            raise ContractValidationError("identity registry history has duplicate release versions")
        if versions != list(range(1, len(versions) + 1)):
            raise ContractValidationError(
                "identity registry releases must form a contiguous chain beginning at version 1"
            )
        if active.release.version != max(versions):
            raise ContractValidationError("active identity registry is not the newest signed release")
        if any(
            registry.trust_anchor is None
            or registry.trust_anchor.anchor_id != active.trust_anchor.anchor_id
            or registry.trust_anchor.root_key_id != active.trust_anchor.root_key_id
            or registry.trust_anchor.root_public_key_pem
            != active.trust_anchor.root_public_key_pem
            or registry.trust_anchor.expected_registry_id
            != active.trust_anchor.expected_registry_id
            for registry in registries
        ):
            raise ContractValidationError("identity registry history changes its trust anchor")
        lifetime_roles_by_subject: Dict[str, set[IdentityRole]] = {}
        identity_contracts: Dict[str, tuple[str, tuple[IdentityRole, ...]]] = {}
        for registry in ordered:
            assert registry.release is not None
            if registry.issued_at > registry.release.released_at:
                raise ContractValidationError(
                    "registry issued_at cannot follow its release timestamp"
                )
            for key in registry._keys:
                lifetime_roles_by_subject.setdefault(key.subject_id, set()).update(
                    key.allowed_roles
                )
                identity_contract = (key.subject_id, key.allowed_roles)
                prior_identity_contract = identity_contracts.setdefault(
                    key.identity_id, identity_contract
                )
                if prior_identity_contract != identity_contract:
                    raise ContractValidationError(
                        "identity subject or role contract mutates across releases"
                    )
        if any(len(roles) > 1 for roles in lifetime_roles_by_subject.values()):
            raise ContractValidationError(
                "a subject cannot change or combine owner and independent roles across releases"
            )

        def immutable_key_contract(key: IdentityKey) -> tuple[Any, ...]:
            return (
                key.identity_id,
                key.subject_id,
                key.key_id,
                key.public_key_pem,
                key.public_key_fingerprint,
                key.allowed_roles,
                key.issued_at,
                key.expires_at,
                key.shared_identity,
                key.recovery_issued,
                key.schema_version,
            )

        for prior, current in zip(ordered, ordered[1:]):
            assert prior.release is not None and current.release is not None
            if current.release.released_at <= prior.release.released_at:
                raise ContractValidationError(
                    "identity registry release timestamps must increase monotonically"
                )
            if current.release.previous_registry_hash != prior.registry_hash:
                raise ContractValidationError(
                    "identity registry release predecessor hash is not contiguous"
                )
            if current.release.previous_release_hash != prior.release.release_hash:
                raise ContractValidationError(
                    "identity registry release predecessor signature is not contiguous"
                )
            if current.issued_at <= prior.issued_at:
                raise ContractValidationError(
                    "registry directory timestamps must increase monotonically"
                )
            if current.issued_at < prior.release.released_at:
                raise ContractValidationError(
                    "new registry directory predates its predecessor release"
                )
            prior_keys = {key.key_id: key for key in prior._keys}
            current_keys = {key.key_id: key for key in current._keys}
            removed = set(prior_keys) - set(current_keys)
            if removed:
                raise ContractValidationError(
                    "registry releases cannot remove historical key records"
                )
            for key_id, old in prior_keys.items():
                new = current_keys[key_id]
                if immutable_key_contract(old) != immutable_key_contract(new):
                    raise ContractValidationError(
                        "key identity, public key, subject, or role mutates across releases"
                    )
                if old.status is IdentityStatus.ACTIVE:
                    if new.status is IdentityStatus.ACTIVE:
                        if (
                            new.revoked_at != old.revoked_at
                            or new.replacement_key_id != old.replacement_key_id
                        ):
                            raise ContractValidationError(
                                "active key lifecycle metadata mutates without rotation"
                            )
                    else:
                        if new.replacement_key_id is None:
                            raise ContractValidationError(
                                "retirement or revocation requires an explicit replacement key"
                            )
                        replacement = current_keys.get(new.replacement_key_id)
                        if (
                            replacement is None
                            or replacement.identity_id != new.identity_id
                            or replacement.subject_id != new.subject_id
                            or replacement.allowed_roles != new.allowed_roles
                            or replacement.status is not IdentityStatus.ACTIVE
                            or replacement.issued_at <= old.issued_at
                        ):
                            raise ContractValidationError(
                                "replacement key is not a legitimate same-role rotation"
                            )
                        if new.status is IdentityStatus.REVOKED and (
                            new.revoked_at is None
                            or new.revoked_at <= prior.release.released_at
                            or new.revoked_at > current.release.released_at
                        ):
                            raise ContractValidationError(
                                "revocation time is outside its signed release interval"
                            )
                elif new.status is not old.status:
                    raise ContractValidationError(
                        "retired or revoked key status cannot be resurrected or changed"
                    )
                elif (
                    new.revoked_at != old.revoked_at
                    or new.replacement_key_id != old.replacement_key_id
                ):
                    raise ContractValidationError(
                        "terminal key lifecycle metadata is immutable"
                    )
            for key_id in set(current_keys) - set(prior_keys):
                added = current_keys[key_id]
                if added.status is not IdentityStatus.ACTIVE:
                    raise ContractValidationError(
                        "a newly appearing key must be active, not retrospective history"
                    )
                if (
                    added.issued_at <= prior.release.released_at
                    or added.issued_at > current.issued_at
                ):
                    raise ContractValidationError(
                        "new key issuance must fall after the predecessor release and no later than the directory"
                    )
        self._ordered = tuple(ordered)
        self._successor_by_hash = {
            prior.registry_hash: current
            for prior, current in zip(self._ordered, self._ordered[1:])
        }
        self.active_registry_hash = active_registry_hash
        self.externally_pinned_registry_hash = externally_pinned_registry_hash

    @property
    def is_anchored(self) -> bool:
        return True

    @property
    def registry_hash(self) -> str:
        return self.active_registry_hash

    def resolve(self, registry_hash: str) -> IdentityRegistry:
        try:
            return self._registries[registry_hash]
        except KeyError as exc:
            raise ContractValidationError("attestation references an unavailable historical registry version") from exc

    def verify(
        self,
        attestation: ResearchAttestation,
        *,
        for_new_event: bool = False,
        **kwargs: Any,
    ) -> None:
        """Verify against the release that was valid for the signed record.

        New writes must use the externally pinned active release. Historical
        releases remain usable only for records strictly before their signed
        supersession time.
        """

        if for_new_event and attestation.registry_hash != self.active_registry_hash:
            raise ContractValidationError(
                "new attestations must use the externally pinned active registry"
            )
        registry = self.resolve(attestation.registry_hash)
        recorded_at = kwargs.get("recorded_at")
        assert registry.release is not None
        if recorded_at is None:
            raise ContractValidationError(
                "registry-history verification requires an event recording time"
            )
        _require_aware(recorded_at, "recorded_at")
        if (
            attestation.attested_at < registry.release.released_at
            or recorded_at < registry.release.released_at
        ):
            raise ContractValidationError(
                "attestation or event predates the referenced registry release"
            )
        successor = self._successor_by_hash.get(attestation.registry_hash)
        if successor is not None:
            assert successor.release is not None
            if recorded_at is None or recorded_at >= successor.release.released_at:
                raise ContractValidationError(
                    "historical registry release was superseded before this record"
                )
        registry.verify(attestation, **kwargs)

    def subject_id_for(self, *, identity_id: str, key_id: str, registry_hash: Optional[str] = None) -> str:
        registry = self.resolve(registry_hash or self.active_registry_hash)
        return registry.subject_id_for(identity_id=identity_id, key_id=key_id)


class IdentityActivationEvidence:
    """Verified signed evidence for one legacy-to-authenticated cutover.

    Construction is deliberately routed through :meth:`from_signed_plan`; a
    caller cannot activate an arbitrary ledger head by supplying only a hash.
    """

    def __init__(
        self,
        *,
        plan: Mapping[str, Any],
        owner_attestation: ResearchAttestation,
        _verified_marker: object,
    ) -> None:
        if _verified_marker is not _ACTIVATION_VERIFIED:
            raise ContractValidationError(
                "identity activation must come from a verified signed migration plan"
            )
        self.plan = dict(plan)
        self.owner_attestation = owner_attestation
        self.plan_sha256 = canonical_hash(plan)
        self.plan_identity_sha256 = str(plan["plan_identity_sha256"])
        self.identity_activation_head_hash = str(
            plan["identity_activation_head_hash"]
        )

    @classmethod
    def from_signed_plan(
        cls,
        value: Mapping[str, Any],
        *,
        identity_history: IdentityRegistryHistory,
    ) -> "IdentityActivationEvidence":
        if value.get("schema_version") != "caerus_alpha_lab_signed_migration_plan_v1":
            raise ContractValidationError("signed migration plan wrapper is invalid")
        plan = value.get("plan")
        if not isinstance(plan, Mapping):
            raise ContractValidationError("signed migration plan lacks its canonical plan")
        if plan.get("schema_version") != "caerus_alpha_lab_migration_event_plan_v2":
            raise ContractValidationError("migration event plan schema is invalid")
        if plan.get("decision") != "RATIFY_GLOBAL_RESEARCH_LEDGER_MIGRATION":
            raise ContractValidationError("migration event plan decision is invalid")
        identity_fields = (
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
        try:
            identity_material = {key: plan[key] for key in identity_fields}
        except KeyError as exc:
            raise ContractValidationError("migration plan identity is incomplete") from exc
        identity_material["schema_version"] = (
            "caerus_alpha_lab_migration_plan_identity_v1"
        )
        if canonical_hash(identity_material) != plan.get("plan_identity_sha256"):
            raise ContractValidationError("migration plan identity hash is invalid")
        events = plan.get("ordered_events")
        if not isinstance(events, list) or not events:
            raise ContractValidationError("migration event plan has no ordered events")
        required_event_fields = {
            "event_id",
            "event_type",
            "typed_payload_sha256",
            "record_payload_sha256",
            "previous_event_hash",
            "event_hash",
            "recorded_at",
        }
        if any(
            not isinstance(item, Mapping) or set(item) != required_event_fields
            for item in events
        ):
            raise ContractValidationError(
                "migration event plan descriptors are incomplete or mutable"
            )
        if len({str(item["event_id"]) for item in events}) != len(events):
            raise ContractValidationError("migration event plan has duplicate event IDs")
        previous: Optional[str] = None
        for item in events:
            require_non_empty(str(item["event_id"]), "event_id")
            require_non_empty(str(item["event_type"]), "event_type")
            require_sha256(
                str(item["typed_payload_sha256"]), "typed_payload_sha256"
            )
            require_sha256(
                str(item["record_payload_sha256"]), "record_payload_sha256"
            )
            require_sha256(str(item["event_hash"]), "event_hash")
            parse_datetime(str(item["recorded_at"]))
            if item["previous_event_hash"] != previous:
                raise ContractValidationError(
                    "migration event plan predecessor chain is not contiguous"
                )
            previous = str(item["event_hash"])
        if plan.get("expected_terminal_head") != previous:
            raise ContractValidationError("migration plan terminal head is inconsistent")
        if plan.get("identity_activation_head_hash") != previous:
            raise ContractValidationError(
                "signed identity activation boundary is inconsistent"
            )
        require_sha256(str(plan.get("expected_ledger_sha256", "")), "expected_ledger_sha256")
        try:
            attestation = ResearchAttestation.from_dict(value["owner_attestation"])
            identity_history.verify(
                attestation,
                expected_role=IdentityRole.OWNER_RATIFIER,
                artifact_sha256=canonical_hash(plan),
                ledger_head_hash=GENESIS_LEDGER_HEAD,
                context_sha256=migration_plan_attestation_context_hash(plan),
                recorded_at=parse_datetime(str(plan["recorded_at"])),
                for_new_event=True,
            )
        except (KeyError, ContractValidationError) as exc:
            raise ContractValidationError(
                "migration plan lacks a valid active-registry owner signature"
            ) from exc
        return cls(
            plan=plan,
            owner_attestation=attestation,
            _verified_marker=_ACTIVATION_VERIFIED,
        )

    def verify_legacy_records(self, records: Sequence["EventRecord"]) -> None:
        """Prove the canonical legacy prefix is exactly the signed event plan."""

        expected = self.plan["ordered_events"]
        if len(records) < len(expected):
            raise ContractValidationError(
                "canonical ledger is shorter than the signed migration plan"
            )
        prefix = records[: len(expected)]
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
                "recorded_at": format_datetime(record.recorded_at),
            }
            for record in prefix
        ]
        if canonical_hash(descriptors) != canonical_hash(expected):
            raise ContractValidationError(
                "canonical legacy events differ from the owner-signed migration plan"
            )
        if prefix[-1].event_hash != self.identity_activation_head_hash:
            raise ContractValidationError(
                "canonical evidence does not recover the signed activation boundary"
            )


_ACTIVATION_VERIFIED = object()

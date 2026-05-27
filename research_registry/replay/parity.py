"""Deterministic parity validation for cross-environment registry checks."""

from __future__ import annotations

from dataclasses import dataclass, field

from research_registry.models.base import ResearchObjectEnvelope, sha256_hex
from research_registry.replay.rebuild import DeterministicRebuilder


@dataclass(frozen=True)
class ParityReport:
    status: str
    first_digest: str
    second_digest: str
    findings: list[str] = field(default_factory=list)


class DeterministicParityValidator:
    def compare_rebuilds(self, *, first_db_path, second_db_path, envelopes: list[ResearchObjectEnvelope]) -> ParityReport:
        rebuilder = DeterministicRebuilder()
        first = rebuilder.rebuild(db_path=first_db_path, envelopes=list(reversed(envelopes)))
        second = rebuilder.rebuild(db_path=second_db_path, envelopes=sorted(envelopes, key=lambda item: item.object_id))
        findings: list[str] = []
        if first.object_ids != second.object_ids:
            findings.append("OBJECT_ID_SET_DIVERGENCE")
        if first.registry_digest != second.registry_digest:
            findings.append("REGISTRY_DIGEST_DIVERGENCE")
        return ParityReport(
            status="PASS" if not findings else "FAIL",
            first_digest=first.registry_digest,
            second_digest=second.registry_digest,
            findings=findings,
        )

    def serialization_digest(self, envelopes: list[ResearchObjectEnvelope]) -> str:
        return sha256_hex([envelope.to_dict() for envelope in sorted(envelopes, key=lambda item: item.object_id)])

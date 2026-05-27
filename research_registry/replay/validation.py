"""Replay hardening primitives for canonical envelope reconstruction."""

from __future__ import annotations

from dataclasses import dataclass, field

from research_registry.models.base import ResearchObjectEnvelope, sha256_hex
from research_registry.temporal import TemporalFence


@dataclass(frozen=True)
class ReplayValidationResult:
    replay_target: str
    replay_anchor: str
    replay_result: str
    replay_quality: str
    findings: list[str] = field(default_factory=list)
    original_hash: str | None = None
    replay_hash: str | None = None


class ReplayValidator:
    def __init__(self) -> None:
        self.fence = TemporalFence()

    def validate_canonical_replay(
        self,
        original: ResearchObjectEnvelope,
        replayed: ResearchObjectEnvelope,
        *,
        anchor: str,
        ancestors: list[ResearchObjectEnvelope] | None = None,
    ) -> ReplayValidationResult:
        findings: list[str] = []
        participants = [original, replayed] + list(ancestors or [])
        for envelope in participants:
            if not self.fence.admissible(envelope, anchor):
                findings.append(f"FUTURE_INFORMATION:{envelope.object_id}")
        if original.object_id != replayed.object_id:
            findings.append("OBJECT_ID_DIVERGENCE")
        if original.lineage["transformation_chain_hash"] != replayed.lineage["transformation_chain_hash"]:
            findings.append("CHAIN_HASH_DIVERGENCE")
        original_hash = sha256_hex(original.to_dict())
        replay_hash = sha256_hex(replayed.to_dict())
        if original_hash != replay_hash:
            findings.append("PAYLOAD_DIVERGENCE")
        if findings:
            return ReplayValidationResult(
                replay_target=original.object_id,
                replay_anchor=anchor,
                replay_result="DIVERGENT",
                replay_quality="GUARANTEED",
                findings=sorted(set(findings)),
                original_hash=original_hash,
                replay_hash=replay_hash,
            )
        return ReplayValidationResult(
            replay_target=original.object_id,
            replay_anchor=anchor,
            replay_result="IDENTICAL",
            replay_quality="GUARANTEED",
            original_hash=original_hash,
            replay_hash=replay_hash,
        )

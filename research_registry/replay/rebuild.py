"""Deterministic rebuild and parity verification helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from research_registry.models.base import ResearchObjectEnvelope
from research_registry.registry.sqlite_registry import SQLiteResearchRegistry


@dataclass(frozen=True)
class RebuildResult:
    object_ids: list[str]
    registry_digest: str


class DeterministicRebuilder:
    def rebuild(self, *, db_path: str | Path, envelopes: list[ResearchObjectEnvelope]) -> RebuildResult:
        if Path(db_path).exists():
            raise ValueError("rebuild target must not already exist")
        registry = SQLiteResearchRegistry(db_path)
        try:
            remaining = {envelope.object_id: envelope for envelope in envelopes}
            ingested: set[str] = set()
            while remaining:
                ready = [
                    envelope
                    for envelope in remaining.values()
                    if set(envelope.lineage.get("parent_refs", [])).issubset(ingested)
                ]
                if not ready:
                    raise ValueError("LINEAGE_DANGLING_PARENT_OR_CYCLE")
                for envelope in sorted(ready, key=lambda item: item.object_id):
                    registry.ingest(envelope)
                    ingested.add(envelope.object_id)
                    del remaining[envelope.object_id]
            return RebuildResult(
                object_ids=registry.store.object_ids(),
                registry_digest=registry.registry_digest(),
            )
        finally:
            registry.close()

    def assert_parity(self, first: RebuildResult, second: RebuildResult) -> None:
        if first != second:
            raise ValueError("REGISTRY_REBUILD_DIVERGENCE")

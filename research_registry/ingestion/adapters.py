"""Artifact hydration adapters that keep filesystem paths out of ontology."""

from __future__ import annotations

import json
from pathlib import Path

from research_registry.models.base import ResearchObjectEnvelope


class EnvelopeJsonAdapter:
    """Hydrates already-envelope-bearing JSON artifacts deterministically."""

    def hydrate_path(self, path: str | Path) -> ResearchObjectEnvelope:
        with Path(path).open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return ResearchObjectEnvelope.from_dict(payload)

    def hydrate_paths(self, paths: list[str | Path]) -> list[ResearchObjectEnvelope]:
        return [self.hydrate_path(path) for path in sorted(paths, key=lambda item: str(item))]

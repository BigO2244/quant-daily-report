"""Validated source registry; credentials are referenced by environment name only."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from projects.alpha_lab.factory import ContractValidationError, canonical_hash


@dataclass(frozen=True)
class SourceRegistry:
    schema_version: str
    output_root: str
    production_integration: bool
    sources: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.schema_version != "caerus_alpha_lab_data_spine_config_v1":
            raise ContractValidationError("unsupported data-spine config schema")
        if self.production_integration is not False:
            raise ContractValidationError("data spine must remain research-only")
        if self.output_root != "outputs/research/alpha_lab/data_spine":
            raise ContractValidationError("data-spine output root is frozen")
        object.__setattr__(self, "sources", MappingProxyType(dict(self.sources)))

    @property
    def config_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> Mapping[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "output_root": self.output_root,
            "production_integration": self.production_integration,
        }
        payload.update(self.sources)
        return payload


def load_registry(path: Path | None = None) -> SourceRegistry:
    target = path or Path(__file__).with_name("config.json")
    raw = json.loads(Path(target).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ContractValidationError("data-spine config must be an object")
    core = {key: raw.pop(key) for key in ("schema_version", "output_root", "production_integration")}
    return SourceRegistry(sources=raw, **core)

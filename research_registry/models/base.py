"""Canonical SEM-001 metadata envelope and deterministic identity helpers."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping


SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def snake_object_type(object_type: str) -> str:
    chars: list[str] = []
    for index, char in enumerate(object_type):
        if char.isupper() and index > 0:
            chars.append("_")
        chars.append(char.lower())
    return "".join(chars)


def canonical_object_id(
    object_type: str,
    strategy_ref: str | None,
    trade_date: str | None,
    surface_ref: str | None,
    schema_version: str,
) -> str:
    return "__".join(
        [
            snake_object_type(object_type),
            strategy_ref or "_",
            trade_date or "_",
            surface_ref or "_",
            schema_version,
        ]
    )


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def compute_source_state_hash(source_payloads: Mapping[str, Any]) -> str:
    return sha256_hex(source_payloads)


def compute_transformation_chain_hash(
    parent_chain_hashes: list[str],
    schema_version: str,
    ontology_version: str,
    produced_by: str,
    transformation: str,
    deterministic: bool,
    source_state_hash: str | None,
) -> str:
    return sha256_hex(
        [
            sorted(parent_chain_hashes),
            schema_version,
            ontology_version,
            produced_by,
            transformation,
            deterministic,
            source_state_hash or "",
        ]
    )


@dataclass(frozen=True)
class ResearchObjectEnvelope:
    object_type: str
    object_id: str
    schema: dict[str, Any]
    identity: dict[str, Any]
    temporal: dict[str, Any]
    provenance: dict[str, Any]
    confidence: dict[str, Any]
    governance: dict[str, Any]
    surface: dict[str, Any]
    lineage: dict[str, Any]
    data: dict[str, Any]
    annotations: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.to_dict()).encode("utf-8")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ResearchObjectEnvelope":
        return cls(
            object_type=payload["object_type"],
            object_id=payload["object_id"],
            schema=dict(payload["schema"]),
            identity=dict(payload["identity"]),
            temporal=dict(payload["temporal"]),
            provenance=dict(payload["provenance"]),
            confidence=dict(payload["confidence"]),
            governance=dict(payload["governance"]),
            surface=dict(payload["surface"]),
            lineage=dict(payload["lineage"]),
            data=dict(payload["data"]),
            annotations=dict(payload.get("annotations", {})),
        )


class MetadataEnvelopeBuilder:
    """Small explicit builder that refuses to infer semantic fields."""

    def build(
        self,
        *,
        object_type: str,
        data: Mapping[str, Any],
        strategy_ref: str | None,
        trade_date: str | None,
        surface_ref: str | None,
        schema_id: str,
        schema_version: str,
        ontology_version: str,
        as_of: str,
        produced_by: str,
        produced_at: str,
        source_paths: list[str],
        input_object_ids: list[str],
        transformation: str,
        deterministic: bool,
        source_state_hash: str | None,
        materiality_map: Mapping[str, str] | None = None,
        parent_refs: list[str],
        parent_chain_hashes: list[str],
        confidence_level: str,
        confidence_rationale: str,
        governance_state: str,
        governance_coverage_type: str,
        nav_surface_type: str | None,
        execution_realism: str | None,
        chain_status: str,
        governing_frs: list[str] | None = None,
        observation_status: str = "not_required",
        downgrade_reasons: list[str] | None = None,
        limiting_dependency: str | None = None,
        valid_from: str | None = None,
        valid_to: str | None = None,
        staleness_threshold_seconds: int | None = None,
        is_stale: bool = False,
        annotations: Mapping[str, Any] | None = None,
    ) -> ResearchObjectEnvelope:
        object_id = canonical_object_id(
            object_type=object_type,
            strategy_ref=strategy_ref,
            trade_date=trade_date,
            surface_ref=surface_ref,
            schema_version=schema_version,
        )
        chain_hash = compute_transformation_chain_hash(
            parent_chain_hashes=parent_chain_hashes,
            schema_version=schema_version,
            ontology_version=ontology_version,
            produced_by=produced_by,
            transformation=transformation,
            deterministic=deterministic,
            source_state_hash=source_state_hash,
        )
        return ResearchObjectEnvelope(
            object_type=object_type,
            object_id=object_id,
            schema={
                "schema_id": schema_id,
                "schema_version": schema_version,
                "ontology_version": ontology_version,
            },
            identity={
                "strategy_ref": strategy_ref,
                "trade_date": trade_date,
                "surface_ref": surface_ref,
            },
            temporal={
                "as_of": as_of,
                "trade_date": trade_date,
                "valid_from": valid_from,
                "valid_to": valid_to,
                "staleness_threshold_seconds": staleness_threshold_seconds,
                "is_stale": is_stale,
            },
            provenance={
                "produced_by": produced_by,
                "produced_at": produced_at,
                "source_paths": list(source_paths),
                "input_object_ids": list(input_object_ids),
                "transformation": transformation,
                "deterministic": deterministic,
                "source_state_hash": source_state_hash,
                "materiality_map": dict(materiality_map or {}),
            },
            confidence={
                "level": confidence_level,
                "rationale": confidence_rationale,
                "limiting_dependency": limiting_dependency,
                "downgrade_reasons": list(downgrade_reasons or []),
            },
            governance={
                "state": governance_state,
                "governing_frs": list(governing_frs or []),
                "coverage_type": governance_coverage_type,
                "observation_status": observation_status,
            },
            surface={
                "nav_surface_type": nav_surface_type,
                "execution_realism": execution_realism,
                "chain_status": chain_status,
            },
            lineage={
                "node_id": f"{object_id}#node",
                "parent_refs": list(parent_refs),
                "transformation_chain_hash": chain_hash,
            },
            data=dict(data),
            annotations=dict(annotations or {}),
        )

#!/usr/bin/env python3
"""Read-only operator CLI for the Caerus research registry.

The CLI builds or opens a caller-specified disposable SQLite registry. Source
artifacts are only read; they are never modified.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from research_registry.ingestion import ingest_artifact_family
from research_registry.query import RegistryQuery
from research_registry.registry import SQLiteResearchRegistry


def _json_default(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=_json_default))


def _load_manifest(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("families"), list):
        entries = payload["families"]
    elif isinstance(payload, dict):
        entries = [
            {"family": family, "paths": paths}
            for family, paths in sorted(payload.items())
        ]
    elif isinstance(payload, list):
        entries = payload
    else:
        raise ValueError("manifest must be a JSON object or list")

    normalized: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("manifest entries must be objects")
        family = str(entry.get("family") or "").strip()
        paths = entry.get("paths") or entry.get("artifact_paths") or []
        if not family:
            raise ValueError("manifest entry missing family")
        if isinstance(paths, str):
            paths = [paths]
        if not isinstance(paths, list):
            raise ValueError(f"manifest entry for {family} has non-list paths")
        normalized.append({"family": family, "paths": [str(path) for path in paths]})
    return normalized


def _open_registry(db_path: Path) -> SQLiteResearchRegistry:
    return SQLiteResearchRegistry(db_path)


def cmd_build(args: argparse.Namespace) -> int:
    manifest = _load_manifest(Path(args.manifest))
    registry = _open_registry(Path(args.db))
    try:
        family_results = []
        for entry in manifest:
            result = ingest_artifact_family(
                family=entry["family"],
                artifact_paths=[Path(path) for path in entry["paths"]],
                registry=registry,
            )
            family_results.append(
                {
                    "family": entry["family"],
                    "path_count": len(entry["paths"]),
                    "envelope_count": len(result.envelopes),
                    "findings": [dataclasses.asdict(finding) for finding in result.findings],
                }
            )
        query = RegistryQuery(registry)
        _print_json(
            {
                "status": "BUILT",
                "db_path": str(Path(args.db)),
                "families": family_results,
                "summary": query.registry_summary(),
            }
        )
    finally:
        registry.close()
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    registry = _open_registry(Path(args.db))
    try:
        query = RegistryQuery(registry)
        _print_json(
            {
                "db_path": str(Path(args.db)),
                "summary": query.registry_summary(),
                "statistics": query.registry_statistics(),
            }
        )
    finally:
        registry.close()
    return 0


def _filtered_objects(args: argparse.Namespace, query: RegistryQuery) -> list[Any]:
    objects = query.list_objects()
    if args.artifact_type:
        objects = [obj for obj in objects if obj.object_type == args.artifact_type]
    if args.surface:
        objects = [obj for obj in objects if obj.surface.get("nav_surface_type") == args.surface]
    if args.confidence:
        objects = [obj for obj in objects if obj.confidence.get("level") == args.confidence]
    if args.governance:
        objects = [obj for obj in objects if obj.governance.get("state") == args.governance]
    return objects


def cmd_list_or_query(args: argparse.Namespace) -> int:
    registry = _open_registry(Path(args.db))
    try:
        query = RegistryQuery(registry)
        objects = _filtered_objects(args, query)
        if args.limit is not None:
            objects = objects[: max(0, int(args.limit))]
        _print_json(
            {
                "db_path": str(Path(args.db)),
                "object_count": len(objects),
                "objects": [obj.to_dict() for obj in objects],
            }
        )
    finally:
        registry.close()
    return 0


def cmd_lineage(args: argparse.Namespace) -> int:
    registry = _open_registry(Path(args.db))
    try:
        query = RegistryQuery(registry)
        _print_json(query.get_lineage(args.object_id))
    finally:
        registry.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only Caerus research registry operator CLI."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="Build a disposable registry from a bounded manifest.")
    build.add_argument("--db", required=True, help="SQLite registry path to create or update.")
    build.add_argument("--manifest", required=True, help="JSON manifest of artifact families and paths.")
    build.set_defaults(func=cmd_build)

    summary = subparsers.add_parser("summary", help="Print registry summary and statistics.")
    summary.add_argument("--db", required=True, help="SQLite registry path to open.")
    summary.set_defaults(func=cmd_summary)

    list_cmd = subparsers.add_parser("list", help="List registered objects.")
    list_cmd.add_argument("--db", required=True, help="SQLite registry path to open.")
    list_cmd.add_argument("--limit", type=int, default=None)
    list_cmd.add_argument("--artifact-type")
    list_cmd.add_argument("--surface")
    list_cmd.add_argument("--confidence")
    list_cmd.add_argument("--governance", help="Governance state/category filter.")
    list_cmd.set_defaults(func=cmd_list_or_query)

    query_cmd = subparsers.add_parser("query", help="Query registered objects with filters.")
    query_cmd.add_argument("--db", required=True, help="SQLite registry path to open.")
    query_cmd.add_argument("--limit", type=int, default=None)
    query_cmd.add_argument("--artifact-type")
    query_cmd.add_argument("--surface")
    query_cmd.add_argument("--confidence")
    query_cmd.add_argument("--governance", help="Governance state/category filter.")
    query_cmd.set_defaults(func=cmd_list_or_query)

    lineage = subparsers.add_parser("lineage", help="Print lineage for one object.")
    lineage.add_argument("--db", required=True, help="SQLite registry path to open.")
    lineage.add_argument("--object-id", required=True)
    lineage.set_defaults(func=cmd_lineage)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())

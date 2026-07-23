"""Immutable, checksummed research bundles for external data."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping

from projects.alpha_lab.factory import ResearchBoundaryError, canonical_hash, canonical_json
from projects.alpha_lab.factory.canonical import format_datetime


RELATIVE_ROOT = Path("outputs/research/alpha_lab/data_spine")
_SOURCE_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


def output_root(repo_root: Path) -> Path:
    root = Path(repo_root).expanduser().resolve()
    target = (root / RELATIVE_ROOT).resolve()
    target.relative_to((root / "outputs/research/alpha_lab").resolve())
    return target


def require_research_path(path: Path, repo_root: Path) -> Path:
    target = Path(path).expanduser().resolve()
    try:
        target.relative_to(output_root(repo_root))
    except ValueError as exc:
        raise ResearchBoundaryError("data-spine path escaped the research root") from exc
    return target


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_immutable(path: Path, payload: bytes, repo_root: Path) -> Path:
    target = require_research_path(path, repo_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != payload:
            raise FileExistsError("immutable data-spine artifact differs")
        return target
    temporary = target.with_name(".{}.tmp.{}".format(target.name, os.getpid()))
    with temporary.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, target)
    return target


def write_bundle(
    *,
    repo_root: Path,
    source_id: str,
    files: Mapping[str, bytes],
    metadata: Mapping[str, Any],
    retrieved_at: datetime | None = None,
) -> Dict[str, Any]:
    if not _SOURCE_ID.fullmatch(source_id):
        raise ValueError("invalid source_id")
    if not files:
        raise ValueError("bundle must contain at least one file")
    timestamp = retrieved_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("retrieved_at must be timezone-aware")
    records = []
    for name, payload in sorted(files.items()):
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ResearchBoundaryError("invalid bundle member path")
        records.append({"name": relative.as_posix(), "bytes": len(payload), "sha256": sha256_bytes(payload)})
    unsigned = {
        "schema_version": "caerus_alpha_lab_source_bundle_v1",
        "classification": "RESEARCH_ONLY_NON_EXECUTIONAL",
        "source_id": source_id,
        "retrieved_at": format_datetime(timestamp),
        "files": records,
        "metadata": dict(metadata),
        "trading_behavior_changed": False,
        "credentials_persisted": False,
    }
    bundle_hash = canonical_hash(unsigned)
    bundle_id = "{}-{}".format(timestamp.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"), bundle_hash[:12])
    root = output_root(repo_root) / source_id / bundle_id
    paths = {}
    for name, payload in files.items():
        paths[name] = _write_immutable(root / "data" / name, payload, repo_root)
    manifest = dict(unsigned)
    manifest["bundle_id"] = bundle_id
    manifest["bundle_hash"] = canonical_hash(manifest)
    manifest_path = _write_immutable(
        root / "manifest.json", (canonical_json(manifest) + "\n").encode("utf-8"), repo_root
    )
    return {"bundle_id": bundle_id, "bundle_hash": manifest["bundle_hash"], "manifest_path": manifest_path, "paths": paths, "manifest": manifest}


def write_bundle_from_paths(
    *,
    repo_root: Path,
    source_id: str,
    files: Mapping[str, Path],
    metadata: Mapping[str, Any],
    retrieved_at: datetime | None = None,
) -> Dict[str, Any]:
    """Stream existing files into an immutable bundle without loading them in memory."""

    if not _SOURCE_ID.fullmatch(source_id):
        raise ValueError("invalid source_id")
    if not files:
        raise ValueError("bundle must contain at least one file")
    timestamp = retrieved_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("retrieved_at must be timezone-aware")
    records = []
    normalized: Dict[str, Path] = {}
    for name, source in sorted(files.items()):
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ResearchBoundaryError("invalid bundle member path")
        source_path = Path(source).expanduser().resolve()
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        normalized[name] = source_path
        records.append(
            {
                "name": relative.as_posix(),
                "bytes": source_path.stat().st_size,
                "sha256": sha256_file(source_path),
            }
        )
    unsigned = {
        "schema_version": "caerus_alpha_lab_source_bundle_v1",
        "classification": "RESEARCH_ONLY_NON_EXECUTIONAL",
        "source_id": source_id,
        "retrieved_at": format_datetime(timestamp),
        "files": records,
        "metadata": dict(metadata),
        "trading_behavior_changed": False,
        "credentials_persisted": False,
    }
    bundle_hash = canonical_hash(unsigned)
    bundle_id = "{}-{}".format(
        timestamp.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        bundle_hash[:12],
    )
    root = output_root(repo_root) / source_id / bundle_id
    paths = {}
    expected_by_name = {record["name"]: record for record in records}
    for name, source_path in normalized.items():
        target = require_research_path(root / "data" / name, repo_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        expected = expected_by_name[Path(name).as_posix()]
        if target.exists():
            if (
                target.stat().st_size != expected["bytes"]
                or sha256_file(target) != expected["sha256"]
            ):
                raise FileExistsError("immutable data-spine artifact differs")
        else:
            temporary = target.with_name(".{}.tmp.{}".format(target.name, os.getpid()))
            with source_path.open("rb") as source_stream, temporary.open("xb") as target_stream:
                shutil.copyfileobj(source_stream, target_stream, length=1024 * 1024)
                target_stream.flush()
                os.fsync(target_stream.fileno())
            os.replace(temporary, target)
        paths[name] = target
    manifest = dict(unsigned)
    manifest["bundle_id"] = bundle_id
    manifest["bundle_hash"] = canonical_hash(manifest)
    manifest_path = _write_immutable(
        root / "manifest.json",
        (canonical_json(manifest) + "\n").encode("utf-8"),
        repo_root,
    )
    return {
        "bundle_id": bundle_id,
        "bundle_hash": manifest["bundle_hash"],
        "manifest_path": manifest_path,
        "paths": paths,
        "manifest": manifest,
    }


def latest_manifest(repo_root: Path, source_id: str) -> Path | None:
    candidates = sorted((output_root(repo_root) / source_id).glob("*/manifest.json"))
    return candidates[-1] if candidates else None

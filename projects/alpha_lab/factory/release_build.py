"""Create-only Gate A clean-release build, seal, and verification tooling.

The operator surface is standard-library-first.  It accepts externally
prepared, content-addressed source and release-input manifests, verifies every
input before touching the release parent, and never repairs or reuses an
incomplete release directory.  Build mode is explicit and separately binds the
operator authorization to the exact canonical release-input SHA-256.

This module does not authorize GCP, KMS, a ledger write, a deployment, a
scheduler change, holdout access, or trading.  Exact Ubuntu execution remains a
separate Gate A operator action.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import importlib.util
import json
import os
import platform
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence, Tuple

if __package__:
    from .release_dependencies import (
        EXPECTED_TARGET,
        MANIFEST_RELATIVE_PATH,
        ReleaseDependencyError,
        validate_release_dependency_contract,
    )
else:  # Exact direct-file bootstrap: `python -I -S -B /.../release_build.py`.
    _dependency_path = Path(__file__).with_name("release_dependencies.py")
    _dependency_spec = importlib.util.spec_from_file_location(
        "caerus_gate_a_release_dependencies", _dependency_path
    )
    if _dependency_spec is None or _dependency_spec.loader is None:
        raise RuntimeError("cannot load the colocated Gate A dependency validator")
    _dependency_module = importlib.util.module_from_spec(_dependency_spec)
    _dependency_spec.loader.exec_module(_dependency_module)
    EXPECTED_TARGET = _dependency_module.EXPECTED_TARGET
    MANIFEST_RELATIVE_PATH = _dependency_module.MANIFEST_RELATIVE_PATH
    ReleaseDependencyError = _dependency_module.ReleaseDependencyError
    validate_release_dependency_contract = (
        _dependency_module.validate_release_dependency_contract
    )


SOURCE_SCHEMA = "caerus_alpha_lab_clean_release_source_v1"
RELEASE_INPUT_SCHEMA = "caerus_alpha_lab_clean_release_input_v1"
BUILT_RUNTIME_SCHEMA = "caerus_alpha_lab_built_runtime_manifest_v2"
VERIFICATION_RECEIPT_SCHEMA = "caerus_alpha_lab_release_verification_receipt_v2"
READY_SCHEMA = "caerus_alpha_lab_release_ready_v1"
SOURCE_READY_SCHEMA = "caerus_alpha_lab_source_ready_v1"
VERIFY_SCHEMA = "caerus_alpha_lab_sealed_release_verification_v2"
ATLAS_GATE_E_RUNTIME_RECEIPT_SCHEMA = (
    "caerus_alpha_lab_atlas_gate_e_runtime_receipt_v1"
)
FILE_MANIFEST_SCHEMA = "canonical-json-sorted-file-and-symlink-records-v1"
LOCK_RELATIVE_PATH = Path(
    "projects/alpha_lab/release/phase1-cp310-linux-x86_64.lock"
)
WHEEL_MANIFEST_RELEASE_NAME = "phase1-cp310-linux-x86_64-wheel-manifest.json"
BUILDER_RELATIVE_PATH = Path("projects/alpha_lab/factory/release_build.py")
DEPENDENCY_VALIDATOR_RELATIVE_PATH = Path(
    "projects/alpha_lab/factory/release_dependencies.py"
)
LOCK_RELEASE_NAME = "phase1-cp310-linux-x86_64.lock"
BUILT_MANIFEST_NAME = "built_runtime_manifest.json"
RECEIPT_NAME = "verification_receipt.json"
READY_NAME = "READY"
SOURCE_READY_NAME = "SOURCE_READY"
EXPECTED_AUTHORITATIVE_REPO_ROOT = "/mnt/disks/alpha-lab/alpha-lab-project"
EXPECTED_AUTHORITATIVE_DATA_ROOT = (
    "/mnt/disks/alpha-lab/alpha-lab-project/outputs/research/alpha_lab"
)
EXPECTED_CANONICAL_LEDGER = (
    "/mnt/disks/alpha-lab/alpha-lab-project/outputs/research/alpha_lab/"
    "ledger/research_events.v1.jsonl"
)
EXPECTED_PYTEST_PASSED = 355
EXPECTED_DUCKDB_SKIPPED = 2
EXPECTED_DUCKDB_SKIP_NODE_IDS = (
    "projects/alpha_lab/tests/test_data_spine.py::"
    "test_terminal_return_sensitivity_relabels_legacy_proxy_without_certifying_it",
    "projects/alpha_lab/tests/test_data_spine.py::"
    "test_market_materialization_does_not_mislabel_last_daily_return_as_settlement",
)
ALLOWED_BOOTSTRAP_DISTRIBUTIONS = {"pip", "setuptools"}
_METADATA_NAMES = {BUILT_MANIFEST_NAME, RECEIPT_NAME, READY_NAME}
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SHA1 = re.compile(r"[0-9a-f]{40}")
_MODE = re.compile(r"[0-7]{4}")
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_READ_DIR_FLAGS = os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC
_READ_FILE_FLAGS = os.O_RDONLY | _O_NOFOLLOW | _O_CLOEXEC


class ReleaseBuildError(RuntimeError):
    """A Gate A input, construction, or sealed-release invariant failed."""


@dataclass(frozen=True)
class SourceBundle:
    archive_path: Path
    archive_bytes: int
    archive_sha256: str
    archive_records: Tuple[Mapping[str, Any], ...]
    archive_directories: Tuple[Mapping[str, Any], ...]
    source_manifest: Mapping[str, Any]
    source_manifest_bytes: bytes
    source_manifest_sha256: str
    file_manifest: Tuple[Mapping[str, Any], ...]
    file_manifest_bytes: bytes
    file_manifest_sha256: str


@dataclass(frozen=True)
class ReleaseInputs:
    source: SourceBundle
    release_input: Mapping[str, Any]
    release_input_bytes: bytes
    release_input_sha256: str
    wheelhouse: Path
    release_parent: Path
    repo_root: Path
    lock_bytes: bytes
    wheel_manifest_bytes: bytes
    builder_origin: Mapping[str, Any]


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _strict_json(value: bytes, *, label: str) -> Any:
    def object_pairs(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ReleaseBuildError(f"duplicate JSON key in {label}: {key}")
            result[key] = item
        return result

    try:
        parsed = json.loads(
            value.decode("utf-8"),
            object_pairs_hook=object_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ReleaseBuildError(f"non-finite JSON value in {label}: {token}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseBuildError(f"invalid strict JSON in {label}") from exc
    return parsed


def _strict_canonical_json(value: bytes, *, label: str, object_required: bool) -> Any:
    parsed = _strict_json(value, label=label)
    if object_required and not isinstance(parsed, dict):
        raise ReleaseBuildError(f"{label} must be a JSON object")
    if not object_required and not isinstance(parsed, list):
        raise ReleaseBuildError(f"{label} must be a JSON array")
    if value != _canonical_bytes(parsed):
        raise ReleaseBuildError(f"{label} is not exact canonical JSON")
    return parsed


def _normalize_absolute_path(value: str, *, label: str) -> Path:
    if not isinstance(value, str) or not value.startswith("/") or "\x00" in value:
        raise ReleaseBuildError(f"{label} must be an absolute path")
    normalized = os.path.normpath(value)
    if normalized != value or value == "/":
        raise ReleaseBuildError(f"{label} is not a canonical absolute path")
    return Path(value)


def _safe_relative_parts(value: str, *, label: str) -> Tuple[str, ...]:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise ReleaseBuildError(f"{label} is not a safe relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or value.endswith("/") or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        raise ReleaseBuildError(f"{label} is not a canonical relative POSIX path")
    if str(path) != value:
        raise ReleaseBuildError(f"{label} contains path normalization drift")
    return tuple(path.parts)


def _safe_symlink_target(path: str, target: str) -> None:
    if not isinstance(target, str) or not target or "\x00" in target or "\\" in target:
        raise ReleaseBuildError(f"unsafe symlink target for {path}")
    link = PurePosixPath(target)
    if link.is_absolute():
        raise ReleaseBuildError(f"absolute symlink target is forbidden: {path}")
    depth = len(PurePosixPath(path).parent.parts)
    for part in link.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            depth -= 1
            if depth < 0:
                raise ReleaseBuildError(f"symlink target escapes release: {path}")
        else:
            depth += 1


def _open_absolute_directory(
    path: Path, *, create_missing: bool = False, create_mode: int = 0o755
) -> int:
    absolute = _normalize_absolute_path(str(path), label="directory")
    descriptor = os.open("/", _READ_DIR_FLAGS)
    try:
        for part in absolute.parts[1:]:
            try:
                child = os.open(part, _READ_DIR_FLAGS, dir_fd=descriptor)
            except FileNotFoundError:
                if not create_missing:
                    raise
                os.mkdir(part, create_mode, dir_fd=descriptor)
                os.fsync(descriptor)
                child = os.open(part, _READ_DIR_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _check_absolute_path_without_mutation(path: Path) -> None:
    absolute = _normalize_absolute_path(str(path), label="release parent")
    descriptor = os.open("/", _READ_DIR_FLAGS)
    try:
        for part in absolute.parts[1:]:
            try:
                child = os.open(part, _READ_DIR_FLAGS, dir_fd=descriptor)
            except FileNotFoundError:
                return
            os.close(descriptor)
            descriptor = child
    finally:
        os.close(descriptor)


def _open_regular_path(path: Path) -> Tuple[int, os.stat_result]:
    absolute = _normalize_absolute_path(str(path), label="input file")
    parent = _open_absolute_directory(absolute.parent)
    try:
        descriptor = os.open(absolute.name, _READ_FILE_FLAGS, dir_fd=parent)
    finally:
        os.close(parent)
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        os.close(descriptor)
        raise ReleaseBuildError(f"input is not a single-link regular file: {path}")
    return descriptor, metadata


def _read_regular_path(path: Path) -> bytes:
    descriptor, before = _open_regular_path(path)
    try:
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
    ):
        raise ReleaseBuildError(f"input changed while being read: {path}")
    return b"".join(chunks)


def _hash_regular_path(path: Path) -> Tuple[int, str]:
    descriptor, before = _open_regular_path(path)
    digest = hashlib.sha256()
    total = 0
    try:
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if total != before.st_size or (before.st_dev, before.st_ino, before.st_mtime_ns) != (
        after.st_dev, after.st_ino, after.st_mtime_ns
    ):
        raise ReleaseBuildError(f"input changed while hashing: {path}")
    return total, digest.hexdigest()


def _validated_file_record(record: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(record, Mapping):
        raise ReleaseBuildError("file-manifest record must be an object")
    kind = record.get("type")
    common = {"path", "type", "mode"}
    if kind == "file":
        if set(record) != common | {"bytes", "sha256"}:
            raise ReleaseBuildError("file-manifest file record schema is invalid")
        if not isinstance(record["bytes"], int) or record["bytes"] < 0:
            raise ReleaseBuildError("file-manifest byte count is invalid")
        if not _SHA256.fullmatch(str(record["sha256"])):
            raise ReleaseBuildError("file-manifest content hash is invalid")
    elif kind == "symlink":
        # The exact Alpha source contract has no links.  The historical schema
        # name is retained for compatibility with the already reviewed source
        # manifest format, but a Gate A source record is deliberately narrower.
        raise ReleaseBuildError("Gate A source file manifest forbids symlinks")
    else:
        raise ReleaseBuildError("Gate A source file manifest permits only files")
    _safe_relative_parts(str(record["path"]), label="file-manifest path")
    if not _MODE.fullmatch(str(record["mode"])):
        raise ReleaseBuildError("file-manifest mode is invalid")
    return dict(record)


def _inspect_tar(
    archive_path: Path, *, expected_commit_sha: Optional[str] = None,
    expected_tree_oid_sha1: Optional[str] = None,
) -> Tuple[Tuple[Mapping[str, Any], ...], Tuple[Mapping[str, Any], ...]]:
    descriptor, _metadata = _open_regular_path(archive_path)
    records: list[Mapping[str, Any]] = []
    directories: list[Mapping[str, Any]] = []
    names: set[str] = set()
    git_files: Dict[Tuple[str, ...], Tuple[str, bytes]] = {}
    try:
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            with tarfile.open(fileobj=stream, mode="r:") as archive:
                if expected_commit_sha is not None and archive.pax_headers.get(
                    "comment"
                ) != expected_commit_sha:
                    raise ReleaseBuildError("git-archive PAX commit identity drift")
                for member in archive:
                    name = member.name
                    _safe_relative_parts(name, label="tar member")
                    if getattr(member, "sparse", None) is not None or any(
                        str(key).startswith("GNU.sparse") for key in member.pax_headers
                    ):
                        raise ReleaseBuildError(f"sparse tar member is forbidden: {name}")
                    if name in names:
                        raise ReleaseBuildError(f"duplicate tar member: {name}")
                    names.add(name)
                    mode = format(member.mode & 0o7777, "04o")
                    if member.isdir():
                        if member.size != 0:
                            raise ReleaseBuildError(f"tar directory has content bytes: {name}")
                        directories.append(
                            {"path": name, "type": "directory", "mode": mode}
                        )
                        continue
                    if member.isfile():
                        source = archive.extractfile(member)
                        if source is None:
                            raise ReleaseBuildError(f"tar file has no content: {name}")
                        digest = hashlib.sha256()
                        git_blob = hashlib.sha1(
                            f"blob {member.size}\0".encode("ascii")
                        )
                        total = 0
                        while True:
                            chunk = source.read(1024 * 1024)
                            if not chunk:
                                break
                            digest.update(chunk)
                            git_blob.update(chunk)
                            total += len(chunk)
                        if total != member.size:
                            raise ReleaseBuildError(f"tar member size changed: {name}")
                        records.append(
                            {
                                "path": name,
                                "type": "file",
                                "mode": mode,
                                "bytes": total,
                                "sha256": digest.hexdigest(),
                            }
                        )
                        git_mode = "100755" if member.mode & 0o111 else "100644"
                        git_files[PurePosixPath(name).parts] = (
                            git_mode, git_blob.digest()
                        )
                    else:
                        raise ReleaseBuildError(
                            f"unsupported tar member type for {name}: {member.type!r}"
                        )
    except (tarfile.TarError, OSError) as exc:
        raise ReleaseBuildError(f"cannot inspect source tar: {archive_path}") from exc
    finally:
        os.close(descriptor)
    leaf_paths = {str(record["path"]) for record in records}
    for directory in directories:
        parts = PurePosixPath(str(directory["path"])).parts
        for index in range(1, len(parts)):
            parent = str(PurePosixPath(*parts[:index]))
            if parent in leaf_paths:
                raise ReleaseBuildError(f"file is used as a directory: {parent}")
    if expected_tree_oid_sha1 is not None:
        tree: Dict[str, Any] = {}
        for parts, leaf in git_files.items():
            node = tree
            for part in parts[:-1]:
                existing = node.setdefault(part, {})
                if not isinstance(existing, dict):
                    raise ReleaseBuildError(f"Git file/tree collision: {'/'.join(parts)}")
                node = existing
            if parts[-1] in node:
                raise ReleaseBuildError(f"duplicate Git tree path: {'/'.join(parts)}")
            node[parts[-1]] = leaf

        def tree_oid(node: Mapping[str, Any]) -> bytes:
            entries = []
            for name, value in node.items():
                is_tree = isinstance(value, dict)
                entries.append((name.encode("utf-8") + (b"/" if is_tree else b""), name, value))
            body = bytearray()
            for _sort_key, name, value in sorted(entries, key=lambda item: item[0]):
                if isinstance(value, dict):
                    mode, digest_value = "40000", tree_oid(value)
                else:
                    mode, digest_value = value
                body.extend(mode.encode("ascii") + b" " + name.encode("utf-8") + b"\0")
                body.extend(digest_value)
            return hashlib.sha1(
                f"tree {len(body)}\0".encode("ascii") + bytes(body)
            ).digest()

        if tree_oid(tree).hex() != expected_tree_oid_sha1:
            raise ReleaseBuildError("source Git tree OID drift")
    return tuple(records), tuple(directories)


def _expected_directories(records: Iterable[Mapping[str, Any]]) -> set[str]:
    result: set[str] = set()
    leaf_paths = {str(record["path"]) for record in records}
    for path in leaf_paths:
        parts = PurePosixPath(path).parts
        for index in range(1, len(parts)):
            parent = str(PurePosixPath(*parts[:index]))
            if parent in leaf_paths:
                raise ReleaseBuildError(f"file/symlink is used as a directory: {parent}")
            result.add(parent)
    return result


def verify_source_bundle(
    *, archive_path: Path, source_manifest_path: Path, file_manifest_path: Path
) -> SourceBundle:
    source_bytes = _read_regular_path(source_manifest_path)
    source_manifest = _strict_canonical_json(
        source_bytes, label="source manifest", object_required=True
    )
    expected_source_fields = {
        "schema_version", "archive_bytes", "archive_format", "archive_sha256",
        "commit_sha", "tree_oid_sha1", "file_manifest_member_count",
        "file_manifest_schema", "file_manifest_sha256",
    }
    if set(source_manifest) != expected_source_fields:
        raise ReleaseBuildError("source manifest schema is invalid")
    if source_manifest.get("schema_version") != SOURCE_SCHEMA:
        raise ReleaseBuildError("source manifest version is invalid")
    if source_manifest.get("archive_format") != "git-archive-tar":
        raise ReleaseBuildError("source archive format is invalid")
    if source_manifest.get("file_manifest_schema") != FILE_MANIFEST_SCHEMA:
        raise ReleaseBuildError("source file-manifest schema is invalid")
    if not _SHA1.fullmatch(str(source_manifest.get("commit_sha", ""))) or not _SHA1.fullmatch(
        str(source_manifest.get("tree_oid_sha1", ""))
    ):
        raise ReleaseBuildError("source commit/tree identity is invalid")
    if not _SHA256.fullmatch(str(source_manifest.get("archive_sha256", ""))) or not _SHA256.fullmatch(
        str(source_manifest.get("file_manifest_sha256", ""))
    ):
        raise ReleaseBuildError("source hash identity is invalid")

    file_bytes = _read_regular_path(file_manifest_path)
    raw_records = _strict_canonical_json(
        file_bytes, label="file manifest", object_required=False
    )
    records = tuple(_validated_file_record(record) for record in raw_records)
    paths = [str(record["path"]) for record in records]
    if len(paths) != len(set(paths)):
        raise ReleaseBuildError("file manifest has duplicate paths")
    if len(records) != source_manifest["file_manifest_member_count"]:
        raise ReleaseBuildError("file-manifest member count drift")
    file_hash = _sha256(file_bytes)
    if file_hash != source_manifest["file_manifest_sha256"]:
        raise ReleaseBuildError("file-manifest hash drift")

    archive_size, archive_hash = _hash_regular_path(archive_path)
    if (
        archive_size != source_manifest["archive_bytes"]
        or archive_hash != source_manifest["archive_sha256"]
    ):
        raise ReleaseBuildError("source archive byte/hash drift")
    archive_records, directories = _inspect_tar(
        archive_path,
        expected_commit_sha=str(source_manifest["commit_sha"]),
        expected_tree_oid_sha1=str(source_manifest["tree_oid_sha1"]),
    )
    if archive_records != records:
        raise ReleaseBuildError("source tar does not exactly match file manifest")
    expected_directories = _expected_directories(records)
    directory_names = [str(record["path"]) for record in directories]
    if set(directory_names) != expected_directories or len(directory_names) != len(
        expected_directories
    ):
        raise ReleaseBuildError("source tar directory census is not exact")
    return SourceBundle(
        archive_path=archive_path,
        archive_bytes=archive_size,
        archive_sha256=archive_hash,
        archive_records=archive_records,
        archive_directories=directories,
        source_manifest=source_manifest,
        source_manifest_bytes=source_bytes,
        source_manifest_sha256=_sha256(source_bytes),
        file_manifest=records,
        file_manifest_bytes=file_bytes,
        file_manifest_sha256=file_hash,
    )


def _validate_wheelhouse_nofollow(wheelhouse: Path, expected: Mapping[str, Mapping[str, Any]]) -> None:
    descriptor = _open_absolute_directory(wheelhouse)
    try:
        names = os.listdir(descriptor)
        if set(names) != set(expected):
            raise ReleaseBuildError("wheelhouse has extra or missing names")
        for name in names:
            try:
                file_descriptor = os.open(name, _READ_FILE_FLAGS, dir_fd=descriptor)
            except OSError as exc:
                raise ReleaseBuildError(
                    f"wheelhouse entry is not a single-link regular file: {name}"
                ) from exc
            try:
                before = os.fstat(file_descriptor)
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                    raise ReleaseBuildError(
                        f"wheelhouse entry is not a single-link regular file: {name}"
                    )
                digest = hashlib.sha256()
                total = 0
                while True:
                    chunk = os.read(file_descriptor, 1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    total += len(chunk)
                after = os.fstat(file_descriptor)
            finally:
                os.close(file_descriptor)
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
            ):
                raise ReleaseBuildError(f"wheelhouse entry changed while hashing: {name}")
            record = expected[name]
            if total != record["bytes"] or digest.hexdigest() != record["sha256"]:
                raise ReleaseBuildError(f"wheelhouse byte/hash drift: {name}")
    finally:
        os.close(descriptor)


def _validate_release_input_shape(
    value: Mapping[str, Any], *, source: SourceBundle, release_parent: Path,
    lock_bytes: bytes, wheel_manifest_bytes: bytes, wheel_manifest: Mapping[str, Any]
) -> None:
    if set(value) != {
        "schema_version", "construction_policy", "dependencies", "roots",
        "source", "target", "verification_contract",
    } or value.get("schema_version") != RELEASE_INPUT_SCHEMA:
        raise ReleaseBuildError("release input manifest schema is invalid")
    if value.get("target") != EXPECTED_TARGET:
        raise ReleaseBuildError("release input target drift")
    expected_policy = {
        "activation_pointer_created": False,
        "create_only": True,
        "isolated_venv": True,
        "network_dependency_resolution": False,
        "require_hashes": True,
        "scheduler_or_service_mutation": "NONE",
    }
    if value.get("construction_policy") != expected_policy:
        raise ReleaseBuildError("release construction policy drift")
    verification = value.get("verification_contract")
    if verification != {
        "dependency_validator_schema": "caerus_alpha_lab_phase1_dependency_validation_v1",
        "optional_duckdb_skipped": EXPECTED_DUCKDB_SKIPPED,
        "pytest_passed": EXPECTED_PYTEST_PASSED,
    }:
        raise ReleaseBuildError("release verification contract drift")
    roots = value.get("roots")
    if roots != {
        "authoritative_data_root": EXPECTED_AUTHORITATIVE_DATA_ROOT,
        "authoritative_repo_root": EXPECTED_AUTHORITATIVE_REPO_ROOT,
        "canonical_ledger_path": EXPECTED_CANONICAL_LEDGER,
        "release_parent": str(release_parent),
    }:
        raise ReleaseBuildError("release root contract drift")
    source_record = value.get("source")
    if source_record != {
        "source_manifest": source.source_manifest,
        "source_manifest_sha256": source.source_manifest_sha256,
    }:
        raise ReleaseBuildError("release source identity drift")
    dependencies = value.get("dependencies")
    if not isinstance(dependencies, Mapping) or set(dependencies) != {
        "lock", "wheel_bytes_total", "wheel_count", "wheel_manifest", "wheels"
    }:
        raise ReleaseBuildError("release dependency input schema is invalid")
    lock = wheel_manifest.get("lock")
    if dependencies.get("lock") != {
        "bytes": len(lock_bytes),
        "path": str(LOCK_RELATIVE_PATH),
        "requirement_count": lock["requirement_count"],
        "sha256": _sha256(lock_bytes),
    }:
        raise ReleaseBuildError("release lock input drift")
    wheel_manifest_record = dependencies.get("wheel_manifest")
    if wheel_manifest_record != {
        "bytes": len(wheel_manifest_bytes),
        "path": str(MANIFEST_RELATIVE_PATH),
        "schema_version": wheel_manifest["schema_version"],
        "sha256": _sha256(wheel_manifest_bytes),
    }:
        raise ReleaseBuildError("release wheel-manifest input drift")
    expected_wheels = [
        {
            "bytes": record["bytes"],
            "filename": record["filename"],
            "sha256": record["sha256"],
        }
        for record in wheel_manifest["wheels"]
    ]
    if dependencies.get("wheels") != expected_wheels:
        raise ReleaseBuildError("release wheel list drift")
    if dependencies.get("wheel_count") != len(expected_wheels) or dependencies.get(
        "wheel_bytes_total"
    ) != sum(record["bytes"] for record in expected_wheels):
        raise ReleaseBuildError("release wheel census drift")


def _verify_builder_origin(
    *, repo_root: Path, source: SourceBundle, release_parent: Path
) -> Mapping[str, Any]:
    source_records = {
        str(record["path"]): record for record in source.file_manifest
    }
    actual_module_paths = {
        str(BUILDER_RELATIVE_PATH): _normalize_absolute_path(
            str(Path(__file__).absolute()), label="loaded release builder"
        ),
        str(DEPENDENCY_VALIDATOR_RELATIVE_PATH): _normalize_absolute_path(
            str(Path(validate_release_dependency_contract.__code__.co_filename).absolute()),
            label="loaded dependency validator",
        ),
    }
    modules = []
    for relative, loaded_path in actual_module_paths.items():
        expected_path = repo_root / relative
        if loaded_path != expected_path:
            raise ReleaseBuildError(
                f"loaded Gate A module is outside the reviewed source root: {relative}"
            )
        expected = source_records.get(relative)
        if expected is None or expected.get("type") != "file":
            raise ReleaseBuildError(
                f"loaded Gate A module is absent from the source manifest: {relative}"
            )
        value = _read_regular_path(loaded_path)
        if len(value) != expected.get("bytes") or _sha256(value) != expected.get(
            "sha256"
        ):
            raise ReleaseBuildError(
                f"loaded Gate A module differs from the reviewed source archive: {relative}"
            )
        modules.append(
            {
                "bytes": len(value),
                "path": str(loaded_path),
                "sha256": _sha256(value),
                "source_relative_path": relative,
            }
        )
    expected_bootstrap_root = (
        release_parent / "bootstrap/sha256" / source.archive_sha256 / "app"
    )
    return {
        "content_addressed": repo_root == expected_bootstrap_root,
        "expected_bootstrap_root": str(expected_bootstrap_root),
        "modules": modules,
        "repo_root": str(repo_root),
        "source_archive_sha256": source.archive_sha256,
    }


def _verify_bound_builder_origin(
    origin: Any, *, source: SourceBundle, release_parent: Path
) -> None:
    if not isinstance(origin, Mapping) or set(origin) != {
        "content_addressed", "expected_bootstrap_root", "modules", "repo_root",
        "source_archive_sha256",
    }:
        raise ReleaseBuildError("bound builder-origin schema drift")
    expected_root = release_parent / "bootstrap/sha256" / source.archive_sha256 / "app"
    if (
        origin.get("content_addressed") is not True
        or origin.get("expected_bootstrap_root") != str(expected_root)
        or origin.get("repo_root") != str(expected_root)
        or origin.get("source_archive_sha256") != source.archive_sha256
    ):
        raise ReleaseBuildError("bound builder is not the reviewed content-addressed source")
    source_records = {record["path"]: record for record in source.file_manifest}
    expected_modules = []
    for relative in (str(BUILDER_RELATIVE_PATH), str(DEPENDENCY_VALIDATOR_RELATIVE_PATH)):
        record = source_records.get(relative)
        if record is None or record.get("type") != "file":
            raise ReleaseBuildError(f"bound builder module is absent from source: {relative}")
        expected_modules.append(
            {
                "bytes": record["bytes"],
                "path": str(expected_root / relative),
                "sha256": record["sha256"],
                "source_relative_path": relative,
            }
        )
    if origin.get("modules") != expected_modules:
        raise ReleaseBuildError("bound builder module identities drift from source")
    for expected in expected_modules:
        loaded = _read_regular_path(Path(str(expected["path"])))
        if len(loaded) != expected["bytes"] or _sha256(loaded) != expected["sha256"]:
            raise ReleaseBuildError("content-addressed builder bytes drift from source")


def _verify_executing_builder_is_bound(origin: Mapping[str, Any]) -> None:
    modules = origin.get("modules")
    if not isinstance(modules, list):
        raise ReleaseBuildError("executing builder origin has no module census")
    expected = {
        str(record.get("source_relative_path")): record for record in modules
        if isinstance(record, Mapping)
    }
    actual = {
        str(BUILDER_RELATIVE_PATH): Path(__file__).absolute(),
        str(DEPENDENCY_VALIDATOR_RELATIVE_PATH): Path(
            validate_release_dependency_contract.__code__.co_filename
        ).absolute(),
    }
    for relative, path in actual.items():
        record = expected.get(relative)
        if record is None or str(path) != record.get("path"):
            raise ReleaseBuildError(
                f"executing Gate A module is outside the bound bootstrap: {relative}"
            )
        value = _read_regular_path(path)
        if len(value) != record.get("bytes") or _sha256(value) != record.get("sha256"):
            raise ReleaseBuildError(f"executing Gate A module byte drift: {relative}")


def verify_release_inputs(
    *, repo_root: Path, source_archive: Path, source_manifest: Path,
    file_manifest: Path, wheelhouse: Path, release_input_manifest: Path,
    release_parent: Path, verify_builder_origin: bool = True,
) -> ReleaseInputs:
    repo_root = _normalize_absolute_path(str(repo_root), label="repo root")
    wheelhouse = _normalize_absolute_path(str(wheelhouse), label="wheelhouse")
    release_parent = _normalize_absolute_path(str(release_parent), label="release parent")
    _check_absolute_path_without_mutation(release_parent)
    source = verify_source_bundle(
        archive_path=source_archive,
        source_manifest_path=source_manifest,
        file_manifest_path=file_manifest,
    )
    lock_bytes = _read_regular_path(repo_root / LOCK_RELATIVE_PATH)
    wheel_manifest_bytes = _read_regular_path(repo_root / MANIFEST_RELATIVE_PATH)
    wheel_manifest = _strict_json(wheel_manifest_bytes, label="dependency wheel manifest")
    if not isinstance(wheel_manifest, Mapping):
        raise ReleaseBuildError("dependency wheel manifest must be an object")
    expected_wheels = {record["filename"]: record for record in wheel_manifest["wheels"]}
    _validate_wheelhouse_nofollow(wheelhouse, expected_wheels)
    try:
        dependency_result = validate_release_dependency_contract(
            repo_root, wheelhouse=wheelhouse
        )
    except ReleaseDependencyError as exc:
        raise ReleaseBuildError(f"dependency release validation failed: {exc}") from exc
    if dependency_result.get("status") != "PASS" or not dependency_result.get(
        "wheelhouse_verified"
    ):
        raise ReleaseBuildError("dependency wheelhouse did not verify")
    release_bytes = _read_regular_path(release_input_manifest)
    release_value = _strict_canonical_json(
        release_bytes, label="release input manifest", object_required=True
    )
    _validate_release_input_shape(
        release_value,
        source=source,
        release_parent=release_parent,
        lock_bytes=lock_bytes,
        wheel_manifest_bytes=wheel_manifest_bytes,
        wheel_manifest=wheel_manifest,
    )
    builder_origin = (
        _verify_builder_origin(
            repo_root=repo_root, source=source, release_parent=release_parent
        )
        if verify_builder_origin
        else {}
    )
    archive_records = {str(record["path"]): record for record in source.file_manifest}
    for relative, expected_hash in (
        (str(LOCK_RELATIVE_PATH), _sha256(lock_bytes)),
        (str(MANIFEST_RELATIVE_PATH), _sha256(wheel_manifest_bytes)),
    ):
        record = archive_records.get(relative)
        if record is None or record.get("type") != "file" or record.get(
            "sha256"
        ) != expected_hash:
            raise ReleaseBuildError(f"source archive dependency artifact drift: {relative}")
    return ReleaseInputs(
        source=source,
        release_input=release_value,
        release_input_bytes=release_bytes,
        release_input_sha256=_sha256(release_bytes),
        wheelhouse=wheelhouse,
        release_parent=release_parent,
        repo_root=repo_root,
        lock_bytes=lock_bytes,
        wheel_manifest_bytes=wheel_manifest_bytes,
        builder_origin=builder_origin,
    )


def _mkdir_exclusive(parent_fd: int, name: str, mode: int = 0o700) -> int:
    if "/" in name or name in {"", ".", ".."}:
        raise ReleaseBuildError(f"unsafe directory component: {name}")
    try:
        os.mkdir(name, mode, dir_fd=parent_fd)
    except FileExistsError as exc:
        raise ReleaseBuildError(f"content-address collision: {name}") from exc
    os.fsync(parent_fd)
    return os.open(name, _READ_DIR_FLAGS, dir_fd=parent_fd)


def _open_or_create_child(parent_fd: int, name: str, mode: int = 0o755) -> int:
    try:
        return os.open(name, _READ_DIR_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        os.mkdir(name, mode, dir_fd=parent_fd)
        os.fsync(parent_fd)
        return os.open(name, _READ_DIR_FLAGS, dir_fd=parent_fd)


def _write_exclusive(parent_fd: int, name: str, value: bytes, mode: int = 0o600) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_CLOEXEC
    descriptor = os.open(name, flags, mode, dir_fd=parent_fd)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ReleaseBuildError(f"new release file is not single-link regular: {name}")
        offset = 0
        while offset < len(value):
            offset += os.write(descriptor, value[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.fsync(parent_fd)


def _copy_exclusive(parent_fd: int, name: str, source: Path, mode: int = 0o600) -> None:
    source_fd, before = _open_regular_path(source)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_CLOEXEC
    destination = os.open(name, flags, mode, dir_fd=parent_fd)
    total = 0
    digest = hashlib.sha256()
    try:
        destination_stat = os.fstat(destination)
        if not stat.S_ISREG(destination_stat.st_mode) or destination_stat.st_nlink != 1:
            raise ReleaseBuildError(
                f"new copied release file is not single-link regular: {name}"
            )
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            written = 0
            while written < len(chunk):
                written += os.write(destination, chunk[written:])
            total += len(chunk)
        os.fsync(destination)
        after = os.fstat(source_fd)
    finally:
        os.close(source_fd)
        os.close(destination)
    if total != before.st_size or (before.st_dev, before.st_ino, before.st_mtime_ns) != (
        after.st_dev, after.st_ino, after.st_mtime_ns
    ):
        raise ReleaseBuildError(f"source changed during exclusive copy: {source}")
    os.fsync(parent_fd)


def _ensure_directory_at(root_fd: int, parts: Sequence[str], mode: int = 0o700) -> int:
    descriptor = os.dup(root_fd)
    try:
        for part in parts:
            try:
                child = os.open(part, _READ_DIR_FLAGS, dir_fd=descriptor)
            except FileNotFoundError:
                os.mkdir(part, mode, dir_fd=descriptor)
                os.fsync(descriptor)
                child = os.open(part, _READ_DIR_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _extract_tar_exact(
    source: SourceBundle, app_fd: int, *, archive_path: Optional[Path] = None
) -> None:
    expected = {str(record["path"]): record for record in source.file_manifest}
    selected_archive = archive_path or source.archive_path
    archive_fd, archive_before = _open_regular_path(selected_archive)
    seen: list[str] = []
    try:
        with os.fdopen(os.dup(archive_fd), "rb") as stream:
            with tarfile.open(fileobj=stream, mode="r:") as archive:
                for member in archive:
                    name = member.name
                    parts = _safe_relative_parts(name, label="tar extraction member")
                    if getattr(member, "sparse", None) is not None or any(
                        str(key).startswith("GNU.sparse") for key in member.pax_headers
                    ):
                        raise ReleaseBuildError(f"sparse tar member is forbidden: {name}")
                    if member.isdir():
                        directory_fd = _ensure_directory_at(app_fd, parts)
                        try:
                            os.fchmod(directory_fd, member.mode & 0o777)
                            os.fsync(directory_fd)
                        finally:
                            os.close(directory_fd)
                        continue
                    record = expected.get(name)
                    if record is None:
                        raise ReleaseBuildError(f"tar extraction has unmanifested member: {name}")
                    parent_fd = _ensure_directory_at(app_fd, parts[:-1])
                    try:
                        if member.isfile() and record["type"] == "file":
                            descriptor = os.open(
                                parts[-1],
                                os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_CLOEXEC,
                                int(str(record["mode"]), 8) & 0o777,
                                dir_fd=parent_fd,
                            )
                            digest = hashlib.sha256()
                            total = 0
                            try:
                                destination_stat = os.fstat(descriptor)
                                if (
                                    not stat.S_ISREG(destination_stat.st_mode)
                                    or destination_stat.st_nlink != 1
                                ):
                                    raise ReleaseBuildError(
                                        f"new extracted file is not single-link regular: {name}"
                                    )
                                source_stream = archive.extractfile(member)
                                if source_stream is None:
                                    raise ReleaseBuildError(f"missing tar content: {name}")
                                while True:
                                    chunk = source_stream.read(1024 * 1024)
                                    if not chunk:
                                        break
                                    digest.update(chunk)
                                    written = 0
                                    while written < len(chunk):
                                        written += os.write(descriptor, chunk[written:])
                                    total += len(chunk)
                                os.fchmod(descriptor, int(str(record["mode"]), 8))
                                os.fsync(descriptor)
                            finally:
                                os.close(descriptor)
                            if total != record["bytes"] or digest.hexdigest() != record["sha256"]:
                                raise ReleaseBuildError(f"extracted tar content drift: {name}")
                        else:
                            raise ReleaseBuildError(f"tar type drift during extraction: {name}")
                        os.fsync(parent_fd)
                    finally:
                        os.close(parent_fd)
                    seen.append(name)
        archive_after = os.fstat(archive_fd)
    finally:
        os.close(archive_fd)
    if (
        archive_before.st_dev,
        archive_before.st_ino,
        archive_before.st_size,
        archive_before.st_mtime_ns,
    ) != (
        archive_after.st_dev,
        archive_after.st_ino,
        archive_after.st_size,
        archive_after.st_mtime_ns,
    ):
        raise ReleaseBuildError("source tar changed during exact extraction")
    if seen != [str(record["path"]) for record in source.file_manifest]:
        raise ReleaseBuildError("extracted member order/census drift")
    actual = _scan_tree_fd(app_fd)
    actual_files = tuple(record for record in actual if record["type"] == "file")
    actual_directories = tuple(
        record for record in actual if record["type"] == "directory"
    )
    if actual_files != tuple(sorted(source.file_manifest, key=lambda item: item["path"])):
        raise ReleaseBuildError("extracted application file identity is not exact")
    if actual_directories != tuple(
        sorted(source.archive_directories, key=lambda item: item["path"])
    ):
        raise ReleaseBuildError("extracted application directory identity is not exact")


def _directory_names(fd: int) -> list[str]:
    return sorted(os.listdir(fd))


def _hash_open_file(parent_fd: int, name: str) -> Tuple[int, str, os.stat_result]:
    descriptor = os.open(name, _READ_FILE_FLAGS, dir_fd=parent_fd)
    digest = hashlib.sha256()
    total = 0
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ReleaseBuildError(f"release entry is not regular: {name}")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
    ):
        raise ReleaseBuildError(f"release file changed while hashing: {name}")
    return total, digest.hexdigest(), before


def _scan_tree_fd(
    root_fd: int, *, prefix: Tuple[str, ...] = (), exclude_root_names: set[str] | None = None
) -> list[Dict[str, Any]]:
    records: list[Dict[str, Any]] = []
    excluded = exclude_root_names or set()
    for name in _directory_names(root_fd):
        if not prefix and name in excluded:
            continue
        metadata = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        relative = "/".join(prefix + (name,))
        if stat.S_ISDIR(metadata.st_mode):
            child = os.open(name, _READ_DIR_FLAGS, dir_fd=root_fd)
            try:
                opened = os.fstat(child)
                if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                    raise ReleaseBuildError(f"release directory raced during scan: {relative}")
                records.append(
                    {
                        "path": relative,
                        "type": "directory",
                        "mode": format(stat.S_IMODE(opened.st_mode), "04o"),
                    }
                )
                records.extend(
                    _scan_tree_fd(child, prefix=prefix + (name,), exclude_root_names=set())
                )
            finally:
                os.close(child)
        elif stat.S_ISREG(metadata.st_mode):
            total, digest, opened = _hash_open_file(root_fd, name)
            if opened.st_nlink != 1:
                raise ReleaseBuildError(f"release file is hard-linked: {relative}")
            records.append(
                {
                    "path": relative,
                    "type": "file",
                    "mode": format(stat.S_IMODE(opened.st_mode), "04o"),
                    "bytes": total,
                    "sha256": digest,
                }
            )
        elif stat.S_ISLNK(metadata.st_mode):
            target = os.readlink(name, dir_fd=root_fd)
            after = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
            if (metadata.st_dev, metadata.st_ino, metadata.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_mtime_ns
            ):
                raise ReleaseBuildError(f"release symlink raced during scan: {relative}")
            _safe_symlink_target(relative, target)
            records.append(
                {
                    "path": relative,
                    "type": "symlink",
                    "mode": format(stat.S_IMODE(after.st_mode), "04o"),
                    "target": target,
                }
            )
        else:
            raise ReleaseBuildError(f"unsupported release filesystem entry: {relative}")
    return sorted(records, key=lambda record: str(record["path"]))


def _scan_tree(path: Path, *, exclude_metadata: bool = False) -> list[Dict[str, Any]]:
    descriptor = _open_absolute_directory(path)
    try:
        return _scan_tree_fd(
            descriptor,
            exclude_root_names=_METADATA_NAMES if exclude_metadata else set(),
        )
    finally:
        os.close(descriptor)


def _seal_tree_fd(root_fd: int) -> None:
    for name in _directory_names(root_fd):
        metadata = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        if stat.S_ISDIR(metadata.st_mode):
            child = os.open(name, _READ_DIR_FLAGS, dir_fd=root_fd)
            try:
                _seal_tree_fd(child)
                os.fchmod(child, 0o555)
                os.fsync(child)
            finally:
                os.close(child)
        elif stat.S_ISREG(metadata.st_mode):
            descriptor = os.open(name, _READ_FILE_FLAGS, dir_fd=root_fd)
            try:
                executable = bool(stat.S_IMODE(metadata.st_mode) & 0o111)
                os.fchmod(descriptor, 0o555 if executable else 0o444)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        elif not stat.S_ISLNK(metadata.st_mode):
            raise ReleaseBuildError(f"cannot seal unsupported entry: {name}")
    os.fsync(root_fd)


def _assert_sealed_records(records: Iterable[Mapping[str, Any]]) -> None:
    for record in records:
        if record["type"] == "directory" and record["mode"] != "0555":
            raise ReleaseBuildError(f"release directory is mutable: {record['path']}")
        if record["type"] == "file" and record["mode"] not in {"0444", "0555"}:
            raise ReleaseBuildError(f"release file is mutable: {record['path']}")


def _sanitized_environment(*, temporary_root: Path, venv_bin: Optional[Path] = None) -> Dict[str, str]:
    for name in ("home", "xdg-cache", "xdg-config", "xdg-data", "xdg-state"):
        (temporary_root / name).mkdir(mode=0o700, exist_ok=True)
    path = "/usr/bin:/bin"
    if venv_bin is not None:
        path = f"{venv_bin}:{path}"
    return {
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "HOME": str(temporary_root / "home"),
        "PATH": path,
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_CONFIG_FILE": "/dev/null",
        "PIP_NO_CACHE_DIR": "1",
        "PIP_NO_INDEX": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONNOUSERSITE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "TMPDIR": str(temporary_root),
        "TZ": "UTC",
        "XDG_CACHE_HOME": str(temporary_root / "xdg-cache"),
        "XDG_CONFIG_HOME": str(temporary_root / "xdg-config"),
        "XDG_DATA_HOME": str(temporary_root / "xdg-data"),
        "XDG_STATE_HOME": str(temporary_root / "xdg-state"),
    }


def _validate_temporary_parent(path: Path, *, protected_roots: Sequence[Path]) -> None:
    descriptor = _open_absolute_directory(path)
    os.close(descriptor)
    for root in protected_roots:
        root = _normalize_absolute_path(str(root), label="protected root")
        if path == root or path in root.parents or root in path.parents:
            raise ReleaseBuildError(
                f"temporary parent overlaps protected release/input state: {root}"
            )


def _run_command(
    command: Sequence[str], *, cwd: Path, environment: Mapping[str, str],
    prefix: Sequence[str] = (),
) -> subprocess.CompletedProcess[str]:
    effective_command = [*prefix, *command]
    result = subprocess.run(
        effective_command,
        cwd=str(cwd),
        env=dict(environment),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode != 0:
        raise ReleaseBuildError(
            f"release command failed ({result.returncode}): {' '.join(command)}\n"
            f"output_sha256={_sha256(result.stdout.encode('utf-8'))}"
        )
    return result


def _isolated_module_command(
    python_path: Path, app: Path, module: str, arguments: Sequence[str]
) -> list[str]:
    """Return a `-I` command that adds only the verified app to sys.path."""

    bootstrap = (
        "import runpy,sys; app=sys.argv.pop(1); module=sys.argv.pop(1); "
        "sys.path.insert(0,app); sys.argv[0]=module; "
        "runpy.run_module(module,run_name='__main__')"
    )
    return [
        str(python_path), "-I", "-B", "-c", bootstrap, str(app), module,
        *arguments,
    ]


def _network_isolation_contract(
    python_path: Path, *, cwd: Path, environment: Mapping[str, str]
) -> Tuple[Mapping[str, Any], Tuple[str, ...]]:
    """Prove and return a fail-closed Linux user+network namespace prefix."""

    if platform.system() != "Linux":
        raise ReleaseBuildError("Gate A requires Linux OS-level network isolation")
    utility = Path("/usr/bin/unshare")
    utility_fd, utility_stat = _open_regular_path(utility)
    digest = hashlib.sha256()
    total = 0
    try:
        while True:
            chunk = os.read(utility_fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
    finally:
        os.close(utility_fd)
    if not utility_stat.st_mode & 0o111:
        raise ReleaseBuildError("network-isolation utility is not executable")
    try:
        parent_namespace = os.readlink("/proc/self/ns/net")
    except OSError as exc:
        raise ReleaseBuildError("cannot identify the parent Linux network namespace") from exc
    prefix = (
        str(utility), "--user", "--map-root-user", "--net", "--",
    )
    probe = r"""
import json,os,socket,sys
parent=sys.argv[1]
current=os.readlink('/proc/self/ns/net')
if current == parent:
    raise SystemExit(91)
blocked=False
try:
    socket.create_connection(('1.1.1.1', 53), timeout=0.25)
except OSError:
    blocked=True
if not blocked:
    raise SystemExit(92)
print(json.dumps({'child_network_namespace':current,'outbound_connect_blocked':True},sort_keys=True,separators=(',',':')))
"""
    result = _run_command(
        [str(python_path), "-I", "-B", "-c", probe, parent_namespace],
        cwd=cwd,
        environment=environment,
        prefix=prefix,
    )
    evidence = _strict_json(
        result.stdout.strip().encode("utf-8"), label="network-isolation proof"
    )
    if not isinstance(evidence, Mapping) or evidence.get(
        "outbound_connect_blocked"
    ) is not True or evidence.get("child_network_namespace") == parent_namespace:
        raise ReleaseBuildError("OS-level network-isolation proof failed")
    return (
        {
            "mechanism": "linux_user_and_network_namespace_v1",
            "parent_network_namespace": parent_namespace,
            "probe": evidence,
            "utility": {
                "bytes": total,
                "mode": format(stat.S_IMODE(utility_stat.st_mode), "04o"),
                "path": str(utility),
                "sha256": digest.hexdigest(),
            },
        },
        prefix,
    )


_NETWORK_GUARD = b"""\
import sys

def _caerus_no_network(event, args):
    if event in {"socket.connect", "socket.getaddrinfo", "socket.gethostbyname"}:
        raise RuntimeError("CAERUS_GATE_A_NETWORK_FORBIDDEN")

sys.addaudithook(_caerus_no_network)
"""


def _runtime_identity(
    python_path: Path, *, cwd: Path, environment: Mapping[str, str],
    prefix: Sequence[str] = (),
) -> Mapping[str, Any]:
    script = r"""
import importlib.metadata as m, json, os, platform, sys, sysconfig
norm=lambda x: __import__('re').sub(r'[-_.]+','-',x).lower()
d={norm(x.metadata['Name']):x.version for x in m.distributions()}
paths=sysconfig.get_paths()
os_release_path='/usr/lib/os-release'
if not os.path.isfile(os_release_path): os_release_path='/etc/os-release'
os_release={}
with open(os_release_path,encoding='utf-8') as stream:
    for raw in stream:
        if '=' in raw:
            key,value=raw.rstrip('\n').split('=',1); os_release[key]=value.strip('"')
shared=set()
with open('/proc/self/maps',encoding='utf-8') as stream:
    for raw in stream:
        fields=raw.rstrip('\n').split(None,5)
        if len(fields)==6 and fields[5].startswith('/'): shared.add(fields[5])
venv_root=os.path.realpath(sys.prefix)
shared={path for path in shared if os.path.realpath(path)!=os.path.realpath(sys.executable) and not os.path.realpath(path).startswith(venv_root+os.sep)}
print(json.dumps({'python_version':platform.python_version(),'python_implementation':platform.python_implementation(),'architecture':platform.machine(),'libc':list(platform.libc_ver()),'operating_system':{'id':os_release.get('ID'),'version_id':os_release.get('VERSION_ID'),'receipt_path':os_release_path},'executable':sys.executable,'base_executable':getattr(sys,'_base_executable',None),'base_prefix':sys.base_prefix,'base_exec_prefix':sys.base_exec_prefix,'stdlib_paths':sorted(set([paths['stdlib'],paths['platstdlib']])),'loaded_shared_objects':sorted(shared),'reviewed_tools':{'git':'/usr/bin/git'},'distributions':dict(sorted(d.items()))},sort_keys=True,separators=(',',':')))
"""
    result = _run_command(
        [str(python_path), "-I", "-B", "-c", script], cwd=cwd,
        environment=environment, prefix=prefix,
    )
    parsed = _strict_json(result.stdout.strip().encode("utf-8"), label="runtime identity")
    if not isinstance(parsed, Mapping):
        raise ReleaseBuildError("runtime identity is invalid")
    return parsed


def _scan_external_tree_fd(
    root_fd: int, *, prefix: Tuple[str, ...] = ()
) -> list[Dict[str, Any]]:
    """Hash a trusted host runtime tree without following links."""

    records: list[Dict[str, Any]] = []
    for name in _directory_names(root_fd):
        metadata = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        relative = "/".join(prefix + (name,))
        mode = format(stat.S_IMODE(metadata.st_mode), "04o")
        if stat.S_ISDIR(metadata.st_mode):
            child = os.open(name, _READ_DIR_FLAGS, dir_fd=root_fd)
            try:
                records.append({"path": relative, "type": "directory", "mode": mode})
                records.extend(_scan_external_tree_fd(child, prefix=prefix + (name,)))
            finally:
                os.close(child)
        elif stat.S_ISREG(metadata.st_mode):
            total, digest, _ = _hash_open_file(root_fd, name)
            records.append(
                {
                    "path": relative,
                    "type": "file",
                    "mode": mode,
                    "bytes": total,
                    "sha256": digest,
                }
            )
        elif stat.S_ISLNK(metadata.st_mode):
            records.append(
                {
                    "path": relative,
                    "type": "symlink",
                    "mode": mode,
                    "target": os.readlink(name, dir_fd=root_fd),
                }
            )
        else:
            raise ReleaseBuildError(
                f"unsupported base-runtime filesystem entry: {relative}"
            )
    return sorted(records, key=lambda record: str(record["path"]))


def _base_runtime_receipt(identity: Mapping[str, Any]) -> Mapping[str, Any]:
    base_executable = _normalize_absolute_path(
        str(identity.get("base_executable")), label="base interpreter"
    )
    executable_bytes, executable_sha256 = _hash_regular_path(base_executable)
    executable_fd, executable_stat = _open_regular_path(base_executable)
    os.close(executable_fd)
    stdlib_values = identity.get("stdlib_paths")
    if not isinstance(stdlib_values, list) or not stdlib_values:
        raise ReleaseBuildError("runtime did not report its external stdlib roots")
    stdlib_receipts = []
    for raw_path in stdlib_values:
        path = _normalize_absolute_path(str(raw_path), label="stdlib root")
        descriptor = _open_absolute_directory(path)
        try:
            records = _scan_external_tree_fd(descriptor)
            root_mode = format(stat.S_IMODE(os.fstat(descriptor).st_mode), "04o")
        finally:
            os.close(descriptor)
        stdlib_receipts.append(
            {
                "path": str(path),
                "record_count": len(records),
                "records_sha256": _sha256(_canonical_bytes(records)),
                "root_mode": root_mode,
            }
        )
    operating_system = identity.get("operating_system")
    if not isinstance(operating_system, Mapping):
        raise ReleaseBuildError("runtime operating-system identity is missing")
    os_release_path = _normalize_absolute_path(
        str(operating_system.get("receipt_path")), label="OS release receipt"
    )
    os_release_bytes = _read_regular_path(os_release_path)
    shared_values = identity.get("loaded_shared_objects")
    if not isinstance(shared_values, list) or not shared_values:
        raise ReleaseBuildError("runtime loaded shared-object identity is missing")
    shared_receipts = []
    for raw_path in shared_values:
        path = _normalize_absolute_path(str(raw_path), label="loaded shared object")
        size, digest = _hash_regular_path(path)
        descriptor, metadata = _open_regular_path(path)
        os.close(descriptor)
        shared_receipts.append(
            {
                "bytes": size,
                "mode": format(stat.S_IMODE(metadata.st_mode), "04o"),
                "path": str(path),
                "sha256": digest,
            }
        )
    reviewed_tools = identity.get("reviewed_tools")
    if not isinstance(reviewed_tools, Mapping) or set(reviewed_tools) != {"git"}:
        raise ReleaseBuildError("reviewed external-tool identity drift")
    git_path = _normalize_absolute_path(
        str(reviewed_tools["git"]), label="reviewed Git executable"
    )
    git_bytes, git_sha256 = _hash_regular_path(git_path)
    git_descriptor, git_metadata = _open_regular_path(git_path)
    os.close(git_descriptor)
    return {
        "base_executable": {
            "bytes": executable_bytes,
            "mode": format(stat.S_IMODE(executable_stat.st_mode), "04o"),
            "path": str(base_executable),
            "sha256": executable_sha256,
        },
        "base_exec_prefix": identity.get("base_exec_prefix"),
        "base_prefix": identity.get("base_prefix"),
        "loaded_shared_objects": shared_receipts,
        "operating_system_release": {
            "bytes": len(os_release_bytes),
            "id": operating_system.get("id"),
            "path": str(os_release_path),
            "sha256": _sha256(os_release_bytes),
            "version_id": operating_system.get("version_id"),
        },
        "reviewed_tools": {
            "git": {
                "bytes": git_bytes,
                "mode": format(stat.S_IMODE(git_metadata.st_mode), "04o"),
                "path": str(git_path),
                "sha256": git_sha256,
            }
        },
        "stdlib_roots": stdlib_receipts,
    }


def _validate_runtime_target(identity: Mapping[str, Any], python_path: Path) -> None:
    if set(identity) != {
        "architecture", "base_exec_prefix", "base_executable", "base_prefix",
        "distributions", "executable", "libc", "loaded_shared_objects",
        "operating_system", "python_implementation", "python_version",
        "reviewed_tools", "stdlib_paths",
    }:
        raise ReleaseBuildError("release runtime identity schema drift")
    if identity.get("python_version") != EXPECTED_TARGET["python_version"]:
        raise ReleaseBuildError("release interpreter version drift")
    if identity.get("python_implementation") != EXPECTED_TARGET["python_implementation"]:
        raise ReleaseBuildError("release interpreter implementation drift")
    if identity.get("architecture") != EXPECTED_TARGET["architecture"]:
        raise ReleaseBuildError("release architecture drift")
    if identity.get("operating_system") != {
        "id": "ubuntu",
        "receipt_path": "/usr/lib/os-release",
        "version_id": EXPECTED_TARGET["operating_system_version"],
    }:
        raise ReleaseBuildError("release Ubuntu operating-system identity drift")
    libc = identity.get("libc")
    if libc != ["glibc", EXPECTED_TARGET["glibc_version"]]:
        raise ReleaseBuildError("release glibc drift")
    if identity.get("executable") != str(python_path):
        raise ReleaseBuildError("release interpreter path drift")
    if identity.get("reviewed_tools") != {"git": "/usr/bin/git"}:
        raise ReleaseBuildError("release reviewed external-tool path drift")


def _expected_distributions(lock_bytes: bytes) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for line in lock_bytes.decode("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        requirement = line.split(" --hash=", 1)[0]
        name, version = requirement.split("==", 1)
        normalized = re.sub(r"[-_.]+", "-", name).lower()
        result[normalized] = version
    return dict(sorted(result.items()))


def _validate_distribution_closure(
    identity: Mapping[str, Any], lock_bytes: bytes
) -> Mapping[str, Mapping[str, str]]:
    distributions = identity.get("distributions")
    if not isinstance(distributions, Mapping) or not all(
        isinstance(name, str) and isinstance(version, str)
        for name, version in distributions.items()
    ):
        raise ReleaseBuildError("installed distribution identity is invalid")
    locked = _expected_distributions(lock_bytes)
    installed_locked = {
        name: distributions[name] for name in locked if name in distributions
    }
    if installed_locked != locked:
        raise ReleaseBuildError("installed locked distribution set/version drift")
    bootstrap = {
        str(name): str(version)
        for name, version in distributions.items()
        if name not in locked
    }
    if not bootstrap or not set(bootstrap) <= ALLOWED_BOOTSTRAP_DISTRIBUTIONS:
        raise ReleaseBuildError(
            "venv bootstrap distributions must be a non-empty subset of pip/setuptools"
        )
    if set(distributions) != set(locked) | set(bootstrap):
        raise ReleaseBuildError("installed distribution closure has undeclared extras")
    return {
        "bootstrap_distributions": dict(sorted(bootstrap.items())),
        "locked_distributions": locked,
    }


def _execute_runtime_build(
    *, release_dir: Path, inputs: ReleaseInputs, interpreter: Path,
    temporary_root: Path,
) -> Mapping[str, Any]:
    app = release_dir / "app"
    venv = release_dir / "venv"
    wheelhouse = release_dir / "wheelhouse"
    base_env = _sanitized_environment(temporary_root=temporary_root)
    _run_command(
        [str(interpreter), "-I", "-B", "-m", "venv", "--copies", str(venv)],
        cwd=app,
        environment=base_env,
    )
    python_path = venv / "bin/python"
    metadata = os.lstat(python_path)
    if not stat.S_ISREG(metadata.st_mode):
        raise ReleaseBuildError("venv interpreter must be a regular copied executable")
    environment = _sanitized_environment(
        temporary_root=temporary_root, venv_bin=venv / "bin"
    )
    identity_before = _runtime_identity(
        python_path, cwd=app, environment=environment
    )
    _validate_runtime_target(identity_before, python_path)
    network_isolation, namespace_prefix = _network_isolation_contract(
        python_path, cwd=app, environment=environment
    )
    site_script = (
        "import json,sysconfig; print(json.dumps(sysconfig.get_paths()['purelib']))"
    )
    site_result = _run_command(
        [str(python_path), "-I", "-B", "-c", site_script],
        cwd=app,
        environment=environment,
        prefix=namespace_prefix,
    )
    site_packages = Path(json.loads(site_result.stdout.strip()))
    try:
        site_packages.relative_to(venv)
    except ValueError as exc:
        raise ReleaseBuildError("venv site-packages escapes release") from exc
    site_fd = _open_absolute_directory(site_packages)
    try:
        _write_exclusive(site_fd, "sitecustomize.py", _NETWORK_GUARD, 0o400)
    finally:
        os.close(site_fd)
    guard_hash = _sha256(_NETWORK_GUARD)
    install = _run_command(
        [
            str(python_path), "-B", "-m", "pip", "install", "--no-index",
            f"--find-links={wheelhouse}", "--require-hashes", "--no-cache-dir",
            "--no-compile", "-r", str(release_dir / LOCK_RELEASE_NAME),
        ],
        cwd=app,
        environment=environment,
        prefix=namespace_prefix,
    )
    dependency = _run_command(
        _isolated_module_command(
            python_path,
            app,
            "projects.alpha_lab.factory.release_dependencies",
            ["--repo-root", str(app), "--wheelhouse", str(wheelhouse)],
        ),
        cwd=app,
        environment=environment,
        prefix=namespace_prefix,
    )
    dependency_result = _strict_json(
        dependency.stdout.strip().encode("utf-8"), label="dependency validation output"
    )
    if dependency_result.get("status") != "PASS" or not dependency_result.get(
        "wheelhouse_verified"
    ):
        raise ReleaseBuildError("release dependency validator did not verify wheelhouse")
    pip_check = _run_command(
        [str(python_path), "-B", "-m", "pip", "check"],
        cwd=app,
        environment=environment,
        prefix=namespace_prefix,
    )
    sys_path_script = (
        "import json,os,sys; app=sys.argv[1]; sys.path.insert(0,app); "
        "print(json.dumps({'cwd':os.getcwd(),'path':sys.path},"
        "sort_keys=True,separators=(',',':')))"
    )
    sys_path_result = _run_command(
        [str(python_path), "-I", "-B", "-c", sys_path_script, str(app)],
        cwd=app,
        environment=environment,
        prefix=namespace_prefix,
    )
    sys_path = _strict_json(
        sys_path_result.stdout.strip().encode("utf-8"), label="release sys.path"
    )
    if sys_path.get("cwd") != str(app):
        raise ReleaseBuildError("release test working directory drift")
    paths = sys_path.get("path")
    if not isinstance(paths, list) or paths[:1] != [str(app)] or "" in paths:
        raise ReleaseBuildError("release sys.path bootstrap is not exact")
    forbidden = {
        EXPECTED_AUTHORITATIVE_REPO_ROOT,
        "/home/brettolson/.venvs/quant-daily-report",
    }
    for item in sys_path.get("path", []):
        if any(str(item) == root or str(item).startswith(root + "/") for root in forbidden):
            raise ReleaseBuildError("release sys.path includes forbidden runtime source")
    app_before = _scan_tree(app)
    venv_before = _scan_tree(venv)
    release_before = _scan_tree(release_dir)
    collection = _run_command(
        _isolated_module_command(
            python_path,
            app,
            "pytest",
            ["-p", "no:cacheprovider", "--collect-only", "-q", "projects/alpha_lab/tests"],
        ),
        cwd=app,
        environment=environment,
        prefix=namespace_prefix,
    )
    test_node_ids = tuple(
        line.strip()
        for line in collection.stdout.splitlines()
        if line.strip().startswith("projects/alpha_lab/tests/") and "::" in line
    )
    if len(test_node_ids) != EXPECTED_PYTEST_PASSED + EXPECTED_DUCKDB_SKIPPED or len(
        set(test_node_ids)
    ) != len(test_node_ids):
        raise ReleaseBuildError("release pytest inventory does not match 357 unique tests")
    junit_path = temporary_root / "pytest-results.xml"
    tests = _run_command(
        _isolated_module_command(
            python_path,
            app,
            "pytest",
            [
                "-p", "no:cacheprovider", "-q", "-r", "s",
                f"--junitxml={junit_path}", "projects/alpha_lab/tests",
            ],
        ),
        cwd=app,
        environment=environment,
        prefix=namespace_prefix,
    )
    summary = re.search(r"(\d+) passed, (\d+) skipped in ", tests.stdout)
    if summary is None or tuple(map(int, summary.groups())) != (
        EXPECTED_PYTEST_PASSED,
        EXPECTED_DUCKDB_SKIPPED,
    ):
        raise ReleaseBuildError("release pytest result does not match 355/2 contract")
    try:
        junit = ET.parse(junit_path)
    except (ET.ParseError, OSError) as exc:
        raise ReleaseBuildError("cannot parse release pytest JUnit evidence") from exc
    skipped_node_ids = []
    junit_case_count = 0
    for case in junit.getroot().iter("testcase"):
        junit_case_count += 1
        if case.find("skipped") is None:
            continue
        classname = str(case.attrib.get("classname", ""))
        name = str(case.attrib.get("name", ""))
        if not classname or not name:
            raise ReleaseBuildError("pytest skip evidence is missing its identity")
        skipped_node_ids.append(f"{classname.replace('.', '/')}.py::{name}")
    if junit_case_count != len(test_node_ids) or tuple(sorted(skipped_node_ids)) != tuple(
        sorted(EXPECTED_DUCKDB_SKIP_NODE_IDS)
    ):
        raise ReleaseBuildError("release pytest skip identities drifted")
    app_after = _scan_tree(app)
    if app_after != app_before:
        raise ReleaseBuildError("release tests mutated application source")
    guard_path = site_packages / "sitecustomize.py"
    guard_fd, guard_stat = _open_regular_path(guard_path)
    try:
        guard_bytes = b""
        while True:
            chunk = os.read(guard_fd, 4096)
            if not chunk:
                break
            guard_bytes += chunk
    finally:
        os.close(guard_fd)
    if guard_bytes != _NETWORK_GUARD or _sha256(guard_bytes) != guard_hash:
        raise ReleaseBuildError("Gate A network guard mutated")
    site_parent = _open_absolute_directory(site_packages)
    try:
        os.unlink("sitecustomize.py", dir_fd=site_parent)
        os.fsync(site_parent)
    finally:
        os.close(site_parent)
    venv_after = _scan_tree(venv)
    expected_venv = [record for record in venv_before if record["path"] != str(
        guard_path.relative_to(venv)
    )]
    if venv_after != expected_venv:
        raise ReleaseBuildError("release validation mutated venv or left cache/temp files")
    release_after = _scan_tree(release_dir)
    guard_relative = f"venv/{guard_path.relative_to(venv)}"
    expected_release = [
        record for record in release_before if record["path"] != guard_relative
    ]
    if release_after != expected_release:
        raise ReleaseBuildError(
            "release validation mutated inputs or left unexpected release state"
        )
    identity = _runtime_identity(
        python_path, cwd=app, environment=environment, prefix=namespace_prefix
    )
    _validate_runtime_target(identity, python_path)
    for key in set(identity) - {"distributions"}:
        if identity.get(key) != identity_before.get(key):
            raise ReleaseBuildError(f"release base runtime identity changed: {key}")
    distribution_closure = _validate_distribution_closure(identity, inputs.lock_bytes)
    base_runtime = _base_runtime_receipt(identity)
    return {
        "base_runtime": base_runtime,
        "distribution_closure": distribution_closure,
        "dependency_validation": dependency_result,
        "install_output_sha256": _sha256(install.stdout.encode("utf-8")),
        "network_isolation": network_isolation,
        "network_guard_sha256": guard_hash,
        "pip_check_output_sha256": _sha256(pip_check.stdout.encode("utf-8")),
        "pytest_output_sha256": _sha256(tests.stdout.encode("utf-8")),
        "pytest_passed": EXPECTED_PYTEST_PASSED,
        "pytest_skipped": EXPECTED_DUCKDB_SKIPPED,
        "runtime_identity": identity,
        "entire_release_unchanged": True,
        "source_and_venv_unchanged": True,
        "site_packages_absolute_path": str(site_packages),
        "site_packages_relative_path": str(site_packages.relative_to(release_dir)),
        "temporary_state_outside_release": True,
        "temporary_parent": str(temporary_root.parent),
        "test_inventory": {
            "collected": len(test_node_ids),
            "node_ids_sha256": _sha256(_canonical_bytes(list(test_node_ids))),
            "skipped_node_ids": list(sorted(skipped_node_ids)),
        },
    }


def _fault(requested: Optional[str], point: str) -> None:
    if requested == point:
        raise ReleaseBuildError(f"FAULT_INJECTED:{point}")


def _content_address_directory(parent_fd: int, category: str, digest: str) -> Tuple[int, bool]:
    category_fd = _open_or_create_child(parent_fd, category)
    try:
        sha_fd = _open_or_create_child(category_fd, "sha256")
    finally:
        os.close(category_fd)
    try:
        try:
            result = _mkdir_exclusive(sha_fd, digest)
            return result, True
        except ReleaseBuildError as exc:
            if "content-address collision" not in str(exc):
                raise
            existing = os.open(digest, _READ_DIR_FLAGS, dir_fd=sha_fd)
            return existing, False
    finally:
        os.close(sha_fd)


def _source_store_ready(source_dir: Path, source: SourceBundle) -> Mapping[str, Any]:
    descriptor = _open_absolute_directory(source_dir)
    try:
        names = set(os.listdir(descriptor))
        expected = {"source.tar", "source_manifest.json", "file_manifest.json", SOURCE_READY_NAME}
        if names != expected:
            raise ReleaseBuildError("source store is incomplete or has extra state")
        metadata = os.fstat(descriptor)
        if stat.S_IMODE(metadata.st_mode) != 0o555:
            raise ReleaseBuildError("source store directory is mutable")
        expected_files = {
            "source.tar": (source.archive_bytes, source.archive_sha256),
            "source_manifest.json": (len(source.source_manifest_bytes), source.source_manifest_sha256),
            "file_manifest.json": (len(source.file_manifest_bytes), source.file_manifest_sha256),
        }
        for name, identity in expected_files.items():
            size, digest, file_stat = _hash_open_file(descriptor, name)
            if (size, digest) != identity or stat.S_IMODE(file_stat.st_mode) != 0o444:
                raise ReleaseBuildError(f"source store artifact drift: {name}")
        ready_size, ready_hash, ready_stat = _hash_open_file(descriptor, SOURCE_READY_NAME)
        ready_bytes = _read_at(descriptor, SOURCE_READY_NAME)
        ready = _strict_canonical_json(ready_bytes, label="SOURCE_READY", object_required=True)
        if stat.S_IMODE(ready_stat.st_mode) != 0o444 or ready_size != len(ready_bytes):
            raise ReleaseBuildError("SOURCE_READY mode/size drift")
        expected_ready = {
            "schema_version": SOURCE_READY_SCHEMA,
            "archive_sha256": source.archive_sha256,
            "file_manifest_sha256": source.file_manifest_sha256,
            "source_manifest_sha256": source.source_manifest_sha256,
            "status": "READY",
        }
        if ready != expected_ready or ready_hash != _sha256(ready_bytes):
            raise ReleaseBuildError("SOURCE_READY content drift")
        return {"path": str(source_dir), "ready_sha256": ready_hash}
    finally:
        os.close(descriptor)


def _read_at(parent_fd: int, name: str) -> bytes:
    descriptor = os.open(name, _READ_FILE_FLAGS, dir_fd=parent_fd)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ReleaseBuildError(
                f"release control file is not single-link regular: {name}"
            )
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
    ):
        raise ReleaseBuildError(f"release control file changed while reading: {name}")
    return b"".join(chunks)


def _materialize_source_store(
    parent_fd: int, inputs: ReleaseInputs, *, fault_at: Optional[str]
) -> Mapping[str, Any]:
    source_fd, created = _content_address_directory(
        parent_fd, "sources", inputs.source.archive_sha256
    )
    source_dir = inputs.release_parent / "sources/sha256" / inputs.source.archive_sha256
    if not created:
        os.close(source_fd)
        return _source_store_ready(source_dir, inputs.source)
    try:
        _fault(fault_at, "source_directory_created")
        _copy_exclusive(source_fd, "source.tar", inputs.source.archive_path)
        _write_exclusive(
            source_fd, "source_manifest.json", inputs.source.source_manifest_bytes
        )
        _write_exclusive(source_fd, "file_manifest.json", inputs.source.file_manifest_bytes)
        _fault(fault_at, "source_payload_written")
        for name in ("source.tar", "source_manifest.json", "file_manifest.json"):
            descriptor = os.open(name, _READ_FILE_FLAGS, dir_fd=source_fd)
            try:
                os.fchmod(descriptor, 0o444)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        ready = {
            "schema_version": SOURCE_READY_SCHEMA,
            "archive_sha256": inputs.source.archive_sha256,
            "file_manifest_sha256": inputs.source.file_manifest_sha256,
            "source_manifest_sha256": inputs.source.source_manifest_sha256,
            "status": "READY",
        }
        _write_exclusive(source_fd, SOURCE_READY_NAME, _canonical_bytes(ready), 0o444)
        os.fchmod(source_fd, 0o555)
        os.fsync(source_fd)
    finally:
        os.close(source_fd)
    return _source_store_ready(source_dir, inputs.source)


def _copy_wheelhouse(release_fd: int, inputs: ReleaseInputs) -> None:
    wheel_fd = _mkdir_exclusive(release_fd, "wheelhouse")
    try:
        manifest = _strict_json(inputs.wheel_manifest_bytes, label="wheel manifest")
        for record in manifest["wheels"]:
            _copy_exclusive(
                wheel_fd,
                record["filename"],
                inputs.wheelhouse / record["filename"],
                0o400,
            )
    finally:
        os.close(wheel_fd)


def _build_manifest_payload(
    *, inputs: ReleaseInputs, records: Sequence[Mapping[str, Any]],
    runtime_evidence: Mapping[str, Any], source_store: Mapping[str, Any],
) -> Dict[str, Any]:
    records_hash = _sha256(_canonical_bytes(list(records)))
    payload: Dict[str, Any] = {
        "schema_version": BUILT_RUNTIME_SCHEMA,
        "builder_origin": inputs.builder_origin,
        "release_input_sha256": inputs.release_input_sha256,
        "source_archive_sha256": inputs.source.archive_sha256,
        "source_store": source_store,
        "lock_sha256": _sha256(inputs.lock_bytes),
        "wheel_manifest_sha256": _sha256(inputs.wheel_manifest_bytes),
        "records": list(records),
        "records_sha256": records_hash,
        "record_count": len(records),
        "release_directory_mode": "0555",
        "app_relative_path": "app",
        "python_relative_path": "venv/bin/python",
        "runtime_evidence": runtime_evidence,
    }
    build_identity = _sha256(_canonical_bytes(payload))
    payload["build_identity_sha256"] = build_identity
    return payload


def _directory_identity(path: Path) -> Mapping[str, Any]:
    descriptor = _open_absolute_directory(path)
    try:
        metadata = os.fstat(descriptor)
        return {
            "absolute_path": str(path),
            "gid": metadata.st_gid,
            "mode": format(stat.S_IMODE(metadata.st_mode), "04o"),
            "uid": metadata.st_uid,
        }
    finally:
        os.close(descriptor)


def _record_at(records: Sequence[Mapping[str, Any]], relative: str) -> Mapping[str, Any]:
    matches = [record for record in records if record.get("path") == relative]
    if len(matches) != 1:
        raise ReleaseBuildError(f"built runtime record is not unique: {relative}")
    return matches[0]


def _atlas_gate_e_runtime_receipt(
    *, release_dir: Path, manifest: Mapping[str, Any], built_manifest_sha256: str,
    allow_pending_release_root_seal: bool = False,
) -> Mapping[str, Any]:
    """Return the immutable Alpha provenance object consumed directly by Atlas."""

    records = manifest.get("records")
    evidence = manifest.get("runtime_evidence")
    if not isinstance(records, list) or not isinstance(evidence, Mapping):
        raise ReleaseBuildError("cannot derive Atlas Gate E receipt from runtime manifest")
    python_relative = str(manifest.get("python_relative_path"))
    python_record = _record_at(records, python_relative)
    if python_record.get("type") != "file" or python_record.get("mode") != "0555":
        raise ReleaseBuildError("Atlas Gate E interpreter record is not sealed executable")
    site_relative = str(evidence.get("site_packages_relative_path"))
    _safe_relative_parts(site_relative, label="Atlas Gate E site-packages path")
    site_absolute = release_dir / site_relative
    if evidence.get("site_packages_absolute_path") != str(site_absolute):
        raise ReleaseBuildError("Atlas Gate E site-packages path chain drift")
    site_records = _scan_tree(site_absolute)
    if any(record.get("type") == "symlink" for record in site_records):
        raise ReleaseBuildError("Atlas Gate E site-packages subtree contains a symlink")
    _assert_sealed_records(site_records)
    site_root = _directory_identity(site_absolute)
    if site_root["mode"] != "0555":
        raise ReleaseBuildError("Atlas Gate E site-packages root is mutable")
    lock_record = _record_at(records, LOCK_RELEASE_NAME)
    wheel_manifest_record = _record_at(records, WHEEL_MANIFEST_RELEASE_NAME)
    for name, record in (
        (LOCK_RELEASE_NAME, lock_record),
        (WHEEL_MANIFEST_RELEASE_NAME, wheel_manifest_record),
    ):
        if record.get("type") != "file" or record.get("mode") != "0444":
            raise ReleaseBuildError(f"Atlas Gate E dependency artifact is mutable: {name}")
    closure = evidence.get("distribution_closure")
    base_runtime = evidence.get("base_runtime")
    if not isinstance(closure, Mapping) or not isinstance(base_runtime, Mapping):
        raise ReleaseBuildError("Atlas Gate E dependency/base-runtime evidence is missing")
    git_record = base_runtime.get("reviewed_tools", {}).get("git")
    if not isinstance(git_record, Mapping) or git_record.get("path") != "/usr/bin/git":
        raise ReleaseBuildError("Atlas Gate E reviewed Git identity is missing")
    release_identity = _directory_identity(release_dir)
    if allow_pending_release_root_seal and release_identity["mode"] in {"0700", "0755"}:
        # The receipt must be serialized before its containing metadata can be
        # created. READY is created through the still-open root, root 0555 is
        # applied immediately afterward, and the independent verifier derives
        # this same object from the actual sealed root before build returns.
        release_identity = {**release_identity, "mode": "0555"}
    app_identity = _directory_identity(release_dir / "app")
    venv_identity = _directory_identity(release_dir / "venv")
    for label, value in (
        ("release", release_identity), ("app", app_identity), ("venv", venv_identity)
    ):
        if value["mode"] != "0555":
            raise ReleaseBuildError(f"Atlas Gate E {label} root is mutable")
    mode_census: Dict[str, int] = {}
    for record in records:
        key = f"{record.get('type')}:{record.get('mode')}"
        mode_census[key] = mode_census.get(key, 0) + 1
    ancestors = [
        _directory_identity(release_dir.parents[2]),
        _directory_identity(release_dir.parents[1]),
        _directory_identity(release_dir.parent),
        release_identity,
    ]
    return {
        "schema_version": ATLAS_GATE_E_RUNTIME_RECEIPT_SCHEMA,
        "status": "PASS",
        "release_input_sha256": manifest["release_input_sha256"],
        "build_identity_sha256": manifest["build_identity_sha256"],
        "built_runtime_manifest_sha256": built_manifest_sha256,
        "python": {
            "absolute_path": str(release_dir / python_relative),
            "bytes": python_record["bytes"],
            "mode": python_record["mode"],
            "relative_path": python_relative,
            "sha256": python_record["sha256"],
            "single_link": True,
            "type": "file",
        },
        "site_packages": {
            "absolute_path": str(site_absolute),
            "no_links": True,
            "record_count": len(site_records),
            "records": site_records,
            "records_sha256": _sha256(_canonical_bytes(site_records)),
            "relative_path": site_relative,
            "root_mode": site_root["mode"],
            "single_link_regular_files": True,
        },
        "dependency_contract": {
            "bootstrap_distributions": closure.get("bootstrap_distributions"),
            "lock": {
                "absolute_path": str(release_dir / LOCK_RELEASE_NAME),
                "bytes": lock_record["bytes"],
                "mode": lock_record["mode"],
                "relative_path": LOCK_RELEASE_NAME,
                "sha256": lock_record["sha256"],
                "single_link": True,
            },
            "locked_distributions": closure.get("locked_distributions"),
            "wheel_manifest": {
                "absolute_path": str(release_dir / WHEEL_MANIFEST_RELEASE_NAME),
                "bytes": wheel_manifest_record["bytes"],
                "mode": wheel_manifest_record["mode"],
                "relative_path": WHEEL_MANIFEST_RELEASE_NAME,
                "sha256": wheel_manifest_record["sha256"],
                "single_link": True,
            },
        },
        "base_runtime": base_runtime,
        "seal_evidence": {
            "accepted_production_controls": ["different_principal", "read_only_mount"],
            "ancestor_census": ancestors,
            "app": app_identity,
            "files_read_only": True,
            "mode_census": dict(sorted(mode_census.items())),
            "release": release_identity,
            "requires_gate_e_effective_uid_different_from_release_owner": True,
            "reverified_immediately": True,
            "same_user_adversarial_seal": False,
            "site_packages_no_links": True,
            "single_link_regular_files": True,
            "venv": venv_identity,
        },
    }


def _verify_built_payload(release_dir: Path, manifest: Mapping[str, Any]) -> None:
    if set(manifest) != {
        "schema_version", "builder_origin", "release_input_sha256", "source_archive_sha256",
        "source_store", "lock_sha256", "wheel_manifest_sha256", "records",
        "records_sha256", "record_count", "release_directory_mode",
        "app_relative_path", "python_relative_path", "runtime_evidence",
        "build_identity_sha256",
    } or manifest.get("schema_version") != BUILT_RUNTIME_SCHEMA:
        raise ReleaseBuildError("built runtime manifest schema is invalid")
    unsigned = dict(manifest)
    build_identity = unsigned.pop("build_identity_sha256")
    if _sha256(_canonical_bytes(unsigned)) != build_identity:
        raise ReleaseBuildError("built runtime identity drift")
    records = manifest.get("records")
    if not isinstance(records, list) or len(records) != manifest.get("record_count"):
        raise ReleaseBuildError("built runtime record census drift")
    if _sha256(_canonical_bytes(records)) != manifest.get("records_sha256"):
        raise ReleaseBuildError("built runtime records hash drift")
    actual = _scan_tree(release_dir, exclude_metadata=True)
    if actual != records:
        raise ReleaseBuildError("sealed release tree has extra, missing, or mutated state")
    _assert_sealed_records(actual)
    python_record = next(
        (record for record in actual if record["path"] == "venv/bin/python"), None
    )
    if python_record is None or python_record["type"] != "file" or python_record[
        "mode"
    ] != "0555":
        raise ReleaseBuildError("sealed release interpreter is not an executable regular file")


def _verify_sealed_app_matches_source(release_dir: Path, source: SourceBundle) -> None:
    expected: list[Mapping[str, Any]] = []
    for directory in source.archive_directories:
        expected.append(
            {"mode": "0555", "path": directory["path"], "type": "directory"}
        )
    for record in source.file_manifest:
        sealed_mode = "0555" if int(str(record["mode"]), 8) & 0o111 else "0444"
        expected.append({**record, "mode": sealed_mode})
    expected = sorted(expected, key=lambda record: str(record["path"]))
    actual = _scan_tree(release_dir / "app")
    if actual != expected:
        raise ReleaseBuildError(
            "sealed application does not exactly match the canonical source manifest"
        )


def _verify_network_isolation_record(record: Any) -> None:
    if not isinstance(record, Mapping) or set(record) != {
        "mechanism", "parent_network_namespace", "probe", "utility"
    } or record.get("mechanism") != "linux_user_and_network_namespace_v1":
        raise ReleaseBuildError("network-isolation evidence schema drift")
    probe = record.get("probe")
    if not isinstance(probe, Mapping) or set(probe) != {
        "child_network_namespace", "outbound_connect_blocked"
    } or probe.get("outbound_connect_blocked") is not True:
        raise ReleaseBuildError("network-isolation proof evidence drift")
    utility = record.get("utility")
    if not isinstance(utility, Mapping) or set(utility) != {
        "bytes", "mode", "path", "sha256"
    }:
        raise ReleaseBuildError("network-isolation utility evidence drift")
    utility_path = _normalize_absolute_path(
        str(utility.get("path")), label="network-isolation utility"
    )
    actual_bytes, actual_sha256 = _hash_regular_path(utility_path)
    descriptor, metadata = _open_regular_path(utility_path)
    os.close(descriptor)
    if utility != {
        "bytes": actual_bytes,
        "mode": format(stat.S_IMODE(metadata.st_mode), "04o"),
        "path": str(utility_path),
        "sha256": actual_sha256,
    } or not metadata.st_mode & 0o111:
        raise ReleaseBuildError("network-isolation utility identity drift")


def _verify_runtime_evidence(
    evidence: Any, *, release_dir: Path, dependency_result: Mapping[str, Any]
) -> None:
    expected_fields = {
        "base_runtime", "dependency_validation", "distribution_closure",
        "entire_release_unchanged",
        "install_output_sha256", "network_guard_sha256", "network_isolation",
        "pip_check_output_sha256", "pytest_output_sha256", "pytest_passed",
        "pytest_skipped", "runtime_identity", "source_and_venv_unchanged",
        "site_packages_absolute_path", "site_packages_relative_path",
        "temporary_state_outside_release", "test_inventory",
        "temporary_parent",
    }
    if not isinstance(evidence, Mapping) or set(evidence) != expected_fields:
        raise ReleaseBuildError("sealed runtime evidence schema drift")
    for key in (
        "install_output_sha256", "network_guard_sha256", "pip_check_output_sha256",
        "pytest_output_sha256",
    ):
        if not _SHA256.fullmatch(str(evidence.get(key, ""))):
            raise ReleaseBuildError(f"sealed runtime evidence hash drift: {key}")
    if (
        evidence.get("dependency_validation") != dependency_result
        or evidence.get("pytest_passed") != EXPECTED_PYTEST_PASSED
        or evidence.get("pytest_skipped") != EXPECTED_DUCKDB_SKIPPED
        or evidence.get("entire_release_unchanged") is not True
        or evidence.get("source_and_venv_unchanged") is not True
        or evidence.get("temporary_state_outside_release") is not True
        or not isinstance(evidence.get("temporary_parent"), str)
    ):
        raise ReleaseBuildError("sealed runtime result evidence drift")
    inventory = evidence.get("test_inventory")
    if not isinstance(inventory, Mapping) or set(inventory) != {
        "collected", "node_ids_sha256", "skipped_node_ids"
    } or inventory.get("collected") != EXPECTED_PYTEST_PASSED + EXPECTED_DUCKDB_SKIPPED or tuple(
        inventory.get("skipped_node_ids", [])
    ) != tuple(sorted(EXPECTED_DUCKDB_SKIP_NODE_IDS)) or not _SHA256.fullmatch(
        str(inventory.get("node_ids_sha256", ""))
    ):
        raise ReleaseBuildError("sealed pytest inventory evidence drift")
    python_path = release_dir / "venv/bin/python"
    app = release_dir / "app"
    site_relative = evidence.get("site_packages_relative_path")
    if not isinstance(site_relative, str):
        raise ReleaseBuildError("sealed site-packages relative path is missing")
    _safe_relative_parts(site_relative, label="sealed site-packages relative path")
    site_absolute = release_dir / site_relative
    if evidence.get("site_packages_absolute_path") != str(site_absolute):
        raise ReleaseBuildError("sealed site-packages absolute/relative path drift")
    site_descriptor = _open_absolute_directory(site_absolute)
    os.close(site_descriptor)
    with tempfile.TemporaryDirectory(prefix="caerus-alpha-verify-") as temporary:
        environment = _sanitized_environment(
            temporary_root=Path(temporary), venv_bin=python_path.parent
        )
        actual_network, namespace_prefix = _network_isolation_contract(
            python_path, cwd=app, environment=environment
        )
        actual_identity = _runtime_identity(
            python_path, cwd=app, environment=environment,
            prefix=namespace_prefix,
        )
        _validate_runtime_target(actual_identity, python_path)
    if actual_identity != evidence.get("runtime_identity"):
        raise ReleaseBuildError("sealed runtime interpreter/distribution identity drift")
    actual_closure = _validate_distribution_closure(
        actual_identity, _read_regular_path(release_dir / LOCK_RELEASE_NAME)
    )
    if actual_closure != evidence.get("distribution_closure"):
        raise ReleaseBuildError("sealed distribution-closure evidence drift")
    if _base_runtime_receipt(actual_identity) != evidence.get("base_runtime"):
        raise ReleaseBuildError("external base interpreter or stdlib identity drift")
    _verify_network_isolation_record(evidence.get("network_isolation"))
    if actual_network.get("utility") != evidence["network_isolation"].get("utility"):
        raise ReleaseBuildError("current network-isolation implementation drift")


def verify_sealed_release(release_dir: Path) -> Mapping[str, Any]:
    release_dir = _normalize_absolute_path(str(release_dir), label="release directory")
    root = _open_absolute_directory(release_dir)
    try:
        if stat.S_IMODE(os.fstat(root).st_mode) != 0o555:
            raise ReleaseBuildError("release directory is mutable")
        names = set(os.listdir(root))
        if not _METADATA_NAMES <= names:
            raise ReleaseBuildError("release is incomplete; READY metadata is missing")
        ready_bytes = _read_at(root, READY_NAME)
        manifest_bytes = _read_at(root, BUILT_MANIFEST_NAME)
        receipt_bytes = _read_at(root, RECEIPT_NAME)
        for name in _METADATA_NAMES:
            metadata = os.stat(name, dir_fd=root, follow_symlinks=False)
            if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o444:
                raise ReleaseBuildError(f"release metadata is mutable or non-regular: {name}")
    finally:
        os.close(root)
    ready = _strict_canonical_json(ready_bytes, label="READY", object_required=True)
    manifest = _strict_canonical_json(
        manifest_bytes, label="built runtime manifest", object_required=True
    )
    receipt = _strict_canonical_json(
        receipt_bytes, label="verification receipt", object_required=True
    )
    expected_ready_fields = {
        "schema_version", "status", "release_dir", "release_input_sha256",
        "build_identity_sha256", "built_runtime_manifest_sha256",
        "verification_receipt_sha256", "app_path", "python_path",
    }
    if set(ready) != expected_ready_fields or ready.get("schema_version") != READY_SCHEMA:
        raise ReleaseBuildError("READY schema is invalid")
    if ready.get("status") != "READY" or ready.get("release_dir") != str(release_dir):
        raise ReleaseBuildError("READY path/status drift")
    if release_dir.name != ready.get("release_input_sha256"):
        raise ReleaseBuildError("release directory is not addressed by release input")
    if ready.get("app_path") != str(release_dir / "app") or ready.get(
        "python_path"
    ) != str(release_dir / "venv/bin/python"):
        raise ReleaseBuildError("READY application/interpreter path drift")
    if _sha256(manifest_bytes) != ready.get("built_runtime_manifest_sha256"):
        raise ReleaseBuildError("READY built-manifest hash drift")
    if _sha256(receipt_bytes) != ready.get("verification_receipt_sha256"):
        raise ReleaseBuildError("READY receipt hash drift")
    if manifest.get("build_identity_sha256") != ready.get("build_identity_sha256"):
        raise ReleaseBuildError("READY build identity drift")
    _verify_built_payload(release_dir, manifest)
    if release_dir.parent.name != "sha256" or release_dir.parent.parent.name != "releases":
        raise ReleaseBuildError("release directory is outside the canonical release layout")
    release_parent = release_dir.parents[2]
    source_archive_sha256 = manifest.get("source_archive_sha256")
    if not _SHA256.fullmatch(str(source_archive_sha256 or "")):
        raise ReleaseBuildError("built source archive identity drift")
    source_dir = release_parent / "sources/sha256" / str(source_archive_sha256)
    verified_inputs = verify_release_inputs(
        repo_root=release_dir / "app",
        source_archive=source_dir / "source.tar",
        source_manifest=source_dir / "source_manifest.json",
        file_manifest=source_dir / "file_manifest.json",
        wheelhouse=release_dir / "wheelhouse",
        release_input_manifest=release_dir / "release_input_manifest.json",
        release_parent=release_parent,
        verify_builder_origin=False,
    )
    if verified_inputs.release_input_sha256 != ready["release_input_sha256"]:
        raise ReleaseBuildError("release input address drift after full input verification")
    _verify_sealed_app_matches_source(release_dir, verified_inputs.source)
    source_store = _source_store_ready(source_dir, verified_inputs.source)
    _verify_bound_builder_origin(
        manifest.get("builder_origin"),
        source=verified_inputs.source,
        release_parent=release_parent,
    )
    _verify_executing_builder_is_bound(manifest["builder_origin"])
    copied_lock = _read_regular_path(release_dir / LOCK_RELEASE_NAME)
    copied_wheel_manifest = _read_regular_path(
        release_dir / WHEEL_MANIFEST_RELEASE_NAME
    )
    if (
        copied_lock != verified_inputs.lock_bytes
        or copied_wheel_manifest != verified_inputs.wheel_manifest_bytes
        or manifest.get("source_store") != source_store
        or manifest.get("source_archive_sha256") != verified_inputs.source.archive_sha256
        or manifest.get("lock_sha256") != _sha256(verified_inputs.lock_bytes)
        or manifest.get("wheel_manifest_sha256")
        != _sha256(verified_inputs.wheel_manifest_bytes)
    ):
        raise ReleaseBuildError("built runtime input identity chain drift")
    try:
        dependency_result = validate_release_dependency_contract(
            release_dir / "app", wheelhouse=release_dir / "wheelhouse"
        )
    except ReleaseDependencyError as exc:
        raise ReleaseBuildError("sealed dependency validation failed") from exc
    if dependency_result.get("status") != "PASS" or not dependency_result.get(
        "wheelhouse_verified"
    ):
        raise ReleaseBuildError("sealed dependency validation failed")
    _verify_runtime_evidence(
        manifest.get("runtime_evidence"),
        release_dir=release_dir,
        dependency_result=dependency_result,
    )
    expected_receipt_fields = {
        "schema_version", "status", "release_input_sha256",
        "build_identity_sha256", "built_runtime_manifest_sha256",
        "atlas_gate_e_runtime_receipt",
        "source_store", "dependency_validation", "pytest_passed",
        "pytest_skipped", "pip_check_passed", "network_forbidden",
        "release_tree_unchanged", "source_and_venv_unchanged",
        "sealed_runtime_verified_before_ready",
    }
    if set(receipt) != expected_receipt_fields or receipt.get(
        "schema_version"
    ) != VERIFICATION_RECEIPT_SCHEMA:
        raise ReleaseBuildError("verification receipt schema is invalid")
    expected_gate_e_receipt = _atlas_gate_e_runtime_receipt(
        release_dir=release_dir,
        manifest=manifest,
        built_manifest_sha256=ready["built_runtime_manifest_sha256"],
    )
    if (
        receipt.get("status") != "PASS"
        or receipt.get("release_input_sha256") != ready["release_input_sha256"]
        or receipt.get("build_identity_sha256") != ready["build_identity_sha256"]
        or receipt.get("built_runtime_manifest_sha256")
        != ready["built_runtime_manifest_sha256"]
        or receipt.get("source_store") != source_store
        or receipt.get("dependency_validation") != dependency_result
        or receipt.get("dependency_validation")
        != manifest["runtime_evidence"]["dependency_validation"]
        or receipt.get("pytest_passed") != EXPECTED_PYTEST_PASSED
        or receipt.get("pytest_passed")
        != manifest["runtime_evidence"]["pytest_passed"]
        or receipt.get("pytest_skipped") != EXPECTED_DUCKDB_SKIPPED
        or receipt.get("pytest_skipped")
        != manifest["runtime_evidence"]["pytest_skipped"]
        or receipt.get("pip_check_passed") is not True
        or receipt.get("network_forbidden") is not True
        or receipt.get("release_tree_unchanged") is not True
        or receipt.get("source_and_venv_unchanged") is not True
        or receipt.get("sealed_runtime_verified_before_ready") is not True
        or receipt.get("atlas_gate_e_runtime_receipt") != expected_gate_e_receipt
    ):
        raise ReleaseBuildError("verification receipt evidence drift")
    release_input_path = release_dir / "release_input_manifest.json"
    release_input_bytes = _read_regular_path(release_input_path)
    if _sha256(release_input_bytes) != ready["release_input_sha256"]:
        raise ReleaseBuildError("sealed release input manifest drift")
    return {
        "schema_version": VERIFY_SCHEMA,
        "status": "PASS",
        "release_dir": str(release_dir),
        "release_input_sha256": ready["release_input_sha256"],
        "build_identity_sha256": ready["build_identity_sha256"],
        "ready_sha256": _sha256(ready_bytes),
        "app_path": ready["app_path"],
        "python_path": ready["python_path"],
        "record_count": manifest["record_count"],
        "builder_origin": manifest["builder_origin"],
        "atlas_gate_e_runtime_receipt": expected_gate_e_receipt,
        "atlas_gate_e_runtime_receipt_sha256": _sha256(
            _canonical_bytes(expected_gate_e_receipt)
        ),
    }


def build_release(
    inputs: ReleaseInputs, *, write: bool, authorized_release_input_sha256: Optional[str],
    interpreter: Path = Path("/usr/bin/python3"), temporary_parent: Path = Path("/tmp"),
    fault_at: Optional[str] = None,
    runtime_executor: Callable[..., Mapping[str, Any]] = _execute_runtime_build,
    runtime_verifier: Callable[..., None] = _verify_runtime_evidence,
) -> Mapping[str, Any]:
    release_dir = inputs.release_parent / "releases/sha256" / inputs.release_input_sha256
    source_dir = inputs.release_parent / "sources/sha256" / inputs.source.archive_sha256
    plan = {
        "schema_version": "caerus_alpha_lab_release_build_plan_v1",
        "status": "DRY_RUN_VERIFIED" if not write else "WRITE_REQUESTED",
        "release_input_sha256": inputs.release_input_sha256,
        "release_dir": str(release_dir),
        "source_dir": str(source_dir),
        "builder_origin": inputs.builder_origin,
        "write": write,
    }
    if not write:
        return plan
    if (
        authorized_release_input_sha256 is None
        or authorized_release_input_sha256 != inputs.release_input_sha256
        or not _SHA256.fullmatch(authorized_release_input_sha256)
    ):
        raise ReleaseBuildError("exact authorized release-input SHA-256 is required")
    if inputs.builder_origin.get("content_addressed") is not True:
        raise ReleaseBuildError(
            "write requires the builder and dependency validator to execute from "
            "the reviewed content-addressed bootstrap source"
        )
    interpreter = _normalize_absolute_path(str(interpreter), label="interpreter")
    temporary_parent = _normalize_absolute_path(str(temporary_parent), label="temporary parent")
    _validate_temporary_parent(
        temporary_parent,
        protected_roots=(
            inputs.repo_root,
            inputs.release_parent,
            inputs.source.archive_path,
            inputs.wheelhouse,
            Path(EXPECTED_AUTHORITATIVE_REPO_ROOT),
            Path(EXPECTED_AUTHORITATIVE_DATA_ROOT),
        ),
    )
    interpreter_fd, interpreter_stat = _open_regular_path(interpreter)
    os.close(interpreter_fd)
    if not interpreter_stat.st_mode & 0o111:
        raise ReleaseBuildError("release interpreter is not executable")
    parent_fd = _open_absolute_directory(inputs.release_parent, create_missing=True)
    try:
        source_store = _materialize_source_store(parent_fd, inputs, fault_at=fault_at)
        _fault(fault_at, "source_ready")
        release_fd, created = _content_address_directory(
            parent_fd, "releases", inputs.release_input_sha256
        )
        if not created:
            os.close(release_fd)
            try:
                verified = verify_sealed_release(release_dir)
            except Exception as exc:
                raise ReleaseBuildError(
                    "release address collision is incomplete or invalid; never reuse or repair it"
                ) from exc
            return {**verified, "status": "ALREADY_READY"}
        try:
            _fault(fault_at, "release_directory_created")
            app_fd = _mkdir_exclusive(release_fd, "app")
            try:
                _extract_tar_exact(
                    inputs.source,
                    app_fd,
                    archive_path=source_dir / "source.tar",
                )
            finally:
                os.close(app_fd)
            _fault(fault_at, "app_extracted")
            _copy_wheelhouse(release_fd, inputs)
            _write_exclusive(
                release_fd, "release_input_manifest.json", inputs.release_input_bytes
            )
            _write_exclusive(release_fd, LOCK_RELEASE_NAME, inputs.lock_bytes)
            _write_exclusive(
                release_fd, WHEEL_MANIFEST_RELEASE_NAME, inputs.wheel_manifest_bytes
            )
            os.fsync(release_fd)
        finally:
            os.close(release_fd)
    finally:
        os.close(parent_fd)
    _fault(fault_at, "release_inputs_copied")
    with tempfile.TemporaryDirectory(
        prefix="caerus-alpha-gate-a-", dir=str(temporary_parent)
    ) as temporary:
        runtime_evidence = runtime_executor(
            release_dir=release_dir,
            inputs=inputs,
            interpreter=interpreter,
            temporary_root=Path(temporary),
        )
        _fault(fault_at, "runtime_validated")
    release_fd = _open_absolute_directory(release_dir)
    try:
        _seal_tree_fd(release_fd)
        records = _scan_tree_fd(release_fd, exclude_root_names=_METADATA_NAMES)
        _assert_sealed_records(records)
        manifest = _build_manifest_payload(
            inputs=inputs,
            records=records,
            runtime_evidence=runtime_evidence,
            source_store=source_store,
        )
        manifest_bytes = _canonical_bytes(manifest)
        _write_exclusive(release_fd, BUILT_MANIFEST_NAME, manifest_bytes, 0o444)
        _fault(fault_at, "built_manifest_written")
    finally:
        os.close(release_fd)
    _verify_built_payload(release_dir, manifest)
    try:
        sealed_dependency_result = validate_release_dependency_contract(
            release_dir / "app", wheelhouse=release_dir / "wheelhouse"
        )
    except ReleaseDependencyError as exc:
        raise ReleaseBuildError("sealed dependency validation failed before READY") from exc
    runtime_verifier(
        runtime_evidence,
        release_dir=release_dir,
        dependency_result=sealed_dependency_result,
    )
    manifest_hash = _sha256(manifest_bytes)
    atlas_gate_e_runtime_receipt = _atlas_gate_e_runtime_receipt(
        release_dir=release_dir,
        manifest=manifest,
        built_manifest_sha256=manifest_hash,
        allow_pending_release_root_seal=True,
    )
    receipt = {
        "schema_version": VERIFICATION_RECEIPT_SCHEMA,
        "status": "PASS",
        "release_input_sha256": inputs.release_input_sha256,
        "build_identity_sha256": manifest["build_identity_sha256"],
        "built_runtime_manifest_sha256": manifest_hash,
        "source_store": source_store,
        "dependency_validation": runtime_evidence["dependency_validation"],
        "pytest_passed": runtime_evidence["pytest_passed"],
        "pytest_skipped": runtime_evidence["pytest_skipped"],
        "pip_check_passed": True,
        "network_forbidden": True,
        "release_tree_unchanged": runtime_evidence["entire_release_unchanged"],
        "source_and_venv_unchanged": runtime_evidence["source_and_venv_unchanged"],
        "sealed_runtime_verified_before_ready": True,
        "atlas_gate_e_runtime_receipt": atlas_gate_e_runtime_receipt,
    }
    receipt_bytes = _canonical_bytes(receipt)
    ready = {
        "schema_version": READY_SCHEMA,
        "status": "READY",
        "release_dir": str(release_dir),
        "release_input_sha256": inputs.release_input_sha256,
        "build_identity_sha256": manifest["build_identity_sha256"],
        "built_runtime_manifest_sha256": manifest_hash,
        "verification_receipt_sha256": _sha256(receipt_bytes),
        "app_path": str(release_dir / "app"),
        "python_path": str(release_dir / "venv/bin/python"),
    }
    release_fd = _open_absolute_directory(release_dir)
    try:
        _write_exclusive(release_fd, RECEIPT_NAME, receipt_bytes, 0o444)
        _fault(fault_at, "receipt_written")
        _write_exclusive(release_fd, READY_NAME, _canonical_bytes(ready), 0o444)
        os.fchmod(release_fd, 0o555)
        os.fsync(release_fd)
    finally:
        os.close(release_fd)
    sha_parent_fd = _open_absolute_directory(release_dir.parent)
    try:
        os.fsync(sha_parent_fd)
    finally:
        os.close(sha_parent_fd)
    release_parent_fd = _open_absolute_directory(inputs.release_parent)
    try:
        os.fsync(release_parent_fd)
    finally:
        os.close(release_parent_fd)
    _fault(fault_at, "ready_written")
    return verify_sealed_release(release_dir)


_CEREMONY_PATH_OPTIONS = {
    "--authorization", "--attestation", "--data-root", "--directory",
    "--event-draft", "--history", "--identity-history",
    "--identity-trust-anchor", "--ledger", "--output", "--output-dir",
    "--owner-packet", "--preparation", "--previous-history", "--registry",
    "--repo-root", "--request", "--research-root", "--signature",
    "--signed-export", "--signed-migration-plan", "--signed-plan",
    "--trust-anchor",
}
_CEREMONY_OUTPUT_OPTIONS = {"--output", "--output-dir"}


def _validate_ceremony_arguments(
    arguments: Sequence[str], *, release_dir: Path, approved_output_root: Path
) -> None:
    approved_output_root = _normalize_absolute_path(
        str(approved_output_root), label="approved ceremony output root"
    )
    approved_fd = _open_absolute_directory(approved_output_root)
    os.close(approved_fd)
    if any(
        approved_output_root == root or root in approved_output_root.parents
        for root in (
            release_dir,
            Path(EXPECTED_AUTHORITATIVE_REPO_ROOT),
            Path(EXPECTED_AUTHORITATIVE_DATA_ROOT),
        )
    ):
        raise ReleaseBuildError("approved ceremony output root is protected")
    if len(arguments) < 2 or arguments[0] not in {
        "attestation", "migration", "projection", "publication", "registry"
    }:
        raise ReleaseBuildError("ceremony domain and subcommand are required")
    write_prefixes = {"--write"[:length] for length in range(3, len("--write") + 1)}
    if any(token.split("=", 1)[0] in write_prefixes for token in arguments):
        raise ReleaseBuildError(
            "the sealed ceremony launcher is public-only and forbids publication writes"
        )
    path_values: list[Tuple[str, Path]] = []
    index = 0
    while index < len(arguments):
        token = arguments[index]
        option = token.split("=", 1)[0]
        if option in _CEREMONY_PATH_OPTIONS:
            if "=" in token:
                raw_value = token.split("=", 1)[1]
            else:
                index += 1
                if index >= len(arguments):
                    raise ReleaseBuildError(f"ceremony path value is missing: {option}")
                raw_value = arguments[index]
            path_values.append(
                (option, _normalize_absolute_path(raw_value, label=f"ceremony {option}"))
            )
        index += 1
    outputs = [path for option, path in path_values if option in _CEREMONY_OUTPUT_OPTIONS]
    protected = [
        path for option, path in path_values if option not in _CEREMONY_OUTPUT_OPTIONS
    ]
    forbidden_roots = (
        release_dir,
        Path(EXPECTED_AUTHORITATIVE_REPO_ROOT),
        Path(EXPECTED_AUTHORITATIVE_DATA_ROOT),
    )
    for output in outputs:
        if any(output == root or root in output.parents for root in forbidden_roots):
            raise ReleaseBuildError("ceremony output enters a protected release/data root")
        if output == approved_output_root or approved_output_root not in output.parents:
            raise ReleaseBuildError("ceremony output escapes its approved workspace")
        relative_parts = output.relative_to(approved_output_root).parts
        descriptor = _open_absolute_directory(approved_output_root)
        try:
            for part in relative_parts[:-1]:
                try:
                    child = os.open(part, _READ_DIR_FLAGS, dir_fd=descriptor)
                except OSError as exc:
                    raise ReleaseBuildError(
                        "ceremony output parent must preexist without symlink components"
                    ) from exc
                os.close(descriptor)
                descriptor = child
            try:
                os.stat(relative_parts[-1], dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ReleaseBuildError("ceremony output already exists")
        finally:
            os.close(descriptor)
        for source in protected:
            if output == source:
                raise ReleaseBuildError("ceremony output aliases a protected input")
            try:
                source_metadata = os.lstat(source)
            except FileNotFoundError:
                continue
            if stat.S_ISDIR(source_metadata.st_mode) and source in output.parents:
                raise ReleaseBuildError("ceremony output enters a protected input directory")


def _production_seal_control(
    release_dir: Path, *, builder_origin: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Prove the ceremony principal cannot substitute the verified runtime paths."""

    effective_uid = os.geteuid()
    if effective_uid == 0:
        raise ReleaseBuildError("Gate E ceremony must not execute as root")
    bootstrap_app = _normalize_absolute_path(
        str(builder_origin.get("expected_bootstrap_root")),
        label="content-addressed bootstrap root",
    )
    release_parent = release_dir.parents[2]
    release_parent_ancestors = tuple(
        reversed(tuple(path for path in release_parent.parents if str(path) != "/"))
    )
    paths = (
        *release_parent_ancestors,
        release_parent,
        release_dir.parents[1],
        release_dir.parent,
        release_dir,
        release_dir / "app",
        release_dir / "venv",
        release_parent / "bootstrap",
        release_parent / "bootstrap/sha256",
        bootstrap_app.parent,
        bootstrap_app,
    )
    readonly_flag = getattr(os, "ST_RDONLY", getattr(stat, "ST_RDONLY", 1))
    readonly_mount = bool(os.statvfs(release_parent).f_flag & readonly_flag)
    census = []
    for path in paths:
        identity = _directory_identity(path)
        metadata = os.stat(path, follow_symlinks=False)
        if not stat.S_ISDIR(metadata.st_mode):
            raise ReleaseBuildError(f"Gate E protected path is not a directory: {path}")
        writable = os.access(path, os.W_OK, effective_ids=True)
        identity = {**identity, "effective_principal_writable": writable}
        census.append(identity)
        if not readonly_mount and (
            identity["uid"] == effective_uid
            or int(identity["mode"], 8) & 0o022
            or writable
        ):
            raise ReleaseBuildError(
                "Gate E requires a different non-writing principal or a read-only mount: "
                f"{path}"
            )
    write_denial_census: list[Mapping[str, Any]] = []

    def census_tree(root_path: Path) -> None:
        root_fd = _open_absolute_directory(root_path)
        try:
            def visit(parent_fd: int, prefix: Tuple[str, ...] = ()) -> None:
                for name in _directory_names(parent_fd):
                    before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                    relative = "/".join(prefix + (name,))
                    if stat.S_ISDIR(before.st_mode):
                        child = os.open(name, _READ_DIR_FLAGS, dir_fd=parent_fd)
                        try:
                            opened = os.fstat(child)
                            if (opened.st_dev, opened.st_ino) != (
                                before.st_dev, before.st_ino
                            ):
                                raise ReleaseBuildError(
                                    f"Gate E directory raced during ACL census: {relative}"
                                )
                            writable = os.access(
                                name, os.W_OK, dir_fd=parent_fd,
                                effective_ids=True, follow_symlinks=False,
                            )
                            write_denial_census.append(
                                {
                                    "absolute_path": str(root_path / relative),
                                    "effective_principal_writable": writable,
                                    "type": "directory",
                                }
                            )
                            if writable and not readonly_mount:
                                raise ReleaseBuildError(
                                    f"Gate E principal can write protected directory: {root_path / relative}"
                                )
                            visit(child, prefix + (name,))
                        finally:
                            os.close(child)
                    elif stat.S_ISREG(before.st_mode):
                        descriptor = os.open(name, _READ_FILE_FLAGS, dir_fd=parent_fd)
                        try:
                            opened = os.fstat(descriptor)
                            if (
                                not stat.S_ISREG(opened.st_mode)
                                or opened.st_nlink != 1
                                or (opened.st_dev, opened.st_ino)
                                != (before.st_dev, before.st_ino)
                            ):
                                raise ReleaseBuildError(
                                    f"Gate E protected file is raced or linked: {relative}"
                                )
                            writable = os.access(
                                name, os.W_OK, dir_fd=parent_fd,
                                effective_ids=True, follow_symlinks=False,
                            )
                        finally:
                            os.close(descriptor)
                        write_denial_census.append(
                            {
                                "absolute_path": str(root_path / relative),
                                "effective_principal_writable": writable,
                                "type": "file",
                            }
                        )
                        if writable and not readonly_mount:
                            raise ReleaseBuildError(
                                f"Gate E principal can write protected file: {root_path / relative}"
                            )
                    elif not stat.S_ISLNK(before.st_mode):
                        raise ReleaseBuildError(
                            f"Gate E protected tree contains special state: {relative}"
                        )

            visit(root_fd)
        finally:
            os.close(root_fd)

    census_tree(release_dir)
    census_tree(bootstrap_app)
    return {
        "effective_gid": os.getegid(),
        "effective_uid": effective_uid,
        "mechanism": "read_only_mount" if readonly_mount else "different_principal",
        "protected_directory_census": census,
        "protected_tree_write_denial_count": len(write_denial_census),
        "protected_tree_write_denial_sha256": _sha256(
            _canonical_bytes(write_denial_census)
        ),
        "readonly_mount": readonly_mount,
        "status": "PASS",
    }


def run_ceremony(
    release_dir: Path, ceremony_arguments: Sequence[str], *, approved_output_root: Path
) -> int:
    verified = verify_sealed_release(release_dir)
    release_dir = Path(str(verified["release_dir"]))
    app = Path(str(verified["app_path"]))
    python_path = Path(str(verified["python_path"]))
    _production_seal_control(
        release_dir,
        builder_origin=verified["builder_origin"],
    )
    verified_after_seal = verify_sealed_release(release_dir)
    if verified_after_seal != verified:
        raise ReleaseBuildError("sealed release identity changed during Gate E handoff")
    app = Path(str(verified_after_seal["app_path"]))
    python_path = Path(str(verified_after_seal["python_path"]))
    _validate_ceremony_arguments(
        ceremony_arguments,
        release_dir=release_dir,
        approved_output_root=approved_output_root,
    )
    with tempfile.TemporaryDirectory(prefix="caerus-alpha-ceremony-") as temporary:
        environment = _sanitized_environment(
            temporary_root=Path(temporary), venv_bin=python_path.parent
        )
        environment["CAERUS_CEREMONY_OUTPUT_ROOT"] = str(approved_output_root)
        _network_evidence, prefix = _network_isolation_contract(
            python_path, cwd=app, environment=environment
        )
        try:
            result = subprocess.run(
                [
                    *prefix,
                    *_isolated_module_command(
                        python_path,
                        app,
                        "projects.alpha_lab.factory.ceremony",
                        ceremony_arguments,
                    ),
                ],
                cwd=str(app),
                env=environment,
                check=False,
                close_fds=True,
            )
        finally:
            verify_sealed_release(release_dir)
    return result.returncode


def _common_input_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--source-archive", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--file-manifest", type=Path, required=True)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--release-input-manifest", type=Path, required=True)
    parser.add_argument("--release-parent", type=Path, required=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build, seal, and verify an Alpha Lab Gate A clean release",
        allow_abbrev=False,
    )
    commands = parser.add_subparsers(dest="command", required=True)
    preflight = commands.add_parser(
        "preflight", help="verify exact inputs without mutation", allow_abbrev=False
    )
    _common_input_arguments(preflight)
    build = commands.add_parser(
        "build", help="dry-run by default; --write is create-only", allow_abbrev=False
    )
    _common_input_arguments(build)
    build.add_argument("--write", action="store_true")
    build.add_argument("--authorized-release-input-sha256")
    build.add_argument("--interpreter", type=Path, default=Path("/usr/bin/python3"))
    build.add_argument("--temporary-parent", type=Path, default=Path("/tmp"))
    build.add_argument(
        "--fault-at",
        choices=(
            "source_directory_created", "source_payload_written", "source_ready",
            "release_directory_created", "app_extracted", "release_inputs_copied",
            "runtime_validated", "built_manifest_written", "receipt_written",
            "ready_written",
        ),
        help=argparse.SUPPRESS,
    )
    verify = commands.add_parser(
        "verify", help="independently verify a sealed release", allow_abbrev=False
    )
    verify.add_argument("--release-dir", type=Path, required=True)
    ceremony = commands.add_parser(
        "ceremony", help="verify READY, then invoke the exact sealed ceremony runtime",
        allow_abbrev=False,
    )
    ceremony.add_argument("--release-dir", type=Path, required=True)
    ceremony.add_argument("--ceremony-output-root", type=Path, required=True)
    ceremony.add_argument("ceremony_arguments", nargs=argparse.REMAINDER)
    return parser


def _inputs_from_arguments(arguments: argparse.Namespace) -> ReleaseInputs:
    return verify_release_inputs(
        repo_root=arguments.repo_root,
        source_archive=arguments.source_archive,
        source_manifest=arguments.source_manifest,
        file_manifest=arguments.file_manifest,
        wheelhouse=arguments.wheelhouse,
        release_input_manifest=arguments.release_input_manifest,
        release_parent=arguments.release_parent,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "verify":
            result = verify_sealed_release(arguments.release_dir)
        elif arguments.command == "ceremony":
            ceremony_arguments = list(arguments.ceremony_arguments)
            if ceremony_arguments[:1] == ["--"]:
                ceremony_arguments = ceremony_arguments[1:]
            if not ceremony_arguments:
                raise ReleaseBuildError("ceremony arguments are required after --")
            return run_ceremony(
                arguments.release_dir,
                ceremony_arguments,
                approved_output_root=arguments.ceremony_output_root,
            )
        else:
            inputs = _inputs_from_arguments(arguments)
            if arguments.command == "preflight":
                result = {
                    "schema_version": "caerus_alpha_lab_release_input_preflight_v1",
                    "status": "PASS",
                    "release_input_sha256": inputs.release_input_sha256,
                    "source_archive_sha256": inputs.source.archive_sha256,
                    "wheel_count": inputs.release_input["dependencies"]["wheel_count"],
                    "builder_origin": inputs.builder_origin,
                    "write": False,
                }
            else:
                result = build_release(
                    inputs,
                    write=arguments.write,
                    authorized_release_input_sha256=arguments.authorized_release_input_sha256,
                    interpreter=arguments.interpreter,
                    temporary_parent=arguments.temporary_parent,
                    fault_at=arguments.fault_at,
                )
    except (ReleaseBuildError, OSError, ValueError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

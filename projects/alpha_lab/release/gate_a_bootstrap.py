"""Minimal create-only bootstrap for the Alpha Lab Gate A release builder.

Invoke this file directly with CPython ``-I -S -B`` after an owner has
authorized its exact SHA-256 and the source-archive SHA-256.  It imports only
the standard library, rejects all source links and special members, and creates
the content-addressed bootstrap checkout without using the checkout it creates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple


SOURCE_SCHEMA = "caerus_alpha_lab_clean_release_source_v1"
FILE_MANIFEST_SCHEMA = "canonical-json-sorted-file-and-symlink-records-v1"
READY_SCHEMA = "caerus_alpha_lab_gate_a_bootstrap_ready_v1"
READY_NAME = "BOOTSTRAP_READY"
BOOTSTRAP_RELATIVE_PATH = "projects/alpha_lab/release/gate_a_bootstrap.py"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SHA1 = re.compile(r"[0-9a-f]{40}")
_MODE = re.compile(r"[0-7]{4}")
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_DIR_FLAGS = os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC
_FILE_FLAGS = os.O_RDONLY | _O_NOFOLLOW | _O_CLOEXEC


class BootstrapError(RuntimeError):
    """A bootstrap authorization, source, or filesystem invariant failed."""


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _strict_json(value: bytes, *, label: str, list_required: bool = False) -> Any:
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise BootstrapError(f"duplicate JSON key in {label}: {key}")
            result[key] = item
        return result

    try:
        parsed = json.loads(
            value.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                BootstrapError(f"non-finite JSON value in {label}: {token}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BootstrapError(f"invalid JSON in {label}") from exc
    expected_type = list if list_required else dict
    if not isinstance(parsed, expected_type) or value != _canonical(parsed):
        raise BootstrapError(f"{label} must be exact canonical JSON")
    return parsed


def _absolute(value: Path, *, label: str) -> Path:
    raw = str(value)
    if not raw.startswith("/") or "\x00" in raw or os.path.normpath(raw) != raw:
        raise BootstrapError(f"{label} must be a canonical absolute path")
    if raw == "/":
        raise BootstrapError(f"{label} cannot be the filesystem root")
    return value


def _parts(value: str, *, label: str) -> Tuple[str, ...]:
    if not value or "\x00" in value or "\\" in value or value.endswith("/"):
        raise BootstrapError(f"unsafe {label}")
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        raise BootstrapError(f"unsafe {label}")
    return tuple(path.parts)


def _open_dir(path: Path, *, create: bool = False) -> int:
    path = _absolute(path, label="directory")
    descriptor = os.open("/", _DIR_FLAGS)
    try:
        for part in path.parts[1:]:
            try:
                child = os.open(part, _DIR_FLAGS, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(part, 0o755, dir_fd=descriptor)
                os.fsync(descriptor)
                child = os.open(part, _DIR_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _open_file(path: Path) -> Tuple[int, os.stat_result]:
    path = _absolute(path, label="input file")
    parent = _open_dir(path.parent)
    try:
        descriptor = os.open(path.name, _FILE_FLAGS, dir_fd=parent)
    finally:
        os.close(parent)
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        os.close(descriptor)
        raise BootstrapError(f"input is not a single-link regular file: {path}")
    return descriptor, metadata


def _read_file(path: Path) -> bytes:
    descriptor, before = _open_file(path)
    chunks = []
    try:
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
        raise BootstrapError(f"input changed while reading: {path}")
    return b"".join(chunks)


def _validated_records(raw: Any) -> Tuple[Mapping[str, Any], ...]:
    records = []
    seen = set()
    for record in raw:
        if not isinstance(record, Mapping) or set(record) != {
            "bytes", "mode", "path", "sha256", "type"
        } or record.get("type") != "file":
            raise BootstrapError("bootstrap source manifest permits regular files only")
        path = str(record.get("path"))
        _parts(path, label="file-manifest path")
        if path in seen:
            raise BootstrapError(f"duplicate file-manifest path: {path}")
        seen.add(path)
        if (
            not isinstance(record.get("bytes"), int)
            or record["bytes"] < 0
            or not _SHA256.fullmatch(str(record.get("sha256", "")))
            or not _MODE.fullmatch(str(record.get("mode", "")))
        ):
            raise BootstrapError(f"invalid file-manifest record: {path}")
        records.append(dict(record))
    return tuple(records)


def _expected_directories(records: Iterable[Mapping[str, Any]]) -> set[str]:
    leaves = {str(record["path"]) for record in records}
    directories = set()
    for leaf in leaves:
        parts = PurePosixPath(leaf).parts
        for index in range(1, len(parts)):
            parent = str(PurePosixPath(*parts[:index]))
            if parent in leaves:
                raise BootstrapError(f"source file is used as a directory: {parent}")
            directories.add(parent)
    return directories


def _inspect_tar(
    archive_path: Path, *, expected_commit_sha: Optional[str] = None,
    expected_tree_oid_sha1: Optional[str] = None,
) -> Tuple[Tuple[Mapping[str, Any], ...], Tuple[Mapping[str, Any], ...]]:
    descriptor, before = _open_file(archive_path)
    records = []
    directories = []
    names = set()
    git_files: Dict[Tuple[str, ...], Tuple[str, bytes]] = {}
    try:
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            with tarfile.open(fileobj=stream, mode="r:") as archive:
                if expected_commit_sha is not None and archive.pax_headers.get(
                    "comment"
                ) != expected_commit_sha:
                    raise BootstrapError("git-archive PAX commit identity drift")
                for member in archive:
                    name = member.name
                    _parts(name, label="tar member")
                    if name in names:
                        raise BootstrapError(f"duplicate tar member: {name}")
                    names.add(name)
                    if getattr(member, "sparse", None) is not None or any(
                        str(key).startswith("GNU.sparse") for key in member.pax_headers
                    ):
                        raise BootstrapError(f"sparse tar member is forbidden: {name}")
                    mode = format(member.mode & 0o7777, "04o")
                    if member.isdir():
                        if member.size != 0:
                            raise BootstrapError(f"tar directory has bytes: {name}")
                        directories.append(
                            {"mode": mode, "path": name, "type": "directory"}
                        )
                        continue
                    if not member.isfile():
                        raise BootstrapError(f"tar link or special member is forbidden: {name}")
                    source = archive.extractfile(member)
                    if source is None:
                        raise BootstrapError(f"tar file lacks content: {name}")
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
                    records.append(
                        {
                            "bytes": total,
                            "mode": mode,
                            "path": name,
                            "sha256": digest.hexdigest(),
                            "type": "file",
                        }
                    )
                    git_files[PurePosixPath(name).parts] = (
                        "100755" if member.mode & 0o111 else "100644",
                        git_blob.digest(),
                    )
        after = os.fstat(descriptor)
    except (OSError, tarfile.TarError) as exc:
        raise BootstrapError("cannot inspect exact source tar") from exc
    finally:
        os.close(descriptor)
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
    ):
        raise BootstrapError("source tar changed during inspection")
    leaves = {str(record["path"]) for record in records}
    for directory in directories:
        parts = PurePosixPath(str(directory["path"])).parts
        if any(
            str(PurePosixPath(*parts[:index])) in leaves
            for index in range(1, len(parts))
        ):
            raise BootstrapError("source file is used as a directory")
    if expected_tree_oid_sha1 is not None:
        tree: Dict[str, Any] = {}
        for parts, leaf in git_files.items():
            node = tree
            for part in parts[:-1]:
                child = node.setdefault(part, {})
                if not isinstance(child, dict):
                    raise BootstrapError("Git file/tree collision")
                node = child
            if parts[-1] in node:
                raise BootstrapError("duplicate Git tree path")
            node[parts[-1]] = leaf

        def tree_oid(node: Mapping[str, Any]) -> bytes:
            entries = []
            for name, value in node.items():
                is_tree = isinstance(value, dict)
                entries.append((name.encode("utf-8") + (b"/" if is_tree else b""), name, value))
            body = bytearray()
            for _key, name, value in sorted(entries, key=lambda item: item[0]):
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
            raise BootstrapError("source Git tree OID drift")
    return tuple(records), tuple(directories)


def _verify_inputs(
    *, archive: Path, source_manifest_path: Path, file_manifest_path: Path,
    authorized_archive_sha256: str, authorized_bootstrap_sha256: str,
) -> Mapping[str, Any]:
    if not _SHA256.fullmatch(authorized_archive_sha256) or not _SHA256.fullmatch(
        authorized_bootstrap_sha256
    ):
        raise BootstrapError("exact owner-authorized SHA-256 values are required")
    source_bytes = _read_file(source_manifest_path)
    source = _strict_json(source_bytes, label="source manifest")
    if set(source) != {
        "archive_bytes", "archive_format", "archive_sha256", "commit_sha",
        "file_manifest_member_count", "file_manifest_schema",
        "file_manifest_sha256", "schema_version", "tree_oid_sha1",
    } or source.get("schema_version") != SOURCE_SCHEMA or source.get(
        "archive_format"
    ) != "git-archive-tar" or source.get("file_manifest_schema") != FILE_MANIFEST_SCHEMA:
        raise BootstrapError("source manifest schema drift")
    if not _SHA1.fullmatch(str(source.get("commit_sha", ""))) or not _SHA1.fullmatch(
        str(source.get("tree_oid_sha1", ""))
    ):
        raise BootstrapError("source Git identity drift")
    file_bytes = _read_file(file_manifest_path)
    records = _validated_records(
        _strict_json(file_bytes, label="file manifest", list_required=True)
    )
    archive_bytes = _read_file(archive)
    if (
        source.get("archive_sha256") != authorized_archive_sha256
        or _sha256(archive_bytes) != authorized_archive_sha256
        or source.get("archive_bytes") != len(archive_bytes)
        or source.get("file_manifest_sha256") != _sha256(file_bytes)
        or source.get("file_manifest_member_count") != len(records)
    ):
        raise BootstrapError("owner-authorized source identity drift")
    inspected, directories = _inspect_tar(
        archive,
        expected_commit_sha=str(source["commit_sha"]),
        expected_tree_oid_sha1=str(source["tree_oid_sha1"]),
    )
    if inspected != records:
        raise BootstrapError("tar and file manifest differ")
    expected_directories = _expected_directories(records)
    directory_names = [str(record["path"]) for record in directories]
    if len(directory_names) != len(expected_directories) or set(
        directory_names
    ) != expected_directories:
        raise BootstrapError("tar directory census drift")
    self_bytes = _read_file(Path(__file__).absolute())
    source_record = next(
        (record for record in records if record["path"] == BOOTSTRAP_RELATIVE_PATH),
        None,
    )
    if (
        source_record is None
        or _sha256(self_bytes) != authorized_bootstrap_sha256
        or source_record.get("sha256") != authorized_bootstrap_sha256
        or source_record.get("bytes") != len(self_bytes)
    ):
        raise BootstrapError("executed bootstrap is not the owner-authorized source member")
    return {
        "archive_bytes": archive_bytes,
        "directories": directories,
        "file_manifest_bytes": file_bytes,
        "records": records,
        "source": source,
        "source_manifest_bytes": source_bytes,
    }


def _mkdir(parent_fd: int, name: str, mode: int = 0o700) -> int:
    if "/" in name or name in {"", ".", ".."}:
        raise BootstrapError(f"unsafe directory component: {name}")
    os.mkdir(name, mode, dir_fd=parent_fd)
    os.fsync(parent_fd)
    return os.open(name, _DIR_FLAGS, dir_fd=parent_fd)


def _open_or_create(parent_fd: int, name: str) -> int:
    try:
        return os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        return _mkdir(parent_fd, name, 0o755)


def _ensure(root_fd: int, parts: Sequence[str]) -> int:
    descriptor = os.dup(root_fd)
    try:
        for part in parts:
            try:
                child = os.open(part, _DIR_FLAGS, dir_fd=descriptor)
            except FileNotFoundError:
                child = _mkdir(descriptor, part)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _write(parent_fd: int, name: str, value: bytes, mode: int = 0o600) -> None:
    descriptor = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_CLOEXEC,
        mode,
        dir_fd=parent_fd,
    )
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise BootstrapError(f"new bootstrap file is not regular: {name}")
        offset = 0
        while offset < len(value):
            offset += os.write(descriptor, value[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.fsync(parent_fd)


def _extract(archive_path: Path, records: Sequence[Mapping[str, Any]], app_fd: int) -> None:
    expected = {str(record["path"]): record for record in records}
    descriptor, before = _open_file(archive_path)
    seen = []
    try:
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            with tarfile.open(fileobj=stream, mode="r:") as archive:
                for member in archive:
                    parts = _parts(member.name, label="tar extraction member")
                    if getattr(member, "sparse", None) is not None or any(
                        str(key).startswith("GNU.sparse") for key in member.pax_headers
                    ):
                        raise BootstrapError(f"sparse tar member is forbidden: {member.name}")
                    if member.isdir():
                        child = _ensure(app_fd, parts)
                        try:
                            os.fchmod(child, member.mode & 0o777)
                            os.fsync(child)
                        finally:
                            os.close(child)
                        continue
                    record = expected.get(member.name)
                    if record is None or not member.isfile():
                        raise BootstrapError(f"unmanifested or unsafe tar member: {member.name}")
                    parent = _ensure(app_fd, parts[:-1])
                    try:
                        output = os.open(
                            parts[-1],
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_CLOEXEC,
                            int(str(record["mode"]), 8),
                            dir_fd=parent,
                        )
                        digest = hashlib.sha256()
                        total = 0
                        try:
                            metadata = os.fstat(output)
                            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                                raise BootstrapError(f"new bootstrap member is not regular: {member.name}")
                            source = archive.extractfile(member)
                            if source is None:
                                raise BootstrapError(f"missing tar bytes: {member.name}")
                            while True:
                                chunk = source.read(1024 * 1024)
                                if not chunk:
                                    break
                                digest.update(chunk)
                                total += len(chunk)
                                offset = 0
                                while offset < len(chunk):
                                    offset += os.write(output, chunk[offset:])
                            os.fchmod(output, int(str(record["mode"]), 8))
                            os.fsync(output)
                        finally:
                            os.close(output)
                        if total != record["bytes"] or digest.hexdigest() != record["sha256"]:
                            raise BootstrapError(f"extracted member identity drift: {member.name}")
                        os.fsync(parent)
                    finally:
                        os.close(parent)
                    seen.append(member.name)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if seen != [record["path"] for record in records] or (
        before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns
    ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise BootstrapError("source changed or member census drifted during extraction")


def _hash_at(parent_fd: int, name: str) -> Tuple[int, str, os.stat_result]:
    descriptor = os.open(name, _FILE_FLAGS, dir_fd=parent_fd)
    digest = hashlib.sha256()
    total = 0
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise BootstrapError(f"bootstrap entry is not regular: {name}")
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
        raise BootstrapError(f"bootstrap entry changed while hashing: {name}")
    return total, digest.hexdigest(), before


def _scan(root_fd: int, prefix: Tuple[str, ...] = ()) -> list[Dict[str, Any]]:
    records = []
    for name in sorted(os.listdir(root_fd)):
        metadata = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        relative = "/".join(prefix + (name,))
        if stat.S_ISDIR(metadata.st_mode):
            child = os.open(name, _DIR_FLAGS, dir_fd=root_fd)
            try:
                opened = os.fstat(child)
                if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                    raise BootstrapError(f"bootstrap directory raced: {relative}")
                records.append(
                    {"mode": format(stat.S_IMODE(opened.st_mode), "04o"),
                     "path": relative, "type": "directory"}
                )
                records.extend(_scan(child, prefix + (name,)))
            finally:
                os.close(child)
        elif stat.S_ISREG(metadata.st_mode):
            total, digest, opened = _hash_at(root_fd, name)
            records.append(
                {"bytes": total, "mode": format(stat.S_IMODE(opened.st_mode), "04o"),
                 "path": relative, "sha256": digest, "type": "file"}
            )
        else:
            raise BootstrapError(f"bootstrap tree link or special entry: {relative}")
    return sorted(records, key=lambda record: record["path"])


def _seal(root_fd: int) -> None:
    for name in sorted(os.listdir(root_fd)):
        metadata = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        if stat.S_ISDIR(metadata.st_mode):
            child = os.open(name, _DIR_FLAGS, dir_fd=root_fd)
            try:
                _seal(child)
                os.fchmod(child, 0o555)
                os.fsync(child)
            finally:
                os.close(child)
        elif stat.S_ISREG(metadata.st_mode):
            descriptor = os.open(name, _FILE_FLAGS, dir_fd=root_fd)
            try:
                os.fchmod(descriptor, 0o555 if metadata.st_mode & 0o111 else 0o444)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        else:
            raise BootstrapError(f"cannot seal bootstrap entry: {name}")
    os.fsync(root_fd)


def _expected_app_records(inputs: Mapping[str, Any]) -> list[Dict[str, Any]]:
    records: list[Dict[str, Any]] = [
        {"mode": "0555", "path": directory["path"], "type": "directory"}
        for directory in inputs["directories"]
    ]
    records.extend(
        {
            "bytes": record["bytes"],
            "mode": "0555" if int(str(record["mode"]), 8) & 0o111 else "0444",
            "path": record["path"],
            "sha256": record["sha256"],
            "type": "file",
        }
        for record in inputs["records"]
    )
    return sorted(records, key=lambda record: record["path"])


def _ready_payload(inputs: Mapping[str, Any], app_records: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    return {
        "app_record_count": len(app_records),
        "app_records_sha256": _sha256(_canonical(list(app_records))),
        "bootstrap_sha256": _sha256(_read_file(Path(__file__).absolute())),
        "file_manifest_sha256": _sha256(inputs["file_manifest_bytes"]),
        "schema_version": READY_SCHEMA,
        "source_archive_sha256": inputs["source"]["archive_sha256"],
        "source_manifest_sha256": _sha256(inputs["source_manifest_bytes"]),
        "status": "READY",
    }


def _read_at(parent_fd: int, name: str) -> bytes:
    descriptor = os.open(name, _FILE_FLAGS, dir_fd=parent_fd)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise BootstrapError(f"bootstrap control is not regular: {name}")
        chunks = []
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
        raise BootstrapError(f"bootstrap control changed: {name}")
    return b"".join(chunks)


def _verify_ready(target: Path, inputs: Mapping[str, Any]) -> Mapping[str, Any]:
    root = _open_dir(target)
    try:
        if stat.S_IMODE(os.fstat(root).st_mode) != 0o555 or set(os.listdir(root)) != {
            "app", "source.tar", "source_manifest.json", "file_manifest.json", READY_NAME
        }:
            raise BootstrapError("bootstrap target is incomplete, mutable, or has extra state")
        app = os.open("app", _DIR_FLAGS, dir_fd=root)
        try:
            records = _scan(app)
            if stat.S_IMODE(os.fstat(app).st_mode) != 0o555:
                raise BootstrapError("bootstrap app root is mutable")
        finally:
            os.close(app)
        expected_records = _expected_app_records(inputs)
        if records != expected_records:
            raise BootstrapError("bootstrap app differs from the authorized source")
        for name, expected in (
            ("source.tar", inputs["archive_bytes"]),
            ("source_manifest.json", inputs["source_manifest_bytes"]),
            ("file_manifest.json", inputs["file_manifest_bytes"]),
        ):
            value = _read_at(root, name)
            metadata = os.stat(name, dir_fd=root, follow_symlinks=False)
            if value != expected or stat.S_IMODE(metadata.st_mode) != 0o444:
                raise BootstrapError(f"bootstrap input copy drift: {name}")
        ready_bytes = _read_at(root, READY_NAME)
        ready = _strict_json(ready_bytes, label=READY_NAME)
        if ready != _ready_payload(inputs, expected_records):
            raise BootstrapError("bootstrap READY identity drift")
        if any(
            record["mode"] not in ({"0555"} if record["type"] == "directory" else {"0444", "0555"})
            for record in records
        ):
            raise BootstrapError("bootstrap app contains mutable state")
    finally:
        os.close(root)
    return {
        "app_path": str(target / "app"),
        "bootstrap_ready_sha256": _sha256(ready_bytes),
        "source_archive_sha256": inputs["source"]["archive_sha256"],
        "status": "READY",
    }


def bootstrap(
    *, archive: Path, source_manifest: Path, file_manifest: Path,
    release_parent: Path, authorized_source_archive_sha256: str,
    authorized_bootstrap_sha256: str, write: bool,
) -> Mapping[str, Any]:
    release_parent = _absolute(release_parent, label="release parent")
    inputs = _verify_inputs(
        archive=archive,
        source_manifest_path=source_manifest,
        file_manifest_path=file_manifest,
        authorized_archive_sha256=authorized_source_archive_sha256,
        authorized_bootstrap_sha256=authorized_bootstrap_sha256,
    )
    target = (
        release_parent / "bootstrap/sha256" / authorized_source_archive_sha256
    )
    if not write:
        return {
            "app_path": str(target / "app"),
            "bootstrap_sha256": authorized_bootstrap_sha256,
            "source_archive_sha256": authorized_source_archive_sha256,
            "status": "DRY_RUN_VERIFIED",
            "write": False,
        }
    parent = _open_dir(release_parent, create=True)
    try:
        bootstrap_fd = _open_or_create(parent, "bootstrap")
        try:
            sha_fd = _open_or_create(bootstrap_fd, "sha256")
        finally:
            os.close(bootstrap_fd)
        try:
            try:
                target_fd = _mkdir(sha_fd, authorized_source_archive_sha256)
            except FileExistsError:
                os.close(sha_fd)
                return _verify_ready(target, inputs)
        finally:
            if "target_fd" in locals():
                os.close(sha_fd)
        try:
            app_fd = _mkdir(target_fd, "app")
            try:
                _extract(archive, inputs["records"], app_fd)
                _seal(app_fd)
                os.fchmod(app_fd, 0o555)
                os.fsync(app_fd)
                app_records = _scan(app_fd)
            finally:
                os.close(app_fd)
            _write(target_fd, "source.tar", inputs["archive_bytes"], 0o444)
            _write(target_fd, "source_manifest.json", inputs["source_manifest_bytes"], 0o444)
            _write(target_fd, "file_manifest.json", inputs["file_manifest_bytes"], 0o444)
            ready = _ready_payload(inputs, app_records)
            _write(target_fd, READY_NAME, _canonical(ready), 0o444)
            os.fchmod(target_fd, 0o555)
            os.fsync(target_fd)
        finally:
            os.close(target_fd)
        os.fsync(parent)
    finally:
        os.close(parent)
    return _verify_ready(target, inputs)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Materialize the owner-authorized Gate A bootstrap checkout",
        allow_abbrev=False,
    )
    parser.add_argument("--source-archive", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--file-manifest", type=Path, required=True)
    parser.add_argument("--release-parent", type=Path, required=True)
    parser.add_argument("--authorized-source-archive-sha256", required=True)
    parser.add_argument("--authorized-bootstrap-sha256", required=True)
    parser.add_argument("--write", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = bootstrap(
            archive=arguments.source_archive,
            source_manifest=arguments.source_manifest,
            file_manifest=arguments.file_manifest,
            release_parent=arguments.release_parent,
            authorized_source_archive_sha256=arguments.authorized_source_archive_sha256,
            authorized_bootstrap_sha256=arguments.authorized_bootstrap_sha256,
            write=arguments.write,
        )
    except (BootstrapError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc), "status": "FAIL"}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

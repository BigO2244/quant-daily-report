"""Create-only Gate A packet transfer and dirty-checkout preservation receipts.

This module deliberately uses only the Python standard library.  It is intended
to be copied to, and executed by, the privileged transfer principal without
importing code from either the staging packet or the dirty checkout.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import datetime as dt
import grp
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence


VERSION = "1.4"
TRANSFER_SCHEMA = "caerus_alpha_lab_gate_a_protected_transfer_v1"
DIRTY_SCHEMA = "caerus_alpha_lab_dirty_snapshot_v2"
SEMANTIC_SCHEMA = "caerus_alpha_lab_dirty_snapshot_semantic_v2"
RECEIPT_NAME = "TRANSFER_RECEIPT.json"
GIT = "/usr/bin/git"
GIT_ENVIRONMENT = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_TERMINAL_PROMPT": "0",
    "LANG": "C",
    "LC_ALL": "C",
}
EXPECTED_INPUTS = {
    "gate_a_bootstrap.py",
    "source.tar",
    "source_manifest.json",
    "file_manifest.json",
    "release_input_manifest.json",
    "wheelhouse",
}
SHA256 = re.compile(r"[0-9a-f]{64}")
MODE = re.compile(r"[0-7]{4}")
O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
DIR_FLAGS = os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC
FILE_FLAGS = os.O_RDONLY | O_NOFOLLOW | O_CLOEXEC
PROBE_FILE_FLAGS = FILE_FLAGS | getattr(os, "O_NONBLOCK", 0)


class HandoffError(RuntimeError):
    """A handoff authorization, input, or filesystem invariant failed."""


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _strict_json(value: bytes, *, label: str) -> Any:
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise HandoffError(f"duplicate JSON key in {label}: {key}")
            result[key] = item
        return result

    try:
        parsed = json.loads(
            value.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                HandoffError(f"non-finite JSON value in {label}: {token}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HandoffError(f"invalid JSON in {label}") from exc
    if value != _canonical(parsed):
        raise HandoffError(f"{label} must be exact canonical JSON")
    return parsed


def _hash(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or SHA256.fullmatch(value) is None:
        raise HandoffError(f"{label} must be a lowercase SHA-256")
    return value


def _absolute(path: Path, *, label: str) -> Path:
    raw = str(path)
    if (
        not raw.startswith("/")
        or raw.startswith("//")
        or raw == "/"
        or "\x00" in raw
        or os.path.normpath(raw) != raw
    ):
        raise HandoffError(f"{label} must be a canonical absolute path")
    return path


def _safe_relative(value: str, *, label: str) -> tuple[str, ...]:
    if not value or "\x00" in value or "\\" in value or value.endswith("/"):
        raise HandoffError(f"unsafe {label}: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        raise HandoffError(f"unsafe {label}: {value!r}")
    return tuple(path.parts)


def _open_dir(path: Path) -> int:
    path = _absolute(path, label="directory")
    fd = os.open("/", DIR_FLAGS)
    try:
        for part in path.parts[1:]:
            child = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except Exception:
        os.close(fd)
        raise


def _stable_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_uid,
        value.st_gid,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _metadata(value: os.stat_result) -> dict[str, Any]:
    return {
        "gid": value.st_gid,
        "mode": format(stat.S_IMODE(value.st_mode), "04o"),
        "nlink": value.st_nlink,
        "uid": value.st_uid,
    }


def _read_regular_at(
    parent_fd: int, name: str, *, label: str,
    require_single_link: bool = True,
) -> tuple[bytes, os.stat_result]:
    try:
        fd = os.open(name, PROBE_FILE_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        raise HandoffError(f"cannot open {label} without following links") from exc
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or (
            require_single_link and before.st_nlink != 1
        ):
            raise HandoffError(f"{label} is not an approved regular file")
        # O_NONBLOCK prevents a malicious FIFO from hanging before fstat.  Once
        # the descriptor is proven regular, restore ordinary blocking semantics
        # before any content read; the descriptor is never returned to callers.
        os.set_blocking(fd, True)
        chunks = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(fd)
        if _stable_identity(before) != _stable_identity(after):
            raise HandoffError(f"{label} changed while reading")
        value = b"".join(chunks)
        if len(value) != before.st_size:
            raise HandoffError(f"short read from {label}")
        return value, before
    finally:
        os.close(fd)


def _write_all(fd: int, value: bytes) -> None:
    view = memoryview(value)
    while view:
        count = os.write(fd, view)
        if count is None or count <= 0:
            raise HandoffError("short write while creating protected input")
        view = view[count:]


def _create_file_at(
    parent_fd: int,
    name: str,
    value: bytes,
    mode: int,
    *,
    owner: tuple[int, int] | None = None,
) -> os.stat_result:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_NOFOLLOW | O_CLOEXEC
    try:
        fd = os.open(name, flags, mode, dir_fd=parent_fd)
    except OSError as exc:
        raise HandoffError(f"cannot exclusively create target: {name}") from exc
    try:
        _write_all(fd, value)
        if owner is not None:
            os.fchown(fd, *owner)
        os.fchmod(fd, mode)
        os.fsync(fd)
        result = os.fstat(fd)
        if not stat.S_ISREG(result.st_mode) or result.st_nlink != 1:
            raise HandoffError(f"created target is not a single-link file: {name}")
        if result.st_size != len(value):
            raise HandoffError(f"created target has a short byte count: {name}")
        return result
    finally:
        os.close(fd)


def _seal_regular_at(
    parent_fd: int, name: str, value: bytes,
    expected: os.stat_result, *, label: str,
) -> os.stat_result:
    """Seal one already-created target file and verify identity and content."""
    try:
        fd = os.open(name, FILE_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        raise HandoffError(f"cannot reopen {label} for sealing") from exc
    try:
        before = os.fstat(fd)
        if _stable_identity(before) != _stable_identity(expected):
            raise HandoffError(f"{label} changed before sealing")
        os.fchmod(fd, 0o444)
        os.fsync(fd)
        sealed = os.fstat(fd)
        if (
            not stat.S_ISREG(sealed.st_mode)
            or stat.S_IMODE(sealed.st_mode) != 0o444
            or sealed.st_nlink != 1
            or (
                sealed.st_dev, sealed.st_ino, sealed.st_uid, sealed.st_gid,
                sealed.st_nlink, sealed.st_size, sealed.st_mtime_ns,
            ) != (
                before.st_dev, before.st_ino, before.st_uid, before.st_gid,
                before.st_nlink, before.st_size, before.st_mtime_ns,
            )
        ):
            raise HandoffError(f"{label} seal identity drift")
        os.lseek(fd, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
        after = os.fstat(fd)
        if (
            _stable_identity(sealed) != _stable_identity(after)
            or total != len(value)
            or digest.hexdigest() != _sha256(value)
        ):
            raise HandoffError(f"{label} changed while verifying its seal")
        return after
    finally:
        os.close(fd)


def _seal_directory(fd: int, expected: os.stat_result, *, label: str) -> os.stat_result:
    before = os.fstat(fd)
    if _stable_identity(before) != _stable_identity(expected):
        raise HandoffError(f"{label} changed before sealing")
    os.fchmod(fd, 0o555)
    os.fsync(fd)
    after = os.fstat(fd)
    if (
        not stat.S_ISDIR(after.st_mode)
        or stat.S_IMODE(after.st_mode) != 0o555
        or (
            after.st_dev, after.st_ino, after.st_uid, after.st_gid,
            after.st_nlink,
        ) != (
            before.st_dev, before.st_ino, before.st_uid, before.st_gid,
            before.st_nlink,
        )
    ):
        raise HandoffError(f"{label} seal identity drift")
    return after


def _is_privileged() -> bool:
    if os.geteuid() == 0:
        return True
    try:
        names = {grp.getgrgid(gid).gr_name for gid in set(os.getgroups()) | {os.getegid()}}
    except KeyError:
        names = set()
    return bool(names & {"admin", "wheel", "sudo"})


def _verify_root_owned_ancestors(path: Path) -> None:
    """Require the complete existing target hierarchy to be root-controlled."""
    path = _absolute(path, label="protected target parent")
    fd = os.open("/", DIR_FLAGS)
    try:
        for index, part in enumerate(("/", *path.parts[1:])):
            if index:
                child = os.open(part, DIR_FLAGS, dir_fd=fd)
                os.close(fd)
                fd = child
            metadata = os.fstat(fd)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != 0
                or stat.S_IMODE(metadata.st_mode) & 0o022
            ):
                raise HandoffError(
                    f"protected target ancestor is not root-owned and non-writable: {part}"
                )
    finally:
        os.close(fd)


def _record(value: bytes, metadata: os.stat_result, path: Path) -> dict[str, Any]:
    return {
        "absolute_path": str(path),
        "bytes": len(value),
        **_metadata(metadata),
        "sha256": _sha256(value),
    }


def _expect_file_record(record: Any, *, label: str) -> tuple[int, str]:
    if not isinstance(record, Mapping):
        raise HandoffError(f"missing {label} record")
    size = record.get("bytes")
    digest = record.get("sha256")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise HandoffError(f"invalid byte count in {label}")
    return size, _hash(digest, label=f"{label} hash")


def _same_record(record: Any, value: bytes, *, label: str) -> None:
    size, digest = _expect_file_record(record, label=label)
    if size != len(value) or digest != _sha256(value):
        raise HandoffError(f"{label} byte/hash drift")


def _summary_core(summary: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    records = summary.get("core_artifact_records")
    if not isinstance(records, list):
        raise HandoffError("identity summary has no core artifact records")
    found = [item for item in records if isinstance(item, Mapping) and item.get("path") == name]
    if len(found) != 1:
        raise HandoffError(f"identity summary must contain exactly one {name} record")
    if summary.get("core_artifact_record_count") != len(records):
        raise HandoffError("identity-summary core record count drift")
    if summary.get("core_artifact_records_sha256") != _sha256(_canonical(records)):
        raise HandoffError("identity-summary core record hash drift")
    return found[0]


def _validate_packet(
    *, summary_bytes: bytes, summary: Mapping[str, Any], values: Mapping[str, bytes]
) -> list[Mapping[str, Any]]:
    if summary.get("schema_version") != "caerus_alpha_lab_post_commit_identity_packet_v1" or summary.get("status") != "PASS":
        raise HandoffError("identity summary schema/status is not approved")
    for name in (
        "source.tar", "source_manifest.json", "file_manifest.json",
        "release_input_manifest.json", "gate_a_bootstrap.py",
    ):
        _same_record(_summary_core(summary, name), values[name], label=f"summary {name}")

    source = _strict_json(values["source_manifest.json"], label="source manifest")
    files = _strict_json(values["file_manifest.json"], label="file manifest")
    release = _strict_json(values["release_input_manifest.json"], label="release input manifest")
    if not isinstance(source, Mapping) or not isinstance(files, list) or not isinstance(release, Mapping):
        raise HandoffError("source and release manifest top-level types are invalid")
    if source.get("archive_sha256") != _sha256(values["source.tar"]) or source.get("archive_bytes") != len(values["source.tar"]):
        raise HandoffError("source archive does not match source manifest")
    if source.get("file_manifest_sha256") != _sha256(values["file_manifest.json"]) or source.get("file_manifest_member_count") != len(files):
        raise HandoffError("file manifest does not match source manifest")
    source_summary = summary.get("source")
    if not isinstance(source_summary, Mapping):
        raise HandoffError("identity summary source section is invalid")
    _same_record(source_summary.get("archive"), values["source.tar"], label="summary source archive")
    _same_record(source_summary.get("source_manifest"), values["source_manifest.json"], label="summary source manifest")
    _same_record(source_summary.get("file_manifest"), values["file_manifest.json"], label="summary file manifest")
    release_source = release.get("source")
    if not isinstance(release_source, Mapping) or release_source.get("source_manifest") != source or release_source.get("source_manifest_sha256") != _sha256(values["source_manifest.json"]):
        raise HandoffError("release input source identity drift")

    _same_record(summary.get("release_input"), values["release_input_manifest.json"], label="summary release input")
    tools = summary.get("reviewed_tools")
    if not isinstance(tools, Mapping):
        raise HandoffError("identity summary reviewed-tools section is invalid")
    _same_record(tools.get("gate_a_bootstrap"), values["gate_a_bootstrap.py"], label="summary bootstrap")

    dependencies = summary.get("dependencies")
    release_dependencies = release.get("dependencies")
    if not isinstance(dependencies, Mapping) or not isinstance(release_dependencies, Mapping):
        raise HandoffError("dependency identity is invalid")
    records = dependencies.get("wheel_records")
    release_records = release_dependencies.get("wheels")
    if not isinstance(records, list) or records != release_records or len(records) != 25:
        raise HandoffError("wheel records do not bind exactly 25 wheels")
    if dependencies.get("wheel_count") != 25 or release_dependencies.get("wheel_count") != 25:
        raise HandoffError("wheel count drift")
    total = 0
    names = set()
    normalized = []
    for item in records:
        if not isinstance(item, Mapping) or set(item) != {"bytes", "filename", "sha256"}:
            raise HandoffError("invalid wheel record schema")
        filename = item.get("filename")
        if not isinstance(filename, str) or len(_safe_relative(filename, label="wheel filename")) != 1 or not filename.endswith(".whl") or filename in names:
            raise HandoffError("invalid or duplicate wheel filename")
        size, digest = _expect_file_record(item, label=f"wheel {filename}")
        names.add(filename)
        total += size
        normalized.append({"bytes": size, "filename": filename, "sha256": digest})
    if dependencies.get("wheel_bytes_total") != total or release_dependencies.get("wheel_bytes_total") != total:
        raise HandoffError("wheel byte census drift")
    if dependencies.get("wheel_records_sha256") != _sha256(_canonical(records)):
        raise HandoffError("wheel-record manifest hash drift")
    summary_wheel_manifest = dependencies.get("wheel_manifest")
    release_wheel_manifest = release_dependencies.get("wheel_manifest")
    if not isinstance(summary_wheel_manifest, Mapping) or not isinstance(release_wheel_manifest, Mapping):
        raise HandoffError("wheel-manifest identity is invalid")
    if (
        summary_wheel_manifest.get("bytes") != release_wheel_manifest.get("bytes")
        or summary_wheel_manifest.get("sha256") != release_wheel_manifest.get("sha256")
    ):
        raise HandoffError("wheel-manifest identity drift")
    return normalized


def _list_exact(fd: int, *, expected: set[str], label: str) -> None:
    try:
        names = set(os.listdir(fd))
    except OSError as exc:
        raise HandoffError(f"cannot enumerate {label}") from exc
    if names != expected:
        raise HandoffError(
            f"{label} has missing or extra entries: expected {sorted(expected)!r}, got {sorted(names)!r}"
        )


def _verify_unchanged_file(
    parent_fd: int, name: str, expected_value: bytes,
    expected_stat: os.stat_result, *, label: str,
) -> None:
    value, metadata = _read_regular_at(parent_fd, name, label=label)
    if value != expected_value or _stable_identity(metadata) != _stable_identity(expected_stat):
        raise HandoffError(f"{label} changed during transfer")


def protected_transfer(
    *, staging: Path, identity_summary: Path, protected_leaf: Path,
    authorized_packet_summary_sha256: str,
    authorized_source_archive_sha256: str,
    authorized_bootstrap_sha256: str,
    authorized_release_input_sha256: str,
    authorized_handoff_tool_sha256: str,
    require_privileged: bool = True,
    _enforce_protected_ancestors: bool = True,
) -> Mapping[str, Any]:
    """Verify and copy the exact approved six-input packet into an absent leaf."""
    if require_privileged and not _is_privileged():
        raise HandoffError("protected transfer requires a root/admin principal")
    staging = _absolute(staging, label="staging directory")
    identity_summary = _absolute(identity_summary, label="identity summary")
    protected_leaf = _absolute(protected_leaf, label="protected leaf")
    packet_hash = _hash(authorized_packet_summary_sha256, label="authorized packet-summary hash")
    archive_hash = _hash(authorized_source_archive_sha256, label="authorized source archive hash")
    bootstrap_hash = _hash(authorized_bootstrap_sha256, label="authorized bootstrap hash")
    release_hash = _hash(authorized_release_input_sha256, label="authorized release-input hash")
    tool_hash = _hash(authorized_handoff_tool_sha256, label="authorized handoff-tool hash")
    if os.path.commonpath((str(staging), str(protected_leaf))) == str(staging):
        raise HandoffError("protected target must be outside staging")

    # Self-authentication precedes every staging input read.  The caller's
    # separately approved digest, not the running file, is the trust anchor.
    tool_path = Path(__file__).absolute()
    tool_parent_fd = _open_dir(tool_path.parent)
    try:
        tool_bytes, tool_stat = _read_regular_at(
            tool_parent_fd, tool_path.name, label="handoff tool"
        )
    finally:
        os.close(tool_parent_fd)
    if _sha256(tool_bytes) != tool_hash:
        raise HandoffError("handoff-tool authorization hash mismatch")
    if _enforce_protected_ancestors:
        _verify_root_owned_ancestors(protected_leaf.parent)

    staging_fd = _open_dir(staging)
    identity_fd = _open_dir(identity_summary.parent)
    try:
        _list_exact(staging_fd, expected=EXPECTED_INPUTS, label="staging packet")
        summary_bytes, summary_stat = _read_regular_at(identity_fd, identity_summary.name, label="identity summary")
        if _sha256(summary_bytes) != packet_hash:
            raise HandoffError("packet-summary authorization hash mismatch")
        summary = _strict_json(summary_bytes, label="identity summary")
        if not isinstance(summary, Mapping):
            raise HandoffError("identity summary must be an object")
        values: dict[str, bytes] = {}
        stats: dict[str, os.stat_result] = {}
        for name in sorted(EXPECTED_INPUTS - {"wheelhouse"}):
            values[name], stats[name] = _read_regular_at(staging_fd, name, label=name)
        wheel_fd = os.open("wheelhouse", DIR_FLAGS, dir_fd=staging_fd)
        try:
            wheel_records = _validate_packet(summary_bytes=summary_bytes, summary=summary, values=values)
            wheel_names = {str(record["filename"]) for record in wheel_records}
            _list_exact(wheel_fd, expected=wheel_names, label="wheelhouse")
            wheel_values: dict[str, bytes] = {}
            wheel_stats: dict[str, os.stat_result] = {}
            for record in wheel_records:
                name = str(record["filename"])
                value, metadata = _read_regular_at(wheel_fd, name, label=f"wheel {name}")
                _same_record(record, value, label=f"wheel {name}")
                wheel_values[name], wheel_stats[name] = value, metadata
            wheel_dir_before = os.fstat(wheel_fd)
        finally:
            os.close(wheel_fd)
        if _sha256(values["source.tar"]) != archive_hash:
            raise HandoffError("source-archive authorization hash mismatch")
        if _sha256(values["gate_a_bootstrap.py"]) != bootstrap_hash:
            raise HandoffError("bootstrap authorization hash mismatch")
        if _sha256(values["release_input_manifest.json"]) != release_hash:
            raise HandoffError("release-input authorization hash mismatch")
        staging_before = os.fstat(staging_fd)

        parent_fd = _open_dir(protected_leaf.parent)
        try:
            try:
                os.mkdir(protected_leaf.name, 0o750, dir_fd=parent_fd)
            except OSError as exc:
                raise HandoffError("protected target leaf must be absent") from exc
            os.fsync(parent_fd)
            target_fd = os.open(protected_leaf.name, DIR_FLAGS, dir_fd=parent_fd)
        finally:
            os.close(parent_fd)
        if _enforce_protected_ancestors and os.fstat(target_fd).st_uid != 0:
            os.close(target_fd)
            raise HandoffError("protected target leaf is not root-owned")
        source_records = []
        target_records = []
        target_stats: dict[str, os.stat_result] = {}
        try:
            for name in sorted(values):
                source_records.append({"logical_path": name, **_record(values[name], stats[name], staging / name)})
                target_stat = _create_file_at(target_fd, name, values[name], stat.S_IMODE(stats[name].st_mode))
                target_stats[name] = target_stat
                target_record = {"logical_path": name, **_record(values[name], target_stat, protected_leaf / name)}
                target_record["mode"] = "0444"
                target_records.append(target_record)
            os.mkdir("wheelhouse", 0o750, dir_fd=target_fd)
            os.fsync(target_fd)
            target_wheel_fd = os.open("wheelhouse", DIR_FLAGS, dir_fd=target_fd)
            try:
                for name in sorted(wheel_values):
                    source_records.append({"logical_path": f"wheelhouse/{name}", **_record(wheel_values[name], wheel_stats[name], staging / "wheelhouse" / name)})
                    target_stat = _create_file_at(target_wheel_fd, name, wheel_values[name], stat.S_IMODE(wheel_stats[name].st_mode))
                    target_stats[f"wheelhouse/{name}"] = target_stat
                    target_record = {"logical_path": f"wheelhouse/{name}", **_record(wheel_values[name], target_stat, protected_leaf / "wheelhouse" / name)}
                    target_record["mode"] = "0444"
                    target_records.append(target_record)
                os.fsync(target_wheel_fd)
            finally:
                os.close(target_wheel_fd)
            # Re-open and re-census all source descriptors before publishing receipt.
            _list_exact(staging_fd, expected=EXPECTED_INPUTS, label="staging packet after copy")
            if _stable_identity(staging_before) != _stable_identity(os.fstat(staging_fd)):
                raise HandoffError("staging directory changed during transfer")
            check_wheel_fd = os.open("wheelhouse", DIR_FLAGS, dir_fd=staging_fd)
            try:
                _list_exact(check_wheel_fd, expected=set(wheel_values), label="wheelhouse after copy")
                if _stable_identity(wheel_dir_before) != _stable_identity(os.fstat(check_wheel_fd)):
                    raise HandoffError("wheelhouse changed during transfer")
                for name in sorted(wheel_values):
                    _verify_unchanged_file(
                        check_wheel_fd, name, wheel_values[name], wheel_stats[name],
                        label=f"wheel {name}",
                    )
            finally:
                os.close(check_wheel_fd)
            for name in sorted(values):
                _verify_unchanged_file(
                    staging_fd, name, values[name], stats[name], label=name,
                )
            _verify_unchanged_file(
                identity_fd, identity_summary.name, summary_bytes, summary_stat,
                label="identity summary",
            )
            _list_exact(
                target_fd, expected=EXPECTED_INPUTS,
                label="protected target before receipt",
            )
            for name in sorted(values):
                _verify_unchanged_file(
                    target_fd, name, values[name], target_stats[name],
                    label=f"protected target {name}",
                )
            target_wheel_check_fd = os.open("wheelhouse", DIR_FLAGS, dir_fd=target_fd)
            try:
                _list_exact(
                    target_wheel_check_fd, expected=set(wheel_values),
                    label="protected wheelhouse before receipt",
                )
                for name in sorted(wheel_values):
                    _verify_unchanged_file(
                        target_wheel_check_fd, name, wheel_values[name],
                        target_stats[f"wheelhouse/{name}"],
                        label=f"protected wheel {name}",
                    )
            finally:
                os.close(target_wheel_check_fd)
            tool_parent_fd = _open_dir(tool_path.parent)
            try:
                _verify_unchanged_file(
                    tool_parent_fd, tool_path.name, tool_bytes, tool_stat,
                    label="handoff tool",
                )
            finally:
                os.close(tool_parent_fd)
            receipt = {
                "authorization": {
                    "bootstrap_sha256": bootstrap_hash,
                    "packet_summary_sha256": packet_hash,
                    "release_input_sha256": release_hash,
                    "source_archive_sha256": archive_hash,
                    "handoff_tool_sha256": tool_hash,
                },
                "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
                "identity_summary": _record(summary_bytes, summary_stat, identity_summary),
                "schema_version": TRANSFER_SCHEMA,
                "seal_policy": {
                    "directory_mode": "0555",
                    "file_mode": "0444",
                    "ownership_changed": False,
                    "sealed_after_receipt_creation": True,
                },
                "source_root": str(staging),
                "source_records": source_records,
                "target_root": str(protected_leaf),
                "target_records": target_records,
                "tool": {"version": VERSION, **_record(tool_bytes, tool_stat, tool_path)},
                "wheel_census": {
                    "bytes": sum(len(value) for value in wheel_values.values()),
                    "count": len(wheel_values),
                    "records_sha256": _sha256(_canonical(wheel_records)),
                },
            }
            receipt_bytes = _canonical(receipt)
            receipt_stat = _create_file_at(target_fd, RECEIPT_NAME, receipt_bytes, 0o600)
            os.fsync(target_fd)
            target_dir_stat = os.fstat(target_fd)
            # Receipt exists before the final irreversible mode seal.  Every copied
            # file and the receipt become world-readable, while both directories
            # become traversable but immutable to the non-owner Gate A principal.
            for name in sorted(values):
                target_stats[name] = _seal_regular_at(
                    target_fd, name, values[name], target_stats[name],
                    label=f"protected target {name}",
                )
            target_wheel_seal_fd = os.open("wheelhouse", DIR_FLAGS, dir_fd=target_fd)
            try:
                target_wheel_dir_stat = os.fstat(target_wheel_seal_fd)
                for name in sorted(wheel_values):
                    key = f"wheelhouse/{name}"
                    target_stats[key] = _seal_regular_at(
                        target_wheel_seal_fd, name, wheel_values[name],
                        target_stats[key], label=f"protected wheel {name}",
                    )
                _seal_directory(
                    target_wheel_seal_fd, target_wheel_dir_stat,
                    label="protected wheelhouse",
                )
            finally:
                os.close(target_wheel_seal_fd)
            receipt_stat = _seal_regular_at(
                target_fd, RECEIPT_NAME, receipt_bytes, receipt_stat,
                label="protected transfer receipt",
            )
            _seal_directory(target_fd, target_dir_stat, label="protected leaf")
            _list_exact(
                target_fd, expected=EXPECTED_INPUTS | {RECEIPT_NAME},
                label="sealed protected target",
            )
            sealed_wheel_fd = os.open("wheelhouse", DIR_FLAGS, dir_fd=target_fd)
            try:
                _list_exact(
                    sealed_wheel_fd, expected=set(wheel_values),
                    label="sealed protected wheelhouse",
                )
            finally:
                os.close(sealed_wheel_fd)
            sealed_parent_fd = _open_dir(protected_leaf.parent)
            try:
                os.fsync(sealed_parent_fd)
            finally:
                os.close(sealed_parent_fd)
            return {**receipt, "receipt": _record(receipt_bytes, receipt_stat, protected_leaf / RECEIPT_NAME)}
        finally:
            os.close(target_fd)
    finally:
        os.close(identity_fd)
        os.close(staging_fd)


def _repository_principal(metadata: os.stat_result) -> Mapping[str, Any]:
    """Return the one non-root identity allowed to inspect this checkout."""
    if not stat.S_ISDIR(metadata.st_mode):
        raise HandoffError("repository root is not a directory")
    if metadata.st_uid == 0 or metadata.st_gid == 0:
        raise HandoffError("repository root must have a non-root owner and group")
    return {
        "gid": metadata.st_gid,
        "supplementary_gids": [],
        "uid": metadata.st_uid,
    }


def _git_directory_identity(
    repo_fd: int, principal: Mapping[str, Any],
) -> tuple[int, ...]:
    """Require an owned in-root .git directory, never a gitfile or symlink."""
    try:
        git_fd = os.open(".git", DIR_FLAGS, dir_fd=repo_fd)
    except OSError as exc:
        raise HandoffError("repository root must contain an in-root .git directory") from exc
    try:
        metadata = os.fstat(git_fd)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != principal["uid"]
            or metadata.st_gid != principal["gid"]
        ):
            raise HandoffError(".git directory ownership differs from repository root")
        return _stable_identity(metadata)
    finally:
        os.close(git_fd)


def _git_child_setup(
    repo_fd: int, principal: Mapping[str, Any], *, drop_privileges: bool,
):
    """Build the minimal child setup used immediately before fixed Git exec."""
    uid = int(principal["uid"])
    gid = int(principal["gid"])

    def setup() -> None:
        if drop_privileges:
            # This ordering is deliberate: supplementary groups can be cleared
            # only while privileged, and setuid is the final privilege drop.
            os.setgroups([])
            os.setgid(gid)
            os.setuid(uid)
            if (
                os.getuid() != uid
                or os.geteuid() != uid
                or os.getgid() != gid
                or os.getegid() != gid
                or os.getgroups() != []
            ):
                raise OSError("Git child identity drop did not take effect")
        # Bind Git to the same already-open directory scanned descriptor-
        # relatively by the privileged parent.  No attacker-controlled path is
        # resolved again between the material scan and Git inspection.
        os.fchdir(repo_fd)

    return setup


def _git(
    repo_fd: int,
    arguments: Sequence[str],
    *,
    principal: Mapping[str, Any],
    drop_privileges: bool,
) -> bytes:
    try:
        result = subprocess.run(
            [
                GIT, "--no-replace-objects",
                "--git-dir=.git",
                "--work-tree=.",
                # Git <=2.35.1 treats Boolean "false" as a hook pathname.
                # An explicitly empty value disables both old hook-based and
                # newer built-in fsmonitor implementations without execution.
                "-c", "core.fsmonitor=",
                "-c", "core.untrackedCache=false",
                *arguments,
            ],
            check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=GIT_ENVIRONMENT,
            pass_fds=(repo_fd,),
            preexec_fn=_git_child_setup(
                repo_fd, principal, drop_privileges=drop_privileges
            ),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise HandoffError("cannot invoke fixed /usr/bin/git") from exc
    if result.returncode != 0:
        raise HandoffError(f"git inspection failed with exit {result.returncode}")
    return result.stdout


def _porcelain_entries(raw: bytes) -> list[tuple[str, str]]:
    pieces = raw.split(b"\0")
    if pieces[-1:] != [b""]:
        raise HandoffError("git porcelain output is not NUL terminated")
    result = []
    index = 0
    while index < len(pieces) - 1:
        item = pieces[index]
        if len(item) < 4 or item[2:3] != b" ":
            raise HandoffError("invalid git porcelain record")
        try:
            status_code = item[:2].decode("ascii")
            path = item[3:].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HandoffError("dirty path is not UTF-8") from exc
        result.append((status_code, path))
        if "R" in status_code or "C" in status_code:
            index += 1
            if index >= len(pieces) - 1:
                raise HandoffError("rename/copy porcelain record is incomplete")
            try:
                old_path = pieces[index].decode("utf-8")
            except UnicodeDecodeError as exc:
                raise HandoffError("dirty path is not UTF-8") from exc
            if "R" in status_code:
                result.append(("D ", old_path))
        index += 1
    return result


def _read_path_record(root_fd: int, parts: tuple[str, ...], *, path: str, status_code: str) -> dict[str, Any]:
    parent = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            try:
                child = os.open(part, DIR_FLAGS, dir_fd=parent)
            except FileNotFoundError:
                # A tracked nested path whose complete parent was deleted is
                # one absent material record, not a scanner failure.  Git's
                # bracketing porcelain calls still detect a concurrent change
                # into or out of this state.
                return {
                    "path": path,
                    "status": status_code,
                    "type": "absent",
                }
            except OSError as exc:
                raise HandoffError(
                    f"dirty path has an unsafe intermediate component: {path}"
                ) from exc
            os.close(parent)
            parent = child
        try:
            before = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return {"path": path, "status": status_code, "type": "absent"}
        mode = format(stat.S_IMODE(before.st_mode), "04o")
        if stat.S_ISREG(before.st_mode):
            value, metadata = _read_regular_at(parent, parts[-1], label=f"dirty path {path}")
            return {"bytes": len(value), "mode": mode, "path": path, "sha256": _sha256(value), "status": status_code, "type": "file"}
        if stat.S_ISLNK(before.st_mode):
            target = os.readlink(parts[-1], dir_fd=parent)
            after = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            if _stable_identity(before) != _stable_identity(after):
                raise HandoffError(f"dirty symlink changed during scan: {path}")
            return {"mode": mode, "path": path, "status": status_code, "target": target, "type": "symlink"}
        if stat.S_ISDIR(before.st_mode):
            raise IsADirectoryError(path)
        raise HandoffError(f"dirty path is a special file: {path}")
    finally:
        os.close(parent)


def _expand_directory(root_fd: int, parts: tuple[str, ...], *, prefix: str, status_code: str) -> list[dict[str, Any]]:
    fd = os.dup(root_fd)
    try:
        for part in parts:
            child = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        before = os.fstat(fd)
        records = []
        for name in sorted(os.listdir(fd)):
            if name in {".", ".."} or "/" in name or "\x00" in name:
                raise HandoffError("unsafe directory entry during dirty scan")
            metadata = os.stat(name, dir_fd=fd, follow_symlinks=False)
            child_path = f"{prefix}/{name}"
            child_parts = parts + (name,)
            if stat.S_ISDIR(metadata.st_mode):
                records.extend(_expand_directory(root_fd, child_parts, prefix=child_path, status_code=status_code))
            else:
                records.append(_read_path_record(root_fd, child_parts, path=child_path, status_code=status_code))
        if _stable_identity(before) != _stable_identity(os.fstat(fd)):
            raise HandoffError(f"dirty directory changed during scan: {prefix}")
        return records
    finally:
        os.close(fd)


def _scan_dirty_records(root_fd: int, raw: bytes) -> list[dict[str, Any]]:
    records = []
    seen = set()
    for status_code, path in _porcelain_entries(raw):
        parts = _safe_relative(path.rstrip("/"), label="dirty path")
        normalized = "/".join(parts)
        try:
            current = _read_path_record(
                root_fd, parts, path=normalized, status_code=status_code
            )
            expanded = [current]
        except IsADirectoryError:
            expanded = _expand_directory(
                root_fd, parts, prefix=normalized, status_code=status_code
            )
        for record in expanded:
            key = record["path"]
            if key in seen:
                raise HandoffError(f"duplicate dirty path after expansion: {key}")
            seen.add(key)
            records.append(record)
    records.sort(key=lambda item: item["path"].encode("utf-8"))
    return records


def _dirty_census(records: Sequence[Mapping[str, Any]]) -> tuple[int, int]:
    return (
        len(records),
        sum(
            int(record["bytes"])
            for record in records
            if record.get("type") == "file"
        ),
    )


def _tool_record() -> Mapping[str, Any]:
    path = Path(__file__).absolute()
    parent_fd = _open_dir(path.parent)
    try:
        value, metadata = _read_regular_at(parent_fd, path.name, label="dirty scanner tool")
    finally:
        os.close(parent_fd)
    return {"bytes": len(value), "path": str(path), "sha256": _sha256(value), "version": VERSION}


def _git_tool_record() -> Mapping[str, Any]:
    path = Path(GIT)
    parent_fd = _open_dir(path.parent)
    try:
        value, metadata = _read_regular_at(
            parent_fd, path.name, label="fixed git executable",
            require_single_link=False,
        )
    finally:
        os.close(parent_fd)
    return {
        "bytes": len(value),
        "gid": metadata.st_gid,
        "mode": format(stat.S_IMODE(metadata.st_mode), "04o"),
        "nlink": metadata.st_nlink,
        "path": str(path),
        "sha256": _sha256(value),
        "uid": metadata.st_uid,
    }


def dirty_snapshot(
    *, repo_root: Path, output: Path,
    _require_root: bool = True,
    _enforce_protected_output: bool = True,
) -> Mapping[str, Any]:
    """Create a lossless, create-only receipt for the current dirty material."""
    repo_root = _absolute(repo_root, label="repository root")
    output = _absolute(output, label="snapshot output")
    if _require_root and os.geteuid() != 0:
        raise HandoffError("dirty snapshot requires the root receipt principal")
    if os.path.commonpath((str(repo_root), str(output))) == str(repo_root):
        raise HandoffError("snapshot output must be outside the repository")
    if _enforce_protected_output:
        _verify_root_owned_ancestors(output.parent)
    root_fd = _open_dir(repo_root)
    try:
        root_before = os.fstat(root_fd)
        principal = _repository_principal(root_before)
        git_directory_before = _git_directory_identity(root_fd, principal)
        git_tool = _git_tool_record()
        top_level_raw = _git(
            root_fd, ["rev-parse", "--show-toplevel"],
            principal=principal, drop_privileges=_require_root,
        )
        expected_top_level = f"{repo_root}\n".encode("utf-8")
        if top_level_raw != expected_top_level:
            raise HandoffError("Git top level differs from the canonical repository root")
        head_raw = _git(
            root_fd, ["rev-parse", "--verify", "HEAD"],
            principal=principal, drop_privileges=_require_root,
        )
        if re.fullmatch(rb"[0-9a-f]{40}\n", head_raw) is None:
            raise HandoffError("git HEAD identity is invalid")
        head = head_raw[:-1].decode("ascii")
        raw = _git(
            root_fd,
            ["status", "--porcelain=v1", "-z", "--untracked-files=normal"],
            principal=principal, drop_privileges=_require_root,
        )
        first_records = _scan_dirty_records(root_fd, raw)
        first_census = _dirty_census(first_records)
        # Each complete material scan is bracketed by the Git namespace.  The
        # second independent descriptor-relative scan also catches byte or mode
        # mutation that leaves porcelain unchanged.
        middle_head = _git(
            root_fd, ["rev-parse", "--verify", "HEAD"],
            principal=principal, drop_privileges=_require_root,
        )
        middle_raw = _git(
            root_fd,
            ["status", "--porcelain=v1", "-z", "--untracked-files=normal"],
            principal=principal, drop_privileges=_require_root,
        )
        if middle_head != head_raw or middle_raw != raw:
            raise HandoffError("git HEAD or porcelain changed during dirty scan")
        second_records = _scan_dirty_records(root_fd, middle_raw)
        second_census = _dirty_census(second_records)
        final_head = _git(
            root_fd, ["rev-parse", "--verify", "HEAD"],
            principal=principal, drop_privileges=_require_root,
        )
        final_raw = _git(
            root_fd,
            ["status", "--porcelain=v1", "-z", "--untracked-files=normal"],
            principal=principal, drop_privileges=_require_root,
        )
        if final_head != middle_head or final_raw != middle_raw:
            raise HandoffError("git HEAD or porcelain changed during dirty scan")
        if first_records != second_records or first_census != second_census:
            raise HandoffError("dirty material records changed during stable porcelain scan")
        records = second_records
        git_tool_after = _git_tool_record()
        if git_tool_after != git_tool:
            raise HandoffError("fixed /usr/bin/git changed during dirty scan")
        if _stable_identity(os.fstat(root_fd)) != _stable_identity(root_before):
            raise HandoffError("repository root changed during dirty scan")
        if _git_directory_identity(root_fd, principal) != git_directory_before:
            raise HandoffError(".git directory changed during dirty scan")
        tool = _tool_record()
        record_count, total_bytes = second_census
        semantic = {
            "git_head": head,
            "git_inspection_principal": principal,
            "git_top_level": str(repo_root),
            "git_tool": git_tool,
            "porcelain_bytes": len(raw),
            "porcelain_sha256": _sha256(raw),
            "record_count": record_count,
            "records": records,
            "repo_root": str(repo_root),
            "schema_version": SEMANTIC_SCHEMA,
            "scanner": tool,
            "total_file_bytes": total_bytes,
        }
        receipt = {
            "captured_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
            "git": {
                "executable": GIT,
                "head": head,
                "inspection_principal": principal,
                "porcelain_base64": base64.b64encode(raw).decode("ascii"),
                "porcelain_bytes": len(raw),
                "porcelain_sha256": _sha256(raw),
                "top_level": str(repo_root),
                "tool": git_tool,
            },
            "records": records,
            "repo_root": str(repo_root),
            "scanner": tool,
            "schema_version": DIRTY_SCHEMA,
            "semantic_snapshot": semantic,
            "semantic_snapshot_sha256": _sha256(_canonical(semantic)),
        }
    finally:
        os.close(root_fd)
    parent_fd = _open_dir(output.parent)
    try:
        metadata = _create_file_at(
            parent_fd, output.name, _canonical(receipt), 0o440,
            owner=(0, 0) if _enforce_protected_output else None,
        )
        if _enforce_protected_output and (
            metadata.st_uid != 0
            or metadata.st_gid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o440
        ):
            raise HandoffError("snapshot receipt is not root-owned, root-grouped, and 0440")
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)
    return {**receipt, "output": {"bytes": metadata.st_size, "path": str(output), "sha256": _sha256(_canonical(receipt))}}


def _validate_snapshot_receipt(receipt: Any, *, label: str) -> None:
    top_fields = {
        "captured_at_utc", "git", "records", "repo_root", "scanner",
        "schema_version", "semantic_snapshot", "semantic_snapshot_sha256",
    }
    if (
        not isinstance(receipt, Mapping)
        or set(receipt) != top_fields
        or receipt.get("schema_version") != DIRTY_SCHEMA
    ):
        raise HandoffError(f"{label} snapshot schema is invalid")
    if not isinstance(receipt.get("captured_at_utc"), str):
        raise HandoffError(f"{label} snapshot timestamp is invalid")
    try:
        repo_root = _absolute(Path(str(receipt.get("repo_root"))), label="snapshot repository root")
    except HandoffError as exc:
        raise HandoffError(f"{label} snapshot repository root is invalid") from exc
    scanner = receipt.get("scanner")
    if (
        not isinstance(scanner, Mapping)
        or set(scanner) != {"bytes", "path", "sha256", "version"}
        or not isinstance(scanner.get("bytes"), int)
        or isinstance(scanner.get("bytes"), bool)
        or scanner["bytes"] < 0
        or not isinstance(scanner.get("path"), str)
        or SHA256.fullmatch(str(scanner.get("sha256", ""))) is None
        or scanner.get("version") != VERSION
    ):
        raise HandoffError(f"{label} snapshot scanner record is invalid")
    try:
        _absolute(Path(str(scanner["path"])), label="snapshot scanner path")
    except HandoffError as exc:
        raise HandoffError(f"{label} snapshot scanner path is invalid") from exc
    git = receipt.get("git")
    if not isinstance(git, Mapping) or set(git) != {
        "executable", "head", "inspection_principal", "porcelain_base64",
        "porcelain_bytes", "porcelain_sha256", "tool", "top_level",
    }:
        raise HandoffError(f"{label} snapshot git record is invalid")
    principal = git.get("inspection_principal")
    if (
        not isinstance(principal, Mapping)
        or set(principal) != {"gid", "supplementary_gids", "uid"}
        or not isinstance(principal.get("uid"), int)
        or isinstance(principal.get("uid"), bool)
        or principal["uid"] <= 0
        or not isinstance(principal.get("gid"), int)
        or isinstance(principal.get("gid"), bool)
        or principal["gid"] <= 0
        or principal.get("supplementary_gids") != []
    ):
        raise HandoffError(f"{label} snapshot Git inspection principal is invalid")
    git_tool = git.get("tool")
    if (
        not isinstance(git_tool, Mapping)
        or set(git_tool) != {"bytes", "gid", "mode", "nlink", "path", "sha256", "uid"}
        or git_tool.get("path") != GIT
        or not isinstance(git_tool.get("bytes"), int)
        or isinstance(git_tool.get("bytes"), bool)
        or git_tool["bytes"] < 0
        or not isinstance(git_tool.get("uid"), int)
        or not isinstance(git_tool.get("gid"), int)
        or not isinstance(git_tool.get("nlink"), int)
        or git_tool["nlink"] < 1
        or MODE.fullmatch(str(git_tool.get("mode", ""))) is None
        or SHA256.fullmatch(str(git_tool.get("sha256", ""))) is None
    ):
        raise HandoffError(f"{label} snapshot fixed-git identity is invalid")
    if (
        git.get("executable") != GIT
        or git.get("top_level") != str(repo_root)
        or re.fullmatch(r"[0-9a-f]{40}", str(git.get("head", ""))) is None
    ):
        raise HandoffError(f"{label} snapshot git identity is invalid")
    try:
        raw = base64.b64decode(str(git.get("porcelain_base64", "")), validate=True)
    except (binascii.Error, ValueError, TypeError) as exc:
        raise HandoffError(f"{label} snapshot porcelain bytes are invalid") from exc
    if (
        git.get("porcelain_bytes") != len(raw)
        or git.get("porcelain_sha256") != _sha256(raw)
    ):
        raise HandoffError(f"{label} snapshot porcelain identity is invalid")
    records = receipt.get("records")
    if not isinstance(records, list):
        raise HandoffError(f"{label} snapshot records are invalid")
    prior = None
    total = 0
    for record in records:
        if not isinstance(record, Mapping):
            raise HandoffError(f"{label} snapshot record is invalid")
        kind = record.get("type")
        expected_fields = {
            "file": {"bytes", "mode", "path", "sha256", "status", "type"},
            "symlink": {"mode", "path", "status", "target", "type"},
            "absent": {"path", "status", "type"},
        }.get(str(kind))
        if expected_fields is None or set(record) != expected_fields:
            raise HandoffError(f"{label} snapshot record schema is invalid")
        path = record.get("path")
        if not isinstance(path, str):
            raise HandoffError(f"{label} snapshot path is invalid")
        _safe_relative(path, label="snapshot path")
        path_key = path.encode("utf-8")
        if prior is not None and path_key <= prior:
            raise HandoffError(f"{label} snapshot paths are not unique and sorted")
        prior = path_key
        if not isinstance(record.get("status"), str) or len(record["status"]) != 2:
            raise HandoffError(f"{label} snapshot status is invalid")
        if kind in {"file", "symlink"} and MODE.fullmatch(str(record.get("mode", ""))) is None:
            raise HandoffError(f"{label} snapshot mode is invalid")
        if kind == "file":
            size = record.get("bytes")
            if (
                not isinstance(size, int) or isinstance(size, bool) or size < 0
                or SHA256.fullmatch(str(record.get("sha256", ""))) is None
            ):
                raise HandoffError(f"{label} snapshot file identity is invalid")
            total += size
        elif kind == "symlink" and not isinstance(record.get("target"), str):
            raise HandoffError(f"{label} snapshot symlink target is invalid")
    semantic = receipt.get("semantic_snapshot")
    semantic_fields = {
        "git_head", "git_inspection_principal", "git_tool", "git_top_level",
        "porcelain_bytes", "porcelain_sha256", "record_count", "records",
        "repo_root", "schema_version", "scanner", "total_file_bytes",
    }
    digest = receipt.get("semantic_snapshot_sha256")
    if (
        not isinstance(semantic, Mapping)
        or set(semantic) != semantic_fields
        or semantic.get("schema_version") != SEMANTIC_SCHEMA
        or digest != _sha256(_canonical(semantic))
    ):
        raise HandoffError(f"{label} snapshot semantic hash is invalid")
    if (
        semantic.get("git_head") != git["head"]
        or semantic.get("git_inspection_principal") != principal
        or semantic.get("git_top_level") != git["top_level"]
        or semantic.get("git_tool") != git_tool
        or semantic.get("porcelain_bytes") != len(raw)
        or semantic.get("porcelain_sha256") != _sha256(raw)
        or semantic.get("record_count") != len(records)
        or semantic.get("records") != records
        or semantic.get("repo_root") != str(repo_root)
        or semantic.get("scanner") != scanner
        or semantic.get("total_file_bytes") != total
    ):
        raise HandoffError(f"{label} snapshot duplicated fields drift from semantics")


def compare_snapshots(*, before: Path, after: Path) -> Mapping[str, Any]:
    """Fail unless two strict snapshot receipts have identical semantics."""
    receipts = []
    for label, path in (("before", before), ("after", after)):
        path = _absolute(path, label=f"{label} snapshot")
        parent_fd = _open_dir(path.parent)
        try:
            value, _metadata_value = _read_regular_at(parent_fd, path.name, label=f"{label} snapshot")
        finally:
            os.close(parent_fd)
        receipt = _strict_json(value, label=f"{label} snapshot")
        _validate_snapshot_receipt(receipt, label=label)
        receipts.append(receipt)
    if receipts[0]["semantic_snapshot"] != receipts[1]["semantic_snapshot"]:
        raise HandoffError("dirty snapshots differ")
    return {
        "schema_version": "caerus_alpha_lab_dirty_snapshot_comparison_v1",
        "semantic_snapshot_sha256": receipts[0]["semantic_snapshot_sha256"],
        "status": "EQUAL",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    transfer = commands.add_parser("protected-transfer")
    transfer.add_argument("--staging", type=Path, required=True)
    transfer.add_argument("--identity-summary", type=Path, required=True)
    transfer.add_argument("--protected-leaf", type=Path, required=True)
    transfer.add_argument("--authorized-packet-summary-sha256", required=True)
    transfer.add_argument("--authorized-source-archive-sha256", required=True)
    transfer.add_argument("--authorized-bootstrap-sha256", required=True)
    transfer.add_argument("--authorized-release-input-sha256", required=True)
    transfer.add_argument("--authorized-handoff-tool-sha256", required=True)
    snapshot = commands.add_parser("dirty-snapshot")
    snapshot.add_argument("--repo-root", type=Path, required=True)
    snapshot.add_argument("--output", type=Path, required=True)
    compare = commands.add_parser("compare")
    compare.add_argument("--before", type=Path, required=True)
    compare.add_argument("--after", type=Path, required=True)
    dirty_compare = commands.add_parser("dirty-compare")
    dirty_compare.add_argument("--before", type=Path, required=True)
    dirty_compare.add_argument("--after", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "protected-transfer":
            result = protected_transfer(
                staging=arguments.staging,
                identity_summary=arguments.identity_summary,
                protected_leaf=arguments.protected_leaf,
                authorized_packet_summary_sha256=arguments.authorized_packet_summary_sha256,
                authorized_source_archive_sha256=arguments.authorized_source_archive_sha256,
                authorized_bootstrap_sha256=arguments.authorized_bootstrap_sha256,
                authorized_release_input_sha256=arguments.authorized_release_input_sha256,
                authorized_handoff_tool_sha256=arguments.authorized_handoff_tool_sha256,
            )
        elif arguments.command == "dirty-snapshot":
            result = dirty_snapshot(repo_root=arguments.repo_root, output=arguments.output)
        else:
            result = compare_snapshots(before=arguments.before, after=arguments.after)
    except HandoffError as exc:
        print(f"gate-a-handoff: {exc}", file=sys.stderr)
        return 2
    print(_canonical(result).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

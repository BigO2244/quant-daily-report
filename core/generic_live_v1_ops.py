"""Operational safety primitives for the date-bound generic Live v1 pilot.

This module has no broker dependency.  It constrains local paths, permissions,
JSON persistence, configuration replacement, and rollback so the executable
shell around Live v1 cannot be redirected through symlinks or broad paths.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence


class GenericLiveV1OpsError(RuntimeError):
    """Raised when an operational safety invariant is not satisfied."""


_FORBIDDEN_KEY_FRAGMENTS = (
    "api_key",
    "api_secret",
    "secret_key",
    "access_token",
    "refresh_token",
    "password",
    "credential",
)


def reject_sensitive_payload(payload: Any) -> None:
    """Reject credential-shaped keys and raw account identifiers recursively."""

    if isinstance(payload, Mapping):
        for raw_key, value in payload.items():
            key = str(raw_key).lower()
            if key == "account_id" or any(fragment in key for fragment in _FORBIDDEN_KEY_FRAGMENTS):
                raise GenericLiveV1OpsError(f"sensitive field is forbidden in persisted payload: {raw_key}")
            reject_sensitive_payload(value)
    elif isinstance(payload, (list, tuple)):
        for value in payload:
            reject_sensitive_payload(value)


def _absolute_no_symlink(path: Path) -> Path:
    if not path.is_absolute():
        raise GenericLiveV1OpsError(f"path must be absolute: {path}")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise GenericLiveV1OpsError(f"symlink path component is forbidden: {current}")
    return path


def secure_path(
    path: Path | str,
    *,
    allowed_roots: Sequence[Path | str],
    must_exist: bool,
    kind: str,
) -> Path:
    """Return an absolute, non-symlink path contained in an explicit root."""

    candidate = _absolute_no_symlink(Path(path))
    roots = [_absolute_no_symlink(Path(root)) for root in allowed_roots]
    if not roots:
        raise GenericLiveV1OpsError("at least one allowlisted root is required")
    prohibited_roots = {Path("/"), Path("/home"), Path("/tmp"), Path("/var"), Path("/Users")}
    if any(root in prohibited_roots for root in roots):
        raise GenericLiveV1OpsError("broad system directories cannot be allowlisted")
    if not any(candidate == root or root in candidate.parents for root in roots):
        raise GenericLiveV1OpsError(f"path is outside the allowlisted roots: {candidate}")
    if must_exist and not candidate.exists():
        raise GenericLiveV1OpsError(f"required path does not exist: {candidate}")
    if candidate.exists():
        if candidate.is_symlink():
            raise GenericLiveV1OpsError(f"symlink path is forbidden: {candidate}")
        if kind == "file" and not candidate.is_file():
            raise GenericLiveV1OpsError(f"path must be a regular file: {candidate}")
        if kind == "directory" and not candidate.is_dir():
            raise GenericLiveV1OpsError(f"path must be a directory: {candidate}")
    elif kind not in {"file", "directory"}:
        raise GenericLiveV1OpsError(f"unsupported secure path kind: {kind}")
    return candidate


def require_protected_mode(path: Path | str, *, directory: bool) -> None:
    """Require owner-only data files (0600) or directories (0700)."""

    candidate = Path(path)
    mode = stat.S_IMODE(os.lstat(candidate).st_mode)
    expected = 0o700 if directory else 0o600
    if mode != expected:
        raise GenericLiveV1OpsError(
            f"protected {'directory' if directory else 'file'} mode must be {expected:04o}: {candidate} is {mode:04o}"
        )


def ensure_protected_directory(path: Path | str, *, allowed_roots: Sequence[Path | str]) -> Path:
    candidate = secure_path(path, allowed_roots=allowed_roots, must_exist=False, kind="directory")
    if not candidate.exists():
        candidate.mkdir(parents=False, exist_ok=False, mode=0o700)
    require_protected_mode(candidate, directory=True)
    return candidate


def secure_read_json(path: Path | str, *, allowed_roots: Sequence[Path | str]) -> dict[str, Any]:
    candidate = secure_path(path, allowed_roots=allowed_roots, must_exist=True, kind="file")
    require_protected_mode(candidate, directory=False)

    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise GenericLiveV1OpsError(f"duplicate JSON key: {key}")
            value[key] = item
        return value

    def no_constant(value: str) -> None:
        raise GenericLiveV1OpsError(f"non-finite JSON constant: {value}")

    with candidate.open("r", encoding="utf-8") as handle:
        payload = json.load(handle, object_pairs_hook=no_duplicates, parse_constant=no_constant)
    if not isinstance(payload, dict):
        raise GenericLiveV1OpsError(f"{candidate} must contain an object")
    reject_sensitive_payload(payload)
    return payload


def atomic_write_protected(
    path: Path | str,
    data: bytes,
    *,
    allowed_roots: Sequence[Path | str],
    replace: bool,
) -> Path:
    candidate = secure_path(path, allowed_roots=allowed_roots, must_exist=False, kind="file")
    parent = ensure_protected_directory(candidate.parent, allowed_roots=allowed_roots)
    descriptor, temporary_raw = tempfile.mkstemp(prefix=f".{candidate.name}.", dir=parent)
    temporary = Path(temporary_raw)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if candidate.exists() and not replace:
            raise GenericLiveV1OpsError(f"protected artifact already exists: {candidate}")
        os.replace(temporary, candidate)
        os.chmod(candidate, 0o600)
        directory_fd = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)
    return candidate


def install_config_with_backup(
    *,
    candidate_path: Path | str,
    active_path: Path | str,
    backup_path: Path | str,
    allowed_roots: Sequence[Path | str],
) -> dict[str, str | bool]:
    """Atomically install a protected config after making an immutable backup."""

    candidate = secure_path(candidate_path, allowed_roots=allowed_roots, must_exist=True, kind="file")
    active = secure_path(active_path, allowed_roots=allowed_roots, must_exist=False, kind="file")
    backup = secure_path(backup_path, allowed_roots=allowed_roots, must_exist=False, kind="file")
    require_protected_mode(candidate, directory=False)
    body = candidate.read_bytes()
    if not body or b"CAERUS_GENERIC_LIVE_SCHEDULE_ENABLED=0" not in body:
        raise GenericLiveV1OpsError("candidate config must be non-empty and schedule-disabled")
    config_keys = {
        line.split(b"=", 1)[0].strip().upper()
        for line in body.splitlines()
        if b"=" in line and not line.lstrip().startswith(b"#")
    }
    forbidden_config_keys = {
        b"APCA_API_KEY_ID", b"APCA_API_SECRET_KEY", b"ALPACA_API_KEY",
        b"ALPACA_API_SECRET", b"CAERUS_GENERIC_LIVE_ACCOUNT_ID",
    }
    if config_keys & forbidden_config_keys:
        raise GenericLiveV1OpsError("generic Live config cannot contain credentials or a raw account id")
    backed_up = False
    if active.exists():
        require_protected_mode(active, directory=False)
        atomic_write_protected(backup, active.read_bytes(), allowed_roots=allowed_roots, replace=False)
        backed_up = True
    atomic_write_protected(active, body, allowed_roots=allowed_roots, replace=True)
    return {"active_path": str(active), "backup_path": str(backup), "backup_created": backed_up}


def restore_config_backup(
    *, active_path: Path | str, backup_path: Path | str, allowed_roots: Sequence[Path | str]
) -> dict[str, str]:
    """Atomically restore a protected backup; never delete the rollback source."""

    active = secure_path(active_path, allowed_roots=allowed_roots, must_exist=False, kind="file")
    backup = secure_path(backup_path, allowed_roots=allowed_roots, must_exist=True, kind="file")
    require_protected_mode(backup, directory=False)
    atomic_write_protected(active, backup.read_bytes(), allowed_roots=allowed_roots, replace=True)
    return {"active_path": str(active), "restored_from": str(backup)}


__all__ = [
    "GenericLiveV1OpsError",
    "atomic_write_protected",
    "ensure_protected_directory",
    "install_config_with_backup",
    "reject_sensitive_payload",
    "require_protected_mode",
    "restore_config_backup",
    "secure_path",
    "secure_read_json",
]

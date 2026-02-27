"""Utility helpers for aiops."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

VALID_MODES = ("EXPLORE", "BUILD", "HARDEN")


@dataclass
class CommandResult:
    """Captured subprocess execution result."""

    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    error: str = ""


def now_local() -> datetime:
    """Return local timezone-aware datetime."""

    return datetime.now().astimezone()


def now_utc() -> datetime:
    """Return UTC timezone-aware datetime."""

    return datetime.now(timezone.utc)


def make_run_id(dt_local: datetime, short_sha: str | None) -> str:
    """Create deterministic run id from local timestamp and git identity."""

    suffix = short_sha or "nogit"
    return f"{dt_local.strftime('%Y-%m-%dT%H%M%S%z')}_{suffix}"


def ensure_writable_dir(path: Path) -> None:
    """Create a directory and verify it is writable."""

    path.mkdir(parents=True, exist_ok=True)
    probe = path / ".write_probe"
    try:
        probe.write_text("ok", encoding="utf-8")
    finally:
        if probe.exists():
            probe.unlink()

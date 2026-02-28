"""Git metadata helpers with graceful non-git fallback."""

from __future__ import annotations

import subprocess
from pathlib import Path


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=path,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def get_git_metadata(repo_root: Path) -> dict[str, str | bool]:
    """Return branch, commit SHA, and dirty status if available."""

    try:
        branch = _git(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
        head_sha = _git(repo_root, "rev-parse", "HEAD")
        short_sha = _git(repo_root, "rev-parse", "--short", "HEAD")
        dirty = bool(_git(repo_root, "status", "--porcelain"))
        return {
            "available": True,
            "branch": branch,
            "head_sha": head_sha,
            "short_sha": short_sha,
            "dirty": dirty,
        }
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {
            "available": False,
            "branch": "unavailable",
            "head_sha": "unavailable",
            "short_sha": "nogit",
            "dirty": "unavailable",
        }

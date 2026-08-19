#!/usr/bin/env python3
"""Render and manage one exact, date-bound generic Live v1 cron entry."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import stat
import subprocess
from pathlib import Path

from core.generic_live_v1_ops import GenericLiveV1OpsError, secure_path


MARKER_PREFIX = "# CAERUS_GENERIC_LIVE_V1_SESSION="


def render_cron_line(
    *, effective_session: str, wrapper_path: Path, log_path: Path,
    allowed_roots: list[Path],
) -> str:
    try:
        session = dt.date.fromisoformat(effective_session)
    except ValueError as exc:
        raise GenericLiveV1OpsError("effective session must be YYYY-MM-DD") from exc
    if session.isoformat() != effective_session:
        raise GenericLiveV1OpsError("effective session must use canonical YYYY-MM-DD")
    wrapper = secure_path(wrapper_path, allowed_roots=allowed_roots, must_exist=True, kind="file")
    log = secure_path(log_path, allowed_roots=allowed_roots, must_exist=False, kind="file")
    if not os.access(wrapper, os.X_OK) or stat.S_IMODE(wrapper.stat().st_mode) & 0o022:
        raise GenericLiveV1OpsError("cron wrapper must be executable and not group/world writable")
    if any(character.isspace() for character in f"{wrapper}{log}"):
        raise GenericLiveV1OpsError("cron paths cannot contain whitespace")
    return (
        f"36 9 {session.day} {session.month} * {wrapper} "
        f"--effective-session {effective_session} >> {log} 2>&1 "
        f"{MARKER_PREFIX}{effective_session}"
    )


def update_crontab(existing: str, *, exact_line: str, install: bool) -> str:
    lines = existing.splitlines()
    conflicts = [line for line in lines if MARKER_PREFIX in line and line != exact_line]
    if conflicts:
        raise GenericLiveV1OpsError("a different generic Live v1 session cron entry already exists")
    retained = [line for line in lines if line != exact_line]
    if install:
        retained.append(exact_line)
    return "\n".join(retained).rstrip() + ("\n" if retained else "")


def _current_crontab() -> str:
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if result.returncode not in {0, 1}:
        raise GenericLiveV1OpsError("could not read current crontab")
    return result.stdout if result.returncode == 0 else ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("render", "install", "uninstall"), required=True)
    parser.add_argument("--effective-session", required=True)
    parser.add_argument("--wrapper-path", type=Path, required=True)
    parser.add_argument("--log-path", type=Path, required=True)
    parser.add_argument("--allowed-root", action="append", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    line = render_cron_line(
        effective_session=args.effective_session,
        wrapper_path=args.wrapper_path,
        log_path=args.log_path,
        allowed_roots=args.allowed_root,
    )
    if args.mode == "render":
        print(line)
        return 0
    updated = update_crontab(
        _current_crontab(), exact_line=line, install=args.mode == "install"
    )
    if args.apply:
        subprocess.run(["crontab", "-"], input=updated, text=True, check=True)
    else:
        print(updated, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

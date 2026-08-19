#!/usr/bin/env python3
"""Install or remove only the inert, date-bound governed Lyra capture cron.

The module has no import-time side effects. A crontab write requires the
literal ``--install`` or ``--remove`` operator action.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys


CRON_TZ = "CRON_TZ=America/New_York"
TZ_OWNER_MARKER = "# CAERUS_GOVERNED_LYRA_CAPTURE_TZ=2026-08-25"
CAPTURE_MARKER = "# CAERUS_GOVERNED_LYRA_CAPTURE=2026-08-25"
CAPTURE_CRON = (
    "15 8 25 8 * "
    "/home/brettolson/quant-daily-report/scripts/"
    "cron_governed_lyra_capture_20260825.sh "
    ">> /home/brettolson/quant-daily-report/logs/"
    "governed_lyra_capture_20260825.log 2>&1 "
    f"{CAPTURE_MARKER}"
)


class GovernedLyraCaptureCronError(ValueError):
    """Raised before mutation when existing cron state is unsafe or ambiguous."""


def render_crontab(current: str, *, install: bool) -> str:
    """Return desired bytes while preserving every unrelated line exactly."""

    if current and not current.endswith("\n"):
        raise GovernedLyraCaptureCronError(
            "crontab lacks a terminal newline; crontab was not changed"
        )
    raw_lines = current.splitlines(keepends=True)
    lines = [line.removesuffix("\n").removesuffix("\r") for line in raw_lines]
    conflicting_timezones = [
        line for line in lines if line.startswith("CRON_TZ=") and line != CRON_TZ
    ]
    if conflicting_timezones:
        raise GovernedLyraCaptureCronError(
            "conflicting CRON_TZ exists; crontab was not changed"
        )

    canonical_timezones = [line for line in lines if line == CRON_TZ]
    if len(canonical_timezones) > 1:
        raise GovernedLyraCaptureCronError(
            "duplicate canonical CRON_TZ exists; crontab was not changed"
        )

    marked_lines = [line for line in lines if CAPTURE_MARKER in line]
    if any(line != CAPTURE_CRON for line in marked_lines):
        raise GovernedLyraCaptureCronError(
            "noncanonical governed Lyra capture cron exists; crontab was not changed"
        )
    if len(marked_lines) > 1:
        raise GovernedLyraCaptureCronError(
            "duplicate governed Lyra capture cron exists; crontab was not changed"
        )
    if install and marked_lines:
        capture_index = lines.index(CAPTURE_CRON)
        if CRON_TZ not in lines or lines.index(CRON_TZ) > capture_index:
            raise GovernedLyraCaptureCronError(
                "capture cron is not governed by the canonical CRON_TZ; "
                "crontab was not changed"
            )

    owner_indexes = [index for index, line in enumerate(lines) if line == TZ_OWNER_MARKER]
    if len(owner_indexes) > 1:
        raise GovernedLyraCaptureCronError(
            "duplicate capture timezone owner markers exist; crontab was not changed"
        )
    if owner_indexes:
        owner_index = owner_indexes[0]
        if owner_index + 1 >= len(lines) or lines[owner_index + 1] != CRON_TZ:
            raise GovernedLyraCaptureCronError(
                "ambiguous capture timezone ownership; crontab was not changed"
            )

    if install:
        additions: list[str] = []
        if CRON_TZ not in lines:
            additions.extend([TZ_OWNER_MARKER + "\n", CRON_TZ + "\n"])
        if CAPTURE_CRON not in lines:
            additions.append(CAPTURE_CRON + "\n")
        return current + "".join(additions)

    remove_indexes = {
        index for index, line in enumerate(lines) if line == CAPTURE_CRON
    }
    if owner_indexes:
        remove_indexes.update({owner_indexes[0], owner_indexes[0] + 1})
    return "".join(
        raw_line for index, raw_line in enumerate(raw_lines) if index not in remove_indexes
    )


def _read_crontab() -> str:
    completed = subprocess.run(
        ["crontab", "-l"], capture_output=True, text=True, check=False
    )
    if completed.returncode == 0:
        return completed.stdout
    no_crontab = re.fullmatch(
        r"(?:crontab:\s*)?no crontab for [A-Za-z0-9._-]+",
        completed.stderr.strip(),
        flags=re.IGNORECASE,
    )
    if completed.returncode == 1 and no_crontab and completed.stdout == "":
        return ""
    raise GovernedLyraCaptureCronError("could not read crontab; no change made")


def _write_crontab(value: str) -> None:
    completed = subprocess.run(
        ["crontab", "-"], input=value, capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        raise GovernedLyraCaptureCronError("could not write crontab")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--install", action="store_true")
    action.add_argument("--remove", action="store_true")
    args = parser.parse_args(argv)

    try:
        current = _read_crontab()
        desired = render_crontab(current, install=args.install)
        if desired != current:
            _write_crontab(desired)
    except GovernedLyraCaptureCronError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

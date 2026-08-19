#!/usr/bin/env python3
"""Install/remove the exact Lyra initialization and Tuesday cadence."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys


TZ_LINE = "CRON_TZ=America/New_York"
TZ_MARKER = "# CAERUS_LYRA_LIVE_TZ"
INIT_MARKER = "# CAERUS_LYRA_LIVE_INITIALIZATION=2026-08-20"
WEEKLY_MARKER = "# CAERUS_LYRA_LIVE_WEEKLY=TUESDAY"
WRAPPER = "/home/brettolson/quant-daily-report/scripts/cron_lyra_live_portfolio.sh"
INIT_LINE = f"35 9 20 8 * {WRAPPER} initialization >> /home/brettolson/quant-daily-report/logs/lyra_live.log 2>&1 {INIT_MARKER}"
WEEKLY_LINE = f"35 9 * * 2 {WRAPPER} recurring >> /home/brettolson/quant-daily-report/logs/lyra_live.log 2>&1 {WEEKLY_MARKER}"


class LyraLiveCronError(ValueError):
    pass


def render(current: str, *, install: bool) -> str:
    if current and not current.endswith("\n"):
        raise LyraLiveCronError("crontab lacks terminal newline")
    lines = current.splitlines()
    if any(line.startswith("CRON_TZ=") and line != TZ_LINE for line in lines):
        raise LyraLiveCronError("conflicting CRON_TZ exists")
    for marker, canonical in ((INIT_MARKER, INIT_LINE), (WEEKLY_MARKER, WEEKLY_LINE)):
        marked = [line for line in lines if marker in line]
        if len(marked) > 1 or any(line != canonical for line in marked):
            raise LyraLiveCronError("noncanonical or duplicate Lyra Live cron exists")
    if install:
        additions = []
        if TZ_LINE not in lines:
            additions.extend([TZ_MARKER, TZ_LINE])
        if INIT_LINE not in lines:
            additions.append(INIT_LINE)
        if WEEKLY_LINE not in lines:
            additions.append(WEEKLY_LINE)
        return current + "".join(line + "\n" for line in additions)
    remove = {TZ_MARKER, INIT_LINE, WEEKLY_LINE}
    if TZ_MARKER in lines:
        remove.add(TZ_LINE)
    return "".join(line + "\n" for line in lines if line not in remove)


def _read() -> str:
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=False)
    if result.returncode == 0:
        return result.stdout
    if result.returncode == 1 and re.search(r"no crontab", result.stderr, re.I):
        return ""
    raise LyraLiveCronError("could not read crontab")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--install", action="store_true")
    action.add_argument("--remove", action="store_true")
    args = parser.parse_args()
    try:
        current = _read()
        desired = render(current, install=args.install)
        if desired != current:
            result = subprocess.run(["crontab", "-"], input=desired, text=True, capture_output=True)
            if result.returncode:
                raise LyraLiveCronError("could not write crontab")
    except LyraLiveCronError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

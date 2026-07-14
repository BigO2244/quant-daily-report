#!/usr/bin/env python3
"""CLI wrapper around ``core.live_pilot_sha_guard`` for the cron lane (BLOCKER 4).

Prints the drift verdict as JSON on stdout so the shell can read individual
fields. Exit code encodes whether the SUBMIT path must fail closed:

  * exit 0  -> pin verified (running HEAD == deployed_sha, tree clean)
  * exit 3  -> block_submit (drift / dirty tree / unresolved SHA)

The DRY path ignores the exit code and merely logs the drift; the SUBMIT path
treats exit 3 as fatal.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.live_pilot_sha_guard import resolve_sha_state  # noqa: E402

BLOCK_EXIT_CODE = 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LIVE_PILOT deploy-SHA drift guard")
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--deploy-state", default="")
    args = parser.parse_args(argv)

    deploy_state = Path(args.deploy_state) if args.deploy_state else None
    verdict = resolve_sha_state(args.repo_root, deploy_state)
    print(json.dumps(verdict.to_dict(), indent=2, sort_keys=True))
    return BLOCK_EXIT_CODE if verdict.block_submit else 0


if __name__ == "__main__":
    raise SystemExit(main())

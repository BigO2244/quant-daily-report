#!/usr/bin/env python3
"""Compile and optionally persist the lane-specific Caerus operating truth."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.operating_truth import (
    compile_operating_truth,
    installed_crontab,
    render_operating_state,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--crontab-file", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--render-doc", type=Path)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)
    root = args.repo_root.resolve()
    if args.crontab_file:
        cron = args.crontab_file.read_text(encoding="utf-8")
    else:
        cron = installed_crontab()
    observed_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    payload = compile_operating_truth(
        repo_root=root,
        home=args.home.resolve(),
        crontab_text=cron,
        observed_at=observed_at,
    )
    if args.output_dir:
        output = args.output_dir.resolve()
        output.mkdir(parents=True, exist_ok=True)
        (output / "operating_truth.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (output / "operating_truth.md").write_text(
            render_operating_state(payload), encoding="utf-8"
        )
    if args.render_doc:
        args.render_doc.write_text(render_operating_state(payload), encoding="utf-8")
    print(json.dumps(payload, sort_keys=True))
    if args.strict and payload["context_integrity"]["status"] != "PASS":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

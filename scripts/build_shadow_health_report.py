#!/usr/bin/env python3
"""Publish a dated read-only report; strict failure never submits or blocks orders."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from core.shadow_health_report import build_shadow_health_report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--observation-start", default="2026-05-12")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)
    payload = build_shadow_health_report(repo_root=args.repo_root, trade_date=args.trade_date,
                                         observation_start=args.observation_start)
    output = args.output or args.repo_root / "outputs/shadow_health" / args.trade_date / "shadow_health_report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n")
    temporary.replace(output)
    print(json.dumps({"status": payload["status"], "output": str(output), "failures": len(payload["pipeline_failures"])}))
    return 1 if args.strict and payload["status"] != "PASS" else 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Bind PAPER broker truth to exact decisions and publish one causal valuation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.causal_ownership_ledger import build_causal_ownership


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ledger-dir",
        type=Path,
        default=REPO_ROOT / "outputs" / "ledger" / "paper",
    )
    parser.add_argument(
        "--plans-root",
        type=Path,
        default=REPO_ROOT / "outputs" / "paper_lane" / "plans",
    )
    parser.add_argument("--opening-contract", type=Path, default=None)
    args = parser.parse_args()
    paths = sorted(set(args.plans_root.rglob("exact_execution_plan*.json")) | set((args.plans_root / "authority").rglob("plan_*.json")))
    result = build_causal_ownership(
        ledger_dir=args.ledger_dir,
        exact_plan_paths=paths,
        plans_root=args.plans_root,
        opening_contract_path=args.opening_contract,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

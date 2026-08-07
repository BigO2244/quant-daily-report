#!/usr/bin/env python3
"""Wrap a validated precompute payload in the canonical authority chain.

This is intentionally deterministic and offline. It does not fetch prices,
contact a broker, or submit orders.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from authority.pipeline import wrap_precompute_payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("payload", type=Path, help="validated precompute payload JSON")
    parser.add_argument("--outdir", type=Path, required=True)
    parser.add_argument("--decision-id", required=True)
    parser.add_argument("--risk-id", required=True)
    args = parser.parse_args()
    payload = json.loads(args.payload.read_text(encoding="utf-8"))
    evidence, decision, risk, execution = wrap_precompute_payload(
        payload,
        evidence_refs=[str(args.payload)],
        decision_id=args.decision_id,
        risk_id=args.risk_id,
    )
    args.outdir.mkdir(parents=True, exist_ok=True)
    artifacts = {
        "evidence_package.json": evidence.to_dict(),
        "decision_package.json": decision.to_dict(),
        "risk_package.json": risk.to_dict(),
        "execution_package.json": execution.to_dict(),
    }
    for name, artifact in artifacts.items():
        (args.outdir / name).write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "OK", "outdir": str(args.outdir), "artifacts": sorted(artifacts)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

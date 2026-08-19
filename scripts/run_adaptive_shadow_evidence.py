#!/usr/bin/env python3
"""Stdout-only Adaptive Shadow v1 activation/readiness runner."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.adaptive_shadow_activation import (  # noqa: E402
    AdaptiveShadowActivationError,
    REQUIRED_GOVERNED_INPUTS,
    build_activation_readiness,
    canonical_json,
)


_ROLE_OPTIONS = {
    "SHADOW_DEPLOYMENT_MEMBERSHIP": "--shadow-deployment-membership",
    "DECISION_BATCH_V2": "--decision-batch-v2",
    "POLARIS_CAUSAL_SIGNAL": "--polaris-causal-signal",
    "LYRA_CAUSAL_SIGNAL": "--lyra-causal-signal",
    "READINESS_HISTORY_60_VALID_20_GREEN": "--readiness-history",
    "CAPACITY_LIQUIDITY_OVERLAP_CONSTRAINT_EVIDENCE": "--constraint-evidence",
}


def _strict_json(path: Path) -> dict[str, Any]:
    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise AdaptiveShadowActivationError(
                    f"duplicate JSON key in {path}: {key}"
                )
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise AdaptiveShadowActivationError(
            f"non-finite JSON constant in {path}: {value}"
        )

    payload = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=object_pairs,
        parse_constant=reject_constant,
    )
    if not isinstance(payload, dict):
        raise AdaptiveShadowActivationError(f"{path} must contain a JSON object")
    return payload


def _hash_file(path: Path | None) -> str | None:
    if path is None:
        return None
    if not path.is_file():
        raise AdaptiveShadowActivationError(f"governed input does not exist: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--owner-decision", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--observed-at", required=True)
    parser.add_argument("--enable-shadow-observation", action="store_true")
    for option in _ROLE_OPTIONS.values():
        parser.add_argument(option, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    candidate = _strict_json(args.candidate)
    owner_decision = _strict_json(args.owner_decision)
    option_values = {
        "SHADOW_DEPLOYMENT_MEMBERSHIP": args.shadow_deployment_membership,
        "DECISION_BATCH_V2": args.decision_batch_v2,
        "POLARIS_CAUSAL_SIGNAL": args.polaris_causal_signal,
        "LYRA_CAUSAL_SIGNAL": args.lyra_causal_signal,
        "READINESS_HISTORY_60_VALID_20_GREEN": args.readiness_history,
        "CAPACITY_LIQUIDITY_OVERLAP_CONSTRAINT_EVIDENCE": args.constraint_evidence,
    }
    if set(option_values) != set(REQUIRED_GOVERNED_INPUTS):
        raise AdaptiveShadowActivationError("CLI governed input roles differ")
    result = build_activation_readiness(
        candidate=candidate,
        owner_decision=owner_decision,
        registry_hash=_hash_file(args.registry) or "",
        observed_at=args.observed_at,
        enable_requested=args.enable_shadow_observation,
        governed_input_hashes={
            role: _hash_file(path) for role, path in option_values.items()
        },
    )
    print(canonical_json(result))
    return 2 if result["readiness_status"] == "BLOCKED_STATIC_POLARIS_FALLBACK" else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AdaptiveShadowActivationError, OSError, json.JSONDecodeError) as exc:
        print(canonical_json({"status": "ERROR", "error": str(exc)}))
        raise SystemExit(2)

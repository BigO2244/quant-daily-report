"""Default-off FR-105 Shadow Alpha Chase framework artifact.

This file documents the required evidence for future shadow comparison. It does
not build an Alpha Chase portfolio and does not influence trading.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from research.fr105_replay_contract import DEFAULT_OUTPUT_ROOT, FR_ID


SHADOW_ALPHA_FRAMEWORK_SCHEMA_VERSION = "fr105_shadow_alpha_chase_framework.v1"
ARTIFACT_NAME = "shadow_alpha_chase_framework.json"


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_shadow_alpha_chase_framework(*, generated_at: str = "unavailable") -> dict[str, Any]:
    return {
        "schema_version": SHADOW_ALPHA_FRAMEWORK_SCHEMA_VERSION,
        "metadata": {
            "generated_at": generated_at,
            "fr_id": FR_ID,
            "mode": "shadow_framework_only",
            "enabled": False,
            "default_off": True,
            "capitalized": False,
            "trading_behavior_changed": False,
            "optimizer_behavior_changed": False,
            "sizing_behavior_changed": False,
            "broker_behavior_changed": False,
            "paper_behavior_changed": False,
            "live_pilot_behavior_changed": False,
            "production_execution_modules_invoked": [],
        },
        "supported_modes": [
            {
                "mode_id": "current_sleeve_merge_baseline",
                "description": "Read-only current-policy baseline from FR-105 Phase 1.",
                "enabled": True,
                "trading_influence": False,
            },
            {
                "mode_id": "global_alpha_chase_shadow",
                "description": "Future shadow-only comparison variant; disabled until Phase 0/1 evidence is complete.",
                "enabled": False,
                "trading_influence": False,
            },
            {
                "mode_id": "core_satellite_shadow",
                "description": "Optional future comparison variant requiring Brett approval.",
                "enabled": False,
                "trading_influence": False,
                "requires_brett_approval": True,
            },
        ],
        "required_inputs": [
            "phase0_replay_contract",
            "phase1_current_policy_baseline",
            "phase01_artifact_completeness",
            "candidate_universe",
            "candidate_pool",
            "pit_lineage",
            "score_source",
            "sleeve_source",
            "target_portfolio",
            "current_holdings",
            "current_weights",
            "target_weights",
            "suppression_reasons",
            "active_constraints",
            "execution_residuals",
        ],
        "required_artifacts": {
            "phase0_replay_contract": "outputs/research/fr_105/<RUN_ID_OR_DATE>/global_optimizer_replay_contract.json",
            "phase1_current_policy_baseline": "outputs/research/fr_105/<RUN_ID_OR_DATE>/phase1_current_policy_baseline.json",
            "phase01_artifact_completeness": "outputs/research/fr_105/<RUN_ID_OR_DATE>/phase01_artifact_completeness.json",
            "future_phase4_shadow_comparison": "outputs/research/fr_105/<RUN_ID_OR_DATE>/phase4_shadow_alpha_chase_comparison.json",
        },
        "required_constraints": [
            "max_single_name_weight",
            "effective_n_floor",
            "sector_exposure",
            "turnover_cap",
            "liquidity_constraints",
            "cash_target",
            "min_notional_policy",
            "no_duplicate_tickers",
            "no_lookahead_inputs",
        ],
        "score_policy": {
            "allowed_score_fields": ["conviction_score", "score", "expected_alpha", "global_rank"],
            "prohibited_score_sources": [
                "target_weight",
                "allocation_weight",
                "final_target_weight",
                "final_allocation_weight",
                "weight",
            ],
            "missing_score_behavior": "UNAVAILABLE",
        },
        "comparison_metrics": [
            "position_count",
            "cash_weight",
            "gross_exposure",
            "max_single_name_weight",
            "HHI",
            "effective_N",
            "sector_exposure",
            "estimated_turnover_from_current_policy",
            "score_backed_candidate_count",
            "unavailable_score_count",
            "names_added",
            "names_removed",
            "names_retained",
            "suppressed_higher_ranked_candidates",
            "source_artifact_completeness",
        ],
        "evaluation_status": {
            "status": "DISABLED",
            "reason": "Alpha Chase implementation is not approved; framework is default-off and shadow-only.",
            "alpha_chase_recommendations_allowed": False,
            "paper_or_live_influence_allowed": False,
            "next_required_artifact": "phase01_artifact_completeness",
        },
    }


def write_shadow_alpha_chase_framework(
    *,
    repo_root: Path | str,
    output_root: Path | str = DEFAULT_OUTPUT_ROOT,
    generated_at: str = "unavailable",
) -> tuple[Path, dict[str, Any]]:
    root = Path(repo_root).resolve()
    out_root = Path(output_root)
    if not out_root.is_absolute():
        out_root = root / out_root
    path = out_root / ARTIFACT_NAME
    payload = build_shadow_alpha_chase_framework(generated_at=generated_at)
    _write_json(path, payload)
    return path, payload

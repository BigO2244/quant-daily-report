"""FR-105 Phase 3 optimizer-derived holding-count research.

This module ranks Phase 2 global top-N frontier variants with a research-only
scoring policy. It does not invoke allocation, production optimization, sizing,
execution, broker, scheduler, paper, or live trading code.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from research.fr105_phase1_baseline import (
    find_phase0_contract_path,
    validate_fr105_phase1_baseline,
)
from research.fr105_phase2_topn_frontier import (
    BLOCKED_ARTIFACT_GAPS,
    READY,
    SCORE_SOURCE_REPLAY_GAP,
    find_phase1_baseline_path,
    validate_fr105_phase2_topn_frontier,
    find_phase01_completeness_path,
    phase01_completeness_gate,
    phase01_score_driven_blocking_gaps,
)
from research.fr105_replay_contract import (
    DEFAULT_OUTPUT_ROOT,
    FR_ID,
    PROHIBITED_PRODUCTION_MODULES,
    read_fr105_replay_contract,
    validate_fr105_replay_contract,
)


PHASE3_SCHEMA_VERSION = "fr105_phase3_optimizer_derived_holding_count.v1"
ARTIFACT_NAME = "phase3_optimizer_derived_holding_count.json"
SHADOW_COMPARISON_SCHEMA_VERSION = "fr105_shadow_alpha_chase_comparison.v1"
SHADOW_COMPARISON_ARTIFACT_NAME = "shadow_alpha_chase_comparison.json"

POLICY_NAME = "fr105_optimizer_derived_holding_count_research"
POLICY_VERSION = "v1"
MAX_SINGLE_NAME_WEIGHT = 0.25
MIN_EFFECTIVE_N = 5.0
DEFAULT_TURNOVER_CAP = 0.95

REQUIRED_TOP_LEVEL_SECTIONS = (
    "metadata",
    "input_contract",
    "input_baseline",
    "input_frontier",
    "pit_controls",
    "decision_policy",
    "candidate_variants",
    "selected_research_variant",
    "comparison_to_current_policy",
    "data_quality",
    "validation_status",
)

REQUIRED_PIT_CONTROL_KEYS = (
    "trade_date",
    "data_asof",
    "universe_asof",
    "price_asof",
    "no_forward_returns_used",
    "no_production_modules_invoked",
    "source_artifact_paths",
    "unavailable_fields",
)

REQUIRED_DECISION_POLICY_KEYS = (
    "policy_name",
    "policy_version",
    "objective",
    "inputs_used",
    "inputs_not_used",
    "guardrails",
    "tie_breakers",
    "failure_modes",
)

REQUIRED_CANDIDATE_VARIANT_KEYS = (
    "variant_id",
    "top_n",
    "selected_count",
    "selected_tickers",
    "metrics_available",
    "guardrail_status",
    "score",
    "score_components",
    "rejection_reasons",
    "rank",
    "eligible_for_research_selection",
)


@dataclass(frozen=True)
class FR105Phase3ValidationResult:
    status: str
    findings: list[str]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "findings": list(self.findings),
            "warnings": list(self.warnings),
        }


def _read_json(path: Path | None) -> Any:
    if path is None or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _float(value: Any) -> float | None:
    try:
        if value in ("", None):
            return None
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _round(value: float | None, digits: int = 10) -> float | None:
    return round(float(value), digits) if value is not None and math.isfinite(float(value)) else None


def _clean(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean(item) for item in value]
    return value


def _relative(path: Path | str | None, repo_root: Path) -> str | None:
    if path is None:
        return None
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    try:
        return candidate.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return str(candidate)


def find_phase2_frontier_path(
    *,
    repo_root: Path | str,
    trade_date: str,
    run_id: str | None = None,
    input_frontier_path: Path | str | None = None,
    output_root: Path | str = DEFAULT_OUTPUT_ROOT,
) -> Path | None:
    root = Path(repo_root).resolve()
    if input_frontier_path is not None:
        path = Path(input_frontier_path)
        if not path.is_absolute():
            path = root / path
        return path if path.exists() else None
    out_root = Path(output_root)
    if not out_root.is_absolute():
        out_root = root / out_root
    if run_id:
        path = out_root / run_id / "phase2_global_topn_frontier.json"
        return path if path.exists() else None
    date_path = out_root / trade_date / "phase2_global_topn_frontier.json"
    if date_path.exists():
        return date_path
    matches: list[Path] = []
    for path in sorted(out_root.glob("*/phase2_global_topn_frontier.json")):
        payload = _as_dict(_read_json(path))
        if _as_dict(payload.get("metadata")).get("trade_date") == trade_date:
            matches.append(path)
    return matches[-1] if matches else None


def _decision_policy(turnover_cap: float | None) -> dict[str, Any]:
    return {
        "policy_name": POLICY_NAME,
        "policy_version": POLICY_VERSION,
        "objective": (
            "Rank Phase 2 global top-N variants using ex-ante/same-time "
            "candidate strength while penalizing concentration and turnover."
        ),
        "inputs_used": [
            "aggregate_conviction_score",
            "average_rank",
            "effective_N",
            "HHI",
            "max_single_name_weight",
            "estimated_turnover_from_current_policy",
            "data_completeness",
        ],
        "inputs_not_used": [
            "forward_returns",
            "realized_returns",
            "post_decision_price_moves",
            "production_optimizer_outputs",
            "allocator_outputs_not_recorded_in_replay_contract",
            "broker_submission_side_effects",
        ],
        "guardrails": {
            "max_single_name_weight": {"operator": "<=", "value": MAX_SINGLE_NAME_WEIGHT},
            "effective_N": {"operator": ">=", "value": MIN_EFFECTIVE_N, "where_available": True},
            "estimated_turnover_from_current_policy": {
                "operator": "<=",
                "value": turnover_cap if turnover_cap is not None else "unavailable",
                "where_available": True,
            },
            "no_duplicate_tickers": True,
            "selected_count_above_zero": True,
            "selected_count_not_above_available_candidate_count": True,
            "data_completeness_must_be_acceptable": True,
        },
        "tie_breakers": [
            "higher_score",
            "higher_aggregate_conviction_score",
            "lower_HHI",
            "lower_estimated_turnover_from_current_policy",
            "lower_top_n",
            "lexicographic_variant_id",
        ],
        "failure_modes": [
            "NO_SELECTION_SPARSE_INPUT",
            "NO_SELECTION_GUARDRAILS_FAILED",
            "NO_SELECTION_NO_NUMERIC_SCORE",
        ],
    }


def _source_artifact_paths(
    *,
    contract: Mapping[str, Any],
    baseline: Mapping[str, Any],
    frontier: Mapping[str, Any],
    contract_path: Path,
    baseline_path: Path,
    frontier_path: Path,
    repo_root: Path,
) -> dict[str, Any]:
    paths = dict(_as_dict(contract.get("source_artifacts")))
    paths["phase0_replay_contract_path"] = _relative(contract_path, repo_root)
    paths["phase1_baseline_path"] = _relative(baseline_path, repo_root)
    paths["phase2_frontier_path"] = _relative(frontier_path, repo_root)
    for key, value in _as_dict(_as_dict(baseline.get("pit_controls")).get("source_artifact_paths")).items():
        paths.setdefault(f"phase1.{key}", value)
    for key, value in _as_dict(_as_dict(frontier.get("pit_controls")).get("source_artifact_paths")).items():
        paths.setdefault(f"phase2.{key}", value)
    return paths


def _asof_values(
    contract: Mapping[str, Any],
    baseline: Mapping[str, Any],
    frontier: Mapping[str, Any],
) -> tuple[Any, Any, Any]:
    frontier_controls = _as_dict(frontier.get("pit_controls"))
    baseline_controls = _as_dict(baseline.get("pit_controls"))
    universe = _as_dict(contract.get("universe_snapshot"))
    return (
        _first_present(frontier_controls.get("data_asof"), baseline_controls.get("data_asof")),
        _first_present(
            frontier_controls.get("universe_asof"),
            baseline_controls.get("universe_asof"),
            universe.get("asof") if universe.get("status") != "unavailable" else None,
        ),
        _first_present(frontier_controls.get("price_asof"), baseline_controls.get("price_asof")),
    )


def _turnover_cap(contract: Mapping[str, Any]) -> float | None:
    value = _float(_as_dict(contract.get("constraints_snapshot")).get("turnover_cap"))
    return value if value is not None else DEFAULT_TURNOVER_CAP


def _metrics_available(variant: Mapping[str, Any]) -> dict[str, bool]:
    data_completeness = _as_dict(variant.get("data_completeness"))
    return {
        "aggregate_conviction_score": _float(variant.get("aggregate_conviction_score")) is not None,
        "average_rank": _float(variant.get("average_rank")) is not None,
        "effective_N": _float(variant.get("effective_N")) is not None,
        "HHI": _float(variant.get("HHI")) is not None,
        "max_single_name_weight": _float(variant.get("max_single_name_weight")) is not None,
        "estimated_turnover_from_current_policy": _float(variant.get("estimated_turnover_from_current_policy")) is not None,
        "data_completeness": bool(data_completeness) and data_completeness.get("status") != "SPARSE",
    }


def _guardrail_status(
    variant: Mapping[str, Any],
    *,
    available_candidate_count: int | None,
    turnover_cap: float | None,
) -> dict[str, Any]:
    selected_tickers = [str(ticker) for ticker in _as_list(variant.get("selected_tickers"))]
    selected_count = int(variant.get("selected_count") or 0)
    max_weight = _float(variant.get("max_single_name_weight"))
    effective_n = _float(variant.get("effective_N"))
    turnover = _float(variant.get("estimated_turnover_from_current_policy"))
    data_completeness = _as_dict(variant.get("data_completeness"))
    checks: dict[str, dict[str, Any]] = {
        "selected_count_above_zero": {
            "status": "PASS" if selected_count > 0 else "FAIL",
            "value": selected_count,
            "threshold": 0,
        },
        "no_duplicate_tickers": {
            "status": "PASS" if len(selected_tickers) == len(set(selected_tickers)) else "FAIL",
            "value": selected_tickers,
        },
        "selected_count_not_above_available_candidate_count": {
            "status": (
                "PASS"
                if available_candidate_count is not None and selected_count <= int(available_candidate_count)
                else "UNAVAILABLE"
                if available_candidate_count is None
                else "FAIL"
            ),
            "value": selected_count,
            "threshold": available_candidate_count,
        },
        "max_single_name_weight": {
            "status": (
                "PASS"
                if max_weight is not None and max_weight <= MAX_SINGLE_NAME_WEIGHT + 1e-12
                else "UNAVAILABLE"
                if max_weight is None
                else "FAIL"
            ),
            "value": max_weight,
            "threshold": MAX_SINGLE_NAME_WEIGHT,
        },
        "effective_N": {
            "status": (
                "PASS"
                if effective_n is not None and effective_n + 1e-12 >= MIN_EFFECTIVE_N
                else "UNAVAILABLE"
                if effective_n is None
                else "FAIL"
            ),
            "value": effective_n,
            "threshold": MIN_EFFECTIVE_N,
            "where_available": True,
        },
        "estimated_turnover_from_current_policy": {
            "status": (
                "PASS"
                if turnover is not None and turnover_cap is not None and turnover <= turnover_cap + 1e-12
                else "UNAVAILABLE"
                if turnover is None or turnover_cap is None
                else "FAIL"
            ),
            "value": turnover,
            "threshold": turnover_cap,
            "where_available": True,
        },
        "data_completeness": {
            "status": (
                "PASS"
                if data_completeness and data_completeness.get("status") != "SPARSE" and not variant.get("unavailable_reason")
                else "FAIL"
            ),
            "value": data_completeness.get("status"),
            "unavailable_reason": variant.get("unavailable_reason"),
        },
    }
    hard_failures = {
        key: value
        for key, value in checks.items()
        if value.get("status") == "FAIL"
        or key == "selected_count_not_above_available_candidate_count"
        and value.get("status") == "UNAVAILABLE"
    }
    return {
        "overall": "PASS" if not hard_failures else "FAIL",
        "checks": checks,
    }


def _rejection_reasons(variant: Mapping[str, Any], guardrails: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    if variant.get("unavailable_reason"):
        reasons.append(str(variant.get("unavailable_reason")))
    checks = _as_dict(guardrails.get("checks"))
    for name, payload in checks.items():
        status = _as_dict(payload).get("status")
        if status == "FAIL":
            reasons.append(f"{name}_guardrail_failed")
        elif name == "selected_count_not_above_available_candidate_count" and status == "UNAVAILABLE":
            reasons.append("available_candidate_count_unavailable")
    if _float(variant.get("aggregate_conviction_score")) is None:
        reasons.append("aggregate_conviction_score_unavailable")
    return sorted(set(reasons))


def _score_components(variant: Mapping[str, Any]) -> dict[str, Any]:
    conviction = _float(variant.get("aggregate_conviction_score"))
    average_rank = _float(variant.get("average_rank"))
    hhi = _float(variant.get("HHI"))
    turnover = _float(variant.get("estimated_turnover_from_current_policy"))
    completeness = _as_dict(variant.get("data_completeness"))
    rank_component = (1.0 / average_rank) if average_rank is not None and average_rank > 0 else None
    concentration_penalty = hhi if hhi is not None else None
    turnover_penalty = turnover if turnover is not None else None
    completeness_component = 0.05 if completeness and completeness.get("status") != "SPARSE" else 0.0
    if conviction is None:
        total = None
    else:
        total = (
            conviction
            + (0.10 * rank_component if rank_component is not None else 0.0)
            + completeness_component
            - (0.50 * concentration_penalty if concentration_penalty is not None else 0.0)
            - (0.25 * turnover_penalty if turnover_penalty is not None else 0.0)
        )
    return {
        "conviction_component": _round(conviction),
        "rank_component": _round(rank_component),
        "concentration_penalty": _round(concentration_penalty),
        "turnover_penalty": _round(turnover_penalty),
        "data_completeness_component": _round(completeness_component),
        "total": _round(total),
    }


def _candidate_variants(frontier: Mapping[str, Any], *, turnover_cap: float | None) -> list[dict[str, Any]]:
    available_candidate_count = _as_dict(frontier.get("candidate_pool")).get("unique_eligible_ticker_count")
    variants: list[dict[str, Any]] = []
    for variant in _as_list(frontier.get("frontier_variants")):
        if not isinstance(variant, Mapping):
            continue
        guardrails = _guardrail_status(
            variant,
            available_candidate_count=int(available_candidate_count) if isinstance(available_candidate_count, int) else None,
            turnover_cap=turnover_cap,
        )
        score_components = _score_components(variant)
        rejection_reasons = _rejection_reasons(variant, guardrails)
        score = score_components.get("total")
        eligible = guardrails.get("overall") == "PASS" and score is not None and not rejection_reasons
        variants.append(
            {
                "variant_id": variant.get("variant_id"),
                "top_n": variant.get("top_n"),
                "selected_count": variant.get("selected_count"),
                "selected_tickers": list(_as_list(variant.get("selected_tickers"))),
                "metrics_available": _metrics_available(variant),
                "guardrail_status": guardrails,
                "score": score,
                "score_components": score_components,
                "rejection_reasons": rejection_reasons,
                "rank": None,
                "eligible_for_research_selection": bool(eligible),
                "source_frontier_metrics": {
                    "aggregate_conviction_score": variant.get("aggregate_conviction_score"),
                    "average_rank": variant.get("average_rank"),
                    "effective_N": variant.get("effective_N"),
                    "HHI": variant.get("HHI"),
                    "max_single_name_weight": variant.get("max_single_name_weight"),
                    "estimated_turnover_from_current_policy": variant.get("estimated_turnover_from_current_policy"),
                    "data_completeness": variant.get("data_completeness"),
                    "unavailable_reason": variant.get("unavailable_reason"),
                },
            }
        )
    ranked = sorted(
        [variant for variant in variants if variant["eligible_for_research_selection"]],
        key=lambda variant: (
            -float(variant["score"]),
            -float(_first_present(_as_dict(variant.get("source_frontier_metrics")).get("aggregate_conviction_score"), 0.0)),
            float(_first_present(_as_dict(variant.get("source_frontier_metrics")).get("HHI"), float("inf"))),
            float(
                _first_present(
                    _as_dict(variant.get("source_frontier_metrics")).get("estimated_turnover_from_current_policy"),
                    float("inf"),
                )
            ),
            int(_first_present(variant.get("top_n"), 10**9)),
            str(variant.get("variant_id") or ""),
        ),
    )
    for rank, variant in enumerate(ranked, start=1):
        variant["rank"] = rank
    return variants


def _select_variant(candidate_variants: list[Mapping[str, Any]]) -> dict[str, Any]:
    ranked = sorted(
        [variant for variant in candidate_variants if variant.get("eligible_for_research_selection") and variant.get("rank")],
        key=lambda variant: int(variant.get("rank") or 10**9),
    )
    if not ranked:
        sparse = all(not _as_list(variant.get("selected_tickers")) for variant in candidate_variants)
        return {
            "selected_variant_id": None,
            "selected_top_n": None,
            "selected_tickers": [],
            "selection_reason": None,
            "selection_confidence": "LOW",
            "fallback_reason": "sparse_or_unavailable_input" if sparse else "all_variants_failed_guardrails_or_scoring",
            "status": "NO_SELECTION_SPARSE_INPUT" if sparse else "NO_SELECTION_GUARDRAILS_FAILED",
        }
    selected = ranked[0]
    metrics = _as_dict(selected.get("metrics_available"))
    confidence = "HIGH" if all(metrics.values()) else "MEDIUM"
    return {
        "selected_variant_id": selected.get("variant_id"),
        "selected_top_n": selected.get("top_n"),
        "selected_tickers": list(_as_list(selected.get("selected_tickers"))),
        "selection_reason": "highest_guardrail_passing_research_score",
        "selection_confidence": confidence,
        "fallback_reason": None,
        "status": "SELECTED_RESEARCH_ONLY",
    }


def _comparison_to_current_policy(
    baseline: Mapping[str, Any],
    selected: Mapping[str, Any],
    candidate_variants: list[Mapping[str, Any]],
) -> dict[str, Any]:
    metrics = _as_dict(baseline.get("baseline_metrics"))
    selected_variant = next(
        (variant for variant in candidate_variants if variant.get("variant_id") == selected.get("selected_variant_id")),
        None,
    )
    selected_metrics = _as_dict(selected_variant.get("source_frontier_metrics")) if isinstance(selected_variant, Mapping) else {}
    current_count = metrics.get("position_count")
    selected_count = selected_variant.get("selected_count") if isinstance(selected_variant, Mapping) else None
    current_hhi = _float(metrics.get("HHI"))
    selected_hhi = _float(selected_metrics.get("HHI"))
    current_effective_n = _float(metrics.get("effective_N"))
    selected_effective_n = _float(selected_metrics.get("effective_N"))
    turnover = _float(selected_metrics.get("estimated_turnover_from_current_policy"))
    cannot_compare: list[str] = []
    if current_count is None:
        cannot_compare.append("current_policy_position_count_unavailable")
    if selected_count is None:
        cannot_compare.append("selected_research_position_count_unavailable")
    if current_hhi is None:
        cannot_compare.append("current_policy_HHI_unavailable")
    if selected_hhi is None:
        cannot_compare.append("selected_research_HHI_unavailable")
    if current_effective_n is None:
        cannot_compare.append("current_policy_effective_N_unavailable")
    if selected_effective_n is None:
        cannot_compare.append("selected_research_effective_N_unavailable")
    if turnover is None:
        cannot_compare.append("estimated_turnover_from_current_policy_unavailable")
    delta = (int(selected_count) - int(current_count)) if isinstance(selected_count, int) and isinstance(current_count, int) else None
    return {
        "current_policy_position_count": current_count,
        "selected_research_position_count": selected_count,
        "delta_position_count": delta,
        "current_policy_HHI": current_hhi,
        "selected_research_HHI": selected_hhi,
        "current_policy_effective_N": current_effective_n,
        "selected_research_effective_N": selected_effective_n,
        "estimated_turnover_from_current_policy": turnover,
        "cannot_compare_reasons": sorted(set(cannot_compare)),
    }


def _unavailable_fields(
    *,
    source_paths: Mapping[str, Any],
    data_asof: Any,
    universe_asof: Any,
    price_asof: Any,
    candidate_variants: list[Mapping[str, Any]],
    selected: Mapping[str, Any],
    comparison: Mapping[str, Any],
) -> list[str]:
    unavailable: list[str] = []
    for key, value in source_paths.items():
        if value is None or value == "unavailable" or value == []:
            unavailable.append(f"source_artifact_paths.{key}")
    if data_asof is None:
        unavailable.append("pit_controls.data_asof")
    if universe_asof is None:
        unavailable.append("pit_controls.universe_asof")
    if price_asof is None:
        unavailable.append("pit_controls.price_asof")
    if selected.get("status") == "NO_SELECTION_SPARSE_INPUT":
        unavailable.append("selected_research_variant")
    for idx, variant in enumerate(candidate_variants):
        if variant.get("score") is None:
            unavailable.append(f"candidate_variants[{idx}].score")
        if variant.get("rejection_reasons"):
            unavailable.append(f"candidate_variants[{idx}].rejection_reasons")
    for reason in _as_list(comparison.get("cannot_compare_reasons")):
        unavailable.append(f"comparison_to_current_policy.{reason}")
    return sorted(set(unavailable))


def _metadata_flags() -> dict[str, Any]:
    return {
        "shadow_only": True,
        "alpha_chase_default": "off",
        "trading_behavior_changed": False,
        "optimizer_behavior_changed": False,
        "sizing_behavior_changed": False,
        "broker_behavior_changed": False,
        "paper_behavior_changed": False,
        "live_pilot_behavior_changed": False,
        "cron_or_scheduler_behavior_changed": False,
        "order_submission_behavior_changed": False,
        "paper_or_live_influence_allowed": False,
        "alpha_chase_recommendations_allowed": False,
    }


def _blocked_phase3_holding_count(
    *,
    repo_root: Path,
    trade_date: str,
    run_id: str | None,
    contract_path: Path | None,
    baseline_path: Path | None,
    frontier_path: Path | None,
    completeness_gate: Mapping[str, Any],
    reason: str,
    generated_at: str | None,
) -> dict[str, Any]:
    blocking_gaps = phase01_score_driven_blocking_gaps(completeness_gate)
    if reason == "phase01_completeness_blocked" and blocking_gaps == [SCORE_SOURCE_REPLAY_GAP]:
        reason = SCORE_SOURCE_REPLAY_GAP
    if reason and reason not in blocking_gaps:
        blocking_gaps.append(reason)
        blocking_gaps = sorted(set(blocking_gaps))
    contract_id = str(completeness_gate.get("contract_id") or run_id or trade_date)
    unavailable = sorted(
        set(
            [
                "candidate_variants",
                "selected_research_variant",
                "comparison_to_current_policy",
                "phase2_frontier",
            ]
            + [f"phase01.{gap}" for gap in blocking_gaps]
        )
    )
    phase3: dict[str, Any] = {
        "metadata": {
            "trade_date": trade_date,
            "generated_at": generated_at or "unavailable",
            "git_sha": "unavailable",
            "mode": "research_only",
            "fr_id": FR_ID,
            "phase": "Phase 3",
            "schema_version": PHASE3_SCHEMA_VERSION,
            "contract_id": contract_id,
            "production_execution_modules_invoked": [],
            **_metadata_flags(),
        },
        "input_completeness": dict(completeness_gate),
        "input_contract": {
            "path": _relative(contract_path, repo_root),
            "schema_version": None,
            "contract_id": None,
            "trade_date": trade_date,
            "validation_status": {"status": "UNAVAILABLE", "findings": [], "warnings": []},
        },
        "input_baseline": {
            "path": _relative(baseline_path, repo_root),
            "schema_version": None,
            "contract_id": None,
            "trade_date": trade_date,
            "validation_status": {"status": "UNAVAILABLE", "findings": [], "warnings": []},
        },
        "input_frontier": {
            "path": _relative(frontier_path, repo_root),
            "schema_version": None,
            "contract_id": None,
            "trade_date": trade_date,
            "validation_status": {"status": "UNAVAILABLE", "findings": [], "warnings": []},
        },
        "readiness": {
            "status": BLOCKED_ARTIFACT_GAPS,
            "blocking_gaps": blocking_gaps,
            "shadow_evaluation_ready": False,
            "recommendations_allowed": False,
            "paper_or_live_influence_allowed": False,
        },
        "pit_controls": {
            "trade_date": trade_date,
            "data_asof": None,
            "universe_asof": None,
            "price_asof": None,
            "no_forward_returns_used": True,
            "no_production_modules_invoked": True,
            "source_artifact_paths": {
                "phase01_completeness_path": completeness_gate.get("path"),
                "phase0_replay_contract_path": _relative(contract_path, repo_root),
                "phase1_baseline_path": _relative(baseline_path, repo_root),
                "phase2_frontier_path": _relative(frontier_path, repo_root),
            },
            "unavailable_fields": unavailable,
        },
        "decision_policy": _decision_policy(None),
        "candidate_variants": [],
        "selected_research_variant": {
            "selected_variant_id": None,
            "selected_top_n": None,
            "selected_tickers": [],
            "selection_reason": None,
            "selection_confidence": "LOW",
            "fallback_reason": reason,
            "status": BLOCKED_ARTIFACT_GAPS,
        },
        "comparison_to_current_policy": {
            "current_policy_position_count": None,
            "selected_research_position_count": None,
            "delta_position_count": None,
            "current_policy_HHI": None,
            "selected_research_HHI": None,
            "current_policy_effective_N": None,
            "selected_research_effective_N": None,
            "estimated_turnover_from_current_policy": None,
            "cannot_compare_reasons": [BLOCKED_ARTIFACT_GAPS],
        },
        "data_quality": {
            "status": BLOCKED_ARTIFACT_GAPS,
            "sparse_artifact_handling": "PASS",
            "forward_returns_used": False,
            "pit_safe_return_data_available": False,
            "unavailable_fields": unavailable,
            "diagnostics": ["phase01_or_phase2_readiness_blocks_phase3_shadow_evaluation"],
        },
        "validation_status": {
            "status": "UNVALIDATED",
            "findings": [],
            "warnings": [],
        },
    }
    phase3 = _clean(phase3)
    phase3["validation_status"] = validate_fr105_phase3_holding_count(phase3).to_dict()
    return phase3


def build_shadow_alpha_chase_comparison(
    phase3: Mapping[str, Any],
    *,
    phase3_path: Path | str | None = None,
    repo_root: Path | str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    metadata = _as_dict(phase3.get("metadata"))
    readiness = _as_dict(phase3.get("readiness"))
    selected = _as_dict(phase3.get("selected_research_variant"))
    comparison = _as_dict(phase3.get("comparison_to_current_policy"))
    blocked = readiness.get("status") != READY or selected.get("status") != "SELECTED_RESEARCH_ONLY"
    root = Path(repo_root).resolve() if repo_root is not None else None
    phase3_rel = _relative(phase3_path, root) if root is not None else (str(phase3_path) if phase3_path else None)
    status = BLOCKED_ARTIFACT_GAPS if readiness.get("status") == BLOCKED_ARTIFACT_GAPS else selected.get("status") or "UNAVAILABLE"
    return {
        "schema_version": SHADOW_COMPARISON_SCHEMA_VERSION,
        "metadata": {
            "trade_date": metadata.get("trade_date"),
            "generated_at": generated_at or metadata.get("generated_at") or "unavailable",
            "fr_id": FR_ID,
            "mode": "shadow_only_research",
            "contract_id": metadata.get("contract_id"),
            "enabled": False,
            "default_off": True,
            "capitalized": False,
            "production_execution_modules_invoked": [],
            **_metadata_flags(),
        },
        "input_phase3_holding_count": {
            "path": phase3_rel,
            "schema_version": metadata.get("schema_version"),
            "validation_status": phase3.get("validation_status"),
        },
        "readiness": {
            "status": status,
            "blocking_gaps": list(_as_list(readiness.get("blocking_gaps"))),
            "shadow_evaluation_ready": not blocked,
            "recommendations_allowed": False,
            "paper_or_live_influence_allowed": False,
        },
        "current_policy": {
            "position_count": comparison.get("current_policy_position_count"),
            "HHI": comparison.get("current_policy_HHI"),
            "effective_N": comparison.get("current_policy_effective_N"),
        },
        "shadow_research_variant": {
            "status": selected.get("status"),
            "selected_variant_id": selected.get("selected_variant_id"),
            "selected_top_n": selected.get("selected_top_n"),
            "selected_tickers": list(_as_list(selected.get("selected_tickers"))),
            "position_count": comparison.get("selected_research_position_count"),
            "HHI": comparison.get("selected_research_HHI"),
            "effective_N": comparison.get("selected_research_effective_N"),
            "estimated_turnover_from_current_policy": comparison.get("estimated_turnover_from_current_policy"),
        },
        "recommendations": {
            "allowed": False,
            "items": [],
            "reason": "readiness_only_no_paper_or_live_influence",
        },
        "forbidden_claims": [
            "buy_sell_recommendations",
            "allocation_recommendations",
            "portfolio_replacement_claims",
            "paper_or_live_trading_instructions",
        ],
    }


def build_fr105_phase3_holding_count(
    *,
    repo_root: Path | str,
    input_contract_path: Path | str,
    input_baseline_path: Path | str,
    input_frontier_path: Path | str,
    input_completeness_path: Path | str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    contract_path = Path(input_contract_path)
    baseline_path = Path(input_baseline_path)
    frontier_path = Path(input_frontier_path)
    if not contract_path.is_absolute():
        contract_path = root / contract_path
    if not baseline_path.is_absolute():
        baseline_path = root / baseline_path
    if not frontier_path.is_absolute():
        frontier_path = root / frontier_path
    completeness_path = Path(input_completeness_path) if input_completeness_path is not None else contract_path.parent / "phase01_artifact_completeness.json"
    if not completeness_path.is_absolute():
        completeness_path = root / completeness_path
    completeness_gate = phase01_completeness_gate(completeness_path, root)
    if not completeness_gate.get("ready"):
        return _blocked_phase3_holding_count(
            repo_root=root,
            trade_date=str(_as_dict(_read_json(contract_path)).get("metadata", {}).get("trade_date") or _as_dict(_read_json(baseline_path)).get("metadata", {}).get("trade_date") or "unavailable"),
            run_id=None,
            contract_path=contract_path,
            baseline_path=baseline_path,
            frontier_path=frontier_path,
            completeness_gate=completeness_gate,
            reason="phase01_completeness_blocked",
            generated_at=generated_at,
        )
    contract = read_fr105_replay_contract(contract_path)
    baseline = _as_dict(_read_json(baseline_path))
    frontier = _as_dict(_read_json(frontier_path))
    frontier_readiness = _as_dict(frontier.get("readiness"))
    if frontier_readiness.get("status") != READY:
        gate = dict(completeness_gate)
        gaps = set(_as_list(gate.get("blocking_gaps")))
        gaps.update(_as_list(frontier_readiness.get("blocking_gaps")))
        gaps.add("phase2_frontier_not_ready")
        gate.update({"ready": False, "status": BLOCKED_ARTIFACT_GAPS, "blocking_gaps": sorted(gaps)})
        return _blocked_phase3_holding_count(
            repo_root=root,
            trade_date=str(_as_dict(contract.get("metadata")).get("trade_date") or _as_dict(baseline.get("metadata")).get("trade_date") or _as_dict(frontier.get("metadata")).get("trade_date") or "unavailable"),
            run_id=None,
            contract_path=contract_path,
            baseline_path=baseline_path,
            frontier_path=frontier_path,
            completeness_gate=gate,
            reason="phase2_frontier_not_ready",
            generated_at=generated_at,
        )
    metadata = _as_dict(contract.get("metadata"))
    baseline_metadata = _as_dict(baseline.get("metadata"))
    frontier_metadata = _as_dict(frontier.get("metadata"))
    trade_date = str(
        metadata.get("trade_date")
        or baseline_metadata.get("trade_date")
        or frontier_metadata.get("trade_date")
        or "unavailable"
    )
    turnover_cap = _turnover_cap(contract)
    candidate_variants = _candidate_variants(frontier, turnover_cap=turnover_cap)
    selected = _select_variant(candidate_variants)
    comparison = _comparison_to_current_policy(baseline, selected, candidate_variants)
    source_paths = _source_artifact_paths(
        contract=contract,
        baseline=baseline,
        frontier=frontier,
        contract_path=contract_path,
        baseline_path=baseline_path,
        frontier_path=frontier_path,
        repo_root=root,
    )
    data_asof, universe_asof, price_asof = _asof_values(contract, baseline, frontier)
    unavailable = _unavailable_fields(
        source_paths=source_paths,
        data_asof=data_asof,
        universe_asof=universe_asof,
        price_asof=price_asof,
        candidate_variants=candidate_variants,
        selected=selected,
        comparison=comparison,
    )
    phase3: dict[str, Any] = {
        "metadata": {
            "trade_date": trade_date,
            "generated_at": generated_at or "unavailable",
            "git_sha": metadata.get("git_sha") or baseline_metadata.get("git_sha") or frontier_metadata.get("git_sha") or "unavailable",
            "mode": "research_only",
            "fr_id": FR_ID,
            "phase": "Phase 3",
            "schema_version": PHASE3_SCHEMA_VERSION,
            "contract_id": str(metadata.get("contract_id") or baseline_metadata.get("contract_id") or frontier_metadata.get("contract_id") or trade_date),
            "production_execution_modules_invoked": [],
            **_metadata_flags(),
        },
        "input_completeness": dict(completeness_gate),
        "input_contract": {
            "path": _relative(contract_path, root),
            "schema_version": metadata.get("schema_version"),
            "contract_id": metadata.get("contract_id"),
            "trade_date": metadata.get("trade_date"),
            "validation_status": validate_fr105_replay_contract(contract).to_dict(),
        },
        "input_baseline": {
            "path": _relative(baseline_path, root),
            "schema_version": baseline_metadata.get("schema_version"),
            "contract_id": baseline_metadata.get("contract_id"),
            "trade_date": baseline_metadata.get("trade_date"),
            "validation_status": validate_fr105_phase1_baseline(baseline).to_dict(),
        },
        "input_frontier": {
            "path": _relative(frontier_path, root),
            "schema_version": frontier_metadata.get("schema_version"),
            "contract_id": frontier_metadata.get("contract_id"),
            "trade_date": frontier_metadata.get("trade_date"),
            "validation_status": validate_fr105_phase2_topn_frontier(frontier).to_dict(),
        },
        "readiness": {
            "status": READY,
            "blocking_gaps": [],
            "shadow_evaluation_ready": True,
            "recommendations_allowed": False,
            "paper_or_live_influence_allowed": False,
        },
        "pit_controls": {
            "trade_date": trade_date,
            "data_asof": data_asof,
            "universe_asof": universe_asof,
            "price_asof": price_asof,
            "no_forward_returns_used": True,
            "no_production_modules_invoked": True,
            "source_artifact_paths": source_paths,
            "unavailable_fields": unavailable,
        },
        "decision_policy": _decision_policy(turnover_cap),
        "candidate_variants": candidate_variants,
        "selected_research_variant": selected,
        "comparison_to_current_policy": comparison,
        "data_quality": {
            "status": "SPARSE" if selected.get("status") == "NO_SELECTION_SPARSE_INPUT" else "PARTIAL",
            "sparse_artifact_handling": "PASS",
            "forward_returns_used": False,
            "pit_safe_return_data_available": False,
            "unavailable_fields": unavailable,
            "diagnostics": (
                ["phase2_frontier_sparse_no_research_selection"]
                if selected.get("status") == "NO_SELECTION_SPARSE_INPUT"
                else ["phase2_frontier_ranked_by_research_policy"]
            ),
        },
        "validation_status": {
            "status": "UNVALIDATED",
            "findings": [],
            "warnings": [],
        },
    }
    phase3 = _clean(phase3)
    phase3["validation_status"] = validate_fr105_phase3_holding_count(phase3).to_dict()
    return phase3


def write_fr105_phase3_holding_count(
    *,
    repo_root: Path | str,
    trade_date: str,
    run_id: str | None = None,
    input_contract_path: Path | str | None = None,
    input_baseline_path: Path | str | None = None,
    input_frontier_path: Path | str | None = None,
    input_completeness_path: Path | str | None = None,
    output_root: Path | str = DEFAULT_OUTPUT_ROOT,
    generated_at: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    root = Path(repo_root).resolve()
    completeness_path = find_phase01_completeness_path(
        repo_root=root,
        trade_date=trade_date,
        run_id=run_id,
        input_completeness_path=input_completeness_path,
        output_root=output_root,
    )
    completeness_gate = phase01_completeness_gate(completeness_path, root)
    contract_path = find_phase0_contract_path(
        repo_root=root,
        trade_date=trade_date,
        run_id=run_id,
        input_contract_path=input_contract_path,
        output_root=output_root,
    )
    baseline_path = find_phase1_baseline_path(
        repo_root=root,
        trade_date=trade_date,
        run_id=run_id,
        input_baseline_path=input_baseline_path,
        output_root=output_root,
    )
    frontier_path = find_phase2_frontier_path(
        repo_root=root,
        trade_date=trade_date,
        run_id=run_id,
        input_frontier_path=input_frontier_path,
        output_root=output_root,
    )
    if contract_path is None or baseline_path is None or frontier_path is None:
        gaps = set(_as_list(completeness_gate.get("blocking_gaps")))
        if contract_path is None:
            gaps.add("phase0_replay_contract")
        if baseline_path is None:
            gaps.add("phase1_current_policy_baseline")
        if frontier_path is None:
            gaps.add("phase2_frontier")
        completeness_gate = dict(completeness_gate)
        completeness_gate.update({"ready": False, "status": BLOCKED_ARTIFACT_GAPS, "blocking_gaps": sorted(gaps)})
    if not completeness_gate.get("ready"):
        phase3 = _blocked_phase3_holding_count(
            repo_root=root,
            trade_date=trade_date,
            run_id=run_id,
            contract_path=contract_path,
            baseline_path=baseline_path,
            frontier_path=frontier_path,
            completeness_gate=completeness_gate,
            reason="phase01_completeness_blocked",
            generated_at=generated_at,
        )
    else:
        phase3 = build_fr105_phase3_holding_count(
            repo_root=root,
            input_contract_path=contract_path,
            input_baseline_path=baseline_path,
            input_frontier_path=frontier_path,
            input_completeness_path=completeness_path,
            generated_at=generated_at,
        )
    out_root = Path(output_root)
    if not out_root.is_absolute():
        out_root = root / out_root
    out_path = out_root / str(phase3["metadata"]["contract_id"]) / ARTIFACT_NAME
    _write_json(out_path, phase3)
    shadow_path = out_path.parent / SHADOW_COMPARISON_ARTIFACT_NAME
    shadow = build_shadow_alpha_chase_comparison(
        phase3,
        phase3_path=out_path,
        repo_root=root,
        generated_at=generated_at,
    )
    _write_json(shadow_path, shadow)
    return out_path, phase3


def _empty_string_paths(value: Any, prefix: str = "$") -> list[str]:
    if isinstance(value, str):
        return [prefix] if value == "" else []
    if isinstance(value, list):
        paths: list[str] = []
        for idx, item in enumerate(value):
            paths.extend(_empty_string_paths(item, f"{prefix}[{idx}]"))
        return paths
    if isinstance(value, Mapping):
        paths = []
        for key, item in value.items():
            paths.extend(_empty_string_paths(item, f"{prefix}.{key}"))
        return paths
    return []


def _numeric_or_null(value: Any) -> bool:
    if value is None or value == "unavailable":
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    return False


def validate_fr105_phase3_holding_count(phase3: Mapping[str, Any]) -> FR105Phase3ValidationResult:
    findings: list[str] = []
    warnings: list[str] = []

    missing_sections = [key for key in REQUIRED_TOP_LEVEL_SECTIONS if key not in phase3]
    if missing_sections:
        findings.append(f"MISSING_TOP_LEVEL_SECTIONS:{','.join(missing_sections)}")

    metadata = _as_dict(phase3.get("metadata"))
    if metadata.get("mode") != "research_only":
        findings.append("MODE_NOT_RESEARCH_ONLY")
    if metadata.get("fr_id") != FR_ID:
        findings.append("FR_ID_MISMATCH")
    if not metadata.get("schema_version"):
        findings.append("MISSING_SCHEMA_VERSION")
    invoked = metadata.get("production_execution_modules_invoked")
    if invoked not in ([], None):
        findings.append("PRODUCTION_EXECUTION_MODULES_INVOKED")
    if isinstance(invoked, list):
        prohibited = sorted(set(str(item) for item in invoked if str(item) in PROHIBITED_PRODUCTION_MODULES))
        if prohibited:
            findings.append(f"PROHIBITED_PRODUCTION_MODULES:{','.join(prohibited)}")

    controls = _as_dict(phase3.get("pit_controls"))
    missing_controls = [key for key in REQUIRED_PIT_CONTROL_KEYS if key not in controls]
    if missing_controls:
        findings.append(f"MISSING_PIT_CONTROL_KEYS:{','.join(missing_controls)}")
    if controls.get("no_forward_returns_used") is not True:
        findings.append("FORWARD_RETURNS_USED_OR_UNCONFIRMED")
    if controls.get("no_production_modules_invoked") is not True:
        findings.append("PRODUCTION_MODULE_INVOCATION_FLAG_NOT_TRUE")
    if "source_artifact_paths" in controls and not isinstance(controls.get("source_artifact_paths"), Mapping):
        findings.append("MALFORMED_MAPPING:pit_controls.source_artifact_paths")
    if "unavailable_fields" in controls and not isinstance(controls.get("unavailable_fields"), list):
        findings.append("MALFORMED_LIST:pit_controls.unavailable_fields")

    policy = _as_dict(phase3.get("decision_policy"))
    missing_policy = [key for key in REQUIRED_DECISION_POLICY_KEYS if key not in policy]
    if missing_policy:
        findings.append(f"MISSING_DECISION_POLICY_KEYS:{','.join(missing_policy)}")

    candidate_variants = phase3.get("candidate_variants")
    if not isinstance(candidate_variants, list):
        findings.append("MALFORMED_LIST:candidate_variants")
        candidate_variants = []
    candidate_by_id: dict[str, Mapping[str, Any]] = {}
    for idx, variant in enumerate(candidate_variants):
        if not isinstance(variant, Mapping):
            findings.append(f"MALFORMED_CANDIDATE_VARIANT:{idx}")
            continue
        missing_variant = [key for key in REQUIRED_CANDIDATE_VARIANT_KEYS if key not in variant]
        if missing_variant:
            findings.append(f"MISSING_CANDIDATE_VARIANT_KEYS:{idx}:{','.join(missing_variant)}")
        variant_id = str(variant.get("variant_id") or "")
        if variant_id:
            candidate_by_id[variant_id] = variant
        if not _numeric_or_null(variant.get("score")):
            findings.append(f"MALFORMED_SCORE:{idx}")
        selected_tickers = _as_list(variant.get("selected_tickers"))
        if len(selected_tickers) != len(set(selected_tickers)):
            findings.append(f"DUPLICATE_TICKERS_IN_VARIANT:{idx}")

    selected = _as_dict(phase3.get("selected_research_variant"))
    status = selected.get("status")
    selected_id = selected.get("selected_variant_id")
    selected_tickers = _as_list(selected.get("selected_tickers"))
    if len(selected_tickers) != len(set(selected_tickers)):
        findings.append("DUPLICATE_TICKERS_IN_SELECTED_VARIANT")
    if status == "SELECTED_RESEARCH_ONLY":
        if not selected_id or selected_id not in candidate_by_id:
            findings.append("SELECTED_VARIANT_NOT_FOUND")
        else:
            variant = candidate_by_id[str(selected_id)]
            if variant.get("eligible_for_research_selection") is not True:
                findings.append("SELECTED_VARIANT_NOT_ELIGIBLE")
            if _as_dict(variant.get("guardrail_status")).get("overall") != "PASS":
                findings.append("SELECTED_VARIANT_GUARDRAILS_NOT_PASS")
    elif status == "NO_SELECTION_SPARSE_INPUT":
        sparse_ok = bool(candidate_variants) and all(not _as_list(variant.get("selected_tickers")) for variant in candidate_variants)
        if not sparse_ok:
            findings.append("NO_SELECTION_SPARSE_INPUT_WITH_NONSPARSE_VARIANTS")
    elif status == BLOCKED_ARTIFACT_GAPS:
        readiness = _as_dict(phase3.get("readiness"))
        if readiness.get("status") != BLOCKED_ARTIFACT_GAPS:
            findings.append("BLOCKED_SELECTED_VARIANT_WITHOUT_BLOCKED_READINESS")
    elif status not in {"NO_SELECTION_GUARDRAILS_FAILED", "NO_SELECTION_NO_NUMERIC_SCORE"}:
        findings.append("UNKNOWN_SELECTED_RESEARCH_VARIANT_STATUS")

    data_quality = _as_dict(phase3.get("data_quality"))
    if data_quality.get("sparse_artifact_handling") != "PASS":
        findings.append("SPARSE_ARTIFACT_HANDLING_NOT_PASS")
    if data_quality.get("forward_returns_used") not in (False, None):
        findings.append("FORWARD_RETURNS_USED_IN_DATA_QUALITY")
    if "unavailable_fields" in data_quality and not isinstance(data_quality.get("unavailable_fields"), list):
        findings.append("MALFORMED_LIST:data_quality.unavailable_fields")

    empty_strings = _empty_string_paths(phase3)
    if empty_strings:
        findings.append(f"EMPTY_STRING_VALUES:{','.join(empty_strings[:10])}")
        if len(empty_strings) > 10:
            warnings.append(f"EMPTY_STRING_VALUES_TRUNCATED:{len(empty_strings)}")

    return FR105Phase3ValidationResult(
        status="PASS" if not findings else "FAIL",
        findings=sorted(set(findings)),
        warnings=sorted(set(warnings)),
    )

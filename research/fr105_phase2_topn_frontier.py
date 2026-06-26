"""FR-105 Phase 2 global top-N frontier research.

This module builds hypothetical global top-N portfolios from Phase 0 candidate
provenance and compares them with the Phase 1 current-policy baseline. It is
research-only and does not invoke allocation, optimization, sizing, execution,
broker, scheduler, paper, or live trading code.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from research.fr105_phase1_baseline import find_phase0_contract_path
from research.fr105_replay_contract import (
    DEFAULT_OUTPUT_ROOT,
    FR_ID,
    PROHIBITED_PRODUCTION_MODULES,
    read_fr105_replay_contract,
    validate_fr105_replay_contract,
)


PHASE2_SCHEMA_VERSION = "fr105_phase2_global_topn_frontier.v1"
ARTIFACT_NAME = "phase2_global_topn_frontier.json"
COMPLETENESS_ARTIFACT_NAME = "phase01_artifact_completeness.json"
DEFAULT_TOP_N_VALUES = (5, 10, 15, 20, 25, 30)
READY = "READY"
BLOCKED_ARTIFACT_GAPS = "BLOCKED_ARTIFACT_GAPS"

REQUIRED_TOP_LEVEL_SECTIONS = (
    "metadata",
    "input_contract",
    "input_baseline",
    "pit_controls",
    "candidate_pool",
    "frontier_variants",
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

REQUIRED_CANDIDATE_KEYS = (
    "ticker",
    "sleeve_id",
    "strategy_id",
    "source_model",
    "rank",
    "score",
    "conviction_score",
    "expected_alpha",
    "expected_risk",
    "current_weight",
    "target_weight",
    "source_artifact_path",
    "inclusion_status",
    "exclusion_reason",
)

REQUIRED_VARIANT_KEYS = (
    "variant_id",
    "top_n",
    "selected_tickers",
    "selected_count",
    "unavailable_reason",
    "aggregate_conviction_score",
    "average_rank",
    "max_single_name_weight",
    "estimated_equal_weight",
    "HHI",
    "effective_N",
    "overlap_with_current_policy",
    "names_added_vs_current_policy",
    "names_removed_vs_current_policy",
    "estimated_turnover_from_current_policy",
    "data_completeness",
)


@dataclass(frozen=True)
class FR105Phase2ValidationResult:
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


def find_phase1_baseline_path(
    *,
    repo_root: Path | str,
    trade_date: str,
    run_id: str | None = None,
    input_baseline_path: Path | str | None = None,
    output_root: Path | str = DEFAULT_OUTPUT_ROOT,
) -> Path | None:
    root = Path(repo_root).resolve()
    if input_baseline_path is not None:
        path = Path(input_baseline_path)
        if not path.is_absolute():
            path = root / path
        return path if path.exists() else None
    out_root = Path(output_root)
    if not out_root.is_absolute():
        out_root = root / out_root
    if run_id:
        path = out_root / run_id / "phase1_current_policy_baseline.json"
        return path if path.exists() else None
    date_path = out_root / trade_date / "phase1_current_policy_baseline.json"
    if date_path.exists():
        return date_path
    matches: list[Path] = []
    for path in sorted(out_root.glob("*/phase1_current_policy_baseline.json")):
        payload = _as_dict(_read_json(path))
        if _as_dict(payload.get("metadata")).get("trade_date") == trade_date:
            matches.append(path)
    return matches[-1] if matches else None


def find_phase01_completeness_path(
    *,
    repo_root: Path | str,
    trade_date: str,
    run_id: str | None = None,
    input_completeness_path: Path | str | None = None,
    output_root: Path | str = DEFAULT_OUTPUT_ROOT,
) -> Path | None:
    root = Path(repo_root).resolve()
    if input_completeness_path is not None:
        path = Path(input_completeness_path)
        if not path.is_absolute():
            path = root / path
        return path if path.exists() else None
    out_root = Path(output_root)
    if not out_root.is_absolute():
        out_root = root / out_root
    if run_id:
        path = out_root / run_id / COMPLETENESS_ARTIFACT_NAME
        return path if path.exists() else None
    date_path = out_root / trade_date / COMPLETENESS_ARTIFACT_NAME
    if date_path.exists():
        return date_path
    matches: list[Path] = []
    for path in sorted(out_root.glob(f"*/{COMPLETENESS_ARTIFACT_NAME}")):
        payload = _as_dict(_read_json(path))
        if _as_dict(payload.get("metadata")).get("trade_date") == trade_date:
            matches.append(path)
    return matches[-1] if matches else None


def phase01_completeness_gate(
    completeness_path: Path | str | None,
    repo_root: Path | str,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    path = Path(completeness_path) if completeness_path is not None else None
    if path is not None and not path.is_absolute():
        path = root / path
    payload = _as_dict(_read_json(path))
    if not payload:
        return {
            "ready": False,
            "status": BLOCKED_ARTIFACT_GAPS,
            "blocking_gaps": [COMPLETENESS_ARTIFACT_NAME],
            "path": _relative(path, root),
            "artifact_status": "MISSING",
            "summary_status": "MISSING",
            "complete": False,
            "contract_id": None,
        }
    summary = _as_dict(payload.get("summary"))
    readiness = _as_dict(payload.get("readiness"))
    blocking_gaps = list(_as_list(readiness.get("blocking_gaps")))
    if not blocking_gaps:
        blocking_gaps = list(_as_list(summary.get("missing_fields"))) + list(_as_list(summary.get("unavailable_fields")))
    status = str(readiness.get("status") or (READY if summary.get("complete") is True and not blocking_gaps else BLOCKED_ARTIFACT_GAPS))
    complete = summary.get("complete") is True
    ready = status == READY and complete and not blocking_gaps
    return {
        "ready": ready,
        "status": READY if ready else BLOCKED_ARTIFACT_GAPS,
        "blocking_gaps": sorted({str(gap) for gap in blocking_gaps if str(gap)}),
        "path": _relative(path, root),
        "artifact_status": "FOUND",
        "summary_status": summary.get("status") or ("COMPLETE" if complete else "INCOMPLETE"),
        "complete": complete,
        "contract_id": _as_dict(payload.get("metadata")).get("contract_id"),
    }


def _source_artifact_paths(
    *,
    contract: Mapping[str, Any],
    baseline: Mapping[str, Any],
    contract_path: Path,
    baseline_path: Path,
    repo_root: Path,
) -> dict[str, Any]:
    paths = dict(_as_dict(contract.get("source_artifacts")))
    paths["phase0_replay_contract_path"] = _relative(contract_path, repo_root)
    paths["phase1_baseline_path"] = _relative(baseline_path, repo_root)
    for key, value in _as_dict(_as_dict(baseline.get("pit_controls")).get("source_artifact_paths")).items():
        paths.setdefault(f"phase1.{key}", value)
    return paths


def _asof_values(contract: Mapping[str, Any], baseline: Mapping[str, Any]) -> tuple[Any, Any, Any]:
    baseline_controls = _as_dict(baseline.get("pit_controls"))
    universe = _as_dict(contract.get("universe_snapshot"))
    candidates = [row for row in _as_list(contract.get("sleeve_candidates")) if isinstance(row, Mapping)]
    candidate_asofs = sorted({str(row.get("data_asof")) for row in candidates if row.get("data_asof")})
    data_asof = _first_present(baseline_controls.get("data_asof"), candidate_asofs[0] if len(candidate_asofs) == 1 else None)
    universe_asof = _first_present(
        baseline_controls.get("universe_asof"),
        universe.get("asof") if universe.get("status") != "unavailable" else None,
    )
    price_asof = _first_present(baseline_controls.get("price_asof"), _as_dict(contract.get("metadata")).get("price_asof"))
    return data_asof, universe_asof, price_asof


def _candidate_signal(candidate: Mapping[str, Any]) -> tuple[float | None, str | None]:
    for key in ("conviction_score", "score", "expected_alpha"):
        value = _float(candidate.get(key))
        if value is not None:
            return value, key
    rank = _float(candidate.get("rank"))
    if rank is not None:
        return -rank, "rank"
    return None, None


def _candidate_pool(contract: Mapping[str, Any]) -> list[dict[str, Any]]:
    pool: list[dict[str, Any]] = []
    for item in _as_list(contract.get("sleeve_candidates")):
        if not isinstance(item, Mapping):
            continue
        ticker = str(item.get("ticker") or "").strip().upper() or None
        signal, signal_source = _candidate_signal(item)
        eligible = ticker is not None and signal is not None
        exclusion_reason = None
        if ticker is None:
            exclusion_reason = "missing_ticker"
        elif signal is None:
            exclusion_reason = "missing_rank_score_conviction_or_alpha"
        pool.append(
            {
                "ticker": ticker,
                "sleeve_id": item.get("sleeve_id"),
                "strategy_id": item.get("strategy_id"),
                "source_model": item.get("source_model"),
                "rank": item.get("rank"),
                "score": item.get("score"),
                "conviction_score": item.get("conviction_score"),
                "expected_alpha": item.get("expected_alpha"),
                "expected_risk": item.get("expected_risk"),
                "current_weight": item.get("current_weight"),
                "target_weight": item.get("target_weight"),
                "source_artifact_path": item.get("source_artifact_path"),
                "inclusion_status": "eligible" if eligible else "excluded",
                "exclusion_reason": exclusion_reason,
                "selection_signal": signal,
                "selection_signal_source": signal_source,
            }
        )
    return pool


def _candidate_sort_key(candidate: Mapping[str, Any]) -> tuple[float, float, float, float, str]:
    conviction = _float(candidate.get("conviction_score"))
    score = _float(candidate.get("score"))
    expected_alpha = _float(candidate.get("expected_alpha"))
    rank = _float(candidate.get("rank"))
    return (
        -(conviction if conviction is not None else float("-inf")),
        -(score if score is not None else float("-inf")),
        -(expected_alpha if expected_alpha is not None else float("-inf")),
        rank if rank is not None else float("inf"),
        str(candidate.get("ticker") or ""),
    )


def _eligible_unique_candidates(pool: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    best_by_ticker: dict[str, dict[str, Any]] = {}
    for candidate in sorted(pool, key=_candidate_sort_key):
        if candidate.get("inclusion_status") != "eligible":
            continue
        ticker = str(candidate.get("ticker") or "").upper()
        if ticker and ticker not in best_by_ticker:
            best_by_ticker[ticker] = dict(candidate)
    return [best_by_ticker[ticker] for ticker in sorted(best_by_ticker, key=lambda ticker: _candidate_sort_key(best_by_ticker[ticker]))]


def _current_policy_weights(baseline: Mapping[str, Any]) -> dict[str, float]:
    weights: dict[str, float] = {}
    for row in _as_list(_as_dict(baseline.get("baseline_positions")).get("positions")):
        if not isinstance(row, Mapping):
            continue
        ticker = str(_first_present(row.get("ticker"), row.get("symbol")) or "").strip().upper()
        weight = _float(_first_present(row.get("current_weight"), row.get("weight")))
        if ticker and weight is not None:
            weights[ticker] = abs(float(weight))
    return weights


def _estimated_turnover(selected_tickers: list[str], current_weights: Mapping[str, float]) -> float | None:
    if not current_weights:
        return None
    if not selected_tickers:
        return None
    equal_weight = 1.0 / float(len(selected_tickers))
    new_weights = {ticker: equal_weight for ticker in selected_tickers}
    names = sorted(set(current_weights) | set(new_weights))
    return 0.5 * sum(abs(float(new_weights.get(name, 0.0)) - float(current_weights.get(name, 0.0))) for name in names)


def _variant_for_top_n(
    *,
    top_n: int,
    pool: list[Mapping[str, Any]],
    eligible: list[Mapping[str, Any]],
    current_weights: Mapping[str, float],
) -> dict[str, Any]:
    selected = [dict(candidate) for candidate in eligible[: max(0, int(top_n))]]
    selected_tickers = [str(candidate["ticker"]) for candidate in selected]
    selected_count = len(selected_tickers)
    current_tickers = set(current_weights)
    overlap = sorted(set(selected_tickers) & current_tickers)
    names_added = sorted(set(selected_tickers) - current_tickers)
    names_removed = sorted(current_tickers - set(selected_tickers))
    conviction_values = [_float(candidate.get("conviction_score")) for candidate in selected]
    numeric_conviction = [float(value) for value in conviction_values if value is not None]
    rank_values = [_float(candidate.get("rank")) for candidate in selected]
    numeric_ranks = [float(value) for value in rank_values if value is not None]
    equal_weight = 1.0 / float(selected_count) if selected_count else None
    hhi = selected_count * equal_weight * equal_weight if equal_weight is not None else None
    unavailable_reason = None
    if not pool:
        unavailable_reason = "candidate_pool_unavailable"
    elif not eligible:
        unavailable_reason = "eligible_candidate_pool_unavailable"
    elif selected_count < int(top_n):
        unavailable_reason = "candidate_pool_smaller_than_top_n"
    return {
        "variant_id": f"global_top_{int(top_n)}",
        "top_n": int(top_n),
        "selected_tickers": selected_tickers,
        "selected_count": selected_count,
        "unavailable_reason": unavailable_reason,
        "aggregate_conviction_score": _round(sum(numeric_conviction)) if numeric_conviction else None,
        "average_rank": _round(sum(numeric_ranks) / len(numeric_ranks)) if numeric_ranks else None,
        "max_single_name_weight": _round(equal_weight),
        "estimated_equal_weight": _round(equal_weight),
        "HHI": _round(hhi),
        "effective_N": _round(1.0 / hhi) if hhi is not None and hhi > 0 else None,
        "overlap_with_current_policy": overlap,
        "names_added_vs_current_policy": names_added,
        "names_removed_vs_current_policy": names_removed,
        "estimated_turnover_from_current_policy": _round(_estimated_turnover(selected_tickers, current_weights)),
        "data_completeness": {
            "candidate_pool_count": len(pool),
            "eligible_unique_candidate_count": len(eligible),
            "selected_with_conviction_score": len(numeric_conviction),
            "selected_with_rank": len(numeric_ranks),
            "current_policy_positions_available": bool(current_weights),
            "status": "SPARSE" if unavailable_reason in {"candidate_pool_unavailable", "eligible_candidate_pool_unavailable"} else "PARTIAL",
        },
    }


def _frontier_variants(
    *,
    pool: list[Mapping[str, Any]],
    top_n_values: Sequence[int],
    current_weights: Mapping[str, float],
) -> list[dict[str, Any]]:
    eligible = _eligible_unique_candidates(pool)
    return [
        _variant_for_top_n(
            top_n=int(top_n),
            pool=pool,
            eligible=eligible,
            current_weights=current_weights,
        )
        for top_n in top_n_values
    ]


def _comparison_to_current_policy(baseline: Mapping[str, Any], variants: list[Mapping[str, Any]]) -> dict[str, Any]:
    metrics = _as_dict(baseline.get("baseline_metrics"))
    current_hhi = _float(metrics.get("HHI"))
    current_effective_n = _float(metrics.get("effective_N"))
    current_max = _float(metrics.get("max_single_name_weight"))
    current_count = metrics.get("position_count")
    variants_with_conviction = [
        variant
        for variant in variants
        if _float(variant.get("aggregate_conviction_score")) is not None
    ]
    best = None
    if variants_with_conviction:
        best_variant = max(
            variants_with_conviction,
            key=lambda variant: (
                float(_float(variant.get("aggregate_conviction_score")) or 0.0),
                -int(variant.get("top_n") or 0),
                str(variant.get("variant_id") or ""),
            ),
        )
        best = best_variant.get("variant_id")
    cannot_compare: list[str] = []
    if current_hhi is None:
        cannot_compare.append("current_policy_HHI_unavailable")
    if current_effective_n is None:
        cannot_compare.append("current_policy_effective_N_unavailable")
    if current_max is None:
        cannot_compare.append("current_policy_max_single_name_weight_unavailable")
    if current_count is None:
        cannot_compare.append("current_policy_position_count_unavailable")
    if not variants_with_conviction:
        cannot_compare.append("variant_conviction_scores_unavailable")
    more_concentrated: list[str] = []
    less_concentrated: list[str] = []
    if current_hhi is not None:
        for variant in variants:
            hhi = _float(variant.get("HHI"))
            if hhi is None:
                continue
            if hhi > current_hhi:
                more_concentrated.append(str(variant.get("variant_id")))
            elif hhi < current_hhi:
                less_concentrated.append(str(variant.get("variant_id")))
    return {
        "current_policy_position_count": current_count,
        "current_policy_HHI": current_hhi,
        "current_policy_effective_N": current_effective_n,
        "current_policy_max_single_name_weight": current_max,
        "best_available_variant_by_conviction": best,
        "variants_more_concentrated_than_current": more_concentrated,
        "variants_less_concentrated_than_current": less_concentrated,
        "cannot_compare_reasons": sorted(set(cannot_compare)),
    }


def _score_source_status(pool: list[Mapping[str, Any]]) -> dict[str, Any]:
    if not pool:
        return {"status": "UNAVAILABLE", "reason": "candidate_pool_unavailable"}
    counts: dict[str, int] = {}
    for candidate in pool:
        source = str(candidate.get("selection_signal_source") or "unavailable")
        counts[source] = counts.get(source, 0) + 1
    return {
        "status": "FOUND" if any(source != "unavailable" for source in counts) else "UNAVAILABLE",
        "selection_signal_source_counts": dict(sorted(counts.items())),
        "prohibited_weight_derived_score_used": False,
    }


def _selected_universe_status(contract: Mapping[str, Any], pool: list[Mapping[str, Any]]) -> dict[str, Any]:
    universe = _as_dict(contract.get("universe_snapshot"))
    found = universe.get("status") not in (None, "", "unavailable") or bool(universe.get("universe_id"))
    return {
        "status": "FOUND" if found else "UNAVAILABLE",
        "universe_id": universe.get("universe_id"),
        "asof": universe.get("asof"),
        "ticker_count": universe.get("ticker_count"),
        "candidate_count": len(pool),
        "source_artifact_path": universe.get("source_artifact_path"),
    }


def _constraint_summary(contract: Mapping[str, Any]) -> dict[str, Any]:
    constraints = _as_dict(contract.get("constraints_snapshot"))
    active = {
        key: value
        for key, value in constraints.items()
        if value not in (None, "", "unavailable", [], {})
    }
    return {
        "status": "FOUND" if active else "UNAVAILABLE",
        "active_constraints": active,
    }


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


def _blocked_variants(top_n_values: Sequence[int], reason: str) -> list[dict[str, Any]]:
    return [
        {
            "variant_id": f"global_top_{int(top_n)}",
            "top_n": int(top_n),
            "selected_tickers": [],
            "selected_count": 0,
            "unavailable_reason": reason,
            "aggregate_conviction_score": None,
            "average_rank": None,
            "max_single_name_weight": None,
            "estimated_equal_weight": None,
            "HHI": None,
            "effective_N": None,
            "overlap_with_current_policy": [],
            "names_added_vs_current_policy": [],
            "names_removed_vs_current_policy": [],
            "estimated_turnover_from_current_policy": None,
            "data_completeness": {
                "candidate_pool_count": 0,
                "eligible_unique_candidate_count": 0,
                "selected_with_conviction_score": 0,
                "selected_with_rank": 0,
                "current_policy_positions_available": False,
                "status": "SPARSE",
            },
        }
        for top_n in top_n_values
    ]


def _blocked_phase2_frontier(
    *,
    repo_root: Path,
    trade_date: str,
    run_id: str | None,
    contract_path: Path | None,
    baseline_path: Path | None,
    completeness_gate: Mapping[str, Any],
    top_n_values: Sequence[int],
    generated_at: str | None,
) -> dict[str, Any]:
    blocking_gaps = sorted({str(gap) for gap in _as_list(completeness_gate.get("blocking_gaps")) if str(gap)})
    contract_id = str(completeness_gate.get("contract_id") or run_id or trade_date)
    variants = _blocked_variants(top_n_values, "phase01_completeness_blocked")
    unavailable = sorted(
        set(
            ["candidate_pool.candidates", "frontier_variants", "score_source_status", "selected_universe_status"]
            + [f"phase01.{gap}" for gap in blocking_gaps]
        )
    )
    frontier: dict[str, Any] = {
        "metadata": {
            "trade_date": trade_date,
            "generated_at": generated_at or "unavailable",
            "git_sha": "unavailable",
            "mode": "research_only",
            "fr_id": FR_ID,
            "phase": "Phase 2",
            "schema_version": PHASE2_SCHEMA_VERSION,
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
            },
            "unavailable_fields": unavailable,
        },
        "score_source_status": {"status": "UNAVAILABLE", "reason": BLOCKED_ARTIFACT_GAPS},
        "selected_universe_status": {"status": "UNAVAILABLE", "reason": BLOCKED_ARTIFACT_GAPS},
        "constraint_summary": {"status": "UNAVAILABLE", "reason": BLOCKED_ARTIFACT_GAPS},
        "candidate_pool": {
            "candidate_count": 0,
            "eligible_candidate_count": 0,
            "unique_eligible_ticker_count": 0,
            "selection_method": "blocked_until_phase01_completeness_ready",
            "candidates": [],
        },
        "frontier_variants": variants,
        "comparison_to_current_policy": {
            "current_policy_position_count": None,
            "current_policy_HHI": None,
            "current_policy_effective_N": None,
            "current_policy_max_single_name_weight": None,
            "best_available_variant_by_conviction": None,
            "variants_more_concentrated_than_current": [],
            "variants_less_concentrated_than_current": [],
            "cannot_compare_reasons": [BLOCKED_ARTIFACT_GAPS],
        },
        "data_quality": {
            "status": BLOCKED_ARTIFACT_GAPS,
            "sparse_artifact_handling": "PASS",
            "forward_returns_used": False,
            "pit_safe_return_data_available": False,
            "unavailable_fields": unavailable,
            "diagnostics": ["phase01_completeness_blocks_phase2_shadow_evaluation"],
        },
        "validation_status": {
            "status": "UNVALIDATED",
            "findings": [],
            "warnings": [],
        },
    }
    frontier = _clean(frontier)
    frontier["validation_status"] = validate_fr105_phase2_topn_frontier(frontier).to_dict()
    return frontier


def _unavailable_fields(
    *,
    source_paths: Mapping[str, Any],
    data_asof: Any,
    universe_asof: Any,
    price_asof: Any,
    pool: list[Mapping[str, Any]],
    variants: list[Mapping[str, Any]],
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
    if not pool:
        unavailable.append("candidate_pool.candidates")
    for idx, variant in enumerate(variants):
        if variant.get("unavailable_reason"):
            unavailable.append(f"frontier_variants[{idx}].unavailable_reason")
        for key in ("aggregate_conviction_score", "average_rank", "estimated_turnover_from_current_policy"):
            if variant.get(key) is None:
                unavailable.append(f"frontier_variants[{idx}].{key}")
    for reason in _as_list(comparison.get("cannot_compare_reasons")):
        unavailable.append(f"comparison_to_current_policy.{reason}")
    return sorted(set(unavailable))


def build_fr105_phase2_topn_frontier(
    *,
    repo_root: Path | str,
    input_contract_path: Path | str,
    input_baseline_path: Path | str,
    input_completeness_path: Path | str | None = None,
    top_n_values: Sequence[int] = DEFAULT_TOP_N_VALUES,
    generated_at: str | None = None,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    contract_path = Path(input_contract_path)
    if not contract_path.is_absolute():
        contract_path = root / contract_path
    baseline_path = Path(input_baseline_path)
    if not baseline_path.is_absolute():
        baseline_path = root / baseline_path
    completeness_path = Path(input_completeness_path) if input_completeness_path is not None else contract_path.parent / COMPLETENESS_ARTIFACT_NAME
    if not completeness_path.is_absolute():
        completeness_path = root / completeness_path
    completeness_gate = phase01_completeness_gate(completeness_path, root)
    if not completeness_gate.get("ready"):
        return _blocked_phase2_frontier(
            repo_root=root,
            trade_date=str(_as_dict(_read_json(contract_path)).get("metadata", {}).get("trade_date") or _as_dict(_read_json(baseline_path)).get("metadata", {}).get("trade_date") or "unavailable"),
            run_id=None,
            contract_path=contract_path,
            baseline_path=baseline_path,
            completeness_gate=completeness_gate,
            top_n_values=top_n_values,
            generated_at=generated_at,
        )
    contract = read_fr105_replay_contract(contract_path)
    baseline = _as_dict(_read_json(baseline_path))
    contract_validation = validate_fr105_replay_contract(contract).to_dict()
    metadata = _as_dict(contract.get("metadata"))
    baseline_metadata = _as_dict(baseline.get("metadata"))
    trade_date = str(metadata.get("trade_date") or baseline_metadata.get("trade_date") or "unavailable")
    pool = _candidate_pool(contract)
    current_weights = _current_policy_weights(baseline)
    variants = _frontier_variants(pool=pool, top_n_values=top_n_values, current_weights=current_weights)
    comparison = _comparison_to_current_policy(baseline, variants)
    source_paths = _source_artifact_paths(
        contract=contract,
        baseline=baseline,
        contract_path=contract_path,
        baseline_path=baseline_path,
        repo_root=root,
    )
    data_asof, universe_asof, price_asof = _asof_values(contract, baseline)
    unavailable = _unavailable_fields(
        source_paths=source_paths,
        data_asof=data_asof,
        universe_asof=universe_asof,
        price_asof=price_asof,
        pool=pool,
        variants=variants,
        comparison=comparison,
    )
    frontier: dict[str, Any] = {
        "metadata": {
            "trade_date": trade_date,
            "generated_at": generated_at or "unavailable",
            "git_sha": metadata.get("git_sha") or baseline_metadata.get("git_sha") or "unavailable",
            "mode": "research_only",
            "fr_id": FR_ID,
            "phase": "Phase 2",
            "schema_version": PHASE2_SCHEMA_VERSION,
            "contract_id": str(metadata.get("contract_id") or baseline_metadata.get("contract_id") or trade_date),
            "production_execution_modules_invoked": [],
            **_metadata_flags(),
        },
        "input_completeness": dict(completeness_gate),
        "input_contract": {
            "path": _relative(contract_path, root),
            "schema_version": metadata.get("schema_version"),
            "contract_id": metadata.get("contract_id"),
            "trade_date": metadata.get("trade_date"),
            "validation_status": contract_validation,
        },
        "input_baseline": {
            "path": _relative(baseline_path, root),
            "schema_version": baseline_metadata.get("schema_version"),
            "contract_id": baseline_metadata.get("contract_id"),
            "trade_date": baseline_metadata.get("trade_date"),
            "validation_status": baseline.get("validation_status"),
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
        "score_source_status": _score_source_status(pool),
        "selected_universe_status": _selected_universe_status(contract, pool),
        "constraint_summary": _constraint_summary(contract),
        "candidate_pool": {
            "candidate_count": len(pool),
            "eligible_candidate_count": sum(1 for candidate in pool if candidate.get("inclusion_status") == "eligible"),
            "unique_eligible_ticker_count": len(_eligible_unique_candidates(pool)),
            "selection_method": "conviction_score_desc_score_desc_expected_alpha_desc_rank_asc_ticker_asc",
            "candidates": pool,
        },
        "frontier_variants": variants,
        "comparison_to_current_policy": comparison,
        "data_quality": {
            "status": "SPARSE" if not pool else "PARTIAL",
            "sparse_artifact_handling": "PASS",
            "forward_returns_used": False,
            "pit_safe_return_data_available": False,
            "unavailable_fields": unavailable,
            "diagnostics": (
                ["phase0_contract_candidate_pool_unavailable"]
                if not pool
                else ["phase0_contract_supplied_candidate_pool"]
            ),
        },
        "validation_status": {
            "status": "UNVALIDATED",
            "findings": [],
            "warnings": [],
        },
    }
    frontier = _clean(frontier)
    frontier["validation_status"] = validate_fr105_phase2_topn_frontier(frontier).to_dict()
    return frontier


def write_fr105_phase2_topn_frontier(
    *,
    repo_root: Path | str,
    trade_date: str,
    run_id: str | None = None,
    input_contract_path: Path | str | None = None,
    input_baseline_path: Path | str | None = None,
    input_completeness_path: Path | str | None = None,
    output_root: Path | str = DEFAULT_OUTPUT_ROOT,
    top_n_values: Sequence[int] = DEFAULT_TOP_N_VALUES,
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
    if contract_path is None or baseline_path is None:
        gaps = set(_as_list(completeness_gate.get("blocking_gaps")))
        if contract_path is None:
            gaps.add("phase0_replay_contract")
        if baseline_path is None:
            gaps.add("phase1_current_policy_baseline")
        completeness_gate = dict(completeness_gate)
        completeness_gate.update({"ready": False, "status": BLOCKED_ARTIFACT_GAPS, "blocking_gaps": sorted(gaps)})
    if not completeness_gate.get("ready"):
        frontier = _blocked_phase2_frontier(
            repo_root=root,
            trade_date=trade_date,
            run_id=run_id,
            contract_path=contract_path,
            baseline_path=baseline_path,
            completeness_gate=completeness_gate,
            top_n_values=top_n_values,
            generated_at=generated_at,
        )
    else:
        frontier = build_fr105_phase2_topn_frontier(
            repo_root=root,
            input_contract_path=contract_path,
            input_baseline_path=baseline_path,
            input_completeness_path=completeness_path,
            top_n_values=top_n_values,
            generated_at=generated_at,
        )
    out_root = Path(output_root)
    if not out_root.is_absolute():
        out_root = root / out_root
    out_path = out_root / str(frontier["metadata"]["contract_id"]) / ARTIFACT_NAME
    _write_json(out_path, frontier)
    return out_path, frontier


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


def validate_fr105_phase2_topn_frontier(frontier: Mapping[str, Any]) -> FR105Phase2ValidationResult:
    findings: list[str] = []
    warnings: list[str] = []

    missing_sections = [key for key in REQUIRED_TOP_LEVEL_SECTIONS if key not in frontier]
    if missing_sections:
        findings.append(f"MISSING_TOP_LEVEL_SECTIONS:{','.join(missing_sections)}")

    metadata = _as_dict(frontier.get("metadata"))
    if metadata.get("mode") != "research_only":
        findings.append("MODE_NOT_RESEARCH_ONLY")
    if metadata.get("fr_id") != FR_ID:
        findings.append("FR_ID_MISMATCH")
    if not metadata.get("schema_version"):
        findings.append("MISSING_SCHEMA_VERSION")
    if "production_execution_modules_invoked" not in metadata:
        findings.append("MISSING_PRODUCTION_EXECUTION_MODULE_INVOCATION_RECORD")
    invoked = metadata.get("production_execution_modules_invoked")
    if invoked not in ([], None):
        findings.append("PRODUCTION_EXECUTION_MODULES_INVOKED")
    if isinstance(invoked, list):
        prohibited = sorted(set(str(item) for item in invoked if str(item) in PROHIBITED_PRODUCTION_MODULES))
        if prohibited:
            findings.append(f"PROHIBITED_PRODUCTION_MODULES:{','.join(prohibited)}")

    controls = _as_dict(frontier.get("pit_controls"))
    missing_controls = [key for key in REQUIRED_PIT_CONTROL_KEYS if key not in controls]
    if missing_controls:
        findings.append(f"MISSING_PIT_CONTROL_KEYS:{','.join(missing_controls)}")
    if controls.get("no_production_modules_invoked") is not True:
        findings.append("PRODUCTION_MODULE_INVOCATION_FLAG_NOT_TRUE")
    if controls.get("no_forward_returns_used") is not True:
        if _as_dict(frontier.get("data_quality")).get("pit_safe_return_data_available") is not True:
            findings.append("FORWARD_RETURNS_USED_WITHOUT_PIT_SAFE_RETURN_DATA")
    if "source_artifact_paths" in controls and not isinstance(controls.get("source_artifact_paths"), Mapping):
        findings.append("MALFORMED_MAPPING:pit_controls.source_artifact_paths")
    if "unavailable_fields" in controls and not isinstance(controls.get("unavailable_fields"), list):
        findings.append("MALFORMED_LIST:pit_controls.unavailable_fields")

    candidate_pool = _as_dict(frontier.get("candidate_pool"))
    candidates = candidate_pool.get("candidates")
    if not isinstance(candidates, list):
        findings.append("MALFORMED_LIST:candidate_pool.candidates")
        candidates = []
    for idx, candidate in enumerate(candidates):
        if not isinstance(candidate, Mapping):
            findings.append(f"MALFORMED_CANDIDATE:{idx}")
            continue
        missing_candidate = [key for key in REQUIRED_CANDIDATE_KEYS if key not in candidate]
        if missing_candidate:
            findings.append(f"MISSING_CANDIDATE_KEYS:{idx}:{','.join(missing_candidate)}")

    variants = frontier.get("frontier_variants")
    if not isinstance(variants, list) or not variants:
        findings.append("MISSING_FRONTIER_VARIANTS")
        variants = []
    for idx, variant in enumerate(variants):
        if not isinstance(variant, Mapping):
            findings.append(f"MALFORMED_VARIANT:{idx}")
            continue
        missing_variant = [key for key in REQUIRED_VARIANT_KEYS if key not in variant]
        if missing_variant:
            findings.append(f"MISSING_VARIANT_KEYS:{idx}:{','.join(missing_variant)}")
        selected = variant.get("selected_tickers")
        if not isinstance(selected, list):
            findings.append(f"MALFORMED_LIST:frontier_variants[{idx}].selected_tickers")
            selected = []
        if len(selected) != len(set(selected)):
            findings.append(f"DUPLICATE_SELECTED_TICKERS:{idx}")
        top_n = variant.get("top_n")
        selected_count = variant.get("selected_count")
        if isinstance(top_n, int) and isinstance(selected_count, int) and selected_count > top_n:
            findings.append(f"SELECTED_COUNT_EXCEEDS_TOP_N:{idx}")
        for metric in (
            "aggregate_conviction_score",
            "average_rank",
            "max_single_name_weight",
            "estimated_equal_weight",
            "HHI",
            "effective_N",
            "estimated_turnover_from_current_policy",
        ):
            if metric in variant and not _numeric_or_null(variant.get(metric)):
                findings.append(f"MALFORMED_VARIANT_METRIC:{idx}:{metric}")

    sparse = not candidates
    if sparse:
        for idx, variant in enumerate(variants):
            if not _as_dict(variant).get("unavailable_reason"):
                findings.append(f"SPARSE_VARIANT_MISSING_UNAVAILABLE_REASON:{idx}")

    data_quality = _as_dict(frontier.get("data_quality"))
    if data_quality.get("sparse_artifact_handling") != "PASS":
        findings.append("SPARSE_ARTIFACT_HANDLING_NOT_PASS")
    if data_quality.get("forward_returns_used") not in (False, None):
        if data_quality.get("pit_safe_return_data_available") is not True:
            findings.append("FORWARD_RETURNS_USED_WITHOUT_PIT_SAFE_DATA_QUALITY_MARKER")
    if "unavailable_fields" in data_quality and not isinstance(data_quality.get("unavailable_fields"), list):
        findings.append("MALFORMED_LIST:data_quality.unavailable_fields")

    empty_strings = _empty_string_paths(frontier)
    if empty_strings:
        findings.append(f"EMPTY_STRING_VALUES:{','.join(empty_strings[:10])}")
        if len(empty_strings) > 10:
            warnings.append(f"EMPTY_STRING_VALUES_TRUNCATED:{len(empty_strings)}")

    status = "PASS" if not findings else "FAIL"
    return FR105Phase2ValidationResult(
        status=status,
        findings=sorted(set(findings)),
        warnings=sorted(set(warnings)),
    )

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "alpha_lab_v2_h2_h6_shadow_observation_v1"
CANDIDATE_ID = "caerus_alpha_lab_v2_h2_h6"
STRATEGY = "h2_rank_decay_exit_h6_top5"
APPROVAL = "ACCEPT_FOR_SIDE_BY_SIDE_SHADOW_RESEARCH"
DEFAULT_ALPHA_LAB_ROOT = Path("outputs/research/alpha_lab_v2")
DEFAULT_SHADOW_SUMMARY_PATH = Path("outputs/shadow_candidates/performance/shadow_summary.json")
DEFAULT_OUTPUT_ROOT = Path("outputs/research")
REQUIRED_OUTPUT_FILENAMES = (
    "alpha_lab_v2_h2_h6_shadow_observation_plan.json",
    "alpha_lab_v2_h2_h6_shadow_metrics_schema.json",
    "alpha_lab_v2_h2_h6_shadow_boundary_attestation.json",
)
FORBIDDEN_RUNTIME_TOUCHPOINTS = (
    "broker",
    "execution",
    "allocation",
    "scheduler",
    "cron",
    "paper",
    "pilot",
    "live",
)
READ_ONLY_SOURCE_FILES = (
    "summary.json",
    f"{STRATEGY}.json",
    f"{STRATEGY}_daily.csv",
    f"{STRATEGY}_nav.csv",
    "baseline_top10_daily.json",
    "comparison_table.csv",
    "randomized_windows_summary.json",
)


@dataclass(frozen=True)
class ShadowObservationArtifacts:
    observation_plan: dict[str, Any]
    metrics_schema: dict[str, Any]
    boundary_attestation: dict[str, Any]


def build_shadow_observation_artifacts(
    *,
    repo_root: str | Path = ".",
    alpha_lab_root: str | Path = DEFAULT_ALPHA_LAB_ROOT,
    shadow_summary_path: str | Path = DEFAULT_SHADOW_SUMMARY_PATH,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
    generated_at_utc: str = "2026-07-02T00:00:00Z",
) -> ShadowObservationArtifacts:
    repo = Path(repo_root)
    alpha_root = _resolve(repo, alpha_lab_root)
    shadow_summary_file = _resolve(repo, shadow_summary_path)
    output = _resolve(repo, output_root)

    alpha_sources = _source_inventory(alpha_root / name for name in READ_ONLY_SOURCE_FILES)
    shadow_sources = _shadow_source_inventory(repo=repo, shadow_summary_path=shadow_summary_file)
    source_status = _source_status(alpha_sources=alpha_sources, shadow_sources=shadow_sources)
    summary = _read_json_or_none(alpha_root / "summary.json") or {}
    strategy_metrics = _read_json_or_none(alpha_root / f"{STRATEGY}.json") or {}
    baseline_metrics = _read_json_or_none(alpha_root / "baseline_top10_daily.json") or {}
    shadow_summary = _read_json_or_none(shadow_summary_file) or {}

    metrics_schema = build_metrics_schema(generated_at_utc=generated_at_utc)
    boundary_attestation = build_boundary_attestation(
        output_root=output,
        source_status=source_status,
        generated_at_utc=generated_at_utc,
    )
    observation_plan = {
        "schema_version": SCHEMA_VERSION,
        "candidate_id": CANDIDATE_ID,
        "strategy": STRATEGY,
        "generated_at_utc": generated_at_utc,
        "cio_decision": {
            "decision": APPROVAL,
            "scope": "NON_EXECUTING_RESEARCH_OBSERVATION_ONLY",
            "capital_authority_granted": False,
            "production_change_authority_granted": False,
        },
        "objective": "Observe Alpha Lab v2 H2/H6 side-by-side against existing Caerus research and shadow baselines without changing production behavior.",
        "source_ingestion": {
            "mode": "read_only",
            "alpha_lab_v2_sources": alpha_sources,
            "caerus_shadow_sources": shadow_sources,
            "source_status": source_status,
            "blockers": source_status["blockers"],
        },
        "candidate_evidence": {
            "alpha_lab_summary": _alpha_lab_summary(summary),
            "candidate_metrics": _metrics_subset(strategy_metrics),
            "baseline_metrics": _metrics_subset(baseline_metrics),
            "randomized_window_summary": _randomized_window_summary(summary),
        },
        "baseline_comparisons": build_baseline_comparisons(
            candidate_metrics=strategy_metrics,
            alpha_baseline=baseline_metrics,
            alpha_summary=summary,
            shadow_summary=shadow_summary,
        ),
        "side_by_side_comparison_schema_ref": str(output / REQUIRED_OUTPUT_FILENAMES[1]),
        "observation_metrics": metrics_schema["metrics"],
        "observation_cadence": {
            "cadence": "daily_after_caerus_shadow_artifacts_are_available",
            "minimum_review_window": "20 trading days before first readout",
            "decision_readout_windows": ["20d", "60d", "120d"],
            "allowed_actions": ["observe", "defer", "park", "request_more_evidence"],
            "disallowed_actions": ["submit_order", "change_allocation", "change_scheduler", "promote_to_paper", "promote_to_pilot", "promote_to_live"],
        },
        "production_boundary_attestation_ref": str(output / REQUIRED_OUTPUT_FILENAMES[2]),
        "interpretation_limits": [
            "This plan does not create a production strategy, order, schedule, paper sleeve, pilot sleeve, or allocation.",
            "Current Alpha Lab v2 historical evidence ends before the latest shadow summary; comparisons are observation framing, not a promotion decision.",
            "Any paper, pilot, live, or capital action requires a separate Caerus/CIO approval path.",
        ],
        "status": "READY_FOR_NON_EXECUTING_OBSERVATION" if not source_status["blockers"] else "READY_WITH_OBSERVATION_BLOCKERS",
    }
    return ShadowObservationArtifacts(
        observation_plan=observation_plan,
        metrics_schema=metrics_schema,
        boundary_attestation=boundary_attestation,
    )


def write_shadow_observation_artifacts(
    artifacts: ShadowObservationArtifacts,
    *,
    repo_root: str | Path = ".",
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
) -> dict[str, str]:
    output = _resolve(Path(repo_root), output_root)
    paths = {
        "observation_plan": output / REQUIRED_OUTPUT_FILENAMES[0],
        "metrics_schema": output / REQUIRED_OUTPUT_FILENAMES[1],
        "boundary_attestation": output / REQUIRED_OUTPUT_FILENAMES[2],
    }
    _write_json(paths["observation_plan"], artifacts.observation_plan)
    _write_json(paths["metrics_schema"], artifacts.metrics_schema)
    _write_json(paths["boundary_attestation"], artifacts.boundary_attestation)
    return {name: str(path) for name, path in paths.items()}


def build_metrics_schema(*, generated_at_utc: str = "2026-07-02T00:00:00Z") -> dict[str, Any]:
    return {
        "schema_version": "alpha_lab_v2_h2_h6_shadow_metrics_schema_v1",
        "candidate_id": CANDIDATE_ID,
        "generated_at_utc": generated_at_utc,
        "row_grain": "candidate_strategy_by_observation_date",
        "required_fields": [
            "observation_date",
            "candidate_id",
            "source_artifact_digest",
            "candidate_daily_return",
            "candidate_cumulative_return",
            "candidate_drawdown",
            "benchmark_symbol",
            "benchmark_daily_return",
            "excess_return_vs_spy",
            "excess_return_vs_polaris",
            "excess_return_vs_orion",
            "excess_return_vs_lyra",
            "rolling_20d_excess_return_vs_polaris",
            "rolling_60d_excess_return_vs_polaris",
            "rolling_20d_hit_rate",
            "rolling_60d_hit_rate",
            "turnover",
            "holdings_count",
            "gross_exposure",
            "cash_weight",
            "top5_overlap_with_polaris",
            "top5_overlap_with_orion",
            "top5_overlap_with_lyra",
            "rank_drift",
            "reason_codes",
        ],
        "metrics": {
            "return": [
                "candidate_daily_return",
                "candidate_cumulative_return",
                "excess_return_vs_spy",
                "excess_return_vs_polaris",
                "excess_return_vs_orion",
                "excess_return_vs_lyra",
            ],
            "risk": [
                "candidate_drawdown",
                "rolling_20d_volatility",
                "rolling_60d_volatility",
                "rolling_60d_max_drawdown",
            ],
            "robustness": [
                "rolling_20d_hit_rate",
                "rolling_60d_hit_rate",
                "rolling_20d_excess_return_vs_polaris",
                "rolling_60d_excess_return_vs_polaris",
            ],
            "portfolio_fit": [
                "holdings_count",
                "gross_exposure",
                "cash_weight",
                "top5_overlap_with_polaris",
                "top5_overlap_with_orion",
                "top5_overlap_with_lyra",
                "rank_drift",
            ],
            "operability": [
                "turnover",
                "source_artifact_digest",
                "reason_codes",
            ],
        },
        "classification_rules": {
            "shadow_only": "All rows are advisory and non-executing.",
            "insufficient_data": "Less than 20 trading days of observations.",
            "review_candidate": "At least 60 trading days with positive excess return versus Polaris and no boundary exceptions.",
            "park_candidate": "Negative 60d excess return versus Polaris or unresolved source readiness blocker.",
        },
    }


def build_baseline_comparisons(
    *,
    candidate_metrics: dict[str, Any],
    alpha_baseline: dict[str, Any],
    alpha_summary: dict[str, Any],
    shadow_summary: dict[str, Any],
) -> dict[str, Any]:
    baselines: dict[str, dict[str, Any]] = {
        "alpha_lab_v2_baseline_top10_daily": alpha_baseline,
    }
    best_single = alpha_summary.get("best_single_change_metrics")
    if isinstance(best_single, dict):
        baselines["alpha_lab_v2_best_single_change"] = best_single
    for strategy_id, payload in (shadow_summary.get("strategies") or {}).items():
        summary = payload.get("summary") if isinstance(payload, dict) else None
        if isinstance(summary, dict):
            baselines[f"shadow_summary_{strategy_id}"] = summary

    return {
        name: {
            "baseline_metrics": _metrics_subset(metrics),
            "candidate_minus_baseline": _metric_deltas(candidate_metrics, metrics),
            "comparability": _comparability_note(name, metrics),
        }
        for name, metrics in baselines.items()
    }


def build_boundary_attestation(
    *,
    output_root: Path,
    source_status: dict[str, Any],
    generated_at_utc: str,
) -> dict[str, Any]:
    output_paths = [str(output_root / name) for name in REQUIRED_OUTPUT_FILENAMES]
    return {
        "schema_version": "alpha_lab_v2_h2_h6_shadow_boundary_attestation_v1",
        "candidate_id": CANDIDATE_ID,
        "generated_at_utc": generated_at_utc,
        "decision": APPROVAL,
        "scope": "RESEARCH_ONLY_NON_EXECUTING",
        "runtime_behavior_changed": False,
        "broker_orders_submitted": False,
        "broker_code_modified": False,
        "allocation_or_sizing_modified": False,
        "scheduler_or_cron_modified": False,
        "paper_pilot_live_promotion": False,
        "capital_path_touched": False,
        "production_boundary_status": "CLEAN",
        "forbidden_runtime_touchpoints": list(FORBIDDEN_RUNTIME_TOUCHPOINTS),
        "files_written": output_paths,
        "allowed_write_prefixes": [str(output_root)],
        "source_readiness": source_status,
        "attestation": "This artifact set is a Caerus research observation plan only. It reads existing Alpha Lab v2 and shadow research outputs and writes deterministic research artifacts under outputs/research.",
    }


def _source_inventory(paths: Any) -> list[dict[str, Any]]:
    return [_file_inventory(Path(path)) for path in paths]


def _shadow_source_inventory(*, repo: Path, shadow_summary_path: Path) -> list[dict[str, Any]]:
    sources = [_file_inventory(shadow_summary_path)]
    shadow_summary = _read_json_or_none(shadow_summary_path) or {}
    trade_date = shadow_summary.get("trade_date")
    if trade_date:
        dated_root = repo / "outputs/shadow_candidates" / str(trade_date)
        for name in (
            "caerus_polaris.json",
            "caerus_orion.json",
            "caerus_lyra.json",
            "caerus_polaris_alpha.json",
            "caerus_orion_alpha.json",
            "comparison.json",
        ):
            sources.append(_file_inventory(dated_root / name))
    return sources


def _source_status(
    *,
    alpha_sources: list[dict[str, Any]],
    shadow_sources: list[dict[str, Any]],
) -> dict[str, Any]:
    blockers = []
    missing_alpha = [item["path"] for item in alpha_sources if item["status"] == "MISSING"]
    missing_shadow = [item["path"] for item in shadow_sources if item["status"] == "MISSING"]
    if missing_alpha:
        blockers.append({"classification": "MISSING_ALPHA_LAB_V2_SOURCE", "paths": missing_alpha})
    if missing_shadow:
        blockers.append({"classification": "MISSING_SHADOW_BASELINE_SOURCE", "paths": missing_shadow})
    return {
        "status": "READY" if not blockers else "PARTIAL",
        "alpha_sources_ready": not missing_alpha,
        "shadow_sources_ready": not missing_shadow,
        "blockers": blockers,
    }


def _alpha_lab_summary(summary: dict[str, Any]) -> dict[str, Any]:
    data = summary.get("data") or {}
    study = summary.get("study_answers") or {}
    best = study.get("best_variant_details") or {}
    return {
        "schema_version": summary.get("schema_version"),
        "coverage": data.get("coverage"),
        "download_performed": data.get("download_performed"),
        "recommended_next_action": study.get("recommended_next_action"),
        "best_strategy": best.get("strategy"),
        "best_verdict": best.get("verdict"),
    }


def _randomized_window_summary(summary: dict[str, Any]) -> dict[str, Any]:
    best = ((summary.get("study_answers") or {}).get("best_variant_details") or {})
    return {
        "avg_pct_windows_beating_baseline": best.get("avg_pct_windows_beating_baseline"),
        "avg_pct_windows_beating_best_single_change": best.get("avg_pct_windows_beating_best_single_change"),
        "windows": best.get("randomized_windows", []),
    }


def _file_inventory(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "status": "MISSING", "sha256": None, "modified_at": None}
    stat = path.stat()
    return {
        "path": str(path),
        "status": "READY",
        "sha256": _sha256(path),
        "modified_at": round(stat.st_mtime, 6),
        "size_bytes": stat.st_size,
    }


def _metric_deltas(candidate: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    fields = ("cagr", "sharpe", "sortino", "max_drawdown", "annualised_vol", "avg_turnover", "avg_holding_period_days")
    deltas: dict[str, Any] = {}
    for field in fields:
        left = _number(candidate.get(field))
        right = _number(baseline.get(field))
        deltas[field] = round(left - right, 6) if left is not None and right is not None else None
    return deltas


def _metrics_subset(metrics: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "strategy",
        "label",
        "description",
        "n_years",
        "cagr",
        "sharpe",
        "sortino",
        "max_drawdown",
        "calmar",
        "hit_rate",
        "annualised_vol",
        "avg_turnover",
        "avg_holding_period_days",
        "top_n",
        "rebalance_mode",
        "use_rank_decay_exit",
        "benchmark_cumulative_return",
        "excess_return_vs_spy",
    )
    return {field: metrics.get(field) for field in fields if field in metrics}


def _comparability_note(name: str, metrics: dict[str, Any]) -> dict[str, str]:
    if name.startswith("shadow_summary_"):
        return {
            "status": "OBSERVATION_FRAME_ONLY",
            "reason": "Caerus shadow summary may use a later evidence window than the Alpha Lab v2 source artifact.",
        }
    if metrics.get("strategy") == STRATEGY:
        return {
            "status": "DIRECT_STRATEGY_MATCH",
            "reason": "Baseline uses the same H2/H6 strategy label; compare implementation lineage before interpreting deltas.",
        }
    return {
        "status": "COMPARABLE_RESEARCH_BASELINE",
        "reason": "Same Alpha Lab v2 study family or declared Caerus shadow baseline.",
    }


def _number(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json_or_none(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _resolve(repo_root: Path, path: str | Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else repo_root / candidate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build Alpha Lab v2 H2/H6 non-executing shadow observation artifacts.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--alpha-lab-root", default=str(DEFAULT_ALPHA_LAB_ROOT))
    parser.add_argument("--shadow-summary-path", default=str(DEFAULT_SHADOW_SUMMARY_PATH))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--generated-at-utc", default="2026-07-02T00:00:00Z")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    artifacts = build_shadow_observation_artifacts(
        repo_root=args.repo_root,
        alpha_lab_root=args.alpha_lab_root,
        shadow_summary_path=args.shadow_summary_path,
        output_root=args.output_root,
        generated_at_utc=args.generated_at_utc,
    )
    paths = write_shadow_observation_artifacts(
        artifacts,
        repo_root=args.repo_root,
        output_root=args.output_root,
    )
    print(json.dumps({"status": artifacts.observation_plan["status"], "paths": paths}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

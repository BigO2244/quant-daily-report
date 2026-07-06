from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from research.alpha_lab_v2.shadow_observation import (
    APPROVAL,
    CANDIDATE_ID,
    REQUIRED_OUTPUT_FILENAMES,
    build_shadow_observation_artifacts,
    write_shadow_observation_artifacts,
)


def test_shadow_observation_plan_reads_alpha_and_shadow_sources(tmp_path: Path) -> None:
    repo = _fixture_repo(tmp_path)

    artifacts = build_shadow_observation_artifacts(repo_root=repo)

    plan = artifacts.observation_plan
    assert plan["candidate_id"] == CANDIDATE_ID
    assert plan["cio_decision"]["decision"] == APPROVAL
    assert plan["cio_decision"]["capital_authority_granted"] is False
    assert plan["source_ingestion"]["source_status"]["status"] == "READY"
    assert plan["status"] == "READY_FOR_NON_EXECUTING_OBSERVATION"
    assert "alpha_lab_v2_baseline_top10_daily" in plan["baseline_comparisons"]
    assert "shadow_summary_caerus_polaris" in plan["baseline_comparisons"]
    assert plan["baseline_comparisons"]["alpha_lab_v2_baseline_top10_daily"]["candidate_minus_baseline"]["sharpe"] == 0.208
    assert "daily_after_caerus_shadow_artifacts_are_available" == plan["observation_cadence"]["cadence"]


def test_shadow_metrics_schema_and_boundary_attestation_are_non_executional(tmp_path: Path) -> None:
    repo = _fixture_repo(tmp_path)

    artifacts = build_shadow_observation_artifacts(repo_root=repo)

    schema = artifacts.metrics_schema
    boundary = artifacts.boundary_attestation
    assert "candidate_daily_return" in schema["required_fields"]
    assert "excess_return_vs_polaris" in schema["required_fields"]
    assert boundary["runtime_behavior_changed"] is False
    assert boundary["broker_orders_submitted"] is False
    assert boundary["scheduler_or_cron_modified"] is False
    assert boundary["paper_pilot_live_promotion"] is False
    assert boundary["production_boundary_status"] == "CLEAN"


def test_shadow_observation_writer_creates_required_artifacts(tmp_path: Path) -> None:
    repo = _fixture_repo(tmp_path)
    artifacts = build_shadow_observation_artifacts(repo_root=repo)

    paths = write_shadow_observation_artifacts(artifacts, repo_root=repo)

    assert sorted(Path(path).name for path in paths.values()) == sorted(REQUIRED_OUTPUT_FILENAMES)
    for path in paths.values():
        assert Path(path).exists(), path
    plan = json.loads((repo / "outputs/research/alpha_lab_v2_h2_h6_shadow_observation_plan.json").read_text())
    assert plan["interpretation_limits"]


def test_shadow_observation_classifies_missing_shadow_baseline_as_blocker(tmp_path: Path) -> None:
    repo = _fixture_repo(tmp_path, include_shadow=False)

    artifacts = build_shadow_observation_artifacts(repo_root=repo)

    status = artifacts.observation_plan["source_ingestion"]["source_status"]
    assert status["status"] == "PARTIAL"
    assert status["shadow_sources_ready"] is False
    assert status["blockers"][0]["classification"] == "MISSING_SHADOW_BASELINE_SOURCE"
    assert artifacts.observation_plan["status"] == "READY_WITH_OBSERVATION_BLOCKERS"


def test_shadow_observation_cli_smoke(tmp_path: Path) -> None:
    repo = _fixture_repo(tmp_path)
    project_root = Path(__file__).resolve().parents[1]

    result = subprocess.run(
        [
            sys.executable,
            "scripts/research/build_alpha_lab_v2_h2_h6_shadow_observation_plan.py",
            "--repo-root",
            str(repo),
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload["status"] == "READY_FOR_NON_EXECUTING_OBSERVATION"
    assert (repo / "outputs/research/alpha_lab_v2_h2_h6_shadow_boundary_attestation.json").exists()


def _fixture_repo(tmp_path: Path, *, include_shadow: bool = True) -> Path:
    repo = tmp_path / "repo"
    alpha = repo / "outputs/research/alpha_lab_v2"
    alpha.mkdir(parents=True)
    _write_json(
        alpha / "summary.json",
        {
            "schema_version": "alpha_lab_v2",
            "data": {
                "download_performed": False,
                "coverage": {"start_date": "2014-01-02", "end_date": "2026-04-21", "symbols": 200},
            },
            "best_single_change_metrics": _metrics("h6_top5_daily", cagr=0.4616, sharpe=1.273, max_drawdown=-0.4118),
            "study_answers": {
                "recommended_next_action": "promote_to_side_by_side_shadow_candidate",
                "best_variant_details": {
                    "strategy": "h2_rank_decay_exit_h6_top5",
                    "verdict": "PASS",
                    "avg_pct_windows_beating_baseline": 0.815,
                    "avg_pct_windows_beating_best_single_change": 0.58,
                    "randomized_windows": [{"horizon_years": 2, "pct_windows_beating_baseline": 0.76}],
                },
            },
        },
    )
    _write_json(alpha / "h2_rank_decay_exit_h6_top5.json", _metrics("h2_rank_decay_exit_h6_top5", cagr=0.4751, sharpe=1.314, max_drawdown=-0.4239))
    _write_json(alpha / "baseline_top10_daily.json", _metrics("baseline_top10_daily", cagr=0.3158, sharpe=1.106, max_drawdown=-0.4307))
    for name in [
        "h2_rank_decay_exit_h6_top5_daily.csv",
        "h2_rank_decay_exit_h6_top5_nav.csv",
        "comparison_table.csv",
        "randomized_windows_summary.json",
    ]:
        path = alpha / name
        if path.suffix == ".json":
            _write_json(path, {"ok": True})
        else:
            path.write_text("date,value\n2026-04-21,1.0\n", encoding="utf-8")

    if include_shadow:
        shadow_perf = repo / "outputs/shadow_candidates/performance"
        _write_json(
            shadow_perf / "shadow_summary.json",
            {
                "schema_version": "shadow_candidates_v1",
                "trade_date": "2026-06-24",
                "strategies": {
                    "caerus_polaris": {"strategy_name": "Caerus Polaris", "summary": _metrics("baseline_top10_daily", cagr=0.3543, sharpe=1.187, max_drawdown=-0.4307)},
                    "caerus_orion": {"strategy_name": "Caerus Orion", "summary": _metrics("h2_rank_decay_exit_h6_top5", cagr=0.529, sharpe=1.398, max_drawdown=-0.4239)},
                    "caerus_lyra": {"strategy_name": "Caerus Lyra", "summary": _metrics("h1_weekly_h6_top5", cagr=0.5282, sharpe=1.379, max_drawdown=-0.4176)},
                },
            },
        )
        dated = repo / "outputs/shadow_candidates/2026-06-24"
        for name in [
            "caerus_polaris.json",
            "caerus_orion.json",
            "caerus_lyra.json",
            "caerus_polaris_alpha.json",
            "caerus_orion_alpha.json",
            "comparison.json",
        ]:
            _write_json(dated / name, {"name": name})
    return repo


def _metrics(strategy: str, *, cagr: float, sharpe: float, max_drawdown: float) -> dict:
    return {
        "strategy": strategy,
        "label": strategy,
        "description": strategy,
        "n_years": 12.27,
        "cagr": cagr,
        "sharpe": sharpe,
        "sortino": sharpe + 0.3,
        "max_drawdown": max_drawdown,
        "calmar": 1.0,
        "hit_rate": 0.51,
        "annualised_vol": 0.34,
        "avg_turnover": 0.027749 if strategy == "h2_rank_decay_exit_h6_top5" else 0.141979,
        "avg_holding_period_days": 95.07 if strategy == "h2_rank_decay_exit_h6_top5" else 18.75,
        "top_n": 5 if "h6" in strategy else 10,
        "rebalance_mode": "daily",
        "use_rank_decay_exit": strategy == "h2_rank_decay_exit_h6_top5",
        "benchmark_cumulative_return": 3.738718,
        "excess_return_vs_spy": 113.151766,
    }


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")

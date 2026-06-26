from __future__ import annotations

import json
import sys
from pathlib import Path

from research.fr105_phase2_topn_frontier import (
    validate_fr105_phase2_topn_frontier,
    write_fr105_phase2_topn_frontier,
)
from research.fr105_replay_contract import PROHIBITED_PRODUCTION_MODULES


TRADE_DATE = "2026-06-25"
CONTRACT_ID = "2026-06-25T093000-0400_fr105"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _candidate(
    ticker: str,
    *,
    rank: int | None,
    conviction_score: float | None,
    score: float | None = None,
    current_weight: float | None = None,
    target_weight: float | None = None,
) -> dict:
    return {
        "ticker": ticker,
        "sleeve_id": "caerus_polaris",
        "strategy_id": "polaris_v1",
        "source_model": "momentum",
        "lifecycle": {"side": "BUY", "submitted": True, "accepted": True, "filled": False, "clipped": False},
        "rank": rank,
        "score": score,
        "conviction_score": conviction_score,
        "expected_alpha": None,
        "expected_risk": None,
        "target_weight": target_weight,
        "target_notional": None,
        "current_weight": current_weight,
        "current_notional": None,
        "delta_notional": None,
        "reason_included": "model_selected",
        "reason_excluded": None,
        "data_asof": TRADE_DATE,
        "source_artifact_path": f"outputs/runs/{CONTRACT_ID}/audit/candidate_trade_lifecycle_{TRADE_DATE}.json",
    }


def _phase0_contract(*, sparse: bool = False) -> dict:
    source_artifacts = {
        "candidate_trade_lifecycle_path": None if sparse else f"outputs/runs/{CONTRACT_ID}/audit/candidate_trade_lifecycle_{TRADE_DATE}.json",
        "target_portfolio_path": None,
        "sleeve_artifacts": [],
        "execution_results_path": None if sparse else f"outputs/runs/{CONTRACT_ID}/execution_results.json",
        "reconciliation_path": None,
        "broker_positions_path": None if sparse else f"outputs/runs/{CONTRACT_ID}/broker/posttrade_positions.json",
        "price_source": "unavailable",
    }
    candidates = [] if sparse else [
        _candidate("AAA", rank=4, conviction_score=0.80, current_weight=0.50, target_weight=0.40),
        _candidate("AAA", rank=2, conviction_score=0.95, current_weight=0.50, target_weight=0.45),
        _candidate("BBB", rank=1, conviction_score=0.90, current_weight=0.0, target_weight=0.30),
        _candidate("CCC", rank=3, conviction_score=None, score=0.70, current_weight=0.0, target_weight=0.25),
        _candidate("DDD", rank=None, conviction_score=None, current_weight=0.25, target_weight=0.0),
    ]
    return {
        "metadata": {
            "trade_date": TRADE_DATE,
            "generated_at": "2026-06-25T16:00:00Z",
            "git_sha": "testsha",
            "mode": "research_only",
            "fr_id": "FR-105",
            "schema_version": "fr105_global_optimizer_replay_contract.v1",
            "contract_id": CONTRACT_ID,
            "production_execution_modules_invoked": [],
        },
        "source_artifacts": source_artifacts,
        "universe_snapshot": {
            "status": "unavailable",
            "universe_id": None,
            "asof": TRADE_DATE,
            "ticker_count": None,
            "source_artifact_path": None,
        },
        "sleeve_candidates": candidates,
        "current_portfolio": {
            "source_artifact_path": source_artifacts["broker_positions_path"],
            "positions_count": None if sparse else 2,
            "positions": [] if sparse else [
                {"ticker": "AAA", "quantity": 5.0, "market_value": 500.0, "current_weight": 0.50},
                {"ticker": "DDD", "quantity": 10.0, "market_value": 250.0, "current_weight": 0.25},
            ],
        },
        "constraints_snapshot": {
            "max_single_name_weight": 0.20,
            "sector_caps": None,
            "effective_n_floor": None,
            "turnover_cap": 0.95,
            "liquidity_constraints": "unavailable",
            "min_trade_dollars": 100.0,
            "cash_target": None,
            "gross_exposure_target": None,
            "buying_power_available": None,
            "rebudget_policy": "unavailable",
            "min_notional_policy": {"min_trade_dollars": 100.0},
        },
        "execution_residuals": {
            "planned_candidates": None,
            "executable_candidates": None,
            "intended_orders": None,
            "submitted_orders": None,
            "filled_orders": None,
            "suppressed_count": None,
            "clipped_count": None,
            "suppression_reason_counts": {},
            "clipping_reason_counts": {},
            "estimated_unexecuted_notional_total": None,
        },
        "provenance_schema_version": "fr105_candidate_provenance.v1",
        "validation_status": {"status": "PASS", "findings": [], "warnings": []},
    }


def _phase1_baseline(*, sparse: bool = False) -> dict:
    return {
        "metadata": {
            "trade_date": TRADE_DATE,
            "generated_at": "2026-06-25T17:00:00Z",
            "git_sha": "testsha",
            "mode": "research_only",
            "fr_id": "FR-105",
            "phase": "Phase 1",
            "schema_version": "fr105_phase1_current_policy_baseline.v1",
            "contract_id": CONTRACT_ID,
            "production_execution_modules_invoked": [],
        },
        "pit_controls": {
            "trade_date": TRADE_DATE,
            "data_asof": None if sparse else TRADE_DATE,
            "universe_asof": None,
            "price_asof": None,
            "source_artifact_paths": {},
            "no_forward_returns_used": True,
            "no_production_modules_invoked": True,
            "unavailable_fields": [],
        },
        "baseline_positions": {
            "source_artifact_path": None if sparse else f"outputs/runs/{CONTRACT_ID}/broker/posttrade_positions.json",
            "positions_count": None if sparse else 2,
            "positions": [] if sparse else [
                {"ticker": "AAA", "quantity": 5.0, "market_value": 500.0, "current_weight": 0.50},
                {"ticker": "DDD", "quantity": 10.0, "market_value": 250.0, "current_weight": 0.25},
            ],
        },
        "baseline_metrics": {
            "position_count": None if sparse else 2,
            "gross_exposure": None if sparse else 0.75,
            "cash_weight": None if sparse else 0.25,
            "max_single_name_weight": None if sparse else 0.50,
            "HHI": None if sparse else 0.3125,
            "effective_N": None if sparse else 3.2,
            "turnover": None,
            "planned_candidates": None,
            "submitted_orders": None,
            "filled_orders": None,
            "suppressed_count": None,
            "clipped_count": None,
            "estimated_unexecuted_notional_total": None,
        },
        "validation_status": {"status": "PASS", "findings": [], "warnings": []},
    }


def _phase01_completeness(*, sparse: bool = False) -> dict:
    gaps = [
        "candidate_pool",
        "candidate_universe",
        "current_holdings",
        "current_weights",
        "execution_residuals",
        "pit_lineage",
        "score_source",
        "sleeve_source",
        "suppression_reasons",
        "target_portfolio",
        "target_weights",
    ] if sparse else []
    return {
        "schema_version": "fr105_phase01_artifact_completeness.v1",
        "metadata": {
            "trade_date": TRADE_DATE,
            "contract_id": CONTRACT_ID,
            "mode": "research_only",
            "fr_id": "FR-105",
        },
        "summary": {
            "status": "INCOMPLETE" if sparse else "COMPLETE",
            "complete": not sparse,
            "missing_fields": [],
            "unavailable_fields": gaps,
        },
        "phase_status": {"phase0": "COMPLETE", "phase1": "COMPLETE"},
        "readiness": {
            "status": "BLOCKED_ARTIFACT_GAPS" if sparse else "READY",
            "blocking_gaps": gaps,
            "alpha_chase_evaluation_ready": not sparse,
            "shadow_comparison_ready": not sparse,
        },
    }


def _write_inputs(root: Path, *, sparse: bool = False) -> tuple[Path, Path]:
    out = root / "outputs" / "research" / "fr_105" / CONTRACT_ID
    contract = out / "global_optimizer_replay_contract.json"
    baseline = out / "phase1_current_policy_baseline.json"
    completeness = out / "phase01_artifact_completeness.json"
    _write_json(contract, _phase0_contract(sparse=sparse))
    _write_json(baseline, _phase1_baseline(sparse=sparse))
    _write_json(completeness, _phase01_completeness(sparse=sparse))
    return contract, baseline


def test_phase2_sparse_inputs_block_shadow_evaluation(tmp_path: Path) -> None:
    contract_path, baseline_path = _write_inputs(tmp_path, sparse=True)

    out_path, frontier = write_fr105_phase2_topn_frontier(
        repo_root=tmp_path,
        trade_date=TRADE_DATE,
        input_contract_path=contract_path,
        input_baseline_path=baseline_path,
        top_n_values=(5, 10),
        generated_at="2026-06-25T18:00:00Z",
    )

    assert out_path == tmp_path / "outputs" / "research" / "fr_105" / CONTRACT_ID / "phase2_global_topn_frontier.json"
    assert frontier["validation_status"]["status"] == "PASS"
    assert frontier["readiness"]["status"] == "BLOCKED_ARTIFACT_GAPS"
    assert frontier["readiness"]["shadow_evaluation_ready"] is False
    assert frontier["metadata"]["paper_or_live_influence_allowed"] is False
    assert frontier["metadata"]["alpha_chase_recommendations_allowed"] is False
    assert frontier["data_quality"]["status"] == "BLOCKED_ARTIFACT_GAPS"
    assert frontier["candidate_pool"]["candidate_count"] == 0
    assert [row["unavailable_reason"] for row in frontier["frontier_variants"]] == [
        "phase01_completeness_blocked",
        "phase01_completeness_blocked",
    ]
    assert "phase01.candidate_pool" in frontier["data_quality"]["unavailable_fields"]
    assert frontier["pit_controls"]["no_forward_returns_used"] is True
    assert frontier["pit_controls"]["no_production_modules_invoked"] is True


def test_phase2_populated_candidate_pool_generates_topn_variants(tmp_path: Path) -> None:
    contract_path, baseline_path = _write_inputs(tmp_path, sparse=False)

    _, frontier = write_fr105_phase2_topn_frontier(
        repo_root=tmp_path,
        trade_date=TRADE_DATE,
        input_contract_path=contract_path,
        input_baseline_path=baseline_path,
        top_n_values=(2, 3),
        generated_at="2026-06-25T18:00:00Z",
    )

    assert frontier["validation_status"]["status"] == "PASS"
    assert frontier["readiness"]["status"] == "READY"
    assert frontier["metadata"]["optimizer_behavior_changed"] is False
    assert frontier["score_source_status"]["prohibited_weight_derived_score_used"] is False
    assert frontier["candidate_pool"]["candidate_count"] == 5
    assert frontier["candidate_pool"]["eligible_candidate_count"] == 4
    assert frontier["candidate_pool"]["unique_eligible_ticker_count"] == 3
    top2 = frontier["frontier_variants"][0]
    assert top2["variant_id"] == "global_top_2"
    assert top2["selected_tickers"] == ["AAA", "BBB"]
    assert len(top2["selected_tickers"]) == len(set(top2["selected_tickers"]))
    assert top2["selected_count"] == 2
    assert top2["aggregate_conviction_score"] == 1.85
    assert top2["average_rank"] == 1.5
    assert top2["estimated_equal_weight"] == 0.5
    assert top2["HHI"] == 0.5
    assert top2["effective_N"] == 2.0
    assert top2["overlap_with_current_policy"] == ["AAA"]
    assert top2["names_added_vs_current_policy"] == ["BBB"]
    assert top2["names_removed_vs_current_policy"] == ["DDD"]
    assert top2["estimated_turnover_from_current_policy"] == 0.375
    top3 = frontier["frontier_variants"][1]
    assert top3["selected_tickers"] == ["AAA", "BBB", "CCC"]
    assert top3["selected_count"] == 3
    assert top3["unavailable_reason"] is None


def test_phase2_artifact_is_deterministic_by_default(tmp_path: Path) -> None:
    contract_path, baseline_path = _write_inputs(tmp_path, sparse=False)

    out_path, first_payload = write_fr105_phase2_topn_frontier(
        repo_root=tmp_path,
        trade_date=TRADE_DATE,
        input_contract_path=contract_path,
        input_baseline_path=baseline_path,
        top_n_values=(2, 3),
    )
    first_text = out_path.read_text(encoding="utf-8")
    _, second_payload = write_fr105_phase2_topn_frontier(
        repo_root=tmp_path,
        trade_date=TRADE_DATE,
        input_contract_path=contract_path,
        input_baseline_path=baseline_path,
        top_n_values=(2, 3),
    )

    assert first_payload["metadata"]["generated_at"] == "unavailable"
    assert second_payload == first_payload
    assert out_path.read_text(encoding="utf-8") == first_text


def test_phase2_comparison_metrics_versus_current_policy_fixture(tmp_path: Path) -> None:
    contract_path, baseline_path = _write_inputs(tmp_path, sparse=False)

    _, frontier = write_fr105_phase2_topn_frontier(
        repo_root=tmp_path,
        trade_date=TRADE_DATE,
        input_contract_path=contract_path,
        input_baseline_path=baseline_path,
        top_n_values=(2, 3),
        generated_at="2026-06-25T18:00:00Z",
    )

    comparison = frontier["comparison_to_current_policy"]
    assert comparison["current_policy_position_count"] == 2
    assert comparison["current_policy_HHI"] == 0.3125
    assert comparison["current_policy_effective_N"] == 3.2
    assert comparison["current_policy_max_single_name_weight"] == 0.5
    assert comparison["best_available_variant_by_conviction"] == "global_top_2"
    assert comparison["variants_more_concentrated_than_current"] == ["global_top_2", "global_top_3"]
    assert comparison["variants_less_concentrated_than_current"] == []
    assert comparison["cannot_compare_reasons"] == []


def test_phase2_does_not_use_target_weight_as_score_signal(tmp_path: Path) -> None:
    contract_path, baseline_path = _write_inputs(tmp_path, sparse=False)
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["sleeve_candidates"].append(
        _candidate("EEE", rank=None, conviction_score=None, score=None, current_weight=0.0, target_weight=0.95)
    )
    _write_json(contract_path, contract)

    _, frontier = write_fr105_phase2_topn_frontier(
        repo_root=tmp_path,
        trade_date=TRADE_DATE,
        input_contract_path=contract_path,
        input_baseline_path=baseline_path,
        top_n_values=(5,),
        generated_at="2026-06-25T18:00:00Z",
    )

    eee = next(row for row in frontier["candidate_pool"]["candidates"] if row["ticker"] == "EEE")
    assert eee["inclusion_status"] == "excluded"
    assert eee["selection_signal"] is None
    assert eee["selection_signal_source"] is None
    assert "EEE" not in frontier["frontier_variants"][0]["selected_tickers"]


def test_phase2_validator_rejects_malformed_frontier_artifact(tmp_path: Path) -> None:
    contract_path, baseline_path = _write_inputs(tmp_path, sparse=False)
    _, frontier = write_fr105_phase2_topn_frontier(
        repo_root=tmp_path,
        trade_date=TRADE_DATE,
        input_contract_path=contract_path,
        input_baseline_path=baseline_path,
        top_n_values=(2,),
        generated_at="2026-06-25T18:00:00Z",
    )
    bad = json.loads(json.dumps(frontier))
    bad["metadata"]["mode"] = "paper"
    bad["metadata"]["production_execution_modules_invoked"] = ["paper.paper_broker"]
    bad["pit_controls"]["no_forward_returns_used"] = False
    bad["pit_controls"]["no_production_modules_invoked"] = False
    bad["frontier_variants"][0]["selected_tickers"] = ["AAA", "AAA"]
    bad["frontier_variants"][0]["selected_count"] = 3
    bad["frontier_variants"][0]["top_n"] = 2
    del bad["candidate_pool"]

    result = validate_fr105_phase2_topn_frontier(bad)

    assert result.status == "FAIL"
    assert "MODE_NOT_RESEARCH_ONLY" in result.findings
    assert "PRODUCTION_EXECUTION_MODULES_INVOKED" in result.findings
    assert "PROHIBITED_PRODUCTION_MODULES:paper.paper_broker" in result.findings
    assert "FORWARD_RETURNS_USED_WITHOUT_PIT_SAFE_RETURN_DATA" in result.findings
    assert "PRODUCTION_MODULE_INVOCATION_FLAG_NOT_TRUE" in result.findings
    assert "DUPLICATE_SELECTED_TICKERS:0" in result.findings
    assert "SELECTED_COUNT_EXCEEDS_TOP_N:0" in result.findings
    assert "MISSING_TOP_LEVEL_SECTIONS:candidate_pool" in result.findings


def test_phase2_builder_does_not_import_production_trading_modules(tmp_path: Path) -> None:
    contract_path, baseline_path = _write_inputs(tmp_path, sparse=False)
    before = set(sys.modules)

    write_fr105_phase2_topn_frontier(
        repo_root=tmp_path,
        trade_date=TRADE_DATE,
        input_contract_path=contract_path,
        input_baseline_path=baseline_path,
        top_n_values=(2, 3),
        generated_at="2026-06-25T18:00:00Z",
    )

    newly_imported = set(sys.modules) - before
    prohibited = {
        name
        for name in newly_imported
        for module in PROHIBITED_PRODUCTION_MODULES
        if name == module or name.startswith(module + ".")
    }
    assert prohibited == set()

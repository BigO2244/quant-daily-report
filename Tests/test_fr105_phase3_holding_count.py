from __future__ import annotations

import json
import sys
from pathlib import Path

from research.fr105_phase3_holding_count import (
    validate_fr105_phase3_holding_count,
    write_fr105_phase3_holding_count,
)
from research.fr105_replay_contract import PROHIBITED_PRODUCTION_MODULES


TRADE_DATE = "2026-06-25"
CONTRACT_ID = "2026-06-25T093000-0400_fr105"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _phase0_contract() -> dict:
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
        "source_artifacts": {
            "candidate_trade_lifecycle_path": f"outputs/runs/{CONTRACT_ID}/audit/candidate_trade_lifecycle_{TRADE_DATE}.json",
            "target_portfolio_path": None,
            "sleeve_artifacts": [],
            "execution_results_path": f"outputs/runs/{CONTRACT_ID}/execution_results.json",
            "reconciliation_path": None,
            "broker_positions_path": f"outputs/runs/{CONTRACT_ID}/broker/posttrade_positions.json",
            "price_source": "unavailable",
        },
        "universe_snapshot": {
            "status": "unavailable",
            "universe_id": None,
            "asof": TRADE_DATE,
            "ticker_count": None,
            "source_artifact_path": None,
        },
        "sleeve_candidates": [],
        "current_portfolio": {
            "source_artifact_path": f"outputs/runs/{CONTRACT_ID}/broker/posttrade_positions.json",
            "positions_count": 8,
            "positions": [],
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
        "input_contract": {},
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
        "current_policy_snapshot": {"policy_id": "current_policy_baseline"},
        "replay_window": {},
        "baseline_positions": {"source_artifact_path": None, "positions_count": None if sparse else 8, "positions": []},
        "baseline_trades": {"source_artifact_path": None, "candidates_count": None, "trades": []},
        "baseline_metrics": {
            "position_count": None if sparse else 8,
            "gross_exposure": None if sparse else 0.90,
            "cash_weight": None if sparse else 0.10,
            "max_single_name_weight": None if sparse else 0.15,
            "HHI": None if sparse else 0.14,
            "effective_N": None if sparse else 7.1428571429,
            "turnover": None,
            "planned_candidates": None,
            "submitted_orders": None,
            "filled_orders": None,
            "suppressed_count": None,
            "clipped_count": None,
            "estimated_unexecuted_notional_total": None,
        },
        "data_quality": {"sparse_artifact_handling": "PASS", "unavailable_fields": []},
        "validation_status": {"status": "PASS", "findings": [], "warnings": []},
    }


def _phase01_completeness(*, sparse: bool = False, waived_score_source: bool = False) -> dict:
    gaps = ["candidate_pool", "score_source", "target_weights"] if sparse else []
    if waived_score_source:
        gaps = ["score_source"]
    complete = not sparse and not waived_score_source
    readiness_status = (
        "BLOCKED_ARTIFACT_GAPS"
        if sparse
        else "READY_WITH_SCORE_SOURCE_UNAVAILABLE"
        if waived_score_source
        else "READY"
    )
    return {
        "schema_version": "fr105_phase01_artifact_completeness.v1",
        "metadata": {
            "trade_date": TRADE_DATE,
            "contract_id": CONTRACT_ID,
            "mode": "research_only",
            "fr_id": "FR-105",
        },
        "summary": {
            "status": "COMPLETE" if complete else "INCOMPLETE",
            "complete": complete,
            "missing_fields": [],
            "unavailable_fields": gaps,
        },
        "phase_status": {"phase0": "COMPLETE", "phase1": "COMPLETE"},
        "readiness": {
            "status": readiness_status,
            "blocking_gaps": [] if waived_score_source else gaps,
            "waived_gaps": ["score_source"] if waived_score_source else [],
            "historical_replay_ready": complete or waived_score_source,
            "score_driven_ranking_replayable": complete,
            "alpha_chase_evaluation_ready": complete,
            "shadow_comparison_ready": complete,
            "score_source_unavailable_waiver": {
                "applied": waived_score_source,
                "waived_fields": ["score_source"] if waived_score_source else [],
                "reason": (
                    "historical_non_weight_score_source_not_retained"
                    if waived_score_source
                    else "not_applicable"
                ),
            },
        },
    }


def _variant(
    variant_id: str,
    top_n: int,
    selected_tickers: list[str],
    *,
    aggregate_conviction_score: float | None,
    average_rank: float | None,
    hhi: float | None,
    effective_n: float | None,
    max_single_name_weight: float | None,
    turnover: float | None,
    unavailable_reason: str | None = None,
) -> dict:
    return {
        "variant_id": variant_id,
        "top_n": top_n,
        "selected_tickers": selected_tickers,
        "selected_count": len(selected_tickers),
        "unavailable_reason": unavailable_reason,
        "aggregate_conviction_score": aggregate_conviction_score,
        "average_rank": average_rank,
        "max_single_name_weight": max_single_name_weight,
        "estimated_equal_weight": max_single_name_weight,
        "HHI": hhi,
        "effective_N": effective_n,
        "overlap_with_current_policy": [],
        "names_added_vs_current_policy": selected_tickers,
        "names_removed_vs_current_policy": [],
        "estimated_turnover_from_current_policy": turnover,
        "data_completeness": {
            "candidate_pool_count": 6,
            "eligible_unique_candidate_count": 6,
            "selected_with_conviction_score": len(selected_tickers) if aggregate_conviction_score is not None else 0,
            "selected_with_rank": len(selected_tickers) if average_rank is not None else 0,
            "current_policy_positions_available": True,
            "status": "PARTIAL" if unavailable_reason is None else "SPARSE",
        },
    }


def _phase2_frontier(*, sparse: bool = False, tie_fixture: bool = False) -> dict:
    if sparse:
        variants = [
            _variant(
                "global_top_5",
                5,
                [],
                aggregate_conviction_score=None,
                average_rank=None,
                hhi=None,
                effective_n=None,
                max_single_name_weight=None,
                turnover=None,
                unavailable_reason="candidate_pool_unavailable",
            )
        ]
        candidate_pool = {"candidate_count": 0, "eligible_candidate_count": 0, "unique_eligible_ticker_count": 0, "candidates": []}
    elif tie_fixture:
        variants = [
            _variant("global_top_6", 6, ["A", "B", "C", "D", "E", "F"], aggregate_conviction_score=4.0, average_rank=2.0, hhi=0.20, effective_n=5.0, max_single_name_weight=0.20, turnover=0.10),
            _variant("global_top_5", 5, ["A", "B", "C", "D", "E"], aggregate_conviction_score=4.0, average_rank=2.0, hhi=0.20, effective_n=5.0, max_single_name_weight=0.20, turnover=0.10),
        ]
        candidate_pool = {"candidate_count": 10, "eligible_candidate_count": 10, "unique_eligible_ticker_count": 10, "candidates": []}
    else:
        variants = [
            _variant("global_top_3", 3, ["A", "B", "C"], aggregate_conviction_score=2.9, average_rank=2.0, hhi=0.3333333333, effective_n=3.0, max_single_name_weight=0.3333333333, turnover=0.20),
            _variant("global_top_5", 5, ["A", "B", "C", "D", "E"], aggregate_conviction_score=4.5, average_rank=3.0, hhi=0.20, effective_n=5.0, max_single_name_weight=0.20, turnover=0.30),
            _variant("global_top_6", 6, ["A", "B", "C", "D", "E", "F"], aggregate_conviction_score=5.0, average_rank=3.5, hhi=0.1666666667, effective_n=6.0, max_single_name_weight=0.1666666667, turnover=1.20),
        ]
        candidate_pool = {"candidate_count": 6, "eligible_candidate_count": 6, "unique_eligible_ticker_count": 6, "candidates": []}
    return {
        "metadata": {
            "trade_date": TRADE_DATE,
            "generated_at": "2026-06-25T18:00:00Z",
            "git_sha": "testsha",
            "mode": "research_only",
            "fr_id": "FR-105",
            "phase": "Phase 2",
            "schema_version": "fr105_phase2_global_topn_frontier.v1",
            "contract_id": CONTRACT_ID,
            "production_execution_modules_invoked": [],
        },
        "input_contract": {},
        "input_baseline": {},
        "pit_controls": {
            "trade_date": TRADE_DATE,
            "data_asof": None if sparse else TRADE_DATE,
            "universe_asof": None,
            "price_asof": None,
            "no_forward_returns_used": True,
            "no_production_modules_invoked": True,
            "source_artifact_paths": {},
            "unavailable_fields": [],
        },
        "candidate_pool": candidate_pool,
        "frontier_variants": variants,
        "comparison_to_current_policy": {},
        "readiness": {
            "status": "BLOCKED_ARTIFACT_GAPS" if sparse else "READY",
            "blocking_gaps": ["candidate_pool"] if sparse else [],
            "shadow_evaluation_ready": not sparse,
            "recommendations_allowed": False,
            "paper_or_live_influence_allowed": False,
        },
        "score_source_status": {"status": "UNAVAILABLE" if sparse else "FOUND"},
        "selected_universe_status": {"status": "UNAVAILABLE"},
        "constraint_summary": {"status": "FOUND"},
        "data_quality": {
            "sparse_artifact_handling": "PASS",
            "forward_returns_used": False,
            "pit_safe_return_data_available": False,
            "unavailable_fields": [],
        },
        "validation_status": {"status": "PASS", "findings": [], "warnings": []},
    }


def _write_inputs(
    root: Path,
    *,
    sparse: bool = False,
    tie_fixture: bool = False,
    waived_score_source: bool = False,
) -> tuple[Path, Path, Path]:
    out = root / "outputs" / "research" / "fr_105" / CONTRACT_ID
    contract = out / "global_optimizer_replay_contract.json"
    baseline = out / "phase1_current_policy_baseline.json"
    frontier = out / "phase2_global_topn_frontier.json"
    completeness = out / "phase01_artifact_completeness.json"
    _write_json(contract, _phase0_contract())
    _write_json(baseline, _phase1_baseline(sparse=sparse))
    _write_json(frontier, _phase2_frontier(sparse=sparse, tie_fixture=tie_fixture))
    _write_json(
        completeness,
        _phase01_completeness(sparse=sparse, waived_score_source=waived_score_source),
    )
    return contract, baseline, frontier


def test_phase3_sparse_inputs_block_shadow_selection(tmp_path: Path) -> None:
    contract_path, baseline_path, frontier_path = _write_inputs(tmp_path, sparse=True)

    out_path, artifact = write_fr105_phase3_holding_count(
        repo_root=tmp_path,
        trade_date=TRADE_DATE,
        input_contract_path=contract_path,
        input_baseline_path=baseline_path,
        input_frontier_path=frontier_path,
        generated_at="2026-06-25T19:00:00Z",
    )

    assert out_path == tmp_path / "outputs" / "research" / "fr_105" / CONTRACT_ID / "phase3_optimizer_derived_holding_count.json"
    assert artifact["validation_status"]["status"] == "PASS"
    assert artifact["readiness"]["status"] == "BLOCKED_ARTIFACT_GAPS"
    assert artifact["metadata"]["paper_or_live_influence_allowed"] is False
    assert artifact["metadata"]["alpha_chase_recommendations_allowed"] is False
    assert artifact["selected_research_variant"]["status"] == "BLOCKED_ARTIFACT_GAPS"
    assert artifact["selected_research_variant"]["selected_variant_id"] is None
    assert artifact["data_quality"]["status"] == "BLOCKED_ARTIFACT_GAPS"
    assert "selected_research_variant" in artifact["data_quality"]["unavailable_fields"]
    shadow_path = out_path.parent / "shadow_alpha_chase_comparison.json"
    shadow = json.loads(shadow_path.read_text(encoding="utf-8"))
    assert shadow["metadata"]["enabled"] is False
    assert shadow["metadata"]["default_off"] is True
    assert shadow["readiness"]["status"] == "BLOCKED_ARTIFACT_GAPS"
    assert shadow["recommendations"]["allowed"] is False


def test_phase3_preserves_score_source_waiver_context_while_blocking_shadow(tmp_path: Path) -> None:
    contract_path, baseline_path, frontier_path = _write_inputs(tmp_path, waived_score_source=True)

    out_path, artifact = write_fr105_phase3_holding_count(
        repo_root=tmp_path,
        trade_date=TRADE_DATE,
        input_contract_path=contract_path,
        input_baseline_path=baseline_path,
        input_frontier_path=frontier_path,
        generated_at="2026-06-25T19:00:00Z",
    )

    assert artifact["readiness"]["status"] == "BLOCKED_ARTIFACT_GAPS"
    assert artifact["readiness"]["blocking_gaps"] == ["score_source_unavailable_for_score_driven_ranking"]
    gate = artifact["input_completeness"]
    assert gate["phase01_readiness_status"] == "READY_WITH_SCORE_SOURCE_UNAVAILABLE"
    assert gate["historical_replay_ready"] is True
    assert gate["score_driven_ranking_replayable"] is False
    assert gate["alpha_chase_evaluation_ready"] is False
    assert gate["shadow_comparison_ready"] is False
    assert gate["waived_gaps"] == ["score_source"]
    assert (
        gate["score_source_unavailable_waiver"]["reason"]
        == "historical_non_weight_score_source_not_retained"
    )
    assert (
        artifact["selected_research_variant"]["fallback_reason"]
        == "score_source_unavailable_for_score_driven_ranking"
    )

    shadow = json.loads(
        (out_path.parent / "shadow_alpha_chase_comparison.json").read_text(encoding="utf-8")
    )
    assert shadow["readiness"]["blocking_gaps"] == ["score_source_unavailable_for_score_driven_ranking"]
    assert shadow["recommendations"]["allowed"] is False


def test_phase3_populated_frontier_selects_valid_research_variant(tmp_path: Path) -> None:
    contract_path, baseline_path, frontier_path = _write_inputs(tmp_path)

    _, artifact = write_fr105_phase3_holding_count(
        repo_root=tmp_path,
        trade_date=TRADE_DATE,
        input_contract_path=contract_path,
        input_baseline_path=baseline_path,
        input_frontier_path=frontier_path,
        generated_at="2026-06-25T19:00:00Z",
    )

    assert artifact["validation_status"]["status"] == "PASS"
    assert artifact["readiness"]["status"] == "READY"
    selected = artifact["selected_research_variant"]
    assert selected["status"] == "SELECTED_RESEARCH_ONLY"
    assert selected["selected_variant_id"] == "global_top_5"
    assert selected["selected_top_n"] == 5
    assert selected["selected_tickers"] == ["A", "B", "C", "D", "E"]
    selected_variant = next(row for row in artifact["candidate_variants"] if row["variant_id"] == "global_top_5")
    assert selected_variant["eligible_for_research_selection"] is True
    assert selected_variant["guardrail_status"]["overall"] == "PASS"
    assert selected_variant["rank"] == 1
    assert selected_variant["score"] is not None
    assert artifact["comparison_to_current_policy"]["selected_research_position_count"] == 5
    assert artifact["comparison_to_current_policy"]["delta_position_count"] == -3
    shadow_path = tmp_path / "outputs" / "research" / "fr_105" / CONTRACT_ID / "shadow_alpha_chase_comparison.json"
    shadow = json.loads(shadow_path.read_text(encoding="utf-8"))
    assert shadow["metadata"]["enabled"] is False
    assert shadow["readiness"]["recommendations_allowed"] is False
    assert shadow["recommendations"]["items"] == []


def test_phase3_artifact_and_shadow_comparison_are_deterministic_by_default(tmp_path: Path) -> None:
    contract_path, baseline_path, frontier_path = _write_inputs(tmp_path)

    out_path, first_payload = write_fr105_phase3_holding_count(
        repo_root=tmp_path,
        trade_date=TRADE_DATE,
        input_contract_path=contract_path,
        input_baseline_path=baseline_path,
        input_frontier_path=frontier_path,
    )
    shadow_path = out_path.parent / "shadow_alpha_chase_comparison.json"
    first_phase3_text = out_path.read_text(encoding="utf-8")
    first_shadow_text = shadow_path.read_text(encoding="utf-8")
    _, second_payload = write_fr105_phase3_holding_count(
        repo_root=tmp_path,
        trade_date=TRADE_DATE,
        input_contract_path=contract_path,
        input_baseline_path=baseline_path,
        input_frontier_path=frontier_path,
    )

    assert first_payload["metadata"]["generated_at"] == "unavailable"
    assert second_payload == first_payload
    assert out_path.read_text(encoding="utf-8") == first_phase3_text
    assert shadow_path.read_text(encoding="utf-8") == first_shadow_text


def test_phase3_guardrails_reject_concentration_and_turnover(tmp_path: Path) -> None:
    contract_path, baseline_path, frontier_path = _write_inputs(tmp_path)

    _, artifact = write_fr105_phase3_holding_count(
        repo_root=tmp_path,
        trade_date=TRADE_DATE,
        input_contract_path=contract_path,
        input_baseline_path=baseline_path,
        input_frontier_path=frontier_path,
        generated_at="2026-06-25T19:00:00Z",
    )

    top3 = next(row for row in artifact["candidate_variants"] if row["variant_id"] == "global_top_3")
    top6 = next(row for row in artifact["candidate_variants"] if row["variant_id"] == "global_top_6")
    assert top3["eligible_for_research_selection"] is False
    assert "max_single_name_weight_guardrail_failed" in top3["rejection_reasons"]
    assert "effective_N_guardrail_failed" in top3["rejection_reasons"]
    assert top6["eligible_for_research_selection"] is False
    assert "estimated_turnover_from_current_policy_guardrail_failed" in top6["rejection_reasons"]


def test_phase3_tie_breakers_are_deterministic(tmp_path: Path) -> None:
    contract_path, baseline_path, frontier_path = _write_inputs(tmp_path, tie_fixture=True)

    _, artifact = write_fr105_phase3_holding_count(
        repo_root=tmp_path,
        trade_date=TRADE_DATE,
        input_contract_path=contract_path,
        input_baseline_path=baseline_path,
        input_frontier_path=frontier_path,
        generated_at="2026-06-25T19:00:00Z",
    )

    assert artifact["validation_status"]["status"] == "PASS"
    assert artifact["selected_research_variant"]["selected_variant_id"] == "global_top_5"
    top5 = next(row for row in artifact["candidate_variants"] if row["variant_id"] == "global_top_5")
    top6 = next(row for row in artifact["candidate_variants"] if row["variant_id"] == "global_top_6")
    assert top5["score"] == top6["score"]
    assert top5["rank"] == 1
    assert top6["rank"] == 2


def test_phase3_validator_rejects_malformed_artifact(tmp_path: Path) -> None:
    contract_path, baseline_path, frontier_path = _write_inputs(tmp_path)
    _, artifact = write_fr105_phase3_holding_count(
        repo_root=tmp_path,
        trade_date=TRADE_DATE,
        input_contract_path=contract_path,
        input_baseline_path=baseline_path,
        input_frontier_path=frontier_path,
        generated_at="2026-06-25T19:00:00Z",
    )
    bad = json.loads(json.dumps(artifact))
    bad["metadata"]["mode"] = "paper"
    bad["metadata"]["production_execution_modules_invoked"] = ["paper.paper_broker"]
    bad["pit_controls"]["no_forward_returns_used"] = False
    bad["pit_controls"]["no_production_modules_invoked"] = False
    bad["selected_research_variant"]["selected_variant_id"] = "missing"
    bad["selected_research_variant"]["selected_tickers"] = ["A", "A"]
    bad["candidate_variants"][0]["score"] = "bad"
    del bad["decision_policy"]

    result = validate_fr105_phase3_holding_count(bad)

    assert result.status == "FAIL"
    assert "MODE_NOT_RESEARCH_ONLY" in result.findings
    assert "PRODUCTION_EXECUTION_MODULES_INVOKED" in result.findings
    assert "PROHIBITED_PRODUCTION_MODULES:paper.paper_broker" in result.findings
    assert "FORWARD_RETURNS_USED_OR_UNCONFIRMED" in result.findings
    assert "PRODUCTION_MODULE_INVOCATION_FLAG_NOT_TRUE" in result.findings
    assert "SELECTED_VARIANT_NOT_FOUND" in result.findings
    assert "DUPLICATE_TICKERS_IN_SELECTED_VARIANT" in result.findings
    assert "MALFORMED_SCORE:0" in result.findings
    assert "MISSING_TOP_LEVEL_SECTIONS:decision_policy" in result.findings


def test_phase3_builder_does_not_import_production_trading_modules(tmp_path: Path) -> None:
    contract_path, baseline_path, frontier_path = _write_inputs(tmp_path)
    before = set(sys.modules)

    write_fr105_phase3_holding_count(
        repo_root=tmp_path,
        trade_date=TRADE_DATE,
        input_contract_path=contract_path,
        input_baseline_path=baseline_path,
        input_frontier_path=frontier_path,
        generated_at="2026-06-25T19:00:00Z",
    )

    newly_imported = set(sys.modules) - before
    prohibited = {
        name
        for name in newly_imported
        for module in PROHIBITED_PRODUCTION_MODULES
        if name == module or name.startswith(module + ".")
    }
    assert prohibited == set()

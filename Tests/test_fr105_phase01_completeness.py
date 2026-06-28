from __future__ import annotations

import json
from pathlib import Path

from research.fr105_phase01_completeness import (
    FOUND,
    MISSING,
    READY_WITH_SCORE_SOURCE_UNAVAILABLE,
    UNAVAILABLE,
    _score_source_status,
    build_fr105_phase01_completeness,
    write_fr105_phase01_completeness,
)
from scripts.research.run_fr105_phase01_readiness import run_phase01_readiness


TRADE_DATE = "2026-06-26"
CONTRACT_ID = "2026-06-26_fr105"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _phase0(root: Path, *, score_source: str | None = "model_score") -> Path:
    lifecycle = root / "sources" / "candidate_trade_lifecycle.json"
    target = root / "sources" / "target_portfolio.json"
    universe = root / "sources" / "candidate_universe.json"
    pool = root / "sources" / "candidate_pool.json"
    score_available = score_source is not None
    _write_json(lifecycle, {"status": "FOUND"})
    _write_json(target, {"targets": [{"ticker": "AAA", "target_weight": 0.25}]})
    _write_json(
        universe,
        {
            "readiness": {"status": "FOUND"},
            "candidate_universe_count": 2,
            "symbols": ["AAA", "BBB"],
            "metadata": {"generated_at": "2026-06-26T20:00:00Z"},
        },
    )
    _write_json(
        pool,
        {
            "readiness": {"status": "FOUND"},
            "candidate_count": 2,
            "candidates": [
                {
                    "ticker": "AAA",
                    "score_source": score_source if score_available else "UNAVAILABLE",
                    "score_value": 0.91 if score_available else None,
                },
                {
                    "ticker": "BBB",
                    "score_source": score_source if score_available else "UNAVAILABLE",
                    "score_value": 0.81 if score_available else None,
                },
            ],
            "metadata": {"generated_at": "2026-06-26T20:00:00Z"},
        },
    )
    path = root / "outputs" / "research" / "fr_105" / CONTRACT_ID / "global_optimizer_replay_contract.json"
    _write_json(
        path,
        {
            "metadata": {
                "trade_date": TRADE_DATE,
                "contract_id": CONTRACT_ID,
                "mode": "research_only",
                "fr_id": "FR-105",
                "schema_version": "fr105_global_optimizer_replay_contract.v1",
                "price_asof": TRADE_DATE,
                "production_execution_modules_invoked": [],
            },
            "source_artifacts": {
                "candidate_universe_path": "sources/candidate_universe.json",
                "candidate_pool_path": "sources/candidate_pool.json",
                "candidate_trade_lifecycle_path": "sources/candidate_trade_lifecycle.json",
                "target_portfolio_path": "sources/target_portfolio.json",
                "sleeve_artifacts": ["sources/candidate_trade_lifecycle.json"],
                "execution_results_path": None,
                "reconciliation_path": None,
                "broker_positions_path": None,
                "price_source": "test_prices",
            },
            "universe_snapshot": {
                "status": "FOUND",
                "universe_id": "test_universe",
                "asof": TRADE_DATE,
                "ticker_count": 2,
                "source_artifact_path": "sources/candidate_universe.json",
            },
            "sleeve_candidates": [
                {
                    "ticker": "AAA",
                    "sleeve_id": "caerus_polaris",
                    "strategy_id": "polaris_v1",
                    "source_model": "momentum",
                    "rank": 1 if score_available else None,
                    "score": 0.91 if score_available else None,
                    "score_source": score_source if score_available else None,
                    "conviction_score": 0.91 if score_available else None,
                    "expected_alpha": None,
                    "expected_risk": None,
                    "target_weight": 0.25,
                    "current_weight": 0.10,
                    "data_asof": TRADE_DATE,
                    "source_artifact_path": "sources/candidate_trade_lifecycle.json",
                    "lifecycle": {"decision_reason": "selected"},
                    "reason_excluded": None,
                },
                {
                    "ticker": "BBB",
                    "sleeve_id": "caerus_orion",
                    "strategy_id": "orion_v1",
                    "source_model": "quality",
                    "rank": 2 if score_available else None,
                    "score": 0.81 if score_available else None,
                    "score_source": score_source if score_available else None,
                    "conviction_score": 0.81 if score_available else None,
                    "expected_alpha": None,
                    "expected_risk": None,
                    "target_weight": 0.15,
                    "current_weight": 0.00,
                    "data_asof": TRADE_DATE,
                    "source_artifact_path": "sources/candidate_trade_lifecycle.json",
                    "lifecycle": {"decision_reason": "min_notional_filter"},
                    "reason_excluded": "min_notional_filter",
                },
            ],
            "current_portfolio": {
                "source_artifact_path": "sources/current_positions.json",
                "positions_count": 1,
                "positions": [{"ticker": "AAA", "quantity": 10.0, "market_value": 1000.0, "current_weight": 0.10}],
            },
            "constraints_snapshot": {
                "max_single_name_weight": 0.25,
                "sector_caps": {"Technology": 0.40},
                "effective_n_floor": 5,
                "turnover_cap": 0.95,
                "liquidity_constraints": {"min_adv": 1000000},
                "min_trade_dollars": 100.0,
                "cash_target": 0.05,
                "gross_exposure_target": 0.95,
                "buying_power_available": 25000.0,
                "rebudget_policy": "post_sell_rebudget",
                "min_notional_policy": {"min_trade_dollars": 100.0},
            },
            "execution_residuals": {
                "planned_candidates": 2,
                "executable_candidates": 1,
                "intended_orders": 1,
                "submitted_orders": 1,
                "filled_orders": 1,
                "suppressed_count": 1,
                "clipped_count": 0,
                "suppression_reason_counts": {"min_notional_filter": 1},
                "clipping_reason_counts": {},
                "estimated_unexecuted_notional_total": 50.0,
            },
            "provenance_schema_version": "fr105_candidate_provenance.v1",
            "validation_status": {"status": "PASS", "findings": [], "warnings": []},
        },
    )
    return path


def _phase1(root: Path) -> Path:
    path = root / "outputs" / "research" / "fr_105" / CONTRACT_ID / "phase1_current_policy_baseline.json"
    _write_json(
        path,
        {
            "metadata": {
                "trade_date": TRADE_DATE,
                "contract_id": CONTRACT_ID,
                "mode": "research_only",
                "fr_id": "FR-105",
                "schema_version": "fr105_phase1_current_policy_baseline.v1",
                "production_execution_modules_invoked": [],
            },
            "pit_controls": {
                "trade_date": TRADE_DATE,
                "data_asof": TRADE_DATE,
                "universe_asof": TRADE_DATE,
                "price_asof": TRADE_DATE,
                "source_artifact_paths": {},
                "no_forward_returns_used": True,
                "no_production_modules_invoked": True,
                "unavailable_fields": [],
            },
            "baseline_positions": {
                "positions_count": 1,
                "positions": [{"ticker": "AAA", "quantity": 10.0, "market_value": 1000.0, "current_weight": 0.10}],
            },
            "baseline_metrics": {"position_count": 1},
            "validation_status": {"status": "PASS", "findings": [], "warnings": []},
        },
    )
    return path


def _phase01_readiness_repo(root: Path) -> None:
    run_id = "2026-06-26T093000-0400_readiness"
    run = root / "outputs" / "runs" / run_id
    _write_json(
        root / "paper" / "config_paper.json",
        {
            "constraints": {"min_trade_dollars": 100.0},
            "risk": {"max_position_pct": 0.20, "max_turnover_pct": 0.95},
        },
    )
    _write_json(
        root / "outputs" / "precompute" / TRADE_DATE / "signals.json",
        {
            "meta": {"asof_date": "2026-06-25", "trade_date": TRADE_DATE},
            "signals": [
                {"ticker": "AAA", "sleeve": "sleeve_trend", "raw_score": 0.10, "target_weight": 0.10},
                {"ticker": "BBB", "sleeve": "sleeve_quality", "raw_score": 0.20, "target_weight": 0.20},
                {"ticker": "CASH", "sleeve": "core", "raw_score": 0.0, "target_weight": 0.70},
            ],
        },
    )
    _write_json(
        root / "outputs" / "precompute" / TRADE_DATE / "planned_execution_payload.json",
        {"trade_date": TRADE_DATE, "pricing_asof": "2026-06-25", "planner_intended_trades_count": 2},
    )
    _write_json(
        run / "snapshots" / f"risk_adjusted_{TRADE_DATE}.json",
        {
            "meta": {"asof_date": "2026-06-25", "trade_date": TRADE_DATE},
            "signals": [
                {"ticker": "AAA", "sleeve": "sleeve_trend", "target_weight": 0.11},
                {"ticker": "BBB", "sleeve": "sleeve_quality", "target_weight": 0.19},
            ],
        },
    )
    _write_json(
        run / "audit" / "risk_controls.json",
        {"result": {"actions": [{"action": "exposure_cap", "before_gross": 1.05, "after_gross": 0.95}]}},
    )
    _write_json(
        run / "audit" / f"execution_target_attainment_{TRADE_DATE}.json",
        {
            "missing_intended_buys": [{"ticker": "BBB", "reason": "buy_blocked_insufficient_buying_power"}],
            "skipped_deferred_buy_notional": 125.0,
        },
    )
    _write_json(
        run / "audit" / "execution_integrity.json",
        {
            "intended_orders_count": 2,
            "explicit_block_reasons": ["buy_phase_block_reason:sell_phase_completed"],
        },
    )
    _write_json(
        run / "broker" / f"post_sell_rebudget_{TRADE_DATE}.json",
        {
            "enabled": True,
            "status": "REBUILT",
            "target_cash_weight": 0.05,
            "post_sell_buying_power": 500.0,
            "skipped_buy_orders": [{"ticker": "BBB", "block_reason": "min_trade_dollars_after_budget_clip"}],
        },
    )
    _write_json(run / "broker" / f"intended_orders_{TRADE_DATE}.json", {"orders_intended_count": 2})
    _write_json(run / "broker" / "posttrade_account_snapshot.json", {"equity": 1000.0, "cash": 700.0})
    _write_json(
        run / "broker" / "posttrade_positions.json",
        {"positions": [{"symbol": "AAA", "qty": "1", "market_value": "100.00"}]},
    )
    _write_json(
        run / "execution_results.json",
        {
            "trade_date": TRADE_DATE,
            "planned_payload_trade_count": 3,
            "executable_trades_count": 2,
            "submitted_count": 1,
            "orders_filled_count": 1,
            "skipped_buy_count": 1,
        },
    )
    _write_json(run / "execution_payload.json", {"trade_date": TRADE_DATE, "submitted_count": 1})


def test_phase01_completeness_reports_complete_artifact_set(tmp_path: Path) -> None:
    phase0 = _phase0(tmp_path)
    phase1 = _phase1(tmp_path)

    out_path, payload = write_fr105_phase01_completeness(
        repo_root=tmp_path,
        trade_date=TRADE_DATE,
        input_contract_path=phase0,
        input_baseline_path=phase1,
        generated_at="2026-06-26T20:00:00Z",
    )

    assert out_path == tmp_path / "outputs" / "research" / "fr_105" / CONTRACT_ID / "phase01_artifact_completeness.json"
    assert payload["summary"]["status"] == "COMPLETE"
    assert payload["readiness"]["alpha_chase_evaluation_ready"] is True
    assert payload["metadata"]["trading_behavior_changed"] is False
    assert payload["metadata"]["optimizer_behavior_changed"] is False
    assert all(row["status"] == FOUND for row in payload["required_evidence"].values())
    assert payload["source_artifacts"]["phase0_replay_contract"]["sha256"]
    assert payload["source_artifacts"]["phase1_current_policy_baseline"]["sha256"]

    first = out_path.read_text(encoding="utf-8")
    _, second_payload = write_fr105_phase01_completeness(
        repo_root=tmp_path,
        trade_date=TRADE_DATE,
        input_contract_path=phase0,
        input_baseline_path=phase1,
        generated_at="2026-06-26T20:00:00Z",
    )
    assert second_payload == payload
    assert out_path.read_text(encoding="utf-8") == first


def test_phase01_completeness_sparse_artifacts_block_readiness(tmp_path: Path) -> None:
    payload = build_fr105_phase01_completeness(
        repo_root=tmp_path,
        trade_date=TRADE_DATE,
        run_id=CONTRACT_ID,
    )

    assert payload["summary"]["status"] == "INCOMPLETE"
    assert payload["readiness"]["status"] == "BLOCKED_ARTIFACT_GAPS"
    assert payload["source_artifacts"]["phase0_replay_contract"]["status"] == MISSING
    assert payload["source_artifacts"]["phase1_current_policy_baseline"]["status"] == MISSING
    assert payload["required_evidence"]["candidate_pool"]["status"] == UNAVAILABLE
    assert payload["required_evidence"]["target_portfolio"]["status"] == UNAVAILABLE


def test_score_source_status_requires_explicit_score_provenance() -> None:
    for row in (
        {"conviction_score": 0.91},
        {"score": 0.5},
        {"expected_alpha": 0.02},
        {"conviction_score": 0.91, "source_model": "orion"},
    ):
        result = _score_source_status([row])
        assert result["status"] == UNAVAILABLE
        assert result["reason"] == "score_source_provenance_unavailable"

    for source_field in ("score_source", "conviction_score_source", "expected_alpha_source"):
        result = _score_source_status(
            [{"conviction_score": 0.91, source_field: "orion_model_output"}]
        )
        assert result["status"] == FOUND
        assert result["evidence"]["score_sources"] == ["orion_model_output"]


def test_score_source_status_rejects_construction_derived_sources() -> None:
    prohibited_sources = (
        "target_weight",
        "allocation_weight",
        "portfolio_weight",
        "final_target_weight",
        "rebalance_delta",
        "target_notional",
        "notional",
        "construction_output",
        "portfolio_construction",
        "target_portfolio",
        "allocation",
        "weight",
        "weights",
    )
    for source_field in ("score_source", "conviction_score_source", "expected_alpha_source"):
        for source in prohibited_sources:
            result = _score_source_status(
                [{"conviction_score": 0.91, source_field: source}]
            )
            assert result["status"] == MISSING
            assert result["reason"] == "weight_derived_score_source_present"


def test_phase01_completeness_waives_unavailable_historical_score_source_provenance(tmp_path: Path) -> None:
    phase0 = _phase0(tmp_path, score_source="")
    phase1 = _phase1(tmp_path)

    payload = build_fr105_phase01_completeness(
        repo_root=tmp_path,
        trade_date=TRADE_DATE,
        input_contract_path=phase0,
        input_baseline_path=phase1,
    )

    assert payload["summary"]["status"] == "INCOMPLETE"
    assert payload["required_evidence"]["score_source"]["status"] == UNAVAILABLE
    assert payload["required_evidence"]["score_source"]["reason"] == "score_source_provenance_unavailable"
    assert payload["readiness"]["status"] == READY_WITH_SCORE_SOURCE_UNAVAILABLE
    assert payload["readiness"]["blocking_gaps"] == []
    assert payload["readiness"]["waived_gaps"] == ["score_source"]
    assert payload["readiness"]["historical_replay_ready"] is True
    assert payload["readiness"]["score_driven_ranking_replayable"] is False
    assert payload["readiness"]["alpha_chase_evaluation_ready"] is False
    assert payload["readiness"]["shadow_comparison_ready"] is False
    assert payload["readiness"]["score_source_unavailable_waiver"]["applied"] is True
    assert (
        payload["readiness"]["score_source_unavailable_waiver"]["reason"]
        == "historical_non_weight_score_source_not_retained"
    )


def test_phase01_completeness_waives_unavailable_historical_score_source(tmp_path: Path) -> None:
    phase0 = _phase0(tmp_path, score_source=None)
    phase1 = _phase1(tmp_path)

    payload = build_fr105_phase01_completeness(
        repo_root=tmp_path,
        trade_date=TRADE_DATE,
        input_contract_path=phase0,
        input_baseline_path=phase1,
    )

    assert payload["summary"]["status"] == "INCOMPLETE"
    assert payload["required_evidence"]["score_source"]["status"] == UNAVAILABLE
    assert payload["required_evidence"]["score_source"]["reason"] == "score_fields_unavailable"
    assert payload["readiness"]["status"] == READY_WITH_SCORE_SOURCE_UNAVAILABLE
    assert payload["readiness"]["blocking_gaps"] == []
    assert payload["readiness"]["waived_gaps"] == ["score_source"]
    assert payload["readiness"]["historical_replay_ready"] is True
    assert payload["readiness"]["alpha_chase_evaluation_ready"] is False
    assert payload["readiness"]["shadow_comparison_ready"] is False
    assert payload["readiness"]["score_driven_ranking_replayable"] is False
    assert payload["readiness"]["score_source_unavailable_waiver"]["applied"] is True
    assert (
        payload["readiness"]["score_source_unavailable_waiver"]["reason"]
        == "historical_non_weight_score_source_not_retained"
    )
    assert payload["validation_status"]["warnings"] == [
        "score_source_unavailable_historical_waiver_score_driven_ranking_not_replayable"
    ]


def test_phase01_completeness_requires_candidate_artifacts(tmp_path: Path) -> None:
    phase0 = _phase0(tmp_path)
    phase1 = _phase1(tmp_path)
    (tmp_path / "sources" / "candidate_universe.json").unlink()
    (tmp_path / "sources" / "candidate_pool.json").unlink()

    payload = build_fr105_phase01_completeness(
        repo_root=tmp_path,
        trade_date=TRADE_DATE,
        input_contract_path=phase0,
        input_baseline_path=phase1,
    )

    assert payload["summary"]["status"] == "INCOMPLETE"
    assert payload["required_evidence"]["candidate_universe"]["status"] == UNAVAILABLE
    assert payload["required_evidence"]["candidate_universe"]["reason"] == "candidate_universe_artifact_unavailable"
    assert payload["required_evidence"]["candidate_pool"]["status"] == UNAVAILABLE
    assert payload["required_evidence"]["candidate_pool"]["reason"] == "candidate_pool_artifact_unavailable"


def test_phase01_completeness_rejects_weight_derived_score_sources(tmp_path: Path) -> None:
    phase0 = _phase0(tmp_path, score_source="target_weight")
    phase1 = _phase1(tmp_path)

    payload = build_fr105_phase01_completeness(
        repo_root=tmp_path,
        trade_date=TRADE_DATE,
        input_contract_path=phase0,
        input_baseline_path=phase1,
    )

    assert payload["summary"]["status"] == "INCOMPLETE"
    assert payload["required_evidence"]["score_source"]["status"] == MISSING
    assert payload["required_evidence"]["score_source"]["reason"] == "weight_derived_score_source_present"


def test_phase01_readiness_orchestrator_wires_existing_artifacts_but_keeps_core_gaps_blocked(tmp_path: Path) -> None:
    _phase01_readiness_repo(tmp_path)
    output_root = tmp_path / "research_out"

    path, summary = run_phase01_readiness(
        repo_root=tmp_path,
        trade_date=TRADE_DATE,
        output_root=output_root,
        generated_at="2026-06-26T20:00:00Z",
    )
    first_payload = json.loads(path.read_text(encoding="utf-8"))
    first_text = path.read_text(encoding="utf-8")
    _, second_summary = run_phase01_readiness(
        repo_root=tmp_path,
        trade_date=TRADE_DATE,
        output_root=output_root,
        generated_at="2026-06-26T20:00:00Z",
    )

    assert second_summary == summary
    assert path.read_text(encoding="utf-8") == first_text
    assert summary["status"] == READY_WITH_SCORE_SOURCE_UNAVAILABLE
    assert summary["alpha_chase_evaluation_ready"] is False
    assert summary["shadow_comparison_ready"] is False
    assert summary["safety"]["broker_behavior_changed"] is False
    assert summary["safety"]["production_execution_modules_invoked"] == []
    assert set(summary["closed_gaps"]) >= {
        "active_constraints",
        "candidate_pool",
        "candidate_universe",
        "current_holdings",
        "current_weights",
        "execution_residuals",
        "lifecycle_artifact",
        "pit_lineage",
        "provenance_availability",
        "sleeve_source",
        "suppression_reasons",
        "target_portfolio",
        "target_weights",
    }
    assert summary["remaining_blocking_gaps"] == []
    assert set(summary["unavailable_fields"]) == {"score_source"}
    assert first_payload["required_evidence"]["candidate_pool"]["status"] == FOUND
    assert first_payload["required_evidence"]["candidate_universe"]["status"] == FOUND
    assert first_payload["required_evidence"]["lifecycle_artifact"]["status"] == FOUND
    assert first_payload["required_evidence"]["score_source"]["status"] == UNAVAILABLE
    assert first_payload["readiness"]["historical_replay_ready"] is True
    assert first_payload["readiness"]["score_driven_ranking_replayable"] is False
    assert first_payload["readiness"]["score_source_unavailable_waiver"]["applied"] is True
    assert first_payload["required_evidence"]["target_portfolio"]["status"] == FOUND
    assert first_payload["required_evidence"]["target_weights"]["status"] == FOUND

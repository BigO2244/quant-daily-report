from __future__ import annotations

import json
from pathlib import Path

from research.fr105_phase01_completeness import (
    FOUND,
    MISSING,
    UNAVAILABLE,
    build_fr105_phase01_completeness,
    write_fr105_phase01_completeness,
)


TRADE_DATE = "2026-06-26"
CONTRACT_ID = "2026-06-26_fr105"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _phase0(root: Path, *, score_source: str = "model_score") -> Path:
    lifecycle = root / "sources" / "candidate_trade_lifecycle.json"
    target = root / "sources" / "target_portfolio.json"
    _write_json(lifecycle, {"status": "FOUND"})
    _write_json(target, {"targets": [{"ticker": "AAA", "target_weight": 0.25}]})
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
                "source_artifact_path": "sources/universe.json",
            },
            "sleeve_candidates": [
                {
                    "ticker": "AAA",
                    "sleeve_id": "caerus_polaris",
                    "strategy_id": "polaris_v1",
                    "source_model": "momentum",
                    "rank": 1,
                    "score": 0.91,
                    "score_source": score_source,
                    "conviction_score": 0.91,
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
                    "rank": 2,
                    "score": 0.81,
                    "score_source": score_source,
                    "conviction_score": 0.81,
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

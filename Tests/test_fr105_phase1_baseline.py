from __future__ import annotations

import json
import sys
from pathlib import Path

from research.fr105_phase1_baseline import (
    validate_fr105_phase1_baseline,
    write_fr105_phase1_baseline,
)
from research.fr105_replay_contract import PROHIBITED_PRODUCTION_MODULES


TRADE_DATE = "2026-06-25"
CONTRACT_ID = "2026-06-25T093000-0400_fr105"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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
    positions = [] if sparse else [
        {"ticker": "AAA", "quantity": 5.0, "market_value": 500.0, "current_weight": 0.50},
        {"ticker": "BBB", "quantity": 10.0, "market_value": 250.0, "current_weight": 0.25},
    ]
    candidates = [] if sparse else [
        {
            "ticker": "AAA",
            "sleeve_id": "caerus_polaris",
            "strategy_id": "polaris_v1",
            "source_model": "momentum",
            "lifecycle": {
                "side": "BUY",
                "submitted": True,
                "accepted": True,
                "filled": True,
                "clipped": False,
                "decision_stage": "posttrade_reconciliation",
                "decision_reason": "filled",
                "precompute": {"notional": 100.0},
                "executable": {"notional": 100.0},
                "intended": {"notional": 100.0},
                "final": {"submitted_shares": 1.0, "filled_shares": 1.0},
            },
            "rank": 1,
            "score": None,
            "conviction_score": 0.91,
            "expected_alpha": None,
            "expected_risk": None,
            "target_weight": 0.60,
            "target_notional": 600.0,
            "current_weight": 0.50,
            "current_notional": 500.0,
            "delta_notional": 100.0,
            "reason_included": "model_selected",
            "reason_excluded": None,
            "data_asof": TRADE_DATE,
            "source_artifact_path": source_artifacts["candidate_trade_lifecycle_path"],
        },
        {
            "ticker": "CCC",
            "sleeve_id": "caerus_orion",
            "strategy_id": "orion_v1",
            "source_model": "quality",
            "lifecycle": {
                "side": "SELL",
                "submitted": False,
                "accepted": False,
                "filled": False,
                "clipped": False,
                "decision_stage": "executable_filter",
                "decision_reason": "min_notional_filter",
                "precompute": {"notional": 50.0},
                "executable": {"notional": 50.0},
                "intended": {"notional": None},
                "final": {"submitted_shares": None, "filled_shares": None},
            },
            "rank": 4,
            "score": None,
            "conviction_score": 0.55,
            "expected_alpha": None,
            "expected_risk": None,
            "target_weight": 0.0,
            "target_notional": 0.0,
            "current_weight": 0.05,
            "current_notional": 50.0,
            "delta_notional": -50.0,
            "reason_included": "model_selected",
            "reason_excluded": "min_notional_filter",
            "data_asof": TRADE_DATE,
            "source_artifact_path": source_artifacts["candidate_trade_lifecycle_path"],
        },
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
            "positions": positions,
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
            "planned_candidates": None if sparse else 2,
            "executable_candidates": None if sparse else 1,
            "intended_orders": None if sparse else 1,
            "submitted_orders": None if sparse else 1,
            "filled_orders": None if sparse else 1,
            "suppressed_count": None if sparse else 1,
            "clipped_count": None if sparse else 0,
            "suppression_reason_counts": {} if sparse else {"min_notional_filter": 1},
            "clipping_reason_counts": {},
            "estimated_unexecuted_notional_total": None if sparse else 50.0,
        },
        "provenance_schema_version": "fr105_candidate_provenance.v1",
        "validation_status": {"status": "PASS", "findings": [], "warnings": []},
    }


def _write_phase0(root: Path, *, sparse: bool = False) -> Path:
    path = root / "outputs" / "research" / "fr_105" / CONTRACT_ID / "global_optimizer_replay_contract.json"
    _write_json(path, _phase0_contract(sparse=sparse))
    return path


def test_phase1_builds_sparse_baseline_from_sparse_phase0_contract(tmp_path: Path) -> None:
    contract_path = _write_phase0(tmp_path, sparse=True)

    out_path, baseline = write_fr105_phase1_baseline(
        repo_root=tmp_path,
        trade_date=TRADE_DATE,
        input_contract_path=contract_path,
    )

    assert out_path == tmp_path / "outputs" / "research" / "fr_105" / CONTRACT_ID / "phase1_current_policy_baseline.json"
    assert baseline["metadata"]["generated_at"] == "unavailable"
    assert baseline["validation_status"]["status"] == "PASS"
    assert baseline["metadata"]["mode"] == "research_only"
    assert baseline["pit_controls"]["no_forward_returns_used"] is True
    assert baseline["pit_controls"]["no_production_modules_invoked"] is True
    assert baseline["current_policy_snapshot"]["status"] == "SPARSE_INPUT"
    assert baseline["baseline_positions"]["positions"] == []
    assert baseline["baseline_trades"]["trades"] == []
    assert baseline["baseline_metrics"]["position_count"] is None
    assert baseline["baseline_metrics"]["gross_exposure"] is None
    assert "baseline_positions" in baseline["data_quality"]["unavailable_fields"]
    assert "baseline_trades" in baseline["data_quality"]["unavailable_fields"]


def test_phase1_builds_baseline_metrics_from_fixture_positions_and_trades(tmp_path: Path) -> None:
    contract_path = _write_phase0(tmp_path, sparse=False)

    _, baseline = write_fr105_phase1_baseline(
        repo_root=tmp_path,
        trade_date=TRADE_DATE,
        input_contract_path=contract_path,
        generated_at="2026-06-25T17:00:00Z",
    )

    assert baseline["validation_status"]["status"] == "PASS"
    assert baseline["current_policy_snapshot"]["status"] == "AVAILABLE_FROM_PHASE0_CONTRACT"
    assert baseline["baseline_metrics"]["position_count"] == 2
    assert baseline["baseline_metrics"]["gross_exposure"] == 0.75
    assert baseline["baseline_metrics"]["cash_weight"] == 0.25
    assert baseline["baseline_metrics"]["max_single_name_weight"] == 0.5
    assert baseline["baseline_metrics"]["HHI"] == 0.3125
    assert baseline["baseline_metrics"]["effective_N"] == 3.2
    assert baseline["baseline_metrics"]["turnover"] == 0.075
    assert baseline["baseline_metrics"]["planned_candidates"] == 2
    assert baseline["baseline_metrics"]["submitted_orders"] == 1
    assert baseline["baseline_metrics"]["filled_orders"] == 1
    assert baseline["baseline_metrics"]["suppressed_count"] == 1
    assert baseline["baseline_metrics"]["clipped_count"] == 0
    assert baseline["baseline_metrics"]["estimated_unexecuted_notional_total"] == 50.0
    assert baseline["pit_controls"]["data_asof"] == TRADE_DATE
    assert baseline["pit_controls"]["universe_asof"] is None
    assert baseline["pit_controls"]["price_asof"] is None
    assert baseline["data_quality"]["status"] == "PARTIAL"


def test_phase1_validator_rejects_malformed_baseline_artifact(tmp_path: Path) -> None:
    contract_path = _write_phase0(tmp_path, sparse=True)
    _, baseline = write_fr105_phase1_baseline(
        repo_root=tmp_path,
        trade_date=TRADE_DATE,
        input_contract_path=contract_path,
        generated_at="2026-06-25T17:00:00Z",
    )
    bad = json.loads(json.dumps(baseline))
    bad["metadata"]["mode"] = "paper"
    bad["metadata"]["production_execution_modules_invoked"] = ["paper.paper_broker"]
    bad["pit_controls"]["no_forward_returns_used"] = False
    bad["pit_controls"]["no_production_modules_invoked"] = False
    bad["baseline_metrics"]["gross_exposure"] = "unknown"
    bad["baseline_positions"]["positions"] = "not-a-list"
    del bad["replay_window"]

    result = validate_fr105_phase1_baseline(bad)

    assert result.status == "FAIL"
    assert "MODE_NOT_RESEARCH_ONLY" in result.findings
    assert "PRODUCTION_EXECUTION_MODULES_INVOKED" in result.findings
    assert "PROHIBITED_PRODUCTION_MODULES:paper.paper_broker" in result.findings
    assert "FORWARD_RETURNS_USED_OR_UNCONFIRMED" in result.findings
    assert "PRODUCTION_MODULE_INVOCATION_FLAG_NOT_TRUE" in result.findings
    assert "MALFORMED_BASELINE_METRIC:gross_exposure" in result.findings
    assert "MALFORMED_LIST:baseline_positions.positions" in result.findings
    assert "MISSING_TOP_LEVEL_SECTIONS:replay_window" in result.findings


def test_phase1_builder_does_not_import_production_trading_modules(tmp_path: Path) -> None:
    contract_path = _write_phase0(tmp_path, sparse=False)
    before = set(sys.modules)

    write_fr105_phase1_baseline(
        repo_root=tmp_path,
        trade_date=TRADE_DATE,
        input_contract_path=contract_path,
        generated_at="2026-06-25T17:00:00Z",
    )

    newly_imported = set(sys.modules) - before
    prohibited = {
        name
        for name in newly_imported
        for module in PROHIBITED_PRODUCTION_MODULES
        if name == module or name.startswith(module + ".")
    }
    assert prohibited == set()

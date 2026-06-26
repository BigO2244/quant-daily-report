from __future__ import annotations

import json
import sys
from pathlib import Path

from research.fr105_replay_contract import (
    PROHIBITED_PRODUCTION_MODULES,
    build_fr105_replay_contract,
    validate_fr105_replay_contract,
    write_fr105_replay_contract,
)


TRADE_DATE = "2026-06-25"
RUN_ID = "2026-06-25T093000-0400_fr105"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _repo_fixture(tmp_path: Path) -> Path:
    _write_json(
        tmp_path / "paper" / "config_paper.json",
        {
            "constraints": {"min_trade_dollars": 100.0},
            "risk": {"max_position_pct": 0.20, "max_turnover_pct": 0.95},
        },
    )
    run = tmp_path / "outputs" / "runs" / RUN_ID
    lifecycle_path = run / "audit" / f"candidate_trade_lifecycle_{TRADE_DATE}.json"
    _write_json(
        lifecycle_path,
        {
            "schema_version": "candidate_trade_lifecycle.v1",
            "trade_date": TRADE_DATE,
            "run_id": RUN_ID,
            "source_artifacts": {
                "precompute_payload": f"outputs/precompute/{TRADE_DATE}/planned_execution_payload.json",
                "intended_orders": f"outputs/runs/{RUN_ID}/broker/intended_orders_{TRADE_DATE}.json",
            },
            "execution_config": {"min_trade_dollars": 100.0, "allow_fractional": True},
            "counts": {
                "precompute_candidates": 3,
                "passed_executable_filter": 2,
                "intended_orders": 2,
                "submitted": 2,
                "accepted": 2,
                "filled": 1,
                "suppressed": 1,
                "clipped": 1,
                "suppression_reason_counts": {"min_notional_filter": 1},
                "clipping_reason_counts": {"post_sell_rebudget_capital_clipped": 1},
            },
            "candidates": [
                {
                    "ticker": "AAA",
                    "side": "BUY",
                    "candidate_source": "precompute_payload",
                    "precompute_shares": 2.0,
                    "precompute_price": 50.0,
                    "precompute_notional": 100.0,
                    "precompute_reason": "model_selected",
                    "normalized_executable_shares": 2.0,
                    "normalized_executable_price": 50.0,
                    "normalized_executable_notional": 100.0,
                    "passed_min_notional": True,
                    "reached_intended_orders": True,
                    "intended_shares": 2.0,
                    "intended_price": 50.0,
                    "intended_notional": 100.0,
                    "post_sell_rebudget_status": "submitted",
                    "submitted": True,
                    "accepted": True,
                    "filled": True,
                    "rejected": False,
                    "clipped": False,
                    "final_submitted_shares": 2.0,
                    "final_filled_shares": 2.0,
                    "decision_stage": "posttrade_reconciliation",
                    "decision_reason": "filled",
                    "sleeve_id": "caerus_polaris",
                    "strategy_id": "polaris_v1",
                    "source_model": "momentum",
                    "candidate_rank": 1,
                    "conviction_score": 0.91,
                    "target_weight": 0.10,
                    "target_notional": 100.0,
                    "current_weight": 0.0,
                    "current_notional": 0.0,
                    "delta_notional": 100.0,
                    "estimated_unexecuted_notional": 0.0,
                },
                {
                    "ticker": "BBB",
                    "side": "SELL",
                    "candidate_source": "precompute_payload",
                    "precompute_shares": 1.0,
                    "precompute_price": 71.5,
                    "precompute_notional": 71.5,
                    "normalized_executable_shares": 1.0,
                    "normalized_executable_price": 71.5,
                    "normalized_executable_notional": 71.5,
                    "passed_min_notional": False,
                    "reached_intended_orders": False,
                    "post_sell_rebudget_status": "not_applicable",
                    "submitted": False,
                    "accepted": False,
                    "filled": False,
                    "rejected": False,
                    "clipped": False,
                    "suppression_or_clipping_reason": "min_notional_filter",
                    "decision_stage": "executable_filter",
                    "decision_reason": "min_notional_filter",
                    "estimated_unexecuted_notional": 71.5,
                },
                {
                    "ticker": "CCC",
                    "side": "BUY",
                    "candidate_source": "precompute_payload",
                    "precompute_shares": 3.0,
                    "precompute_price": 40.0,
                    "precompute_notional": 120.0,
                    "normalized_executable_shares": 3.0,
                    "normalized_executable_price": 40.0,
                    "normalized_executable_notional": 120.0,
                    "passed_min_notional": True,
                    "reached_intended_orders": True,
                    "intended_shares": 3.0,
                    "intended_price": 40.0,
                    "intended_notional": 120.0,
                    "post_sell_rebudget_status": "submitted",
                    "submitted": True,
                    "accepted": True,
                    "filled": False,
                    "rejected": False,
                    "clipped": True,
                    "suppression_or_clipping_reason": "post_sell_rebudget_capital_clipped",
                    "decision_stage": "post_sell_rebudget",
                    "decision_reason": "post_sell_rebudget_capital_clipped",
                    "final_submitted_shares": 2.25,
                    "final_filled_shares": 0.0,
                    "estimated_unexecuted_notional": 30.25,
                },
            ],
        },
    )
    _write_json(
        run / "execution_results.json",
        {
            "trade_date": TRADE_DATE,
            "candidate_trade_lifecycle_artifact": f"outputs/runs/{RUN_ID}/audit/candidate_trade_lifecycle_{TRADE_DATE}.json",
            "buying_power_at_buy_decision": 250.0,
        },
    )
    _write_json(run / "execution_payload.json", {"trade_date": TRADE_DATE, "price_source": "alpaca_latest_trade"})
    _write_json(
        run / "broker" / f"post_sell_rebudget_{TRADE_DATE}.json",
        {
            "schema_version": "post_sell_rebudget.v1",
            "enabled": True,
            "status": "APPLIED",
            "target_cash_weight": 0.05,
            "post_sell_buying_power": 250.0,
            "reason_codes": ["post_sell_rebudget_capital_clipped"],
        },
    )
    _write_json(run / "broker" / f"posttrade_reconciliation_{TRADE_DATE}.json", {"status": "OK_RECONCILED"})
    _write_json(
        run / "broker" / "posttrade_positions.json",
        {"positions": [{"symbol": "AAA", "qty": "2", "market_value": "100.00", "weight": 0.10}]},
    )
    return tmp_path


def test_fr105_contract_generation_with_candidate_lifecycle_rollups(tmp_path: Path) -> None:
    repo_root = _repo_fixture(tmp_path)

    out_path, contract = write_fr105_replay_contract(
        repo_root=repo_root,
        trade_date=TRADE_DATE,
        run_id=RUN_ID,
        generated_at="2026-06-25T16:00:00Z",
        git_sha="testsha",
    )

    assert out_path == repo_root / "outputs" / "research" / "fr_105" / RUN_ID / "global_optimizer_replay_contract.json"
    assert contract["validation_status"]["status"] == "PASS"
    assert contract["metadata"]["mode"] == "research_only"
    assert contract["metadata"]["fr_id"] == "FR-105"
    assert contract["source_artifacts"]["candidate_trade_lifecycle_path"].endswith(
        f"audit/candidate_trade_lifecycle_{TRADE_DATE}.json"
    )
    assert contract["source_artifacts"]["price_source"] == "alpaca_latest_trade"
    assert contract["execution_residuals"] == {
        "planned_candidates": 3,
        "executable_candidates": 2,
        "intended_orders": 2,
        "submitted_orders": 2,
        "filled_orders": 1,
        "suppressed_count": 1,
        "clipped_count": 1,
        "suppression_reason_counts": {"min_notional_filter": 1},
        "clipping_reason_counts": {"post_sell_rebudget_capital_clipped": 1},
        "estimated_unexecuted_notional_total": 101.75,
    }
    assert contract["constraints_snapshot"]["max_single_name_weight"] == 0.20
    assert contract["constraints_snapshot"]["turnover_cap"] == 0.95
    assert contract["constraints_snapshot"]["min_trade_dollars"] == 100.0
    assert contract["constraints_snapshot"]["buying_power_available"] == 250.0
    assert contract["current_portfolio"]["positions_count"] == 1

    bbb = next(row for row in contract["sleeve_candidates"] if row["ticker"] == "BBB")
    assert bbb["reason_excluded"] == "min_notional_filter"
    assert bbb["sleeve_id"] is None
    assert bbb["expected_alpha"] is None
    assert bbb["source_artifact_path"].endswith(f"candidate_trade_lifecycle_{TRADE_DATE}.json")


def test_missing_optional_artifacts_use_explicit_null_or_unavailable(tmp_path: Path) -> None:
    contract = build_fr105_replay_contract(
        repo_root=tmp_path,
        trade_date=TRADE_DATE,
        run_id="missing-run",
        git_sha="unavailable",
    )

    assert contract["metadata"]["generated_at"] == "unavailable"
    assert contract["validation_status"]["status"] == "PASS"
    assert contract["source_artifacts"]["candidate_trade_lifecycle_path"] is None
    assert contract["source_artifacts"]["price_source"] == "unavailable"
    assert contract["source_artifacts"]["sleeve_artifacts"] == []
    assert contract["sleeve_candidates"] == []
    assert contract["current_portfolio"]["positions_count"] is None
    assert contract["constraints_snapshot"]["liquidity_constraints"] == "unavailable"
    assert contract["constraints_snapshot"]["sector_caps"] is None
    assert contract["execution_residuals"]["planned_candidates"] is None


def test_validator_rejects_malformed_contracts(tmp_path: Path) -> None:
    contract = build_fr105_replay_contract(
        repo_root=tmp_path,
        trade_date=TRADE_DATE,
        generated_at="2026-06-25T16:00:00Z",
        git_sha="unavailable",
    )
    bad = json.loads(json.dumps(contract))
    bad["metadata"]["mode"] = "paper"
    bad["metadata"]["production_execution_modules_invoked"] = ["paper.paper_broker"]
    bad["source_artifacts"]["sleeve_artifacts"] = "not-a-list"
    bad["sleeve_candidates"] = {}
    bad["source_artifacts"]["candidate_trade_lifecycle_path"] = ""
    del bad["provenance_schema_version"]

    result = validate_fr105_replay_contract(bad)

    assert result.status == "FAIL"
    assert "MODE_NOT_RESEARCH_ONLY" in result.findings
    assert "PRODUCTION_EXECUTION_MODULES_INVOKED" in result.findings
    assert "PROHIBITED_PRODUCTION_MODULES:paper.paper_broker" in result.findings
    assert "MALFORMED_LIST:source_artifacts.sleeve_artifacts" in result.findings
    assert "MALFORMED_LIST:sleeve_candidates" in result.findings
    assert "MISSING_PROVENANCE_SCHEMA_VERSION" in result.findings
    assert any(item.startswith("EMPTY_STRING_VALUES:") for item in result.findings)


def test_builder_does_not_import_production_trading_modules(tmp_path: Path) -> None:
    before = set(sys.modules)

    write_fr105_replay_contract(
        repo_root=_repo_fixture(tmp_path),
        trade_date=TRADE_DATE,
        run_id=RUN_ID,
        generated_at="2026-06-25T16:00:00Z",
        git_sha="testsha",
    )

    newly_imported = set(sys.modules) - before
    prohibited = {
        name
        for name in newly_imported
        for module in PROHIBITED_PRODUCTION_MODULES
        if name == module or name.startswith(module + ".")
    }
    assert prohibited == set()

from __future__ import annotations

from pathlib import Path

from core.construction_provenance import UNAVAILABLE, build_construction_provenance, write_construction_provenance


def _row(payload: dict, ticker: str) -> dict:
    for row in payload["rows"]:
        if row["ticker"] == ticker:
            return row
    raise AssertionError(f"missing construction provenance row for {ticker}")


def test_construction_provenance_joins_artifact_backed_fields(tmp_path: Path) -> None:
    trade_date = "2026-06-26"
    run_root = tmp_path / "outputs" / "runs" / "run-1"
    signals = {
        "signals": [
            {"ticker": "AAA", "target_weight": 0.08, "sleeve": "sleeve_trend"},
            {"ticker": "BBB", "target_weight": 0.04, "sleeve": "sleeve_quality", "raw_score": 0.04},
            {
                "ticker": "DDD",
                "target_weight": 0.03,
                "sleeve": "sleeve_mean_reversion",
                "raw_score": 0.83,
                "raw_score_source": "mean_reversion_model_score",
            },
        ]
    }
    current_positions = {
        "positions": [
            {"ticker": "AAA", "current_weight": 0.05},
            {"ticker": "CCC", "current_weight": 0.06},
        ]
    }
    planned_payload = {
        "min_trade_dollars": 100.0,
        "cash_target_weight": 0.05,
        "risk_meta": {"turnover_scaled": True, "turnover_scale": 0.5},
        "trades": [
            {"ticker": "AAA", "side": "BUY", "notional": 300.0},
            {"ticker": "BBB", "side": "BUY", "notional": 80.0},
            {"ticker": "CCC", "side": "SELL", "notional": 600.0, "reason": "removed_from_targets"},
        ],
    }
    lifecycle = {
        "candidates": [
            {
                "ticker": "AAA",
                "side": "BUY",
                "sleeve_id": "sleeve_trend",
                "candidate_rank": 2,
                "capital_rank": 1,
                "conviction_score": 0.91,
                "submitted": True,
                "target_weight": 0.08,
                "current_weight": 0.05,
                "delta_notional": 300.0,
            },
            {
                "ticker": "BBB",
                "side": "BUY",
                "sleeve_id": "sleeve_quality",
                "candidate_rank": 8,
                "submitted": False,
                "suppression_or_clipping_reason": "min_notional",
                "decision_stage": "executable_filter",
            },
        ]
    }

    artifact_path, payload = write_construction_provenance(
        run_root=run_root,
        trade_date=trade_date,
        run_id="run-1",
        signals_payload=signals,
        planned_payload=planned_payload,
        candidate_lifecycle_payload=lifecycle,
        current_positions_payload=current_positions,
        source_artifact_paths={
            "signals": "signals/2026-06-26.json",
            "planned_payload": "outputs/precompute/2026-06-26/planned_execution_payload.json",
            "candidate_trade_lifecycle": "outputs/runs/run-1/audit/candidate_trade_lifecycle_2026-06-26.json",
            "current_positions": "outputs/runs/run-1/broker/pretrade_positions.json",
        },
    )

    assert artifact_path == run_root / "audit" / "construction_provenance_2026-06-26.json"
    assert payload["mode"] == "REPORTING_ONLY"
    assert payload["trading_behavior_changed"] is False
    assert payload["summary"]["row_count"] == 4
    assert payload["summary"]["score_backed_count"] == 2

    aaa = _row(payload, "AAA")
    assert aaa["sleeve_sources"] == ["sleeve_trend"]
    assert aaa["sleeve_local_rank"] == 2
    assert aaa["global_rank"] == 1
    assert aaa["raw_score"] == 0.91
    assert aaa["score_source"] == "candidate_trade_lifecycle.conviction_score"
    assert aaa["current_weight"] == 0.05
    assert aaa["final_target_weight"] == 0.08
    assert aaa["field_sources"]["current_weight"] == "current_positions.current_weight"
    assert aaa["field_sources"]["final_target_weight"] == "signals.target_weight"
    assert aaa["trade_delta_weight"] == 0.03
    assert aaa["trade_delta_notional"] == 300.0
    assert aaa["construction_action"] == "retained"

    bbb = _row(payload, "BBB")
    assert bbb["raw_score"] == UNAVAILABLE
    assert bbb["score_source"] == UNAVAILABLE
    assert bbb["field_sources"]["raw_score"] == UNAVAILABLE
    assert bbb["construction_action"] == "skipped"
    assert bbb["suppression_block_reason"] == "min_notional"
    assert "executable_filter:min_notional" in bbb["active_constraints"]
    assert "min_trade_dollars:100.0" in bbb["active_constraints"]

    ccc = _row(payload, "CCC")
    assert ccc["current_weight"] == 0.06
    assert ccc["final_target_weight"] == 0.0
    assert ccc["construction_action"] == "removed"

    ddd = _row(payload, "DDD")
    assert ddd["current_weight"] == 0.0
    assert ddd["final_target_weight"] == 0.03
    assert ddd["raw_score"] == 0.83
    assert ddd["score_source"] == "mean_reversion_model_score"
    assert ddd["construction_action"] == "added"


def test_construction_provenance_missing_artifacts_degrade_to_no_rows(tmp_path: Path) -> None:
    payload = build_construction_provenance(
        trade_date="2026-06-26",
        run_id="run-missing",
        source_artifact_paths={
            "signals": "missing/signals.json",
            "planned_payload": "missing/planned_execution_payload.json",
            "candidate_trade_lifecycle": "missing/candidate_trade_lifecycle.json",
            "current_positions": "missing/current_positions.json",
        },
        repo_root=tmp_path,
    )

    assert payload["summary"]["status"] == "NO_ROWS"
    assert payload["summary"]["row_count"] == 0
    assert payload["rows"] == []
    assert payload["source_artifacts"]["signals"]["status"] == "MISSING"
    assert payload["source_artifacts"]["planned_payload"]["status"] == "MISSING"
    assert payload["source_artifacts"]["candidate_trade_lifecycle"]["status"] == "MISSING"
    assert payload["source_artifacts"]["current_positions"]["status"] == "MISSING"


def test_construction_provenance_source_labels_follow_fallback_artifacts() -> None:
    payload = build_construction_provenance(
        trade_date="2026-06-26",
        run_id="run-lifecycle-only",
        candidate_lifecycle_payload={
            "candidates": [
                {
                    "ticker": "AAA",
                    "target_weight": 0.02,
                    "current_weight": 0.01,
                    "raw_score": 0.02,
                    "raw_score_source": "target_weight",
                },
                {
                    "ticker": "BBB",
                    "target_weight": 0.03,
                    "current_weight": 0.01,
                    "raw_score": 0.03,
                    "raw_score_source": "allocation_weight",
                }
            ]
        },
    )

    aaa = _row(payload, "AAA")
    assert aaa["final_target_weight"] == 0.02
    assert aaa["current_weight"] == 0.01
    assert aaa["field_sources"]["final_target_weight"] == "candidate_trade_lifecycle.target_weight"
    assert aaa["field_sources"]["current_weight"] == "candidate_trade_lifecycle.current_weight"
    assert aaa["raw_score"] == UNAVAILABLE
    assert aaa["field_sources"]["raw_score"] == UNAVAILABLE

    bbb = _row(payload, "BBB")
    assert bbb["raw_score"] == UNAVAILABLE
    assert bbb["field_sources"]["raw_score"] == UNAVAILABLE


def test_construction_provenance_failed_current_positions_do_not_imply_zero() -> None:
    payload = build_construction_provenance(
        trade_date="2026-06-26",
        run_id="run-current-missing",
        signals_payload={"signals": [{"ticker": "AAA", "target_weight": 0.02}]},
        current_positions_payload={"ok": False, "positions": []},
    )

    aaa = _row(payload, "AAA")
    assert aaa["final_target_weight"] == 0.02
    assert aaa["current_weight"] == UNAVAILABLE
    assert aaa["trade_delta_weight"] == UNAVAILABLE
    assert aaa["field_sources"]["current_weight"] == UNAVAILABLE

from __future__ import annotations

import json
from pathlib import Path

from core.strategy_registry import active_shadow_security_selection_ids
from scripts.caerus_daily_health_check import build_health_check, render_console, write_artifacts


TRADE_DATE = "2026-04-28"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))


def _write_base_artifacts(root: Path, *, reconciliation: dict | None = None, vix: dict | None = None) -> None:
    shadow_latest = root / "outputs" / "shadow_candidates" / "latest"
    shadow_latest.mkdir(parents=True, exist_ok=True)
    (shadow_latest / "comparison.md").write_text(
        "\n".join(
            [
                "# Shadow Candidates Comparison",
                "",
                "## Trade Date",
                f"- {TRADE_DATE}",
                "",
                "## Executive Summary",
                "- Decision-useful chain.",
                "",
                "## Performance Scoreboard",
                "| Strategy | Data Status |",
                "|---|---|",
                "| Caerus Polaris | OK |",
                "| Caerus Orion | OK |",
                "| Caerus Lyra | OK |",
                "| SPY | OK |",
                "",
                "## Relative Performance",
                "- Polaris excess vs SPY: 0.00%",
                "",
                "## Chain Health",
                "- Any NO_DATA: NO",
            ]
        )
    )
    _write_json(
        shadow_latest / "shadow_evaluation.json",
        {
            "trade_date": TRADE_DATE,
            "benchmark_symbol": "SPY",
            "strategies": {
                **{
                    strategy_id: {
                        "data_status": "OK",
                        "status": "OK",
                        "rolling_count_of_valid_days": 6,
                    }
                    for strategy_id in active_shadow_security_selection_ids()
                },
                "spy_benchmark": {
                    "data_status": "OK",
                    "status": "OK",
                    "rolling_count_of_valid_days": 6,
                },
            },
        },
    )
    _write_json(
        root / "outputs" / "vix_regime" / "regime_current.json",
        vix or {"date": TRADE_DATE, "vix": 21.5, "regime": "ELEVATED", "source": "fixture", "fallback_used": False},
    )
    _write_json(
        root / "outputs" / "precompute" / TRADE_DATE / "daily_snapshot.json",
        {"trade_date": TRADE_DATE, "market_analyzer": {"vix": 21.5, "regime": "ELEVATED"}},
    )
    _write_json(
        root / "outputs" / "precompute" / TRADE_DATE / "signals.json",
        {
            "snapshot_date": TRADE_DATE,
            "strategy_identity": {
                "live_strategy_id": "growth_engine_v4",
                "shadow_baseline_strategy": "caerus_polaris",
                "live_tracks_shadow_baseline": False,
            },
            "signals": [{"ticker": "AAA", "target_weight": 1.0}],
        },
    )
    _write_json(
        root / "outputs" / "reconciliation" / "live_vs_shadow" / "latest" / "live_vs_shadow_reconciliation.json",
        reconciliation
        or {
            "trade_date": TRADE_DATE,
            "generated_at": "2026-04-28T20:00:00Z",
            "classification": "RECONCILED",
            "status": "RECONCILED",
            "reason_codes": ["RETURNS_RECONCILED", "HOLDINGS_RECONCILED"],
            "live_strategy_id": "growth_engine_v4",
            "shadow_baseline_strategy": "caerus_polaris",
            "strategy_alignment": {
                "live_strategy_id": "growth_engine_v4",
                "shadow_baseline_strategy": "caerus_polaris",
                "status": "ALIGNED",
            },
        },
    )
    run_root = root / "outputs" / "runs" / "run-health"
    _write_json(
        root / "outputs" / "latest_run.json",
        {
            "run_id": "run-health",
            "trade_date": TRADE_DATE,
            "mode": "PAPER",
            "run_root": str(run_root),
            "status": "success",
            "workflow_stage": "execution",
        },
    )
    _write_json(
        run_root / "operator_summary.json",
        {
            "trade_date": TRADE_DATE,
            "terminal_status": "success",
            "operator_execution_status": "executed",
            "execution_integrity_status": "OK",
        },
    )
    _write_json(
        run_root / "execution_payload.json",
        {
            "trade_date": TRADE_DATE,
            "execution_source": "planned_payload_exact",
            "planning_price_basis": "PREV_CLOSE",
            "pricing_asof": "2026-04-27",
            "execution_price_requirement": "PRECOMPUTE_VALIDATED",
            "price_freshness_scope": "precompute_bundle",
        },
    )
    _write_json(
        run_root / "execution_timeline.json",
        {
            "trade_date": TRADE_DATE,
            "event_count": 15,
            "provenance": {
                "execution_source": "planned_payload_exact",
                "planning_price_basis": "PREV_CLOSE",
                "pricing_asof": "2026-04-27",
                "execution_price_requirement": "PRECOMPUTE_VALIDATED",
                "price_freshness_scope": "precompute_bundle",
            },
        },
    )
    _write_json(run_root / "audit" / "execution_integrity.json", {"status": "OK", "findings": []})


def test_strategy_identity_warns_when_live_target_does_not_track_approved_strategy(
    tmp_path: Path,
) -> None:
    _write_base_artifacts(tmp_path)
    signals_path = (
        tmp_path / "outputs" / "precompute" / TRADE_DATE / "signals.json"
    )
    signals = json.loads(signals_path.read_text(encoding="utf-8"))
    signals["strategy_identity"].update(
        {
            "execution_target_strategy_id": "growth_engine_v4",
            "live_pilot_governed_strategy_id": "caerus_orion",
            "live_pilot_mapping_status": "NOT_TRACKING_GOVERNED_STRATEGY",
            "live_pilot_tracks_approved_strategy": False,
        }
    )
    _write_json(signals_path, signals)

    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)

    identity = next(
        check for check in payload["checks"] if check["name"] == "Strategy identity"
    )
    assert identity["status"] == "YELLOW"
    assert "LIVE_PILOT_STRATEGY_TARGET_MISMATCH" in identity["reason_codes"]


def _status(payload: dict, name: str) -> str:
    return next(check["status"] for check in payload["checks"] if check["name"] == name)


def test_green_case(tmp_path: Path) -> None:
    _write_base_artifacts(tmp_path)
    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)
    assert payload["overall_status"] == "GREEN"
    assert payload["recommended_action"] == "HOLD_NO_ACTION"
    assert _status(payload, "VIX/regime") == "GREEN"
    assert _status(payload, "Shadow performance report") == "GREEN"
    assert _status(payload, "Execution timeline provenance") == "GREEN"
    assert payload["equality_gate_observe"]["status"] == "unavailable"
    assert "Caerus Daily Health Check" in render_console(payload)


def test_equality_gate_divergence_is_advisory_not_health_degrading(tmp_path: Path) -> None:
    _write_base_artifacts(tmp_path)
    _write_json(
        tmp_path / "outputs" / "runs" / "run-health" / "equality_gate.json",
        {
            "decision": "WOULD_HALT_HASH_MISMATCH",
            "would_block": True,
            "hashes_equal": False,
            "pricing_asof_match": True,
            "execution_source": "planned_payload_exact",
        },
    )

    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)

    assert payload["overall_status"] == "GREEN"
    assert payload["equality_gate_observe"]["status"] == "divergence_observed"
    assert payload["equality_gate_observe"]["decision"] == "WOULD_HALT_HASH_MISMATCH"


def test_execution_timeline_missing_is_yellow_operator_visibility(tmp_path: Path) -> None:
    _write_base_artifacts(tmp_path)
    (tmp_path / "outputs" / "runs" / "run-health" / "execution_timeline.json").unlink()

    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)
    check = next(item for item in payload["checks"] if item["name"] == "Execution timeline provenance")

    assert payload["overall_status"] == "YELLOW"
    assert check["status"] == "YELLOW"
    assert "EXECUTION_TIMELINE_MISSING" in check["reason_codes"]
    assert "timeline_present=false" in check["summary"]


def test_yellow_not_aligned_case(tmp_path: Path) -> None:
    _write_base_artifacts(
        tmp_path,
        reconciliation={
            "trade_date": TRADE_DATE,
            "generated_at": "2026-04-28T20:00:00Z",
            "classification": "NOT_ALIGNED",
            "status": "NOT_ALIGNED",
            "reason_codes": ["DIFFERENT_STRATEGY_PATH"],
            "live_strategy_id": "growth_engine_v4",
            "shadow_baseline_strategy": "caerus_polaris",
        },
    )
    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)
    assert payload["overall_status"] == "YELLOW"
    assert _status(payload, "Live vs shadow reconciliation") == "YELLOW"
    assert payload["recommended_action"] == "HOLD_MONITOR"


def test_yellow_not_comparable_explicit_reasons(tmp_path: Path) -> None:
    _write_base_artifacts(
        tmp_path,
        reconciliation={
            "trade_date": TRADE_DATE,
            "generated_at": "2026-04-28T20:00:00Z",
            "classification": "NOT_COMPARABLE",
            "status": "NOT_COMPARABLE",
            "reason_codes": ["INSUFFICIENT_HISTORY", "BENCHMARK_MISSING"],
            "live_strategy_id": "growth_engine_v4",
            "shadow_baseline_strategy": "caerus_polaris",
        },
    )
    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)
    assert payload["overall_status"] == "YELLOW"
    assert _status(payload, "Live vs shadow reconciliation") == "YELLOW"


def test_aligned_initializing_is_green_only_with_lineage_and_attainment(tmp_path: Path) -> None:
    _write_base_artifacts(
        tmp_path,
        reconciliation={
            "trade_date": TRADE_DATE,
            "classification": "ALIGNED_INITIALIZING",
            "status": "ALIGNED_INITIALIZING",
            "reason_codes": [
                "INSUFFICIENT_HISTORY",
                "IMMUTABLE_LINEAGE_VERIFIED",
                "TARGET_ATTAINED",
                "PERFORMANCE_HISTORY_INITIALIZING",
            ],
            "live_strategy_id": "caerus_orion",
            "shadow_baseline_strategy": "caerus_orion",
            "strategy_alignment": {
                "live_strategy_id": "caerus_orion",
                "shadow_baseline_strategy": "caerus_orion",
                "status": "ALIGNED",
            },
            "immutable_lineage": {"verified": True},
        },
    )
    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)
    assert _status(payload, "Live vs shadow reconciliation") == "GREEN"
    assert _status(payload, "Strategy identity") == "GREEN"
    assert payload["overall_status"] == "GREEN"


def test_yellow_price_cache_stale_from_shadow_sidecars(tmp_path: Path) -> None:
    _write_base_artifacts(tmp_path)
    shadow_latest = tmp_path / "outputs" / "shadow_candidates" / "latest"
    (shadow_latest / "comparison.md").write_text(
        "\n".join(
            [
                "# Shadow Candidates Comparison",
                "## Executive Summary",
                "- Chain health: NO_DATA",
                "## Performance Scoreboard",
                "| Strategy | Data Status |",
                "|---|---|",
                "| Caerus Polaris | NO_DATA |",
                "| Caerus Orion | NO_DATA |",
                "| Caerus Lyra | NO_DATA |",
                "| SPY | NO_DATA |",
                "## Chain Health",
                "- Any NO_DATA: YES",
            ]
        )
    )
    _write_json(shadow_latest / "comparison.json", {"trade_date": TRADE_DATE, "status": "NO_DATA", "reason_code": "PRICE_CACHE_STALE"})
    _write_json(
        tmp_path / "outputs" / "shadow_candidates" / TRADE_DATE / "shadow_performance.json",
        {"trade_date": TRADE_DATE, "data_status": "NO_DATA", "data_reason": "PRICE_CACHE_STALE", "strategies": {}},
    )
    evaluation = json.loads((shadow_latest / "shadow_evaluation.json").read_text())
    for row in evaluation["strategies"].values():
        row["data_status"] = "NO_DATA"
        row.pop("data_reason", None)
    _write_json(shadow_latest / "shadow_evaluation.json", evaluation)

    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)
    assert payload["overall_status"] == "YELLOW"
    assert _status(payload, "Shadow artifacts") == "YELLOW"
    assert _status(payload, "Shadow performance report") == "YELLOW"


def test_red_missing_shadow_latest(tmp_path: Path) -> None:
    _write_base_artifacts(tmp_path)
    (tmp_path / "outputs" / "shadow_candidates" / "latest" / "comparison.md").unlink()
    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)
    assert payload["overall_status"] == "RED"
    assert _status(payload, "Shadow artifacts") == "RED"
    assert payload["recommended_action"] == "INVESTIGATE_BEFORE_TRADING_CHANGES"


def test_red_ambiguous_unknown_regime(tmp_path: Path) -> None:
    _write_base_artifacts(tmp_path, vix={"date": TRADE_DATE, "vix": "?", "regime": "UNKNOWN"})
    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)
    assert payload["overall_status"] == "RED"
    assert _status(payload, "VIX/regime") == "RED"


def test_latest_publishing(tmp_path: Path) -> None:
    _write_base_artifacts(tmp_path)
    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)
    dated_json, dated_md, latest_json, latest_md = write_artifacts(payload, root=tmp_path)

    assert dated_json == tmp_path / "outputs" / "health" / "caerus_daily_health_check" / TRADE_DATE / "health_check.json"
    assert dated_md.exists()
    assert latest_json.exists()
    assert latest_md.exists()
    latest_payload = json.loads(latest_json.read_text())
    assert latest_payload["trade_date"] == TRADE_DATE
    assert latest_payload["overall_status"] == "GREEN"

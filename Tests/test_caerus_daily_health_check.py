from __future__ import annotations

import json
from pathlib import Path

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
                "caerus_polaris": {"data_status": "OK", "status": "OK", "rolling_count_of_valid_days": 6},
                "caerus_orion": {"data_status": "OK", "status": "OK", "rolling_count_of_valid_days": 6},
                "caerus_lyra": {"data_status": "OK", "status": "OK", "rolling_count_of_valid_days": 6},
                "spy_benchmark": {"data_status": "OK", "status": "OK", "rolling_count_of_valid_days": 6},
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


def _status(payload: dict, name: str) -> str:
    return next(check["status"] for check in payload["checks"] if check["name"] == name)


def test_green_case(tmp_path: Path) -> None:
    _write_base_artifacts(tmp_path)
    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)
    assert payload["overall_status"] == "GREEN"
    assert payload["recommended_action"] == "HOLD_NO_ACTION"
    assert _status(payload, "VIX/regime") == "GREEN"
    assert _status(payload, "Shadow performance report") == "GREEN"
    assert "Caerus Daily Health Check" in render_console(payload)


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

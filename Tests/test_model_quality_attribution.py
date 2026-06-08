from __future__ import annotations

import json
from pathlib import Path

from research_registry.research.model_quality_attribution import build_model_quality_attribution


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _write_position_sources(root: Path, trade_date: str, positions: list[dict]) -> None:
    _write_json(
        root / "outputs" / "attribution" / trade_date / "position_attribution.json",
        {"date": trade_date, "schema_version": "test", "positions": positions},
    )
    _write_json(
        root / "outputs" / "attribution" / trade_date / "attribution_summary.json",
        {
            "date": trade_date,
            "top_contributor_per_strategy": {"caerus_polaris": {"symbol": "AAA"}},
            "top_detractor_per_strategy": {"caerus_polaris": {"symbol": "BBB"}},
        },
    )


def _write_decision_sources(root: Path, trade_date: str, decisions: list[dict]) -> None:
    base = root / "outputs" / "decision_attribution" / trade_date
    _write_json(base / "decision_attribution.json", {"date": trade_date, "schema_version": "test", "decisions": decisions})
    _write_json(
        base / "strategy_decision_summary.json",
        {"date": trade_date, "strategies": [{"strategy": "caerus_polaris", "decisions_analyzed": len(decisions)}], "reason_codes": ["ok"]},
    )
    _write_json(
        base / "signal_outcome_summary.json",
        {"date": trade_date, "signals": [{"signal_name": "momentum_score", "observations": len(decisions), "hit_rate": 0.5}], "reason_codes": ["ok"]},
    )


def test_model_quality_attribution_populated_input(tmp_path: Path) -> None:
    trade_date = "2026-06-02"
    _write_position_sources(
        tmp_path,
        trade_date,
        [
            {"strategy": "caerus_polaris", "symbol": "BBB", "weight": 0.4, "return_pct": -0.01, "pnl_contribution_pct": -0.004, "reason_codes": ["ok"]},
            {"strategy": "caerus_polaris", "symbol": "AAA", "weight": 0.6, "return_pct": 0.02, "pnl_contribution_pct": 0.012, "reason_codes": ["ok"]},
        ],
    )
    _write_decision_sources(
        tmp_path,
        trade_date,
        [
            {"strategy": "caerus_polaris", "symbol": "AAA", "rank": 1, "weight": 0.6, "realized_return": 0.02, "pnl_contribution": 0.012, "signal_snapshot": {"momentum_score": 1.0}, "reason_codes": ["ok"]},
            {"strategy": "caerus_polaris", "symbol": "BBB", "rank": 2, "weight": 0.4, "realized_return": -0.01, "pnl_contribution": -0.004, "signal_snapshot": {"momentum_score": 0.5}, "reason_codes": ["ok"]},
        ],
    )

    payload = build_model_quality_attribution(trade_date=trade_date, repo_root=tmp_path)

    assert payload["available"] is True
    assert payload["strategy_return_contribution"]["strategies"][0]["strategy"] == "caerus_polaris"
    assert payload["strategy_return_contribution"]["strategies"][0]["pnl_contribution"] == 0.008
    assert payload["symbol_return_contribution"]["symbols"][0]["symbol"] == "AAA"
    assert payload["entry_exit_contribution"]["top_entries"][0]["symbol"] == "AAA"
    assert (tmp_path / "outputs" / "model_quality" / trade_date / "attribution_quality.json").exists()
    assert (tmp_path / "outputs" / "model_quality" / trade_date / "attribution_quality.md").exists()


def test_model_quality_attribution_missing_sources_degrades(tmp_path: Path) -> None:
    payload = build_model_quality_attribution(trade_date="2026-06-02", repo_root=tmp_path)

    assert payload["available"] is False
    assert payload["status"] == "NO_DATA"
    assert "position_attribution_missing" in payload["reason_codes"]
    assert "decision_attribution_missing" in payload["reason_codes"]


def test_model_quality_attribution_empty_positions_and_decisions(tmp_path: Path) -> None:
    trade_date = "2026-06-02"
    _write_position_sources(tmp_path, trade_date, [])
    _write_decision_sources(tmp_path, trade_date, [])

    payload = build_model_quality_attribution(trade_date=trade_date, repo_root=tmp_path)

    assert payload["status"] == "PARTIAL"
    assert "EMPTY_POSITION_ATTRIBUTION" in payload["reason_codes"]
    assert "EMPTY_DECISION_ATTRIBUTION" in payload["reason_codes"]
    assert payload["symbol_return_contribution"]["symbols"] == []


def test_model_quality_attribution_does_not_use_future_sources(tmp_path: Path) -> None:
    _write_position_sources(tmp_path, "2026-06-01", [{"strategy": "caerus_polaris", "symbol": "AAA", "weight": 1.0, "return_pct": 0.01, "pnl_contribution_pct": 0.01}])
    _write_position_sources(tmp_path, "2026-06-03", [{"strategy": "caerus_polaris", "symbol": "ZZZ", "weight": 1.0, "return_pct": 0.99, "pnl_contribution_pct": 0.99}])

    payload = build_model_quality_attribution(trade_date="2026-06-02", repo_root=tmp_path)

    position_source = [row for row in payload["source_statuses"] if row["name"] == "position_attribution"][0]
    assert position_source["source_date"] == "2026-06-01"
    assert payload["symbol_return_contribution"]["symbols"][0]["symbol"] == "AAA"


def test_model_quality_attribution_deterministic_symbol_ordering(tmp_path: Path) -> None:
    trade_date = "2026-06-02"
    _write_position_sources(
        tmp_path,
        trade_date,
        [
            {"strategy": "caerus_polaris", "symbol": "BBB", "weight": 0.5, "return_pct": 0.02, "pnl_contribution_pct": 0.01},
            {"strategy": "caerus_polaris", "symbol": "AAA", "weight": 0.5, "return_pct": 0.02, "pnl_contribution_pct": 0.01},
        ],
    )

    payload = build_model_quality_attribution(trade_date=trade_date, repo_root=tmp_path)

    assert [row["symbol"] for row in payload["symbol_return_contribution"]["symbols"]] == ["AAA", "BBB"]

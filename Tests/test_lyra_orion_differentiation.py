from __future__ import annotations

import csv
import json
from pathlib import Path

from research_registry.research.lyra_orion_differentiation import build_lyra_orion_differentiation


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _snapshot(strategy: str, holdings: list[tuple[str, float]], trade_date: str = "2026-06-08") -> dict:
    return {
        "strategy_slug": strategy,
        "trade_date": trade_date,
        "effective_trade_date": trade_date,
        "holdings": [
            {"ticker": ticker, "target_weight": weight, "momentum_rank": idx + 1, "momentum_score": 10 - idx}
            for idx, (ticker, weight) in enumerate(holdings)
        ],
        "target_weights": {ticker: weight for ticker, weight in holdings},
        "expected_turnover": 0.0,
    }


def _write_base_inputs(tmp_path: Path, lyra_holdings: list[tuple[str, float]], orion_holdings: list[tuple[str, float]]) -> None:
    trade_date = "2026-06-08"
    _write_csv(
        tmp_path / "data" / "universe.csv",
        [
            {"ticker": "AAA", "sector": "Tech"},
            {"ticker": "BBB", "sector": "Health"},
            {"ticker": "CCC", "sector": "Industrials"},
        ],
    )
    _write_json(tmp_path / "outputs" / "shadow_candidates" / trade_date / "caerus_lyra.json", _snapshot("caerus_lyra", lyra_holdings))
    _write_json(tmp_path / "outputs" / "shadow_candidates" / trade_date / "caerus_orion.json", _snapshot("caerus_orion", orion_holdings))
    _write_json(tmp_path / "outputs" / "shadow_candidates" / "2026-06-01" / "caerus_lyra.json", _snapshot("caerus_lyra", [("AAA", 1.0)], "2026-06-01"))
    _write_json(tmp_path / "outputs" / "shadow_candidates" / "2026-06-01" / "caerus_orion.json", _snapshot("caerus_orion", [("CCC", 1.0)], "2026-06-01"))
    _write_json(
        tmp_path / "outputs" / "model_quality" / trade_date / "model_tournament.json",
        {
            "strategies": [
                {"strategy": "caerus_lyra", "metrics": {"total_return": 2.0, "excess_return_vs_spy": 1.0, "coverage_days": 20, "turnover": 0.1}},
                {"strategy": "caerus_orion", "metrics": {"total_return": 1.0, "excess_return_vs_spy": 0.4, "coverage_days": 20, "turnover": 0.2}},
            ]
        },
    )
    _write_json(
        tmp_path / "outputs" / "shadow_candidates" / trade_date / "shadow_evaluation.json",
        {"strategies": {"caerus_lyra": {"daily_return": 0.03, "excess_return_vs_spy": 0.02}, "caerus_orion": {"daily_return": 0.01, "excess_return_vs_spy": 0.0}}},
    )
    _write_json(
        tmp_path / "outputs" / "attribution" / trade_date / "position_attribution.json",
        {
            "positions": [
                {"strategy": "caerus_lyra", "symbol": "BBB", "pnl_contribution_pct": 0.02},
                {"strategy": "caerus_orion", "symbol": "CCC", "pnl_contribution_pct": 0.01},
            ]
        },
    )
    _write_json(
        tmp_path / "outputs" / "decision_attribution" / trade_date / "decision_attribution.json",
        {
            "decisions": [
                {"strategy": "caerus_lyra", "symbol": "BBB", "pnl_contribution": 0.02},
                {"strategy": "caerus_orion", "symbol": "CCC", "pnl_contribution": 0.01},
            ]
        },
    )


def test_lyra_orion_differentiation_full_populated_comparison(tmp_path: Path) -> None:
    _write_base_inputs(tmp_path, [("AAA", 0.5), ("BBB", 0.5)], [("AAA", 0.5), ("CCC", 0.5)])

    payload = build_lyra_orion_differentiation(trade_date="2026-06-08", repo_root=tmp_path)

    assert payload["holdings_overlap_difference"]["common_symbols"] == ["AAA"]
    assert payload["holdings_overlap_difference"]["lyra_only_symbols"] == ["BBB"]
    assert payload["holdings_overlap_difference"]["orion_only_symbols"] == ["CCC"]
    assert payload["performance_spread"]["spread_lyra_minus_orion"]["current_day_return"] == 0.02
    assert payload["decision_grade_flag"] is False


def test_lyra_orion_differentiation_missing_lyra_history(tmp_path: Path) -> None:
    _write_json(tmp_path / "outputs" / "shadow_candidates" / "2026-06-08" / "caerus_orion.json", _snapshot("caerus_orion", [("AAA", 1.0)]))

    payload = build_lyra_orion_differentiation(trade_date="2026-06-08", repo_root=tmp_path)

    assert "CAERUS_LYRA_SNAPSHOT_MISSING" in payload["reason_codes"]


def test_lyra_orion_differentiation_missing_orion_history(tmp_path: Path) -> None:
    _write_json(tmp_path / "outputs" / "shadow_candidates" / "2026-06-08" / "caerus_lyra.json", _snapshot("caerus_lyra", [("AAA", 1.0)]))

    payload = build_lyra_orion_differentiation(trade_date="2026-06-08", repo_root=tmp_path)

    assert "CAERUS_ORION_SNAPSHOT_MISSING" in payload["reason_codes"]


def test_lyra_orion_differentiation_no_overlap_case(tmp_path: Path) -> None:
    _write_base_inputs(tmp_path, [("AAA", 1.0)], [("CCC", 1.0)])

    payload = build_lyra_orion_differentiation(trade_date="2026-06-08", repo_root=tmp_path)

    assert "NO_HOLDINGS_OVERLAP" in payload["reason_codes"]


def test_lyra_orion_differentiation_identical_strategy_case(tmp_path: Path) -> None:
    _write_base_inputs(tmp_path, [("AAA", 1.0)], [("AAA", 1.0)])

    payload = build_lyra_orion_differentiation(trade_date="2026-06-08", repo_root=tmp_path)

    assert "IDENTICAL_STRATEGY_HOLDINGS" in payload["reason_codes"]


def test_lyra_orion_differentiation_stale_input_degrades(tmp_path: Path) -> None:
    _write_json(tmp_path / "outputs" / "shadow_candidates" / "2026-06-01" / "caerus_lyra.json", _snapshot("caerus_lyra", [("AAA", 1.0)], "2026-06-01"))
    _write_json(tmp_path / "outputs" / "shadow_candidates" / "2026-06-01" / "caerus_orion.json", _snapshot("caerus_orion", [("CCC", 1.0)], "2026-06-01"))

    payload = build_lyra_orion_differentiation(trade_date="2026-06-08", repo_root=tmp_path)

    assert "SOURCE_DATE_DIFFERS_FROM_TARGET" in payload["reason_codes"]


def test_lyra_orion_differentiation_deterministic_and_not_decision_grade(tmp_path: Path) -> None:
    _write_base_inputs(tmp_path, [("BBB", 0.5), ("AAA", 0.5)], [("CCC", 0.5), ("AAA", 0.5)])

    first = build_lyra_orion_differentiation(trade_date="2026-06-08", repo_root=tmp_path, write=False)
    second = build_lyra_orion_differentiation(trade_date="2026-06-08", repo_root=tmp_path, write=False)

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert first["evidence_sufficiency"]["decision_grade"] is False
    assert "NO_DECISION_GRADE_RECOMMENDATION" in first["evidence_sufficiency"]["reason_codes"]

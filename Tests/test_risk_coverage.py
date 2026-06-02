from __future__ import annotations

import json
from pathlib import Path

from research.risk_coverage import build_risk_coverage


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _write_universe(root: Path) -> None:
    path = root / "data" / "universe.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("ticker,sector\nAAA,Tech\nBBB,Tech\nCCC,Industrials\nDDD,Health\nEEE,Financials\n", encoding="utf-8")


def _write_shadow(root: Path, trade_date: str) -> None:
    _write_json(
        root / "outputs" / "shadow_candidates" / trade_date / "comparison.json",
        {
            "strategies": {
                "caerus_lyra": {"holdings": [{"ticker": "AAA", "target_weight": 0.3}, {"ticker": "BBB", "target_weight": 0.25}, {"ticker": "CCC", "target_weight": 0.2}, {"ticker": "DDD", "target_weight": 0.15}, {"ticker": "EEE", "target_weight": 0.1}]},
                "caerus_orion": {"holdings": [{"ticker": "AAA", "target_weight": 0.2}, {"ticker": "CCC", "target_weight": 0.2}, {"ticker": "DDD", "target_weight": 0.2}, {"ticker": "EEE", "target_weight": 0.2}, {"ticker": "BBB", "target_weight": 0.2}]},
            }
        },
    )


def _write_factor(root: Path, trade_date: str) -> None:
    _write_json(
        root / "outputs" / "attribution" / trade_date / "factor_exposure.json",
        {
            "date": trade_date,
            "strategies": {
                "caerus_lyra": {"market_beta": 1.1, "sector_exposure": {"weights": {"Tech": 0.55}}},
                "caerus_orion": {"market_beta": 0.9, "sector_exposure": {"weights": {"Tech": 0.4}}},
            },
        },
    )


def test_risk_coverage_populated_holdings_available(tmp_path):
    trade_date = "2026-06-02"
    _write_universe(tmp_path)
    _write_shadow(tmp_path, trade_date)
    _write_factor(tmp_path, trade_date)

    payload = build_risk_coverage(trade_date=trade_date, repo_root=tmp_path)

    assert payload["available"] is True
    assert payload["strategies_covered"] == ["caerus_lyra", "caerus_orion"]
    assert payload["position_count"] == 10
    assert payload["strategies"]["caerus_lyra"]["top3_concentration"] == 0.75
    assert payload["strategies"]["caerus_lyra"]["top10_concentration"] == 1.0
    assert (tmp_path / "outputs" / "research" / "risk_coverage" / trade_date / "risk_coverage.json").exists()


def test_risk_coverage_empty_holdings_unavailable(tmp_path):
    payload = build_risk_coverage(trade_date="2026-06-02", repo_root=tmp_path)

    assert payload["available"] is False
    assert "no_holdings" in payload["reason_codes"]
    assert "risk_coverage_unavailable" in payload["reason_codes"]


def test_risk_coverage_missing_sector_and_factor_degrades_gracefully(tmp_path):
    trade_date = "2026-06-02"
    _write_shadow(tmp_path, trade_date)

    payload = build_risk_coverage(trade_date=trade_date, repo_root=tmp_path)

    assert payload["available"] is True
    assert payload["confidence"] == "MEDIUM"
    assert "sector_lookup_missing" in payload["reason_codes"]
    assert "factor_exposure_missing" in payload["reason_codes"]


def test_risk_coverage_does_not_use_future_holdings(tmp_path):
    _write_universe(tmp_path)
    _write_shadow(tmp_path, "2026-06-03")

    payload = build_risk_coverage(trade_date="2026-06-02", repo_root=tmp_path)

    assert payload["available"] is False
    assert payload["holdings_source_date"] is None

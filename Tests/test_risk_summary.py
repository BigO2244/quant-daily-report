from __future__ import annotations

import json
from pathlib import Path

from research.review_packet import build_research_review_packet
from research.risk_summary import build_risk_summary


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _write_universe(root: Path) -> None:
    path = root / "data" / "universe.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "ticker,sector",
                "AAA,Technology",
                "BBB,Technology",
                "CCC,Industrials",
                "DDD,Financials",
                "EEE,Healthcare",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _write_holdings(root: Path, trade_date: str) -> None:
    _write_json(
        root / "outputs" / "portfolio_history" / trade_date / "holdings_snapshot.json",
        {
            "trade_date": trade_date,
            "strategies": {
                "caerus_orion": {
                    "holdings": [
                        {"ticker": "AAA", "target_weight": 0.30},
                        {"ticker": "BBB", "target_weight": 0.25},
                        {"ticker": "CCC", "target_weight": 0.20},
                        {"ticker": "DDD", "target_weight": 0.15},
                        {"ticker": "EEE", "target_weight": 0.10},
                    ]
                },
                "caerus_polaris": {
                    "holdings": [
                        {"ticker": "AAA", "target_weight": 0.50, "sector": "Technology"},
                        {"ticker": "CCC", "target_weight": 0.50, "sector": "Industrials"},
                    ]
                },
            },
        },
    )


def _write_exposure(root: Path, trade_date: str) -> None:
    _write_json(
        root / "outputs" / "attribution" / trade_date / "exposure_summary.json",
        {
            "trade_date": trade_date,
            "strategies": {
                "caerus_orion": {
                    "market_beta": 1.6,
                    "sector_exposure": {
                        "max_sector_weight": 0.55,
                        "weights": {"Technology": 0.55, "Industrials": 0.20, "Financials": 0.15, "Healthcare": 0.10},
                    },
                }
            },
        },
    )


def _write_shadow_candidates(root: Path, trade_date: str) -> None:
    base = root / "outputs" / "shadow_candidates" / trade_date
    payloads = {
        "caerus_lyra": [("AAA", 0.2), ("BBB", 0.2), ("CCC", 0.2), ("DDD", 0.2), ("EEE", 0.2)],
        "caerus_orion": [("AAA", 0.2), ("BBB", 0.2), ("CCC", 0.2), ("DDD", 0.2), ("EEE", 0.2)],
        "caerus_polaris": [
            ("AAA", 0.1),
            ("BBB", 0.1),
            ("CCC", 0.1),
            ("DDD", 0.1),
            ("EEE", 0.1),
            ("FFF", 0.1),
            ("GGG", 0.1),
            ("HHH", 0.1),
            ("III", 0.1),
            ("JJJ", 0.1),
        ],
    }
    for strategy, holdings in payloads.items():
        _write_json(
            base / f"{strategy}.json",
            {
                "trade_date": trade_date,
                "strategy_slug": strategy,
                "holdings": [
                    {"ticker": symbol, "target_weight": weight}
                    for symbol, weight in holdings
                ],
            },
        )


def _write_position_attribution(root: Path, trade_date: str) -> None:
    positions = []
    for strategy, weights in {
        "caerus_lyra": [("AAA", 0.2), ("BBB", 0.2)],
        "caerus_orion": [("CCC", 0.5), ("DDD", 0.5)],
        "caerus_polaris": [("EEE", 1.0)],
    }.items():
        for symbol, weight in weights:
            positions.append(
                {
                    "date": trade_date,
                    "strategy": strategy,
                    "symbol": symbol,
                    "weight": weight,
                    "return_pct": 0.01,
                    "pnl_contribution_pct": weight * 0.01,
                    "reason_codes": ["ok"],
                }
            )
    _write_json(
        root / "outputs" / "attribution" / trade_date / "position_attribution.json",
        {"date": trade_date, "positions": positions, "schema_version": "position_pnl_attribution_phase_a_v1"},
    )


def _write_packet_core(root: Path, trade_date: str) -> None:
    _write_json(
        root / "outputs" / "attribution" / trade_date / "attribution_summary.json",
        {
            "date": trade_date,
            "strategies_covered": ["caerus_orion"],
            "total_positions_analyzed": 1,
            "positions_with_complete_price_data": 1,
            "positions_missing_price_data": 0,
            "aggregate_confidence": "HIGH",
            "is_price_source_fresh": True,
            "freshness_reason_codes": ["ok"],
            "top_contributor_per_strategy": {},
            "top_detractor_per_strategy": {},
            "reason_codes": ["ok"],
            "source_artifacts": [],
        },
    )
    base = root / "outputs" / "decision_attribution" / trade_date
    _write_json(
        base / "strategy_decision_summary.json",
        {
            "date": trade_date,
            "strategies": [
                {
                    "strategy": "caerus_orion",
                    "decisions_analyzed": 1,
                    "average_realized_return": 0.01,
                    "average_pnl_contribution": 0.01,
                    "hit_rate": 1.0,
                    "confidence": "LOW",
                    "reason_codes": ["ok"],
                }
            ],
            "reason_codes": ["ok"],
            "source_artifacts": [],
        },
    )
    _write_json(
        base / "signal_outcome_summary.json",
        {"date": trade_date, "signals": [], "reason_codes": ["ok"], "source_artifacts": []},
    )


def _write_empty_canonical_risk_summary(root: Path, trade_date: str) -> None:
    _write_json(
        root / "outputs" / "risk_summary" / trade_date / "risk_summary.json",
        {
            "date": trade_date,
            "position_count": 0,
            "strategies_covered": [],
            "strategies": [],
            "top_holdings": {},
            "confidence": "LOW",
            "reason_codes": ["attribution_positions_empty", "no_holdings"],
            "source_artifacts": ["outputs/attribution/2026-06-01/position_attribution.json"],
        },
    )


def test_risk_summary_builds_canonical_artifacts(tmp_path):
    trade_date = "2026-06-01"
    _write_universe(tmp_path)
    _write_holdings(tmp_path, trade_date)
    _write_exposure(tmp_path, trade_date)

    result = build_risk_summary(trade_date=trade_date, repo_root=tmp_path)
    summary = result["risk_summary"]

    assert summary["date"] == trade_date
    assert summary["strategies_covered"] == ["caerus_orion", "caerus_polaris"]
    assert summary["position_count"] == 7
    assert summary["top3_concentration"] == 1.0
    assert summary["concentration_risk_level"] == "HIGH"
    assert summary["exposure_risk_level"] == "HIGH"
    assert summary["missing_sector_coverage_count"] == 0
    assert summary["reason_codes"] == ["ok"]
    assert (tmp_path / "outputs" / "risk_summary" / trade_date / "risk_summary.json").exists()
    assert (tmp_path / "outputs" / "risk_summary" / trade_date / "concentration_summary.json").exists()
    assert (tmp_path / "outputs" / "risk_summary" / trade_date / "exposure_summary.json").exists()
    required = {
        "date",
        "strategies_covered",
        "position_count",
        "top_holdings",
        "max_position_weight",
        "top3_concentration",
        "top5_concentration",
        "sector_exposure",
        "missing_sector_coverage_count",
        "concentration_risk_level",
        "exposure_risk_level",
        "confidence",
        "reason_codes",
        "source_artifacts",
    }
    assert required <= set(result["risk_summary"])
    assert required <= set(result["concentration_summary"])
    assert required <= set(result["exposure_summary"])


def test_missing_source_data_emits_reason_codes(tmp_path):
    trade_date = "2026-06-01"

    result = build_risk_summary(trade_date=trade_date, repo_root=tmp_path)

    assert result["risk_summary"]["strategies_covered"] == []
    assert result["risk_summary"]["confidence"] == "LOW"
    assert "holdings_source_missing" in result["risk_summary"]["reason_codes"]


def test_shadow_candidate_fallback_without_portfolio_history(tmp_path):
    trade_date = "2026-05-29"
    _write_universe(tmp_path)
    _write_shadow_candidates(tmp_path, trade_date)

    result = build_risk_summary(trade_date=trade_date, repo_root=tmp_path)
    summary = result["risk_summary"]

    assert summary["strategies_covered"] == ["caerus_lyra", "caerus_orion", "caerus_polaris"]
    assert summary["position_count"] == 20
    assert summary["strategies"]["caerus_lyra"]["position_count"] == 5
    assert summary["strategies"]["caerus_orion"]["position_count"] == 5
    assert summary["strategies"]["caerus_polaris"]["position_count"] == 10
    assert summary["strategies"]["caerus_orion"]["top3_concentration"] == 0.6
    assert "holdings_source_missing" not in summary["reason_codes"]


def test_position_attribution_fallback_when_holding_sources_missing(tmp_path):
    trade_date = "2026-06-01"
    _write_universe(tmp_path)
    _write_position_attribution(tmp_path, trade_date)

    result = build_risk_summary(trade_date=trade_date, repo_root=tmp_path)
    summary = result["risk_summary"]

    assert summary["strategies_covered"] == ["caerus_lyra", "caerus_orion", "caerus_polaris"]
    assert summary["position_count"] == 5
    assert summary["strategies"]["caerus_orion"]["max_position_weight"] == 0.5
    assert summary["strategies"]["caerus_polaris"]["top5_concentration"] == 1.0
    assert "holdings_source_missing" not in summary["reason_codes"]
    assert any(
        str(path).endswith("outputs/attribution/2026-06-01/position_attribution.json")
        for path in summary["source_artifacts"]
    )


def test_missing_sector_coverage_is_partial_not_crash(tmp_path):
    trade_date = "2026-06-01"
    _write_json(
        tmp_path / "outputs" / "portfolio_history" / trade_date / "holdings_snapshot.json",
        {
            "trade_date": trade_date,
            "strategies": {
                "caerus_orion": {
                    "holdings": [
                        {"ticker": "AAA", "target_weight": 0.6},
                        {"ticker": "ZZZ", "target_weight": 0.4},
                    ]
                }
            },
        },
    )

    result = build_risk_summary(trade_date=trade_date, repo_root=tmp_path)
    strategy = result["risk_summary"]["strategies"]["caerus_orion"]

    assert strategy["missing_sector_coverage_count"] == 2
    assert result["risk_summary"]["confidence"] == "MEDIUM"
    assert "missing_sector_coverage" in result["risk_summary"]["reason_codes"]


def test_risk_summary_output_is_deterministic(tmp_path):
    trade_date = "2026-06-01"
    _write_universe(tmp_path)
    _write_holdings(tmp_path, trade_date)

    build_risk_summary(trade_date=trade_date, repo_root=tmp_path)
    first = (tmp_path / "outputs" / "risk_summary" / trade_date / "risk_summary.json").read_text()
    build_risk_summary(trade_date=trade_date, repo_root=tmp_path)
    second = (tmp_path / "outputs" / "risk_summary" / trade_date / "risk_summary.json").read_text()

    assert first == second


def test_research_review_packet_consumes_canonical_risk_summary(tmp_path):
    trade_date = "2026-06-01"
    _write_universe(tmp_path)
    _write_holdings(tmp_path, trade_date)
    _write_packet_core(tmp_path, trade_date)
    build_risk_summary(trade_date=trade_date, repo_root=tmp_path)

    packet = build_research_review_packet(trade_date=trade_date, repo_root=tmp_path)

    assert packet["sources"]["risk"]["status"] == "PRESENT"
    assert packet["sections"]["risk_concentration"]["available"] is True
    assert "missing_risk" not in packet["overall"]["reason_codes"]
    assert "missing_risk_summary" not in packet["sections"]["data_freshness"]["reason_codes"]


def test_empty_canonical_risk_summary_does_not_clear_missing_risk(tmp_path):
    trade_date = "2026-06-01"
    _write_packet_core(tmp_path, trade_date)
    _write_empty_canonical_risk_summary(tmp_path, trade_date)

    packet = build_research_review_packet(trade_date=trade_date, repo_root=tmp_path)
    risk = packet["sections"]["risk_concentration"]

    assert risk["available"] is False
    assert packet["sources"]["risk"]["status"] == "MISSING"
    assert packet["sources"]["risk"]["path"].endswith("outputs/risk_summary/2026-06-01/risk_summary.json")
    assert "attribution_positions_empty" in risk["reason_codes"]
    assert "no_holdings" in risk["reason_codes"]
    assert "empty_risk_summary" in risk["reason_codes"]
    assert "missing_risk_summary" in risk["reason_codes"]
    assert "missing_risk" in packet["overall"]["reason_codes"]
    assert "missing_risk_summary" in packet["sections"]["data_freshness"]["reason_codes"]
    action_text = " ".join(packet["sections"]["recommended_next_actions"]).lower()
    assert "risk/concentration" in action_text

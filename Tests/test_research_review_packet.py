from __future__ import annotations

import json
from pathlib import Path

from research.review_packet import build_research_review_packet


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _write_attribution(root: Path, trade_date: str, *, fresh: bool = True) -> None:
    _write_json(
        root / "outputs" / "attribution" / trade_date / "attribution_summary.json",
        {
            "date": trade_date,
            "strategies_covered": ["caerus_polaris"],
            "total_positions_analyzed": 2,
            "positions_with_complete_price_data": 2 if fresh else 1,
            "positions_missing_price_data": 0 if fresh else 1,
            "aggregate_confidence": "HIGH" if fresh else "MEDIUM",
            "price_source": "outputs/research/flow_detection_v1/price_panel.parquet",
            "price_source_max_date": trade_date if fresh else "2026-05-29",
            "is_price_source_fresh": fresh,
            "freshness_lag_days": 0 if fresh else 3,
            "freshness_reason_codes": ["ok"] if fresh else ["price_source_stale"],
            "top_contributor_per_strategy": {
                "caerus_polaris": {
                    "symbol": "AAA",
                    "return_pct": 0.1,
                    "pnl_contribution_pct": 0.05,
                }
            },
            "top_detractor_per_strategy": {
                "caerus_polaris": {
                    "symbol": "BBB",
                    "return_pct": -0.02,
                    "pnl_contribution_pct": -0.01,
                }
            },
            "reason_codes": ["ok"] if fresh else ["price_source_stale", "missing_end_price"],
            "source_artifacts": ["holdings.json", "prices.parquet"],
        },
    )


def _write_decision(root: Path, trade_date: str, observations: int = 3) -> None:
    base = root / "outputs" / "decision_attribution" / trade_date
    _write_json(
        base / "strategy_decision_summary.json",
        {
            "date": trade_date,
            "strategies": [
                {
                    "strategy": "caerus_polaris",
                    "decisions_analyzed": observations,
                    "average_realized_return": 0.02,
                    "average_pnl_contribution": 0.01,
                    "hit_rate": 0.6666666667,
                    "top_decision": {"symbol": "AAA", "realized_return": 0.1, "pnl_contribution": 0.05},
                    "worst_decision": {"symbol": "BBB", "realized_return": -0.02, "pnl_contribution": -0.01},
                    "confidence": "MEDIUM",
                    "reason_codes": ["ok"],
                }
            ],
            "reason_codes": ["ok"],
            "source_artifacts": ["decision_attribution.json"],
        },
    )
    _write_json(
        base / "signal_outcome_summary.json",
        {
            "date": trade_date,
            "signals": [
                {
                    "signal_name": "momentum_score",
                    "observations": observations,
                    "average_score": 1.4,
                    "average_realized_return": 0.02,
                    "hit_rate": 0.6666666667,
                    "confidence": "MEDIUM",
                    "reason_codes": ["ok"],
                }
            ],
            "reason_codes": ["ok"],
            "source_artifacts": ["signal_outcome_summary.json"],
        },
    )


def _write_optional_sources(root: Path, trade_date: str) -> None:
    (root / "research").mkdir(parents=True, exist_ok=True)
    (root / "research" / f"model_review_{trade_date}.md").write_text(
        "\n".join(
            [
                "| Dimension | Score |",
                "|---|---:|",
                "| Signal Quality | **4/10** |",
                "| Infrastructure | **8/10** |",
                "| Risk Management | **7/10** |",
                "| Regime Detection | **6/10** |",
                "| Execution | **5/10** |",
                "| Attribution | **3/10** |",
                "| Data Quality | **6/10** |",
            ]
        ),
        encoding="utf-8",
    )
    _write_json(
        root / "outputs" / "daily" / f"health_{trade_date}.json",
        {"trade_date": trade_date, "status": "PASS", "reconciliation_status": "PASS", "warnings": []},
    )
    _write_json(
        root / "outputs" / "attribution" / trade_date / "concentration_analysis.json",
        {
            "trade_date": trade_date,
            "strategies": {
                "caerus_polaris": {
                    "holdings_count": 2,
                    "max_weight": 0.5,
                    "top3_weight": 1.0,
                    "top3_contribution_share_21d": 1.0,
                }
            },
        },
    )
    _write_json(
        root / "outputs" / "attribution" / trade_date / "exposure_summary.json",
        {
            "trade_date": trade_date,
            "strategies": {
                "caerus_polaris": {
                    "market_beta": 1.1,
                    "sector_exposure": {"max_sector_weight": 0.5, "weights": {"Tech": 0.5, "Cash": 0.5}},
                }
            },
        },
    )
    _write_json(
        root / "outputs" / "portfolio_history" / trade_date / "holdings_snapshot.json",
        {
            "trade_date": trade_date,
            "strategies": {
                "caerus_polaris": {
                    "holdings": [
                        {"ticker": "AAA", "target_weight": 0.5, "sector": "Tech"},
                        {"ticker": "BBB", "target_weight": 0.5, "sector": "Cash"},
                    ]
                }
            },
        },
    )
    _write_json(
        root / "outputs" / "attribution" / trade_date / "regime_performance_breakdown.json",
        {
            "trade_date": trade_date,
            "strategies": {
                "caerus_polaris": {
                    "interpretation": {"best_risk_regime": "risk_on", "worst_risk_regime": "risk_off"},
                    "performance_by_regime": {
                        "risk_regime": {
                            "risk_on": {"hit_rate": 0.6},
                            "risk_off": {"hit_rate": 0.4},
                        }
                    },
                }
            },
        },
    )
    _write_json(
        root / "outputs" / "vix_regime" / "regime_current.json",
        {"as_of": trade_date, "regime": "NORMAL", "vix": 18.0, "position_scale": 1.0},
    )


def _write_tier1_sources(root: Path, trade_date: str) -> None:
    _write_json(
        root / "outputs" / "research" / "execution_timing" / trade_date / "execution_timing_summary.json",
        {
            "date": trade_date,
            "available": True,
            "confidence": "MEDIUM",
            "baseline_offset": "T+5m",
            "baseline_time_et": "09:35",
            "coverage_ratio": 1.0,
            "symbols_evaluated": 2,
            "symbols_missing_bars": [],
            "best_offset_vs_baseline": {"offset_label": "T+1m", "execution_time_et": "09:31", "total_estimated_bps_impact_vs_baseline": -4.0},
            "worst_offset_vs_baseline": {"offset_label": "T+10m", "execution_time_et": "09:40", "total_estimated_bps_impact_vs_baseline": 8.0},
            "reason_codes": ["ok"],
            "source_artifacts": ["planned_execution_payload.json"],
        },
    )
    _write_json(
        root / "outputs" / "research" / "promotion_readiness" / trade_date / "promotion_readiness_windows.json",
        {
            "date": trade_date,
            "available": True,
            "confidence": "LOW",
            "promotion_recommendation": "NO_PROMOTION_RECOMMENDED",
            "blockers": ["caerus_lyra:insufficient_observations"],
            "windows": ["20", "40", "60"],
            "strategies": {
                "caerus_lyra": {
                    "windows": {
                        "20": {"readiness_state": "NOT_READY", "observation_count": 5},
                        "40": {"readiness_state": "NOT_READY", "observation_count": 5},
                        "60": {"readiness_state": "NOT_READY", "observation_count": 5},
                    }
                }
            },
            "reason_codes": ["caerus_lyra:insufficient_observations"],
            "source_artifacts": ["shadow_nav_series.csv"],
        },
    )
    _write_json(
        root / "outputs" / "research" / "strategy_differentiation" / trade_date / "strategy_differentiation.json",
        {
            "date": trade_date,
            "available": True,
            "confidence": "MEDIUM",
            "blockers": ["caerus_lyra_vs_caerus_orion:weak_differentiation"],
            "pairs": [
                {
                    "left_strategy": "caerus_lyra",
                    "right_strategy": "caerus_orion",
                    "holdings_overlap_percentage": 0.8,
                    "daily_return_correlation": 0.95,
                    "average_active_share_proxy": 0.2,
                    "behavioral_differentiation_score": 0.15,
                    "differentiation_readiness_flag": "WEAK",
                    "reason_codes": ["high_overlap_high_correlation"],
                }
            ],
            "reason_codes": ["caerus_lyra_vs_caerus_orion:weak_differentiation"],
            "source_artifacts": ["comparison.json"],
        },
    )


def test_all_core_artifacts_present_generates_packet(tmp_path):
    trade_date = "2026-06-01"
    _write_attribution(tmp_path, trade_date)
    _write_decision(tmp_path, trade_date)
    _write_optional_sources(tmp_path, trade_date)

    result = build_research_review_packet(trade_date=trade_date, repo_root=tmp_path)

    assert result["date"] == trade_date
    assert result["sections"]["position_attribution"]["total_positions_analyzed"] == 2
    assert result["sections"]["decision_attribution"]["decisions_analyzed"] == 3
    assert result["sections"]["model_review"]["scores"]["Signal Quality"] == 4
    assert "Move next to signal IC and rank IC analysis" in " ".join(result["sections"]["recommended_next_actions"])
    out_dir = tmp_path / "outputs" / "research_review" / trade_date
    assert (out_dir / "research_review.json").exists()
    assert (out_dir / "research_review.md").exists()
    assert (out_dir / "research_review.html").exists()


def test_missing_attribution_recommends_phase_a(tmp_path):
    trade_date = "2026-06-01"
    _write_decision(tmp_path, trade_date)

    result = build_research_review_packet(trade_date=trade_date, repo_root=tmp_path)

    assert "missing_attribution" in result["overall"]["reason_codes"]
    assert "build_position_attribution.py" in " ".join(result["sections"]["recommended_next_actions"])


def test_missing_decision_attribution_recommends_phase_b(tmp_path):
    trade_date = "2026-06-01"
    _write_attribution(tmp_path, trade_date)

    result = build_research_review_packet(trade_date=trade_date, repo_root=tmp_path)

    assert "missing_decision_attribution" in result["overall"]["reason_codes"]
    assert "build_decision_attribution.py" in " ".join(result["sections"]["recommended_next_actions"])


def test_stale_price_source_is_reported(tmp_path):
    trade_date = "2026-06-01"
    _write_attribution(tmp_path, trade_date, fresh=False)
    _write_decision(tmp_path, trade_date)

    result = build_research_review_packet(trade_date=trade_date, repo_root=tmp_path)

    position = result["sections"]["position_attribution"]
    freshness = result["sections"]["data_freshness"]
    assert position["is_price_source_fresh"] is False
    assert "price_source_stale" in position["reason_codes"]
    assert "price_source_stale" in freshness["reason_codes"]
    assert "hydrate_price_cache_only.py" in " ".join(result["sections"]["recommended_next_actions"])


def test_deterministic_output_is_stable(tmp_path):
    trade_date = "2026-06-01"
    _write_attribution(tmp_path, trade_date)
    _write_decision(tmp_path, trade_date)
    _write_optional_sources(tmp_path, trade_date)

    first = build_research_review_packet(trade_date=trade_date, repo_root=tmp_path)
    first_json = (tmp_path / "outputs" / "research_review" / trade_date / "research_review.json").read_text()
    second = build_research_review_packet(trade_date=trade_date, repo_root=tmp_path)
    second_json = (tmp_path / "outputs" / "research_review" / trade_date / "research_review.json").read_text()

    assert first["overall"] == second["overall"]
    assert first_json == second_json


def test_latest_date_selection_uses_latest_core_artifact_date(tmp_path):
    _write_attribution(tmp_path, "2026-05-30")
    _write_decision(tmp_path, "2026-05-30")
    _write_attribution(tmp_path, "2026-06-01")
    _write_decision(tmp_path, "2026-06-01")

    result = build_research_review_packet(repo_root=tmp_path)

    assert result["date"] == "2026-06-01"
    assert "date_selected_latest_attribution_and_decision" in result["overall"]["reason_codes"]


def test_markdown_and_html_smoke(tmp_path):
    trade_date = "2026-06-01"
    _write_attribution(tmp_path, trade_date)
    _write_decision(tmp_path, trade_date)

    build_research_review_packet(trade_date=trade_date, repo_root=tmp_path)
    out_dir = tmp_path / "outputs" / "research_review" / trade_date
    markdown = (out_dir / "research_review.md").read_text()
    html = (out_dir / "research_review.html").read_text()

    assert "Executive Summary" in markdown
    assert "Attribution Phase A" in markdown
    assert "Attribution Phase B: Decision Attribution" in markdown
    assert "Recommended Next Actions" in markdown
    assert "Executive Summary" in html
    assert "Attribution" in html
    assert "Decision Attribution" in html
    assert "Recommended Next Actions" in html


def test_tier1_sections_populate_when_artifacts_exist(tmp_path):
    trade_date = "2026-06-01"
    _write_attribution(tmp_path, trade_date)
    _write_decision(tmp_path, trade_date)
    _write_tier1_sources(tmp_path, trade_date)

    result = build_research_review_packet(trade_date=trade_date, repo_root=tmp_path)

    assert result["sections"]["execution_timing_study"]["available"] is True
    assert result["sections"]["execution_timing_study"]["best_offset_vs_baseline"]["execution_time_et"] == "09:31"
    assert result["sections"]["promotion_readiness_windows"]["promotion_recommendation"] == "NO_PROMOTION_RECOMMENDED"
    assert result["sections"]["strategy_differentiation"]["pairs"][0]["differentiation_readiness_flag"] == "WEAK"
    assert result["sections"]["tier1_research_controls"]["recommendation"] == "No promotion recommended"
    markdown = (tmp_path / "outputs" / "research_review" / trade_date / "research_review.md").read_text()
    assert "Execution Timing Study" in markdown
    assert "Promotion Readiness Windows" in markdown
    assert "Strategy Differentiation" in markdown


def test_tier1_missing_degrades_gracefully_and_recommends_no_promotion(tmp_path):
    trade_date = "2026-06-01"
    _write_attribution(tmp_path, trade_date)
    _write_decision(tmp_path, trade_date)

    result = build_research_review_packet(trade_date=trade_date, repo_root=tmp_path)

    assert result["sections"]["execution_timing_study"]["available"] is False
    assert "missing_execution_timing_study" in result["sections"]["execution_timing_study"]["reason_codes"]
    assert result["sections"]["tier1_research_controls"]["recommendation"] == "No promotion recommended"
    action_text = " ".join(result["sections"]["recommended_next_actions"])
    assert "build_execution_timing_counterfactual.py" in action_text
    assert "build_promotion_readiness_windows.py" in action_text
    assert "build_strategy_differentiation.py" in action_text

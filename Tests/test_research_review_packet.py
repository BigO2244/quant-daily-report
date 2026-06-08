from __future__ import annotations

import json
from pathlib import Path

from research.review_packet import build_research_review_packet


REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _write_registry_with_active_phoenix(root: Path) -> None:
    payload = json.loads((REPO_ROOT / "config" / "research" / "strategy_registry.json").read_text(encoding="utf-8"))
    for entry in payload["strategies"]:
        if entry["strategy_id"] == "caerus_phoenix":
            entry["status"] = "shadow"
            entry["role"] = "challenger"
            entry["shadow_tracking"]["enabled"] = True
            entry["shadow_tracking"]["source_variant"] = "phoenix_fixture"
    _write_json(root / "config" / "research" / "strategy_registry.json", payload)


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
            "factor_exposure_available": True,
            "position_contributions_available": True,
            "factor_exposure_source_artifacts": ["factor_exposure.json"],
            "position_contribution_source_artifacts": ["position_attribution.json"],
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


def _write_tier2_sources(root: Path, trade_date: str, *, universe_available: bool = True, deep_verdict: str = "WEAK_DIFFERENTIATION") -> None:
    _write_json(
        root / "outputs" / "research" / "risk_coverage" / trade_date / "risk_coverage.json",
        {
            "date": trade_date,
            "available": True,
            "confidence": "MEDIUM",
            "risk_level": "MEDIUM",
            "holdings_source_date": trade_date,
            "position_count": 5,
            "gross_exposure": 1.0,
            "net_exposure": 1.0,
            "top3_concentration": 0.6,
            "top5_concentration": 1.0,
            "top10_concentration": 1.0,
            "max_single_name_weight": 0.2,
            "strategies": {
                "caerus_lyra": {
                    "position_count": 5,
                    "gross_exposure": 1.0,
                    "net_exposure": 1.0,
                    "top3_concentration": 0.6,
                    "top5_concentration": 1.0,
                    "top10_concentration": 1.0,
                    "max_single_name_weight": 0.2,
                    "risk_level": "MEDIUM",
                    "confidence": "MEDIUM",
                    "reason_codes": ["ok"],
                }
            },
            "reason_codes": ["ok"],
            "source_artifacts": ["comparison.json"],
        },
    )
    _write_json(
        root / "outputs" / "research" / "strategy_differentiation" / trade_date / "strategy_differentiation_deep.json",
        {
            "date": trade_date,
            "available": True,
            "confidence": "MEDIUM",
            "aggregate_verdict": deep_verdict,
            "blockers": [] if deep_verdict == "STRONG_DIFFERENTIATION" else ["caerus_lyra_vs_caerus_orion:weak_differentiation"],
            "pairs": [
                {
                    "left_strategy": "caerus_lyra",
                    "right_strategy": "caerus_orion",
                    "verdict": deep_verdict,
                    "behavioral_differentiation_score": 0.2 if deep_verdict == "WEAK_DIFFERENTIATION" else 0.7,
                    "shared_top_contributors": ["AAA"],
                    "shared_top_detractors": ["BBB"],
                    "reason_codes": ["weak_behavioral_differentiation"] if deep_verdict == "WEAK_DIFFERENTIATION" else ["ok"],
                }
            ],
            "reason_codes": ["weak_behavioral_differentiation"] if deep_verdict == "WEAK_DIFFERENTIATION" else ["ok"],
            "source_artifacts": ["comparison.json"],
        },
    )
    _write_json(
        root / "outputs" / "research" / "position_sizing" / trade_date / "position_sizing_research.json",
        {
            "date": trade_date,
            "available": True,
            "confidence": "MEDIUM",
            "holdings_source_date": trade_date,
            "returns_source_date": trade_date,
            "strategies": {"caerus_lyra": {"best_research_alternative": "equal_weight", "confidence": "MEDIUM", "reason_codes": ["ok"]}},
            "reason_codes": ["ok"],
            "source_artifacts": ["position_attribution.json"],
        },
    )
    _write_json(
        root / "outputs" / "research" / "universe_governance" / trade_date / "universe_governance.json",
        {
            "date": trade_date,
            "available": universe_available,
            "confidence": "HIGH" if universe_available else "LOW",
            "security_master_asof_date": trade_date,
            "stale_universe": False,
            "blockers": [] if universe_available else ["planned:unknown_symbol:NOPE"],
            "alias_resolutions": [],
            "coverage_summary": {"planned_symbol_count": 1},
            "reason_codes": ["ok"] if universe_available else ["planned:unknown_symbol:NOPE"],
            "source_artifacts": ["ticker_universe_latest.json"],
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


def test_tier1_distinguishes_data_coverage_from_model_differentiation(tmp_path):
    trade_date = "2026-06-01"
    _write_attribution(tmp_path, trade_date)
    _write_decision(tmp_path, trade_date)
    _write_optional_sources(tmp_path, trade_date)
    _write_tier1_sources(tmp_path, trade_date)

    result = build_research_review_packet(trade_date=trade_date, repo_root=tmp_path)

    tier1 = result["sections"]["tier1_research_controls"]
    assert "MODEL_DIFFERENTIATION" in tier1["blocker_categories"]
    assert "OBSERVATION_WINDOW" in tier1["blocker_categories"]
    assert "DATA_COVERAGE" not in tier1["blocker_categories"]


def test_tier1_missing_factor_and_contribution_inputs_are_data_coverage_blockers(tmp_path):
    trade_date = "2026-06-01"
    _write_attribution(tmp_path, trade_date)
    _write_decision(tmp_path, trade_date)
    _write_optional_sources(tmp_path, trade_date)
    _write_json(
        tmp_path / "outputs" / "research" / "execution_timing" / trade_date / "execution_timing_summary.json",
        {
            "date": trade_date,
            "available": True,
            "confidence": "MEDIUM",
            "baseline_offset": "T+5m",
            "baseline_time_et": "09:35",
            "coverage_ratio": 1.0,
            "symbols_evaluated": 2,
            "symbols_missing_bars": [],
            "reason_codes": ["ok"],
        },
    )
    _write_json(
        tmp_path / "outputs" / "research" / "promotion_readiness" / trade_date / "promotion_readiness_windows.json",
        {
            "date": trade_date,
            "available": True,
            "confidence": "MEDIUM",
            "promotion_recommendation": "PROMOTION_REVIEW_READY:caerus_lyra",
            "blockers": [],
            "strategies": {},
            "reason_codes": ["ok"],
        },
    )
    _write_json(
        tmp_path / "outputs" / "research" / "strategy_differentiation" / trade_date / "strategy_differentiation.json",
        {
            "date": trade_date,
            "available": True,
            "confidence": "LOW",
            "factor_exposure_available": False,
            "position_contributions_available": False,
            "blockers": [],
            "pairs": [
                {
                    "left_strategy": "caerus_lyra",
                    "right_strategy": "caerus_orion",
                    "differentiation_readiness_flag": "READY",
                    "reason_codes": ["ok"],
                }
            ],
            "reason_codes": ["factor_exposure_missing", "position_contributions_missing"],
        },
    )

    result = build_research_review_packet(trade_date=trade_date, repo_root=tmp_path)

    tier1 = result["sections"]["tier1_research_controls"]
    assert tier1["recommendation"] == "No promotion recommended"
    assert tier1["factor_exposure_status"] == "missing_or_unavailable"
    assert tier1["position_contribution_status"] == "missing_or_unavailable"
    assert "DATA_COVERAGE" in tier1["blocker_categories"]
    assert "MODEL_DIFFERENTIATION" not in tier1["blocker_categories"]
    action_text = " ".join(result["sections"]["recommended_next_actions"])
    assert "factor exposure" in action_text
    assert "build_position_attribution.py" in action_text


def test_tier2_sections_populate_and_keep_promotion_blocked_on_weak_differentiation(tmp_path):
    trade_date = "2026-06-01"
    _write_attribution(tmp_path, trade_date)
    _write_decision(tmp_path, trade_date)
    _write_tier1_sources(tmp_path, trade_date)
    _write_tier2_sources(tmp_path, trade_date, deep_verdict="WEAK_DIFFERENTIATION")

    result = build_research_review_packet(trade_date=trade_date, repo_root=tmp_path)

    assert result["sections"]["risk_coverage"]["available"] is True
    assert result["sections"]["strategy_differentiation_deep"]["aggregate_verdict"] == "WEAK_DIFFERENTIATION"
    assert result["sections"]["position_sizing_research"]["available"] is True
    assert result["sections"]["universe_governance"]["available"] is True
    tier2 = result["sections"]["tier2_research_controls"]
    assert tier2["recommendation"] == "No promotion recommended"
    assert "MODEL_DIFFERENTIATION" in tier2["blocker_categories"]
    markdown = (tmp_path / "outputs" / "research_review" / trade_date / "research_review.md").read_text()
    assert "Tier 2 Risk Coverage" in markdown
    assert "Deep Strategy Differentiation" in markdown
    assert "Universe Governance" in markdown


def test_tier2_universe_blocker_prevents_promotion(tmp_path):
    trade_date = "2026-06-01"
    _write_attribution(tmp_path, trade_date)
    _write_decision(tmp_path, trade_date)
    _write_tier1_sources(tmp_path, trade_date)
    _write_tier2_sources(tmp_path, trade_date, universe_available=False, deep_verdict="STRONG_DIFFERENTIATION")

    result = build_research_review_packet(trade_date=trade_date, repo_root=tmp_path)

    tier2 = result["sections"]["tier2_research_controls"]
    assert tier2["recommendation"] == "No promotion recommended"
    assert "UNIVERSE_GOVERNANCE" in tier2["blocker_categories"]


def _write_tier3_sources(
    root: Path,
    trade_date: str,
    *,
    governance_available: bool = True,
    promotion_recommendation: str = "NO_PROMOTION_RECOMMENDED",
    regime_available: bool = True,
    allocation_available: bool = True,
    allocation_recommendation: str = "no_allocation_change_recommended",
    promotion_governance_allows_change: bool = False,
) -> None:
    _write_json(
        root / "outputs" / "research" / "promotion_governance" / trade_date / "promotion_governance.json",
        {
            "schema_version": "caerus_promotion_governance_v1",
            "date": trade_date,
            "available": governance_available,
            "confidence": "MEDIUM" if governance_available else "LOW",
            "current_control_strategy": "caerus_polaris",
            "promotion_recommendation": promotion_recommendation,
            "demotion_recommendation": "NO_DEMOTION_RECOMMENDED",
            "challenger_rankings": [
                {"rank": 1, "strategy": "caerus_lyra", "decision": "HOLD", "rank_score": 3, "max_observation_count": 60, "evidence_strength": "MEDIUM"},
                {"rank": 2, "strategy": "caerus_orion", "decision": "HOLD", "rank_score": 3, "max_observation_count": 60, "evidence_strength": "MEDIUM"},
            ],
            "strategies": {
                "caerus_polaris": {"strategy": "caerus_polaris", "decision": "HOLD", "evidence_strength": "MEDIUM", "reason_codes": ["ok"], "gates": {}},
                "caerus_orion": {"strategy": "caerus_orion", "decision": "HOLD", "evidence_strength": "LOW", "reason_codes": ["differentiation:weak_differentiation"], "gates": {}},
                "caerus_lyra": {"strategy": "caerus_lyra", "decision": "HOLD", "evidence_strength": "LOW", "reason_codes": ["differentiation:weak_differentiation"], "gates": {}},
            },
            "blocker_categories": ["DIFFERENTIATION"] if governance_available else ["NONE"],
            "evidence_strength": "MEDIUM" if governance_available else "LOW",
            "reason_codes": ["ok"] if governance_available else ["missing_inputs"],
            "source_artifacts": [],
        },
    )
    _write_json(
        root / "outputs" / "research" / "regime_attribution" / trade_date / "regime_attribution.json",
        {
            "schema_version": "caerus_regime_attribution_v1",
            "date": trade_date,
            "available": regime_available,
            "confidence": "HIGH" if regime_available else "LOW",
            "regime_labels": ["bull_trend", "bear_trend", "high_vol", "low_vol", "panic", "recovery", "neutral"],
            "regime_distribution": {"bull_trend": 100, "bear_trend": 30, "neutral": 50, "low_vol": 20, "high_vol": 5, "panic": 5, "recovery": 5} if regime_available else {},
            "history_window": {"first_date": "2024-01-02", "last_date": trade_date, "total_days": 250, "classified_days": 215},
            "strategies": {},
            "reason_codes": ["ok"] if regime_available else ["missing_shadow_nav_series"],
            "source_artifacts": [],
        },
    )
    _write_json(
        root / "outputs" / "research" / "dynamic_strategy_allocation" / trade_date / "dynamic_strategy_allocation.json",
        {
            "schema_version": "caerus_dynamic_strategy_allocation_v1",
            "date": trade_date,
            "is_research_only": True,
            "production_weights_modified": False,
            "available": allocation_available,
            "confidence": "HIGH" if allocation_available else "LOW",
            "policies": [
                {
                    "policy": "static_equal_weight",
                    "is_research_only": True,
                    "available": allocation_available,
                    "observation_count": 215,
                    "total_return": 0.10,
                    "excess_return_vs_polaris": 0.02,
                    "realized_volatility": 0.20,
                    "max_drawdown": -0.10,
                    "hit_rate": 0.55,
                    "turnover_proxy": 0.0,
                    "concentration_proxy": 0.4,
                    "risk_adjusted_score": 0.10,
                    "reason_codes": ["ok"] if allocation_available else ["insufficient_history"],
                },
            ],
            "ranking": (
                [{"rank": 1, "policy": "static_equal_weight", "risk_adjusted_score": 0.10, "excess_return_vs_polaris": 0.02, "realized_volatility": 0.20, "max_drawdown": -0.10, "confidence": "HIGH"}]
                if allocation_available else []
            ),
            "promotion_governance_allows_change": promotion_governance_allows_change,
            "allocation_recommendation": allocation_recommendation,
            "reason_codes": ["ok"] if allocation_available else ["insufficient_history"],
            "source_artifacts": [],
        },
    )


def test_tier3_sections_populate_when_artifacts_exist(tmp_path):
    trade_date = "2026-06-02"
    _write_attribution(tmp_path, trade_date)
    _write_decision(tmp_path, trade_date)
    _write_tier1_sources(tmp_path, trade_date)
    _write_tier2_sources(tmp_path, trade_date, deep_verdict="STRONG_DIFFERENTIATION")
    _write_tier3_sources(tmp_path, trade_date)

    result = build_research_review_packet(trade_date=trade_date, repo_root=tmp_path)
    assert result["sections"]["promotion_governance"]["available"] is True
    assert result["sections"]["regime_attribution"]["available"] is True
    assert result["sections"]["dynamic_strategy_allocation"]["available"] is True
    tier3 = result["sections"]["tier3_research_controls"]
    assert tier3["promotion_governance_status"] == "available"
    assert tier3["regime_attribution_status"] == "available"
    assert tier3["dynamic_strategy_allocation_status"] == "available"
    final = result["sections"]["final_control_summary"]
    assert final["current_recommendation"] == "No promotion recommended"
    assert final["polaris_status"] == "BENCHMARK_CONTROL"
    assert set(final["strategy_statuses"]) == {"caerus_polaris", "caerus_orion", "caerus_lyra"}
    assert "caerus_phoenix" not in final["strategy_statuses"]
    assert "caerus_argo" not in final["strategy_statuses"]
    markdown = (tmp_path / "outputs" / "research_review" / trade_date / "research_review.md").read_text()
    assert "Promotion Governance (Tier 3)" in markdown
    assert "Regime Attribution (Tier 3)" in markdown
    assert "Dynamic Strategy Allocation (Tier 3, Research Only)" in markdown
    assert "Final Control Summary" in markdown


def test_final_control_summary_surfaces_fixture_active_phoenix_without_overlay(tmp_path):
    trade_date = "2026-06-02"
    _write_registry_with_active_phoenix(tmp_path)
    _write_attribution(tmp_path, trade_date)
    _write_decision(tmp_path, trade_date)
    _write_tier1_sources(tmp_path, trade_date)
    _write_tier2_sources(tmp_path, trade_date, deep_verdict="STRONG_DIFFERENTIATION")
    _write_tier3_sources(tmp_path, trade_date)
    governance_path = tmp_path / "outputs" / "research" / "promotion_governance" / trade_date / "promotion_governance.json"
    governance = json.loads(governance_path.read_text(encoding="utf-8"))
    governance["strategies"]["caerus_phoenix"] = {
        "strategy": "caerus_phoenix",
        "decision": "HOLD",
        "evidence_strength": "LOW",
        "reason_codes": ["research_only_fixture"],
        "gates": {},
    }
    _write_json(governance_path, governance)

    result = build_research_review_packet(trade_date=trade_date, repo_root=tmp_path)

    final = result["sections"]["final_control_summary"]
    assert list(final["strategy_statuses"]) == [
        "caerus_polaris",
        "caerus_orion",
        "caerus_lyra",
        "caerus_phoenix",
    ]
    assert final["strategy_statuses"]["caerus_phoenix"]["status"] == "HOLD"
    assert "caerus_argo" not in final["strategy_statuses"]
    markdown = (tmp_path / "outputs" / "research_review" / trade_date / "research_review.md").read_text(encoding="utf-8")
    assert "Caerus Phoenix" in markdown
    assert "Caerus Argo" not in markdown


def test_tier3_missing_artifacts_degrade_gracefully(tmp_path):
    trade_date = "2026-06-02"
    _write_attribution(tmp_path, trade_date)
    _write_decision(tmp_path, trade_date)
    _write_tier1_sources(tmp_path, trade_date)
    _write_tier2_sources(tmp_path, trade_date)

    result = build_research_review_packet(trade_date=trade_date, repo_root=tmp_path)
    assert result["sections"]["promotion_governance"]["available"] is False
    assert result["sections"]["regime_attribution"]["available"] is False
    assert result["sections"]["dynamic_strategy_allocation"]["available"] is False
    tier3 = result["sections"]["tier3_research_controls"]
    assert tier3["recommendation"] == "No promotion recommended"
    assert "promotion_governance_incomplete" in tier3["blockers"]
    final = result["sections"]["final_control_summary"]
    assert final["current_recommendation"] == "No promotion recommended"
    actions = " ".join(result["sections"]["recommended_next_actions"])
    assert "build_promotion_governance.py" in actions
    assert "build_regime_attribution.py" in actions
    assert "build_dynamic_strategy_allocation.py" in actions


def test_tier3_no_promotion_under_blockers(tmp_path):
    """Even if promotion_governance names a candidate, the final
    recommendation stays conservative when Tier 1 or Tier 2 carry
    blockers."""
    trade_date = "2026-06-02"
    _write_attribution(tmp_path, trade_date)
    _write_decision(tmp_path, trade_date)
    _write_tier1_sources(tmp_path, trade_date)
    _write_tier2_sources(tmp_path, trade_date, deep_verdict="WEAK_DIFFERENTIATION")
    _write_tier3_sources(
        tmp_path,
        trade_date,
        promotion_recommendation="caerus_lyra",
        promotion_governance_allows_change=True,
        allocation_recommendation="benchmark_heavy",
    )

    result = build_research_review_packet(trade_date=trade_date, repo_root=tmp_path)
    final = result["sections"]["final_control_summary"]
    # Tier 2 still says no promotion because of weak deep differentiation,
    # so the final rollup must stay conservative.
    assert final["current_recommendation"] == "No promotion recommended"
    assert final["allocation_status"] == "no_allocation_change_recommended"
    assert final["polaris_status"] == "BENCHMARK_CONTROL"


def test_tier3_surfaces_regime_findings(tmp_path):
    trade_date = "2026-06-02"
    _write_attribution(tmp_path, trade_date)
    _write_decision(tmp_path, trade_date)
    _write_tier1_sources(tmp_path, trade_date)
    _write_tier2_sources(tmp_path, trade_date, deep_verdict="STRONG_DIFFERENTIATION")
    _write_tier3_sources(tmp_path, trade_date)

    result = build_research_review_packet(trade_date=trade_date, repo_root=tmp_path)
    regime = result["sections"]["regime_attribution"]
    assert regime["available"] is True
    assert sum(regime["regime_distribution"].values()) > 0
    assert regime["history_window"]["classified_days"] >= 200


def test_tier3_surfaces_dynamic_allocation_findings(tmp_path):
    trade_date = "2026-06-02"
    _write_attribution(tmp_path, trade_date)
    _write_decision(tmp_path, trade_date)
    _write_tier1_sources(tmp_path, trade_date)
    _write_tier2_sources(tmp_path, trade_date, deep_verdict="STRONG_DIFFERENTIATION")
    _write_tier3_sources(tmp_path, trade_date)

    result = build_research_review_packet(trade_date=trade_date, repo_root=tmp_path)
    alloc = result["sections"]["dynamic_strategy_allocation"]
    assert alloc["is_research_only"] is True
    assert alloc["production_weights_modified"] is False
    assert len(alloc["ranking"]) >= 1

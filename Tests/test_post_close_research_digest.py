from __future__ import annotations

import json
from pathlib import Path

from scripts.send_post_close_research_digest import build_digest_email, select_target_date


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _write_review(root: Path, trade_date: str) -> None:
    base = root / "outputs" / "research_review" / trade_date
    _write_json(
        base / "research_review.json",
        {
            "date": trade_date,
            "cio_briefing": {
                "cio_takeaway": "Research readiness is MEDIUM and attribution is operational.",
                "thirty_second_read": {
                    "readiness": "MEDIUM",
                    "confidence": "LOW",
                    "leading_strategy": "caerus_polaris",
                    "main_contributor": "AAA",
                    "main_detractor": "BBB",
                    "biggest_blocker": "missing model review",
                    "recommended_action": "Run scripts/weekly_model_review.py.",
                },
                "strategy_leaderboard": [
                    {
                        "rank": 1,
                        "strategy": "caerus_polaris",
                        "hit_rate": 0.5,
                        "average_realized_return": 0.02,
                        "average_pnl_contribution": 0.01,
                    }
                ],
                "attribution_interpretation": {"narrative": "AAA led while BBB detracted."},
                "signal_evidence_assessment": {
                    "conclusion": "Signal evidence is not yet differentiated; sample size remains too small to identify a durable signal edge.",
                    "confidence": "LOW",
                },
                "risk_blocker_assessment": {"narrative": "The primary blocker is missing model review."},
                "cio_recommendation": {"primary": "Run scripts/weekly_model_review.py.", "secondary": []},
            },
            "overall": {
                "readiness": "MEDIUM",
                "confidence": "LOW",
                "reason_codes": ["missing_model_review"],
            },
            "sections": {
                "position_attribution": {
                    "available": True,
                    "total_positions_analyzed": 2,
                    "is_price_source_fresh": True,
                    "price_source_max_date": trade_date,
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
                },
                "decision_attribution": {
                    "available": True,
                    "decisions_analyzed": 2,
                },
                "signal_quality": {
                    "strongest_observed_signal": {"signal_name": "momentum_score"},
                    "weakest_observed_signal": {"signal_name": "momentum_rank"},
                    "confidence": "LOW",
                    "reason_codes": ["signal_evidence_sample_size_low"],
                },
                "data_freshness": {
                    "reason_codes": ["missing_model_review", "detected_regime_date_differs_from_packet_date"],
                },
                "risk_coverage": {"available": True, "risk_level": "MEDIUM"},
                "strategy_differentiation_deep": {"available": True, "aggregate_verdict": "WEAK_DIFFERENTIATION"},
                "position_sizing_research": {"available": True},
                "universe_governance": {"available": False},
                "tier2_research_controls": {"recommendation": "No promotion recommended"},
                "recommended_next_actions": [
                    "Run scripts/weekly_model_review.py.",
                    "Accumulate more decision attribution observations.",
                ],
            },
        },
    )
    _write_json(
        base / "research_review_summary.json",
        {
            "date": trade_date,
            "overall": {"readiness": "MEDIUM", "confidence": "LOW"},
        },
    )


def test_build_digest_email_extracts_summary_fields(tmp_path):
    trade_date = "2026-04-30"
    _write_review(tmp_path, trade_date)

    digest = build_digest_email(tmp_path, trade_date)

    assert digest["subject"] == "[Alpha Stack] CIO Research Briefing — 2026-04-30"
    assert digest["body_text"].startswith("CIO Briefing")
    assert "30-Second Read" in digest["body_text"]
    assert digest["body_text"].index("CIO Briefing") < digest["body_text"].index("Technical Appendix")
    assert "Research readiness: MEDIUM" in digest["body_text"]
    assert "Positions analyzed: 2" in digest["body_text"]
    assert "Decisions analyzed: 2" in digest["body_text"]
    assert "Tier 2 risk coverage: available (MEDIUM)" in digest["body_text"]
    assert "Deep differentiation verdict: WEAK_DIFFERENTIATION" in digest["body_text"]
    assert "Tier 2 recommendation: No promotion recommended" in digest["body_text"]
    assert "caerus_polaris: AAA ret=0.1 pnl=0.05" in digest["body_text"]
    assert "missing_model_review" in digest["body_text"]
    assert "outputs/research_review/2026-04-30/research_review.html" in digest["body_text"]
    assert "CIO Research Briefing" in digest["body_html"]


def test_select_target_date_prefers_successful_price_hydration(tmp_path):
    _write_json(
        tmp_path / "outputs" / "price_hydration" / "2026-04-29" / "status.json",
        {"status": "OK", "as_of_date": "2026-04-29"},
    )
    _write_json(
        tmp_path / "outputs" / "price_hydration" / "2026-04-30" / "status.json",
        {"status": "FAILED", "as_of_date": "2026-04-30"},
    )
    _write_json(
        tmp_path / "outputs" / "decision_attribution" / "2026-04-30" / "strategy_decision_summary.json",
        {"date": "2026-04-30"},
    )

    target, reasons = select_target_date(tmp_path)

    assert target == "2026-04-29"
    assert reasons == ["date_selected_latest_successful_price_hydration"]


def test_select_target_date_falls_back_to_shadow_artifact(tmp_path):
    _write_json(
        tmp_path / "outputs" / "shadow_candidates" / "2026-04-30" / "caerus_polaris.json",
        {"strategy_slug": "caerus_polaris"},
    )

    target, reasons = select_target_date(tmp_path)

    assert target == "2026-04-30"
    assert reasons == ["date_selected_latest_shadow_candidate_artifact"]


def test_select_target_date_explicit_override(tmp_path):
    target, reasons = select_target_date(tmp_path, "2026-04-30")

    assert target == "2026-04-30"
    assert reasons == ["date_explicit"]

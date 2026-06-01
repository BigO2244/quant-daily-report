from __future__ import annotations

import json
from pathlib import Path

from research.cio_briefing import build_cio_briefing
from scripts.send_post_close_research_digest import build_digest_email


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _packet(
    *,
    date: str = "2026-06-01",
    readiness: str = "MEDIUM",
    confidence: str = "LOW",
    risk_available: bool = True,
    strategies: list[dict] | None = None,
    signals: list[dict] | None = None,
) -> dict:
    if strategies is None:
        strategies = [
            {
                "strategy": "caerus_orion",
                "decisions_analyzed": 5,
                "hit_rate": 0.6,
                "average_realized_return": 0.02,
                "average_pnl_contribution": 0.01,
                "top_decision": {"symbol": "AAA", "realized_return": 0.1, "pnl_contribution": 0.05},
                "worst_decision": {"symbol": "BBB", "realized_return": -0.02, "pnl_contribution": -0.01},
                "confidence": "MEDIUM",
            }
        ]
    if signals is None:
        signals = [
            {
                "signal_name": "momentum_score",
                "observations": 20,
                "average_realized_return": 0.02,
                "hit_rate": 0.6,
                "confidence": "LOW",
            }
        ]
    risk_reason = ["ok"] if risk_available else ["missing_risk_summary"]
    health = [
        {
            "artifact_name": "risk",
            "status": "PRESENT" if risk_available else "MISSING",
            "reason_codes": risk_reason,
            "confidence": "MEDIUM" if risk_available else "LOW",
        }
    ]
    return {
        "date": date,
        "overall": {
            "readiness": readiness,
            "confidence": confidence,
            "biggest_blocker": "missing_risk" if not risk_available else "No blocking artifact gaps detected.",
            "reason_codes": [] if risk_available else ["missing_risk"],
        },
        "sections": {
            "position_attribution": {
                "available": True,
                "total_positions_analyzed": 2,
                "is_price_source_fresh": True,
                "aggregate_confidence": "HIGH",
                "top_contributor_per_strategy": {
                    "caerus_orion": {"strategy": "caerus_orion", "symbol": "AAA", "return_pct": 0.1, "pnl_contribution_pct": 0.05},
                    "caerus_polaris": {"strategy": "caerus_polaris", "symbol": "AAA", "return_pct": 0.08, "pnl_contribution_pct": 0.04},
                },
                "top_detractor_per_strategy": {
                    "caerus_orion": {"strategy": "caerus_orion", "symbol": "BBB", "return_pct": -0.02, "pnl_contribution_pct": -0.01},
                    "caerus_polaris": {"strategy": "caerus_polaris", "symbol": "CCC", "return_pct": -0.03, "pnl_contribution_pct": -0.02},
                },
                "reason_codes": ["ok"],
            },
            "decision_attribution": {
                "available": True,
                "decisions_analyzed": sum(int(row.get("decisions_analyzed") or 0) for row in strategies),
                "strategies": strategies,
                "signals": signals,
                "confidence": "MEDIUM",
                "reason_codes": ["ok"],
            },
            "signal_quality": {
                "signals": signals,
                "confidence": "LOW",
                "reason_codes": ["signal_evidence_sample_size_low"],
            },
            "risk_concentration": {
                "available": risk_available,
                "reason_codes": risk_reason,
            },
            "data_freshness": {
                "health_table": health,
                "reason_codes": [] if risk_available else ["missing_risk_summary"],
            },
            "recommended_next_actions": ["Accumulate more decision attribution observations."],
        },
    }


def test_cio_briefing_generated_with_core_sections(tmp_path):
    briefing = build_cio_briefing(_packet(), tmp_path)

    assert briefing["cio_takeaway"]
    assert briefing["thirty_second_read"]["readiness"] == "MEDIUM"
    assert briefing["strategy_leaderboard"][0]["strategy"] == "caerus_orion"
    assert briefing["cio_recommendation"]["primary"]


def test_missing_prior_review_is_nonfatal(tmp_path):
    briefing = build_cio_briefing(_packet(), tmp_path)

    changed = briefing["what_changed_since_prior_review"]
    assert changed["reason_codes"] == ["prior_review_missing"]
    assert changed["narrative"] == "No prior review packet available for comparison."


def test_prior_review_comparison_detects_changes(tmp_path):
    prior = _packet(
        date="2026-05-31",
        readiness="LOW",
        confidence="LOW",
        risk_available=False,
        strategies=[
            {
                "strategy": "caerus_lyra",
                "decisions_analyzed": 1,
                "hit_rate": 0.0,
                "average_realized_return": -0.01,
                "average_pnl_contribution": -0.01,
                "confidence": "LOW",
            }
        ],
    )
    _write_json(tmp_path / "outputs" / "research_review" / "2026-05-31" / "research_review.json", prior)

    briefing = build_cio_briefing(_packet(), tmp_path)
    changed = briefing["what_changed_since_prior_review"]

    assert changed["readiness_change"] == {"from": "LOW", "to": "MEDIUM"}
    assert changed["confidence_change"] == {"from": "LOW", "to": "LOW"}
    assert changed["positions_analyzed_change"] == 0
    assert changed["decisions_analyzed_change"] == 4
    assert changed["strategy_leader_change"] == {"from": "caerus_lyra", "to": "caerus_orion"}
    assert "missing_risk" in changed["resolved_blockers"]


def test_strategy_leaderboard_sort_is_deterministic(tmp_path):
    briefing = build_cio_briefing(
        _packet(
            strategies=[
                {"strategy": "ccc", "decisions_analyzed": 1, "hit_rate": 0.9, "average_realized_return": 0.02, "average_pnl_contribution": 0.01},
                {"strategy": "aaa", "decisions_analyzed": 1, "hit_rate": 0.5, "average_realized_return": 0.03, "average_pnl_contribution": 0.02},
                {"strategy": "bbb", "decisions_analyzed": 1, "hit_rate": 0.7, "average_realized_return": 0.03, "average_pnl_contribution": 0.02},
                {"strategy": "ddd", "decisions_analyzed": 1, "hit_rate": 0.8, "average_realized_return": 0.01, "average_pnl_contribution": 0.02},
            ]
        ),
        tmp_path,
    )

    assert [row["strategy"] for row in briefing["strategy_leaderboard"]] == ["bbb", "aaa", "ddd", "ccc"]


def test_signal_degenerate_case_uses_plain_undifferentiated_language(tmp_path):
    briefing = build_cio_briefing(
        _packet(
            signals=[
                {
                    "signal_name": "estimated_holding_period_days",
                    "observations": 20,
                    "average_realized_return": 0.02,
                    "hit_rate": 0.6,
                    "confidence": "LOW",
                }
            ]
        ),
        tmp_path,
    )

    conclusion = briefing["signal_evidence_assessment"]["conclusion"]
    assert "not yet differentiated" in conclusion
    assert "signal_evidence_not_differentiated" in briefing["signal_evidence_assessment"]["reason_codes"]


def test_missing_risk_summary_prioritizes_risk_recommendation(tmp_path):
    briefing = build_cio_briefing(_packet(risk_available=False), tmp_path)

    assert "risk/concentration" in briefing["cio_recommendation"]["primary"]
    assert briefing["risk_blocker_assessment"]["prevents_confidence_upgrade"] is True
    assert "risk/concentration summary" in briefing["risk_blocker_assessment"]["narrative"]


def test_email_body_leads_with_cio_briefing(tmp_path):
    packet = _packet()
    packet["cio_briefing"] = build_cio_briefing(packet, tmp_path)
    packet["sections"]["cio_briefing"] = packet["cio_briefing"]
    _write_json(tmp_path / "outputs" / "research_review" / "2026-06-01" / "research_review.json", packet)
    _write_json(
        tmp_path / "outputs" / "research_review" / "2026-06-01" / "research_review_summary.json",
        {"date": "2026-06-01", "cio_briefing": packet["cio_briefing"]},
    )

    digest = build_digest_email(tmp_path, "2026-06-01")

    assert digest["subject"] == "[Alpha Stack] CIO Research Briefing — 2026-06-01"
    assert digest["body_text"].startswith("CIO Briefing")
    assert digest["body_text"].index("30-Second Read") < digest["body_text"].index("Technical Appendix")
    assert "Strategy Leaderboard" in digest["body_text"]
    assert "Recommended Action" in digest["body_text"]


def test_cio_briefing_maps_2026_05_29_review_packet_alias_fields(tmp_path):
    packet = {
        "date": "2026-05-29",
        "overall": {
            "readiness": "MEDIUM",
            "confidence": "LOW",
            "reason_codes": ["missing_risk"],
            "biggest_blocker": "missing_risk_summary",
        },
        "sections": {
            "position_attribution": {
                "available": True,
                "positions_analyzed": 20,
                "is_price_source_fresh": True,
                "aggregate_confidence": "MEDIUM",
                "top_contributors": {
                    "positions": [
                        {
                            "strategy_id": "caerus_orion",
                            "ticker": "MU",
                            "return_pct": 0.041,
                            "pnl_contribution": 0.012,
                        },
                        {
                            "strategy_id": "caerus_polaris",
                            "ticker": "PCAR",
                            "return_pct": 0.025,
                            "pnl_contribution": 0.004,
                        },
                    ]
                },
                "top_detractors": {
                    "positions": [
                        {
                            "strategy_id": "caerus_orion",
                            "ticker": "INTC",
                            "return_pct": -0.030,
                            "pnl_contribution": -0.009,
                        },
                        {
                            "strategy_id": "caerus_polaris",
                            "ticker": "WBD",
                            "return_pct": -0.020,
                            "pnl_contribution": -0.004,
                        },
                    ]
                },
                "reason_codes": ["ok"],
            },
            "decision_attribution": {
                "total_decisions_analyzed": 20,
                "strategy_decision_summary": {
                    "strategies": [
                        {
                            "strategy_id": "caerus_polaris",
                            "decision_count": 10,
                            "hit_rate": 0.5,
                            "average_return": 0.004,
                            "avg_pnl_contribution": 0.001,
                            "confidence": "LOW",
                        },
                        {
                            "strategy_id": "caerus_orion",
                            "decision_count": 5,
                            "hit_rate": 0.8,
                            "average_return": 0.012,
                            "avg_pnl_contribution": 0.004,
                            "top_contributor": {"ticker": "MU", "pnl_contribution": 0.012, "realized_return": 0.041},
                            "top_detractor": {"ticker": "INTC", "pnl_contribution": -0.009, "realized_return": -0.030},
                            "confidence": "MEDIUM",
                        },
                        {
                            "strategy_id": "caerus_lyra",
                            "decision_count": 5,
                            "hit_rate": 0.4,
                            "average_return": -0.001,
                            "avg_pnl_contribution": -0.001,
                            "confidence": "LOW",
                        },
                    ]
                },
                "signal_outcome_summary": {
                    "signals": [
                        {
                            "name": "momentum_score",
                            "observations": 20,
                            "average_realized_return": 0.012,
                            "hit_rate": 0.8,
                            "confidence": "LOW",
                        }
                    ]
                },
                "reason_codes": ["ok"],
            },
            "risk_concentration": {
                "available": False,
                "reason_codes": ["missing_risk_summary"],
            },
            "data_freshness": {
                "health_table": [
                    {
                        "artifact_name": "risk",
                        "status": "MISSING",
                        "reason_codes": ["missing_risk_summary"],
                    }
                ],
                "reason_codes": ["missing_risk_summary"],
            },
            "recommended_next_actions": [
                "Regenerate research clarity or attribution exposure artifacts to populate concentration and sector risk."
            ],
        },
    }

    briefing = build_cio_briefing(packet, tmp_path)

    assert "20 positions and 20 decisions" in briefing["cio_takeaway"]
    assert briefing["thirty_second_read"]["leading_strategy"] == "caerus_orion"
    assert briefing["thirty_second_read"]["main_contributor"] == "MU"
    assert briefing["thirty_second_read"]["main_detractor"] == "INTC"
    assert briefing["strategy_leaderboard"][0]["strategy"] == "caerus_orion"

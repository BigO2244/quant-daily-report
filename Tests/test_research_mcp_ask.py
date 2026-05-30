"""Coverage for the operator-facing research-MCP CLI bridge.

The tests cover three concerns:

1. Pure rendering — given a structured payload, the human and Markdown
   outputs are deterministic and contain the headline numbers the
   operator needs.
2. CLI behaviour — argument parsing, artifact writing, exit codes for
   each terminal status, ``--no-write`` and ``--raw-json`` paths.
3. End-to-end against the real MCP — the script routes through the
   actual ``call_tool`` and produces non-empty output without raising.

Nothing here writes outside ``tmp_path`` (or, for the end-to-end test,
the standard outputs root with a unique timestamp).
"""

from __future__ import annotations

import datetime as dt
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import research_mcp_ask as rma


REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Pure rendering
# ---------------------------------------------------------------------------


def _ok_payload() -> dict:
    return {
        "status": "OK",
        "tool": "answer_research_question",
        "question": "Does timing matter more in high VIX regimes?",
        "intent": "timing_by_vix_regime",
        "routed_to": "execution_timing_by_vix_regime",
        "warnings": [],
        "answer": {
            "status": "OK",
            "run_date": "2026-05-29",
            "baseline_offset": "T+5m",
            "cache_key_version": "intraday_bars_v1_iex_0925_1030",
            "offsets": ["T+0m", "T+1m", "T+5m", "T+10m"],
            "coverage": {"insufficient_sample_threshold": 5},
            "regime_aggregates": [
                {
                    "regime": "ELEVATED",
                    "n_days": 12,
                    "insufficient_sample": False,
                    "by_offset": {
                        "T+0m": {"mean_opportunity_usd": 5.50, "median_opportunity_usd": 5.10},
                        "T+1m": {"mean_opportunity_usd": 4.20, "median_opportunity_usd": 4.00},
                        "T+5m": {"mean_opportunity_usd": 0.0, "median_opportunity_usd": 0.0},
                        "T+10m": {"mean_opportunity_usd": -3.10, "median_opportunity_usd": -2.80},
                    },
                },
                {
                    "regime": "NORMAL",
                    "n_days": 3,
                    "insufficient_sample": True,
                    "by_offset": {
                        "T+0m": {"mean_opportunity_usd": 0.20, "median_opportunity_usd": 0.10},
                        "T+1m": {"mean_opportunity_usd": 0.10, "median_opportunity_usd": 0.05},
                        "T+5m": {"mean_opportunity_usd": 0.0, "median_opportunity_usd": 0.0},
                        "T+10m": {"mean_opportunity_usd": -0.50, "median_opportunity_usd": -0.40},
                    },
                },
            ],
        },
    }


def test_render_ok_payload_includes_headline_numbers_and_baseline():
    human, md = rma.render_human_and_markdown("Does timing matter?", _ok_payload())

    # Headline numbers visible in human view.
    assert "ELEVATED" in human
    assert "12" in human  # n_days
    assert "$5.50" in human  # T+0 mean for ELEVATED
    assert "T+5m" in human  # baseline marker
    # Insufficient-sample tag rendered for the small-N regime.
    assert "NORMAL *" in human or "* = insufficient" in human

    # Markdown carries the same content in a table format.
    assert "| regime |" in md or "| regime " in md
    assert "ELEVATED" in md
    assert "$5.50" in md
    assert "Per-regime opportunity" in md


def test_render_no_timing_data_includes_next_command():
    payload = {
        "status": "NO_TIMING_DATA",
        "tool": "answer_research_question",
        "warnings": ["execution_timing replay has not been run yet"],
        "answer": {
            "status": "NO_TIMING_DATA",
            "reason": "no timing-replay run found",
            "regime_aggregates": [],
        },
    }
    human, md = rma.render_human_and_markdown("Q", payload)
    assert "outputs/research/execution_timing" in human
    assert "scripts.research.execution_timing_replay" in human
    assert "Required artifact missing" in md


def test_render_bad_regime_schema_lists_missing_columns():
    payload = {
        "status": "BAD_REGIME_SCHEMA",
        "warnings": [],
        "answer": {
            "status": "BAD_REGIME_SCHEMA",
            "missing_columns": ["date|as_of|execution_date"],
            "regime_aggregates": [],
        },
    }
    human, _ = rma.render_human_and_markdown("Q", payload)
    assert "date|as_of|execution_date" in human
    assert "_REGIME_DATE_COLUMN_CANDIDATES" in human


def test_render_unsupported_intent_lists_supported_phrasings():
    payload = {
        "status": "UNSUPPORTED_INTENT",
        "intent": None,
        "warnings": [],
        "available_intents": [
            {
                "intent": "timing_by_vix_regime",
                "matches": ["timing + VIX", "timing + regime", "high VIX + timing"],
                "example_question": "Does execution timing matter more in high-VIX regimes?",
            }
        ],
    }
    human, md = rma.render_human_and_markdown("alpha?", payload)
    assert "timing + VIX" in human
    assert "Does execution timing matter more in high-VIX regimes?" in human
    assert "Supported phrasings" in md


def test_status_to_exit_code_mapping():
    assert rma.status_to_exit_code("OK") == 0
    assert rma.status_to_exit_code("NO_TIMING_DATA") == 2
    assert rma.status_to_exit_code("NO_REGIME_DATA") == 2
    assert rma.status_to_exit_code("BAD_REGIME_SCHEMA") == 2
    assert rma.status_to_exit_code("NEEDS_DATA") == 2
    assert rma.status_to_exit_code("UNSUPPORTED_INTENT") == 3
    assert rma.status_to_exit_code("NEEDS_CAPABILITY") == 3
    assert rma.status_to_exit_code("WEIRD_FUTURE_STATUS") == 0  # clean default


def test_render_needs_data_lists_missing_artifacts():
    payload = {
        "status": "NEEDS_DATA",
        "tool": "answer_research_question",
        "intent": "timing_by_vix_regime",
        "routed_to": "execution_timing_by_vix_regime",
        "matched_capability": {"name": "timing_by_vix_regime", "description": "Stratify timing by VIX regime."},
        "missing_artifacts": [
            "outputs/research/execution_timing/*/timing_summary.json",
            "outputs/vix_regime/regime_history.csv",
        ],
        "warnings": ["missing artifact: outputs/research/execution_timing/*/timing_summary.json"],
    }
    human, md = rma.render_human_and_markdown("Q", payload)
    assert "Capability matched: timing_by_vix_regime" in human
    assert "outputs/research/execution_timing/*/timing_summary.json" in human
    assert "outputs/vix_regime/regime_history.csv" in human
    assert "Required artifacts missing for `timing_by_vix_regime`" in md


def test_render_needs_capability_shows_suggested_next_build():
    payload = {
        "status": "NEEDS_CAPABILITY",
        "tool": "answer_research_question",
        "intent": "shadow_comparison",
        "routed_to": None,
        "matched_capability": {
            "name": "shadow_comparison",
            "description": "Pairwise strategy comparison on shadow performance.",
        },
        "suggested_next_build": (
            "Add a shadow_comparison MCP tool that reads outputs/shadow_candidates/ "
            "and computes pairwise deltas."
        ),
        "warnings": [],
    }
    human, md = rma.render_human_and_markdown("Polaris vs Orion?", payload)
    assert "Capability matched: shadow_comparison" in human
    assert "Pairwise strategy comparison" in human
    assert "Suggested next build:" in human
    assert "shadow_candidates" in human
    assert "Capability `shadow_comparison`" in md
    assert "Suggested next build" in md


def _attribution_payload() -> dict:
    """Synthesized attribution payload mirroring real shape."""
    return {
        "status": "OK",
        "tool": "answer_research_question",
        "question": "Why did Orion outperform Polaris?",
        "intent": "attribution_analysis",
        "routed_to": "attribution_analysis",
        "warnings": [],
        "answer": {
            "status": "OK",
            "trade_date": "2026-04-30",
            "leader_by_return": "caerus_orion",
            "panels": {
                "caerus_orion": {
                    "strategy_name": "Orion",
                    "portfolio_return_21d": 0.41,
                    "market_beta": 2.04,
                    "hidden_factor_flags": ["high_market_beta", "sector_concentration"],
                    "top_contributor": {
                        "ticker": "STX", "sector": "Information Technology",
                        "contribution": 0.144, "weight": 0.20,
                    },
                    "top_detractor": {
                        "ticker": "WBD", "sector": "Communication Services",
                        "contribution": -0.003, "weight": 0.10,
                    },
                    "top_drawdown_contributors": [
                        {"ticker": "MU", "sector": "IT", "contribution_to_drawdown": -0.026},
                        {"ticker": "LRCX", "sector": "IT", "contribution_to_drawdown": -0.020},
                    ],
                    "sector_exposure": {
                        "weights": {"Information Technology": 0.8, "Industrials": 0.2},
                        "max_sector_weight": 0.8, "sector_hhi": 0.68,
                    },
                    "factor_exposures": {"weighted_12_1_momentum": 2.5, "weighted_20d_ann_vol": 0.48},
                    "regime_performance": {},
                    "unavailable_metrics": [],
                },
                "caerus_polaris": {
                    "strategy_name": "Polaris",
                    "portfolio_return_21d": 0.293,
                    "market_beta": 1.83,
                    "hidden_factor_flags": ["high_market_beta", "sector_concentration"],
                    "top_contributor": {
                        "ticker": "STX", "sector": "Information Technology",
                        "contribution": 0.072, "weight": 0.10,
                    },
                    "top_detractor": {
                        "ticker": "WBD", "sector": "Communication Services",
                        "contribution": -0.0015, "weight": 0.10,
                    },
                    "top_drawdown_contributors": [
                        {"ticker": "MU", "sector": "IT", "contribution_to_drawdown": -0.026},
                    ],
                    "sector_exposure": {
                        "weights": {"Information Technology": 0.6, "Industrials": 0.2},
                        "max_sector_weight": 0.6, "sector_hhi": 0.42,
                    },
                    "factor_exposures": {"weighted_12_1_momentum": 2.52, "weighted_20d_ann_vol": 0.48},
                    "regime_performance": {},
                    "unavailable_metrics": [],
                },
            },
            "comparison": {
                "outperformer": "caerus_orion",
                "underperformer": "caerus_polaris",
                "outperformance": 0.117,
                "explicitly_requested": True,
            },
            "narrative": (
                "Performance attribution for trade date 2026-04-30.\n"
                "  • Orion: 21d return +41.00%. Top contributor: STX.\n"
                "  • Polaris: 21d return +29.30%. Top contributor: STX."
            ),
        },
    }


def test_render_attribution_ok_shows_per_strategy_panel():
    human, md = rma.render_human_and_markdown("Why did Orion outperform Polaris?", _attribution_payload())
    # Table header + per-strategy rows.
    assert "strategy" in human and "21d_return" in human
    assert "caerus_orion" in human
    assert "caerus_polaris" in human
    # Signed-percent formatting.
    assert "+41.00%" in human
    assert "+29.30%" in human
    # Top contributor / detractor rendered as "TICKER ±X%".
    assert "STX" in human
    assert "WBD" in human
    # Beta shown.
    assert "2.04" in human
    assert "1.83" in human
    # Sector concentration line includes the dominant sector name.
    assert "Information Technology" in human
    # Markdown variant includes the trade date heading and the table.
    assert "Attribution panels — trade date `2026-04-30`" in md
    assert "| caerus_orion |" in md


def test_render_attribution_ok_shows_hidden_factor_flags():
    human, _ = rma.render_human_and_markdown("What drove returns?", _attribution_payload())
    assert "hidden_factor_flags" in human
    assert "high_market_beta" in human
    assert "sector_concentration" in human


def test_render_attribution_ok_shows_top_drawdown_contributors():
    human, _ = rma.render_human_and_markdown("What drove returns?", _attribution_payload())
    assert "top drawdown contributors" in human
    # First two drawdown rows for orion appear in the human view.
    assert "MU" in human
    assert "LRCX" in human


def test_render_attribution_ok_shows_comparison_block():
    human, md = rma.render_human_and_markdown("Why did Orion outperform Polaris?", _attribution_payload())
    assert "Comparison:" in human
    assert "caerus_orion outperformed caerus_polaris" in human
    assert "+11.70%" in human  # the outperformance gap
    assert "(explicitly requested)" in human
    assert "**Comparison:**" in md


def test_render_attribution_ok_shows_narrative():
    human, md = rma.render_human_and_markdown("Why did Orion outperform Polaris?", _attribution_payload())
    assert "Narrative:" in human
    assert "Performance attribution for trade date 2026-04-30" in human
    # Markdown wraps the narrative in a fenced code block.
    assert "```" in md


def _strategy_differentiation_payload() -> dict:
    return {
        "status": "OK",
        "tool": "answer_research_question",
        "question": "Which strategies are most similar?",
        "intent": "strategy_differentiation",
        "routed_to": "strategy_differentiation",
        "warnings": [],
        "answer": {
            "status": "OK",
            "trade_date": "2026-04-30",
            "diversification_verdict": "moderate_diversification",
            "diversification_rationale": "1 highly_overlapping; 2 partially_differentiated across 3 pairs.",
            "common_factor_flags": ["high_market_beta", "sector_concentration"],
            "most_similar_pair": {
                "left_slug": "caerus_orion",
                "right_slug": "caerus_lyra",
                "similarity_score": 0.907,
            },
            "most_differentiated_pair": {
                "left_slug": "caerus_polaris",
                "right_slug": "caerus_orion",
                "similarity_score": 0.633,
            },
            "pairwise_differentiation": [
                {
                    "left_slug": "caerus_orion", "right_slug": "caerus_lyra",
                    "holdings_overlap_pct": 0.8,
                    "shared_top_sector": "Information Technology",
                    "shared_top_contributor": "STX",
                    "shared_drawdown_contributors": ["MU", "STX"],
                    "factor_proximity_score": 0.95,
                    "sector_overlap_score": 0.91,
                    "similarity_score": 0.907,
                    "verdict": "highly_overlapping",
                    "caveats": [],
                },
                {
                    "left_slug": "caerus_polaris", "right_slug": "caerus_orion",
                    "holdings_overlap_pct": 0.5,
                    "shared_top_sector": "Information Technology",
                    "shared_top_contributor": "STX",
                    "shared_drawdown_contributors": ["MU"],
                    "factor_proximity_score": 0.85,
                    "sector_overlap_score": 0.94,
                    "similarity_score": 0.633,
                    "verdict": "partially_differentiated",
                    "caveats": [],
                },
            ],
            "narrative": (
                "Strategy differentiation for trade date 2026-04-30 (2 pairs).\n"
                "  • caerus_orion ↔ caerus_lyra: verdict=highly_overlapping, similarity=0.907, …\n"
                "Diversification: moderate_diversification."
            ),
        },
    }


def test_render_strategy_differentiation_shows_pairwise_table():
    human, md = rma.render_human_and_markdown(
        "Which strategies are most similar?",
        _strategy_differentiation_payload(),
    )
    assert "pair" in human and "verdict" in human and "similarity" in human
    assert "caerus_orion↔caerus_lyra" in human
    assert "highly_overlapping" in human
    assert "partially_differentiated" in human
    assert "Information Technology" not in human  # not in row (column truncated names elsewhere) — sector shows in shared_top
    assert "STX" in human
    assert "## Strategy differentiation — trade date `2026-04-30`" in md
    assert "| caerus_orion↔caerus_lyra |" in md


def test_render_strategy_differentiation_shows_common_flags_and_verdict():
    human, _ = rma.render_human_and_markdown(
        "Are the strategies the same factor bet?",
        _strategy_differentiation_payload(),
    )
    assert "Diversification: moderate_diversification" in human
    assert "Common factor flags across all strategies:" in human
    assert "high_market_beta" in human


def test_render_strategy_differentiation_shows_most_similar_and_most_differentiated():
    human, _ = rma.render_human_and_markdown(
        "Compare strategy overlap.",
        _strategy_differentiation_payload(),
    )
    assert "Most similar: caerus_orion ↔ caerus_lyra" in human
    assert "Most differentiated: caerus_polaris ↔ caerus_orion" in human


def test_render_strategy_differentiation_includes_narrative():
    human, md = rma.render_human_and_markdown(
        "Do we have diversification across strategies?",
        _strategy_differentiation_payload(),
    )
    assert "Narrative:" in human
    assert "Strategy differentiation for trade date 2026-04-30" in human
    assert "```" in md


def _promotion_readiness_payload() -> dict:
    """Synthesized strategy-aware promotion-readiness payload."""
    return {
        "status": "OK",
        "tool": "answer_research_question",
        "question": "Compare Polaris and Orion promotion readiness.",
        "intent": "promotion_readiness",
        "routed_to": "promotion_readiness",
        "warnings": [],
        "answer": {
            "status": "OK",
            "strategy_trade_date": "2026-04-30",
            "closest_to_promotion": "caerus_polaris",
            "has_phase_c_sidecar": False,
            "ranking_by_recommendation": ["caerus_polaris", "caerus_orion"],
            "strategy_panels": {
                "caerus_polaris": {
                    "strategy_slug": "caerus_polaris",
                    "strategy_name": "Caerus Polaris",
                    "readiness_state": None,
                    "phase_c_confidence": None,
                    "recommendation": "hold",
                    "confidence": "LOW",
                    "reason_codes": [],
                    "blockers": [
                        "metric_unavailable:realized_volatility_ann",
                        "metric_unavailable:max_drawdown",
                        "insufficient_observation_window:0/20",
                    ],
                    "metrics": {
                        "data_status": "OK",
                        "excess_return_vs_spy": 0.0297,
                        "max_drawdown": None,
                        "realized_volatility_ann": None,
                        "avg_turnover": 0.10,
                    },
                    "valid_observation_windows": 0,
                    "explanation": "Excess return vs SPY is +0.0297. Hold pending: ...",
                    "unavailable_metrics": ["max_drawdown", "realized_volatility_ann"],
                },
                "caerus_orion": {
                    "strategy_slug": "caerus_orion",
                    "strategy_name": "Caerus Orion",
                    "readiness_state": None,
                    "phase_c_confidence": None,
                    "recommendation": "hold",
                    "confidence": "LOW",
                    "reason_codes": [],
                    "blockers": [
                        "metric_unavailable:realized_volatility_ann",
                        "metric_unavailable:max_drawdown",
                    ],
                    "metrics": {
                        "data_status": "OK",
                        "excess_return_vs_spy": 0.0168,
                        "max_drawdown": None,
                        "realized_volatility_ann": None,
                        "avg_turnover": 0.10,
                    },
                    "valid_observation_windows": 0,
                    "explanation": "Excess return vs SPY is +0.0168. Hold pending: ...",
                    "unavailable_metrics": ["max_drawdown", "realized_volatility_ann"],
                },
            },
            "strategy_warnings": [
                "no_promotion_readiness_sidecar: recommendation derived from shadow_evaluation metrics + per-strategy stability_analysis only",
            ],
        },
    }


def test_render_promotion_readiness_shows_per_strategy_table():
    human, md = rma.render_human_and_markdown(
        "Compare Polaris and Orion promotion readiness.",
        _promotion_readiness_payload(),
    )
    # Table header + per-strategy rows.
    assert "strategy" in human and "recommendation" in human
    assert "caerus_polaris" in human
    assert "caerus_orion" in human
    # Recommendation tier rendered.
    assert "hold" in human
    # Excess vs SPY in signed-percent format.
    assert "+2.97%" in human
    assert "+1.68%" in human
    # Trade date + closest-to-promotion header.
    assert "Trade date: 2026-04-30" in human
    assert "Closest to promotion: caerus_polaris" in human
    # Markdown variant has a panel section.
    assert "Promotion readiness — trade date `2026-04-30`" in md
    assert "| caerus_polaris |" in md


def test_render_promotion_readiness_shows_phase_c_missing_note():
    human, _ = rma.render_human_and_markdown(
        "Which strategy is closest to promotion?",
        _promotion_readiness_payload(),
    )
    assert "Phase C sidecar: missing" in human
    assert "shadow_evaluation + stability_analysis only" in human


def test_render_promotion_readiness_shows_blockers_and_explanation():
    human, _ = rma.render_human_and_markdown(
        "Why is Polaris not promotion-ready?",
        _promotion_readiness_payload(),
    )
    # Per-strategy block.
    assert "caerus_polaris:" in human
    assert "blockers:" in human
    assert "metric_unavailable:realized_volatility_ann" in human
    assert "explanation:" in human


def test_render_promotion_readiness_shows_strategy_warnings():
    human, md = rma.render_human_and_markdown(
        "Compare strategies.",
        _promotion_readiness_payload(),
    )
    assert "Strategy warnings:" in human
    assert "no_promotion_readiness_sidecar" in human
    assert "Strategy warnings" in md


def test_render_promotion_readiness_with_phase_c_present():
    payload = _promotion_readiness_payload()
    payload["answer"]["has_phase_c_sidecar"] = True
    payload["answer"]["strategy_panels"]["caerus_polaris"]["readiness_state"] = "CANDIDATE_FOR_CAPITAL"
    payload["answer"]["strategy_panels"]["caerus_polaris"]["recommendation"] = "promote"
    payload["answer"]["strategy_panels"]["caerus_polaris"]["confidence"] = "HIGH"
    human, _ = rma.render_human_and_markdown("Compare strategies.", payload)
    assert "Phase C sidecar: present" in human
    assert "promote" in human
    assert "HIGH" in human


def test_render_unsupported_includes_closest_capabilities():
    payload = {
        "status": "UNSUPPORTED_INTENT",
        "intent": None,
        "warnings": [],
        "closest_capabilities": [
            {"name": "morning_brief", "description": "Daily operator brief.", "example_questions": ["What ran today?"]},
        ],
        "available_intents": [
            {"intent": "morning_brief", "matches": ["What ran today?"], "example_question": "What ran today?"},
        ],
    }
    human, md = rma.render_human_and_markdown("totally off topic", payload)
    assert "Closest capabilities (by token overlap):" in human
    assert "morning_brief" in human
    assert "Closest capabilities" in md


def test_now_stamp_is_filesystem_safe():
    stamp = rma._now_stamp(dt.datetime(2026, 5, 29, 16, 35, 12, tzinfo=dt.timezone.utc))
    assert stamp == "2026-05-29T16-35-12Z"
    assert ":" not in stamp


# ---------------------------------------------------------------------------
# CLI behaviour
# ---------------------------------------------------------------------------


def _run_cli(argv: list[str], capsys, monkeypatch=None) -> tuple[int, str, str]:
    rc = rma.main(argv)
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def test_cli_writes_artifacts_for_unsupported_intent(tmp_path, capsys):
    rc, out, err = _run_cli(
        [
            "What was the alpha last quarter?",
            "--output-root", str(tmp_path / "out"),
            "--timestamp", "2026-05-29T16-35-12Z",
        ],
        capsys,
    )
    assert rc == 3
    assert "UNSUPPORTED_INTENT" in out
    artifact_dir = tmp_path / "out" / "2026-05-29T16-35-12Z"
    assert (artifact_dir / "answer.json").exists()
    assert (artifact_dir / "answer.md").exists()
    # answer.json round-trips as JSON.
    payload = json.loads((artifact_dir / "answer.json").read_text())
    assert payload["status"] == "UNSUPPORTED_INTENT"
    md = (artifact_dir / "answer.md").read_text()
    assert "Supported phrasings" in md


def test_cli_routes_timing_question_against_real_mcp_when_no_data(tmp_path, capsys):
    """The MCP knows the timing artifact is absent in tmp_path's pseudo-root.
    The script must still complete cleanly and write its artifacts."""
    rc, out, err = _run_cli(
        [
            "Does timing matter more in high VIX regimes?",
            "--output-root", str(tmp_path / "out"),
            "--timestamp", "2026-05-29T16-35-12Z",
        ],
        capsys,
    )
    # Exit 2 because on a clean machine outputs/research/execution_timing is
    # absent; on the VM with a populated cache this would be 0.
    assert rc in {0, 2}
    assert "Question:" in out
    assert "Status:" in out
    artifact_dir = tmp_path / "out" / "2026-05-29T16-35-12Z"
    payload = json.loads((artifact_dir / "answer.json").read_text())
    assert payload["intent"] == "timing_by_vix_regime"


def test_cli_no_write_skips_artifacts(tmp_path, capsys):
    rc, out, err = _run_cli(
        [
            "What was the alpha last quarter?",
            "--output-root", str(tmp_path / "out"),
            "--no-write",
        ],
        capsys,
    )
    assert rc == 3
    assert not (tmp_path / "out").exists()
    assert "Artifacts:" not in out  # no artifact section when --no-write


def test_cli_raw_json_emits_parseable_json(tmp_path, capsys):
    rc, out, err = _run_cli(
        [
            "What was the alpha last quarter?",
            "--output-root", str(tmp_path / "out"),
            "--timestamp", "2026-05-29T16-35-12Z",
            "--raw-json",
        ],
        capsys,
    )
    payload = json.loads(out)
    assert payload["status"] == "UNSUPPORTED_INTENT"


def test_cli_empty_question_is_rejected(tmp_path, capsys):
    rc, out, err = _run_cli(
        [
            "   ",
            "--output-root", str(tmp_path / "out"),
            "--no-write",
        ],
        capsys,
    )
    assert rc == 1
    assert "must not be empty" in err


def test_cli_unknown_tool_reports_error(tmp_path, capsys):
    """If --tool overrides to something that doesn't accept 'question', the
    script reports a clean error and exits 1 — it doesn't crash."""
    rc, out, err = _run_cli(
        [
            "Does timing matter more in high VIX regimes?",
            "--output-root", str(tmp_path / "out"),
            "--no-write",
            "--tool", "execution_timing_by_vix_regime",  # does NOT accept question
        ],
        capsys,
    )
    assert rc == 1
    assert "did not accept 'question'" in err


# ---------------------------------------------------------------------------
# Subprocess smoke — exercise the module entrypoint exactly as an operator
# would type it on the VM.
# ---------------------------------------------------------------------------


def test_module_entrypoint_runs_via_python_m(tmp_path):
    result = subprocess.run(
        [
            sys.executable, "-m", "scripts.research_mcp_ask",
            "Does timing matter more in high VIX regimes?",
            "--output-root", str(tmp_path / "out"),
            "--timestamp", "2026-05-29T16-35-12Z",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode in {0, 2}
    assert "Question:" in result.stdout
    assert (tmp_path / "out" / "2026-05-29T16-35-12Z" / "answer.json").exists()
    assert (tmp_path / "out" / "2026-05-29T16-35-12Z" / "answer.md").exists()

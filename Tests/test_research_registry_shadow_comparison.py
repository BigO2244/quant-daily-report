"""Targeted coverage for the shadow_comparison tool.

Exercises the parser, latest-date selection, the OK / NEEDS_DATA / NO_SHADOW_DATA
branches, leader selection, and the end-to-end MCP routing for
"How is Polaris doing versus Orion?" and "Which strategy is performing best?".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_registry.mcp_server import call_tool
from research_registry.research import shadow_comparison as sc


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _write_shadow_evaluation(
    shadow_root: Path,
    date: str,
    strategies: dict[str, dict],
) -> Path:
    date_dir = shadow_root / date
    date_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "trade_date": date,
        "benchmark_symbol": "SPY",
        "strategies": strategies,
    }
    (date_dir / "shadow_evaluation.json").write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return date_dir


def _write_comparison(date_dir: Path, pairwise: list[dict]) -> None:
    payload = {
        "trade_date": date_dir.name,
        "strategies": {},
        "pairwise_overlap": pairwise,
    }
    (date_dir / "comparison.json").write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _strategy_entry(name: str, *, nav: float, cum: float, excess: float, **extras) -> dict:
    base = {
        "strategy_name": name,
        "data_status": "OK",
        "data_reason": None,
        "nav": nav,
        "daily_return": 0.0,
        "cumulative_return": cum,
        "excess_return_vs_spy": excess,
        "avg_turnover": 0.1,
        "avg_top_3_concentration": 0.3,
        "realized_volatility_ann": None,
        "max_drawdown": None,
    }
    base.update(extras)
    return base


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("question,expected", [
    ("How is Polaris doing versus Orion?",       ["polaris", "orion"]),
    ("Compare Orion and Lyra",                   ["orion", "lyra"]),
    ("Which strategy is performing best?",       []),
    ("compare Polaris Polaris and POLARIS",      ["polaris"]),  # dedup
    ("polaris vs leda",                          ["polaris", "leda"]),
    ("show me growth strategy",                  []),  # unknown name, ignored
])
def test_parse_strategy_names(question, expected):
    assert sc.parse_strategy_names(question) == expected


def test_strategy_slug_normalises():
    assert sc.strategy_slug("Polaris") == "caerus_polaris"
    assert sc.strategy_slug("caerus_orion") == "caerus_orion"


def test_registered_overlay_names_are_not_nav_comparison_targets():
    assert "phoenix" in sc.KNOWN_STRATEGY_NAMES
    assert "argo" not in sc.KNOWN_STRATEGY_NAMES
    assert sc.parse_strategy_names("compare Argo and Phoenix") == ["phoenix"]


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_select_latest_shadow_date_picks_alphabetical_max(tmp_path):
    root = tmp_path / "outputs" / "shadow_candidates"
    _write_shadow_evaluation(root, "2026-04-01", {})
    _write_shadow_evaluation(root, "2026-05-15", {})
    chosen = sc.select_latest_shadow_date(root)
    assert chosen is not None
    assert chosen.name == "2026-05-15"


def test_select_latest_shadow_date_prefers_latest_alias_when_present(tmp_path):
    root = tmp_path / "outputs" / "shadow_candidates"
    _write_shadow_evaluation(root, "latest", {"caerus_polaris": _strategy_entry("Caerus Polaris", nav=1.05, cum=0.05, excess=0.02)})
    _write_shadow_evaluation(root, "2026-05-15", {"caerus_polaris": _strategy_entry("Caerus Polaris", nav=1.10, cum=0.10, excess=0.04)})
    chosen = sc.select_latest_shadow_date(root)
    assert chosen is not None
    assert chosen.name == "latest"  # alias wins


# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------


def test_compare_returns_no_shadow_data_when_root_missing(tmp_path):
    answer = sc.compare_shadow_strategies(shadow_root=tmp_path / "nope")
    assert answer.status == "NO_SHADOW_DATA"
    assert answer.panels == {}


def test_compare_returns_panels_with_leader_and_pairwise(tmp_path):
    root = tmp_path / "outputs" / "shadow_candidates"
    date_dir = _write_shadow_evaluation(root, "2026-05-15", {
        "caerus_polaris": _strategy_entry("Caerus Polaris", nav=1.10, cum=0.10, excess=0.04),
        "caerus_orion":   _strategy_entry("Caerus Orion",   nav=1.05, cum=0.05, excess=0.02),
        "caerus_lyra":    _strategy_entry("Caerus Lyra",    nav=1.03, cum=0.03, excess=0.01),
        "spy_benchmark":  _strategy_entry("SPY Benchmark",  nav=1.06, cum=0.06, excess=0.0),
    })
    _write_comparison(date_dir, [
        {"left_slug": "caerus_polaris", "right_slug": "caerus_orion",
         "left_strategy": "Caerus Polaris", "right_strategy": "Caerus Orion",
         "overlap_weight_pct": 0.5, "shared_names": ["MU"], "left_unique_names": ["WDC"], "right_unique_names": []},
    ])

    answer = sc.compare_shadow_strategies(
        shadow_root=root,
        question="How is Polaris doing versus Orion?",
    )
    assert answer.status == "OK"
    assert answer.trade_date == "2026-05-15"
    assert answer.leader_by_cumulative_return == "caerus_polaris"
    assert answer.leader_by_excess_vs_spy == "caerus_polaris"
    assert "caerus_polaris" in answer.panels
    assert "caerus_orion" in answer.panels
    # Benchmark surfaced for context.
    assert "spy_benchmark" in answer.panels
    # Pairwise filtered to the requested pair.
    assert len(answer.pairwise_overlap) == 1
    assert answer.pairwise_overlap[0]["overlap_weight_pct"] == 0.5
    assert "Polaris" in answer.leader_summary or "polaris" in answer.leader_summary


def test_compare_returns_needs_data_for_unknown_strategy(tmp_path):
    root = tmp_path / "outputs" / "shadow_candidates"
    _write_shadow_evaluation(root, "2026-05-15", {
        "caerus_polaris": _strategy_entry("Caerus Polaris", nav=1.10, cum=0.10, excess=0.04),
    })
    answer = sc.compare_shadow_strategies(
        shadow_root=root,
        # Leda isn't in the artifact.
        strategies=["leda"],
    )
    assert answer.status == "NEEDS_DATA"
    assert "caerus_leda" in answer.missing_strategies


def test_compare_returns_all_strategies_when_no_names_in_question(tmp_path):
    root = tmp_path / "outputs" / "shadow_candidates"
    _write_shadow_evaluation(root, "2026-05-15", {
        "caerus_polaris": _strategy_entry("Caerus Polaris", nav=1.10, cum=0.10, excess=0.04),
        "caerus_orion":   _strategy_entry("Caerus Orion",   nav=1.05, cum=0.05, excess=0.02),
    })
    answer = sc.compare_shadow_strategies(
        shadow_root=root,
        question="Which strategy is performing best?",
    )
    assert answer.status == "OK"
    assert set(answer.panels.keys()) >= {"caerus_polaris", "caerus_orion"}
    assert answer.leader_by_cumulative_return == "caerus_polaris"


def test_compare_records_unavailable_metrics_per_strategy(tmp_path):
    root = tmp_path / "outputs" / "shadow_candidates"
    _write_shadow_evaluation(root, "2026-05-15", {
        "caerus_polaris": _strategy_entry(
            "Caerus Polaris", nav=1.10, cum=0.10, excess=0.04,
            realized_volatility_ann=None, max_drawdown=None,
        ),
    })
    answer = sc.compare_shadow_strategies(shadow_root=root, strategies=["polaris"])
    polaris_panel = answer.panels["caerus_polaris"]
    assert "realized_volatility_ann" in polaris_panel["unavailable_metrics"]
    assert "max_drawdown" in polaris_panel["unavailable_metrics"]
    assert polaris_panel["realized_volatility_ann"] is None  # never invented


def test_compare_flags_stale_data_status_in_warnings(tmp_path):
    root = tmp_path / "outputs" / "shadow_candidates"
    _write_shadow_evaluation(root, "2026-05-15", {
        "caerus_polaris": _strategy_entry(
            "Caerus Polaris", nav=1.05, cum=0.05, excess=0.02,
            data_status="NO_DATA", data_reason="PRICE_CACHE_STALE",
        ),
    })
    answer = sc.compare_shadow_strategies(shadow_root=root, strategies=["polaris"])
    assert any("NO_DATA" in w for w in answer.warnings)


# ---------------------------------------------------------------------------
# MCP routing
# ---------------------------------------------------------------------------


def test_call_tool_routes_shadow_comparison(tmp_path):
    root = tmp_path / "outputs" / "shadow_candidates"
    _write_shadow_evaluation(root, "2026-05-15", {
        "caerus_polaris": _strategy_entry("Caerus Polaris", nav=1.10, cum=0.10, excess=0.04),
        "caerus_orion":   _strategy_entry("Caerus Orion",   nav=1.05, cum=0.05, excess=0.02),
    })
    result = call_tool(
        "shadow_comparison",
        {"shadow_root": str(root), "question": "How is Polaris doing versus Orion?"},
    )
    assert result["tool"] == "shadow_comparison"
    assert result["status"] == "OK"
    assert result["leader_by_cumulative_return"] == "caerus_polaris"
    assert "caerus_polaris" in result["panels"]
    assert "caerus_orion" in result["panels"]


def test_planner_routes_question_to_shadow_comparison(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "outputs" / "shadow_candidates"
    _write_shadow_evaluation(root, "2026-05-15", {
        "caerus_polaris": _strategy_entry("Caerus Polaris", nav=1.10, cum=0.10, excess=0.04),
        "caerus_orion":   _strategy_entry("Caerus Orion",   nav=1.05, cum=0.05, excess=0.02),
        "caerus_lyra":    _strategy_entry("Caerus Lyra",    nav=1.03, cum=0.03, excess=0.01),
    })
    result = call_tool(
        "answer_research_question",
        {"question": "Which strategy is performing best?"},
    )
    assert result["intent"] == "shadow_comparison"
    assert result["routed_to"] == "shadow_comparison"
    assert result["status"] == "OK"
    assert result["answer"]["leader_by_cumulative_return"] == "caerus_polaris"

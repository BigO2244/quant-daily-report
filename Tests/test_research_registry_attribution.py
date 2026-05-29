"""Targeted coverage for the attribution_analysis MCP tool.

Pins the loader, the artifact merge across the four attribution source
files, the deterministic narrative template, the OK / NEEDS_DATA /
NO_ATTRIBUTION_DATA branches, and the end-to-end MCP routing for the
five question phrasings in the task spec.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_registry.mcp_server import call_tool
from research_registry.research import attribution as attr


# ---------------------------------------------------------------------------
# Fixture builders — match the real on-disk schema (see
# `outputs/attribution/2026-04-30/`).
# ---------------------------------------------------------------------------


def _summary_entry(
    *, name: str, ret: float, top_ret_ticker: str, top_ret_value: float,
    detractor_ticker: str, detractor_value: float, hidden_flags: list[str] | None = None,
    market_beta: float = 1.0, best_regime: str = "risk_on", worst_regime: str = "risk_off",
) -> dict:
    return {
        "strategy_name": name,
        "portfolio_21d_return_current_book": ret,
        "market_beta": market_beta,
        "max_sector_weight": 0.5,
        "top3_contribution_share_21d": 0.4,
        "best_risk_regime": best_regime,
        "worst_risk_regime": worst_regime,
        "hidden_factor_flags": list(hidden_flags or []),
        "decision_attribution_status": "FOUNDATIONAL",
        "primary_21d_return_source": {
            "ticker": top_ret_ticker,
            "sector": "Information Technology",
            "weight": 0.1,
            "return": top_ret_value * 10,  # so contribution = top_ret_value
            "contribution": top_ret_value,
            "contribution_pct_of_portfolio_return": 0.25,
            "equal_weight_contribution": top_ret_value,
            "sizing_contribution": 0.0,
        },
        "primary_21d_detractor": {
            "ticker": detractor_ticker,
            "sector": "Communication Services",
            "weight": 0.1,
            "return": detractor_value * 10,
            "contribution": detractor_value,
            "contribution_pct_of_portfolio_return": -0.05,
            "equal_weight_contribution": detractor_value,
            "sizing_contribution": 0.0,
        },
    }


def _contribution_entry(*, top_drawdown: list[tuple[str, str, float]]) -> dict:
    return {
        "attribution_convention": "current_book_trailing_exposure_not_historical_realized_positions",
        "data_through_date": "2026-04-30",
        "concentration_impact": {"hhi": 0.1, "holdings_count": 10, "max_weight": 0.1},
        "drawdown_contribution": {
            "peak_date": "2026-02-25",
            "trough_date": "2026-03-30",
            "status": "OK",
            "top_drawdown_contributors": [
                {"ticker": t, "sector": s, "contribution_to_drawdown": c}
                for t, s, c in top_drawdown
            ],
        },
    }


def _factor_entry(*, beta: float = 1.0, sector_weights: dict[str, float] | None = None) -> dict:
    weights = sector_weights or {"Information Technology": 0.6, "Industrials": 0.2,
                                  "Materials": 0.1, "Communication Services": 0.1}
    return {
        "strategy_name": "Caerus Test",
        "market_beta": beta,
        "market_correlation": 0.6,
        "realized_volatility_ann_current_book": 0.30,
        "momentum_exposure": {"weighted_12_1_momentum": 2.0, "by_ticker": {}},
        "volatility_exposure": {"weighted_20d_ann_vol": 0.40, "by_ticker": {}},
        "sector_exposure": {
            "weights": weights,
            "max_sector_weight": max(weights.values()) if weights else 0.0,
            "sector_hhi": sum(w * w for w in weights.values()),
        },
        "growth_value_tilt": {"status": "UNAVAILABLE", "reason": "no fundamentals"},
        "quality_profitability_tilt": {"status": "UNAVAILABLE", "reason": "no panel"},
        "market_cap_tilt_proxy": {"status": "LIQUIDITY_PROXY_ONLY", "coverage": 10},
        "selection_alpha_interpretation": "PARTIAL: factor proxy only",
    }


def _regime_entry() -> dict:
    return {
        "interpretation": {
            "best_risk_regime": "risk_off",
            "worst_risk_regime": "risk_on",
            "regime_dependency_note": "descriptive",
        },
        "performance_by_regime": {
            "risk_regime": {
                "risk_off": {"avg_daily_return": 0.002, "cumulative_return": 1.5,
                              "excess_vs_spy": 0.7, "hit_rate": 0.56, "valid_days": 468,
                              "worst_daily_return": -0.15},
                "risk_on": {"avg_daily_return": 0.001, "cumulative_return": 10.0,
                             "excess_vs_spy": 8.0, "hit_rate": 0.51, "valid_days": 2630,
                             "worst_daily_return": -0.08},
            },
            "volatility_regime": {
                "high_vol": {"avg_daily_return": 0.0019, "cumulative_return": 5.0,
                              "excess_vs_spy": 4.0, "hit_rate": 0.54, "valid_days": 1000,
                              "worst_daily_return": -0.10},
                "low_vol": {"avg_daily_return": 0.001, "cumulative_return": 6.0,
                             "excess_vs_spy": 5.0, "hit_rate": 0.52, "valid_days": 2000,
                             "worst_daily_return": -0.05},
            },
        },
    }


def _write_attribution_artifacts(
    root: Path,
    date: str,
    strategies: list[tuple[str, dict, dict, dict, dict]],
) -> Path:
    """strategies = [(slug, summary_entry, contribution_entry, factor_entry, regime_entry)]"""
    date_dir = root / date
    date_dir.mkdir(parents=True, exist_ok=True)
    (date_dir / "attribution_summary.json").write_text(
        json.dumps({
            "trade_date": date,
            "schema_version": "caerus_attribution_summary_v1",
            "classification": "RESEARCH_GRADE_PHASE_1_PARTIAL",
            "methodology_note": "current-book trailing exposure",
            "cio_questions": {},
            "strategies": {slug: s for slug, s, _, _, _ in strategies},
        }, sort_keys=True),
        encoding="utf-8",
    )
    (date_dir / "contribution_report.json").write_text(
        json.dumps({
            "trade_date": date,
            "schema_version": "caerus_position_attribution_v1",
            "strategies": {slug: c for slug, _, c, _, _ in strategies},
        }, sort_keys=True),
        encoding="utf-8",
    )
    (date_dir / "factor_exposure.json").write_text(
        json.dumps({
            "trade_date": date,
            "schema_version": "caerus_factor_exposure_v1",
            "strategies": {slug: f for slug, _, _, f, _ in strategies},
        }, sort_keys=True),
        encoding="utf-8",
    )
    (date_dir / "regime_performance_breakdown.json").write_text(
        json.dumps({
            "trade_date": date,
            "schema_version": "caerus_regime_attribution_v1",
            "status": "OK",
            "strategies": {slug: r for slug, _, _, _, r in strategies},
        }, sort_keys=True),
        encoding="utf-8",
    )
    return date_dir


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_select_latest_attribution_date_picks_alphabetical_max(tmp_path):
    root = tmp_path / "outputs" / "attribution"
    _write_attribution_artifacts(root, "2026-04-15", [
        ("caerus_polaris",
         _summary_entry(name="Polaris", ret=0.20, top_ret_ticker="MU", top_ret_value=0.05,
                        detractor_ticker="WBD", detractor_value=-0.01),
         _contribution_entry(top_drawdown=[("MU", "IT", -0.02)]),
         _factor_entry(), _regime_entry()),
    ])
    _write_attribution_artifacts(root, "2026-05-15", [
        ("caerus_polaris",
         _summary_entry(name="Polaris", ret=0.30, top_ret_ticker="MU", top_ret_value=0.07,
                        detractor_ticker="WBD", detractor_value=-0.01),
         _contribution_entry(top_drawdown=[("MU", "IT", -0.03)]),
         _factor_entry(), _regime_entry()),
    ])
    chosen = attr.select_latest_attribution_date(root)
    assert chosen is not None
    assert chosen.name == "2026-05-15"


def test_select_latest_ignores_non_date_directories(tmp_path):
    root = tmp_path / "outputs" / "attribution"
    root.mkdir(parents=True)
    (root / "rolling_exposure_history.csv").write_text("not,a,date,dir\n", encoding="utf-8")
    (root / "latest").mkdir()
    (root / "latest" / "attribution_summary.json").write_text("{}", encoding="utf-8")
    _write_attribution_artifacts(root, "2026-05-15", [
        ("caerus_polaris",
         _summary_entry(name="Polaris", ret=0.30, top_ret_ticker="MU", top_ret_value=0.07,
                        detractor_ticker="WBD", detractor_value=-0.01),
         _contribution_entry(top_drawdown=[]), _factor_entry(), _regime_entry()),
    ])
    chosen = attr.select_latest_attribution_date(root)
    assert chosen is not None and chosen.name == "2026-05-15"


# ---------------------------------------------------------------------------
# Loader / panel assembly
# ---------------------------------------------------------------------------


def _two_strategy_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "outputs" / "attribution"
    _write_attribution_artifacts(root, "2026-05-15", [
        ("caerus_polaris",
         _summary_entry(name="Polaris", ret=0.30,
                        top_ret_ticker="STX", top_ret_value=0.072,
                        detractor_ticker="WBD", detractor_value=-0.0015,
                        hidden_flags=["high_market_beta", "sector_concentration"],
                        market_beta=1.83),
         _contribution_entry(top_drawdown=[
             ("MU", "Information Technology", -0.026),
             ("LRCX", "Information Technology", -0.020),
             ("GLW", "Information Technology", -0.019),
         ]),
         _factor_entry(beta=1.83), _regime_entry()),
        ("caerus_orion",
         _summary_entry(name="Orion", ret=0.35,
                        top_ret_ticker="MU", top_ret_value=0.085,
                        detractor_ticker="GEV", detractor_value=-0.0010,
                        hidden_flags=["high_market_beta"],
                        market_beta=1.65),
         _contribution_entry(top_drawdown=[
             ("LRCX", "Information Technology", -0.022),
             ("MU", "Information Technology", -0.019),
         ]),
         _factor_entry(beta=1.65), _regime_entry()),
    ])
    return root


def test_analyse_returns_no_attribution_data_when_root_absent(tmp_path):
    answer = attr.analyse_attribution(attribution_root=tmp_path / "nope")
    assert answer.status == "NO_ATTRIBUTION_DATA"
    assert answer.panels == {}


def test_analyse_returns_panels_with_top_contributors_and_detractors(tmp_path):
    root = _two_strategy_fixture(tmp_path)
    answer = attr.analyse_attribution(attribution_root=root, strategies=["polaris", "orion"])
    assert answer.status == "OK"
    assert answer.trade_date == "2026-05-15"
    assert "caerus_polaris" in answer.panels
    assert "caerus_orion" in answer.panels

    polaris = answer.panels["caerus_polaris"]
    assert polaris["portfolio_return_21d"] == pytest.approx(0.30)
    assert polaris["top_contributor"]["ticker"] == "STX"
    assert polaris["top_contributor"]["contribution"] == pytest.approx(0.072)
    assert polaris["top_detractor"]["ticker"] == "WBD"
    assert "high_market_beta" in polaris["hidden_factor_flags"]
    assert polaris["market_beta"] == pytest.approx(1.83)


def test_panel_merges_factor_and_regime_blocks(tmp_path):
    root = _two_strategy_fixture(tmp_path)
    answer = attr.analyse_attribution(attribution_root=root, strategies=["polaris"])
    p = answer.panels["caerus_polaris"]
    # Factor block
    assert p["factor_exposures"]["weighted_12_1_momentum"] == pytest.approx(2.0)
    assert p["factor_exposures"]["weighted_20d_ann_vol"] == pytest.approx(0.40)
    assert p["factor_exposures"]["growth_value_tilt_status"] == "UNAVAILABLE"
    assert p["factor_exposures"]["quality_profitability_tilt_status"] == "UNAVAILABLE"
    # Sector exposure with weights sorted descending
    weights = p["sector_exposure"]["weights"]
    assert list(weights.keys())[0] == "Information Technology"  # highest weight first
    assert p["sector_exposure"]["max_sector_weight"] == pytest.approx(0.6)
    # Regime block
    assert "risk_regime" in p["regime_performance"]
    assert p["regime_performance"]["best_risk_regime"] == "risk_off"
    assert p["regime_performance"]["risk_regime"]["risk_off"]["hit_rate"] == pytest.approx(0.56)


def test_panel_records_drawdown_contributors(tmp_path):
    root = _two_strategy_fixture(tmp_path)
    answer = attr.analyse_attribution(attribution_root=root, strategies=["polaris"])
    drawdown = answer.panels["caerus_polaris"]["top_drawdown_contributors"]
    assert len(drawdown) == 3
    assert drawdown[0]["ticker"] == "MU"
    assert drawdown[0]["contribution_to_drawdown"] == pytest.approx(-0.026)


def test_comparison_block_identifies_outperformer(tmp_path):
    root = _two_strategy_fixture(tmp_path)
    answer = attr.analyse_attribution(
        attribution_root=root,
        question="Why did Orion outperform Polaris?",
    )
    assert answer.status == "OK"
    assert answer.comparison is not None
    assert answer.comparison["outperformer"] == "caerus_orion"
    assert answer.comparison["underperformer"] == "caerus_polaris"
    assert answer.comparison["outperformance"] == pytest.approx(0.05)
    assert answer.comparison["explicitly_requested"] is True
    assert answer.leader_by_return == "caerus_orion"


def test_narrative_is_deterministic_and_template_driven(tmp_path):
    root = _two_strategy_fixture(tmp_path)
    a1 = attr.analyse_attribution(attribution_root=root, question="Why did Orion outperform Polaris?")
    a2 = attr.analyse_attribution(attribution_root=root, question="Why did Orion outperform Polaris?")
    assert a1.narrative == a2.narrative  # determinism
    assert "Orion" in a1.narrative
    assert "Polaris" in a1.narrative
    assert "outperformed" in a1.narrative
    # Mentions the top contributor and detractor tickers.
    assert "STX" in a1.narrative
    assert "WBD" in a1.narrative


def test_returns_needs_data_for_unknown_strategy(tmp_path):
    root = _two_strategy_fixture(tmp_path)
    answer = attr.analyse_attribution(attribution_root=root, strategies=["leda"])
    assert answer.status == "NEEDS_DATA"
    assert "caerus_leda" in answer.missing_strategies


def test_returns_all_strategies_when_no_names_in_question(tmp_path):
    root = _two_strategy_fixture(tmp_path)
    answer = attr.analyse_attribution(
        attribution_root=root,
        question="What drove returns?",
    )
    assert answer.status == "OK"
    assert set(answer.panels.keys()) == {"caerus_polaris", "caerus_orion"}


def test_unavailable_metrics_surfaced_per_strategy(tmp_path):
    root = _two_strategy_fixture(tmp_path)
    answer = attr.analyse_attribution(attribution_root=root, strategies=["polaris"])
    p = answer.panels["caerus_polaris"]
    # growth/value status is UNAVAILABLE in the fixture but that's a status string,
    # not a null metric — it's surfaced via factor_exposures.growth_value_tilt_status.
    # Verify the loader doesn't claim those tilts have numeric values.
    assert p["factor_exposures"]["growth_value_tilt_status"] == "UNAVAILABLE"


def test_missing_optional_files_still_produce_panel(tmp_path):
    """Only attribution_summary.json is strictly required; contribution /
    factor / regime files are best-effort enrichments. The loader must not
    crash when they're absent."""
    root = tmp_path / "outputs" / "attribution"
    date_dir = root / "2026-05-15"
    date_dir.mkdir(parents=True)
    (date_dir / "attribution_summary.json").write_text(json.dumps({
        "trade_date": "2026-05-15",
        "strategies": {
            "caerus_polaris": _summary_entry(
                name="Polaris", ret=0.20,
                top_ret_ticker="STX", top_ret_value=0.05,
                detractor_ticker="WBD", detractor_value=-0.01,
            ),
        },
    }), encoding="utf-8")

    answer = attr.analyse_attribution(attribution_root=root, strategies=["polaris"])
    assert answer.status == "OK"
    p = answer.panels["caerus_polaris"]
    assert p["portfolio_return_21d"] == pytest.approx(0.20)
    # Optional enrichment files were absent → their fields are empty/unavailable.
    assert p["top_drawdown_contributors"] == []
    assert p["factor_exposures"] == {} or all(v is None for v in p["factor_exposures"].values() if not isinstance(v, str))
    assert "drawdown_contribution" in p["unavailable_metrics"]


# ---------------------------------------------------------------------------
# MCP tool routing
# ---------------------------------------------------------------------------


def test_call_tool_routes_attribution_analysis(tmp_path):
    root = _two_strategy_fixture(tmp_path)
    result = call_tool("attribution_analysis", {
        "attribution_root": str(root),
        "question": "Why did Orion outperform Polaris?",
    })
    assert result["tool"] == "attribution_analysis"
    assert result["status"] == "OK"
    assert result["leader_by_return"] == "caerus_orion"
    assert result["comparison"]["outperformer"] == "caerus_orion"
    assert "caerus_polaris" in result["panels"]


@pytest.mark.parametrize("question,expected_focus", [
    ("What drove returns?",                              "all"),
    ("Why did Orion outperform Polaris?",                "comparison"),
    ("What contributed most to performance?",            "all"),
    ("What hurt performance?",                           "all"),
    ("What factors drove returns?",                      "all"),
])
def test_planner_routes_attribution_questions_through_call_tool(tmp_path, monkeypatch, question, expected_focus):
    """All five task-spec questions should match the attribution_analysis
    capability and route through to the tool."""
    root = _two_strategy_fixture(tmp_path)
    monkeypatch.chdir(tmp_path)  # so the planner's artifact pre-check sees our data
    result = call_tool("answer_research_question", {
        "question": question,
        "attribution_root": str(root),
    })
    assert result["intent"] == "attribution_analysis"
    assert result["routed_to"] == "attribution_analysis"
    assert result["status"] == "OK"
    if expected_focus == "comparison":
        assert result["answer"]["comparison"] is not None
        assert result["answer"]["comparison"]["outperformer"] == "caerus_orion"


def test_planner_returns_needs_data_when_attribution_root_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # cwd has no outputs/ tree → artifact pre-check fails
    result = call_tool("answer_research_question", {"question": "What drove returns?"})
    assert result["intent"] == "attribution_analysis"
    assert result["routed_to"] == "attribution_analysis"
    assert result["status"] == "NEEDS_DATA"
    assert "outputs/attribution/*/attribution_summary.json" in result["missing_artifacts"]

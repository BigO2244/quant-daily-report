"""Coverage for strategy_differentiation MCP tool + capability.

Pins:
- per-pair similarity (holdings + factor + sector components)
- pair verdict tiers (highly_overlapping / partially_differentiated /
  differentiated / insufficient_evidence)
- all-strategies rollup (most-similar, most-differentiated, common
  factor flags, diversification verdict)
- fail-closed paths (NO_SHADOW_DATA when shadow_candidates absent;
  NEEDS_DATA for unknown strategies; per-pair caveats when factor
  data is missing)
- MCP routing for the six task-spec phrasings
- no unintended kwarg leakage (the existing inspect-based pass-through
  filter is exercised via the planner)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_registry.mcp_server import call_tool
from research_registry.research import strategy_differentiation as sd


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _shadow_eval_entry(name: str) -> dict:
    return {
        "strategy_name": name,
        "data_status": "OK",
        "data_reason": None,
        "nav": 1.04,
        "daily_return": 0.04,
        "cumulative_return": 0.04,
        "excess_return_vs_spy": 0.03,
        "avg_turnover": 0.10,
        "avg_top_3_concentration": 0.30,
        "max_drawdown": None,
        "realized_volatility_ann": None,
    }


def _factor_entry(
    *,
    beta: float,
    momentum: float,
    vol: float,
    sector_weights: dict[str, float],
) -> dict:
    return {
        "market_beta": beta,
        "market_correlation": 0.6,
        "realized_volatility_ann_current_book": vol,
        "momentum_exposure": {"weighted_12_1_momentum": momentum, "by_ticker": {}},
        "volatility_exposure": {"weighted_20d_ann_vol": vol, "by_ticker": {}},
        "sector_exposure": {
            "weights": sector_weights,
            "max_sector_weight": max(sector_weights.values()) if sector_weights else 0.0,
            "sector_hhi": sum(w * w for w in sector_weights.values()),
        },
    }


def _summary_entry(
    *,
    name: str,
    top_ticker: str,
    top_value: float,
    detractor_ticker: str,
    hidden_flags: list[str],
) -> dict:
    return {
        "strategy_name": name,
        "portfolio_21d_return_current_book": 0.30,
        "hidden_factor_flags": hidden_flags,
        "primary_21d_return_source": {
            "ticker": top_ticker, "sector": "Information Technology",
            "weight": 0.1, "return": top_value * 10, "contribution": top_value,
            "contribution_pct_of_portfolio_return": 0.25,
        },
        "primary_21d_detractor": {
            "ticker": detractor_ticker, "sector": "Communication Services",
            "weight": 0.1, "return": -0.01, "contribution": -0.0015,
            "contribution_pct_of_portfolio_return": -0.005,
        },
    }


def _contribution_entry(top_drawdown: list[tuple[str, str, float]]) -> dict:
    return {
        "drawdown_contribution": {
            "top_drawdown_contributors": [
                {"ticker": t, "sector": s, "contribution_to_drawdown": c}
                for t, s, c in top_drawdown
            ],
        },
    }


def _write_shadow(shadow_root: Path, date: str, *, strategies: dict, pairwise: list) -> Path:
    date_dir = shadow_root / date
    date_dir.mkdir(parents=True, exist_ok=True)
    (date_dir / "shadow_evaluation.json").write_text(
        json.dumps({"trade_date": date, "strategies": strategies}, sort_keys=True),
        encoding="utf-8",
    )
    (date_dir / "comparison.json").write_text(
        json.dumps({
            "trade_date": date,
            "strategies": {},
            "pairwise_overlap": pairwise,
        }, sort_keys=True),
        encoding="utf-8",
    )
    return date_dir


def _write_attribution(
    attribution_root: Path,
    date: str,
    *,
    factor: dict,
    summary: dict,
    contribution: dict | None = None,
) -> Path:
    date_dir = attribution_root / date
    date_dir.mkdir(parents=True, exist_ok=True)
    (date_dir / "factor_exposure.json").write_text(
        json.dumps({"trade_date": date, "strategies": factor}, sort_keys=True),
        encoding="utf-8",
    )
    (date_dir / "attribution_summary.json").write_text(
        json.dumps({"trade_date": date, "strategies": summary}, sort_keys=True),
        encoding="utf-8",
    )
    if contribution is not None:
        (date_dir / "contribution_report.json").write_text(
            json.dumps({"trade_date": date, "strategies": contribution}, sort_keys=True),
            encoding="utf-8",
        )
    return date_dir


def _full_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Three strategies with realistic on-disk shapes.

    polaris and orion deliberately share top sector (IT 60%), beta ~1.8,
    holdings overlap 0.5 — they should land "highly_overlapping" or
    "partially_differentiated". lyra has a different top sector (Materials)
    and a lower beta to test the differentiated path.
    """
    outputs = tmp_path / "outputs"
    shadow_root = outputs / "shadow_candidates"
    attribution_root = outputs / "attribution"
    _write_shadow(shadow_root, "2026-05-04",
        strategies={
            "caerus_polaris": _shadow_eval_entry("Caerus Polaris"),
            "caerus_orion": _shadow_eval_entry("Caerus Orion"),
            "caerus_lyra": _shadow_eval_entry("Caerus Lyra"),
            "spy_benchmark": _shadow_eval_entry("SPY Benchmark"),
        },
        pairwise=[
            # polaris ↔ orion: high overlap, shared core names
            {"left_slug": "caerus_polaris", "right_slug": "caerus_orion",
             "left_strategy": "Caerus Polaris", "right_strategy": "Caerus Orion",
             "overlap_weight_pct": 0.5,
             "shared_names": ["MU", "STX", "LRCX", "WBD", "WDC"],
             "left_unique_names": ["AMAT", "CAT", "GEV", "GLW", "NEM"],
             "right_unique_names": []},
            # polaris ↔ lyra: lower overlap
            {"left_slug": "caerus_polaris", "right_slug": "caerus_lyra",
             "left_strategy": "Caerus Polaris", "right_strategy": "Caerus Lyra",
             "overlap_weight_pct": 0.2,
             "shared_names": ["MU", "STX"],
             "left_unique_names": ["GLW", "WBD", "WDC", "AMAT", "CAT", "GEV", "LRCX", "NEM"],
             "right_unique_names": []},
            # orion ↔ lyra: moderate
            {"left_slug": "caerus_orion", "right_slug": "caerus_lyra",
             "left_strategy": "Caerus Orion", "right_strategy": "Caerus Lyra",
             "overlap_weight_pct": 0.3,
             "shared_names": ["MU", "STX", "WBD"],
             "left_unique_names": ["LRCX", "WDC"],
             "right_unique_names": []},
        ],
    )
    _write_attribution(attribution_root, "2026-05-04",
        factor={
            # polaris and orion: similar betas, both IT-heavy
            "caerus_polaris": _factor_entry(beta=1.83, momentum=2.5, vol=0.48,
                                            sector_weights={"Information Technology": 0.60,
                                                            "Industrials": 0.20,
                                                            "Materials": 0.10,
                                                            "Communication Services": 0.10}),
            "caerus_orion": _factor_entry(beta=1.95, momentum=2.7, vol=0.50,
                                          sector_weights={"Information Technology": 0.65,
                                                          "Industrials": 0.20,
                                                          "Materials": 0.05,
                                                          "Communication Services": 0.10}),
            # lyra: low-beta Materials-heavy → differentiated
            "caerus_lyra": _factor_entry(beta=0.70, momentum=0.3, vol=0.18,
                                         sector_weights={"Materials": 0.55,
                                                         "Utilities": 0.25,
                                                         "Consumer Staples": 0.20}),
        },
        summary={
            "caerus_polaris": _summary_entry(name="Polaris", top_ticker="STX", top_value=0.072,
                                             detractor_ticker="WBD",
                                             hidden_flags=["high_market_beta", "sector_concentration"]),
            "caerus_orion": _summary_entry(name="Orion", top_ticker="STX", top_value=0.144,
                                           detractor_ticker="WBD",
                                           hidden_flags=["high_market_beta", "sector_concentration"]),
            "caerus_lyra": _summary_entry(name="Lyra", top_ticker="NEM", top_value=0.05,
                                          detractor_ticker="XOM",
                                          hidden_flags=["low_market_beta"]),
        },
        contribution={
            "caerus_polaris": _contribution_entry([
                ("MU", "Information Technology", -0.026),
                ("LRCX", "Information Technology", -0.020),
                ("STX", "Information Technology", -0.013),
            ]),
            "caerus_orion": _contribution_entry([
                ("MU", "Information Technology", -0.026),
                ("LRCX", "Information Technology", -0.022),
                ("STX", "Information Technology", -0.013),
            ]),
            "caerus_lyra": _contribution_entry([
                ("NEM", "Materials", -0.015),
                ("XOM", "Energy", -0.010),
            ]),
        },
    )
    return outputs, shadow_root, attribution_root


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_clipped_proximity_returns_one_when_diff_is_zero():
    assert sd._clipped_proximity(0.0, 1.0) == 1.0


def test_clipped_proximity_clamps_to_zero_beyond_clip():
    assert sd._clipped_proximity(2.0, 1.0) == 0.0
    assert sd._clipped_proximity(-2.0, 1.0) == 0.0


def test_clipped_proximity_returns_none_for_none_input():
    assert sd._clipped_proximity(None, 1.0) is None


def test_pair_verdict_highly_overlapping_when_score_high():
    assert sd._pair_verdict(0.8, 0.5) == "highly_overlapping"
    assert sd._pair_verdict(None, 0.8) == "highly_overlapping"


def test_pair_verdict_differentiated_only_when_both_low():
    assert sd._pair_verdict(0.2, 0.2) == "differentiated"


def test_pair_verdict_partially_for_mid_range():
    assert sd._pair_verdict(0.5, 0.5) == "partially_differentiated"


def test_pair_verdict_insufficient_evidence_when_both_none():
    assert sd._pair_verdict(None, None) == "insufficient_evidence"


# ---------------------------------------------------------------------------
# Loader / orchestrator
# ---------------------------------------------------------------------------


def test_no_shadow_data_when_root_absent(tmp_path):
    answer = sd.analyse_strategy_differentiation(
        outputs_root=tmp_path / "nope",
        shadow_root=tmp_path / "nope" / "shadow_candidates",
        attribution_root=tmp_path / "nope" / "attribution",
    )
    assert answer.status == "NO_SHADOW_DATA"
    assert answer.pairwise_differentiation == []


def test_happy_path_pairwise_with_all_three_strategies(tmp_path):
    outputs, shadow_root, attribution_root = _full_fixture(tmp_path)
    answer = sd.analyse_strategy_differentiation(
        outputs_root=outputs,
        shadow_root=shadow_root,
        attribution_root=attribution_root,
    )
    assert answer.status == "OK"
    assert answer.trade_date == "2026-05-04"
    assert set(answer.strategy_snapshots.keys()) == {"caerus_polaris", "caerus_orion", "caerus_lyra"}
    # 3 strategies → 3 unordered pairs.
    assert len(answer.pairwise_differentiation) == 3
    # Polaris vs Orion: high overlap + similar beta + same top sector → highly_overlapping.
    polaris_orion = next(
        p for p in answer.pairwise_differentiation
        if {p["left_slug"], p["right_slug"]} == {"caerus_polaris", "caerus_orion"}
    )
    assert polaris_orion["holdings_overlap_pct"] == 0.5
    assert polaris_orion["shared_top_sector"] == "Information Technology"
    assert polaris_orion["shared_top_contributor"] == "STX"
    assert polaris_orion["shared_top_detractor"] == "WBD"
    assert "MU" in polaris_orion["shared_drawdown_contributors"]
    # Similarity composite should be >= 0.5 given the construction.
    assert polaris_orion["similarity_score"] is not None
    assert polaris_orion["similarity_score"] >= 0.5
    # Polaris vs Lyra: low overlap + dissimilar betas + different sector.
    polaris_lyra = next(
        p for p in answer.pairwise_differentiation
        if {p["left_slug"], p["right_slug"]} == {"caerus_polaris", "caerus_lyra"}
    )
    assert polaris_lyra["shared_top_sector"] is None
    assert polaris_lyra["shared_top_contributor"] is None  # STX vs NEM


def test_most_similar_and_most_differentiated_picked_correctly(tmp_path):
    outputs, shadow_root, attribution_root = _full_fixture(tmp_path)
    answer = sd.analyse_strategy_differentiation(
        outputs_root=outputs,
        shadow_root=shadow_root,
        attribution_root=attribution_root,
    )
    assert answer.most_similar_pair is not None
    # The polaris-orion pair should be the most similar (high overlap + same sector + similar beta).
    assert {answer.most_similar_pair["left_slug"], answer.most_similar_pair["right_slug"]} == {
        "caerus_polaris", "caerus_orion",
    }
    # The polaris-lyra OR orion-lyra pair should be the most differentiated.
    assert answer.most_differentiated_pair is not None
    diff = {answer.most_differentiated_pair["left_slug"], answer.most_differentiated_pair["right_slug"]}
    assert "caerus_lyra" in diff


def test_common_factor_flags_intersection_only(tmp_path):
    outputs, shadow_root, attribution_root = _full_fixture(tmp_path)
    answer = sd.analyse_strategy_differentiation(
        outputs_root=outputs,
        shadow_root=shadow_root,
        attribution_root=attribution_root,
    )
    # polaris+orion share ["high_market_beta", "sector_concentration"]; lyra has
    # ["low_market_beta"]. Intersection across ALL three is empty.
    assert answer.common_factor_flags == []


def test_common_factor_flags_present_when_all_share(tmp_path):
    outputs, shadow_root, attribution_root = _full_fixture(tmp_path)
    # Overwrite attribution_summary so all three strategies share a flag.
    attribution_path = attribution_root / "2026-05-04" / "attribution_summary.json"
    payload = json.loads(attribution_path.read_text())
    for slug in payload["strategies"]:
        payload["strategies"][slug]["hidden_factor_flags"] = ["high_market_beta", "sector_concentration"]
    attribution_path.write_text(json.dumps(payload, sort_keys=True))
    answer = sd.analyse_strategy_differentiation(
        outputs_root=outputs,
        shadow_root=shadow_root,
        attribution_root=attribution_root,
    )
    assert "high_market_beta" in answer.common_factor_flags
    assert "sector_concentration" in answer.common_factor_flags


def test_diversification_verdict_low_when_majority_highly_overlapping(tmp_path):
    """Construct a case where all 3 pairs are highly_overlapping → low_diversification."""
    outputs = tmp_path / "outputs"
    shadow_root = outputs / "shadow_candidates"
    attribution_root = outputs / "attribution"
    _write_shadow(shadow_root, "2026-05-04",
        strategies={
            "caerus_polaris": _shadow_eval_entry("Caerus Polaris"),
            "caerus_orion": _shadow_eval_entry("Caerus Orion"),
            "caerus_lyra": _shadow_eval_entry("Caerus Lyra"),
        },
        pairwise=[
            {"left_slug": "caerus_polaris", "right_slug": "caerus_orion", "overlap_weight_pct": 0.8,
             "shared_names": ["MU"], "left_unique_names": [], "right_unique_names": []},
            {"left_slug": "caerus_polaris", "right_slug": "caerus_lyra", "overlap_weight_pct": 0.8,
             "shared_names": ["MU"], "left_unique_names": [], "right_unique_names": []},
            {"left_slug": "caerus_orion", "right_slug": "caerus_lyra", "overlap_weight_pct": 0.8,
             "shared_names": ["MU"], "left_unique_names": [], "right_unique_names": []},
        ],
    )
    answer = sd.analyse_strategy_differentiation(
        outputs_root=outputs, shadow_root=shadow_root, attribution_root=attribution_root,
    )
    assert answer.diversification_verdict == "low_diversification"
    assert all(p["verdict"] == "highly_overlapping" for p in answer.pairwise_differentiation)


def test_needs_data_for_unknown_strategy(tmp_path):
    outputs, shadow_root, attribution_root = _full_fixture(tmp_path)
    answer = sd.analyse_strategy_differentiation(
        outputs_root=outputs,
        shadow_root=shadow_root,
        attribution_root=attribution_root,
        strategies=["leda"],
    )
    assert answer.status == "NEEDS_DATA"
    assert "caerus_leda" in answer.missing_strategies


def test_single_strategy_question_expands_to_all_pairs(tmp_path):
    """Asking "is Polaris different?" should yield all three strategies'
    pairs so the operator can see Polaris's relationships."""
    outputs, shadow_root, attribution_root = _full_fixture(tmp_path)
    answer = sd.analyse_strategy_differentiation(
        outputs_root=outputs,
        shadow_root=shadow_root,
        attribution_root=attribution_root,
        question="Is Polaris different?",
    )
    assert answer.status == "OK"
    assert len(answer.pairwise_differentiation) == 3


def test_caveats_when_attribution_missing(tmp_path):
    """No attribution dir → factor proximity unavailable per pair."""
    outputs = tmp_path / "outputs"
    shadow_root = outputs / "shadow_candidates"
    attribution_root = outputs / "attribution"  # never created
    _write_shadow(shadow_root, "2026-05-04",
        strategies={
            "caerus_polaris": _shadow_eval_entry("Caerus Polaris"),
            "caerus_orion": _shadow_eval_entry("Caerus Orion"),
        },
        pairwise=[
            {"left_slug": "caerus_polaris", "right_slug": "caerus_orion", "overlap_weight_pct": 0.5,
             "shared_names": ["MU"], "left_unique_names": [], "right_unique_names": []},
        ],
    )
    answer = sd.analyse_strategy_differentiation(
        outputs_root=outputs, shadow_root=shadow_root, attribution_root=attribution_root,
    )
    assert answer.status == "OK"
    assert any("no attribution directory" in w for w in answer.warnings)
    pair = answer.pairwise_differentiation[0]
    assert "no_factor_proximity_data" in pair["caveats"]
    assert "no_sector_overlap_data" in pair["caveats"]


def test_narrative_is_deterministic(tmp_path):
    outputs, shadow_root, attribution_root = _full_fixture(tmp_path)
    a1 = sd.analyse_strategy_differentiation(
        outputs_root=outputs, shadow_root=shadow_root, attribution_root=attribution_root,
    )
    a2 = sd.analyse_strategy_differentiation(
        outputs_root=outputs, shadow_root=shadow_root, attribution_root=attribution_root,
    )
    assert a1.narrative == a2.narrative
    assert "Most similar:" in a1.narrative
    assert "Diversification:" in a1.narrative


# ---------------------------------------------------------------------------
# MCP routing
# ---------------------------------------------------------------------------


def test_call_tool_routes_strategy_differentiation(tmp_path):
    outputs, shadow_root, attribution_root = _full_fixture(tmp_path)
    result = call_tool("strategy_differentiation", {
        "outputs_root": str(outputs),
        "shadow_root": str(shadow_root),
        "attribution_root": str(attribution_root),
    })
    assert result["tool"] == "strategy_differentiation"
    assert result["status"] == "OK"
    assert len(result["pairwise_differentiation"]) == 3


@pytest.mark.parametrize("question", [
    "Are Polaris and Orion actually different?",
    "How different are Lyra and Orion?",
    "Which strategies are most similar?",
    "Are the strategies mostly the same factor bet?",
    "Compare strategy overlap and factor concentration.",
    "Do we have diversification across strategies?",
])
def test_planner_routes_differentiation_questions(tmp_path, monkeypatch, question):
    outputs, shadow_root, attribution_root = _full_fixture(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = call_tool("answer_research_question", {
        "question": question,
        "outputs_root": str(outputs),
        "shadow_root": str(shadow_root),
        "attribution_root": str(attribution_root),
    })
    assert result["intent"] == "strategy_differentiation", question
    assert result["routed_to"] == "strategy_differentiation", question
    assert result["status"] == "OK", question


def test_planner_pass_through_kwargs_do_not_leak_to_unrelated_tools(tmp_path, monkeypatch):
    """The planner forwards shadow_root/attribution_root/outputs_root to
    strategy_differentiation. The inspect-based filter must NOT forward
    them to tools that don't accept them (e.g., promotion_readiness, which
    accepts outputs_root but not shadow_root or attribution_root)."""
    outputs, shadow_root, attribution_root = _full_fixture(tmp_path)
    monkeypatch.chdir(tmp_path)
    # "Is Orion ready for promotion?" routes to promotion_readiness, NOT
    # strategy_differentiation. The shadow_root + attribution_root kwargs
    # in the call should be silently dropped (filtered) for that tool.
    result = call_tool("answer_research_question", {
        "question": "Is Orion ready for promotion?",
        "outputs_root": str(outputs),
        "shadow_root": str(shadow_root),
        "attribution_root": str(attribution_root),
    })
    assert result["intent"] == "promotion_readiness"
    assert result["routed_to"] == "promotion_readiness"
    # If kwargs had leaked, the call would have TypeError'd.


def test_planner_compare_overlap_and_factor_concentration_returns_pairs(tmp_path, monkeypatch):
    outputs, shadow_root, attribution_root = _full_fixture(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = call_tool("answer_research_question", {
        "question": "Compare strategy overlap and factor concentration.",
        "outputs_root": str(outputs),
        "shadow_root": str(shadow_root),
        "attribution_root": str(attribution_root),
    })
    assert result["status"] == "OK"
    inner = result["answer"]
    assert inner["most_similar_pair"] is not None
    assert inner["diversification_verdict"] in {
        "low_diversification", "moderate_diversification",
        "high_diversification", "insufficient_evidence",
    }

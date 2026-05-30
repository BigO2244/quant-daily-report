"""Targeted coverage for strategy_behavior_differentiation.

Pins:
- happy path against a synthesized fixture NAV CSV
- pair-specific filtering
- all-strategy ranking + diversification verdict tiers
- insufficient observations → tier=insufficient_evidence
- missing NAV file → status=NO_RETURN_STREAM with proposed contract
- unknown strategy → status=NEEDS_DATA with missing_strategies
- rolling-correlation requires enough data
- shared drawdown / co-negative-day logic
- renderer output (via the gateway)
- planner regex routing (six question phrasings)
- no kwarg leakage to unrelated tools
"""

from __future__ import annotations

import csv
import math
from pathlib import Path

import pytest

from research_registry.mcp_server import call_tool
from research_registry.research import strategy_behavior_differentiation as sbd


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _synthesize_nav_csv(
    path: Path,
    *,
    n_days: int = 400,
    polaris_seed: float = 0.0008,
    orion_seed: float = 0.0008,
    lyra_seed: float = -0.0005,
    correlated: bool = True,
) -> Path:
    """Write a wide-format NAV CSV. With ``correlated=True`` the three
    challengers share a noisy common factor + small idiosyncratic noise
    (high pairwise correlation). With ``correlated=False`` each strategy
    has independent noise (low correlation). All series start at NAV=1.0
    and the first 20 rows are pre-inception (NAV stays at 1.0) to test
    the inception-skip path.
    """
    import datetime as _dt
    import random
    random.seed(42)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[tuple] = []
    polaris = orion = lyra = spy = 1.0
    start = _dt.date(2020, 1, 1)
    for i in range(n_days):
        date = (start + _dt.timedelta(days=i)).isoformat()
        if i < 20:
            rows.append((date, 1.0, 1.0, 1.0, spy))
            spy *= (1.0 + random.gauss(0.0003, 0.005))
            continue
        if correlated:
            common = random.gauss(0.0006, 0.012)
            polaris_ret = common + random.gauss(polaris_seed, 0.002)
            orion_ret = common + random.gauss(orion_seed, 0.002)
            lyra_ret = common + random.gauss(lyra_seed, 0.002)
        else:
            polaris_ret = random.gauss(polaris_seed, 0.012)
            orion_ret = random.gauss(orion_seed, 0.012)
            lyra_ret = random.gauss(lyra_seed, 0.012)
        polaris *= (1.0 + polaris_ret)
        orion *= (1.0 + orion_ret)
        lyra *= (1.0 + lyra_ret)
        spy *= (1.0 + random.gauss(0.0003, 0.008))
        rows.append((date, polaris, orion, lyra, spy))
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["date", "caerus_polaris", "caerus_orion", "caerus_lyra", "spy_benchmark"])
        writer.writerows(rows)
    return path


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_behavioral_tier_buckets_by_threshold():
    assert sbd._behavioral_tier(0.95, 100) == "highly_similar_behavior"
    assert sbd._behavioral_tier(0.85, 100) == "highly_similar_behavior"
    assert sbd._behavioral_tier(0.70, 100) == "partially_similar_behavior"
    assert sbd._behavioral_tier(0.30, 100) == "behaviorally_differentiated"
    assert sbd._behavioral_tier(-0.95, 100) == "highly_similar_behavior"  # |corr|
    assert sbd._behavioral_tier(0.95, 10) == "insufficient_evidence"
    assert sbd._behavioral_tier(None, 1000) == "insufficient_evidence"


def test_diversification_verdict_tiers():
    v, _ = sbd._diversification_verdict(0.80, 3)
    assert v == "low_behavioral_diversification"
    v, _ = sbd._diversification_verdict(0.50, 3)
    assert v == "moderate_behavioral_diversification"
    v, _ = sbd._diversification_verdict(0.20, 3)
    assert v == "high_behavioral_diversification"
    v, _ = sbd._diversification_verdict(None, 0)
    assert v == "insufficient_evidence"


def test_candidate_inventory_returns_structured_payload(tmp_path):
    # No artifacts under tmp_path → all candidates missing.
    inv = sbd._candidate_inventory(tmp_path)
    assert "candidates_found" in inv
    assert "candidates_missing" in inv
    assert inv["candidates_found"] == []
    assert len(inv["candidates_missing"]) >= 1


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_analyse_happy_path_correlated_series(tmp_path):
    nav_path = tmp_path / "shadow_nav_series.csv"
    _synthesize_nav_csv(nav_path, n_days=400, correlated=True)
    answer = sbd.analyse_behavior_differentiation(nav_series_path=nav_path, repo_root=tmp_path)
    assert answer.status == "OK"
    # Strategies + window.
    assert "caerus_polaris" in answer.available_strategies
    assert answer.date_range_start is not None and answer.date_range_end is not None
    # Three challengers → 3 pairs.
    assert len(answer.behavior_pairs) == 3
    # Correlated fixture: tier should be highly_similar across pairs.
    for pair in answer.behavior_pairs:
        assert pair["n_observations"] >= sbd.MIN_OBSERVATIONS
        assert pair["return_correlation"] is not None
        assert pair["behavioral_similarity_tier"] == "highly_similar_behavior"
    # Diversification verdict is low when correlations are high.
    assert answer.behavioral_diversification_verdict == "low_behavioral_diversification"
    # Most-similar and most-differentiated pairs are populated.
    assert answer.most_behaviorally_similar_pair is not None
    assert answer.most_behaviorally_differentiated_pair is not None


def test_analyse_happy_path_uncorrelated_series(tmp_path):
    nav_path = tmp_path / "shadow_nav_series.csv"
    _synthesize_nav_csv(nav_path, n_days=400, correlated=False)
    answer = sbd.analyse_behavior_differentiation(nav_series_path=nav_path, repo_root=tmp_path)
    assert answer.status == "OK"
    # Uncorrelated returns → average correlation should be near zero.
    assert answer.average_pairwise_correlation is not None
    assert abs(answer.average_pairwise_correlation) < 0.30
    # Diversification verdict should be high or moderate (not low).
    assert answer.behavioral_diversification_verdict in {
        "high_behavioral_diversification",
        "moderate_behavioral_diversification",
    }


def test_analyse_pair_specific_filtering(tmp_path):
    nav_path = tmp_path / "shadow_nav_series.csv"
    _synthesize_nav_csv(nav_path, n_days=400)
    answer = sbd.analyse_behavior_differentiation(
        nav_series_path=nav_path,
        question="Do Orion and Lyra behave differently over time?",
        repo_root=tmp_path,
    )
    assert answer.status == "OK"
    # Both Orion and Lyra are named → exactly that one pair.
    assert len(answer.behavior_pairs) == 1
    slugs = {answer.behavior_pairs[0]["left_slug"], answer.behavior_pairs[0]["right_slug"]}
    assert slugs == {"caerus_orion", "caerus_lyra"}


def test_analyse_single_strategy_question_expands_to_all_pairs(tmp_path):
    """Asking only about Polaris should still produce all 3 pairs so the
    operator can see Polaris's relationships."""
    nav_path = tmp_path / "shadow_nav_series.csv"
    _synthesize_nav_csv(nav_path, n_days=400)
    answer = sbd.analyse_behavior_differentiation(
        nav_series_path=nav_path,
        question="How does Polaris behave?",
        repo_root=tmp_path,
    )
    assert answer.status == "OK"
    assert len(answer.behavior_pairs) == 3


def test_analyse_unknown_strategy_returns_needs_data(tmp_path):
    nav_path = tmp_path / "shadow_nav_series.csv"
    _synthesize_nav_csv(nav_path, n_days=400)
    answer = sbd.analyse_behavior_differentiation(
        nav_series_path=nav_path,
        strategies=["leda"],
        repo_root=tmp_path,
    )
    assert answer.status == "NEEDS_DATA"
    assert "caerus_leda" in answer.missing_strategies


def test_analyse_insufficient_observations_tags_pair(tmp_path):
    nav_path = tmp_path / "shadow_nav_series.csv"
    # Only 25 active days (20 pre-inception + 5 active) — below MIN_OBSERVATIONS.
    _synthesize_nav_csv(nav_path, n_days=25, correlated=True)
    answer = sbd.analyse_behavior_differentiation(nav_series_path=nav_path, repo_root=tmp_path)
    assert answer.status == "OK"
    # Every pair should be flagged insufficient_evidence.
    for pair in answer.behavior_pairs:
        assert pair["behavioral_similarity_tier"] == "insufficient_evidence"
        assert any(c.startswith("insufficient_observations") for c in pair["caveats"])


def test_analyse_rolling_correlation_requires_enough_data(tmp_path):
    """With ~40 active days we have enough for the full-window correlation
    but not enough for a stable rolling 60D window (needs 65 obs)."""
    nav_path = tmp_path / "shadow_nav_series.csv"
    _synthesize_nav_csv(nav_path, n_days=60, correlated=True)
    answer = sbd.analyse_behavior_differentiation(nav_series_path=nav_path, repo_root=tmp_path)
    pair = answer.behavior_pairs[0]
    # Caveat lists the missing rolling window explicitly.
    assert any("insufficient_rolling_60d" in c for c in pair["caveats"])
    # Rolling 60D stats should be empty (n=0).
    assert pair["rolling_60d_correlation"]["n"] == 0


def test_analyse_co_negative_days_and_worst_shared_drawdown(tmp_path):
    nav_path = tmp_path / "shadow_nav_series.csv"
    _synthesize_nav_csv(nav_path, n_days=400, correlated=True)
    answer = sbd.analyse_behavior_differentiation(nav_series_path=nav_path, repo_root=tmp_path)
    # Highly correlated → many shared negative days.
    assert answer.common_negative_days_count > 0
    pair = answer.behavior_pairs[0]
    assert pair["shared_negative_days"] > 0
    assert pair["shared_negative_pct"] is not None
    # Worst-shared-drawdown is populated (may or may not exist depending
    # on whether both strategies hit -5%; with 380 active days of noise
    # this fixture typically does).
    worst = pair.get("worst_shared_drawdown")
    if worst is not None:
        assert worst["date"] is not None
        assert worst["drawdown_left"] < sbd.SHARED_DRAWDOWN_FLOOR
        assert worst["drawdown_right"] < sbd.SHARED_DRAWDOWN_FLOOR


# ---------------------------------------------------------------------------
# Fail-closed paths
# ---------------------------------------------------------------------------


def test_no_return_stream_when_csv_absent(tmp_path):
    answer = sbd.analyse_behavior_differentiation(
        nav_series_path=tmp_path / "nope.csv",
        repo_root=tmp_path,
    )
    assert answer.status == "NO_RETURN_STREAM"
    assert answer.behavior_pairs == []
    assert answer.proposed_artifact_contract is not None
    assert "outputs/shadow_candidates/performance/shadow_nav_series.csv" in answer.proposed_artifact_contract
    # Candidate inventory populated.
    assert answer.candidate_artifact_inventory is not None


def test_no_return_stream_when_csv_has_no_caerus_columns(tmp_path):
    nav_path = tmp_path / "shadow_nav_series.csv"
    nav_path.write_text("date,spy_benchmark\n2024-01-01,1.0\n2024-01-02,1.001\n", encoding="utf-8")
    answer = sbd.analyse_behavior_differentiation(nav_series_path=nav_path, repo_root=tmp_path)
    assert answer.status == "NO_RETURN_STREAM"
    assert "nav_series_missing_caerus_columns" in answer.warnings


# ---------------------------------------------------------------------------
# MCP routing
# ---------------------------------------------------------------------------


def test_call_tool_routes_behavior_differentiation(tmp_path):
    nav_path = tmp_path / "shadow_nav_series.csv"
    _synthesize_nav_csv(nav_path, n_days=400, correlated=True)
    result = call_tool("strategy_behavior_differentiation", {
        "nav_series_path": str(nav_path),
    })
    assert result["tool"] == "strategy_behavior_differentiation"
    assert result["status"] == "OK"
    assert len(result["behavior_pairs"]) == 3


def test_call_tool_no_return_stream_when_path_missing(tmp_path):
    result = call_tool("strategy_behavior_differentiation", {
        "nav_series_path": str(tmp_path / "nope.csv"),
    })
    assert result["status"] == "NO_RETURN_STREAM"
    assert result["proposed_artifact_contract"] is not None
    assert "candidate_artifact_inventory" in result


@pytest.mark.parametrize("question", [
    "Do Orion and Lyra behave differently over time?",
    "Are the strategies correlated?",
    "Which strategies have the highest return correlation?",
    "Do the strategies draw down at the same time?",
    "Do we have behavioral diversification?",
    "Are Polaris, Orion, and Lyra just the same return stream?",
])
def test_planner_routes_behavior_questions(tmp_path, monkeypatch, question):
    nav_path = tmp_path / "outputs" / "shadow_candidates" / "performance" / "shadow_nav_series.csv"
    _synthesize_nav_csv(nav_path, n_days=400, correlated=True)
    monkeypatch.chdir(tmp_path)
    result = call_tool("answer_research_question", {
        "question": question,
        "nav_series_path": str(nav_path),
    })
    assert result["intent"] == "strategy_behavior_differentiation", question
    assert result["routed_to"] == "strategy_behavior_differentiation", question
    assert result["status"] == "OK", question


def test_planner_pass_through_nav_path_does_not_leak_to_unrelated_tools(tmp_path, monkeypatch):
    """The planner forwards ``nav_series_path`` to behavior_differentiation.
    The inspect-based filter must NOT forward it to tools that don't
    accept it (e.g. promotion_readiness, attribution_analysis)."""
    nav_path = tmp_path / "outputs" / "shadow_candidates" / "performance" / "shadow_nav_series.csv"
    _synthesize_nav_csv(nav_path, n_days=400)
    monkeypatch.chdir(tmp_path)
    # Question routes to promotion_readiness, not behavior. nav_series_path
    # kwarg in the call must be silently dropped (filtered) for that tool.
    result = call_tool("answer_research_question", {
        "question": "Is Orion ready for promotion?",
        "nav_series_path": str(nav_path),
    })
    assert result["intent"] == "promotion_readiness"
    assert result["routed_to"] == "promotion_readiness"
    # If the filter leaked, the call would have TypeError'd.


# ---------------------------------------------------------------------------
# Renderer (via the gateway entry point)
# ---------------------------------------------------------------------------


def test_renderer_shows_behavior_table_for_ok_payload(tmp_path):
    """Indirectly verify the renderer by inspecting stdout from the
    gateway main() — but cheaper to import the helper directly."""
    from scripts.research_mcp_ask import render_human_and_markdown
    nav_path = tmp_path / "shadow_nav_series.csv"
    _synthesize_nav_csv(nav_path, n_days=400, correlated=True)
    inner = sbd.behavior_differentiation_to_dict(
        sbd.analyse_behavior_differentiation(nav_series_path=nav_path, repo_root=tmp_path)
    )
    # The gateway expects the payload to live under .answer when wrapped;
    # the renderer also works when the payload IS the inner. Test the
    # planner-wrapped shape (more common).
    payload = {
        "status": "OK",
        "tool": "answer_research_question",
        "question": "Are the strategies correlated?",
        "intent": "strategy_behavior_differentiation",
        "routed_to": "strategy_behavior_differentiation",
        "warnings": [],
        "answer": inner,
    }
    human, md = render_human_and_markdown("Are the strategies correlated?", payload)
    assert "Diversification: low_behavioral_diversification" in human
    assert "Average pairwise correlation:" in human
    assert "Most behaviorally similar:" in human
    assert "highly_similar_behavior" in human
    assert "## Behavioral differentiation — NAV window" in md


def test_renderer_shows_no_return_stream_with_contract(tmp_path):
    from scripts.research_mcp_ask import render_human_and_markdown
    inner = sbd.behavior_differentiation_to_dict(
        sbd.analyse_behavior_differentiation(
            nav_series_path=tmp_path / "nope.csv",
            repo_root=tmp_path,
        )
    )
    payload = {
        "status": "NO_RETURN_STREAM",
        "tool": "answer_research_question",
        "question": "Are the strategies correlated?",
        "intent": "strategy_behavior_differentiation",
        "routed_to": "strategy_behavior_differentiation",
        "warnings": [],
        "answer": inner,
    }
    human, md = render_human_and_markdown("Are the strategies correlated?", payload)
    assert "Required artifact missing" in human
    assert "Proposed artifact contract:" in human
    assert "outputs/shadow_candidates/performance/shadow_nav_series.csv" in human
    assert "Proposed artifact contract" in md

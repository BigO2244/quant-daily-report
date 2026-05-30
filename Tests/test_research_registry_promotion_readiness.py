"""Targeted coverage for strategy-aware promotion readiness.

Pins the loader (shadow_evaluation + Phase C sidecar + per-strategy
stability_analysis), per-strategy recommendation derivation, ranking,
NEEDS_DATA / NO_SHADOW_DATA paths, and the end-to-end MCP routing
for the four question phrasings in the task spec.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_registry.mcp_server import call_tool
from research_registry.research import promotion_readiness as pr


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _eval_entry(
    *,
    name: str,
    nav: float,
    daily: float,
    cum: float,
    excess: float,
    data_status: str = "OK",
    max_drawdown: float | None = None,
    realized_vol: float | None = None,
    turnover: float = 0.10,
    concentration: float = 0.30,
    rolling_count_of_valid_days: float | None = None,
) -> dict:
    return {
        "strategy_name": name,
        "status": "OK",
        "data_status": data_status,
        "data_reason": None,
        "return_convention": "weights_as_of_t",
        "daily_return": daily,
        "nav": nav,
        "cumulative_return": cum,
        "excess_return_vs_spy": excess,
        "rolling_count_of_valid_days": rolling_count_of_valid_days,
        "realized_volatility_ann": realized_vol,
        "max_drawdown": max_drawdown,
        "avg_turnover": turnover,
        "avg_top_3_concentration": concentration,
    }


def _stability_entry(
    *,
    strategy: str,
    flags: list[str] | None = None,
    rolling_10d_valid_days: int = 1,
    rolling_30d_valid_days: int = 1,
) -> dict:
    return {
        "flags": list(flags or []),
        "rolling_windows": {
            "10d": {
                "avg_top_3_concentration": 0.3,
                "avg_turnover": 0.1,
                "constituent_change_count": 0,
                "excess_return_vs_spy": 0.029,
                "max_turnover": 0.2,
                "return": 0.039,
                "top_position_contribution_share": None,
                "valid_days": rolling_10d_valid_days,
            },
            "30d": {
                "avg_top_3_concentration": 0.3,
                "avg_turnover": 0.1,
                "constituent_change_count": 0,
                "excess_return_vs_spy": 0.029,
                "max_turnover": 0.2,
                "return": 0.039,
                "top_position_contribution_share": None,
                "valid_days": rolling_30d_valid_days,
            },
        },
        "status": "PARTIAL",
        "strategy": strategy,
        "trade_date": "2026-05-04",
    }


def _write_shadow_fixture(
    shadow_root: Path,
    date: str,
    *,
    strategies: dict[str, dict],
    phase_c: dict | None = None,
    stability: dict[str, dict] | None = None,
) -> Path:
    """Write a complete fixture under ``shadow_root/<date>/``."""
    date_dir = shadow_root / date
    date_dir.mkdir(parents=True, exist_ok=True)
    (date_dir / "shadow_evaluation.json").write_text(
        json.dumps({
            "trade_date": date,
            "benchmark_symbol": "SPY",
            "strategies": strategies,
        }, sort_keys=True),
        encoding="utf-8",
    )
    # comparison.json is read by the legacy helpers; keep it shallow.
    (date_dir / "comparison.json").write_text(
        json.dumps({
            "trade_date": date,
            "benchmark_symbol": "SPY",
            "strategies": {},
            "pairwise_overlap": [],
            "delta": {},
        }, sort_keys=True),
        encoding="utf-8",
    )
    if phase_c is not None:
        (date_dir / "promotion_readiness.json").write_text(
            json.dumps(phase_c, sort_keys=True), encoding="utf-8"
        )
    if stability:
        for short_name, entry in stability.items():
            sub = date_dir / short_name
            sub.mkdir(parents=True, exist_ok=True)
            (sub / "stability_analysis.json").write_text(
                json.dumps(entry, sort_keys=True), encoding="utf-8"
            )
    return date_dir


def _three_strategy_fixture(tmp_path: Path) -> Path:
    """Three challengers + benchmark; no Phase C sidecar (mirrors current
    local on-disk state)."""
    shadow_root = tmp_path / "outputs" / "shadow_candidates"
    _write_shadow_fixture(
        shadow_root, "2026-05-04",
        strategies={
            "caerus_polaris": _eval_entry(name="Caerus Polaris",
                                          nav=1.0397, daily=0.0397, cum=0.0397,
                                          excess=0.0297),
            "caerus_orion": _eval_entry(name="Caerus Orion",
                                        nav=1.0268, daily=0.0268, cum=0.0268,
                                        excess=0.0168),
            "caerus_lyra": _eval_entry(name="Caerus Lyra",
                                       nav=1.0357, daily=0.0357, cum=0.0357,
                                       excess=0.0258),
            "spy_benchmark": _eval_entry(name="SPY Benchmark",
                                         nav=1.0099, daily=0.0099, cum=0.0099,
                                         excess=0.0),
        },
        stability={
            "polaris": _stability_entry(strategy="polaris",
                                        flags=["INSUFFICIENT_VALID_DAYS"]),
            "orion": _stability_entry(strategy="orion",
                                      flags=["INSUFFICIENT_VALID_DAYS"]),
            "lyra": _stability_entry(strategy="lyra",
                                     flags=["INSUFFICIENT_VALID_DAYS"]),
        },
    )
    return shadow_root


# ---------------------------------------------------------------------------
# Recommendation derivation — unit tests on _derive_recommendation
# ---------------------------------------------------------------------------


def test_derive_recommendation_phase_c_candidate_for_capital_maps_to_promote():
    rec, conf, codes, blockers, expl = pr._derive_recommendation(
        metrics={},
        phase_c_state="CANDIDATE_FOR_CAPITAL",
        phase_c_confidence="HIGH",
        phase_c_reason_codes=["healthy_progression"],
        valid_observation_windows=24,
        stability_flags=[],
    )
    assert rec == "promote"
    assert conf == "HIGH"
    assert "healthy_progression" in codes
    assert blockers == []
    assert "CANDIDATE_FOR_CAPITAL" in expl


def test_derive_recommendation_phase_c_continue_shadow_maps_to_hold():
    rec, _, _, blockers, _ = pr._derive_recommendation(
        metrics={},
        phase_c_state="CONTINUE_SHADOW",
        phase_c_confidence="LOW",
        phase_c_reason_codes=[],
        valid_observation_windows=4,
        stability_flags=[],
    )
    assert rec == "hold"
    assert "phase_c_state:CONTINUE_SHADOW" in blockers


def test_derive_recommendation_phase_c_not_ready_maps_to_research_only():
    rec, _, _, _, _ = pr._derive_recommendation(
        metrics={},
        phase_c_state="NOT_READY",
        phase_c_confidence="LOW",
        phase_c_reason_codes=[],
        valid_observation_windows=2,
        stability_flags=[],
    )
    assert rec == "research_only"


def test_derive_recommendation_metric_path_excess_unavailable_is_insufficient_evidence():
    rec, conf, _, blockers, _ = pr._derive_recommendation(
        metrics={"data_status": "OK", "excess_return_vs_spy": None,
                 "max_drawdown": None, "realized_volatility_ann": None},
        phase_c_state=None,
        phase_c_confidence=None,
        phase_c_reason_codes=[],
        valid_observation_windows=0,
        stability_flags=[],
    )
    assert rec == "insufficient_evidence"
    assert conf == "LOW"
    assert "metric_unavailable:excess_return_vs_spy" in blockers


def test_derive_recommendation_metric_path_no_data_status_is_insufficient_evidence():
    rec, _, _, blockers, _ = pr._derive_recommendation(
        metrics={"data_status": "NO_DATA", "excess_return_vs_spy": 0.05,
                 "max_drawdown": -0.1, "realized_volatility_ann": 0.2},
        phase_c_state=None,
        phase_c_confidence=None,
        phase_c_reason_codes=[],
        valid_observation_windows=30,
        stability_flags=[],
    )
    assert rec == "insufficient_evidence"
    assert any(b.startswith("data_status:") for b in blockers)


def test_derive_recommendation_metric_path_negative_excess_is_research_only():
    rec, _, codes, blockers, expl = pr._derive_recommendation(
        metrics={"data_status": "OK", "excess_return_vs_spy": -0.02,
                 "max_drawdown": -0.10, "realized_volatility_ann": 0.20},
        phase_c_state=None,
        phase_c_confidence=None,
        phase_c_reason_codes=[],
        valid_observation_windows=30,
        stability_flags=[],
    )
    assert rec == "research_only"
    assert "negative_excess_vs_spy" in blockers
    assert "negative_excess_vs_spy" in codes
    assert "not currently beating" in expl


def test_derive_recommendation_metric_path_positive_excess_missing_gating_is_hold():
    """The local case: excess > 0 but max_drawdown / realized_vol are null
    and observation window is small. Expected: hold (not promote)."""
    rec, conf, _, blockers, _ = pr._derive_recommendation(
        metrics={"data_status": "OK", "excess_return_vs_spy": 0.03,
                 "max_drawdown": None, "realized_volatility_ann": None},
        phase_c_state=None,
        phase_c_confidence=None,
        phase_c_reason_codes=[],
        valid_observation_windows=0,
        stability_flags=["INSUFFICIENT_VALID_DAYS"],
    )
    assert rec == "hold"
    assert conf == "LOW"
    assert "metric_unavailable:max_drawdown" in blockers
    assert "metric_unavailable:realized_volatility_ann" in blockers
    assert "stability_flag:INSUFFICIENT_VALID_DAYS" in blockers
    assert any("insufficient_observation_window" in b for b in blockers)


def test_derive_recommendation_metric_path_all_green_is_promote():
    rec, conf, _, blockers, _ = pr._derive_recommendation(
        metrics={"data_status": "OK", "excess_return_vs_spy": 0.05,
                 "max_drawdown": -0.12, "realized_volatility_ann": 0.18},
        phase_c_state=None,
        phase_c_confidence=None,
        phase_c_reason_codes=[],
        valid_observation_windows=30,
        stability_flags=[],
    )
    assert rec == "promote"
    assert conf == "MODERATE"
    assert blockers == []


# ---------------------------------------------------------------------------
# Loader + assemble — assess_strategy_readiness
# ---------------------------------------------------------------------------


def test_assess_no_shadow_data_when_root_absent(tmp_path):
    answer = pr.assess_strategy_readiness(
        outputs_root=tmp_path / "nope",
        shadow_root=tmp_path / "nope" / "shadow_candidates",
    )
    assert answer.status == "NO_SHADOW_DATA"
    assert answer.strategy_panels == {}
    assert answer.closest_to_promotion is None


def test_assess_returns_all_strategies_when_no_names_in_question(tmp_path):
    shadow_root = _three_strategy_fixture(tmp_path)
    answer = pr.assess_strategy_readiness(
        outputs_root=tmp_path / "outputs",
        shadow_root=shadow_root,
    )
    assert answer.status == "OK"
    # Benchmark filtered out.
    assert set(answer.strategy_panels.keys()) == {
        "caerus_polaris", "caerus_orion", "caerus_lyra"
    }
    assert answer.has_phase_c_sidecar is False
    # All three should land on "hold" given positive excess but missing
    # gating metrics; ranking is tie-broken by excess descending.
    assert all(p["recommendation"] == "hold" for p in answer.strategy_panels.values())
    # Polaris has the highest excess (0.0297), then Lyra (0.0258), then Orion (0.0168).
    assert answer.closest_to_promotion == "caerus_polaris"
    assert answer.ranking_by_recommendation[0] == "caerus_polaris"
    assert answer.ranking_by_recommendation[-1] == "caerus_orion"


def test_assess_filters_by_question_strategy_names(tmp_path):
    shadow_root = _three_strategy_fixture(tmp_path)
    answer = pr.assess_strategy_readiness(
        outputs_root=tmp_path / "outputs",
        shadow_root=shadow_root,
        question="Is Orion ready for promotion?",
    )
    assert answer.status == "OK"
    # Only Orion in the panel.
    assert set(answer.strategy_panels.keys()) == {"caerus_orion"}
    assert answer.requested_strategies == ("orion",)


def test_assess_returns_needs_data_for_unknown_strategy(tmp_path):
    shadow_root = _three_strategy_fixture(tmp_path)
    answer = pr.assess_strategy_readiness(
        outputs_root=tmp_path / "outputs",
        shadow_root=shadow_root,
        strategies=["leda"],
    )
    assert answer.status == "NEEDS_DATA"
    assert "caerus_leda" in answer.missing_strategies


def test_assess_compare_polaris_and_orion(tmp_path):
    shadow_root = _three_strategy_fixture(tmp_path)
    answer = pr.assess_strategy_readiness(
        outputs_root=tmp_path / "outputs",
        shadow_root=shadow_root,
        question="Compare Polaris and Orion promotion readiness.",
    )
    assert answer.status == "OK"
    assert set(answer.strategy_panels.keys()) == {"caerus_polaris", "caerus_orion"}
    # Of the two, Polaris has higher excess → leads the ranking.
    assert answer.ranking_by_recommendation == ["caerus_polaris", "caerus_orion"]


def test_assess_uses_phase_c_sidecar_when_present(tmp_path):
    shadow_root = tmp_path / "outputs" / "shadow_candidates"
    _write_shadow_fixture(
        shadow_root, "2026-05-04",
        strategies={
            "caerus_polaris": _eval_entry(name="Polaris", nav=1.04, daily=0.04,
                                          cum=0.04, excess=0.03),
        },
        phase_c={
            "trade_date": "2026-05-04",
            "current_leader": "caerus_polaris",
            "strategies": {
                "caerus_polaris": {
                    "readiness_state": "CANDIDATE_FOR_CAPITAL",
                    "confidence": "HIGH",
                    "reason_codes": ["healthy_progression"],
                    "valid_observation_windows": 24,
                    "cumulative_excess_vs_spy": 0.03,
                    "max_drawdown": -0.12,
                    "avg_turnover": 0.10,
                    "avg_top_3_concentration": 0.30,
                }
            },
        },
    )
    answer = pr.assess_strategy_readiness(
        outputs_root=tmp_path / "outputs",
        shadow_root=shadow_root,
    )
    assert answer.status == "OK"
    assert answer.has_phase_c_sidecar is True
    panel = answer.strategy_panels["caerus_polaris"]
    assert panel["readiness_state"] == "CANDIDATE_FOR_CAPITAL"
    assert panel["phase_c_confidence"] == "HIGH"
    assert panel["recommendation"] == "promote"
    assert panel["confidence"] == "HIGH"
    assert "healthy_progression" in panel["reason_codes"]
    assert panel["valid_observation_windows"] == 24


def test_assess_surfaces_stability_flags_in_blockers(tmp_path):
    shadow_root = _three_strategy_fixture(tmp_path)
    answer = pr.assess_strategy_readiness(
        outputs_root=tmp_path / "outputs",
        shadow_root=shadow_root,
        strategies=["polaris"],
    )
    panel = answer.strategy_panels["caerus_polaris"]
    assert "stability_flag:INSUFFICIENT_VALID_DAYS" in panel["blockers"]


def test_assess_surfaces_non_ok_data_status_as_warning(tmp_path):
    shadow_root = tmp_path / "outputs" / "shadow_candidates"
    _write_shadow_fixture(
        shadow_root, "2026-05-04",
        strategies={
            "caerus_polaris": _eval_entry(
                name="Polaris", nav=1.04, daily=0.0, cum=0.04, excess=0.0297,
                data_status="NO_DATA",
            ),
        },
    )
    answer = pr.assess_strategy_readiness(
        outputs_root=tmp_path / "outputs",
        shadow_root=shadow_root,
    )
    assert any("non-OK data_status" in w for w in answer.warnings)


def test_assess_records_unavailable_metrics_per_strategy(tmp_path):
    shadow_root = _three_strategy_fixture(tmp_path)
    answer = pr.assess_strategy_readiness(
        outputs_root=tmp_path / "outputs",
        shadow_root=shadow_root,
        strategies=["polaris"],
    )
    panel = answer.strategy_panels["caerus_polaris"]
    # Source fixture sets max_drawdown and realized_volatility_ann to None.
    assert "max_drawdown" in panel["unavailable_metrics"]
    assert "realized_volatility_ann" in panel["unavailable_metrics"]


def test_assess_explanation_grounded_in_artifacts(tmp_path):
    shadow_root = _three_strategy_fixture(tmp_path)
    answer = pr.assess_strategy_readiness(
        outputs_root=tmp_path / "outputs",
        shadow_root=shadow_root,
        question="Why is Lyra not promotion-ready?",
    )
    panel = answer.strategy_panels["caerus_lyra"]
    assert panel["recommendation"] == "hold"
    assert "+" in panel["explanation"]  # signed excess present
    assert "Hold pending" in panel["explanation"]


# ---------------------------------------------------------------------------
# MCP routing
# ---------------------------------------------------------------------------


def test_observation_evidence_block_is_additive_and_governance_unchanged(tmp_path):
    """Patch contract: three distinct evidence counts exist additively
    (valid_shadow_observation_days, valid_live_execution_days,
    promotion_evidence_days), the legacy valid_observation_windows stays
    at 0 when Phase C sidecar is absent, and the recommendation tier
    remains 'hold' (capital governance preserved)."""
    shadow_root = _three_strategy_fixture(tmp_path)
    # Write a small NAV series so shadow-day counts are non-zero.
    import datetime as _dt
    perf_dir = shadow_root / "performance"
    perf_dir.mkdir(parents=True, exist_ok=True)
    nav_path = perf_dir / "shadow_nav_series.csv"
    start = _dt.date(2020, 1, 1)
    rows = ["date,caerus_polaris,caerus_orion,caerus_lyra,spy_benchmark"]
    polaris = orion = lyra = spy = 1.0
    for i in range(50):
        d = (start + _dt.timedelta(days=i)).isoformat()
        if i >= 10:  # inception at row 10 so NAV != 1.0 from there on
            polaris += 0.001 * (i - 9)
            orion += 0.002 * (i - 9)
            lyra += 0.0015 * (i - 9)
            spy += 0.0005 * (i - 9)
        rows.append(f"{d},{polaris},{orion},{lyra},{spy}")
    nav_path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    result = call_tool("promotion_readiness", {
        "outputs_root": str(shadow_root.parent),
    })
    # New top-level block present.
    evidence = result.get("observation_evidence")
    assert isinstance(evidence, dict)
    assert "valid_shadow_observation_days" in evidence
    assert "valid_live_execution_days" in evidence
    assert "promotion_evidence_days" in evidence
    assert "explanation" in evidence
    # Shadow NAV count is > 0 (~40 active days after the 10-day pre-inception block).
    shadow_days = evidence["valid_shadow_observation_days"]
    assert shadow_days.get("caerus_polaris", 0) > 0
    # Live execution count = 1 (one dated dir with OK data_status in the fixture).
    live_days = evidence["valid_live_execution_days"]
    assert live_days.get("caerus_polaris", 0) >= 1
    # Strict Phase C count is 0 (no stable_window_evaluation artifact in fixture).
    strict = evidence["promotion_evidence_days"]["strict"]
    assert strict["valid_days_since_inception"] == 0

    # Governance unchanged: per-panel valid_observation_windows is still 0
    # and recommendation is 'hold' (not promoted to 'promote' by the new evidence).
    panel = next(iter(result["strategy_panels"].values()))
    assert panel["valid_observation_windows"] == 0
    assert panel["recommendation"] == "hold"
    # Per-panel additive fields surface alongside the legacy field.
    assert panel["valid_shadow_observation_days"] > 0
    assert "valid_live_execution_days" in panel
    # The legacy blocker text still appears.
    blockers = panel.get("blockers") or []
    assert any("insufficient_observation_window" in b for b in blockers)


def test_observation_evidence_handles_missing_nav_and_stable_window_artifacts(tmp_path):
    """If neither NAV CSV nor stable_window_evaluation/*.json exist,
    the new fields gracefully report 0 / source_path_missing=True without
    crashing or affecting the legacy recommendation."""
    shadow_root = _three_strategy_fixture(tmp_path)
    result = call_tool("promotion_readiness", {
        "outputs_root": str(shadow_root.parent),
    })
    evidence = result["observation_evidence"]
    # No NAV CSV in the fixture → all shadow_observation counts are 0.
    assert all(n == 0 for n in evidence["valid_shadow_observation_days"].values())
    # latest_loose.json / latest_strict.json absent → source_path_missing flag set.
    assert evidence["promotion_evidence_days"]["loose"]["source_path_missing"] is True
    assert evidence["promotion_evidence_days"]["strict"]["source_path_missing"] is True


def test_call_tool_promotion_readiness_remains_backward_compatible(tmp_path):
    """Calling promotion_readiness with NO strategy/question kwargs should
    still produce the legacy top-level OK status + recommendation field."""
    shadow_root = _three_strategy_fixture(tmp_path)
    result = call_tool("promotion_readiness", {
        "outputs_root": str(shadow_root.parent),
    })
    # Legacy fields preserved.
    assert result["status"] == "OK"
    for legacy_field in (
        "current_leader",
        "recommendation",
        "confidence_level",
        "valid_observation_window_count",
        "phase_c_readiness",
        "evidence",
        "guardrail",
    ):
        assert legacy_field in result, f"missing legacy field {legacy_field!r}"
    # New strategy fields are additive.
    assert "strategy_panels" in result
    assert "closest_to_promotion" in result
    assert "ranking_by_recommendation" in result
    assert "has_phase_c_sidecar" in result


def test_call_tool_promotion_readiness_question_filters_strategies(tmp_path):
    shadow_root = _three_strategy_fixture(tmp_path)
    result = call_tool("promotion_readiness", {
        "outputs_root": str(shadow_root.parent),
        "question": "Is Orion ready for promotion?",
    })
    assert result["status"] == "OK"
    assert set(result["strategy_panels"].keys()) == {"caerus_orion"}


@pytest.mark.parametrize("question", [
    "Is Orion ready for promotion?",
    "Compare Polaris and Orion promotion readiness.",
    "Which strategy is closest to promotion?",
    "Why is Lyra not promotion-ready?",
])
def test_planner_routes_promotion_questions(tmp_path, monkeypatch, question):
    shadow_root = _three_strategy_fixture(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = call_tool("answer_research_question", {
        "question": question,
        "outputs_root": str(shadow_root.parent),
    })
    assert result["intent"] == "promotion_readiness", question
    assert result["routed_to"] == "promotion_readiness", question
    assert result["status"] == "OK", question


def test_planner_closest_to_promotion_question_returns_ranking(tmp_path, monkeypatch):
    shadow_root = _three_strategy_fixture(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = call_tool("answer_research_question", {
        "question": "Which strategy is closest to promotion?",
        "outputs_root": str(shadow_root.parent),
    })
    assert result["status"] == "OK"
    inner = result["answer"]
    assert inner["closest_to_promotion"] == "caerus_polaris"
    assert inner["ranking_by_recommendation"][0] == "caerus_polaris"


def test_planner_compare_question_filters_to_two_strategies(tmp_path, monkeypatch):
    shadow_root = _three_strategy_fixture(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = call_tool("answer_research_question", {
        "question": "Compare Polaris and Orion promotion readiness.",
        "outputs_root": str(shadow_root.parent),
    })
    assert result["status"] == "OK"
    inner = result["answer"]
    assert set(inner["strategy_panels"].keys()) == {"caerus_polaris", "caerus_orion"}

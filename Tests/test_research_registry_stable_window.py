"""Targeted coverage for stable_window_evaluation MCP tool.

Pins the CSV loader, per-policy dispersion math, the consistency +
sensitivity heuristics, the promotion-validity reader, the
NO_WINDOW_DATA fail-closed path, the insufficient_sample flag, and
the end-to-end MCP routing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_registry.mcp_server import call_tool
from research_registry.research import stable_window_evaluation as swe


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_WINDOW_HEADERS = (
    "start_date,end_date,policy,exposure_multiplier,total_return,cagr,sharpe,"
    "max_drawdown,ulcer_index,avg_turnover,n_days,trade_count,invested_before,"
    "invested_after,cash_target,ending_equity,allow_empty_sleeves,window_id"
)


def _write_random_windows(
    research_root: Path,
    policy: str,
    years: int,
    rows: list[tuple[str, str, float, float, float, float, float]],
) -> Path:
    """rows = [(start, end, total_return, cagr, sharpe, max_drawdown, ulcer)]"""
    research_root.mkdir(parents=True, exist_ok=True)
    path = research_root / f"random_windows_{years}y_{policy.lower()}.csv"
    lines = [_WINDOW_HEADERS]
    for i, (s, e, tr, cagr, sh, dd, ul) in enumerate(rows, start=1):
        lines.append(
            f"{s},{e},{policy},1.0,{tr},{cagr},{sh},{dd},{ul},0.08,756,3900,1.0,1.0,0.0,10000.0,False,{i}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_summary(
    research_root: Path,
    policy: str,
    n_windows: int,
    median_cagr: float,
    worst_cagr: float,
    worst_drawdown: float,
) -> Path:
    research_root.mkdir(parents=True, exist_ok=True)
    path = research_root / f"random_windows_summary_{policy.lower()}.csv"
    path.write_text(
        "policy,years,n_windows,seed,selection_metric,worst_start_date,"
        "worst_end_date,worst_max_drawdown,worst_cagr,worst_ulcer_index,"
        "median_cagr,median_max_drawdown,median_ulcer_index,mean_trade_count\n"
        f"{policy},3,{n_windows},42,MAX_DD,2022-01-07,2025-01-06,"
        f"{worst_drawdown},{worst_cagr},16.35,{median_cagr},-0.27,10.85,3914\n",
        encoding="utf-8",
    )
    return path


def _write_promotion_validity(stable_root: Path, mode: str, valid_days: int = 0) -> Path:
    stable_root.mkdir(parents=True, exist_ok=True)
    path = stable_root / f"latest_{mode}.json"
    path.write_text(json.dumps({
        "schema_version": "1.1",
        "validity_mode": mode,
        "generated_at": "2026-05-07T15:16:43Z",
        "windows": {"since": {}, "stable": {}, "rolling14": {}, "rolling30": {}},
        "strategies": {"caerus_polaris": {}, "caerus_orion": {}},
        "valid_days_since_inception": [{} for _ in range(valid_days)],
        "valid_days_stable_window": [{} for _ in range(valid_days)],
        "shadow_only_days_since_inception": [{"trade_date": "2026-04-30"}],
        "shadow_only_days_stable_window": [{"trade_date": "2026-04-30"}],
        "diagnostic_excluded_since": [{"trade_date": "2026-03-24"}] * 7,
    }), encoding="utf-8")
    return path


def _good_rows(n: int = 50) -> list[tuple[str, str, float, float, float, float, float]]:
    """Synthesised plausible window outcomes — most windows positive."""
    out = []
    for i in range(n):
        # CAGR varies from -0.05 to +0.40, max_drawdown -0.10 to -0.45.
        cagr = -0.05 + (i / (n - 1)) * 0.45
        dd = -0.10 - (i % 7) * 0.05
        sh = cagr / 0.18 if cagr else 0.0
        out.append((f"2020-{(i % 12) + 1:02d}-01", f"2023-{(i % 12) + 1:02d}-01",
                    cagr * 3, cagr, sh, dd, abs(dd) * 50))
    return out


# ---------------------------------------------------------------------------
# Percentile helper
# ---------------------------------------------------------------------------


def test_percentile_handles_single_value_and_interpolation():
    assert swe._percentile([5.0], 0.5) == 5.0
    # 10 values: p10 interpolates between sorted[0] and sorted[1].
    p10 = swe._percentile([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0], 0.10)
    # rank = 0.10 * 9 = 0.9 → sorted[0]*0.1 + sorted[1]*0.9 = 0.1 + 1.8 = 1.9
    assert p10 == pytest.approx(1.9)


def test_percentile_empty_returns_none():
    assert swe._percentile([], 0.5) is None


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_discover_random_window_files_picks_only_named_pattern(tmp_path):
    root = tmp_path / "outputs" / "research"
    root.mkdir(parents=True)
    _write_random_windows(root, "FULL", 3, _good_rows(5))
    _write_random_windows(root, "PARTIAL", 3, _good_rows(5))
    # Decoy files that should be ignored.
    (root / "random_windows_summary_full.csv").write_text("not a window file\n")
    (root / "some_other_file.csv").write_text("ignore\n")
    found = swe.discover_random_window_files(root)
    policies = [policy for policy, _, _ in found]
    assert policies == ["FULL", "PARTIAL"]


def test_discover_returns_empty_when_research_root_missing(tmp_path):
    assert swe.discover_random_window_files(tmp_path / "nope") == []


# ---------------------------------------------------------------------------
# Per-policy panel
# ---------------------------------------------------------------------------


def test_policy_panel_computes_dispersion_and_consistency(tmp_path):
    root = tmp_path / "outputs" / "research"
    _write_random_windows(root, "FULL", 3, _good_rows(50))
    _write_summary(root, "FULL", 50, median_cagr=0.24, worst_cagr=0.05, worst_drawdown=-0.35)
    answer = swe.evaluate_stable_windows(
        research_root=root, stable_window_root=tmp_path / "no-promotion",
    )
    assert answer.status == "OK"
    assert len(answer.policy_panels) == 1
    panel = answer.policy_panels[0]
    assert panel["policy"] == "FULL"
    assert panel["years"] == 3
    assert panel["n_windows"] == 50
    assert not panel["insufficient_sample"]
    # Dispersion populated for all four metrics.
    for metric in ("cagr", "max_drawdown", "sharpe", "ulcer_index"):
        d = panel["dispersion"][metric]
        assert d["n"] == 50
        assert d["median"] is not None
        assert d["range"] is not None
    # Consistency: fraction_positive_return ≤ 1.
    fp = panel["consistency"]["fraction_positive_return"]
    assert fp is not None and 0.0 <= fp <= 1.0
    # Source summary cross-referenced.
    assert panel["source_summary"]["policy"] == "FULL"
    assert panel["source_summary"]["median_cagr"] == pytest.approx(0.24)


def test_policy_panel_flags_insufficient_sample_below_30(tmp_path):
    root = tmp_path / "outputs" / "research"
    _write_random_windows(root, "FULL", 3, _good_rows(10))
    answer = swe.evaluate_stable_windows(
        research_root=root, stable_window_root=tmp_path / "no-promotion",
    )
    assert answer.policy_panels[0]["insufficient_sample"] is True
    assert "insufficient_sample" in answer.confidence_caveats


def test_policy_panel_picks_best_and_worst_windows(tmp_path):
    root = tmp_path / "outputs" / "research"
    rows = _good_rows(50)
    # Tag a clear best + worst so we can assert exact dates.
    rows[0] = ("2018-01-01", "2021-01-01", 0.10, 0.03, 0.2, -0.55, 25.0)  # worst drawdown
    rows[-1] = ("2019-06-01", "2022-06-01", 1.50, 0.45, 1.8, -0.20, 10.0)  # best CAGR
    _write_random_windows(root, "FULL", 3, rows)
    answer = swe.evaluate_stable_windows(
        research_root=root, stable_window_root=tmp_path / "no-promotion",
    )
    panel = answer.policy_panels[0]
    assert panel["best_window_by_cagr"]["start_date"] == "2019-06-01"
    assert panel["worst_window_by_drawdown"]["start_date"] == "2018-01-01"


def test_start_date_sensitivity_buckets(tmp_path):
    root = tmp_path / "outputs" / "research"
    # Very low dispersion: CAGRs clustered tightly.
    rows = [
        ("2020-01-01", "2023-01-01", 0.30, 0.10, 0.6, -0.20, 10.0),
        ("2020-02-01", "2023-02-01", 0.31, 0.103, 0.61, -0.20, 10.0),
        ("2020-03-01", "2023-03-01", 0.29, 0.097, 0.59, -0.20, 10.0),
    ] * 12  # 36 rows
    _write_random_windows(root, "FULL", 3, rows)
    answer = swe.evaluate_stable_windows(
        research_root=root, stable_window_root=tmp_path / "no-promotion",
    )
    assert answer.policy_panels[0]["start_date_sensitivity"]["interpretation"] == "low"


# ---------------------------------------------------------------------------
# Promotion-validity
# ---------------------------------------------------------------------------


def test_promotion_validity_block_surfaced(tmp_path):
    research_root = tmp_path / "outputs" / "research"
    _write_random_windows(research_root, "FULL", 3, _good_rows(50))
    stable_root = research_root / "stable_window_evaluation"
    _write_promotion_validity(stable_root, "loose", valid_days=0)
    _write_promotion_validity(stable_root, "strict", valid_days=0)
    answer = swe.evaluate_stable_windows(
        research_root=research_root, stable_window_root=stable_root,
    )
    assert answer.promotion_validity is not None
    assert "loose" in answer.promotion_validity
    assert "strict" in answer.promotion_validity
    assert answer.promotion_validity["loose"]["valid_days_since_inception"] == 0
    assert "zero_valid_days_for_promotion_math" in answer.confidence_caveats


def test_no_window_data_when_both_sources_absent(tmp_path):
    answer = swe.evaluate_stable_windows(
        research_root=tmp_path / "nope-research",
        stable_window_root=tmp_path / "nope-stable",
    )
    assert answer.status == "NO_WINDOW_DATA"
    assert answer.policy_panels == []
    assert "no_random_window_csv" in answer.confidence_caveats


# ---------------------------------------------------------------------------
# MCP routing
# ---------------------------------------------------------------------------


def test_call_tool_routes_stable_window_evaluation(tmp_path):
    root = tmp_path / "outputs" / "research"
    _write_random_windows(root, "FULL", 3, _good_rows(50))
    result = call_tool(
        "stable_window_evaluation",
        {"research_root": str(root), "stable_window_root": str(tmp_path / "no-promotion")},
    )
    assert result["tool"] == "stable_window_evaluation"
    assert result["status"] == "OK"
    assert result["policy_panels"][0]["policy"] == "FULL"
    assert "narrative" in result


def test_planner_routes_stable_window_questions(tmp_path, monkeypatch):
    root = tmp_path / "outputs" / "research"
    _write_random_windows(root, "FULL", 3, _good_rows(50))
    monkeypatch.chdir(tmp_path)  # so artifact pre-check passes
    for question in (
        "How does the strategy perform across random windows?",
        "Stable window Sharpe distribution.",
        "How consistent is the strategy across backtest windows?",
        "Start-date sensitivity for the 3-year backtest.",
    ):
        result = call_tool("answer_research_question", {
            "question": question,
            "research_root": str(root),
            "stable_window_root": str(tmp_path / "no-promotion"),
        })
        assert result["intent"] == "stable_window_evaluation", question
        assert result["routed_to"] == "stable_window_evaluation", question
        assert result["status"] == "OK", question


def test_planner_returns_needs_data_when_no_random_windows(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = call_tool(
        "answer_research_question",
        {"question": "How does the strategy perform across random windows?"},
    )
    assert result["intent"] == "stable_window_evaluation"
    assert result["status"] == "NEEDS_DATA"
    assert "outputs/research/random_windows_*.csv" in result["missing_artifacts"]

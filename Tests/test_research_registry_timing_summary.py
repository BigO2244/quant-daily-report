"""Targeted coverage for execution_timing_summary (aggregate, non-regime).

Pins the loader, the offset/clock-time parser, the recommendation rule,
and the end-to-end MCP routing for the three question phrasings in the
task spec.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_registry.mcp_server import call_tool
from research_registry.research import timing_summary as ts


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


_OFFSETS = ["T+0m", "T+1m", "T+2m", "T+3m", "T+4m", "T+5m", "T+10m"]
_BASELINE = "T+5m"


def _write_timing_summary(
    timing_root: Path,
    run_date: str,
    *,
    days_replayed: int,
    mean_by_offset: dict[str, float],
    median_by_offset: dict[str, float] | None = None,
    n_by_offset: dict[str, int] | None = None,
) -> Path:
    median_by_offset = median_by_offset or {k: v for k, v in mean_by_offset.items()}
    n_by_offset = n_by_offset or {k: days_replayed for k in mean_by_offset}
    run_dir = timing_root / run_date
    run_dir.mkdir(parents=True, exist_ok=True)
    by_offset = {}
    for label in _OFFSETS:
        by_offset[label] = {
            "opportunity_usd": {
                "mean": mean_by_offset.get(label, 0.0),
                "median": median_by_offset.get(label, 0.0),
                "p10": 0.0,
                "p90": 0.0,
                "sum": mean_by_offset.get(label, 0.0) * n_by_offset.get(label, days_replayed),
                "n": n_by_offset.get(label, days_replayed),
            },
            "opportunity_bps": {
                "mean": mean_by_offset.get(label, 0.0) * 10.0,
                "median": median_by_offset.get(label, 0.0) * 10.0,
                "p10": 0.0,
                "p90": 0.0,
                "sum": 0.0,
                "n": n_by_offset.get(label, days_replayed),
            },
            "cost_usd": {"mean": 0.0, "median": 0.0, "p10": 0.0, "p90": 0.0, "sum": 0.0, "n": 0},
            "gross_notional_usd": {"mean": 1000.0, "median": 1000.0, "p10": 0.0, "p90": 0.0, "sum": 0.0, "n": 0},
        }
    payload = {
        "schema_version": "1.0",
        "run_date": run_date,
        "generated_at": "2026-05-29T14:00:00Z",
        "cache_key_version": "intraday_bars_v1_iex_0925_1030",
        "offsets": _OFFSETS,
        "baseline_offset": _BASELINE,
        "coverage_summary": {"days_replayed": days_replayed, "days_in_scope": days_replayed},
        "by_offset": by_offset,
    }
    (run_dir / "timing_summary.json").write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return run_dir


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("question,expected", [
    ("Is 9:35 better than 9:30?",                ("T+5m", "T+0m")),
    ("What is the best execution time?",         ()),
    ("Compare 9:30 and 9:35 execution timing.", ("T+0m", "T+5m")),
    ("look at T+0m and T+5m",                   ("T+0m", "T+5m")),
    ("between 9:32 and 9:34",                   ("T+2m", "T+4m")),
])
def test_parse_offset_highlights(question, expected):
    assert ts.parse_offset_highlights(question) == expected


def test_parse_offset_highlights_ignores_out_of_band_minutes():
    # 9:62 doesn't exist; 8:35 is before market open by our heuristic
    assert ts.parse_offset_highlights("look at 9:62 or 8:35 please") == ()


# ---------------------------------------------------------------------------
# Summariser
# ---------------------------------------------------------------------------


def test_summarise_returns_no_timing_data_when_root_absent(tmp_path):
    answer = ts.summarise_timing(timing_root=tmp_path / "nope")
    assert answer.status == "NO_TIMING_DATA"
    assert answer.recommendation == "insufficient_evidence"


def test_summarise_picks_latest_run(tmp_path):
    timing_root = tmp_path / "outputs" / "research" / "execution_timing"
    _write_timing_summary(timing_root, "2026-04-01", days_replayed=10,
                          mean_by_offset={"T+0m": 1.0, "T+5m": 0.0, "T+10m": -1.0})
    _write_timing_summary(timing_root, "2026-05-29", days_replayed=20,
                          mean_by_offset={"T+0m": 5.0, "T+5m": 0.0, "T+10m": -5.0})
    answer = ts.summarise_timing(timing_root=timing_root)
    assert answer.run_date == "2026-05-29"
    assert answer.days_replayed == 20


def test_summarise_recommendation_insufficient_evidence_below_threshold(tmp_path):
    timing_root = tmp_path / "outputs" / "research" / "execution_timing"
    _write_timing_summary(timing_root, "2026-05-29", days_replayed=3,
                          mean_by_offset={"T+0m": 5.0, "T+5m": 0.0, "T+10m": -5.0},
                          n_by_offset={k: 3 for k in _OFFSETS})
    answer = ts.summarise_timing(timing_root=timing_root)
    assert answer.recommendation == "insufficient_evidence"
    assert "below the threshold" in answer.recommendation_reason


def test_summarise_recommendation_earlier_better_when_t0_dominates(tmp_path):
    timing_root = tmp_path / "outputs" / "research" / "execution_timing"
    _write_timing_summary(
        timing_root, "2026-05-29", days_replayed=20,
        mean_by_offset={"T+0m": 5.0, "T+1m": 4.0, "T+5m": 0.0, "T+10m": -5.0},
        median_by_offset={"T+0m": 4.5, "T+1m": 3.8, "T+5m": 0.0, "T+10m": -5.0},
    )
    answer = ts.summarise_timing(timing_root=timing_root)
    assert answer.recommendation == "earlier_timing_appears_better"
    assert answer.best_offset == "T+0m"


def test_summarise_recommendation_retain_when_baseline_wins(tmp_path):
    timing_root = tmp_path / "outputs" / "research" / "execution_timing"
    _write_timing_summary(
        timing_root, "2026-05-29", days_replayed=20,
        mean_by_offset={"T+0m": -2.0, "T+1m": -1.5, "T+5m": 0.0, "T+10m": -3.0},
        median_by_offset={"T+0m": -2.0, "T+1m": -1.5, "T+5m": 0.0, "T+10m": -3.0},
    )
    answer = ts.summarise_timing(timing_root=timing_root)
    assert answer.recommendation == "retain_9_35_baseline"


def test_summarise_recommendation_retain_when_mean_positive_but_median_zero(tmp_path):
    """Outlier-driven positive mean alone is not enough — median must also be > 0."""
    timing_root = tmp_path / "outputs" / "research" / "execution_timing"
    _write_timing_summary(
        timing_root, "2026-05-29", days_replayed=20,
        mean_by_offset={"T+0m": 5.0, "T+5m": 0.0, "T+10m": -5.0},
        median_by_offset={"T+0m": 0.0, "T+5m": 0.0, "T+10m": -5.0},
    )
    answer = ts.summarise_timing(timing_root=timing_root)
    assert answer.recommendation == "retain_9_35_baseline"


def test_summarise_recommendation_retain_when_later_offset_wins(tmp_path):
    """If T+10 has the best mean opportunity, the recommendation is still
    retain — we won't push for moving execution later."""
    timing_root = tmp_path / "outputs" / "research" / "execution_timing"
    _write_timing_summary(
        timing_root, "2026-05-29", days_replayed=20,
        mean_by_offset={"T+0m": -1.0, "T+5m": 0.0, "T+10m": 3.0},
        median_by_offset={"T+0m": -1.0, "T+5m": 0.0, "T+10m": 3.0},
    )
    answer = ts.summarise_timing(timing_root=timing_root)
    assert answer.best_offset == "T+10m"
    assert answer.recommendation == "retain_9_35_baseline"
    assert "not earlier" in answer.recommendation_reason


def test_summarise_highlights_offsets_from_question(tmp_path):
    timing_root = tmp_path / "outputs" / "research" / "execution_timing"
    _write_timing_summary(timing_root, "2026-05-29", days_replayed=20,
                          mean_by_offset={"T+0m": 5.0, "T+5m": 0.0})
    answer = ts.summarise_timing(timing_root=timing_root, question="Is 9:35 better than 9:30?")
    assert "T+5m" in answer.highlighted_offsets
    assert "T+0m" in answer.highlighted_offsets


# ---------------------------------------------------------------------------
# MCP routing
# ---------------------------------------------------------------------------


def test_call_tool_routes_execution_timing_summary(tmp_path):
    timing_root = tmp_path / "outputs" / "research" / "execution_timing"
    _write_timing_summary(
        timing_root, "2026-05-29", days_replayed=20,
        mean_by_offset={"T+0m": 5.0, "T+5m": 0.0, "T+10m": -5.0},
        median_by_offset={"T+0m": 4.5, "T+5m": 0.0, "T+10m": -5.0},
    )
    result = call_tool(
        "execution_timing_summary",
        {"timing_root": str(timing_root), "question": "Is 9:35 better than 9:30?"},
    )
    assert result["tool"] == "execution_timing_summary"
    assert result["status"] == "OK"
    assert result["baseline_offset"] == "T+5m"
    assert result["recommendation"] == "earlier_timing_appears_better"
    assert "T+0m" in result["highlighted_offsets"]


def test_planner_routes_question_to_timing_summary(tmp_path, monkeypatch):
    """The capability planner should route 'Is 9:35 better than 9:30?' to
    execution_timing_summary (renamed from the prior baseline capability)."""
    timing_root = tmp_path / "outputs" / "research" / "execution_timing"
    _write_timing_summary(
        timing_root, "2026-05-29", days_replayed=20,
        mean_by_offset={"T+0m": 5.0, "T+5m": 0.0, "T+10m": -5.0},
    )
    monkeypatch.chdir(tmp_path)  # so artifact pre-check passes
    result = call_tool(
        "answer_research_question",
        {"question": "Is 9:35 better than 9:30?", "timing_root": str(timing_root)},
    )
    assert result["intent"] == "execution_timing_summary"
    assert result["routed_to"] == "execution_timing_summary"
    assert result["status"] == "OK"
    assert "T+0m" in result["answer"]["highlighted_offsets"]

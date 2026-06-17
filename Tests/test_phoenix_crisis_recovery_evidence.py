from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scripts.research.build_phoenix_crisis_recovery_evidence import (
    _phoenix_candidates,
    detect_crisis_windows,
)


def test_detect_crisis_windows_from_spy_drawdown() -> None:
    dates = pd.bdate_range("2020-01-01", periods=90)
    prices = [100.0] * 20 + [95.0, 90.0, 84.0, 80.0, 82.0, 86.0, 90.0, 94.0, 99.0] + [101.0] * 61
    spy = pd.Series(prices[: len(dates)], index=dates)

    windows = detect_crisis_windows(
        spy,
        start_date="2020-01-01",
        end_date="2020-05-05",
        threshold=-0.12,
        min_separation_days=30,
    )

    assert len(windows) == 1
    assert windows[0]["crisis_start"] == "2020-01-01"
    assert windows[0]["trough_date"] == str(dates[23].date())
    assert windows[0]["spy_drawdown_at_trough"] == -0.2


def test_phoenix_candidates_select_dislocated_pit_names() -> None:
    dates = pd.bdate_range("2020-01-01", periods=270)
    data = {
        "SPY": [100.0] * len(dates),
        "AAA": [100.0] * 250 + [90.0] * 10 + [88.0, 86.0, 84.0, 82.0, 80.0, 79.0, 78.0, 77.0, 76.0, 75.0],
        "BBB": [100.0] * 250 + [92.0] * 10 + [91.0, 90.0, 89.0, 88.0, 87.0, 86.0, 85.0, 84.0, 83.0, 82.0],
        "CCC": [100.0] * 250 + [99.0] * 10 + [98.0, 98.0, 98.0, 98.0, 98.0, 98.0, 98.0, 98.0, 98.0, 98.0],
    }
    matrix = pd.DataFrame(data, index=dates)

    weights, candidates = _phoenix_candidates(matrix, dates[-1], top_n=2)

    assert list(weights.index) == ["AAA", "BBB"]
    assert round(float(weights.sum()), 10) == 0.8
    assert candidates[0]["selection_reason"] == "pit_close_dislocation_recovery_candidate"
    assert candidates[0]["return_20d_at_trough"] < 0


def test_generated_phoenix_evidence_artifact_contract() -> None:
    path = Path("outputs/research/phoenix_evidence/phoenix_crisis_recovery_2026-06-17.json")
    assert path.exists(), "Generate with scripts/research/build_phoenix_crisis_recovery_evidence.py --date 2026-06-17"
    payload = json.loads(path.read_text())

    assert payload["schema_version"] == "caerus_phoenix_crisis_recovery_evidence_v1"
    assert payload["governance_label"] == "RESEARCH_ONLY"
    assert payload["execution_impact"] == "NON_EXECUTIONAL"
    assert payload["production_impact"] == "none"
    assert payload["inputs"]["universe_method"] == "pit_universe"
    assert payload["inputs"]["membership_sha256"]
    assert payload["inputs"]["price_matrix_sha256"]
    assert payload["crisis_window_definitions"]["windows"]
    assert payload["returns_during_crisis_and_recovery_windows"]["event_count"] >= 1
    assert "caerus_polaris" in payload["overlap_correlation_vs_existing_sleeves"]["daily_return_correlation"]
    assert payload["liquidity"]["available"] is False
    assert "volume_source_missing_from_repo_local_pit_price_cache" in payload["liquidity"]["reason_codes"]
    assert payload["readiness_classification"]["classification"] in {
        "RESEARCH_ONLY_NOT_SHADOW_READY",
        "RESEARCH_READY_WITH_WARNINGS",
        "RESEARCH_READY_FOR_SHADOW_SPEC",
    }

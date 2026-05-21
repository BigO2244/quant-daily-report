from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from scripts.research.build_fr028_timing_surface import (
    PROVENANCE_CURRENT,
    PROVENANCE_PROPOSED,
    _load_returns,
    _load_snapshots,
    build_divergence_analysis,
    build_ranking_delta,
    build_semantics_chains,
    run_fr028_phase_a,
)


def _panel() -> pd.DataFrame:
    dates = pd.date_range("2026-01-02", periods=5, freq="B")
    closes = {
        "AAA": [100.0, 110.0, 99.0, 120.0, 132.0],
        "BBB": [100.0, 90.0, 99.0, 95.0, 90.0],
        "SPY": [100.0, 101.0, 102.0, 101.0, 103.0],
    }
    rows = []
    for ticker, values in closes.items():
        for date, close in zip(dates, values):
            rows.append({"date": date, "ticker": ticker, "open": close, "high": close, "low": close, "close": close, "volume": 1_000_000})
    return pd.DataFrame(rows)


def _snapshot(weights: dict[str, float]) -> dict:
    return {
        "strategy_name": "strategy",
        "target_weights": weights,
        "holdings": [{"ticker": ticker, "target_weight": weight} for ticker, weight in weights.items()],
    }


def _write_shadow(repo: Path) -> None:
    shadow = repo / "outputs" / "shadow_candidates"
    for date, weights in {
        "2026-01-05": {"AAA": 1.0},
        "2026-01-06": {"BBB": 1.0},
    }.items():
        dated = shadow / date
        dated.mkdir(parents=True, exist_ok=True)
        for slug in ("caerus_polaris", "caerus_orion", "caerus_lyra"):
            (dated / f"{slug}.json").write_text(json.dumps(_snapshot(weights)), encoding="utf-8")


def test_semantics_chains_separate_current_and_proposed(tmp_path: Path) -> None:
    price_path = tmp_path / "price.parquet"
    _panel().to_parquet(price_path, index=False)
    _write_shadow(tmp_path)
    snapshots = _load_snapshots(tmp_path / "outputs" / "shadow_candidates")
    returns = _load_returns(price_path, through_date=None)

    current, proposed = build_semantics_chains(snapshots=snapshots, returns=returns)

    assert current["nav_surface_type"] == PROVENANCE_CURRENT["nav_surface_type"]
    assert proposed["nav_surface_type"] == PROVENANCE_PROPOSED["nav_surface_type"]
    assert current["strategies"]["caerus_polaris"]["points"][0]["date"] == "2026-01-05"
    assert proposed["strategies"]["caerus_polaris"]["points"][0]["date"] == "2026-01-06"
    assert current["strategies"]["caerus_polaris"]["metrics"]["valid_days"] == 2
    assert proposed["strategies"]["caerus_polaris"]["metrics"]["valid_days"] == 2


def test_divergence_and_ranking_are_deterministic(tmp_path: Path) -> None:
    price_path = tmp_path / "price.parquet"
    _panel().to_parquet(price_path, index=False)
    _write_shadow(tmp_path)
    snapshots = _load_snapshots(tmp_path / "outputs" / "shadow_candidates")
    returns = _load_returns(price_path, through_date=None)
    current, proposed = build_semantics_chains(snapshots=snapshots, returns=returns)

    divergence = build_divergence_analysis(current, proposed)
    ranking = build_ranking_delta(current, proposed)

    assert divergence["strategies"]["caerus_polaris"]["deltas"]["cumulative_return_delta"] is not None
    assert ranking["current_ranking"] == ["caerus_polaris", "caerus_orion", "caerus_lyra"]
    assert ranking["proposed_ranking"] == ["caerus_polaris", "caerus_orion", "caerus_lyra"]


def test_run_fr028_phase_a_writes_only_research_surface(tmp_path: Path) -> None:
    price_path = tmp_path / "outputs" / "research" / "flow_detection_v1" / "price_panel.parquet"
    price_path.parent.mkdir(parents=True)
    _panel().to_parquet(price_path, index=False)
    _write_shadow(tmp_path)

    summary, written = run_fr028_phase_a(
        repo_root=tmp_path,
        price_cache_path=price_path,
        shadow_root=tmp_path / "outputs" / "shadow_candidates",
        output_root=tmp_path / "outputs" / "fr028_research_surface",
        through_date=None,
    )

    assert summary["surface_status"] == "RESEARCH_ONLY_PARALLEL_ANALYSIS"
    names = {path.name for path in written}
    assert "current_semantics_nav.json" in names
    assert "proposed_semantics_nav.json" in names
    assert "governance_impact_review.json" in names
    proposed = json.loads((tmp_path / "outputs" / "fr028_research_surface" / "2026-01-06" / "proposed_semantics_nav.json").read_text())
    assert proposed["confidence_classification"] == "RESEARCH_ONLY_NOT_GOVERNANCE_APPROVED"
    assert not (tmp_path / "outputs" / "shadow_candidates" / "performance" / "shadow_nav_series.csv").exists()

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scripts.research.build_fr028_timing_surface import run_fr028_phase_a
from scripts.research.review_fr028_phase_b import (
    PROVENANCE,
    build_long_horizon_review,
    build_ranking_stability,
    run_phase_b,
)


def _panel() -> pd.DataFrame:
    dates = pd.date_range("2026-01-02", periods=5, freq="B")
    rows = []
    for ticker, closes in {
        "AAA": [100, 110, 100, 120, 130],
        "BBB": [100, 95, 105, 100, 98],
        "SPY": [100, 101, 102, 101, 103],
    }.items():
        for date, close in zip(dates, closes):
            rows.append({"date": date, "ticker": ticker, "open": close, "high": close, "low": close, "close": close, "volume": 1_000_000})
    return pd.DataFrame(rows)


def _snapshot(weights: dict[str, float]) -> dict:
    return {"strategy_name": "strategy", "target_weights": weights}


def _write_inputs(repo: Path) -> Path:
    price_path = repo / "outputs" / "research" / "flow_detection_v1" / "price_panel.parquet"
    price_path.parent.mkdir(parents=True)
    _panel().to_parquet(price_path, index=False)
    shadow = repo / "outputs" / "shadow_candidates"
    for date, weights in {"2026-01-05": {"AAA": 1.0}, "2026-01-06": {"BBB": 1.0}}.items():
        dated = shadow / date
        dated.mkdir(parents=True, exist_ok=True)
        for slug in ("caerus_polaris", "caerus_orion", "caerus_lyra"):
            (dated / f"{slug}.json").write_text(json.dumps(_snapshot(weights)), encoding="utf-8")
    attr = repo / "outputs" / "attribution" / "2026-01-06"
    attr.mkdir(parents=True)
    (attr / "factor_exposure.json").write_text(json.dumps({"strategies": {}}), encoding="utf-8")
    (attr / "concentration_analysis.json").write_text(json.dumps({"strategies": {}}), encoding="utf-8")
    (attr / "factor_risk_flags.json").write_text(json.dumps({"strategies": {}}), encoding="utf-8")
    return price_path


def test_phase_b_builds_governance_only_outputs(tmp_path: Path) -> None:
    price_path = _write_inputs(tmp_path)
    run_fr028_phase_a(
        repo_root=tmp_path,
        price_cache_path=price_path,
        shadow_root=tmp_path / "outputs" / "shadow_candidates",
        output_root=tmp_path / "outputs" / "fr028_research_surface",
        through_date=None,
    )

    summary, written = run_phase_b(
        repo_root=tmp_path,
        surface_root=tmp_path / "outputs" / "fr028_research_surface",
        attribution_root=tmp_path / "outputs" / "attribution",
        as_of_date="2026-01-06",
    )

    assert summary["status"] == "RESEARCH_ONLY_GOVERNANCE_INTERPRETATION"
    assert summary["migration_recommendation"] == "DO_NOT_MIGRATE_YET_BUILD_LONGER_PARALLEL_HISTORY"
    names = {path.name for path in written}
    assert "long_horizon_timing_review.json" in names
    assert "promotion_governance_impact_review.json" in names
    assert "authoritative_surface_review.json" in names
    review = json.loads((tmp_path / "outputs" / "fr028_research_surface" / "2026-01-06" / "phase_b_governance_review" / "authoritative_surface_review.json").read_text())
    assert review["migration_status"] == "NOT_MIGRATED"
    assert review["governance_scope"] == "NO_RULE_CHANGE_NO_MIGRATION"


def test_phase_b_reviews_classify_limited_history() -> None:
    payloads = {
        "divergence": {
            "strategies": {
                slug: {"comparison_observation_count": 2, "timing_sensitivity_abs": 0.02}
                for slug in ("caerus_polaris", "caerus_orion", "caerus_lyra")
            }
        },
        "ranking": {
            "current_ranking": ["caerus_polaris", "caerus_orion", "caerus_lyra"],
            "proposed_ranking": ["caerus_polaris", "caerus_orion", "caerus_lyra"],
            "rank_changes": {
                slug: {"delta": 0}
                for slug in ("caerus_polaris", "caerus_orion", "caerus_lyra")
            },
        },
    }

    long_horizon = build_long_horizon_review(payloads)
    ranking = build_ranking_stability(payloads)

    assert long_horizon["nav_surface_type"] == PROVENANCE["nav_surface_type"]
    assert long_horizon["strategies"]["caerus_polaris"]["long_horizon_confidence"] == "INSUFFICIENT_HISTORY"
    assert ranking["ranking_stability"] == "STABLE"

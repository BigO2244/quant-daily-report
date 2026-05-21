from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from scripts.research.build_strategy_attribution import (
    _load_sector_map,
    _returns_matrix,
    build_factor_exposure,
    build_position_attribution,
    run_attribution,
)


def _panel() -> pd.DataFrame:
    dates = pd.date_range("2025-01-02", periods=280, freq="B")
    rows = []
    slopes = {"AAA": 0.002, "BBB": -0.001, "CCC": 0.001, "SPY": 0.0005}
    sectors = {"AAA": "Technology", "BBB": "Energy", "CCC": "Technology", "SPY": "Benchmark"}
    for ticker, slope in slopes.items():
        price = 100.0
        for idx, date in enumerate(dates):
            price *= 1.0 + slope
            rows.append(
                {
                    "date": date,
                    "ticker": ticker,
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "volume": 1_000_000 + idx,
                    "sector": sectors[ticker],
                }
            )
    return pd.DataFrame(rows)


def _snapshot(slug: str, weights: dict[str, float]) -> dict:
    return {
        "strategy_name": slug,
        "strategy_slug": slug,
        "trade_date": "2026-01-28",
        "target_weights": weights,
        "expected_turnover": 0.2,
        "rank_table": [
            {"ticker": ticker, "momentum_score": 1.0 - idx * 0.1, "momentum_rank": idx + 1, "is_selected": True}
            for idx, ticker in enumerate(weights)
        ],
        "performance_summary": {"transaction_cost_bps": 10.0},
    }


def test_sector_map_handles_leading_blank_line(tmp_path: Path) -> None:
    path = tmp_path / "universe.csv"
    path.write_text("\n" "ticker,sector\n" "AAA,Technology\n", encoding="utf-8")

    assert _load_sector_map(path) == {"AAA": "Technology"}


def test_position_attribution_contribution_math() -> None:
    panel = _panel()
    returns = _returns_matrix(panel)
    snapshots = {"caerus_polaris": _snapshot("caerus_polaris", {"AAA": 0.5, "BBB": 0.5})}

    payload = build_position_attribution(
        snapshots=snapshots,
        returns=returns,
        trade_date="2026-01-28",
        sector_map={"AAA": "Technology", "BBB": "Energy"},
        lookback_days=63,
    )

    one_day = payload["strategies"]["caerus_polaris"]["windows"]["1d"]
    total = sum(row["contribution"] for row in one_day["positions"])
    assert total == pytest.approx(one_day["portfolio_return"])
    assert one_day["top_contributors"][0]["ticker"] == "AAA"
    assert one_day["top_detractors"][0]["ticker"] == "BBB"


def test_factor_exposure_flags_sector_and_position_concentration() -> None:
    panel = _panel()
    returns = _returns_matrix(panel)
    prices = panel.pivot(index="date", columns="ticker", values="close").sort_index()
    snapshots = {"caerus_orion": _snapshot("caerus_orion", {"AAA": 0.8, "CCC": 0.2})}

    payload = build_factor_exposure(
        snapshots=snapshots,
        panel=panel,
        returns=returns,
        prices=prices,
        trade_date="2026-01-28",
        sector_map={"AAA": "Technology", "CCC": "Technology"},
        lookback_days=252,
    )

    strategy = payload["strategies"]["caerus_orion"]
    assert strategy["sector_exposure"]["max_sector_weight"] == pytest.approx(1.0)
    assert "sector_concentration" in strategy["hidden_factor_flags"]
    assert "position_concentration" in strategy["hidden_factor_flags"]


def test_run_attribution_writes_expected_artifacts(tmp_path: Path) -> None:
    repo = tmp_path
    price_path = repo / "outputs" / "research" / "flow_detection_v1" / "price_panel.parquet"
    price_path.parent.mkdir(parents=True)
    _panel().to_parquet(price_path, index=False)
    universe = repo / "data" / "universe.csv"
    universe.parent.mkdir()
    universe.write_text("\nticker,sector\nAAA,Technology\nBBB,Energy\nCCC,Technology\n", encoding="utf-8")
    shadow = repo / "outputs" / "shadow_candidates" / "2026-01-28"
    shadow.mkdir(parents=True)
    for slug, weights in {
        "caerus_polaris": {"AAA": 0.5, "BBB": 0.5},
        "caerus_orion": {"AAA": 0.8, "CCC": 0.2},
        "caerus_lyra": {"AAA": 0.6, "CCC": 0.4},
    }.items():
        (shadow / f"{slug}.json").write_text(json.dumps(_snapshot(slug, weights)), encoding="utf-8")
    perf = repo / "outputs" / "shadow_candidates" / "performance"
    perf.mkdir()
    pd.DataFrame(
        {
            "date": pd.date_range("2025-01-02", periods=280, freq="B"),
            "caerus_polaris": [1.0 + idx * 0.001 for idx in range(280)],
            "caerus_orion": [1.0 + idx * 0.002 for idx in range(280)],
            "caerus_lyra": [1.0 + idx * 0.0015 for idx in range(280)],
            "spy_benchmark": [1.0 + idx * 0.0005 for idx in range(280)],
        }
    ).to_csv(perf / "shadow_nav_series.csv", index=False)

    summary, written = run_attribution(
        repo_root=repo,
        trade_date="2026-01-28",
        price_cache_path=price_path,
        shadow_root=repo / "outputs" / "shadow_candidates",
        output_root=repo / "outputs" / "attribution",
        lookback_days=63,
    )

    assert summary["classification"] == "RESEARCH_GRADE_PHASE_1_PARTIAL"
    names = {path.name for path in written}
    assert "contribution_report.json" in names
    assert "factor_exposure.json" in names
    assert "regime_analysis.json" in names
    assert "nav_surface_registry.json" in names
    assert "exposure_summary.json" in names
    assert "regime_fragility_report.json" in names
    assert "track_b_governance_recommendations.json" in names
    assert (repo / "outputs" / "attribution" / "2026-01-28" / "attribution_summary.md").exists()
    assert (repo / "outputs" / "portfolio_history" / "2026-01-28" / "holdings_snapshot.json").exists()
    manifest = json.loads((repo / "outputs" / "portfolio_history" / "2026-01-28" / "manifest.json").read_text())
    assert manifest["validation"]["status"] == "OK"
    assert manifest["files"]["holdings_snapshot.json"]["sha256"]
    registry = json.loads((repo / "outputs" / "attribution" / "2026-01-28" / "nav_surface_registry.json").read_text())
    assert set(registry["surfaces"]) == {"research_backtest_nav", "operational_shadow_nav", "live_broker_paper_nav"}
    governance = json.loads((repo / "outputs" / "attribution" / "2026-01-28" / "track_b_governance_recommendations.json").read_text())
    fr_028 = next(item for item in governance["recommendations"] if item["fr_id"] == "FR-028")
    assert fr_028["track"] == "B"
    assert fr_028["friday_governance_required"] is True

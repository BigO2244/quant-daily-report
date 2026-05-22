import json
from pathlib import Path

import pytest

from scripts.research.build_research_clarity_wave import (
    ImmutableArtifactError,
    build_research_clarity_wave,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _fixture_repo(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    source = repo / "outputs" / "shadow_candidates" / "2026-05-22"
    output = repo / "outputs" / "research_clarity" / "2026-05-22"
    (repo / "data").mkdir(parents=True)
    (repo / "data" / "universe.csv").write_text(
        "ticker,sector\nAAPL,Information Technology\nMSFT,Information Technology\nJPM,Financials\nSPY,Benchmark\n",
        encoding="utf-8",
    )
    strategy_payloads = {
        "caerus_polaris": {
            "strategy_name": "Caerus Polaris",
            "trade_date": "2026-05-22",
            "holdings": [
                {"ticker": "AAPL", "target_weight": 0.4, "momentum_score": 1.8, "momentum_rank": 1},
                {"ticker": "MSFT", "target_weight": 0.3, "momentum_score": 1.5, "momentum_rank": 2},
                {"ticker": "JPM", "target_weight": 0.2, "momentum_score": 0.2, "momentum_rank": 3},
            ],
            "expected_turnover": 0.15,
        },
        "caerus_orion": {
            "strategy_name": "Caerus Orion",
            "trade_date": "2026-05-22",
            "holdings": [
                {"ticker": "AAPL", "target_weight": 0.5, "momentum_score": 2.1, "momentum_rank": 1},
                {"ticker": "MSFT", "target_weight": 0.5, "momentum_score": 1.7, "momentum_rank": 2},
            ],
            "expected_turnover": 0.05,
        },
        "caerus_lyra": {
            "strategy_name": "Caerus Lyra",
            "trade_date": "2026-05-22",
            "holdings": [
                {"ticker": "AAPL", "target_weight": 0.34, "momentum_score": 2.0, "momentum_rank": 1},
                {"ticker": "MSFT", "target_weight": 0.33, "momentum_score": 1.6, "momentum_rank": 2},
                {"ticker": "JPM", "target_weight": 0.33, "momentum_score": 0.5, "momentum_rank": 3},
            ],
            "expected_turnover": 0.12,
        },
    }
    for strategy_id, payload in strategy_payloads.items():
        _write_json(source / f"{strategy_id}.json", payload)
    _write_json(
        source / "comparison.json",
        {
            "trade_date": "2026-05-22",
            "benchmark_symbol": "SPY",
            "shadow_methodology": "model_portfolio",
            "regime": {"risk": "risk_on", "volatility": "calm", "trend": "trending", "breadth": "broad"},
            "strategies": strategy_payloads,
        },
    )
    _write_json(
        source / "shadow_performance.json",
        {
            "trade_date": "2026-05-22",
            "previous_trade_date": "2026-05-21",
            "status": "OK",
            "return_convention": "weights_as_of_t",
            "strategies": {
                "caerus_polaris": {"daily_return": 0.01, "nav": 1.01, "weights_count": 3},
                "caerus_orion": {"daily_return": 0.02, "nav": 1.02, "weights_count": 2},
                "caerus_lyra": {"daily_return": 0.015, "nav": 1.015, "weights_count": 3},
                "spy_benchmark": {"daily_return": 0.005, "nav": 1.005, "weights_count": 1},
            },
        },
    )
    return repo, source, output


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_research_clarity_wave_writes_expected_additive_artifacts(tmp_path):
    repo, source, output = _fixture_repo(tmp_path)

    result = build_research_clarity_wave(
        repo_root=repo,
        trade_date="2026-05-22",
        source_dir=source,
        output_dir=output,
    )

    expected = {
        "nav_surface_registry.json",
        "surface_metadata.json",
        "holdings_snapshot.json",
        "weights_snapshot.json",
        "exposures_snapshot.json",
        "rebalance_delta.json",
        "exposure_summary.json",
        "factor_risk_flags.json",
        "concentration_monitor.json",
        "exposure_drift_summary.json",
        "regime_performance_breakdown.json",
        "regime_fragility_report.json",
        "regime_exposure_matrix.json",
        "attribution_by_regime.json",
        "research_clarity_summary.md",
        "manifest.json",
    }
    assert set(result["artifacts"]) == expected
    assert all((output / name).exists() for name in expected)

    registry = _read_json(output / "nav_surface_registry.json")
    assert registry["accounting_semantics_changed"] is False
    assert registry["execution_behavior_changed"] is False
    assert registry["historical_chains_rewritten"] is False
    assert registry["surfaces"]["OPERATIONAL_SHADOW_NAV"]["confidence"] == "LOW"

    holdings = _read_json(output / "holdings_snapshot.json")
    assert holdings["strategies"]["caerus_orion"]["immutable_snapshot"] is True
    assert holdings["strategies"]["caerus_orion"]["holdings_count"] == 2

    exposures = _read_json(output / "exposures_snapshot.json")
    assert exposures["strategies"]["caerus_orion"]["top3_concentration"] == 1.0
    assert exposures["strategies"]["caerus_orion"]["sector_exposure"]["Information Technology"] == 1.0

    flags = _read_json(output / "factor_risk_flags.json")
    assert any(flag["flag"] == "POSITION_CONCENTRATION" for flag in flags["flags"])

    summary = (output / "research_clarity_summary.md").read_text(encoding="utf-8")
    assert "No accounting semantics" in summary
    assert "FR-028 timing semantics remain unresolved" in summary


def test_research_clarity_wave_is_deterministic_and_immutable(tmp_path):
    repo, source, output = _fixture_repo(tmp_path)

    first = build_research_clarity_wave(repo, "2026-05-22", source, output)
    manifest_first = (output / "manifest.json").read_text(encoding="utf-8")
    second = build_research_clarity_wave(repo, "2026-05-22", source, output)
    manifest_second = (output / "manifest.json").read_text(encoding="utf-8")

    assert first == second
    assert manifest_first == manifest_second

    (output / "exposure_summary.json").write_text('{"mutated": true}\n', encoding="utf-8")
    with pytest.raises(ImmutableArtifactError):
        build_research_clarity_wave(repo, "2026-05-22", source, output)


def test_research_clarity_wave_preserves_source_artifacts(tmp_path):
    repo, source, output = _fixture_repo(tmp_path)
    before = {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(source.glob("*.json"))
    }

    build_research_clarity_wave(repo, "2026-05-22", source, output)

    after = {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(source.glob("*.json"))
    }
    assert after == before

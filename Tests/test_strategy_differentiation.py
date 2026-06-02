from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from research.strategy_differentiation import build_strategy_differentiation


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _write_comparison(root: Path, trade_date: str, *, overlap: bool) -> None:
    if overlap:
        lyra = orion = [{"ticker": f"A{i}", "target_weight": 0.2} for i in range(5)]
        polaris = [{"ticker": f"A{i}", "target_weight": 0.1} for i in range(10)]
    else:
        lyra = [{"ticker": f"L{i}", "target_weight": 0.2} for i in range(5)]
        orion = [{"ticker": f"O{i}", "target_weight": 0.2} for i in range(5)]
        polaris = [{"ticker": f"P{i}", "target_weight": 0.1} for i in range(10)]
    _write_json(
        root / "outputs" / "shadow_candidates" / trade_date / "comparison.json",
        {
            "trade_date": trade_date,
            "strategies": {
                "caerus_lyra": {"holdings": lyra},
                "caerus_orion": {"holdings": orion},
                "caerus_polaris": {"holdings": polaris},
            },
        },
    )


def _write_nav(root: Path, *, correlated: bool) -> None:
    path = root / "outputs" / "shadow_candidates" / "performance" / "shadow_nav_series.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, day in enumerate(pd.date_range("2026-01-01", periods=70, freq="B")):
        if correlated:
            lyra = orion = polaris = 1.0 + i * 0.001
        else:
            lyra = 1.0 + i * 0.002
            orion = 1.2 - i * 0.001
            polaris = 1.0 + (0.002 if i % 2 == 0 else -0.001) * i / 10
        rows.append({"date": day.date().isoformat(), "caerus_lyra": lyra, "caerus_orion": orion, "caerus_polaris": polaris, "spy_benchmark": 1.0 + i * 0.0001})
    pd.DataFrame(rows).to_csv(path, index=False)


def _write_factor_and_contrib(root: Path, trade_date: str, *, shared_contributors: bool = False) -> None:
    _write_json(
        root / "outputs" / "attribution" / trade_date / "factor_exposure.json",
        {
            "strategies": {
                "caerus_lyra": {"market_beta": 0.5, "sector_exposure": {"weights": {"Utilities": 1.0}}},
                "caerus_orion": {"market_beta": 1.5, "sector_exposure": {"weights": {"Tech": 1.0}}},
                "caerus_polaris": {"market_beta": 1.6, "sector_exposure": {"weights": {"Tech": 0.9, "Industrials": 0.1}}},
            }
        },
    )
    positions = []
    symbols_by_strategy = (
        {
            "caerus_lyra": ["AAA", "BBB"],
            "caerus_orion": ["AAA", "BBB"],
            "caerus_polaris": ["AAA", "BBB"],
        }
        if shared_contributors
        else {"caerus_lyra": ["L1", "L2"], "caerus_orion": ["O1", "O2"], "caerus_polaris": ["P1", "P2"]}
    )
    for strategy, symbols in symbols_by_strategy.items():
        positions.extend(
            [
                {"strategy": strategy, "symbol": symbols[0], "pnl_contribution": 0.02},
                {"strategy": strategy, "symbol": symbols[1], "pnl_contribution": -0.01},
            ]
        )
    _write_json(root / "outputs" / "attribution" / trade_date / "position_attribution.json", {"positions": positions})


def _write_contribution_report(root: Path, trade_date: str) -> None:
    strategies = {}
    for strategy in ("caerus_lyra", "caerus_orion", "caerus_polaris"):
        strategies[strategy] = {
            "windows": {
                "1d": {
                    "positions": [
                        {"ticker": "AAA", "contribution": 0.03},
                        {"ticker": "BBB", "contribution": -0.02},
                    ]
                }
            }
        }
    _write_json(
        root / "outputs" / "attribution" / trade_date / "contribution_report.json",
        {"trade_date": trade_date, "strategies": strategies},
    )


def test_high_overlap_high_correlation_is_weak(tmp_path):
    trade_date = "2026-04-03"
    _write_comparison(tmp_path, trade_date, overlap=True)
    _write_nav(tmp_path, correlated=True)

    payload = build_strategy_differentiation(trade_date=trade_date, repo_root=tmp_path)

    lyra_orion = payload["pairs"][0]
    assert lyra_orion["left_strategy"] == "caerus_lyra"
    assert lyra_orion["right_strategy"] == "caerus_orion"
    assert lyra_orion["differentiation_readiness_flag"] == "WEAK"
    assert "high_overlap_high_correlation" in lyra_orion["reason_codes"]


def test_low_overlap_low_correlation_is_stronger_with_missing_factor_graceful(tmp_path):
    trade_date = "2026-04-03"
    _write_comparison(tmp_path, trade_date, overlap=False)
    _write_nav(tmp_path, correlated=False)

    payload = build_strategy_differentiation(trade_date=trade_date, repo_root=tmp_path)

    assert [f"{row['left_strategy']}:{row['right_strategy']}" for row in payload["pairs"]] == [
        "caerus_lyra:caerus_orion",
        "caerus_lyra:caerus_polaris",
        "caerus_orion:caerus_polaris",
    ]
    assert payload["pairs"][0]["differentiation_readiness_flag"] in {"READY", "WATCH"}
    assert "factor_exposure_missing" in payload["reason_codes"]


def test_strategy_differentiation_uses_date_bounded_shadow_inputs(tmp_path):
    _write_comparison(tmp_path, "2026-04-03", overlap=False)
    _write_comparison(tmp_path, "2026-12-31", overlap=True)
    _write_nav(tmp_path, correlated=False)
    _write_factor_and_contrib(tmp_path, "2026-04-03")

    payload = build_strategy_differentiation(trade_date="2026-04-03", repo_root=tmp_path)

    assert payload["pairs"][0]["holdings_overlap_percentage"] == 0.0
    assert payload["available"] is True


def test_strategy_differentiation_uses_exact_factor_and_position_contributions(tmp_path):
    trade_date = "2026-04-03"
    _write_comparison(tmp_path, trade_date, overlap=True)
    _write_nav(tmp_path, correlated=True)
    _write_factor_and_contrib(tmp_path, trade_date, shared_contributors=True)

    payload = build_strategy_differentiation(trade_date=trade_date, repo_root=tmp_path)

    assert payload["factor_exposure_available"] is True
    assert payload["position_contributions_available"] is True
    assert "factor_exposure_missing" not in payload["reason_codes"]
    assert "position_contributions_missing" not in payload["reason_codes"]
    assert payload["pairs"][0]["common_top_contributors"] == ["AAA"]
    assert payload["pairs"][0]["common_top_detractors"] == ["BBB"]


def test_strategy_differentiation_uses_date_bounded_factor_and_contribution_fallback(tmp_path):
    _write_comparison(tmp_path, "2026-04-10", overlap=False)
    _write_nav(tmp_path, correlated=False)
    _write_factor_and_contrib(tmp_path, "2026-04-03")

    payload = build_strategy_differentiation(trade_date="2026-04-10", repo_root=tmp_path)

    assert payload["factor_exposure_available"] is True
    assert payload["position_contributions_available"] is True
    assert "factor_exposure_missing" not in payload["reason_codes"]
    assert "position_contributions_missing" not in payload["reason_codes"]
    assert "factor_exposure_date_differs_from_target" in payload["reason_codes"]
    assert "position_contribution_date_differs_from_target" in payload["reason_codes"]


def test_strategy_differentiation_handles_malformed_factor_exposure(tmp_path):
    trade_date = "2026-04-03"
    _write_comparison(tmp_path, trade_date, overlap=False)
    _write_nav(tmp_path, correlated=False)
    _write_json(tmp_path / "outputs" / "attribution" / trade_date / "position_attribution.json", {"positions": []})
    bad_path = tmp_path / "outputs" / "attribution" / trade_date / "factor_exposure.json"
    bad_path.parent.mkdir(parents=True, exist_ok=True)
    bad_path.write_text("{bad json", encoding="utf-8")

    payload = build_strategy_differentiation(trade_date=trade_date, repo_root=tmp_path)

    assert payload["factor_exposure_available"] is False
    assert "factor_exposure_parser_error" in payload["reason_codes"]
    assert "factor_exposure_missing" in payload["reason_codes"]


def test_strategy_differentiation_parses_contribution_report(tmp_path):
    trade_date = "2026-04-03"
    _write_comparison(tmp_path, trade_date, overlap=True)
    _write_nav(tmp_path, correlated=True)
    _write_json(
        tmp_path / "outputs" / "attribution" / trade_date / "factor_exposure.json",
        {
            "strategies": {
                "caerus_lyra": {"market_beta": 1.0},
                "caerus_orion": {"market_beta": 1.1},
                "caerus_polaris": {"market_beta": 0.9},
            }
        },
    )
    _write_contribution_report(tmp_path, trade_date)

    payload = build_strategy_differentiation(trade_date=trade_date, repo_root=tmp_path)

    assert payload["position_contributions_available"] is True
    assert "position_contribution_source_contribution_report" in payload["reason_codes"]
    assert payload["pairs"][0]["common_top_contributors"] == ["AAA"]
    assert payload["pairs"][0]["common_top_detractors"] == ["BBB"]


def test_strategy_differentiation_empty_contribution_file_is_explicit(tmp_path):
    trade_date = "2026-04-03"
    _write_comparison(tmp_path, trade_date, overlap=False)
    _write_nav(tmp_path, correlated=False)
    _write_factor_and_contrib(tmp_path, trade_date)
    _write_json(tmp_path / "outputs" / "attribution" / trade_date / "position_attribution.json", {"positions": []})

    payload = build_strategy_differentiation(trade_date=trade_date, repo_root=tmp_path)

    assert payload["position_contributions_available"] is False
    assert "position_contributions_empty" in payload["reason_codes"]
    assert "position_contributions_missing" in payload["reason_codes"]


def test_empty_strategy_inputs_fail_closed(tmp_path):
    payload = build_strategy_differentiation(trade_date="2026-04-03", repo_root=tmp_path)

    assert payload["available"] is False
    assert "shadow_comparison_missing" in payload["reason_codes"]

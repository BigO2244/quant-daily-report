from __future__ import annotations

import json
from pathlib import Path

from research.shadow_tracking.evidence_chain import (
    build_alpha_evidence_chain_payload,
    write_alpha_evidence_chain_artifacts,
)


REQUIRED_SLUGS = (
    "caerus_polaris",
    "caerus_polaris_alpha",
    "caerus_orion",
    "caerus_orion_alpha",
    "caerus_lyra",
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _seed_complete_shadow_chain(root: Path, trade_date: str = "2026-06-24") -> Path:
    output_root = root / "outputs" / "shadow_candidates"
    dated = output_root / trade_date
    dated.mkdir(parents=True, exist_ok=True)
    strategies = {}
    comparison_strategies = {}
    nav_values = {}
    for index, slug in enumerate(REQUIRED_SLUGS, start=1):
        nav = 1.0 + index / 100.0
        nav_values[slug] = nav
        concentration = {
            "holdings_count": 3,
            "max_weight": 0.25,
            "top3_concentration": 0.75,
            "top5_concentration": 0.75,
            "gross_exposure": 0.75,
            "cash_weight": 0.25,
            "hhi": 0.333333,
            "effective_n": 3.0,
        }
        holdings = [
            {"ticker": "AAA", "target_weight": 0.25, "momentum_rank": 1},
            {"ticker": "BBB", "target_weight": 0.25, "momentum_rank": 2},
            {"ticker": "CCC", "target_weight": 0.25, "momentum_rank": 3},
        ]
        strategies[slug] = {
            "strategy_name": slug,
            "status": "OK",
            "data_status": "OK",
            "daily_return": 0.01,
            "nav": nav,
            "cumulative_return": nav - 1.0,
            "max_drawdown": -0.01,
            "avg_turnover": 0.05,
            "avg_top_3_concentration": 0.75,
            "avg_cash_weight": 0.25,
            "avg_hhi": 0.333333,
            "avg_effective_n": 3.0,
            "rolling_count_of_valid_days": 1,
        }
        strategy_artifact = {
            "strategy_name": slug,
            "strategy_slug": slug,
            "trade_date": trade_date,
            "expected_turnover": 0.05,
            "holdings": holdings,
            "rank_table": [{"ticker": item["ticker"], "momentum_rank": item["momentum_rank"]} for item in holdings],
            "weight_concentration": concentration,
        }
        _write_json(dated / f"{slug}.json", strategy_artifact)
        comparison_strategies[slug] = {
            "strategy_name": slug,
            "expected_turnover": 0.05,
            "holdings": holdings,
            "weight_concentration": concentration,
        }
    _write_json(dated / "shadow_evaluation.json", {"trade_date": trade_date, "benchmark_symbol": "SPY", "strategies": strategies})
    _write_json(dated / "comparison.json", {"trade_date": trade_date, "strategies": comparison_strategies})
    _write_json(dated / "shadow_performance.json", {"trade_date": trade_date, "status": "OK", "data_status": "OK"})
    _write_json(dated / "delta.json", {"trade_date": trade_date, "status": "OK"})
    latest = output_root / "latest"
    _write_json(latest / "shadow_evaluation.json", {"trade_date": trade_date, "strategies": strategies})
    _write_json(latest / "comparison.json", {"trade_date": trade_date, "strategies": comparison_strategies})
    performance = output_root / "performance"
    performance.mkdir(parents=True, exist_ok=True)
    header = "date," + ",".join(REQUIRED_SLUGS) + ",spy_benchmark\n"
    row = trade_date + "," + ",".join(str(nav_values[slug]) for slug in REQUIRED_SLUGS) + ",1.0\n"
    (performance / "shadow_nav_series.csv").write_text(header + row, encoding="utf-8")
    return output_root


def test_alpha_evidence_chain_complete_artifacts_are_collectible(tmp_path: Path) -> None:
    output_root = _seed_complete_shadow_chain(tmp_path)

    payload = build_alpha_evidence_chain_payload(output_root=output_root, trade_date="2026-06-24")

    assert payload["status"] == "OK"
    assert payload["can_start_20_60_day_evidence_collection"] is True
    assert payload["reporting_status"] == "CURRENT"
    assert not payload["blocked_reasons"]
    assert {row["strategy_id"] for row in payload["strategies"]} == set(REQUIRED_SLUGS)
    assert all(row["status"] == "PASS" for row in payload["strategies"])


def test_alpha_evidence_chain_missing_shadow_artifacts_blocks_collection(tmp_path: Path) -> None:
    payload = build_alpha_evidence_chain_payload(
        output_root=tmp_path / "outputs" / "shadow_candidates",
        trade_date="2026-06-24",
    )

    assert payload["status"] == "BLOCKED"
    assert payload["can_start_20_60_day_evidence_collection"] is False
    assert payload["blocked_reasons"]


def test_alpha_evidence_chain_writer_creates_json_and_markdown(tmp_path: Path) -> None:
    output_root = _seed_complete_shadow_chain(tmp_path)

    payload = write_alpha_evidence_chain_artifacts(output_root=output_root, trade_date="2026-06-24")

    dated = output_root / "2026-06-24"
    assert payload["status"] == "OK"
    assert (dated / "alpha_evidence_chain.json").exists()
    assert (dated / "alpha_evidence_chain.md").exists()
    assert "Alpha Evidence Chain" in (dated / "alpha_evidence_chain.md").read_text(encoding="utf-8")

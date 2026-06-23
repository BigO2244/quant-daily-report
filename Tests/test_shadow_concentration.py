from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from research.shadow_concentration import (
    VARIANT_DEFINITIONS,
    build_shadow_concentration_artifact,
    compare_current_and_shadow,
    concentration_metrics,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _current_payload(*, slug: str, name: str, source_variant: str, top_count: int, total_ranked: int = 10) -> dict:
    tickers = [f"T{i}" for i in range(1, total_ranked + 1)]
    selected = tickers[:top_count]
    weight = round(1.0 / top_count, 10)
    return {
        "strategy_name": name,
        "strategy_slug": slug,
        "source_variant": source_variant,
        "trade_date": "2026-06-08",
        "effective_trade_date": "2026-06-08",
        "holdings": [
            {
                "ticker": ticker,
                "target_weight": weight,
                "momentum_rank": float(index + 1),
                "momentum_score": float(100 - index),
            }
            for index, ticker in enumerate(selected)
        ],
        "target_weights": {ticker: weight for ticker in selected},
        "rank_table": [
            {
                "ticker": ticker,
                "momentum_rank": float(index + 1),
                "momentum_score": float(100 - index),
                "is_selected": ticker in selected,
            }
            for index, ticker in enumerate(tickers)
        ],
        "expected_turnover": 0.0,
        "weight_concentration": {
            "holdings_count": top_count,
            "max_weight": weight,
            "top3_concentration": round(min(3, top_count) * weight, 10),
        },
    }


def _write_shadow_inputs(root: Path) -> None:
    dated = root / "2026-06-08"
    _write_json(
        dated / "caerus_polaris.json",
        _current_payload(
            slug="caerus_polaris",
            name="Caerus Polaris",
            source_variant="baseline_top10_daily",
            top_count=10,
            total_ranked=12,
        ),
    )
    _write_json(
        dated / "caerus_orion.json",
        _current_payload(
            slug="caerus_orion",
            name="Caerus Orion",
            source_variant="h2_rank_decay_exit_h6_top5",
            top_count=5,
            total_ranked=10,
        ),
    )


def _write_price_panel(path: Path) -> None:
    rows = []
    returns = {
        "T1": 0.10,
        "T2": 0.08,
        "T3": 0.06,
        "T4": 0.04,
        "T5": -0.02,
        "T6": -0.01,
        "T7": 0.00,
        "T8": 0.01,
        "T9": 0.02,
        "T10": 0.03,
        "T11": 0.04,
        "T12": 0.05,
    }
    for ticker, daily_return in returns.items():
        rows.append({"date": pd.Timestamp("2026-06-08"), "ticker": ticker, "close": 100.0})
        rows.append({"date": pd.Timestamp("2026-06-09"), "ticker": ticker, "close": 100.0 * (1.0 + daily_return)})
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, index=False)


def test_variant_definitions_are_shadow_only_and_avoid_score_squared() -> None:
    definitions = {definition.variant_name: definition for definition in VARIANT_DEFINITIONS}

    assert definitions["polaris_concentrated_shadow"].top_n == 4
    assert definitions["polaris_concentrated_shadow"].max_position_weight == 0.20
    assert definitions["polaris_concentrated_shadow"].weighting_method == "equal"
    assert definitions["orion_concentrated_shadow"].top_n == 3
    assert definitions["orion_concentrated_shadow"].max_position_weight == 0.25
    assert definitions["orion_concentrated_shadow"].weighting_method == "equal"
    assert all(definition.deployment_status == "RESEARCH_SHADOW_ONLY" for definition in VARIANT_DEFINITIONS)
    assert all(definition.capital_impact == "NONE" for definition in VARIANT_DEFINITIONS)
    assert all(definition.weighting_method != "score_squared" for definition in VARIANT_DEFINITIONS)


def test_concentration_metrics_use_deployed_effective_n_and_cash() -> None:
    metrics = concentration_metrics({"A": 0.20, "B": 0.20, "C": 0.20, "D": 0.20})

    assert metrics["gross_exposure"] == 0.8
    assert metrics["cash_weight"] == 0.2
    assert metrics["hhi"] == 0.25
    assert metrics["effective_n"] == 4.0
    assert metrics["capital_hhi"] == 0.16


def test_builder_enforces_top_n_caps_cash_overlap_and_next_day_return(tmp_path: Path) -> None:
    shadow_root = tmp_path / "shadow_candidates"
    output_root = tmp_path / "outputs" / "research" / "shadow_concentration"
    price_panel = tmp_path / "price_panel.parquet"
    _write_shadow_inputs(shadow_root)
    _write_price_panel(price_panel)

    payload = build_shadow_concentration_artifact(
        trade_date="2026-06-08",
        shadow_candidate_root=shadow_root,
        price_panel_path=price_panel,
        output_root=output_root,
    )

    polaris = payload["shadow_variants"]["polaris_concentrated_shadow"]
    orion = payload["shadow_variants"]["orion_concentrated_shadow"]
    assert [item["ticker"] for item in polaris["holdings"]] == ["T1", "T2", "T3", "T4"]
    assert [item["ticker"] for item in orion["holdings"]] == ["T1", "T2", "T3"]
    assert max(polaris["target_weights"].values()) == 0.20
    assert max(orion["target_weights"].values()) == 0.25
    assert polaris["cash_weight"] == 0.20
    assert orion["cash_weight"] == 0.25
    assert polaris["score_squared_used"] is False
    assert orion["score_squared_used"] is False

    assert payload["comparisons"]["caerus_polaris"]["overlap_weight"] == 0.4
    assert payload["comparisons"]["caerus_orion"]["overlap_weight"] == 0.6
    assert payload["performance"]["next_day_return_context"]["status"] == "OK"
    assert payload["performance"]["portfolios"]["polaris_concentrated_shadow"]["next_day_return"] == 0.056
    assert payload["performance"]["portfolios"]["orion_concentrated_shadow"]["next_day_return"] == 0.06
    assert payload["execution_impact"] == "NON_EXECUTIONAL"
    assert payload["runtime_behavior_changed"] is False


def test_compare_current_and_shadow_reports_diffs() -> None:
    current = {
        "portfolio_name": "current",
        "sleeve": "caerus_polaris",
        "trade_date": "2026-06-08",
        "target_weights": {"A": 0.1, "B": 0.1, "C": 0.1},
        "concentration": concentration_metrics({"A": 0.1, "B": 0.1, "C": 0.1}),
    }
    shadow = {
        "variant_name": "shadow",
        "target_weights": {"A": 0.2, "B": 0.2},
        "concentration": concentration_metrics({"A": 0.2, "B": 0.2}),
    }

    comparison = compare_current_and_shadow(current=current, shadow=shadow)

    assert comparison["overlap_names"] == ["A", "B"]
    assert comparison["current_only"] == ["C"]
    assert comparison["overlap_weight"] == 0.2
    assert comparison["transition_turnover_vs_current"] == 0.15


def test_artifact_generation_is_deterministic(tmp_path: Path) -> None:
    shadow_root = tmp_path / "shadow_candidates"
    output_root = tmp_path / "outputs" / "research" / "shadow_concentration"
    price_panel = tmp_path / "price_panel.parquet"
    _write_shadow_inputs(shadow_root)
    _write_price_panel(price_panel)

    first = build_shadow_concentration_artifact(
        trade_date="2026-06-08",
        shadow_candidate_root=shadow_root,
        price_panel_path=price_panel,
        output_root=output_root,
    )
    first_bytes = (output_root / "2026-06-08" / "shadow_concentration.json").read_bytes()
    second = build_shadow_concentration_artifact(
        trade_date="2026-06-08",
        shadow_candidate_root=shadow_root,
        price_panel_path=price_panel,
        output_root=output_root,
    )
    second_bytes = (output_root / "2026-06-08" / "shadow_concentration.json").read_bytes()

    assert first["artifact_digest"] == second["artifact_digest"]
    assert first_bytes == second_bytes


def test_builder_does_not_import_execution_or_allocator_surfaces() -> None:
    module_text = Path("research/shadow_concentration.py").read_text(encoding="utf-8")
    script_text = Path("scripts/research/build_shadow_concentration_artifact.py").read_text(encoding="utf-8")
    combined = module_text + "\n" + script_text

    forbidden_tokens = [
        "alpaca",
        "submit_order",
        "run_precomputed",
        "paper_broker",
        "live_pilot",
        "load_strategy_registry",
        "build_target_snapshot",
        "run_backtest",
        "ensure_price_panel",
        "core.allocation",
        "core.portfolio",
    ]
    for token in forbidden_tokens:
        assert token not in combined

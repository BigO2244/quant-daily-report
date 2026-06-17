from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scripts.research.build_phoenix_phase_b_risk_shaping import (
    RiskVariant,
    _apply_stop_loss,
    _candidate_frame,
    _staged_exposure,
    default_variants,
)


def test_default_variants_cover_required_risk_shaping_dimensions() -> None:
    variant_ids = {variant.variant_id for variant in default_variants()}

    assert "stricter_crisis_entry" in variant_ids
    assert "liquidity_capacity_filter" in variant_ids
    assert "volatility_cap_70" in variant_ids
    assert "staged_entry_0_5_10" in variant_ids
    assert "recovery_confirmation_5d" in variant_ids
    assert "stop_loss_10pct" in variant_ids
    assert "concentrated_top5_50gross" in variant_ids
    assert "broader_top15_75gross" in variant_ids


def test_candidate_frame_applies_stricter_filters_and_vol_cap() -> None:
    dates = pd.bdate_range("2020-01-01", periods=270)
    matrix = pd.DataFrame(
        {
            "SPY": [100.0] * len(dates),
            "AAA": [100.0] * 250 + [90.0] * 10 + [88.0, 86.0, 84.0, 82.0, 80.0, 79.0, 78.0, 77.0, 76.0, 75.0],
            "BBB": [100.0] * 250 + [97.0] * 10 + [96.0, 95.0, 95.0, 95.0, 95.0, 95.0, 95.0, 95.0, 95.0, 95.0],
        },
        index=dates,
    )
    variant = RiskVariant(
        variant_id="strict_test",
        description="test",
        min_return_20d=-0.12,
        min_return_5d=-0.04,
        max_vol_20d_ann=2.0,
    )

    candidates = _candidate_frame(matrix, dates[-1], variant)

    assert list(candidates.index) == ["AAA"]
    assert candidates.loc["AAA", "return_20d"] < -0.12


def test_staged_exposure_and_stop_loss_reduce_path_risk() -> None:
    dates = pd.bdate_range("2020-01-01", periods=8)
    variant = RiskVariant(
        variant_id="stage_test",
        description="test",
        stage_lags_and_weights=((0, 0.4), (2, 0.3), (4, 0.3)),
        stop_loss=-0.10,
    )

    exposure = _staged_exposure(dates, dates[0], variant)
    assert exposure.iloc[0] == 0.4
    assert exposure.iloc[2] == 0.7
    assert exposure.iloc[4] == 1.0

    returns = pd.Series([-0.02, -0.03, -0.06, 0.05, 0.05], index=dates[:5])
    stopped, stop_date = _apply_stop_loss(returns, -0.10)
    assert stop_date == str(dates[2].date())
    assert stopped.loc[dates[3]] == 0.0


def test_generated_phase_b_risk_shaping_artifact_contract() -> None:
    path = Path("outputs/research/phoenix_evidence/phoenix_phase_b_risk_shaping_2026-06-17.json")
    assert path.exists(), "Generate with scripts/research/build_phoenix_phase_b_risk_shaping.py --date 2026-06-17"
    payload = json.loads(path.read_text())

    assert payload["schema_version"] == "caerus_phoenix_phase_b_risk_shaping_v1"
    assert payload["governance_label"] == "RESEARCH_ONLY"
    assert payload["execution_impact"] == "NON_EXECUTIONAL"
    assert payload["production_impact"] == "none"
    assert payload["inputs"]["universe_method"] == "pit_universe"
    assert payload["readiness_conclusion"]["classification"] == "PHOENIX_RISK_SHAPING_CANDIDATE_PENDING_LIQUIDITY"
    assert payload["readiness_conclusion"]["is_shadow_eligible"] is False
    assert payload["readiness_conclusion"]["shadow_readiness_work_justified"] is True
    assert payload["best_research_candidate"]["variant_id"] in {"stop_loss_10pct", "concentrated_top5_50gross"}
    assert payload["liquidity_capacity"]["decision_grade"] is False
    assert "pit_liquidity_source_missing" in payload["liquidity_capacity"]["reason_codes"]

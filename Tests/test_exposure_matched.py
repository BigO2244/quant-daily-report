from __future__ import annotations

import pandas as pd

from research.exposure_matched import (
    attribution_decomposition,
    daily_gross_exposure,
    exposure_match_weights,
    exposure_metrics,
)


def _weights(rows):
    return pd.DataFrame(rows)


def test_exposure_match_scales_candidate_to_baseline_gross() -> None:
    baseline = _weights([
        {"trade_date": "2020-01-02", "security_id": "A", "target_weight": 0.25},
        {"trade_date": "2020-01-02", "security_id": "B", "target_weight": 0.25},
    ])
    candidate = _weights([
        {"trade_date": "2020-01-02", "security_id": "A", "target_weight": 0.75},
        {"trade_date": "2020-01-02", "security_id": "B", "target_weight": 0.25},
    ])
    matched = exposure_match_weights(candidate, baseline)
    assert daily_gross_exposure(matched).loc["2020-01-02"] == 0.5
    assert matched.set_index("security_id")["target_weight"].round(6).to_dict() == {"A": 0.375, "B": 0.125}


def test_exposure_metrics_reports_cash_and_concentration() -> None:
    weights = _weights([
        {"trade_date": "2020-01-02", "security_id": "A", "target_weight": 0.25},
        {"trade_date": "2020-01-02", "security_id": "B", "target_weight": 0.25},
        {"trade_date": "2020-01-03", "security_id": "A", "target_weight": 1.0},
    ])
    metrics = exposure_metrics(weights)
    assert metrics.average_gross_exposure == 0.75
    assert metrics.average_cash_weight == 0.25
    assert metrics.average_holdings_count == 1.5
    assert metrics.average_hhi == 0.5625


def test_attribution_decomposition_separates_sizing_from_deployment() -> None:
    baseline = _weights([
        {"trade_date": "2020-01-02", "security_id": "A", "target_weight": 0.5},
    ])
    candidate = _weights([
        {"trade_date": "2020-01-02", "security_id": "A", "target_weight": 1.0},
    ])
    returns = pd.DataFrame([
        {"trade_date": "2020-01-02", "security_id": "A", "forward_return": 0.10},
    ])
    result = attribution_decomposition(
        baseline_weights=baseline,
        candidate_weights=candidate,
        forward_returns=returns,
    )
    assert result["sizing_effect"] == 0.0
    assert result["deployment_effect"] == 0.05
    assert result["total_effect"] == 0.05

from __future__ import annotations

import pandas as pd
import pytest

from research.cygnus import features as F
from research.cygnus.backtest import annualized_ir, spearman_rank_ic
from research.cygnus.strategy import compute_v0_scores, percentile_rank, select_basket


# --------------------------------------------------------------------------- #
# strategy scoring primitives
# --------------------------------------------------------------------------- #
def test_percentile_rank_handles_ties_and_none() -> None:
    assert percentile_rank([10.0, 20.0, 30.0]) == [0.0, 0.5, 1.0]
    assert percentile_rank([5.0]) == [0.5]
    out = percentile_rank([1.0, None, 3.0])
    assert out[1] is None and out[0] == 0.0 and out[2] == 1.0


def test_compute_v0_scores_weights_and_penalties() -> None:
    cohort = [
        {"event_reaction_abnormal_return": 0.05, "revenue_yoy_acceleration": 0.10,
         "drift_confirmation": 1.0, "filing_quality_bonus": 1.0, "pre_event_runup": 0.01},
        {"event_reaction_abnormal_return": 0.01, "revenue_yoy_acceleration": 0.00,
         "drift_confirmation": 0.5, "filing_quality_bonus": 1.0, "pre_event_runup": 0.50},
        {"event_reaction_abnormal_return": -0.02, "revenue_yoy_acceleration": -0.10,
         "drift_confirmation": 0.0, "filing_quality_bonus": 0.0, "pre_event_runup": 0.99},
    ]
    scored = compute_v0_scores(cohort)
    assert all(s["cygnus_v0_score"] is not None for s in scored)
    # best event ranks highest
    assert scored[0]["cygnus_v0_score"] > scored[1]["cygnus_v0_score"] > scored[2]["cygnus_v0_score"]
    # negative reaction triggers failed-reaction penalty
    assert scored[2]["failed_reaction_penalty"] == 0.10


def test_compute_v0_scores_requires_reaction() -> None:
    scored = compute_v0_scores([{"event_reaction_abnormal_return": None, "drift_confirmation": 0.5}])
    assert scored[0]["cygnus_v0_score"] is None


def test_select_basket_quality_gate_and_min() -> None:
    cohort = compute_v0_scores([
        {"event_reaction_abnormal_return": 0.03, "revenue_yoy_acceleration": 0.1,
         "drift_confirmation": 1.0, "filing_quality_bonus": 1.0, "pre_event_runup": 0.0},
        {"event_reaction_abnormal_return": -0.01, "revenue_yoy_acceleration": 0.0,
         "drift_confirmation": 0.0, "filing_quality_bonus": 0.0, "pre_event_runup": 0.0},
    ])
    # only 1 positive-reaction event -> below min_basket=5 -> empty
    assert select_basket(cohort, top_n=10, min_basket=5) == []
    assert select_basket(cohort, top_n=10, min_basket=1)[0]["event_reaction_abnormal_return"] == 0.03


# --------------------------------------------------------------------------- #
# backtest statistics
# --------------------------------------------------------------------------- #
def test_spearman_rank_ic_perfect_monotone() -> None:
    scores = [1.0, 2.0, 3.0, 4.0, 5.0]
    fwd = [0.1, 0.2, 0.3, 0.4, 0.5]
    rho, t, n = spearman_rank_ic(scores, fwd)
    assert rho == pytest.approx(1.0)
    assert n == 5 and t is not None and t > 2


def test_spearman_rank_ic_insufficient_n() -> None:
    rho, t, n = spearman_rank_ic([1.0, 2.0], [0.1, 0.2])
    assert rho is None and n == 2


def test_annualized_ir() -> None:
    assert annualized_ir([]) is None
    assert annualized_ir([0.001]) is None  # need >= 2 obs
    ir = annualized_ir([0.001, -0.0005, 0.0012, 0.0003, -0.0002])
    assert ir is not None and isinstance(ir, float)


# --------------------------------------------------------------------------- #
# features
# --------------------------------------------------------------------------- #
def test_close_to_close_and_forward_return_bounds() -> None:
    s = pd.Series([100.0, 110.0, 121.0], index=pd.to_datetime(["2020-01-02", "2020-01-03", "2020-01-06"]))
    assert F.close_to_close_return(s, 1, 1) == pytest.approx(0.10)
    assert F.forward_return(s, 0, 2) == pytest.approx(0.21)
    assert F.forward_return(s, 1, 5) is None  # beyond data (holdout-safe)


def test_revenue_yoy_acceleration_pit_safe() -> None:
    # Two years of quarterly revenue; acceleration = latest YoY - prior YoY.
    rows = []
    base = {"tag": "Revenues", "unit": "USD"}
    quarters = [
        ("2022-03-31", "2022-01-01", 100), ("2022-06-30", "2022-04-01", 100),
        ("2023-03-31", "2023-01-01", 110), ("2023-06-30", "2023-04-01", 121),
    ]
    for pe, ps, val in quarters:
        rows.append({**base, "period_end": pe, "period_start": ps,
                     "filed_date": pe, "value": float(val) * 1e6})
    df = pd.DataFrame(rows)
    # As-of after the 2023-06-30 filing: latest YoY=21%, prior YoY=10% -> accel ~0.11
    accel = F.revenue_yoy_acceleration(df, pd.Timestamp("2023-08-01"))
    assert accel == pytest.approx(0.11, abs=1e-6)
    # PIT: as-of before the 2023 filings -> not enough data
    assert F.revenue_yoy_acceleration(df, pd.Timestamp("2022-08-01")) is None

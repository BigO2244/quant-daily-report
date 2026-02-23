import pandas as pd

import daily_quant_report as dqr
from paper.reporting_consistency import compute_exposure, determine_sleeve_state


def test_nav_matches_snapshot():
    snapshot = {
        "performance_diagnostics": {"current_equity": 9999.0},
    }
    nav_ts = pd.DataFrame(
        [
            {"date": "2026-02-19", "equity": 10400.0, "return_1d": 0.01},
            {"date": "2026-02-20", "equity": 10546.0, "return_1d": 0.0140384615},
        ]
    )

    dqr._merge_nav_metrics_into_snapshot(snapshot, nav_ts, asof_date="2026-02-20")

    assert abs(snapshot["nav_metrics"]["equity"] - 10546.0) < 1e-9
    assert (
        abs(
            float(snapshot["performance_diagnostics"]["current_equity"])
            - float(snapshot["nav_metrics"]["equity"])
        )
        < 1e-9
    )


def test_gross_exposure_long_only():
    weights = {"AAPL": 0.40, "MSFT": 0.35, "CASH": 0.25}
    exposure = compute_exposure(weights, leverage_enabled=False, enforce_bounds=True)

    assert abs(float(exposure["gross_exposure"]) - 0.75) < 1e-9
    assert abs(float(exposure["net_exposure"]) - 0.75) < 1e-9
    assert float(exposure["gross_exposure_pct"]) <= 100.0 + 1e-6


def test_breaker_scaling_reduces_exposure():
    pre = {"AAPL": 0.60, "MSFT": 0.40}
    pre_exp = compute_exposure(pre, leverage_enabled=False, enforce_bounds=True)
    mult = 0.5
    post = {ticker: weight * mult for ticker, weight in pre.items()}
    post_exp = compute_exposure(post, leverage_enabled=False, enforce_bounds=True)

    assert abs(float(pre_exp["gross_exposure"]) - 1.0) < 1e-9
    assert abs(float(post_exp["gross_exposure"]) - 0.5) < 1e-9
    assert abs(float(post_exp["gross_exposure"]) - float(pre_exp["gross_exposure"]) * mult) < 1e-9


def test_inactive_sleeve_reporting():
    sleeve_states = {
        "sleeve_trend": determine_sleeve_state(
            {
                "equity_df": pd.DataFrame({"date": ["2026-02-20"], "equity": [10000.0]}),
                "target_weights": pd.DataFrame({"AAPL": [1.0]}),
            },
            allocation_weight=0.7,
        ),
        "sleeve_2": determine_sleeve_state(
            {
                "equity_df": pd.DataFrame({"date": ["2026-02-20"], "equity": [10000.0]}),
                "target_weights": pd.DataFrame(),
            },
            allocation_weight=0.3,
        ),
        "charlie_munger": determine_sleeve_state(
            {
                "equity_df": pd.DataFrame(),
                "target_weights": pd.DataFrame(),
            },
            allocation_weight=0.0,
        ),
    }
    snapshot = {
        "asof": "2026-02-20",
        "allocations": {
            "sleeves": {"sleeve_trend": 0.7, "sleeve_2": 0.3, "charlie_munger": 0.0},
            "cash": 0.0,
        },
        "target_cash_weight": 0.0,
        "sleeve_states": sleeve_states,
        "performance_summary": {"wtd": 0.01, "mtd": 0.02, "total_return": 0.03},
        "performance_diagnostics": {"current_equity": 10546.0, "day_return": 0.01},
        "alpha_attribution": {"ok": False, "reason": "insufficient overlap"},
        "charlie_munger": {},
        "orders": [],
        "skipped_trades": [],
        "nav_metrics": {"equity": 10546.0, "return_1d": 0.01, "wtd": 0.01, "mtd": 0.02, "si": 0.03},
        "inception_metrics": {"inception_date": "2026-01-01", "spy_return_since_inception": 0.01},
        "allocation_diagnostics": {"sleeve_1": {"desired_allocation": 0.7, "achieved_invested": 0.7, "forced_cash": 0.3}},
    }

    _, body = dqr.create_snapshot_email(
        snapshot,
        execution_payload={"mode": "SHADOW", "trades": [], "executable_trades_count": 0},
    )

    assert "SLEEVE 2 — VALUATION (OPPORTUNISTIC)\n• Status: ACTIVE" not in body
    assert "CHARLIE MUNGER SLEEVE — LONG HOLD\n• Status: ACTIVE" not in body
    assert "SLEEVE 2 — VALUATION (OPPORTUNISTIC)\n• Status: INACTIVE" in body
    assert "CHARLIE MUNGER SLEEVE — LONG HOLD\n• Status: INACTIVE" in body


def test_monthly_resample_me_stability():
    idx = pd.date_range("2026-01-01", "2026-03-31", freq="D")
    s = pd.Series(range(len(idx)), index=idx, dtype=float)

    rule = "ME"
    try:
        s.resample("ME").last()
    except Exception:
        rule = "M"
    out = s.resample(rule).last()

    assert out.index.strftime("%Y-%m-%d").tolist() == [
        "2026-01-31",
        "2026-02-28",
        "2026-03-31",
    ]

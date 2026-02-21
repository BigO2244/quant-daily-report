import daily_quant_report as dqr


def test_apply_breaker_allocation_diagnostics_lock_mode_sets_post_breaker_values():
    diagnostics = {
        "sleeve_1": {
            "desired_allocation": 1.0,
            "achieved_invested": 0.95,
            "forced_cash": 0.05,
            "selected_names": 10,
            "min_required_names": 10,
            "limiting_constraint": "max_position_weight=10%",
        }
    }
    snapshot = {
        "target_cash_weight": 1.0,
        "breaker": {
            "mode": "lock",
            "exposure_multiplier_today": 0.0,
            "invested_after_overlay": 0.0,
        },
    }

    out = dqr._apply_breaker_allocation_diagnostics(diagnostics, snapshot)
    sleeve1 = out["sleeve_1"]

    assert sleeve1["desired_allocation_pre_breaker"] == 1.0
    assert sleeve1["desired_allocation"] == 0.0
    assert sleeve1["achieved_invested"] == 0.0
    assert sleeve1["forced_cash"] == 1.0
    assert sleeve1["limiting_constraint"] == "BREAKER_MODE=lock"


def test_apply_breaker_allocation_diagnostics_partial_mode_includes_breaker_constraint():
    diagnostics = {
        "sleeve_1": {
            "desired_allocation": 1.0,
            "achieved_invested": 0.95,
            "forced_cash": 0.05,
            "selected_names": 10,
            "min_required_names": 10,
            "limiting_constraint": "max_position_weight=10%",
        }
    }
    snapshot = {
        "target_cash_weight": 0.5,
        "breaker": {
            "mode": "partial",
            "exposure_multiplier_today": 0.5,
            "invested_after_overlay": 0.5,
        },
    }

    out = dqr._apply_breaker_allocation_diagnostics(diagnostics, snapshot)
    sleeve1 = out["sleeve_1"]

    assert sleeve1["desired_allocation_pre_breaker"] == 1.0
    assert sleeve1["desired_allocation"] == 0.5
    assert sleeve1["achieved_invested"] == 0.5
    assert sleeve1["forced_cash"] == 0.5
    assert sleeve1["limiting_constraint"] == "BREAKER_MODE=partial + max_position_weight=10%"


def test_snapshot_email_shows_breaker_lock_allocation_diagnostics():
    snapshot = {
        "asof": "2026-02-19",
        "allocations": {
            "sleeves": {
                "sleeve_trend": 0.0,
                "sleeve_2": 0.0,
                "charlie_munger": 0.0,
            },
            "cash": 1.0,
        },
        "target_cash_weight": 1.0,
        "performance_summary": {"wtd": 0.01, "mtd": 0.02, "total_return": 0.15},
        "performance_diagnostics": {"current_equity": 10000.0, "day_return": 0.001},
        "alpha_attribution": {"ok": False, "reason": "insufficient overlap"},
        "charlie_munger": {},
        "orders": [],
        "skipped_trades": [],
        "nav_metrics": {"equity": 10000.0, "return_1d": 0.001, "wtd": 0.01, "mtd": 0.02, "si": 0.15},
        "inception_metrics": {"inception_date": "2026-01-05", "spy_return_since_inception": 0.06},
        "allocation_diagnostics": {
            "sleeve_1": {
                "desired_allocation": 0.0,
                "desired_allocation_pre_breaker": 1.0,
                "achieved_invested": 0.0,
                "forced_cash": 1.0,
                "selected_names": 10,
                "min_required_names": 10,
                "limiting_constraint": "BREAKER_MODE=lock",
            }
        },
    }

    _, body = dqr.create_snapshot_email(
        snapshot,
        execution_payload={"mode": "SHADOW", "trades": [], "executable_trades_count": 0},
    )

    assert "Sleeve 1 desired allocation (post-breaker): 0.00%" in body
    assert "Sleeve 1 achieved invested: 0.00%" in body
    assert "Sleeve 1 forced cash: 100.00%" in body
    assert "Limiting constraint: BREAKER_MODE=lock" in body

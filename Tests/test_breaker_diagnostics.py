import pandas as pd
import pytest

import daily_quant_report as dqr
from engine.breaker import apply_portfolio_exposure_overlay, get_breaker_config


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


# ---------------------------------------------------------------------------
# New: source/reason diagnostics and cash overlay correctness
# ---------------------------------------------------------------------------

def _make_weights(tickers_and_weights):
    rows = [{"ticker": t, "target_weight": w} for t, w in tickers_and_weights]
    return pd.DataFrame(rows)


def test_breaker_config_has_source_and_reason_fields(monkeypatch):
    """get_breaker_config always returns source and reason."""
    monkeypatch.delenv("BREAKER_MODE", raising=False)
    cfg = get_breaker_config()
    assert "source" in cfg and "reason" in cfg
    assert cfg["source"] == "default"


def test_cash_target_weight_full_when_breaker_off(monkeypatch):
    """Breaker OFF → exposure=1.0, no spurious cash injected."""
    monkeypatch.delenv("BREAKER_MODE", raising=False)
    cfg = get_breaker_config()
    assert cfg["exposure_multiplier"] == 1.0

    df = _make_weights([("SPY", 0.6), ("QQQ", 0.4)])
    result = apply_portfolio_exposure_overlay(df, cfg["exposure_multiplier"])

    cash_rows = result[result["ticker"] == "CASH"]
    cash_weight = float(cash_rows["target_weight"].sum()) if not cash_rows.empty else 0.0
    assert cash_weight < 1e-9, f"Expected ~0 cash, got {cash_weight}"


def test_overlay_weight_sum_invariant(monkeypatch):
    """Total weight always sums to 1.0 after overlay for any mode."""
    for mode, partial in [("off", None), ("partial", "0.3"), ("lock", None)]:
        monkeypatch.setenv("BREAKER_MODE", mode)
        if partial:
            monkeypatch.setenv("BREAKER_PARTIAL_EXPOSURE", partial)
        else:
            monkeypatch.delenv("BREAKER_PARTIAL_EXPOSURE", raising=False)
        cfg = get_breaker_config()
        df = _make_weights([("SPY", 0.5), ("AGG", 0.3), ("GLD", 0.2)])
        result = apply_portfolio_exposure_overlay(df, cfg["exposure_multiplier"])
        total = float(result["target_weight"].sum())
        assert abs(total - 1.0) < 1e-9, f"mode={mode}: total={total}"

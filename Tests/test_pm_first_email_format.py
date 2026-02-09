from daily_quant_report import create_pm_first_trade_email


def test_pm_first_email_uses_shadow_portfolio_digest_layout():
    snapshot = {
        "asof": "2026-02-05",
        "allocations": {
            "cash": 0.30,
            "sleeves": {
                "sleeve_trend": 0.45,
                "sleeve_2": 0.25,
                "charlie_munger": 0.30,
            },
        },
        "performance_diagnostics": {
            "current_equity": 10406,
            "day_return": 0.0006,
        },
        "performance_summary": {
            "wtd": 0.008,
            "mtd": 0.0072,
            "total_return": 0.0406,
        },
        "charlie_munger": {
            "meta": {"near_ma_candidates": 0},
            "selected": [],
        },
        "alpha_attribution": {"n_days": 5},
    }

    subject, body = create_pm_first_trade_email(snapshot)

    assert subject == "Daily Trade Rundown — 02/05/2026"
    assert "ENVIRONMENT: SHADOW (NO CAPITAL AT RISK)" in body
    assert "• Total Equity: $10,406.00" in body
    assert "• Day Move: 0.06%" in body
    assert "• Sleeve 1 — Momentum: 45.00%" in body
    assert "• Sleeve 2 — Valuation: 25.00%" in body
    assert "• Allocation: 30.00%" in body
    assert "• Status: Pending (insufficient lookback window)" in body
    assert "— Automated Portfolio Engine" in body

import daily_quant_report as dqr


def test_enforce_charlie_floor_uses_other_sleeves_first():
    sleeves, cash = dqr.enforce_charlie_bounds(
        {"sleeve_trend": 0.55, "sleeve_2": 0.25, "charlie_munger": 0.10},
        0.10,
        charlie_active=True,
    )
    assert abs(sleeves["charlie_munger"] - dqr.CHARLIE_MIN) < 1e-9
    assert sleeves["sleeve_trend"] < 0.55
    assert sleeves["sleeve_2"] < 0.25
    assert abs(cash - 0.10) < 1e-9
    assert abs(sum(sleeves.values()) + cash - 1.0) < 1e-9


def test_enforce_charlie_cap_redistributes_to_fast_sleeves():
    sleeves, cash = dqr.enforce_charlie_bounds(
        {"sleeve_trend": 0.35, "sleeve_2": 0.20, "charlie_munger": 0.40},
        0.05,
        charlie_active=True,
    )
    assert abs(sleeves["charlie_munger"] - dqr.CHARLIE_MAX) < 1e-9
    assert sleeves["sleeve_trend"] > 0.35
    assert sleeves["sleeve_2"] > 0.20
    assert abs(sum(sleeves.values()) + cash - 1.0) < 1e-9


def test_execution_email_no_trades_wording():
    payload = {
        "trade_date": "2026-02-09",
        "mode": "SHADOW",
        "execution_status": "READY",
        "trades": [],
    }
    _subject, body = dqr.build_execution_email_text(payload)
    assert "NO TRADES TODAY" in body
    assert "NEW ORDERS" not in body


def test_snapshot_email_labels_proposed_and_charlie():
    subject, body = dqr.create_snapshot_email(
        {
            "asof": "2026-02-09",
            "allocations": {"sleeves": {"sleeve_trend": 0.5, "sleeve_2": 0.25, "charlie_munger": 0.2}, "cash": 0.05},
            "charlie_munger": {"selected": [], "meta": {"near_ma_candidates": 0}},
            "orders": [],
            "proposed_trades": [],
            "performance_summary": {},
            "performance_diagnostics": {},
        },
        execution_payload={"mode": "SHADOW"},
    )
    assert "MODEL & PERFORMANCE SNAPSHOT" in subject
    assert "PROPOSED / NEXT REBALANCE (NOT EXECUTED TODAY)" in body
    assert "Charlie Munger" in body

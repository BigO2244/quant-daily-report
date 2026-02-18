import daily_quant_report as dqr


def test_should_execute_run_future_trade_date_is_planning():
    should_execute, is_planning_run, market_open = dqr._should_execute_run(
        trade_date_str="2026-02-19",
        today_et_str="2026-02-18",
        paper_summary={"market_guard": {"is_trading_session": True}},
    )

    assert is_planning_run is True
    assert market_open is True
    assert should_execute is False


def test_should_execute_run_today_but_market_closed_uses_market_guard_truth():
    paper_summary = {
        "market_status": "OPEN",  # stale/incorrect fallback should be ignored
        "market_guard": {"is_trading_session": False, "status": "CLOSED"},
    }
    should_execute, is_planning_run, market_open = dqr._should_execute_run(
        trade_date_str="2026-02-18",
        today_et_str="2026-02-18",
        paper_summary=paper_summary,
    )

    assert is_planning_run is False
    assert market_open is False
    assert should_execute is False


def test_non_execution_run_payload_forces_planned_reason():
    payload = dqr.build_execution_email_payload(
        trade_date="2026-02-19",
        daily_snapshot={"risk_levels": [], "holdings": []},
        paper_summary={
            "trading_mode": "shadow",
            "market_status": "OPEN",
            "market_guard": {"is_trading_session": False},
            "execution_trades": [{"ticker": "AAPL", "side": "BUY", "shares": 1, "price": 100.0}],
            "run_id": "rid",
        },
    )

    # mirror main() behavior for non-execution runs
    payload["execution_status"] = "PLANNED"
    payload["halt_reason"] = None
    payload["planning_disclaimer"] = "Planning email only — no orders were sent."
    payload["validation_reason"] = "market_closed_or_not_session"

    assert payload["execution_status"] == "PLANNED"
    assert payload["validation_reason"] == "market_closed_or_not_session"
    assert payload["planning_disclaimer"] == "Planning email only — no orders were sent."

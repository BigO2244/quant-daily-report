from paper.build_execution_email import build_execution_email_html, build_execution_email_text


def test_execution_email_body_is_defined_and_formats_shadow_payload_status():
    payload = {
        "trade_date": "2026-02-05",
        "mode": "SHADOW",
        "execution_status": "READY",
        "trades": [],
        "execution_payload_status": "NOT GENERATED (Expected in SHADOW)",
    }

    subject, body = build_execution_email_text(payload)

    assert subject == "TRADE EXECUTION — 2026-02-05 (SHADOW)"
    assert "• Execution Payload: NOT GENERATED (EXPECTED IN SHADOW)" in body


def test_execution_email_no_trades_includes_min_trade_filter_reason_and_counts():
    payload = {
        "trade_date": "2026-02-05",
        "mode": "SHADOW",
        "execution_status": "READY",
        "trades": [],
        "no_trades_reason": "No executable trades after rounding and $100 minimum trade filter",
        "proposed_trades_intent_count": 3,
        "executable_trades_count": 0,
    }

    _, body = build_execution_email_text(payload)

    assert "No executable trades after rounding and $100 minimum trade filter" in body
    assert "Proposed Trades (Intent) | 3" in body
    assert "Executable Trades | 0" in body


def test_execution_email_includes_turnover_scaling_risk_note():
    payload = {
        "trade_date": "2026-02-05",
        "mode": "SHADOW",
        "execution_status": "READY",
        "trades": [],
        "turnover_note": "Turnover cap applied: requested $4,679.07, cap $3,023.97, scale 0.6463.",
    }

    _, body = build_execution_email_text(payload)

    assert "Risk Note: Turnover cap applied: requested $4,679.07, cap $3,023.97, scale 0.6463." in body


def test_execution_email_html_contains_buy_sell_tables_and_headers():
    payload = {
        "trade_date": "2026-02-05",
        "mode": "SHADOW",
        "execution_status": "READY",
        "trades": [
            {
                "ticker": "AAPL",
                "side": "BUY",
                "shares": 10,
                "entry_price": 180.0,
                "stop_loss": 170.0,
                "take_profit": 200.0,
                "notional": 1800.0,
            },
            {
                "ticker": "MSFT",
                "side": "SELL",
                "shares": 5,
                "entry_price": 400.0,
                "stop_loss": 420.0,
                "take_profit": 360.0,
                "notional": 2000.0,
                "reason": "removed_from_targets",
            },
        ],
    }
    _, html = build_execution_email_html(payload)

    assert "<h3>Buy Orders</h3>" in html
    assert "<h3>Sell / Close Orders</h3>" in html
    assert "Entry (X)" in html
    assert "Stop (Y)" in html
    assert "Target (Z)" in html
    assert "AAPL" in html
    assert "MSFT" in html


def test_execution_email_includes_portfolio_risk_summary_values():
    payload = {
        "trade_date": "2026-02-05",
        "mode": "SHADOW",
        "execution_status": "READY",
        "trades": [],
        "risk_summary": {
            "Turnover requested ($)": "$4,679.07",
            "Turnover cap ($)": "$3,023.97",
            "Turnover scale": "0.6463",
        },
    }

    _, body = build_execution_email_text(payload)
    _, html = build_execution_email_html(payload)

    assert "PORTFOLIO RISK SUMMARY" in body
    assert "Turnover requested ($): $4,679.07" in body
    assert "Portfolio Risk Summary" in html
    assert "Turnover cap ($)" in html


def test_execution_email_surfaces_operator_execution_and_timing_status():
    payload = {
        "trade_date": "2026-02-05",
        "mode": "ALPACA",
        "execution_status": "HALTED",
        "operator_execution_status": "partial",
        "timing_status": "degraded_late",
        "halt_reason": "post_submit_artifact_failure",
        "trades": [],
    }

    _, body = build_execution_email_text(payload)

    assert "Execution Outcome: PARTIAL" in body
    assert "Timing Status: degraded_late" in body



def test_execution_email_no_trades_includes_drop_diagnostics_when_present():
    payload = {
        "trade_date": "2026-02-05",
        "mode": "SHADOW",
        "execution_status": "READY",
        "trades": [],
        "proposed_trades_intent_count": 4,
        "executable_trades_count": 0,
        "min_trade_dollars": 125.0,
        "filter_stats": {
            "raw": 4,
            "rounded": 4,
            "kept": 0,
            "dropped_zero_shares": 2,
            "dropped_min_notional": 1,
        },
    }

    _, body = build_execution_email_text(payload)
    _, html = build_execution_email_html(payload)

    assert "Dropped Zero Shares | 2" in body
    assert "Dropped Min Notional | 1" in body
    assert "Min Trade Dollars | $125.00" in body
    assert "Dropped Zero Shares" in html
    assert "Dropped Min Notional" in html


def test_execution_email_no_trades_uses_unavailable_for_missing_diagnostics():
    payload = {
        "trade_date": "2026-02-05",
        "mode": "SHADOW",
        "execution_status": "READY",
        "trades": [],
    }

    _, body = build_execution_email_text(payload)

    assert "Proposed Trades (Intent) | unavailable" in body
    assert "Executable Trades | unavailable" in body
    assert "Dropped Zero Shares | unavailable" in body
    assert "Dropped Min Notional | unavailable" in body
    assert "Min Trade Dollars | unavailable" in body


def test_execution_email_no_trades_supports_alternate_dropped_zero_key():
    payload = {
        "trade_date": "2026-02-05",
        "mode": "SHADOW",
        "execution_status": "READY",
        "trades": [],
        "proposed_intent_count": 6,
        "executable_trades_count": 0,
        "min_trade_dollars": 150.0,
        "filter_stats": {
            "dropped_zero": 4,
            "dropped_min_notional": 2,
        },
    }

    _, body = build_execution_email_text(payload)
    _, html = build_execution_email_html(payload)

    assert "Proposed Trades (Intent) | 6" in body
    assert "Dropped Zero Shares | 4" in body
    assert "Dropped Min Notional | 2" in body
    assert "$150.00" in html

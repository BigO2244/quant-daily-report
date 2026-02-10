from paper.build_execution_email import build_execution_email_text


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
        "proposed_trades_intent": 3,
        "executable_trades_count": 0,
    }

    _, body = build_execution_email_text(payload)

    assert "No executable trades after rounding and $100 minimum trade filter" in body
    assert "Proposed Trades (Intent): 3" in body
    assert "Executable Trades: 0" in body


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

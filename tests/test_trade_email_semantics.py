from core.quant_report import create_trade_email


def test_trade_email_new_orders_comes_from_execution_payload_and_no_trades_wording():
    _subject, body = create_trade_email(
        {"asof": "2026-02-09", "orders": [{"ticker": "SHOULD_NOT_APPEAR"}]},
        execution_payload={"trades": [], "halt_reason": "NO EXECUTABLE TRADES"},
    )
    assert "1) Trades for Today (NEW ORDERS)" in body
    assert "NO TRADES" in body
    assert "reason=NO EXECUTABLE TRADES" in body
    assert "SHOULD_NOT_APPEAR" not in body

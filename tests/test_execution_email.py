from paper.build_execution_email import build_execution_email_text


def test_execution_email_halted_has_header_only():
    subject, body = build_execution_email_text(
        {
            "trade_date": "2026-02-05",
            "mode": "SHADOW",
            "execution_status": "HALTED",
            "halt_reason": "STALE PRICES",
            "trades": [
                {"ticker": "COIN", "side": "BUY", "shares": 1.0},
            ],
        }
    )

    assert subject == "TRADE EXECUTION — 2026-02-05 (SHADOW)"
    assert "Execution Status: HALTED — STALE PRICES" in body
    assert "TODAY’S ACTION" not in body
    assert "ORDER META" not in body


def test_execution_email_trades_contains_required_sections():
    subject, body = build_execution_email_text(
        {
            "trade_date": "2026-02-05",
            "mode": "SHADOW",
            "execution_status": "READY",
            "run_id": "2026-02-05:main:v2",
            "order_ids": [
                "2026-02-05:main:v2:COIN:BUY",
                "2026-02-05:main:v2:ADBE:SELL",
            ],
            "trades": [
                {
                    "ticker": "COIN",
                    "side": "BUY",
                    "shares": 6.36,
                    "entry_price": 161.72,
                    "stop_loss": 147.03,
                    "take_profit": 181.30,
                    "notional": 1029.16,
                    "reason": "rebalance",
                },
                {
                    "ticker": "ADBE",
                    "side": "SELL",
                    "shares": 8.69,
                    "entry_price": 510.00,
                    "stop_loss": 480.00,
                    "take_profit": 540.00,
                    "notional": 4431.90,
                    "reason": "Rebalance",
                },
            ],
        }
    )

    assert subject == "TRADE EXECUTION — 2026-02-05 (SHADOW)"
    assert "1) TODAY’S ACTION — EXECUTE THESE ORDERS" in body
    assert "2) ORDER META (FOR TRACKING / IDEMPOTENCY)" in body
    assert "3) EXECUTION NOTES" in body
    assert "COIN | BUY | 6.36 | $161.72 | $147.03 | $181.30 | ~$1,029.16" in body
    assert "ADBE | SELL | 8.69 | Rebalance" in body
    assert "- 2026-02-05:main:v2:COIN:BUY" in body


def test_execution_email_no_trade_block_present():
    _, body = build_execution_email_text(
        {
            "trade_date": "2026-02-05",
            "mode": "SHADOW",
            "execution_status": "READY",
            "trades": [],
        }
    )

    assert "NO TRADES TODAY" in body
    assert "- Market OPEN" in body
    assert "- Signals evaluated" in body
    assert "- No assets met entry criteria" in body

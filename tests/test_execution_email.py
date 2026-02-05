from paper.build_execution_email import build_execution_email_text


def test_execution_email_halted_has_header_only():
    subject, body = build_execution_email_text(
        {
            "trade_date": "2026-02-05",
            "mode": "SHADOW",
            "execution_status": "HALTED",
            "halt_reason": "STALE PRICES",
            "trades": [
                {"ticker": "COIN", "side": "BUY", "shares": 1},
            ],
        }
    )

    assert subject == "TRADE EXECUTION — 2026-02-05 (SHADOW)"
    assert "Execution Status: HALTED — STALE PRICES" in body
    assert "TODAY’S ACTION" not in body
    assert "ORDER META" not in body


def test_execution_email_trades_sorted_and_sell_has_risk_columns():
    subject, body = build_execution_email_text(
        {
            "trade_date": "2026-02-05",
            "mode": "SHADOW",
            "execution_status": "READY",
            "run_id": "2026-02-05:main:v2",
            "order_ids": [
                "2026-02-05:main:v2:COIN:BUY",
                "2026-02-05:main:v2:ADBE:SELL",
                "2026-02-05:main:v2:AAPL:BUY",
            ],
            "trades": [
                {
                    "ticker": "COIN",
                    "side": "BUY",
                    "shares": 6,
                    "entry_price": 161.72,
                    "stop_loss": 147.03,
                    "take_profit": 181.30,
                    "notional": 970.32,
                    "reason": "rebalance",
                },
                {
                    "ticker": "ADBE",
                    "side": "SELL",
                    "shares": 8,
                    "entry_price": 510.00,
                    "stop_loss": 480.00,
                    "take_profit": 540.00,
                    "notional": 4080.00,
                    "reason": "Rebalance",
                },
                {
                    "ticker": "AAPL",
                    "side": "BUY",
                    "shares": 4,
                    "entry_price": 210.00,
                    "stop_loss": 200.00,
                    "take_profit": 225.00,
                    "notional": 840.00,
                    "reason": "rebalance",
                },
            ],
        }
    )

    assert subject == "TRADE EXECUTION — 2026-02-05 (SHADOW)"
    assert "1) TODAY’S ACTION — EXECUTE THESE ORDERS" in body
    assert "2) ORDER META (FOR TRACKING / IDEMPOTENCY)" in body
    assert "3) EXECUTION NOTES" in body
    assert "Ticker | Side | Shares | Entry (X) | Stop-Loss (Y) | Take Profit (Z) | Notes" in body
    assert body.index("AAPL | BUY") < body.index("COIN | BUY")
    assert "ADBE | SELL | 8 | $510.00 | $480.00 | $540.00 | Rebalance" in body
    # Order IDs are sorted by ticker/side
    assert body.index("- 2026-02-05:main:v2:AAPL:BUY") < body.index("- 2026-02-05:main:v2:ADBE:SELL") < body.index("- 2026-02-05:main:v2:COIN:BUY")


def test_execution_email_sgov_sell_kept_with_na_risk_fields():
    _, body = build_execution_email_text(
        {
            "trade_date": "2026-02-05",
            "mode": "SHADOW",
            "execution_status": "READY",
            "trades": [
                {"ticker": "SGOV", "side": "SELL", "shares": 10, "notes": "reduce"},
            ],
        }
    )

    assert "SGOV | SELL | 10 | n/a | n/a | n/a | reduce" in body


def test_execution_email_no_trade_block_present():
    _, body = build_execution_email_text(
        {
            "trade_date": "2026-02-05",
            "mode": "SHADOW",
            "execution_status": "READY",
            "trades": [],
        }
    )

    assert "1) TODAY’S ACTION — EXECUTE THESE ORDERS" in body
    assert "NO TRADES TODAY" in body
    assert "2) ORDER META (FOR TRACKING / IDEMPOTENCY)" not in body
    assert "3) EXECUTION NOTES" in body
    assert "- Market OPEN" in body
    assert "- Signals evaluated" in body
    assert "- No assets met entry criteria" in body
    assert "CASH is not the same thing as SGOV" in body


def test_execution_email_no_trailing_percent_or_whitespace():
    _, body = build_execution_email_text(
        {
            "trade_date": "2026-02-05",
            "mode": "SHADOW",
            "execution_status": "READY",
            "trades": [],
        }
    )

    assert not body.endswith("%")
    assert body.rstrip("\n") == body

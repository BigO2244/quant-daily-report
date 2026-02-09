from pathlib import Path

from daily_quant_report import build_execution_email_payload, _write_execution_email_payload


def test_build_execution_payload_whole_shares_sorting_and_holdings_cap():
    payload = build_execution_email_payload(
        trade_date="2026-02-05",
        daily_snapshot={
            "risk_levels": [
                {"ticker": "AAPL", "entry_price": 210.0, "stop_loss": 200.0, "take_profit": 225.0},
                {"ticker": "MSFT", "entry_price": 430.0, "stop_loss": 410.0, "take_profit": 455.0},
                {"ticker": "SGOV"},
            ],
            "holdings": [
                {"ticker": "MSFT", "shares": 3.8},
                {"ticker": "SGOV", "shares": 10.0},
            ],
        },
        paper_summary={
            "trading_mode": "shadow",
            "target_cash_weight": 0.30,
            "investable_dollars": 7000.0,
            "sizing_equity": 10000.0,
            "target_cash_dollars": 3000.0,
            "shadow_orders": [
                {"ticker": "MSFT", "side": "SELL", "quantity": 9.9, "reason": "reduce", "order_id": "rid:MSFT:SELL"},
                {"ticker": "AAPL", "side": "BUY", "quantity": 1.9, "reason": "rebalance", "order_id": "rid:AAPL:BUY"},
                {"ticker": "SGOV", "side": "SELL", "quantity": 0.7, "reason": "rebalance", "order_id": "rid:SGOV:SELL"},
            ],
            "run_id": "rid",
        },
    )

    # SGOV sell is dropped because floored shares become 0
    assert [t["ticker"] for t in payload["trades"]] == ["AAPL", "MSFT"]
    assert payload["trades"][0]["shares"] == 1
    # capped to available holdings (floor(3.8) == 3)
    assert payload["trades"][1]["shares"] == 3
    assert payload["trades"][1]["notional"] == 3 * 430.0
    assert payload["order_ids"] == ["rid:AAPL:BUY", "rid:MSFT:SELL"]


    assert payload["cash_target_weight"] == 0.30
    assert payload["investable_dollars"] == 7000.0
    assert payload["equity"] == 10000.0
    assert payload["cash_target_dollars"] == 3000.0


def test_execution_payload_json_ends_with_newline(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out_path = _write_execution_email_payload({"trade_date": "2026-02-05", "trades": []}, "2026-02-05")
    data = Path(out_path).read_bytes()
    assert data.endswith(b"\n")


def test_build_execution_payload_shadow_no_trades_includes_recommendation_block():
    payload = build_execution_email_payload(
        trade_date="2026-02-05",
        daily_snapshot={"risk_levels": [], "holdings": []},
        paper_summary={
            "trading_mode": "shadow",
            "shadow_orders": [],
            "blocked_reasons": ["max_position_change_pct exceeded ticker=ADBE"],
            "run_id": "rid",
        },
    )

    assert payload["recommended_action"] == "NO"
    assert payload["confidence_level"] == "HIGH"
    assert payload["human_override_required"] == "NO"
    assert payload["blocked_by_constraints"] == ["max position change pct exceeded ticker=ADBE"]
    assert payload["execution_payload_status"] == "NOT GENERATED (Expected in SHADOW)"

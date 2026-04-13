from __future__ import annotations

from scripts.analyze_trade_day_pnl import build_summary, build_trade_rows


def test_build_trade_rows_and_summary() -> None:
    broker_snapshot = {
        "positions_current": [
            {
                "symbol": "XYZ",
                "qty": "3",
                "current_price": "21.5",
                "cost_basis": "60.0",
                "unrealized_pl": "4.5",
            }
        ],
        "orders_report_date": [
            {
                "id": "sell-1",
                "symbol": "ABC",
                "side": "OrderSide.SELL",
                "qty": "2",
                "filled_qty": "2",
                "filled_avg_price": "12.0",
                "submitted_at": "2026-04-07T14:10:45+00:00",
                "filled_at": "2026-04-07T14:10:46+00:00",
                "status": "OrderStatus.FILLED",
            },
            {
                "id": "buy-1",
                "symbol": "XYZ",
                "side": "OrderSide.BUY",
                "qty": "3",
                "filled_qty": "3",
                "filled_avg_price": "20.0",
                "submitted_at": "2026-04-07T14:10:50+00:00",
                "filled_at": "2026-04-07T14:10:51+00:00",
                "status": "OrderStatus.FILLED",
            },
        ],
    }

    rows = build_trade_rows(
        trade_date="2026-04-07",
        orders=broker_snapshot["orders_report_date"],
        pretrade_by_symbol={
            "ABC": {
                "qty": 2.0,
                "avg_entry_price": 10.0,
                "cost_basis": 20.0,
                "current_price": None,
            }
        },
        current_by_symbol={
            "XYZ": {
                "qty": 3.0,
                "current_price": 21.5,
                "cost_basis": 60.0,
                "unrealized_pl": 4.5,
            }
        },
    )

    rows_by_symbol = {row["symbol"]: row for row in rows}
    assert rows_by_symbol["ABC"]["realized_pnl"] == 4.0
    assert rows_by_symbol["XYZ"]["open_mark_pnl"] == 4.5

    summary = build_summary(rows)
    assert summary["realized_exit_pnl"] == 4.0
    assert summary["open_buy_mark_pnl"] == 4.5
    assert summary["winning_exits"] == 1
    assert summary["winning_buys_on_mark"] == 1

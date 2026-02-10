import datetime as dt
from zoneinfo import ZoneInfo

import pandas as pd

import daily_quant_report as dqr
from paper.build_execution_email import build_execution_email_text
import paper.paper_broker as broker


def test_shadow_market_closed_generates_planning_email():
    payload = dqr.build_execution_email_payload(
        trade_date="2026-02-09",
        daily_snapshot={"risk_levels": [], "holdings": []},
        paper_summary={
            "trading_mode": "shadow",
            "market_status": "CLOSED",
            "planned_for": "2026-02-10T09:30:00-05:00",
            "plan_only": False,
            "shadow_orders": [],
            "blocked_reasons": [],
            "run_id": "rid-1",
        },
    )

    assert payload["execution_status"] == "PLANNED"
    assert payload["planning_disclaimer"] == "Planning email only — no orders were sent."

    _, body = build_execution_email_text(payload)
    assert "Execution Status: PLANNED — MARKET CLOSED (NEXT OPEN)" in body
    assert "Planned For: 2026-02-10 09:30 ET" in body
    assert "Planning email only — no orders were sent." in body


def test_live_market_closed_halts_and_generates_no_plan():
    payload = dqr.build_execution_email_payload(
        trade_date="2026-02-09",
        daily_snapshot={"risk_levels": [], "holdings": []},
        paper_summary={
            "trading_mode": "live",
            "market_status": "CLOSED",
            "planned_for": "2026-02-10T09:30:00-05:00",
            "shadow_orders": [{"ticker": "AAPL"}],
            "blocked_reasons": [],
            "run_id": "rid-2",
        },
    )

    assert payload["execution_status"] == "HALTED"
    assert payload["halt_reason"] == "LIVE MODE BLOCKED"
    assert payload["trades"] == []


def _mock_open_market(monkeypatch):
    cfg = broker.PaperConfig(
        initial_equity=10000.0,
        benchmark_ticker="SPY",
        slippage_bps=0.0,
        allow_fractional=True,
        min_trade_dollars=1.0,
        trading_mode="shadow",
    )
    monkeypatch.setattr(broker, "load_config", lambda path: cfg)
    monkeypatch.setattr(
        broker,
        "read_latest_holdings_from_ledger",
        lambda path: (pd.DataFrame(columns=["ticker", "sleeve", "shares"]), 10000.0, 10000.0, ""),
    )
    monkeypatch.setattr(
        broker,
        "load_targets",
        lambda *args, **kwargs: (pd.DataFrame([{"ticker": "AAPL", "target_weight": 1.0, "sleeve": "core"}]), 0.0, "2026-02-10", "2026-02-09"),
    )
    monkeypatch.setattr(
        broker,
        "fetch_open_prices_yfinance",
        lambda tickers, run_date: pd.DataFrame(
            [
                {"ticker": "AAPL", "open": 100.0, "price_date": run_date},
                {"ticker": "SPY", "open": 500.0, "price_date": run_date},
            ]
        ),
    )
    monkeypatch.setattr(
        broker,
        "fetch_prev_closes_yfinance",
        lambda tickers, asof_date: pd.DataFrame([{"ticker": "AAPL", "prev_close": 99.0, "price_date": asof_date}]),
    )
    monkeypatch.setattr(
        broker,
        "validate_open_window",
        lambda **kwargs: (
            True,
            [],
            {
                "blocked_tickers": {},
                "asof_date": "2026-02-09",
                "cutoff_date": "2026-02-09",
            },
        ),
    )
    def _mock_build_rebalance_trades(**kwargs):
        px = float(kwargs["prices"].get("AAPL"))
        return (
            pd.DataFrame(
                [
                    {
                        "ticker": "AAPL",
                        "side": "BUY",
                        "shares": 10,
                        "price": px,
                        "slippage_cost": 0.0,
                        "notional": px * 10,
                        "reason": "Rebalance",
                    }
                ]
            ),
            {"target_investable_dollars": 10000.0, "scaled_tickers": [], "overspend_prevented": False},
        )

    monkeypatch.setattr(broker, "build_rebalance_trades", _mock_build_rebalance_trades)
    monkeypatch.setattr(broker, "apply_risk_guards", lambda trades, equity_prev, cfg: (trades, [], False))
    monkeypatch.setattr(broker, "append_csv", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        broker,
        "apply_trades_to_holdings",
        lambda **kwargs: (pd.DataFrame([{"ticker": "AAPL", "sleeve": "core", "shares": 10.0}]), 9000.0),
    )
    monkeypatch.setattr(
        broker,
        "mark_to_market",
        lambda holdings, prices: pd.DataFrame([{"ticker": "AAPL", "sleeve": "core", "shares": 10.0, "price": 100.0, "market_value": 1000.0}]),
    )
    monkeypatch.setattr(
        broker,
        "_broker_reconciliation",
        lambda **kwargs: {"status": "PASS", "cash_delta": 0.0, "equity_delta": 0.0, "equity_tolerance": 1.0, "position_deltas": []},
    )
    monkeypatch.setattr(broker, "_persist_sent_orders", lambda *args, **kwargs: None)
    monkeypatch.setattr(broker, "_filter_idempotent_orders", lambda orders, path: (orders, []))
    monkeypatch.setattr(broker, "_write_shadow_orders", lambda run_date, orders: f"outputs/shadow_orders/{run_date}.json")


def test_shadow_market_open_not_halted(monkeypatch):
    _mock_open_market(monkeypatch)

    now_et = dt.datetime(2026, 2, 10, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    result = broker.run_paper_day(
        run_date="2026-02-10",
        signals_path="signals/2026-02-10.json",
        ledger_path="paper/ledger.csv",
        trades_path="paper/trades.csv",
        config_path="paper/config_paper.json",
        now_et=now_et,
    )

    assert result["execution_status"] == "READY"
    assert result["market_status"] == "OPEN"



def test_shadow_market_closed_uses_prev_close_and_renders_trades(monkeypatch):
    _mock_open_market(monkeypatch)

    now_et = dt.datetime(2026, 2, 10, 8, 0, tzinfo=ZoneInfo("America/New_York"))
    result = broker.run_paper_day(
        run_date="2026-02-10",
        signals_path="signals/2026-02-10.json",
        ledger_path="paper/ledger.csv",
        trades_path="paper/trades.csv",
        config_path="paper/config_paper.json",
        now_et=now_et,
    )

    assert result["execution_status"] == "PLANNED"
    assert result["pricing_source"] == "PREV_CLOSE"
    assert result["shadow_orders"] == []

    payload = dqr.build_execution_email_payload(
        trade_date="2026-02-10",
        daily_snapshot={"risk_levels": [], "holdings": []},
        paper_summary=result,
    )
    _, body = build_execution_email_text(payload)
    assert "Pricing Source: PREV_CLOSE" in body
    assert "Pricing As-Of: 2026-02-09" in body
    assert "AAPL | BUY | 10" in body


def test_plan_only_open_generates_plan_without_orders(monkeypatch):
    _mock_open_market(monkeypatch)

    now_et = dt.datetime(2026, 2, 10, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    result = broker.run_paper_day(
        run_date="2026-02-10",
        signals_path="signals/2026-02-10.json",
        ledger_path="paper/ledger.csv",
        trades_path="paper/trades.csv",
        config_path="paper/config_paper.json",
        now_et=now_et,
        plan_only=True,
    )

    assert result["plan_only"] is True
    assert result["execution_status"] == "PLANNED"
    assert result["pricing_source"] == "PREV_CLOSE"
    assert result["shadow_orders"] == []

    payload = dqr.build_execution_email_payload(
        trade_date="2026-02-10",
        daily_snapshot={"risk_levels": [], "holdings": []},
        paper_summary=result,
    )
    _, body = build_execution_email_text(payload)
    assert "Planning email only — no orders were sent." in body
    assert "Execution Status: PLANNED — PLAN ONLY" in body
    assert "Pricing Source: PREV_CLOSE" in body
    assert "AAPL | BUY | 10" in body
    assert "MARKET CLOSED" not in body

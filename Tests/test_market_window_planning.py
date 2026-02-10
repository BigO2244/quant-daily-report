import datetime as dt
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

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


def test_build_rebalance_trades_non_fractional_sell_rounds_toward_zero():
    holdings = pd.DataFrame([{"ticker": "AAPL", "sleeve": "core", "shares": 10.0}])
    targets = pd.DataFrame([{"ticker": "AAPL", "target_weight": 0.55, "sleeve": "core"}])
    prices = pd.Series({"AAPL": 100.0})
    cfg = broker.PaperConfig(
        initial_equity=1000.0,
        benchmark_ticker="SPY",
        slippage_bps=0.0,
        allow_fractional=False,
        min_trade_dollars=1.0,
    )

    trades, _ = broker.build_rebalance_trades(
        holdings=holdings,
        targets=targets,
        prices=prices,
        total_equity=1000.0,
        starting_cash=0.0,
        target_cash_weight=0.0,
        cfg=cfg,
    )

    assert len(trades) == 1
    assert trades.iloc[0]["side"] == "SELL"
    assert trades.iloc[0]["shares"] == 5.0
    assert trades.iloc[0]["notional"] == 500.0


def test_build_rebalance_trades_non_fractional_drops_sub_share_delta_after_rounding():
    holdings = pd.DataFrame([{"ticker": "AAPL", "sleeve": "core", "shares": 0.2}])
    targets = pd.DataFrame([{"ticker": "AAPL", "target_weight": 0.11, "sleeve": "core"}])
    prices = pd.Series({"AAPL": 100.0})
    cfg = broker.PaperConfig(
        initial_equity=1000.0,
        benchmark_ticker="SPY",
        slippage_bps=0.0,
        allow_fractional=False,
        min_trade_dollars=1.0,
    )

    trades, _ = broker.build_rebalance_trades(
        holdings=holdings,
        targets=targets,
        prices=prices,
        total_equity=1000.0,
        starting_cash=1000.0,
        target_cash_weight=0.0,
        cfg=cfg,
    )

    assert trades.empty


def test_build_rebalance_trades_drops_below_min_trade_dollars():
    holdings = pd.DataFrame(columns=["ticker", "sleeve", "shares"])
    targets = pd.DataFrame([{"ticker": "AAPL", "target_weight": 1.0, "sleeve": "core"}])
    prices = pd.Series({"AAPL": 99.0})
    cfg = broker.PaperConfig(
        initial_equity=99.0,
        benchmark_ticker="SPY",
        slippage_bps=0.0,
        allow_fractional=False,
        min_trade_dollars=100.0,
    )

    trades, _ = broker.build_rebalance_trades(
        holdings=holdings,
        targets=targets,
        prices=prices,
        total_equity=99.0,
        starting_cash=99.0,
        target_cash_weight=0.0,
        cfg=cfg,
    )

    assert trades.empty


def test_build_rebalance_trades_min_trade_dollars_is_configurable():
    holdings = pd.DataFrame(columns=["ticker", "sleeve", "shares"])
    targets = pd.DataFrame([{"ticker": "AAPL", "target_weight": 1.0, "sleeve": "core"}])
    prices = pd.Series({"AAPL": 99.0})

    high_cfg = broker.PaperConfig(
        initial_equity=99.0,
        benchmark_ticker="SPY",
        slippage_bps=0.0,
        allow_fractional=False,
        min_trade_dollars=100.0,
    )
    low_cfg = broker.PaperConfig(
        initial_equity=99.0,
        benchmark_ticker="SPY",
        slippage_bps=0.0,
        allow_fractional=False,
        min_trade_dollars=90.0,
    )

    high_threshold_trades, _ = broker.build_rebalance_trades(
        holdings=holdings,
        targets=targets,
        prices=prices,
        total_equity=99.0,
        starting_cash=99.0,
        target_cash_weight=0.0,
        cfg=high_cfg,
    )
    low_threshold_trades, _ = broker.build_rebalance_trades(
        holdings=holdings,
        targets=targets,
        prices=prices,
        total_equity=99.0,
        starting_cash=99.0,
        target_cash_weight=0.0,
        cfg=low_cfg,
    )

    assert high_threshold_trades.empty
    assert len(low_threshold_trades) == 1
    assert low_threshold_trades.iloc[0]["notional"] == 99.0



def test_normalize_and_filter_executable_trades_applies_rounding_and_min_notional():
    trades = pd.DataFrame(
        [
            {"ticker": "AAPL", "side": "BUY", "shares": 0.8, "price": 200.0, "slippage_cost": 0.0, "notional": 160.0, "reason": "small"},
            {"ticker": "MSFT", "side": "BUY", "shares": 1.2, "price": 90.0, "slippage_cost": 0.0, "notional": 108.0, "reason": "small_after_round"},
            {"ticker": "NVDA", "side": "SELL", "shares": 2.9, "price": 60.0, "slippage_cost": 0.0, "notional": 174.0, "reason": "keep"},
        ]
    )
    cfg = broker.PaperConfig(
        initial_equity=10000.0,
        benchmark_ticker="SPY",
        slippage_bps=0.0,
        allow_fractional=False,
        min_trade_dollars=100.0,
    )

    out, stats = broker._normalize_and_filter_executable_trades(trades, cfg)

    assert list(out["ticker"]) == ["NVDA"]
    assert float(out.iloc[0]["shares"]) == 2.0
    assert float(out.iloc[0]["notional"]) == 120.0
    assert stats["raw"] == 3
    assert stats["dropped_zero_shares"] == 1
    assert stats["dropped_min_notional"] == 1
    assert stats["kept"] == 1


def test_apply_risk_guards_turnover_cap_scales_without_hard_stop_and_drops_dust():
    cfg = broker.PaperConfig(
        initial_equity=10000.0,
        benchmark_ticker="SPY",
        slippage_bps=0.0,
        allow_fractional=False,
        min_trade_dollars=350.0,
        max_turnover_pct=0.30,
        max_position_change_pct=1.0,
        risk_action="hard_stop",
    )
    trades = pd.DataFrame(
        [
            {"ticker": "AAPL", "side": "BUY", "shares": 10.0, "price": 200.0, "slippage_cost": 1.0, "notional": 2000.0, "reason": "rebalance"},
            {"ticker": "MSFT", "side": "BUY", "shares": 5.0, "price": 200.0, "slippage_cost": 0.5, "notional": 1000.0, "reason": "rebalance"},
            {"ticker": "TSLA", "side": "BUY", "shares": 4.0, "price": 100.0, "slippage_cost": 0.2, "notional": 400.0, "reason": "rebalance"},
        ]
    )

    guarded, blocked, hard_stop = broker.apply_risk_guards(trades=trades, equity=10000.0, cfg=cfg)

    assert hard_stop is False
    assert blocked == []

    expected_scale = 3000.0 / 3400.0
    assert guarded.attrs["risk_meta"]["turnover_scaled"] is True
    assert guarded.attrs["risk_meta"]["turnover_scale"] == pytest.approx(expected_scale, rel=1e-6)

    # Shares should be reduced by turnover scale, then rounded for executable orders.
    assert float(guarded.loc[guarded["ticker"] == "AAPL", "shares"].iloc[0]) == pytest.approx(10.0 * expected_scale, abs=1.0)
    assert float(guarded.loc[guarded["ticker"] == "MSFT", "shares"].iloc[0]) == pytest.approx(5.0 * expected_scale, abs=1.0)

    # Dust trade should be removed by min_trade_dollars after scaling + post-processing.
    assert set(guarded["ticker"].tolist()) == {"AAPL", "MSFT"}
    assert "TSLA" not in guarded["ticker"].tolist()

    normalized, stats = broker._normalize_and_filter_executable_trades(guarded, cfg)
    assert set(normalized["ticker"].tolist()) == {"AAPL", "MSFT"}
    assert stats["dropped_zero_shares"] == 0
    assert stats["dropped_min_notional"] == 0


def test_run_paper_day_raises_on_signal_date_mismatch(monkeypatch):
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
        lambda *args, **kwargs: (
            pd.DataFrame([{"ticker": "AAPL", "target_weight": 1.0, "sleeve": "core"}]),
            0.0,
            "2026-02-09",
            "2026-02-09",
        ),
    )

    now_et = dt.datetime(2026, 2, 10, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    with pytest.raises(RuntimeError, match="\\[HALT\\] signal_date_mismatch"):
        broker.run_paper_day(
            run_date="2026-02-10",
            signals_path="signals/2026-02-10.json",
            ledger_path="paper/ledger.csv",
            trades_path="paper/trades.csv",
            config_path="paper/config_paper.json",
            now_et=now_et,
        )

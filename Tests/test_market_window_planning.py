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


def _mock_open_market(monkeypatch, trading_mode: str = "shadow"):
    cfg = broker.PaperConfig(
        initial_equity=10000.0,
        benchmark_ticker="SPY",
        slippage_bps=0.0,
        allow_fractional=True,
        min_trade_dollars=1.0,
        trading_mode=trading_mode,
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
    # 2026-02-10 is a Tuesday: buys should still be allowed unless exit_only is explicit.
    assert result["exit_only"] is False
    assert len(result["execution_trades"]) == 1
    assert str(result["execution_trades"][0]["side"]).upper() == "BUY"


def test_shadow_idempotent_skip_prevents_same_day_reexecution(monkeypatch):
    _mock_open_market(monkeypatch)
    monkeypatch.setattr(
        broker,
        "_filter_idempotent_orders",
        lambda orders, path: ([], [str(o.get("order_id", "")) for o in orders]),
    )

    def _apply_noop_holdings(holdings, targets, trades, starting_cash):
        _ = targets
        assert trades is not None
        assert trades.empty
        return holdings.copy(), float(starting_cash)

    monkeypatch.setattr(broker, "apply_trades_to_holdings", _apply_noop_holdings)
    monkeypatch.setattr(
        broker,
        "mark_to_market",
        lambda holdings, prices: pd.DataFrame(
            columns=["ticker", "sleeve", "shares", "price", "market_value"]
        ),
    )

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
    assert int(result["num_trades"]) == 0
    assert float(result["turnover_notional"]) == 0.0
    assert result["execution_trades"] == []
    assert result["shadow_orders"] == []
    assert len(result["idempotent_skips"]) == 1


def test_shadow_can_use_explicit_precomputed_trade_plan(monkeypatch):
    _mock_open_market(monkeypatch)
    monkeypatch.setattr(
        broker,
        "build_rebalance_trades",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not rebuild trades")),
    )
    monkeypatch.setattr(
        broker,
        "apply_risk_guards",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not mutate exact plan")),
    )
    monkeypatch.setattr(
        broker,
        "_risk_controls_preflight",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not mutate exact plan")),
    )

    now_et = dt.datetime(2026, 2, 10, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    precomputed_trade_plan = [
        {
            "ticker": "AAPL",
            "side": "BUY",
            "shares": 7,
            "entry_price": 100.0,
            "notional": 700.0,
            "reason": "precomputed_exact_plan",
        }
    ]
    result = broker.run_paper_day(
        run_date="2026-02-10",
        signals_path="signals/2026-02-10.json",
        ledger_path="paper/ledger.csv",
        trades_path="paper/trades.csv",
        config_path="paper/config_paper.json",
        now_et=now_et,
        precomputed_trade_plan=precomputed_trade_plan,
    )

    assert result["execution_status"] == "READY"
    assert result["precomputed_trade_plan_used"] is True
    assert result["execution_trades"] == [
        {
            "ticker": "AAPL",
            "side": "BUY",
            "shares": 7.0,
            "price": 100.0,
            "notional": 700.0,
            "reason": "precomputed_exact_plan",
        }
    ]
    assert result["trade_plan"] == [
        {
            "ticker": "AAPL",
            "side": "BUY",
            "shares": 7.0,
            "price": 100.0,
            "slippage_cost": 0.0,
            "notional": 700.0,
            "reason": "precomputed_exact_plan",
            "quantity": 7.0,
        }
    ]


def test_alpaca_mode_submits_orders_and_uses_broker_state(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _mock_open_market(monkeypatch, trading_mode="alpaca")

    class _StubAlpaca:
        paper = True

        @classmethod
        def from_env(cls):
            return cls()

        def find_order_by_client_id(self, client_id):
            _ = client_id
            return None

        def submit_market_order(self, symbol, qty, side, client_order_id, tif="day"):
            _ = (symbol, qty, side, client_order_id, tif)
            return {
                "id": "alpaca-oid-1",
                "status": "accepted",
                "submitted_at": "2026-02-10T10:00:00-05:00",
            }

        def submit_limit_order(
            self, symbol, qty, side, limit_price, client_order_id, tif="day"
        ):
            _ = (symbol, qty, side, limit_price, client_order_id, tif)
            return {
                "id": "alpaca-oid-limit-1",
                "status": "accepted",
                "submitted_at": "2026-02-10T10:00:00-05:00",
            }

        def get_account(self):
            return {"cash": "9000.0", "equity": "10050.0", "buying_power": "18000.0"}

        def get_positions(self):
            return [
                {
                    "symbol": "AAPL",
                    "qty": "10",
                    "current_price": "105.0",
                    "market_value": "1050.0",
                }
            ]

    monkeypatch.setattr(broker, "AlpacaBroker", _StubAlpaca)

    def _unexpected_apply_trades(*args, **kwargs):
        raise AssertionError("apply_trades_to_holdings should not be used in alpaca mode")

    monkeypatch.setattr(broker, "apply_trades_to_holdings", _unexpected_apply_trades)

    now_et = dt.datetime(2026, 2, 10, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    result = broker.run_paper_day(
        run_date="2026-02-10",
        signals_path="signals/2026-02-10.json",
        ledger_path="paper/ledger.csv",
        trades_path="paper/trades.csv",
        config_path="paper/config_paper.json",
        now_et=now_et,
    )

    assert result["trading_mode"] == "alpaca"
    assert result["execution_status"] == "READY"
    assert int(result["num_trades"]) == 1
    assert float(result["cash"]) == pytest.approx(9000.0, abs=1e-9)
    assert float(result["total_equity"]) == pytest.approx(10050.0, abs=1e-9)
    assert float(result["invested_dollars"]) == pytest.approx(1050.0, abs=1e-9)
    assert len(result["alpaca_submissions"]) == 1
    assert result["alpaca_orders_path"].endswith("orders_2026-02-10.csv")
    assert (tmp_path / result["alpaca_orders_path"]).exists()


def test_alpaca_mode_invariant_raises_when_executable_but_zero_submit_attempts(
    monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    _mock_open_market(monkeypatch, trading_mode="alpaca")

    class _StubAlpaca:
        paper = True

        @classmethod
        def from_env(cls):
            return cls()

        def find_order_by_client_id(self, client_id):
            _ = client_id
            return {"id": "existing-order", "status": "accepted", "submitted_at": "2026-02-10T10:00:00-05:00"}

        def submit_market_order(self, symbol, qty, side, client_order_id, tif="day"):
            _ = (symbol, qty, side, client_order_id, tif)
            raise AssertionError("submit_market_order should not be called in this test")

        def submit_limit_order(
            self, symbol, qty, side, limit_price, client_order_id, tif="day"
        ):
            _ = (symbol, qty, side, limit_price, client_order_id, tif)
            raise AssertionError("submit_limit_order should not be called in this test")

        def get_account(self):
            return {"id": "acct", "status": "ACTIVE", "cash": "9000.0", "equity": "10050.0", "buying_power": "18000.0"}

        def get_positions(self):
            return []

    monkeypatch.setattr(broker, "AlpacaBroker", _StubAlpaca)

    now_et = dt.datetime(2026, 2, 10, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    with pytest.raises(
        RuntimeError,
        match="\\[INVARIANT\\] ALPACA mode had executable trades but 0 submit attempts",
    ):
        broker.run_paper_day(
            run_date="2026-02-10",
            signals_path="signals/2026-02-10.json",
            ledger_path="paper/ledger.csv",
            trades_path="paper/trades.csv",
            config_path="paper/config_paper.json",
            now_et=now_et,
        )


def test_alpaca_mode_same_day_sent_ledger_lock_blocks_submission(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _mock_open_market(monkeypatch, trading_mode="alpaca")

    sent_ledger = tmp_path / "outputs" / "orders_sent" / "orders_sent.csv"
    sent_ledger.parent.mkdir(parents=True, exist_ok=True)
    sent_ledger.write_text(
        "\n".join(
            [
                "date,run_id,order_id,ticker,side,client_order_id,alpaca_order_id,status",
                "2026-02-10,prior-run,2026-02-10:main:v1:AAPL:BUY,AAPL,BUY,cid-1,alpaca-1,accepted",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    class _StubAlpaca:
        paper = True

        @classmethod
        def from_env(cls):
            raise AssertionError("from_env should not be called when same-day sent ledger lock is active")

    monkeypatch.setattr(broker, "AlpacaBroker", _StubAlpaca)

    now_et = dt.datetime(2026, 2, 10, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    result = broker.run_paper_day(
        run_date="2026-02-10",
        signals_path="signals/2026-02-10.json",
        ledger_path="paper/ledger.csv",
        trades_path="paper/trades.csv",
        config_path="paper/config_paper.json",
        now_et=now_et,
    )

    assert result["trading_mode"] == "alpaca"
    assert result["execution_status"] == "HALTED"
    assert result["execution_enabled"] is False
    assert result["alpaca_submissions"] == []
    assert result["alpaca_submission_summary"]["same_day_submission_lock"] is True
    assert result["alpaca_submission_summary"]["same_day_recorded_orders"] == 1
    assert any("same_day_submission_lock:2026-02-10:recorded_orders=1" in reason for reason in result["blocked_reasons"])





def test_market_closed_validation_does_not_log_market_open(monkeypatch, caplog):
    _mock_open_market(monkeypatch)
    caplog.set_level("INFO")

    now_et = dt.datetime(2026, 2, 10, 8, 0, tzinfo=ZoneInfo("America/New_York"))
    result = broker.run_paper_day(
        run_date="2026-02-10",
        signals_path="signals/2026-02-10.json",
        ledger_path="paper/ledger.csv",
        trades_path="paper/trades.csv",
        config_path="paper/config_paper.json",
        now_et=now_et,
    )

    assert result["market_guard"]["is_trading_session"] is False
    assert any("market_guard:" in reason for reason in result["blocked_reasons"])
    assert "reason=market_open" not in caplog.text
    assert "market_closed_or_not_session" in caplog.text
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


def test_build_rebalance_trades_whole_share_cash_sweep_improves_capital_usage():
    holdings = pd.DataFrame(columns=["ticker", "sleeve", "shares"])
    targets = pd.DataFrame(
        [
            {"ticker": "AAPL", "target_weight": 0.20, "sleeve": "core"},
            {"ticker": "MSFT", "target_weight": 0.15, "sleeve": "core"},
            {"ticker": "NVDA", "target_weight": 0.10, "sleeve": "core"},
        ]
    )
    prices = pd.Series({"AAPL": 501.0, "MSFT": 499.0, "NVDA": 497.0})
    cfg = broker.PaperConfig(
        initial_equity=10000.0,
        benchmark_ticker="SPY",
        slippage_bps=0.0,
        allow_fractional=False,
        min_trade_dollars=100.0,
    )

    trades, meta = broker.build_rebalance_trades(
        holdings=holdings,
        targets=targets,
        prices=prices,
        total_equity=10000.0,
        starting_cash=10000.0,
        target_cash_weight=0.55,
        cfg=cfg,
    )

    buys = trades[trades["side"].astype(str).str.upper() == "BUY"].copy()
    spent = float(buys["notional"].sum()) if not buys.empty else 0.0
    target_investable = float(meta.get("target_investable_dollars", 0.0))
    residual = max(0.0, target_investable - spent)

    # Naive whole-share allocation baseline before sweep:
    # floor(target_dollars / price) * price per ticker.
    naive_spent = (
        int((0.20 * 10000.0) // 501.0) * 501.0
        + int((0.15 * 10000.0) // 499.0) * 499.0
        + int((0.10 * 10000.0) // 497.0) * 497.0
    )

    assert spent >= naive_spent
    assert int(meta.get("cash_sweep_added_shares", 0)) >= 1
    assert residual <= 100.0 + 1e-9



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
    assert guarded.attrs["risk_meta"]["turnover_cap_scope"] == "buys_only"
    assert guarded.attrs["risk_meta"]["turnover_requested"] == pytest.approx(3400.0)
    assert guarded.attrs["risk_meta"]["turnover_requested_buys"] == pytest.approx(3400.0)
    assert guarded.attrs["risk_meta"]["turnover_requested_sells"] == pytest.approx(0.0)
    assert guarded.attrs["risk_meta"]["turnover_requested_total"] == pytest.approx(3400.0)
    assert guarded.attrs["risk_meta"]["turnover_scale"] == pytest.approx(expected_scale, rel=1e-6)

    # BUY shares should be reduced by turnover scale, then rounded for executable orders.
    assert float(guarded.loc[guarded["ticker"] == "AAPL", "shares"].iloc[0]) == pytest.approx(10.0 * expected_scale, abs=1.0)
    assert float(guarded.loc[guarded["ticker"] == "MSFT", "shares"].iloc[0]) == pytest.approx(5.0 * expected_scale, abs=1.0)

    # Dust trade should be removed by min_trade_dollars after scaling + post-processing.
    assert set(guarded["ticker"].tolist()) == {"AAPL", "MSFT"}
    assert "TSLA" not in guarded["ticker"].tolist()

    normalized, stats = broker._normalize_and_filter_executable_trades(guarded, cfg)
    assert set(normalized["ticker"].tolist()) == {"AAPL", "MSFT"}
    assert stats["dropped_zero_shares"] == 0
    assert stats["dropped_min_notional"] == 0


def test_apply_risk_guards_turnover_cap_exempts_sells_and_preserves_full_exits():
    cfg = broker.PaperConfig(
        initial_equity=10000.0,
        benchmark_ticker="SPY",
        slippage_bps=0.0,
        allow_fractional=False,
        min_trade_dollars=100.0,
        max_turnover_pct=0.30,
        max_position_change_pct=1.0,
        risk_action="hard_stop",
    )
    trades = pd.DataFrame(
        [
            {"ticker": "MU", "side": "SELL", "shares": 2.0, "price": 200.0, "slippage_cost": 0.0, "notional": 400.0, "reason": "removed_from_targets"},
            {"ticker": "AAPL", "side": "BUY", "shares": 10.0, "price": 200.0, "slippage_cost": 0.0, "notional": 2000.0, "reason": "rebalance"},
            {"ticker": "MSFT", "side": "BUY", "shares": 10.0, "price": 200.0, "slippage_cost": 0.0, "notional": 2000.0, "reason": "rebalance"},
        ]
    )

    guarded, blocked, hard_stop = broker.apply_risk_guards(trades=trades, equity=10000.0, cfg=cfg)

    assert hard_stop is False
    assert blocked == []
    assert guarded.attrs["risk_meta"]["turnover_scaled"] is True
    assert guarded.attrs["risk_meta"]["turnover_cap_scope"] == "buys_only"
    assert guarded.attrs["risk_meta"]["turnover_requested"] == pytest.approx(4000.0)
    assert guarded.attrs["risk_meta"]["turnover_requested_buys"] == pytest.approx(4000.0)
    assert guarded.attrs["risk_meta"]["turnover_requested_sells"] == pytest.approx(400.0)
    assert guarded.attrs["risk_meta"]["turnover_requested_total"] == pytest.approx(4400.0)
    assert guarded.attrs["risk_meta"]["turnover_scale"] == pytest.approx(0.75, rel=1e-6)

    mu_row = guarded.loc[guarded["ticker"] == "MU"].iloc[0]
    assert str(mu_row["side"]).upper() == "SELL"
    assert float(mu_row["shares"]) == 2.0
    assert float(mu_row["notional"]) == 400.0
    assert str(mu_row["reason"]) == "removed_from_targets"

    assert float(guarded.loc[guarded["ticker"] == "AAPL", "shares"].iloc[0]) == 7.0
    assert float(guarded.loc[guarded["ticker"] == "MSFT", "shares"].iloc[0]) == 7.0


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

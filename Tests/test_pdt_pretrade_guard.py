import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from brokers.alpaca_broker import (
    BROKER_REJECT_PDT,
    CASH_REBALANCE_INCOMPLETE,
    EXECUTION_OUTCOME_PARTIAL_BROKER_ABORT,
)
import paper.paper_broker as broker


def _mock_open_market(monkeypatch, *, trades_df: pd.DataFrame) -> None:
    cfg = broker.PaperConfig(
        initial_equity=10000.0,
        benchmark_ticker="SPY",
        slippage_bps=0.0,
        allow_fractional=True,
        min_trade_dollars=1.0,
        cash_buffer_bps=0.0,
        trading_mode="alpaca",
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
            pd.DataFrame(
                [
                    {"ticker": "AEP", "target_weight": 0.4, "sleeve": "core"},
                    {"ticker": "MSFT", "target_weight": 0.6, "sleeve": "core"},
                ]
            ),
            0.0,
            "2026-03-16",
            "2026-03-13",
        ),
    )
    monkeypatch.setattr(
        broker,
        "fetch_open_prices_yfinance",
        lambda tickers, run_date: pd.DataFrame(
            [
                {"ticker": "AEP", "open": 100.0, "price_date": run_date},
                {"ticker": "MSFT", "open": 100.0, "price_date": run_date},
                {"ticker": "SPY", "open": 500.0, "price_date": run_date},
            ]
        ),
    )
    monkeypatch.setattr(
        broker,
        "fetch_prev_closes_yfinance",
        lambda tickers, asof_date: pd.DataFrame(
            [
                {"ticker": "AEP", "prev_close": 99.0, "price_date": asof_date},
                {"ticker": "MSFT", "prev_close": 99.0, "price_date": asof_date},
                {"ticker": "SPY", "prev_close": 499.0, "price_date": asof_date},
            ]
        ),
    )
    monkeypatch.setattr(
        broker,
        "validate_open_window",
        lambda **kwargs: (
            True,
            [],
            {"blocked_tickers": {}, "asof_date": "2026-03-13", "cutoff_date": "2026-03-13"},
        ),
    )
    monkeypatch.setattr(
        broker,
        "build_rebalance_trades",
        lambda **kwargs: (
            trades_df.copy(),
            {"target_investable_dollars": 10000.0, "scaled_tickers": [], "overspend_prevented": False},
        ),
    )
    monkeypatch.setattr(
        broker,
        "apply_risk_guards",
        lambda trades, equity_prev, cfg: (trades, [], False),
    )
    monkeypatch.setattr(broker, "append_csv", lambda df, path: None)


class _PdtRiskAlpaca:
    paper = True

    def __init__(self) -> None:
        self.submitted = []
        self._account_calls = 0
        self._positions_calls = 0

    @classmethod
    def from_env(cls):
        return cls()

    def find_order_by_client_id(self, client_id):
        return None

    def submit_market_order(self, symbol, qty, side, client_order_id, tif="day"):
        self.submitted.append((side, symbol, float(qty), client_order_id))
        return {
            "id": f"alpaca-{len(self.submitted)}",
            "status": "accepted",
            "submitted_at": "2026-03-16T09:40:00-04:00",
        }

    def submit_limit_order(self, symbol, qty, side, limit_price, client_order_id, tif="day"):
        raise AssertionError("limit orders not expected in PDT pretrade test")

    def get_account(self):
        self._account_calls += 1
        if self._account_calls <= 2:
            return {
                "cash": "2500.0",
                "equity": "25000.0",
                "buying_power": "5000.0",
                "status": "ACTIVE",
                "raw": {
                    "pattern_day_trader": True,
                    "daytrade_count": 3,
                    "daytrading_buying_power": "0",
                },
            }
        return {
            "cash": "2400.0",
            "equity": "25010.0",
            "buying_power": "4900.0",
            "status": "ACTIVE",
            "raw": {
                "pattern_day_trader": True,
                "daytrade_count": 3,
                "daytrading_buying_power": "0",
            },
        }

    def get_positions(self):
        self._positions_calls += 1
        if self._positions_calls == 1:
            return [
                {"symbol": "AEP", "qty": "1", "current_price": "100.0", "market_value": "100.0"},
            ]
        return [
            {"symbol": "AEP", "qty": "1", "current_price": "100.0", "market_value": "100.0"},
            {"symbol": "MSFT", "qty": "2", "current_price": "100.0", "market_value": "200.0"},
        ]


def test_run_paper_day_defers_pdt_risk_sells_before_submit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RUN_OUTPUT_ROOT", str(tmp_path / "outputs" / "runs" / "run-pdt"))
    monkeypatch.setenv("MODE", "alpaca")
    monkeypatch.setenv("TRADING_MODE", "alpaca")

    trades = pd.DataFrame(
        [
            {"ticker": "AEP", "side": "SELL", "shares": 1.0, "quantity": 1.0, "price": 100.0, "notional": 100.0, "slippage_cost": 0.0, "reason": "trim"},
            {"ticker": "MSFT", "side": "BUY", "shares": 2.0, "quantity": 2.0, "price": 100.0, "notional": 200.0, "slippage_cost": 0.0, "reason": "add"},
        ]
    )
    _mock_open_market(monkeypatch, trades_df=trades)

    fake = _PdtRiskAlpaca()
    monkeypatch.setattr(_PdtRiskAlpaca, "from_env", classmethod(lambda cls: fake), raising=False)
    monkeypatch.setattr(broker, "AlpacaBroker", _PdtRiskAlpaca)

    result = broker.run_paper_day(
        run_date="2026-03-16",
        signals_path="signals.json",
        ledger_path="ledger.csv",
        trades_path="trades.csv",
        config_path="config.json",
        now_et=dt.datetime(2026, 3, 16, 9, 40, tzinfo=ZoneInfo("America/New_York")),
    )

    assert [side for side, _, _, _ in fake.submitted] == ["BUY"]
    assert [symbol for _, symbol, _, _ in fake.submitted] == ["MSFT"]
    assert "pdt_pretrade_block:AEP:broker_pdt_zero_daytrading_buying_power" in result["blocked_reasons"]
    assert result["pdt_pretrade"]["broker_pdt_risk_status"] == "BLOCKED"
    assert result["pdt_pretrade"]["deferred_symbols"] == ["AEP"]
    assert "deferred planned sells before submit" in str(result["pdt_pretrade"]["broker_pdt_warning_message"])
    assert [trade["ticker"] for trade in result["execution_trades"]] == ["MSFT"]
    intended_path = Path(tmp_path / "outputs" / "runs" / "run-pdt" / "broker" / "intended_orders_2026-03-16.json")
    assert intended_path.exists()


class _PartialPdtRejectAlpaca:
    paper = True

    def __init__(self) -> None:
        self.submitted = []
        self._positions_calls = 0
        self._account_calls = 0
        self._orders_by_client_id = {}

    @classmethod
    def from_env(cls):
        return cls()

    def find_order_by_client_id(self, client_order_id):
        return self._orders_by_client_id.get(client_order_id)

    def submit_market_order(self, symbol, qty, side, client_order_id, tif="day"):
        self.submitted.append((side, symbol, float(qty), client_order_id))
        if symbol == "AEP":
            raise Exception(
                '{"code":40310000,"message":"trade denied due to pattern day trading protection"}'
            )
        order = {
            "id": f"alpaca-{len(self.submitted)}",
            "status": "accepted",
            "submitted_at": "2026-03-16T09:40:00-04:00",
        }
        self._orders_by_client_id[client_order_id] = order
        return order

    def submit_limit_order(self, symbol, qty, side, limit_price, client_order_id, tif="day"):
        raise AssertionError("limit orders not expected in PDT reject test")

    def get_account(self):
        self._account_calls += 1
        if self._account_calls == 1:
            return {
                "cash": "1000.0",
                "equity": "25000.0",
                "buying_power": "5000.0",
                "status": "ACTIVE",
                "raw": {
                    "pattern_day_trader": False,
                    "daytrade_count": 1,
                    "daytrading_buying_power": "5000",
                },
            }
        return {
            "cash": "3000.0",
            "equity": "25000.0",
            "buying_power": "7000.0",
            "status": "ACTIVE",
            "raw": {
                "pattern_day_trader": False,
                "daytrade_count": 1,
                "daytrading_buying_power": "7000",
            },
        }

    def get_positions(self):
        self._positions_calls += 1
        if self._positions_calls == 1:
            return [
                {"symbol": "DELL", "qty": "1", "current_price": "100.0", "market_value": "100.0"},
                {"symbol": "LRCX", "qty": "1", "current_price": "100.0", "market_value": "100.0"},
                {"symbol": "AEP", "qty": "1", "current_price": "100.0", "market_value": "100.0"},
            ]
        return [
            {"symbol": "AEP", "qty": "1", "current_price": "100.0", "market_value": "100.0"},
        ]


def test_run_paper_day_preserves_partial_execution_on_pdt_reject(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RUN_OUTPUT_ROOT", str(tmp_path / "outputs" / "runs" / "run-partial-pdt"))
    monkeypatch.setenv("MODE", "alpaca")
    monkeypatch.setenv("TRADING_MODE", "alpaca")

    trades = pd.DataFrame(
        [
            {"ticker": "DELL", "side": "SELL", "shares": 1.0, "quantity": 1.0, "price": 100.0, "notional": 100.0, "slippage_cost": 0.0, "reason": "trim"},
            {"ticker": "LRCX", "side": "SELL", "shares": 1.0, "quantity": 1.0, "price": 100.0, "notional": 100.0, "slippage_cost": 0.0, "reason": "trim"},
            {"ticker": "AEP", "side": "SELL", "shares": 1.0, "quantity": 1.0, "price": 100.0, "notional": 100.0, "slippage_cost": 0.0, "reason": "trim"},
            {"ticker": "MSFT", "side": "BUY", "shares": 1.0, "quantity": 1.0, "price": 100.0, "notional": 100.0, "slippage_cost": 0.0, "reason": "add"},
        ]
    )
    _mock_open_market(monkeypatch, trades_df=trades)
    monkeypatch.setattr(
        broker,
        "read_latest_holdings_from_ledger",
        lambda path: (
            pd.DataFrame(
                [
                    {"ticker": "DELL", "sleeve": "main", "shares": 1.0},
                    {"ticker": "LRCX", "sleeve": "main", "shares": 1.0},
                    {"ticker": "AEP", "sleeve": "main", "shares": 1.0},
                ]
            ),
            1000.0,
            25000.0,
            "2026-03-15",
        ),
    )

    fake = _PartialPdtRejectAlpaca()
    monkeypatch.setattr(_PartialPdtRejectAlpaca, "from_env", classmethod(lambda cls: fake), raising=False)
    monkeypatch.setattr(broker, "AlpacaBroker", _PartialPdtRejectAlpaca)

    result = broker.run_paper_day(
        run_date="2026-03-16",
        signals_path="signals.json",
        ledger_path="ledger.csv",
        trades_path="trades.csv",
        config_path="config.json",
        now_et=dt.datetime(2026, 3, 16, 9, 40, tzinfo=ZoneInfo("America/New_York")),
    )

    assert result["execution_status"] == "HALTED"
    assert result["execution_outcome"] == EXECUTION_OUTCOME_PARTIAL_BROKER_ABORT
    assert result["broker_reject_status"] == BROKER_REJECT_PDT
    assert result["cash_rebalance_status"] == CASH_REBALANCE_INCOMPLETE
    assert result["halt_remaining_buys"] is True
    assert result["alpaca_submission_summary"]["submit_success"] == 2
    assert result["alpaca_submission_summary"]["submit_failed"] == 1
    assert result["trade_plan"] and len(result["trade_plan"]) == 4
    assert set(result["execution_submitted_symbols"]) == {"DELL", "LRCX"}
    assert {trade["ticker"] for trade in result["execution_trades"]} == {"DELL", "LRCX"}
    assert [side for side, _, _, _ in fake.submitted] == ["SELL", "SELL", "SELL"]
    assert all(side != "BUY" for side, _, _, _ in fake.submitted)

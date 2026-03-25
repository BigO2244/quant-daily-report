import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import paper.paper_broker as broker


def _mock_open_market(monkeypatch, *, trades_df: pd.DataFrame, trade_meta: dict, holdings=None):
    cfg = broker.PaperConfig(
        initial_equity=10000.0,
        benchmark_ticker="SPY",
        slippage_bps=0.0,
        allow_fractional=True,
        min_trade_dollars=1.0,
        cash_buffer_bps=0.0,
        trading_mode="alpaca",
    )
    if holdings is None:
        holdings = pd.DataFrame(columns=["ticker", "sleeve", "shares"])
    monkeypatch.setattr(broker, "load_config", lambda path: cfg)
    monkeypatch.setattr(
        broker,
        "read_latest_holdings_from_ledger",
        lambda path: (holdings.copy(), 10000.0, 10000.0, ""),
    )
    monkeypatch.setattr(
        broker,
        "load_targets",
        lambda *args, **kwargs: (
            pd.DataFrame(
                [
                    {"ticker": "AAA", "target_weight": 0.2, "sleeve": "core"},
                    {"ticker": "BBB", "target_weight": 0.4, "sleeve": "core"},
                    {"ticker": "CCC", "target_weight": 0.4, "sleeve": "core"},
                ]
            ),
            0.0,
            "2026-03-11",
            "2026-03-10",
        ),
    )
    monkeypatch.setattr(
        broker,
        "fetch_open_prices_yfinance",
        lambda tickers, run_date: pd.DataFrame(
            [
                {"ticker": "AAA", "open": 100.0, "price_date": run_date},
                {"ticker": "BBB", "open": 90.0, "price_date": run_date},
                {"ticker": "CCC", "open": 100.0, "price_date": run_date},
                {"ticker": "SPY", "open": 500.0, "price_date": run_date},
            ]
        ),
    )
    monkeypatch.setattr(
        broker,
        "fetch_prev_closes_yfinance",
        lambda tickers, asof_date: pd.DataFrame(
            [{"ticker": "AAA", "prev_close": 99.0, "price_date": asof_date}]
        ),
    )
    monkeypatch.setattr(
        broker,
        "validate_open_window",
        lambda **kwargs: (
            True,
            [],
            {"blocked_tickers": {}, "asof_date": "2026-03-10", "cutoff_date": "2026-03-10"},
        ),
    )
    monkeypatch.setattr(
        broker,
        "build_rebalance_trades",
        lambda **kwargs: (trades_df.copy(), dict(trade_meta)),
    )
    monkeypatch.setattr(
        broker,
        "apply_risk_guards",
        lambda trades, equity_prev, cfg: (trades, [], False),
    )
    monkeypatch.setattr(
        broker,
        "append_csv",
        lambda df, path: None,
    )


class _SequencedAlpaca:
    paper = True

    def __init__(self, *, account_sequence, positions_sequence):
        self._account_sequence = list(account_sequence)
        self._positions_sequence = list(positions_sequence)
        self.submitted = []
        self.find_calls = []

    def find_order_by_client_id(self, client_id):
        self.find_calls.append(client_id)
        return None

    def submit_market_order(self, symbol, qty, side, client_order_id, tif="day"):
        self.submitted.append((side, symbol, float(qty), client_order_id))
        return {
            "id": f"alpaca-{len(self.submitted)}",
            "status": "accepted",
            "submitted_at": "2026-03-11T10:00:00-05:00",
        }

    def submit_limit_order(self, symbol, qty, side, limit_price, client_order_id, tif="day"):
        raise AssertionError("limit orders not expected in phase 3 tests")

    def get_account(self):
        if self._account_sequence:
            return self._account_sequence.pop(0)
        return {"cash": "0", "equity": "0", "buying_power": "0", "status": "ACTIVE"}

    def get_positions(self):
        if self._positions_sequence:
            return self._positions_sequence.pop(0)
        return []


def test_phase3_sell_first_postsell_snapshot_and_buy_budget(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RUN_OUTPUT_ROOT", str(tmp_path / "outputs" / "runs" / "run-p3"))
    trades = pd.DataFrame(
        [
            {"ticker": "AAA", "side": "SELL", "shares": 1.0, "quantity": 1.0, "price": 100.0, "notional": 100.0, "slippage_cost": 0.0, "reason": "trim"},
            {"ticker": "BBB", "side": "BUY", "shares": 2.0, "quantity": 2.0, "price": 90.0, "notional": 180.0, "slippage_cost": 0.0, "reason": "add"},
        ]
    )
    _mock_open_market(
        monkeypatch,
        trades_df=trades,
        trade_meta={"target_investable_dollars": 10000.0, "scaled_tickers": [], "overspend_prevented": False},
        holdings=pd.DataFrame([{"ticker": "AAA", "sleeve": "core", "shares": 1.0}]),
    )
    fake = _SequencedAlpaca(
        account_sequence=[
            {"cash": "2000.0", "equity": "10000.0", "buying_power": "2000.0", "status": "ACTIVE"},
            {"cash": "2200.0", "equity": "10100.0", "buying_power": "2200.0", "status": "ACTIVE"},
            {"cash": "2020.0", "equity": "10120.0", "buying_power": "2020.0", "status": "ACTIVE"},
        ],
        positions_sequence=[
            [],
            [{"symbol": "BBB", "qty": "2", "current_price": "90.0", "market_value": "180.0"}],
        ],
    )
    monkeypatch.setattr(_SequencedAlpaca, "from_env", classmethod(lambda cls: fake), raising=False)
    monkeypatch.setattr(broker, "AlpacaBroker", _SequencedAlpaca)

    result = broker.run_paper_day(
        run_date="2026-03-11",
        signals_path="signals.json",
        ledger_path="ledger.csv",
        trades_path="trades.csv",
        config_path="config.json",
        now_et=dt.datetime(2026, 3, 11, 10, 0, tzinfo=ZoneInfo("America/New_York")),
    )

    assert [side for side, _, _, _ in fake.submitted] == ["SELL", "BUY"]
    assert result["sell_phase_status"] == "COMPLETED"
    assert float(result["postsell_cash_confirmed"]) == pytest.approx(2200.0)
    assert float(result["buy_budget_computed"]) == pytest.approx(1200.0)
    postsell_path = Path(result["postsell_account_snapshot_path"])
    assert postsell_path.exists()
    payload = json.loads(postsell_path.read_text(encoding="utf-8"))
    assert float(payload["cash"]) == pytest.approx(2200.0)
    assert int(result["alpaca_submission_summary"]["sell_phase_submitted"]) == 1
    assert int(result["alpaca_submission_summary"]["buy_phase_submitted"]) == 1


def test_phase3_buy_budget_reduces_buys_when_confirmed_cash_is_lower(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RUN_OUTPUT_ROOT", str(tmp_path / "outputs" / "runs" / "run-p3-budget"))
    trades = pd.DataFrame(
        [
            {"ticker": "AAA", "side": "SELL", "shares": 1.0, "quantity": 1.0, "price": 100.0, "notional": 100.0, "slippage_cost": 0.0, "reason": "trim"},
            {"ticker": "BBB", "side": "BUY", "shares": 10.0, "quantity": 10.0, "price": 90.0, "notional": 900.0, "slippage_cost": 0.0, "reason": "add"},
            {"ticker": "CCC", "side": "BUY", "shares": 3.0, "quantity": 3.0, "price": 100.0, "notional": 300.0, "slippage_cost": 0.0, "reason": "add"},
        ]
    )
    _mock_open_market(
        monkeypatch,
        trades_df=trades,
        trade_meta={"target_investable_dollars": 10000.0, "scaled_tickers": [], "overspend_prevented": False},
        holdings=pd.DataFrame([{"ticker": "AAA", "sleeve": "core", "shares": 1.0}]),
    )
    fake = _SequencedAlpaca(
        account_sequence=[
            {"cash": "2500.0", "equity": "10000.0", "buying_power": "2500.0", "status": "ACTIVE"},
            {"cash": "1900.0", "equity": "10100.0", "buying_power": "1900.0", "status": "ACTIVE"},
            {"cash": "1000.0", "equity": "10100.0", "buying_power": "1000.0", "status": "ACTIVE"},
        ],
        positions_sequence=[
            [],
            [{"symbol": "BBB", "qty": "10", "current_price": "90.0", "market_value": "900.0"}],
        ],
    )
    monkeypatch.setattr(_SequencedAlpaca, "from_env", classmethod(lambda cls: fake), raising=False)
    monkeypatch.setattr(broker, "AlpacaBroker", _SequencedAlpaca)

    result = broker.run_paper_day(
        run_date="2026-03-11",
        signals_path="signals.json",
        ledger_path="ledger.csv",
        trades_path="trades.csv",
        config_path="config.json",
        now_et=dt.datetime(2026, 3, 11, 10, 0, tzinfo=ZoneInfo("America/New_York")),
    )

    submitted_symbols = [symbol for _, symbol, _, _ in fake.submitted]
    assert submitted_symbols == ["AAA", "BBB"]
    assert int(result["alpaca_submission_summary"]["budget_skipped_orders"]) == 1
    assert [o["ticker"] for o in result["budget_skipped_orders"]] == ["CCC"]


def test_phase3_exact_precomputed_plan_bypasses_buy_budget_clipping(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RUN_OUTPUT_ROOT", str(tmp_path / "outputs" / "runs" / "run-p3-exact"))
    trades = pd.DataFrame(
        [
            {"ticker": "AAA", "side": "SELL", "shares": 1.0, "quantity": 1.0, "price": 100.0, "notional": 100.0, "slippage_cost": 0.0, "reason": "trim"},
            {"ticker": "BBB", "side": "BUY", "shares": 10.0, "quantity": 10.0, "price": 90.0, "notional": 900.0, "slippage_cost": 0.0, "reason": "add"},
            {"ticker": "CCC", "side": "BUY", "shares": 3.0, "quantity": 3.0, "price": 100.0, "notional": 300.0, "slippage_cost": 0.0, "reason": "add"},
        ]
    )
    _mock_open_market(
        monkeypatch,
        trades_df=trades,
        trade_meta={"target_investable_dollars": 10000.0, "scaled_tickers": [], "overspend_prevented": False},
        holdings=pd.DataFrame([{"ticker": "AAA", "sleeve": "core", "shares": 1.0}]),
    )
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
    fake = _SequencedAlpaca(
        account_sequence=[
            {"cash": "2500.0", "equity": "10000.0", "buying_power": "2500.0", "status": "ACTIVE"},
            {"cash": "1900.0", "equity": "10100.0", "buying_power": "1900.0", "status": "ACTIVE"},
            {"cash": "700.0", "equity": "10100.0", "buying_power": "700.0", "status": "ACTIVE"},
        ],
        positions_sequence=[
            [],
            [
                {"symbol": "BBB", "qty": "10", "current_price": "90.0", "market_value": "900.0"},
                {"symbol": "CCC", "qty": "3", "current_price": "100.0", "market_value": "300.0"},
            ],
        ],
    )
    monkeypatch.setattr(_SequencedAlpaca, "from_env", classmethod(lambda cls: fake), raising=False)
    monkeypatch.setattr(broker, "AlpacaBroker", _SequencedAlpaca)

    result = broker.run_paper_day(
        run_date="2026-03-11",
        signals_path="signals.json",
        ledger_path="ledger.csv",
        trades_path="trades.csv",
        config_path="config.json",
        now_et=dt.datetime(2026, 3, 11, 10, 0, tzinfo=ZoneInfo("America/New_York")),
        precomputed_trade_plan=[
            {"ticker": "AAA", "side": "SELL", "shares": 1.0, "entry_price": 100.0, "notional": 100.0, "reason": "trim"},
            {"ticker": "BBB", "side": "BUY", "shares": 10.0, "entry_price": 90.0, "notional": 900.0, "reason": "add"},
            {"ticker": "CCC", "side": "BUY", "shares": 3.0, "entry_price": 100.0, "notional": 300.0, "reason": "add"},
        ],
    )

    submitted_symbols = [symbol for _, symbol, _, _ in fake.submitted]
    assert submitted_symbols == ["AAA", "BBB", "CCC"]
    assert result["precomputed_trade_plan_used"] is True
    assert result["alpaca_submission_summary"]["exact_plan_buy_budget_bypassed"] is True
    assert int(result["alpaca_submission_summary"]["budget_skipped_orders"]) == 0
    assert result["budget_skipped_orders"] == []
    assert int(result["alpaca_submission_summary"]["buy_phase_planned"]) == 2
    assert int(result["alpaca_submission_summary"]["buy_phase_submitted"]) == 2


def test_phase3_idempotent_remote_existing_preserved(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RUN_OUTPUT_ROOT", str(tmp_path / "outputs" / "runs" / "run-p3-idem"))
    trades = pd.DataFrame(
        [
            {"ticker": "AAA", "side": "SELL", "shares": 1.0, "quantity": 1.0, "price": 100.0, "notional": 100.0, "slippage_cost": 0.0, "reason": "trim"},
            {"ticker": "BBB", "side": "BUY", "shares": 2.0, "quantity": 2.0, "price": 90.0, "notional": 180.0, "slippage_cost": 0.0, "reason": "add"},
        ]
    )
    _mock_open_market(
        monkeypatch,
        trades_df=trades,
        trade_meta={"target_investable_dollars": 10000.0, "scaled_tickers": [], "overspend_prevented": False},
        holdings=pd.DataFrame([{"ticker": "AAA", "sleeve": "core", "shares": 1.0}]),
    )

    class _IdemAlpaca(_SequencedAlpaca):
        def find_order_by_client_id(self, client_id):
            self.find_calls.append(client_id)
            if len(self.find_calls) == 1:
                return {"id": "existing-sell", "status": "accepted", "submitted_at": "2026-03-11T09:59:00-05:00"}
            return None

    fake = _IdemAlpaca(
        account_sequence=[
            {"cash": "1000.0", "equity": "10000.0", "buying_power": "1000.0", "status": "ACTIVE"},
            {"cash": "1200.0", "equity": "10100.0", "buying_power": "1200.0", "status": "ACTIVE"},
            {"cash": "1020.0", "equity": "10120.0", "buying_power": "1020.0", "status": "ACTIVE"},
        ],
        positions_sequence=[
            [],
            [{"symbol": "BBB", "qty": "2", "current_price": "90.0", "market_value": "180.0"}],
        ],
    )
    monkeypatch.setattr(_IdemAlpaca, "from_env", classmethod(lambda cls: fake), raising=False)
    monkeypatch.setattr(broker, "AlpacaBroker", _IdemAlpaca)

    result = broker.run_paper_day(
        run_date="2026-03-11",
        signals_path="signals.json",
        ledger_path="ledger.csv",
        trades_path="trades.csv",
        config_path="config.json",
        now_et=dt.datetime(2026, 3, 11, 10, 0, tzinfo=ZoneInfo("America/New_York")),
    )

    assert [side for side, _, _, _ in fake.submitted] == ["BUY"]
    assert "AAA" not in [symbol for _, symbol, _, _ in fake.submitted]
    assert int(result["alpaca_submission_summary"]["remote_existing_orders"]) == 1


def test_phase3_blocks_buys_when_sell_phase_times_out(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RUN_OUTPUT_ROOT", str(tmp_path / "outputs" / "runs" / "run-p3-timeout"))
    monkeypatch.setenv("ALPACA_SELL_PHASE_TIMEOUT_SECONDS", "0")
    monkeypatch.setenv("ALPACA_SELL_PHASE_POLL_INTERVAL_SECONDS", "0")
    trades = pd.DataFrame(
        [
            {"ticker": "AAA", "side": "SELL", "shares": 1.0, "quantity": 1.0, "price": 100.0, "notional": 100.0, "slippage_cost": 0.0, "reason": "trim"},
            {"ticker": "BBB", "side": "BUY", "shares": 2.0, "quantity": 2.0, "price": 90.0, "notional": 180.0, "slippage_cost": 0.0, "reason": "add"},
        ]
    )
    _mock_open_market(
        monkeypatch,
        trades_df=trades,
        trade_meta={"target_investable_dollars": 10000.0, "scaled_tickers": [], "overspend_prevented": False},
        holdings=pd.DataFrame([{"ticker": "AAA", "sleeve": "core", "shares": 1.0}]),
    )

    class _PendingSellAlpaca(_SequencedAlpaca):
        def __init__(self, *, account_sequence, positions_sequence):
            super().__init__(account_sequence=account_sequence, positions_sequence=positions_sequence)
            self._client_lookup_calls = 0

        def find_order_by_client_id(self, client_id):
            self.find_calls.append(client_id)
            self._client_lookup_calls += 1
            if self._client_lookup_calls == 1:
                return None
            return {"id": "pending-sell", "status": "accepted", "submitted_at": "2026-03-11T10:00:00-05:00"}

    fake = _PendingSellAlpaca(
        account_sequence=[
            {"cash": "2000.0", "equity": "10000.0", "buying_power": "2000.0", "status": "ACTIVE"},
            {"cash": "2000.0", "equity": "10000.0", "buying_power": "2000.0", "status": "ACTIVE"},
            {"cash": "2000.0", "equity": "10000.0", "buying_power": "2000.0", "status": "ACTIVE"},
        ],
        positions_sequence=[
            [{"symbol": "AAA", "qty": "1", "current_price": "100.0", "market_value": "100.0"}],
            [{"symbol": "AAA", "qty": "1", "current_price": "100.0", "market_value": "100.0"}],
        ],
    )
    monkeypatch.setattr(_PendingSellAlpaca, "from_env", classmethod(lambda cls: fake), raising=False)
    monkeypatch.setattr(broker, "AlpacaBroker", _PendingSellAlpaca)

    result = broker.run_paper_day(
        run_date="2026-03-11",
        signals_path="signals.json",
        ledger_path="ledger.csv",
        trades_path="trades.csv",
        config_path="config.json",
        now_et=dt.datetime(2026, 3, 11, 10, 0, tzinfo=ZoneInfo("America/New_York")),
    )

    assert [side for side, _, _, _ in fake.submitted] == ["SELL"]
    assert result["sell_phase_status"] == "TIMEOUT"
    assert result["alpaca_submission_summary"]["buy_phase_block_reason"] == "sell_phase_timeout"
    assert int(result["alpaca_submission_summary"]["buy_phase_submitted"]) == 0
    assert [order["ticker"] for order in result["budget_skipped_orders"]] == ["BBB"]


def test_phase3_blocks_buys_when_postsell_cash_below_reserve(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RUN_OUTPUT_ROOT", str(tmp_path / "outputs" / "runs" / "run-p3-reserve"))
    trades = pd.DataFrame(
        [
            {"ticker": "AAA", "side": "SELL", "shares": 1.0, "quantity": 1.0, "price": 100.0, "notional": 100.0, "slippage_cost": 0.0, "reason": "trim"},
            {"ticker": "BBB", "side": "BUY", "shares": 2.0, "quantity": 2.0, "price": 90.0, "notional": 180.0, "slippage_cost": 0.0, "reason": "add"},
        ]
    )
    _mock_open_market(
        monkeypatch,
        trades_df=trades,
        trade_meta={"target_investable_dollars": 10000.0, "scaled_tickers": [], "overspend_prevented": False},
        holdings=pd.DataFrame([{"ticker": "AAA", "sleeve": "core", "shares": 1.0}]),
    )
    fake = _SequencedAlpaca(
        account_sequence=[
            {"cash": "1200.0", "equity": "10000.0", "buying_power": "1200.0", "status": "ACTIVE"},
            {"cash": "950.0", "equity": "10000.0", "buying_power": "950.0", "status": "ACTIVE"},
            {"cash": "950.0", "equity": "10000.0", "buying_power": "950.0", "status": "ACTIVE"},
        ],
        positions_sequence=[
            [],
            [],
        ],
    )
    monkeypatch.setattr(_SequencedAlpaca, "from_env", classmethod(lambda cls: fake), raising=False)
    monkeypatch.setattr(broker, "AlpacaBroker", _SequencedAlpaca)

    result = broker.run_paper_day(
        run_date="2026-03-11",
        signals_path="signals.json",
        ledger_path="ledger.csv",
        trades_path="trades.csv",
        config_path="config.json",
        now_et=dt.datetime(2026, 3, 11, 10, 0, tzinfo=ZoneInfo("America/New_York")),
    )

    assert [side for side, _, _, _ in fake.submitted] == ["SELL"]
    assert result["sell_phase_status"] == "COMPLETED"
    assert float(result["buy_budget_computed"]) == pytest.approx(0.0)
    assert result["alpaca_submission_summary"]["buy_phase_block_reason"] == "post_sell_cash_below_reserve"
    assert [order["ticker"] for order in result["budget_skipped_orders"]] == ["BBB"]

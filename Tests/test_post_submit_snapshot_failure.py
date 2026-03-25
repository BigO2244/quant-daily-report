from __future__ import annotations

import datetime as dt
import json
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from brokers.alpaca_broker import (
    CASH_REBALANCE_INCOMPLETE,
    EXECUTION_OUTCOME_POST_SUBMIT_ARTIFACT_FAILURE,
)
import paper.paper_broker as broker


def _mock_open_market(
    monkeypatch,
    *,
    trades_df: pd.DataFrame,
    holdings_df: pd.DataFrame,
) -> None:
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
        lambda path: (
            holdings_df.copy(),
            1000.0,
            25000.0,
            "2026-03-16",
        ),
    )
    monkeypatch.setattr(
        broker,
        "load_targets",
        lambda *args, **kwargs: (
            pd.DataFrame(
                [
                    {"ticker": "MSFT", "target_weight": 0.2, "sleeve": "core"},
                ]
            ),
            0.0,
            "2026-03-17",
            "2026-03-16",
        ),
    )
    monkeypatch.setattr(
        broker,
        "fetch_open_prices_yfinance",
        lambda tickers, run_date: pd.DataFrame(
            [
                {"ticker": "AEP", "open": 100.0, "price_date": run_date},
                {"ticker": "HWM", "open": 100.0, "price_date": run_date},
                {"ticker": "LRCX", "open": 100.0, "price_date": run_date},
                {"ticker": "ROST", "open": 100.0, "price_date": run_date},
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
                {"ticker": "HWM", "prev_close": 99.0, "price_date": asof_date},
                {"ticker": "LRCX", "prev_close": 99.0, "price_date": asof_date},
                {"ticker": "ROST", "prev_close": 99.0, "price_date": asof_date},
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
            {"blocked_tickers": {}, "asof_date": "2026-03-16", "cutoff_date": "2026-03-16"},
        ),
    )
    monkeypatch.setattr(
        broker,
        "build_rebalance_trades",
        lambda **kwargs: (
            trades_df.copy(),
            {
                "target_investable_dollars": 25000.0,
                "scaled_tickers": [],
                "overspend_prevented": False,
            },
        ),
    )
    monkeypatch.setattr(
        broker,
        "apply_risk_guards",
        lambda trades, equity_prev, cfg: (trades, [], False),
    )
    monkeypatch.setattr(broker, "append_csv", lambda df, path: None)


class _UuidPostsellSnapshotAlpaca:
    paper = True

    def __init__(self) -> None:
        self.submitted = []
        self._account_calls = 0
        self._positions_calls = 0
        self._orders_by_client_id = {}
        self._account_uuid = uuid.uuid4()

    @classmethod
    def from_env(cls):
        return cls()

    def find_order_by_client_id(self, client_order_id):
        return self._orders_by_client_id.get(client_order_id)

    def submit_market_order(self, symbol, qty, side, client_order_id, tif="day"):
        self.submitted.append((side, symbol, float(qty), client_order_id))
        status = "filled" if side.upper() == "SELL" else "accepted"
        order = {
            "id": f"alpaca-{len(self.submitted)}",
            "status": status,
            "submitted_at": "2026-03-17T09:40:00-04:00",
        }
        self._orders_by_client_id[client_order_id] = order
        return order

    def submit_limit_order(self, symbol, qty, side, limit_price, client_order_id, tif="day"):
        raise AssertionError("limit orders not expected in snapshot failure test")

    def get_account(self):
        self._account_calls += 1
        base = {
            "cash": "3000.0",
            "equity": "25000.0",
            "buying_power": "8000.0",
            "status": "ACTIVE",
            "raw": {},
        }
        if self._account_calls >= 2:
            base["raw"] = {
                "account_uuid": self._account_uuid,
                "daytrading_buying_power": "8000",
            }
        return base

    def get_positions(self):
        self._positions_calls += 1
        if self._positions_calls == 1:
            return [
                {"symbol": "AEP", "qty": "1", "current_price": "100.0", "market_value": "100.0"},
                {"symbol": "HWM", "qty": "1", "current_price": "100.0", "market_value": "100.0"},
                {"symbol": "LRCX", "qty": "1", "current_price": "100.0", "market_value": "100.0"},
                {"symbol": "ROST", "qty": "1", "current_price": "100.0", "market_value": "100.0"},
            ]
        return [
            {"symbol": "MSFT", "qty": "1", "current_price": "100.0", "market_value": "100.0"},
        ]


def _trade_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"ticker": "AEP", "side": "SELL", "shares": 1.0, "quantity": 1.0, "price": 100.0, "notional": 100.0, "slippage_cost": 0.0, "reason": "trim"},
            {"ticker": "HWM", "side": "SELL", "shares": 1.0, "quantity": 1.0, "price": 100.0, "notional": 100.0, "slippage_cost": 0.0, "reason": "trim"},
            {"ticker": "LRCX", "side": "SELL", "shares": 1.0, "quantity": 1.0, "price": 100.0, "notional": 100.0, "slippage_cost": 0.0, "reason": "trim"},
            {"ticker": "ROST", "side": "SELL", "shares": 1.0, "quantity": 1.0, "price": 100.0, "notional": 100.0, "slippage_cost": 0.0, "reason": "trim"},
            {"ticker": "MSFT", "side": "BUY", "shares": 1.0, "quantity": 1.0, "price": 100.0, "notional": 100.0, "slippage_cost": 0.0, "reason": "add"},
        ]
    )


def _holdings_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"ticker": "AEP", "sleeve": "main", "shares": 1.0},
            {"ticker": "HWM", "sleeve": "main", "shares": 1.0},
            {"ticker": "LRCX", "sleeve": "main", "shares": 1.0},
            {"ticker": "ROST", "sleeve": "main", "shares": 1.0},
        ]
    )


def test_write_broker_account_snapshot_normalizes_uuid(tmp_path, monkeypatch):
    monkeypatch.setenv("RUN_OUTPUT_ROOT", str(tmp_path / "outputs"))
    account_uuid = uuid.uuid4()

    path = broker._write_broker_account_snapshot(
        "postsell_account_snapshot.json",
        "2026-03-17",
        {
            "cash": "3000.0",
            "equity": "25000.0",
            "buying_power": "8000.0",
            "status": "ACTIVE",
            "raw": {"account_uuid": account_uuid},
        },
    )

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    assert payload["account"]["raw"]["account_uuid"] == str(account_uuid)


def test_write_broker_account_snapshot_overwrites_latest_file(tmp_path, monkeypatch):
    monkeypatch.setenv("RUN_OUTPUT_ROOT", str(tmp_path / "outputs"))

    path = broker._write_broker_account_snapshot(
        "postsell_account_snapshot.json",
        "2026-03-17",
        {"cash": "3000.0", "equity": "25000.0", "buying_power": "8000.0", "status": "ACTIVE"},
    )
    broker._write_broker_account_snapshot(
        "postsell_account_snapshot.json",
        "2026-03-17",
        {"cash": "4000.0", "equity": "25500.0", "buying_power": "9000.0", "status": "ACTIVE"},
    )

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    assert payload["cash"] == 4000.0
    assert payload["equity"] == 25500.0


def test_run_paper_day_handles_uuid_in_postsell_snapshot(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RUN_OUTPUT_ROOT", str(tmp_path / "outputs" / "runs" / "run-uuid-postsell"))
    monkeypatch.setenv("MODE", "alpaca")
    monkeypatch.setenv("TRADING_MODE", "alpaca")

    _mock_open_market(
        monkeypatch,
        trades_df=_trade_frame(),
        holdings_df=_holdings_frame(),
    )

    fake = _UuidPostsellSnapshotAlpaca()
    monkeypatch.setattr(_UuidPostsellSnapshotAlpaca, "from_env", classmethod(lambda cls: fake), raising=False)
    monkeypatch.setattr(broker, "AlpacaBroker", _UuidPostsellSnapshotAlpaca)

    result = broker.run_paper_day(
        run_date="2026-03-17",
        signals_path="signals.json",
        ledger_path="ledger.csv",
        trades_path="trades.csv",
        config_path="config.json",
        now_et=dt.datetime(2026, 3, 17, 9, 40, tzinfo=ZoneInfo("America/New_York")),
    )

    assert result["execution_outcome"] is None
    assert result["alpaca_submission_summary"]["submit_success"] == 5
    assert [side for side, _, _, _ in fake.submitted] == ["SELL", "SELL", "SELL", "SELL", "BUY"]
    snapshot_path = Path(result["postsell_account_snapshot_path"])
    assert snapshot_path.exists()
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert isinstance(payload["account"]["raw"]["account_uuid"], str)


def test_postsell_snapshot_failure_preserves_submissions_and_halts_buys(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RUN_OUTPUT_ROOT", str(tmp_path / "outputs" / "runs" / "run-postsell-fail"))
    monkeypatch.setenv("MODE", "alpaca")
    monkeypatch.setenv("TRADING_MODE", "alpaca")

    _mock_open_market(
        monkeypatch,
        trades_df=_trade_frame(),
        holdings_df=_holdings_frame(),
    )

    fake = _UuidPostsellSnapshotAlpaca()
    monkeypatch.setattr(_UuidPostsellSnapshotAlpaca, "from_env", classmethod(lambda cls: fake), raising=False)
    monkeypatch.setattr(broker, "AlpacaBroker", _UuidPostsellSnapshotAlpaca)

    original = broker._write_broker_account_snapshot

    def _fail_postsell_snapshot(filename, run_date, account):
        if filename == "postsell_account_snapshot.json":
            raise TypeError("Object of type UUID is not JSON serializable")
        return original(filename, run_date, account)

    monkeypatch.setattr(broker, "_write_broker_account_snapshot", _fail_postsell_snapshot)

    result = broker.run_paper_day(
        run_date="2026-03-17",
        signals_path="signals.json",
        ledger_path="ledger.csv",
        trades_path="trades.csv",
        config_path="config.json",
        now_et=dt.datetime(2026, 3, 17, 9, 40, tzinfo=ZoneInfo("America/New_York")),
    )

    assert result["execution_status"] == "HALTED"
    assert result["execution_outcome"] == EXECUTION_OUTCOME_POST_SUBMIT_ARTIFACT_FAILURE
    assert result["execution_reason"] == "post_sell_account_snapshot_write_failed"
    assert result["cash_rebalance_status"] == CASH_REBALANCE_INCOMPLETE
    assert result["artifact_failure_stage"] == "post_sell_account_snapshot"
    assert "UUID is not JSON serializable" in str(result["artifact_failure_message"])
    assert result["halt_remaining_buys"] is True
    assert result["alpaca_submission_summary"]["submit_success"] == 4
    assert result["alpaca_submission_summary"]["submit_failed"] == 0
    assert set(result["execution_submitted_symbols"]) == {"AEP", "HWM", "LRCX", "ROST"}
    assert [side for side, _, _, _ in fake.submitted] == ["SELL", "SELL", "SELL", "SELL"]

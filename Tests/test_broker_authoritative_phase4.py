import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import paper.paper_broker as broker
import reconciliation


def _mock_open_market(monkeypatch, *, trades_df: pd.DataFrame, trade_meta: dict):
    cfg = broker.PaperConfig(
        initial_equity=10000.0,
        benchmark_ticker="SPY",
        slippage_bps=0.0,
        allow_fractional=True,
        min_trade_dollars=1.0,
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
            pd.DataFrame([{"ticker": "BBB", "target_weight": 1.0, "sleeve": "core"}]),
            0.0,
            "2026-03-12",
            "2026-03-11",
        ),
    )
    monkeypatch.setattr(
        broker,
        "fetch_open_prices_yfinance",
        lambda tickers, run_date: pd.DataFrame(
            [
                {"ticker": "BBB", "open": 90.0, "price_date": run_date},
                {"ticker": "SPY", "open": 500.0, "price_date": run_date},
            ]
        ),
    )
    monkeypatch.setattr(
        broker,
        "fetch_prev_closes_yfinance",
        lambda tickers, asof_date: pd.DataFrame(
            [{"ticker": "BBB", "prev_close": 89.0, "price_date": asof_date}]
        ),
    )
    monkeypatch.setattr(
        broker,
        "validate_open_window",
        lambda **kwargs: (
            True,
            [],
            {"blocked_tickers": {}, "asof_date": "2026-03-11", "cutoff_date": "2026-03-11"},
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
    monkeypatch.setattr(broker, "append_csv", lambda df, path: None)


class _Phase4Alpaca:
    paper = True

    def __init__(self):
        self.submitted = False
        self.account_calls = [
            {"cash": "1000.0", "equity": "10000.0", "buying_power": "1000.0", "status": "ACTIVE"},
            {"cash": "1000.0", "equity": "10000.0", "buying_power": "1000.0", "status": "ACTIVE"},
            {"cash": "820.0", "equity": "10010.0", "buying_power": "820.0", "status": "ACTIVE"},
        ]
        self.positions_calls = [
            [],
            [{"symbol": "BBB", "qty": "2", "current_price": "90.0", "market_value": "180.0"}],
        ]

    @classmethod
    def from_env(cls):
        return cls()

    def find_order_by_client_id(self, client_id):
        if self.submitted:
            return {"id": "alpaca-1", "status": "filled", "filled_qty": "2"}
        return None

    def get_order(self, order_id):
        if order_id == "alpaca-1":
            return {"id": order_id, "status": "filled", "filled_qty": "2"}
        return None

    def submit_market_order(self, symbol, qty, side, client_order_id, tif="day"):
        self.submitted = True
        return {
            "id": "alpaca-1",
            "status": "accepted",
            "submitted_at": "2026-03-12T10:00:00-05:00",
        }

    def submit_limit_order(self, symbol, qty, side, limit_price, client_order_id, tif="day"):
        raise AssertionError("limit orders not expected")

    def get_account(self):
        if self.account_calls:
            return self.account_calls.pop(0)
        return {"cash": "820.0", "equity": "10010.0", "buying_power": "820.0", "status": "ACTIVE"}

    def get_positions(self):
        if self.positions_calls:
            return self.positions_calls.pop(0)
        return [{"symbol": "BBB", "qty": "2", "current_price": "90.0", "market_value": "180.0"}]


def test_phase4_run_paper_day_writes_posttrade_artifacts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_root = tmp_path / "outputs" / "runs" / "run-p4"
    monkeypatch.setenv("RUN_OUTPUT_ROOT", str(run_root))
    trades = pd.DataFrame(
        [
            {"ticker": "BBB", "side": "BUY", "shares": 2.0, "quantity": 2.0, "price": 90.0, "notional": 180.0, "slippage_cost": 0.0, "reason": "add"},
        ]
    )
    _mock_open_market(
        monkeypatch,
        trades_df=trades,
        trade_meta={"target_investable_dollars": 10000.0, "scaled_tickers": [], "overspend_prevented": False},
    )
    fake = _Phase4Alpaca()
    monkeypatch.setattr(_Phase4Alpaca, "from_env", classmethod(lambda cls: fake), raising=False)
    monkeypatch.setattr(broker, "AlpacaBroker", _Phase4Alpaca)

    result = broker.run_paper_day(
        run_date="2026-03-12",
        signals_path="signals.json",
        ledger_path="ledger.csv",
        trades_path="trades.csv",
        config_path="config.json",
        now_et=dt.datetime(2026, 3, 12, 10, 0, tzinfo=ZoneInfo("America/New_York")),
    )

    account_path = Path(result["posttrade_account_snapshot_path"])
    positions_path = Path(result["posttrade_positions_snapshot_path"])
    recon_path = Path(result["posttrade_recon_path"])
    assert account_path.exists()
    assert positions_path.exists()
    assert recon_path.exists()
    account_payload = json.loads(account_path.read_text(encoding="utf-8"))
    positions_payload = json.loads(positions_path.read_text(encoding="utf-8"))
    recon_payload = json.loads(recon_path.read_text(encoding="utf-8"))
    assert float(account_payload["cash"]) == pytest.approx(820.0)
    assert positions_payload["normalized_positions"] == {"BBB": 2.0}
    assert recon_payload["expected_positions"] == {"BBB": 2.0}
    assert recon_payload["actual_positions"] == {"BBB": 2.0}


def test_phase4_refresh_from_posttrade_snapshot_matches_canonical(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    positions_snapshot = {
        "positions": [
            {"symbol": "AAPL", "qty": "5", "current_price": "190.0", "market_value": "950.0"},
            {"symbol": "MSFT", "qty": "3", "current_price": "400.0", "market_value": "1200.0"},
        ]
    }
    account_snapshot = {
        "account": {"cash": "2500.0", "equity": "4650.0", "portfolio_value": "4650.0"},
        "cash": 2500.0,
        "equity": 4650.0,
    }

    ok = reconciliation.refresh_canonical_snapshot_from_posttrade_snapshot(
        positions_snapshot=positions_snapshot,
        account_snapshot=account_snapshot,
        run_date="2026-03-12",
    )

    assert ok is True
    canonical = json.loads(Path("outputs/paper_state/canonical_positions.json").read_text(encoding="utf-8"))
    assert canonical["positions"] == {"AAPL": 5.0, "MSFT": 3.0}
    assert canonical["cash"] == pytest.approx(2500.0)
    assert canonical["equity"] == pytest.approx(4650.0)
    assert canonical["reason"] == "posttrade_refresh_from_snapshot"


class _ReconFillAlpaca:
    def __init__(self, statuses):
        self.statuses = statuses

    def get_order(self, order_id):
        return self.statuses.get(order_id)

    def find_order_by_client_id(self, client_id):
        return self.statuses.get(client_id)


def test_posttrade_expected_positions_apply_actual_filled_orders():
    submitted = [
        {"alpaca_order_id": "sell-gild", "ticker": "GILD", "side": "SELL", "quantity": 4},
        {"alpaca_order_id": "sell-gm", "ticker": "GM", "side": "SELL", "quantity": 4},
        {"alpaca_order_id": "sell-qcom", "ticker": "QCOM", "side": "SELL", "quantity": 1},
        {"alpaca_order_id": "buy-hlt", "ticker": "HLT", "side": "BUY", "quantity": 1},
        {"alpaca_order_id": "buy-intc", "ticker": "INTC", "side": "BUY", "quantity": 2},
        {"alpaca_order_id": "buy-unh", "ticker": "UNH", "side": "BUY", "quantity": 1},
        {"alpaca_order_id": "buy-wm", "ticker": "WM", "side": "BUY", "quantity": 2},
    ]
    alpaca = _ReconFillAlpaca(
        {
            "sell-gild": {"status": "filled", "filled_qty": "4"},
            "sell-gm": {"status": "filled", "filled_qty": "4"},
            "sell-qcom": {"status": "filled", "filled_qty": "1"},
            "buy-hlt": {"status": "filled", "filled_qty": "1"},
            "buy-intc": {"status": "filled", "filled_qty": "2"},
            "buy-unh": {"status": "filled", "filled_qty": "1"},
            "buy-wm": {"status": "filled", "filled_qty": "2"},
        }
    )

    resolved = broker._resolve_filled_orders_for_recon(alpaca, submitted)
    expected = broker._expected_positions_after_orders(
        {"GILD": 4, "GM": 15, "QCOM": 3},
        resolved,
    )

    assert expected == {
        "GM": 11.0,
        "QCOM": 2.0,
        "HLT": 1.0,
        "INTC": 2.0,
        "UNH": 1.0,
        "WM": 2.0,
    }


def test_posttrade_expected_positions_ignore_rejected_orders():
    submitted = [{"alpaca_order_id": "buy-hlt", "ticker": "HLT", "side": "BUY", "quantity": 1}]
    alpaca = _ReconFillAlpaca({"buy-hlt": {"status": "rejected", "filled_qty": "0"}})

    resolved = broker._resolve_filled_orders_for_recon(alpaca, submitted)
    expected = broker._expected_positions_after_orders({}, resolved)

    assert resolved == []
    assert expected == {}


def test_posttrade_expected_positions_apply_partial_fill_quantity_only():
    submitted = [{"alpaca_order_id": "sell-gm", "ticker": "GM", "side": "SELL", "quantity": 4}]
    alpaca = _ReconFillAlpaca({"sell-gm": {"status": "partially_filled", "filled_qty": "1.5"}})

    resolved = broker._resolve_filled_orders_for_recon(alpaca, submitted)
    expected = broker._expected_positions_after_orders({"GM": 15}, resolved)

    assert resolved[0]["quantity"] == pytest.approx(1.5)
    assert expected == {"GM": 13.5}


def test_posttrade_recon_derives_partial_sell_fill_from_position_delta():
    submitted = [{"alpaca_order_id": "sell-slb", "ticker": "SLB", "side": "SELL", "quantity": 12}]
    alpaca = _ReconFillAlpaca({"sell-slb": {"status": "partially_filled"}})
    unresolved = []

    resolved = broker._resolve_filled_orders_for_recon(
        alpaca,
        submitted,
        starting_positions={"SLB": 12},
        actual_positions={"SLB": 1},
        unresolved_orders=unresolved,
    )
    expected = broker._expected_positions_after_orders({"SLB": 12}, resolved)

    assert unresolved == []
    assert resolved[0]["quantity"] == pytest.approx(11.0)
    assert resolved[0]["filled_quantity_source"] == "position_delta"
    assert expected == {"SLB": 1.0}


class _PosttradePartialAlpaca:
    def __init__(self, *, order_status, positions):
        self.order_status = order_status
        self.positions = positions

    def get_account(self):
        return {"cash": "2186.55", "equity": "10583.10", "buying_power": "12769.65", "status": "ACTIVE"}

    def get_positions(self):
        return list(self.positions)

    def get_order(self, order_id):
        return self.order_status.get(order_id)

    def find_order_by_client_id(self, client_id):
        return self.order_status.get(client_id)


def test_posttrade_state_capture_uses_position_delta_for_partial_sell_without_filled_qty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    submitted = [{"alpaca_order_id": "sell-slb", "order_id": "day:SLB:SELL", "ticker": "SLB", "side": "SELL", "quantity": 12}]
    holdings_prev = pd.DataFrame([{"ticker": "SLB", "sleeve": "core", "shares": 12.0}])
    alpaca = _PosttradePartialAlpaca(
        order_status={"sell-slb": {"status": "partially_filled"}},
        positions=[{"symbol": "SLB", "qty": "1", "current_price": "57.0", "market_value": "57.0"}],
    )

    state = broker._capture_alpaca_posttrade_state(
        alpaca=alpaca,
        run_date="2026-05-26",
        holdings_prev=holdings_prev,
        submitted_orders=submitted,
        cfg=broker.PaperConfig(
            initial_equity=10000.0,
            benchmark_ticker="SPY",
            slippage_bps=0.0,
            allow_fractional=True,
            min_trade_dollars=1.0,
        ),
        raise_on_failure=True,
    )

    recon_path = Path(state["posttrade_recon_path"])
    recon_payload = json.loads(recon_path.read_text(encoding="utf-8"))
    assert state["posttrade_recon_status"] == "OK_RECONCILED"
    assert state["posttrade_unresolved_orders"] == []
    assert recon_payload["expected_positions"] == {"SLB": 1.0}
    assert recon_payload["actual_positions"] == {"SLB": 1.0}


def test_posttrade_state_capture_records_unresolved_partial_without_fatal_artifact_failure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    submitted = [{"alpaca_order_id": "sell-slb", "order_id": "day:SLB:SELL", "ticker": "SLB", "side": "SELL", "quantity": 12}]
    holdings_prev = pd.DataFrame([{"ticker": "SLB", "sleeve": "core", "shares": 12.0}])
    alpaca = _PosttradePartialAlpaca(
        order_status={"sell-slb": {"status": "partially_filled"}},
        positions=[{"symbol": "SLB", "qty": "12", "current_price": "57.0", "market_value": "684.0"}],
    )

    state = broker._capture_alpaca_posttrade_state(
        alpaca=alpaca,
        run_date="2026-05-26",
        holdings_prev=holdings_prev,
        submitted_orders=submitted,
        cfg=broker.PaperConfig(
            initial_equity=10000.0,
            benchmark_ticker="SPY",
            slippage_bps=0.0,
            allow_fractional=True,
            min_trade_dollars=1.0,
        ),
        raise_on_failure=True,
    )

    recon_path = Path(state["posttrade_recon_path"])
    recon_payload = json.loads(recon_path.read_text(encoding="utf-8"))
    assert state["posttrade_recon_status"] == "NOT_COMPARABLE"
    assert state["posttrade_unresolved_orders"][0]["ticker"] == "SLB"
    assert recon_payload["manual_intervention_required"] is True
    assert recon_payload["unresolved_submitted_orders_count"] == 1
    assert "unresolved_submitted_orders" in recon_payload["not_comparable_reasons"]


def test_posttrade_expected_positions_ignore_zero_share_orders():
    submitted = [{"alpaca_order_id": "buy-hlt", "ticker": "HLT", "side": "BUY", "quantity": 0}]
    alpaca = _ReconFillAlpaca({"buy-hlt": {"status": "filled", "filled_qty": "0"}})

    resolved = broker._resolve_filled_orders_for_recon(alpaca, submitted)
    expected = broker._expected_positions_after_orders({}, resolved)

    assert resolved == []
    assert expected == {}


class _SequencingAlpaca:
    """Returns PRE-fill account/positions until an order is resolved via
    get_order(), then POST-fill state. Proves posttrade snapshots are captured
    AFTER fill resolution (regression for the 2026-06-11 stale-snapshot defect)."""

    def __init__(self):
        self._fills_visible = False
        self.account_calls_seen = 0
        self.positions_calls_seen = 0

    def get_order(self, order_id):
        # The act of resolving the order is when the fill becomes visible.
        self._fills_visible = True
        return {"id": order_id, "status": "filled", "filled_qty": "2"}

    def find_order_by_client_id(self, client_id):
        self._fills_visible = True
        return {"id": "alpaca-1", "status": "filled", "filled_qty": "2"}

    def get_account(self):
        self.account_calls_seen += 1
        if self._fills_visible:
            return {"cash": "820.0", "equity": "10010.0", "buying_power": "820.0", "status": "ACTIVE"}
        return {"cash": "10000.0", "equity": "10000.0", "buying_power": "10000.0", "status": "ACTIVE"}

    def get_positions(self):
        self.positions_calls_seen += 1
        if self._fills_visible:
            return [{"symbol": "BBB", "qty": "2", "current_price": "90.0", "market_value": "180.0"}]
        return []


def test_posttrade_snapshot_captured_after_fill_resolution_not_before(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    submitted = [
        {"alpaca_order_id": "alpaca-1", "order_id": "day:BBB:BUY", "ticker": "BBB", "side": "BUY", "quantity": 2}
    ]
    alpaca = _SequencingAlpaca()

    state = broker._capture_alpaca_posttrade_state(
        alpaca=alpaca,
        run_date="2026-06-11",
        holdings_prev=pd.DataFrame(columns=["ticker", "sleeve", "shares"]),
        submitted_orders=submitted,
        cfg=broker.PaperConfig(
            initial_equity=10000.0,
            benchmark_ticker="SPY",
            slippage_bps=0.0,
            allow_fractional=True,
            min_trade_dollars=1.0,
        ),
        raise_on_failure=True,
    )

    # Returned snapshot reflects POST-fill account cash (820), not the pre-fill 10000.
    assert float(state["alpaca_account_snapshot"]["cash"]) == pytest.approx(820.0)

    # Written canonical artifacts reflect post-fill state.
    account_payload = json.loads(Path(state["posttrade_account_snapshot_path"]).read_text(encoding="utf-8"))
    positions_payload = json.loads(Path(state["posttrade_positions_snapshot_path"]).read_text(encoding="utf-8"))
    assert float(account_payload["cash"]) == pytest.approx(820.0)
    assert positions_payload["normalized_positions"] == {"BBB": 2.0}

    # Reconciliation uses the refreshed post-fill positions.
    recon_payload = json.loads(Path(state["posttrade_recon_path"]).read_text(encoding="utf-8"))
    assert recon_payload["expected_positions"] == {"BBB": 2.0}
    assert recon_payload["actual_positions"] == {"BBB": 2.0}
    assert state["posttrade_filled_orders_count"] == 1


class _DelayedBuyFillAlpaca:
    def __init__(self, *, fill_after_polls: int, final_status: str = "filled", final_filled_qty: str = "2"):
        self.fill_after_polls = fill_after_polls
        self.final_status = final_status
        self.final_filled_qty = final_filled_qty
        self.order_polls = 0

    def get_order(self, order_id):
        self.order_polls += 1
        if self.order_polls >= self.fill_after_polls:
            return {
                "id": order_id,
                "status": self.final_status,
                "filled_qty": self.final_filled_qty,
                "filled_at": "2026-06-12T13:36:40+00:00",
            }
        return {"id": order_id, "status": "pending_new", "filled_qty": "0"}

    def find_order_by_client_id(self, client_id):
        return self.get_order(client_id)

    def get_account(self):
        if self.order_polls >= self.fill_after_polls and self.final_status == "filled":
            return {"cash": "820.0", "equity": "10010.0", "buying_power": "820.0", "status": "ACTIVE"}
        return {"cash": "1000.0", "equity": "10000.0", "buying_power": "1000.0", "status": "ACTIVE"}

    def get_positions(self):
        if self.order_polls >= self.fill_after_polls and self.final_status == "filled":
            return [{"symbol": "BBB", "qty": "2", "current_price": "90.0", "market_value": "180.0"}]
        return []


def test_posttrade_capture_waits_for_delayed_buy_fill_before_snapshot(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    submitted = [
        {"alpaca_order_id": "buy-bbb", "order_id": "day:BBB:BUY", "ticker": "BBB", "side": "BUY", "quantity": 2}
    ]
    alpaca = _DelayedBuyFillAlpaca(fill_after_polls=3)

    state = broker._capture_alpaca_posttrade_state(
        alpaca=alpaca,
        run_date="2026-06-12",
        holdings_prev=pd.DataFrame(columns=["ticker", "sleeve", "shares"]),
        submitted_orders=submitted,
        cfg=broker.PaperConfig(
            initial_equity=10000.0,
            benchmark_ticker="SPY",
            slippage_bps=0.0,
            allow_fractional=True,
            min_trade_dollars=1.0,
        ),
        raise_on_failure=True,
        buy_fill_timeout_seconds=5.0,
        buy_fill_poll_interval_seconds=0.0,
    )

    account_payload = json.loads(Path(state["posttrade_account_snapshot_path"]).read_text(encoding="utf-8"))
    positions_payload = json.loads(Path(state["posttrade_positions_snapshot_path"]).read_text(encoding="utf-8"))
    assert float(account_payload["cash"]) == pytest.approx(820.0)
    assert positions_payload["normalized_positions"] == {"BBB": 2.0}
    assert state["buy_phase_status"] == broker.BUY_PHASE_COMPLETED
    assert state["buy_phase_completion_reason"] == "all_buy_orders_filled"
    assert state["buy_fill_poll_count"] >= 3
    assert state["posttrade_snapshot_stage"] == "post_buy"
    assert state["filled_buy_count"] == 1
    assert state["pending_buy_count"] == 0


def test_posttrade_observed_buy_lifecycle_rewrites_broker_orders_csv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RUN_OUTPUT_ROOT", str(tmp_path / "outputs" / "runs" / "run-buy-fill"))
    submissions = [
        {
            "trade_date": "2026-06-16",
            "order_id": "day:BBB:BUY",
            "client_order_id": "caerus-day-bbb-buy",
            "alpaca_order_id": "buy-bbb",
            "ticker": "BBB",
            "side": "BUY",
            "quantity": 2,
            "status": "PENDING_NEW",
            "latest_status": "PENDING_NEW",
            "filled_qty": "0",
            "filled_at": "",
            "submitted_at": "2026-06-16T13:36:01+00:00",
            "mode": "alpaca",
        }
    ]
    observed = [
        {
            "order_id": "day:BBB:BUY",
            "alpaca_order_id": "buy-bbb",
            "ticker": "BBB",
            "side": "BUY",
            "quantity": 2,
            "latest_status": "filled",
            "filled_qty": "2",
            "filled_at": "2026-06-16T13:36:09+00:00",
            "submitted_at": "2026-06-16T13:36:01+00:00",
            "last_polled_at": "2026-06-16T13:36:10+00:00",
            "seconds_to_fill": 8.0,
        }
    ]

    broker._merge_observed_order_lifecycle(submissions, observed)
    orders_path = broker._write_alpaca_orders("2026-06-16", submissions)
    rows = pd.read_csv(orders_path)

    assert rows.loc[0, "status"] == "filled"
    assert rows.loc[0, "latest_status"] == "filled"
    assert float(rows.loc[0, "filled_qty"]) == pytest.approx(2.0)
    assert rows.loc[0, "filled_at"] == "2026-06-16T13:36:09+00:00"
    assert rows.loc[0, "last_polled_at"] == "2026-06-16T13:36:10+00:00"


def test_posttrade_capture_marks_buy_timeout_and_unresolved_recon(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    submitted = [
        {"alpaca_order_id": "buy-bbb", "order_id": "day:BBB:BUY", "ticker": "BBB", "side": "BUY", "quantity": 2}
    ]
    alpaca = _DelayedBuyFillAlpaca(fill_after_polls=999)

    state = broker._capture_alpaca_posttrade_state(
        alpaca=alpaca,
        run_date="2026-06-12",
        holdings_prev=pd.DataFrame(columns=["ticker", "sleeve", "shares"]),
        submitted_orders=submitted,
        cfg=broker.PaperConfig(
            initial_equity=10000.0,
            benchmark_ticker="SPY",
            slippage_bps=0.0,
            allow_fractional=True,
            min_trade_dollars=1.0,
        ),
        raise_on_failure=True,
        buy_fill_timeout_seconds=0.0,
        buy_fill_poll_interval_seconds=0.0,
    )

    recon_payload = json.loads(Path(state["posttrade_recon_path"]).read_text(encoding="utf-8"))
    assert state["buy_phase_status"] == broker.BUY_PHASE_TIMEOUT
    assert state["posttrade_snapshot_stage"] == "buy_timeout"
    assert state["pending_buy_count"] == 1
    assert state["posttrade_unresolved_orders"][0]["ticker"] == "BBB"
    assert state["posttrade_recon_status"] == "NOT_COMPARABLE"
    assert recon_payload["manual_intervention_required"] is True


def test_posttrade_capture_marks_rejected_buy_failed_without_fabricating_fill(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    submitted = [
        {"alpaca_order_id": "buy-bbb", "order_id": "day:BBB:BUY", "ticker": "BBB", "side": "BUY", "quantity": 2}
    ]
    alpaca = _DelayedBuyFillAlpaca(fill_after_polls=1, final_status="rejected", final_filled_qty="0")

    state = broker._capture_alpaca_posttrade_state(
        alpaca=alpaca,
        run_date="2026-06-12",
        holdings_prev=pd.DataFrame(columns=["ticker", "sleeve", "shares"]),
        submitted_orders=submitted,
        cfg=broker.PaperConfig(
            initial_equity=10000.0,
            benchmark_ticker="SPY",
            slippage_bps=0.0,
            allow_fractional=True,
            min_trade_dollars=1.0,
        ),
        raise_on_failure=True,
        buy_fill_timeout_seconds=5.0,
        buy_fill_poll_interval_seconds=0.0,
    )

    recon_payload = json.loads(Path(state["posttrade_recon_path"]).read_text(encoding="utf-8"))
    assert state["buy_phase_status"] == broker.BUY_PHASE_FAILED
    assert state["failed_buy_count"] == 1
    assert state["filled_buy_count"] == 0
    assert state["posttrade_unresolved_orders"] == []
    assert recon_payload["expected_positions"] == {}
    assert recon_payload["actual_positions"] == {}


def test_posttrade_capture_no_buy_run_preserves_existing_snapshot_flow(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    submitted = [
        {"alpaca_order_id": "sell-aaa", "order_id": "day:AAA:SELL", "ticker": "AAA", "side": "SELL", "quantity": 1}
    ]
    alpaca = _PosttradePartialAlpaca(
        order_status={"sell-aaa": {"status": "filled", "filled_qty": "1"}},
        positions=[],
    )

    state = broker._capture_alpaca_posttrade_state(
        alpaca=alpaca,
        run_date="2026-06-12",
        holdings_prev=pd.DataFrame([{"ticker": "AAA", "sleeve": "core", "shares": 1.0}]),
        submitted_orders=submitted,
        cfg=broker.PaperConfig(
            initial_equity=10000.0,
            benchmark_ticker="SPY",
            slippage_bps=0.0,
            allow_fractional=True,
            min_trade_dollars=1.0,
        ),
        raise_on_failure=True,
        buy_fill_timeout_seconds=0.0,
        buy_fill_poll_interval_seconds=0.0,
    )

    assert state["buy_phase_status"] == broker.BUY_PHASE_COMPLETED
    assert state["buy_phase_completion_reason"] == "no_buy_orders"
    assert state["submitted_buy_count"] == 0
    assert state["buy_fill_poll_count"] == 0
    assert state["posttrade_snapshot_stage"] == "post_buy"

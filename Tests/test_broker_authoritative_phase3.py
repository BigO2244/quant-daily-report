import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import paper.paper_broker as broker


def _mock_open_market(
    monkeypatch,
    *,
    trades_df: pd.DataFrame,
    trade_meta: dict,
    holdings=None,
    target_rows=None,
    cash_target_weight: float = 0.0,
    prev_close_rows=None,
    allow_fractional: bool = True,
    min_trade_dollars: float = 1.0,
    cash_buffer_bps: float = 0.0,
    rebalance_deadband_pct: float = 0.0,
    max_trades_per_day: int = 20,
):
    cfg = broker.PaperConfig(
        initial_equity=10000.0,
        benchmark_ticker="SPY",
        slippage_bps=0.0,
        allow_fractional=allow_fractional,
        min_trade_dollars=min_trade_dollars,
        cash_buffer_bps=cash_buffer_bps,
        rebalance_deadband_pct=rebalance_deadband_pct,
        max_trades_per_day=max_trades_per_day,
        trading_mode="alpaca",
    )
    if holdings is None:
        holdings = pd.DataFrame(columns=["ticker", "sleeve", "shares"])
    if target_rows is None:
        target_rows = [
            {"ticker": "AAA", "target_weight": 0.2, "sleeve": "core"},
            {"ticker": "BBB", "target_weight": 0.4, "sleeve": "core"},
            {"ticker": "CCC", "target_weight": 0.4, "sleeve": "core"},
        ]
    if prev_close_rows is None:
        prev_close_rows = [
            {"ticker": "AAA", "prev_close": 99.0, "price_date": "2026-03-10"},
            {"ticker": "BBB", "prev_close": 90.0, "price_date": "2026-03-10"},
            {"ticker": "CCC", "prev_close": 100.0, "price_date": "2026-03-10"},
            {"ticker": "SPY", "prev_close": 500.0, "price_date": "2026-03-10"},
        ]
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
            pd.DataFrame(target_rows),
            float(cash_target_weight),
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
            [{**row, "price_date": asof_date} for row in prev_close_rows]
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

    def get_order(self, order_id):
        text = str(order_id)
        if not text.startswith("alpaca-"):
            return None
        try:
            idx = int(text.rsplit("-", 1)[-1]) - 1
        except Exception:
            return None
        if idx < 0 or idx >= len(self.submitted):
            return None
        side, symbol, qty, client_order_id = self.submitted[idx]
        return {
            "id": text,
            "client_order_id": client_order_id,
            "symbol": symbol,
            "side": side,
            "status": "filled",
            "submitted_at": "2026-03-11T10:00:00-05:00",
            "filled_at": "2026-03-11T10:00:02-05:00",
            "filled_qty": str(qty),
        }

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
    assert float(result["buy_budget_computed"]) == pytest.approx(2100.0)
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
            {"cash": "1000.0", "equity": "10100.0", "buying_power": "1000.0", "status": "ACTIVE"},
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
    assert fake.submitted[1][2] == pytest.approx(900.0 / 90.0)
    assert int(result["alpaca_submission_summary"]["budget_skipped_orders"]) == 0
    rebudget = result["post_sell_rebudget"]
    assert rebudget["status"] == "REBUILT"
    assert float(rebudget["buy_budget_after_safeguards"]) == pytest.approx(900.0)
    assert float(rebudget["recomputed_buy_notional"]) == pytest.approx(900.0)
    assert [order["ticker"] for order in rebudget["final_buy_orders_submitted"]] == ["BBB"]


def test_phase3_exact_precomputed_plan_rebuilds_buys_from_postsell_state(tmp_path, monkeypatch):
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
    assert submitted_symbols == ["AAA", "BBB"]
    assert fake.submitted[1][2] == pytest.approx(1800.0 / 90.0)
    assert result["precomputed_trade_plan_used"] is True
    assert result["buy_phase_decision_reason"] == "buy_submitted_using_available_buying_power"
    assert int(result["alpaca_submission_summary"]["budget_skipped_orders"]) == 0
    assert result["budget_skipped_orders"] == []
    assert int(result["alpaca_submission_summary"]["buy_phase_planned"]) == 1
    assert int(result["alpaca_submission_summary"]["buy_phase_submitted"]) == 1
    rebudget = result["post_sell_rebudget"]
    assert rebudget["status"] == "REBUILT"
    assert float(rebudget["original_precomputed_buy_notional"]) == pytest.approx(1200.0)
    assert float(rebudget["buy_budget_after_safeguards"]) == pytest.approx(1800.0)
    assert float(rebudget["recomputed_buy_notional"]) == pytest.approx(1800.0)
    assert [order["ticker"] for order in rebudget["final_buy_orders_submitted"]] == ["BBB"]


def test_phase3_post_sell_rebudget_moves_cash_toward_risk_target(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RUN_OUTPUT_ROOT", str(tmp_path / "outputs" / "runs" / "run-p3-rebudget"))
    trades = pd.DataFrame(
        [
            {"ticker": "OLD", "side": "SELL", "shares": 1.0, "quantity": 1.0, "price": 400.0, "notional": 400.0, "slippage_cost": 0.0, "reason": "exit"},
            {"ticker": "AAA", "side": "BUY", "shares": 1.0, "quantity": 1.0, "price": 100.0, "notional": 100.0, "slippage_cost": 0.0, "reason": "stale_precompute"},
        ]
    )
    _mock_open_market(
        monkeypatch,
        trades_df=trades,
        trade_meta={"target_investable_dollars": 950.0, "scaled_tickers": [], "overspend_prevented": False},
        holdings=pd.DataFrame([{"ticker": "OLD", "sleeve": "core", "shares": 1.0}]),
        target_rows=[{"ticker": "AAA", "target_weight": 0.95, "sleeve": "core"}],
        cash_target_weight=0.05,
        prev_close_rows=[
            {"ticker": "AAA", "prev_close": 100.0, "price_date": "2026-03-10"},
            {"ticker": "OLD", "prev_close": 400.0, "price_date": "2026-03-10"},
            {"ticker": "SPY", "prev_close": 500.0, "price_date": "2026-03-10"},
        ],
    )
    monkeypatch.setattr(
        broker,
        "build_rebalance_trades",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not rebuild stale precompute before sells")),
    )
    fake = _SequencedAlpaca(
        account_sequence=[
            {"cash": "100.0", "equity": "1000.0", "buying_power": "100.0", "status": "ACTIVE"},
            {"cash": "500.0", "equity": "1000.0", "buying_power": "500.0", "status": "ACTIVE"},
            {"cash": "50.0", "equity": "1000.0", "buying_power": "50.0", "status": "ACTIVE"},
        ],
        positions_sequence=[
            [{"symbol": "OLD", "qty": "1", "current_price": "400.0", "market_value": "400.0"}],
            [],
            [{"symbol": "AAA", "qty": "4.5", "current_price": "100.0", "market_value": "450.0"}],
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
            {"ticker": "OLD", "side": "SELL", "shares": 1.0, "entry_price": 400.0, "notional": 400.0, "reason": "exit"},
            {"ticker": "AAA", "side": "BUY", "shares": 1.0, "entry_price": 100.0, "notional": 100.0, "reason": "stale_precompute"},
        ],
    )

    assert fake.submitted == [
        ("SELL", "OLD", 1.0, fake.submitted[0][3]),
        ("BUY", "AAA", 4.5, fake.submitted[1][3]),
    ]
    rebudget = result["post_sell_rebudget"]
    assert rebudget["status"] == "REBUILT"
    assert float(rebudget["pre_sell_cash"]) == pytest.approx(100.0)
    assert float(rebudget["confirmed_sell_proceeds"]) == pytest.approx(400.0)
    assert float(rebudget["post_sell_cash"]) == pytest.approx(500.0)
    assert float(rebudget["risk_cash_target"]) == pytest.approx(50.0)
    assert float(rebudget["buy_budget_before_safeguards"]) == pytest.approx(500.0)
    assert float(rebudget["buy_budget_after_safeguards"]) == pytest.approx(450.0)
    assert float(rebudget["original_precomputed_buy_notional"]) == pytest.approx(100.0)
    assert float(rebudget["recomputed_buy_notional"]) == pytest.approx(450.0)
    assert float(rebudget["estimated_ending_cash_vs_risk_target"]) == pytest.approx(0.0)
    artifact_path = Path(result["post_sell_rebudget_artifact_path"])
    assert artifact_path.exists()
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert artifact["schema_version"] == "post_sell_rebudget.v1"
    assert float(artifact["buy_budget_after_safeguards"]) == pytest.approx(450.0)


@pytest.mark.parametrize(
    ("allow_fractional", "expected_sides", "expected_buy_qty", "expected_status"),
    [
        (True, ["SELL", "BUY"], 0.1, "REBUILT"),
        (False, ["SELL"], None, "NO_BUYS"),
    ],
)
def test_phase3_post_sell_rebudget_preserves_fractional_mode(
    tmp_path,
    monkeypatch,
    allow_fractional,
    expected_sides,
    expected_buy_qty,
    expected_status,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RUN_OUTPUT_ROOT", str(tmp_path / "outputs" / "runs" / f"run-p3-frac-{allow_fractional}"))
    trades = pd.DataFrame(
        [
            {"ticker": "OLD", "side": "SELL", "shares": 1.0, "quantity": 1.0, "price": 100.0, "notional": 100.0, "slippage_cost": 0.0, "reason": "exit"},
        ]
    )
    _mock_open_market(
        monkeypatch,
        trades_df=trades,
        trade_meta={"target_investable_dollars": 950.0, "scaled_tickers": [], "overspend_prevented": False},
        holdings=pd.DataFrame([{"ticker": "OLD", "sleeve": "core", "shares": 1.0}]),
        target_rows=[{"ticker": "AAA", "target_weight": 0.10, "sleeve": "core"}],
        cash_target_weight=0.05,
        prev_close_rows=[
            {"ticker": "AAA", "prev_close": 1000.0, "price_date": "2026-03-10"},
            {"ticker": "OLD", "prev_close": 100.0, "price_date": "2026-03-10"},
            {"ticker": "SPY", "prev_close": 500.0, "price_date": "2026-03-10"},
        ],
        allow_fractional=allow_fractional,
    )
    fake = _SequencedAlpaca(
        account_sequence=[
            {"cash": "50.0", "equity": "1000.0", "buying_power": "50.0", "status": "ACTIVE"},
            {"cash": "150.0", "equity": "1000.0", "buying_power": "150.0", "status": "ACTIVE"},
            {"cash": "50.0", "equity": "1000.0", "buying_power": "50.0", "status": "ACTIVE"},
        ],
        positions_sequence=[
            [{"symbol": "OLD", "qty": "1", "current_price": "100.0", "market_value": "100.0"}],
            [],
            [],
            [{"symbol": "AAA", "qty": "0.1", "current_price": "1000.0", "market_value": "100.0"}],
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
            {"ticker": "OLD", "side": "SELL", "shares": 1.0, "entry_price": 100.0, "notional": 100.0, "reason": "exit"},
        ],
    )

    assert [side for side, _, _, _ in fake.submitted] == expected_sides
    if expected_buy_qty is not None:
        assert fake.submitted[1][1] == "AAA"
        assert fake.submitted[1][2] == pytest.approx(expected_buy_qty)
    assert result["post_sell_rebudget"]["status"] == expected_status


def test_phase3_post_sell_rebudget_partial_fill_uses_confirmed_proceeds_only(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RUN_OUTPUT_ROOT", str(tmp_path / "outputs" / "runs" / "run-p3-partial"))
    monkeypatch.setenv("ALPACA_SELL_PHASE_TIMEOUT_SECONDS", "0")
    monkeypatch.setenv("ALPACA_SELL_PHASE_POLL_INTERVAL_SECONDS", "0")
    trades = pd.DataFrame(
        [
            {"ticker": "OLD", "side": "SELL", "shares": 4.0, "quantity": 4.0, "price": 100.0, "notional": 400.0, "slippage_cost": 0.0, "reason": "exit"},
            {"ticker": "AAA", "side": "BUY", "shares": 4.0, "quantity": 4.0, "price": 100.0, "notional": 400.0, "slippage_cost": 0.0, "reason": "stale_precompute"},
        ]
    )
    _mock_open_market(
        monkeypatch,
        trades_df=trades,
        trade_meta={"target_investable_dollars": 950.0, "scaled_tickers": [], "overspend_prevented": False},
        holdings=pd.DataFrame([{"ticker": "OLD", "sleeve": "core", "shares": 4.0}]),
        target_rows=[{"ticker": "AAA", "target_weight": 0.95, "sleeve": "core"}],
        cash_target_weight=0.05,
        prev_close_rows=[
            {"ticker": "AAA", "prev_close": 100.0, "price_date": "2026-03-10"},
            {"ticker": "OLD", "prev_close": 100.0, "price_date": "2026-03-10"},
            {"ticker": "SPY", "prev_close": 500.0, "price_date": "2026-03-10"},
        ],
    )

    class _PartialSellAlpaca(_SequencedAlpaca):
        def get_order(self, order_id):
            if str(order_id) == "alpaca-1":
                return {
                    "id": "alpaca-1",
                    "status": "accepted",
                    "submitted_at": "2026-03-11T10:00:00-05:00",
                    "filled_qty": "2",
                }
            return super().get_order(order_id)

    fake = _PartialSellAlpaca(
        account_sequence=[
            {"cash": "100.0", "equity": "1000.0", "buying_power": "100.0", "status": "ACTIVE"},
            {"cash": "300.0", "equity": "1000.0", "buying_power": "300.0", "status": "ACTIVE"},
            {"cash": "50.0", "equity": "1000.0", "buying_power": "50.0", "status": "ACTIVE"},
        ],
        positions_sequence=[
            [{"symbol": "OLD", "qty": "4", "current_price": "100.0", "market_value": "400.0"}],
            [{"symbol": "OLD", "qty": "2", "current_price": "100.0", "market_value": "200.0"}],
            [
                {"symbol": "OLD", "qty": "2", "current_price": "100.0", "market_value": "200.0"},
                {"symbol": "AAA", "qty": "2.5", "current_price": "100.0", "market_value": "250.0"},
            ],
        ],
    )
    monkeypatch.setattr(_PartialSellAlpaca, "from_env", classmethod(lambda cls: fake), raising=False)
    monkeypatch.setattr(broker, "AlpacaBroker", _PartialSellAlpaca)

    result = broker.run_paper_day(
        run_date="2026-03-11",
        signals_path="signals.json",
        ledger_path="ledger.csv",
        trades_path="trades.csv",
        config_path="config.json",
        now_et=dt.datetime(2026, 3, 11, 10, 0, tzinfo=ZoneInfo("America/New_York")),
        precomputed_trade_plan=[
            {"ticker": "OLD", "side": "SELL", "shares": 4.0, "entry_price": 100.0, "notional": 400.0, "reason": "exit"},
            {"ticker": "AAA", "side": "BUY", "shares": 4.0, "entry_price": 100.0, "notional": 400.0, "reason": "stale_precompute"},
        ],
    )

    assert [side for side, _, _, _ in fake.submitted] == ["SELL", "BUY"]
    assert fake.submitted[1][2] == pytest.approx(2.5)
    rebudget = result["post_sell_rebudget"]
    assert result["sell_phase_status"] == "TIMEOUT"
    assert float(rebudget["confirmed_sell_proceeds"]) == pytest.approx(200.0)
    assert float(rebudget["buy_budget_after_safeguards"]) == pytest.approx(250.0)
    assert "sell_phase_not_fully_confirmed" in rebudget["reason_codes"]
    assert "pending_sells_excluded_from_buy_budget" in rebudget["reason_codes"]


@pytest.mark.parametrize("sell_status", ["accepted", "rejected"])
def test_phase3_post_sell_rebudget_unconfirmed_sell_uses_cash_only(tmp_path, monkeypatch, sell_status):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RUN_OUTPUT_ROOT", str(tmp_path / "outputs" / "runs" / f"run-p3-{sell_status}"))
    monkeypatch.setenv("ALPACA_SELL_PHASE_TIMEOUT_SECONDS", "0")
    monkeypatch.setenv("ALPACA_SELL_PHASE_POLL_INTERVAL_SECONDS", "0")
    trades = pd.DataFrame(
        [
            {"ticker": "OLD", "side": "SELL", "shares": 4.0, "quantity": 4.0, "price": 100.0, "notional": 400.0, "slippage_cost": 0.0, "reason": "exit"},
            {"ticker": "AAA", "side": "BUY", "shares": 4.0, "quantity": 4.0, "price": 100.0, "notional": 400.0, "slippage_cost": 0.0, "reason": "stale_precompute"},
        ]
    )
    _mock_open_market(
        monkeypatch,
        trades_df=trades,
        trade_meta={"target_investable_dollars": 950.0, "scaled_tickers": [], "overspend_prevented": False},
        holdings=pd.DataFrame([{"ticker": "OLD", "sleeve": "core", "shares": 4.0}]),
        target_rows=[{"ticker": "AAA", "target_weight": 0.95, "sleeve": "core"}],
        cash_target_weight=0.05,
        prev_close_rows=[
            {"ticker": "AAA", "prev_close": 100.0, "price_date": "2026-03-10"},
            {"ticker": "OLD", "prev_close": 100.0, "price_date": "2026-03-10"},
            {"ticker": "SPY", "prev_close": 500.0, "price_date": "2026-03-10"},
        ],
    )

    class _UnconfirmedSellAlpaca(_SequencedAlpaca):
        def get_order(self, order_id):
            if str(order_id) == "alpaca-1":
                return {
                    "id": "alpaca-1",
                    "status": sell_status,
                    "submitted_at": "2026-03-11T10:00:00-05:00",
                    "filled_qty": "0",
                }
            return super().get_order(order_id)

    fake = _UnconfirmedSellAlpaca(
        account_sequence=[
            {"cash": "100.0", "equity": "1000.0", "buying_power": "100.0", "status": "ACTIVE"},
            {"cash": "100.0", "equity": "1000.0", "buying_power": "100.0", "status": "ACTIVE"},
            {"cash": "50.0", "equity": "1000.0", "buying_power": "50.0", "status": "ACTIVE"},
        ],
        positions_sequence=[
            [{"symbol": "OLD", "qty": "4", "current_price": "100.0", "market_value": "400.0"}],
            [{"symbol": "OLD", "qty": "4", "current_price": "100.0", "market_value": "400.0"}],
            [
                {"symbol": "OLD", "qty": "4", "current_price": "100.0", "market_value": "400.0"},
                {"symbol": "AAA", "qty": "0.5", "current_price": "100.0", "market_value": "50.0"},
            ],
        ],
    )
    monkeypatch.setattr(_UnconfirmedSellAlpaca, "from_env", classmethod(lambda cls: fake), raising=False)
    monkeypatch.setattr(broker, "AlpacaBroker", _UnconfirmedSellAlpaca)

    result = broker.run_paper_day(
        run_date="2026-03-11",
        signals_path="signals.json",
        ledger_path="ledger.csv",
        trades_path="trades.csv",
        config_path="config.json",
        now_et=dt.datetime(2026, 3, 11, 10, 0, tzinfo=ZoneInfo("America/New_York")),
        precomputed_trade_plan=[
            {"ticker": "OLD", "side": "SELL", "shares": 4.0, "entry_price": 100.0, "notional": 400.0, "reason": "exit"},
            {"ticker": "AAA", "side": "BUY", "shares": 4.0, "entry_price": 100.0, "notional": 400.0, "reason": "stale_precompute"},
        ],
    )

    assert [side for side, _, _, _ in fake.submitted] == ["SELL", "BUY"]
    assert fake.submitted[1][2] == pytest.approx(0.5)
    rebudget = result["post_sell_rebudget"]
    assert float(rebudget["confirmed_sell_proceeds"]) == pytest.approx(0.0)
    assert float(rebudget["buy_budget_after_safeguards"]) == pytest.approx(50.0)
    assert float(rebudget["recomputed_buy_notional"]) == pytest.approx(50.0)
    assert "sell_phase_not_fully_confirmed" in rebudget["reason_codes"]
    if sell_status == "accepted":
        assert "pending_sells_excluded_from_buy_budget" in rebudget["reason_codes"]


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


def test_phase3_sell_timeout_does_not_block_affordable_buy(tmp_path, monkeypatch):
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

        def get_order(self, order_id):
            if str(order_id) == "alpaca-1":
                return {
                    "id": "alpaca-1",
                    "status": "accepted",
                    "submitted_at": "2026-03-11T10:00:00-05:00",
                    "filled_qty": "0",
                }
            return super().get_order(order_id)

    fake = _PendingSellAlpaca(
        account_sequence=[
            {"cash": "2000.0", "equity": "10000.0", "buying_power": "2000.0", "status": "ACTIVE"},
            {"cash": "2000.0", "equity": "10000.0", "buying_power": "2000.0", "status": "ACTIVE"},
            {"cash": "2000.0", "equity": "10000.0", "buying_power": "2000.0", "status": "ACTIVE"},
        ],
        positions_sequence=[
            [{"symbol": "AAA", "qty": "1", "current_price": "100.0", "market_value": "100.0"}],
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

    assert [side for side, _, _, _ in fake.submitted] == ["SELL", "BUY"]
    assert result["sell_phase_status"] == "TIMEOUT"
    assert result["buy_phase_decision_reason"] == "buy_submitted_using_available_buying_power"
    assert result["pending_sell_count_at_buy_decision"] == 1
    assert int(result["alpaca_submission_summary"]["buy_phase_submitted"]) == 1
    assert result["budget_skipped_orders"] == []


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
            {"cash": "50.0", "equity": "10000.0", "buying_power": "50.0", "status": "ACTIVE"},
            {"cash": "50.0", "equity": "10000.0", "buying_power": "50.0", "status": "ACTIVE"},
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
    assert result["buy_phase_decision_reason"] == "no_buy_orders"
    rebudget = result["post_sell_rebudget"]
    assert rebudget["status"] == "BLOCKED"
    assert "buy_budget_exhausted" in rebudget["reason_codes"]
    assert [order["ticker"] for order in rebudget["skipped_buy_orders"]][:1] == ["BBB"]

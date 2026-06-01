from __future__ import annotations

import datetime as dt
from collections import Counter
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import paper.paper_broker as broker


def _patch_open_precomputed_run(
    monkeypatch,
    tmp_path,
    *,
    fake_alpaca,
    holdings: pd.DataFrame,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RUN_OUTPUT_ROOT", str(tmp_path / "outputs" / "runs" / "run-cash-gate"))
    monkeypatch.setenv("ALPACA_SELL_PHASE_TIMEOUT_SECONDS", "0")
    monkeypatch.setenv("ALPACA_SELL_PHASE_POLL_INTERVAL_SECONDS", "0")
    cfg = broker.PaperConfig(
        initial_equity=10000.0,
        benchmark_ticker="SPY",
        slippage_bps=0.0,
        allow_fractional=True,
        min_trade_dollars=1.0,
        trading_mode="paper",
    )
    monkeypatch.setattr(broker, "load_config", lambda _path: cfg)
    monkeypatch.setattr(
        broker,
        "read_latest_holdings_from_ledger",
        lambda _path: (holdings.copy(), 1000.0, 10000.0, ""),
    )
    monkeypatch.setattr(
        broker,
        "load_targets",
        lambda *_args, **_kwargs: (
            pd.DataFrame([{"ticker": "SPY", "target_weight": 1.0, "sleeve": "core"}]),
            0.0,
            "2026-05-29",
            "2026-05-28",
        ),
    )
    monkeypatch.setattr(
        broker,
        "fetch_prev_closes_yfinance",
        lambda tickers, asof_date: pd.DataFrame(
            [{"ticker": str(ticker), "prev_close": 100.0, "price_date": asof_date} for ticker in tickers]
        ),
    )
    monkeypatch.setattr(
        broker,
        "validate_open_window",
        lambda **_kwargs: (
            True,
            [],
            {"blocked_tickers": {}, "asof_date": "2026-05-28", "cutoff_date": "2026-05-28"},
        ),
    )
    monkeypatch.setattr(
        broker,
        "market_session_status",
        lambda **_kwargs: SimpleNamespace(
            is_open_now=True,
            is_trading_day=True,
            calendar_name="XNYS",
            reason="MARKET_OPEN",
            session_open_et=dt.datetime(2026, 5, 29, 9, 30, tzinfo=ZoneInfo("America/New_York")),
            session_close_et=dt.datetime(2026, 5, 29, 16, 0, tzinfo=ZoneInfo("America/New_York")),
            next_open_et=None,
        ),
    )
    monkeypatch.setattr(broker, "append_csv", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        broker,
        "_capture_alpaca_posttrade_state",
        lambda **_kwargs: {
            "alpaca_account_snapshot": {"cash": "800.0", "equity": "10000.0", "buying_power": "800.0"},
            "alpaca_positions_snapshot": [],
            "posttrade_account_snapshot_path": "account.json",
            "posttrade_positions_snapshot_path": "positions.json",
            "posttrade_recon_path": "recon.json",
            "posttrade_recon_status": "PASS",
            "posttrade_unresolved_orders": [],
            "posttrade_repair_suggestions": [],
            "posttrade_affected_symbols": [],
            "posttrade_duplicate_fill_suspicions_count": 0,
        },
    )

    fake_cls = fake_alpaca.__class__
    monkeypatch.setattr(fake_cls, "from_env", classmethod(lambda cls: fake_alpaca), raising=False)
    monkeypatch.setattr(broker, "AlpacaBroker", fake_cls)


class _CashGateAlpaca:
    paper = True

    def __init__(self, *, account: dict[str, object], assets: dict[str, dict[str, object] | None]):
        self.account = dict(account)
        self.assets = {str(k).upper(): v for k, v in assets.items()}
        self.submitted: list[dict[str, object]] = []
        self.polls: dict[str, int] = {}

    def list_orders(self, status="open", limit=500):
        return []

    def find_order_by_client_id(self, _client_id):
        return None

    def get_asset(self, symbol):
        return self.assets.get(str(symbol).upper())

    def get_account(self):
        return dict(self.account)

    def get_positions(self):
        return []

    def submit_market_order(self, symbol, qty, side, client_order_id, tif="day"):
        order_id = f"{str(side).lower()}-{str(symbol).lower()}"
        self.submitted.append(
            {
                "symbol": str(symbol).upper(),
                "qty": float(qty),
                "side": str(side).upper(),
                "client_order_id": client_order_id,
                "tif": tif,
                "id": order_id,
            }
        )
        return {
            "id": order_id,
            "client_order_id": client_order_id,
            "symbol": str(symbol).upper(),
            "side": str(side).upper(),
            "status": "accepted",
            "submitted_at": "2026-05-29T09:35:00-04:00",
            "filled_qty": "0",
        }

    def submit_limit_order(self, *_args, **_kwargs):
        raise AssertionError("limit orders are not expected")

    def get_order(self, order_id):
        order_id = str(order_id)
        self.polls[order_id] = self.polls.get(order_id, 0) + 1
        symbol = order_id.split("-", 1)[-1].upper()
        side = "SELL" if order_id.startswith("sell-") else "BUY"
        if side == "SELL":
            return {
                "id": order_id,
                "symbol": symbol,
                "side": side,
                "status": "accepted",
                "submitted_at": "2026-05-29T09:35:00-04:00",
                "filled_qty": "0",
            }
        return {
            "id": order_id,
            "symbol": symbol,
            "side": side,
            "status": "filled",
            "submitted_at": "2026-05-29T09:35:00-04:00",
            "filled_at": "2026-05-29T09:35:03-04:00",
            "filled_qty": "1",
        }


def _asset(symbol: str) -> dict[str, object]:
    return {"symbol": symbol, "status": "active", "tradable": True}


def test_invalid_buy_symbol_blocks_before_sells(monkeypatch, tmp_path):
    fake = _CashGateAlpaca(
        account={"cash": "1000.0", "equity": "10000.0", "buying_power": "1000.0"},
        assets={"ABNB": _asset("ABNB"), "ZZZZ": None},
    )
    _patch_open_precomputed_run(
        monkeypatch,
        tmp_path,
        fake_alpaca=fake,
        holdings=pd.DataFrame([{"ticker": "ABNB", "sleeve": "core", "shares": 1.0}]),
    )

    result = broker.run_paper_day(
        run_date="2026-05-29",
        signals_path="signals.json",
        ledger_path="ledger.csv",
        trades_path="trades.csv",
        config_path="config.json",
        now_et=dt.datetime(2026, 5, 29, 9, 35, tzinfo=ZoneInfo("America/New_York")),
        precomputed_trade_plan=[
            {"ticker": "ABNB", "side": "SELL", "shares": 1, "price": 100.0, "notional": 100.0},
            {"ticker": "ZZZZ", "side": "BUY", "shares": 1, "price": 50.0, "notional": 50.0},
        ],
    )

    assert fake.submitted == []
    assert result["execution_status"] == "HALTED"
    assert result["asset_validation_status"] == "FAIL"
    assert result["invalid_symbols"] == ["ZZZZ"]
    assert "pretrade_asset_validation_failed" in result["halt_reason"]
    assert "invalid_tradable_symbol:ZZZZ" in result["asset_validation_reason"]
    assert result["alpaca_submission_summary"]["submit_attempts"] == 0


def test_bk_alias_resolves_to_bny_before_alpaca_validation(monkeypatch, tmp_path):
    (tmp_path / "data" / "security_master").mkdir(parents=True)
    (tmp_path / "data" / "security_master" / "manual_aliases.json").write_text(
        '{"aliases":{"BK":"BNY"},"notes":{}}\n',
        encoding="utf-8",
    )
    fake = _CashGateAlpaca(
        account={"cash": "1000.0", "equity": "10000.0", "buying_power": "1000.0"},
        assets={"BNY": _asset("BNY")},
    )
    _patch_open_precomputed_run(
        monkeypatch,
        tmp_path,
        fake_alpaca=fake,
        holdings=pd.DataFrame(),
    )

    result = broker.run_paper_day(
        run_date="2026-05-29",
        signals_path="signals.json",
        ledger_path="ledger.csv",
        trades_path="trades.csv",
        config_path="config.json",
        now_et=dt.datetime(2026, 5, 29, 9, 35, tzinfo=ZoneInfo("America/New_York")),
        precomputed_trade_plan=[
            {"ticker": "BK", "side": "BUY", "shares": 1, "price": 50.0, "notional": 50.0},
        ],
    )

    assert [row["symbol"] for row in fake.submitted] == ["BNY"]
    assert result["asset_validation_status"] == "PASS"
    assert result["invalid_symbols"] == []
    assert result["symbol_aliases_applied"] == {"BK": "BNY"}
    assert {row["ticker"] for row in result["trade_plan"]} == {"BNY"}
    assert "BK" not in {row["ticker"] for row in result["trade_plan"]}


def test_pending_sell_does_not_block_affordable_buy(monkeypatch, tmp_path):
    fake = _CashGateAlpaca(
        account={"cash": "1000.0", "equity": "10000.0", "buying_power": "1000.0"},
        assets={"AAA": _asset("AAA"), "BBB": _asset("BBB")},
    )
    _patch_open_precomputed_run(
        monkeypatch,
        tmp_path,
        fake_alpaca=fake,
        holdings=pd.DataFrame([{"ticker": "AAA", "sleeve": "core", "shares": 1.0}]),
    )

    result = broker.run_paper_day(
        run_date="2026-05-29",
        signals_path="signals.json",
        ledger_path="ledger.csv",
        trades_path="trades.csv",
        config_path="config.json",
        now_et=dt.datetime(2026, 5, 29, 9, 35, tzinfo=ZoneInfo("America/New_York")),
        precomputed_trade_plan=[
            {"ticker": "AAA", "side": "SELL", "shares": 1, "price": 100.0, "notional": 100.0},
            {"ticker": "BBB", "side": "BUY", "shares": 1, "price": 200.0, "notional": 200.0},
        ],
    )

    assert [row["side"] for row in fake.submitted] == ["SELL", "BUY"]
    assert result["pending_sell_count_at_buy_decision"] == 1
    assert result["buy_phase_decision_reason"] == "buy_submitted_using_available_buying_power"
    assert result["alpaca_submission_summary"]["buy_phase_submitted"] == 1


def test_pending_sell_blocks_only_unaffordable_buy(monkeypatch, tmp_path):
    fake = _CashGateAlpaca(
        account={"cash": "1000.0", "equity": "10000.0", "buying_power": "1000.0"},
        assets={"AAA": _asset("AAA"), "BIG": _asset("BIG"), "SML": _asset("SML")},
    )
    _patch_open_precomputed_run(
        monkeypatch,
        tmp_path,
        fake_alpaca=fake,
        holdings=pd.DataFrame([{"ticker": "AAA", "sleeve": "core", "shares": 1.0}]),
    )

    result = broker.run_paper_day(
        run_date="2026-05-29",
        signals_path="signals.json",
        ledger_path="ledger.csv",
        trades_path="trades.csv",
        config_path="config.json",
        now_et=dt.datetime(2026, 5, 29, 9, 35, tzinfo=ZoneInfo("America/New_York")),
        precomputed_trade_plan=[
            {"ticker": "AAA", "side": "SELL", "shares": 1, "price": 100.0, "notional": 100.0},
            {"ticker": "BIG", "side": "BUY", "shares": 1, "price": 950.0, "notional": 950.0},
            {"ticker": "SML", "side": "BUY", "shares": 1, "price": 100.0, "notional": 100.0},
        ],
    )

    assert [(row["side"], row["symbol"]) for row in fake.submitted] == [("SELL", "AAA"), ("BUY", "SML")]
    assert result["blocked_buy_count"] == 1
    assert result["pending_sell_count_at_buy_decision"] == 1
    assert result["budget_skipped_orders"][0]["ticker"] == "BIG"
    assert result["budget_skipped_orders"][0]["block_reason"] == "buy_blocked_pending_sells_required_for_cash"
    assert result["alpaca_submission_summary"]["buy_phase_block_reason"] == "buy_blocked_pending_sells_required_for_cash"


def test_order_polling_updates_lifecycle_status():
    class _PollingBroker:
        def list_orders(self, status="open", limit=500):
            return []

        def find_order_by_client_id(self, _client_id):
            return None

        def submit_market_order(self, symbol, qty, side, client_order_id, tif="day"):
            return {
                "id": "alpaca-1",
                "client_order_id": client_order_id,
                "symbol": symbol,
                "side": side,
                "status": "pending_new",
                "submitted_at": "2026-05-29T09:35:00-04:00",
                "filled_qty": "0",
            }

        def get_order(self, order_id):
            assert order_id == "alpaca-1"
            return {
                "id": "alpaca-1",
                "status": "filled",
                "submitted_at": "2026-05-29T09:35:00-04:00",
                "filled_at": "2026-05-29T09:35:05-04:00",
                "filled_qty": "1",
            }

    submissions: list[dict[str, object]] = []
    broker._submit_alpaca_orders(
        alpaca=_PollingBroker(),
        orders=[{"order_id": "oid-1", "ticker": "AAPL", "side": "BUY", "quantity": 1, "order_type": "MKT"}],
        run_date="2026-05-29",
        alpaca_submissions=submissions,
        submission_metadata={},
        idempotent_skips=[],
        idempotent_drop_reasons=Counter(),
        alpaca_submission_summary={},
    )

    assert submissions[0]["first_seen_status"] == "pending_new"
    assert submissions[0]["latest_status"] == "filled"
    assert submissions[0]["filled_qty"] == "1"
    assert submissions[0]["filled_at"] == "2026-05-29T09:35:05-04:00"
    assert submissions[0]["seconds_to_fill"] == pytest.approx(5.0)

import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

import daily_quant_report as dqr
import paper.paper_broker as broker
from paper.mark_to_market import update_nav_timeseries
from paper.state_paths import ensure_paper_state_files


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
        lambda path: (
            pd.DataFrame(columns=["ticker", "sleeve", "shares"]),
            10000.0,
            10000.0,
            "",
        ),
    )
    monkeypatch.setattr(
        broker,
        "load_targets",
        lambda *args, **kwargs: (
            pd.DataFrame([{"ticker": "AAPL", "target_weight": 1.0, "sleeve": "core"}]),
            0.0,
            "2026-02-10",
            "2026-02-09",
        ),
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
        lambda tickers, asof_date: pd.DataFrame(
            [{"ticker": "AAPL", "prev_close": 99.0, "price_date": asof_date}]
        ),
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
            {
                "target_investable_dollars": 10000.0,
                "scaled_tickers": [],
                "overspend_prevented": False,
            },
        )

    monkeypatch.setattr(broker, "build_rebalance_trades", _mock_build_rebalance_trades)
    monkeypatch.setattr(
        broker, "apply_risk_guards", lambda trades, equity_prev, cfg: (trades, [], False)
    )
    monkeypatch.setattr(broker, "append_csv", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        broker,
        "apply_trades_to_holdings",
        lambda **kwargs: (
            pd.DataFrame([{"ticker": "AAPL", "sleeve": "core", "shares": 10.0}]),
            9000.0,
        ),
    )
    monkeypatch.setattr(
        broker,
        "mark_to_market",
        lambda holdings, prices: pd.DataFrame(
            [
                {
                    "ticker": "AAPL",
                    "sleeve": "core",
                    "shares": 10.0,
                    "price": 100.0,
                    "market_value": 1000.0,
                }
            ]
        ),
    )
    monkeypatch.setattr(
        broker,
        "_broker_reconciliation",
        lambda **kwargs: {
            "status": "PASS",
            "cash_delta": 0.0,
            "equity_delta": 0.0,
            "equity_tolerance": 1.0,
            "position_deltas": [],
        },
    )
    monkeypatch.setattr(broker, "_persist_sent_orders", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        broker, "_filter_idempotent_orders", lambda orders, path: (orders, [])
    )
    monkeypatch.setattr(
        broker,
        "_write_shadow_orders",
        lambda run_date, orders: f"outputs/shadow_orders/{run_date}.json",
    )


def test_parse_args_supports_paper_reset_flags():
    args = dqr._parse_args(["--paper-reset", "--paper-start-cash", "12345"])
    assert args.paper_reset is True
    assert abs(float(args.paper_start_cash) - 12345.0) < 1e-9


def test_paper_reset_starts_clean_and_seeds_nav(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ledger_path, trades_path = ensure_paper_state_files()

    dqr._apply_paper_reset(
        trade_date="2026-02-21",
        paper_start_cash=10000.0,
        paper_ledger_path=ledger_path,
        paper_trades_path=trades_path,
    )

    paper_ledger = pd.read_csv(ledger_path)
    paper_trades = pd.read_csv(trades_path)
    nav_ts = pd.read_csv("outputs/perf/nav_timeseries.csv")

    assert paper_ledger.empty
    assert paper_trades.empty
    assert len(nav_ts) == 1
    assert nav_ts.iloc[0]["date"] == "2026-02-21"
    assert float(nav_ts.iloc[0]["equity"]) == 10000.0
    assert float(nav_ts.iloc[0]["cash"]) == 10000.0
    assert float(nav_ts.iloc[0]["gross_exposure"]) == 0.0
    assert float(nav_ts.iloc[0]["net_exposure"]) == 0.0
    assert float(nav_ts.iloc[0]["turnover_dollars"]) == 0.0
    assert float(nav_ts.iloc[0]["turnover_pct"]) == 0.0


def test_turnover_computed_for_execution_and_zero_for_planning(monkeypatch):
    _mock_open_market(monkeypatch)

    open_result = broker.run_paper_day(
        run_date="2026-02-10",
        signals_path="signals/2026-02-10.json",
        ledger_path="paper/ledger.csv",
        trades_path="paper/trades.csv",
        config_path="paper/config_paper.json",
        now_et=dt.datetime(2026, 2, 10, 10, 0, tzinfo=ZoneInfo("America/New_York")),
    )
    assert open_result["execution_status"] == "READY"
    assert abs(float(open_result["turnover_notional"]) - 1000.0) < 1e-9
    assert abs(float(open_result["turnover_pct"]) - 0.1) < 1e-9

    closed_result = broker.run_paper_day(
        run_date="2026-02-10",
        signals_path="signals/2026-02-10.json",
        ledger_path="paper/ledger.csv",
        trades_path="paper/trades.csv",
        config_path="paper/config_paper.json",
        now_et=dt.datetime(2026, 2, 10, 8, 0, tzinfo=ZoneInfo("America/New_York")),
    )
    assert closed_result["execution_status"] == "PLANNED"
    assert float(closed_result["turnover_notional"]) == 0.0
    assert float(closed_result["turnover_pct"]) == 0.0
    assert float(closed_result["gross_exposure"]) == 0.0
    assert float(closed_result["achieved_cash_weight"]) == 1.0


def test_nav_timeseries_overwrites_duplicate_date_and_health_agrees(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    nav = {
        "equity": 10000.0,
        "cash": 9000.0,
        "gross_exposure": 0.1,
        "net_exposure": 0.1,
    }
    ledger = pd.DataFrame([{"trade_date": "2026-02-10", "notional": 1000.0}])
    path = update_nav_timeseries("2026-02-10", nav, ledger)
    update_nav_timeseries("2026-02-10", nav, ledger)

    ts = pd.read_csv(path)
    assert int((ts["date"] == "2026-02-10").sum()) == 1

    paper_summary = {
        "trade_plan": [],
        "num_trades": 1,
        "total_equity": 10000.0,
        "cash": 9000.0,
        "achieved_cash_weight": 0.9,
        "gross_exposure": 0.1,
        "net_exposure": 0.1,
        "turnover_notional": 1000.0,
        "turnover_pct": 0.1,
        "market_guard": {"status": "OPEN"},
    }
    health = dqr._build_health_payload(
        trade_date="2026-02-10",
        paper_summary=paper_summary,
        execution_payload={"trades": []},
        nav_ts_path=path,
        should_execute=True,
        leverage_enabled=False,
    )

    assert abs(float(health["nav_equity_last_row"]) - float(health["broker_equity"])) < 1e-9
    assert abs(float(health["turnover_dollars"]) - 1000.0) < 1e-9

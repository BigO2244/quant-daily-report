import datetime as dt
import json
from pathlib import Path

import pandas as pd
import pytest

from paper import paper_broker


def _write_config(path: Path, mode: str = "shadow", risk_action: str = "hard_stop", turnover: float = 1.5) -> None:
    path.write_text(
        json.dumps(
            {
                "initial_equity": 10000.0,
                "benchmark_ticker": "SPY",
                "execution": {"price": "next_open", "slippage_bps": 0},
                "constraints": {
                    "allow_fractional_shares": False,
                    "min_trade_dollars": 0.0,
                    "cash_buffer_bps": 0.0,
                },
                "mode": {"trading_mode": mode, "portfolio_id": "p1", "strategy_version": "s1"},
                "safety": {
                    "market_cutoff_time_et": "15:45",
                    "reconciliation_abs_tolerance_dollars": 1.0,
                    "reconciliation_bps_tolerance": 1.0,
                    "halt_on_data_error": True,
                    "require_benchmark_price": True,
                },
                "risk": {
                    "max_turnover_pct": turnover,
                    "max_trades_per_day": 10,
                    "max_position_change_pct": 1.0,
                    "action": risk_action,
                },
            }
        )
    )


def _write_signals(path: Path, snapshot_date: str = "2025-01-06") -> None:
    path.write_text(
        json.dumps(
            {
                "snapshot_date": snapshot_date,
                "signals": [
                    {"ticker": "AAA", "sleeve": "core", "target_weight": 0.6},
                    {"ticker": "BBB", "sleeve": "core", "target_weight": 0.4},
                ],
            }
        )
    )


def _mock_prices(run_date: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"ticker": "AAA", "open": 100.0, "price_date": run_date},
            {"ticker": "BBB", "open": 50.0, "price_date": run_date},
            {"ticker": "SPY", "open": 500.0, "price_date": run_date},
        ]
    )


def test_idempotent_rerun_same_day_skips_duplicate_orders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(paper_broker, "fetch_open_prices_yfinance", lambda tickers, run_date: _mock_prices(run_date))

    cfg = tmp_path / "config.json"
    sig = tmp_path / "signals.json"
    _write_config(cfg, mode="shadow")
    _write_signals(sig)

    kwargs = dict(
        run_date="2025-01-06",
        signals_path=str(sig),
        ledger_path=str(tmp_path / "ledger.csv"),
        trades_path=str(tmp_path / "trades.csv"),
        config_path=str(cfg),
        now_et=dt.datetime(2025, 1, 6, 10, 0),
    )
    first = paper_broker.run_paper_day(**kwargs)

    # Simulate replaying the same day from identical starting state while preserving order ledger.
    (tmp_path / "ledger.csv").unlink(missing_ok=True)
    (tmp_path / "trades.csv").unlink(missing_ok=True)
    second = paper_broker.run_paper_day(**kwargs)

    assert len(first["shadow_orders"]) > 0
    assert len(second["shadow_orders"]) == 0
    assert len(second["idempotent_skips"]) > 0


def test_market_closed_day_generates_no_orders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(paper_broker, "fetch_open_prices_yfinance", lambda tickers, run_date: _mock_prices(run_date))

    cfg = tmp_path / "config.json"
    sig = tmp_path / "signals.json"
    _write_config(cfg, mode="shadow")
    _write_signals(sig, snapshot_date="2025-01-11")

    out = paper_broker.run_paper_day(
        run_date="2025-01-11",
        signals_path=str(sig),
        ledger_path=str(tmp_path / "ledger.csv"),
        trades_path=str(tmp_path / "trades.csv"),
        config_path=str(cfg),
        now_et=dt.datetime(2025, 1, 11, 10, 0),
    )

    assert out["market_status"] == "CLOSED"
    assert len(out["shadow_orders"]) == 0


def test_stale_price_day_hard_stops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)

    def stale_prices(tickers, run_date):
        df = _mock_prices(run_date)
        df["price_date"] = "2025-01-03"
        return df

    monkeypatch.setattr(paper_broker, "fetch_open_prices_yfinance", stale_prices)

    cfg = tmp_path / "config.json"
    sig = tmp_path / "signals.json"
    _write_config(cfg, mode="shadow")
    _write_signals(sig)

    with pytest.raises(RuntimeError, match="stale_prices"):
        paper_broker.run_paper_day(
            run_date="2025-01-06",
            signals_path=str(sig),
            ledger_path=str(tmp_path / "ledger.csv"),
            trades_path=str(tmp_path / "trades.csv"),
            config_path=str(cfg),
            now_et=dt.datetime(2025, 1, 6, 10, 0),
        )


def test_turnover_limit_breach_hard_stops_orders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(paper_broker, "fetch_open_prices_yfinance", lambda tickers, run_date: _mock_prices(run_date))

    cfg = tmp_path / "config.json"
    sig = tmp_path / "signals.json"
    _write_config(cfg, mode="shadow", turnover=0.01, risk_action="hard_stop")
    _write_signals(sig)

    out = paper_broker.run_paper_day(
        run_date="2025-01-06",
        signals_path=str(sig),
        ledger_path=str(tmp_path / "ledger.csv"),
        trades_path=str(tmp_path / "trades.csv"),
        config_path=str(cfg),
        now_et=dt.datetime(2025, 1, 6, 10, 0),
    )

    assert out["num_trades"] == 0
    assert any("max_turnover_pct" in reason for reason in out["blocked_reasons"])


def test_shadow_vs_paper_consistency_on_same_signals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(paper_broker, "fetch_open_prices_yfinance", lambda tickers, run_date: _mock_prices(run_date))

    sig = tmp_path / "signals.json"
    _write_signals(sig)

    cfg_shadow = tmp_path / "cfg_shadow.json"
    cfg_paper = tmp_path / "cfg_paper.json"
    _write_config(cfg_shadow, mode="shadow")
    _write_config(cfg_paper, mode="paper")

    out_shadow = paper_broker.run_paper_day(
        run_date="2025-01-06",
        signals_path=str(sig),
        ledger_path=str(tmp_path / "ledger_shadow.csv"),
        trades_path=str(tmp_path / "trades_shadow.csv"),
        config_path=str(cfg_shadow),
        now_et=dt.datetime(2025, 1, 6, 10, 0),
    )
    out_paper = paper_broker.run_paper_day(
        run_date="2025-01-06",
        signals_path=str(sig),
        ledger_path=str(tmp_path / "ledger_paper.csv"),
        trades_path=str(tmp_path / "trades_paper.csv"),
        config_path=str(cfg_paper),
        now_et=dt.datetime(2025, 1, 6, 10, 0),
    )

    shadow_trades = pd.read_csv(tmp_path / "trades_shadow.csv")
    paper_trades = pd.read_csv(tmp_path / "trades_paper.csv")

    cols = ["ticker", "side", "shares"]
    assert shadow_trades[cols].to_dict("records") == paper_trades[cols].to_dict("records")
    assert out_shadow["num_trades"] == out_paper["num_trades"]

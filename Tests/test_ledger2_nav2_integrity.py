import json

import pandas as pd

import daily_quant_report as dqr
from daily_quant_report import write_integrity_artifact
from paper.ledger2 import append_rows, ensure_dirs, load_ledger
from paper.nav2 import update_nav


def test_ledger2_idempotent_append(tmp_path):
    ledger_path = tmp_path / "outputs" / "ledger" / "trades.csv"
    ensure_dirs(str(ledger_path))
    row = {
        "timestamp_et": "2026-01-01T00:00:00-05:00",
        "run_id": "r1",
        "source": "SHADOW",
        "trade_date": "2026-01-01",
        "asof_date": "2025-12-31",
        "order_id": "oid-1",
        "ticker": "AAPL",
        "sleeve": "main",
        "side": "BUY",
        "quantity": 1,
        "fill_price": 100,
        "notional": 100,
        "fees": 0,
        "reason": "test",
        "execution_status": "READY",
    }
    appended_first, skipped_first = append_rows(str(ledger_path), [row])
    appended_second, skipped_second = append_rows(str(ledger_path), [row])

    assert appended_first == 1
    assert skipped_first == 0
    assert appended_second == 0
    assert skipped_second > 0


def test_nav2_upsert_no_duplicate_dates(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ledger_path = tmp_path / "outputs" / "ledger" / "trades.csv"
    ensure_dirs(str(ledger_path))
    append_rows(
        str(ledger_path),
        [
            {
                "timestamp_et": "2026-01-02T10:00:00-05:00",
                "run_id": "r1",
                "source": "SHADOW",
                "trade_date": "2026-01-02",
                "asof_date": "2026-01-01",
                "order_id": "oid-1",
                "ticker": "AAPL",
                "sleeve": "main",
                "side": "BUY",
                "quantity": 10,
                "fill_price": 100,
                "notional": 1000,
                "fees": 0,
                "reason": "seed",
                "execution_status": "READY",
            }
        ],
    )

    prices = {"AAPL": 110.0}

    def get_price_fn(ticker: str, asof_date: str):
        _ = asof_date
        return prices.get(ticker)

    update_nav("2026-01-02", "2026-01-02", get_price_fn, "SHADOW", "run-1", ledger_path=str(ledger_path))
    update_nav("2026-01-02", "2026-01-02", get_price_fn, "SHADOW", "run-2", ledger_path=str(ledger_path))

    ts = pd.read_csv("outputs/perf/nav_timeseries.csv")
    assert (ts["date"] == "2026-01-02").sum() == 1


def test_integrity_artifact_written(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    payload = {"trade_date": "2026-01-02", "asof_date": "2026-01-01", "mode": "SHADOW"}
    path = write_integrity_artifact("2026-01-01", payload)
    assert (tmp_path / path).exists()
    written = json.loads((tmp_path / path).read_text(encoding="utf-8"))
    assert written["mode"] == "SHADOW"


def test_ledger_schema_with_signal_hash_parses_and_nav2_health_ok(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ledger_path = tmp_path / "outputs" / "ledger" / "trades.csv"
    ensure_dirs(str(ledger_path))

    # Simulate legacy malformed file: header missing signal_hash, row includes it.
    malformed_header = (
        "timestamp_et,run_id,source,trade_date,asof_date,order_id,ticker,sleeve,side,"
        "quantity,fill_price,notional,fees,reason,execution_status\n"
    )
    malformed_row = (
        "2026-01-02T10:00:00-05:00,r1,SHADOW,2026-01-02,2026-01-01,oid-1,AAPL,main,BUY,"
        "10,100,1000,0.0,rebalance_to_target,abc123,FILLED_ESTIMATE\n"
    )
    ledger_path.write_text(malformed_header + malformed_row, encoding="utf-8")

    normalized = load_ledger(str(ledger_path), rewrite_if_needed=True)
    assert "signal_hash" in normalized.columns
    assert normalized.iloc[0]["reason"] == "rebalance_to_target"
    assert normalized.iloc[0]["signal_hash"] == "abc123"
    assert normalized.iloc[0]["execution_status"] == "FILLED_ESTIMATE"
    assert float(normalized.iloc[0]["fees"]) == 0.0

    def get_price_fn(ticker: str, asof_date: str):
        _ = asof_date
        return {"AAPL": 110.0}.get(ticker)

    nav_result = update_nav(
        "2026-01-02",
        "2026-01-02",
        get_price_fn,
        "SHADOW",
        "run-1",
        ledger_path=str(ledger_path),
    )
    ts = pd.read_csv("outputs/perf/nav_timeseries.csv")
    assert not ts.empty
    assert (ts["date"] == "2026-01-02").sum() == 1

    equity = float(nav_result["equity"])
    cash = float(nav_result["cash"])
    gross = float(ts.iloc[-1]["gross_exposure"])
    net = float(ts.iloc[-1]["net_exposure"])
    achieved_cash_weight = (cash / equity) if equity > 0 else 0.0

    health = dqr._build_health_payload(
        trade_date="2026-01-02",
        paper_summary={
            "trade_plan": [],
            "num_trades": 1,
            "total_equity": equity,
            "cash": cash,
            "achieved_cash_weight": achieved_cash_weight,
            "gross_exposure": gross,
            "net_exposure": net,
            "turnover_notional": 1000.0,
            "turnover_pct": 0.1,
            "market_guard": {"status": "OPEN"},
        },
        execution_payload={"trades": []},
        nav_ts_path="outputs/perf/nav_timeseries.csv",
        should_execute=True,
        leverage_enabled=False,
    )

    assert abs(float(health["broker_equity"]) - float(health["nav_equity_last_row"])) < 1e-9

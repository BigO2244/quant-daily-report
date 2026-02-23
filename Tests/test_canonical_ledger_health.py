import json

import pandas as pd
import pytest

import daily_quant_report as dqr
from paper.ledger2 import append_rows, load_ledger
from paper.paths import (
    LEDGER_TRADES_LEGACY_PATH,
    LEDGER_TRADES_PATH,
    ensure_no_legacy_ledger,
)


def _row(order_id: str, *, trade_date: str = "2026-02-23", notional: float = 1000.0) -> dict:
    qty = abs(float(notional)) / 100.0
    return {
        "timestamp_et": "2026-02-23T10:00:00-05:00",
        "run_id": "r1",
        "source": "SHADOW",
        "trade_date": trade_date,
        "asof_date": "2026-02-22",
        "order_id": order_id,
        "ticker": "AAPL",
        "sleeve": "main",
        "side": "BUY",
        "quantity": qty,
        "fill_price": 100.0,
        "notional": abs(float(notional)),
        "fees": 0.0,
        "reason": "rebalance_to_target",
        "signal_hash": "h",
        "execution_status": "FILLED_ESTIMATE",
    }


def _write_nav(path: str, *, equity: float, cash: float) -> None:
    out = pd.DataFrame(
        [
            {
                "date": "2026-02-23",
                "equity": float(equity),
                "cash": float(cash),
                "gross_exposure": 0.1,
                "net_exposure": 0.1,
                "return_1d": 0.0,
                "turnover_dollars": 0.0,
                "turnover_pct": 0.0,
                "turnover": 0.0,
            }
        ]
    )
    out.to_csv(path, index=False)


def test_ledger_writes_canonical_path_only(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    append_rows(str(LEDGER_TRADES_PATH), [_row("oid-1")])
    assert LEDGER_TRADES_PATH.exists()
    assert not LEDGER_TRADES_LEGACY_PATH.exists()


def test_legacy_guard_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    LEDGER_TRADES_LEGACY_PATH.parent.mkdir(parents=True, exist_ok=True)
    LEDGER_TRADES_LEGACY_PATH.write_text("x\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="FORBIDDEN: outputs/ledger/trades_legacy.csv exists"):
        ensure_no_legacy_ledger(logger=dqr.logger, when="test")


def test_health_json_written_on_fail(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    append_rows(str(LEDGER_TRADES_PATH), [_row("oid-1")])
    nav_path = tmp_path / "outputs" / "perf" / "nav_timeseries.csv"
    nav_path.parent.mkdir(parents=True, exist_ok=True)
    _write_nav(str(nav_path), equity=10020.0, cash=9000.0)

    payload = dqr._build_health_payload(
        trade_date="2026-02-23",
        paper_summary={
            "run_id": "run-1",
            "trade_plan": [],
            "num_trades": 1,
            "total_equity": 9990.0,  # force mismatch vs execution basis
            "cash": 9000.0,
            "achieved_cash_weight": 0.9009009009,
            "gross_exposure": 0.1,
            "net_exposure": 0.1,
            "market_guard": {"status": "OPEN"},
        },
        execution_payload={"trades": []},
        nav_ts_path=str(nav_path),
        ledger_path=str(LEDGER_TRADES_PATH),
        should_execute=True,
        leverage_enabled=False,
    )
    assert payload["status"] == "FAIL"

    with pytest.raises(AssertionError):
        dqr._finalize_health_payload("2026-02-23", payload)

    health_path = tmp_path / "outputs" / "daily" / "health_2026-02-23.json"
    assert health_path.exists()
    written = json.loads(health_path.read_text(encoding="utf-8"))
    assert written["status"] == "FAIL"
    assert "execution-basis equity" in str(written.get("error", ""))


def test_ledger_idempotency_no_double_count(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    row1 = _row("oid-1", notional=1000.0)
    row2 = _row("oid-2", notional=500.0)

    append_rows(str(LEDGER_TRADES_PATH), [row1, row2, row1])
    append_rows(str(LEDGER_TRADES_PATH), [row1, row2])

    ledger = load_ledger(str(LEDGER_TRADES_PATH), rewrite_if_needed=True)
    assert len(ledger) == 2
    assert sorted(ledger["order_id"].tolist()) == ["oid-1", "oid-2"]

    metrics = dqr._compute_execution_basis_metrics(
        trade_date="2026-02-23",
        ledger_path=str(LEDGER_TRADES_PATH),
        starting_cash=10000.0,
    )
    assert abs(float(metrics["turnover_dollars"]) - 1500.0) < 1e-9


def test_exec_basis_equity_ties_broker(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    append_rows(str(LEDGER_TRADES_PATH), [_row("oid-1", notional=1000.0)])
    nav_path = tmp_path / "outputs" / "perf" / "nav_timeseries.csv"
    nav_path.parent.mkdir(parents=True, exist_ok=True)
    _write_nav(str(nav_path), equity=10020.0, cash=9000.0)  # mark basis intentionally different

    payload = dqr._build_health_payload(
        trade_date="2026-02-23",
        paper_summary={
            "run_id": "run-1",
            "trade_plan": [],
            "num_trades": 1,
            "total_equity": 10000.0,
            "cash": 9000.0,
            "achieved_cash_weight": 0.9,
            "gross_exposure": 0.1,
            "net_exposure": 0.1,
            "market_guard": {"status": "OPEN"},
        },
        execution_payload={"trades": []},
        nav_ts_path=str(nav_path),
        ledger_path=str(LEDGER_TRADES_PATH),
        should_execute=True,
        leverage_enabled=False,
    )

    assert payload["status"] == "PASS"
    assert abs(float(payload["execution_basis_equity"]) - float(payload["broker_equity"])) <= 0.1

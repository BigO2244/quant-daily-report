import json
import math
from pathlib import Path

import pandas as pd

import daily_quant_report as dqr
from core.sleeve_numeric_diagnostics import (
    REASON_INPUT_PRICE_NAN,
    REASON_MTM_EQUITY_NON_FINITE,
    REASON_SLEEVE_TERMINAL_EQUITY_NAN,
    diagnostic_event,
)


def test_non_finite_terminal_equity_writes_trace_artifact(tmp_path, monkeypatch):
    run_root = tmp_path / "run"
    dqr.ensure_dir(run_root / "audit")
    monkeypatch.setattr(
        dqr,
        "_RUN_CONTEXT",
        dqr.RunContext(
            run_id="rid",
            run_root=run_root,
            allow_overwrite=True,
            created_at="2026-06-17T00:00:00Z",
            git_sha="abc",
            mode="paper",
            trading_mode="paper",
            report_date="2026-06-17",
        ),
    )
    equity = pd.DataFrame(
        {"date": pd.to_datetime(["2026-06-16"]), "equity": [float("nan")]}
    )
    equity.attrs["numeric_diagnostics"] = [
        diagnostic_event(
            sleeve_id="sleeve_trend",
            calculation_stage="mark_to_market",
            reason_code=REASON_MTM_EQUITY_NON_FINITE,
            ticker="ABC",
            date=pd.Timestamp("2026-06-16"),
            field="equity",
            value=float("nan"),
            downstream_effect="terminal sleeve equity may become non-finite",
        )
    ]

    valid, reason = dqr._sleeve_is_valid(
        equity,
        sleeve_id="sleeve_trend",
        write_trace=True,
    )

    assert valid is False
    assert REASON_SLEEVE_TERMINAL_EQUITY_NAN in json.loads(
        Path(equity.attrs["numeric_trace_path"]).read_text()
    )["reason_code"]
    payload = json.loads(Path(equity.attrs["numeric_trace_path"]).read_text())
    assert payload["sleeve_id"] == "sleeve_trend"
    assert payload["trade_date"] == "2026-06-17"
    assert payload["first_event"]["ticker"] == "ABC"
    assert payload["first_event"]["field"] == "equity"
    assert "diagnostic=" in reason


def test_trend_backtest_records_non_finite_mtm_price_and_equity():
    from sleeves.sleeve_trend.backtest import backtest

    dates = pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06"])
    signals = pd.DataFrame(
        [
            {
                "date": dates[0],
                "ticker": "ABC",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "signal_long": True,
                "signal_short": False,
                "final_signal": 90.0,
                "atr": 1.0,
                "sector": "Tech",
            },
            {
                "date": dates[1],
                "ticker": "ABC",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": float("nan"),
                "signal_long": False,
                "signal_short": False,
                "final_signal": 0.0,
                "atr": 1.0,
                "sector": "Tech",
            },
            {
                "date": dates[2],
                "ticker": "ABC",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "signal_long": False,
                "signal_short": False,
                "final_signal": 0.0,
                "atr": 1.0,
                "sector": "Tech",
            },
        ]
    )

    equity, trades = backtest(signals)

    diagnostics = equity.attrs["numeric_diagnostics"]
    assert any(
        event["reason_code"] == REASON_INPUT_PRICE_NAN
        and event["calculation_stage"] == "mark_to_market"
        and event["ticker"] == "ABC"
        and event["field"] == "close"
        for event in diagnostics
    )
    assert any(
        event["reason_code"] == REASON_MTM_EQUITY_NON_FINITE
        and event["field"] == "equity"
        for event in diagnostics
    )
    assert math.isnan(float(equity.loc[equity["date"] == dates[1], "equity"].iloc[0]))
    assert trades.attrs["numeric_diagnostics"] == diagnostics


def test_snapshot_email_shows_sleeve_cash_route_diagnostic():
    snapshot = {
        "asof": "2026-06-17",
        "allocations": {
            "sleeves": {"sleeve_trend": 0.0, "sleeve_2": 0.2, "charlie_munger": 0.0},
            "cash": 0.8,
        },
        "target_cash_weight": 0.8,
        "performance_summary": {},
        "performance_diagnostics": {"current_equity": 10000.0},
        "alpha_attribution": {"ok": False},
        "charlie_munger": {},
        "orders": [],
        "skipped_trades": [],
        "nav_metrics": {"equity": 10000.0, "cash": 8000.0},
        "sleeve_states": {"sleeve_trend": {"active": False, "reason": "invalid terminal equity (nan)"}},
        "allocation_diagnostics": {
            "sleeve_1": {
                "desired_allocation": 0.0,
                "achieved_invested": 0.0,
                "forced_cash": 0.558,
                "selected_names": 10,
                "min_required_names": 10,
                "limiting_constraint": "none",
                "cash_routing": [
                    {
                        "sleeve_id": "sleeve_trend",
                        "invalid_reason": "invalid terminal equity (nan)",
                        "routed_weight": 0.558,
                        "diagnostic_artifact": "outputs/runs/rid/audit/sleeve_numeric_trace_sleeve_trend_2026-06-17.json",
                    }
                ],
            }
        },
    }

    _, body = dqr.create_snapshot_email(
        snapshot,
        execution_payload={"mode": "SHADOW", "trades": [], "executable_trades_count": 0},
    )

    assert "Cash route: sleeve_trend 55.80%" in body
    assert "invalid terminal equity (nan)" in body
    assert "sleeve_numeric_trace_sleeve_trend_2026-06-17.json" in body


def test_execution_payload_distinguishes_current_and_target_exposure_labels():
    payload = dqr.build_execution_email_payload(
        trade_date="2026-06-17",
        daily_snapshot={
            "risk_levels": [],
            "holdings": [{"ticker": "ABC", "shares": 10, "last_price": 95.0}],
            "target_cash_weight": 0.5583412399742469,
        },
        paper_summary={
            "trading_mode": "paper",
            "total_equity": 1000.0,
            "cash": 50.0,
            "achieved_cash_weight": 0.05,
            "risk_meta": {},
        },
    )

    risk_summary = payload["risk_summary"]
    assert risk_summary["Target cash weight (%)"] == "55.83%"
    assert risk_summary["Target gross exposure (%)"] == "44.17%"
    assert risk_summary["Current achieved cash weight (%)"] == "5.00%"
    assert risk_summary["Current achieved gross exposure (%)"] == "95.00%"

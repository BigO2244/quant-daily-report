import pandas as pd

from daily_quant_report import _format_df_for_email, build_execution_email_payload
from paper.build_execution_email import build_execution_email_text, build_execution_email_html
from paper.paper_report import build_paper_report_html


def test_snapshot_paper_execution_unavailable_in_shadow_without_zero_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ledger_path = tmp_path / "outputs" / "ledger" / "ledger.csv"
    trades_path = tmp_path / "outputs" / "paper_state" / "trades.csv"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([
        {"date": "2026-02-16", "ticker": "AAPL", "total_equity": 10123.45, "cash": 1200.0, "market_value": 8923.45, "sleeve": "sleeve_2", "shares": 10, "price": 892.345}
    ]).to_csv(ledger_path, index=False)

    html = build_paper_report_html(
        run_date="2026-02-17",
        ledger_path=str(ledger_path),
        trades_path=str(trades_path),
        shadow_status={"trading_mode": "SHADOW", "missing_inputs": ["outputs/perf/nav_timeseries.csv"]},
    )

    assert "Paper execution summary unavailable (SHADOW run)" in html
    assert "$0.00" not in html


def test_execution_email_no_orders_includes_why_block_with_reason():
    payload = {
        "trade_date": "2026-02-17",
        "mode": "SHADOW",
        "execution_status": "READY",
        "trades": [],
        "proposed_trades_intent": 5,
        "executable_trades_count": 0,
        "no_trades_reason": "No executable trades after minimum-notional filter",
    }

    _, body = build_execution_email_text(payload)
    _, html = build_execution_email_html(payload)

    assert "WHY NO ORDERS?" in body
    assert "No executable trades after minimum-notional filter" in body
    assert "Why no orders?" in html


def test_shares_delta_formats_as_quantity_not_percent():
    out = _format_df_for_email(pd.DataFrame([{"shares_delta": 1.25}]))
    assert "%" not in str(out.loc[0, "shares_delta"])


def test_snapshot_missing_inputs_uses_canonical_ledger_trades_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ledger_path = tmp_path / "outputs" / "ledger" / "ledger.csv"
    trades_path = tmp_path / "outputs" / "paper_state" / "trades.csv"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "date": "2026-02-17",
                "ticker": "AAPL",
                "total_equity": 10123.45,
                "cash": 1200.0,
                "market_value": 8923.45,
                "sleeve": "sleeve_2",
                "shares": 10,
                "price": 892.345,
            }
        ]
    ).to_csv(ledger_path, index=False)

    html = build_paper_report_html(
        run_date="2026-02-17",
        ledger_path=str(ledger_path),
        trades_path=str(trades_path),
        shadow_status={"trading_mode": "SHADOW", "market_status": "OPEN"},
    )

    assert "Paper execution summary unavailable (SHADOW run)" in html
    assert "outputs/ledger/trades.csv" in html
    assert "outputs/paper_state/ledger.csv" not in html


def test_execution_email_risk_summary_uses_unavailable_for_missing_turnover_metrics():
    payload = {
        "trade_date": "2026-02-05",
        "mode": "SHADOW",
        "execution_status": "READY",
        "trades": [],
        "risk_summary": {
            "Turnover requested ($)": "unavailable",
            "Turnover cap ($)": "unavailable",
            "Turnover scale": "unavailable",
        },
    }

    _, body = build_execution_email_text(payload)

    assert "Turnover requested ($): unavailable" in body
    assert "Turnover cap ($): unavailable" in body
    assert "Turnover scale: unavailable" in body


def test_build_execution_payload_turnover_metrics_unavailable_when_missing():
    payload = build_execution_email_payload(
        trade_date="2026-02-17",
        daily_snapshot={"risk_levels": [], "holdings": []},
        paper_summary={
            "trading_mode": "SHADOW",
            "market_status": "OPEN",
            "risk_meta": {},
        },
    )

    assert payload["risk_summary"]["Turnover requested ($)"] == "unavailable"
    assert payload["risk_summary"]["Turnover cap ($)"] == "unavailable"
    assert payload["risk_summary"]["Turnover scale"] == "unavailable"

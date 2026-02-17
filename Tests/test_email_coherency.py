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
    assert payload["risk_summary"]["Target cash weight (%)"] == "unavailable"
    assert payload["risk_summary"]["Achieved cash weight (%)"] == "unavailable"
    assert payload["investable_dollars"] is None
    assert payload["equity"] is None
    assert payload["cash_target_dollars"] is None


def test_build_execution_payload_uses_execution_filter_stats_and_intent_count():
    payload = build_execution_email_payload(
        trade_date="2026-02-17",
        daily_snapshot={
            "risk_levels": [{"ticker": "AAPL", "entry_price": 200.0}],
            "holdings": [],
            "proposed_trades": [{"ticker": "AAPL"}, {"ticker": "MSFT"}],
        },
        paper_summary={
            "trading_mode": "SHADOW",
            "market_status": "OPEN",
            "execution_trades": [{"ticker": "AAPL", "side": "BUY", "shares": 2, "notional": 400.0}],
            "execution_filter": {
                "raw": 2,
                "rounded": 2,
                "kept": 1,
                "dropped_zero_shares": 1,
                "dropped_min_notional": 0,
            },
            "min_trade_dollars": 100.0,
            "risk_meta": {},
        },
    )

    assert payload["filter_stats"]["dropped_zero_shares"] == 1
    assert payload["proposed_trades_intent_count"] == 2
    assert payload["executable_trades_count"] == len(payload["trades"])


def test_execution_email_no_trades_with_missing_filter_stats_and_intent_shows_unavailable():
    payload = {
        "trade_date": "2026-02-17",
        "mode": "SHADOW",
        "execution_status": "READY",
        "trades": [],
        "executable_trades_count": 0,
        "filter_stats": None,
        "min_trade_dollars": None,
    }

    _, body = build_execution_email_text(payload)

    assert "Proposed Trades (Intent) | unavailable" in body
    assert "Dropped Zero Shares | unavailable" in body
    assert "Dropped Min Notional | unavailable" in body
    assert "Min Trade Dollars | unavailable" in body


def test_execution_email_turnover_none_renders_unavailable_not_zero():
    payload = build_execution_email_payload(
        trade_date="2026-02-17",
        daily_snapshot={"risk_levels": [], "holdings": [], "proposed_trades": []},
        paper_summary={
            "trading_mode": "SHADOW",
            "market_status": "OPEN",
            "risk_meta": {
                "turnover_requested": None,
                "turnover_cap": None,
                "turnover_scale": None,
            },
        },
    )

    assert payload["risk_summary"]["Turnover requested ($)"] == "unavailable"
    assert payload["risk_summary"]["Turnover cap ($)"] == "unavailable"
    assert payload["risk_summary"]["Turnover scale"] == "unavailable"


def test_execution_email_missing_paper_summary_does_not_emit_zero_placeholders():
    payload = build_execution_email_payload(
        trade_date="2026-02-17",
        daily_snapshot={"risk_levels": [], "holdings": []},
        paper_summary=None,
    )

    _, body = build_execution_email_text(payload)

    assert "Target cash weight (%): unavailable" in body
    assert "Achieved cash weight (%): unavailable" in body
    assert "$0.00" not in body


def test_snapshot_market_status_uses_market_guard_when_status_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    trades_path = tmp_path / "outputs" / "ledger" / "trades.csv"
    nav_path = tmp_path / "outputs" / "perf" / "nav_timeseries.csv"
    paper_ledger_path = tmp_path / "outputs" / "paper_state" / "ledger.csv"
    state_trades_path = tmp_path / "outputs" / "paper_state" / "trades.csv"

    trades_path.parent.mkdir(parents=True, exist_ok=True)
    nav_path.parent.mkdir(parents=True, exist_ok=True)
    paper_ledger_path.parent.mkdir(parents=True, exist_ok=True)

    pd.DataFrame([
        {"date": "2026-02-17", "ticker": "AAPL", "total_equity": 10123.45, "cash": 1200.0, "market_value": 8923.45, "sleeve": "sleeve_2", "shares": 10, "price": 892.345}
    ]).to_csv(paper_ledger_path, index=False)
    pd.DataFrame([{"date": "2026-02-17", "ticker": "AAPL", "side": "BUY", "shares": 1, "price": 100.0, "slippage_cost": 0.0, "notional": 100.0, "reason": "rebalance"}]).to_csv(state_trades_path, index=False)
    pd.DataFrame([{"date": "2026-02-17", "ticker": "AAPL", "side": "BUY"}]).to_csv(trades_path, index=False)
    pd.DataFrame([{"date": "2026-02-17", "equity": 10123.45, "return_1d": 0.0}]).to_csv(nav_path, index=False)

    html = build_paper_report_html(
        run_date="2026-02-17",
        ledger_path=str(paper_ledger_path),
        trades_path=str(state_trades_path),
        shadow_status={"trading_mode": "SHADOW", "market_status": None, "market_guard": {"status": "OPEN"}},
    )

    assert "Market open/closed:</b> OPEN" in html


def test_turnover_note_not_set_when_turnover_metrics_missing():
    payload = build_execution_email_payload(
        trade_date="2026-02-17",
        daily_snapshot={"risk_levels": [], "holdings": []},
        paper_summary={
            "trading_mode": "SHADOW",
            "market_status": "OPEN",
            "risk_meta": {
                "turnover_scaled": True,
                "turnover_requested": 1000.0,
                "turnover_cap": None,
                "turnover_scale": 0.5,
            },
        },
    )

    assert payload.get("turnover_note") is None

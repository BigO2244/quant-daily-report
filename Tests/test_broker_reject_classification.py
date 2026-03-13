from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from brokers.alpaca_broker import (
    AlpacaSubmissionRejectError,
    BROKER_REJECT_BUYING_POWER,
    BROKER_REJECT_PDT,
    BROKER_REJECT_SHORT_NOT_ALLOWED,
    classify_alpaca_broker_reject,
)
from core.operator_summary import format_execution_health_banner, write_operator_summary
from core.run_pointer import read_latest_run_pointer, write_latest_run_pointer
from core.trading_day_summary import build_trading_day_summary
from paper.paper_broker import _submit_alpaca_orders


def test_classify_pdt_reject_from_alpaca_json_message() -> None:
    reject = classify_alpaca_broker_reject(
        '{"code":40310000,"message":"trade denied due to pattern day trading protection"}'
    )
    assert reject["classification"] == BROKER_REJECT_PDT
    assert reject["code"] == 40310000
    assert "pattern day trading protection" in reject["message"]


def test_classify_other_known_rejects() -> None:
    bp = classify_alpaca_broker_reject("insufficient buying power to complete order")
    short = classify_alpaca_broker_reject("trade denied because short selling is not allowed")
    assert bp["classification"] == BROKER_REJECT_BUYING_POWER
    assert short["classification"] == BROKER_REJECT_SHORT_NOT_ALLOWED


def test_submit_alpaca_orders_raises_structured_reject_for_pdt() -> None:
    class FakeBroker:
        def find_order_by_client_id(self, _client_id):
            return None

        def submit_market_order(self, **_kwargs):
            raise Exception(
                '{"code":40310000,"message":"trade denied due to pattern day trading protection"}'
            )

    try:
        _submit_alpaca_orders(
            alpaca=FakeBroker(),
            orders=[{"order_id": "oid-1", "ticker": "AAPL", "side": "BUY", "quantity": 1, "order_type": "MKT"}],
            run_date="2026-03-13",
            alpaca_submissions=[],
            submission_metadata={},
            idempotent_skips=[],
            idempotent_drop_reasons=Counter(),
            alpaca_submission_summary={},
        )
    except AlpacaSubmissionRejectError as exc:
        assert exc.classification == BROKER_REJECT_PDT
        assert exc.order_id == "oid-1"
        assert exc.symbol == "AAPL"
        assert exc.side == "BUY"
        assert exc.quantity == 1.0
        assert "pattern day trading protection" in exc.broker_message
    else:
        raise AssertionError("Expected AlpacaSubmissionRejectError")


def test_operator_banner_and_trading_day_summary_surface_broker_reject(tmp_path: Path) -> None:
    run_root = tmp_path / "outputs" / "runs" / "run-1"
    run_root.mkdir(parents=True, exist_ok=True)
    broker_dir = run_root / "broker"
    broker_dir.mkdir(parents=True, exist_ok=True)
    (broker_dir / "pretrade_account_snapshot.json").write_text(
        json.dumps(
            {
                "account": {
                    "status": "ACTIVE",
                    "cash": "1000.00",
                    "equity": "25000.00",
                    "buying_power": "1000.00",
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (broker_dir / "pretrade_positions.json").write_text(
        json.dumps({"positions_count": 2}, indent=2),
        encoding="utf-8",
    )

    write_operator_summary(
        run_root,
        run_id="run-1",
        trade_date="2026-03-13",
        mode="ALPACA",
        broker_reject_status=BROKER_REJECT_PDT,
        broker_reject_message="trade denied due to pattern day trading protection",
        duplicate_guard_status="CLEAR",
    )
    banner = format_execution_health_banner(
        json.loads((run_root / "operator_summary.json").read_text(encoding="utf-8"))
    )
    assert "broker_reject=BROKER_REJECT_PDT" in banner
    assert "pattern day trading protection" in banner

    summary = build_trading_day_summary(
        run_root=run_root,
        run_id="run-1",
        trade_date="2026-03-13",
        workspace_root=tmp_path,
    )
    broker_context = summary["broker_context"]
    assert broker_context["broker_reject_status"] == BROKER_REJECT_PDT
    assert broker_context["broker_reject_message"] == "trade denied due to pattern day trading protection"
    assert broker_context["pretrade_account_status"] == "ACTIVE"
    assert broker_context["pretrade_buying_power"] == "1000.00"


def test_run_pointer_supports_broker_reject_substatus(tmp_path: Path) -> None:
    write_latest_run_pointer(
        run_id="run-1",
        trade_date="2026-03-13",
        mode="ALPACA",
        run_root="outputs/runs/run-1",
        status="failed_pre_execution",
        substatus=BROKER_REJECT_PDT,
        status_message="trade denied due to pattern day trading protection",
        workspace_root=str(tmp_path),
    )
    latest = read_latest_run_pointer(str(tmp_path))
    assert latest is not None
    assert latest["status"] == "failed_pre_execution"
    assert latest["substatus"] == BROKER_REJECT_PDT
    assert latest["status_message"] == "trade denied due to pattern day trading protection"

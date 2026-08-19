from __future__ import annotations

import json

import pytest

from brokers.alpaca_broker import AlpacaBroker
from core.generic_live_v1_broker_collector import (
    collect_and_finalize_generic_live_v1_posttrade,
)
from core.lane_reconciliation import seal_broker_order_evidence
from core.generic_live_v1_posttrade import (
    build_and_finalize_generic_live_v1_production_posttrade,
)
from Tests.test_generic_live_v1_posttrade_orchestrator import (
    _rollback, _session_fixture,
)


class ReadBroker:
    def __init__(self, arguments, *, raw_account_id="RAW_ACCOUNT_MUST_NOT_PERSIST"):
        self.arguments = arguments
        self.raw_account_id = raw_account_id
        self.plan = arguments["exact_plan"]
        self.submission = arguments["submission_result"]
        self.order = [*self.plan["sell_orders"], *self.plan["buy_orders"]][0]

    def get_order(self, order_id):
        assert order_id == self.submission["broker_order"]["broker_order_id"]
        return {
            "id": order_id, "client_order_id": self.order["client_order_id"],
            "symbol": self.order["symbol"], "side": self.order["side"],
            "status": "filled", "qty": str(self.order["quantity"]),
            "filled_qty": str(self.order["quantity"]),
        }

    def list_generic_live_v1_fill_activities(self, date_iso):
        assert date_iso == self.plan["trade_date"]
        return [{
            "id": "alpaca-fill-activity-1", "activity_type": "FILL",
            "transaction_time": "2026-08-19T13:34:30+00:00",
            "order_id": self.submission["broker_order"]["broker_order_id"],
            "symbol": self.order["symbol"], "side": self.order["side"],
            "qty": str(self.order["quantity"]),
            "price": str(self.order["enforcement_price"]), "fee_amount": "0",
        }]

    def get_account(self):
        gross = float(self.order["quantity"]) * float(self.order["enforcement_price"])
        cash = float(self.plan["starting_cash"]) - gross
        return {
            "id": self.raw_account_id,
            "id_hash": self.plan["account_id_hash"], "status": "ACTIVE",
            "trading_blocked": False, "account_blocked": False,
            "cash": str(cash), "equity": str(cash + 400.0),
        }

    def get_positions(self):
        return [{
            "symbol": self.order["symbol"], "qty": str(self.order["quantity"]),
            "current_price": "100", "market_value": "400",
        }]


def _collector_arguments(tmp_path):
    raw = _session_fixture(tmp_path)
    return raw, {
        key: value for key, value in raw.items()
        if key not in {"order_lifecycle", "broker_orders", "broker_fills", "ending_state"}
    }


def test_fresh_read_only_broker_evidence_closes_before_publishing_pointer(tmp_path) -> None:
    raw, arguments = _collector_arguments(tmp_path)
    pointer = tmp_path / "published" / "posttrade.json"
    result = collect_and_finalize_generic_live_v1_posttrade(
        broker=ReadBroker(raw), observed_at="2026-08-19T20:00:00+00:00",
        evidence_directory=tmp_path / "broker-evidence",
        published_pointer_path=pointer, **arguments,
    )

    assert result["closure"]["status"] == "GREEN_REARMED"
    assert pointer.exists()
    published = json.loads(pointer.read_text())
    assert published["closure_hash"] == result["closure"]["content_hash"]
    assert published["broker_write_performed"] is False
    persisted = "".join(
        path.read_text() for path in (tmp_path / "broker-evidence").glob("*.json")
    )
    assert "RAW_ACCOUNT_MUST_NOT_PERSIST" not in persisted


def test_pointer_is_never_published_when_broker_state_cannot_reconcile(tmp_path) -> None:
    raw, arguments = _collector_arguments(tmp_path)

    class BadStateBroker(ReadBroker):
        def get_account(self):
            account = dict(super().get_account())
            account["cash"] = "1"
            account["equity"] = "401"
            return account

    pointer = tmp_path / "published" / "posttrade.json"
    with pytest.raises(Exception):
        collect_and_finalize_generic_live_v1_posttrade(
            broker=BadStateBroker(raw), observed_at="2026-08-19T20:00:00+00:00",
            evidence_directory=tmp_path / "broker-evidence",
            published_pointer_path=pointer, **arguments,
        )
    assert not pointer.exists()


def test_lifecycle_reconciliation_mismatch_is_rejected_before_claim_persistence(
    tmp_path,
) -> None:
    arguments = _session_fixture(tmp_path)
    changed = dict(arguments["broker_orders"][0])
    changed.pop("content_hash")
    changed["source_hash"] = "e" * 64
    arguments["broker_orders"] = [seal_broker_order_evidence(changed)]
    observed = []
    arguments["rollback_handler"] = (
        lambda trigger: observed.append(trigger) or _rollback(trigger)
    )

    with pytest.raises(Exception, match="causality differs"):
        build_and_finalize_generic_live_v1_production_posttrade(**arguments)
    assert observed == ["RECONCILIATION_BREAK"]
    assert not arguments["reporting_artifact_directory"].exists()


def test_alpaca_fill_activity_reader_preserves_order_and_explicit_fee_lineage() -> None:
    class Client:
        def get(self, path, data):
            assert path == "/account/activities/FILL"
            assert data["date"] == "2026-08-19"
            return [{
                "id": "fill-1", "activity_type": "FILL",
                "transaction_time": "2026-08-19T13:34:30Z",
                "order_id": "broker-order-1", "symbol": "AAPL",
                "side": "buy", "qty": "4", "price": "100.25",
                "fee_amount": "0",
            }]

    rows = AlpacaBroker(Client(), paper=False).list_generic_live_v1_fill_activities(
        "2026-08-19"
    )
    assert rows == [{
        "id": "fill-1", "activity_type": "FILL",
        "transaction_time": "2026-08-19T13:34:30Z",
        "order_id": "broker-order-1", "symbol": "AAPL", "side": "buy",
        "qty": "4", "price": "100.25", "fee_amount": "0",
    }]


def test_alpaca_sell_fill_without_explicit_fee_fails_closed() -> None:
    class Client:
        def get(self, path, data):
            return [{
                "id": "fill-1", "activity_type": "FILL",
                "transaction_time": "2026-08-19T13:34:30Z",
                "order_id": "broker-order-1", "symbol": "AAPL",
                "side": "sell", "qty": "1", "price": "100.25",
            }]

    with pytest.raises(RuntimeError, match="SELL fill lacks explicit fee"):
        AlpacaBroker(Client(), paper=False).list_generic_live_v1_fill_activities(
            "2026-08-19"
        )

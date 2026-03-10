import json

from scripts.export_alpaca_broker_snapshot import (
    build_snapshot_payload,
    write_snapshot_json,
)


def test_build_snapshot_payload_shape_and_redaction():
    account = {
        "id": "acct-1",
        "status": "ACTIVE",
        "cash": "1000.00",
        "equity": "1200.00",
        "raw": {"buying_power": "5000.00"},
    }
    positions = [
        {"symbol": "ZZZ", "qty": "1", "side": "long", "raw": {"market_value": "10.00"}},
        {"symbol": "AAA", "qty": "2", "side": "long", "raw": {"market_value": "20.00"}},
    ]
    orders_all = [
        {
            "id": "ord-old",
            "client_order_id": "cid-old",
            "symbol": "OLD",
            "side": "sell",
            "status": "filled",
            "submitted_at": "2026-03-09T14:00:00Z",
            "raw": {"filled_at": "2026-03-09T14:00:10Z", "qty": "1"},
        },
        {
            "id": "ord-today",
            "client_order_id": "cid-today",
            "symbol": "MPC",
            "side": "buy",
            "status": "filled",
            "submitted_at": "2026-03-10T14:00:00Z",
            "raw": {"filled_at": "2026-03-10T14:00:10Z", "qty": "2", "filled_qty": "2"},
        },
    ]
    orders_closed = list(orders_all)
    fills = [
        {
            "id": "fill-1",
            "activity_type": "FILL",
            "symbol": "MPC",
            "qty": "2",
            "price": "101.50",
            "transaction_time": "2026-03-10T14:00:11Z",
            "raw": {"order_id": "ord-today", "side": "buy"},
        }
    ]

    payload = build_snapshot_payload(
        report_date="2026-03-10",
        workflow_run_id="123456",
        git_sha="deadbeef",
        account=account,
        positions=positions,
        orders_all=orders_all,
        orders_closed=orders_closed,
        fills=fills,
    )

    assert set(payload.keys()) == {
        "meta",
        "account",
        "positions_current",
        "orders_report_date",
        "orders_closed_recent",
        "fills_report_date",
        "counts",
    }
    assert payload["meta"]["report_date"] == "2026-03-10"
    assert payload["meta"]["workflow_run_id"] == "123456"
    assert payload["account"]["id"] == "acct-1"
    assert [p["symbol"] for p in payload["positions_current"]] == ["AAA", "ZZZ"]
    assert len(payload["orders_report_date"]) == 1
    assert payload["orders_report_date"][0]["id"] == "ord-today"
    assert payload["counts"]["orders_report_date"] == 1

    text = json.dumps(payload)
    assert '"raw"' not in text
    assert "ALPACA_API_SECRET_KEY" not in text


def test_write_snapshot_json_uses_deterministic_filename(tmp_path):
    payload = {"meta": {"report_date": "2026-03-10"}}
    out_path = write_snapshot_json(payload, tmp_path, "2026-03-10")
    assert out_path == tmp_path / "broker_snapshot_2026-03-10.json"
    assert out_path.exists()

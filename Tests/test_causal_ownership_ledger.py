from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from core.causal_ownership_ledger import CausalOwnershipError, build_causal_ownership


def _hash(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _fixture(tmp_path: Path, *, unmatched_after_epoch: bool = False) -> tuple[Path, Path]:
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    fills = [
        {
            "activity_id": "legacy-buy",
            "transaction_time_utc": "2026-08-13T14:00:00Z",
            "trade_date_et": "2026-08-13",
            "symbol": "AAPL",
            "side": "buy",
            "qty": 10,
            "price": 100,
            "multiplier": 1,
            "notional": 1000,
            "order_id": "broker-legacy",
            "fill_type": "fill",
            "cum_qty": 10,
            "leaves_qty": 0,
        },
        {
            "activity_id": "causal-sell",
            "transaction_time_utc": "2026-08-14T13:36:00Z",
            "trade_date_et": "2026-08-14",
            "symbol": "AAPL",
            "side": "sell",
            "qty": 2,
            "price": 101,
            "multiplier": 1,
            "notional": 202,
            "order_id": "broker-sell",
            "fill_type": "fill",
            "cum_qty": 2,
            "leaves_qty": 0,
        },
        {
            "activity_id": "causal-buy",
            "transaction_time_utc": "2026-08-14T13:37:00Z",
            "trade_date_et": "2026-08-14",
            "symbol": "AAPL",
            "side": "buy",
            "qty": 4,
            "price": 102,
            "multiplier": 1,
            "notional": 408,
            "order_id": "broker-unmatched" if unmatched_after_epoch else "broker-buy",
            "fill_type": "fill",
            "cum_qty": 4,
            "leaves_qty": 0,
        },
    ]
    with (ledger / "fills.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fills[0]))
        writer.writeheader()
        writer.writerows(fills)
    _write_jsonl(
        ledger / "orders.jsonl",
        [
            {"id": "broker-sell", "client_order_id": "cx-sell", "updated_at": "2026-08-14T13:36:00Z"},
            {"id": "broker-buy", "client_order_id": "cx-buy", "updated_at": "2026-08-14T13:37:00Z"},
        ],
    )
    _write_jsonl(
        ledger / "account_snapshots.jsonl",
        [
            {
                "pulled_at_utc": "2026-08-14T23:15:00Z",
                "equity": "1424",
                "cash": "200",
            }
        ],
    )
    (ledger / "positions_latest.json").write_text(
        json.dumps(
            {
                "pulled_at_utc": "2026-08-14T23:15:00Z",
                "positions": [
                    {
                        "symbol": "AAPL",
                        "qty": "12",
                        "market_value": "1224",
                        "current_price": "102",
                        "cost_basis": "1200",
                        "unrealized_pl": "24",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    plan = {
        "schema_version": "caerus.execution_plan.v3",
        "plan_id": "plan:2026-08-14:test",
        "created_at": "2026-08-14T13:35:00Z",
        "sell_orders": [
            {
                "symbol": "AAPL",
                "side": "SELL",
                "client_order_id": "cx-sell",
                "allocation_id": "allocation:test",
                "session_id": "session:test",
                "sleeve_contributions": [
                    {"sleeve_id": "caerus_alpha", "allocation_fraction": 0.75},
                    {"sleeve_id": "caerus_beta", "allocation_fraction": 0.25},
                ],
            }
        ],
        "buy_orders": [
            {
                "symbol": "AAPL",
                "side": "BUY",
                "client_order_id": "cx-buy",
                "allocation_id": "allocation:test",
                "session_id": "session:test",
                "sleeve_contributions": [
                    {"sleeve_id": "caerus_alpha", "allocation_fraction": 0.75},
                    {"sleeve_id": "caerus_beta", "allocation_fraction": 0.25},
                ],
            }
        ],
    }
    plan["content_hash"] = _hash(plan)
    plan_path = tmp_path / "plans" / "exact_execution_plan_2026-08-14.json"
    plan_path.parent.mkdir()
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    return ledger, plan_path


def test_causal_ownership_preserves_legacy_and_reconciles(tmp_path: Path) -> None:
    ledger, plan = _fixture(tmp_path)
    result = build_causal_ownership(ledger_dir=ledger, exact_plan_paths=[plan])

    assert result["status"] == "PASS"
    ownership = json.loads((ledger / "ownership_latest.json").read_text())
    by_owner = {
        row["sleeve_id"]: row["quantity"] for row in ownership["positions"]
    }
    assert by_owner == {
        "caerus_alpha": pytest.approx(3.0),
        "caerus_beta": pytest.approx(1.0),
        "legacy_unattributed": pytest.approx(8.0),
    }
    valuation = json.loads((ledger / "valuation_latest.json").read_text())
    assert valuation["as_of"] == "2026-08-14T23:15:00Z"
    assert sum(
        row["market_value"] for row in valuation["positions"][0]["ownership"]
    ) == pytest.approx(1224.0)
    records = [json.loads(line) for line in (ledger / "causal_fills.jsonl").read_text().splitlines()]
    assert records[0]["attribution_status"] == "LEGACY_UNATTRIBUTED"
    assert records[-1]["allocation_id"] == "allocation:test"


def test_post_cutover_unmatched_fill_fails_closed(tmp_path: Path) -> None:
    ledger, plan = _fixture(tmp_path, unmatched_after_epoch=True)
    with pytest.raises(CausalOwnershipError, match="lack exact-plan lineage"):
        build_causal_ownership(ledger_dir=ledger, exact_plan_paths=[plan])


def test_causal_fill_history_is_immutable(tmp_path: Path) -> None:
    ledger, plan = _fixture(tmp_path)
    build_causal_ownership(ledger_dir=ledger, exact_plan_paths=[plan])
    rows = [json.loads(line) for line in (ledger / "causal_fills.jsonl").read_text().splitlines()]
    rows[-1]["quantity"] = 99
    _write_jsonl(ledger / "causal_fills.jsonl", rows)
    with pytest.raises(CausalOwnershipError, match="append-only causal fill changed"):
        build_causal_ownership(ledger_dir=ledger, exact_plan_paths=[plan])

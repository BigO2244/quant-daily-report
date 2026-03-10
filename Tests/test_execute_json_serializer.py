"""
Tests for _json_default serializer in scripts/execute_alpaca_orders.py.

Covers: UUID, datetime, date, Enum, Decimal, Path, to_dict(), nested structures.
Reproduces the TypeError: Object of type UUID is not JSON serializable failure
from GitHub Actions run 22921950542.
"""
from __future__ import annotations

import json
import tempfile
import uuid
import decimal
from datetime import date, datetime
from enum import Enum
from pathlib import Path

import pytest
import sys

# Make sure repo root is importable
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.execute_alpaca_orders import _json_default, _write_json


# ---------------------------------------------------------------------------
# Unit tests for _json_default
# ---------------------------------------------------------------------------

class TestJsonDefault:
    def test_uuid_serializes_to_string(self):
        val = uuid.uuid4()
        result = _json_default(val)
        assert result == str(val)
        assert isinstance(result, str)

    def test_datetime_serializes_to_isoformat(self):
        val = datetime(2026, 3, 10, 14, 35, 0)
        result = _json_default(val)
        assert result == "2026-03-10T14:35:00"

    def test_date_serializes_to_isoformat(self):
        val = date(2026, 3, 10)
        result = _json_default(val)
        assert result == "2026-03-10"

    def test_enum_serializes_to_value(self):
        class Side(Enum):
            BUY = "buy"
            SELL = "sell"

        result = _json_default(Side.BUY)
        assert result == "buy"

    def test_enum_without_value_falls_back_to_str(self):
        # Enum.value is always present in Python, but test str() path via
        # a fake object that has no .value attribute
        class FakeEnum:
            pass

        obj = FakeEnum()
        result = _json_default(obj)
        assert isinstance(result, str)

    def test_decimal_serializes_to_string(self):
        val = decimal.Decimal("123.456789")
        result = _json_default(val)
        assert result == "123.456789"

    def test_path_serializes_to_string(self):
        val = Path("/outputs/runs/test/execution_results.json")
        result = _json_default(val)
        assert result == "/outputs/runs/test/execution_results.json"

    def test_object_with_to_dict_uses_to_dict(self):
        class FakeOrder:
            def to_dict(self):
                return {"symbol": "SPY", "qty": 10, "side": "buy"}

        result = _json_default(FakeOrder())
        assert result == {"symbol": "SPY", "qty": 10, "side": "buy"}

    def test_unknown_object_falls_back_to_str(self):
        class Blob:
            def __repr__(self):
                return "Blob()"

        result = _json_default(Blob())
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Integration tests: _write_json handles Alpaca-style payloads
# ---------------------------------------------------------------------------

class TestWriteJsonRobustness:
    def _roundtrip(self, payload):
        """Write payload to a temp file and reload as JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "execution_results.json"
            _write_json(path, payload)
            assert path.exists()
            text = path.read_text(encoding="utf-8")
            return json.loads(text)

    def test_uuid_in_payload_does_not_crash(self):
        payload = {"order_id": uuid.uuid4(), "symbol": "SPY"}
        result = self._roundtrip(payload)
        assert isinstance(result["order_id"], str)

    def test_datetime_in_payload_does_not_crash(self):
        payload = {"submitted_at": datetime(2026, 3, 10, 14, 35, 0), "symbol": "QQQ"}
        result = self._roundtrip(payload)
        assert result["submitted_at"] == "2026-03-10T14:35:00"

    def test_nested_uuid_in_list_does_not_crash(self):
        payload = {
            "accepted_orders": [
                {"order_id": uuid.uuid4(), "symbol": "SPY", "qty": 5},
                {"order_id": uuid.uuid4(), "symbol": "AGG", "qty": 3},
            ]
        }
        result = self._roundtrip(payload)
        assert len(result["accepted_orders"]) == 2
        for order in result["accepted_orders"]:
            assert isinstance(order["order_id"], str)

    def test_enum_in_nested_dict(self):
        class OrderType(Enum):
            MARKET = "market"
            LIMIT = "limit"

        payload = {
            "trades": [
                {"symbol": "SPY", "order_type": OrderType.MARKET, "qty": 10}
            ]
        }
        result = self._roundtrip(payload)
        assert result["trades"][0]["order_type"] == "market"

    def test_output_is_valid_json(self):
        payload = {
            "run_id": "20260310T093500Z_paper_1",
            "trade_date": "2026-03-10",
            "order_id": uuid.uuid4(),
            "submitted_at": datetime(2026, 3, 10, 9, 35, 0),
            "status": "accepted",
            "qty": decimal.Decimal("10.0"),
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "execution_results.json"
            _write_json(path, payload)
            # Must parse without raising
            parsed = json.loads(path.read_text(encoding="utf-8"))
            assert parsed["run_id"] == "20260310T093500Z_paper_1"

    def test_execution_results_structure_preserved(self):
        """Accepted orders payload structure survives serialization intact."""
        order_id = uuid.uuid4()
        payload = {
            "status": "executed",
            "run_id": "20260310T093500Z_12345678_1",
            "trade_date": "2026-03-10",
            "accepted_orders": [
                {
                    "symbol": "SPY",
                    "qty": 5,
                    "side": "buy",
                    "order_id": order_id,
                    "submitted_at": datetime(2026, 3, 10, 14, 35, 0),
                }
            ],
            "rejected_orders": [],
            "submitted_count": 1,
            "accepted_count": 1,
            "rejected_count": 0,
        }
        result = self._roundtrip(payload)
        assert result["status"] == "executed"
        assert result["accepted_count"] == 1
        assert result["accepted_orders"][0]["symbol"] == "SPY"
        assert result["accepted_orders"][0]["order_id"] == str(order_id)

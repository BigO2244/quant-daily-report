"""
Regression tests for idempotent replay semantics in execute_alpaca_orders.

Reproduces the semantic problem from GitHub Actions run 22921950542:
- All orders returned DUPLICATE_CLIENT_ORDER_ID after a post-submit serializer crash.
- The run was (incorrectly) reported as NO_ACTION with rejected_count=10.
- Correct behavior: status=IDEMPOTENT_REPLAY, rejected_count=0, duplicate_count=N.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest
import sys

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.execution_payload import (
    STATUS_EXECUTED,
    STATUS_HALTED,
    STATUS_IDEMPOTENT_REPLAY,
    STATUS_NO_ACTION,
)
from scripts.execute_alpaca_orders import (
    _deterministic_internal_order_id,
    _submit_orders,
    _write_json,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_order(symbol: str, side: str, qty: float, order_id: str = "abc-123") -> Dict[str, Any]:
    """Simulate the dict returned by broker.find_order_by_client_id."""
    return {
        "id": order_id,
        "symbol": symbol,
        "side": side,
        "qty": str(qty),
        "status": "accepted",
        "submitted_at": "2026-03-10T09:35:00Z",
        "client_order_id": f"caerus_{symbol}_{side}_{qty}",
    }


def _make_broker(existing_orders: dict[str, Any] | None = None, submit_raises=None):
    """
    Return a mock AlpacaBroker.
    existing_orders: {client_order_id: order_dict} for find_order_by_client_id to return.
    """
    broker = MagicMock()
    existing_orders = existing_orders or {}

    def find_order(client_order_id):
        return existing_orders.get(client_order_id)

    broker.find_order_by_client_id.side_effect = find_order

    if submit_raises:
        broker.submit_market_order.side_effect = submit_raises
    else:
        broker.submit_market_order.return_value = {"id": "new-order-id", "status": "accepted"}

    return broker


def _make_payload(trades: list[Dict[str, Any]], run_id: str = "test-run-1") -> Dict[str, Any]:
    return {
        "run_id": run_id,
        "trade_date": "2026-03-10",
        "mode": "ALPACA",
        "status": "READY",
        "trades": trades,
    }


# ---------------------------------------------------------------------------
# Case 1: Duplicate-only replay
# ---------------------------------------------------------------------------

class TestDuplicateOnlyReplay:
    def _run_all_duplicate(self, n=3):
        trades = [
            {"ticker": f"SYM{i}", "side": "BUY", "shares": 10.0}
            for i in range(n)
        ]
        payload = _make_payload(trades)

        # Populate existing orders for every client_order_id that will be generated
        from brokers.alpaca_broker import alpaca_client_order_id
        existing = {}
        for i, t in enumerate(trades):
            symbol = t["ticker"].upper()
            side = "BUY"
            qty = 10.0
            internal = _deterministic_internal_order_id(payload, t)
            cid = alpaca_client_order_id(internal)
            existing[cid] = _fake_order(symbol, side, qty)

        broker = _make_broker(existing_orders=existing)
        with tempfile.TemporaryDirectory() as tmpdir:
            summary = _submit_orders(payload, broker, Path(tmpdir))

        return summary

    def test_duplicate_count_equals_n(self):
        summary = self._run_all_duplicate(3)
        assert summary["duplicate_count"] == 3

    def test_rejected_count_is_zero(self):
        summary = self._run_all_duplicate(3)
        assert summary["rejected_count"] == 0

    def test_submitted_count_is_zero(self):
        summary = self._run_all_duplicate(3)
        assert summary["submitted_count"] == 0

    def test_accepted_count_is_zero(self):
        summary = self._run_all_duplicate(3)
        assert summary["accepted_count"] == 0

    def test_responses_marked_idempotent_replay(self):
        summary = self._run_all_duplicate(2)
        for resp in summary["broker_responses"]:
            assert resp["status"] == "IDEMPOTENT_REPLAY"

    def test_rejected_reasons_empty(self):
        summary = self._run_all_duplicate(2)
        assert summary["rejected_reasons"] == []


# ---------------------------------------------------------------------------
# Case 2: Mixed outcome (fresh + duplicate + true reject)
# ---------------------------------------------------------------------------

class TestMixedOutcome:
    def test_counters_separated_correctly(self):
        trades = [
            {"ticker": "SPY", "side": "BUY", "shares": 5.0},   # duplicate
            {"ticker": "QQQ", "side": "BUY", "shares": 3.0},   # fresh accept
            {"ticker": "GLD", "side": "BUY", "shares": 1.0},   # broker error
        ]
        payload = _make_payload(trades)

        from brokers.alpaca_broker import alpaca_client_order_id

        spy_internal = _deterministic_internal_order_id(payload, trades[0])
        spy_cid = alpaca_client_order_id(spy_internal)
        existing = {spy_cid: _fake_order("SPY", "BUY", 5.0)}

        def _submit(symbol, qty, side, client_order_id, tif):
            if symbol == "GLD":
                raise RuntimeError("broker error")
            return {"id": "new-qqq-id", "status": "accepted"}

        broker = _make_broker(existing_orders=existing)
        broker.submit_market_order.side_effect = _submit

        with tempfile.TemporaryDirectory() as tmpdir:
            summary = _submit_orders(payload, broker, Path(tmpdir))

        assert summary["duplicate_count"] == 1, summary
        assert summary["accepted_count"] == 1, summary
        assert summary["rejected_count"] == 1, summary
        assert summary["submitted_count"] == 2  # QQQ + GLD both attempted

    def test_fresh_accept_still_in_order_ids(self):
        trades = [
            {"ticker": "SPY", "side": "BUY", "shares": 5.0},
            {"ticker": "QQQ", "side": "BUY", "shares": 3.0},
        ]
        payload = _make_payload(trades)

        from brokers.alpaca_broker import alpaca_client_order_id
        spy_internal = _deterministic_internal_order_id(payload, trades[0])
        spy_cid = alpaca_client_order_id(spy_internal)
        existing = {spy_cid: _fake_order("SPY", "BUY", 5.0)}

        broker = _make_broker(existing_orders=existing)
        broker.submit_market_order.return_value = {"id": "fresh-id-qqq", "status": "accepted"}

        with tempfile.TemporaryDirectory() as tmpdir:
            summary = _submit_orders(payload, broker, Path(tmpdir))

        assert "fresh-id-qqq" in summary["order_ids"]
        assert summary["duplicate_count"] == 1
        assert summary["accepted_count"] == 1


# ---------------------------------------------------------------------------
# Case 3: True NO_ACTION (no executable trades)
# ---------------------------------------------------------------------------

class TestNoAction:
    def test_empty_trades_all_zero(self):
        payload = _make_payload([])
        broker = _make_broker()
        with tempfile.TemporaryDirectory() as tmpdir:
            summary = _submit_orders(payload, broker, Path(tmpdir))

        assert summary["submitted_count"] == 0
        assert summary["accepted_count"] == 0
        assert summary["rejected_count"] == 0
        assert summary["duplicate_count"] == 0
        assert summary["broker_responses"] == []


class TestDeterministicFallbackIdempotency:
    def test_same_trade_same_day_different_run_ids_share_fallback_order_id(self):
        trade = {"ticker": "SPY", "side": "BUY", "shares": 5.0}
        payload_a = _make_payload([trade], run_id="run-a")
        payload_b = _make_payload([trade], run_id="run-b")

        internal_a = _deterministic_internal_order_id(payload_a, trade)
        internal_b = _deterministic_internal_order_id(payload_b, trade)

        assert internal_a == "2026-03-10:ALPACA:SPY:BUY:5"
        assert internal_b == internal_a

    def test_fresh_runner_duplicate_replay_resolves_via_remote_existing(self):
        trade = {"ticker": "SPY", "side": "BUY", "shares": 5.0}
        original_payload = _make_payload([trade], run_id="original-run")
        replay_payload = _make_payload([trade], run_id="fresh-runner-replay")

        from brokers.alpaca_broker import alpaca_client_order_id

        stable_internal = _deterministic_internal_order_id(original_payload, trade)
        stable_cid = alpaca_client_order_id(stable_internal)
        existing = {stable_cid: _fake_order("SPY", "BUY", 5.0)}

        broker = _make_broker(existing_orders=existing)
        with tempfile.TemporaryDirectory() as tmpdir:
            summary = _submit_orders(replay_payload, broker, Path(tmpdir))

        assert summary["submitted_count"] == 0
        assert summary["duplicate_count"] == 1
        assert summary["accepted_count"] == 0
        assert summary["broker_responses"][0]["status"] == "IDEMPOTENT_REPLAY"


# ---------------------------------------------------------------------------
# Case 4: Status resolution logic
# ---------------------------------------------------------------------------

class TestStatusResolution:
    """
    Test the status = IDEMPOTENT_REPLAY / EXECUTED / NO_ACTION decision
    by simulating what run_execution does after _submit_orders returns.
    """

    def _resolve_status(self, submitted, accepted, rejected, duplicates):
        from core.execution_payload import (
            STATUS_EXECUTED,
            STATUS_IDEMPOTENT_REPLAY,
            STATUS_NO_ACTION,
        )
        if submitted > 0:
            return STATUS_EXECUTED
        elif duplicates > 0:
            return STATUS_IDEMPOTENT_REPLAY
        else:
            return STATUS_NO_ACTION

    def test_all_duplicates_gives_idempotent_replay(self):
        assert self._resolve_status(0, 0, 0, 10) == STATUS_IDEMPOTENT_REPLAY

    def test_fresh_submit_gives_executed(self):
        assert self._resolve_status(5, 4, 1, 0) == STATUS_EXECUTED

    def test_mix_with_fresh_submit_gives_executed(self):
        # Fresh submission + duplicates → still EXECUTED (new work was done)
        assert self._resolve_status(1, 1, 0, 5) == STATUS_EXECUTED

    def test_zero_everything_gives_no_action(self):
        assert self._resolve_status(0, 0, 0, 0) == STATUS_NO_ACTION

    def test_duplicates_plus_real_rejects_gives_replay(self):
        # No new submissions; duplicates found + some truly failed validation
        assert self._resolve_status(0, 0, 2, 3) == STATUS_IDEMPOTENT_REPLAY


# ---------------------------------------------------------------------------
# Case 5: Audit summary verdict
# ---------------------------------------------------------------------------

class TestAuditVerdict:
    """Verdict tests with a full planner block present (normal two-job run)."""

    def _make_audit(self, executor_status, duplicate_count=0, submitted=0, accepted=0):
        from core.execution_audit import _compute_verdict
        audit = {
            "planner": {
                "executable_trades_count": 5,
                "execution_status": "READY",
                "gates": {"should_execute": True},
                "buy_orders_count": 5,
                "sell_orders_count": 0,
                "blocked_reasons": [],
            },
            "executor": {
                "execution_status": executor_status,
                "submitted_count": submitted,
                "accepted_count": accepted,
                "rejected_count": 0,
                "duplicate_count": duplicate_count,
                "halt_reason": None,
            },
        }
        return _compute_verdict(audit)

    def test_idempotent_replay_execution_attempted_true(self):
        verdict = self._make_audit("IDEMPOTENT_REPLAY", duplicate_count=5)
        assert verdict["execution_attempted"] is True

    def test_idempotent_replay_is_flagged(self):
        verdict = self._make_audit("IDEMPOTENT_REPLAY", duplicate_count=5)
        assert verdict["is_idempotent_replay"] is True

    def test_idempotent_replay_cash_reason_is_canonical(self):
        """Exact canonical string — not the generic no-action fallback."""
        verdict = self._make_audit("IDEMPOTENT_REPLAY", duplicate_count=5)
        assert verdict["cash_not_deployed_reason"] == "orders_already_submitted_prior_run"

    def test_idempotent_replay_does_not_inherit_no_action_reason(self):
        verdict = self._make_audit("IDEMPOTENT_REPLAY", duplicate_count=5)
        assert verdict["cash_not_deployed_reason"] != "no_executable_trades_after_constraints"

    def test_no_action_execution_attempted_false(self):
        verdict = self._make_audit("NO_ACTION", duplicate_count=0)
        assert verdict["execution_attempted"] is False

    def test_no_action_cash_reason_is_no_trades(self):
        """NO_ACTION without trades must still get the generic no-trades reason."""
        from core.execution_audit import _compute_verdict
        audit = {
            "planner": {
                "executable_trades_count": 0,
                "execution_status": "NO_ACTION",
                "gates": {"should_execute": True},
                "buy_orders_count": 0,
                "sell_orders_count": 0,
                "blocked_reasons": [],
            },
            "executor": {
                "execution_status": "NO_ACTION",
                "submitted_count": 0,
                "accepted_count": 0,
                "rejected_count": 0,
                "duplicate_count": 0,
                "halt_reason": None,
            },
        }
        verdict = _compute_verdict(audit)
        assert verdict["cash_not_deployed_reason"] == "no_executable_trades_after_constraints"

    def test_executed_is_not_idempotent_replay(self):
        verdict = self._make_audit("EXECUTED", submitted=3, accepted=3)
        assert verdict["is_idempotent_replay"] is False

    def test_halted_execution_attempted_false(self):
        verdict = self._make_audit("HALTED", duplicate_count=0)
        assert verdict["execution_attempted"] is False


class TestAuditVerdictReplayNoPlannerBlock:
    """
    Covers the production failure case: executor-only retry job where the
    planner block is absent (different run_id, or audit file not carried over).
    trades_proposed and proposed_count were False/0 from empty planner data,
    causing the verdict to fall through to "no_executable_trades_after_constraints".
    """

    def _replay_audit_no_planner(self, duplicate_count=10):
        from core.execution_audit import _compute_verdict
        return _compute_verdict({
            "planner": None,   # absent — matches production artifact structure
            "executor": {
                "execution_status": "IDEMPOTENT_REPLAY",
                "submitted_count": 0,
                "accepted_count": 0,
                "rejected_count": 0,
                "duplicate_count": duplicate_count,
                "halt_reason": None,
            },
            "trade_date": "2026-03-10",
        })

    def test_cash_reason_is_replay_not_no_trades(self):
        """Reproduces the exact artifact bug: cash_not_deployed_reason was wrong."""
        verdict = self._replay_audit_no_planner(10)
        assert verdict["cash_not_deployed_reason"] == "orders_already_submitted_prior_run"
        assert verdict["cash_not_deployed_reason"] != "no_executable_trades_after_constraints"

    def test_execution_attempted_true(self):
        verdict = self._replay_audit_no_planner(10)
        assert verdict["execution_attempted"] is True

    def test_is_idempotent_replay_true(self):
        verdict = self._replay_audit_no_planner(10)
        assert verdict["is_idempotent_replay"] is True

    def test_trades_proposed_inferred_from_duplicate_count(self):
        """With no planner data but duplicate_count > 0, trades_proposed becomes True."""
        verdict = self._replay_audit_no_planner(5)
        assert verdict["trades_proposed"] is True

    def test_proposed_count_falls_back_to_duplicate_count(self):
        """proposed_count uses duplicate_count as proxy when planner is absent."""
        verdict = self._replay_audit_no_planner(7)
        assert verdict["proposed_count"] == 7

    def test_zero_duplicates_does_not_infer_trades_proposed(self):
        """Safety: duplicate_count=0 with no planner block should not set trades_proposed=True."""
        from core.execution_audit import _compute_verdict
        verdict = _compute_verdict({
            "planner": None,
            "executor": {
                "execution_status": "NO_ACTION",
                "submitted_count": 0,
                "accepted_count": 0,
                "rejected_count": 0,
                "duplicate_count": 0,
                "halt_reason": None,
            },
        })
        assert verdict["trades_proposed"] is False
        assert verdict["proposed_count"] == 0


# ---------------------------------------------------------------------------
# Case 6: JSON serialization of idempotent replay result
# ---------------------------------------------------------------------------

class TestIdempotentReplaySerializable:
    def test_result_with_replay_orders_is_valid_json(self, tmp_path):
        import uuid
        from datetime import datetime

        payload = {
            "status": STATUS_IDEMPOTENT_REPLAY,
            "run_id": "20260310T093500Z_paper_1",
            "trade_date": "2026-03-10",
            "submitted_count": 0,
            "accepted_count": 0,
            "rejected_count": 0,
            "duplicate_count": 3,
            "broker_responses": [
                {
                    "ticker": "SPY",
                    "side": "BUY",
                    "shares": 5.0,
                    "status": "IDEMPOTENT_REPLAY",
                    "client_order_id": "caerus_SPY_BUY_5.0",
                    "order": {
                        "id": uuid.uuid4(),         # UUID from Alpaca SDK
                        "submitted_at": datetime(2026, 3, 10, 9, 35, 0),
                        "status": "accepted",
                    },
                }
            ],
        }

        path = tmp_path / "execution_results.json"
        _write_json(path, payload)

        parsed = json.loads(path.read_text(encoding="utf-8"))
        assert parsed["status"] == STATUS_IDEMPOTENT_REPLAY
        assert parsed["duplicate_count"] == 3
        assert parsed["rejected_count"] == 0
        assert isinstance(parsed["broker_responses"][0]["order"]["id"], str)

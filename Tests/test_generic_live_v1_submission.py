from __future__ import annotations

import json
import hashlib

import pytest

from core.generic_live_v1_activation import build_generic_live_v1_activation_preflight
from core.generic_live_v1_submission import (
    GenericLiveV1SubmissionError,
    execute_generic_live_v1_session as _execute_generic_live_v1_session,
    finalize_generic_live_v1_posttrade,
    seal_generic_live_v1_order_lifecycle,
)
from authority.lane_exact_plan import canonical_json
from brokers.alpaca_broker import _GENERIC_LIVE_V4_CAPABILITY
from Tests.test_generic_live_v1_activation import (
    EXPECTED,
    OBSERVATION,
    OWNER,
    _decision,
    _plan,
    _proofs,
)


def execute_generic_live_v1_session(**kwargs):
    if kwargs.get("submit_enabled") is True:
        kwargs.setdefault("lyra_decision", _decision())
    return _execute_generic_live_v1_session(**kwargs)


class Broker:
    def __init__(
        self, *, fail: bool = False, status: str = "accepted",
        terminal_status: str = "filled", equity: str = "460.9",
        cash: str = "460.9", buying_power: str = "460.9",
    ):
        self.orders = {}
        self.submit_calls = 0
        self.fail = fail
        self.status = status
        self.terminal_status = terminal_status
        self.by_id = {}
        self.cancel_contexts = []
        self.equity = equity
        self.cash = cash
        self.buying_power = buying_power

    def get_account(self):
        return {
            "id_hash": OBSERVATION["account_id_hash"], "status": "ACTIVE",
            "trading_blocked": False, "account_blocked": False,
            "equity": self.equity, "cash": self.cash,
            "buying_power": self.buying_power,
        }

    def get_positions(self):
        return []

    def list_orders(self, status="open", limit=100):
        return []

    def get_asset(self, symbol):
        return {"symbol": symbol, "status": "active", "tradable": True}

    def get_market_session_calendar(self, trade_date):
        return {
            "trade_date": trade_date,
            "session_open_et": f"{trade_date}T09:30:00-04:00",
            "session_close_et": f"{trade_date}T16:00:00-04:00",
        }

    def find_order_by_client_id(self, client_id):
        return self.orders.get(client_id)

    def submit_generic_live_v4_limit_order(self, **kwargs):
        self.submit_calls += 1
        if self.fail:
            raise RuntimeError("broker unavailable")
        assert kwargs["_generic_live_v4_capability"] is not None
        order = {
            "id": "broker-order-1", "client_order_id": kwargs["client_order_id"],
            "status": self.status, "symbol": kwargs["symbol"], "side": kwargs["side"],
            "qty": kwargs["qty"], "filled_qty": "0",
        }
        self.orders[kwargs["client_order_id"]] = order
        self.by_id[order["id"]] = order
        return order

    def get_order(self, order_id):
        order = self.by_id.get(order_id)
        if order is not None and order["status"] in {"accepted", "new", "pending_new"}:
            order["status"] = self.terminal_status
            if self.terminal_status == "filled":
                order["filled_qty"] = order["qty"]
            elif self.terminal_status == "partially_filled":
                order["filled_qty"] = str(float(order["qty"]) / 2.0)
        return order

    def cancel_generic_live_v4_order(self, **kwargs):
        self.cancel_contexts.append(kwargs["mutation_context"])
        order = self.by_id[kwargs["broker_order_id"]]
        order["status"] = "canceled"


def _ready():
    decision = _decision()
    plan = _plan(decision)
    preflight = build_generic_live_v1_activation_preflight(
        owner_decision=OWNER, live_account_observation=OBSERVATION,
        operational_proofs=_proofs(
            deployed_sha=EXPECTED, generic_schedule_installed=True,
            generic_submission_adapter_deployed=True, rollback_rearm_proven=True,
            order_lifecycle_pipeline_green=True, reconciliation_pipeline_green=True,
            accounting_pipeline_green=True, reporting_pipeline_green=True,
        ),
        evaluated_at="2026-08-19T13:30:00+00:00",
        lyra_decision=decision, exact_plan=plan,
    )
    return preflight, plan


def _disarm(path, preflight, plan):
    body = {
        "schema_version": "caerus.generic_live_v1_session_gate.v1",
        "status": "DISARMED_FOR_EXACT_SESSION",
        "effective_session": preflight["effective_session"],
        "preflight_hash": preflight["content_hash"],
        "plan_hash": plan["content_hash"],
        "legacy_executor_enabled": False,
        "paper_cutover_enabled": False,
    }
    body["content_hash"] = hashlib.sha256(canonical_json(body).encode()).hexdigest()
    path.write_text(json.dumps(body))
    path.chmod(0o600)


def test_default_is_deterministic_no_write_and_no_broker_call(tmp_path) -> None:
    preflight, plan = _ready()
    broker = Broker()
    first = execute_generic_live_v1_session(
        activation_preflight=preflight, exact_plan=plan,
        executed_at="2026-08-19T13:31:00+00:00", broker=broker,
        wal_directory=tmp_path / "wal", rearm_state_path=tmp_path / "rearm.json",
    )
    second = execute_generic_live_v1_session(
        activation_preflight=preflight, exact_plan=plan,
        executed_at="2026-08-19T13:31:00+00:00", broker=broker,
        wal_directory=tmp_path / "wal", rearm_state_path=tmp_path / "rearm.json",
    )
    assert first == second
    assert first["status"] == "VALIDATED_NO_WRITE"
    assert broker.submit_calls == 0
    assert not (tmp_path / "wal").exists()
    assert not (tmp_path / "rearm.json").exists()


def test_explicit_submission_writes_wal_first_and_automatically_rearms(tmp_path) -> None:
    preflight, plan = _ready()
    broker = Broker()
    _disarm(tmp_path / "rearm.json", preflight, plan)
    result = execute_generic_live_v1_session(
        activation_preflight=preflight, exact_plan=plan,
        executed_at="2026-08-19T13:31:00+00:00", submit_enabled=True,
        broker=broker, wal_directory=tmp_path / "wal",
        rearm_state_path=tmp_path / "rearm.json",
        result_path=tmp_path / "result.json",
    )
    assert result["status"] == "FILLED_REARMED"
    assert result["broker_submission_performed"] is True
    assert broker.submit_calls == 1
    assert len(list((tmp_path / "wal").glob("intent-*.json"))) == 1
    assert len(list((tmp_path / "wal").glob("receipt-*.json"))) == 1
    assert json.loads((tmp_path / "rearm.json").read_text())["status"] == "ARMED"


def test_rerun_recovers_by_stable_client_id_without_resubmission(tmp_path) -> None:
    preflight, plan = _ready()
    broker = Broker()
    kwargs = {
        "activation_preflight": preflight, "exact_plan": plan,
        "executed_at": "2026-08-19T13:31:00+00:00", "submit_enabled": True,
        "broker": broker, "wal_directory": tmp_path / "wal",
        "rearm_state_path": tmp_path / "rearm.json",
        "result_path": tmp_path / "result-1.json",
    }
    _disarm(tmp_path / "rearm.json", preflight, plan)
    execute_generic_live_v1_session(**kwargs)
    _disarm(tmp_path / "rearm.json", preflight, plan)
    kwargs["result_path"] = tmp_path / "result-2.json"
    replay = execute_generic_live_v1_session(**kwargs)
    assert replay["status"] == "RECOVERED_FILLED_REARMED"
    assert replay["broker_submission_performed"] is False
    assert broker.submit_calls == 1


def test_submission_break_rearms_before_raising(tmp_path) -> None:
    preflight, plan = _ready()
    _disarm(tmp_path / "rearm.json", preflight, plan)
    with pytest.raises(RuntimeError, match="broker unavailable"):
        execute_generic_live_v1_session(
            activation_preflight=preflight, exact_plan=plan,
            executed_at="2026-08-19T13:31:00+00:00", submit_enabled=True,
            broker=Broker(fail=True), wal_directory=tmp_path / "wal",
            rearm_state_path=tmp_path / "rearm.json",
            result_path=tmp_path / "result.json",
        )
    state = json.loads((tmp_path / "rearm.json").read_text())
    assert state["status"] == "ARMED"
    assert state["trigger"] == "SUBMISSION_BREAK"


def test_rejected_broker_response_is_order_break_and_rearms(tmp_path) -> None:
    preflight, plan = _ready()
    _disarm(tmp_path / "rearm.json", preflight, plan)
    result = execute_generic_live_v1_session(
        activation_preflight=preflight, exact_plan=plan,
        executed_at="2026-08-19T13:31:00+00:00", submit_enabled=True,
        broker=Broker(status="rejected"), wal_directory=tmp_path / "wal",
        rearm_state_path=tmp_path / "rearm.json",
        result_path=tmp_path / "result.json",
    )
    assert result["status"] == "ORDER_BREAK_REARMED"
    assert json.loads((tmp_path / "rearm.json").read_text())["trigger"] == "ORDER_BREAK"


def test_receipt_failure_after_accept_rearms_and_replay_does_not_resubmit(tmp_path, monkeypatch) -> None:
    import core.generic_live_v1_submission as module

    preflight, plan = _ready()
    _disarm(tmp_path / "rearm.json", preflight, plan)
    broker = Broker()
    original = module._write_exclusive

    def fail_receipt(path, payload):
        if path.name.startswith("receipt-"):
            raise OSError("receipt disk failure")
        return original(path, payload)

    monkeypatch.setattr(module, "_write_exclusive", fail_receipt)
    with pytest.raises(OSError, match="receipt disk failure"):
        execute_generic_live_v1_session(
            activation_preflight=preflight, exact_plan=plan,
            executed_at="2026-08-19T13:31:00+00:00", submit_enabled=True,
            broker=broker, wal_directory=tmp_path / "wal",
            rearm_state_path=tmp_path / "rearm.json",
            result_path=tmp_path / "result.json",
        )
    assert broker.submit_calls == 1
    assert json.loads((tmp_path / "rearm.json").read_text())["trigger"] == "ORDER_BREAK"
    monkeypatch.setattr(module, "_write_exclusive", original)
    _disarm(tmp_path / "rearm.json", preflight, plan)
    recovered = execute_generic_live_v1_session(
        activation_preflight=preflight, exact_plan=plan,
        executed_at="2026-08-19T13:31:00+00:00", submit_enabled=True,
        broker=broker, wal_directory=tmp_path / "wal",
        rearm_state_path=tmp_path / "rearm.json",
        result_path=tmp_path / "result-recovered.json",
    )
    assert recovered["status"] == "RECOVERED_FILLED_REARMED"
    assert broker.submit_calls == 1


@pytest.mark.parametrize(
    ("terminal_status", "expected_filled", "expected_cancel"),
    [("partially_filled", 2.0, True), ("accepted", 0.0, True)],
)
def test_partial_or_still_open_order_is_canceled_and_never_treated_as_green(
    tmp_path, terminal_status, expected_filled, expected_cancel,
) -> None:
    preflight, plan = _ready()
    _disarm(tmp_path / "rearm.json", preflight, plan)
    broker = Broker(terminal_status=terminal_status)
    result = execute_generic_live_v1_session(
        activation_preflight=preflight, exact_plan=plan,
        executed_at="2026-08-19T13:31:00+00:00", submit_enabled=True,
        broker=broker, wal_directory=tmp_path / "wal",
        rearm_state_path=tmp_path / "rearm.json",
        result_path=tmp_path / "result.json", poll_attempts=2,
        poll_interval_seconds=0,
    )
    assert result["status"] == "ORDER_BREAK_REARMED"
    assert result["filled_quantity"] == expected_filled
    assert result["cancel_performed"] is expected_cancel
    assert result["broker_order"]["broker_status"] == "canceled"
    context = broker.cancel_contexts[0]
    assert context["action"] == "CANCEL"
    assert context["broker_order_id"] == result["broker_order"]["broker_order_id"]
    assert _GENERIC_LIVE_V4_CAPABILITY.verify(
        context["content_hash"], context["capability_signature"]
    )
    assert result["cancellation_context_hash"] == context["content_hash"]
    receipts = list((tmp_path / "wal").glob("receipt-*.json"))
    persisted = json.loads(receipts[0].read_text())
    assert persisted["cancellation_context_hash"] == context["content_hash"]
    assert persisted["cancel_performed"] is True
    assert json.loads((tmp_path / "rearm.json").read_text())["trigger"] == "ORDER_BREAK"


def test_unknown_broker_status_fails_closed_and_rearms(tmp_path) -> None:
    preflight, plan = _ready()
    _disarm(tmp_path / "rearm.json", preflight, plan)
    with pytest.raises(GenericLiveV1SubmissionError, match="status is unknown"):
        execute_generic_live_v1_session(
            activation_preflight=preflight, exact_plan=plan,
            executed_at="2026-08-19T13:31:00+00:00", submit_enabled=True,
            broker=Broker(status="calculated"), wal_directory=tmp_path / "wal",
            rearm_state_path=tmp_path / "rearm.json",
            result_path=tmp_path / "result.json", poll_interval_seconds=0,
        )
    assert json.loads((tmp_path / "rearm.json").read_text())["trigger"] == "ORDER_BREAK"


def test_recovered_partial_order_is_canceled_without_resubmission(tmp_path) -> None:
    preflight, plan = _ready()
    order = [*plan["sell_orders"], *plan["buy_orders"]][0]
    broker = Broker(terminal_status="partially_filled")
    recovered = {
        "id": "broker-order-1", "client_order_id": order["client_order_id"],
        "status": "accepted", "symbol": order["symbol"], "side": order["side"],
        "qty": order["quantity"], "filled_qty": "0",
    }
    broker.orders[order["client_order_id"]] = recovered
    broker.by_id[recovered["id"]] = recovered
    _disarm(tmp_path / "rearm.json", preflight, plan)
    result = execute_generic_live_v1_session(
        activation_preflight=preflight, exact_plan=plan,
        executed_at="2026-08-19T13:31:00+00:00", submit_enabled=True,
        broker=broker, wal_directory=tmp_path / "wal",
        rearm_state_path=tmp_path / "rearm.json",
        result_path=tmp_path / "result.json", poll_interval_seconds=0,
    )
    assert result["status"] == "ORDER_BREAK_REARMED"
    assert result["broker_submission_performed"] is False
    assert result["cancel_performed"] is True
    assert broker.submit_calls == 0


def test_exact_plan_client_id_and_signed_mutation_context_reach_boundary(tmp_path) -> None:
    preflight, plan = _ready()
    order = [*plan["sell_orders"], *plan["buy_orders"]][0]
    broker = Broker()
    captured = {}
    original = broker.submit_generic_live_v4_limit_order

    def capture(**kwargs):
        captured.update(kwargs)
        return original(**kwargs)

    broker.submit_generic_live_v4_limit_order = capture
    _disarm(tmp_path / "rearm.json", preflight, plan)
    result = execute_generic_live_v1_session(
        activation_preflight=preflight, exact_plan=plan,
        executed_at="2026-08-19T13:31:00+00:00", submit_enabled=True,
        broker=broker, wal_directory=tmp_path / "wal",
        rearm_state_path=tmp_path / "rearm.json",
        result_path=tmp_path / "result.json", poll_interval_seconds=0,
    )
    context = captured["mutation_context"]
    assert captured["client_order_id"] == order["client_order_id"]
    assert context["action"] == "SUBMIT"
    assert context["client_order_id"] == order["client_order_id"]
    assert context["limit_price"] == order["enforcement_price"]
    assert context["content_hash"] == result["mutation_context_hash"]
    assert len(context["capability_signature"]) == 64
    assert _GENERIC_LIVE_V4_CAPABILITY.verify(
        context["content_hash"], context["capability_signature"]
    )
    assert not _GENERIC_LIVE_V4_CAPABILITY.verify(
        "f" * 64, context["capability_signature"]
    )


def test_order_lifecycle_builder_binds_partial_fill_hashes(tmp_path) -> None:
    preflight, plan = _ready()
    _disarm(tmp_path / "rearm.json", preflight, plan)
    result = execute_generic_live_v1_session(
        activation_preflight=preflight, exact_plan=plan,
        executed_at="2026-08-19T13:31:00+00:00", submit_enabled=True,
        broker=Broker(terminal_status="partially_filled"),
        wal_directory=tmp_path / "wal", rearm_state_path=tmp_path / "rearm.json",
        result_path=tmp_path / "result.json", poll_interval_seconds=0,
    )
    fill_hash = "a" * 64
    lifecycle = seal_generic_live_v1_order_lifecycle(
        submission_result=result, observed_at="2026-08-19T13:35:00+00:00",
        broker_order_evidence_hash="c" * 64,
        broker_fill_evidence_hashes=[fill_hash],
    )
    assert lifecycle["status"] == "PARTIAL_CANCELED"
    assert lifecycle["exact_order_id"] == result["exact_order_id"]
    assert lifecycle["broker_fill_evidence_hashes"] == [fill_hash]
    assert lifecycle["cancellation_context_hash"] == result["cancellation_context_hash"]


@pytest.mark.parametrize("stage", ["order", "reconciliation", "accounting", "reporting"])
def test_every_typed_downstream_break_rearms(tmp_path, monkeypatch, stage) -> None:
    import core.generic_live_v1_submission as module

    preflight, plan = _ready()
    broker = Broker()
    _disarm(tmp_path / "session.json", preflight, plan)
    submitted = execute_generic_live_v1_session(
        activation_preflight=preflight, exact_plan=plan,
        executed_at="2026-08-19T13:31:00+00:00",
        submit_enabled=True, broker=broker, wal_directory=tmp_path / "wal",
        rearm_state_path=tmp_path / "session.json",
        result_path=tmp_path / "submission.json", poll_interval_seconds=0,
    )
    fill_hash = "b" * 64
    order = seal_generic_live_v1_order_lifecycle(
        submission_result=submitted, observed_at="2026-08-19T20:00:00+00:00",
        broker_order_evidence_hash="c" * 64,
        broker_fill_evidence_hashes=[fill_hash],
    )
    trigger = {
        "order": "ORDER_BREAK", "reconciliation": "RECONCILIATION_BREAK",
        "accounting": "ACCOUNTING_BREAK", "reporting": "REPORTING_BREAK",
    }[stage]
    if stage == "order":
        order["status"] = "PARTIALLY_FILLED"
    else:
        monkeypatch.setattr(module, "validate_lane_reconciliation", lambda *a, **k: (
            (_ for _ in ()).throw(ValueError("reconciliation break"))
            if stage == "reconciliation" else {
                "status": "PASS", "accounting_ready": True, "reconciled_fills": [],
                "content_hash": "a" * 64, "account_id_hash": submitted["account_id_hash"],
                "lane_id": "generic-live-v1", "plan_hash": submitted["plan_hash"],
                "broker_orders": [{
                    "order_id": submitted["exact_order_id"],
                    "broker_order_id": submitted["broker_order"]["broker_order_id"],
                    "filled_quantity": submitted["filled_quantity"],
                }],
                "broker_fills": [{}], "source_hashes": {
                    "broker_fills": [fill_hash], "broker_orders": ["c" * 64],
                    "wal_intents": [submitted["intent_hash"]],
                },
            }
        ))
        monkeypatch.setattr(module, "validate_accounting_journal", lambda rows: (
            (_ for _ in ()).throw(ValueError("accounting break"))
            if stage == "accounting" else []
        ))
        monkeypatch.setattr(module, "validate_lane_performance", lambda payload: (
            (_ for _ in ()).throw(ValueError("reporting break"))
        ))
    with pytest.raises(Exception):
        finalize_generic_live_v1_posttrade(
            submission_result=submitted, exact_plan=plan, order_lifecycle=order,
            reconciliation={}, journal_entries=[], performance={}, dashboard_projection={},
            finalized_at="2026-08-19T22:00:00+00:00",
            rearm_state_path=tmp_path / "rearm.json", result_path=tmp_path / "posttrade.json",
        )
    assert json.loads((tmp_path / "rearm.json").read_text())["trigger"] == trigger


def test_posttrade_rejects_arbitrary_boolean_hash_interface(tmp_path) -> None:
    with pytest.raises(TypeError):
        finalize_generic_live_v1_posttrade(
            submission_result={}, finalized_at="2026-08-19T22:00:00+00:00",
            order_lifecycle_green=True, reconciliation_green=True,
            accounting_green=True, reporting_green=True,
            rearm_state_path=tmp_path / "rearm.json", evidence_hashes=["a" * 64],
        )


def test_blocked_preflight_cannot_submit(tmp_path) -> None:
    preflight, plan = _ready()
    blocked = dict(preflight)
    blocked["status"] = "BLOCKED"
    with pytest.raises(Exception):
        execute_generic_live_v1_session(
            activation_preflight=blocked, exact_plan=plan,
            executed_at="2026-08-19T13:31:00+00:00", submit_enabled=True,
            broker=Broker(), wal_directory=tmp_path / "wal",
            rearm_state_path=tmp_path / "rearm.json",
            result_path=tmp_path / "result.json",
        )


def test_submission_requires_exact_protected_lyra_decision(tmp_path) -> None:
    preflight, plan = _ready()
    with pytest.raises(GenericLiveV1SubmissionError, match="exact protected Lyra"):
        _execute_generic_live_v1_session(
            activation_preflight=preflight, exact_plan=plan,
            executed_at="2026-08-19T13:31:00+00:00", submit_enabled=True,
            broker=Broker(), wal_directory=tmp_path / "wal",
            rearm_state_path=tmp_path / "rearm.json",
            result_path=tmp_path / "result.json",
        )


def test_submission_requires_exact_preflight_plan_binding(tmp_path) -> None:
    preflight, plan = _ready()
    tampered = dict(plan)
    tampered["content_hash"] = "f" * 64
    with pytest.raises(GenericLiveV1SubmissionError, match="exact plan is invalid"):
        execute_generic_live_v1_session(
            activation_preflight=preflight, exact_plan=tampered,
            executed_at="2026-08-19T13:31:00+00:00",
        )

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import sys

import pytest

from brokers.alpaca_broker import AlpacaBroker
from core.execution_authority_policy import (
    EXACT_PAPER_EXECUTION_AUTHORITY,
    LEGACY_EQUITY_BROKER_MUTATION_ENABLED,
    LIVE_CAPITAL_MUTATION_ENABLED,
    OPTIONS_CAPITAL_MUTATION_ENABLED,
)
from core.options_execution import build_options_execution_review
from core.options_smoke_session import build_options_smoke_session
from paper.paper_broker import _submit_alpaca_orders
from scripts.execute_alpaca_orders import _submit_orders
from scripts.options_smoke_session import main as options_smoke_main


class NoMutationClient:
    def __init__(self) -> None:
        self.read_calls = 0
        self.submit_calls = 0

    def get_orders(self, *_args, **_kwargs):
        self.read_calls += 1
        raise AssertionError("legacy guard must run before broker reads")

    def submit_order(self, *_args, **_kwargs):
        self.submit_calls += 1
        raise AssertionError("legacy/live/options path reached broker mutation")


class RecordingOptionsBroker:
    def __init__(self) -> None:
        self.submit_calls = 0

    def submit_option_market_order(self, **_kwargs):
        self.submit_calls += 1
        raise AssertionError("options mutation must remain structurally disabled")


def _ready_option_review() -> dict[str, object]:
    return {
        "mode": "paper_review",
        "paper_review_status": "READY_FOR_PAPER_REVIEW",
        "paper_ready": True,
        "allocator_review_status": "ready",
        "paper_plan": {
            "strategy": "protective_put",
            "contracts_recommended": 1,
            "expiry": "2026-09-18",
            "target_dte": 37,
            "long_put": {"strike": 600.0, "kind": "PUT"},
        },
    }


def test_choice2_policy_constants_have_one_paper_authority_and_no_live_or_options() -> None:
    assert EXACT_PAPER_EXECUTION_AUTHORITY == "caerus_orchestrator_exact_plan_only"
    assert LEGACY_EQUITY_BROKER_MUTATION_ENABLED is False
    assert LIVE_CAPITAL_MUTATION_ENABLED is False
    assert OPTIONS_CAPITAL_MUTATION_ENABLED is False


def test_legacy_paper_broker_boundary_is_not_unlocked_by_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in (
        "PYTEST_CURRENT_TEST",
        "ALLOW_LIVE_TRADING",
        "CAERUS_LIVE_PILOT_APPROVED",
        "ALLOW_OPTIONS_EXECUTION",
        "ALLOW_OPTIONS_SUBMISSION",
    ):
        monkeypatch.setenv(key, "1")
    client = NoMutationClient()
    broker = AlpacaBroker(
        trading_client=client,
        paper=True,
        base_url="https://paper-api.alpaca.markets",
    )
    with pytest.raises(PermissionError, match="legacy_executor_disabled_choice2_exact_plan_required"):
        _submit_alpaca_orders(
            alpaca=broker,
            orders=[
                {
                    "order_id": "legacy:AAPL:BUY",
                    "ticker": "AAPL",
                    "side": "BUY",
                    "quantity": 1,
                    "order_type": "MKT",
                }
            ],
            run_date="2026-08-12",
            alpaca_submissions=[],
            submission_metadata={},
            idempotent_skips=[],
            idempotent_drop_reasons=Counter(),
            alpaca_submission_summary={},
        )
    assert client.read_calls == 0
    assert client.submit_calls == 0


def test_legacy_direct_executor_boundary_blocks_real_adapter_before_read(
    tmp_path: Path,
) -> None:
    client = NoMutationClient()
    broker = AlpacaBroker(
        trading_client=client,
        paper=True,
        base_url="https://paper-api.alpaca.markets",
    )
    with pytest.raises(PermissionError, match="legacy_executor_disabled_choice2_exact_plan_required"):
        _submit_orders(
            {
                "trade_date": "2026-08-12",
                "mode": "PAPER",
                "trades": [{"ticker": "AAPL", "side": "BUY", "shares": 1}],
            },
            broker,
            tmp_path,
        )
    assert client.read_calls == 0
    assert client.submit_calls == 0


def test_legacy_paper_cli_cannot_be_unlocked_by_pytest_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paper import run_paper

    monkeypatch.setenv("PYTEST_CURRENT_TEST", "spoofed")
    monkeypatch.setattr(sys, "argv", ["run_paper.py", "2026-08-11"])
    with pytest.raises(PermissionError, match="legacy_executor_disabled_choice2_exact_plan_required"):
        run_paper.main()


def test_live_equity_adapter_is_disabled_even_when_all_env_gates_are_armed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key, value in {
        "CAERUS_LIVE_PILOT_APPROVED": "1",
        "CAERUS_LIVE_PILOT_CAPITAL_CAP": "1000000",
        "CAERUS_LIVE_PILOT_SLEEVE_ID": "orion",
        "CAERUS_LIVE_PILOT_MAX_ORDERS": "100",
        "CAERUS_LIVE_PILOT_DRY_RUN": "0",
        "CAERUS_LIVE_PILOT_KILL_SWITCH": "0",
    }.items():
        monkeypatch.setenv(key, value)
    client = NoMutationClient()
    broker = AlpacaBroker(
        trading_client=client,
        paper=False,
        base_url="https://api.alpaca.markets",
    )
    with pytest.raises(RuntimeError, match="live_capital_disabled_by_owner_policy"):
        broker.submit_market_order(
            symbol="AAPL",
            qty=1,
            side="BUY",
            client_order_id="must-not-submit",
            estimated_notional=200.0,
        )
    assert client.submit_calls == 0


def test_direct_paper_adapter_requires_exact_executor_capability() -> None:
    client = NoMutationClient()
    broker = AlpacaBroker(
        trading_client=client,
        paper=True,
        base_url="https://paper-api.alpaca.markets",
    )
    with pytest.raises(PermissionError, match="exact_execution_capability_required"):
        broker.submit_market_order(
            symbol="AAPL",
            qty=1,
            side="BUY",
            client_order_id="direct-paper-bypass",
            estimated_notional=200.0,
        )
    assert client.submit_calls == 0


def test_options_review_cannot_submit_when_argument_and_policy_request_it(
    tmp_path: Path,
) -> None:
    policy = tmp_path / "options-policy.json"
    policy.write_text(
        json.dumps(
            {
                "allow_live_submission": True,
                "allowed_strategies": ["protective_put"],
                "max_contracts": 1,
            }
        ),
        encoding="utf-8",
    )
    broker = RecordingOptionsBroker()
    review = build_options_execution_review(
        trade_date="2026-08-12",
        asof_date="2026-08-12",
        paper_review=_ready_option_review(),
        policy_path=policy,
        allow_live_submission=True,
        broker=broker,
    )
    assert review["execution_status"] == "BLOCKED_OWNER_POLICY"
    assert review["policy"]["allow_live_submission"] is False
    assert review["submission"]["attempted"] is False
    assert broker.submit_calls == 0


def test_options_smoke_request_is_review_only_and_does_not_write_false_state(
    tmp_path: Path,
) -> None:
    broker = RecordingOptionsBroker()
    state_root = tmp_path / "state"
    review = build_options_smoke_session(
        trade_date="2026-08-12",
        asof_date="2026-08-12",
        broker=broker,
        account={"equity": "100000", "options_buying_power": "50000"},
        positions=[],
        state_root=state_root,
        allow_submission=True,
    )
    assert review["execution_status"] == "BLOCKED_OWNER_POLICY"
    assert review["submitted_count"] == 0
    assert broker.submit_calls == 0
    assert not (state_root / "options_smoke_session_state.json").exists()


def test_options_smoke_cli_blocks_submit_before_credentials_or_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
    monkeypatch.delenv("ALPACA_KEY_ID", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    with pytest.raises(PermissionError, match="options_capital_disabled_by_owner_policy"):
        options_smoke_main(["--trade-date", "2026-08-12", "--submit"])


def test_option_adapter_mutation_methods_are_unconditionally_disabled() -> None:
    client = NoMutationClient()
    broker = AlpacaBroker(
        trading_client=client,
        paper=True,
        base_url="https://paper-api.alpaca.markets",
    )
    with pytest.raises(RuntimeError, match="options_capital_disabled_by_owner_policy"):
        broker.submit_option_market_order(
            symbol="SPY260918P00600000",
            qty=1,
            side="BUY",
            client_order_id="must-not-submit-option",
        )
    with pytest.raises(RuntimeError, match="options_capital_disabled_by_owner_policy"):
        broker.submit_option_limit_order(
            symbol="SPY260918P00600000",
            qty=1,
            side="BUY",
            limit_price=2.0,
            client_order_id="must-not-submit-option-limit",
        )
    assert client.submit_calls == 0

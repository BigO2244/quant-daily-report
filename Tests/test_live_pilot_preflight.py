from __future__ import annotations

import pytest

from brokers.alpaca_broker import AlpacaBroker
from core.live_pilot_preflight import (
    LIVE_CAPITAL_CAP_ENV,
    LIVE_PILOT_SLEEVE_ENV,
    LIVE_TRADING_FLAG_ENV,
    build_live_pilot_preflight_result,
    validate_alpaca_submission_guardrails,
)
from core.trading_mode import canonical_trading_mode_label


def _clear_live_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "MODE",
        "TRADING_MODE",
        LIVE_TRADING_FLAG_ENV,
        LIVE_CAPITAL_CAP_ENV,
        LIVE_PILOT_SLEEVE_ENV,
    ):
        monkeypatch.delenv(name, raising=False)


def test_live_preflight_mode_is_canonical() -> None:
    assert canonical_trading_mode_label("live_preflight") == "LIVE_PREFLIGHT"
    assert canonical_trading_mode_label("live-preflight") == "LIVE_PREFLIGHT"


def test_paper_guardrail_allows_paper_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_live_env(monkeypatch)
    monkeypatch.setenv("TRADING_MODE", "paper")

    result = validate_alpaca_submission_guardrails(
        broker_paper=True,
        base_url="https://paper-api.alpaca.markets",
    )

    assert result.status == "PASS"
    assert result.reason_code == "paper_submission_endpoint_confirmed"


def test_paper_mode_refuses_live_broker_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_live_env(monkeypatch)
    monkeypatch.setenv("TRADING_MODE", "paper")

    with pytest.raises(RuntimeError, match="live_broker_requires_explicit_live_mode"):
        validate_alpaca_submission_guardrails(
            broker_paper=False,
            base_url="https://api.alpaca.markets",
        )


def test_live_preflight_refuses_orders_even_with_live_approvals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_live_env(monkeypatch)
    monkeypatch.setenv("TRADING_MODE", "live_preflight")
    monkeypatch.setenv(LIVE_TRADING_FLAG_ENV, "approve_live_pilot")
    monkeypatch.setenv(LIVE_CAPITAL_CAP_ENV, "1000")
    monkeypatch.setenv(LIVE_PILOT_SLEEVE_ENV, "polaris")

    result = build_live_pilot_preflight_result(
        broker_paper=False,
        base_url="https://api.alpaca.markets",
    )

    assert result.status == "BLOCKED"
    assert result.reason_code == "live_preflight_never_submits_orders"
    assert result.live_orders_allowed is False
    with pytest.raises(RuntimeError, match="live_preflight_never_submits_orders"):
        validate_alpaca_submission_guardrails(
            broker_paper=False,
            base_url="https://api.alpaca.markets",
        )


def test_legacy_live_mode_is_blocked_use_live_pilot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_live_env(monkeypatch)
    monkeypatch.setenv("TRADING_MODE", "live")

    with pytest.raises(RuntimeError, match="legacy_live_mode_blocked_use_live_pilot"):
        validate_alpaca_submission_guardrails(
            broker_paper=False,
            base_url="https://api.alpaca.markets",
        )


def test_mode_ambiguity_blocks_live_submission(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_live_env(monkeypatch)
    monkeypatch.setenv("MODE", "paper")
    monkeypatch.setenv("TRADING_MODE", "live")

    with pytest.raises(RuntimeError, match="Mode ambiguity"):
        validate_alpaca_submission_guardrails(
            broker_paper=False,
            base_url="https://api.alpaca.markets",
        )


def test_broker_submit_blocks_live_preflight_before_sdk_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_live_env(monkeypatch)
    monkeypatch.setenv("TRADING_MODE", "live_preflight")
    broker = AlpacaBroker(
        trading_client=object(),
        paper=False,
        base_url="https://api.alpaca.markets",
    )

    with pytest.raises(RuntimeError, match="live_capital_disabled_by_owner_policy"):
        broker.submit_market_order(
            symbol="AAPL",
            qty=1,
            side="BUY",
            client_order_id="test-live-preflight",
        )

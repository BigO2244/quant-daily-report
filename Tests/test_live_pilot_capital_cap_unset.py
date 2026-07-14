from __future__ import annotations

import pytest

from core.live_pilot_guardrails import (
    LIVE_PILOT_ACCOUNT_ID_ENV,
    LIVE_PILOT_APPROVED_ENV,
    LIVE_PILOT_CAPITAL_CAP_ENV,
    LIVE_PILOT_DRY_RUN_ENV,
    LIVE_PILOT_KILL_SWITCH_ENV,
    LIVE_PILOT_MAX_ORDERS_ENV,
    LIVE_PILOT_MODE,
    LIVE_PILOT_SLEEVE_ID_ENV,
    build_live_pilot_gate_result,
    validate_live_pilot_submission_guardrails,
)


def _approve_without_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reach the live-pilot submission branch with every gate satisfied and the
    capital cap left unset. An unset cap means UNCAPPED (portfolio-proportional
    sizing, no fixed USD ceiling), so the gate must pass through, not block."""
    for key in (
        "MODE",
        "WORKFLOW_KIND",
        "CAERUS_CRON_CONTEXT",
        "RUNNING_UNDER_CRON",
        "CAERUS_LIVE_PILOT_CRON_CONTEXT",
        "CAERUS_LIVE_PILOT_CRON_APPROVED",
        LIVE_PILOT_CAPITAL_CAP_ENV,
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TRADING_MODE", LIVE_PILOT_MODE)
    monkeypatch.setenv(LIVE_PILOT_APPROVED_ENV, "1")
    monkeypatch.setenv(LIVE_PILOT_SLEEVE_ID_ENV, "polaris")
    monkeypatch.setenv(LIVE_PILOT_ACCOUNT_ID_ENV, "acct-123")
    monkeypatch.setenv(LIVE_PILOT_MAX_ORDERS_ENV, "1")
    monkeypatch.setenv(LIVE_PILOT_DRY_RUN_ENV, "0")
    monkeypatch.setenv(LIVE_PILOT_KILL_SWITCH_ENV, "0")
    monkeypatch.setenv("CAERUS_LIVE_PILOT_SUBMIT_APPROVED", "1")


def test_gate_treats_unset_cap_as_uncapped_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    # An unset capital cap is UNCAPPED, not blocked: capital scales proportionally
    # with portfolio assets, so the gate must pass the order through at full sizing.
    _approve_without_cap(monkeypatch)

    result = build_live_pilot_gate_result(
        broker_paper=False,
        base_url="https://api.alpaca.markets",
        order_notional=300.0,
        submission_intent=True,
    )

    assert result.capital_cap_usd is None
    assert result.reason_code == "live_pilot_guardrails_satisfied"
    assert result.status == "PASS"
    assert result.live_orders_allowed is True


def test_submission_guardrail_passes_through_on_unset_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Must not raise: an unset cap is uncapped, so submission guardrails pass and the
    # order is allowed through at full portfolio-proportional sizing.
    _approve_without_cap(monkeypatch)

    result = validate_live_pilot_submission_guardrails(
        broker_paper=False,
        base_url="https://api.alpaca.markets",
        order_notional=300.0,
    )

    assert result.capital_cap_usd is None
    assert result.status == "PASS"
    assert result.live_orders_allowed is True

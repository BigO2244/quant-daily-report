from __future__ import annotations

from pathlib import Path

import pytest

from brokers.alpaca_broker import AlpacaBroker
from core.live_pilot_guardrails import (
    LIVE_PILOT_ACCOUNT_ID_ENV,
    LIVE_PILOT_APPROVED_MAX_CAP_USD,
    LIVE_PILOT_APPROVED_ENV,
    LIVE_PILOT_CAPITAL_CAP_ENV,
    LIVE_PILOT_DRY_RUN_ENV,
    LIVE_PILOT_KILL_SWITCH_ENV,
    LIVE_PILOT_MAX_CAP_USD,
    LIVE_PILOT_MAX_ORDERS_ENV,
    LIVE_PILOT_MODE,
    LIVE_PILOT_SLEEVE_ID_ENV,
    build_live_pilot_gate_result,
    normalize_live_pilot_limit_price,
    validate_live_pilot_plan,
    validate_live_pilot_submission_guardrails,
)
from core.trading_mode import canonical_trading_mode_label


def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "MODE",
        "TRADING_MODE",
        "WORKFLOW_KIND",
        "CAERUS_CRON_CONTEXT",
        "RUNNING_UNDER_CRON",
        "CAERUS_LIVE_PILOT_CRON_CONTEXT",
        "CAERUS_LIVE_PILOT_CRON_APPROVED",
        LIVE_PILOT_APPROVED_ENV,
        LIVE_PILOT_CAPITAL_CAP_ENV,
        LIVE_PILOT_SLEEVE_ID_ENV,
        LIVE_PILOT_ACCOUNT_ID_ENV,
        "CAERUS_LIVE_PILOT_ACCOUNT_ID_HASH",
        LIVE_PILOT_MAX_ORDERS_ENV,
        LIVE_PILOT_KILL_SWITCH_ENV,
        LIVE_PILOT_DRY_RUN_ENV,
        "CAERUS_LIVE_PILOT_SELL_WHITELIST",
        "CAERUS_LIVE_PILOT_SELLS_ENABLED",
        "CAERUS_LIVE_PILOT_ALLOW_FRACTIONAL",
    ):
        monkeypatch.delenv(key, raising=False)


def _approve(monkeypatch: pytest.MonkeyPatch, *, dry_run: str = "1") -> None:
    monkeypatch.setenv("TRADING_MODE", LIVE_PILOT_MODE)
    monkeypatch.setenv(LIVE_PILOT_APPROVED_ENV, "1")
    monkeypatch.setenv(LIVE_PILOT_CAPITAL_CAP_ENV, "500")
    monkeypatch.setenv(LIVE_PILOT_SLEEVE_ID_ENV, "polaris")
    monkeypatch.setenv(LIVE_PILOT_ACCOUNT_ID_ENV, "acct-123")
    monkeypatch.setenv(LIVE_PILOT_MAX_ORDERS_ENV, "1")
    monkeypatch.setenv(LIVE_PILOT_DRY_RUN_ENV, dry_run)
    # Kill switch fails closed: arming live now requires an explicit recognized
    # "off" value; an unset/garbage value blocks. Disarm it for the approve fixture.
    monkeypatch.setenv(LIVE_PILOT_KILL_SWITCH_ENV, "0")
    # Master sell gate fails closed: enable it so sell-path fixtures reach the
    # per-symbol whitelist check. Default-off behavior has dedicated coverage.
    monkeypatch.setenv("CAERUS_LIVE_PILOT_SELLS_ENABLED", "1")


def test_live_pilot_mode_is_canonical() -> None:
    assert canonical_trading_mode_label("live_pilot") == "LIVE_PILOT"
    assert canonical_trading_mode_label("live-pilot") == "LIVE_PILOT"


def test_live_pilot_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)

    result = build_live_pilot_gate_result(
        broker_paper=False,
        base_url="https://api.alpaca.markets",
    )

    assert result.status == "BLOCKED"
    assert result.reason_code == "live_broker_requires_live_pilot_mode"
    assert result.live_orders_allowed is False


@pytest.mark.parametrize(
    ("env_name", "env_value", "reason"),
    [
        (LIVE_PILOT_APPROVED_ENV, None, "missing_live_pilot_approval"),
        # Capital cap is no longer required or ceiling-limited at the pre-snapshot
        # gate: it is resolved dynamically from the account portfolio value after the
        # broker snapshot (see resolve_dynamic_cap). The optional env cap only tightens.
        (LIVE_PILOT_SLEEVE_ID_ENV, None, "missing_live_pilot_sleeve_id"),
        (LIVE_PILOT_ACCOUNT_ID_ENV, None, "missing_live_pilot_expected_account_id"),
        (LIVE_PILOT_MAX_ORDERS_ENV, None, "missing_positive_live_pilot_max_orders"),
        (LIVE_PILOT_KILL_SWITCH_ENV, "1", "live_pilot_kill_switch_enabled"),
    ],
)
def test_live_pilot_refuses_missing_or_invalid_gates(
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
    env_value: str | None,
    reason: str,
) -> None:
    _clear(monkeypatch)
    _approve(monkeypatch)
    if env_value is None:
        monkeypatch.delenv(env_name, raising=False)
    else:
        monkeypatch.setenv(env_name, env_value)

    result = build_live_pilot_gate_result(
        broker_paper=False,
        base_url="https://api.alpaca.markets",
    )

    assert result.status == "BLOCKED"
    assert result.reason_code == reason
    assert result.live_orders_allowed is False


def test_live_pilot_accepts_approved_500_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    _approve(monkeypatch, dry_run="0")

    result = validate_live_pilot_submission_guardrails(
        broker_paper=False,
        base_url="https://api.alpaca.markets",
        order_notional=500.0,
    )

    assert LIVE_PILOT_APPROVED_MAX_CAP_USD == 500.0
    assert LIVE_PILOT_MAX_CAP_USD == LIVE_PILOT_APPROVED_MAX_CAP_USD
    assert result.status == "PASS"
    assert result.capital_cap_usd == 500.0
    assert result.live_orders_allowed is True


def test_live_pilot_kill_switch_blocks_submission(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    _approve(monkeypatch, dry_run="0")
    monkeypatch.setenv(LIVE_PILOT_KILL_SWITCH_ENV, "1")

    result = build_live_pilot_gate_result(
        broker_paper=False,
        base_url="https://api.alpaca.markets",
        order_notional=50.0,
        submission_intent=True,
    )

    assert result.status == "BLOCKED"
    assert result.reason_code == "live_pilot_kill_switch_enabled"
    assert result.live_orders_allowed is False


def test_live_pilot_refuses_paper_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    _approve(monkeypatch)

    result = build_live_pilot_gate_result(
        broker_paper=True,
        base_url="https://paper-api.alpaca.markets",
    )

    assert result.status == "BLOCKED"
    assert result.reason_code == "live_pilot_requires_live_alpaca_endpoint"


def test_live_pilot_refuses_cron_without_explicit_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    _approve(monkeypatch)
    monkeypatch.setenv("WORKFLOW_KIND", "live")

    result = build_live_pilot_gate_result(
        broker_paper=False,
        base_url="https://api.alpaca.markets",
    )

    assert result.reason_code == "live_pilot_cron_context_requires_explicit_approval"


def test_cron_execute_remains_paper_forced() -> None:
    text = Path("scripts/cron_execute.sh").read_text(encoding="utf-8")

    assert 'export MODE="paper"' in text
    assert 'export TRADING_MODE="paper"' in text
    assert 'export ALPACA_PAPER="1"' in text
    assert "live_pilot" not in text


def test_live_pilot_dry_run_passes_but_does_not_allow_orders(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    _approve(monkeypatch, dry_run="1")

    result = build_live_pilot_gate_result(
        broker_paper=False,
        base_url="https://api.alpaca.markets",
    )

    assert result.status == "PASS"
    assert result.reason_code == "live_pilot_dry_run_enabled"
    assert result.live_orders_allowed is False


def test_live_pilot_live_submit_requires_order_notional(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    _approve(monkeypatch, dry_run="0")

    with pytest.raises(RuntimeError, match="missing_live_order_notional"):
        validate_live_pilot_submission_guardrails(
            broker_paper=False,
            base_url="https://api.alpaca.markets",
        )


def test_live_pilot_live_submit_requires_notional_under_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    _approve(monkeypatch, dry_run="0")

    with pytest.raises(RuntimeError, match="order_notional_exceeds_live_pilot_cap"):
        validate_live_pilot_submission_guardrails(
            broker_paper=False,
            base_url="https://api.alpaca.markets",
            order_notional=501.0,
        )

    result = validate_live_pilot_submission_guardrails(
        broker_paper=False,
        base_url="https://api.alpaca.markets",
        order_notional=500.0,
    )
    assert result.status == "PASS"
    assert result.live_orders_allowed is True


def test_broker_market_submit_guard_failure_does_not_call_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    _approve(monkeypatch, dry_run="0")

    class Client:
        calls = 0

        def submit_order(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError("SDK submit must not be called")

    client = Client()
    broker = AlpacaBroker(client, paper=False, base_url="https://api.alpaca.markets")

    with pytest.raises(RuntimeError, match="missing_live_order_notional"):
        broker.submit_market_order(
            symbol="AAPL",
            qty=1,
            side="BUY",
            client_order_id="test-live-pilot-market",
        )
    assert client.calls == 0



def test_live_pilot_limit_price_normalization(monkeypatch: pytest.MonkeyPatch) -> None:
    assert normalize_live_pilot_limit_price(228.38999938964844) == 228.39
    assert normalize_live_pilot_limit_price(0.123456) == 0.1235

    _clear(monkeypatch)
    _approve(monkeypatch)
    monkeypatch.setenv("CAERUS_LIVE_PILOT_ALLOW_FRACTIONAL", "1")
    result = validate_live_pilot_plan(
        [
            {
                "ticker": "JNJ",
                "side": "BUY",
                "shares": 0.4378475426561624,
                "limit_price": 228.39,
                "original_limit_price": 228.38999938964844,
                "normalized_limit_price": 228.39,
            }
        ],
        capital_cap_usd=500,
        max_orders=1,
        run_id="run-price-normalization",
    )

    assert result.status == "PASS"
    order = result.orders[0].to_dict()
    assert order["original_limit_price"] == 228.38999938964844
    assert order["normalized_limit_price"] == 228.39
    assert order["limit_price"] == 228.39


def test_plan_validation_blocks_over_cap_and_too_many_orders(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    _approve(monkeypatch)
    trades = [
        {"ticker": "AAPL", "side": "BUY", "shares": 4, "limit_price": 100},
        {"ticker": "MSFT", "side": "BUY", "shares": 2, "limit_price": 60},
    ]

    result = validate_live_pilot_plan(
        trades,
        capital_cap_usd=500,
        max_orders=1,
        run_id="run-1",
    )

    assert result.status == "BLOCKED"
    assert "live_pilot_order_count_exceeds_max_orders" in result.reason_codes
    assert "live_pilot_total_notional_exceeds_cap" in result.reason_codes


def test_full_rebalance_high_turnover_not_blocked_by_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    # FR-104 full rebalance: gross turnover (buys + sells) can exceed the cap, but the
    # cap bounds deployed BUY notional only. Sells are exits and must not consume it.
    _clear(monkeypatch)
    _approve(monkeypatch)
    monkeypatch.setenv("CAERUS_LIVE_PILOT_SELLS_ENABLED", "1")
    monkeypatch.setenv("CAERUS_LIVE_PILOT_SELL_WHITELIST", "*")
    trades = [
        {"ticker": "OLD", "side": "SELL", "shares": 3, "limit_price": 100},   # $300 exit
        {"ticker": "NEWA", "side": "BUY", "shares": 5, "limit_price": 30},     # $150
        {"ticker": "NEWB", "side": "BUY", "shares": 5, "limit_price": 20},     # $100
    ]
    # Gross = $550 > $502 cap; buy notional = $250 <= cap -> must PASS.
    result = validate_live_pilot_plan(
        trades,
        capital_cap_usd=502,
        max_orders=50,
        run_id="full-rebalance",
    )
    assert result.status == "PASS", result.reason_codes
    assert result.total_notional == 550.0  # gross reported unchanged


def test_full_rebalance_blocks_when_buys_exceed_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    _approve(monkeypatch)
    monkeypatch.setenv("CAERUS_LIVE_PILOT_SELLS_ENABLED", "1")
    monkeypatch.setenv("CAERUS_LIVE_PILOT_SELL_WHITELIST", "*")
    trades = [
        {"ticker": "OLD", "side": "SELL", "shares": 1, "limit_price": 50},     # $50 exit
        {"ticker": "NEWA", "side": "BUY", "shares": 4, "limit_price": 100},     # $400
        {"ticker": "NEWB", "side": "BUY", "shares": 4, "limit_price": 60},      # $240
    ]
    # Buy notional $640 > $502 cap -> BLOCKED even though sells are small.
    result = validate_live_pilot_plan(
        trades,
        capital_cap_usd=502,
        max_orders=50,
        run_id="over-buy",
    )
    assert result.status == "BLOCKED"
    assert "live_pilot_total_notional_exceeds_cap" in result.reason_codes


def test_plan_validation_normalizes_limit_price(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    _approve(monkeypatch)
    monkeypatch.setenv("CAERUS_LIVE_PILOT_ALLOW_FRACTIONAL", "1")

    assert normalize_live_pilot_limit_price(228.38999938964844) == 228.39
    assert normalize_live_pilot_limit_price(0.123456) == 0.1235

    result = validate_live_pilot_plan(
        [
            {
                "ticker": "JNJ",
                "side": "BUY",
                "shares": 0.437847541,
                "limit_price": 228.38999938964844,
            }
        ],
        capital_cap_usd=500,
        max_orders=1,
        run_id="run-1",
    )

    assert result.status == "PASS"
    order = result.orders[0]
    assert order.original_limit_price == 228.38999938964844
    assert order.normalized_limit_price == 228.39
    assert order.limit_price == 228.39
    assert order.notional <= 500


def test_plan_validation_allows_fr104_market_order_with_cap_price(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    _approve(monkeypatch)

    result = validate_live_pilot_plan(
        [
            {
                "ticker": "AAPL",
                "side": "BUY",
                "shares": 1,
                "order_type": "market",
                "expected_price": 50,
            }
        ],
        capital_cap_usd=500,
        max_orders=1,
        run_id="run-1",
    )

    assert result.status == "PASS"
    order = result.orders[0]
    assert order.order_type == "market"
    assert order.expected_price == 50
    assert order.notional == 50


@pytest.mark.parametrize(
    ("trade", "reason"),
    [
        ({"ticker": "AAPL", "side": "SELL", "shares": 1, "limit_price": 10}, "AAPL:sell_not_whitelisted"),
        ({"ticker": "AAPL", "side": "BUY", "shares": -1, "limit_price": 10}, "AAPL:non_positive_qty"),
        ({"ticker": "BTCUSD", "side": "BUY", "shares": 1, "limit_price": 10}, "BTCUSD:unsupported_crypto_symbol"),
        ({"ticker": "AAPL", "side": "BUY", "shares": 1, "order_type": "stop", "price": 10}, "AAPL:unsupported_order_type:stop"),
    ],
)
def test_plan_validation_blocks_unsupported_orders(
    monkeypatch: pytest.MonkeyPatch,
    trade: dict[str, object],
    reason: str,
) -> None:
    _clear(monkeypatch)
    _approve(monkeypatch)

    result = validate_live_pilot_plan(
        [trade],
        capital_cap_usd=500,
        max_orders=1,
        run_id="run-1",
    )

    assert result.status == "BLOCKED"
    assert reason in result.reason_codes

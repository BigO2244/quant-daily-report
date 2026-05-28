"""Tests for Issues 1-4: stale guard, capital budget, PDT constrained, workflow chain."""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from core.live_retry_policy import evaluate_live_retry, retry_reason_compatible
from core.workflow_status import classify_precompute_window, resolve_precompute_final_status

ET = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# ISSUE 1: stale guard + valid bundle → SKIPPED_STALE_VALID (not GUARD_FAILED)
# ---------------------------------------------------------------------------


class TestStaleValidPrecompute:
    def test_stale_guard_blocks_run(self) -> None:
        result = classify_precompute_window(
            now=dt.datetime(2026, 3, 20, 9, 37, tzinfo=ET),
            force_refresh=False,
            event_name="schedule",
        )
        assert result["allow_run"] is False
        assert result["event_freshness_status"] == "stale_precompute"

    def test_resolve_final_status_stale_valid_returns_skipped_stale_valid(self) -> None:
        """When guard blocks but bundle is already VALID, final status should be
        SKIPPED_STALE_VALID — not GUARD_FAILED or SKIPPED_AS_STALE."""
        status = resolve_precompute_final_status(
            guard_outcome="success",
            bundle_outcome="success",
            guard_allow_run="false",
            prior_bundle_status="VALID",
        )
        assert status == "SKIPPED_STALE_VALID"

    def test_resolve_final_status_guard_failed_with_valid_bundle(self) -> None:
        """Even when the guard step fails (exit 1), if the prior bundle was VALID
        the result should be SKIPPED_STALE_VALID — not a hard failure."""
        status = resolve_precompute_final_status(
            guard_outcome="failure",
            bundle_outcome="skipped",
            guard_allow_run="",
            prior_bundle_status="VALID",
        )
        assert status == "SKIPPED_STALE_VALID"

    def test_resolve_final_status_guard_failed_no_bundle_is_guard_failed(self) -> None:
        """Without a valid bundle, guard failure is still GUARD_FAILED."""
        status = resolve_precompute_final_status(
            guard_outcome="failure",
            bundle_outcome="skipped",
            guard_allow_run="",
            prior_bundle_status="",
        )
        assert status == "GUARD_FAILED"

    def test_resolve_final_status_stale_missing_is_skipped_as_stale(self) -> None:
        """Stale + MISSING → SKIPPED_AS_STALE (hard failure pathway)."""
        status = resolve_precompute_final_status(
            guard_outcome="success",
            bundle_outcome="success",
            guard_allow_run="false",
            prior_bundle_status="MISSING",
        )
        assert status == "SKIPPED_AS_STALE"

    def test_workflow_yaml_stale_step_checks_bundle_status(self) -> None:
        """VM cron must validate bundle status before execution can continue."""
        cron_precompute = Path("scripts/cron_precompute.sh").read_text(encoding="utf-8")
        cron_execute = Path("scripts/cron_execute.sh").read_text(encoding="utf-8")

        assert "precompute_bundle_validation.json" in cron_precompute
        assert "execution_bundle_validation.json" in cron_execute
        assert "core.precompute_bundle_validation" in cron_execute
        assert "execution halted to avoid degraded bundle execution" in cron_execute


# ---------------------------------------------------------------------------
# ISSUE 2: capital budget clips all buys → retry eligible
# ---------------------------------------------------------------------------


class TestCapitalConstrainedRetry:
    def test_capital_constrained_no_trades_is_retryable_reason(self) -> None:
        assert retry_reason_compatible("capital_constrained_no_trades")

    def test_retry_eligible_when_capital_constrained_zero_submitted(self) -> None:
        result = evaluate_live_retry(
            submitted_count=0,
            retry_attempt_count=0,
            reason="capital_constrained_no_trades",
            now=dt.datetime(2026, 3, 20, 9, 40, tzinfo=ET),
        )
        assert result["retry_allowed"] is True
        assert result["retry_reason"] == "capital_constrained_no_trades"

    def test_retry_not_allowed_on_second_attempt(self) -> None:
        result = evaluate_live_retry(
            submitted_count=0,
            retry_attempt_count=1,
            reason="capital_constrained_no_trades",
            now=dt.datetime(2026, 3, 20, 9, 40, tzinfo=ET),
        )
        assert result["retry_allowed"] is False


# ---------------------------------------------------------------------------
# ISSUE 3: PDT constrained field
# ---------------------------------------------------------------------------


class TestPdtConstrained:
    def test_pdt_constrained_set_when_high_count_zero_bp(self) -> None:
        from brokers.alpaca_snapshot import summarize_broker_pdt_risk

        result = summarize_broker_pdt_risk({
            "account": {
                "raw": {
                    "pattern_day_trader": False,
                    "daytrade_count": 3,
                    "daytrading_buying_power": "0",
                },
            },
        })
        assert result["pdt_constrained"] is True
        assert result["broker_pdt_risk_status"] == "WARN"

    def test_pdt_not_constrained_when_count_low(self) -> None:
        from brokers.alpaca_snapshot import summarize_broker_pdt_risk

        result = summarize_broker_pdt_risk({
            "account": {
                "raw": {
                    "pattern_day_trader": False,
                    "daytrade_count": 1,
                    "daytrading_buying_power": "5000",
                },
            },
        })
        assert result["pdt_constrained"] is False

    def test_pdt_constrained_propagates_to_pretrade_policy(self) -> None:
        from brokers.alpaca_snapshot import summarize_pretrade_broker_policy

        policy = summarize_pretrade_broker_policy({
            "account": {
                "status": "ACTIVE",
                "cash": "5000",
                "equity": "25000",
                "buying_power": "5000",
                "raw": {
                    "pattern_day_trader": False,
                    "daytrade_count": 4,
                    "daytrading_buying_power": "0",
                },
            },
        })
        assert policy["pdt_constrained"] is True

    def test_email_subject_includes_pdt_warn(self) -> None:
        from paper.build_execution_email import build_execution_email_text

        subject, _ = build_execution_email_text({
            "trade_date": "2026-03-20",
            "mode": "ALPACA",
            "execution_status": "READY",
            "pdt_constrained": True,
        })
        assert "[PDT WARN]" in subject

    def test_email_subject_includes_capital_constrained(self) -> None:
        from paper.build_execution_email import build_execution_email_text

        subject, _ = build_execution_email_text({
            "trade_date": "2026-03-20",
            "mode": "ALPACA",
            "execution_status": "READY",
            "capital_constrained_no_trades": True,
        })
        assert "[CAPITAL CONSTRAINED]" in subject

    def test_email_subject_clean_when_no_constraints(self) -> None:
        from paper.build_execution_email import build_execution_email_text

        subject, _ = build_execution_email_text({
            "trade_date": "2026-03-20",
            "mode": "ALPACA",
            "execution_status": "READY",
        })
        assert "[PDT WARN]" not in subject
        assert "[CAPITAL CONSTRAINED]" not in subject

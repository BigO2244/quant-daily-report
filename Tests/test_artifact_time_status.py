from __future__ import annotations

import datetime as dt

from core.artifact_time_status import (
    BLOCKED_MISSING,
    BLOCKED_STALE,
    NOT_EXPECTED_NON_TRADING_DAY,
    NOT_EXPECTED_YET,
    PASS,
    PENDING_EOD_IMPORT,
    PENDING_PUBLICATION,
    classify_artifact_time_status,
)
from paper.trading_calendar import ET_TZ


def _now(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value).replace(tzinfo=ET_TZ)


def test_current_trading_day_before_eod_import_is_pending() -> None:
    status = classify_artifact_time_status(
        artifact_date="2026-06-30",
        now=_now("2026-06-30T17:00:00"),
        artifact_type="alpha_evidence_chain",
        required_fields_missing=["holdings_ranks"],
        freshness_status="PRICE_CACHE_STALE",
    )

    assert status.status == PENDING_EOD_IMPORT
    assert status.expected_yet is False
    assert status.missing_fields_actionable is False
    assert status.should_affect_evidence_clock is False


def test_current_trading_day_after_import_before_deadline_is_pending_publication() -> None:
    status = classify_artifact_time_status(
        artifact_date="2026-06-30",
        now=_now("2026-06-30T18:45:00"),
        artifact_type="alpha_evidence_chain",
        required_fields_missing=["holdings_ranks"],
        freshness_status="PRICE_CACHE_STALE",
    )

    assert status.status == PENDING_PUBLICATION
    assert status.expected_yet is False
    assert status.counts_as_evidence_failure is False


def test_current_trading_day_after_publication_deadline_blocks_missing_fields() -> None:
    status = classify_artifact_time_status(
        artifact_date="2026-06-30",
        now=_now("2026-06-30T19:15:00"),
        artifact_type="alpha_evidence_chain",
        required_fields_missing=["holdings_ranks"],
        freshness_status="PRICE_CACHE_STALE",
    )

    assert status.status == BLOCKED_MISSING
    assert status.expected_yet is True
    assert status.missing_fields_actionable is True
    assert status.counts_as_evidence_failure is True


def test_prior_trading_day_missing_fields_block() -> None:
    status = classify_artifact_time_status(
        artifact_date="2026-06-29",
        now=_now("2026-06-30T10:00:00"),
        artifact_type="alpha_evidence_chain",
        required_fields_missing=["holdings_ranks"],
    )

    assert status.status == BLOCKED_MISSING
    assert status.expected_yet is True


def test_prior_trading_day_stale_artifact_blocks_stale() -> None:
    status = classify_artifact_time_status(
        artifact_date="2026-06-29",
        now=_now("2026-06-30T10:00:00"),
        artifact_type="alpha_evidence_chain",
        required_fields_missing=[],
        latest_available_date="2026-06-26",
        freshness_status="OK",
    )

    assert status.status == BLOCKED_STALE
    assert status.missing_fields_actionable is True


def test_future_trading_day_is_not_expected_yet() -> None:
    status = classify_artifact_time_status(
        artifact_date="2026-07-01",
        now=_now("2026-06-30T10:00:00"),
        artifact_type="alpha_evidence_chain",
        required_fields_missing=["holdings_ranks"],
    )

    assert status.status == NOT_EXPECTED_YET
    assert status.missing_fields_actionable is False


def test_market_holiday_is_not_expected() -> None:
    status = classify_artifact_time_status(
        artifact_date="2026-07-03",
        now=_now("2026-07-03T10:00:00"),
        artifact_type="alpha_evidence_chain",
        required_fields_missing=["holdings_ranks"],
    )

    assert status.status == NOT_EXPECTED_NON_TRADING_DAY
    assert status.expected_yet is False


def test_complete_historical_day_passes() -> None:
    status = classify_artifact_time_status(
        artifact_date="2026-06-29",
        now=_now("2026-06-30T10:00:00"),
        artifact_type="alpha_evidence_chain",
        required_fields_missing=[],
        freshness_status="OK",
        latest_available_date="2026-06-29",
    )

    assert status.status == PASS
    assert status.should_affect_evidence_clock is True


def test_missing_required_fields_never_pass() -> None:
    before_due = classify_artifact_time_status(
        artifact_date="2026-06-30",
        now=_now("2026-06-30T17:00:00"),
        artifact_type="alpha_evidence_chain",
        required_fields_missing=["holdings_ranks"],
    )
    after_due = classify_artifact_time_status(
        artifact_date="2026-06-30",
        now=_now("2026-06-30T19:15:00"),
        artifact_type="alpha_evidence_chain",
        required_fields_missing=["holdings_ranks"],
    )

    assert before_due.status != PASS
    assert after_due.status != PASS

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from core.live_retry_policy import evaluate_live_retry
from core.precompute_contract import REASON_PRECOMPUTE_MISSING


ET = ZoneInfo("America/New_York")


def test_retry_allowed_once_before_deadline_with_no_submissions() -> None:
    decision = evaluate_live_retry(
        submitted_count=0,
        retry_attempt_count=0,
        reason=REASON_PRECOMPUTE_MISSING,
        now=dt.datetime(2026, 3, 17, 10, 5, tzinfo=ET),
    )
    assert decision["retry_allowed"] is True
    assert decision["retry_reason"] == REASON_PRECOMPUTE_MISSING


def test_retry_blocked_after_submission() -> None:
    decision = evaluate_live_retry(
        submitted_count=2,
        retry_attempt_count=0,
        reason="post_submit_artifact_failure",
        now=dt.datetime(2026, 3, 17, 10, 5, tzinfo=ET),
    )
    assert decision["retry_allowed"] is False
    assert decision["retry_reason"] == "submission_already_occurred"


def test_retry_blocked_after_deadline() -> None:
    decision = evaluate_live_retry(
        submitted_count=0,
        retry_attempt_count=0,
        reason=REASON_PRECOMPUTE_MISSING,
        now=dt.datetime(2026, 3, 17, 13, 1, tzinfo=ET),
    )
    assert decision["retry_allowed"] is False
    assert decision["retry_reason"] == "after_deadline"


def test_retry_blocked_after_one_attempt() -> None:
    decision = evaluate_live_retry(
        submitted_count=0,
        retry_attempt_count=1,
        reason=REASON_PRECOMPUTE_MISSING,
        now=dt.datetime(2026, 3, 17, 10, 5, tzinfo=ET),
    )
    assert decision["retry_allowed"] is False
    assert decision["retry_reason"] == "retry_already_used"

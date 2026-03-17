from __future__ import annotations

import datetime as dt
from typing import Any

from core.precompute_contract import (
    REASON_PRECOMPUTE_INCOMPLETE,
    REASON_PRECOMPUTE_INVALID,
    REASON_PRECOMPUTE_MISSING,
    REASON_PRECOMPUTE_VALIDATION_FAILED,
    REASON_PRECOMPUTE_WRONG_TRADE_DATE,
)
from core.timing_policy import auto_trade_allowed, current_et

RETRY_ELIGIBLE_REASONS = {
    REASON_PRECOMPUTE_MISSING,
    REASON_PRECOMPUTE_WRONG_TRADE_DATE,
    REASON_PRECOMPUTE_INCOMPLETE,
    REASON_PRECOMPUTE_INVALID,
    REASON_PRECOMPUTE_VALIDATION_FAILED,
    "post_submit_artifact_failure",
    "system_failure",
    "artifact_write_failed",
    "non_trading_infrastructure_failure",
    "execution_payload_write_failed",
}


def _normalize_reason(reason: Any) -> str:
    return str(reason or "").strip().lower()


def retry_reason_compatible(reason: Any) -> bool:
    normalized = _normalize_reason(reason)
    if not normalized:
        return False
    if normalized in RETRY_ELIGIBLE_REASONS:
        return True
    return normalized.startswith("precompute_") or normalized.endswith("_artifact_failure")


def evaluate_live_retry(
    *,
    submitted_count: int,
    retry_attempt_count: int,
    reason: Any,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    now_et = current_et(now)
    if retry_attempt_count >= 1:
        return {
            "retry_allowed": False,
            "retry_reason": "retry_already_used",
            "retry_attempt_count": int(retry_attempt_count),
            "evaluated_at_et": now_et.isoformat(),
        }
    if int(submitted_count or 0) > 0:
        return {
            "retry_allowed": False,
            "retry_reason": "submission_already_occurred",
            "retry_attempt_count": int(retry_attempt_count),
            "evaluated_at_et": now_et.isoformat(),
        }
    if not auto_trade_allowed(now_et):
        return {
            "retry_allowed": False,
            "retry_reason": "after_deadline",
            "retry_attempt_count": int(retry_attempt_count),
            "evaluated_at_et": now_et.isoformat(),
        }
    if not retry_reason_compatible(reason):
        return {
            "retry_allowed": False,
            "retry_reason": "reason_not_retryable",
            "retry_attempt_count": int(retry_attempt_count),
            "evaluated_at_et": now_et.isoformat(),
        }
    return {
        "retry_allowed": True,
        "retry_reason": _normalize_reason(reason),
        "retry_attempt_count": int(retry_attempt_count),
        "evaluated_at_et": now_et.isoformat(),
    }

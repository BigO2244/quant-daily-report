from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
PREFERRED_TARGET_HOUR = 9
PREFERRED_TARGET_MINUTE = 35
AUTO_TRADE_DEADLINE_HOUR = 13
AUTO_TRADE_DEADLINE_MINUTE = 0


def current_et(now: dt.datetime | None = None) -> dt.datetime:
    if now is None:
        return dt.datetime.now(ET)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(ET)


def preferred_target_for_day(now_et: dt.datetime) -> dt.datetime:
    return now_et.replace(
        hour=PREFERRED_TARGET_HOUR,
        minute=PREFERRED_TARGET_MINUTE,
        second=0,
        microsecond=0,
    )


def deadline_for_day(now_et: dt.datetime) -> dt.datetime:
    return now_et.replace(
        hour=AUTO_TRADE_DEADLINE_HOUR,
        minute=AUTO_TRADE_DEADLINE_MINUTE,
        second=0,
        microsecond=0,
    )


def auto_trade_allowed(now: dt.datetime | None = None) -> bool:
    now_et = current_et(now)
    return now_et <= deadline_for_day(now_et)


def classify_timing(
    *,
    now: dt.datetime | None = None,
    workflow_start_et: dt.datetime | None = None,
    execution_start_et: dt.datetime | None = None,
    first_submit_et: dt.datetime | None = None,
    retry_attempt: bool = False,
) -> dict[str, str | bool | None]:
    now_et = current_et(now)
    preferred = preferred_target_for_day(now_et)
    deadline = deadline_for_day(now_et)
    reference = first_submit_et or execution_start_et or workflow_start_et or now_et
    if reference.tzinfo is None:
        raise ValueError("reference times must be timezone-aware")
    reference = reference.astimezone(ET)

    if first_submit_et is not None:
        first_submit_et = first_submit_et.astimezone(ET)
        if first_submit_et <= preferred:
            status = "on_time"
        elif first_submit_et <= deadline:
            status = "degraded_late"
        else:
            status = "after_deadline"
    elif now_et > deadline:
        status = "after_deadline"
    elif retry_attempt:
        status = "retry_window"
    elif reference > preferred:
        status = "missed_preferred_window"
    else:
        status = "on_time"

    return {
        "preferred_target_et": preferred.isoformat(),
        "degraded_auto_trade_deadline_et": deadline.isoformat(),
        "actual_workflow_start_et": workflow_start_et.astimezone(ET).isoformat() if workflow_start_et else None,
        "actual_execution_start_et": execution_start_et.astimezone(ET).isoformat() if execution_start_et else None,
        "first_submit_et": first_submit_et.isoformat() if first_submit_et else None,
        "timing_status": status,
        "auto_trade_allowed": bool(now_et <= deadline),
        "retry_attempt": bool(retry_attempt),
    }

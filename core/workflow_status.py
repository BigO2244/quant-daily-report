from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")
PRECOMPUTE_STALE_THRESHOLD_HOUR = 5
PRECOMPUTE_STALE_THRESHOLD_MINUTE = 1
LIVE_WINDOW_START_HOUR = 9
LIVE_WINDOW_START_MINUTE = 35
LIVE_WINDOW_DEADLINE_HOUR = 13
LIVE_WINDOW_DEADLINE_MINUTE = 0


def current_et(now: dt.datetime | None = None) -> dt.datetime:
    if now is None:
        return dt.datetime.now(ET)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(ET)


def stale_precompute_threshold(now_et: dt.datetime) -> dt.datetime:
    return now_et.replace(
        hour=PRECOMPUTE_STALE_THRESHOLD_HOUR,
        minute=PRECOMPUTE_STALE_THRESHOLD_MINUTE,
        second=0,
        microsecond=0,
    )


def live_window_start(now_et: dt.datetime) -> dt.datetime:
    return now_et.replace(
        hour=LIVE_WINDOW_START_HOUR,
        minute=LIVE_WINDOW_START_MINUTE,
        second=0,
        microsecond=0,
    )


def live_window_deadline(now_et: dt.datetime) -> dt.datetime:
    return now_et.replace(
        hour=LIVE_WINDOW_DEADLINE_HOUR,
        minute=LIVE_WINDOW_DEADLINE_MINUTE,
        second=0,
        microsecond=0,
    )


def classify_precompute_window(
    *,
    now: dt.datetime | None = None,
    force_refresh: bool = False,
    event_name: str = "schedule",
) -> dict[str, Any]:
    now_et = current_et(now)
    start_et = now_et.replace(hour=5, minute=0, second=0, microsecond=0)
    deadline_et = stale_precompute_threshold(now_et)
    # All canonical starts are admitted in the 05:00 ET cron minute. A run
    # admitted then can finish later; neither dispatch nor force bypasses this.
    allow_run = now_et.weekday() < 5 and start_et <= now_et < deadline_et
    reason = "within_precompute_window" if allow_run else "outside_0500_precompute_start"
    return {
        "workflow_kind": "precompute",
        "intended_schedule_class": "precompute_0500_et",
        "now_et": now_et.isoformat(),
        "precompute_start_et": start_et.isoformat(),
        "stale_threshold_et": deadline_et.isoformat(),
        "event_freshness_status": "fresh_precompute" if allow_run else "stale_precompute",
        "execution_window_status": "precompute_window_open" if allow_run else "stale_precompute",
        "allow_run": allow_run,
        "reason": reason,
    }


def classify_live_window(
    *,
    now: dt.datetime | None = None,
    force_outside_window: bool = False,
    event_name: str = "schedule",
) -> dict[str, Any]:
    now_et = current_et(now)
    start_et = live_window_start(now_et)
    deadline_et = live_window_deadline(now_et)
    manual = str(event_name or "").strip() == "workflow_dispatch"
    if now_et < start_et and not force_outside_window:
        execution_window_status = "too_early_for_live"
        allow_run = False
        reason = "too_early_for_live"
    elif now_et > deadline_et and not force_outside_window:
        execution_window_status = "after_deadline"
        allow_run = False
        reason = "after_deadline"
    elif force_outside_window and (now_et < start_et or now_et > deadline_et):
        execution_window_status = "forced_live_outside_window"
        allow_run = True
        reason = "force_live_outside_window"
    elif now_et == start_et:
        execution_window_status = "on_time"
        allow_run = True
        reason = "within_live_window"
    else:
        execution_window_status = "degraded_late"
        allow_run = True
        reason = "within_live_window"
    return {
        "workflow_kind": "live",
        "intended_schedule_class": "live",
        "now_et": now_et.isoformat(),
        "live_window_start_et": start_et.isoformat(),
        "live_window_deadline_et": deadline_et.isoformat(),
        "event_freshness_status": "manual_live" if manual else "live_schedule_event",
        "execution_window_status": execution_window_status,
        "allow_run": allow_run,
        "reason": reason,
    }


def workflow_status_dir(report_date: str) -> Path:
    return Path("outputs/workflow_status") / str(report_date)


def resolve_precompute_final_status(
    *,
    guard_outcome: str,
    bundle_outcome: str,
    guard_allow_run: str | bool | None,
    prior_bundle_status: str | None,
) -> str:
    guard_ok = str(guard_outcome or "").strip().lower() == "success"
    bundle_ok = str(bundle_outcome or "").strip().lower() == "success"
    allow_run = str(guard_allow_run or "").strip().lower() == "true"
    prior = str(prior_bundle_status or "").strip().upper()

    if not guard_ok:
        # Guard step itself failed — but if a valid bundle already existed,
        # this is a benign stale-skip, not a hard failure.
        if prior == "VALID":
            return "SKIPPED_STALE_VALID"
        return "GUARD_FAILED"
    if not allow_run:
        # Guard ran successfully but disallowed the run (stale window).
        # If bundle is already valid, this is a safe skip.
        if prior == "VALID":
            return "SKIPPED_STALE_VALID"
        return "SKIPPED_AS_STALE"
    if not bundle_ok and not prior:
        return "BUNDLE_STATUS_UNKNOWN"
    if prior == "MISSING":
        return "CREATED"
    if prior in {"INVALID", "REFRESH_REQUESTED"}:
        return "REFRESHED"
    if prior == "VALID":
        return "REUSED"
    return "REUSED"

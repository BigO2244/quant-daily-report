from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from core.workflow_status import classify_live_window, classify_precompute_window


ET = ZoneInfo("America/New_York")


def test_delayed_precompute_event_is_marked_stale_after_threshold() -> None:
    status = classify_precompute_window(
        now=dt.datetime(2026, 3, 18, 9, 59, tzinfo=ET),
        force_refresh=False,
        event_name="schedule",
    )

    assert status["allow_run"] is False
    assert status["event_freshness_status"] == "stale_precompute"
    assert status["execution_window_status"] == "stale_precompute"


def test_manual_force_refresh_can_override_stale_precompute() -> None:
    status = classify_precompute_window(
        now=dt.datetime(2026, 3, 18, 9, 59, tzinfo=ET),
        force_refresh=True,
        event_name="workflow_dispatch",
    )

    assert status["allow_run"] is True
    assert status["event_freshness_status"] == "forced_stale_precompute"


def test_live_window_rejects_too_early_start() -> None:
    status = classify_live_window(
        now=dt.datetime(2026, 3, 18, 9, 20, tzinfo=ET),
        event_name="schedule",
    )

    assert status["allow_run"] is False
    assert status["execution_window_status"] == "too_early_for_live"


def test_live_window_allows_late_but_before_deadline() -> None:
    status = classify_live_window(
        now=dt.datetime(2026, 3, 18, 10, 5, tzinfo=ET),
        event_name="schedule",
    )

    assert status["allow_run"] is True
    assert status["execution_window_status"] == "degraded_late"


def test_live_window_rejects_after_deadline_without_force() -> None:
    status = classify_live_window(
        now=dt.datetime(2026, 3, 18, 13, 5, tzinfo=ET),
        event_name="schedule",
    )

    assert status["allow_run"] is False
    assert status["execution_window_status"] == "after_deadline"


def test_live_window_handles_dst_dates_in_et() -> None:
    est = classify_live_window(now=dt.datetime(2026, 1, 15, 9, 35, tzinfo=ET))
    edt = classify_live_window(now=dt.datetime(2026, 4, 15, 9, 35, tzinfo=ET))

    assert est["live_window_start_et"].endswith("-05:00")
    assert edt["live_window_start_et"].endswith("-04:00")

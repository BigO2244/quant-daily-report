from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Callable

from paper.trading_calendar import ET_TZ, is_trading_day


PASS = "PASS"
PENDING_EOD_IMPORT = "PENDING_EOD_IMPORT"
PENDING_PUBLICATION = "PENDING_PUBLICATION"
BLOCKED_MISSING = "BLOCKED_MISSING"
BLOCKED_STALE = "BLOCKED_STALE"
NOT_EXPECTED_YET = "NOT_EXPECTED_YET"
NOT_EXPECTED_NON_TRADING_DAY = "NOT_EXPECTED_NON_TRADING_DAY"


@dataclass(frozen=True)
class ArtifactPublicationSchedule:
    eod_import_time: dt.time
    expected_publish_time: dt.time


@dataclass(frozen=True)
class ArtifactTimeStatus:
    status: str
    explanation: str
    artifact_date: str | None
    current_date: str
    artifact_type: str
    publication_window_status: str
    expected_yet: bool
    missing_fields_actionable: bool
    should_affect_evidence_clock: bool
    counts_as_evidence_failure: bool
    expected_publish_time_et: str
    eod_import_time_et: str
    latest_available_date: str | None
    required_fields_missing: list[str]
    freshness_status: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "explanation": self.explanation,
            "artifact_date": self.artifact_date,
            "current_date": self.current_date,
            "artifact_type": self.artifact_type,
            "publication_window_status": self.publication_window_status,
            "expected_yet": self.expected_yet,
            "missing_fields_actionable": self.missing_fields_actionable,
            "should_affect_evidence_clock": self.should_affect_evidence_clock,
            "counts_as_evidence_failure": self.counts_as_evidence_failure,
            "expected_publish_time_et": self.expected_publish_time_et,
            "eod_import_time_et": self.eod_import_time_et,
            "latest_available_date": self.latest_available_date,
            "required_fields_missing": list(self.required_fields_missing),
            "freshness_status": self.freshness_status,
        }


DEFAULT_PUBLICATION_SCHEDULES = {
    "price_cache_refresh": ArtifactPublicationSchedule(dt.time(hour=18, minute=30), dt.time(hour=18, minute=45)),
    "shadow_candidate_publication": ArtifactPublicationSchedule(dt.time(hour=18, minute=30), dt.time(hour=19, minute=0)),
    "shadow_scorecard_refresh": ArtifactPublicationSchedule(dt.time(hour=18, minute=30), dt.time(hour=19, minute=0)),
    "alpha_evidence_chain": ArtifactPublicationSchedule(dt.time(hour=18, minute=30), dt.time(hour=19, minute=0)),
    "dashboard_data_refresh": ArtifactPublicationSchedule(dt.time(hour=18, minute=30), dt.time(hour=19, minute=10)),
    "default_eod_artifact": ArtifactPublicationSchedule(dt.time(hour=18, minute=30), dt.time(hour=19, minute=0)),
}

_STALE_FRESHNESS_TOKENS = {
    "STALE",
    "PRICE_CACHE_STALE",
    "BROKEN_CHAIN",
    "OUT_OF_DATE",
    "OLD",
}


def classify_artifact_time_status(
    *,
    artifact_date: str | dt.date | None,
    now: dt.datetime | None = None,
    artifact_type: str = "default_eod_artifact",
    expected_publish_time: str | dt.time | None = None,
    eod_import_time: str | dt.time | None = None,
    latest_available_date: str | dt.date | None = None,
    required_fields_missing: list[str] | tuple[str, ...] | set[str] | None = None,
    freshness_status: str | None = None,
    is_trading_day_fn: Callable[[str], bool] = is_trading_day,
) -> ArtifactTimeStatus:
    now_et = _coerce_now(now)
    current_day = now_et.date()
    schedule = _schedule_for(
        artifact_type=artifact_type,
        expected_publish_time=expected_publish_time,
        eod_import_time=eod_import_time,
    )
    missing = sorted(str(field) for field in (required_fields_missing or []) if str(field))
    artifact_day = _parse_date(artifact_date)
    latest_day = _parse_date(latest_available_date)
    freshness = str(freshness_status).strip().upper() if freshness_status else None

    if artifact_day is None:
        return _result(
            status=BLOCKED_MISSING,
            explanation="Artifact date is missing or invalid; expected-date semantics cannot be resolved.",
            artifact_day=None,
            now_et=now_et,
            artifact_type=artifact_type,
            schedule=schedule,
            publication_window_status="INVALID_ARTIFACT_DATE",
            expected_yet=True,
            missing_fields_actionable=True,
            latest_day=latest_day,
            missing=missing,
            freshness=freshness,
        )

    artifact_date_text = artifact_day.isoformat()
    if artifact_day > current_day:
        return _result(
            status=NOT_EXPECTED_YET,
            explanation=f"{artifact_date_text} is in the future relative to {current_day.isoformat()}.",
            artifact_day=artifact_day,
            now_et=now_et,
            artifact_type=artifact_type,
            schedule=schedule,
            publication_window_status="FUTURE_DATE",
            expected_yet=False,
            missing_fields_actionable=False,
            latest_day=latest_day,
            missing=missing,
            freshness=freshness,
        )

    if not is_trading_day_fn(artifact_date_text):
        return _result(
            status=NOT_EXPECTED_NON_TRADING_DAY,
            explanation=f"{artifact_date_text} is not an expected market trading session.",
            artifact_day=artifact_day,
            now_et=now_et,
            artifact_type=artifact_type,
            schedule=schedule,
            publication_window_status="NON_TRADING_DAY",
            expected_yet=False,
            missing_fields_actionable=False,
            latest_day=latest_day,
            missing=missing,
            freshness=freshness,
        )

    has_stale_data = _is_stale(
        artifact_day=artifact_day,
        latest_day=latest_day,
        freshness=freshness,
    )
    has_missing_or_stale = bool(missing) or has_stale_data
    if artifact_day == current_day and has_missing_or_stale:
        if now_et.timetz().replace(tzinfo=None) < schedule.eod_import_time:
            return _result(
                status=PENDING_EOD_IMPORT,
                explanation=(
                    f"Current trading date {artifact_date_text}; EOD import is not due until "
                    f"{_format_time(schedule.eod_import_time)} ET."
                ),
                artifact_day=artifact_day,
                now_et=now_et,
                artifact_type=artifact_type,
                schedule=schedule,
                publication_window_status="BEFORE_EOD_IMPORT",
                expected_yet=False,
                missing_fields_actionable=False,
                latest_day=latest_day,
                missing=missing,
                freshness=freshness,
            )
        if now_et.timetz().replace(tzinfo=None) < schedule.expected_publish_time:
            return _result(
                status=PENDING_PUBLICATION,
                explanation=(
                    f"Current trading date {artifact_date_text}; publication window is open and "
                    f"not due until {_format_time(schedule.expected_publish_time)} ET."
                ),
                artifact_day=artifact_day,
                now_et=now_et,
                artifact_type=artifact_type,
                schedule=schedule,
                publication_window_status="PENDING_PUBLICATION",
                expected_yet=False,
                missing_fields_actionable=False,
                latest_day=latest_day,
                missing=missing,
                freshness=freshness,
            )

    if missing:
        return _result(
            status=BLOCKED_MISSING,
            explanation="Required artifact evidence is missing after it is expected to be published.",
            artifact_day=artifact_day,
            now_et=now_et,
            artifact_type=artifact_type,
            schedule=schedule,
            publication_window_status="PUBLICATION_DUE",
            expected_yet=True,
            missing_fields_actionable=True,
            latest_day=latest_day,
            missing=missing,
            freshness=freshness,
        )

    if has_stale_data:
        return _result(
            status=BLOCKED_STALE,
            explanation="Artifact evidence is stale after it is expected to be published.",
            artifact_day=artifact_day,
            now_et=now_et,
            artifact_type=artifact_type,
            schedule=schedule,
            publication_window_status="PUBLICATION_DUE",
            expected_yet=True,
            missing_fields_actionable=True,
            latest_day=latest_day,
            missing=missing,
            freshness=freshness,
        )

    return _result(
        status=PASS,
        explanation="Required artifact evidence is present, fresh, and valid.",
        artifact_day=artifact_day,
        now_et=now_et,
        artifact_type=artifact_type,
        schedule=schedule,
        publication_window_status="PUBLICATION_COMPLETE",
        expected_yet=True,
        missing_fields_actionable=False,
        latest_day=latest_day,
        missing=missing,
        freshness=freshness,
    )


def _schedule_for(
    *,
    artifact_type: str,
    expected_publish_time: str | dt.time | None,
    eod_import_time: str | dt.time | None,
) -> ArtifactPublicationSchedule:
    base = DEFAULT_PUBLICATION_SCHEDULES.get(artifact_type) or DEFAULT_PUBLICATION_SCHEDULES["default_eod_artifact"]
    return ArtifactPublicationSchedule(
        eod_import_time=_parse_time(eod_import_time) if eod_import_time is not None else base.eod_import_time,
        expected_publish_time=_parse_time(expected_publish_time) if expected_publish_time is not None else base.expected_publish_time,
    )


def _coerce_now(now: dt.datetime | None) -> dt.datetime:
    if now is None:
        return dt.datetime.now(ET_TZ)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(ET_TZ)


def _parse_date(value: str | dt.date | None) -> dt.date | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _parse_time(value: str | dt.time) -> dt.time:
    if isinstance(value, dt.time):
        return value.replace(tzinfo=None)
    hour, minute, *rest = str(value).split(":")
    second = int(rest[0]) if rest else 0
    return dt.time(hour=int(hour), minute=int(minute), second=second)


def _format_time(value: dt.time) -> str:
    return value.strftime("%H:%M")


def _is_stale(*, artifact_day: dt.date, latest_day: dt.date | None, freshness: str | None) -> bool:
    if latest_day is not None and latest_day < artifact_day:
        return True
    if not freshness:
        return False
    return any(token in freshness for token in _STALE_FRESHNESS_TOKENS)


def _result(
    *,
    status: str,
    explanation: str,
    artifact_day: dt.date | None,
    now_et: dt.datetime,
    artifact_type: str,
    schedule: ArtifactPublicationSchedule,
    publication_window_status: str,
    expected_yet: bool,
    missing_fields_actionable: bool,
    latest_day: dt.date | None,
    missing: list[str],
    freshness: str | None,
) -> ArtifactTimeStatus:
    return ArtifactTimeStatus(
        status=status,
        explanation=explanation,
        artifact_date=artifact_day.isoformat() if artifact_day else None,
        current_date=now_et.date().isoformat(),
        artifact_type=artifact_type,
        publication_window_status=publication_window_status,
        expected_yet=expected_yet,
        missing_fields_actionable=missing_fields_actionable,
        should_affect_evidence_clock=status == PASS,
        counts_as_evidence_failure=status in {BLOCKED_MISSING, BLOCKED_STALE},
        expected_publish_time_et=_format_time(schedule.expected_publish_time),
        eod_import_time_et=_format_time(schedule.eod_import_time),
        latest_available_date=latest_day.isoformat() if latest_day else None,
        required_fields_missing=list(missing),
        freshness_status=freshness,
    )

"""Deployment-segmented lane and sleeve performance from lane valuations.

Returns are computed only from validated ``caerus.lane_valuation.v1``
artifacts.  Each deployment version starts a new, unchained segment at its
causal start.  Positive and negative external flows are removed from the
period numerator, while fees remain in NAV and therefore remain a return drag.

The legacy-unattributed bucket is included in factual lane NAV but never given
a sleeve return.  That prevents an account-level fact from being rewritten as
historical strategy attribution.
"""

from __future__ import annotations

import copy
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Any, Iterable, Mapping

from core.accounting_journal import LEGACY_UNATTRIBUTED, canonical_json
from core.lane_valuation import (
    LaneValuationError,
    validate_lane_valuation,
)


LANE_PERFORMANCE_SCHEMA = "caerus.lane_performance.v1"
PERFORMANCE_METHODOLOGY = "external_flow_adjusted_daily_return_net_of_fees_v1"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_ZERO = Decimal("0")
_TOLERANCE = Decimal("0.0000000001")

_PERFORMANCE_FIELDS = frozenset(
    {
        "schema_version",
        "performance_id",
        "account_id_hash",
        "lane_id",
        "lane_kind",
        "performance_surface",
        "economic_authority",
        "methodology",
        "factual",
        "execution_authority",
        "segments",
        "latest_as_of",
        "source_valuation_hashes",
        "content_hash",
    }
)
_SEGMENT_FIELDS = frozenset(
    {
        "deployment_version",
        "causal_start",
        "inception_date",
        "latest_as_of",
        "lane_series",
        "sleeve_series",
        "legacy_unattributed",
        "source_valuation_hashes",
        "segment_hash",
    }
)
_RETURN_ROW_FIELDS = frozenset(
    {
        "valuation_date",
        "as_of",
        "nav",
        "period_external_flow",
        "period_fee_amount",
        "daily_return",
        "cumulative_return",
        "valuation_hash",
    }
)
_SLEEVE_SERIES_FIELDS = frozenset({"sleeve_id", "causal_start", "rows"})
_LEGACY_FIELDS = frozenset(
    {"sleeve_id", "return_status", "reason", "latest_nav"}
)


class LanePerformanceError(ValueError):
    """Raised when performance cannot be proven from valuation artifacts."""


def _strict_fields(payload: Mapping[str, Any], expected: frozenset[str], *, label: str) -> None:
    missing = sorted(expected - set(payload))
    unknown = sorted(set(payload) - expected)
    if missing or unknown:
        raise LanePerformanceError(
            f"{label} fields mismatch; missing={missing}, unknown={unknown}"
        )


def _strict_string(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise LanePerformanceError(
            f"{label} must be a non-blank string without surrounding whitespace"
        )
    return value


def _safe_id(value: Any, *, label: str) -> str:
    result = _strict_string(value, label=label)
    if not _SAFE_ID.fullmatch(result) or ".." in result:
        raise LanePerformanceError(f"{label} is invalid")
    return result


def _sha256(value: Any, *, label: str) -> str:
    result = _strict_string(value, label=label)
    if not _SHA256.fullmatch(result):
        raise LanePerformanceError(f"{label} must be a lowercase SHA-256 digest")
    return result


def _decimal(value: Any, *, label: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise LanePerformanceError(f"{label} must be a finite number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise LanePerformanceError(f"{label} must be a finite number") from exc
    if not result.is_finite():
        raise LanePerformanceError(f"{label} must be a finite number")
    return result


def _number(value: Decimal) -> float:
    result = float(value)
    return 0.0 if result == 0.0 else result


def _timestamp(value: Any, *, label: str) -> dt.datetime:
    raw = _strict_string(value, label=label)
    try:
        result = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LanePerformanceError(f"{label} must be an ISO-8601 timestamp") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise LanePerformanceError(f"{label} must include a timezone")
    return result


def _date(value: Any, *, label: str) -> str:
    raw = _strict_string(value, label=label)
    try:
        dt.date.fromisoformat(raw)
    except ValueError as exc:
        raise LanePerformanceError(f"{label} must be an ISO date") from exc
    return raw


def lane_performance_hash(payload: Mapping[str, Any]) -> str:
    body = copy.deepcopy(dict(payload))
    body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def _segment_hash(payload: Mapping[str, Any]) -> str:
    body = copy.deepcopy(dict(payload))
    body.pop("segment_hash", None)
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def _return_rows(
    observations: list[tuple[Mapping[str, Any], Mapping[str, Any] | None]],
    *,
    nav_field: str,
    flow_field: str,
    fee_field: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    prior_nav: Decimal | None = None
    prior_flow: Decimal | None = None
    prior_fee: Decimal | None = None
    cumulative = Decimal("1")
    for valuation, sleeve in observations:
        source = sleeve if sleeve is not None else valuation
        nav = _decimal(source[nav_field], label="nav")
        cumulative_flow = _decimal(source[flow_field], label="cumulative_flow")
        cumulative_fee = _decimal(source[fee_field], label="cumulative_fee_amount")
        if cumulative_fee < _ZERO:
            raise LanePerformanceError("cumulative fees cannot be negative")
        if prior_nav is None:
            period_flow = _ZERO
            period_fee = _ZERO
            daily_return: Decimal | None = None
            cumulative_return = _ZERO
        else:
            if prior_nav <= _ZERO:
                raise LanePerformanceError("a positive prior NAV is required for returns")
            assert prior_flow is not None and prior_fee is not None
            period_flow = cumulative_flow - prior_flow
            period_fee = cumulative_fee - prior_fee
            if period_fee < -_TOLERANCE:
                raise LanePerformanceError("cumulative fees cannot decrease within a deployment")
            daily_return = (nav - period_flow) / prior_nav - Decimal("1")
            cumulative *= Decimal("1") + daily_return
            cumulative_return = cumulative - Decimal("1")
        rows.append(
            {
                "valuation_date": valuation["valuation_date"],
                "as_of": valuation["as_of"],
                "nav": _number(nav),
                "period_external_flow": _number(period_flow),
                "period_fee_amount": _number(period_fee),
                "daily_return": None if daily_return is None else _number(daily_return),
                "cumulative_return": _number(cumulative_return),
                "valuation_hash": valuation["content_hash"],
            }
        )
        prior_nav = nav
        prior_flow = cumulative_flow
        prior_fee = cumulative_fee
    return rows


def _build_segment(
    deployment_version: str,
    valuations: list[Mapping[str, Any]],
) -> dict[str, Any]:
    valuations = sorted(
        valuations, key=lambda row: _timestamp(row["as_of"], label="as_of")
    )
    causal_starts = {
        str(row["causal_start"])
        for row in valuations
        if row["causal_start"] is not None
    }
    if len(causal_starts) > 1:
        raise LanePerformanceError(
            f"deployment causal_start changed across valuations: {deployment_version}"
        )
    if not causal_starts:
        raise LanePerformanceError(
            f"deployment lacks a causal performance start: {deployment_version}"
        )
    causal_start = next(iter(causal_starts))
    causal_time = _timestamp(causal_start, label="causal_start")
    eligible = [
        row
        for row in valuations
        if _timestamp(row["as_of"], label="as_of") >= causal_time
    ]
    if not eligible:
        raise LanePerformanceError(
            f"deployment has no valuation at or after causal start: {deployment_version}"
        )
    dates = [row["valuation_date"] for row in eligible]
    if dates != sorted(set(dates)):
        raise LanePerformanceError(
            f"deployment must have at most one valuation per date: {deployment_version}"
        )
    lane_series = _return_rows(
        [(valuation, None) for valuation in eligible],
        nav_field="lane_nav",
        flow_field="cumulative_external_flow",
        fee_field="cumulative_fee_amount",
    )

    sleeve_ids = sorted(
        {
            sleeve["sleeve_id"]
            for valuation in eligible
            for sleeve in valuation["sleeves"]
        }
    )
    sleeve_series: list[dict[str, Any]] = []
    for sleeve_id in sleeve_ids:
        observations: list[tuple[Mapping[str, Any], Mapping[str, Any] | None]] = []
        starts: set[str] = set()
        for valuation in eligible:
            sleeve = next(
                (row for row in valuation["sleeves"] if row["sleeve_id"] == sleeve_id),
                None,
            )
            if sleeve is None or sleeve["causal_start"] is None:
                continue
            starts.add(str(sleeve["causal_start"]))
            if _timestamp(valuation["as_of"], label="as_of") >= _timestamp(
                sleeve["causal_start"], label="sleeve.causal_start"
            ):
                observations.append((valuation, sleeve))
        if not observations:
            continue
        if len(starts) != 1:
            raise LanePerformanceError(
                f"sleeve causal_start changed within deployment: {sleeve_id}"
            )
        sleeve_series.append(
            {
                "sleeve_id": sleeve_id,
                "causal_start": next(iter(starts)),
                "rows": _return_rows(
                    observations,
                    nav_field="nav",
                    flow_field="cumulative_flow",
                    fee_field="cumulative_fee_amount",
                ),
            }
        )

    latest_legacy = eligible[-1]["legacy_unattributed"]
    legacy = {
        "sleeve_id": LEGACY_UNATTRIBUTED,
        "return_status": "UNAVAILABLE",
        "reason": "historical_attribution_not_proven",
        "latest_nav": latest_legacy["nav"],
    }
    source_hashes = [row["content_hash"] for row in eligible]
    segment = {
        "deployment_version": deployment_version,
        "causal_start": causal_start,
        "inception_date": eligible[0]["valuation_date"],
        "latest_as_of": eligible[-1]["as_of"],
        "lane_series": lane_series,
        "sleeve_series": sleeve_series,
        "legacy_unattributed": legacy,
        "source_valuation_hashes": source_hashes,
    }
    segment["segment_hash"] = _segment_hash(segment)
    return segment


def build_lane_performance(
    valuations: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build deployment-segmented lane and sleeve daily performance."""

    validated: list[dict[str, Any]] = []
    for raw in valuations:
        try:
            validated.append(validate_lane_valuation(raw))
        except LaneValuationError as exc:
            raise LanePerformanceError(f"lane valuation is invalid: {exc}") from exc
    if not validated:
        raise LanePerformanceError("performance requires at least one lane valuation")
    scope_fields = (
        "account_id_hash",
        "lane_id",
        "lane_kind",
        "performance_surface",
        "economic_authority",
    )
    scope = tuple(validated[0][field] for field in scope_fields)
    if any(tuple(row[field] for field in scope_fields) != scope for row in validated):
        raise LanePerformanceError(
            "performance cannot mix account, lane, kind, surface, or authority"
        )
    hashes = [row["content_hash"] for row in validated]
    if len(hashes) != len(set(hashes)):
        raise LanePerformanceError("performance valuations must be unique")
    by_deployment: dict[str, list[Mapping[str, Any]]] = {}
    for row in validated:
        by_deployment.setdefault(row["deployment_version"], []).append(row)
    factual = validated[0]["lane_kind"] in {"PAPER", "LIVE"}
    segments = [
        _build_segment(version, rows)
        for version, rows in sorted(by_deployment.items())
    ]
    segments.sort(
        key=lambda row: (
            _timestamp(row["causal_start"], label="causal_start"),
            row["deployment_version"],
        )
    )
    latest_as_of = max(
        (row["latest_as_of"] for row in segments),
        key=lambda value: _timestamp(value, label="latest_as_of"),
    )
    source_hashes = [
        valuation_hash
        for segment in segments
        for valuation_hash in segment["source_valuation_hashes"]
    ]
    seed = hashlib.sha256(
        canonical_json(
            {
                "lane_id": scope[1],
                "latest_as_of": latest_as_of,
                "segments": [row["segment_hash"] for row in segments],
            }
        ).encode("utf-8")
    ).hexdigest()
    body = {
        "schema_version": LANE_PERFORMANCE_SCHEMA,
        "performance_id": f"lane-performance:{scope[1]}:{seed[:24]}",
        "account_id_hash": scope[0],
        "lane_id": scope[1],
        "lane_kind": scope[2],
        "performance_surface": scope[3],
        "economic_authority": scope[4],
        "methodology": PERFORMANCE_METHODOLOGY,
        "factual": factual,
        "execution_authority": False,
        "segments": segments,
        "latest_as_of": latest_as_of,
        "source_valuation_hashes": source_hashes,
    }
    body["content_hash"] = lane_performance_hash(body)
    return validate_lane_performance(body)


def _validate_return_rows(rows: Any, *, label: str) -> None:
    if not isinstance(rows, list) or not rows:
        raise LanePerformanceError(f"{label} must be a non-empty array")
    dates: list[str] = []
    prior_nav: Decimal | None = None
    cumulative = Decimal("1")
    for index, row in enumerate(rows):
        row_label = f"{label}[{index}]"
        if not isinstance(row, Mapping):
            raise LanePerformanceError(f"{row_label} must be an object")
        _strict_fields(row, _RETURN_ROW_FIELDS, label=row_label)
        dates.append(_date(row["valuation_date"], label=f"{row_label}.valuation_date"))
        _timestamp(row["as_of"], label=f"{row_label}.as_of")
        nav = _decimal(row["nav"], label=f"{row_label}.nav")
        flow = _decimal(row["period_external_flow"], label=f"{row_label}.period_external_flow")
        fee = _decimal(row["period_fee_amount"], label=f"{row_label}.period_fee_amount")
        if fee < -_TOLERANCE:
            raise LanePerformanceError(f"{row_label}.period_fee_amount cannot be negative")
        _sha256(row["valuation_hash"], label=f"{row_label}.valuation_hash")
        if index == 0:
            if row["daily_return"] is not None or abs(flow) > _TOLERANCE or abs(fee) > _TOLERANCE:
                raise LanePerformanceError(f"{row_label} inception row is invalid")
            if abs(_decimal(row["cumulative_return"], label="cumulative_return")) > _TOLERANCE:
                raise LanePerformanceError(f"{row_label} inception cumulative return must be zero")
        else:
            assert prior_nav is not None
            if prior_nav <= _ZERO:
                raise LanePerformanceError(f"{row_label} prior NAV must be positive")
            daily_return = _decimal(row["daily_return"], label=f"{row_label}.daily_return")
            # Valuation artifacts are the source for cumulative flows.  The
            # row preserves the resulting period flow, so return identity is
            # checked directly from consecutive NAV observations.
            expected_return = (nav - flow) / prior_nav - Decimal("1")
            if abs(daily_return - expected_return) > _TOLERANCE:
                raise LanePerformanceError(f"{row_label}.daily_return mismatch")
            cumulative *= Decimal("1") + daily_return
            observed_cumulative = _decimal(
                row["cumulative_return"], label=f"{row_label}.cumulative_return"
            )
            if abs(observed_cumulative - (cumulative - Decimal("1"))) > _TOLERANCE:
                raise LanePerformanceError(f"{row_label}.cumulative_return mismatch")
        prior_nav = nav
    if dates != sorted(set(dates)):
        raise LanePerformanceError(f"{label} dates must be unique and increasing")


def validate_lane_performance(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Strictly validate a serialized lane-performance artifact."""

    if not isinstance(payload, Mapping):
        raise LanePerformanceError("lane performance must be an object")
    _strict_fields(payload, _PERFORMANCE_FIELDS, label="lane performance")
    if payload["schema_version"] != LANE_PERFORMANCE_SCHEMA:
        raise LanePerformanceError("unsupported lane performance schema")
    _safe_id(payload["performance_id"], label="performance_id")
    _sha256(payload["account_id_hash"], label="account_id_hash")
    _safe_id(payload["lane_id"], label="lane_id")
    lane_kind = _strict_string(payload["lane_kind"], label="lane_kind")
    expected = {
        "PAPER": ("FACTUAL_PAPER", "BROKER_RECONCILED", True),
        "LIVE": ("FACTUAL_LIVE", "BROKER_RECONCILED", True),
        "SHADOW": ("MODELED_SHADOW_NAV", "THEORETICAL_MODEL", False),
    }.get(lane_kind)
    if expected is None or (
        payload["performance_surface"],
        payload["economic_authority"],
        payload["factual"],
    ) != expected:
        raise LanePerformanceError("performance surface/authority/factual flag mismatch")
    if payload["methodology"] != PERFORMANCE_METHODOLOGY:
        raise LanePerformanceError("unsupported performance methodology")
    if payload["execution_authority"] is not False:
        raise LanePerformanceError("performance cannot carry execution authority")
    latest_as_of = _timestamp(payload["latest_as_of"], label="latest_as_of")
    segments = payload["segments"]
    if not isinstance(segments, list) or not segments:
        raise LanePerformanceError("segments must be a non-empty array")
    versions: set[str] = set()
    all_source_hashes: list[str] = []
    segment_order: list[tuple[dt.datetime, str]] = []
    observed_latest: list[dt.datetime] = []
    for index, segment in enumerate(segments):
        label = f"segments[{index}]"
        if not isinstance(segment, Mapping):
            raise LanePerformanceError(f"{label} must be an object")
        _strict_fields(segment, _SEGMENT_FIELDS, label=label)
        version = _safe_id(segment["deployment_version"], label=f"{label}.deployment_version")
        if version in versions:
            raise LanePerformanceError("deployment versions must be unique")
        versions.add(version)
        causal_start = _strict_string(segment["causal_start"], label=f"{label}.causal_start")
        _timestamp(causal_start, label=f"{label}.causal_start")
        inception_date = _date(segment["inception_date"], label=f"{label}.inception_date")
        segment_order.append(
            (_timestamp(causal_start, label=f"{label}.causal_start"), version)
        )
        observed_latest.append(_timestamp(segment["latest_as_of"], label=f"{label}.latest_as_of"))
        _validate_return_rows(segment["lane_series"], label=f"{label}.lane_series")
        if segment["lane_series"][0]["valuation_date"] != inception_date:
            raise LanePerformanceError(f"{label}.inception_date mismatch")
        sleeve_series = segment["sleeve_series"]
        if not isinstance(sleeve_series, list):
            raise LanePerformanceError(f"{label}.sleeve_series must be an array")
        sleeve_ids: list[str] = []
        for sleeve_index, sleeve in enumerate(sleeve_series):
            sleeve_label = f"{label}.sleeve_series[{sleeve_index}]"
            if not isinstance(sleeve, Mapping):
                raise LanePerformanceError(f"{sleeve_label} must be an object")
            _strict_fields(sleeve, _SLEEVE_SERIES_FIELDS, label=sleeve_label)
            sleeve_id = _safe_id(sleeve["sleeve_id"], label=f"{sleeve_label}.sleeve_id")
            if sleeve_id == LEGACY_UNATTRIBUTED:
                raise LanePerformanceError("legacy cannot have a sleeve return series")
            sleeve_ids.append(sleeve_id)
            _timestamp(sleeve["causal_start"], label=f"{sleeve_label}.causal_start")
            _validate_return_rows(sleeve["rows"], label=f"{sleeve_label}.rows")
        if sleeve_ids != sorted(set(sleeve_ids)):
            raise LanePerformanceError(f"{label}.sleeve_series must be sorted and unique")
        legacy = segment["legacy_unattributed"]
        if not isinstance(legacy, Mapping):
            raise LanePerformanceError(f"{label}.legacy_unattributed must be an object")
        _strict_fields(legacy, _LEGACY_FIELDS, label=f"{label}.legacy_unattributed")
        if (
            legacy["sleeve_id"] != LEGACY_UNATTRIBUTED
            or legacy["return_status"] != "UNAVAILABLE"
            or legacy["reason"] != "historical_attribution_not_proven"
        ):
            raise LanePerformanceError("legacy performance status is invalid")
        _decimal(legacy["latest_nav"], label="legacy.latest_nav")
        source_hashes = segment["source_valuation_hashes"]
        if not isinstance(source_hashes, list) or not source_hashes:
            raise LanePerformanceError(f"{label}.source_valuation_hashes must be non-empty")
        for source_hash in source_hashes:
            _sha256(source_hash, label="source_valuation_hash")
        lane_hashes = [row["valuation_hash"] for row in segment["lane_series"]]
        if source_hashes != lane_hashes:
            raise LanePerformanceError(f"{label}.source valuation lineage mismatch")
        all_source_hashes.extend(source_hashes)
        declared_segment_hash = _sha256(segment["segment_hash"], label="segment_hash")
        if declared_segment_hash != _segment_hash(segment):
            raise LanePerformanceError(f"{label}.segment_hash mismatch")
    if segment_order != sorted(segment_order):
        raise LanePerformanceError("segments must be ordered by causal start and version")
    if latest_as_of != max(observed_latest):
        raise LanePerformanceError("latest_as_of does not match segment observations")
    if payload["source_valuation_hashes"] != all_source_hashes:
        raise LanePerformanceError("top-level source valuation lineage mismatch")
    declared_hash = _sha256(payload["content_hash"], label="content_hash")
    if declared_hash != lane_performance_hash(payload):
        raise LanePerformanceError("lane performance content_hash mismatch")
    return json.loads(canonical_json(payload))


__all__ = [
    "LANE_PERFORMANCE_SCHEMA",
    "PERFORMANCE_METHODOLOGY",
    "LanePerformanceError",
    "build_lane_performance",
    "lane_performance_hash",
    "validate_lane_performance",
]

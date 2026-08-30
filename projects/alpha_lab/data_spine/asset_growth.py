"""Materialize HYP-2026-012 filing-time asset-growth features without returns.

The builder consumes the compact SEC Company Facts asset and the effective-
dated PIT security master.  It emits only observations available before the
sealed 2025-2026 challenge period.  Annual filings update balance-sheet facts
prospectively by accession; later corrections never rewrite an earlier signal.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from projects.alpha_lab.data_spine.materialize import (
    _next_session_open,
)
from projects.alpha_lab.data_spine.storage import sha256_file
from projects.alpha_lab.experiments.catalog import PIT_ASSET_GROWTH_FEATURES, PIT_MEMBERSHIP
from projects.alpha_lab.factory import canonical_hash, canonical_json


ANNUAL_FORMS = (
    "10-K",
    "10-K/A",
    "10-KT",
    "10-KT/A",
    "20-F",
    "20-F/A",
    "40-F",
    "40-F/A",
)
OUTPUT_FIELDS = (
    "security_id",
    "fiscal_period_end",
    "available_at",
    "asset_growth_1y",
    "asset_growth_2y",
    "source_accessions",
)
DEFAULT_SOURCE = "outputs/research/alpha_lab/shared/sec_companyfacts_compact.csv.gz"
DEFAULT_SOURCE_MANIFEST = "outputs/research/alpha_lab/shared/sec_companyfacts_compact_manifest.json"
DEFAULT_SECURITY_MASTER = "data/pit_universe/security_master.csv"
DEFAULT_SECURITY_CERTIFICATION = "outputs/research/alpha_lab/provider_readiness/pit_security_master_v1.json"
DEFAULT_MEMBERSHIP = "data/pit_universe/membership_universe.csv"
DEFAULT_MEMBERSHIP_CERTIFICATION = "outputs/research/alpha_lab/provider_readiness/pit_membership_v1.json"
DEFAULT_OUTPUT = "outputs/research/alpha_lab/shared/pit_asset_growth_features.parquet"
SEALED_CHALLENGE_START = "2025-01-01T00:00:00+00:00"
DEFAULT_MIN_AVAILABLE = "2012-01-01T00:00:00+00:00"
MIN_MAPPING_COVERAGE = 0.95
MIN_UNIQUE_SECURITIES = 500
MIN_SECURITIES_PER_YEAR = 100
MIN_MEMBERSHIP_COVERAGE = 0.80
COVERAGE_YEARS = tuple(str(year) for year in range(2012, 2025))


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _normalize_cik(value: Any) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits.zfill(10) if digits else ""


def _load_annual_assets(path: Path) -> pd.DataFrame:
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover - production dependency guard
        raise RuntimeError("duckdb is required to scan the compact Company Facts asset") from exc

    placeholders = ",".join("?" for _ in ANNUAL_FORMS)
    query = f"""
        SELECT
            cik,
            source_fact,
            unit,
            value,
            "end" AS fiscal_period_end,
            filed,
            available_at,
            accession_number,
            upper(form) AS form,
            upper(fiscal_period) AS fiscal_period,
            frame
        FROM read_csv_auto(?, all_varchar=true)
        WHERE lower(logical_fact) = ?
          AND upper(unit) = ?
          AND upper(form) IN ({placeholders})
          AND upper(fiscal_period) = 'FY'
          AND available_at < ?
          AND "end" < ?
    """
    cutoff_date = str(pd.Timestamp(SEALED_CHALLENGE_START).date())
    parameters: list[Any] = [
        path.as_posix(),
        "assets",
        "USD",
        *ANNUAL_FORMS,
        SEALED_CHALLENGE_START,
        cutoff_date,
    ]
    connection = duckdb.connect()
    try:
        return connection.execute(query, parameters).fetchdf()
    finally:
        connection.close()


def _load_security_master(path: Path) -> pd.DataFrame:
    required = {"security_id", "cik", "effective_start", "effective_end"}
    frame = pd.read_csv(path, dtype=str).fillna("")
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"security master missing required fields: {missing}")
    return frame


def _load_membership(path: Path) -> pd.DataFrame:
    required = {
        "security_id",
        "membership_start_date",
        "membership_end_date",
        "membership_family",
    }
    frame = pd.read_csv(path, dtype=str).fillna("")
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"PIT membership missing required fields: {missing}")
    return frame


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError(f"required authority artifact is unreadable: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"required authority artifact is not a JSON object: {path}")
    return payload


def verify_upstream_authority(
    *,
    repo_root: Path,
    source_path: Path,
    source_manifest_path: Path,
    security_master_path: Path,
    security_certification_path: Path,
    membership_path: Path,
    membership_certification_path: Path,
) -> list[dict[str, Any]]:
    source_record = _source_record(repo_root, source_path)
    manifest_record = _source_record(repo_root, source_manifest_path)
    master_record = _source_record(repo_root, security_master_path)
    certification_record = _source_record(repo_root, security_certification_path)
    membership_record = _source_record(repo_root, membership_path)
    membership_certification_record = _source_record(repo_root, membership_certification_path)
    manifest = _read_json_object(source_manifest_path)
    expected_source = source_record["path"]
    if manifest.get("schema_version") != "caerus_alpha_lab_sec_facts_compact_v1":
        raise ValueError("compact Company Facts manifest schema is not authoritative")
    if manifest.get("output") != expected_source:
        raise ValueError("compact Company Facts manifest points to a different output")
    if manifest.get("availability_rule") != "next_session_open_after_SEC_filed_date":
        raise ValueError("compact Company Facts availability rule is not causal")
    if "assets" not in set(manifest.get("selected_facts") or []):
        raise ValueError("compact Company Facts manifest does not declare the assets fact")
    if manifest.get("output_bytes") != source_record["bytes"]:
        raise ValueError("compact Company Facts byte count does not match its manifest")
    if manifest.get("output_sha256") != source_record["sha256"]:
        raise ValueError("compact Company Facts hash does not match its manifest")

    certification = _read_json_object(security_certification_path)
    unsigned = dict(certification)
    declared_hash = unsigned.pop("evidence_hash", None)
    if declared_hash != canonical_hash(unsigned):
        raise ValueError("security-master certification evidence hash is invalid")
    if (
        certification.get("provider_id") != "caerus.fr068"
        or certification.get("dataset_id") != "effective_dated_security_identity"
        or certification.get("status") != "READY"
        or certification.get("historical_point_in_time_verified") is not True
        or certification.get("schema_validation_status") != "PASS"
        or certification.get("blockers") not in ([], ())
    ):
        raise ValueError("security-master readiness is not PIT READY")
    if certification.get("data_files") != [master_record]:
        raise ValueError("security-master certification is not bound to the current file")
    membership_certification = _read_json_object(membership_certification_path)
    unsigned_membership = dict(membership_certification)
    declared_membership_hash = unsigned_membership.pop("evidence_hash", None)
    if declared_membership_hash != canonical_hash(unsigned_membership):
        raise ValueError("membership certification evidence hash is invalid")
    if (
        membership_certification.get("provider_id") != PIT_MEMBERSHIP.provider_id
        or membership_certification.get("dataset_id") != PIT_MEMBERSHIP.dataset_id
        or membership_certification.get("status") != "READY"
        or membership_certification.get("historical_point_in_time_verified") is not True
        or membership_certification.get("schema_validation_status") != "PASS"
        or membership_certification.get("blockers") not in ([], ())
    ):
        raise ValueError("membership readiness is not PIT READY")
    if membership_certification.get("data_files") != [membership_record]:
        raise ValueError("membership certification is not bound to the current file")
    return [
        source_record,
        manifest_record,
        master_record,
        certification_record,
        membership_record,
        membership_certification_record,
    ]


@lru_cache(maxsize=None)
def _floor_available_at(fiscal_period_end: date) -> pd.Timestamp:
    floor_day = fiscal_period_end + timedelta(days=90)
    # _next_session_open always advances at least one day. Passing the prior
    # day gives the regular-session open on or after the 90-day floor.
    return pd.Timestamp(_next_session_open(floor_day - timedelta(days=1)))


def _normalize_assets(frame: pd.DataFrame) -> pd.DataFrame:
    required = {
        "cik",
        "source_fact",
        "unit",
        "value",
        "fiscal_period_end",
        "filed",
        "available_at",
        "accession_number",
        "form",
        "fiscal_period",
        "frame",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"annual asset rows missing fields: {missing}")
    out = frame.copy()
    out["cik"] = out["cik"].map(_normalize_cik)
    out["value"] = pd.to_numeric(out["value"], errors="coerce")
    out["fiscal_period_end"] = pd.to_datetime(out["fiscal_period_end"], errors="coerce").dt.normalize()
    out["available_at"] = pd.to_datetime(out["available_at"], errors="coerce", utc=True)
    out["filed"] = pd.to_datetime(out["filed"], errors="coerce").dt.normalize()
    out["accession_number"] = out["accession_number"].fillna("").astype(str).str.strip()
    out["unit"] = out["unit"].fillna("").astype(str).str.upper()
    out["form"] = out["form"].fillna("").astype(str).str.upper()
    out["fiscal_period"] = out["fiscal_period"].fillna("").astype(str).str.upper()
    invalid_lineage = out[
        out["cik"].eq("")
        | out["accession_number"].eq("")
        | out["fiscal_period_end"].isna()
        | out["available_at"].isna()
        | out["filed"].isna()
        | out["unit"].ne("USD")
        | ~out["form"].isin(ANNUAL_FORMS)
        | out["fiscal_period"].ne("FY")
    ]
    if not invalid_lineage.empty:
        raise ValueError(f"annual asset input contains {len(invalid_lineage)} invalid lineage row(s)")
    impossible_periods = out[out["fiscal_period_end"].gt(out["filed"])]
    impossible_period_count = int(len(impossible_periods))
    # These source contexts cannot be made causal: the filing purports to
    # report a fiscal period that had not ended.  Exclude them deterministically
    # and disclose the count; malformed lineage still fails the entire build.
    out = out[~out.index.isin(impossible_periods.index)].copy()
    expected_by_filed = {
        filed: pd.Timestamp(_next_session_open(filed.date()))
        for filed in out["filed"].drop_duplicates()
    }
    expected_available = out["filed"].map(expected_by_filed)
    causally_early = out[out["available_at"].lt(expected_available)]
    causally_early_count = int(len(causally_early))
    early_mask = out["available_at"].lt(expected_available)
    out.loc[early_mask, "available_at"] = expected_available[early_mask]
    finite_values = out["value"].map(
        lambda value: pd.notna(value) and math.isfinite(float(value))
    )
    finite_conflicts = (
        out[finite_values]
        .groupby(["cik", "accession_number", "fiscal_period_end"], dropna=False)["value"]
        .nunique()
    )
    conflict_count = int((finite_conflicts > 1).sum())
    if conflict_count:
        raise ValueError(
            f"conflicting annual asset values in {conflict_count} accession-period group(s)"
        )
    invalid_value = out["value"].isna() | ~out["value"].map(
        lambda value: math.isfinite(float(value)) and float(value) > 0.0
    )
    invalid_value_count = int(invalid_value.sum())
    out = out[~invalid_value].copy()
    out = out.sort_values(
        ["available_at", "cik", "accession_number", "fiscal_period_end"]
    ).reset_index(drop=True)
    out.attrs["invalid_value_rows_excluded"] = invalid_value_count
    out.attrs["impossible_period_rows_excluded"] = impossible_period_count
    out.attrs["causally_early_rows_adjusted"] = causally_early_count
    return out


def _normalize_master(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"security_id", "cik", "effective_start", "effective_end"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"security master missing fields: {missing}")
    out = frame.copy()
    out["security_id"] = out["security_id"].fillna("").astype(str).str.strip()
    out["cik"] = out["cik"].map(_normalize_cik)
    missing_cik_rows_excluded = int(out["cik"].eq("").sum())
    out = out[out["cik"].ne("")].copy()
    raw_start = out["effective_start"].fillna("").astype(str).str.strip()
    raw_end = out["effective_end"].fillna("").astype(str).str.strip()
    out["effective_start"] = pd.to_datetime(raw_start, errors="coerce").dt.normalize()
    out["effective_end"] = pd.to_datetime(raw_end.where(raw_end.ne("")), errors="coerce").dt.normalize()
    invalid = out[
        out["security_id"].eq("")
        | raw_start.eq("")
        | out["effective_start"].isna()
        | (raw_end.ne("") & out["effective_end"].isna())
    ]
    if not invalid.empty:
        raise ValueError(f"security master contains {len(invalid)} invalid identity row(s)")
    reversed_intervals = out[out["effective_end"].notna() & out["effective_end"].lt(out["effective_start"])]
    if not reversed_intervals.empty:
        raise ValueError(f"security master contains {len(reversed_intervals)} reversed interval(s)")
    out = out.drop_duplicates(
        ["security_id", "cik", "effective_start", "effective_end"], keep="last"
    ).sort_values(["security_id", "effective_start", "cik"]).reset_index(drop=True)
    records_by_security: dict[
        str, list[tuple[str, pd.Timestamp, pd.Timestamp | None]]
    ] = {}
    for security_id, cik, effective_start, effective_end in out[
        ["security_id", "cik", "effective_start", "effective_end"]
    ].itertuples(index=False, name=None):
        records_by_security.setdefault(str(security_id), []).append(
            (
                str(cik),
                pd.Timestamp(effective_start),
                pd.Timestamp(effective_end) if pd.notna(effective_end) else None,
            )
        )
    for security_id, records in records_by_security.items():
        for index, left in enumerate(records):
            left_cik, left_start, left_raw_end = left
            left_end = left_raw_end or pd.Timestamp.max.normalize()
            for right in records[index + 1 :]:
                right_cik, right_start, right_raw_end = right
                if right_start > left_end:
                    break
                right_end = right_raw_end or pd.Timestamp.max.normalize()
                overlap = max(left_start, right_start) <= min(left_end, right_end)
                if overlap and left_cik != right_cik:
                    raise ValueError(
                        f"security master maps {security_id} to conflicting CIKs over an overlapping interval"
                    )
    out = out.sort_values(["cik", "effective_start", "security_id"]).reset_index(drop=True)
    out.attrs["missing_cik_rows_excluded"] = missing_cik_rows_excluded
    return out


def _normalize_membership(frame: pd.DataFrame) -> pd.DataFrame:
    required = {
        "security_id",
        "membership_start_date",
        "membership_end_date",
        "membership_family",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"PIT membership missing required fields: {missing}")
    out = frame.copy()
    out["security_id"] = out["security_id"].fillna("").astype(str).str.strip()
    out["membership_family"] = out["membership_family"].fillna("").astype(str).str.strip()
    raw_start = out["membership_start_date"].fillna("").astype(str).str.strip()
    raw_end = out["membership_end_date"].fillna("").astype(str).str.strip()
    out["membership_start_date"] = pd.to_datetime(raw_start, errors="coerce").dt.normalize()
    out["membership_end_date"] = pd.to_datetime(
        raw_end.where(raw_end.ne("")), errors="coerce"
    ).dt.normalize()
    invalid = out[
        out["security_id"].eq("")
        | out["membership_family"].eq("")
        | raw_start.eq("")
        | out["membership_start_date"].isna()
        | (raw_end.ne("") & out["membership_end_date"].isna())
    ]
    if not invalid.empty:
        raise ValueError(f"PIT membership contains {len(invalid)} invalid row(s)")
    reversed_intervals = out[
        out["membership_end_date"].notna()
        & out["membership_end_date"].lt(out["membership_start_date"])
    ]
    if not reversed_intervals.empty:
        raise ValueError(f"PIT membership contains {len(reversed_intervals)} reversed interval(s)")
    return out.drop_duplicates(
        ["security_id", "membership_start_date", "membership_end_date", "membership_family"]
    ).reset_index(drop=True)


def _prior_period(
    state: Mapping[pd.Timestamp, tuple[float, str]], current: pd.Timestamp
) -> pd.Timestamp | None:
    candidates = [
        period
        for period in state
        if 300 <= int((current - period).days) <= 430
    ]
    return min(candidates, key=lambda period: (abs((current - period).days - 365), -period.value)) if candidates else None


def _active_security_ids(
    master_by_cik: Mapping[str, Sequence[tuple[str, pd.Timestamp, pd.Timestamp | None]]],
    cik: str,
    available_date: pd.Timestamp,
    membership_by_security: Mapping[
        str, Sequence[tuple[pd.Timestamp, pd.Timestamp | None]]
    ]
    | None = None,
) -> list[str]:
    security_ids = {
        security_id
        for security_id, start, end in master_by_cik.get(cik, ())
        if start <= available_date and (end is None or end >= available_date)
    }
    if membership_by_security is not None:
        security_ids = {
            security_id
            for security_id in security_ids
            if any(
                start <= available_date and (end is None or end >= available_date)
                for start, end in membership_by_security.get(security_id, ())
            )
        }
    return sorted(security_ids)


def build_feature_rows(
    annual_assets: pd.DataFrame,
    security_master: pd.DataFrame,
    *,
    pit_membership: pd.DataFrame | None = None,
    min_available_at: str = DEFAULT_MIN_AVAILABLE,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    assets = _normalize_assets(annual_assets)
    invalid_value_rows_excluded = int(assets.attrs.get("invalid_value_rows_excluded", 0))
    impossible_period_rows_excluded = int(assets.attrs.get("impossible_period_rows_excluded", 0))
    causally_early_rows_adjusted = int(assets.attrs.get("causally_early_rows_adjusted", 0))
    master = _normalize_master(security_master)
    missing_master_cik_rows_excluded = int(master.attrs.get("missing_cik_rows_excluded", 0))
    membership = _normalize_membership(pit_membership) if pit_membership is not None else None
    cutoff_ts = pd.Timestamp(SEALED_CHALLENGE_START)
    min_ts = pd.Timestamp(min_available_at)
    if cutoff_ts.tzinfo is None or min_ts.tzinfo is None:
        raise ValueError("sealed cutoff and min_available_at must be timezone-aware")

    assets = assets.drop_duplicates(
        ["cik", "accession_number", "fiscal_period_end", "value", "available_at"],
        keep="last",
    )

    rows: list[dict[str, Any]] = []
    state_by_cik: dict[str, dict[pd.Timestamp, tuple[float, str]]] = {}
    incomplete_predecessor_events = 0
    unmapped_events = 0
    cutoff_events = 0
    event_count = 0
    complete_master_events = 0
    mapped_complete_events = 0
    complete_events_without_master_cik = 0
    multi_security_events = 0
    seen_output_keys: set[tuple[str, str, str]] = set()
    master_ciks = set(master["cik"].astype(str))
    master_by_cik: dict[str, list[tuple[str, pd.Timestamp, pd.Timestamp | None]]] = {}
    for record in master.itertuples(index=False):
        master_by_cik.setdefault(str(record.cik), []).append(
            (
                str(record.security_id),
                pd.Timestamp(record.effective_start),
                pd.Timestamp(record.effective_end) if pd.notna(record.effective_end) else None,
            )
        )
    membership_by_security: dict[
        str, list[tuple[pd.Timestamp, pd.Timestamp | None]]
    ] | None = None
    if membership is not None:
        membership_by_security = {}
        for record in membership.itertuples(index=False):
            membership_by_security.setdefault(str(record.security_id), []).append(
                (
                    pd.Timestamp(record.membership_start_date),
                    pd.Timestamp(record.membership_end_date)
                    if pd.notna(record.membership_end_date)
                    else None,
                )
            )

    source_events: dict[tuple[str, str, pd.Timestamp, str, pd.Timestamp], list[tuple[pd.Timestamp, float]]] = {}
    for record in assets.itertuples(index=False):
        event_key = (
            str(record.cik),
            str(record.accession_number),
            pd.Timestamp(record.available_at),
            str(record.form),
            pd.Timestamp(record.filed),
        )
        source_events.setdefault(event_key, []).append(
            (pd.Timestamp(record.fiscal_period_end), float(record.value))
        )
    slots: dict[tuple[pd.Timestamp, str], list[dict[str, Any]]] = {}
    for event_key, event_rows in source_events.items():
        event_count += 1
        cik, accession, source_available, form, filed = event_key
        current_period = max(period for period, _ in event_rows)
        effective = max(pd.Timestamp(source_available), _floor_available_at(current_period.date()))
        if effective >= cutoff_ts:
            cutoff_events += 1
            continue
        slots.setdefault((effective, str(cik)), []).append(
            {
                "accession": str(accession),
                "source_available": pd.Timestamp(source_available),
                "filed": pd.Timestamp(filed),
                "form": str(form),
                "current_period": current_period,
                "rows": event_rows,
            }
        )

    for (effective, cik), slot_events in sorted(slots.items(), key=lambda item: item[0]):
        state = state_by_cik.setdefault(cik, {})
        ordered_events = sorted(
            slot_events,
            key=lambda event: (
                event["source_available"],
                event["filed"],
                event["form"].endswith("/A"),
                event["accession"],
            ),
        )
        for event in ordered_events:
            for fiscal_period_end, value in event["rows"]:
                state[fiscal_period_end] = (
                    value,
                    event["accession"],
                )

        for current_period in sorted({event["current_period"] for event in ordered_events}):
            previous = _prior_period(state, current_period)
            previous_two = _prior_period(state, previous) if previous is not None else None
            if previous is None or previous_two is None:
                incomplete_predecessor_events += 1
                continue
            current_value, current_accession = state[current_period]
            previous_value, previous_accession = state[previous]
            previous_two_value, previous_two_accession = state[previous_two]
            if min(current_value, previous_value, previous_two_value) <= 0.0:
                raise ValueError("non-positive annual asset value reached feature computation")

            if cik in master_ciks:
                complete_master_events += 1
            else:
                complete_events_without_master_cik += 1
            securities = _active_security_ids(
                master_by_cik,
                cik,
                effective.tz_convert(None).normalize(),
                membership_by_security,
            )
            if not securities:
                if cik in master_ciks:
                    unmapped_events += 1
                continue
            mapped_complete_events += 1
            if len(securities) > 1:
                multi_security_events += 1
            if effective < min_ts:
                continue
            accessions = json.dumps(
                list(dict.fromkeys([current_accession, previous_accession, previous_two_accession])),
                separators=(",", ":"),
            )
            available_text = effective.isoformat()
            period_text = str(current_period.date())
            for security_id in securities:
                key = (security_id, period_text, available_text)
                if key in seen_output_keys:
                    raise ValueError(f"ambiguous duplicate output key: {key}")
                seen_output_keys.add(key)
                rows.append(
                    {
                        "security_id": security_id,
                        "fiscal_period_end": period_text,
                        "available_at": available_text,
                        "asset_growth_1y": (current_value / previous_value) - 1.0,
                        "asset_growth_2y": (current_value / previous_two_value) - 1.0,
                        "source_accessions": accessions,
                    }
                )

    output = pd.DataFrame(rows, columns=OUTPUT_FIELDS)
    if not output.empty:
        output = output.sort_values(
            ["available_at", "security_id", "fiscal_period_end"]
        ).reset_index(drop=True)
    mapping_coverage = (
        mapped_complete_events / complete_master_events if complete_master_events else 0.0
    )
    securities_by_year = (
        output.assign(year=output["available_at"].str[:4])
        .groupby("year")["security_id"]
        .nunique()
        .astype(int)
        .to_dict()
        if not output.empty
        else {}
    )
    required_years = list(COVERAGE_YEARS)
    membership_active_mappable_by_year: dict[str, int] = {}
    feature_covered_membership_by_year: dict[str, int] = {}
    membership_coverage_by_year: dict[str, float] = {}
    if membership is not None:
        mappable_security_ids = set(master["security_id"].astype(str))
        output_year_ids = {
            year: set(group["security_id"].astype(str))
            for year, group in output.assign(year=output["available_at"].str[:4]).groupby("year")
        }
        for year in required_years:
            as_of = pd.Timestamp(f"{year}-12-31")
            active = membership[
                membership["membership_start_date"].le(as_of)
                & (
                    membership["membership_end_date"].isna()
                    | membership["membership_end_date"].ge(as_of)
                )
            ]
            eligible_ids = set(active["security_id"].astype(str)) & mappable_security_ids
            covered_ids = eligible_ids & output_year_ids.get(year, set())
            membership_active_mappable_by_year[year] = len(eligible_ids)
            feature_covered_membership_by_year[year] = len(covered_ids)
            membership_coverage_by_year[year] = (
                len(covered_ids) / len(eligible_ids) if eligible_ids else 0.0
            )
    coverage_blockers: list[str] = []
    if mapping_coverage < MIN_MAPPING_COVERAGE:
        coverage_blockers.append("mapped_complete_event_coverage_below_95pct")
    unique_securities = int(output["security_id"].nunique()) if not output.empty else 0
    if unique_securities < MIN_UNIQUE_SECURITIES:
        coverage_blockers.append("unique_security_coverage_below_500")
    thin_years = [
        year for year in required_years if int(securities_by_year.get(year, 0)) < MIN_SECURITIES_PER_YEAR
    ]
    if thin_years:
        coverage_blockers.append("annual_security_coverage_below_100:" + ",".join(thin_years))
    if membership is None:
        coverage_blockers.append("frozen_pit_membership_coverage_unproven")
    else:
        low_membership_years = [
            year
            for year in required_years
            if membership_coverage_by_year.get(year, 0.0) < MIN_MEMBERSHIP_COVERAGE
        ]
        if low_membership_years:
            coverage_blockers.append(
                "annual_pit_membership_coverage_below_80pct:" + ",".join(low_membership_years)
            )
    if output[list(OUTPUT_FIELDS)].isna().any().any():
        coverage_blockers.append("feature_output_contains_nulls")
    quality = {
        "annual_asset_rows": int(len(assets)),
        "annual_filing_events": event_count,
        "feature_rows": int(len(output)),
        "unique_securities": unique_securities,
        "invalid_value_rows_excluded": invalid_value_rows_excluded,
        "impossible_period_rows_excluded": impossible_period_rows_excluded,
        "causally_early_rows_adjusted": causally_early_rows_adjusted,
        "missing_master_cik_rows_excluded": missing_master_cik_rows_excluded,
        "incomplete_predecessor_events_excluded": incomplete_predecessor_events,
        "unmapped_identity_events_excluded": unmapped_events,
        "complete_events_without_master_cik_excluded": complete_events_without_master_cik,
        "challenge_cutoff_events_excluded": cutoff_events,
        "complete_master_events": complete_master_events,
        "mapped_complete_events": mapped_complete_events,
        "mapping_coverage": mapping_coverage,
        "multi_security_cik_events": multi_security_events,
        "securities_by_available_year": securities_by_year,
        "membership_active_mappable_by_year": membership_active_mappable_by_year,
        "feature_covered_membership_by_year": feature_covered_membership_by_year,
        "membership_coverage_by_year": membership_coverage_by_year,
        "minimum_membership_coverage_required": MIN_MEMBERSHIP_COVERAGE,
        "issuer_share_class_policy": (
            "emit every active PIT membership security mapped to the filing CIK; downstream "
            "evaluation must apply its frozen issuer-neutral/primary-class gate before ranking"
        ),
        "minimum_available_at": output["available_at"].min() if not output.empty else None,
        "maximum_available_at": output["available_at"].max() if not output.empty else None,
        "challenge_source_scanned": True,
        "challenge_rows_used": False,
        "challenge_return_data_accessed": False,
        "sealed_source_fact_cutoff": SEALED_CHALLENGE_START,
        "conflicting_accession_period_groups": 0,
        "coverage_blockers": coverage_blockers,
    }
    return output, quality


def _atomic_parquet(frame: pd.DataFrame, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, staged_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".parquet", dir=path.parent)
    os.close(descriptor)
    staged = Path(staged_name)
    try:
        frame.to_parquet(staged, index=False)
        observed = pd.read_parquet(staged)
        pd.testing.assert_frame_equal(observed, frame, check_dtype=False)
        staged_hash = sha256_file(staged)
        if path.exists():
            if sha256_file(path) != staged_hash:
                raise RuntimeError(f"immutable feature artifact already exists with different bytes: {path}")
            return staged_hash
        os.replace(staged, path)
        try:
            if sha256_file(path) != staged_hash:
                raise RuntimeError("published feature artifact hash mismatch")
        except Exception:
            # A failed first publication is not allowed to leave a canonical
            # artifact behind without a valid certification.
            if path.exists():
                path.unlink()
            raise
        return staged_hash
    finally:
        if staged.exists():
            staged.unlink()


def _source_record(repo_root: Path, path: Path) -> dict[str, Any]:
    return {
        "path": str(path.resolve().relative_to(repo_root.resolve())),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _assert_sources_unchanged(
    *,
    repo_root: Path,
    paths: Sequence[Path],
    pinned_records: Sequence[Mapping[str, Any]],
) -> None:
    observed = [_source_record(repo_root, path) for path in paths]
    if observed != list(pinned_records):
        raise RuntimeError("an upstream authority file changed during materialization")


def _build_certification(
    *,
    repo_root: Path,
    output_path: Path,
    upstream_records: Sequence[Mapping[str, Any]],
    quality: Mapping[str, Any],
) -> dict[str, Any]:
    blockers = list(quality.get("coverage_blockers") or [])
    pit_verified = not blockers
    if not pit_verified:
        blockers.append("historical_point_in_time_not_verified")
    output_record = _source_record(repo_root, output_path)
    unsigned = {
        "provider_id": PIT_ASSET_GROWTH_FEATURES.provider_id,
        "dataset_id": PIT_ASSET_GROWTH_FEATURES.dataset_id,
        "status": "READY" if pit_verified else "BLOCKED",
        "data_files": [output_record],
        "schema_manifest": [
            {
                "logical_field": field,
                "source_path": output_record["path"],
                "physical_field": field,
                "data_type": "string_or_numeric",
            }
            for field in PIT_ASSET_GROWTH_FEATURES.required_fields
        ],
        "schema_validation_status": "PASS",
        "historical_point_in_time_verified": pit_verified,
        "methodology": (
            "SEC FY annual USD Assets by accession; each filing updates comparative facts only "
            "prospectively; availability is max(next-session filing availability, regular-session "
            "open on/after fiscal-period-end plus 90 days); effective-dated CIK-security mapping; "
            "one-year=current/prior-1; two-year=current/t-2-1; source facts available on or after "
            "2025-01-01 are excluded"
        ),
        "blockers": blockers,
        "upstream_sources": [dict(record) for record in upstream_records],
        "quality_summary": dict(quality),
        "sealed_source_fact_cutoff": SEALED_CHALLENGE_START,
        "challenge_source_scanned": True,
        "challenge_rows_used": False,
        "challenge_return_data_accessed": False,
    }
    payload = dict(unsigned)
    payload["evidence_hash"] = canonical_hash(payload)
    return payload


def _atomic_certification(payload: Mapping[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (canonical_json(dict(payload)) + "\n").encode("utf-8")
    unsigned = dict(payload)
    expected_hash = str(unsigned.pop("evidence_hash", ""))
    if expected_hash != canonical_hash(unsigned):
        raise RuntimeError("certification evidence hash is invalid before publication")
    if path.exists():
        if path.read_bytes() != content:
            raise RuntimeError(f"immutable certification already exists with different bytes: {path}")
        return
    descriptor, staged_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".json", dir=path.parent)
    staged = Path(staged_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        staged_payload = json.loads(staged.read_text(encoding="utf-8"))
        staged_unsigned = dict(staged_payload)
        staged_declared_hash = str(staged_unsigned.pop("evidence_hash", ""))
        if staged_declared_hash != expected_hash or canonical_hash(staged_unsigned) != expected_hash:
            raise RuntimeError("staged certification verification failed")
        os.replace(staged, path)
        try:
            observed = json.loads(path.read_text(encoding="utf-8"))
            if observed != dict(payload):
                raise RuntimeError("published certification verification failed")
        except Exception:
            if path.exists():
                path.unlink()
            raise
    finally:
        if staged.exists():
            staged.unlink()


def materialize_asset_growth_features(
    *,
    repo_root: Path,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    source = _resolve(root, DEFAULT_SOURCE)
    source_manifest = _resolve(root, DEFAULT_SOURCE_MANIFEST)
    master_path = _resolve(root, DEFAULT_SECURITY_MASTER)
    security_certification = _resolve(root, DEFAULT_SECURITY_CERTIFICATION)
    membership_path = _resolve(root, DEFAULT_MEMBERSHIP)
    membership_certification = _resolve(root, DEFAULT_MEMBERSHIP_CERTIFICATION)
    output = _resolve(root, DEFAULT_OUTPUT)
    authority_paths = (
        source,
        source_manifest,
        master_path,
        security_certification,
        membership_path,
        membership_certification,
    )
    pinned_records = verify_upstream_authority(
        repo_root=root,
        source_path=source,
        source_manifest_path=source_manifest,
        security_master_path=master_path,
        security_certification_path=security_certification,
        membership_path=membership_path,
        membership_certification_path=membership_certification,
    )
    annual_assets = _load_annual_assets(source)
    security_master = _load_security_master(master_path)
    membership = _load_membership(membership_path)
    features, quality = build_feature_rows(
        annual_assets,
        security_master,
        pit_membership=membership,
    )
    if features.empty:
        raise RuntimeError("no complete PIT asset-growth features were materialized")
    if quality["coverage_blockers"]:
        raise RuntimeError(
            "asset-growth coverage gates failed before canonical publication: "
            + canonical_json(quality)
        )
    _assert_sources_unchanged(
        repo_root=root,
        paths=authority_paths,
        pinned_records=pinned_records,
    )
    output_preexisted = output.exists()
    certification_path = root / PIT_ASSET_GROWTH_FEATURES.certification_path
    try:
        output_sha256 = _atomic_parquet(features, output)
        certification = _build_certification(
            repo_root=root,
            output_path=output,
            upstream_records=pinned_records,
            quality=quality,
        )
        # Recheck every authority after output publication and before the
        # single final certification write.  An uncertified output is safe,
        # but it must not strand the fixed canonical path on failure.
        _assert_sources_unchanged(
            repo_root=root,
            paths=authority_paths,
            pinned_records=pinned_records,
        )
        _atomic_certification(certification, certification_path)
    except Exception:
        if not output_preexisted and output.exists():
            output.unlink()
        raise
    return {
        "schema_version": "caerus_alpha_lab_asset_growth_materialization_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "output_path": str(output),
        "output_sha256": output_sha256,
        "certification_path": str(certification_path),
        "certification_evidence_hash": certification["evidence_hash"],
        "quality": quality,
        "challenge_source_scanned": True,
        "challenge_rows_used": False,
        "challenge_return_data_accessed": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    args = parser.parse_args(argv)
    result = materialize_asset_growth_features(repo_root=args.repo_root)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

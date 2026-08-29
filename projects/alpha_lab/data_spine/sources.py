"""Source-specific collectors for the shared Alpha Lab data spine."""

from __future__ import annotations

import csv
import gzip
import io
import json
import os
import urllib.error
import urllib.parse
import zipfile
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Mapping
from zoneinfo import ZoneInfo

from projects.alpha_lab.factory import canonical_json

from . import http
from .registry import SourceRegistry
from .storage import write_bundle


Fetcher = Callable[..., http.Response]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _csv_bytes(rows: Iterable[Mapping[str, Any]], fieldnames: list[str]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({name: row.get(name) for name in fieldnames})
    return stream.getvalue().encode("utf-8")


def audit_sharadar(
    *,
    repo_root: Path,
    registry: SourceRegistry,
    fetcher: Fetcher = http.get,
    checked_at: datetime | None = None,
) -> Dict[str, Any]:
    """Probe each required table with one row; never persist or report the API key."""

    config = registry.sources["sharadar"]
    env_name = str(config["api_key_env"])
    api_key = os.environ.get(env_name)
    timestamp = checked_at or _now()
    if not api_key:
        results = [
            {"table": table, "status": "BLOCKED_CREDENTIAL"}
            for table in config["required_tables"]
        ]
    else:
        results = []
        for table in config["required_tables"]:
            query = urllib.parse.urlencode({"api_key": api_key, "qopts.per_page": 1})
            url = (
                "https://data.nasdaq.com/api/v3/datatables/SHARADAR/"
                + str(table)
                + ".json?"
                + query
            )
            try:
                response = fetcher(
                    url, headers={"Accept": "application/json"}, timeout=45
                )
                payload = json.loads(response.body)
                datatable = payload.get("datatable") if isinstance(payload, dict) else None
                row_count = len((datatable or {}).get("data") or [])
                results.append(
                    {
                        "table": table,
                        "status": "ACCESSIBLE",
                        "sample_row_count": row_count,
                    }
                )
            except urllib.error.HTTPError as exc:
                results.append(
                    {"table": table, "status": "DENIED", "http_status": int(exc.code)}
                )
            except Exception as exc:
                results.append(
                    {"table": table, "status": "ERROR", "error_type": type(exc).__name__}
                )
    payload = {
        "schema_version": "caerus_sharadar_access_audit_v1",
        "checked_at": timestamp.isoformat(),
        "credential_env": env_name,
        "credential_present": bool(api_key),
        "tables": results,
        "all_required_accessible": bool(results)
        and all(row["status"] == "ACCESSIBLE" for row in results),
        "credential_value_persisted": False,
        "production_integration": False,
    }
    bundle = write_bundle(
        repo_root=repo_root,
        source_id="sharadar_access",
        files={
            "access_audit.json": (canonical_json(payload) + "\n").encode("utf-8")
        },
        metadata={"config_hash": registry.config_hash, "kind": "access_audit"},
        retrieved_at=timestamp,
    )
    return {"audit": payload, **bundle}


def capture_sharadar_table(
    *,
    repo_root: Path,
    registry: SourceRegistry,
    table: str,
    tickers: Iterable[str] = (),
    start_date: str | None = None,
    end_date: str | None = None,
    maximum_rows: int | None = None,
    fetcher: Fetcher = http.get,
    retrieved_at: datetime | None = None,
) -> Dict[str, Any]:
    """Capture a licensed table or scoped ticker slice before entitlement expiry."""

    config = registry.sources["sharadar"]
    table = table.upper()
    if table not in set(config["required_tables"]):
        raise ValueError("Sharadar table is outside the approved research contract")
    env_name = str(config["api_key_env"])
    api_key = os.environ.get(env_name)
    if not api_key:
        raise RuntimeError("{} is required for Sharadar capture".format(env_name))
    timestamp = retrieved_at or _now()
    ticker_values = sorted(
        {str(value).strip().upper() for value in tickers if str(value).strip()}
    )
    scopes = ticker_values or [None]
    rows = []
    columns = None
    page_count = 0
    for ticker in scopes:
        cursor = None
        for _ in range(10000):
            params: Dict[str, Any] = {"api_key": api_key, "qopts.per_page": 10000}
            if ticker:
                params["ticker"] = ticker
            if start_date:
                params["date.gte"] = start_date
            if end_date:
                params["date.lte"] = end_date
            if cursor:
                params["qopts.cursor_id"] = cursor
            url = (
                "https://data.nasdaq.com/api/v3/datatables/SHARADAR/"
                + table
                + ".json?"
                + urllib.parse.urlencode(params)
            )
            response = fetcher(
                url, headers={"Accept": "application/json"}, timeout=180
            )
            payload = json.loads(response.body)
            datatable = payload.get("datatable") or {}
            page_columns = [
                str(item.get("name")) for item in (datatable.get("columns") or [])
            ]
            if columns is None:
                columns = page_columns
            if page_columns != columns:
                raise ValueError("Sharadar schema changed during capture")
            for values in datatable.get("data") or []:
                rows.append(dict(zip(columns, values)))
                if maximum_rows and len(rows) >= maximum_rows:
                    break
            page_count += 1
            if maximum_rows and len(rows) >= maximum_rows:
                break
            cursor = (payload.get("meta") or {}).get("next_cursor_id")
            if not cursor:
                break
        if maximum_rows and len(rows) >= maximum_rows:
            break
    jsonl = b"".join(
        (canonical_json(row) + "\n").encode("utf-8") for row in rows
    )
    return write_bundle(
        repo_root=repo_root,
        source_id="sharadar_{}".format(table.lower()),
        files={"{}.jsonl".format(table.lower()): jsonl or b"\n"},
        metadata={
            "table": "SHARADAR/{}".format(table),
            "tickers": ticker_values,
            "start_date": start_date,
            "end_date": end_date,
            "maximum_rows": maximum_rows,
            "row_count": len(rows),
            "page_count": page_count,
            "columns": columns or [],
            "credential_value_persisted": False,
            "license_rights_must_be_preserved": True,
        },
        retrieved_at=timestamp,
    )


def collect_sec_reference(
    *,
    repo_root: Path,
    registry: SourceRegistry,
    fetcher: Fetcher = http.get,
    user_agent: str | None = None,
    retrieved_at: datetime | None = None,
) -> Dict[str, Any]:
    config = registry.sources["sec"]
    agent = user_agent or os.environ.get(str(config["user_agent_env"]))
    if not agent or "@" not in agent:
        raise RuntimeError(
            "SEC_USER_AGENT must identify the research client and a contact email"
        )
    timestamp = retrieved_at or _now()
    url = "https://www.sec.gov/files/company_tickers_exchange.json"
    response = fetcher(
        url,
        headers={"User-Agent": agent, "Accept-Encoding": "gzip, deflate"},
        timeout=90,
    )
    payload = json.loads(response.body)
    if not isinstance(payload, dict) or "data" not in payload or "fields" not in payload:
        raise ValueError("unexpected SEC ticker mapping payload")
    return write_bundle(
        repo_root=repo_root,
        source_id="sec_reference",
        files={"company_tickers_exchange.json": response.body},
        metadata={
            "source_url": url,
            "row_count": len(payload.get("data") or []),
            "fields": payload.get("fields"),
            "user_agent_persisted": False,
            "current_mapping_not_historical_security_master": True,
        },
        retrieved_at=timestamp,
    )


def collect_sec_master_indexes(
    *,
    repo_root: Path,
    registry: SourceRegistry,
    start_year: int,
    end_year: int,
    fetcher: Fetcher = http.get,
    user_agent: str | None = None,
    retrieved_at: datetime | None = None,
) -> Dict[str, Any]:
    config = registry.sources["sec"]
    if start_year < int(config["index_start_year"]) or end_year < start_year:
        raise ValueError("invalid SEC index year range")
    agent = user_agent or os.environ.get(str(config["user_agent_env"]))
    if not agent or "@" not in agent:
        raise RuntimeError(
            "SEC_USER_AGENT must identify the research client and a contact email"
        )
    forms = set(str(value) for value in config["forms"])
    timestamp = retrieved_at or _now()
    rows = []
    errors = []
    for year in range(start_year, end_year + 1):
        for quarter in range(1, 5):
            url = (
                "https://www.sec.gov/Archives/edgar/full-index/"
                + str(year)
                + "/QTR"
                + str(quarter)
                + "/master.gz"
            )
            try:
                response = fetcher(url, headers={"User-Agent": agent}, timeout=120)
                text = gzip.decompress(response.body).decode("latin-1")
                data_started = False
                for line in text.splitlines():
                    if line.startswith("-----"):
                        data_started = True
                        continue
                    if not data_started:
                        continue
                    parts = line.split("|", 4)
                    if len(parts) != 5 or parts[2] not in forms:
                        continue
                    rows.append(
                        {
                            "cik": parts[0],
                            "company_name": parts[1],
                            "form_type": parts[2],
                            "filed_date": parts[3],
                            "filename": parts[4],
                            "index_year": year,
                            "index_quarter": quarter,
                        }
                    )
            except urllib.error.HTTPError as exc:
                if int(exc.code) != 404:
                    errors.append(
                        {"year": year, "quarter": quarter, "http_status": int(exc.code)}
                    )
            except Exception as exc:
                errors.append(
                    {
                        "year": year,
                        "quarter": quarter,
                        "error_type": type(exc).__name__,
                    }
                )
    rows.sort(
        key=lambda row: (
            row["filed_date"],
            row["cik"],
            row["form_type"],
            row["filename"],
        )
    )
    fieldnames = [
        "cik",
        "company_name",
        "form_type",
        "filed_date",
        "filename",
        "index_year",
        "index_quarter",
    ]
    return write_bundle(
        repo_root=repo_root,
        source_id="sec_event_index",
        files={
            "event_index.csv": _csv_bytes(rows, fieldnames),
            "errors.json": (canonical_json(errors) + "\n").encode("utf-8"),
        },
        metadata={
            "start_year": start_year,
            "end_year": end_year,
            "forms": sorted(forms),
            "row_count": len(rows),
            "error_count": len(errors),
            "acceptance_timestamp_not_in_master_index": True,
            "original_filing_hydration_required": True,
        },
        retrieved_at=timestamp,
    )


_SEC_ACCEPTANCE = re.compile(
    rb"(?:ACCEPTANCE-DATETIME:\s*|<ACCEPTANCE-DATETIME>)(\d{14})"
)
_SEC_ACCESSION = re.compile(rb"ACCESSION NUMBER:\s*([0-9-]+)")


def hydrate_sec_filings(
    *,
    repo_root: Path,
    registry: SourceRegistry,
    index_path: Path,
    forms: Iterable[str] = ("4", "4/A"),
    limit: int = 500,
    fetcher: Fetcher = http.get,
    user_agent: str | None = None,
    retrieved_at: datetime | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> Dict[str, Any]:
    """Hydrate original SEC submissions and preserve authoritative acceptance time."""

    config = registry.sources["sec"]
    agent = user_agent or os.environ.get(str(config["user_agent_env"]))
    if not agent or "@" not in agent:
        raise RuntimeError(
            "SEC_USER_AGENT must identify the research client and a contact email"
        )
    path = Path(index_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    requested_forms = set(str(value) for value in forms)
    with path.open("r", encoding="utf-8", newline="") as stream:
        candidates = [
            row
            for row in csv.DictReader(stream)
            if str(row.get("form_type")) in requested_forms
        ]
    candidates.sort(
        key=lambda row: (
            str(row.get("index_year")),
            str(row.get("filed_date")),
            str(row.get("cik")),
            str(row.get("filename")),
        )
    )
    if limit < 1:
        raise ValueError("SEC hydration limit must be positive")
    if len(candidates) > limit:
        step = len(candidates) / limit
        candidates = [candidates[min(int(index * step), len(candidates) - 1)] for index in range(limit)]
    timestamp = retrieved_at or _now()
    files: Dict[str, bytes] = {}
    records = []
    errors = []
    for index, row in enumerate(candidates):
        filename = str(row["filename"])
        url = "https://www.sec.gov/Archives/" + filename
        try:
            for attempt in range(3):
                try:
                    response = fetcher(url, headers={"User-Agent": agent}, timeout=120)
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    sleeper(float(2 ** attempt))
            acceptance_match = _SEC_ACCEPTANCE.search(response.body)
            accession_match = _SEC_ACCESSION.search(response.body)
            accession = (
                accession_match.group(1).decode("ascii")
                if accession_match
                else Path(filename).stem
            )
            safe_accession = accession.replace("/", "-")
            files["filings/{}.txt".format(safe_accession)] = response.body
            acceptance_local = (
                datetime.strptime(
                    acceptance_match.group(1).decode("ascii"), "%Y%m%d%H%M%S"
                ).replace(tzinfo=ZoneInfo("America/New_York"))
                if acceptance_match
                else None
            )
            records.append(
                {
                    "accession_number": accession,
                    "cik": row.get("cik"),
                    "form_type": row.get("form_type"),
                    "filed_date": row.get("filed_date"),
                    "acceptance_datetime_utc": (
                        acceptance_local.astimezone(timezone.utc).isoformat()
                        if acceptance_local
                        else None
                    ),
                    "acceptance_datetime_et": acceptance_local.isoformat() if acceptance_local else None,
                    "source_filename": filename,
                    "acceptance_parse_status": "PASS" if acceptance_match else "MISSING",
                }
            )
        except Exception as exc:
            errors.append(
                {
                    "filename": filename,
                    "form_type": row.get("form_type"),
                    "error_type": type(exc).__name__,
                }
            )
        if index < len(candidates) - 1:
            sleeper(0.12)
    record_fields = [
        "accession_number",
        "cik",
        "form_type",
        "filed_date",
        "acceptance_datetime_utc",
        "acceptance_datetime_et",
        "source_filename",
        "acceptance_parse_status",
    ]
    files["filing_inventory.csv"] = _csv_bytes(records, record_fields)
    files["errors.json"] = (canonical_json(errors) + "\n").encode("utf-8")
    return write_bundle(
        repo_root=repo_root,
        source_id="sec_original_filings",
        files=files,
        metadata={
            "forms": sorted(requested_forms),
            "candidate_count": len(candidates),
            "hydrated_count": len(records),
            "error_count": len(errors),
            "acceptance_timestamp_pass_count": sum(
                row["acceptance_parse_status"] == "PASS" for row in records
            ),
            "user_agent_persisted": False,
            "original_submission_preserved": True,
        },
        retrieved_at=timestamp,
    )


def _parse_french_zip(payload: bytes) -> tuple[list[str], list[dict[str, str]]]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [
            name
            for name in archive.namelist()
            if name.lower().endswith((".csv", ".txt"))
        ]
        if not names:
            raise ValueError("French archive contains no text data")
        text = archive.read(names[0]).decode("latin-1")
    header = None
    rows = []
    for line in text.splitlines():
        cells = [cell.strip() for cell in line.split(",")]
        while cells and cells[-1] == "":
            cells.pop()
        first = cells[0] if cells else ""
        if (
            header is None
            and first == ""
            and len(cells) >= 2
            and any(value for value in cells[1:])
        ):
            header = ["date"] + [
                value.replace("-", "_").replace(" ", "_") for value in cells[1:]
            ]
            continue
        if (
            header
            and len(first) == 8
            and first.isdigit()
            and len(cells) == len(header)
        ):
            rows.append(dict(zip(header, cells)))
        elif rows:
            break
    if not header or not rows:
        raise ValueError("could not locate daily French factor table")
    return header, rows


def _parse_aqr_us_factor(payload: bytes, factor_name: str) -> list[dict[str, Any]]:
    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError("pandas and openpyxl are required for AQR workbooks") from exc
    frame = pd.read_excel(io.BytesIO(payload), sheet_name=0, header=18)
    if "DATE" not in frame or "USA" not in frame:
        raise ValueError("AQR workbook is missing DATE or USA")
    dates = pd.to_datetime(frame["DATE"], errors="coerce")
    values = pd.to_numeric(frame["USA"], errors="coerce")
    rows = []
    for observed_date, value in zip(dates, values):
        if pd.isna(observed_date) or pd.isna(value):
            continue
        rows.append(
            {
                "date": observed_date.strftime("%Y%m%d"),
                factor_name: float(value),
            }
        )
    if not rows:
        raise ValueError("AQR workbook contains no usable U.S. factor rows")
    return rows


def collect_factors(
    *,
    repo_root: Path,
    registry: SourceRegistry,
    fetcher: Fetcher = http.get,
    retrieved_at: datetime | None = None,
) -> Dict[str, Any]:
    timestamp = retrieved_at or _now()
    raw_files: Dict[str, bytes] = {}
    factor_tables: Dict[str, tuple[list[str], list[dict[str, str]]]] = {}
    for dataset, url in registry.sources["french"]["datasets"].items():
        response = fetcher(
            url, headers={"User-Agent": "Caerus-Alpha-Lab/1.0"}, timeout=120
        )
        raw_files["french/{}.zip".format(dataset)] = response.body
        if dataset != "industry_49_daily":
            factor_tables[dataset] = _parse_french_zip(response.body)
    merged: Dict[str, Dict[str, str]] = {}
    rename = {"Mkt_RF": "MKT_RF", "Mom": "UMD", "ST_Rev": "ST_REV"}
    for _, (_, rows) in factor_tables.items():
        for row in rows:
            date_value = row["date"]
            target = merged.setdefault(date_value, {"date": date_value})
            for key, value in row.items():
                if key != "date":
                    target[rename.get(key, key)] = value
    columns = [
        "date",
        "MKT_RF",
        "SMB",
        "HML",
        "RMW",
        "CMA",
        "RF",
        "UMD",
        "ST_REV",
    ]
    raw_files["normalized/french_factor_panel.csv"] = _csv_bytes(
        (merged[key] for key in sorted(merged)), columns
    )
    aqr_by_date: Dict[str, Dict[str, Any]] = {}
    aqr_names = {"bab_daily": "LOW_VOL_BAB", "qmj_daily": "QMJ"}
    for dataset, url in registry.sources["aqr"]["datasets"].items():
        response = fetcher(
            url, headers={"User-Agent": "Caerus-Alpha-Lab/1.0"}, timeout=120
        )
        raw_files["aqr/{}.xlsx".format(dataset)] = response.body
        for row in _parse_aqr_us_factor(response.body, aqr_names[dataset]):
            aqr_by_date.setdefault(row["date"], {"date": row["date"]}).update(row)
    raw_files["normalized/aqr_factor_panel.csv"] = _csv_bytes(
        (aqr_by_date[key] for key in sorted(aqr_by_date)),
        ["date", "LOW_VOL_BAB", "QMJ"],
    )
    combined = []
    for date_value in sorted(set(merged).union(aqr_by_date)):
        row: Dict[str, Any] = {"date": date_value}
        row.update(merged.get(date_value, {}))
        row.update(aqr_by_date.get(date_value, {}))
        combined.append(row)
    raw_files["normalized/factor_panel.csv"] = _csv_bytes(
        combined,
        columns + ["LOW_VOL_BAB", "QMJ"],
    )
    return write_bundle(
        repo_root=repo_root,
        source_id="factor_library",
        files=raw_files,
        metadata={
            "french_normalized_rows": len(merged),
            "aqr_raw_captured": True,
            "aqr_normalization_status": "COMPLETE_USA_DAILY",
            "aqr_normalized_rows": len(aqr_by_date),
            "combined_factor_rows": len(combined),
            "vintage_preserved": True,
        },
        retrieved_at=timestamp,
    )


def collect_fred_alfred(
    *,
    repo_root: Path,
    registry: SourceRegistry,
    fetcher: Fetcher = http.get,
    retrieved_at: datetime | None = None,
) -> Dict[str, Any]:
    config = registry.sources["fred"]
    env_name = str(config["api_key_env"])
    api_key = os.environ.get(env_name)
    if not api_key:
        raise RuntimeError("{} is required for FRED/ALFRED collection".format(env_name))
    timestamp = retrieved_at or _now()
    rows = []
    errors = []
    fallback_series = []
    for series_id in config["series"]:
        try:
            params = {
                "series_id": series_id,
                "api_key": api_key,
                "file_type": "json",
                "realtime_start": "1776-07-04",
                "realtime_end": "9999-12-31",
                "output_type": 4,
            }
            vintage_mode = "INITIAL_RELEASE"
            try:
                response = fetcher(
                    "https://api.stlouisfed.org/fred/series/observations?"
                    + urllib.parse.urlencode(params),
                    timeout=120,
                )
            except urllib.error.HTTPError:
                params = {
                    "series_id": series_id,
                    "api_key": api_key,
                    "file_type": "json",
                    "output_type": 1,
                }
                vintage_mode = "CURRENT_VINTAGE_FALLBACK"
                fallback_series.append(series_id)
                response = fetcher(
                    "https://api.stlouisfed.org/fred/series/observations?"
                    + urllib.parse.urlencode(params),
                    timeout=120,
                )
            payload = json.loads(response.body)
            for item in payload.get("observations") or []:
                rows.append(
                    {
                        "series_id": series_id,
                        "observation_date": item.get("date"),
                        "value": item.get("value"),
                        "realtime_start": item.get("realtime_start"),
                        "realtime_end": item.get("realtime_end"),
                        "retrieved_at": timestamp.isoformat(),
                        "vintage_mode": vintage_mode,
                    }
                )
        except Exception as exc:
            errors.append(
                {"series_id": series_id, "error_type": type(exc).__name__}
            )
    rows.sort(key=lambda row: (row["series_id"], str(row["observation_date"])))
    fields = [
        "series_id",
        "observation_date",
        "value",
        "realtime_start",
        "realtime_end",
        "retrieved_at",
        "vintage_mode",
    ]
    return write_bundle(
        repo_root=repo_root,
        source_id="fred_alfred",
        files={
            "initial_release_panel.csv": _csv_bytes(rows, fields),
            "errors.json": (canonical_json(errors) + "\n").encode("utf-8"),
        },
        metadata={
            "series": list(config["series"]),
            "row_count": len(rows),
            "error_count": len(errors),
            "fred_output_type": 4,
            "initial_release_values": True,
            "current_vintage_fallback_series": fallback_series,
            "intraday_release_time_not_provided": True,
            "conservative_next_session_availability_required": True,
        },
        retrieved_at=timestamp,
    )


def audit_eia(
    *,
    repo_root: Path,
    registry: SourceRegistry,
    head_fetcher: Fetcher = http.head,
    checked_at: datetime | None = None,
) -> Dict[str, Any]:
    config = registry.sources["eia"]
    timestamp = checked_at or _now()
    key_present = bool(os.environ.get(str(config["api_key_env"])))
    datasets = []
    for name, url in config["bulk"].items():
        try:
            response = head_fetcher(url, timeout=45)
            size = int(response.headers.get("content-length") or 0)
            datasets.append(
                {
                    "dataset": name,
                    "status": "AVAILABLE",
                    "bytes": size,
                    "automatic_capture_allowed": bool(
                        size
                        and size <= int(config["maximum_automatic_bulk_bytes"])
                    ),
                    "last_modified": response.headers.get("last-modified"),
                }
            )
        except Exception as exc:
            datasets.append(
                {
                    "dataset": name,
                    "status": "ERROR",
                    "error_type": type(exc).__name__,
                }
            )
    payload = {
        "schema_version": "caerus_eia_access_audit_v1",
        "checked_at": timestamp.isoformat(),
        "api_key_present": key_present,
        "bulk_requires_key": False,
        "datasets": datasets,
        "credential_value_persisted": False,
    }
    bundle = write_bundle(
        repo_root=repo_root,
        source_id="eia_access",
        files={
            "access_audit.json": (canonical_json(payload) + "\n").encode("utf-8")
        },
        metadata={"kind": "access_audit"},
        retrieved_at=timestamp,
    )
    return {"audit": payload, **bundle}


def collect_eia_bulk(
    *,
    repo_root: Path,
    registry: SourceRegistry,
    datasets: Iterable[str],
    fetcher: Fetcher = http.get,
    retrieved_at: datetime | None = None,
) -> Dict[str, Any]:
    config = registry.sources["eia"]
    timestamp = retrieved_at or _now()
    files = {}
    selected = []
    control_series = set(str(value) for value in config["control_series"])
    normalized_rows = []
    for name in datasets:
        if name not in config["bulk"]:
            raise ValueError("unknown EIA bulk dataset: {}".format(name))
        response = fetcher(config["bulk"][name], timeout=300)
        if len(response.body) > int(config["maximum_automatic_bulk_bytes"]):
            raise RuntimeError(
                "EIA {} bulk file exceeds automatic size gate".format(name)
            )
        files["{}.zip".format(name)] = response.body
        selected.append(name)
        with zipfile.ZipFile(io.BytesIO(response.body)) as archive:
            data_names = [
                value
                for value in archive.namelist()
                if value.lower().endswith((".txt", ".jsonl", ".ndjson"))
            ]
            if not data_names:
                raise ValueError("EIA bulk archive contains no JSON-lines payload")
            with archive.open(data_names[0]) as stream:
                for raw_line in stream:
                    try:
                        row = json.loads(raw_line)
                    except (TypeError, json.JSONDecodeError):
                        continue
                    series_id = str(row.get("series_id") or "")
                    if series_id not in control_series:
                        continue
                    for period, value in row.get("data") or []:
                        normalized_rows.append(
                            {
                                "series_id": series_id,
                                "name": row.get("name"),
                                "period": period,
                                "value": value,
                                "units": row.get("units"),
                                "frequency": row.get("f"),
                                "series_last_updated": row.get("last_updated"),
                                "bulk_retrieved_at": timestamp.isoformat(),
                            }
                        )
    normalized_rows.sort(
        key=lambda row: (str(row["series_id"]), str(row["period"]))
    )
    files["normalized/eia_controls_current_vintage.csv"] = _csv_bytes(
        normalized_rows,
        [
            "series_id",
            "name",
            "period",
            "value",
            "units",
            "frequency",
            "series_last_updated",
            "bulk_retrieved_at",
        ],
    )
    return write_bundle(
        repo_root=repo_root,
        source_id="eia_bulk",
        files=files,
        metadata={
            "datasets": selected,
            "api_key_required": False,
            "normalized_control_series": sorted(control_series),
            "normalized_row_count": len(normalized_rows),
            "historical_point_in_time_verified": False,
            "current_bulk_vintage_only": True,
        },
        retrieved_at=timestamp,
    )


def collect_occ_reference(
    *,
    repo_root: Path,
    registry: SourceRegistry,
    fetcher: Fetcher = http.get,
    retrieved_at: datetime | None = None,
) -> Dict[str, Any]:
    timestamp = retrieved_at or _now()
    files = {}
    statuses = []
    for name, url in registry.sources["occ"].items():
        try:
            response = fetcher(
                url, headers={"User-Agent": "Caerus-Alpha-Lab/1.0"}, timeout=90
            )
            files["{}.html".format(name)] = response.body
            statuses.append(
                {
                    "resource": name,
                    "status": "CAPTURED",
                    "bytes": len(response.body),
                }
            )
        except Exception as exc:
            statuses.append(
                {
                    "resource": name,
                    "status": "ERROR",
                    "error_type": type(exc).__name__,
                }
            )
    files["capture_status.json"] = (canonical_json(statuses) + "\n").encode("utf-8")
    return write_bundle(
        repo_root=repo_root,
        source_id="occ_reference",
        files=files,
        metadata={
            "statuses": statuses,
            "contract_specific_memo_hydration_required": True,
        },
        retrieved_at=timestamp,
    )


def collect_occ_local(
    *,
    repo_root: Path,
    directory: Path,
    retrieved_at: datetime | None = None,
) -> Dict[str, Any]:
    """Intake manually downloaded OCC memos or settlement records."""

    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(root)
    files = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if path.suffix.lower() not in {".pdf", ".csv", ".xlsx", ".xls", ".html", ".txt", ".json"}:
            continue
        files[relative.as_posix()] = path.read_bytes()
    if not files:
        raise ValueError("OCC intake directory contains no supported files")
    timestamp = retrieved_at or _now()
    return write_bundle(
        repo_root=repo_root,
        source_id="occ_manual_intake",
        files=files,
        metadata={
            "manual_download": True,
            "file_count": len(files),
            "contract_mapping_and_effective_date_audit_required": True,
        },
        retrieved_at=timestamp,
    )

"""Materialize certified Alpha Lab assets from immutable source bundles."""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import re
import zipfile
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from pandas.tseries.holiday import (
    AbstractHolidayCalendar,
    GoodFriday,
    Holiday,
    USLaborDay,
    USMartinLutherKingJr,
    USMemorialDay,
    USPresidentsDay,
    USThanksgivingDay,
    nearest_workday,
)

from projects.alpha_lab.experiments.catalog import (
    CIK_IDENTITY_INPUT,
    COMMODITY_CONTROLS,
    EARNINGS_EVENTS,
    FACTOR_PANEL,
    PIT_MEMBERSHIP,
    PIT_SECURITY_MASTER,
    SECTOR_RETURNS,
    DataAsset,
)
from projects.alpha_lab.factory import canonical_hash, canonical_json

from .storage import latest_manifest, sha256_file


_CIK = re.compile(r"[?&]CIK=(\d+)", re.IGNORECASE)
_EASTERN = ZoneInfo("America/New_York")


class _NYSEResearchHolidayCalendar(AbstractHolidayCalendar):
    """Full-session NYSE closures needed by the 2011+ research data spine."""

    rules = (
        Holiday("New Years Day", month=1, day=1, observance=nearest_workday),
        USMartinLutherKingJr,
        USPresidentsDay,
        GoodFriday,
        USMemorialDay,
        Holiday(
            "Juneteenth",
            month=6,
            day=19,
            start_date="2022-01-01",
            observance=nearest_workday,
        ),
        Holiday("Independence Day", month=7, day=4, observance=nearest_workday),
        USLaborDay,
        USThanksgivingDay,
        Holiday("Christmas", month=12, day=25, observance=nearest_workday),
    )


_SPECIAL_NYSE_CLOSURES = frozenset(
    {
        date(2012, 10, 29),
        date(2012, 10, 30),
        date(2018, 12, 5),
    }
)


@lru_cache(maxsize=None)
def _nyse_holidays(year: int) -> frozenset[date]:
    calendar = _NYSEResearchHolidayCalendar()
    values = calendar.holidays(
        start="{}-01-01".format(year - 1),
        end="{}-12-31".format(year + 1),
    )
    return frozenset(value.date() for value in values).union(_SPECIAL_NYSE_CLOSURES)


def _atomic_csv(path: Path, fieldnames: Sequence[str], rows: Iterable[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".{}.tmp".format(path.name))
    count = 0
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fieldnames})
            count += 1
    temporary.replace(path)
    return count


def _manifest_data_path(repo_root: Path, source_id: str, member: str) -> Path:
    manifest_path = latest_manifest(repo_root, source_id)
    if manifest_path is None:
        raise FileNotFoundError("no bundle found for {}".format(source_id))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if member not in {record["name"] for record in manifest["files"]}:
        raise FileNotFoundError("bundle member absent: {}".format(member))
    return manifest_path.parent / "data" / member


def _file_record(repo_root: Path, path: Path) -> Dict[str, Any]:
    return {
        "path": str(path.resolve().relative_to(repo_root.resolve())),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def certify_asset(
    *,
    repo_root: Path,
    asset: DataAsset,
    data_files: Sequence[Path],
    pit_verified: bool,
    methodology: str,
    blockers: Sequence[str] = (),
    evaluator_contract: Mapping[str, Any] | None = None,
) -> Path:
    records = [_file_record(repo_root, path) for path in sorted(data_files)]
    schema = []
    for logical_field in asset.required_fields:
        source_path = next(
            (
                record["path"]
                for record, path in zip(records, sorted(data_files))
                if logical_field in _physical_fields(path)
            ),
            None,
        )
        if source_path:
            schema.append(
                {
                    "logical_field": logical_field,
                    "source_path": source_path,
                    "physical_field": logical_field,
                    "data_type": "string_or_numeric",
                }
            )
    missing = sorted(set(asset.required_fields) - {item["logical_field"] for item in schema})
    all_blockers = list(blockers) + ["missing_field:{}".format(field) for field in missing]
    status = "READY" if not all_blockers and pit_verified else "BLOCKED"
    if not pit_verified and "historical_point_in_time_not_verified" not in all_blockers:
        all_blockers.append("historical_point_in_time_not_verified")
    unsigned = {
        "provider_id": asset.provider_id,
        "dataset_id": asset.dataset_id,
        "status": status,
        "data_files": records,
        "schema_manifest": schema,
        "schema_validation_status": "PASS" if not missing else "FAIL",
        "historical_point_in_time_verified": pit_verified,
        "methodology": methodology,
        "blockers": all_blockers,
    }
    if evaluator_contract is not None:
        unsigned["evaluator_contract"] = dict(evaluator_contract)
    payload = dict(unsigned)
    payload["evidence_hash"] = canonical_hash(unsigned)
    path = repo_root / asset.certification_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(payload) + "\n", encoding="utf-8")
    return path


def _physical_fields(path: Path) -> set[str]:
    if path.name.lower().endswith((".jsonl.gz", ".ndjson.gz")):
        fields = set()
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream):
                if line_number >= 1000:
                    break
                value = json.loads(line)
                if isinstance(value, dict):
                    fields.update(str(key) for key in value)
        return fields
    if path.suffix.lower() in {".parquet", ".pq"}:
        try:
            import pyarrow.parquet as parquet
        except ImportError as exc:
            raise RuntimeError("pyarrow is required to certify parquet assets") from exc
        return set(parquet.read_schema(path).names)
    with path.open("r", encoding="utf-8", newline="") as stream:
        return {value.strip() for value in next(csv.reader(stream), ()) if value.strip()}


def _common_equity(row: Dict[str, Any]) -> bool:
    category = str(row.get("category") or "")
    return (
        row.get("table") == "SEP"
        and "Common Stock" in category
        and "Warrant" not in category
        and row.get("exchange") in {"NASDAQ", "NYSE", "NYSEMKT"}
    )


def materialize_identity(repo_root: Path) -> Dict[str, Any]:
    source = _manifest_data_path(repo_root, "sharadar_tickers", "tickers.jsonl")
    rows = []
    with source.open("r", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if _common_equity(row):
                rows.append(row)
    master = []
    membership = []
    cik_rows = []
    capture_tickers = []
    for row in rows:
        ticker = str(row.get("ticker") or "").strip().upper()
        permaticker = str(row.get("permaticker") or "").strip()
        first = str(row.get("firstpricedate") or "")[:10]
        last = str(row.get("lastpricedate") or "")[:10]
        if not ticker or not permaticker or not first:
            continue
        cik_match = _CIK.search(str(row.get("secfilings") or ""))
        cik = cik_match.group(1).zfill(10) if cik_match else ""
        delisted = str(row.get("isdelisted") or "").upper() == "Y"
        security_id = "SHARADAR:{}".format(permaticker)
        item = {
            "security_id": security_id,
            "permaticker": permaticker,
            "cik": cik,
            "cusip": str(row.get("cusips") or "").split()[0] if row.get("cusips") else "",
            "figi": row.get("figi") or "",
            "ticker": ticker,
            "name": row.get("name") or "",
            "exchange": row.get("exchange") or "",
            "category": row.get("category") or "",
            "sector": row.get("sector") or "",
            "industry": row.get("industry") or "",
            "effective_start": first,
            "effective_end": last if delisted else "",
            "firstpricedate": first,
            "lastpricedate": last,
            "relatedtickers": row.get("relatedtickers") or "",
            "source": "SHARADAR/TICKERS",
            "confidence": "HIGH",
        }
        master.append(item)
        membership.append(
            {
                "security_id": security_id,
                "ticker": ticker,
                "membership_start_date": first,
                "membership_end_date": last if delisted else "",
                "membership_family": "sharadar_security_existence",
                "source": "SHARADAR/TICKERS",
                "confidence": "HIGH",
            }
        )
        if cik:
            cik_rows.append(
                {
                    "security_id": security_id,
                    "cik": cik,
                    "effective_start": first,
                    "effective_end": last if delisted else "",
                    "ticker": ticker,
                    "sector": row.get("sector") or "",
                    "source": "SHARADAR/TICKERS.secfilings",
                    "status": "OK",
                }
            )
        if first <= "2026-06-30" and (not last or last >= "2011-01-01"):
            capture_tickers.append(ticker)
    master.sort(key=lambda row: (row["security_id"], row["ticker"]))
    membership.sort(key=lambda row: (row["security_id"], row["ticker"]))
    cik_rows.sort(key=lambda row: (row["security_id"], row["cik"]))
    capture_tickers = sorted(set(capture_tickers))

    pit_root = repo_root / "data/pit_universe"
    master_path = pit_root / "security_master.csv"
    membership_path = pit_root / "membership_universe.csv"
    cik_path = repo_root / "cik_mapping_results.csv"
    capture_path = repo_root / "outputs/research/alpha_lab/shared/sep_capture_universe.txt"
    _atomic_csv(master_path, tuple(master[0]), master)
    _atomic_csv(membership_path, tuple(membership[0]), membership)
    _atomic_csv(cik_path, tuple(cik_rows[0]), cik_rows)
    capture_path.parent.mkdir(parents=True, exist_ok=True)
    capture_path.write_text("\n".join(capture_tickers) + "\n", encoding="utf-8")
    certify_asset(
        repo_root=repo_root,
        asset=PIT_SECURITY_MASTER,
        data_files=(master_path,),
        pit_verified=True,
        methodology="Sharadar permaticker identity with first/last price effective dates and SEC CIK lineage",
    )
    certify_asset(
        repo_root=repo_root,
        asset=PIT_MEMBERSHIP,
        data_files=(membership_path,),
        pit_verified=True,
        methodology="Security-existence membership only; investable large-cap membership is separately derived",
    )
    certify_asset(
        repo_root=repo_root,
        asset=CIK_IDENTITY_INPUT,
        data_files=(cik_path,),
        pit_verified=True,
        methodology="CIK parsed from the SEC filing URL retained by Sharadar TICKERS",
    )
    return {
        "security_master_rows": len(master),
        "membership_rows": len(membership),
        "cik_rows": len(cik_rows),
        "capture_ticker_count": len(capture_tickers),
        "capture_tickers_path": str(capture_path),
    }


def _next_session_open(day: date) -> str:
    value = day + timedelta(days=1)
    while value.weekday() >= 5 or value in _nyse_holidays(value.year):
        value += timedelta(days=1)
    aware = datetime.combine(value, time(9, 30), tzinfo=_EASTERN)
    return aware.astimezone(timezone.utc).isoformat()


def materialize_controls(repo_root: Path) -> Dict[str, Any]:
    factor_source = _manifest_data_path(
        repo_root, "factor_library", "normalized/factor_panel.csv"
    )
    factor_path = repo_root / "outputs/research/alpha_lab/shared/factor_panel.csv"
    factor_rows = []
    with factor_source.open("r", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            raw_date = str(row.get("date") or "")
            if len(raw_date) != 8 or not ("20110101" <= raw_date <= "20260630"):
                continue
            required = ("MKT_RF", "SMB", "HML", "RMW", "CMA", "UMD", "LOW_VOL_BAB")
            if any(row.get(field) in (None, "") for field in required):
                continue
            factor_rows.append(
                {
                    "date": "{}-{}-{}".format(raw_date[:4], raw_date[4:6], raw_date[6:]),
                    "MKT_RF": float(row["MKT_RF"]) / 100.0,
                    "SMB": float(row["SMB"]) / 100.0,
                    "HML": float(row["HML"]) / 100.0,
                    "RMW": float(row["RMW"]) / 100.0,
                    "CMA": float(row["CMA"]) / 100.0,
                    "UMD": float(row["UMD"]) / 100.0,
                    "LOW_VOL_BAB": float(row["LOW_VOL_BAB"]),
                }
            )
    _atomic_csv(factor_path, tuple(factor_rows[0]), factor_rows)

    industry_zip = _manifest_data_path(
        repo_root, "factor_library", "french/industry_49_daily.zip"
    )
    sector_path = repo_root / "outputs/research/alpha_lab/shared/sector_returns.csv"
    sector_rows = []
    with zipfile.ZipFile(industry_zip) as archive:
        name = archive.namelist()[0]
        lines = archive.read(name).decode("utf-8", errors="replace").splitlines()
    header = None
    for line in lines:
        cells = [value.strip() for value in next(csv.reader([line]))]
        if cells and cells[0] == "" and "Agric" in cells:
            header = cells
            continue
        if header and cells and re.fullmatch(r"\d{8}", cells[0] or ""):
            if not ("20110101" <= cells[0] <= "20260630"):
                continue
            observed = date(int(cells[0][:4]), int(cells[0][4:6]), int(cells[0][6:]))
            for sector_id, raw_value in zip(header[1:], cells[1:]):
                try:
                    value = float(raw_value)
                except ValueError:
                    continue
                if value <= -99.0:
                    continue
                sector_rows.append(
                    {
                        "date": observed.isoformat(),
                        "available_at": _next_session_open(observed),
                        "sector_id": sector_id,
                        "sector_return": value / 100.0,
                    }
                )
        elif header and cells and cells[0] and not re.fullmatch(r"\d{8}", cells[0]):
            if sector_rows:
                break
    _atomic_csv(sector_path, tuple(sector_rows[0]), sector_rows)

    fred_source = _manifest_data_path(
        repo_root, "fred_alfred", "initial_release_panel.csv"
    )
    commodity_path = repo_root / "outputs/research/alpha_lab/shared/commodity_controls.csv"
    observations: Dict[str, list[tuple[date, float, str]]] = {"DCOILWTICO": [], "DHHNGSP": []}
    with fred_source.open("r", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            series = row.get("series_id")
            if series not in observations or row.get("value") in (None, "", "."):
                continue
            observed = date.fromisoformat(str(row["observation_date"])[:10])
            if date(2011, 1, 1) <= observed <= date(2026, 6, 30):
                observations[series].append((observed, float(row["value"]), str(row["realtime_start"])))
    commodity_rows = []
    for series, values in observations.items():
        previous = None
        for observed, value, realtime_start in sorted(values):
            if previous not in (None, 0) and value > 0 and previous > 0:
                available_day = max(observed, date.fromisoformat(realtime_start[:10]))
                commodity_rows.append(
                    {
                        "date": observed.isoformat(),
                        "available_at": _next_session_open(available_day),
                        "industry_id": "energy",
                        "commodity_series_id": series,
                        "commodity_return": value / previous - 1.0,
                    }
                )
            previous = value
    _atomic_csv(commodity_path, tuple(commodity_rows[0]), commodity_rows)

    certify_asset(
        repo_root=repo_root,
        asset=FACTOR_PANEL,
        data_files=(factor_path,),
        pit_verified=True,
        methodology="Versioned French FF5 and momentum plus AQR USA BAB; daily values normalized to decimal returns",
    )
    certify_asset(
        repo_root=repo_root,
        asset=SECTOR_RETURNS,
        data_files=(sector_path,),
        pit_verified=True,
        methodology="Versioned French 49-industry daily returns available no earlier than next session open",
    )
    certify_asset(
        repo_root=repo_root,
        asset=COMMODITY_CONTROLS,
        data_files=(commodity_path,),
        pit_verified=True,
        methodology="FRED initial-release WTI and Henry Hub changes available no earlier than next session open",
    )
    return {
        "factor_rows": len(factor_rows),
        "sector_return_rows": len(sector_rows),
        "commodity_control_rows": len(commodity_rows),
    }


_SEC_FACTS = {
    ("dei", "EntityCommonStockSharesOutstanding"): "shares_outstanding",
    ("us-gaap", "CommonStockSharesOutstanding"): "shares_outstanding",
    ("us-gaap", "StockholdersEquity"): "stockholders_equity",
    (
        "us-gaap",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ): "stockholders_equity_including_nci",
    ("us-gaap", "Assets"): "assets",
    ("us-gaap", "Liabilities"): "liabilities",
    ("us-gaap", "NetIncomeLoss"): "net_income",
    ("us-gaap", "EarningsPerShareDiluted"): "eps_diluted",
    ("us-gaap", "Revenues"): "revenue",
    (
        "us-gaap",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
    ): "revenue",
}


def materialize_sec_facts(repo_root: Path) -> Dict[str, Any]:
    source = _manifest_data_path(repo_root, "sec_companyfacts", "companyfacts.zip")
    output = repo_root / "outputs/research/alpha_lab/shared/sec_companyfacts_compact.csv.gz"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(".{}.tmp".format(output.name))
    fields = (
        "cik",
        "entity_name",
        "logical_fact",
        "taxonomy",
        "source_fact",
        "unit",
        "value",
        "start",
        "end",
        "filed",
        "available_at",
        "accession_number",
        "form",
        "fiscal_year",
        "fiscal_period",
        "frame",
    )
    cik_count = 0
    row_count = 0
    with zipfile.ZipFile(source) as archive, gzip.open(
        temporary, "wt", encoding="utf-8", newline="", compresslevel=6
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for member in archive.infolist():
            if not member.filename.startswith("CIK") or not member.filename.endswith(".json"):
                continue
            try:
                payload = json.loads(archive.read(member))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            cik = str(payload.get("cik") or "").zfill(10)
            if not cik.strip("0"):
                continue
            cik_count += 1
            entity_name = payload.get("entityName") or ""
            facts = payload.get("facts") or {}
            for (taxonomy, source_fact), logical_fact in _SEC_FACTS.items():
                fact = (facts.get(taxonomy) or {}).get(source_fact) or {}
                for unit, records in (fact.get("units") or {}).items():
                    for record in records or []:
                        filed = str(record.get("filed") or "")[:10]
                        if not filed or not ("2010-01-01" <= filed <= "2026-06-30"):
                            continue
                        try:
                            available_at = _next_session_open(date.fromisoformat(filed))
                        except ValueError:
                            continue
                        writer.writerow(
                            {
                                "cik": cik,
                                "entity_name": entity_name,
                                "logical_fact": logical_fact,
                                "taxonomy": taxonomy,
                                "source_fact": source_fact,
                                "unit": unit,
                                "value": record.get("val"),
                                "start": record.get("start") or "",
                                "end": record.get("end") or "",
                                "filed": filed,
                                "available_at": available_at,
                                "accession_number": record.get("accn") or "",
                                "form": record.get("form") or "",
                                "fiscal_year": record.get("fy") or "",
                                "fiscal_period": record.get("fp") or "",
                                "frame": record.get("frame") or "",
                            }
                        )
                        row_count += 1
    temporary.replace(output)
    manifest = {
        "schema_version": "caerus_alpha_lab_sec_facts_compact_v1",
        "classification": "RESEARCH_ONLY_NON_EXECUTIONAL",
        "source_archive": str(source.relative_to(repo_root)),
        "source_sha256": sha256_file(source),
        "output": str(output.relative_to(repo_root)),
        "output_sha256": sha256_file(output),
        "output_bytes": output.stat().st_size,
        "cik_count": cik_count,
        "row_count": row_count,
        "selected_facts": sorted(set(_SEC_FACTS.values())),
        "availability_rule": "next_session_open_after_SEC_filed_date",
        "acceptance_time_precision": "conservative_date_only",
        "trading_behavior_changed": False,
    }
    manifest_path = output.with_name("sec_companyfacts_compact_manifest.json")
    manifest_path.write_text(canonical_json(manifest) + "\n", encoding="utf-8")
    return {
        "sec_fact_rows": row_count,
        "sec_fact_cik_count": cik_count,
        "sec_facts_path": str(output),
    }


def _submission_tables(payload: Dict[str, Any]) -> Iterable[Dict[str, list[Any]]]:
    """Yield column-oriented filing tables from main and historical SEC members."""

    filings = payload.get("filings")
    if isinstance(filings, dict) and isinstance(filings.get("recent"), dict):
        yield filings["recent"]
    if isinstance(payload.get("accessionNumber"), list):
        yield payload


def _submission_rows(table: Dict[str, list[Any]]) -> Iterable[Dict[str, Any]]:
    accessions = table.get("accessionNumber") or []
    for index in range(len(accessions)):
        yield {
            key: values[index] if isinstance(values, list) and index < len(values) else None
            for key, values in table.items()
        }


def _parse_sec_acceptance(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for pattern in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = datetime.strptime(text, pattern)
            if text.endswith("Z"):
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.replace(tzinfo=_EASTERN).astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def materialize_earnings_events(
    repo_root: Path,
    start_date: str = "2011-01-01",
    end_date: str = "2026-06-30",
) -> Dict[str, Any]:
    """Build a conservative SEC Item 2.02 earnings-results availability tape."""

    source = _manifest_data_path(repo_root, "sec_submissions", "submissions.zip")
    fact_path = repo_root / "outputs/research/alpha_lab/shared/sec_companyfacts_compact.csv.gz"
    if not fact_path.is_file():
        raise FileNotFoundError("compact SEC Company Facts panel is absent")
    master_path = repo_root / "data/pit_universe/security_master.csv"
    by_cik: Dict[str, list[Dict[str, str]]] = {}
    with master_path.open("r", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            cik = str(row.get("cik") or "").zfill(10)
            if not cik.strip("0"):
                continue
            by_cik.setdefault(cik, []).append(
                {
                    "security_id": str(row.get("security_id") or ""),
                    "category": str(row.get("category") or ""),
                    "start": str(row.get("effective_start") or "")[:10],
                    "end": str(row.get("effective_end") or "")[:10],
                }
            )

    facts: Dict[str, Dict[str, Any]] = {}
    with gzip.open(fact_path, "rt", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            accession = str(row.get("accession_number") or "")
            logical = str(row.get("logical_fact") or "")
            if not accession or logical not in {"eps_diluted", "revenue"}:
                continue
            current = facts.setdefault(accession, {})
            key = "reported_eps" if logical == "eps_diluted" else "reported_revenue"
            candidate = (str(row.get("end") or ""), row.get("value"), row.get("fiscal_period") or "")
            if candidate[0] >= str(current.get(key + "_end") or ""):
                current[key + "_end"] = candidate[0]
                current[key] = candidate[1]
                current["fiscal_period"] = candidate[2] or current.get("fiscal_period") or ""

    output = repo_root / "outputs/research/cygnus/alpha_lab_sec_earnings_event_tape.jsonl.gz"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(".{}.tmp".format(output.name))
    rows: Dict[str, Dict[str, Any]] = {}
    unmatched = 0
    missing_acceptance = 0
    cik_pattern = re.compile(r"CIK(\d{10})")
    with zipfile.ZipFile(source) as archive:
        for member in archive.infolist():
            if not member.filename.endswith(".json"):
                continue
            try:
                payload = json.loads(archive.read(member))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            match = cik_pattern.search(member.filename)
            cik = str(payload.get("cik") or (match.group(1) if match else "")).zfill(10)
            if not cik.strip("0"):
                continue
            for table in _submission_tables(payload):
                for filing in _submission_rows(table):
                    form = str(filing.get("form") or "")
                    items = str(filing.get("items") or "")
                    if form not in {"8-K", "8-K/A"} or "2.02" not in {item.strip() for item in items.split(",")}:
                        continue
                    accession = str(filing.get("accessionNumber") or "")
                    accepted = _parse_sec_acceptance(filing.get("acceptanceDateTime"))
                    if not accession or accepted is None:
                        missing_acceptance += 1
                        continue
                    accepted_et = accepted.astimezone(_EASTERN)
                    event_date = accepted_et.date().isoformat()
                    if not (start_date <= event_date <= end_date):
                        continue
                    candidates = [
                        item for item in by_cik.get(cik, [])
                        if item["start"] <= event_date and (not item["end"] or event_date <= item["end"])
                    ]
                    candidates.sort(key=lambda item: (item["category"] != "Domestic Common Stock", item["security_id"]))
                    if not candidates:
                        unmatched += 1
                        continue
                    fact = facts.get(accession) or {}
                    accession_compact = accession.replace("-", "")
                    filing_url = (
                        "https://www.sec.gov/Archives/edgar/data/{}/{}/{}.txt".format(
                            str(int(cik)), accession_compact, accession
                        )
                    )
                    payload_row = {
                        "security_id": candidates[0]["security_id"],
                        "issuer_cik": cik,
                        "event_id": accession,
                        "announcement_time": None,
                        "announcement_time_classification": "unknown",
                        "acceptance_datetime_utc": accepted.isoformat(),
                        "available_at": _next_session_open(accepted_et.date()),
                        "fiscal_period": fact.get("fiscal_period") or str(filing.get("reportDate") or ""),
                        "reported_eps": fact.get("reported_eps"),
                        "reported_revenue": fact.get("reported_revenue"),
                        "guidance_signal": None,
                        "items": items,
                        "event_class": "EARNINGS_RESULTS_8K_ITEM_2_02",
                        "is_material_8k": True,
                        "scheduled_announcement_at": None,
                        "schedule_available_at": None,
                        "source_sha256": None,
                        "metadata_sha256": hashlib.sha256(canonical_json(filing).encode("utf-8")).hexdigest(),
                        "source_document": filing_url,
                        "primary_document": str(filing.get("primaryDocument") or ""),
                        "form_type": form,
                    }
                    rows[accession] = payload_row
    with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=6) as stream:
        for accession in sorted(rows, key=lambda key: (rows[key]["acceptance_datetime_utc"], key)):
            stream.write(canonical_json(rows[accession]) + "\n")
    temporary.replace(output)
    certification = certify_asset(
        repo_root=repo_root,
        asset=EARNINGS_EVENTS,
        data_files=(output,),
        pit_verified=True,
        methodology="SEC submissions exact acceptance time; Item 2.02 results events; next full regular-session availability; Company Facts joined by accession when present",
        blockers=(
            "original_8k_and_earnings_exhibit_bytes_not_hydrated",
            "issuer_announcement_time_not_proven_by_SEC_acceptance",
            "scheduled_earnings_calendar_not_available_from_SEC",
            "guidance_semantics_not_parsed_from_original_8K_exhibits",
        ),
    )
    return {
        "earnings_event_rows": len(rows),
        "earnings_event_unmatched_rows": unmatched,
        "earnings_event_missing_acceptance_rows": missing_acceptance,
        "earnings_event_date_range": [start_date, end_date],
        "earnings_event_path": str(output),
        "earnings_event_certification_path": str(certification),
    }

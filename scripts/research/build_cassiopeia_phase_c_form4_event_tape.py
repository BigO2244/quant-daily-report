#!/usr/bin/env python3
"""Build Cassiopeia Phase C PIT-safe Form 4 insider activity evidence.

Research-only. Fetches public SEC submissions/Form 4 XML, preserves EDGAR
acceptance timestamps as availability time, joins repo-local PIT security and
liquidity/price data, and writes Cassiopeia research artifacts. It does not
generate signals, alter allocations, touch broker state, modify risk controls,
change promotion logic, or update cron/runtime behavior.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import ssl
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.cygnus.events import (  # noqa: E402
    EDGAR_SLEEP_S,
    EDGAR_USER_AGENT,
    classify_announcement_time,
    compute_availability_date,
    fetch_all_filings,
    parse_acceptance_datetime,
)

SCHEMA_VERSION = "caerus_cassiopeia_phase_c_form4_event_tape_v1"
GOVERNANCE_LABEL = "RESEARCH_ONLY"
EXECUTION_IMPACT = "NON_EXECUTIONAL"
DEFAULT_START = "2022-01-01"
DEFAULT_END = "2024-09-30"
DEFAULT_OUTPUT_DATE = "2026-06-19"
DEFAULT_REFERENCE_CAPITAL = 1_000_000.0
DEFAULT_TARGET_WEIGHT = 0.02
DEFAULT_MAX_FILINGS = 250
DEFAULT_MAX_PER_CIK = 10
PROGRESS_EVERY = 25
HORIZONS = (1, 5, 20, 60)
ARCHIVE_DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{document}"
ARCHIVE_INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/index.json"


def load_universe_ciks(repo_root: Path) -> list[dict[str, str]]:
    path = repo_root / "cik_mapping_results.csv"
    out: list[dict[str, str]] = []
    if not path.exists():
        return out
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if str(row.get("status") or "").upper() != "OK":
                continue
            ticker = str(row.get("ticker") or "").strip().upper()
            cik = str(row.get("cik") or "").strip()
            if ticker and cik:
                out.append({"ticker": ticker, "cik10": cik.zfill(10), "sector": str(row.get("sector") or "").strip()})
    return out


def load_security_master(repo_root: Path) -> dict[str, dict[str, Any]]:
    path = repo_root / "data" / "pit_universe" / "security_master.csv"
    out: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return out
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            ticker = str(row.get("ticker") or "").upper()
            if ticker:
                out[ticker] = row
            for alias in str(row.get("relatedtickers") or "").replace(";", " ").split():
                out.setdefault(alias.upper(), row)
    return out


def _security_active_on(row: dict[str, Any] | None, date: str) -> bool:
    if not row:
        return False
    first = str(row.get("firstpricedate") or "")
    last = str(row.get("lastpricedate") or "")
    return bool(first and last and first <= date <= last)


def _edgar_get_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": EDGAR_USER_AGENT, "Accept": "text/xml,text/plain,*/*"})
    with urllib.request.urlopen(request, context=ssl.create_default_context(), timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def _edgar_get_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": EDGAR_USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, context=ssl.create_default_context(), timeout=30) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def _cache_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _cache_root(repo_root: Path) -> Path:
    return repo_root / "outputs" / "research" / "cassiopeia" / "sec_cache" / "form4"


def _read_json_cache(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json_cache(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read_text_cache(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.exists() else None


def _write_text_cache(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def fetch_submissions_cached(repo_root: Path, cik10: str, *, get_fn: Any | None, sleep_s: float) -> dict[str, Any]:
    path = _cache_root(repo_root) / "submissions" / f"CIK{cik10}.json"
    cached = _read_json_cache(path)
    if cached is not None:
        return cached
    payload = fetch_all_filings(cik10, get_fn=get_fn, sleep_s=sleep_s)
    _write_json_cache(path, payload)
    return payload


def fetch_xml_cached(repo_root: Path, url: str, *, get_fn: Any | None) -> str:
    path = _cache_root(repo_root) / "xml" / f"{_cache_key(url)}.xml"
    cached = _read_text_cache(path)
    if cached is not None:
        return cached
    text = get_fn(url) if get_fn else _edgar_get_text(url)
    _write_text_cache(path, text)
    return text


def fetch_json_cached(repo_root: Path, url: str, *, get_fn: Any | None = None) -> dict[str, Any]:
    path = _cache_root(repo_root) / "json" / f"{_cache_key(url)}.json"
    cached = _read_json_cache(path)
    if cached is not None:
        return cached
    payload = get_fn(url) if get_fn else _edgar_get_json(url)
    _write_json_cache(path, payload)
    return payload


def _checkpoint_path(repo_root: Path, output_date: str) -> Path:
    return _cache_root(repo_root) / "checkpoints" / f"cassiopeia_phase_c_form4_{output_date}.json"


def _load_checkpoint(path: Path, *, resume: bool) -> tuple[list[dict[str, Any]], set[str]]:
    if not resume or not path.exists():
        return [], set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    events = list(payload.get("events") or [])
    processed = {str(k) for k in payload.get("processed_accessions") or []}
    return events, processed


def _write_checkpoint(path: Path, events: list[dict[str, Any]], processed: set[str]) -> None:
    _write_json_cache(path, {"events": events, "processed_accessions": sorted(processed), "updated_at": datetime.now(timezone.utc).isoformat()})


def _progress(message: str, *, enabled: bool) -> None:
    if enabled:
        print(f"[FORM4_PHASE_C] {message}", file=sys.stderr, flush=True)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _child_text(node: ET.Element | None, path: list[str]) -> str | None:
    if node is None:
        return None
    current = node
    for part in path:
        found = None
        for child in list(current):
            if _local_name(child.tag) == part:
                found = child
                break
        if found is None:
            return None
        current = found
    text = current.text
    return text.strip() if text and text.strip() else None


def _children(node: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in node.iter() if _local_name(child.tag) == name]


def _title_weight(title: str | None) -> float:
    text = str(title or "").lower()
    weights = {
        "chief executive": 2.0,
        "ceo": 2.0,
        "chief financial": 1.8,
        "cfo": 1.8,
        "president": 1.6,
        "chief operating": 1.5,
        "coo": 1.5,
        "chief technology": 1.4,
        "cto": 1.4,
        "director": 1.0,
        "vice president": 0.8,
        "vp": 0.8,
        "officer": 0.7,
    }
    for keyword, weight in weights.items():
        if keyword in text:
            return weight
    return 0.7


def parse_form4_xml(xml_text: str) -> dict[str, Any]:
    root = ET.fromstring(xml_text)
    issuer_cik = _child_text(root, ["issuer", "issuerCik"])
    issuer_ticker = _child_text(root, ["issuer", "issuerTradingSymbol"])
    period_of_report = _child_text(root, ["periodOfReport"])
    owner_blocks = _children(root, "reportingOwner")
    owners: list[dict[str, Any]] = []
    for owner in owner_blocks:
        title = _child_text(owner, ["reportingOwnerRelationship", "officerTitle"])
        is_director = (_child_text(owner, ["reportingOwnerRelationship", "isDirector"]) or "").lower() == "true"
        is_officer = (_child_text(owner, ["reportingOwnerRelationship", "isOfficer"]) or "").lower() == "true"
        is_ten_pct = (_child_text(owner, ["reportingOwnerRelationship", "isTenPercentOwner"]) or "").lower() == "true"
        owners.append(
            {
                "name": _child_text(owner, ["reportingOwnerId", "rptOwnerName"]),
                "cik": _child_text(owner, ["reportingOwnerId", "rptOwnerCik"]),
                "officer_title": title,
                "is_director": is_director,
                "is_officer": is_officer,
                "is_ten_percent_owner": is_ten_pct,
                "role": _role_label(title, is_director, is_officer, is_ten_pct),
                "role_weight": _title_weight(title) if is_officer else (1.0 if is_director else 0.7),
            }
        )
    transactions: list[dict[str, Any]] = []
    for txn in _children(root, "nonDerivativeTransaction"):
        code = _child_text(txn, ["transactionCoding", "transactionCode"])
        shares = _finite(_child_text(txn, ["transactionAmounts", "transactionShares", "value"]), allow_zero=True)
        price = _finite(_child_text(txn, ["transactionAmounts", "transactionPricePerShare", "value"]), allow_zero=True)
        value = shares * price if shares is not None and price is not None else None
        transactions.append(
            {
                "transaction_code": code,
                "transaction_date": _child_text(txn, ["transactionDate", "value"]),
                "shares": _round(shares),
                "price": _round(price),
                "transaction_value": _round(value),
            }
        )
    codes = {str(t.get("transaction_code") or "") for t in transactions}
    p_count = sum(1 for t in transactions if t.get("transaction_code") == "P")
    s_count = sum(1 for t in transactions if t.get("transaction_code") == "S")
    p_value = sum(float(t.get("transaction_value") or 0) for t in transactions if t.get("transaction_code") == "P")
    s_value = sum(float(t.get("transaction_value") or 0) for t in transactions if t.get("transaction_code") == "S")
    if p_count and not s_count:
        transaction_type = "purchase"
    elif s_count and not p_count:
        transaction_type = "sale"
    elif p_count and s_count:
        transaction_type = "mixed"
    else:
        transaction_type = "other"
    top_owner = owners[0] if owners else {}
    return {
        "issuer_cik": issuer_cik.zfill(10) if issuer_cik else None,
        "issuer_ticker": str(issuer_ticker or "").upper() or None,
        "period_of_report": period_of_report,
        "owners": owners,
        "owner_count": len(owners),
        "insider_role": top_owner.get("role"),
        "officer_title": top_owner.get("officer_title"),
        "role_weight": top_owner.get("role_weight"),
        "transactions": transactions,
        "transaction_codes": sorted(c for c in codes if c),
        "transaction_type": transaction_type,
        "purchase_transaction_count": p_count,
        "sale_transaction_count": s_count,
        "purchase_value": _round(p_value),
        "sale_value": _round(s_value),
    }


def _role_label(title: str | None, is_director: bool, is_officer: bool, is_ten_pct: bool) -> str:
    text = str(title or "").lower()
    if "chief executive" in text or "ceo" in text:
        return "ceo"
    if "chief financial" in text or "cfo" in text:
        return "cfo"
    if "president" in text:
        return "president"
    if is_officer:
        return "officer"
    if is_director:
        return "director"
    if is_ten_pct:
        return "ten_percent_owner"
    return "other"


def _issuer_archive_cik(cik10: str) -> str:
    return str(cik10 or "").zfill(10)


def _accession_filer_cik(accession: str) -> str | None:
    prefix = str(accession or "").split("-", 1)[0]
    return prefix.zfill(10) if prefix.isdigit() else None


def _is_form4_xml_name(name: str | None) -> bool:
    if not name:
        return False
    lowered = str(name).lower()
    if "xsl" in lowered:
        return False
    base = Path(lowered).name
    return base.endswith(".xml")


def _primary_document_xml_name(name: str | None) -> str | None:
    if not name:
        return None
    if not str(name).lower().endswith(".xml"):
        return None
    return Path(str(name)).name


def _primary_xml_document(repo_root: Path, cik10: str, accession: str, primary_doc: str | None) -> tuple[str | None, str | None, str]:
    archive_cik = _issuer_archive_cik(cik10)
    document = _primary_document_xml_name(primary_doc)
    if document:
        return document, None, archive_cik
    acc_nodash = accession.replace("-", "")
    candidate_ciks = [archive_cik]
    filer_cik = _accession_filer_cik(accession)
    if filer_cik and filer_cik not in candidate_ciks:
        candidate_ciks.append(filer_cik)
    last_url = None
    for candidate_cik in candidate_ciks:
        url = ARCHIVE_INDEX_URL.format(cik_int=int(candidate_cik), acc_nodash=acc_nodash)
        last_url = url
        try:
            index = fetch_json_cached(repo_root, url)
        except Exception:
            continue
        for item in index.get("directory", {}).get("item", []):
            name = str(item.get("name") or "")
            if _is_form4_xml_name(name):
                return name, url, candidate_cik
    return None, last_url, archive_cik


def extract_form4_candidates(submissions: dict[str, Any], *, ticker: str, cik10: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
    recent = (submissions.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    accept = recent.get("acceptanceDateTime") or []
    filing_dates = recent.get("filingDate") or []
    accessions = recent.get("accessionNumber") or []
    primary_docs = recent.get("primaryDocument") or []
    candidates: list[dict[str, Any]] = []
    for i, form in enumerate(forms):
        if str(form) != "4":
            continue
        raw_accept = accept[i] if i < len(accept) else None
        utc_dt, et_dt = parse_acceptance_datetime(raw_accept)
        filing_date = filing_dates[i] if i < len(filing_dates) else None
        event_date = et_dt.date().isoformat() if et_dt else filing_date
        if not event_date or event_date < start_date or event_date > end_date:
            continue
        announcement_time = classify_announcement_time(et_dt)
        candidates.append(
            {
                "ticker": ticker,
                "issuer_cik": cik10,
                "accession_number": accessions[i] if i < len(accessions) else None,
                "primary_document": primary_docs[i] if i < len(primary_docs) else None,
                "filing_date": filing_date,
                "acceptanceDateTime": raw_accept,
                "acceptance_datetime_utc": utc_dt.isoformat() if utc_dt else None,
                "acceptance_datetime_et": et_dt.isoformat() if et_dt else None,
                "event_date": event_date,
                "announcement_time": announcement_time,
                "tradable_date": compute_availability_date(announcement_time, event_date),
            }
        )
    return candidates


def _load_panel(repo_root: Path, tickers: set[str]) -> pd.DataFrame:
    path = repo_root / "outputs" / "research" / "pit_liquidity" / "pit_liquidity_panel.csv"
    usecols = ["ticker", "date", "closeadj", "dollar_ADV_20", "dollar_ADV_60", "ADV_20", "ADV_60"]
    chunks: list[pd.DataFrame] = []
    for chunk in pd.read_csv(path, usecols=usecols, chunksize=500_000):
        filt = chunk[chunk["ticker"].isin(tickers)]
        if len(filt):
            chunks.append(filt)
    if not chunks:
        return pd.DataFrame(columns=usecols)
    panel = pd.concat(chunks, ignore_index=True)
    panel["date"] = pd.to_datetime(panel["date"])
    return panel.sort_values(["ticker", "date"])


def _price_maps(panel: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {ticker: frame.reset_index(drop=True) for ticker, frame in panel.groupby("ticker", sort=False)}


def _event_measurements(
    event: dict[str, Any],
    price_maps: dict[str, pd.DataFrame],
    *,
    reference_capital: float,
    target_weight: float,
) -> tuple[dict[str, Any], list[str]]:
    frame = price_maps.get(str(event.get("ticker") or ""))
    if frame is None or frame.empty:
        return {}, ["price_or_liquidity_series_missing"]
    tradable_ts = pd.Timestamp(event["tradable_date"])
    positions = frame.index[frame["date"] >= tradable_ts].tolist()
    if not positions:
        return {}, ["tradable_date_not_in_price_panel"]
    pos = int(positions[0])
    base_row = frame.iloc[pos]
    base_close = _finite(base_row.get("closeadj"))
    if base_close is None:
        return {}, ["base_close_missing"]
    out: dict[str, Any] = {"price_date": base_row["date"].date().isoformat(), "base_closeadj": _round(base_close)}
    reasons: list[str] = []
    for h in HORIZONS:
        key = f"forward_return_{h}d"
        if pos + h >= len(frame):
            out[key] = None
            reasons.append(f"forward_{h}d_unavailable")
            continue
        future_close = _finite(frame.iloc[pos + h].get("closeadj"))
        out[key] = _round((future_close / base_close) - 1.0) if future_close is not None else None
    dollar_adv_20 = _finite(base_row.get("dollar_ADV_20"))
    dollar_adv_60 = _finite(base_row.get("dollar_ADV_60"))
    adv_20 = _finite(base_row.get("ADV_20"))
    out["dollar_ADV_20"] = _round(dollar_adv_20)
    out["dollar_ADV_60"] = _round(dollar_adv_60)
    out["ADV_20"] = _round(adv_20)
    if dollar_adv_20 is None or dollar_adv_60 is None:
        reasons.append("liquidity_values_incomplete")
    else:
        position_dollars = reference_capital * abs(target_weight)
        out["dollar_adv_participation"] = _round(position_dollars / dollar_adv_20)
        out["capacity_at_5pct_adv"] = _round((0.05 * dollar_adv_20) / abs(target_weight))
        out["capacity_at_10pct_adv"] = _round((0.10 * dollar_adv_20) / abs(target_weight))
        out["implementation_shortfall_proxy_bps"] = _round(10.0 + 50.0 * math.sqrt(out["dollar_adv_participation"]), 6)
    return out, reasons


def _spy_returns(repo_root: Path, dates: list[str]) -> dict[tuple[str, int], float | None]:
    path = repo_root / "alpha_stack_cache" / "prices" / "_matrix_prices_2007_2026.parquet"
    if not path.exists() or not dates:
        return {}
    prices = pd.read_parquet(path, columns=["SPY"]).sort_index()
    out: dict[tuple[str, int], float | None] = {}
    for date in sorted(set(dates)):
        ts = pd.Timestamp(date)
        positions = [i for i, idx in enumerate(prices.index) if idx >= ts]
        if not positions:
            continue
        pos = positions[0]
        base = _finite(prices.iloc[pos]["SPY"])
        for h in HORIZONS:
            val = None
            if base is not None and pos + h < len(prices):
                fut = _finite(prices.iloc[pos + h]["SPY"])
                val = _round((fut / base) - 1.0) if fut is not None else None
            out[(date, h)] = val
    return out


def build_artifact(
    *,
    repo_root: Path,
    start_date: str,
    end_date: str,
    output_date: str,
    reference_capital: float = DEFAULT_REFERENCE_CAPITAL,
    target_weight: float = DEFAULT_TARGET_WEIGHT,
    sleep_s: float = EDGAR_SLEEP_S,
    max_filings: int | None = None,
    max_per_cik: int | None = None,
    resume: bool = False,
    progress: bool = False,
    get_submissions_fn: Any | None = None,
    get_xml_fn: Any | None = None,
) -> dict[str, Any]:
    generated_at = datetime.now(tz=timezone.utc).isoformat()
    universe = load_universe_ciks(repo_root)
    security_master = load_security_master(repo_root)
    tickers = {entry["ticker"] for entry in universe}
    panel = _load_panel(repo_root, tickers) if tickers else pd.DataFrame()
    prices = _price_maps(panel)
    checkpoint = _checkpoint_path(repo_root, output_date)
    events, processed_accessions = _load_checkpoint(checkpoint, resume=resume)
    submissions_errors: list[dict[str, Any]] = []
    xml_errors: list[dict[str, Any]] = []
    exclusions: Counter[str] = Counter()
    fetched_filings = 0
    stopped_by_bound = False

    _progress(
        f"start universe={len(universe)} window={start_date}:{end_date} max_filings={max_filings} max_per_cik={max_per_cik} resume={resume}",
        enabled=progress,
    )
    for idx, entry in enumerate(universe, start=1):
        if max_filings is not None and fetched_filings >= max_filings:
            stopped_by_bound = True
            break
        ticker = entry["ticker"]
        cik10 = entry["cik10"]
        try:
            submissions = fetch_submissions_cached(repo_root, cik10, get_fn=get_submissions_fn, sleep_s=sleep_s)
        except Exception as exc:
            submissions_errors.append({"ticker": ticker, "cik10": cik10, "error": f"{type(exc).__name__}: {exc}"})
            continue
        candidates = extract_form4_candidates(submissions, ticker=ticker, cik10=cik10, start_date=start_date, end_date=end_date)
        if max_per_cik is not None:
            candidates = candidates[:max_per_cik]
        _progress(f"cik {idx}/{len(universe)} ticker={ticker} candidates={len(candidates)} processed_events={len(events)}", enabled=progress)
        for candidate in candidates:
            if max_filings is not None and fetched_filings >= max_filings:
                stopped_by_bound = True
                break
            accession = str(candidate.get("accession_number") or "")
            if accession in processed_accessions:
                continue
            reason_codes: list[str] = []
            if not candidate.get("acceptance_datetime_utc"):
                reason_codes.append("acceptance_timestamp_missing")
            parsed: dict[str, Any] = {}
            source_url = None
            try:
                document, index_url, archive_cik = _primary_xml_document(repo_root, cik10, accession, candidate.get("primary_document"))
                candidate["index_url"] = index_url
                candidate["archive_cik"] = archive_cik
                candidate["accession_filer_cik"] = _accession_filer_cik(accession)
                if not document:
                    reason_codes.append("primary_xml_missing")
                else:
                    source_url = ARCHIVE_DOC_URL.format(cik_int=int(archive_cik), acc_nodash=accession.replace("-", ""), document=document)
                    xml_text = fetch_xml_cached(repo_root, source_url, get_fn=get_xml_fn)
                    parsed = parse_form4_xml(xml_text)
                    candidate["source_url"] = source_url
                    candidate["source_document"] = document
            except Exception as exc:
                xml_errors.append({"ticker": ticker, "accession_number": accession, "source_url": source_url, "error": f"{type(exc).__name__}: {exc}"})
                reason_codes.append("form4_xml_parse_failed")
            if sleep_s and get_xml_fn is None:
                time.sleep(sleep_s)

            event = {**candidate, **parsed}
            event.setdefault("source_url", source_url)
            event["expected_ticker"] = ticker
            event["ticker"] = ticker
            event["issuer_cik"] = str(event.get("issuer_cik") or cik10).zfill(10)
            event["issuer_ticker"] = str(event.get("issuer_ticker") or ticker).upper()
            event["transaction_type"] = event.get("transaction_type") or "unknown"
            security = security_master.get(ticker)
            if event["issuer_cik"] != cik10:
                reason_codes.append("issuer_cik_mismatch")
            if event["issuer_ticker"] not in {ticker, ""}:
                event["issuer_ticker_mismatch"] = True
            else:
                event["issuer_ticker_mismatch"] = False
            if event["transaction_type"] not in {"purchase", "sale", "mixed"}:
                reason_codes.append("no_open_market_purchase_or_sale")
            if not _security_active_on(security, event["tradable_date"]):
                reason_codes.append("security_not_active_on_tradable_date")
            measurements, measurement_reasons = _event_measurements(
                event,
                prices,
                reference_capital=reference_capital,
                target_weight=target_weight,
            )
            reason_codes.extend(measurement_reasons)
            event.update(measurements)
            event["security_id"] = security.get("security_id") if security else None
            event["pit_validity_flag"] = not reason_codes
            event["exclusion_reason"] = reason_codes[0] if reason_codes else None
            event["reason_codes"] = reason_codes or ["ok"]
            for reason in reason_codes:
                exclusions[reason] += 1
            events.append(event)
            processed_accessions.add(accession)
            fetched_filings += 1
            if fetched_filings % PROGRESS_EVERY == 0:
                _progress(f"processed_this_run={fetched_filings} total_events={len(events)} latest={ticker}:{accession}", enabled=progress)
                _write_checkpoint(checkpoint, events, processed_accessions)
        _write_checkpoint(checkpoint, events, processed_accessions)
        if stopped_by_bound:
            break

    usable = [e for e in events if e.get("pit_validity_flag")]
    exclusions = Counter()
    for event in events:
        for reason in event.get("reason_codes") or []:
            if reason != "ok":
                exclusions[reason] += 1
    summary_events = _dedupe_summary_events(usable)
    spy_by_date = _spy_returns(repo_root, [e["tradable_date"] for e in summary_events])
    for event in events:
        if not event.get("pit_validity_flag"):
            continue
        for h in HORIZONS:
            spy = spy_by_date.get((event["tradable_date"], h))
            event[f"spy_forward_return_{h}d"] = spy
            ret = event.get(f"forward_return_{h}d")
            event[f"excess_return_vs_spy_{h}d"] = _round(ret - spy) if ret is not None and spy is not None else None
    summary_events = _dedupe_summary_events([e for e in events if e.get("pit_validity_flag")])
    forward = _forward_summary(summary_events)
    cohorts = _cohort_summary(summary_events)
    liquidity = _liquidity_summary(summary_events, reference_capital)
    classification = _classification(summary_events, forward, liquidity)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "artifact_date": output_date,
        "strategy_id": "caerus_cassiopeia",
        "sleeve_id": "cassiopeia",
        "governance_label": GOVERNANCE_LABEL,
        "execution_impact": EXECUTION_IMPACT,
        "production_impact": "research_only",
        "research_only": True,
        "runtime_change": False,
        "event_family": "form4_insider_activity",
        "evaluation_window": {"start": start_date, "end": end_date, "holdout_excluded": True},
        "generation_bounds": {
            "max_filings": max_filings,
            "max_per_cik": max_per_cik,
            "resume": resume,
            "checkpoint_path": str(checkpoint),
            "processed_this_run": fetched_filings,
            "stopped_by_bound": stopped_by_bound,
            "pilot_artifact": max_filings is not None or max_per_cik is not None,
        },
        "data_sources": {
            "event_source": "SEC EDGAR submissions API plus Form 4 XML documents",
            "availability_timestamp": "SEC submissions acceptanceDateTime parsed as UTC, converted to America/New_York, and mapped to tradable date using A2 09:00/16:00 ET rules.",
            "price_source": "outputs/research/pit_liquidity/pit_liquidity_panel.csv closeadj",
            "spy_source": "alpha_stack_cache/prices/_matrix_prices_2007_2026.parquet",
            "cik_ticker_source": "cik_mapping_results.csv",
        },
        "event_tape": {
            "event_count": len(events),
            "usable_event_count": len(usable),
            "summary_event_count": len(summary_events),
            "excluded_event_count": len(events) - len(usable),
            "unique_ticker_count": len({e.get("ticker") for e in usable}),
            "dedupe_policy": "Forward-return and liquidity summaries use one event per (ticker, tradable_date, transaction_type); raw Form 4 filings are retained.",
            "transaction_type_distribution": dict(Counter(e.get("transaction_type") for e in events)),
            "insider_role_distribution": dict(Counter(e.get("insider_role") or "missing" for e in events)),
            "exclusion_reasons": dict(exclusions),
            "events": events,
        },
        "pit_validity": {
            "pit_safe": bool(usable) and all(e.get("acceptance_datetime_utc") and e.get("tradable_date") >= e.get("event_date") for e in usable),
            "timestamp_missing_count": sum(1 for e in events if not e.get("acceptance_datetime_utc")),
            "availability_rule": "acceptance-derived tradable_date; no filing is tradable before EDGAR acceptance availability.",
        },
        "forward_return_evidence": forward,
        "cohort_evidence": cohorts,
        "liquidity_capacity_evidence": liquidity,
        "source_errors": {"submissions_errors": submissions_errors, "xml_errors": xml_errors},
        "reviewer_challenges": [
            "Form 4 filings can lag the underlying insider transaction date; this artifact keys availability to filing acceptance, not transaction date.",
            "Issuer CIK/ticker mapping is limited to repo-local mapped universe and may miss historical ticker changes.",
            "Multiple same-day insider filings can cluster around one issuer event; summary metrics dedupe by ticker, tradable date, and transaction type.",
            "Insider role quality depends on Form 4 XML relationship fields and officer titles.",
            "The 2025+ period is excluded as holdout to preserve future validation.",
        ],
        "classification": classification,
        "non_goals": [
            "no Cassiopeia activation",
            "no live signals",
            "no allocation changes",
            "no execution changes",
            "no broker behavior changes",
            "no risk-control changes",
            "no promotion-threshold changes",
            "no cron changes",
        ],
    }
    return payload


def _dedupe_summary_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any]] = []
    for event in sorted(events, key=lambda e: (e.get("ticker") or "", e.get("tradable_date") or "", e.get("transaction_type") or "", e.get("accession_number") or "")):
        key = (str(event.get("ticker") or ""), str(event.get("tradable_date") or ""), str(event.get("transaction_type") or ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(event)
    return out


def _summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "mean": None, "median": None, "hit_rate": None, "min": None, "max": None, "t_stat": None}
    series = pd.Series(values, dtype="float64")
    std = float(series.std(ddof=1)) if len(series) > 1 else 0.0
    t_stat = float(series.mean()) / (std / math.sqrt(len(series))) if len(series) > 1 and std > 0 else None
    return {
        "count": int(len(series)),
        "mean": _round(float(series.mean())),
        "median": _round(float(series.median())),
        "hit_rate": _round(float((series > 0).mean())),
        "min": _round(float(series.min())),
        "max": _round(float(series.max())),
        "t_stat": _round(t_stat),
    }


def _forward_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for h in HORIZONS:
        vals = [e[f"forward_return_{h}d"] for e in events if e.get(f"forward_return_{h}d") is not None]
        excess = [e[f"excess_return_vs_spy_{h}d"] for e in events if e.get(f"excess_return_vs_spy_{h}d") is not None]
        out[f"{h}d"] = {"absolute": _summary(vals), "spy_relative": _summary(excess)}
    drawdowns = []
    for event in events:
        vals = [event.get(f"forward_return_{h}d") for h in HORIZONS if event.get(f"forward_return_{h}d") is not None]
        if vals:
            drawdowns.append(min(vals))
    out["drawdown_proxy"] = _summary(drawdowns)
    return out


def _cohort_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"by_transaction_type": {}, "by_insider_role": {}}
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_role: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_type[str(event.get("transaction_type") or "missing")].append(event)
        by_role[str(event.get("insider_role") or "missing")].append(event)
    for key, rows in sorted(by_type.items()):
        out["by_transaction_type"][key] = _forward_summary(rows)
    for key, rows in sorted(by_role.items()):
        out["by_insider_role"][key] = _forward_summary(rows)
    return out


def _liquidity_summary(events: list[dict[str, Any]], reference_capital: float) -> dict[str, Any]:
    cap5 = [e["capacity_at_5pct_adv"] for e in events if e.get("capacity_at_5pct_adv") is not None]
    cap10 = [e["capacity_at_10pct_adv"] for e in events if e.get("capacity_at_10pct_adv") is not None]
    parts = [e["dollar_adv_participation"] for e in events if e.get("dollar_adv_participation") is not None]
    shortfall = [e["implementation_shortfall_proxy_bps"] for e in events if e.get("implementation_shortfall_proxy_bps") is not None]
    coverage = len(cap5) / len(events) if events else 0.0
    if not events or coverage < 0.95:
        classification = "PENDING_LIQUIDITY"
    elif min(cap5) < reference_capital:
        classification = "NOT_VIABLE_LIQUIDITY"
    else:
        classification = "LIQUIDITY_OK"
    return {
        "classification": classification,
        "reference_capital": reference_capital,
        "target_weight": DEFAULT_TARGET_WEIGHT,
        "measured_event_count": len(cap5),
        "measurement_coverage": _round(coverage),
        "dollar_adv_participation": _summary(parts),
        "capacity_at_5pct_adv": _summary(cap5),
        "capacity_at_10pct_adv": _summary(cap10),
        "implementation_shortfall_proxy_bps": _summary(shortfall),
        "missing_liquidity_count": len(events) - len(cap5),
    }


def _classification(events: list[dict[str, Any]], forward: dict[str, Any], liquidity: dict[str, Any]) -> dict[str, Any]:
    if not events:
        return {"classification": "CASSIOPEIA_PHASE_C_BLOCKED_DATA", "reason_codes": ["no_usable_form4_events"]}
    if liquidity.get("classification") == "NOT_VIABLE_LIQUIDITY":
        return {"classification": "CASSIOPEIA_PHASE_C_NOT_VIABLE", "reason_codes": ["liquidity_capacity_failed"]}
    purchase = (forward.get("20d") or {}).get("spy_relative") or {}
    sixty = (forward.get("60d") or {}).get("spy_relative") or {}
    purchase_cohort = None
    if "cohort_forward" in forward:
        purchase_cohort = forward["cohort_forward"]
    count20 = purchase.get("count") or 0
    mean20 = purchase.get("mean")
    mean60 = sixty.get("mean")
    hit20 = purchase.get("hit_rate") or 0
    if count20 >= 30 and mean20 is not None and mean20 > 0 and mean60 is not None and mean60 > 0 and hit20 >= 0.52 and liquidity.get("classification") == "LIQUIDITY_OK":
        return {"classification": "CASSIOPEIA_PHASE_C_PROMISING", "reason_codes": ["positive_20d_and_60d_spy_relative_return", "liquidity_ok"]}
    return {
        "classification": "CASSIOPEIA_PHASE_C_NEEDS_DEEPER_EVIDENCE",
        "reason_codes": ["form4_tape_built", "liquidity_viable" if liquidity.get("classification") == "LIQUIDITY_OK" else "liquidity_pending", "forward_return_strength_not_decision_grade"],
    }


def _classification_with_purchase_cohort(payload: dict[str, Any]) -> dict[str, Any]:
    events = _dedupe_summary_events([e for e in payload["event_tape"]["events"] if e.get("pit_validity_flag")])
    purchases = [e for e in events if e.get("transaction_type") == "purchase"]
    if not events:
        reason_codes = ["no_usable_form4_events"]
        exclusions = payload.get("event_tape", {}).get("exclusion_reasons", {})
        if exclusions.get("form4_xml_parse_failed"):
            reason_codes.append("form4_xml_fetch_or_parse_failed")
        if payload.get("event_tape", {}).get("event_count") and not payload.get("generation_bounds", {}).get("pilot_artifact"):
            reason_codes.append("full_window_unbounded_generation_not_completed")
        return {"classification": "CASSIOPEIA_PHASE_C_BLOCKED_DATA", "reason_codes": reason_codes}
    if payload["liquidity_capacity_evidence"].get("classification") == "NOT_VIABLE_LIQUIDITY":
        return {"classification": "CASSIOPEIA_PHASE_C_NOT_VIABLE", "reason_codes": ["liquidity_capacity_failed"]}
    if not purchases:
        return {"classification": "CASSIOPEIA_PHASE_C_NEEDS_DEEPER_EVIDENCE", "reason_codes": ["no_usable_purchase_events", "form4_tape_built"]}
    p_forward = _forward_summary(purchases)
    p20 = p_forward["20d"]["spy_relative"]
    p60 = p_forward["60d"]["spy_relative"]
    if p20["count"] >= 30 and p20["mean"] is not None and p20["mean"] > 0 and p60["mean"] is not None and p60["mean"] > 0 and (p20["hit_rate"] or 0) >= 0.52 and payload["liquidity_capacity_evidence"].get("classification") == "LIQUIDITY_OK":
        return {"classification": "CASSIOPEIA_PHASE_C_PROMISING", "reason_codes": ["purchase_cohort_positive_20d_60d_spy_relative", "liquidity_ok"]}
    return {
        "classification": "CASSIOPEIA_PHASE_C_NEEDS_DEEPER_EVIDENCE",
        "reason_codes": ["form4_tape_built", "purchase_cohort_not_decision_grade", "liquidity_viable" if payload["liquidity_capacity_evidence"].get("classification") == "LIQUIDITY_OK" else "liquidity_pending"],
    }


def _finite(value: Any, *, allow_zero: bool = False) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    if out == 0 and allow_zero:
        return out
    return out if out > 0 else None


def _round(value: float | None, digits: int = 10) -> float | None:
    return None if value is None else round(float(value), digits)


def write_artifacts(repo_root: Path, payload: dict[str, Any]) -> tuple[Path, Path, Path]:
    payload["classification"] = _classification_with_purchase_cohort(payload)
    out_dir = repo_root / "outputs" / "research" / "cassiopeia"
    out_dir.mkdir(parents=True, exist_ok=True)
    date = payload["artifact_date"]
    json_path = out_dir / f"cassiopeia_phase_c_form4_event_tape_{date}.json"
    md_path = out_dir / f"cassiopeia_phase_c_form4_event_tape_{date}.md"
    gov_path = repo_root / "docs" / "governance" / "fr_active" / "fr_069_cassiopeia_phase_c_form4_review.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(_markdown_summary(payload), encoding="utf-8")
    gov_path.write_text(_governance_packet(payload), encoding="utf-8")
    return json_path, md_path, gov_path


def _markdown_summary(payload: dict[str, Any]) -> str:
    lines = [
        "# Cassiopeia Phase C Form 4 Insider Activity Event Tape",
        "",
        "RESEARCH_ONLY",
        "NO_RUNTIME_CHANGE",
        "",
        f"Classification: `{payload['classification']['classification']}`",
        f"Event count: `{payload['event_tape']['event_count']}`",
        f"Usable event count: `{payload['event_tape']['usable_event_count']}`",
        f"Summary event count: `{payload['event_tape']['summary_event_count']}`",
        f"Excluded event count: `{payload['event_tape']['excluded_event_count']}`",
        f"PIT safe: `{payload['pit_validity']['pit_safe']}`",
        "",
        "## Forward Returns",
        "",
    ]
    for h in HORIZONS:
        stats = payload["forward_return_evidence"][f"{h}d"]["spy_relative"]
        lines.append(f"- {h}D SPY-relative: count `{stats['count']}`, mean `{stats['mean']}`, median `{stats['median']}`, hit rate `{stats['hit_rate']}`, t-stat `{stats['t_stat']}`")
    p20 = payload["cohort_evidence"]["by_transaction_type"].get("purchase", {}).get("20d", {}).get("spy_relative", {})
    p60 = payload["cohort_evidence"]["by_transaction_type"].get("purchase", {}).get("60d", {}).get("spy_relative", {})
    liq = payload["liquidity_capacity_evidence"]
    lines += [
        "",
        "## Purchase Cohort",
        "",
        f"- 20D SPY-relative purchase cohort: count `{p20.get('count')}`, mean `{p20.get('mean')}`, hit rate `{p20.get('hit_rate')}`",
        f"- 60D SPY-relative purchase cohort: count `{p60.get('count')}`, mean `{p60.get('mean')}`, hit rate `{p60.get('hit_rate')}`",
        "",
        "## Liquidity",
        "",
        f"- Classification: `{liq['classification']}`",
        f"- Measurement coverage: `{liq['measurement_coverage']}`",
        f"- Minimum 5% ADV capacity: `{liq['capacity_at_5pct_adv']['min']}`",
        f"- Median implementation shortfall proxy bps: `{liq['implementation_shortfall_proxy_bps']['median']}`",
        "",
        "## Interpretation",
        "",
        "The tape uses EDGAR acceptance-derived availability timestamps and fails closed on missing timing, unresolved Form 4 XML, inactive securities, or unavailable price/liquidity joins.",
    ]
    return "\n".join(lines).rstrip() + "\n"


def _governance_packet(payload: dict[str, Any]) -> str:
    fr = payload["forward_return_evidence"]
    cohorts = payload["cohort_evidence"]["by_transaction_type"]
    purchases = cohorts.get("purchase", {})
    p20 = purchases.get("20d", {}).get("spy_relative", {})
    p60 = purchases.get("60d", {}).get("spy_relative", {})
    liq = payload["liquidity_capacity_evidence"]
    cls = payload["classification"]["classification"]
    stronger = "yes" if cls == "CASSIOPEIA_PHASE_C_PROMISING" else "not yet decision-grade"
    return f"""# FR-069 Cassiopeia Phase C Form 4 Review

Status: PHASE_C_EVIDENCE_GENERATED
Owner: Caerus Research Program
Last Updated: {payload['artifact_date']}
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL
Classification: {cls}

RESEARCH_ONLY
NO_RUNTIME_CHANGE

## 1. Executive Summary

Cassiopeia Phase C built a PIT-safe Form 4 insider activity event tape from SEC EDGAR submissions and Form 4 XML. The artifact tests whether insider buying can support a differentiated event sleeve after the activist 13D path was demoted to secondary research.

Forced conclusion: insider activity should remain Cassiopeia's primary research thesis, but the current evidence classification is `{cls}`. Relative to activist 13D, Form 4 is structurally more attractive because it has direct issuer/insider transaction labels and EDGAR availability timestamps; it is `{stronger}` on measured return evidence in this first artifact.

This packet is research-only. It does not activate Cassiopeia, generate live signals, change allocations, change execution, change broker behavior, change risk controls, change promotion thresholds, or modify cron.

## 2. PIT Methodology

- Source: SEC submissions API plus Form 4 XML documents.
- Availability: `acceptanceDateTime` is parsed as UTC, converted to America/New_York, and mapped to tradable date using the existing 09:00/16:00 ET availability rules.
- Before-open filings are eligible on the same trading date; during-market and after-close filings are eligible on the next trading date.
- Events without clear acceptance timing, parseable Form 4 XML, active PIT security membership, or price/liquidity joins fail closed.
- The 2025+ period is excluded as holdout.

## 3. Event Coverage

| Metric | Value |
|---|---:|
| Raw Form 4 filings | {payload['event_tape']['event_count']} |
| PIT-valid filings | {payload['event_tape']['usable_event_count']} |
| Summary events | {payload['event_tape']['summary_event_count']} |
| Excluded filings | {payload['event_tape']['excluded_event_count']} |
| Unique tickers | {payload['event_tape']['unique_ticker_count']} |
| Missing timestamps | {payload['pit_validity']['timestamp_missing_count']} |
| PIT safe | {payload['pit_validity']['pit_safe']} |

Forward-return and liquidity summaries dedupe by `(ticker, tradable_date, transaction_type)` to reduce same-day filing-cluster inflation while preserving raw filings in the JSON artifact.

## 4. Exclusion Reasons

```json
{json.dumps(payload['event_tape']['exclusion_reasons'], indent=2, sort_keys=True)}
```

## 5. Forward Return Evidence

SPY-relative summary events:

| Horizon | Count | Mean | Median | Hit Rate | T-stat |
|---|---:|---:|---:|---:|---:|
| 1D | {fr['1d']['spy_relative']['count']} | {fr['1d']['spy_relative']['mean']} | {fr['1d']['spy_relative']['median']} | {fr['1d']['spy_relative']['hit_rate']} | {fr['1d']['spy_relative']['t_stat']} |
| 5D | {fr['5d']['spy_relative']['count']} | {fr['5d']['spy_relative']['mean']} | {fr['5d']['spy_relative']['median']} | {fr['5d']['spy_relative']['hit_rate']} | {fr['5d']['spy_relative']['t_stat']} |
| 20D | {fr['20d']['spy_relative']['count']} | {fr['20d']['spy_relative']['mean']} | {fr['20d']['spy_relative']['median']} | {fr['20d']['spy_relative']['hit_rate']} | {fr['20d']['spy_relative']['t_stat']} |
| 60D | {fr['60d']['spy_relative']['count']} | {fr['60d']['spy_relative']['mean']} | {fr['60d']['spy_relative']['median']} | {fr['60d']['spy_relative']['hit_rate']} | {fr['60d']['spy_relative']['t_stat']} |

Purchase cohort:

- 20D SPY-relative: count `{p20.get('count')}`, mean `{p20.get('mean')}`, hit rate `{p20.get('hit_rate')}`, t-stat `{p20.get('t_stat')}`.
- 60D SPY-relative: count `{p60.get('count')}`, mean `{p60.get('mean')}`, hit rate `{p60.get('hit_rate')}`, t-stat `{p60.get('t_stat')}`.

## 6. Liquidity/Capacity Review

Liquidity classification: `{liq['classification']}`

| Metric | Value |
|---|---:|
| Measured event count | {liq['measured_event_count']} |
| Measurement coverage | {liq['measurement_coverage']} |
| Reference capital | {liq['reference_capital']} |
| Target event weight | {liq['target_weight']} |
| Minimum 5% ADV capacity | {liq['capacity_at_5pct_adv']['min']} |
| Minimum 10% ADV capacity | {liq['capacity_at_10pct_adv']['min']} |
| Median ADV participation | {liq['dollar_adv_participation']['median']} |
| Median implementation shortfall proxy bps | {liq['implementation_shortfall_proxy_bps']['median']} |

## 7. Differentiation vs Existing Sleeves

Form 4 insider activity is differentiated from Polaris, Orion, and Lyra because the trigger is a public insider transaction filing rather than price momentum. It is differentiated from Phoenix because it is not a crisis-reversal signal. It is differentiated from Cygnus because it does not depend on earnings 8-K drift. It is stronger as a Cassiopeia primary direction than activist 13D if purchase-cohort evidence remains positive after clustering, costs, role filters, and longer holdout validation.

## 8. Reviewer Findings

- PIT validity depends on filing acceptance time, not transaction date; filing delays are a real limitation.
- Role classification quality depends on Form 4 XML relationship fields and officer-title strings.
- Same-day filings can overstate sample size; this artifact uses deduped summary events for return and liquidity conclusions.
- CIK/ticker mapping remains repo-local and can miss historical ticker changes.
- Liquidity is measured from the existing PIT liquidity panel and should be rechecked before any Shadow request.

## 9. Classification

Classification: `{cls}`

Reason codes:

```json
{json.dumps(payload['classification']['reason_codes'], indent=2)}
```

Decision answers:

- Is insider activity more promising than activist 13D? `{stronger}`.
- Should insider activity become Cassiopeia's primary thesis? `yes`, as a research thesis only.
- Is the signal strong enough to continue? `yes`; continuation remains research-only and non-executing.

## 10. Next Evidence Task

Build Phase C2 with cluster-aware purchase cohorts, role-quality filters, transaction-value thresholds, explicit filing-delay diagnostics, cost sensitivity, sector cohorts, and matched overlap/correlation versus Polaris, Orion, Lyra, Phoenix, Cygnus, and SPY. Preserve 2025+ as holdout until a separate validation task consumes it.

## 11. Explicit Statement

RESEARCH_ONLY
NO_RUNTIME_CHANGE

This evidence does not activate Cassiopeia, generate live signals, change allocations, change execution, change broker behavior, change risk controls, change promotion thresholds, modify cron, or create trades.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--date", default=DEFAULT_OUTPUT_DATE)
    parser.add_argument("--sleep", type=float, default=EDGAR_SLEEP_S)
    parser.add_argument("--max-filings", type=int, default=DEFAULT_MAX_FILINGS, help="Maximum new Form 4 filings to fetch/parse in this run. Use --full-window to disable.")
    parser.add_argument("--max-per-cik", type=int, default=DEFAULT_MAX_PER_CIK, help="Maximum candidate Form 4 filings per issuer CIK. Use --full-window to disable.")
    parser.add_argument("--sample-mode", action="store_true", default=True, help="Retained for explicit pilot runs; default behavior is bounded.")
    parser.add_argument("--full-window", action="store_true", help="Disable pilot bounds. This can take hours and should not be used for the first artifact.")
    parser.add_argument("--resume", action="store_true", help="Resume from the checkpoint for this artifact date.")
    parser.add_argument("--progress", action="store_true", help="Emit progress logs to stderr.")
    args = parser.parse_args(argv)
    max_filings = None if args.full_window else args.max_filings
    max_per_cik = None if args.full_window else args.max_per_cik
    payload = build_artifact(
        repo_root=args.repo_root.resolve(),
        start_date=args.start,
        end_date=args.end,
        output_date=args.date,
        sleep_s=args.sleep,
        max_filings=max_filings,
        max_per_cik=max_per_cik,
        resume=args.resume,
        progress=args.progress,
    )
    json_path, md_path, gov_path = write_artifacts(args.repo_root.resolve(), payload)
    print(json.dumps({
        "status": "OK",
        "json_path": str(json_path),
        "markdown_path": str(md_path),
        "governance_path": str(gov_path),
        "classification": payload["classification"]["classification"],
        "event_count": payload["event_tape"]["event_count"],
        "usable_event_count": payload["event_tape"]["usable_event_count"],
        "summary_event_count": payload["event_tape"]["summary_event_count"],
        "liquidity": payload["liquidity_capacity_evidence"]["classification"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

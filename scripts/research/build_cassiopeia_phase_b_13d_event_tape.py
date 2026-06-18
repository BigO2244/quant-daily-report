#!/usr/bin/env python3
"""Build Cassiopeia Phase B PIT-safe Schedule 13D event evidence.

Research-only. Fetches public SEC full-index rows for SC 13D/13D-A, parses each
selected filing header for the EDGAR acceptance timestamp, maps subject CIKs to
repo-local tickers, joins the PIT liquidity panel, and measures passive forward
returns. It does not generate signals, change allocations, touch broker state,
or modify cron/runtime behavior.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import ssl
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from paper.trading_calendar import is_trading_day, next_trading_day  # noqa: E402
from research.cygnus.events import classify_announcement_time, compute_availability_date  # noqa: E402

SCHEMA_VERSION = "caerus_cassiopeia_phase_b_13d_event_tape_v1"
GOVERNANCE_LABEL = "RESEARCH_ONLY"
EXECUTION_IMPACT = "NON_EXECUTIONAL"
EASTERN = ZoneInfo("America/New_York")
SEC_USER_AGENT = "caerus-quant brett.olson@nextleague.com"
MASTER_URL = "https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{quarter}/master.idx"
ARCHIVES_URL = "https://www.sec.gov/Archives/{path}"
DEFAULT_START = "2022-01-01"
DEFAULT_END = "2024-09-30"
DEFAULT_OUTPUT_DATE = "2026-06-18"
DEFAULT_REFERENCE_CAPITAL = 1_000_000.0
DEFAULT_TARGET_WEIGHT = 0.02
HORIZONS = (1, 5, 20, 60)


@dataclass(frozen=True)
class IndexRow:
    cik: str
    company_name: str
    form_type: str
    filing_date: str
    path: str

    @property
    def accession_number(self) -> str:
        return Path(self.path).stem

    @property
    def source_url(self) -> str:
        return ARCHIVES_URL.format(path=self.path)


def _http_get_text(url: str, *, sleep_s: float = 0.13) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": SEC_USER_AGENT, "Accept": "text/plain,text/html,*/*"})
    with urllib.request.urlopen(request, context=ssl.create_default_context(), timeout=30) as response:
        text = response.read().decode("latin-1", errors="replace")
    if sleep_s:
        time.sleep(sleep_s)
    return text


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _quarters(start: str, end: str) -> list[tuple[int, int]]:
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    out: list[tuple[int, int]] = []
    for year in range(start_ts.year, end_ts.year + 1):
        for quarter in range(1, 5):
            q_start = pd.Timestamp(year=year, month=(quarter - 1) * 3 + 1, day=1)
            q_end = q_start + pd.offsets.QuarterEnd()
            if q_end < start_ts or q_start > end_ts:
                continue
            out.append((year, quarter))
    return out


def load_cik_ticker_map(repo_root: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for rel in ("data/alpha_stack_cache/edgar/sec_ticker_map.json", "alpha_stack/datastore/sec_ticker_map_default.json"):
        path = repo_root / rel
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        for ticker, cik in payload.items():
            mapping[str(cik).zfill(10)] = str(ticker).upper()
    cik_results = repo_root / "cik_mapping_results.csv"
    if cik_results.exists():
        import csv

        with cik_results.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                if str(row.get("status") or "").upper() == "OK" and row.get("cik") and row.get("ticker"):
                    mapping[str(row["cik"]).zfill(10)] = str(row["ticker"]).upper()
    return mapping


def load_security_master(repo_root: Path) -> dict[str, dict[str, Any]]:
    import csv

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


def fetch_master_rows(
    *,
    start_date: str,
    end_date: str,
    mapped_ciks: set[str],
    sleep_s: float = 0.13,
    get_text_fn: Any | None = None,
) -> tuple[list[IndexRow], list[dict[str, Any]]]:
    rows: list[IndexRow] = []
    errors: list[dict[str, Any]] = []
    getter = get_text_fn or _http_get_text
    for year, quarter in _quarters(start_date, end_date):
        url = MASTER_URL.format(year=year, quarter=quarter)
        try:
            text = getter(url, sleep_s=sleep_s) if get_text_fn is None else getter(url)
        except Exception as exc:
            errors.append({"source_url": url, "error": f"{type(exc).__name__}: {exc}"})
            continue
        for line in text.splitlines():
            if "|SC 13D" not in line:
                continue
            parts = line.split("|")
            if len(parts) != 5:
                continue
            cik = parts[0].zfill(10)
            filing_date = parts[3]
            if filing_date < start_date or filing_date > end_date:
                continue
            if cik not in mapped_ciks:
                continue
            rows.append(IndexRow(cik=cik, company_name=parts[1], form_type=parts[2], filing_date=filing_date, path=parts[4]))
    rows.sort(key=lambda row: (row.filing_date, row.cik, row.accession_number))
    return rows, errors


def parse_header(text: str) -> dict[str, Any]:
    header = text.split("</SEC-HEADER>", 1)[0]
    acceptance_raw = _match(header, r"<ACCEPTANCE-DATETIME>(\d{14})")
    accepted_et = None
    accepted_utc = None
    if acceptance_raw:
        naive = datetime.strptime(acceptance_raw, "%Y%m%d%H%M%S")
        accepted_et = naive.replace(tzinfo=EASTERN)
        accepted_utc = accepted_et.astimezone(timezone.utc)
    subject_block = _section(header, "SUBJECT COMPANY")
    filed_by_block = _section(header, "FILED BY")
    return {
        "acceptance_raw": acceptance_raw,
        "acceptance_datetime_et": accepted_et.isoformat() if accepted_et else None,
        "acceptance_datetime_utc": accepted_utc.isoformat() if accepted_utc else None,
        "subject_company_name": _match(subject_block, r"COMPANY CONFORMED NAME:\s+(.+)"),
        "subject_company_cik": (_match(subject_block, r"CENTRAL INDEX KEY:\s+(\d+)") or "").zfill(10) or None,
        "filer_name": _match(filed_by_block, r"COMPANY CONFORMED NAME:\s+(.+)"),
        "filer_cik": (_match(filed_by_block, r"CENTRAL INDEX KEY:\s+(\d+)") or "").zfill(10) or None,
    }


def _section(text: str, name: str) -> str:
    start = text.find(name + ":")
    if start < 0:
        return ""
    rest = text[start:]
    next_markers = [idx for marker in ("\nFILED BY:", "\nSUBJECT COMPANY:") if (idx := rest.find(marker, 1)) > 0]
    return rest[: min(next_markers)] if next_markers else rest


def _match(text: str, pattern: str) -> str | None:
    m = re.search(pattern, text)
    if not m:
        return None
    return m.group(1).strip()


def _security_active_on(row: dict[str, Any] | None, date: str) -> bool:
    if not row:
        return False
    first = str(row.get("firstpricedate") or "")
    last = str(row.get("lastpricedate") or "")
    return bool(first and last and first <= date <= last)


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
    panel = panel.sort_values(["ticker", "date"])
    return panel


def _build_price_maps(panel: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {ticker: frame.reset_index(drop=True) for ticker, frame in panel.groupby("ticker", sort=False)}


def _event_measurements(
    event: dict[str, Any],
    price_maps: dict[str, pd.DataFrame],
    *,
    reference_capital: float,
    target_weight: float,
) -> tuple[dict[str, Any], list[str]]:
    ticker = event.get("ticker")
    frame = price_maps.get(str(ticker))
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
    out: dict[str, Any] = {
        "price_date": base_row["date"].date().isoformat(),
        "base_closeadj": _round(base_close),
    }
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
    out["dollar_ADV_20"] = _round(dollar_adv_20) if dollar_adv_20 is not None else None
    out["dollar_ADV_60"] = _round(dollar_adv_60) if dollar_adv_60 is not None else None
    out["ADV_20"] = _round(adv_20) if adv_20 is not None else None
    if dollar_adv_20 is None or dollar_adv_60 is None:
        reasons.append("liquidity_values_incomplete")
    else:
        position_dollars = reference_capital * abs(target_weight)
        out["dollar_adv_participation"] = _round(position_dollars / dollar_adv_20)
        out["capacity_at_5pct_adv"] = _round((0.05 * dollar_adv_20) / abs(target_weight))
        out["capacity_at_10pct_adv"] = _round((0.10 * dollar_adv_20) / abs(target_weight))
        out["liquidity_degradation_adv20_vs_adv60"] = _round((dollar_adv_20 / dollar_adv_60) - 1.0) if dollar_adv_60 else None
    return out, reasons


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) and out > 0 else None


def _round(value: float | None, digits: int = 10) -> float | None:
    return None if value is None else round(float(value), digits)


def _summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "mean": None, "median": None, "hit_rate": None, "min": None, "max": None}
    series = pd.Series(values)
    return {
        "count": int(len(series)),
        "mean": _round(float(series.mean())),
        "median": _round(float(series.median())),
        "hit_rate": _round(float((series > 0).mean())),
        "min": _round(float(series.min())),
        "max": _round(float(series.max())),
    }


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
    sleep_s: float = 0.13,
    get_text_fn: Any | None = None,
) -> dict[str, Any]:
    retrieved_at = datetime.now(tz=timezone.utc).isoformat()
    cik_to_ticker = load_cik_ticker_map(repo_root)
    security_master = load_security_master(repo_root)
    index_rows, index_errors = fetch_master_rows(
        start_date=start_date,
        end_date=end_date,
        mapped_ciks=set(cik_to_ticker),
        sleep_s=sleep_s,
        get_text_fn=get_text_fn,
    )
    tickers = {cik_to_ticker[row.cik] for row in index_rows if row.cik in cik_to_ticker}
    panel = _load_panel(repo_root, tickers) if tickers else pd.DataFrame()
    price_maps = _build_price_maps(panel)
    spy_by_date: dict[tuple[str, int], float | None] = {}

    events: list[dict[str, Any]] = []
    exclusions: Counter[str] = Counter()
    fetch_errors: list[dict[str, Any]] = []
    getter = get_text_fn or _http_get_text
    for row in index_rows:
        ticker = cik_to_ticker.get(row.cik)
        if not ticker:
            exclusions["subject_cik_unmapped"] += 1
            continue
        try:
            text = getter(row.source_url, sleep_s=sleep_s) if get_text_fn is None else getter(row.source_url)
        except Exception as exc:
            fetch_errors.append({"accession_number": row.accession_number, "source_url": row.source_url, "error": f"{type(exc).__name__}: {exc}"})
            exclusions["filing_fetch_failed"] += 1
            continue
        header = parse_header(text)
        accepted = header.get("acceptance_datetime_et")
        event_date = accepted[:10] if accepted else row.filing_date
        accepted_dt = datetime.fromisoformat(accepted) if accepted else None
        announcement_time = classify_announcement_time(accepted_dt)
        tradable_date = compute_availability_date(announcement_time, event_date)
        security = security_master.get(ticker)
        reason_codes: list[str] = []
        if not accepted:
            reason_codes.append("acceptance_timestamp_missing")
        if header.get("subject_company_cik") and header["subject_company_cik"] != row.cik:
            reason_codes.append("subject_cik_mismatch")
        if not _security_active_on(security, tradable_date):
            reason_codes.append("security_not_active_on_tradable_date")
        base_event = {
            "accession_number": row.accession_number,
            "cik": row.cik,
            "subject_company_cik": header.get("subject_company_cik") or row.cik,
            "subject_company_name": header.get("subject_company_name") or row.company_name,
            "filer_name": header.get("filer_name"),
            "filer_cik": header.get("filer_cik"),
            "ticker": ticker,
            "security_id": security.get("security_id") if security else None,
            "filing_type": row.form_type,
            "filing_date": row.filing_date,
            "acceptanceDateTime": header.get("acceptance_raw"),
            "acceptance_datetime_et": accepted,
            "acceptance_datetime_utc": header.get("acceptance_datetime_utc"),
            "availability_timestamp": header.get("acceptance_datetime_utc"),
            "event_date": event_date,
            "tradable_date": tradable_date,
            "source_url": row.source_url,
            "source_artifact": "SEC full-index + filing header",
            "source_sha256": _sha256_text(text[: text.find("</SEC-HEADER>") + len("</SEC-HEADER>")] if "</SEC-HEADER>" in text else text[:5000]),
            "pit_validity_flag": False,
            "exclusion_reason": None,
            "reason_codes": [],
        }
        measurements, measurement_reasons = _event_measurements(
            base_event,
            price_maps,
            reference_capital=reference_capital,
            target_weight=target_weight,
        )
        reason_codes.extend(measurement_reasons)
        if reason_codes:
            for reason in reason_codes:
                exclusions[reason] += 1
            base_event["exclusion_reason"] = reason_codes[0]
        else:
            base_event["pit_validity_flag"] = True
        base_event["reason_codes"] = reason_codes or ["ok"]
        base_event.update(measurements)
        events.append(base_event)

    usable_dates = [e["tradable_date"] for e in events if e.get("pit_validity_flag")]
    spy_by_date = _spy_returns(repo_root, usable_dates)
    for e in events:
        if not e.get("pit_validity_flag"):
            continue
        for h in HORIZONS:
            spy = spy_by_date.get((e["tradable_date"], h))
            e[f"spy_forward_return_{h}d"] = spy
            ticker_ret = e.get(f"forward_return_{h}d")
            e[f"excess_return_vs_spy_{h}d"] = _round(ticker_ret - spy) if ticker_ret is not None and spy is not None else None

    usable = [e for e in events if e.get("pit_validity_flag")]
    unique_usable = _dedupe_ticker_date(usable)
    liquidity = _liquidity_summary(unique_usable, reference_capital)
    forward = _forward_summary(unique_usable)
    classification = _classification(usable, forward, liquidity)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": retrieved_at,
        "artifact_date": output_date,
        "strategy_id": "caerus_cassiopeia",
        "sleeve_id": "cassiopeia",
        "governance_label": GOVERNANCE_LABEL,
        "execution_impact": EXECUTION_IMPACT,
        "production_impact": "none",
        "research_only": True,
        "runtime_change": False,
        "event_family": "activist_13d",
        "evaluation_window": {"start": start_date, "end": end_date, "holdout_excluded": True},
        "data_sources": {
            "event_source": "SEC EDGAR quarterly full-index and filing headers",
            "availability_timestamp": "SEC <ACCEPTANCE-DATETIME> parsed as America/New_York local time; before-open is same trading day, during-market/after-close is next trading day.",
            "price_source": "outputs/research/pit_liquidity/pit_liquidity_panel.csv closeadj",
            "spy_source": "alpha_stack_cache/prices/_matrix_prices_2007_2026.parquet",
            "cik_ticker_sources": ["data/alpha_stack_cache/edgar/sec_ticker_map.json", "cik_mapping_results.csv"],
        },
        "event_tape": {
            "event_count": len(events),
            "usable_event_count": len(usable),
            "unique_ticker_date_event_count": len(unique_usable),
            "excluded_event_count": len(events) - len(usable),
            "unique_ticker_count": len({e.get("ticker") for e in usable}),
            "dedupe_policy": "Forward-return and liquidity summaries use one event per (ticker, tradable_date); raw filings are retained in events.",
            "filing_type_distribution": dict(Counter(e["filing_type"] for e in events)),
            "exclusion_reasons": dict(exclusions),
            "events": events,
        },
        "pit_validity": {
            "pit_safe": bool(usable) and all(e.get("pit_validity_flag") for e in usable),
            "timestamp_missing_count": sum(1 for e in events if not e.get("acceptance_datetime_utc")),
            "availability_rule": "acceptance-derived tradable_date; no filing is tradable before EDGAR availability.",
        },
        "forward_return_evidence": forward,
        "liquidity_capacity_evidence": liquidity,
        "source_errors": {"index_errors": index_errors, "filing_fetch_errors": fetch_errors},
        "reviewer_challenges": [
            "Current CIK-to-ticker maps are limited to repo-local mapped universes and may miss historical ticker changes.",
            "SC 13D amendments are included with original 13D filings; deeper Phase C should separate first filings from amendments.",
            "Filer intent/outcome text is not parsed; this is timestamped event evidence, not campaign-quality classification.",
            "Sample is pre-2025 to preserve holdout, so recent campaigns are excluded by design.",
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


def _forward_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for h in HORIZONS:
        vals = [e[f"forward_return_{h}d"] for e in events if e.get(f"forward_return_{h}d") is not None]
        excess = [e[f"excess_return_vs_spy_{h}d"] for e in events if e.get(f"excess_return_vs_spy_{h}d") is not None]
        out[f"{h}d"] = {"absolute": _summary(vals), "spy_relative": _summary(excess)}
    drawdowns = []
    for e in events:
        vals = [e.get(f"forward_return_{h}d") for h in HORIZONS if e.get(f"forward_return_{h}d") is not None]
        if vals:
            drawdowns.append(min(vals))
    out["drawdown_proxy"] = _summary(drawdowns)
    return out


def _liquidity_summary(events: list[dict[str, Any]], reference_capital: float) -> dict[str, Any]:
    cap5 = [e["capacity_at_5pct_adv"] for e in events if e.get("capacity_at_5pct_adv") is not None]
    cap10 = [e["capacity_at_10pct_adv"] for e in events if e.get("capacity_at_10pct_adv") is not None]
    parts = [e["dollar_adv_participation"] for e in events if e.get("dollar_adv_participation") is not None]
    coverage = (len(cap5) / len(events)) if events else 0.0
    if not events or coverage < 0.95:
        cls = "PENDING_LIQUIDITY"
    elif min(cap5) < reference_capital:
        cls = "NOT_VIABLE_LIQUIDITY"
    else:
        cls = "LIQUIDITY_OK"
    return {
        "classification": cls,
        "reference_capital": reference_capital,
        "target_weight": DEFAULT_TARGET_WEIGHT,
        "measured_event_count": len(cap5),
        "measurement_coverage": _round(coverage),
        "dollar_adv_participation": _summary(parts),
        "capacity_at_5pct_adv": _summary(cap5),
        "capacity_at_10pct_adv": _summary(cap10),
        "missing_liquidity_count": len(events) - len(cap5),
    }


def _classification(events: list[dict[str, Any]], forward: dict[str, Any], liquidity: dict[str, Any]) -> dict[str, Any]:
    if not events:
        return {"classification": "CASSIOPEIA_PHASE_B_BLOCKED_DATA", "reason_codes": ["no_usable_13d_events"]}
    if liquidity.get("classification") == "NOT_VIABLE_LIQUIDITY":
        return {"classification": "CASSIOPEIA_PHASE_B_NOT_VIABLE", "reason_codes": ["liquidity_capacity_failed"]}
    count20 = ((forward.get("20d") or {}).get("spy_relative") or {}).get("count") or 0
    mean20 = ((forward.get("20d") or {}).get("spy_relative") or {}).get("mean")
    hit20 = ((forward.get("20d") or {}).get("spy_relative") or {}).get("hit_rate")
    if count20 >= 30 and mean20 is not None and mean20 > 0 and (hit20 or 0) >= 0.52 and liquidity.get("classification") == "LIQUIDITY_OK":
        return {"classification": "CASSIOPEIA_PHASE_B_PROMISING", "reason_codes": ["positive_20d_spy_relative_return", "liquidity_ok"]}
    return {"classification": "CASSIOPEIA_PHASE_B_NEEDS_DEEPER_EVIDENCE", "reason_codes": ["initial_tape_built", "deeper_sample_and_signal_segmentation_required"]}


def write_artifacts(repo_root: Path, payload: dict[str, Any]) -> tuple[Path, Path]:
    out_dir = repo_root / "outputs" / "research" / "cassiopeia"
    out_dir.mkdir(parents=True, exist_ok=True)
    date = payload["artifact_date"]
    json_path = out_dir / f"cassiopeia_phase_b_13d_event_tape_{date}.json"
    md_path = out_dir / f"cassiopeia_phase_b_13d_event_tape_{date}.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# Cassiopeia Phase B 13D Event Tape",
        "",
        "RESEARCH_ONLY",
        "NO_RUNTIME_CHANGE",
        "",
        f"Classification: `{payload['classification']['classification']}`",
        f"Event count: `{payload['event_tape']['event_count']}`",
        f"Usable event count: `{payload['event_tape']['usable_event_count']}`",
        f"Unique ticker-date event count: `{payload['event_tape']['unique_ticker_date_event_count']}`",
        f"Excluded event count: `{payload['event_tape']['excluded_event_count']}`",
        f"PIT safe: `{payload['pit_validity']['pit_safe']}`",
        "",
        "## Forward Returns",
        "",
    ]
    for h in HORIZONS:
        stats = payload["forward_return_evidence"][f"{h}d"]["spy_relative"]
        lines.append(f"- {h}D SPY-relative: count `{stats['count']}`, mean `{stats['mean']}`, median `{stats['median']}`, hit rate `{stats['hit_rate']}`")
    liq = payload["liquidity_capacity_evidence"]
    lines += [
        "",
        "## Liquidity",
        "",
        f"- Classification: `{liq['classification']}`",
        f"- Measurement coverage: `{liq['measurement_coverage']}`",
        f"- Minimum 5% ADV capacity: `{liq['capacity_at_5pct_adv']['min']}`",
        f"- Minimum 10% ADV capacity: `{liq['capacity_at_10pct_adv']['min']}`",
        "",
        "## Interpretation",
        "",
        "The tape uses EDGAR acceptance-derived availability timestamps and fails closed on unresolved mapping, price, or liquidity joins.",
    ]
    md_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return json_path, md_path


def _dedupe_ticker_date(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for event in sorted(events, key=lambda e: (e.get("ticker") or "", e.get("tradable_date") or "", e.get("accession_number") or "")):
        key = (str(event.get("ticker") or ""), str(event.get("tradable_date") or ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(event)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--date", default=DEFAULT_OUTPUT_DATE)
    parser.add_argument("--sleep", type=float, default=0.13)
    args = parser.parse_args(argv)
    payload = build_artifact(repo_root=args.repo_root.resolve(), start_date=args.start, end_date=args.end, output_date=args.date, sleep_s=args.sleep)
    json_path, md_path = write_artifacts(args.repo_root.resolve(), payload)
    print(json.dumps({
        "status": "OK",
        "json_path": str(json_path),
        "markdown_path": str(md_path),
        "classification": payload["classification"]["classification"],
        "event_count": payload["event_tape"]["event_count"],
        "usable_event_count": payload["event_tape"]["usable_event_count"],
        "liquidity": payload["liquidity_capacity_evidence"]["classification"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""FR-051 Cygnus Stage 1 — EDGAR 8-K Item 2.02 earnings event tape.

Builds a point-in-time earnings-event tape from SEC EDGAR submissions, keyed by
``acceptanceDateTime``, and produces an acceptance-timestamp audit so a reviewer
can confirm no event is selectable before its availability date (FR-051 canonical
Stage 1; addendum A2 availability rules).

Availability rules (addendum A2), evaluated in **US/Eastern**:
- acceptance < 09:00 ET   -> available for **same-date** close selection
- acceptance 09:00-16:00  -> during-market -> **next trading date** (conservative)
- acceptance >= 16:00 ET   -> after-close -> **next trading date**
- missing/unparseable time -> treated as after-close -> next trading date
- Friday / holiday acceptance maps to the next trading date (via the calendar)

EDGAR ``acceptanceDateTime`` is UTC (verified: AAPL 2026-04-30 earnings 8-K
accepted 20:30Z = 16:30 EDT, consistent with after-close reporting); it is
converted to ET before applying the thresholds. The audit records this
assumption explicitly for review.

Governance: RESEARCH_ONLY / NON_EXECUTIONAL. Network access is read-only public
SEC data, rate-limited under EDGAR's 10 req/sec courtesy limit. The pure tape /
availability / audit logic below is network-free and unit-tested; the live fetch
is injected so it never runs in tests.
"""
from __future__ import annotations

import csv
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

from research.cygnus import (
    EXECUTION_IMPACT,
    GOVERNANCE_LABEL,
    SCHEMA_VERSION_AUDIT,
    SCHEMA_VERSION_EVENT_TAPE,
    STRATEGY_ID,
)

try:
    from paper.trading_calendar import is_trading_day, next_trading_day
except Exception:  # pragma: no cover - defensive
    is_trading_day = None  # type: ignore[assignment]
    next_trading_day = None  # type: ignore[assignment]

EASTERN = ZoneInfo("America/New_York")
EDGAR_USER_AGENT = "caerus-quant brett.olson@nextleague.com"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
EDGAR_SLEEP_S = 0.13  # ~7.5 req/sec, under EDGAR's 10/sec courtesy limit
EARNINGS_ITEM = "2.02"  # 8-K Item 2.02 — Results of Operations
EXHIBIT_ITEM = "9.01"  # Financial Statements and Exhibits (EX-99 proxy)

# A2 ET availability thresholds.
BEFORE_OPEN_HOUR = 9
AFTER_CLOSE_HOUR = 16


# --------------------------------------------------------------------------- #
# Universe / CIK mapping
# --------------------------------------------------------------------------- #
def load_universe_ciks(repo_root: Path | str = ".") -> list[dict[str, str]]:
    """Ticker -> 10-digit CIK from the repo's existing PIT CIK mapping."""
    path = Path(repo_root) / "cik_mapping_results.csv"
    out: list[dict[str, str]] = []
    if not path.exists():
        return out
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if str(row.get("status") or "").strip().upper() != "OK":
                continue
            cik = str(row.get("cik") or "").strip()
            ticker = str(row.get("ticker") or "").strip().upper()
            if cik and ticker:
                out.append({"ticker": ticker, "cik10": cik.zfill(10), "sector": str(row.get("sector") or "").strip()})
    return out


# --------------------------------------------------------------------------- #
# Pure availability logic (network-free; unit-tested)
# --------------------------------------------------------------------------- #
def classify_announcement_time(acceptance_et: datetime | None) -> str:
    if acceptance_et is None:
        return "unknown"
    hour = acceptance_et.hour
    if hour < BEFORE_OPEN_HOUR:
        return "before_open"
    if hour < AFTER_CLOSE_HOUR:
        return "during_market"
    return "after_close"


def _to_trading_day_on_or_after(date_str: str) -> str:
    if is_trading_day is None or next_trading_day is None:
        return date_str
    return date_str if is_trading_day(date_str) else next_trading_day(date_str)


def compute_availability_date(announcement_time: str, announcement_date: str) -> str:
    """First trade date whose close the event may inform (A2 rules)."""
    if announcement_time == "before_open":
        return _to_trading_day_on_or_after(announcement_date)
    # during_market, after_close, unknown -> next trading date (conservative).
    if next_trading_day is None:
        return announcement_date
    return next_trading_day(announcement_date)


def parse_acceptance_datetime(raw: str | None) -> tuple[datetime | None, datetime | None]:
    """Return (utc_dt, eastern_dt) parsed from an EDGAR acceptanceDateTime."""
    text = str(raw or "").strip()
    if not text:
        return None, None
    cleaned = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(cleaned)
    except ValueError:
        return None, None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    utc_dt = dt.astimezone(timezone.utc)
    return utc_dt, utc_dt.astimezone(EASTERN)


# --------------------------------------------------------------------------- #
# Event extraction
# --------------------------------------------------------------------------- #
def extract_earnings_events(
    submissions: dict[str, Any],
    *,
    ticker: str,
    cik10: str,
    start_date: str | None = None,
    end_date: str | None = None,
    ingested_at: str,
) -> list[dict[str, Any]]:
    """Extract 8-K Item 2.02 earnings events from one submissions payload."""
    recent = (submissions.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    items = recent.get("items") or []
    accept = recent.get("acceptanceDateTime") or []
    filing_dates = recent.get("filingDate") or []
    report_dates = recent.get("reportDate") or []
    accessions = recent.get("accessionNumber") or []
    primary_docs = recent.get("primaryDocument") or []

    n = len(forms)
    events: list[dict[str, Any]] = []
    for i in range(n):
        if forms[i] != "8-K":
            continue
        item_str = str(items[i] if i < len(items) else "") or ""
        item_codes = {c.strip() for c in item_str.split(",") if c.strip()}
        if EARNINGS_ITEM not in item_codes:
            continue

        raw_accept = accept[i] if i < len(accept) else None
        utc_dt, et_dt = parse_acceptance_datetime(raw_accept)
        announcement_date = (
            et_dt.date().isoformat()
            if et_dt is not None
            else (filing_dates[i] if i < len(filing_dates) else None)
        )
        if announcement_date is None:
            continue
        if start_date and announcement_date < start_date:
            continue
        if end_date and announcement_date > end_date:
            continue

        announcement_time = classify_announcement_time(et_dt)
        availability_date = compute_availability_date(announcement_time, announcement_date)

        events.append(
            {
                "ticker": ticker,
                "cik10": cik10,
                "fiscal_period": report_dates[i] if i < len(report_dates) else None,
                "announcement_date": announcement_date,
                "announcement_time": announcement_time,
                "availability_date": availability_date,
                "acceptance_datetime_utc": utc_dt.isoformat() if utc_dt else None,
                "acceptance_datetime_et": et_dt.isoformat() if et_dt else None,
                "acceptance_timestamp_present": utc_dt is not None,
                "filing_date": filing_dates[i] if i < len(filing_dates) else None,
                "items": sorted(item_codes),
                "has_financial_exhibit_item": EXHIBIT_ITEM in item_codes,
                "accession_number": accessions[i] if i < len(accessions) else None,
                "primary_document": primary_docs[i] if i < len(primary_docs) else None,
                # Wave 1 (A3): consensus-dependent fields deferred (vendor-gated).
                "reported_eps": None,
                "consensus_eps": None,
                "reported_revenue": None,
                "consensus_revenue": None,
                "guidance_signal": None,
                "event_class": "earnings_8k_item_202",
                "source": "sec_edgar_submissions",
                "ingested_at": ingested_at,
            }
        )
    return events


# --------------------------------------------------------------------------- #
# Live fetch (injected in tests)
# --------------------------------------------------------------------------- #
_FILING_ARRAY_KEYS = (
    "form",
    "items",
    "acceptanceDateTime",
    "filingDate",
    "reportDate",
    "accessionNumber",
    "primaryDocument",
)
OVERFLOW_URL = "https://data.sec.gov/submissions/{name}"


def _edgar_get(url: str) -> dict[str, Any]:
    import json
    import ssl
    import urllib.request

    request = urllib.request.Request(
        url, headers={"User-Agent": EDGAR_USER_AGENT, "Accept": "application/json"}
    )
    with urllib.request.urlopen(request, context=ssl.create_default_context(), timeout=20) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def fetch_submissions(cik10: str, *, get_fn: Callable[[str], dict[str, Any]] | None = None) -> dict[str, Any]:
    url = SUBMISSIONS_URL.format(cik10=cik10)
    return (get_fn or _edgar_get)(url)


def merge_filing_arrays(recent: dict[str, Any], overflow: dict[str, Any]) -> dict[str, Any]:
    """Concatenate parallel filing arrays from a recent block and an overflow file."""
    merged: dict[str, list[Any]] = {}
    for key in _FILING_ARRAY_KEYS:
        merged[key] = list(recent.get(key) or []) + list(overflow.get(key) or [])
    return merged


def fetch_all_filings(
    cik10: str,
    *,
    get_fn: Callable[[str], dict[str, Any]] | None = None,
    sleep_s: float = EDGAR_SLEEP_S,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Fetch the full filing history (filings.recent + paginated filings.files).

    EDGAR's ``filings.recent`` truncates to the latest ~1000 filings for active
    filers; older filings live in ``filings.files`` overflow JSONs. Missing this
    understates earnings-event coverage for frequent filers (e.g. banks).
    Returns a submissions-shaped dict whose ``filings.recent`` holds every
    filing, so ``extract_earnings_events`` works unchanged.
    """
    submissions = fetch_submissions(cik10, get_fn=get_fn)
    filings = submissions.get("filings") or {}
    merged = {key: list((filings.get("recent") or {}).get(key) or []) for key in _FILING_ARRAY_KEYS}
    for ref in filings.get("files") or []:
        name = ref.get("name") if isinstance(ref, dict) else None
        if not name:
            continue
        url = OVERFLOW_URL.format(name=name)
        overflow = (get_fn or _edgar_get)(url)
        merged = merge_filing_arrays(merged, overflow if isinstance(overflow, dict) else {})
        if get_fn is None and sleep_s:
            sleep_fn(sleep_s)
    return {"filings": {"recent": merged}}


def build_event_tape(
    universe: Iterable[dict[str, str]],
    *,
    start_date: str | None,
    end_date: str | None,
    ingested_at: str,
    get_fn: Callable[[str], dict[str, Any]] | None = None,
    sleep_s: float = EDGAR_SLEEP_S,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build the full event tape across the universe. Returns (events, fetch_errors)."""
    events: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for entry in universe:
        ticker, cik10 = entry["ticker"], entry["cik10"]
        try:
            submissions = fetch_all_filings(cik10, get_fn=get_fn, sleep_s=sleep_s, sleep_fn=sleep_fn)
        except Exception as exc:  # record, never crash the whole tape
            errors.append({"ticker": ticker, "cik10": cik10, "error": f"{type(exc).__name__}: {exc}"})
            if get_fn is None and sleep_s:
                sleep_fn(sleep_s)
            continue
        events.extend(
            extract_earnings_events(
                submissions,
                ticker=ticker,
                cik10=cik10,
                start_date=start_date,
                end_date=end_date,
                ingested_at=ingested_at,
            )
        )
        if get_fn is None and sleep_s:
            sleep_fn(sleep_s)
    events.sort(key=lambda e: (e["availability_date"], e["announcement_date"], e["ticker"]))
    return events, errors


# --------------------------------------------------------------------------- #
# Acceptance-timestamp audit (the Stage 1 deliverable for owner review)
# --------------------------------------------------------------------------- #
def audit_acceptance_timestamps(
    events: list[dict[str, Any]],
    *,
    sample_per_class: int = 5,
) -> dict[str, Any]:
    """Audit the tape for availability-rule correctness and look-ahead safety."""
    time_dist = Counter(e["announcement_time"] for e in events)
    missing_ts = [e for e in events if not e["acceptance_timestamp_present"]]

    # Look-ahead invariants (each must hold for a PIT-safe tape):
    # 1. availability_date is always a trading day.
    # 2. before_open -> availability_date == announcement_date (when that is a
    #    trading day); during/after/unknown -> availability_date > announcement_date.
    # 3. availability_date is never before announcement_date.
    non_trading_availability: list[dict[str, Any]] = []
    availability_before_announcement: list[dict[str, Any]] = []
    same_day_after_close: list[dict[str, Any]] = []
    for e in events:
        avail, ann, when = e["availability_date"], e["announcement_date"], e["announcement_time"]
        if is_trading_day is not None and not is_trading_day(avail):
            non_trading_availability.append({"ticker": e["ticker"], "availability_date": avail})
        if avail < ann:
            availability_before_announcement.append(
                {"ticker": e["ticker"], "announcement_date": ann, "availability_date": avail}
            )
        if when in {"during_market", "after_close", "unknown"} and avail <= ann:
            same_day_after_close.append(
                {"ticker": e["ticker"], "announcement_date": ann, "availability_date": avail, "announcement_time": when}
            )

    samples: dict[str, list[dict[str, Any]]] = {}
    for cls in ("before_open", "during_market", "after_close", "unknown"):
        cls_events = [e for e in events if e["announcement_time"] == cls][:sample_per_class]
        samples[cls] = [
            {
                "ticker": e["ticker"],
                "acceptance_datetime_utc": e["acceptance_datetime_utc"],
                "acceptance_datetime_et": e["acceptance_datetime_et"],
                "announcement_date": e["announcement_date"],
                "availability_date": e["availability_date"],
            }
            for e in cls_events
        ]

    look_ahead_clean = not (
        non_trading_availability or availability_before_announcement or same_day_after_close
    )
    return {
        "schema_version": SCHEMA_VERSION_AUDIT,
        "strategy_id": STRATEGY_ID,
        "governance_label": GOVERNANCE_LABEL,
        "execution_impact": EXECUTION_IMPACT,
        "timestamp_interpretation": (
            "EDGAR acceptanceDateTime parsed as UTC and converted to America/New_York "
            "before applying A2 09:00/16:00 ET thresholds."
        ),
        "event_count": len(events),
        "unique_tickers": len({e["ticker"] for e in events}),
        "announcement_time_distribution": dict(time_dist),
        "missing_timestamp_count": len(missing_ts),
        "with_financial_exhibit_item_count": sum(1 for e in events if e["has_financial_exhibit_item"]),
        "look_ahead_safe": look_ahead_clean,
        "look_ahead_violations": {
            "non_trading_availability_date": non_trading_availability,
            "availability_before_announcement": availability_before_announcement,
            "delayed_event_not_delayed": same_day_after_close,
        },
        "samples_by_announcement_time": samples,
    }

from __future__ import annotations

from research.cygnus.events import (
    audit_acceptance_timestamps,
    build_event_tape,
    classify_announcement_time,
    compute_availability_date,
    extract_earnings_events,
    fetch_all_filings,
    parse_acceptance_datetime,
)

INGESTED = "2026-06-10T00:00:00+00:00"


def _submissions(*filings: dict) -> dict:
    keys = ["form", "items", "acceptanceDateTime", "filingDate", "reportDate",
            "accessionNumber", "primaryDocument"]
    recent = {k: [f.get(k) for f in filings] for k in keys}
    return {"filings": {"recent": recent}}


# --------------------------------------------------------------------------- #
# Timestamp parsing + availability rules (A2)
# --------------------------------------------------------------------------- #
def test_acceptance_datetime_utc_to_eastern() -> None:
    # 20:30Z on 2026-04-30 = 16:30 EDT (UTC-4) -> after close.
    utc, et = parse_acceptance_datetime("2026-04-30T20:30:41.000Z")
    assert utc.isoformat() == "2026-04-30T20:30:41+00:00"
    assert et.hour == 16 and et.minute == 30
    assert classify_announcement_time(et) == "after_close"


def test_classify_before_open_and_during_market() -> None:
    _, before = parse_acceptance_datetime("2026-04-30T12:00:00.000Z")  # 08:00 EDT
    assert classify_announcement_time(before) == "before_open"
    _, during = parse_acceptance_datetime("2026-04-30T17:00:00.000Z")  # 13:00 EDT
    assert classify_announcement_time(during) == "during_market"
    assert classify_announcement_time(None) == "unknown"


def test_availability_before_open_same_trading_day() -> None:
    # 2026-04-30 is a Thursday (trading day).
    assert compute_availability_date("before_open", "2026-04-30") == "2026-04-30"


def test_availability_after_close_next_trading_day() -> None:
    # Thu after-close -> Fri 2026-05-01.
    assert compute_availability_date("after_close", "2026-04-30") == "2026-05-01"


def test_availability_friday_after_close_maps_to_monday() -> None:
    # 2026-05-01 is a Friday; next trading day is Monday 2026-05-04.
    assert compute_availability_date("after_close", "2026-05-01") == "2026-05-04"


# --------------------------------------------------------------------------- #
# Event extraction
# --------------------------------------------------------------------------- #
def test_extract_only_8k_item_202() -> None:
    subs = _submissions(
        {"form": "8-K", "items": "2.02,9.01", "acceptanceDateTime": "2026-04-30T20:30:00.000Z",
         "filingDate": "2026-04-30", "reportDate": "2026-03-28", "accessionNumber": "a-1", "primaryDocument": "d1.htm"},
        {"form": "8-K", "items": "5.02", "acceptanceDateTime": "2026-04-30T20:30:00.000Z",
         "filingDate": "2026-04-30", "reportDate": "", "accessionNumber": "a-2", "primaryDocument": "d2.htm"},
        {"form": "10-Q", "items": "", "acceptanceDateTime": "2026-04-30T20:30:00.000Z",
         "filingDate": "2026-04-30", "reportDate": "2026-03-28", "accessionNumber": "a-3", "primaryDocument": "d3.htm"},
    )
    events = extract_earnings_events(subs, ticker="AAPL", cik10="0000320193", ingested_at=INGESTED)
    assert len(events) == 1
    e = events[0]
    assert e["ticker"] == "AAPL"
    assert e["announcement_time"] == "after_close"
    assert e["availability_date"] == "2026-05-01"
    assert e["has_financial_exhibit_item"] is True
    assert e["fiscal_period"] == "2026-03-28"
    assert e["reported_eps"] is None  # Wave 1 defers consensus-dependent fields


def test_extract_respects_date_window_and_missing_timestamp() -> None:
    subs = _submissions(
        {"form": "8-K", "items": "2.02", "acceptanceDateTime": None,
         "filingDate": "2020-01-15", "reportDate": "2019-12-31", "accessionNumber": "a-9", "primaryDocument": "x.htm"},
    )
    # Missing timestamp -> announcement_date falls back to filingDate, treated as after-close.
    events = extract_earnings_events(subs, ticker="XYZ", cik10="0", start_date="2019-01-01",
                                     end_date="2026-01-01", ingested_at=INGESTED)
    assert len(events) == 1
    assert events[0]["announcement_time"] == "unknown"
    assert events[0]["acceptance_timestamp_present"] is False
    assert events[0]["availability_date"] > events[0]["announcement_date"]
    # Window filter excludes it.
    assert extract_earnings_events(subs, ticker="XYZ", cik10="0", start_date="2025-01-01",
                                   end_date="2026-01-01", ingested_at=INGESTED) == []


# --------------------------------------------------------------------------- #
# build_event_tape + audit (injected fetch, no network)
# --------------------------------------------------------------------------- #
def test_build_tape_and_audit_look_ahead_safe() -> None:
    payloads = {
        "https://data.sec.gov/submissions/CIK0000000001.json": _submissions(
            {"form": "8-K", "items": "2.02,9.01", "acceptanceDateTime": "2026-04-30T12:00:00.000Z",  # 08:00 EDT before open
             "filingDate": "2026-04-30", "reportDate": "2026-03-31", "accessionNumber": "b-1", "primaryDocument": "d.htm"},
        ),
        "https://data.sec.gov/submissions/CIK0000000002.json": _submissions(
            {"form": "8-K", "items": "2.02", "acceptanceDateTime": "2026-04-30T20:30:00.000Z",  # after close
             "filingDate": "2026-04-30", "reportDate": "2026-03-31", "accessionNumber": "b-2", "primaryDocument": "d.htm"},
        ),
    }

    def fake_get(url: str) -> dict:
        return payloads[url]

    universe = [{"ticker": "AAA", "cik10": "0000000001"}, {"ticker": "BBB", "cik10": "0000000002"}]
    events, errors = build_event_tape(
        universe, start_date="2016-01-01", end_date="2026-06-10", ingested_at=INGESTED,
        get_fn=fake_get, sleep_s=0,
    )
    assert errors == []
    assert len(events) == 2
    audit = audit_acceptance_timestamps(events)
    assert audit["look_ahead_safe"] is True
    assert audit["announcement_time_distribution"] == {"before_open": 1, "after_close": 1}
    assert audit["unique_tickers"] == 2


def test_fetch_all_filings_merges_overflow_pages() -> None:
    # Active filer: one 2.02 in recent, one in the paginated overflow file.
    main = {
        "filings": {
            "recent": _submissions(
                {"form": "8-K", "items": "2.02", "acceptanceDateTime": "2026-04-30T20:30:00.000Z",
                 "filingDate": "2026-04-30", "reportDate": "2026-03-31", "accessionNumber": "r-1", "primaryDocument": "d.htm"},
            )["filings"]["recent"],
            "files": [{"name": "CIK0000000019-submissions-001.json"}],
        }
    }
    overflow = _submissions(
        {"form": "8-K", "items": "2.02,9.01", "acceptanceDateTime": "2018-04-13T11:00:00.000Z",  # 07:00 EDT before open
         "filingDate": "2018-04-13", "reportDate": "2018-03-31", "accessionNumber": "o-1", "primaryDocument": "d.htm"},
    )["filings"]["recent"]

    payloads = {
        "https://data.sec.gov/submissions/CIK0000000019.json": main,
        "https://data.sec.gov/submissions/CIK0000000019-submissions-001.json": overflow,
    }
    merged = fetch_all_filings("0000000019", get_fn=lambda u: payloads[u], sleep_s=0)
    events = extract_earnings_events(merged, ticker="JPM", cik10="0000000019",
                                     start_date="2016-01-01", end_date="2026-06-10", ingested_at=INGESTED)
    assert len(events) == 2  # recent + overflow both captured
    assert {e["announcement_date"] for e in events} == {"2026-04-30", "2018-04-13"}


def test_build_tape_records_fetch_errors() -> None:
    def boom(url: str) -> dict:
        raise RuntimeError("EDGAR unreachable")

    events, errors = build_event_tape(
        [{"ticker": "AAA", "cik10": "0000000001"}], start_date="2016-01-01",
        end_date="2026-06-10", ingested_at=INGESTED, get_fn=boom, sleep_s=0,
    )
    assert events == []
    assert len(errors) == 1 and errors[0]["ticker"] == "AAA"

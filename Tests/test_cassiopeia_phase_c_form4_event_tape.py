from __future__ import annotations

import csv
import json
from pathlib import Path

import pandas as pd

from scripts.research.build_cassiopeia_phase_c_form4_event_tape import (
    _is_form4_xml_name,
    build_artifact,
    parse_form4_xml,
)


def _form4_xml(*, code: str = "P", title: str = "Chief Executive Officer") -> str:
    return f"""<?xml version="1.0"?>
<ownershipDocument>
  <issuer>
    <issuerCik>0000001234</issuerCik>
    <issuerTradingSymbol>AAA</issuerTradingSymbol>
  </issuer>
  <periodOfReport>2024-11-25</periodOfReport>
  <reportingOwner>
    <reportingOwnerId>
      <rptOwnerCik>0000009999</rptOwnerCik>
      <rptOwnerName>Jane Insider</rptOwnerName>
    </reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>true</isDirector>
      <isOfficer>true</isOfficer>
      <isTenPercentOwner>false</isTenPercentOwner>
      <officerTitle>{title}</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2024-11-25</value></transactionDate>
      <transactionCoding><transactionCode>{code}</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>1000</value></transactionShares>
        <transactionPricePerShare><value>25.5</value></transactionPricePerShare>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>"""


def test_parse_form4_xml_extracts_role_and_purchase() -> None:
    parsed = parse_form4_xml(_form4_xml())

    assert parsed["issuer_cik"] == "0000001234"
    assert parsed["issuer_ticker"] == "AAA"
    assert parsed["transaction_type"] == "purchase"
    assert parsed["purchase_transaction_count"] == 1
    assert parsed["purchase_value"] == 25500.0
    assert parsed["insider_role"] == "ceo"
    assert parsed["owners"][0]["is_director"] is True


def test_xsl_rendered_form4_path_is_not_treated_as_raw_xml() -> None:
    assert _is_form4_xml_name("form4.xml") is True
    assert _is_form4_xml_name("xslF345X05/wk-form4_1727385738.xml") is False
    assert _is_form4_xml_name("ownership.xsl") is False


def _write_fixture_repo(root: Path) -> None:
    with (root / "cik_mapping_results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["ticker", "sector", "cik", "edgar_name", "status"])
        writer.writeheader()
        writer.writerow({"ticker": "AAA", "sector": "Technology", "cik": "0000001234", "edgar_name": "AAA Corp", "status": "OK"})
    (root / "data/pit_universe").mkdir(parents=True)
    with (root / "data/pit_universe/security_master.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "security_id", "permaticker", "ticker", "name", "exchange", "category", "isdelisted",
            "firstpricedate", "lastpricedate", "relatedtickers", "currency", "location",
            "source", "source_table", "lastupdated", "confidence",
        ])
        writer.writeheader()
        writer.writerow({
            "security_id": "SHARADAR:1", "permaticker": "1", "ticker": "AAA", "name": "AAA Corp",
            "exchange": "NYSE", "category": "Domestic Common Stock", "isdelisted": "N",
            "firstpricedate": "2020-01-01", "lastpricedate": "2026-12-31", "relatedtickers": "",
            "currency": "USD", "location": "U.S.A", "source": "fixture", "source_table": "fixture",
            "lastupdated": "2026-01-01", "confidence": "HIGH",
        })
    (root / "outputs/research/pit_liquidity").mkdir(parents=True)
    dates = pd.bdate_range("2024-11-27", periods=80)
    rows = []
    for i, date in enumerate(dates):
        close = 100 + i
        rows.append({
            "ticker": "AAA",
            "date": date.date().isoformat(),
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "closeadj": close,
            "volume": 1_000_000,
            "dollar_volume": close * 1_000_000,
            "ADV_20": 1_000_000,
            "ADV_60": 1_000_000,
            "dollar_ADV_20": 100_000_000,
            "dollar_ADV_60": 100_000_000,
        })
    pd.DataFrame(rows).to_csv(root / "outputs/research/pit_liquidity/pit_liquidity_panel.csv", index=False)
    (root / "alpha_stack_cache/prices").mkdir(parents=True)
    spy = pd.DataFrame({"SPY": [100 + i * 0.5 for i in range(90)]}, index=pd.bdate_range("2024-11-27", periods=90))
    spy.to_parquet(root / "alpha_stack_cache/prices/_matrix_prices_2007_2026.parquet")


def test_build_artifact_with_injected_form4_sources(tmp_path: Path) -> None:
    _write_fixture_repo(tmp_path)
    submissions_url = "https://data.sec.gov/submissions/CIK0000001234.json"
    xml_url = "https://www.sec.gov/Archives/edgar/data/1234/000000123424000001/form4.xml"
    submissions = {
        "filings": {
            "recent": {
                "form": ["4"],
                "items": [""],
                "acceptanceDateTime": ["2024-11-26T22:18:19.000Z"],
                "filingDate": ["2024-11-26"],
                "reportDate": ["2024-11-25"],
                "accessionNumber": ["0000001234-24-000001"],
                "primaryDocument": ["form4.xml"],
            }
        }
    }

    payload = build_artifact(
        repo_root=tmp_path,
        start_date="2024-01-01",
        end_date="2024-12-31",
        output_date="2026-06-19",
        sleep_s=0,
        get_submissions_fn=lambda url: {submissions_url: submissions}[url],
        get_xml_fn=lambda url: {xml_url: _form4_xml()}[url],
    )

    assert payload["event_tape"]["event_count"] == 1
    assert payload["event_tape"]["usable_event_count"] == 1
    event = payload["event_tape"]["events"][0]
    assert event["tradable_date"] == "2024-11-27"
    assert event["pit_validity_flag"] is True
    assert event["transaction_type"] == "purchase"
    assert event["forward_return_5d"] is not None
    assert event["capacity_at_5pct_adv"] == 250000000.0
    assert payload["pit_validity"]["pit_safe"] is True
    assert payload["source_errors"] == {"submissions_errors": [], "xml_errors": []}


def test_build_artifact_fails_closed_without_purchase_or_sale(tmp_path: Path) -> None:
    _write_fixture_repo(tmp_path)
    submissions_url = "https://data.sec.gov/submissions/CIK0000001234.json"
    xml_url = "https://www.sec.gov/Archives/edgar/data/1234/000000123424000001/form4.xml"
    submissions = {
        "filings": {
            "recent": {
                "form": ["4"],
                "items": [""],
                "acceptanceDateTime": ["2024-11-26T22:18:19.000Z"],
                "filingDate": ["2024-11-26"],
                "reportDate": ["2024-11-25"],
                "accessionNumber": ["0000001234-24-000001"],
                "primaryDocument": ["form4.xml"],
            }
        }
    }

    payload = build_artifact(
        repo_root=tmp_path,
        start_date="2024-01-01",
        end_date="2024-12-31",
        output_date="2026-06-19",
        sleep_s=0,
        get_submissions_fn=lambda url: {submissions_url: submissions}[url],
        get_xml_fn=lambda url: {xml_url: _form4_xml(code="M")}[url],
    )

    assert payload["event_tape"]["usable_event_count"] == 0
    assert payload["event_tape"]["events"][0]["exclusion_reason"] == "no_open_market_purchase_or_sale"
    assert payload["classification"]["classification"] == "CASSIOPEIA_PHASE_C_BLOCKED_DATA"
    assert json.dumps(payload)


def _two_filing_submissions() -> dict:
    return {
        "filings": {
            "recent": {
                "form": ["4", "4"],
                "items": ["", ""],
                "acceptanceDateTime": ["2024-11-26T22:18:19.000Z", "2024-11-27T13:00:00.000Z"],
                "filingDate": ["2024-11-26", "2024-11-27"],
                "reportDate": ["2024-11-25", "2024-11-26"],
                "accessionNumber": ["0000001234-24-000001", "0000001234-24-000002"],
                "primaryDocument": ["form4a.xml", "form4b.xml"],
            }
        }
    }


def test_build_artifact_respects_max_filings_bound(tmp_path: Path) -> None:
    _write_fixture_repo(tmp_path)
    submissions_url = "https://data.sec.gov/submissions/CIK0000001234.json"

    def xml_for(url: str) -> str:
        return _form4_xml(code="P")

    payload = build_artifact(
        repo_root=tmp_path,
        start_date="2024-01-01",
        end_date="2024-12-31",
        output_date="bounded",
        sleep_s=0,
        max_filings=1,
        get_submissions_fn=lambda url: {submissions_url: _two_filing_submissions()}[url],
        get_xml_fn=xml_for,
    )

    assert payload["event_tape"]["event_count"] == 1
    assert payload["generation_bounds"]["max_filings"] == 1
    assert payload["generation_bounds"]["stopped_by_bound"] is True
    assert payload["generation_bounds"]["pilot_artifact"] is True


def test_build_artifact_reuses_local_submissions_and_xml_cache(tmp_path: Path) -> None:
    _write_fixture_repo(tmp_path)
    submissions_url = "https://data.sec.gov/submissions/CIK0000001234.json"
    call_counts = {"submissions": 0, "xml": 0}

    def submissions_for(url: str) -> dict:
        call_counts["submissions"] += 1
        return {submissions_url: _two_filing_submissions()}[url]

    def xml_for(url: str) -> str:
        call_counts["xml"] += 1
        return _form4_xml(code="P")

    first = build_artifact(
        repo_root=tmp_path,
        start_date="2024-01-01",
        end_date="2024-12-31",
        output_date="cache",
        sleep_s=0,
        max_filings=1,
        get_submissions_fn=submissions_for,
        get_xml_fn=xml_for,
    )
    second = build_artifact(
        repo_root=tmp_path,
        start_date="2024-01-01",
        end_date="2024-12-31",
        output_date="cache-second",
        sleep_s=0,
        max_filings=1,
        get_submissions_fn=lambda url: (_ for _ in ()).throw(AssertionError("submissions cache miss")),
        get_xml_fn=lambda url: (_ for _ in ()).throw(AssertionError("xml cache miss")),
    )

    assert first["event_tape"]["event_count"] == 1
    assert second["event_tape"]["event_count"] == 1
    assert call_counts == {"submissions": 1, "xml": 1}


def test_build_artifact_strips_xsl_primary_document_path(tmp_path: Path) -> None:
    _write_fixture_repo(tmp_path)
    submissions_url = "https://data.sec.gov/submissions/CIK0000001234.json"
    raw_xml_url = "https://www.sec.gov/Archives/edgar/data/1234/000000123424000001/wk-form4.xml"
    submissions = {
        "filings": {
            "recent": {
                "form": ["4"],
                "items": [""],
                "acceptanceDateTime": ["2024-11-26T22:18:19.000Z"],
                "filingDate": ["2024-11-26"],
                "reportDate": ["2024-11-25"],
                "accessionNumber": ["0000001234-24-000001"],
                "primaryDocument": ["xslF345X05/wk-form4.xml"],
            }
        }
    }

    payload = build_artifact(
        repo_root=tmp_path,
        start_date="2024-01-01",
        end_date="2024-12-31",
        output_date="xsl-strip",
        sleep_s=0,
        max_filings=1,
        get_submissions_fn=lambda url: {submissions_url: submissions}[url],
        get_xml_fn=lambda url: {raw_xml_url: _form4_xml()}[url],
    )

    assert payload["event_tape"]["usable_event_count"] == 1
    assert payload["event_tape"]["events"][0]["source_url"] == raw_xml_url


def test_build_artifact_resumes_from_checkpoint(tmp_path: Path) -> None:
    _write_fixture_repo(tmp_path)
    submissions_url = "https://data.sec.gov/submissions/CIK0000001234.json"
    xml_calls: list[str] = []

    def xml_for(url: str) -> str:
        xml_calls.append(url)
        return _form4_xml(code="P")

    first = build_artifact(
        repo_root=tmp_path,
        start_date="2024-01-01",
        end_date="2024-12-31",
        output_date="resume",
        sleep_s=0,
        max_filings=1,
        get_submissions_fn=lambda url: {submissions_url: _two_filing_submissions()}[url],
        get_xml_fn=xml_for,
    )
    second = build_artifact(
        repo_root=tmp_path,
        start_date="2024-01-01",
        end_date="2024-12-31",
        output_date="resume",
        sleep_s=0,
        max_filings=2,
        resume=True,
        get_submissions_fn=lambda url: {submissions_url: _two_filing_submissions()}[url],
        get_xml_fn=xml_for,
    )

    assert first["event_tape"]["event_count"] == 1
    assert second["event_tape"]["event_count"] == 2
    assert second["generation_bounds"]["resume"] is True
    assert second["generation_bounds"]["processed_this_run"] == 1
    assert len(xml_calls) == 2

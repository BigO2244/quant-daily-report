from __future__ import annotations

import csv
import json
from pathlib import Path

import pandas as pd

from scripts.research.build_cassiopeia_phase_b_13d_event_tape import (
    build_artifact,
    parse_header,
)


def test_parse_13d_header_acceptance_and_subject() -> None:
    text = """<SEC-HEADER>
<ACCEPTANCE-DATETIME>20241126171819
SUBJECT COMPANY:
    COMPANY DATA:
        COMPANY CONFORMED NAME:            ACME CORP
        CENTRAL INDEX KEY:            0000001234
FILED BY:
    COMPANY DATA:
        COMPANY CONFORMED NAME:            ACTIVIST FUND LP
        CENTRAL INDEX KEY:            0000009999
</SEC-HEADER>"""

    header = parse_header(text)

    assert header["acceptance_datetime_et"] == "2024-11-26T17:18:19-05:00"
    assert header["acceptance_datetime_utc"] == "2024-11-26T22:18:19+00:00"
    assert header["subject_company_cik"] == "0000001234"
    assert header["filer_name"] == "ACTIVIST FUND LP"


def _write_fixture_repo(root: Path) -> None:
    (root / "data/alpha_stack_cache/edgar").mkdir(parents=True)
    (root / "data/alpha_stack_cache/edgar/sec_ticker_map.json").write_text(json.dumps({"AAA": "0000001234"}))
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
            "firstpricedate": "2020-01-01", "lastpricedate": "2026-12-31",
            "relatedtickers": "", "currency": "USD", "location": "U.S.A", "source": "fixture",
            "source_table": "fixture", "lastupdated": "2026-01-01", "confidence": "HIGH",
        })
    (root / "outputs/research/pit_liquidity").mkdir(parents=True)
    dates = pd.bdate_range("2024-11-27", periods=70)
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
    spy = pd.DataFrame({"SPY": [100 + i * 0.5 for i in range(80)]}, index=pd.bdate_range("2024-11-27", periods=80))
    spy.to_parquet(root / "alpha_stack_cache/prices/_matrix_prices_2007_2026.parquet")


def test_build_artifact_with_injected_sec_sources(tmp_path: Path) -> None:
    _write_fixture_repo(tmp_path)
    master_url = "https://www.sec.gov/Archives/edgar/full-index/2024/QTR4/master.idx"
    filing_url = "https://www.sec.gov/Archives/edgar/data/1234/0000000000-24-000001.txt"
    master = "\n".join([
        "CIK|Company Name|Form Type|Date Filed|Filename",
        "0000001234|AAA Corp|SC 13D|2024-11-26|edgar/data/1234/0000000000-24-000001.txt",
    ])
    filing = """<SEC-HEADER>
<ACCEPTANCE-DATETIME>20241126171819
SUBJECT COMPANY:
    COMPANY DATA:
        COMPANY CONFORMED NAME:            AAA Corp
        CENTRAL INDEX KEY:            0000001234
FILED BY:
    COMPANY DATA:
        COMPANY CONFORMED NAME:            ACTIVIST FUND LP
        CENTRAL INDEX KEY:            0000009999
</SEC-HEADER>"""

    def fake_get(url: str) -> str:
        return {master_url: master, filing_url: filing}[url]

    payload = build_artifact(
        repo_root=tmp_path,
        start_date="2024-10-01",
        end_date="2024-12-31",
        output_date="2026-06-18",
        sleep_s=0,
        get_text_fn=fake_get,
    )

    assert payload["event_tape"]["event_count"] == 1
    assert payload["event_tape"]["usable_event_count"] == 1
    event = payload["event_tape"]["events"][0]
    assert event["tradable_date"] == "2024-11-27"
    assert event["pit_validity_flag"] is True
    assert event["capacity_at_5pct_adv"] == 250000000.0
    assert event["forward_return_5d"] is not None
    assert payload["pit_validity"]["pit_safe"] is True

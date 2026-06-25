from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from research_data import load_fundamentals, load_insiders, load_macro, load_sec_events, load_vix
from research_data.hydration import write_json
from research_data.normalization import normalize_p2
from scripts.data_hydration.validate_p2_normalization import validate_p2_normalization_artifact


def _seed_p2_raw(root: Path) -> None:
    write_json(
        root / "data/raw/fundamentals_pit/nasdaq_sharadar/sharadar_sf1_sample.json",
        {
            "source_table": "SHARADAR/SF1",
            "columns": ["ticker", "calendardate", "datekey", "reportperiod", "dimension", "revenue", "netinc"],
            "rows": [
                {
                    "ticker": "AAPL",
                    "calendardate": "2023-12-31",
                    "datekey": "2024-02-02",
                    "reportperiod": "2023-12-31",
                    "dimension": "ARQ",
                    "revenue": 119575000000,
                    "netinc": 33916000000,
                }
            ],
        },
    )
    write_json(
        root / "data/raw/macro_rates/fred_public_csv/macro_rates_sample.json",
        {
            "series_ids": ["FEDFUNDS", "DGS10"],
            "series": {
                "FEDFUNDS.csv": "observation_date,FEDFUNDS\n2026-05-01,3.63\n2026-07-01,3.70\n",
                "DGS10.csv": "observation_date,DGS10\n2026-06-23,4.50\n",
            },
        },
    )
    write_json(
        root / "data/raw/yield_curve/fred_public_csv/yield_curve_sample.json",
        {
            "series_ids": ["DGS2", "DGS10", "DGS30"],
            "series": {
                "DGS2.csv": "observation_date,DGS2\n2026-06-23,4.16\n",
                "DGS10.csv": "observation_date,DGS10\n2026-06-23,4.50\n",
                "DGS30.csv": "observation_date,DGS30\n2026-06-23,4.94\n",
            },
        },
    )
    write_json(
        root / "data/raw/credit_spreads/fred_public_csv/credit_spreads_sample.json",
        {
            "series_ids": ["BAA10Y"],
            "series": {"BAA10Y.csv": "observation_date,BAA10Y\n2026-06-23,1.51\n"},
        },
    )
    vix_timestamp = int(datetime(2026, 6, 23, tzinfo=UTC).timestamp())
    write_json(
        root / "data/raw/vix_volatility_regime/yahoo_chart_public/vix_volatility_regime_sample.json",
        {
            "symbol": "%5EVIX",
            "rows": [{"timestamp": vix_timestamp, "close": 17.5}],
            "source_payload": {
                "chart": {
                    "result": [
                        {
                            "timestamp": [vix_timestamp],
                            "indicators": {
                                "quote": [
                                    {
                                        "open": [18.0],
                                        "high": [18.5],
                                        "low": [17.0],
                                        "close": [17.5],
                                    }
                                ]
                            },
                        }
                    ]
                }
            },
        },
    )
    filing_record = {
        "accessionNumber": "0000320193-26-000013",
        "form": "10-Q",
        "filingDate": "2026-05-01",
        "acceptanceDateTime": "2026-05-01T10:01:00.000Z",
        "reportDate": "2026-03-28",
        "primaryDocument": "aapl-20260328.htm",
        "isInlineXBRL": 1,
        "isXBRLNumeric": 1,
    }
    write_json(
        root / "data/raw/insider_form4/sec_edgar_public/submissions_0000320193_sample.json",
        {
            "cik": "0000320193",
            "forms": ["4"],
            "records": [
                {
                    **filing_record,
                    "accessionNumber": "0001140361-26-025622",
                    "form": "4",
                    "reportDate": "2026-04-30",
                    "filingDate": "2026-05-01",
                    "primaryDocument": "xslF345X06/form4.xml",
                }
            ],
        },
    )
    write_json(
        root / "data/raw/sec_8k_events/sec_edgar_public/submissions_0000320193_sample.json",
        {
            "cik": "0000320193",
            "forms": ["8-K"],
            "records": [
                {
                    **filing_record,
                    "form": "8-K",
                    "items": "2.02,9.01",
                    "reportDate": "2026-04-30",
                    "filingDate": "2026-04-30",
                }
            ],
        },
    )
    write_json(
        root / "data/raw/sec_10q_10k_metadata/sec_edgar_public/submissions_0000320193_sample.json",
        {"cik": "0000320193", "forms": ["10-Q"], "records": [filing_record]},
    )


def test_normalize_p2_writes_observe_only_artifacts_and_manifest(tmp_path: Path) -> None:
    _seed_p2_raw(tmp_path)

    manifest = normalize_p2(repo_root=tmp_path, as_of_date="2026-06-24")

    assert manifest["dataset_count"] == 8
    assert manifest["failed_dataset_count"] == 0
    assert manifest["normalized_dataset_count"] == 8
    assert (tmp_path / "data/normalized/fundamentals/statements.json").exists()
    assert (tmp_path / "data/normalized/macro/macro_rates.json").exists()
    assert (tmp_path / "data/normalized/macro/yield_curve.json").exists()
    assert (tmp_path / "data/normalized/macro/credit_spreads.json").exists()
    assert (tmp_path / "data/normalized/volatility/vix.json").exists()
    assert (tmp_path / "data/normalized/insiders/form4_filings.json").exists()
    assert (tmp_path / "data/normalized/sec_events/eight_k_items.json").exists()
    assert (tmp_path / "data/normalized/sec_events/filings.json").exists()


def test_research_data_api_loads_p2_normalized_rows(tmp_path: Path) -> None:
    _seed_p2_raw(tmp_path)
    normalize_p2(repo_root=tmp_path, as_of_date="2026-06-24")

    fundamentals = load_fundamentals(repo_root=tmp_path)
    macro = load_macro(repo_root=tmp_path)
    insiders = load_insiders(repo_root=tmp_path)
    sec_events = load_sec_events(repo_root=tmp_path)
    vix = load_vix(repo_root=tmp_path)

    assert fundamentals[0]["security_id"] == "SHARADAR_TICKER:AAPL"
    assert any(row.get("series_id") == "DGS10" for row in macro)
    assert insiders[0]["filing_metadata_only"] is True
    assert any(row.get("item_code") == "2.02" for row in sec_events)
    assert vix[0]["vix_close"] == 17.5


def test_validate_p2_normalization_accepts_structural_warnings(tmp_path: Path) -> None:
    _seed_p2_raw(tmp_path)
    normalize_p2(repo_root=tmp_path, as_of_date="2026-06-24")

    errors = validate_p2_normalization_artifact(
        tmp_path / "data/manifests/p2_normalization_manifest.json",
        repo_root=tmp_path,
    )

    assert errors == []


def test_normalize_p2_reports_missing_sources_without_throwing(tmp_path: Path) -> None:
    manifest = normalize_p2(repo_root=tmp_path, as_of_date="2026-06-24", dataset_ids={"fundamentals_pit"})

    assert manifest["dataset_count"] == 1
    assert manifest["failed_dataset_count"] == 1
    assert manifest["datasets"][0]["status"] == "MISSING_SOURCE"


def test_validate_p2_normalization_detects_manifest_row_count_drift(tmp_path: Path) -> None:
    _seed_p2_raw(tmp_path)
    normalize_p2(repo_root=tmp_path, as_of_date="2026-06-24")
    artifact = tmp_path / "data/normalized/fundamentals/statements.json"
    payload = artifact.read_text(encoding="utf-8")
    payload = payload.replace('"row_count": 1', '"row_count": 99', 1)
    artifact.write_text(payload, encoding="utf-8")

    errors = validate_p2_normalization_artifact(
        tmp_path / "data/manifests/p2_normalization_manifest.json",
        repo_root=tmp_path,
    )

    assert any("artifact row_count mismatch" in error for error in errors)

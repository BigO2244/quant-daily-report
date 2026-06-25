from __future__ import annotations

from pathlib import Path

from research_data import load_constituents, load_institutional_holdings, load_news_metadata
from research_data.hydration import write_json
from research_data.normalization import normalize_p3
from scripts.data_hydration.validate_p3_normalization import validate_p3_normalization_artifact


def _seed_p3_raw(root: Path) -> None:
    write_json(
        root / "data/raw/etf_index_constituents/nasdaq_sharadar/sharadar_sp500_sample.json",
        {
            "source_table": "SHARADAR/SP500",
            "columns": ["date", "action", "ticker", "name"],
            "rows": [{"date": "2026-06-24", "action": "current", "ticker": "AAPL", "name": "APPLE INC"}],
        },
    )
    write_json(
        root / "data/raw/institutional_13f/sec_edgar_public/submissions_0001067983_sample.json",
        {
            "cik": "0001067983",
            "forms": ["13F-HR"],
            "records": [
                {
                    "accessionNumber": "0001193125-26-226661",
                    "form": "13F-HR",
                    "reportDate": "2026-03-31",
                    "filingDate": "2026-05-15",
                    "acceptanceDateTime": "2026-05-15T20:06:05.000Z",
                    "primaryDocument": "xslForm13F_X02/primary_doc.xml",
                }
            ],
        },
    )
    write_json(
        root / "data/raw/news_metadata/gdelt_public_news/gdelt_aapl_news_sample.json",
        {
            "articles": [
                {
                    "url": "https://example.com/aapl",
                    "title": "Apple sample",
                    "domain": "example.com",
                    "language": "English",
                    "sourcecountry": "United States",
                    "seendate": "20260518T164500Z",
                }
            ]
        },
    )


def test_normalize_p3_writes_observe_only_artifacts_and_manifest(tmp_path: Path) -> None:
    _seed_p3_raw(tmp_path)

    manifest = normalize_p3(repo_root=tmp_path, as_of_date="2026-06-24")

    assert manifest["dataset_count"] == 3
    assert manifest["failed_dataset_count"] == 0
    assert manifest["normalized_dataset_count"] == 3
    assert (tmp_path / "data/normalized/constituents/constituents.json").exists()
    assert (tmp_path / "data/normalized/institutional_holdings/form13f_filings.json").exists()
    assert (tmp_path / "data/normalized/news/news_metadata.json").exists()


def test_research_data_api_loads_p3_normalized_rows(tmp_path: Path) -> None:
    _seed_p3_raw(tmp_path)
    normalize_p3(repo_root=tmp_path, as_of_date="2026-06-24")

    constituents = load_constituents(repo_root=tmp_path)
    holdings = load_institutional_holdings(repo_root=tmp_path)
    news = load_news_metadata(repo_root=tmp_path)

    assert constituents[0]["index_id"] == "SP500"
    assert holdings[0]["holdings_detail_status"] == "FILING_METADATA_ONLY"
    assert news[0]["publication_timestamp"] == "2026-05-18T16:45:00Z"


def test_validate_p3_normalization_accepts_structural_warnings(tmp_path: Path) -> None:
    _seed_p3_raw(tmp_path)
    normalize_p3(repo_root=tmp_path, as_of_date="2026-06-24")

    errors = validate_p3_normalization_artifact(
        tmp_path / "data/manifests/p3_normalization_manifest.json",
        repo_root=tmp_path,
    )

    assert errors == []


def test_normalize_p3_reports_missing_sources_without_throwing(tmp_path: Path) -> None:
    manifest = normalize_p3(repo_root=tmp_path, as_of_date="2026-06-24", dataset_ids={"news_metadata"})

    assert manifest["dataset_count"] == 1
    assert manifest["failed_dataset_count"] == 1
    assert manifest["datasets"][0]["status"] == "MISSING_SOURCE"


def test_validate_p3_normalization_detects_row_count_drift(tmp_path: Path) -> None:
    _seed_p3_raw(tmp_path)
    normalize_p3(repo_root=tmp_path, as_of_date="2026-06-24")
    artifact = tmp_path / "data/normalized/news/news_metadata.json"
    payload = artifact.read_text(encoding="utf-8")
    payload = payload.replace('"row_count": 1', '"row_count": 99', 1)
    artifact.write_text(payload, encoding="utf-8")

    errors = validate_p3_normalization_artifact(
        tmp_path / "data/manifests/p3_normalization_manifest.json",
        repo_root=tmp_path,
    )

    assert any("artifact row_count mismatch" in error for error in errors)

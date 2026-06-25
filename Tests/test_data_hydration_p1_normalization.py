from __future__ import annotations

from pathlib import Path

from research_data import load_corporate_actions, load_dataset_freshness, load_prices, load_security_master
from research_data.hydration import write_json
from research_data.normalization import normalize_p1
from scripts.data_hydration.validate_p1_normalization import validate_p1_normalization_artifact


def _seed_p1_raw(root: Path) -> None:
    write_json(
        root / "data/raw/ohlcv_prices/yahoo_chart_public/ohlcv_prices_sample.json",
        {
            "symbol": "SPY",
            "rows": [{"timestamp": 1781098200, "close": 725.43}],
            "source_payload": {
                "chart": {
                    "result": [
                        {
                            "timestamp": [1781098200],
                            "indicators": {
                                "quote": [
                                    {
                                        "open": [720.0],
                                        "high": [728.0],
                                        "low": [719.0],
                                        "close": [725.43],
                                        "volume": [1000],
                                    }
                                ],
                                "adjclose": [{"adjclose": [724.0]}],
                            },
                        }
                    ]
                }
            },
        },
    )
    write_json(
        root / "data/raw/security_master_pit/nasdaq_sharadar/sharadar_tickers_sample.json",
        {
            "source_table": "SHARADAR/TICKERS",
            "columns": ["ticker", "permaticker", "name", "exchange", "isdelisted", "firstpricedate", "lastpricedate"],
            "rows": [
                {
                    "ticker": "AAPL",
                    "permaticker": 199059,
                    "name": "APPLE INC",
                    "exchange": "NASDAQ",
                    "isdelisted": "N",
                    "firstpricedate": "1980-12-12",
                    "lastpricedate": "2026-06-24",
                }
            ],
        },
    )
    write_json(
        root / "data/raw/corporate_actions/nasdaq_sharadar/sharadar_actions_sample.json",
        {
            "source_table": "SHARADAR/ACTIONS",
            "columns": ["date", "action", "ticker", "name", "value"],
            "rows": [
                {
                    "date": "2026-05-11",
                    "action": "dividend",
                    "ticker": "AAPL",
                    "name": "APPLE INC",
                    "value": 0.27,
                }
            ],
        },
    )
    write_json(
        root / "data/manifests/dataset_freshness.json",
        {
            "schema_version": "dataset_freshness_v1",
            "generated_at": "2026-06-24T12:00:00Z",
            "as_of_date": "2026-06-24",
            "datasets": [
                {
                    "dataset_id": "ohlcv_prices",
                    "dataset_name": "OHLCV prices",
                    "freshness_status": "OK",
                    "hydration_status": "OK",
                    "latest_ingestion_timestamp": "2026-06-24T12:00:00Z",
                    "artifact_path": "data/raw/ohlcv_prices/yahoo_chart_public/ohlcv_prices_sample.json",
                    "validation_status": "VALIDATED_JSON_SHAPE",
                    "PIT_safe_status": "PIT_SAFE_SAMPLE_AS_OF_DATED",
                    "reason": "hydration_sample_available",
                }
            ],
        },
    )


def test_normalize_p1_writes_canonical_artifacts_and_manifest(tmp_path: Path) -> None:
    _seed_p1_raw(tmp_path)

    manifest = normalize_p1(repo_root=tmp_path, as_of_date="2026-06-24")

    assert manifest["dataset_count"] == 4
    assert manifest["failed_dataset_count"] == 0
    assert (tmp_path / "data/normalized/prices/ohlcv_prices.json").exists()
    assert (tmp_path / "data/normalized/security_master/security_master.json").exists()
    assert (tmp_path / "data/normalized/corporate_actions/actions.json").exists()
    assert (tmp_path / "data/normalized/freshness/dataset_freshness.json").exists()
    assert (tmp_path / "data/manifests/p1_normalization_manifest.json").exists()


def test_research_data_api_loads_p1_normalized_rows(tmp_path: Path) -> None:
    _seed_p1_raw(tmp_path)
    normalize_p1(repo_root=tmp_path, as_of_date="2026-06-24")

    prices = load_prices(repo_root=tmp_path)
    master = load_security_master(repo_root=tmp_path)
    actions = load_corporate_actions(repo_root=tmp_path)
    freshness = load_dataset_freshness(repo_root=tmp_path)

    assert prices[0]["security_id"] == "YAHOO:SPY"
    assert master[0]["security_id"] == "SHARADAR:199059"
    assert actions[0]["action_type"] == "dividend"
    assert actions[0]["effective_date"] == "2026-05-11"
    assert freshness[0]["dataset_id"] == "ohlcv_prices"


def test_normalize_p1_reports_missing_sources_without_throwing(tmp_path: Path) -> None:
    manifest = normalize_p1(repo_root=tmp_path, as_of_date="2026-06-24", dataset_ids={"corporate_actions"})

    assert manifest["dataset_count"] == 1
    assert manifest["failed_dataset_count"] == 1
    assert manifest["datasets"][0]["status"] == "MISSING_SOURCE"


def test_validate_p1_normalization_accepts_clean_artifacts(tmp_path: Path) -> None:
    _seed_p1_raw(tmp_path)
    normalize_p1(repo_root=tmp_path, as_of_date="2026-06-24")

    errors = validate_p1_normalization_artifact(
        tmp_path / "data/manifests/p1_normalization_manifest.json",
        repo_root=tmp_path,
    )

    assert errors == []


def test_validate_p1_normalization_detects_row_count_drift(tmp_path: Path) -> None:
    _seed_p1_raw(tmp_path)
    normalize_p1(repo_root=tmp_path, as_of_date="2026-06-24")
    artifact = tmp_path / "data/normalized/prices/ohlcv_prices.json"
    payload = artifact.read_text(encoding="utf-8")
    payload = payload.replace('"row_count": 1', '"row_count": 99', 1)
    artifact.write_text(payload, encoding="utf-8")

    errors = validate_p1_normalization_artifact(
        tmp_path / "data/manifests/p1_normalization_manifest.json",
        repo_root=tmp_path,
    )

    assert any("artifact row_count mismatch" in error for error in errors)

from __future__ import annotations

from pathlib import Path

from research_data import build_feature_store, load_fundamental_features
from research_data.hydration import write_json
from scripts.data_hydration.validate_feature_store import validate_feature_store_manifest


def _seed_normalized_fundamentals(root: Path) -> None:
    write_json(
        root / "data/normalized/fundamentals/statements.json",
        {
            "schema_version": "fundamentals_pit_normalized_v1",
            "dataset_id": "fundamentals_pit",
            "generated_at": "2026-06-24T12:00:00Z",
            "as_of_date": "2026-06-24",
            "row_count": 1,
            "source_artifacts": [{"path": "data/raw/fundamentals_pit/nasdaq_sharadar/sharadar_sf1_sample.json", "sha256": "sample"}],
            "rows": [
                {
                    "fundamental_id": "fund-1",
                    "security_id": "SHARADAR_TICKER:AAPL",
                    "source_symbol": "AAPL",
                    "fiscal_period_end": "2026-03-28",
                    "filing_date": "2026-05-01",
                    "dimension": "ARQ",
                    "revenue": 100.0,
                    "net_income": 25.0,
                    "as_of_date": "2026-06-24",
                    "source": "nasdaq_sharadar",
                    "ingestion_timestamp": "2026-06-24T12:00:00Z",
                    "source_artifact_digest": "abc",
                    "security_id_resolution_status": "RESOLVED_FOR_TEST",
                    "restatement_policy": "TEST_FIXED_VERSION",
                }
            ],
            "validation": {"status": "PASS", "errors": []},
        },
    )


def test_build_feature_store_writes_fundamental_features_and_manifest(tmp_path: Path) -> None:
    _seed_normalized_fundamentals(tmp_path)

    manifest = build_feature_store(repo_root=tmp_path, as_of_date="2026-06-24")

    assert manifest["feature_set_count"] == 1
    assert manifest["failed_feature_set_count"] == 0
    assert (tmp_path / "data/features/fundamental_features/features.json").exists()
    assert (tmp_path / "data/manifests/feature_store_manifest.json").exists()


def test_feature_api_loads_fundamental_features(tmp_path: Path) -> None:
    _seed_normalized_fundamentals(tmp_path)
    build_feature_store(repo_root=tmp_path, as_of_date="2026-06-24")

    rows = load_fundamental_features(repo_root=tmp_path)

    assert rows[0]["net_margin"] == 0.25
    assert rows[0]["feature_version"] == "fundamental_features_v1_observe_only"


def test_validate_feature_store_accepts_clean_feature_artifacts(tmp_path: Path) -> None:
    _seed_normalized_fundamentals(tmp_path)
    build_feature_store(repo_root=tmp_path, as_of_date="2026-06-24")

    errors = validate_feature_store_manifest(
        tmp_path / "data/manifests/feature_store_manifest.json",
        repo_root=tmp_path,
    )

    assert errors == []


def test_build_feature_store_reports_missing_input_without_throwing(tmp_path: Path) -> None:
    manifest = build_feature_store(repo_root=tmp_path, as_of_date="2026-06-24")

    assert manifest["feature_set_count"] == 1
    assert manifest["failed_feature_set_count"] == 1
    assert manifest["feature_sets"][0]["status"] == "MISSING_INPUT"


def test_validate_feature_store_detects_row_count_drift(tmp_path: Path) -> None:
    _seed_normalized_fundamentals(tmp_path)
    build_feature_store(repo_root=tmp_path, as_of_date="2026-06-24")
    artifact = tmp_path / "data/features/fundamental_features/features.json"
    payload = artifact.read_text(encoding="utf-8")
    payload = payload.replace('"row_count": 1', '"row_count": 99', 1)
    artifact.write_text(payload, encoding="utf-8")

    errors = validate_feature_store_manifest(
        tmp_path / "data/manifests/feature_store_manifest.json",
        repo_root=tmp_path,
    )

    assert any("artifact row_count mismatch" in error for error in errors)

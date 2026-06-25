from __future__ import annotations

from pathlib import Path

from research_data import build_feature_store, load_features, load_fundamental_features, load_macro_regime_features
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


def _seed_normalized_macro_inputs(root: Path) -> None:
    write_json(
        root / "data/normalized/macro/macro_rates.json",
        {
            "schema_version": "macro_rates_normalized_v1",
            "row_count": 1,
            "rows": [
                {
                    "observation_date": "2026-06-21",
                    "series_id": "DGS10",
                    "value_percent": 4.25,
                    "release_date": None,
                    "publication_date_status": "UNVERIFIED_FRED_CSV_NO_RELEASE_DATE",
                }
            ],
        },
    )
    write_json(
        root / "data/normalized/macro/yield_curve.json",
        {
            "schema_version": "yield_curve_normalized_v1",
            "row_count": 1,
            "rows": [
                {
                    "observation_date": "2026-06-21",
                    "dgs10": 4.25,
                    "slope_10y_2y": -0.15,
                    "slope_30y_10y": 0.4,
                    "release_date": None,
                    "publication_date_status": "UNVERIFIED_FRED_CSV_NO_RELEASE_DATE",
                }
            ],
        },
    )
    write_json(
        root / "data/normalized/macro/credit_spreads.json",
        {
            "schema_version": "credit_spreads_normalized_v1",
            "row_count": 1,
            "rows": [
                {
                    "observation_date": "2026-06-21",
                    "series_id": "BAA10Y",
                    "spread_percent": 3.2,
                    "release_date": None,
                    "publication_date_status": "UNVERIFIED_FRED_CSV_NO_RELEASE_DATE",
                }
            ],
        },
    )
    write_json(
        root / "data/normalized/volatility/vix.json",
        {
            "schema_version": "vix_volatility_regime_normalized_v1",
            "row_count": 1,
            "rows": [
                {
                    "observation_date": "2026-06-21",
                    "vix_close": 27.5,
                }
            ],
        },
    )


def test_build_feature_store_writes_fundamental_features_and_manifest(tmp_path: Path) -> None:
    _seed_normalized_fundamentals(tmp_path)

    manifest = build_feature_store(repo_root=tmp_path, as_of_date="2026-06-24", feature_sets={"fundamental_features"})

    assert manifest["feature_set_count"] == 1
    assert manifest["failed_feature_set_count"] == 0
    assert (tmp_path / "data/features/fundamental_features/features.json").exists()
    assert (tmp_path / "data/manifests/feature_store_manifest.json").exists()


def test_feature_api_loads_fundamental_features(tmp_path: Path) -> None:
    _seed_normalized_fundamentals(tmp_path)
    build_feature_store(repo_root=tmp_path, as_of_date="2026-06-24", feature_sets={"fundamental_features"})

    rows = load_fundamental_features(repo_root=tmp_path)

    assert rows[0]["net_margin"] == 0.25
    assert rows[0]["feature_version"] == "fundamental_features_v1_observe_only"


def test_validate_feature_store_accepts_clean_feature_artifacts(tmp_path: Path) -> None:
    _seed_normalized_fundamentals(tmp_path)
    build_feature_store(repo_root=tmp_path, as_of_date="2026-06-24", feature_sets={"fundamental_features"})

    errors = validate_feature_store_manifest(
        tmp_path / "data/manifests/feature_store_manifest.json",
        repo_root=tmp_path,
    )

    assert errors == []


def test_build_feature_store_reports_missing_input_without_throwing(tmp_path: Path) -> None:
    manifest = build_feature_store(repo_root=tmp_path, as_of_date="2026-06-24", feature_sets={"fundamental_features"})

    assert manifest["feature_set_count"] == 1
    assert manifest["failed_feature_set_count"] == 1
    assert manifest["feature_sets"][0]["status"] == "MISSING_INPUT"


def test_validate_feature_store_detects_row_count_drift(tmp_path: Path) -> None:
    _seed_normalized_fundamentals(tmp_path)
    build_feature_store(repo_root=tmp_path, as_of_date="2026-06-24", feature_sets={"fundamental_features"})
    artifact = tmp_path / "data/features/fundamental_features/features.json"
    payload = artifact.read_text(encoding="utf-8")
    payload = payload.replace('"row_count": 1', '"row_count": 99', 1)
    artifact.write_text(payload, encoding="utf-8")

    errors = validate_feature_store_manifest(
        tmp_path / "data/manifests/feature_store_manifest.json",
        repo_root=tmp_path,
    )

    assert any("artifact row_count mismatch" in error for error in errors)


def test_build_feature_store_writes_macro_regime_features(tmp_path: Path) -> None:
    _seed_normalized_macro_inputs(tmp_path)

    manifest = build_feature_store(repo_root=tmp_path, as_of_date="2026-06-24", feature_sets={"macro_regime_features"})
    rows = load_macro_regime_features(repo_root=tmp_path)

    assert manifest["feature_set_count"] == 1
    assert manifest["built_feature_set_count"] == 1
    assert rows[0]["yield_curve_inverted"] is True
    assert rows[0]["credit_stress"] is True
    assert rows[0]["high_volatility"] is True
    assert rows[0]["release_date_policy_status"] == "UNVERIFIED_RELEASE_DATE_POLICY"


def test_feature_api_loads_all_available_feature_sets(tmp_path: Path) -> None:
    _seed_normalized_fundamentals(tmp_path)
    _seed_normalized_macro_inputs(tmp_path)
    build_feature_store(repo_root=tmp_path, as_of_date="2026-06-24")

    rows = load_features(repo_root=tmp_path)
    feature_sets = {row["feature_set"] for row in rows}

    assert {"fundamental_features", "macro_regime_features"} <= feature_sets

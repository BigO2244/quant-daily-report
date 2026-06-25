from __future__ import annotations

from pathlib import Path

from research_data.hydration import write_json
from research_data.observability import build_research_data_observability
from scripts.data_hydration.validate_research_data_observability import validate_research_data_observability


def _seed_observable_price(root: Path) -> None:
    write_json(
        root / "data/normalized/prices/ohlcv_prices.json",
        {
            "schema_version": "ohlcv_prices_normalized_v1",
            "dataset_id": "ohlcv_prices",
            "generated_at": "2026-06-24T12:00:00Z",
            "as_of_date": "2026-06-24",
            "row_count": 1,
            "source_artifacts": [{"path": "data/raw/ohlcv_prices/source.json", "sha256": "missing-source-ok"}],
            "rows": [
                {
                    "security_id": "YAHOO:SPY",
                    "trade_date": "2026-06-23",
                    "close": 600.0,
                    "as_of_date": "2026-06-24",
                }
            ],
            "validation": {"status": "PASS", "errors": []},
        },
    )
    write_json(
        root / "data/manifests/p1_normalization_manifest.json",
        {
            "schema_version": "p1_normalization_manifest_v1",
            "datasets": [
                {
                    "dataset_id": "ohlcv_prices",
                    "status": "OK",
                    "artifact_path": str(root / "data/normalized/prices/ohlcv_prices.json"),
                    "row_count": 1,
                    "validation_status": "PASS",
                    "validation_errors": [],
                }
            ],
        },
    )
    write_json(
        root / "data/manifests/dataset_freshness.json",
        {
            "schema_version": "dataset_freshness_v1",
            "datasets": [
                {
                    "dataset_id": "ohlcv_prices",
                    "freshness_status": "OK",
                    "hydration_status": "OK",
                    "latest_ingestion_timestamp": "2026-06-24T12:00:00Z",
                    "validation_status": "VALIDATED_JSON_SHAPE",
                    "PIT_safe_status": "PIT_SAFE_SAMPLE_AS_OF_DATED",
                    "records_written": 1,
                    "reason": "hydration_sample_available",
                }
            ],
        },
    )


def test_observability_manifest_reports_artifact_and_blocker_state(tmp_path: Path) -> None:
    _seed_observable_price(tmp_path)

    manifest = build_research_data_observability(repo_root=tmp_path, as_of_date="2026-06-24")

    rows = {row["dataset_id"]: row for row in manifest["datasets"]}
    assert manifest["dataset_count"] >= 21
    assert rows["ohlcv_prices"]["readiness_status"] == "OBSERVE_ONLY"
    assert rows["ohlcv_prices"]["artifact_exists"] is True
    assert rows["ohlcv_prices"]["lineage_status"] == "LINEAGE_RECORDED"
    assert rows["analyst_estimate_revisions"]["readiness_status"] == "BLOCKED"
    assert rows["options_iv_open_interest"]["readiness_status"] in {"BLOCKED", "MISSING_ARTIFACT"}


def test_validate_observability_accepts_clean_manifest(tmp_path: Path) -> None:
    _seed_observable_price(tmp_path)
    build_research_data_observability(repo_root=tmp_path, as_of_date="2026-06-24")

    errors = validate_research_data_observability(
        tmp_path / "data/manifests/research_data_observability.json",
        repo_root=tmp_path,
    )

    assert errors == []


def test_validate_observability_detects_artifact_drift(tmp_path: Path) -> None:
    _seed_observable_price(tmp_path)
    build_research_data_observability(repo_root=tmp_path, as_of_date="2026-06-24")
    artifact = tmp_path / "data/normalized/prices/ohlcv_prices.json"
    payload = artifact.read_text(encoding="utf-8")
    artifact.write_text(payload.replace("600.0", "601.0"), encoding="utf-8")

    errors = validate_research_data_observability(
        tmp_path / "data/manifests/research_data_observability.json",
        repo_root=tmp_path,
    )

    assert any("artifact_sha256 mismatch" in error for error in errors)

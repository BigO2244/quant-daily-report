from __future__ import annotations

from pathlib import Path

from research_data.hydration import write_json
from research_data.migration import build_sleeve_migration_readiness
from scripts.data_hydration.validate_sleeve_migration_readiness import validate_sleeve_migration_readiness


def _seed_sleeves(root: Path) -> None:
    write_json(
        root / "research_registry/sleeves/manifest.json",
        {
            "schema_version": "caerus_sleeve_manifest_v1",
            "sleeves": [
                {
                    "sleeve_id": "polaris",
                    "strategy_id": "caerus_polaris",
                    "family": "core_momentum",
                    "lifecycle_stage": "paper_observed",
                    "implementation_status": "existing_strategy_reference",
                    "behavior_change_allowed": False,
                },
                {
                    "sleeve_id": "cygnus",
                    "strategy_id": "caerus_cygnus",
                    "family": "earnings_drift",
                    "lifecycle_stage": "shelved_v0",
                    "implementation_status": "research_placeholder",
                    "behavior_change_allowed": False,
                },
            ],
        },
    )


def _obs_row(dataset_id: str, readiness: str = "OBSERVE_ONLY", freshness: str = "OK") -> dict[str, object]:
    return {
        "dataset_id": dataset_id,
        "readiness_status": readiness,
        "validation_status": "PASS",
        "freshness_status": freshness,
        "PIT_safe_status": "PIT_SAFE_SAMPLE_AS_OF_DATED",
        "lineage_status": "LINEAGE_RECORDED",
        "artifact_exists": readiness == "OBSERVE_ONLY",
        "row_count": 1 if readiness == "OBSERVE_ONLY" else 0,
        "blocker_reason": "" if readiness == "OBSERVE_ONLY" else "blocked for test",
    }


def _seed_observability(root: Path) -> None:
    rows = [
        _obs_row("ohlcv_prices"),
        _obs_row("security_master_pit"),
        _obs_row("corporate_actions"),
        _obs_row("dataset_freshness"),
        _obs_row("fundamentals_pit"),
        _obs_row("fundamental_features"),
        _obs_row("sec_10q_10k_metadata"),
        _obs_row("analyst_estimate_revisions", "BLOCKED"),
    ]
    write_json(
        root / "data/manifests/research_data_observability.json",
        {
            "schema_version": "research_data_observability_v1",
            "as_of_date": "2026-06-24",
            "datasets": rows,
        },
    )


def test_sleeve_migration_readiness_classifies_ready_and_blocked_sleeves(tmp_path: Path) -> None:
    _seed_sleeves(tmp_path)
    _seed_observability(tmp_path)

    payload = build_sleeve_migration_readiness(repo_root=tmp_path, as_of_date="2026-06-24")

    sleeves = {row["sleeve_id"]: row for row in payload["sleeves"]}
    assert sleeves["polaris"]["migration_readiness_status"] == "READY_OBSERVE_ONLY"
    assert sleeves["cygnus"]["migration_readiness_status"] == "BLOCKED"
    assert sleeves["cygnus"]["blocking_dataset_ids"] == ["analyst_estimate_revisions"]
    assert payload["broker_submission_invoked"] is False
    assert payload["sleeve_runtime_invoked"] is False
    assert (tmp_path / "outputs/research/data_migration/2026-06-24/migration_readiness.md").exists()


def test_validate_sleeve_migration_readiness_accepts_clean_artifact(tmp_path: Path) -> None:
    _seed_sleeves(tmp_path)
    _seed_observability(tmp_path)
    build_sleeve_migration_readiness(repo_root=tmp_path, as_of_date="2026-06-24")

    errors = validate_sleeve_migration_readiness(tmp_path / "outputs/research/data_migration/2026-06-24/migration_readiness.json")

    assert errors == []

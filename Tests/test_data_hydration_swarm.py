from __future__ import annotations

from pathlib import Path

from research_data.hydration import BaseHydrationAdapter, HydrationContext, HydrationResult, utc_now_iso
from research_data.hydration import write_json
from scripts.data_hydration.run_data_hydration_swarm import parse_args, run_swarm
from scripts.data_hydration.validate_hydration_swarm import validate_swarm_artifact


def test_swarm_dry_run_classifies_every_catalog_dataset(tmp_path: Path) -> None:
    payload = run_swarm(repo_root=tmp_path, dry_run=True, as_of_date="2026-06-24")

    assert payload["summary"]["dataset_count"] >= 20
    assert payload["summary"]["broker_submission_invoked"] is False
    assert len(payload["datasets"]) == payload["summary"]["dataset_count"]
    assert all(row["hydration_attempted"] for row in payload["datasets"])
    freshness_row = next(row for row in payload["datasets"] if row["dataset_id"] == "dataset_freshness")
    assert freshness_row["final_status"] == "OK"
    assert freshness_row["attempted_sources"] == ["internal_dataset_freshness"]
    macro_feature_row = next(row for row in payload["datasets"] if row["dataset_id"] == "macro_regime_features")
    assert macro_feature_row["attempted_sources"] == ["internal_derived_features"]
    assert (tmp_path / "data" / "manifests" / "research_data_catalog.json").exists()
    assert (tmp_path / "data" / "manifests" / "dataset_freshness.json").exists()
    assert (tmp_path / "data" / "manifests" / "hydration_capability_matrix.json").exists()
    latest = tmp_path / "data" / "hydration_logs" / "latest_hydration_swarm.json"
    assert latest.exists()
    assert validate_swarm_artifact(latest) == []


class OkAdapter(BaseHydrationAdapter):
    source_name = "fred_public_csv"
    source_type = "free_public_macro"

    def supports(self, dataset_id: str) -> bool:
        return True

    def hydrate(self, dataset: dict, context: HydrationContext) -> HydrationResult:
        artifact = context.output_path("raw", dataset["dataset_id"], self.source_name, "sample.json")
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text('{"ok": true}\n', encoding="utf-8")
        return self.result(
            dataset,
            context,
            status="OK",
            started_at=utc_now_iso(),
            records_written=1,
            artifact_path=artifact,
            effective_date_available=True,
            pit_safe_status="PIT_SAFE_SAMPLE_AS_OF_DATED",
            validation_status="VALIDATED_JSON_SHAPE",
        )


def test_swarm_limit_sample_uses_adapter_and_writes_success(tmp_path: Path) -> None:
    payload = run_swarm(
        repo_root=tmp_path,
        limit_sample=True,
        as_of_date="2026-06-24",
        dataset_ids={"macro_rates"},
        adapter_registry={"fred_public_csv": OkAdapter()},
    )

    assert payload["summary"]["dataset_count"] == 1
    assert payload["summary"]["successful_dataset_count"] == 1
    assert payload["datasets"][0]["final_status"] == "OK"
    assert payload["datasets"][0]["records_written"] == 1
    assert payload["datasets"][0]["artifact_path"]


def test_swarm_classifies_existing_derived_feature_artifact(tmp_path: Path) -> None:
    write_json(
        tmp_path / "data/features/macro_regime_features/features.json",
        {
            "schema_version": "macro_regime_features_v1",
            "feature_set": "macro_regime_features",
            "row_count": 1,
            "rows": [
                {
                    "feature_id": "macro-1",
                    "PIT_safe_status": "PIT_DERIVED_FROM_NORMALIZED_MACRO_OBSERVE_ONLY",
                }
            ],
            "validation": {"status": "PASS", "errors": []},
        },
    )

    payload = run_swarm(
        repo_root=tmp_path,
        limit_sample=True,
        as_of_date="2026-06-24",
        dataset_ids={"macro_regime_features"},
    )

    assert payload["summary"]["successful_dataset_count"] == 1
    assert payload["datasets"][0]["final_status"] == "OK"
    assert payload["datasets"][0]["records_written"] == 1


class SharadarOkAdapter(BaseHydrationAdapter):
    source_name = "nasdaq_sharadar"
    source_type = "paid_vendor_optional"

    def supports(self, dataset_id: str) -> bool:
        return True

    def hydrate(self, dataset: dict, context: HydrationContext) -> HydrationResult:
        return self.result(
            dataset,
            context,
            status="PARTIAL",
            started_at=utc_now_iso(),
            records_written=1,
            effective_date_available=True,
            pit_safe_status="PIT_SAFE_SAMPLE_AS_OF_DATED",
            validation_status="VALIDATED_NASDAQ_DATA_LINK_DATATABLE_SHAPE",
        )


def test_swarm_source_filter_can_probe_sharadar_after_public_sources(tmp_path: Path) -> None:
    payload = run_swarm(
        repo_root=tmp_path,
        limit_sample=True,
        as_of_date="2026-06-24",
        dataset_ids={"security_master_pit"},
        source_names={"nasdaq_sharadar"},
        adapter_registry={"nasdaq_sharadar": SharadarOkAdapter()},
    )

    assert payload["summary"]["dataset_count"] == 1
    assert payload["summary"]["successful_dataset_count"] == 1
    assert payload["datasets"][0]["attempted_sources"] == ["nasdaq_sharadar"]
    assert payload["datasets"][0]["final_status"] == "PARTIAL"


def test_cli_accepts_space_separated_dataset_and_source_filters() -> None:
    args = parse_args([
        "--dry-run",
        "--datasets",
        "corporate_actions",
        "security_master_pit",
        "--sources",
        "nasdaq_sharadar",
    ])

    assert args.datasets == ["corporate_actions", "security_master_pit"]
    assert args.sources == ["nasdaq_sharadar"]

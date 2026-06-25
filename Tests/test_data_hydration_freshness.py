from __future__ import annotations

import json
from pathlib import Path

from research_data.hydration import BaseHydrationAdapter, HydrationContext, HydrationResult, utc_now_iso
from scripts.data_hydration.run_data_hydration_swarm import run_swarm
from scripts.data_hydration.validate_dataset_freshness import validate_freshness_artifact


class PartialAdapter(BaseHydrationAdapter):
    source_name = "sec_edgar_public"
    source_type = "free_public_sec"

    def supports(self, dataset_id: str) -> bool:
        return True

    def hydrate(self, dataset: dict, context: HydrationContext) -> HydrationResult:
        return self.result(
            dataset,
            context,
            status="PARTIAL",
            started_at=utc_now_iso(),
            records_written=2,
            pit_safe_status="PIT_SAFE_SAMPLE_FILING_DATE_PRESENT",
            validation_status="VALIDATED_JSON_SHAPE",
        )


def test_dataset_freshness_artifact_is_valid(tmp_path: Path) -> None:
    run_swarm(
        repo_root=tmp_path,
        limit_sample=True,
        as_of_date="2026-06-24",
        dataset_ids={"sec_8k_events"},
        adapter_registry={"sec_edgar_public": PartialAdapter()},
    )

    path = tmp_path / "data" / "manifests" / "dataset_freshness.json"
    assert validate_freshness_artifact(path) == []
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["datasets"][0]["freshness_status"] == "WARN_PARTIAL"
    assert payload["datasets"][0]["PIT_safe_status"] == "PIT_SAFE_SAMPLE_FILING_DATE_PRESENT"

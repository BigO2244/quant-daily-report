from __future__ import annotations

from pathlib import Path

from research_data.hydration import BaseHydrationAdapter, HydrationContext, HydrationResult, read_json, utc_now_iso


class DerivedFeatureAdapter(BaseHydrationAdapter):
    source_name = "internal_derived_features"
    source_type = "internal_derived"

    _FEATURE_ARTIFACTS = {
        "fundamental_features": Path("data/features/fundamental_features/features.json"),
        "macro_regime_features": Path("data/features/macro_regime_features/features.json"),
    }
    _SUPPORTED = set(_FEATURE_ARTIFACTS) | {"news_sentiment_embeddings"}

    def supports(self, dataset_id: str) -> bool:
        return dataset_id in self._SUPPORTED

    def hydrate(self, dataset: dict, context: HydrationContext) -> HydrationResult:
        started_at = utc_now_iso()
        if context.dry_run:
            return self.dry_run_result(dataset, context, started_at)
        dataset_id = str(dataset["dataset_id"])
        if dataset_id in self._FEATURE_ARTIFACTS:
            artifact = context.repo_root / self._FEATURE_ARTIFACTS[dataset_id]
            if not artifact.exists():
                return self.result(
                    dataset,
                    context,
                    status="SOURCE_UNAVAILABLE",
                    started_at=started_at,
                    failure_reason=f"Derived feature artifact is missing: {artifact}",
                    recommended_user_action="Run scripts/data_hydration/build_feature_store.py after normalized inputs are available.",
                )
            payload = read_json(artifact)
            validation = payload.get("validation") or {}
            return self.result(
                dataset,
                context,
                status="OK" if validation.get("status") == "PASS" else "PARTIAL",
                started_at=started_at,
                records_written=int(payload.get("row_count") or 0),
                artifact_path=artifact,
                effective_date_available=True,
                pit_safe_status=_feature_pit_status(payload),
                validation_status=f"FEATURE_VALIDATION_{validation.get('status') or 'UNKNOWN'}",
            )
        return self.result(
            dataset,
            context,
            status="BLOCKED_ACCOUNT_REQUIRED",
            started_at=started_at,
            failure_reason="Derived news sentiment/embeddings require approved source text and model-version policy.",
            recommended_user_action="Approve model and source-text lineage before deriving sentiment or embeddings.",
        )


def _feature_pit_status(payload: dict) -> str:
    rows = payload.get("rows") or []
    statuses = sorted({row.get("PIT_safe_status") for row in rows if row.get("PIT_safe_status")})
    if len(statuses) == 1:
        return str(statuses[0])
    if statuses:
        return "PIT_DERIVED_FROM_FEATURE_ROWS_MIXED_STATUS"
    return "PIT_DERIVED_FEATURE_ARTIFACT_NO_ROW_STATUS"


class DatasetFreshnessAdapter(BaseHydrationAdapter):
    source_name = "internal_dataset_freshness"
    source_type = "internal_manifest"

    def supports(self, dataset_id: str) -> bool:
        return dataset_id == "dataset_freshness"

    def hydrate(self, dataset: dict, context: HydrationContext) -> HydrationResult:
        started_at = utc_now_iso()
        return self.result(
            dataset,
            context,
            status="OK",
            started_at=started_at,
            failure_reason="",
            recommended_user_action="",
            records_written=1,
            artifact_path=context.repo_root / "data" / "manifests" / "dataset_freshness.json",
            effective_date_available=True,
            filing_date_available=False,
            pit_safe_status="PIT_SAFE_INTERNAL_RUN_METADATA",
            validation_status="VALIDATED_INTERNAL_MANIFEST_SHAPE",
        )

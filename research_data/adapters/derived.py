from __future__ import annotations

from research_data.hydration import BaseHydrationAdapter, HydrationContext, HydrationResult, utc_now_iso


class DerivedFeatureAdapter(BaseHydrationAdapter):
    source_name = "internal_derived_features"
    source_type = "internal_derived"

    _SUPPORTED = {"fundamental_features", "news_sentiment_embeddings"}

    def supports(self, dataset_id: str) -> bool:
        return dataset_id in self._SUPPORTED

    def hydrate(self, dataset: dict, context: HydrationContext) -> HydrationResult:
        started_at = utc_now_iso()
        if context.dry_run:
            return self.dry_run_result(dataset, context, started_at)
        dataset_id = str(dataset["dataset_id"])
        if dataset_id == "fundamental_features":
            return self.result(
                dataset,
                context,
                status="BLOCKED_ACCOUNT_REQUIRED",
                started_at=started_at,
                failure_reason="Fundamental features require validated FR-DH-004 PIT fundamentals before derivation.",
                recommended_user_action="Implement PIT fundamentals and feature definitions before deriving this dataset.",
            )
        return self.result(
            dataset,
            context,
            status="BLOCKED_ACCOUNT_REQUIRED",
            started_at=started_at,
            failure_reason="Derived news sentiment/embeddings require approved source text and model-version policy.",
            recommended_user_action="Approve model and source-text lineage before deriving sentiment or embeddings.",
        )


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

from __future__ import annotations

from research_data.hydration import BaseHydrationAdapter, HydrationContext, HydrationResult, utc_now_iso


class FinraAdapter(BaseHydrationAdapter):
    source_name = "finra_public"
    source_type = "free_public_or_account_required"

    _SUPPORTED = {"short_interest"}

    def supports(self, dataset_id: str) -> bool:
        return dataset_id in self._SUPPORTED

    def hydrate(self, dataset: dict, context: HydrationContext) -> HydrationResult:
        started_at = utc_now_iso()
        if context.dry_run:
            return self.dry_run_result(dataset, context, started_at)
        return self.result(
            dataset,
            context,
            status="SOURCE_UNAVAILABLE",
            started_at=started_at,
            failure_reason="No stable, schema-approved FINRA short-interest sample endpoint is configured.",
            recommended_user_action="Identify the exact FINRA or exchange short-interest endpoint and publication-lag fields.",
        )

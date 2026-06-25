from __future__ import annotations

from research_data.hydration import BaseHydrationAdapter, HydrationContext, HydrationResult, utc_now_iso


class PublicReferenceAdapter(BaseHydrationAdapter):
    source_name = "public_reference_stub"
    source_type = "free_public_candidate"

    _SUPPORTED = {"etf_index_constituents"}

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
            failure_reason="No schema-approved public constituent source is configured for the swarm.",
            recommended_user_action="Approve an ETF/index constituent source and PIT membership-date policy.",
        )

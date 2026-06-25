from __future__ import annotations

from research_data.hydration import BaseHydrationAdapter, HydrationContext, HydrationResult, utc_now_iso


class CboeOrVixAdapter(BaseHydrationAdapter):
    source_name = "cboe_or_vix"
    source_type = "free_or_account_required_options_volatility"

    _SUPPORTED = {"vix_volatility_regime", "options_iv_open_interest"}

    def supports(self, dataset_id: str) -> bool:
        return dataset_id in self._SUPPORTED

    def hydrate(self, dataset: dict, context: HydrationContext) -> HydrationResult:
        started_at = utc_now_iso()
        if context.dry_run:
            return self.dry_run_result(dataset, context, started_at)
        if str(dataset["dataset_id"]) == "vix_volatility_regime":
            return self.result(
                dataset,
                context,
                status="SOURCE_UNAVAILABLE",
                started_at=started_at,
                failure_reason="The Stooq adapter is the configured public VIX sample source; no separate CBOE sample endpoint is approved here.",
                recommended_user_action="Keep Stooq for sample VIX, or approve a CBOE endpoint and usage policy.",
            )
        return self.result(
            dataset,
            context,
            status="BLOCKED_ACCOUNT_REQUIRED",
            started_at=started_at,
            failure_reason="Options implied volatility/open interest generally requires an approved options data account or vendor plan.",
            recommended_user_action="Choose an options data vendor and licensing plan before implementing this adapter.",
        )

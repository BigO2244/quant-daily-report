from __future__ import annotations

from research_data.hydration import (
    BaseHydrationAdapter,
    CredentialOrSubscriptionError,
    HydrationContext,
    HydrationResult,
    RateLimitedError,
    request_get,
    status_from_exception,
    utc_now_iso,
)


class FredAdapter(BaseHydrationAdapter):
    source_name = "fred_public_csv"
    source_type = "free_public_macro"

    _SERIES = {
        "macro_rates": ["FEDFUNDS", "DGS10"],
        "yield_curve": ["DGS2", "DGS10", "DGS30"],
        "credit_spreads": ["BAA10Y"],
    }

    def supports(self, dataset_id: str) -> bool:
        return dataset_id in self._SERIES

    def hydrate(self, dataset: dict, context: HydrationContext) -> HydrationResult:
        started_at = utc_now_iso()
        dataset_id = str(dataset["dataset_id"])
        if context.dry_run:
            return self.dry_run_result(dataset, context, started_at)
        series = self._SERIES[dataset_id]
        combined: dict[str, str] = {}
        total_records = 0
        try:
            for series_id in series:
                url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
                response = request_get(url, timeout=context.timeout_seconds)
                text = response.text
                lines = [line for line in text.splitlines() if line.strip()]
                if len(lines) <= 1:
                    continue
                combined[f"{series_id}.csv"] = text
                total_records += max(0, len(lines) - 1)
            if not combined:
                return self.result(
                    dataset,
                    context,
                    status="EMPTY_RESULT",
                    started_at=started_at,
                    failure_reason="FRED public CSV returned no sample rows.",
                    recommended_user_action="Verify series ids or retry later.",
                )
            artifact = context.output_path("raw", dataset_id, self.source_name, f"{dataset_id}_sample.json")
            payload = {"series": combined, "series_ids": series}
            from research_data.hydration import write_json

            write_json(artifact, payload)
            return self.result(
                dataset,
                context,
                status="OK",
                started_at=started_at,
                records_written=total_records,
                artifact_path=artifact,
                effective_date_available=True,
                pit_safe_status="PIT_LIMITED_PUBLIC_CSV_RELEASE_DATES_NOT_VERIFIED",
                validation_status="VALIDATED_CSV_SHAPE",
            )
        except (RateLimitedError, CredentialOrSubscriptionError, Exception) as exc:
            status, reason = status_from_exception(exc)
            return self.result(
                dataset,
                context,
                status=status,
                started_at=started_at,
                failure_reason=reason,
                recommended_user_action="Retry later or configure a macro source with release-date metadata.",
            )

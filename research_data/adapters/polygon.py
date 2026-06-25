from __future__ import annotations

import os

from research_data.hydration import BaseHydrationAdapter, HydrationContext, HydrationResult, request_get, status_from_exception, utc_now_iso, write_json


class PolygonAdapter(BaseHydrationAdapter):
    source_name = "polygon"
    source_type = "paid_vendor_optional"

    _SUPPORTED = {"ohlcv_prices", "options_iv_open_interest", "analyst_estimate_revisions", "news_metadata", "vix_volatility_regime"}

    def supports(self, dataset_id: str) -> bool:
        return dataset_id in self._SUPPORTED

    def hydrate(self, dataset: dict, context: HydrationContext) -> HydrationResult:
        started_at = utc_now_iso()
        api_key = os.environ.get("POLYGON_API_KEY")
        if not api_key:
            return self.result(
                dataset,
                context,
                status="BLOCKED_CREDENTIALS",
                started_at=started_at,
                failure_reason="POLYGON_API_KEY is not present.",
                recommended_user_action="Configure Polygon credentials if this paid source should be evaluated.",
            )
        if context.dry_run:
            return self.dry_run_result(dataset, context, started_at)
        dataset_id = str(dataset["dataset_id"])
        if dataset_id == "news_metadata":
            url = f"https://api.polygon.io/v2/reference/news?ticker=AAPL&limit=5&apiKey={api_key}"
        elif dataset_id == "ohlcv_prices":
            url = f"https://api.polygon.io/v2/aggs/ticker/SPY/range/1/day/2026-01-02/2026-01-09?limit=5&apiKey={api_key}"
        else:
            return self.result(
                dataset,
                context,
                status="BLOCKED_SUBSCRIPTION",
                started_at=started_at,
                failure_reason="Polygon source likely requires a paid plan or a dataset-specific endpoint not approved in this swarm.",
                recommended_user_action="Decide whether to license Polygon for this dataset and add a scoped entitlement probe.",
            )
        try:
            payload = request_get(url, timeout=context.timeout_seconds).json()
            records = payload.get("results") or []
            artifact = context.output_path("raw", dataset_id, self.source_name, f"{dataset_id}_sample.json")
            write_json(artifact, payload)
            if not records:
                return self.result(dataset, context, status="EMPTY_RESULT", started_at=started_at, failure_reason="Polygon returned no sample records.", artifact_path=artifact)
            return self.result(
                dataset,
                context,
                status="PARTIAL",
                started_at=started_at,
                records_written=len(records),
                artifact_path=artifact,
                effective_date_available=True,
                pit_safe_status="PIT_SAFE_SAMPLE_AS_OF_DATED",
                validation_status="VALIDATED_JSON_SHAPE",
            )
        except Exception as exc:
            status, reason = status_from_exception(exc)
            return self.result(dataset, context, status=status, started_at=started_at, failure_reason=reason, recommended_user_action="Check Polygon credentials, plan tier, and rate limits.")

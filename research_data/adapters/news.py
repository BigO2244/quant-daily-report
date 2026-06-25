from __future__ import annotations

from research_data.hydration import BaseHydrationAdapter, CredentialOrSubscriptionError, HydrationContext, HydrationResult, RateLimitedError, request_get, status_from_exception, utc_now_iso, write_json


class NewsAdapter(BaseHydrationAdapter):
    source_name = "gdelt_public_news"
    source_type = "free_public_news"

    _SUPPORTED = {"news_metadata", "news_sentiment_embeddings", "alternative_datasets"}

    def supports(self, dataset_id: str) -> bool:
        return dataset_id in self._SUPPORTED

    def hydrate(self, dataset: dict, context: HydrationContext) -> HydrationResult:
        started_at = utc_now_iso()
        dataset_id = str(dataset["dataset_id"])
        if context.dry_run:
            return self.dry_run_result(dataset, context, started_at)
        if dataset_id == "news_sentiment_embeddings":
            return self.result(
                dataset,
                context,
                status="BLOCKED_ACCOUNT_REQUIRED",
                started_at=started_at,
                failure_reason="Sentiment and embeddings require an approved model/source and versioned feature-generation policy.",
                recommended_user_action="Approve a sentiment/embedding source and PIT-safe model-versioning plan.",
            )
        if dataset_id == "alternative_datasets":
            return self.result(
                dataset,
                context,
                status="BLOCKED_ACCOUNT_REQUIRED",
                started_at=started_at,
                failure_reason="No alternative dataset is approved by default.",
                recommended_user_action="Propose a specific alternative dataset with license, PIT, and validation review.",
            )
        url = "https://api.gdeltproject.org/api/v2/doc/doc?query=AAPL&mode=ArtList&format=json&maxrecords=5"
        try:
            payload = request_get(url, timeout=context.timeout_seconds).json()
            articles = payload.get("articles") or []
            artifact = context.output_path("raw", dataset_id, self.source_name, "gdelt_aapl_news_sample.json")
            write_json(artifact, payload)
            if not articles:
                return self.result(
                    dataset,
                    context,
                    status="EMPTY_RESULT",
                    started_at=started_at,
                    failure_reason="GDELT returned no sample articles.",
                    recommended_user_action="Retry later or choose an approved news source.",
                    artifact_path=artifact,
                )
            return self.result(
                dataset,
                context,
                status="PARTIAL",
                started_at=started_at,
                records_written=len(articles),
                artifact_path=artifact,
                effective_date_available=True,
                pit_safe_status="PIT_LIMITED_PUBLICATION_TIMESTAMP_NEEDS_VALIDATION",
                validation_status="VALIDATED_JSON_SHAPE",
            )
        except (RateLimitedError, CredentialOrSubscriptionError, Exception) as exc:
            status, reason = status_from_exception(exc)
            return self.result(dataset, context, status=status, started_at=started_at, failure_reason=reason, recommended_user_action="Retry later or configure an approved news source.")

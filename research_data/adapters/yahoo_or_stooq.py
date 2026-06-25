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


class YahooOrStooqAdapter(BaseHydrationAdapter):
    source_name = "yahoo_chart_public"
    source_type = "free_public_market_data"

    _SUPPORTED = {"ohlcv_prices", "vix_volatility_regime", "corporate_actions"}

    def supports(self, dataset_id: str) -> bool:
        return dataset_id in self._SUPPORTED

    def hydrate(self, dataset: dict, context: HydrationContext) -> HydrationResult:
        started_at = utc_now_iso()
        dataset_id = str(dataset["dataset_id"])
        if context.dry_run:
            return self.dry_run_result(dataset, context, started_at)
        if dataset_id == "corporate_actions":
            return self.result(
                dataset,
                context,
                status="SOURCE_UNAVAILABLE",
                started_at=started_at,
                failure_reason="No stable free/public corporate action sample endpoint is configured in this adapter.",
                recommended_user_action="Use an approved corporate-actions source or configure a vendor adapter.",
            )
        symbol = "SPY" if dataset_id == "ohlcv_prices" else "%5EVIX"
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=10d&interval=1d"
        try:
            response = request_get(url, timeout=context.timeout_seconds, headers={"User-Agent": context.user_agent})
            payload = response.json()
            result = ((payload.get("chart") or {}).get("result") or [])
            timestamps = result[0].get("timestamp") if result else []
            quote = ((((result[0].get("indicators") or {}).get("quote") or [{}])[0]) if result else {})
            close_values = quote.get("close") or []
            rows = [
                {"timestamp": ts, "close": close_values[idx] if idx < len(close_values) else None}
                for idx, ts in enumerate(timestamps or [])
            ]
            if not rows:
                return self.result(
                    dataset,
                    context,
                    status="EMPTY_RESULT",
                    started_at=started_at,
                    failure_reason="Yahoo chart endpoint returned no sample rows.",
                    recommended_user_action="Verify symbol mapping or choose a different public market-data source.",
                )
            artifact = context.output_path("raw", dataset_id, self.source_name, f"{dataset_id}_sample.json")
            from research_data.hydration import write_json

            write_json(artifact, {"symbol": symbol, "rows": rows, "source_payload": payload})
            return self.result(
                dataset,
                context,
                status="OK",
                started_at=started_at,
                records_written=len(rows),
                artifact_path=artifact,
                effective_date_available=True,
                pit_safe_status="PIT_SAFE_SAMPLE_AS_OF_DATED",
                validation_status="VALIDATED_JSON_SHAPE",
            )
        except (RateLimitedError, CredentialOrSubscriptionError, Exception) as exc:
            status, reason = status_from_exception(exc)
            return self.result(
                dataset,
                context,
                status=status,
                started_at=started_at,
                failure_reason=reason,
                recommended_user_action="Retry later or configure a dedicated market-data source.",
            )

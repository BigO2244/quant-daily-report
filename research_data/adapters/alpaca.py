from __future__ import annotations

import os
from datetime import date, timedelta

from research_data.hydration import BaseHydrationAdapter, HydrationContext, HydrationResult, request_get, status_from_exception, utc_now_iso, write_json


class AlpacaAdapter(BaseHydrationAdapter):
    source_name = "alpaca_market_data"
    source_type = "configured_credentials_market_data"

    _SUPPORTED = {"ohlcv_prices", "security_master_pit"}

    def supports(self, dataset_id: str) -> bool:
        return dataset_id in self._SUPPORTED

    def hydrate(self, dataset: dict, context: HydrationContext) -> HydrationResult:
        started_at = utc_now_iso()
        key = os.environ.get("ALPACA_API_KEY_ID")
        secret = os.environ.get("ALPACA_API_SECRET_KEY")
        if not key or not secret:
            return self.result(
                dataset,
                context,
                status="BLOCKED_CREDENTIALS",
                started_at=started_at,
                failure_reason="ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY are not both present.",
                recommended_user_action="Configure dedicated Alpaca market-data credentials if this source should be used.",
            )
        if context.dry_run:
            return self.dry_run_result(dataset, context, started_at)
        if str(dataset["dataset_id"]) != "ohlcv_prices":
            return self.result(
                dataset,
                context,
                status="SOURCE_UNAVAILABLE",
                started_at=started_at,
                failure_reason="Alpaca adapter only attempts OHLCV sample data; it does not use broker asset APIs in the swarm.",
                recommended_user_action="Use the canonical security-master adapter or a dedicated reference-data source.",
            )
        end = date.fromisoformat(context.as_of_date)
        start = end - timedelta(days=10)
        url = (
            "https://data.alpaca.markets/v2/stocks/SPY/bars"
            f"?timeframe=1Day&start={start.isoformat()}T00:00:00Z&end={end.isoformat()}T23:59:59Z&limit=5"
        )
        try:
            response = request_get(
                url,
                timeout=context.timeout_seconds,
                headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
            )
            payload = response.json()
            bars = payload.get("bars") or []
            artifact = context.output_path("raw", dataset["dataset_id"], self.source_name, "spy_bars_sample.json")
            write_json(artifact, payload)
            if not bars:
                return self.result(
                    dataset,
                    context,
                    status="EMPTY_RESULT",
                    started_at=started_at,
                    failure_reason="Alpaca market-data endpoint returned no bars.",
                    recommended_user_action="Check subscription tier and data feed availability.",
                    artifact_path=artifact,
                )
            return self.result(
                dataset,
                context,
                status="OK",
                started_at=started_at,
                records_written=len(bars),
                artifact_path=artifact,
                effective_date_available=True,
                pit_safe_status="PIT_SAFE_SAMPLE_AS_OF_DATED",
                validation_status="VALIDATED_JSON_SHAPE",
            )
        except Exception as exc:
            status, reason = status_from_exception(exc)
            return self.result(
                dataset,
                context,
                status=status,
                started_at=started_at,
                failure_reason=reason,
                recommended_user_action="Check Alpaca market-data entitlement or prefer the free/public price adapter.",
            )

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import quote

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
            return self._hydrate_corporate_actions(dataset, context, started_at)
        if dataset_id == "ohlcv_prices":
            return self._hydrate_ohlcv(dataset, context, started_at)
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

    def _hydrate_ohlcv(self, dataset: dict, context: HydrationContext, started_at: str) -> HydrationResult:
        symbols = _symbols_or_default(context, "SPY")
        symbol_payloads = []
        rows_written = 0
        try:
            for symbol in symbols:
                payload = _chart(symbol, "range=2y&interval=1d", context)
                rows = _price_rows(payload)
                symbol_payloads.append(
                    {
                        "symbol": symbol,
                        "retrieved_at": utc_now_iso(),
                        "rows": rows,
                        "source_payload": payload,
                    }
                )
                rows_written += len(rows)
            if rows_written <= 0:
                return self.result(
                    dataset,
                    context,
                    status="EMPTY_RESULT",
                    started_at=started_at,
                    failure_reason=f"Yahoo chart endpoint returned no OHLCV rows for symbols: {', '.join(symbols)}.",
                    recommended_user_action="Verify symbol mapping or choose a different public market-data source.",
                )
            artifact = context.output_path("raw", dataset["dataset_id"], self.source_name, f"{dataset['dataset_id']}_sample.json")
            from research_data.hydration import write_json

            write_json(
                artifact,
                {
                    "source_name": self.source_name,
                    "symbols": symbols,
                    "symbol_count": len(symbols),
                    "symbol_payloads": symbol_payloads,
                },
            )
            return self.result(
                dataset,
                context,
                status="OK" if context.symbols else "PARTIAL",
                started_at=started_at,
                records_written=rows_written,
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

    def _hydrate_corporate_actions(self, dataset: dict, context: HydrationContext, started_at: str) -> HydrationResult:
        symbols = _symbols_or_default(context, "AAPL")
        symbol_payloads = []
        rows_written = 0
        try:
            for symbol in symbols:
                payload = _chart(symbol, "range=10y&interval=1d&events=div%2Csplits", context)
                rows = _corporate_action_rows(symbol, payload)
                symbol_payloads.append(
                    {
                        "symbol": symbol,
                        "retrieved_at": utc_now_iso(),
                        "rows": rows,
                        "source_payload": payload,
                    }
                )
                rows_written += len(rows)
            artifact = context.output_path("raw", dataset["dataset_id"], self.source_name, f"{dataset['dataset_id']}_sample.json")
            from research_data.hydration import write_json

            write_json(
                artifact,
                {
                    "source_name": self.source_name,
                    "symbols": symbols,
                    "symbol_count": len(symbols),
                    "symbol_payloads": symbol_payloads,
                },
            )
            return self.result(
                dataset,
                context,
                status="OK" if context.symbols else "PARTIAL",
                started_at=started_at,
                records_written=rows_written,
                artifact_path=artifact,
                effective_date_available=True,
                pit_safe_status="PIT_SAFE_PUBLIC_CHART_EVENTS_AS_OF_DATED_NEEDS_VENDOR_AUDIT",
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
                recommended_user_action="Retry later or configure a dedicated corporate-actions source.",
            )


def _symbols_or_default(context: HydrationContext, default: str) -> list[str]:
    return list(context.symbols) if context.symbols else [default]


def _chart(symbol: str, query: str, context: HydrationContext) -> dict:
    encoded = quote(symbol, safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?{query}"
    response = request_get(url, timeout=context.timeout_seconds, headers={"User-Agent": context.user_agent})
    return response.json()


def _price_rows(payload: dict) -> list[dict]:
    result = ((payload.get("chart") or {}).get("result") or [])
    timestamps = result[0].get("timestamp") if result else []
    quote_payload = ((((result[0].get("indicators") or {}).get("quote") or [{}])[0]) if result else {})
    close_values = quote_payload.get("close") or []
    return [
        {"timestamp": ts, "close": close_values[idx] if idx < len(close_values) else None}
        for idx, ts in enumerate(timestamps or [])
    ]


def _corporate_action_rows(symbol: str, payload: dict) -> list[dict]:
    result = ((payload.get("chart") or {}).get("result") or [])
    events = (result[0].get("events") if result else {}) or {}
    rows: list[dict] = []
    for item in (events.get("dividends") or {}).values():
        timestamp = item.get("date")
        rows.append(
            {
                "ticker": symbol,
                "action": "dividend",
                "timestamp": timestamp,
                "date": _date_from_timestamp(timestamp),
                "value": item.get("amount"),
            }
        )
    for item in (events.get("splits") or {}).values():
        timestamp = item.get("date")
        numerator = item.get("numerator")
        denominator = item.get("denominator")
        ratio = None if numerator in (None, "") or denominator in (None, "", 0) else float(numerator) / float(denominator)
        rows.append(
            {
                "ticker": symbol,
                "action": "split",
                "timestamp": timestamp,
                "date": _date_from_timestamp(timestamp),
                "value": ratio,
                "numerator": numerator,
                "denominator": denominator,
            }
        )
    rows.sort(key=lambda item: (str(item.get("date") or ""), str(item.get("action") or "")))
    return rows


def _date_from_timestamp(timestamp: object) -> str | None:
    if timestamp in (None, ""):
        return None
    return datetime.fromtimestamp(int(timestamp), UTC).date().isoformat()

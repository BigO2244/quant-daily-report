from __future__ import annotations

from typing import Any

from research_data.hydration import (
    BaseHydrationAdapter,
    CredentialOrSubscriptionError,
    HydrationContext,
    HydrationResult,
    RateLimitedError,
    request_get,
    status_from_exception,
    utc_now_iso,
    write_json,
)


class SecEdgarAdapter(BaseHydrationAdapter):
    source_name = "sec_edgar_public"
    source_type = "free_public_sec"

    _SUPPORTED = {
        "security_master_pit",
        "fundamentals_pit",
        "insider_form4",
        "sec_8k_events",
        "sec_10q_10k_metadata",
        "institutional_13f",
    }

    def supports(self, dataset_id: str) -> bool:
        return dataset_id in self._SUPPORTED

    def hydrate(self, dataset: dict, context: HydrationContext) -> HydrationResult:
        started_at = utc_now_iso()
        dataset_id = str(dataset["dataset_id"])
        if context.dry_run:
            return self.dry_run_result(dataset, context, started_at)
        headers = {"User-Agent": context.user_agent, "Accept-Encoding": "gzip, deflate"}
        try:
            if dataset_id == "security_master_pit":
                return self._company_tickers(dataset, context, started_at, headers)
            if dataset_id == "fundamentals_pit":
                return self._companyfacts(dataset, context, started_at, headers)
            if dataset_id == "institutional_13f":
                return self._submissions_filtered(dataset, context, started_at, headers, cik="0001067983", forms={"13F-HR", "13F-HR/A"})
            forms = {
                "insider_form4": {"4", "4/A"},
                "sec_8k_events": {"8-K", "8-K/A"},
                "sec_10q_10k_metadata": {"10-Q", "10-Q/A", "10-K", "10-K/A"},
            }[dataset_id]
            return self._submissions_filtered(dataset, context, started_at, headers, cik="0000320193", forms=forms)
        except (RateLimitedError, CredentialOrSubscriptionError, Exception) as exc:
            status, reason = status_from_exception(exc)
            return self.result(
                dataset,
                context,
                status=status,
                started_at=started_at,
                failure_reason=reason,
                recommended_user_action="Retry later, reduce sample rate, or configure a parsed SEC/vendor source.",
            )

    def _company_tickers(
        self,
        dataset: dict,
        context: HydrationContext,
        started_at: str,
        headers: dict[str, str],
    ) -> HydrationResult:
        url = "https://www.sec.gov/files/company_tickers.json"
        payload = request_get(url, timeout=context.timeout_seconds, headers=headers).json()
        if context.symbols:
            wanted = set(context.symbols)
            payload = {
                key: row
                for key, row in payload.items()
                if str((row or {}).get("ticker") or "").upper() in wanted
            }
        count = len(payload)
        if count <= 0:
            reason = "SEC company tickers returned empty payload."
            if context.symbols:
                reason = f"SEC company tickers returned no rows for requested symbols: {', '.join(context.symbols)}."
            return self.result(dataset, context, status="EMPTY_RESULT", started_at=started_at, failure_reason=reason)
        artifact = context.output_path("raw", dataset["dataset_id"], self.source_name, "company_tickers_sample.json")
        write_json(
            artifact,
            {
                "queried_symbols": list(context.symbols),
                "row_count": count,
                "rows_by_index": payload,
            }
            if context.symbols
            else payload,
        )
        return self.result(
            dataset,
            context,
            status="PARTIAL",
            started_at=started_at,
            records_written=count,
            artifact_path=artifact,
            effective_date_available=False,
            pit_safe_status="PIT_LIMITED_CURRENT_COMPANY_TICKERS_ONLY",
            validation_status="VALIDATED_JSON_SHAPE",
        )

    def _companyfacts(
        self,
        dataset: dict,
        context: HydrationContext,
        started_at: str,
        headers: dict[str, str],
    ) -> HydrationResult:
        url = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"
        payload = request_get(url, timeout=context.timeout_seconds, headers=headers).json()
        facts = payload.get("facts") or {}
        count = sum(len(unit_values) for taxonomy in facts.values() for fact in taxonomy.values() for unit_values in (fact.get("units") or {}).values())
        artifact = context.output_path("raw", dataset["dataset_id"], self.source_name, "aapl_companyfacts_sample.json")
        write_json(artifact, payload)
        return self.result(
            dataset,
            context,
            status="PARTIAL",
            started_at=started_at,
            records_written=count,
            artifact_path=artifact,
            effective_date_available=True,
            filing_date_available=True,
            pit_safe_status="PIT_SAFE_SAMPLE_FILING_FIELDS_PRESENT",
            validation_status="VALIDATED_JSON_SHAPE",
        )

    def _submissions_filtered(
        self,
        dataset: dict,
        context: HydrationContext,
        started_at: str,
        headers: dict[str, str],
        *,
        cik: str,
        forms: set[str],
    ) -> HydrationResult:
        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        payload = request_get(url, timeout=context.timeout_seconds, headers=headers).json()
        recent = ((payload.get("filings") or {}).get("recent") or {})
        rows = _recent_rows(recent)
        filtered = [row for row in rows if str(row.get("form") or "") in forms]
        artifact = context.output_path("raw", dataset["dataset_id"], self.source_name, f"submissions_{cik}_sample.json")
        write_json(artifact, {"cik": cik, "forms": sorted(forms), "records": filtered[:25]})
        if not filtered:
            return self.result(
                dataset,
                context,
                status="EMPTY_RESULT",
                started_at=started_at,
                failure_reason=f"SEC submissions sample had no forms in {sorted(forms)}.",
                recommended_user_action="Try a different issuer sample or a broader SEC index source.",
                artifact_path=artifact,
                records_written=0,
                filing_date_available=True,
            )
        return self.result(
            dataset,
            context,
            status="PARTIAL",
            started_at=started_at,
            records_written=len(filtered),
            artifact_path=artifact,
            effective_date_available=True,
            filing_date_available=True,
            pit_safe_status="PIT_SAFE_SAMPLE_FILING_DATE_PRESENT",
            validation_status="VALIDATED_JSON_SHAPE",
        )


def _recent_rows(recent: dict[str, list[Any]]) -> list[dict[str, Any]]:
    keys = list(recent.keys())
    if not keys:
        return []
    length = max(len(recent.get(key) or []) for key in keys)
    rows: list[dict[str, Any]] = []
    for idx in range(length):
        row = {}
        for key in keys:
            values = recent.get(key) or []
            if idx < len(values):
                row[key] = values[idx]
        rows.append(row)
    return rows

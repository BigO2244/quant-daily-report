from __future__ import annotations

import json
import urllib.error
from dataclasses import dataclass
from typing import Any, Callable

from research_data.hydration import BaseHydrationAdapter, HydrationContext, HydrationResult, utc_now_iso, write_json
from scripts.research.verify_sharadar_coverage import _ndl_get, _rows_from_datatable, resolve_api_key


EXPECTED_ENV_VARS = ("NASDAQ_DATA_LINK_API_KEY", "QUANDL_API_KEY")


@dataclass(frozen=True)
class SharadarProbe:
    table: str
    params: dict[str, Any]
    pit_safe_status: str
    effective_date_available: bool = False
    filing_date_available: bool = False


class NasdaqSharadarAdapter(BaseHydrationAdapter):
    source_name = "nasdaq_sharadar"
    source_type = "paid_vendor_optional"

    _PROBES: dict[str, SharadarProbe] = {
        "ohlcv_prices": SharadarProbe(
            table="SHARADAR/SEP",
            params={
                "ticker": "AAPL",
                "qopts.columns": "ticker,date,open,high,low,close,closeadj,volume",
                "qopts.per_page": 5,
            },
            effective_date_available=True,
            pit_safe_status="PIT_SAFE_SAMPLE_AS_OF_DATED_TABLE_NEEDS_FULL_LINEAGE",
        ),
        "security_master_pit": SharadarProbe(
            table="SHARADAR/TICKERS",
            params={
                "table": "SEP",
                "qopts.columns": "ticker,permaticker,name,exchange,isdelisted,category,scalemarketcap,firstpricedate,lastpricedate",
                "qopts.per_page": 5,
            },
            effective_date_available=True,
            pit_safe_status="PIT_GRADE_SHARADAR_TICKERS_DATE_WINDOWS",
        ),
        "corporate_actions": SharadarProbe(
            table="SHARADAR/ACTIONS",
            params={"ticker": "AAPL", "qopts.per_page": 5},
            effective_date_available=True,
            pit_safe_status="PIT_SAFE_SAMPLE_EFFECTIVE_DATE_REQUIRES_ACTION_TYPE_AUDIT",
        ),
        "fundamentals_pit": SharadarProbe(
            table="SHARADAR/SF1",
            params={
                "ticker": "AAPL",
                "dimension": "ARQ",
                "qopts.columns": "ticker,datekey,reportperiod,dimension,revenue,netinc",
                "qopts.per_page": 5,
            },
            effective_date_available=True,
            filing_date_available=True,
            pit_safe_status="PIT_SAFE_SAMPLE_FILING_FIELDS_REQUIRE_RESTATEMENT_AUDIT",
        ),
        "etf_index_constituents": SharadarProbe(
            table="SHARADAR/SP500",
            params={"qopts.per_page": 5},
            effective_date_available=True,
            pit_safe_status="PIT_SAFE_SAMPLE_MEMBERSHIP_DATES_REQUIRE_CONSTITUENT_AUDIT",
        ),
    }

    def __init__(self, get_fn: Callable[[str, dict[str, Any], str], dict[str, Any]] | None = None) -> None:
        self._get_fn = get_fn

    def supports(self, dataset_id: str) -> bool:
        return dataset_id in self._PROBES

    def hydrate(self, dataset: dict, context: HydrationContext) -> HydrationResult:
        started_at = utc_now_iso()
        dataset_id = str(dataset["dataset_id"])
        if context.dry_run:
            return self.dry_run_result(dataset, context, started_at)

        api_key = resolve_api_key(None)
        if not api_key:
            env_names = ", ".join(EXPECTED_ENV_VARS)
            return self.result(
                dataset,
                context,
                status="BLOCKED_CREDENTIALS",
                started_at=started_at,
                failure_reason=f"No Nasdaq Data Link / Sharadar API key found. Checked env vars: {env_names}.",
                recommended_user_action=(
                    "Export NASDAQ_DATA_LINK_API_KEY, or QUANDL_API_KEY for legacy Nasdaq Data Link scripts, "
                    "outside the repo. Do not commit or log the key."
                ),
            )

        probe = self._PROBES[dataset_id]
        if dataset_id == "fundamentals_pit":
            params_list = _fundamentals_probe_params()
        elif dataset_id == "security_master_pit" and context.symbols:
            params_list = _security_master_probe_params(context.symbols)
        else:
            params_list = [probe.params]
        return self._hydrate_from_probe_params(dataset, context, started_at, probe, params_list, api_key)

    def _hydrate_from_probe_params(
        self,
        dataset: dict,
        context: HydrationContext,
        started_at: str,
        probe: SharadarProbe,
        params_list: list[dict[str, Any]],
        api_key: str,
    ) -> HydrationResult:
        attempted: list[dict[str, Any]] = []
        all_rows: list[dict[str, Any]] = []
        columns: list[str] = []
        for params in params_list:
            attempted.append({"table": probe.table, "params": _sanitize_params(params)})
            try:
                payload = self._fetch(probe.table, params, api_key)
            except Exception as exc:
                return self._classified_exception_result(dataset, context, started_at, probe.table, exc)

            rows = _rows_from_datatable(payload)
            columns = [column.get("name") for column in ((payload.get("datatable") or {}).get("columns") or [])]
            if "datatable" not in payload or not columns:
                return self.result(
                    dataset,
                    context,
                    status="SCHEMA_ERROR",
                    started_at=started_at,
                    failure_reason=(
                        f"Nasdaq Data Link response for {probe.table} did not contain datatable columns. "
                        f"attempted_probes={_attempts_json(attempted)}"
                    ),
                    recommended_user_action="Inspect the approved Sharadar table schema and update the adapter parser.",
                )
            if rows:
                all_rows.extend(rows)
                if len(params_list) > 1 and str(dataset["dataset_id"]) == "security_master_pit":
                    continue
                artifact = context.output_path("raw", dataset["dataset_id"], self.source_name, _artifact_name(probe.table))
                write_json(
                    artifact,
                    {
                        "source_table": probe.table,
                        "sample_params": _sanitize_params(params),
                        "attempted_probes": attempted,
                        "columns": columns,
                        "rows": rows[:5],
                    },
                )
                return self.result(
                    dataset,
                    context,
                    status="PARTIAL",
                    started_at=started_at,
                    records_written=len(rows),
                    artifact_path=artifact,
                    effective_date_available=probe.effective_date_available,
                    filing_date_available=probe.filing_date_available,
                    pit_safe_status=probe.pit_safe_status,
                    validation_status="VALIDATED_NASDAQ_DATA_LINK_DATATABLE_SHAPE",
                )

        if all_rows:
            deduped = _dedupe_rows(all_rows)
            artifact = context.output_path("raw", dataset["dataset_id"], self.source_name, _artifact_name(probe.table))
            write_json(
                artifact,
                {
                    "source_table": probe.table,
                    "sample_params": _sanitize_params(params_list[0]),
                    "attempted_probes": attempted,
                    "queried_symbols": list(context.symbols),
                    "columns": columns,
                    "rows": deduped,
                },
            )
            covered = {str(row.get("ticker") or "").upper() for row in deduped}
            missing = sorted(set(context.symbols) - covered)
            return self.result(
                dataset,
                context,
                status="OK" if not missing else "PARTIAL",
                started_at=started_at,
                records_written=len(deduped),
                artifact_path=artifact,
                effective_date_available=probe.effective_date_available,
                filing_date_available=probe.filing_date_available,
                pit_safe_status=probe.pit_safe_status if not missing else "PIT_PARTIAL_SHARADAR_TICKERS_MISSING_SYMBOLS",
                validation_status="VALIDATED_PIT_SECURITY_MASTER_SHAPE" if not missing else f"VALIDATED_PARTIAL_PIT_SECURITY_MASTER_MISSING:{','.join(missing)}",
            )

        return self.result(
            dataset,
            context,
            status="EMPTY_RESULT",
            started_at=started_at,
            failure_reason=(
                f"Nasdaq Data Link returned no rows for {probe.table} after {len(attempted)} safe sample probes. "
                f"attempted_probes={_attempts_json(attempted)}"
            ),
            recommended_user_action="Check table entitlement/coverage or add a narrower known-good Sharadar sample query.",
        )

    def _fetch(self, table: str, params: dict[str, Any], api_key: str) -> dict[str, Any]:
        if self._get_fn is not None:
            return self._get_fn(table, params, api_key)
        return _ndl_get(table, params, api_key=api_key)

    def _classified_exception_result(
        self,
        dataset: dict,
        context: HydrationContext,
        started_at: str,
        table: str,
        exc: Exception,
    ) -> HydrationResult:
        if isinstance(exc, urllib.error.HTTPError):
            if exc.code == 429:
                return self.result(
                    dataset,
                    context,
                    status="RATE_LIMITED",
                    started_at=started_at,
                    failure_reason=f"Nasdaq Data Link returned HTTP 429 rate limit for {table}.",
                    recommended_user_action="Retry later or lower sample/request cadence.",
                )
            if exc.code in {401, 403}:
                return self.result(
                    dataset,
                    context,
                    status="BLOCKED_AUTH_OR_ENTITLEMENT",
                    started_at=started_at,
                    failure_reason=f"Nasdaq Data Link returned HTTP {exc.code} auth/entitlement failure for {table}.",
                    recommended_user_action="Verify the existing Sharadar key is loaded and entitled for this table.",
                )
            if exc.code in {400, 404, 422}:
                return self.result(
                    dataset,
                    context,
                    status="SCHEMA_ERROR",
                    started_at=started_at,
                    failure_reason=f"Nasdaq Data Link returned HTTP {exc.code} for {table} sample parameters.",
                    recommended_user_action="Check the approved table name, columns, and sample parameters.",
                )
        if isinstance(exc, urllib.error.URLError):
            return self.result(
                dataset,
                context,
                status="SOURCE_UNAVAILABLE",
                started_at=started_at,
                failure_reason=f"Nasdaq Data Link request failed for {table}: {exc.reason}",
                recommended_user_action="Retry when network/source availability is restored.",
            )
        return self.result(
            dataset,
            context,
            status="FAILED_UNKNOWN",
            started_at=started_at,
            failure_reason=f"{type(exc).__name__} while probing {table}.",
            recommended_user_action="Inspect adapter failure and add a narrower Nasdaq Data Link classification.",
        )


def _artifact_name(table: str) -> str:
    return table.lower().replace("/", "_") + "_sample.json"


def _security_master_probe_params(symbols: tuple[str, ...]) -> list[dict[str, Any]]:
    columns = "ticker,permaticker,name,exchange,isdelisted,category,scalemarketcap,firstpricedate,lastpricedate"
    return [
        {
            "table": "SEP",
            "ticker": symbol,
            "qopts.columns": columns,
            "qopts.per_page": 10,
        }
        for symbol in sorted(set(symbols))
    ]


def _fundamentals_probe_params() -> list[dict[str, Any]]:
    columns = "ticker,calendardate,datekey,reportperiod,dimension,revenue,netinc"
    return [
        {
            "ticker": "AAPL",
            "dimension": "ARQ",
            "qopts.columns": columns,
            "qopts.per_page": 5,
        },
        {
            "ticker": "AAPL",
            "qopts.columns": columns,
            "qopts.per_page": 5,
        },
        {
            "ticker": "MSFT",
            "qopts.columns": columns,
            "qopts.per_page": 5,
        },
        {
            "dimension": "ARQ",
            "qopts.columns": columns,
            "qopts.per_page": 5,
        },
        {
            "dimension": "ARY",
            "qopts.columns": columns,
            "qopts.per_page": 5,
        },
        {
            "qopts.columns": columns,
            "qopts.per_page": 5,
        },
    ]


def _sanitize_params(params: dict[str, Any]) -> dict[str, Any]:
    return {str(key): value for key, value in sorted(params.items()) if "api_key" not in str(key).lower()}


def _attempts_json(attempted: list[dict[str, Any]]) -> str:
    return json.dumps(attempted, sort_keys=True, separators=(",", ":"))


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    deduped = []
    for row in rows:
        key = tuple(sorted(row.items()))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped

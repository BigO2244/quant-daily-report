#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research_data.adapters.alpaca import AlpacaAdapter
from research_data.adapters.cboe_or_vix import CboeOrVixAdapter
from research_data.adapters.derived import DatasetFreshnessAdapter, DerivedFeatureAdapter
from research_data.adapters.finra import FinraAdapter
from research_data.adapters.fred import FredAdapter
from research_data.adapters.nasdaq_sharadar import NasdaqSharadarAdapter
from research_data.adapters.news import NewsAdapter
from research_data.adapters.polygon import PolygonAdapter
from research_data.adapters.public_reference import PublicReferenceAdapter
from research_data.adapters.sec_edgar import SecEdgarAdapter
from research_data.adapters.yahoo_or_stooq import YahooOrStooqAdapter
from research_data.catalog import catalog_entries, catalog_payload, validate_catalog
from research_data.hydration import (
    BaseHydrationAdapter,
    HydrationContext,
    HydrationResult,
    SUCCESS_STATUSES,
    default_as_of_date,
    utc_now_iso,
    write_json,
)
from scripts.research.verify_sharadar_coverage import _load_env_file


SOURCE_PLAN: dict[str, list[str]] = {
    "ohlcv_prices": ["yahoo_chart_public", "alpaca_market_data", "polygon", "nasdaq_sharadar"],
    "security_master_pit": ["sec_edgar_public", "alpaca_market_data", "nasdaq_sharadar"],
    "corporate_actions": ["yahoo_chart_public", "nasdaq_sharadar"],
    "dataset_freshness": ["internal_dataset_freshness"],
    "fundamentals_pit": ["sec_edgar_public", "nasdaq_sharadar"],
    "fundamental_features": ["internal_derived_features"],
    "macro_regime_features": ["internal_derived_features"],
    "macro_rates": ["fred_public_csv"],
    "yield_curve": ["fred_public_csv"],
    "credit_spreads": ["fred_public_csv"],
    "vix_volatility_regime": ["yahoo_chart_public", "cboe_or_vix", "polygon"],
    "insider_form4": ["sec_edgar_public"],
    "sec_8k_events": ["sec_edgar_public"],
    "sec_10q_10k_metadata": ["sec_edgar_public"],
    "etf_index_constituents": ["public_reference_stub", "nasdaq_sharadar"],
    "short_interest": ["finra_public"],
    "options_iv_open_interest": ["cboe_or_vix", "polygon"],
    "analyst_estimate_revisions": ["polygon"],
    "news_metadata": ["gdelt_public_news", "polygon"],
    "news_sentiment_embeddings": ["internal_derived_features", "gdelt_public_news"],
    "institutional_13f": ["sec_edgar_public"],
    "alternative_datasets": ["gdelt_public_news"],
}

DEFAULT_SLEEVE_MANIFEST = Path("research_registry/sleeves/manifest.json")
DEFAULT_LEGACY_CANDIDATES_ROOT = Path("outputs/shadow_candidates")


def default_adapters() -> dict[str, BaseHydrationAdapter]:
    adapters: list[BaseHydrationAdapter] = [
        YahooOrStooqAdapter(),
        FredAdapter(),
        SecEdgarAdapter(),
        AlpacaAdapter(),
        NasdaqSharadarAdapter(),
        PolygonAdapter(),
        CboeOrVixAdapter(),
        FinraAdapter(),
        NewsAdapter(),
        DerivedFeatureAdapter(),
        DatasetFreshnessAdapter(),
        PublicReferenceAdapter(),
    ]
    return {adapter.source_name: adapter for adapter in adapters}


def run_swarm(
    *,
    repo_root: Path = REPO_ROOT,
    dry_run: bool = False,
    limit_sample: bool = False,
    as_of_date: str | None = None,
    dataset_ids: set[str] | None = None,
    source_names: set[str] | None = None,
    symbols: set[str] | None = None,
    sleeve_id: str | None = None,
    legacy_candidates_root: Path | None = None,
    timeout_seconds: int = 12,
    adapter_registry: dict[str, BaseHydrationAdapter] | None = None,
) -> dict[str, Any]:
    repo_root = Path(repo_root)
    started_at = utc_now_iso()
    entries = catalog_entries()
    if dataset_ids:
        entries = [entry for entry in entries if str(entry["dataset_id"]) in dataset_ids]
    catalog_errors = validate_catalog(entries)
    adapters = adapter_registry or default_adapters()
    context = HydrationContext(
        repo_root=repo_root,
        as_of_date=as_of_date or default_as_of_date(),
        dry_run=dry_run,
        limit_sample=limit_sample,
        timeout_seconds=timeout_seconds,
        symbols=tuple(_resolve_symbols(repo_root, as_of_date or default_as_of_date(), symbols=symbols, sleeve_id=sleeve_id, legacy_candidates_root=legacy_candidates_root)),
        sleeve_id=sleeve_id,
    )

    all_attempts: list[dict[str, Any]] = []
    dataset_summaries: list[dict[str, Any]] = []
    for dataset in entries:
        attempts = _attempt_dataset(dataset, context, adapters, source_filter=source_names)
        all_attempts.extend([attempt.to_dict() for attempt in attempts])
        dataset_summaries.append(_summarize_dataset(dataset, attempts))

    completed_at = utc_now_iso()
    catalog = catalog_payload(entries)
    freshness = _build_freshness(context, dataset_summaries)
    capability_matrix = _build_capability_matrix(dataset_summaries)
    summary = {
        "schema_version": "data_hydration_swarm_v1",
        "started_at": started_at,
        "completed_at": completed_at,
        "as_of_date": context.as_of_date,
        "dry_run": dry_run,
        "limit_sample": limit_sample,
        "catalog_errors": catalog_errors,
        "dataset_count": len(entries),
        "attempt_count": len(all_attempts),
        "successful_dataset_count": sum(1 for row in dataset_summaries if row["hydration_succeeded"]),
        "blocked_dataset_count": sum(1 for row in dataset_summaries if not row["hydration_succeeded"]),
        "focused_symbols": list(context.symbols),
        "focused_symbol_count": len(context.symbols),
        "focused_sleeve_id": sleeve_id,
        "broker_submission_invoked": False,
        "runtime_impact": "read_only_data_hydration_only_no_trading_path_changes",
    }
    payload = {
        "summary": summary,
        "datasets": dataset_summaries,
        "attempts": all_attempts,
    }

    manifest_dir = repo_root / "data" / "manifests"
    log_dir = repo_root / "data" / "hydration_logs"
    write_json(manifest_dir / "research_data_catalog.json", catalog)
    write_json(manifest_dir / "dataset_freshness.json", freshness)
    write_json(manifest_dir / "hydration_capability_matrix.json", capability_matrix)
    write_json(log_dir / "latest_hydration_swarm.json", payload)
    return payload


def _attempt_dataset(
    dataset: dict[str, Any],
    context: HydrationContext,
    adapters: dict[str, BaseHydrationAdapter],
    *,
    source_filter: set[str] | None = None,
) -> list[HydrationResult]:
    dataset_id = str(dataset["dataset_id"])
    source_names = SOURCE_PLAN.get(dataset_id, [])
    if source_filter:
        source_names = [source_name for source_name in source_names if source_name in source_filter]
    attempts: list[HydrationResult] = []
    for source_name in source_names:
        adapter = adapters.get(source_name)
        if adapter is None:
            attempts.append(_missing_adapter_result(dataset, context, source_name))
            continue
        if not adapter.supports(dataset_id):
            attempts.append(_unsupported_adapter_result(dataset, context, adapter))
            continue
        try:
            result = adapter.hydrate(dataset, context)
        except Exception as exc:  # failure logging must never stop the swarm
            result = _exception_result(dataset, context, adapter, exc)
        attempts.append(result)
        if not context.dry_run and result.status in SUCCESS_STATUSES:
            break
    if not attempts:
        attempts.append(_missing_adapter_result(dataset, context, "no_source_configured"))
    return attempts


def _missing_adapter_result(dataset: dict[str, Any], context: HydrationContext, source_name: str) -> HydrationResult:
    started_at = utc_now_iso()
    adapter = _SyntheticAdapter(source_name)
    return adapter.result(
        dataset,
        context,
        status="SOURCE_UNAVAILABLE",
        started_at=started_at,
        failure_reason=f"No adapter is registered for source {source_name}.",
        recommended_user_action="Add a read-only adapter or mark the dataset explicitly blocked.",
    )


def _unsupported_adapter_result(dataset: dict[str, Any], context: HydrationContext, adapter: BaseHydrationAdapter) -> HydrationResult:
    started_at = utc_now_iso()
    return adapter.result(
        dataset,
        context,
        status="SOURCE_UNAVAILABLE",
        started_at=started_at,
        failure_reason=f"Adapter {adapter.source_name} does not support dataset {dataset['dataset_id']}.",
        recommended_user_action="Fix the source plan or adapter support table.",
    )


def _exception_result(dataset: dict[str, Any], context: HydrationContext, adapter: BaseHydrationAdapter, exc: Exception) -> HydrationResult:
    started_at = utc_now_iso()
    return adapter.result(
        dataset,
        context,
        status="FAILED_UNKNOWN",
        started_at=started_at,
        failure_reason=f"{type(exc).__name__}: {exc}",
        recommended_user_action="Inspect adapter failure and add a narrower source-specific classification.",
    )


class _SyntheticAdapter(BaseHydrationAdapter):
    source_type = "synthetic_missing_adapter"

    def __init__(self, source_name: str) -> None:
        self.source_name = source_name

    def supports(self, dataset_id: str) -> bool:
        return True

    def hydrate(self, dataset: dict[str, Any], context: HydrationContext) -> HydrationResult:
        raise NotImplementedError


def _summarize_dataset(dataset: dict[str, Any], attempts: list[HydrationResult]) -> dict[str, Any]:
    winner = _winner(attempts)
    first_success = next(
        (
            attempt
            for attempt in attempts
            if attempt.status in SUCCESS_STATUSES and attempt.validation_status != "DRY_RUN_CLASSIFIED"
        ),
        None,
    )
    source_types = {attempt.source_type for attempt in attempts}
    paid_required = "paid" in str(dataset.get("cost_classification", "")).lower() or any("paid" in source_type for source_type in source_types)
    credentials_present = any(
        attempt.source_type in {"configured_credentials_market_data", "paid_vendor_optional"} and attempt.status != "BLOCKED_CREDENTIALS"
        for attempt in attempts
    )
    return {
        "dataset_id": dataset["dataset_id"],
        "dataset_name": dataset["dataset_name"],
        "fr_dh_reference": dataset.get("fr_dh_reference", "FR-DH-013"),
        "tier": dataset["tier"],
        "domain": dataset["domain"],
        "final_status": winner.status,
        "hydration_attempted": True,
        "hydration_succeeded": bool(first_success),
        "attempted_sources": [attempt.source_attempted for attempt in attempts],
        "free_source_available": any(source_type.startswith("free") or "free_public" in source_type for source_type in source_types),
        "paid_source_likely_required": paid_required,
        "credentials_present": credentials_present,
        "records_written": sum(max(0, attempt.records_written) for attempt in attempts if attempt.status in SUCCESS_STATUSES),
        "artifact_path": first_success.artifact_path if first_success else None,
        "blocker_reason": "" if first_success else f"{winner.status}: {winner.failure_reason}",
        "next_best_action": "" if first_success else winner.recommended_user_action,
        "expected_value_to_caerus": _expected_value(dataset),
        "PIT_safe_status": winner.PIT_safe_status,
        "validation_status": winner.validation_status,
        "latest_attempt_completed_at": winner.completed_at,
    }


def _winner(attempts: list[HydrationResult]) -> HydrationResult:
    for status in ("OK", "PARTIAL"):
        for attempt in attempts:
            if attempt.status == status:
                return attempt
    return attempts[-1]


def _expected_value(dataset: dict[str, Any]) -> str:
    tier = str(dataset.get("tier", ""))
    if tier == "Tier 1":
        return "Required for canonical platform identity, prices, PIT safety, or validation."
    if tier == "Tier 2":
        return "Supports multiple sleeves or broad research workflows."
    if tier == "Tier 3":
        return "Enables sleeve-specific research and event/fundamental evidence."
    return "Experimental research option; requires legal, cost, PIT, and validation review."


def _build_freshness(context: HydrationContext, dataset_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for row in dataset_summaries:
        status = _freshness_status(row)
        rows.append(
            {
                "dataset_id": row["dataset_id"],
                "dataset_name": row["dataset_name"],
                "freshness_status": status,
                "hydration_status": row["final_status"],
                "latest_ingestion_timestamp": row["latest_attempt_completed_at"],
                "as_of_date": context.as_of_date,
                "artifact_path": row["artifact_path"],
                "validation_status": row["validation_status"],
                "PIT_safe_status": row["PIT_safe_status"],
                "reason": row["blocker_reason"] or "hydration_sample_available",
            }
        )
    return {
        "schema_version": "dataset_freshness_v1",
        "generated_at": utc_now_iso(),
        "as_of_date": context.as_of_date,
        "dataset_count": len(rows),
        "failure_levels": ["OK", "WARN_STALE", "WARN_PARTIAL", "FAIL_MISSING", "FAIL_SCHEMA", "FAIL_PIT_VIOLATION"],
        "datasets": rows,
    }


def _freshness_status(row: dict[str, Any]) -> str:
    status = row["final_status"]
    pit_status = str(row.get("PIT_safe_status") or "")
    if "VIOLATION" in pit_status:
        return "FAIL_PIT_VIOLATION"
    if status == "OK":
        return "OK"
    if status == "PARTIAL":
        return "WARN_PARTIAL"
    if status == "SCHEMA_ERROR":
        return "FAIL_SCHEMA"
    return "FAIL_MISSING"


def _build_capability_matrix(dataset_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for row in dataset_summaries:
        rows.append(
            {
                "dataset_id": row["dataset_id"],
                "dataset_name": row["dataset_name"],
                "free_source_available": row["free_source_available"],
                "paid_source_likely_required": row["paid_source_likely_required"],
                "credentials_present": row["credentials_present"],
                "hydration_attempted": row["hydration_attempted"],
                "hydration_succeeded": row["hydration_succeeded"],
                "blocker_reason": row["blocker_reason"],
                "next_best_action": row["next_best_action"],
                "expected_value_to_caerus": row["expected_value_to_caerus"],
                "attempted_sources": row["attempted_sources"],
                "final_status": row["final_status"],
            }
        )
    return {
        "schema_version": "hydration_capability_matrix_v1",
        "generated_at": utc_now_iso(),
        "dataset_count": len(rows),
        "datasets": rows,
    }


def _resolve_symbols(
    repo_root: Path,
    as_of_date: str,
    *,
    symbols: set[str] | None,
    sleeve_id: str | None,
    legacy_candidates_root: Path | None,
) -> list[str]:
    resolved = {_clean_symbol(symbol) for symbol in (symbols or set()) if _clean_symbol(symbol)}
    if sleeve_id:
        resolved.update(_sleeve_symbols(repo_root, as_of_date, sleeve_id=sleeve_id, legacy_candidates_root=legacy_candidates_root))
    return sorted(resolved)


def _sleeve_symbols(repo_root: Path, as_of_date: str, *, sleeve_id: str, legacy_candidates_root: Path | None) -> list[str]:
    strategy_id = _strategy_id_for_sleeve(repo_root, sleeve_id)
    root = legacy_candidates_root if legacy_candidates_root else DEFAULT_LEGACY_CANDIDATES_ROOT
    root = root if root.is_absolute() else repo_root / root
    candidate = _find_legacy_candidate(root, strategy_id, as_of_date)
    if candidate is None:
        return []
    payload = json.loads(candidate.read_text(encoding="utf-8"))
    symbols = {_clean_symbol(row.get("ticker") or row.get("symbol")) for row in payload.get("holdings") or []}
    symbols.update(_clean_symbol(row.get("ticker") or row.get("symbol")) for row in payload.get("rank_table") or [])
    symbols.update(_clean_symbol(symbol) for symbol in (payload.get("target_weights") or {}).keys())
    return sorted(symbol for symbol in symbols if symbol)


def _strategy_id_for_sleeve(repo_root: Path, sleeve_id: str) -> str:
    path = repo_root / DEFAULT_SLEEVE_MANIFEST
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        for row in payload.get("sleeves") or []:
            if row.get("sleeve_id") == sleeve_id:
                return str(row.get("strategy_id") or sleeve_id)
    return f"caerus_{sleeve_id}" if not sleeve_id.startswith("caerus_") else sleeve_id


def _find_legacy_candidate(root: Path, strategy_id: str, as_of_date: str) -> Path | None:
    if not root.exists():
        return None
    candidates = []
    for dated_dir in root.iterdir():
        if not dated_dir.is_dir() or dated_dir.name > as_of_date:
            continue
        candidate = dated_dir / f"{strategy_id}.json"
        if candidate.exists():
            candidates.append(candidate)
    return sorted(candidates)[-1] if candidates else None


def _clean_symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the read-only FR-DH data hydration swarm.")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--as-of-date", default=default_as_of_date())
    parser.add_argument("--dataset", action="append", default=[], help="Dataset id to run. Repeatable.")
    parser.add_argument("--datasets", "--only", nargs="+", default=[], help="Dataset ids to run as a focused subset.")
    parser.add_argument("--source", action="append", default=[], help="Source adapter name to run. Repeatable.")
    parser.add_argument("--sources", nargs="+", default=[], help="Source adapter names to run as a focused subset.")
    parser.add_argument("--sleeve", "--sleeve-id", dest="sleeve_id", default=None, help="Resolve symbols from the latest legacy candidate for this sleeve.")
    parser.add_argument("--symbol", action="append", default=[], help="Symbol to hydrate. Repeatable.")
    parser.add_argument("--symbols", nargs="+", default=[], help="Symbols to hydrate as a focused universe.")
    parser.add_argument("--legacy-candidates-root", type=Path, default=None)
    parser.add_argument("--env-file", default=None, help="Optional env file with Nasdaq Data Link credentials. Secret values are never logged.")
    parser.add_argument("--timeout-seconds", type=int, default=12)
    parser.add_argument("--dry-run", action="store_true", help="Classify all datasets without network/source calls.")
    parser.add_argument("--limit-sample", action="store_true", help="Attempt small sample pulls where adapters support them.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.env_file:
        _load_env_file(args.env_file)
    dataset_ids = set(args.dataset or []) | set(args.datasets or [])
    source_names = set(args.source or []) | set(args.sources or [])
    symbols = set(args.symbol or []) | set(args.symbols or [])
    payload = run_swarm(
        repo_root=args.repo_root,
        dry_run=bool(args.dry_run),
        limit_sample=bool(args.limit_sample),
        as_of_date=str(args.as_of_date),
        dataset_ids=dataset_ids or None,
        source_names=source_names or None,
        symbols=symbols or None,
        sleeve_id=args.sleeve_id,
        legacy_candidates_root=args.legacy_candidates_root,
        timeout_seconds=int(args.timeout_seconds),
    )
    print(json.dumps(payload["summary"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

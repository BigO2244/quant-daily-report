"""Command-line entrypoint for the research-only Alpha Lab data spine."""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from projects.alpha_lab.factory import canonical_json

from .alpha_vantage import collect_alpha_vantage_free_proxies
from .boundary import build_boundary_attestation
from .bea_io import collect_bea_io_api, collect_bea_io_reference
from .eia_bulk import collect_eia_large_bulk, materialize_eia_electricity_controls
from .materialize import (
    materialize_controls,
    materialize_earnings_events,
    materialize_identity,
    materialize_sec_facts,
)
from .market import materialize_market_panels
from .terminal_returns import build_terminal_return_sensitivity
from .readiness import build_readiness
from .registry import load_registry
from .sec_bulk import collect_sec_companyfacts_bulk, collect_sec_submissions_bulk
from .sec_delisting import prepare_combined_8k_hydration_index, prepare_delisting_hydration_index
from .sec_earnings import prepare_earnings_hydration_index
from .sec_insiders import (
    audit_insider_hydration,
    collect_sec_insider_archives,
    materialize_original_insider_events,
    materialize_insider_events,
    prepare_insider_hydration_index,
)
from .sec_original_stream import capture_sec_original_stream
from .sharadar_bulk import capture_sharadar_bulk
from .sharadar_stream import capture_sharadar_stream
from .sources import (
    audit_eia,
    audit_sharadar,
    capture_sharadar_table,
    collect_eia_bulk,
    collect_factors,
    collect_fred_alfred,
    collect_occ_reference,
    collect_occ_local,
    collect_sec_master_indexes,
    collect_sec_reference,
    hydrate_sec_filings,
)
from .storage import write_bundle
from .vendor import validate_vendor_sample
from .usaspending import capture_usaspending_government_customer_proxy
from .yfinance_analyst import collect_yfinance_analyst_proxy


_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _load_local_env(repo_root: Path) -> None:
    """Load simple ignored project credentials without overriding the caller."""

    path = repo_root / ".env"
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not _ENV_KEY.fullmatch(key):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Research-only point-in-time data-spine collectors"
    )
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--config", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("audit-sharadar")
    sharadar = sub.add_parser("capture-sharadar")
    sharadar.add_argument("--table", required=True)
    sharadar.add_argument("--tickers", default="")
    sharadar.add_argument("--start-date")
    sharadar.add_argument("--end-date")
    sharadar.add_argument("--maximum-rows", type=int)
    sharadar_bulk = sub.add_parser("capture-sharadar-bulk")
    sharadar_bulk.add_argument("--table", required=True)
    sharadar_bulk.add_argument("--columns", required=True)
    sharadar_bulk.add_argument("--tickers", default="")
    sharadar_bulk.add_argument("--tickers-file", type=Path)
    sharadar_bulk.add_argument("--start-date")
    sharadar_bulk.add_argument("--end-date")
    sharadar_bulk.add_argument("--poll-interval", type=float, default=10.0)
    sharadar_bulk.add_argument("--timeout-seconds", type=float, default=1800.0)
    sharadar_stream = sub.add_parser("capture-sharadar-stream")
    sharadar_stream.add_argument("--table", required=True)
    sharadar_stream.add_argument("--columns", required=True)
    sharadar_stream.add_argument("--tickers", default="")
    sharadar_stream.add_argument("--tickers-file", type=Path)
    sharadar_stream.add_argument("--ticker-chunk-size", type=int, default=200)
    sharadar_stream.add_argument("--start-date")
    sharadar_stream.add_argument("--end-date")
    sec_reference = sub.add_parser("sec-reference")
    sec_reference.add_argument("--user-agent")
    sec_index = sub.add_parser("sec-index")
    sec_index.add_argument("--start-year", type=int, default=2012)
    sec_index.add_argument("--end-year", type=int, default=datetime.now().year)
    sec_index.add_argument("--user-agent")
    sec_hydrate = sub.add_parser("sec-hydrate")
    sec_hydrate.add_argument("--index", type=Path, required=True)
    sec_hydrate.add_argument("--forms", default="4,4/A")
    sec_hydrate.add_argument("--limit", type=int, default=500)
    sec_hydrate.add_argument("--user-agent")
    sec_original_stream = sub.add_parser("sec-original-stream")
    sec_original_stream.add_argument("--index", type=Path, required=True)
    sec_original_stream.add_argument("--forms", default="4,4/A")
    sec_original_stream.add_argument("--partition-size", type=int, default=1000)
    sec_original_stream.add_argument("--max-new-partitions", type=int)
    sec_original_stream.add_argument("--request-workers", type=int, default=4)
    sec_original_stream.add_argument("--user-agent")
    sec_companyfacts = sub.add_parser("sec-companyfacts")
    sec_companyfacts.add_argument("--user-agent")
    sec_submissions = sub.add_parser("sec-submissions")
    sec_submissions.add_argument("--user-agent")
    sec_insiders = sub.add_parser("sec-insiders")
    sec_insiders.add_argument("--start-year", type=int, default=2012)
    sec_insiders.add_argument("--end-year", type=int, default=2026)
    sec_insiders.add_argument("--user-agent")
    sub.add_parser("factors")
    sub.add_parser("fred")
    sub.add_parser("bea-io-reference")
    bea_io = sub.add_parser("bea-io-api")
    bea_io.add_argument("--table-ids", default="")
    bea_io.add_argument("--years", default="ALL")
    alpha_vantage = sub.add_parser("alpha-vantage-free-proxies")
    alpha_vantage.add_argument("--tickers", default="")
    alpha_vantage.add_argument("--tickers-file", type=Path)
    alpha_vantage.add_argument("--max-tickers", type=int, default=20)
    alpha_vantage.add_argument("--listing-date")
    alpha_vantage.add_argument("--no-listing-status", action="store_true")
    sub.add_parser("eia-audit")
    eia = sub.add_parser("eia-bulk")
    eia.add_argument("--datasets", default="natural_gas,petroleum")
    eia_large = sub.add_parser("eia-large-bulk")
    eia_large.add_argument("--dataset", required=True)
    sub.add_parser("materialize-eia-electricity")
    sub.add_parser("occ")
    occ_intake = sub.add_parser("occ-intake")
    occ_intake.add_argument("--directory", type=Path, required=True)
    vendor = sub.add_parser("validate-vendor")
    vendor.add_argument("--kind", choices=("analyst_estimates", "supply_chain"), required=True)
    vendor.add_argument("--sample", type=Path, required=True)
    bootstrap = sub.add_parser("bootstrap-free")
    bootstrap.add_argument("--sec-user-agent")
    bootstrap.add_argument("--include-eia-bulk", action="store_true")
    sub.add_parser("validate-boundary")
    sub.add_parser("materialize-identity")
    sub.add_parser("materialize-controls")
    sub.add_parser("materialize-sec-facts")
    sub.add_parser("materialize-earnings-events")
    sub.add_parser("prepare-earnings-hydration")
    sub.add_parser("prepare-delisting-hydration")
    sub.add_parser("prepare-combined-8k-hydration")
    usaspending = sub.add_parser("usaspending-government-customers")
    usaspending.add_argument("--partition-size", type=int, default=100)
    usaspending.add_argument("--max-new-partitions", type=int)
    usaspending.add_argument("--max-pages-per-issuer", type=int, default=100)
    yfinance_analyst = sub.add_parser("yfinance-analyst-proxy")
    yfinance_analyst.add_argument("--tickers", default="")
    yfinance_analyst.add_argument("--tickers-file", type=Path)
    yfinance_analyst.add_argument("--max-tickers", type=int, default=250)
    yfinance_analyst.add_argument("--workers", type=int, default=4)
    sub.add_parser("materialize-insiders")
    sub.add_parser("materialize-original-insiders")
    sub.add_parser("prepare-insider-hydration")
    sub.add_parser("audit-insider-hydration")
    market = sub.add_parser("materialize-market")
    market.add_argument("--sep-manifest", type=Path, required=True)
    market.add_argument("--resume-staged-database", action="store_true")
    terminal = sub.add_parser("terminal-return-sensitivity")
    terminal.add_argument("--panel", type=Path)
    return parser


def _summary(result: Dict[str, Any]) -> Dict[str, Any]:
    output = {
        "classification": "RESEARCH_ONLY_NON_EXECUTIONAL",
        "trading_behavior_changed": False,
    }
    for key in ("bundle_id", "manifest_path"):
        if key in result:
            output[key] = str(result[key])
    if "audit" in result:
        output["audit"] = result["audit"]
    if "readiness" in result:
        output["overall_status"] = result["readiness"]["overall_status"]
        output["blockers"] = result["readiness"]["blockers"]
        output["sharadar_access_decision"] = result["readiness"]["sharadar_access_decision"]
    if "gate" in result:
        output["vendor_gate_status"] = result["gate"]["status"]
        output["missing_fields"] = result["gate"]["missing_fields"]
    if "production_boundary_status" in result:
        output["production_boundary_status"] = result["production_boundary_status"]
        output["findings"] = result["findings"]
    if "steps" in result:
        output["steps"] = result["steps"]
    if "capture_status" in result:
        output["capture_status"] = result["capture_status"]
    return output


def _bootstrap(repo_root: Path, registry, sec_user_agent: str | None, include_eia_bulk: bool) -> Dict[str, Any]:
    steps = []
    actions = (
        ("audit_sharadar", lambda: audit_sharadar(repo_root=repo_root, registry=registry)),
        ("factors", lambda: collect_factors(repo_root=repo_root, registry=registry)),
        ("fred_alfred", lambda: collect_fred_alfred(repo_root=repo_root, registry=registry)),
        ("eia_audit", lambda: audit_eia(repo_root=repo_root, registry=registry)),
        ("occ_reference", lambda: collect_occ_reference(repo_root=repo_root, registry=registry)),
    )
    for name, action in actions:
        try:
            result = action()
            steps.append({"step": name, "status": "COMPLETE", "manifest_path": str(result["manifest_path"])})
        except Exception as exc:
            steps.append({"step": name, "status": "BLOCKED", "error_type": type(exc).__name__, "message": str(exc)})
    try:
        result = collect_sec_reference(
            repo_root=repo_root, registry=registry, user_agent=sec_user_agent
        )
        steps.append({"step": "sec_reference", "status": "COMPLETE", "manifest_path": str(result["manifest_path"])})
    except Exception as exc:
        steps.append({"step": "sec_reference", "status": "BLOCKED", "error_type": type(exc).__name__, "message": str(exc)})
    if include_eia_bulk:
        try:
            result = collect_eia_bulk(
                repo_root=repo_root,
                registry=registry,
                datasets=("natural_gas", "petroleum"),
            )
            steps.append({"step": "eia_bulk", "status": "COMPLETE", "manifest_path": str(result["manifest_path"])})
        except Exception as exc:
            steps.append({"step": "eia_bulk", "status": "BLOCKED", "error_type": type(exc).__name__, "message": str(exc)})
    readiness = build_readiness(repo_root=repo_root, registry=registry)
    return {"steps": steps, "readiness": readiness["readiness"], "manifest_path": readiness["manifest_path"]}


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    repo_root = args.repo_root.expanduser().resolve()
    _load_local_env(repo_root)
    registry = load_registry(args.config)
    if args.command == "status":
        result = build_readiness(repo_root=repo_root, registry=registry)
    elif args.command == "audit-sharadar":
        result = audit_sharadar(repo_root=repo_root, registry=registry)
    elif args.command == "capture-sharadar":
        result = capture_sharadar_table(
            repo_root=repo_root,
            registry=registry,
            table=args.table,
            tickers=args.tickers.split(","),
            start_date=args.start_date,
            end_date=args.end_date,
            maximum_rows=args.maximum_rows,
        )
    elif args.command == "capture-sharadar-bulk":
        tickers = [value.strip() for value in args.tickers.split(",") if value.strip()]
        if args.tickers_file:
            tickers.extend(
                value.strip()
                for value in args.tickers_file.read_text(encoding="utf-8").splitlines()
                if value.strip()
            )
        result = capture_sharadar_bulk(
            repo_root=repo_root,
            registry=registry,
            table=args.table,
            columns=(value.strip() for value in args.columns.split(",")),
            start_date=args.start_date,
            end_date=args.end_date,
            tickers=tickers,
            poll_interval=args.poll_interval,
            timeout_seconds=args.timeout_seconds,
        )
    elif args.command == "capture-sharadar-stream":
        tickers = [value.strip() for value in args.tickers.split(",") if value.strip()]
        if args.tickers_file:
            tickers.extend(
                value.strip()
                for value in args.tickers_file.read_text(encoding="utf-8").splitlines()
                if value.strip()
            )
        result = capture_sharadar_stream(
            repo_root=repo_root,
            registry=registry,
            table=args.table,
            columns=(value.strip() for value in args.columns.split(",")),
            start_date=args.start_date,
            end_date=args.end_date,
            tickers=tickers,
            ticker_chunk_size=args.ticker_chunk_size,
        )
    elif args.command == "sec-reference":
        result = collect_sec_reference(
            repo_root=repo_root, registry=registry, user_agent=args.user_agent
        )
    elif args.command == "sec-index":
        result = collect_sec_master_indexes(
            repo_root=repo_root,
            registry=registry,
            start_year=args.start_year,
            end_year=args.end_year,
            user_agent=args.user_agent,
        )
    elif args.command == "sec-hydrate":
        result = hydrate_sec_filings(
            repo_root=repo_root,
            registry=registry,
            index_path=args.index,
            forms=tuple(value.strip() for value in args.forms.split(",") if value.strip()),
            limit=args.limit,
            user_agent=args.user_agent,
        )
    elif args.command == "sec-original-stream":
        result = capture_sec_original_stream(
            repo_root=repo_root,
            registry=registry,
            index_path=args.index,
            forms=tuple(value.strip() for value in args.forms.split(",") if value.strip()),
            partition_size=args.partition_size,
            max_new_partitions=args.max_new_partitions,
            request_workers=args.request_workers,
            user_agent=args.user_agent,
        )
    elif args.command == "sec-companyfacts":
        result = collect_sec_companyfacts_bulk(
            repo_root=repo_root,
            registry=registry,
            user_agent=args.user_agent,
        )
    elif args.command == "sec-submissions":
        result = collect_sec_submissions_bulk(
            repo_root=repo_root,
            registry=registry,
            user_agent=args.user_agent,
        )
    elif args.command == "sec-insiders":
        result = collect_sec_insider_archives(
            repo_root=repo_root,
            registry=registry,
            start_year=args.start_year,
            end_year=args.end_year,
            user_agent=args.user_agent,
        )
    elif args.command == "factors":
        result = collect_factors(repo_root=repo_root, registry=registry)
    elif args.command == "fred":
        result = collect_fred_alfred(repo_root=repo_root, registry=registry)
    elif args.command == "bea-io-reference":
        result = collect_bea_io_reference(repo_root=repo_root)
    elif args.command == "bea-io-api":
        result = collect_bea_io_api(
            repo_root=repo_root,
            table_ids=tuple(
                int(value.strip())
                for value in args.table_ids.split(",")
                if value.strip()
            ),
            years=args.years,
        )
    elif args.command == "alpha-vantage-free-proxies":
        tickers = [value.strip() for value in args.tickers.split(",") if value.strip()]
        if args.tickers_file:
            tickers.extend(
                value.strip()
                for value in args.tickers_file.read_text(encoding="utf-8").splitlines()
                if value.strip()
            )
        result = collect_alpha_vantage_free_proxies(
            repo_root=repo_root,
            tickers=tickers,
            max_tickers=args.max_tickers,
            listing_date=args.listing_date,
            include_listing_status=not args.no_listing_status,
        )
    elif args.command == "eia-audit":
        result = audit_eia(repo_root=repo_root, registry=registry)
    elif args.command == "eia-bulk":
        result = collect_eia_bulk(
            repo_root=repo_root,
            registry=registry,
            datasets=tuple(value.strip() for value in args.datasets.split(",") if value.strip()),
        )
    elif args.command == "eia-large-bulk":
        result = collect_eia_large_bulk(
            repo_root=repo_root,
            registry=registry,
            dataset=args.dataset,
        )
    elif args.command == "materialize-eia-electricity":
        result = materialize_eia_electricity_controls(repo_root)
    elif args.command == "occ":
        result = collect_occ_reference(repo_root=repo_root, registry=registry)
    elif args.command == "occ-intake":
        result = collect_occ_local(
            repo_root=repo_root, directory=args.directory
        )
    elif args.command == "validate-vendor":
        result = validate_vendor_sample(
            repo_root=repo_root, kind=args.kind, sample_path=args.sample
        )
    elif args.command == "bootstrap-free":
        result = _bootstrap(
            repo_root, registry, args.sec_user_agent, args.include_eia_bulk
        )
    elif args.command == "validate-boundary":
        attestation = build_boundary_attestation()
        result = attestation
        bundle = write_bundle(
            repo_root=repo_root,
            source_id="boundary",
            files={"boundary.json": (canonical_json(attestation) + "\n").encode("utf-8")},
            metadata={"kind": "static_ast_attestation"},
            retrieved_at=datetime.now(timezone.utc),
        )
        result.update(bundle)
    elif args.command == "materialize-identity":
        result = materialize_identity(repo_root)
    elif args.command == "materialize-controls":
        result = materialize_controls(repo_root)
    elif args.command == "materialize-sec-facts":
        result = materialize_sec_facts(repo_root)
    elif args.command == "materialize-earnings-events":
        result = materialize_earnings_events(repo_root)
    elif args.command == "prepare-earnings-hydration":
        result = prepare_earnings_hydration_index(repo_root)
    elif args.command == "prepare-delisting-hydration":
        result = prepare_delisting_hydration_index(repo_root)
    elif args.command == "prepare-combined-8k-hydration":
        result = prepare_combined_8k_hydration_index(repo_root)
    elif args.command == "usaspending-government-customers":
        result = capture_usaspending_government_customer_proxy(
            repo_root=repo_root,
            partition_size=args.partition_size,
            max_new_partitions=args.max_new_partitions,
            max_pages_per_issuer=args.max_pages_per_issuer,
        )
    elif args.command == "yfinance-analyst-proxy":
        tickers = [value.strip() for value in args.tickers.split(",") if value.strip()]
        if args.tickers_file:
            with args.tickers_file.open("r", encoding="utf-8", newline="") as stream:
                if args.tickers_file.suffix.lower() == ".csv":
                    import csv

                    tickers.extend(
                        str(row.get("ticker") or "").strip()
                        for row in csv.DictReader(line for line in stream if line.strip())
                        if str(row.get("ticker") or "").strip()
                    )
                else:
                    tickers.extend(line.strip() for line in stream if line.strip())
        result = collect_yfinance_analyst_proxy(
            repo_root=repo_root,
            tickers=tickers,
            max_tickers=args.max_tickers,
            workers=args.workers,
        )
    elif args.command == "materialize-insiders":
        result = materialize_insider_events(repo_root)
    elif args.command == "materialize-original-insiders":
        result = materialize_original_insider_events(repo_root)
    elif args.command == "prepare-insider-hydration":
        result = prepare_insider_hydration_index(repo_root)
    elif args.command == "audit-insider-hydration":
        result = audit_insider_hydration(repo_root)
    elif args.command == "materialize-market":
        result = materialize_market_panels(
            repo_root=repo_root,
            sep_manifest_path=args.sep_manifest,
            resume_staged_database=args.resume_staged_database,
        )
    elif args.command == "terminal-return-sensitivity":
        result = build_terminal_return_sensitivity(
            repo_root=repo_root,
            panel_path=args.panel,
        )
    else:
        raise AssertionError("unreachable")
    summary = _summary(result)
    for key, value in result.items():
        if key.endswith("_rows") or key.endswith("_count") or key.endswith("_path"):
            summary[key] = value
    print(canonical_json(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

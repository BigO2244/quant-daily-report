#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.price_hydration import (  # noqa: E402
    DEFAULT_CACHE_PATH,
    build_status_payload,
    cache_max_date,
    resolve_completed_trading_day,
    write_status,
)
from core.orion_decision_lineage import (  # noqa: E402
    LINEAGE_SCHEMA,
    build_readiness_payload,
    canonical_hash,
)
from paper.trading_calendar import prev_trading_day  # noqa: E402
from core.sleeve_control_plane import validate_orion_decision_lineage  # noqa: E402
from research.flow_detection.data import ensure_price_panel, load_universe  # noqa: E402
from scripts import refresh_shadow_scorecard_artifacts  # noqa: E402


BENCHMARK_SYMBOL = "SPY"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Hydrate the canonical shadow price cache only, without running shadow strategy artifacts."
    )
    parser.add_argument("--trade-date", default=None, help="Optional completed trading day. Defaults to latest completed trading day.")
    parser.add_argument("--start-date", default="2014-01-01")
    parser.add_argument("--universe-path", default="data/universe.csv")
    parser.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH))
    parser.add_argument("--status-dir", default="outputs/price_hydration")
    parser.add_argument("--ticker-exceptions-path", default="data/ticker_exceptions.json")
    parser.add_argument("--hydration-source", default="vm_cache_only")
    parser.add_argument("--chunk-size", type=int, default=25)
    parser.add_argument(
        "--shadow-start-date",
        default=None,
        help="Start date for artifact-only shadow scorecard refresh. Defaults to Jan 1 of the prior year.",
    )
    parser.add_argument(
        "--refresh-shadow-artifacts",
        action="store_true",
        help="After verified cache hydration, regenerate and publish artifact-only shadow outputs for the completed day.",
    )
    parser.add_argument("--shadow-output-dir", default="outputs/shadow_candidates")
    parser.add_argument("--dry-run", action="store_true", help="Resolve inputs and print planned action without downloads or writes.")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero if cache coverage is not verified.")
    return parser


def _resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else _REPO_ROOT / path


def _load_symbols(universe_path: Path) -> list[str]:
    universe = load_universe(universe_path)
    return sorted(set(universe + [BENCHMARK_SYMBOL]))


def _publish_shadow_latest(shadow_output_dir: Path, trade_date: str) -> dict[str, Any]:
    dated_dir = shadow_output_dir / trade_date
    latest_dir = shadow_output_dir / "latest"
    artifacts = ("comparison.md", "comparison.json", "delta.json", "shadow_evaluation.json", "feedback_loop_summary.json")
    optional_artifacts = ("alpha_evidence_chain.json", "alpha_evidence_chain.md")
    missing: list[str] = []
    published: list[str] = []
    latest_dir.mkdir(parents=True, exist_ok=True)
    for artifact in artifacts:
        source = dated_dir / artifact
        if not source.exists():
            missing.append(artifact)
            continue
        (latest_dir / artifact).write_bytes(source.read_bytes())
        published.append(artifact)
    for artifact in optional_artifacts:
        source = dated_dir / artifact
        if source.exists():
            (latest_dir / artifact).write_bytes(source.read_bytes())
            published.append(artifact)
    return {
        "latest_dir": str(latest_dir),
        "published_artifacts": published,
        "missing_artifacts": missing,
        "status": "OK" if not missing else "PARTIAL",
    }


def _refresh_shadow_artifacts(
    *,
    trade_date: str,
    start_date: str,
    cache_path: Path,
    shadow_output_dir: Path,
) -> dict[str, Any]:
    argv = [
        "--trade-date",
        trade_date,
        "--start-date",
        start_date,
        "--output-dir",
        str(shadow_output_dir),
        "--price-cache-path",
        str(cache_path),
    ]
    rc = refresh_shadow_scorecard_artifacts.main(argv)
    details: dict[str, Any] = {}
    performance_path = shadow_output_dir / trade_date / "shadow_performance.json"
    if performance_path.exists():
        try:
            performance = json.loads(performance_path.read_text(encoding="utf-8"))
            details["performance_status"] = performance.get("status")
            details["data_status"] = performance.get("data_status")
            details["data_reason"] = performance.get("data_reason")
        except Exception as exc:
            details["performance_read_error"] = str(exc)
    nav_path = shadow_output_dir / "performance" / "shadow_nav_series.csv"
    if nav_path.exists():
        try:
            import csv

            with nav_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            details["nav_series_rows"] = len(rows)
            details["nav_series_latest_date"] = rows[-1].get("date") if rows else None
        except Exception as exc:
            details["nav_series_read_error"] = str(exc)
    reason = None
    if details.get("performance_status") and details.get("performance_status") != "OK":
        reason = f"performance_status={details['performance_status']}"
    elif details.get("data_status") and details.get("data_status") != "OK":
        reason = f"data_status={details['data_status']}"
    result = {
        "status": "OK" if rc == 0 else "FAILED",
        "exit_code": int(rc),
        "trade_date": trade_date,
        "shadow_start_date": start_date,
        "shadow_output_dir": str(shadow_output_dir),
    }
    result.update(details)
    if reason:
        result["reason"] = reason
    return result


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    as_of_date = resolve_completed_trading_day(explicit_trade_date=args.trade_date)
    cache_path = _resolve_path(args.cache_path)
    status_path = _resolve_path(args.status_dir) / as_of_date / "status.json"
    universe_path = _resolve_path(args.universe_path)
    ticker_exceptions_path = _resolve_path(args.ticker_exceptions_path)
    shadow_output_dir = _resolve_path(args.shadow_output_dir)
    shadow_start_date = args.shadow_start_date or f"{int(as_of_date[:4]) - 1}-01-01"
    before_max_date = cache_max_date(cache_path)

    symbols = _load_symbols(universe_path)
    planned = {
        "as_of_date": as_of_date,
        "start_date": args.start_date,
        "cache_path": str(cache_path),
        "status_path": str(status_path),
        "ticker_exceptions_path": str(ticker_exceptions_path),
        "symbols_requested": len(symbols),
        "before_max_cache_date": before_max_date,
        "hydration_source": args.hydration_source,
        "refresh_shadow_artifacts": bool(args.refresh_shadow_artifacts),
        "shadow_output_dir": str(shadow_output_dir),
        "shadow_start_date": shadow_start_date,
        "dry_run": bool(args.dry_run),
        "artifact_only": True,
    }
    print(json.dumps(planned, indent=2, sort_keys=True))

    if args.dry_run:
        return 0

    hydration_exit_code = 0
    panel_meta: dict = {}
    try:
        _panel, panel_meta = ensure_price_panel(
            symbols=symbols,
            start_date=args.start_date,
            end_date=as_of_date,
            cache_path=cache_path,
            prefer_local=True,
            allow_download=True,
            chunk_size=args.chunk_size,
            ticker_exceptions_path=ticker_exceptions_path,
            required_anchor_dates=[prev_trading_day(as_of_date)],
            required_history_offsets=[1, 3, 21, 126, 252],
        )
    except Exception as exc:
        hydration_exit_code = 1
        panel_meta = {"error": str(exc)}
        print(f"[PRICE_CACHE_ONLY][WARN] hydration failed: {exc}", file=sys.stderr)

    max_date = cache_max_date(cache_path)
    payload = build_status_payload(
        as_of_date=as_of_date,
        max_cache_date=max_date,
        hydration_exit_code=hydration_exit_code,
        download_attempted=bool(panel_meta.get("download_performed", True)),
        provider="yfinance",
        hydration_source=args.hydration_source,
        coverage_validation=panel_meta.get("coverage_validation"),
    )
    cache_publish = panel_meta.get("cache_publish") or {}
    catchup_validation = panel_meta.get("catchup_validation") or {}
    publication_reasons: list[str] = []
    if cache_publish.get("status") == "BLOCKED_UNCHANGED":
        publication_reasons.append("canonical_cache_publication_blocked")
    if catchup_validation.get("status") == "INCOMPLETE":
        publication_reasons.append("downloaded_session_continuity_incomplete")
    if publication_reasons:
        if payload.get("status") == "OK":
            payload["status"] = "PARTIAL"
            payload["reason"] = "; ".join(publication_reasons)
        else:
            payload["reason"] = "; ".join(
                [str(payload.get("reason") or ""), *publication_reasons]
            ).strip("; ")
        payload["publication_validation"] = {
            "status": "INCOMPLETE",
            "reason_codes": publication_reasons,
        }
    payload["cache_only"] = True
    payload["symbols_requested"] = len(symbols)
    payload["before_max_cache_date"] = before_max_date
    payload["canonical_cache_path"] = str(cache_path)
    payload["ignored_tickers"] = list(panel_meta.get("ignored_tickers") or [])
    payload["aliased_tickers"] = dict(panel_meta.get("aliased_tickers") or {})
    payload["panel_meta"] = panel_meta

    shadow_refresh: dict[str, Any] | None = None
    if args.refresh_shadow_artifacts and payload.get("status") == "OK":
        try:
            shadow_refresh = _refresh_shadow_artifacts(
                trade_date=as_of_date,
                start_date=shadow_start_date,
                cache_path=cache_path,
                shadow_output_dir=shadow_output_dir,
            )
        except Exception as exc:
            shadow_refresh = {
                "status": "FAILED",
                "exit_code": 1,
                "trade_date": as_of_date,
                "shadow_output_dir": str(shadow_output_dir),
                "error": str(exc),
            }
            print(f"[PRICE_CACHE_ONLY][WARN] shadow refresh failed: {exc}", file=sys.stderr)
    elif args.refresh_shadow_artifacts:
        shadow_refresh = {
            "status": "SKIPPED",
            "trade_date": as_of_date,
            "reason": f"price hydration status is {payload.get('status')}",
        }
    if shadow_refresh is not None:
        payload["shadow_refresh"] = shadow_refresh

    write_status(status_path, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))

    if args.strict and payload.get("status") == "OK" and shadow_refresh is not None and shadow_refresh.get("status") == "OK":
        orion_source_path = shadow_output_dir / as_of_date / "caerus_orion.json"
        try:
            source_payload = json.loads(orion_source_path.read_text(encoding="utf-8"))
            lineage = source_payload.get("decision_lineage")
            if not isinstance(lineage, dict) or lineage.get("schema_version") != LINEAGE_SCHEMA:
                raise ValueError("completed Orion source artifact has no valid decision_lineage")
            if source_payload.get("decision_eligible") is not True or source_payload.get("observation_status") != "OK":
                raise ValueError("completed Orion source artifact is not explicitly Decision-eligible")
            if str(lineage.get("effective_trade_date") or "") != as_of_date:
                raise ValueError("Orion lineage effective date does not match completed hydration date")
            if str(lineage.get("trade_date") or "") != as_of_date:
                raise ValueError("Orion lineage trade date does not match completed hydration date")
            if canonical_hash(source_payload.get("target_weights") or {}) != lineage.get("target_weights_hash"):
                raise ValueError("Orion target_weights_hash does not match source artifact")
            previous_source_payload = None
            previous_source_path = (
                shadow_output_dir
                / prev_trading_day(as_of_date)
                / "caerus_orion.json"
            )
            if previous_source_path.is_file():
                previous_source_payload = json.loads(previous_source_path.read_text(encoding="utf-8"))
            lineage_failures = validate_orion_decision_lineage(
                source_payload,
                effective_trade_date=as_of_date,
                previous_source_payload=previous_source_payload,
            )
            if lineage_failures:
                raise ValueError(
                    "completed Orion source artifact failed canonical lineage validation: "
                    + ",".join(lineage_failures)
                )
            readiness = build_readiness_payload(
                trade_date=as_of_date,
                source_artifact_path=orion_source_path,
                decision_lineage=lineage,
                hydration_status_path=status_path,
                repo_root=_REPO_ROOT,
            )
            readiness_path = status_path.parent / "orion_decision_ready.json"
            write_status(readiness_path, readiness)
            print(json.dumps({"orion_decision_readiness": str(readiness_path), "status": "READY"}, sort_keys=True))
        except Exception as exc:
            print(f"[PRICE_CACHE_ONLY][WARN] Orion readiness not written: {exc}", file=sys.stderr)
            return 1

    if args.strict and payload.get("status") != "OK":
        return 1
    if args.strict and shadow_refresh is not None and shadow_refresh.get("status") != "OK":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Restate operational Shadow NAV from dated same-day observations.

This is a reporting/artifact recovery utility. It reads dated Shadow
performance artifacts and point-in-time price inputs, reconstructs the
same-day close-to-close return stream, and writes a staged operational NAV
series. It never runs broker, execution, allocation, or trading workflows.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.strategy_registry import load_strategy_registry  # noqa: E402


BENCHMARK_SLUG = "spy_benchmark"
BENCHMARK_SYMBOL = "SPY"
MODEL_ENTRIES = load_strategy_registry().active_shadow_security_selection_entries()
MODEL_SLUGS = tuple(entry.strategy_id for entry in MODEL_ENTRIES)
MODEL_NAMES = {entry.strategy_id: entry.display_name for entry in MODEL_ENTRIES} | {BENCHMARK_SLUG: "SPY"}
SERIES_COLUMNS = ("date", *MODEL_SLUGS, BENCHMARK_SLUG)
SUMMARY_SCHEMA_VERSION = "shadow_operational_same_day_summary_v1"
MANIFEST_SCHEMA_VERSION = "shadow_operational_same_day_restatement_manifest_v1"
RETURN_CONVENTION = "dated_same_day_close_to_close_v1"
OWNER_DECISION = (
    "2026-06-13 owner decision: Option 3 approved; dated same-day returns are "
    "the canonical operational Shadow observation methodology."
)
DEFAULT_TOLERANCE = 1e-9


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Stage or replace Shadow operational NAV using dated same-day returns."
    )
    parser.add_argument("--repo-root", default=str(_REPO_ROOT))
    parser.add_argument("--output-dir", default="outputs/shadow_candidates")
    parser.add_argument("--price-cache-path", default="outputs/research/flow_detection_v1/price_panel.parquet")
    parser.add_argument("--staging-dir", default=None)
    parser.add_argument("--backup-root", default="outputs/recovery_backups")
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    parser.add_argument("--replace-active", action="store_true")
    parser.add_argument("--expected-existing-nav-sha256", default=None)
    parser.add_argument("--expected-existing-summary-sha256", default=None)
    return parser


def sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(SERIES_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in SERIES_COLUMNS})


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(path)


def _atomic_copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = destination.with_name(f".{destination.name}.tmp")
    tmp_path.write_bytes(source.read_bytes())
    tmp_path.replace(destination)


def _dated_dirs(output_root: Path, *, start_date: str | None, end_date: str | None) -> list[Path]:
    dirs = [
        path
        for path in output_root.iterdir()
        if path.is_dir() and len(path.name) == 10 and path.name[4] == "-" and path.name[7] == "-"
    ]
    selected = []
    for path in sorted(dirs, key=lambda item: item.name):
        if start_date and path.name < start_date:
            continue
        if end_date and path.name > end_date:
            continue
        if (path / "shadow_performance.json").exists():
            selected.append(path)
    return selected


def _load_price_returns(price_cache_path: Path) -> tuple[dict[str, dict[str, float]], dict[str, Any]]:
    panel = pd.read_parquet(price_cache_path)
    required = {"date", "ticker", "close"}
    missing = sorted(required - set(panel.columns))
    if missing:
        raise RuntimeError(f"price panel missing required columns: {missing}")
    panel = panel.copy()
    panel["date"] = pd.to_datetime(panel["date"])
    panel["ticker"] = panel["ticker"].astype(str)
    panel = panel.sort_values(["ticker", "date"]).reset_index(drop=True)
    panel["daily_return"] = panel.groupby("ticker")["close"].pct_change()
    returns_by_date: dict[str, dict[str, float]] = {}
    for date, frame in panel.groupby(panel["date"].dt.strftime("%Y-%m-%d")):
        returns_by_date[str(date)] = {
            str(row.ticker): round(float(row.daily_return), 10)
            for row in frame.itertuples()
            if pd.notna(row.daily_return)
        }
    metadata = {
        "path": str(price_cache_path),
        "sha256": sha256_file(price_cache_path),
        "min_date": str(panel["date"].min().date()) if not panel.empty else None,
        "max_date": str(panel["date"].max().date()) if not panel.empty else None,
        "rows": int(len(panel)),
        "tickers": int(panel["ticker"].nunique()) if not panel.empty else 0,
    }
    return returns_by_date, metadata


def _weights_for_strategy(dated_dir: Path, slug: str) -> tuple[dict[str, float], str | None]:
    path = dated_dir / f"{slug}.json"
    if not path.exists():
        return {}, f"missing strategy artifact {path}"
    payload = _read_json(path)
    weights = {str(key): float(value) for key, value in (payload.get("target_weights") or {}).items()}
    return weights, None


def _weighted_return(weights: dict[str, float], returns_by_ticker: dict[str, float]) -> tuple[float, list[str]]:
    missing = sorted(ticker for ticker in weights if ticker not in returns_by_ticker)
    value = sum(float(weight) * float(returns_by_ticker.get(ticker, 0.0)) for ticker, weight in weights.items())
    return round(float(value), 10), missing


def _validate_daily_returns(
    *,
    dated_dir: Path,
    performance: dict[str, Any],
    returns_by_date: dict[str, dict[str, float]],
    tolerance: float,
) -> list[dict[str, Any]]:
    trade_date = str(performance.get("trade_date") or dated_dir.name)
    ticker_returns = returns_by_date.get(trade_date) or {}
    if not ticker_returns:
        return [
            {
                "date": trade_date,
                "strategy": "*",
                "status": "BLOCKED_MISSING_PRICES",
                "reason": f"no price returns for {trade_date}",
            }
        ]
    rows: list[dict[str, Any]] = []
    strategies = performance.get("strategies") or {}
    for slug in (*MODEL_SLUGS, BENCHMARK_SLUG):
        recorded = (strategies.get(slug) or {}).get("daily_return")
        if recorded is None:
            rows.append(
                {
                    "date": trade_date,
                    "strategy": slug,
                    "status": "BLOCKED_MISSING_PERFORMANCE",
                    "reason": "missing recorded daily_return",
                }
            )
            continue
        if slug == BENCHMARK_SLUG:
            reconstructed = round(float(ticker_returns.get(BENCHMARK_SYMBOL, 0.0)), 10)
            missing_prices: list[str] = [] if BENCHMARK_SYMBOL in ticker_returns else [BENCHMARK_SYMBOL]
            weights_sum = 1.0
        else:
            weights, error = _weights_for_strategy(dated_dir, slug)
            if error:
                rows.append(
                    {
                        "date": trade_date,
                        "strategy": slug,
                        "status": "BLOCKED_MISSING_WEIGHTS",
                        "reason": error,
                    }
                )
                continue
            reconstructed, missing_prices = _weighted_return(weights, ticker_returns)
            weights_sum = round(sum(weights.values()), 12)
        diff = round(float(reconstructed) - float(recorded), 12)
        rows.append(
            {
                "date": trade_date,
                "strategy": slug,
                "recorded_daily_return": round(float(recorded), 10),
                "reconstructed_daily_return": reconstructed,
                "difference": diff,
                "weights_sum": weights_sum,
                "missing_prices": missing_prices,
                "status": "EXACT_MATCH" if abs(diff) <= tolerance and not missing_prices else "RECONSTRUCTION_BLOCKED",
            }
        )
    return rows


def build_restatement(
    *,
    repo_root: Path,
    output_root: Path,
    price_cache_path: Path,
    start_date: str | None,
    end_date: str | None,
    tolerance: float,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    returns_by_date, price_metadata = _load_price_returns(price_cache_path)
    dirs = _dated_dirs(output_root, start_date=start_date, end_date=end_date)
    if not dirs:
        raise RuntimeError("no dated shadow_performance.json artifacts found")

    rows: list[dict[str, Any]] = []
    validation_rows: list[dict[str, Any]] = []
    previous_nav: dict[str, float] = {}
    input_records: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []

    for dated_dir in dirs:
        performance_path = dated_dir / "shadow_performance.json"
        performance = _read_json(performance_path)
        trade_date = str(performance.get("trade_date") or dated_dir.name)
        input_records.append(
            {
                "date": trade_date,
                "shadow_performance_path": str(performance_path.relative_to(repo_root)),
                "shadow_performance_sha256": sha256_file(performance_path),
            }
        )
        if performance.get("status") != "OK" or performance.get("data_status") != "OK":
            blocked.append(
                {
                    "date": trade_date,
                    "status": performance.get("status"),
                    "data_status": performance.get("data_status"),
                    "reason": "non-OK dated performance artifact",
                }
            )
            continue

        daily_validations = _validate_daily_returns(
            dated_dir=dated_dir,
            performance=performance,
            returns_by_date=returns_by_date,
            tolerance=tolerance,
        )
        validation_rows.extend(daily_validations)
        bad = [row for row in daily_validations if row.get("status") != "EXACT_MATCH"]
        if bad:
            blocked.extend(bad)
            continue

        strategies = performance.get("strategies") or {}
        row = {"date": trade_date}
        for slug in (*MODEL_SLUGS, BENCHMARK_SLUG):
            payload = strategies.get(slug) or {}
            daily_return = float(payload["daily_return"])
            if slug not in previous_nav:
                base = payload.get("previous_nav")
                previous_nav[slug] = float(base) if base is not None else 1.0
            nav = round(float(previous_nav[slug] * (1.0 + daily_return)), 10)
            row[slug] = nav
            previous_nav[slug] = nav
        rows.append(row)

    if blocked:
        raise RuntimeError(json.dumps({"reason": "daily return validation blocked restatement", "issues": blocked[:20]}, indent=2))

    latest = rows[-1]
    first = rows[0]
    summary = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "owner_decision": OWNER_DECISION,
        "methodology": "canonical_operational_shadow_observation",
        "return_convention": RETURN_CONVENTION,
        "legacy_shadow_nav_series_status": "SUPERSEDED_BY_OWNER_DECISION",
        "observation_start_date": first["date"],
        "trade_date": latest["date"],
        "rows": len(rows),
        "data": price_metadata,
        "strategies": {},
    }
    for slug in (*MODEL_SLUGS, BENCHMARK_SLUG):
        start_nav = float(first[slug])
        end_nav = float(latest[slug])
        cumulative = (end_nav / start_nav) - 1.0 if start_nav else None
        summary["strategies"][slug] = {
            "strategy_name": MODEL_NAMES[slug],
            "summary": {
                "cumulative_return": round(float(cumulative), 10) if cumulative is not None else None,
                "valid_observation_days": len(rows),
                "start_nav": start_nav,
                "end_nav": end_nav,
                "return_convention": RETURN_CONVENTION,
            },
        }

    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "owner_decision": OWNER_DECISION,
        "repo_head": _git_head(repo_root),
        "methodology": "canonical_operational_shadow_observation",
        "return_convention": RETURN_CONVENTION,
        "source": "dated shadow_performance.json daily_return validated against dated strategy weights and PIT price panel",
        "observation_start_date": first["date"],
        "observation_end_date": latest["date"],
        "row_count": len(rows),
        "price_input": price_metadata,
        "input_records": input_records,
        "daily_return_validation_count": len(validation_rows),
        "daily_return_validation_status": "PASS",
        "preexisting_active_artifacts": {
            "shadow_nav_series.csv": sha256_file(output_root / "performance" / "shadow_nav_series.csv"),
            "shadow_summary.json": sha256_file(output_root / "performance" / "shadow_summary.json"),
        },
        "replacement": {
            "active_artifacts_replaced": False,
            "backup_dir": None,
        },
    }
    return rows, summary, manifest, validation_rows


def _git_head(repo_root: Path) -> str | None:
    git_head = repo_root / ".git" / "HEAD"
    if not git_head.exists():
        return None
    head_text = git_head.read_text(encoding="utf-8").strip()
    if not head_text.startswith("ref: "):
        return head_text
    ref_path = repo_root / ".git" / head_text.split(" ", 1)[1]
    return ref_path.read_text(encoding="utf-8").strip() if ref_path.exists() else None


def write_staging(
    *,
    staging_dir: Path,
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
    manifest: dict[str, Any],
    validation_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    performance_dir = staging_dir / "performance"
    nav_path = performance_dir / "shadow_nav_series.csv"
    summary_path = performance_dir / "shadow_summary.json"
    manifest_path = staging_dir / "recovery_manifest.json"
    validation_path = staging_dir / "daily_return_validation.json"
    _write_csv(nav_path, rows)
    _write_json(summary_path, summary)
    _write_json(manifest_path, manifest)
    _write_json(validation_path, {"status": "PASS", "rows": validation_rows})
    return {
        "staging_dir": str(staging_dir),
        "nav_path": str(nav_path),
        "nav_sha256": sha256_file(nav_path),
        "summary_path": str(summary_path),
        "summary_sha256": sha256_file(summary_path),
        "manifest_path": str(manifest_path),
        "manifest_sha256": sha256_file(manifest_path),
        "validation_path": str(validation_path),
        "validation_sha256": sha256_file(validation_path),
    }


def _copy_backup(source: Path, backup_dir: Path, *, root: Path) -> dict[str, Any]:
    destination = backup_dir / source.relative_to(root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.exists():
        shutil.copy2(source, destination)
    return {
        "original_path": str(source.relative_to(root)),
        "backup_path": str(destination.relative_to(root)),
        "sha256": sha256_file(source),
        "bytes": source.stat().st_size if source.exists() else None,
        "mtime": dt.datetime.fromtimestamp(source.stat().st_mtime, tz=dt.timezone.utc).isoformat() if source.exists() else None,
    }


def replace_active(
    *,
    repo_root: Path,
    output_root: Path,
    backup_root: Path,
    staging_info: dict[str, Any],
    manifest: dict[str, Any],
    expected_existing_nav_sha256: str | None,
    expected_existing_summary_sha256: str | None,
) -> dict[str, Any]:
    active_nav = output_root / "performance" / "shadow_nav_series.csv"
    active_summary = output_root / "performance" / "shadow_summary.json"
    active_manifest = output_root / "performance" / "shadow_nav_restatement_manifest.json"

    active_nav_sha = sha256_file(active_nav)
    active_summary_sha = sha256_file(active_summary)
    if expected_existing_nav_sha256 and active_nav_sha != expected_existing_nav_sha256:
        raise RuntimeError(f"active NAV hash mismatch: expected {expected_existing_nav_sha256}, got {active_nav_sha}")
    if expected_existing_summary_sha256 and active_summary_sha != expected_existing_summary_sha256:
        raise RuntimeError(f"active summary hash mismatch: expected {expected_existing_summary_sha256}, got {active_summary_sha}")

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = backup_root / f"shadow_nav_same_day_restatement_{stamp}"
    backup_records = [
        _copy_backup(active_nav, backup_dir, root=repo_root),
        _copy_backup(active_summary, backup_dir, root=repo_root),
    ]
    backup_manifest = {
        "schema_version": "shadow_operational_same_day_pre_replacement_backup_v1",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "reason": OWNER_DECISION,
        "records": backup_records,
    }
    _write_json(backup_dir / "backup_manifest.json", backup_manifest)

    staged_nav = Path(staging_info["nav_path"])
    staged_summary = Path(staging_info["summary_path"])
    replacement_manifest = dict(manifest)
    replacement_manifest["replacement"] = {
        "active_artifacts_replaced": True,
        "backup_dir": str(backup_dir.relative_to(repo_root)),
        "backup_manifest_sha256": sha256_file(backup_dir / "backup_manifest.json"),
        "active_nav_before_sha256": active_nav_sha,
        "active_summary_before_sha256": active_summary_sha,
        "active_nav_after_sha256": staging_info["nav_sha256"],
        "active_summary_after_sha256": staging_info["summary_sha256"],
    }

    _atomic_copy_file(staged_nav, active_nav)
    _atomic_copy_file(staged_summary, active_summary)
    _write_json(active_manifest, replacement_manifest)

    return {
        "backup_dir": str(backup_dir),
        "backup_manifest_sha256": sha256_file(backup_dir / "backup_manifest.json"),
        "active_nav_sha256": sha256_file(active_nav),
        "active_summary_sha256": sha256_file(active_summary),
        "active_manifest_path": str(active_manifest),
        "active_manifest_sha256": sha256_file(active_manifest),
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    output_root = (repo_root / args.output_dir).resolve() if not Path(args.output_dir).is_absolute() else Path(args.output_dir)
    price_cache_path = (repo_root / args.price_cache_path).resolve() if not Path(args.price_cache_path).is_absolute() else Path(args.price_cache_path)
    backup_root = (repo_root / args.backup_root).resolve() if not Path(args.backup_root).is_absolute() else Path(args.backup_root)
    if args.staging_dir:
        staging_dir = Path(args.staging_dir)
        if not staging_dir.is_absolute():
            staging_dir = repo_root / staging_dir
    else:
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        staging_dir = repo_root / "outputs" / "recovery_staging" / f"shadow_nav_same_day_{stamp}"

    rows, summary, manifest, validation_rows = build_restatement(
        repo_root=repo_root,
        output_root=output_root,
        price_cache_path=price_cache_path,
        start_date=args.start_date,
        end_date=args.end_date,
        tolerance=float(args.tolerance),
    )
    staging_info = write_staging(
        staging_dir=staging_dir,
        rows=rows,
        summary=summary,
        manifest=manifest,
        validation_rows=validation_rows,
    )
    replacement_info = None
    if args.replace_active:
        replacement_info = replace_active(
            repo_root=repo_root,
            output_root=output_root,
            backup_root=backup_root,
            staging_info=staging_info,
            manifest=manifest,
            expected_existing_nav_sha256=args.expected_existing_nav_sha256,
            expected_existing_summary_sha256=args.expected_existing_summary_sha256,
        )

    result = {
        "status": "OK",
        "return_convention": RETURN_CONVENTION,
        "rows": len(rows),
        "observation_start_date": rows[0]["date"],
        "observation_end_date": rows[-1]["date"],
        "staging": staging_info,
        "replacement": replacement_info,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

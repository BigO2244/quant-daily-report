#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from research.flow_detection.data import ensure_price_panel, load_universe
from research.shadow_tracking import run as shadow
from scripts import refresh_shadow_scorecard_artifacts


BENCHMARK_SYMBOL = "SPY"
MODEL_SLUGS = ("caerus_polaris", "caerus_orion", "caerus_lyra", "spy_benchmark")
DEFAULT_CACHE_PATH = Path("outputs/research/flow_detection_v1/price_panel.parquet")
DEFAULT_OUTPUT_DIR = Path("outputs/shadow_candidates")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Artifact-only chronological recovery for Shadow scorecard outputs.")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--anchor-date", required=True)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--price-cache-path", default=str(DEFAULT_CACHE_PATH))
    parser.add_argument("--universe-path", default="data/universe.csv")
    parser.add_argument("--diagnostics-dir", default="outputs/diagnostics")
    parser.add_argument("--backup-root", default="outputs/recovery_backups")
    parser.add_argument("--shadow-start-date", default=None, help="Defaults to Jan 1 of the year before start-date.")
    parser.add_argument("--force-rebuild", action="store_true", help="Rebuild every trading date in the requested range.")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--artifact-only", action="store_true", help="Required safety acknowledgement.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    return parser


def _repo_git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        return "UNKNOWN"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_table(rows: list[dict[str, Any]], *, csv_path: Path, md_path: Path, title: str) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        csv_path.write_text("", encoding="utf-8")
        md_path.write_text(f"# {title}\n\nNo rows.\n", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    with md_path.open("w", encoding="utf-8") as handle:
        handle.write(f"# {title}\n\n")
        handle.write("| " + " | ".join(fieldnames) + " |\n")
        handle.write("|" + "|".join(["---"] * len(fieldnames)) + "|\n")
        for row in rows:
            handle.write("| " + " | ".join(str(row.get(name, "")) for name in fieldnames) + " |\n")


def _load_signals(*, cache_path: Path, universe_path: Path, start_date: str, end_date: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    universe = load_universe(universe_path)
    panel, panel_meta = ensure_price_panel(
        symbols=sorted(set(universe + [BENCHMARK_SYMBOL])),
        start_date=start_date,
        end_date=end_date,
        cache_path=cache_path,
        prefer_local=True,
        allow_download=False,
    )
    max_panel_date = pd.to_datetime(panel["date"], errors="coerce").max() if not panel.empty else pd.NaT
    if pd.notna(max_panel_date) and max_panel_date.strftime("%Y-%m-%d") > end_date:
        raise RuntimeError(f"look-ahead guard failed: panel max {max_panel_date.date()} > {end_date}")
    return shadow.build_alpha_lab_signal_frame(panel), panel_meta


def _trading_dates(signals: pd.DataFrame, *, start_date: str, end_date: str) -> list[str]:
    if signals.empty or "date" not in signals.columns:
        return []
    dates = pd.DatetimeIndex(pd.to_datetime(signals["date"])).sort_values().unique()
    dates = dates[(dates >= pd.Timestamp(start_date)) & (dates <= pd.Timestamp(end_date))]
    return [str(pd.Timestamp(date).date()) for date in dates]


def _nav_dates(output_root: Path) -> set[str]:
    path = output_root / "performance" / "shadow_nav_series.csv"
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as handle:
        return {str(row.get("date") or "") for row in csv.DictReader(handle)}


def _status_for_date(output_root: Path, date: str) -> dict[str, Any]:
    dated_dir = output_root / date
    evaluation = _read_json(dated_dir / "shadow_evaluation.json") or {}
    performance = _read_json(dated_dir / "shadow_performance.json") or {}
    strategies = evaluation.get("strategies") or {}
    data_status = sorted({str((payload or {}).get("data_status")) for payload in strategies.values() if payload})
    status = sorted({str((payload or {}).get("status")) for payload in strategies.values() if payload})
    return {
        "has_shadow_dir": dated_dir.exists(),
        "has_shadow_evaluation": (dated_dir / "shadow_evaluation.json").exists(),
        "has_shadow_performance": (dated_dir / "shadow_performance.json").exists(),
        "has_comparison": (dated_dir / "comparison.json").exists(),
        "current_status": ";".join(status),
        "current_data_status": ";".join(data_status),
        "current_performance_status": performance.get("status"),
    }


def _build_plan(
    *,
    output_root: Path,
    signals: pd.DataFrame,
    start_date: str,
    end_date: str,
    anchor_date: str,
    force_rebuild: bool = False,
) -> list[dict[str, Any]]:
    dates = _trading_dates(signals, start_date=start_date, end_date=end_date)
    nav_dates = _nav_dates(output_root)
    prior_anchor = anchor_date in nav_dates
    rows: list[dict[str, Any]] = []
    for date in dates:
        status = _status_for_date(output_root, date)
        in_nav = date in nav_dates
        has_price = shadow.trade_date_has_data(signals, trade_date=date)
        needs_recovery = bool(has_price and (force_rebuild or not in_nav or status.get("current_data_status") != "OK"))
        planned_action = "refresh_artifacts_and_append_nav" if needs_recovery else "skip_already_in_nav"
        if not has_price:
            planned_action = "skip_missing_price_data"
        rows.append(
            {
                "date": date,
                "trading_day_expected": True,
                **status,
                "prior_anchor_available": prior_anchor,
                "included_in_nav_series": in_nav,
                "needs_recovery": needs_recovery,
                "planned_action": planned_action,
            }
        )
        if in_nav or needs_recovery:
            prior_anchor = True
    return rows


def _affected_dates(plan_rows: list[dict[str, Any]]) -> list[str]:
    return [str(row["date"]) for row in plan_rows if str(row.get("planned_action")) == "refresh_artifacts_and_append_nav"]


def _copy_for_backup(source: Path, backup_dir: Path, *, root: Path) -> list[dict[str, str]]:
    source = source.resolve()
    root = root.resolve()
    if not source.exists():
        return []
    records: list[dict[str, str]] = []
    if source.is_dir():
        for path in sorted(p for p in source.rglob("*") if p.is_file()):
            relative = path.relative_to(root)
            dest = backup_dir / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
            records.append({"path": str(relative), "sha256": _sha256(path)})
    else:
        relative = source.relative_to(root)
        dest = backup_dir / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        records.append({"path": str(relative), "sha256": _sha256(source)})
    return records


def _create_backup(
    *,
    backup_root: Path,
    run_id: str,
    output_root: Path,
    affected_dates: list[str],
    cache_path: Path,
    price_cache_max_date: str | None,
    anchor_date: str,
) -> tuple[Path, dict[str, Any]]:
    backup_dir = backup_root / run_id
    backup_dir.mkdir(parents=True, exist_ok=False)
    file_records: list[dict[str, str]] = []
    repo_root = Path(".").resolve()
    for date in affected_dates:
        file_records.extend(_copy_for_backup(output_root / date, backup_dir, root=repo_root))
    file_records.extend(_copy_for_backup(output_root / "latest", backup_dir, root=repo_root))
    file_records.extend(_copy_for_backup(output_root / "performance" / "shadow_nav_series.csv", backup_dir, root=repo_root))
    file_records.extend(_copy_for_backup(output_root / "performance" / "shadow_summary.json", backup_dir, root=repo_root))
    manifest = {
        "recovery_run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": _repo_git_sha(),
        "affected_dates": affected_dates,
        "files_backed_up": [record["path"] for record in file_records],
        "original_file_hashes": {record["path"]: record["sha256"] for record in file_records},
        "reason_for_recovery": "Recover shadow NAV continuity after price hydration outage.",
        "canonical_price_cache_path": str(cache_path),
        "price_cache_max_date": price_cache_max_date,
        "last_valid_anchor_date": anchor_date,
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return backup_dir, manifest


def _latest_nav_date(output_root: Path) -> str | None:
    dates = sorted(_nav_dates(output_root))
    return dates[-1] if dates else None


def _result_row(output_root: Path, date: str, before: dict[str, Any], action: str, status: str, warning: str = "") -> dict[str, Any]:
    performance = _read_json(output_root / date / "shadow_performance.json") or {}
    evaluation = _read_json(output_root / date / "shadow_evaluation.json") or {}
    strategies = evaluation.get("strategies") or {}
    data_statuses = sorted({str((payload or {}).get("data_status")) for payload in strategies.values() if payload})
    max_date_used = date
    return {
        "date": date,
        "action": action,
        "status_before": before.get("current_status") or "",
        "status_after": ";".join(sorted({str((payload or {}).get("status")) for payload in strategies.values() if payload})),
        "performance_status_after": performance.get("status") or "",
        "data_status_after": ";".join(data_statuses),
        "prior_date_used": performance.get("previous_trade_date") or "",
        "price_max_date_used": max_date_used,
        "included_in_nav_series": date in _nav_dates(output_root),
        "files_written": ";".join(
            name
            for name in (
                "caerus_polaris.json",
                "caerus_orion.json",
                "caerus_lyra.json",
                "delta.json",
                "summary.json",
                "comparison.json",
                "shadow_performance.json",
                "shadow_evaluation.json",
                "comparison.md",
            )
            if (output_root / date / name).exists()
        ),
        "result_status": status,
        "warnings": warning,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.artifact_only:
        raise SystemExit("--artifact-only is required; this command must never be used as a trading workflow.")

    start_date = pd.Timestamp(args.start_date).strftime("%Y-%m-%d")
    end_date = pd.Timestamp(args.end_date).strftime("%Y-%m-%d")
    anchor_date = pd.Timestamp(args.anchor_date).strftime("%Y-%m-%d")
    run_id = args.run_id or datetime.now(timezone.utc).strftime("shadow-backfill-%Y%m%dT%H%M%SZ")
    shadow_start_date = args.shadow_start_date or f"{int(start_date[:4]) - 1}-01-01"
    output_root = Path(args.output_dir)
    cache_path = Path(args.price_cache_path)
    diagnostics_dir = Path(args.diagnostics_dir)

    signals, panel_meta = _load_signals(
        cache_path=cache_path,
        universe_path=Path(args.universe_path),
        start_date=shadow_start_date,
        end_date=end_date,
    )
    price_cache_max_date = ((panel_meta.get("coverage") or {}).get("end_date"))
    plan_rows = _build_plan(
        output_root=output_root,
        signals=signals,
        start_date=start_date,
        end_date=end_date,
        anchor_date=anchor_date,
        force_rebuild=bool(args.force_rebuild),
    )
    plan_csv = diagnostics_dir / f"shadow_backfill_plan_{datetime.now().date().isoformat()}.csv"
    plan_md = diagnostics_dir / f"shadow_backfill_plan_{datetime.now().date().isoformat()}.md"
    _write_table(plan_rows, csv_path=plan_csv, md_path=plan_md, title="Shadow Backfill Plan")

    dry_run_md = diagnostics_dir / f"shadow_backfill_dry_run_{datetime.now().date().isoformat()}.md"
    affected = _affected_dates(plan_rows)
    dry_run_md.write_text(
        "\n".join(
            [
                "# Shadow Backfill Dry Run",
                "",
                f"- Run ID: `{run_id}`",
                f"- Anchor date: `{anchor_date}`",
                f"- Start date: `{start_date}`",
                f"- End date: `{end_date}`",
                f"- Price cache: `{cache_path}`",
                f"- Price cache max date: `{price_cache_max_date}`",
                f"- Affected dates: {', '.join(affected) or 'None'}",
                f"- Plan CSV: `{plan_csv}`",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps({"run_id": run_id, "dry_run": bool(args.dry_run), "affected_dates": affected, "plan_csv": str(plan_csv)}, indent=2))
    if args.dry_run:
        return 0

    if args.strict and anchor_date not in _nav_dates(output_root):
        raise SystemExit(f"anchor date {anchor_date} is not present in shadow_nav_series.csv")

    backup_dir, manifest = _create_backup(
        backup_root=Path(args.backup_root),
        run_id=run_id,
        output_root=output_root,
        affected_dates=affected,
        cache_path=cache_path,
        price_cache_max_date=price_cache_max_date,
        anchor_date=anchor_date,
    )

    result_rows: list[dict[str, Any]] = []
    exit_code = 0
    for row in plan_rows:
        date = str(row["date"])
        action = str(row["planned_action"])
        before = dict(row)
        if action != "refresh_artifacts_and_append_nav":
            result_rows.append(_result_row(output_root, date, before, action, "SKIPPED"))
            continue
        prior_nav_date = _latest_nav_date(output_root)
        if args.strict and prior_nav_date is None:
            raise SystemExit(f"no prior NAV anchor available before {date}")
        try:
            rc = refresh_shadow_scorecard_artifacts.main(
                [
                    "--trade-date",
                    date,
                    "--start-date",
                    shadow_start_date,
                    "--output-dir",
                    str(output_root),
                    "--price-cache-path",
                    str(cache_path),
                ]
            )
        except Exception as exc:
            rc = 1
            warning = str(exc)
        else:
            warning = ""
        performance = _read_json(output_root / date / "shadow_performance.json") or {}
        if rc == 0 and performance.get("status") == "OK" and date in _nav_dates(output_root):
            result_rows.append(_result_row(output_root, date, before, action, "OK", warning))
            continue
        exit_code = 1
        reason = warning or f"rc={rc}; performance_status={performance.get('status')}; included_in_nav={date in _nav_dates(output_root)}"
        result_rows.append(_result_row(output_root, date, before, action, "FAILED", reason))
        if args.strict and not args.continue_on_error:
            break

    result_csv = diagnostics_dir / f"shadow_backfill_result_{datetime.now().date().isoformat()}.csv"
    result_md = diagnostics_dir / f"shadow_backfill_result_{datetime.now().date().isoformat()}.md"
    _write_table(result_rows, csv_path=result_csv, md_path=result_md, title="Shadow Backfill Result")
    final_manifest = dict(manifest)
    final_manifest.update(
        {
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "result_csv": str(result_csv),
            "result_md": str(result_md),
            "final_nav_latest_date": _latest_nav_date(output_root),
            "exit_code": exit_code,
        }
    )
    (backup_dir / "manifest.json").write_text(json.dumps(final_manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"backup_dir": str(backup_dir), "result_csv": str(result_csv), "exit_code": exit_code}, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

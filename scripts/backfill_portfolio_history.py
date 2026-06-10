#!/usr/bin/env python3
"""FR-066 — one-time canonical NAV inception backfill.

Reconstructs the full daily broker equity series for the live paper book from
inception (2026-03-03) to the requested end date using the Alpaca
``GET /v2/account/portfolio/history`` endpoint, reconciles overlapping dates
against the existing canonical ``nav.csv`` and every persisted broker snapshot
(1 bp tolerance), flags any single-day return greater than 5% for operator
review, and writes a manifest documenting source, request window, row counts,
and reconciliation results.

Governance
----------
- Governance label: OPERATIONAL_TELEMETRY. Execution impact: NON_EXECUTIONAL.
- This script performs **read-only** broker access (a portfolio-history GET).
  It never submits orders, never touches execution/allocation/strategy code.
- DRY-RUN IS THE DEFAULT. Nothing on disk is written unless ``--write`` is
  passed. Run the dry run and review its reconciliation output before any write.
- The backfill is a one-time operation. Once a write-mode manifest exists the
  script refuses to run again unless ``--force`` is given (FR-066 §2: "never
  re-run automatically").
- Discrepancies are recorded, never silently overwritten. In write mode the
  prior ``nav.csv`` is backed up and every changed historical row is logged to
  ``restatements.json`` (FR-066 §5 restatement rule).

Usage
-----
    # dry run (default) — prints the reconciliation manifest, writes nothing
    python3 scripts/backfill_portfolio_history.py --env-file .env

    # write mode (only after reviewing the dry run)
    python3 scripts/backfill_portfolio_history.py --env-file .env --write
"""
from __future__ import annotations

import argparse
import csv
import json
import ssl
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from brokers.alpaca_broker import load_alpaca_env  # noqa: E402
from scripts.build_portfolio_history import NAV_FIELDS, _iso_date  # noqa: E402

try:  # canonical env loader; keep import-safe for tests
    from scripts.export_alpaca_broker_snapshot import load_env_file
except Exception:  # pragma: no cover - defensive
    load_env_file = None  # type: ignore[assignment]

try:
    from paper.trading_calendar import is_trading_day, next_trading_day
except Exception:  # pragma: no cover - defensive
    is_trading_day = None  # type: ignore[assignment]
    next_trading_day = None  # type: ignore[assignment]

SCHEMA_VERSION = "caerus_portfolio_history_backfill_v1"
INCEPTION_DATE = "2026-03-03"
RECON_TOLERANCE_BPS = 1.0  # FR-066 §2: 1 bp of equity
RECON_TOLERANCE_REL = RECON_TOLERANCE_BPS / 10_000.0
LARGE_MOVE_THRESHOLD = 0.05  # FR-066 risks: flag |return_1d| > 5%

BACKFILL_SOURCE = "alpaca_portfolio_history_backfill"


# --------------------------------------------------------------------------- #
# Broker access (read-only)
# --------------------------------------------------------------------------- #
def _rest_json(url: str, *, headers: dict[str, str], timeout: int = 30) -> Any:
    request = urllib.request.Request(url, headers=headers)
    context = ssl.create_default_context()
    with urllib.request.urlopen(request, context=context, timeout=timeout) as response:
        body = response.read().decode("utf-8", errors="replace")
    return json.loads(body)


def fetch_portfolio_history(*, date_end: str, inception: str = INCEPTION_DATE) -> dict[str, Any]:
    """Pull the daily portfolio-history series covering ``inception`` .. ``date_end``.

    Alpaca's ``period`` is relative to ``date_end``; we request a window wide
    enough to cover inception and fall back to progressively longer windows so
    the early months are recovered if the paper endpoint still serves them.
    """
    cfg = load_alpaca_env()
    headers = {
        "APCA-API-KEY-ID": cfg.key_id,
        "APCA-API-SECRET-KEY": cfg.secret_key,
    }
    span_days = _calendar_day_span(inception, date_end)
    period_candidates = [f"{max(span_days + 5, 5)}D", "12M", "1A", "all"]
    last_error: Exception | None = None
    for period in period_candidates:
        params = {
            "date_end": date_end,
            "period": period,
            "timeframe": "1D",
            "intraday_reporting": "market_hours",
            "pnl_reset": "no_reset",
            "extended_hours": "false",
        }
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
        url = f"{cfg.base_url.rstrip('/')}/v2/account/portfolio/history?{query}"
        try:
            payload = _rest_json(url, headers=headers)
        except Exception as exc:  # try the next, wider period
            last_error = exc
            continue
        if isinstance(payload, dict) and payload.get("timestamp"):
            payload.setdefault("_request_period", period)
            return payload
    if last_error is not None:
        raise last_error
    return {}


def _calendar_day_span(start: str, end: str) -> int:
    try:
        s = datetime.fromisoformat(start[:10]).date()
        e = datetime.fromisoformat(end[:10]).date()
    except Exception:
        return 400
    return max((e - s).days, 1)


# --------------------------------------------------------------------------- #
# Pure reconstruction / reconciliation helpers (network-free; unit-tested)
# --------------------------------------------------------------------------- #
def _history_date(timestamp: Any) -> str | None:
    try:
        ts = int(float(timestamp))
    except Exception:
        return None
    # Alpaca 1D timestamps are session dates; UTC date is stable for daily bars.
    return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()


def reconstruct_nav_series(
    portfolio_history: dict[str, Any], *, inception: str = INCEPTION_DATE
) -> list[dict[str, Any]]:
    """Turn the Alpaca portfolio-history payload into sorted daily NAV rows."""
    timestamps = portfolio_history.get("timestamp")
    equities = portfolio_history.get("equity")
    if not isinstance(timestamps, list) or not isinstance(equities, list):
        return []

    rows_by_date: dict[str, float] = {}
    for idx, raw_ts in enumerate(timestamps):
        if idx >= len(equities):
            break
        date_text = _history_date(raw_ts)
        equity = _to_float(equities[idx])
        if not date_text or equity is None or equity <= 0.0:
            continue
        if date_text < inception:
            continue
        rows_by_date[date_text] = equity

    ordered_dates = sorted(rows_by_date)
    first_equity = rows_by_date[ordered_dates[0]] if ordered_dates else None
    rows: list[dict[str, Any]] = []
    prev_equity: float | None = None
    for date_text in ordered_dates:
        equity = rows_by_date[date_text]
        return_1d = ((equity / prev_equity) - 1.0) if prev_equity not in (None, 0) else None
        cumulative = ((equity / first_equity) - 1.0) if first_equity not in (None, 0) else None
        rows.append(
            {
                "date": date_text,
                "equity": equity,
                "cash": None,
                "gross_exposure": None,
                "net_exposure": None,
                "return_1d": return_1d,
                "turnover_dollars": None,
                "turnover_pct": None,
                "cumulative_return": cumulative,
                "source": BACKFILL_SOURCE,
            }
        )
        prev_equity = equity
    return rows


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def read_existing_nav(path: Path) -> dict[str, float]:
    """date -> equity from the existing canonical nav.csv (empty if absent)."""
    out: dict[str, float] = {}
    if not path.exists() or path.stat().st_size <= 0:
        return out
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            date = _iso_date(row.get("date"))
            equity = _to_float(row.get("equity"))
            if date and equity is not None:
                out[date] = equity
    return out


def read_broker_snapshot_equity(repo_root: Path) -> dict[str, float]:
    """date -> account equity across all persisted broker snapshots."""
    out: dict[str, float] = {}
    snap_dir = repo_root / "outputs" / "broker_snapshot"
    for path in sorted(snap_dir.glob("broker_snapshot_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        date = _iso_date(meta.get("report_date")) or _iso_date(path.name)
        account = payload.get("account") if isinstance(payload.get("account"), dict) else {}
        equity = _to_float(account.get("equity") or account.get("portfolio_value"))
        if date and equity is not None:
            out[date] = equity
    return out


def reconcile(
    series: list[dict[str, Any]],
    reference: dict[str, float],
    *,
    label: str,
    tolerance_rel: float = RECON_TOLERANCE_REL,
) -> dict[str, Any]:
    """Compare the reconstructed series to a reference date->equity map."""
    fetched = {row["date"]: float(row["equity"]) for row in series if row.get("equity") is not None}
    overlap = sorted(set(fetched) & set(reference))
    discrepancies: list[dict[str, Any]] = []
    for date in overlap:
        ref = reference[date]
        got = fetched[date]
        rel = abs(got - ref) / ref if ref else None
        if rel is None or rel > tolerance_rel:
            discrepancies.append(
                {
                    "date": date,
                    "fetched_equity": round(got, 4),
                    "reference_equity": round(ref, 4),
                    "abs_diff": round(got - ref, 4),
                    "rel_diff_bps": round(rel * 10_000.0, 3) if rel is not None else None,
                }
            )
    reference_only = sorted(set(reference) - set(fetched))
    return {
        "label": label,
        "reference_rows": len(reference),
        "compared": len(overlap),
        "matches": len(overlap) - len(discrepancies),
        "discrepancies": discrepancies,
        "reference_dates_missing_from_backfill": reference_only,
    }


def flag_large_moves(series: list[dict[str, Any]], *, threshold: float = LARGE_MOVE_THRESHOLD) -> list[dict[str, Any]]:
    flags: list[dict[str, Any]] = []
    for row in series:
        ret = row.get("return_1d")
        if ret is not None and abs(float(ret)) > threshold:
            flags.append({"date": row["date"], "return_1d": round(float(ret), 6)})
    return flags


def detect_coverage_gaps(series: list[dict[str, Any]], *, inception: str = INCEPTION_DATE) -> dict[str, Any]:
    """Compare recovered dates against the trading calendar from inception."""
    dates = sorted(row["date"] for row in series)
    earliest = dates[0] if dates else None
    latest = dates[-1] if dates else None
    coverage = {
        "requested_inception": inception,
        "earliest_recovered": earliest,
        "latest_recovered": latest,
        "row_count": len(dates),
        "gap_vs_inception": bool(earliest and earliest > inception),
        "missing_trading_days": [],
    }
    if not dates or is_trading_day is None or next_trading_day is None:
        return coverage
    present = set(dates)
    missing: list[str] = []
    cursor = earliest
    while cursor and cursor < latest:
        if is_trading_day(cursor) and cursor not in present:
            missing.append(cursor)
        cursor = next_trading_day(cursor)
    coverage["missing_trading_days"] = missing
    return coverage


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def build_manifest(
    *,
    series: list[dict[str, Any]],
    nav_recon: dict[str, Any],
    snapshot_recon: dict[str, Any],
    large_moves: list[dict[str, Any]],
    coverage: dict[str, Any],
    request_meta: dict[str, Any],
    mode: str,
    generated_at: str,
    warnings: list[str],
) -> dict[str, Any]:
    total_discrepancies = len(nav_recon["discrepancies"]) + len(snapshot_recon["discrepancies"])
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "governance_label": "OPERATIONAL_TELEMETRY",
        "execution_impact": "NON_EXECUTIONAL",
        "mode": mode,
        "source": "alpaca_portfolio_history",
        "request": request_meta,
        "coverage": coverage,
        "row_counts": {
            "reconstructed_nav_rows": len(series),
            "nav_csv_reference_rows": nav_recon["reference_rows"],
            "broker_snapshot_reference_rows": snapshot_recon["reference_rows"],
        },
        "reconciliation": {
            "tolerance_bps": RECON_TOLERANCE_BPS,
            "nav_csv": nav_recon,
            "broker_snapshots": snapshot_recon,
            "total_discrepancies": total_discrepancies,
            "reconciled_clean": total_discrepancies == 0,
        },
        "large_move_flags": {
            "threshold_pct": LARGE_MOVE_THRESHOLD * 100.0,
            "count": len(large_moves),
            "flags": large_moves,
        },
        "warnings": warnings,
    }


def run_backfill(
    *,
    repo_root: Path | str = ".",
    date_end: str | None = None,
    inception: str = INCEPTION_DATE,
    write: bool = False,
    force: bool = False,
    fetch_fn: Callable[..., dict[str, Any]] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Run the backfill. Returns the manifest dict. Writes only when ``write``."""
    root = Path(repo_root).resolve()
    out_dir = root / "outputs" / "portfolio_history"
    nav_path = out_dir / "nav.csv"
    manifest_path = out_dir / "backfill_manifest.json"
    date_end = _iso_date(date_end) or datetime.now(tz=timezone.utc).date().isoformat()
    generated_at = generated_at or datetime.now(tz=timezone.utc).isoformat()
    warnings: list[str] = []

    if write and not force and manifest_path.exists():
        prior = _read_json(manifest_path)
        if isinstance(prior, dict) and prior.get("mode") == "write":
            raise SystemExit(
                f"[BACKFILL][REFUSED] A write-mode manifest already exists at {manifest_path}. "
                "The inception backfill is a one-time operation (FR-066 §2). "
                "Pass --force only with explicit operator intent."
            )

    fetch = fetch_fn or fetch_portfolio_history
    portfolio_history = fetch(date_end=date_end, inception=inception)
    series = reconstruct_nav_series(portfolio_history, inception=inception)
    if not series:
        warnings.append("Alpaca portfolio history returned no usable equity series for the requested window.")

    nav_recon = reconcile(series, read_existing_nav(nav_path), label="nav.csv")
    snapshot_recon = reconcile(series, read_broker_snapshot_equity(root), label="broker_snapshots")
    large_moves = flag_large_moves(series)
    coverage = detect_coverage_gaps(series, inception=inception)
    if coverage["gap_vs_inception"]:
        warnings.append(
            f"Earliest recovered date {coverage['earliest_recovered']} is later than inception "
            f"{inception}; canonical series begins at earliest recoverable date (FR-066 §Risks)."
        )
    if coverage.get("missing_trading_days"):
        warnings.append(
            f"{len(coverage['missing_trading_days'])} trading day(s) inside the recovered window "
            "are missing from the broker series."
        )
    if is_trading_day is None:
        warnings.append("paper.trading_calendar unavailable; trading-day gap detection skipped.")

    request_meta = {
        "endpoint": "/v2/account/portfolio/history",
        "timeframe": "1D",
        "inception": inception,
        "date_end": date_end,
        "period": portfolio_history.get("_request_period"),
    }
    manifest = build_manifest(
        series=series,
        nav_recon=nav_recon,
        snapshot_recon=snapshot_recon,
        large_moves=large_moves,
        coverage=coverage,
        request_meta=request_meta,
        mode="write" if write else "dry_run",
        generated_at=generated_at,
        warnings=warnings,
    )

    if write:
        _apply_write(
            out_dir=out_dir,
            nav_path=nav_path,
            manifest_path=manifest_path,
            series=series,
            existing=read_existing_nav(nav_path),
            generated_at=generated_at,
            manifest=manifest,
        )
    else:
        manifest["dry_run_note"] = (
            "No files written. Re-run with --write after reviewing reconciliation above."
        )
    return manifest


def _apply_write(
    *,
    out_dir: Path,
    nav_path: Path,
    manifest_path: Path,
    series: list[dict[str, Any]],
    existing: dict[str, float],
    generated_at: str,
    manifest: dict[str, Any],
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    # Preserve the prior canonical artifact before any restatement.
    if nav_path.exists():
        backup = nav_path.with_suffix(".csv.pre_backfill.bak")
        if not backup.exists():
            backup.write_text(nav_path.read_text(encoding="utf-8"), encoding="utf-8")
        manifest.setdefault("write_artifacts", {})["nav_backup"] = str(backup.relative_to(out_dir.parents[1]))

    # Log every changed historical row as a restatement (FR-066 §5).
    restatements_path = out_dir / "restatements.json"
    restatements = _read_json(restatements_path)
    if not isinstance(restatements, list):
        restatements = []
    fetched = {row["date"]: float(row["equity"]) for row in series if row.get("equity") is not None}
    for date in sorted(set(existing) & set(fetched)):
        old, new = existing[date], fetched[date]
        if old and abs(new - old) / old > RECON_TOLERANCE_REL:
            restatements.append(
                {
                    "date": date,
                    "field": "equity",
                    "old_value": round(old, 4),
                    "new_value": round(new, 4),
                    "reason": "FR-066 inception backfill: broker portfolio-history is authoritative.",
                    "source_artifact": "outputs/portfolio_history/backfill_manifest.json",
                    "logged_at": generated_at,
                }
            )
    if restatements:
        restatements_path.write_text(json.dumps(restatements, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifest.setdefault("write_artifacts", {})["restatements"] = "outputs/portfolio_history/restatements.json"

    # Write the broker-authoritative canonical series. Benchmark/beta columns
    # (FR-066 §3) are left blank here and populated by the daily builder.
    with nav_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=NAV_FIELDS)
        writer.writeheader()
        for row in series:
            writer.writerow({field: row.get(field) for field in NAV_FIELDS})
    manifest.setdefault("write_artifacts", {})["nav"] = "outputs/portfolio_history/nav.csv"

    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest.setdefault("write_artifacts", {})["manifest"] = "outputs/portfolio_history/backfill_manifest.json"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _print_dry_run_summary(manifest: dict[str, Any]) -> None:
    cov = manifest["coverage"]
    recon = manifest["reconciliation"]
    lm = manifest["large_move_flags"]
    print("=" * 72)
    print(f"FR-066 BACKFILL — MODE: {manifest['mode'].upper()}  (governance: NON_EXECUTIONAL)")
    print("=" * 72)
    print(f"Request window     : {manifest['request']['inception']} -> {manifest['request']['date_end']} "
          f"(period={manifest['request']['period']})")
    print(f"Rows reconstructed : {manifest['row_counts']['reconstructed_nav_rows']}")
    print(f"Coverage           : {cov['earliest_recovered']} -> {cov['latest_recovered']} "
          f"(gap vs inception: {cov['gap_vs_inception']})")
    if cov.get("missing_trading_days"):
        print(f"  Missing trading days: {cov['missing_trading_days']}")
    print("-" * 72)
    print(f"Reconcile vs nav.csv          : compared={recon['nav_csv']['compared']} "
          f"matches={recon['nav_csv']['matches']} discrepancies={len(recon['nav_csv']['discrepancies'])}")
    for d in recon["nav_csv"]["discrepancies"]:
        print(f"    ! {d['date']}: fetched={d['fetched_equity']} ref={d['reference_equity']} "
              f"({d['rel_diff_bps']} bps)")
    print(f"Reconcile vs broker snapshots : compared={recon['broker_snapshots']['compared']} "
          f"matches={recon['broker_snapshots']['matches']} "
          f"discrepancies={len(recon['broker_snapshots']['discrepancies'])}")
    for d in recon["broker_snapshots"]["discrepancies"]:
        print(f"    ! {d['date']}: fetched={d['fetched_equity']} ref={d['reference_equity']} "
              f"({d['rel_diff_bps']} bps)")
    print(f"Reconciled clean   : {recon['reconciled_clean']} (tolerance {recon['tolerance_bps']} bp)")
    print("-" * 72)
    print(f"Single-day moves > {lm['threshold_pct']}% : {lm['count']}")
    for f in lm["flags"]:
        print(f"    ! {f['date']}: return_1d={f['return_1d']:.4%}")
    if manifest.get("warnings"):
        print("-" * 72)
        print("WARNINGS:")
        for w in manifest["warnings"]:
            print(f"    - {w}")
    print("=" * 72)
    if manifest["mode"] == "dry_run":
        print("DRY RUN — nothing written. Review above, then re-run with --write.")
    else:
        print(f"WRITE COMPLETE — artifacts: {manifest.get('write_artifacts', {})}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FR-066 one-time canonical NAV inception backfill.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--env-file", default=None, help="Path to .env with Alpaca credentials.")
    parser.add_argument("--date-end", default=None, help="End date (YYYY-MM-DD); default today UTC.")
    parser.add_argument("--inception", default=INCEPTION_DATE)
    parser.add_argument("--write", action="store_true", help="Write artifacts (default: dry run).")
    parser.add_argument("--force", action="store_true", help="Override the one-time re-run guard.")
    parser.add_argument("--json", action="store_true", help="Print the full manifest as JSON.")
    args = parser.parse_args(argv)

    if args.env_file and load_env_file is not None:
        load_env_file(args.env_file)

    manifest = run_backfill(
        repo_root=args.repo_root,
        date_end=args.date_end,
        inception=args.inception,
        write=args.write,
        force=args.force,
    )
    if args.json:
        print(json.dumps(manifest, indent=2, sort_keys=True))
    else:
        _print_dry_run_summary(manifest)
    # Exit non-zero on unreconciled discrepancies so an operator notices.
    return 0 if manifest["reconciliation"]["reconciled_clean"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

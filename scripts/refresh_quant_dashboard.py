#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import logging
import ssl
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from brokers.alpaca_broker import load_alpaca_env
from scripts.export_alpaca_broker_snapshot import (
    _resolve_report_date,
    build_snapshot_payload,
    fetch_snapshot_inputs,
    load_env_file,
    write_posttrade_recon_from_snapshot,
    write_snapshot_json,
    write_supporting_broker_artifacts,
)
from scripts.research.build_dashboard_v1 import DashboardV1Builder, write_dashboard_v1_payload

logger = logging.getLogger(__name__)
EASTERN_TZ = ZoneInfo("America/New_York")
DEFAULT_HISTORY_DAYS = 120


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _parse_date(value: str | None) -> dt.date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return dt.date.fromisoformat(text[:10])
    except ValueError:
        return None


def _rest_json(url: str, *, headers: dict[str, str], timeout: int = 20) -> dict[str, Any] | list[Any]:
    request = urllib.request.Request(url, headers=headers)
    context = ssl.create_default_context()
    with urllib.request.urlopen(request, context=context, timeout=timeout) as response:
        body = response.read().decode("utf-8", errors="replace")
    payload = json.loads(body)
    if isinstance(payload, (dict, list)):
        return payload
    raise RuntimeError(f"Unexpected JSON response type for {url}: {type(payload).__name__}")


def _rest_get(
    *,
    base_url: str,
    path: str,
    headers: dict[str, str],
    params: dict[str, Any] | None = None,
) -> dict[str, Any] | list[Any]:
    query = urllib.parse.urlencode(
        {key: value for key, value in (params or {}).items() if value not in (None, "")},
        doseq=True,
    )
    url = f"{base_url.rstrip('/')}{path}"
    if query:
        url = f"{url}?{query}"
    return _rest_json(url, headers=headers)


def _resolve_period_candidates(history_days: int) -> list[str]:
    clamped = max(5, int(history_days))
    return [f"{clamped}D", "6M", "1A"]


def _fetch_portfolio_history(*, report_date: str, history_days: int) -> dict[str, Any]:
    cfg = load_alpaca_env()
    headers = {
        "APCA-API-KEY-ID": cfg.key_id,
        "APCA-API-SECRET-KEY": cfg.secret_key,
    }
    last_error: Exception | None = None
    for period in _resolve_period_candidates(history_days):
        try:
            payload = _rest_get(
                base_url=cfg.base_url,
                path="/v2/account/portfolio/history",
                headers=headers,
                params={
                    "date_end": report_date,
                    "period": period,
                    "timeframe": "1D",
                    "intraday_reporting": "market_hours",
                    "pnl_reset": "no_reset",
                    "extended_hours": "false",
                },
            )
            return payload if isinstance(payload, dict) else {}
        except Exception as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    return {}


def _history_date_from_timestamp(timestamp: Any) -> str | None:
    try:
        ts = int(float(timestamp))
    except Exception:
        return None
    return dt.datetime.fromtimestamp(ts, tz=EASTERN_TZ).date().isoformat()


def _build_live_nav_rows(
    *,
    portfolio_history: dict[str, Any],
    report_date: str,
    snapshot_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    timestamps = portfolio_history.get("timestamp")
    equities = portfolio_history.get("equity")
    if not isinstance(timestamps, list) or not isinstance(equities, list):
        return []

    rows_by_date: dict[str, dict[str, Any]] = {}
    for idx, raw_ts in enumerate(timestamps):
        if idx >= len(equities):
            break
        date_text = _history_date_from_timestamp(raw_ts)
        equity = _to_float(equities[idx])
        if not date_text or equity is None:
            continue
        rows_by_date[date_text] = {
            "date": date_text,
            "equity": equity,
            "cash": None,
            "gross_exposure": None,
            "net_exposure": None,
            "return_1d": None,
            "turnover_dollars": None,
            "turnover_pct": None,
            "turnover": None,
        }

    account = snapshot_payload.get("account") if isinstance(snapshot_payload.get("account"), dict) else {}
    report_row = rows_by_date.get(report_date) or {
        "date": report_date,
        "equity": None,
        "cash": None,
        "gross_exposure": None,
        "net_exposure": None,
        "return_1d": None,
        "turnover_dollars": None,
        "turnover_pct": None,
        "turnover": None,
    }
    report_equity = _to_float(account.get("equity") or account.get("portfolio_value"))
    report_cash = _to_float(account.get("cash"))
    if report_equity is not None:
        report_row["equity"] = report_equity
        rows_by_date[report_date] = report_row
    if report_cash is not None:
        report_row["cash"] = report_cash

    rows = [rows_by_date[key] for key in sorted(rows_by_date)]
    first_positive_index = next(
        (
            index
            for index, row in enumerate(rows)
            if row.get("equity") is not None and float(row["equity"]) > 0.0
        ),
        None,
    )
    if first_positive_index not in (None, 0):
        rows = rows[first_positive_index:]
    previous_equity: float | None = None
    for row in rows:
        equity = row.get("equity")
        if equity is not None and previous_equity not in (None, 0):
            row["return_1d"] = (float(equity) / float(previous_equity)) - 1.0
        previous_equity = equity if equity is not None else previous_equity
    return rows


def _write_nav_series_csv(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "date",
        "equity",
        "cash",
        "gross_exposure",
        "net_exposure",
        "return_1d",
        "turnover_dollars",
        "turnover_pct",
        "turnover",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})
    return path


def _fetch_yahoo_benchmark_rows(
    *,
    symbol: str,
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    start_day = _parse_date(start_date)
    end_day = _parse_date(end_date)
    if start_day is None or end_day is None:
        return []
    period1 = int(dt.datetime.combine(start_day - dt.timedelta(days=5), dt.time.min, tzinfo=dt.timezone.utc).timestamp())
    period2 = int(dt.datetime.combine(end_day + dt.timedelta(days=2), dt.time.min, tzinfo=dt.timezone.utc).timestamp())
    query = urllib.parse.urlencode(
        {
            "period1": period1,
            "period2": period2,
            "interval": "1d",
            "includeAdjustedClose": "true",
            "events": "div,splits",
        }
    )
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?{query}"
    payload = _rest_json(url, headers={"User-Agent": "Mozilla/5.0"})
    result = (
        payload.get("chart", {}).get("result", [None])[0]
        if isinstance(payload, dict)
        else None
    )
    if not isinstance(result, dict):
        return []
    timestamps = result.get("timestamp")
    quotes = result.get("indicators", {}).get("quote", [None])[0]
    closes = quotes.get("close") if isinstance(quotes, dict) else None
    if not isinstance(timestamps, list) or not isinstance(closes, list):
        return []

    rows_by_date: dict[str, dict[str, Any]] = {}
    previous_close: float | None = None
    for idx, raw_ts in enumerate(timestamps):
        if idx >= len(closes):
            break
        close = _to_float(closes[idx])
        if close is None:
            continue
        date_text = _history_date_from_timestamp(raw_ts)
        if date_text is None or date_text < start_date or date_text > end_date:
            continue
        spy_return = ((close / previous_close) - 1.0) if previous_close not in (None, 0) else None
        rows_by_date[date_text] = {
            "date": date_text,
            "spy_close": close,
            "spy_return": spy_return,
        }
        previous_close = close
    return [rows_by_date[key] for key in sorted(rows_by_date)]


def _write_benchmark_series_csv(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "spy_close", "spy_return"])
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "date": row.get("date"),
                    "spy_close": row.get("spy_close"),
                    "spy_return": row.get("spy_return"),
                }
            )
    return path


def refresh_live_performance_artifacts(
    *,
    repo_root: Path,
    report_date: str,
    snapshot_payload: dict[str, Any],
    history_days: int,
) -> dict[str, str]:
    perf_dir = repo_root / "outputs" / "perf"
    portfolio_history = _fetch_portfolio_history(report_date=report_date, history_days=history_days)
    nav_rows = _build_live_nav_rows(
        portfolio_history=portfolio_history,
        report_date=report_date,
        snapshot_payload=snapshot_payload,
    )
    if not nav_rows:
        raise RuntimeError("Alpaca portfolio history returned no usable equity series")
    nav_path = _write_nav_series_csv(perf_dir / "live_overlay_nav_series.csv", nav_rows)

    benchmark_rows: list[dict[str, Any]] = []
    try:
        benchmark_rows = _fetch_yahoo_benchmark_rows(
            symbol="SPY",
            start_date=nav_rows[0]["date"],
            end_date=nav_rows[-1]["date"],
        )
    except Exception as exc:
        logger.warning("[DASHBOARD_REFRESH] benchmark fetch skipped: %s", exc)
    benchmark_path = _write_benchmark_series_csv(
        perf_dir / "live_overlay_benchmark_close_history.csv",
        benchmark_rows,
    )

    return {
        "portfolio_history_points": str(len(nav_rows)),
        "portfolio_history_path": str(nav_path),
        "benchmark_history_points": str(len(benchmark_rows)),
        "benchmark_history_path": str(benchmark_path),
    }


def _classify_broker_failure(exc: Exception) -> str:
    """FR-059: map a live-broker exception to a telemetry reason code (no secrets)."""
    text = str(exc).lower()
    if any(token in text for token in ("unauthorized", "401", "forbidden", "403")):
        return "alpaca_auth_failed"
    return "live_broker_refresh_failed"


def _latest_date_in_csv(path: Path) -> str | None:
    if not path.exists():
        return None
    latest: str | None = None
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                parsed = _parse_date(row.get("date") or row.get("as_of_date"))
                if parsed is None:
                    continue
                iso = parsed.isoformat()
                if latest is None or iso > latest:
                    latest = iso
    except Exception:
        return None
    return latest


def _latest_dated_artifact(directory: Path, prefix: str, suffix: str) -> str | None:
    if not directory.exists():
        return None
    latest: str | None = None
    for path in directory.glob(f"{prefix}*{suffix}"):
        stem = path.name[len(prefix):]
        if suffix:
            stem = stem[: -len(suffix)]
        parsed = _parse_date(stem)
        if parsed is None:
            continue
        iso = parsed.isoformat()
        if latest is None or iso > latest:
            latest = iso
    return latest


def _is_stale(latest_date: str | None, report_date: str, max_age_days: int) -> bool:
    if latest_date is None:
        return True
    latest = _parse_date(latest_date)
    report = _parse_date(report_date)
    if latest is None or report is None:
        return True
    return (report - latest).days > max_age_days


def evaluate_live_telemetry_staleness(
    *, repo_root: Path, report_date: str, max_age_days: int = 4
) -> dict[str, Any]:
    """FR-059: deterministic freshness check of live broker telemetry artifacts.

    Emits nav_artifact_stale / broker_snapshot_stale / recon_artifact_stale when
    the latest artifact date trails the report date beyond the calendar
    tolerance (or the artifact is missing). Pure telemetry; never fabricates.
    """
    nav_date = _latest_date_in_csv(repo_root / "outputs" / "perf" / "live_overlay_nav_series.csv")
    snapshot_date = _latest_dated_artifact(repo_root / "outputs" / "broker_snapshot", "broker_snapshot_", ".json")
    recon_date = _latest_dated_artifact(repo_root / "outputs" / "broker", "recon_posttrade_", ".json")
    reason_codes: list[str] = []
    if _is_stale(nav_date, report_date, max_age_days):
        reason_codes.append("nav_artifact_stale")
    if _is_stale(snapshot_date, report_date, max_age_days):
        reason_codes.append("broker_snapshot_stale")
    if _is_stale(recon_date, report_date, max_age_days):
        reason_codes.append("recon_artifact_stale")
    return {
        "report_date": report_date,
        "max_age_days": max_age_days,
        "nav_latest_date": nav_date,
        "broker_snapshot_latest_date": snapshot_date,
        "recon_latest_date": recon_date,
        "reason_codes": reason_codes,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh live Alpaca artifacts and rebuild the monitor dashboard.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--run-root", default=None)
    parser.add_argument("--trade-date", default=None)
    parser.add_argument("--output-dir", default="web/dashboard")
    parser.add_argument("--mirror-output-dir", default=None)
    parser.add_argument("--env-file", default=None, help="Optional env file with Alpaca credentials.")
    parser.add_argument("--order-limit", type=int, default=200)
    parser.add_argument("--history-days", type=int, default=DEFAULT_HISTORY_DAYS)
    parser.add_argument("--skip-live-broker", action="store_true", help="Do not refresh live Alpaca artifacts before rebuild.")
    parser.add_argument(
        "--require-live-broker",
        action="store_true",
        help="Fail the command if the live broker refresh step cannot complete.",
    )
    return parser.parse_args()


def refresh_live_broker_artifacts(
    *,
    repo_root: Path,
    trade_date: str | None,
    order_limit: int,
    env_file: str | None,
    history_days: int,
) -> dict[str, str]:
    load_env_file(env_file)
    report_date = _resolve_report_date(trade_date)
    account, positions, orders_all, orders_closed, fills, source_mode = fetch_snapshot_inputs(
        report_date=report_date,
        order_limit=order_limit,
    )
    payload = build_snapshot_payload(
        report_date=report_date,
        workflow_run_id=None,
        git_sha=None,
        account=account,
        positions=positions,
        orders_all=orders_all,
        orders_closed=orders_closed,
        fills=fills,
    )
    snapshot_path = write_snapshot_json(payload, repo_root / "outputs" / "broker_snapshot", report_date)
    supporting = write_supporting_broker_artifacts(repo_root=repo_root, payload=payload, source_mode=source_mode)
    recon = write_posttrade_recon_from_snapshot(repo_root=repo_root, payload=payload, report_date=report_date)
    performance = refresh_live_performance_artifacts(
        repo_root=repo_root,
        report_date=report_date,
        snapshot_payload=payload,
        history_days=max(5, int(history_days)),
    )
    return {
        "report_date": report_date,
        "snapshot_path": str(snapshot_path),
        "source_mode": source_mode,
        "supporting_paths": ", ".join(str(path) for path in supporting.values()),
        "recon_path": str(recon.get("report_path")) if isinstance(recon, dict) else "",
        "recon_status": str(recon.get("drift_status")) if isinstance(recon, dict) else "",
        **performance,
    }


def rebuild_dashboard(*, repo_root: Path, run_root: str | None, trade_date: str | None, output_dir: str, mirror_output_dir: str | None = None) -> dict[str, str]:
    resolved_output_dir = repo_root / output_dir
    payload = DashboardV1Builder(repo_root=repo_root, report_date=trade_date).build()
    write_dashboard_v1_payload(payload, resolved_output_dir)
    if mirror_output_dir:
        write_dashboard_v1_payload(payload, repo_root / mirror_output_dir)
    return {
        "dashboard_json": str(resolved_output_dir / "dashboard-data.json"),
        "monitor_json": str(resolved_output_dir / "dashboard_data.json"),
        "summary_json": str(resolved_output_dir / "trading_day_summary.json"),
        "mirror_monitor_json": str((repo_root / mirror_output_dir / "dashboard_data.json")) if mirror_output_dir else "",
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    report_date = _resolve_report_date(args.trade_date)
    result: dict[str, object] = {
        "live_refresh": None,
        "dashboard": None,
    }
    # FR-059: broker telemetry failures must be visible and actionable rather
    # than silently swallowed. live_status carries an explicit status + reason
    # codes; under --require-live-broker a failure exits non-zero (alertable).
    live_status: dict[str, Any] = {
        "status": "skipped" if args.skip_live_broker else "pending",
        "reason_codes": [],
    }

    if not args.skip_live_broker:
        try:
            result["live_refresh"] = refresh_live_broker_artifacts(
                repo_root=repo_root,
                trade_date=args.trade_date,
                order_limit=max(1, int(args.order_limit)),
                env_file=args.env_file,
                history_days=max(5, int(args.history_days)),
            )
            live_status["status"] = "ok"
        except Exception as exc:
            code = _classify_broker_failure(exc)
            live_status["status"] = "failed"
            live_status["reason_codes"].append(code)
            live_status["error"] = str(exc)
            logger.error("[DASHBOARD_REFRESH] live broker refresh failed (%s): %s", code, exc)
            if args.require_live_broker:
                live_status["reason_codes"].append("live_broker_required_failed")
                staleness = evaluate_live_telemetry_staleness(repo_root=repo_root, report_date=report_date)
                for reason in staleness["reason_codes"]:
                    if reason not in live_status["reason_codes"]:
                        live_status["reason_codes"].append(reason)
                result["live_status"] = live_status
                result["live_telemetry_staleness"] = staleness
                logger.error("[DASHBOARD_REFRESH] live_broker_required_failed; exiting non-zero")
                print(json.dumps(result, indent=2, sort_keys=True))
                return 1
            logger.warning("[DASHBOARD_REFRESH] live broker refresh skipped: %s", exc)

    staleness = evaluate_live_telemetry_staleness(repo_root=repo_root, report_date=report_date)
    for reason in staleness["reason_codes"]:
        if reason not in live_status["reason_codes"]:
            live_status["reason_codes"].append(reason)
    result["live_status"] = live_status
    result["live_telemetry_staleness"] = staleness

    result["dashboard"] = rebuild_dashboard(
        repo_root=repo_root,
        run_root=args.run_root,
        trade_date=args.trade_date,
        output_dir=args.output_dir,
        mirror_output_dir=args.mirror_output_dir,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

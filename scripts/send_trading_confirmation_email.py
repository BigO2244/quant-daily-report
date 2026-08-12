from __future__ import annotations

import json
import hashlib
import logging
import os
import sys
import datetime as dt
from pathlib import Path

# Ensure repo root is on sys.path when invoked as `python scripts/send_trading_confirmation_email.py`
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Load .env explicitly so direct SSH/python invocations have the same email
# credentials as cron jobs that source the file before execution.
_env_path = _REPO_ROOT / ".env"
if _env_path.exists():
    with _env_path.open("r", encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from core.email_governance import EmailEvent
from core.dynamic_daily_email import render_dynamic_email_sections
from core.email_reporting_sections import (
    construction_provenance_rows,
    execution_reliability_rows,
    fr105_research_status_rows,
    merge_run_reporting_context,
    simple_html_section,
    target_attainment_rows,
    text_table_section,
)
from core.execution_payload import STATUS_EXECUTED, STATUS_HALTED, STATUS_SKIPPED_DUPLICATE
from core.operator_summary import write_operator_summary, load_operator_summary, format_operator_summary_log
from core.quant_report import send_email
from core.run_pointer import read_trade_stage_pointer
from core.shadow_scoreboard import build_shadow_scoreboard
from core.timing_policy import current_et

logger = logging.getLogger(__name__)


def _record_confirmation_delivery(
    run_root: Path,
    *,
    operator_summary_fields: dict,
    confirmation_email_sent: bool,
) -> None:
    """Record email delivery without rewriting certified Choice-2 truth."""

    existing = load_operator_summary(run_root)
    if str(existing.get("execution_source") or "") == "exact_execution_plan_v3":
        operator_bytes = json.dumps(
            existing,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        payload = {
            "schema_version": "caerus.trading_confirmation_delivery.v1",
            "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "run_id": existing.get("run_id"),
            "trade_date": existing.get("trade_date"),
            "confirmation_email_sent": bool(confirmation_email_sent),
            "operator_summary_sha256": hashlib.sha256(operator_bytes).hexdigest(),
        }
        path = run_root / "trading_confirmation_delivery.json"
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return
    write_operator_summary(
        run_root,
        **operator_summary_fields,
        confirmation_email_sent=confirmation_email_sent,
    )


def _resolve_trade_date() -> str:
    override = str(os.getenv("REPORT_DATE", "")).strip()
    if override:
        return override
    return current_et().strftime("%Y-%m-%d")


def _resolve_execution_pointer(trade_date: str) -> dict:
    try:
        pointer = read_trade_stage_pointer(trade_date, "execution")
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Malformed execution workflow pointer for {trade_date}"
        ) from exc
    if not pointer or not isinstance(pointer, dict):
        raise RuntimeError(
            f"Missing execution workflow pointer for {trade_date}; cannot resolve execution_results.json"
        )
    if str(pointer.get("trade_date") or "") != trade_date:
        raise RuntimeError(
            f"Execution workflow pointer trade_date mismatch for {trade_date}"
        )
    pointer_status = str(pointer.get("status") or "").strip().lower()
    if pointer_status == "running":
        raise RuntimeError(
            f"Execution workflow pointer for {trade_date} is still running; confirmation must wait"
        )
    return pointer


def _resolve_execution_pointer_optional(trade_date: str) -> dict | None:
    try:
        return _resolve_execution_pointer(trade_date)
    except RuntimeError as exc:
        if "still running" in str(exc).lower():
            raise
        logger.warning("[TRADING_CONFIRMATION] execution pointer unavailable for %s: %s", trade_date, exc)
        return None


def _explicit_results_path() -> Path | None:
    raw = str(os.getenv("TRADING_CONFIRMATION_RESULTS_PATH") or "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_absolute() else _REPO_ROOT / path


def _explicit_run_root() -> Path | None:
    raw = str(os.getenv("TRADING_CONFIRMATION_RUN_ROOT") or "").strip()
    if raw:
        path = Path(raw)
        return path if path.is_absolute() else _REPO_ROOT / path
    results_path = _explicit_results_path()
    if results_path is not None:
        return results_path.parent
    return None


def _load_results(trade_date: str) -> tuple[dict, Path]:
    explicit_path = _explicit_results_path()
    if explicit_path is not None:
        if not explicit_path.exists():
            raise RuntimeError(f"Explicit confirmation results path does not exist: {explicit_path}")
        return _load_json_dict(explicit_path), explicit_path

    latest = _resolve_execution_pointer_optional(trade_date)

    if latest:
        run_root = Path(str(latest.get("run_root") or "").strip())
        if run_root:
            results_path = run_root / "execution_results.json"
            if results_path.exists():
                with results_path.open("r", encoding="utf-8") as f:
                    return json.load(f), results_path
            logger.warning(
                "[TRADING_CONFIRMATION] execution results missing for %s at %s; trying execution payload fallback",
                trade_date,
                results_path,
            )
            payload_path = run_root / "execution_payload.json"
            if payload_path.exists():
                payload = _load_json_dict(payload_path)
                if _should_use_execution_payload_fallback(payload):
                    return _build_results_from_execution_payload(trade_date, payload, latest), payload_path
                logger.warning(
                    "[TRADING_CONFIRMATION] execution payload for %s is not terminal; trying broker snapshot fallback",
                    trade_date,
                )

    legacy_payload_path = _REPO_ROOT / "outputs" / "execution_email" / f"{trade_date}.json"
    if legacy_payload_path.exists():
        payload = _load_json_dict(legacy_payload_path)
        if _should_use_execution_payload_fallback(payload):
            return _build_results_from_execution_payload(trade_date, payload, latest or {}), legacy_payload_path
        logger.warning(
            "[TRADING_CONFIRMATION] legacy execution payload for %s is not terminal; trying broker snapshot fallback",
            trade_date,
        )

    return _load_broker_results_fallback(trade_date, latest or {})


def _load_json_dict(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    return payload if isinstance(payload, dict) else {}


def _to_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _should_use_execution_payload_fallback(payload: dict) -> bool:
    raw_status = str(payload.get("status") or payload.get("execution_status") or "").strip().upper()
    halt_reason = str(payload.get("halt_reason") or "").strip()
    submitted_count = _to_int(payload.get("submitted_count") or payload.get("orders_submitted_count"))
    return bool(halt_reason or submitted_count > 0 or raw_status in {STATUS_HALTED, STATUS_SKIPPED_DUPLICATE, "NO_ACTION"})


def _build_results_from_execution_payload(trade_date: str, payload: dict, pointer: dict | None = None) -> dict:
    payload_trade_date = str(payload.get("trade_date") or trade_date)
    if payload_trade_date != trade_date:
        raise RuntimeError(
            f"Execution payload trade_date mismatch for {trade_date}: {payload_trade_date}"
        )

    raw_status = str(payload.get("status") or payload.get("execution_status") or "").strip().upper()
    halt_reason = payload.get("halt_reason")
    submitted_count = _to_int(payload.get("submitted_count") or payload.get("orders_submitted_count"))
    accepted_count = _to_int(payload.get("accepted_count"))
    rejected_count = _to_int(payload.get("rejected_count"))

    if raw_status == STATUS_EXECUTED or submitted_count > 0:
        status = STATUS_EXECUTED
        operator_execution_status = str(payload.get("operator_execution_status") or "executed").strip().lower()
    elif raw_status == STATUS_SKIPPED_DUPLICATE:
        status = STATUS_SKIPPED_DUPLICATE
        operator_execution_status = "skipped_duplicate"
    else:
        status = STATUS_HALTED
        operator_execution_status = str(payload.get("operator_execution_status") or "halted").strip().lower()

    if submitted_count > 0 and accepted_count == 0 and rejected_count == 0:
        accepted_count = submitted_count

    return {
        "trade_date": trade_date,
        "run_id": str(payload.get("run_id") or (pointer or {}).get("run_id") or f"execution_payload:{trade_date}"),
        "mode": str(payload.get("mode") or os.getenv("TRADING_MODE") or os.getenv("MODE") or "paper").upper(),
        "status": status,
        "submitted_count": submitted_count,
        "accepted_count": accepted_count,
        "rejected_count": rejected_count,
        "halt_reason": halt_reason,
        "operator_execution_status": operator_execution_status,
        "execution_payload_fallback": True,
        "broker_fill_count": _to_int(payload.get("orders_filled_count") or payload.get("filled_count")),
    }


def _build_results_from_broker_snapshot(trade_date: str, snapshot: dict, pointer: dict | None = None) -> dict:
    counts = snapshot.get("counts") if isinstance(snapshot.get("counts"), dict) else {}
    orders = snapshot.get("orders_report_date") if isinstance(snapshot.get("orders_report_date"), list) else []
    fills = snapshot.get("fills_report_date") if isinstance(snapshot.get("fills_report_date"), list) else []

    statuses = [str((order or {}).get("status") or "").strip().lower() for order in orders if isinstance(order, dict)]
    submitted_count = int(counts.get("orders_report_date") or len(orders) or 0)
    rejected_count = sum(status in {"rejected"} for status in statuses)
    filled_count = int(counts.get("fills_report_date") or len(fills) or 0)
    # Count orders the broker reports as terminally filled. "accepted"/"new"/
    # "open"/"pending_new" are NOT fills — they are open orders that may still
    # reject. Only genuinely-filled orders count as accepted-and-done here.
    order_filled_statuses = sum(status in {"filled"} for status in statuses)
    open_count = sum(
        status in {
            "accepted", "accepted_for_bidding", "new", "open", "held",
            "pending_new", "pending_replace", "pending_cancel", "done_for_day",
            "partially_filled",
        }
        for status in statuses
    )
    accepted_count = max(order_filled_statuses, filled_count)

    if submitted_count <= 0 and len(fills) <= 0:
        raise RuntimeError(f"Broker snapshot for {trade_date} contains no report-date orders or fills")

    # Classify with the SAME open/new != accepted-filled discipline as the
    # primary execution-results path: an order merely sitting open is NOT
    # "EXECUTED". Only real fills earn EXECUTED; open-with-no-fills surfaces as
    # OPEN (not a silent green EXECUTED).
    terminal_statuses = {status for status in statuses if status}
    has_reject_like = any(status in {"rejected", "canceled", "expired"} for status in terminal_statuses)
    has_fill = filled_count > 0 or order_filled_statuses > 0

    if terminal_statuses and terminal_statuses.issubset({"rejected", "canceled", "expired"}):
        operator_execution_status = "halted"
        halt_reason = "broker_snapshot_no_executed_orders"
        status = STATUS_HALTED
    elif has_fill and has_reject_like:
        operator_execution_status = "partial"
        halt_reason = "broker_snapshot_partial_execution"
        status = STATUS_EXECUTED
    elif has_fill:
        operator_execution_status = "executed"
        halt_reason = None
        status = STATUS_EXECUTED
    elif open_count > 0:
        # Orders submitted and merely open/new with ZERO fills. Never EXECUTED.
        operator_execution_status = "open"
        halt_reason = "broker_snapshot_orders_open_not_filled"
        status = STATUS_HALTED
    else:
        operator_execution_status = "halted"
        halt_reason = "broker_snapshot_no_executed_orders"
        status = STATUS_HALTED

    return {
        "trade_date": trade_date,
        "run_id": str((pointer or {}).get("run_id") or f"broker_snapshot:{trade_date}"),
        "mode": str(os.getenv("TRADING_MODE") or os.getenv("MODE") or "paper").upper(),
        "status": status,
        "submitted_count": submitted_count,
        "accepted_count": accepted_count,
        "rejected_count": int(rejected_count),
        "open_orders_count": int(open_count),
        "halt_reason": halt_reason,
        "operator_execution_status": operator_execution_status,
        "broker_snapshot_fallback": True,
        "broker_fill_count": filled_count,
    }


def _allow_confirmation_broker_fetch() -> bool:
    return str(os.getenv("ALLOW_CONFIRMATION_BROKER_FETCH", "")).strip().lower() in {"1", "true", "yes", "y", "on"}


def _fetch_live_broker_snapshot_inputs(*, report_date: str, order_limit: int):
    from scripts.export_alpaca_broker_snapshot import fetch_snapshot_inputs

    return fetch_snapshot_inputs(report_date=report_date, order_limit=order_limit)


def _build_live_broker_snapshot_payload(**kwargs) -> dict:
    from scripts.export_alpaca_broker_snapshot import build_snapshot_payload

    return build_snapshot_payload(**kwargs)


_LIVE_PILOT_REFRESH_OPEN_STATUSES = {
    "accepted",
    "accepted_for_bidding",
    "done_for_day",
    "held",
    "new",
    "open",
    "pending_new",
    "pending_replace",
    "pending_cancel",
}


def _status_norm(value: object) -> str:
    text = str(value or "").strip().lower()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text


def _live_pilot_confirmation_needs_refresh(results: dict) -> bool:
    if str(results.get("mode") or "").strip().upper() != "LIVE_PILOT":
        return False
    if str(results.get("broker_status_refresh") or "").strip():
        return False
    submitted = _to_int(results.get("submitted_count"))
    filled = _to_int(
        results.get("filled_count")
        or results.get("orders_filled_count")
        or results.get("broker_fill_count")
    )
    if submitted <= 0 or filled >= submitted:
        return False
    submitted_type = str(
        results.get("submitted_order_type")
        or results.get("order_type_submitted")
        or ""
    ).strip().lower()
    marketable_count = _to_int(results.get("marketable_order_count"))
    if submitted_type != "market" and marketable_count <= 0:
        return False
    rows = results.get("broker_responses") or results.get("order_lifecycle") or []
    if not isinstance(rows, list) or not rows:
        return _to_int(results.get("unfilled_buy_count")) > 0 or str(results.get("status") or "").upper() == "SUBMITTED"
    for row in rows:
        if not isinstance(row, dict):
            continue
        order = row.get("order") if isinstance(row.get("order"), dict) else {}
        if _status_norm(row.get("status") or order.get("status")) in _LIVE_PILOT_REFRESH_OPEN_STATUSES:
            return True
    return False


def _write_live_pilot_refresh_failure(
    results: dict,
    results_path: Path,
    error: Exception,
) -> dict:
    updated = dict(results)
    updated["broker_status_refresh"] = "FAILED"
    updated["broker_status_refresh_error"] = str(error)
    updated["broker_status_refresh_errors"] = [str(error)]
    updated["broker_status_refresh_claims_broker_truth"] = False
    updated["broker_status_refresh_note"] = "Read-only broker order refresh failed; email retains artifact status."
    if results_path.name == "execution_results.json":
        results_path.write_text(
            json.dumps(updated, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
    return updated


def _retain_live_pilot_artifact_status_after_refresh_failure(
    original: dict,
    refreshed: dict,
    results_path: Path,
) -> dict:
    retained = dict(original)
    for key in (
        "broker_status_refresh",
        "broker_status_refresh_at",
        "broker_status_refresh_error",
        "broker_status_refresh_errors",
        "broker_status_refresh_claims_broker_truth",
        "broker_status_refresh_note",
    ):
        if key in refreshed:
            retained[key] = refreshed.get(key)
    retained.setdefault(
        "broker_status_refresh_note",
        "Read-only broker order refresh failed; email retains artifact status.",
    )
    retained["broker_status_refresh_claims_broker_truth"] = False
    if results_path.name == "execution_results.json":
        results_path.write_text(
            json.dumps(retained, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
    return retained


def refresh_live_pilot_confirmation_status(
    results: dict,
    results_path: Path,
    *,
    broker: object | None = None,
) -> tuple[dict, Path]:
    if not _live_pilot_confirmation_needs_refresh(results):
        return results, results_path
    run_root = Path(str(results.get("run_root") or "")).expanduser()
    if not str(run_root).strip() or str(run_root) == ".":
        run_root = results_path.parent
    try:
        from scripts.live_pilot_execute import refresh_live_pilot_reconciliation

        refresh_live_pilot_reconciliation(run_root=run_root, broker=broker)
        refreshed_path = run_root / "execution_results.json"
        if refreshed_path.exists():
            refreshed = _load_json_dict(refreshed_path)
            if str(refreshed.get("broker_status_refresh") or "").upper() == "FAILED":
                return _retain_live_pilot_artifact_status_after_refresh_failure(
                    results,
                    refreshed,
                    refreshed_path,
                ), refreshed_path
            return refreshed, refreshed_path
        return _write_live_pilot_refresh_failure(
            results,
            results_path,
            RuntimeError("live_pilot_refresh_missing_execution_results"),
        ), results_path
    except Exception as exc:
        logger.warning("[TRADING_CONFIRMATION] live pilot broker status refresh failed: %s", exc)
        return _write_live_pilot_refresh_failure(results, results_path, exc), results_path


def _load_broker_results_fallback(trade_date: str, pointer: dict | None = None) -> tuple[dict, Path]:
    snapshot_dir = _REPO_ROOT / "outputs" / "broker_snapshot"
    snapshot_path = snapshot_dir / f"broker_snapshot_{trade_date}.json"

    if snapshot_path.exists():
        snapshot = _load_json_dict(snapshot_path)
        return _build_results_from_broker_snapshot(trade_date, snapshot, pointer), snapshot_path

    if not _allow_confirmation_broker_fetch():
        logger.warning(
            "[TRADING_CONFIRMATION] broker snapshot missing for %s and live broker fetch is disabled",
            trade_date,
        )
        return {
            "trade_date": trade_date,
            "run_id": str((pointer or {}).get("run_id") or f"missing_artifacts:{trade_date}"),
            "mode": str(os.getenv("TRADING_MODE") or os.getenv("MODE") or "paper").upper(),
            "status": STATUS_HALTED,
            "submitted_count": 0,
            "accepted_count": 0,
            "rejected_count": 0,
            "halt_reason": "broker_snapshot_unavailable_local_artifact_missing",
            "operator_execution_status": "unavailable",
            "broker_snapshot_fallback": False,
            "broker_snapshot_unavailable": True,
            "broker_snapshot_path": str(snapshot_path),
        }, snapshot_path

    logger.info("[TRADING_CONFIRMATION] broker snapshot missing for %s; fetching Alpaca snapshot fallback", trade_date)
    account, positions, orders_all, orders_closed, fills, _source_mode = _fetch_live_broker_snapshot_inputs(
        report_date=trade_date,
        order_limit=200,
    )
    snapshot = _build_live_broker_snapshot_payload(
        report_date=trade_date,
        workflow_run_id=str((pointer or {}).get("run_id") or ""),
        git_sha=str((pointer or {}).get("git_sha") or ""),
        account=account,
        positions=positions,
        orders_all=orders_all,
        orders_closed=orders_closed,
        fills=fills,
    )
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return _build_results_from_broker_snapshot(trade_date, snapshot, pointer), snapshot_path


def _load_performance_data(trade_date: str) -> dict | None:
    """Load benchmark data and return a dict of performance metrics for today.

    Returns None if the file is missing, corrupt, or has no record for today.
    Wrapped in try/except so it never prevents the confirmation email from sending.
    """
    try:
        from core.benchmark_tracking import load_benchmark_with_meta, BENCHMARK_PATH

        benchmark_path = _REPO_ROOT / BENCHMARK_PATH
        records, inception_date = load_benchmark_with_meta(benchmark_path)

        if not records:
            return None

        # Find today's record
        today_rec = next((r for r in records if r.get("date") == trade_date), None)
        if today_rec is None:
            return None

        portfolio_value = today_rec.get("portfolio_value")
        spy_price = today_rec.get("spy_price")
        port_daily = today_rec.get("portfolio_return_daily", 0.0)
        spy_daily = today_rec.get("spy_return_daily", 0.0)
        excess_daily = today_rec.get("excess_return_daily", 0.0)
        port_cum = today_rec.get("portfolio_return_cum", 0.0)
        spy_cum = today_rec.get("spy_return_cum", 0.0)
        excess_cum = today_rec.get("excess_return_cum", 0.0)

        # Drawdown from peak: min(0, cumulative return)
        drawdown = min(0.0, port_cum)

        # Fall back to first record date if inception_date not stored
        if not inception_date and records:
            inception_date = records[0].get("date")

        return {
            "inception_date": inception_date,
            "portfolio_value": portfolio_value,
            "spy_price": spy_price,
            "port_daily": port_daily,
            "spy_daily": spy_daily,
            "excess_daily": excess_daily,
            "port_cum": port_cum,
            "spy_cum": spy_cum,
            "excess_cum": excess_cum,
            "drawdown": drawdown,
        }
    except Exception as exc:
        logger.warning("[TRADING_CONFIRMATION] benchmark load failed (non-blocking): %s", exc)
        return None


def _load_reconciliation_data(trade_date: str, results_path: Path) -> dict:
    results_payload = _load_json_dict(results_path) if results_path.exists() else {}
    terminal_outcome = str(results_payload.get("terminal_outcome") or "").upper()
    terminal_status = str(results_payload.get("terminal_status") or "").upper()
    if terminal_outcome in {"SYSTEM_FAILURE", "SUBMISSION_UNKNOWN"} or terminal_status in {
        "FAILED_RECONCILIATION", "SUBMISSION_UNKNOWN", "BLOCKED",
        "FAILED_PLAN_INTEGRITY",
    }:
        return {
            "status": "FAILED_RECONCILIATION",
            "path": results_path,
            "payload": results_payload,
            "reason": str(results_payload.get("reason_code") or terminal_status),
        }
    candidates: list[Path] = []
    run_root = results_path.parent if results_path.name in {"execution_results.json", "execution_payload.json"} else None
    if run_root is not None:
        operator_summary = _load_json_dict(run_root / "operator_summary.json") if (run_root / "operator_summary.json").exists() else {}
        summary_path = str(operator_summary.get("post_execution_recon_path") or "").strip()
        if summary_path:
            path = Path(summary_path)
            candidates.append(path if path.is_absolute() else _REPO_ROOT / path)
        candidates.append(run_root / "live_pilot_reconciliation.json")
        candidates.append(run_root / "broker" / f"recon_posttrade_{trade_date}.json")

    candidates.append(_REPO_ROOT / "outputs" / "broker" / f"recon_posttrade_{trade_date}.json")

    for path in candidates:
        if not path.exists():
            continue
        payload = _load_json_dict(path)
        status = str(
            payload.get("drift_status")
            or payload.get("reconciliation_status")
            or payload.get("status")
            or "UNKNOWN"
        ).strip().upper()
        return {
            "status": status or "UNKNOWN",
            "path": path,
            "payload": payload,
        }

    return {
        "status": "INDETERMINATE",
        "path": candidates[0] if candidates else None,
        "payload": {},
        "reason": "posttrade reconciliation artifact missing",
    }


def _format_reconciliation_section(recon: dict) -> tuple[str, str, bool]:
    status = str(recon.get("status") or "INDETERMINATE").upper()
    payload = recon.get("payload") if isinstance(recon.get("payload"), dict) else {}
    path = recon.get("path")
    path_text = str(path) if path else "unavailable"
    # SUBMITTED_UNFILLED (WARNING §f fix) is a non-terminal in-flight broker
    # state, not a drift/failure signal — treat it as healthy here too so this
    # section doesn't render an open-but-not-yet-filled batch as unhealthy.
    healthy = status in {"OK", "PASS", "OK_RECONCILED", "CLEAN", "DRY_RUN_NO_SUBMISSION"}
    if healthy:
        explanation = "Broker positions match expected post-execution state."
    elif status == "DRIFT_DETECTED":
        explanation = "Expected and broker positions differ."
    elif status == "UNEXPECTED_SHORT":
        explanation = "Unexpected short position detected."
    elif status == "INDETERMINATE":
        explanation = str(recon.get("reason") or "Reconciliation could not be completed from artifacts.")
    else:
        explanation = str(payload.get("operator_message") or "Reconciliation requires review.")

    drift_lines: list[str] = []
    for item in payload.get("share_deltas") or []:
        if not isinstance(item, dict):
            continue
        classification = str(item.get("classification") or "")
        if classification == "MATCH":
            continue
        symbol = str(item.get("symbol") or "").strip()
        expected = item.get("expected_qty")
        broker_qty = item.get("broker_qty")
        drift_lines.append(f"- {symbol}: expected {expected}, broker {broker_qty} ({classification})")
    for item in payload.get("qty_mismatches") or []:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").strip()
        line = f"- {symbol}: expected {item.get('expected_qty')}, broker {item.get('actual_qty')} (QTY_MISMATCH)"
        if line not in drift_lines:
            drift_lines.append(line)

    text_lines = [
        "--- Reconciliation Status ---",
        f"Status: {status}",
        f"Explanation: {explanation}",
        f"Artifact: {path_text}",
    ]
    if drift_lines:
        text_lines.append("Drift details:")
        text_lines.extend(drift_lines)
    text = "\n".join(text_lines) + "\n"

    html_lines = [html_escape(line) for line in text_lines]
    html = "<h3>Reconciliation Status</h3><p style='font-family:monospace;'>" + "<br>".join(html_lines) + "</p>"
    return text, html, healthy


def html_escape(value: object) -> str:
    import html

    return html.escape(str(value))


def _fmt_pct(val: float) -> str:
    sign = "+" if val >= 0 else ""
    return f"{sign}{val * 100:.2f}%"


def _format_live_pilot_buy_lifecycle(results: dict) -> tuple[str, str]:
    keys = {
        "approved_buy_count",
        "submitted_buy_count",
        "unfilled_buy_count",
        "escalated_buy_count",
        "entry_execution_policy",
        "prior_unfilled_attempts",
        "order_type_submitted",
        "submitted_order_type",
        "broker_status_refresh",
        "broker_status_refresh_error",
        "filled_qty",
        "avg_fill_price",
        "open_orders_count",
        "idle_cash_reason",
    }
    if not any(key in results for key in keys):
        return "", ""

    submitted_order_type = (
        results.get("order_type_submitted")
        or results.get("submitted_order_type")
        or "unavailable"
    )
    blocked_count = results.get("remaining_blocked_or_suppressed_buy_count")
    if blocked_count is None:
        try:
            blocked_count = max(
                int(results.get("approved_buy_count") or 0) - int(results.get("submitted_buy_count") or 0),
                0,
            )
        except Exception:
            blocked_count = "unavailable"
    rows = [
        ("Approved buys", results.get("approved_buy_count", "unavailable")),
        ("Submitted buys", results.get("submitted_buy_count", "unavailable")),
        ("Unfilled buys", results.get("unfilled_buy_count", "unavailable")),
        ("Escalated buys", results.get("escalated_buy_count", "unavailable")),
        ("Entry execution policy", results.get("entry_execution_policy", "unavailable")),
        ("Submitted order type", submitted_order_type),
        ("Marketable orders", results.get("marketable_order_count", "unavailable")),
        ("Passive orders", results.get("passive_order_count", "unavailable")),
        ("broker_status_refresh", results.get("broker_status_refresh", "not_attempted")),
        ("broker_status_refresh_error", _classify_reason(results.get("broker_status_refresh_error") or "; ".join(results.get("broker_status_refresh_errors") or [])) or "none"),
        ("Filled qty", results.get("filled_qty") or results.get("fill_qty") or "unavailable"),
        ("Avg fill price", results.get("avg_fill_price") or "unavailable"),
        ("Open orders count", results.get("open_orders_count", "unavailable")),
        ("Idle cash reason", results.get("idle_cash_reason", "unavailable")),
        ("Prior unfilled attempts", results.get("prior_unfilled_attempts", "unavailable")),
        ("Escalation reason", results.get("escalation_reason", "none")),
        ("Remaining blocked/suppressed buys", blocked_count),
        ("Blocked/suppressed reason", _classify_reason(results.get("blocked_or_suppressed_buy_reason") or results.get("halt_reason")) or "none"),
    ]
    text = (
        "--- Live Pilot Buy Lifecycle ---\n"
        + "\n".join(f"{label}: {value}" for label, value in rows)
        + "\n"
    )
    html = (
        "<h3>Live Pilot Buy Lifecycle</h3>"
        "<table style='border-collapse:collapse; font-family:monospace; font-size:0.95em;'>"
        + "".join(
            "<tr>"
            f"<td style='padding:4px 12px 4px 0;'><b>{html_escape(label)}</b></td>"
            f"<td style='padding:4px 0;'>{html_escape(value)}</td>"
            "</tr>"
            for label, value in rows
        )
        + "</table>"
    )
    return text, html


def _format_reporting_artifact_sections(results: dict, results_path: Path) -> tuple[str, str]:
    context = merge_run_reporting_context(results, results_path, _REPO_ROOT)
    sections = [
        (
            "Execution Reliability",
            execution_reliability_rows(context, _REPO_ROOT),
        ),
        (
            "Target Attainment",
            target_attainment_rows(context, _REPO_ROOT),
        ),
        (
            "Construction Provenance",
            construction_provenance_rows(context, _REPO_ROOT),
        ),
        (
            "FR-105 Research Status",
            fr105_research_status_rows(context, _REPO_ROOT),
        ),
    ]
    text = "".join(
        f"{text_table_section(title, rows)}\n"
        for title, rows in sections
        if rows
    )
    html = "".join(
        simple_html_section(title, rows)
        for title, rows in sections
        if rows
    )
    return text, html


def _lane_label(mode: str) -> str:
    """Human-facing lane tag for the subject/body. LIVE_PILOT and PAPER are the
    two real lanes; anything else is surfaced verbatim (upper-cased) so a novel
    lane can never be silently rendered as another."""
    normalized = str(mode or "").strip().upper()
    if normalized in {"LIVE_PILOT", "PAPER"}:
        return normalized
    return normalized or "UNKNOWN"


# Substrings that betray a Python exception string masquerading as a broker
# "reason" (e.g. a float() ValueError from our own adapter, not a venue decline).
_INTERNAL_ADAPTER_REASON_MARKERS = (
    "traceback",
    "exception",
    "error(",
    "valueerror",
    "typeerror",
    "keyerror",
    "attributeerror",
    "could not convert",
    "invalid literal",
    "float()",
    "int()",
    "nonetype",
    "unhashable",
    "unsupported operand",
)

_SYSTEM_SAFETY_REASON_MARKERS = (
    "live_pilot_deploy_",
    "live_pilot_running_sha_",
    "live_pilot_working_tree_",
    "live_pilot_kill_switch_",
    "live_pilot_deployment_in_progress",
    "live_pilot_gate_blocked",
    "deploy drift guard",
    "precompute_bundle_",
    "missing_live_pilot_",
    "invalid_live_pilot_",
)


def _classify_reason(reason: object) -> str:
    """Label a rejection/blocked reason as an internal adapter error vs a broker
    decline. A Python exception string surfaced as a broker 'reason' is one of
    OUR bugs, not the venue rejecting the order; conflating them hides adapter
    failures behind an apparent broker decline."""
    text = str(reason or "").strip()
    if not text or text.lower() in {"none", "null"}:
        return text
    lowered = text.lower()
    if any(marker in lowered for marker in _INTERNAL_ADAPTER_REASON_MARKERS):
        return f"internal adapter error (not a broker decline): {text}"
    if any(marker in lowered for marker in _SYSTEM_SAFETY_REASON_MARKERS):
        return f"system safety block (not a broker decline): {text}"
    return f"broker decline: {text}"


def _build_confirmation_email(results: dict, results_path: Path) -> tuple[str, str, str]:
    """
    Build confirmation email from execution results.

    Clearly distinguishes:
    - EXECUTED: orders submitted and processed
    - HALTED: execution could not proceed
    - SKIPPED_DUPLICATE: run already executed

    Includes a Performance vs SPY section when benchmark data is available.
    """
    trade_date = str(results.get("trade_date") or "")
    run_id = str(results.get("run_id") or "")
    terminal_outcome = str(results.get("terminal_outcome") or "").strip().upper()
    status = str(results.get("terminal_status") or results.get("status") or "UNKNOWN").strip().upper()
    mode = str(results.get("mode") or "UNKNOWN")
    submitted = int(
        results.get("submitted_count") or results.get("orders_submitted_count") or 0
    )
    accepted = int(results.get("accepted_count") or submitted)
    rejected = int(
        results.get("rejected_count") or results.get("orders_rejected_count") or 0
    )
    filled = int(
        results.get("filled_count")
        or results.get("orders_filled_count")
        or results.get("broker_fill_count")
        or 0
    )
    halt_reason = results.get("halt_reason")
    operator_execution_status = str(results.get("operator_execution_status") or "").strip().lower()
    reconciled_to_target_state = bool(results.get("reconciled_to_target_state"))
    raw_execution_status = str(results.get("raw_execution_status") or "").strip()
    raw_execution_reason = str(results.get("raw_execution_reason") or "").strip()
    final_execution_reason = str(results.get("final_execution_reason") or "").strip()

    # Determine status display
    if terminal_outcome in {"SYSTEM_FAILURE", "SUBMISSION_UNKNOWN"} or status in {
        "FAILED_RECONCILIATION", "SUBMISSION_UNKNOWN", "BLOCKED",
        "FAILED_PLAN_INTEGRITY",
    }:
        status_display = "HALTED"
        status_emoji = "🛑"
    elif status == STATUS_SKIPPED_DUPLICATE or (submitted > 0 and halt_reason and "duplicate" in halt_reason.lower()):
        status_display = "SKIPPED_DUPLICATE"
        status_emoji = "⏭️"
    elif terminal_outcome == "AUTHORIZED_NO_TRADE":
        status_display = "NO_ACTION"
        status_emoji = "—"
    elif (
        terminal_outcome == "RECONCILED_SUCCESS"
        or reconciled_to_target_state
        or operator_execution_status == "reconciled_success"
    ):
        # Raw broker-abort PARTIAL that post-trade reconciliation confirmed
        # reached the expected post-execution state. Surface the final status;
        # the raw status/reason are preserved in the diagnostic section below.
        status_display = "RECONCILED_SUCCESS"
        status_emoji = "✅"
    elif operator_execution_status == "partial":
        status_display = "PARTIAL"
        status_emoji = "⚠️"
    elif status == "SUBMITTED_UNFILLED" or operator_execution_status == "submitted_unfilled":
        # WARNING §f fix: orders are live at the broker and not yet (fully)
        # filled. This is NOT a failure (no HALTED) and NOT a completed
        # execution (no EXECUTED) — it needs its own display so the email is
        # never mistaken for either a clean success or a halt.
        status_display = "SUBMITTED_UNFILLED"
        status_emoji = "🕒"
    elif status == "DRY_RUN" or operator_execution_status == "dry_run":
        status_display = "DRY_RUN"
        status_emoji = "🧪"
    elif operator_execution_status == "open":
        # Orders submitted and merely open/new at snapshot time with zero fills.
        # This is explicitly NOT "EXECUTED": nothing has filled yet.
        status_display = "OPEN"
        status_emoji = "⏳"
    elif status == STATUS_HALTED or halt_reason:
        status_display = "HALTED"
        status_emoji = "🛑"
    elif submitted > 0:
        status_display = "EXECUTED"
        status_emoji = "✅"
    else:
        status_display = "NO_ACTION"
        status_emoji = "—"

    lane = _lane_label(mode)
    subject = f"[{lane}] Trading Confirmation {trade_date} [{status_display}]"

    # Data-provenance markers. When the primary execution_results.json is missing
    # and the report was rebuilt from a secondary source, say so LOUDLY so an
    # operator never mistakes a reconstructed status for a first-class one.
    if results.get("broker_snapshot_fallback"):
        fallback_note = (
            "derived from broker snapshot — execution results missing "
            "(counts reflect broker order/fill state, not the executor's reconciliation)"
        )
    elif results.get("execution_payload_fallback"):
        fallback_note = (
            "derived from execution payload — execution results missing "
            "(reconciliation-grade fill data may be incomplete)"
        )
    else:
        fallback_note = ""

    # Broker-authoritative flag: whether the broker's own terminal state was
    # successfully read back (vs. relying on our model/ledger view).
    authoritative_raw = results.get("broker_status_refresh_claims_broker_truth")
    if authoritative_raw is None:
        authoritative_raw = results.get("broker_authoritative_state")
    if authoritative_raw is None:
        authoritative_display = "unknown"
    else:
        authoritative_display = "yes" if bool(authoritative_raw) else "no (model/ledger view)"

    # Build reason line (rejection/halt reasons are classified as internal
    # adapter errors vs. broker declines so our own bugs are not read as venue
    # rejections).
    if status_display == "SKIPPED_DUPLICATE":
        reason_line = f"Skip reason: Duplicate execution detected for run_id {run_id}"
    elif status_display == "RECONCILED_SUCCESS":
        reason_line = f"Final reason: {final_execution_reason or 'raw_partial_reconciled_to_target_state'}"
    elif status_display == "SUBMITTED_UNFILLED":
        reason_line = "Status reason: orders submitted and accepted by the broker, not yet fully filled"
    elif halt_reason:
        reason_line = f"Halt reason: {_classify_reason(halt_reason)}"
    else:
        reason_line = "Status reason: none"
    html_reason_label = "Halt/skip reason" if halt_reason or status_display == "SKIPPED_DUPLICATE" else "Status reason"
    html_reason_value = (
        _classify_reason(halt_reason) if halt_reason
        else ("Duplicate execution detected" if status_display == "SKIPPED_DUPLICATE" else "none")
    )

    # When the final status was upgraded by post-trade reconciliation, preserve a
    # plainly-labelled diagnostic record of the raw (pre-reconciliation) status.
    raw_diag_text = ""
    raw_diag_html = ""
    if reconciled_to_target_state and raw_execution_status:
        raw_diag_text = (
            "--- Raw Execution (pre-reconciliation) ---\n"
            f"Raw status: {raw_execution_status}\n"
            f"Raw reason: {raw_execution_reason or 'none'}\n"
            "Note: final status upgraded after post-trade reconciliation confirmed "
            "broker positions match the expected post-execution state.\n"
        )
        raw_diag_html = (
            "<h3>🔎 Raw Execution (pre-reconciliation)</h3>"
            f"<p><b>Raw status:</b> {html_escape(raw_execution_status)}</p>"
            f"<p><b>Raw reason:</b> {html_escape(raw_execution_reason or 'none')}</p>"
            "<p style='font-size: 0.9em; color: #666;'>Final status upgraded after "
            "post-trade reconciliation confirmed broker positions match the expected "
            "post-execution state.</p>"
        )

    artifact_ref = f"Results artifact: {results_path}"
    fallback_text_line = f"Data source: {fallback_note}\n" if fallback_note else ""
    fallback_html_line = (
        f"<p style='color:#B22222;'><b>Data source:</b> {html_escape(fallback_note)}</p>"
        if fallback_note else ""
    )
    execution_text = (
        f"--- Execution Status [{lane}] ---\n"
        f"Lane: {lane}\n"
        f"Run ID: {run_id}\n"
        f"Trade date: {trade_date}\n"
        f"Mode: {mode}\n"
        f"Status: {status_display}\n"
        f"{fallback_text_line}"
        f"Submitted: {submitted}\n"
        f"Accepted: {accepted}\n"
        f"Rejected: {rejected}\n"
        f"Filled: {filled}\n"
        f"Broker authoritative: {authoritative_display}\n"
        f"{reason_line}\n"
        f"{artifact_ref}\n"
    )
    execution_html = (
        f"<h3>{status_emoji} Execution Status [{html_escape(lane)}]</h3>"
        f"<p><b>Lane:</b> {html_escape(lane)}</p>"
        f"<p><b>Run ID:</b> {html_escape(run_id)}</p>"
        f"<p><b>Trade Date:</b> {html_escape(trade_date)}</p>"
        f"<p><b>Mode:</b> {html_escape(mode)}</p>"
        f"<p><b>Status:</b> {html_escape(status_display)}</p>"
        f"{fallback_html_line}"
        f"<p><b>Submitted:</b> {submitted} | <b>Accepted:</b> {accepted} | "
        f"<b>Rejected:</b> {rejected} | <b>Filled:</b> {filled}</p>"
        f"<p><b>Broker authoritative:</b> {html_escape(authoritative_display)}</p>"
        f"<p><b>{html_escape(html_reason_label)}:</b> {html_escape(html_reason_value)}</p>"
        f"<p style='font-size: 0.9em; color: #666;'>{html_escape(artifact_ref)}</p>"
    )

    recon_text, recon_html, recon_healthy = _format_reconciliation_section(
        _load_reconciliation_data(trade_date, results_path)
    )
    live_pilot_lifecycle_text, live_pilot_lifecycle_html = _format_live_pilot_buy_lifecycle(results)
    reporting_artifacts_text, reporting_artifacts_html = _format_reporting_artifact_sections(
        results,
        results_path,
    )

    # ------------------------------------------------------------------ #
    # Performance vs SPY section
    # ------------------------------------------------------------------ #
    perf = _load_performance_data(trade_date)

    if perf:
        inc = perf["inception_date"] or "unknown"
        pv_str = f"${perf['portfolio_value']:,.2f}" if perf["portfolio_value"] is not None else "N/A"
        spy_str = f"${perf['spy_price']:,.2f}" if perf["spy_price"] is not None else "N/A"

        perf_text = (
            "\n"
            "--- Performance vs SPY ---\n"
            f"Portfolio value today:  {pv_str}\n"
            f"SPY today:              {spy_str}\n"
            f"Today's return:         {_fmt_pct(perf['port_daily'])} vs SPY {_fmt_pct(perf['spy_daily'])}"
            f"  (excess: {_fmt_pct(perf['excess_daily'])})\n"
            f"Since inception ({inc}): portfolio {_fmt_pct(perf['port_cum'])} vs SPY {_fmt_pct(perf['spy_cum'])}"
            f"  (excess: {_fmt_pct(perf['excess_cum'])})\n"
            f"Current drawdown from peak: {_fmt_pct(perf['drawdown'])}\n"
        )

        excess_color_daily = "#228B22" if perf["excess_daily"] >= 0 else "#CC0000"
        excess_color_cum = "#228B22" if perf["excess_cum"] >= 0 else "#CC0000"
        drawdown_color = "#CC0000" if perf["drawdown"] < 0 else "#228B22"

        perf_html = (
            "<h3>Performance vs SPY</h3>"
            "<table style='border-collapse:collapse; font-family:monospace; font-size:0.95em;'>"
            "<tr><td style='padding:4px 12px 4px 0;'><b>Portfolio value today</b></td>"
            f"<td style='padding:4px 0;'>{pv_str}</td></tr>"
            "<tr><td style='padding:4px 12px 4px 0;'><b>SPY today</b></td>"
            f"<td style='padding:4px 0;'>{spy_str}</td></tr>"
            "<tr><td style='padding:4px 12px 4px 0;'><b>Today's return</b></td>"
            f"<td style='padding:4px 0;'>{_fmt_pct(perf['port_daily'])} vs SPY {_fmt_pct(perf['spy_daily'])}"
            f"&nbsp;&nbsp;<span style='color:{excess_color_daily};font-weight:bold;'>"
            f"(excess: {_fmt_pct(perf['excess_daily'])})</span></td></tr>"
            f"<tr><td style='padding:4px 12px 4px 0;'><b>Since inception ({inc})</b></td>"
            f"<td style='padding:4px 0;'>{_fmt_pct(perf['port_cum'])} vs SPY {_fmt_pct(perf['spy_cum'])}"
            f"&nbsp;&nbsp;<span style='color:{excess_color_cum};font-weight:bold;'>"
            f"(excess: {_fmt_pct(perf['excess_cum'])})</span></td></tr>"
            "<tr><td style='padding:4px 12px 4px 0;'><b>Drawdown from peak</b></td>"
            f"<td style='padding:4px 0;'><span style='color:{drawdown_color};font-weight:bold;'>"
            f"{_fmt_pct(perf['drawdown'])}</span></td></tr>"
            "</table>"
        )
    else:
        perf_text = "\n--- Performance vs SPY ---\nPerformance data not yet available.\n"
        perf_html = (
            "<h3>Performance vs SPY</h3>"
            "<p style='color:#888;'>Performance data not yet available.</p>"
        )
    shadow_scoreboard = build_shadow_scoreboard(_REPO_ROOT, trade_date)
    shadow_text = shadow_scoreboard["text"]
    shadow_html = shadow_scoreboard["html"]
    shadow_healthy = str(shadow_scoreboard.get("status") or "").upper() == "OK" and "Artifact status: DEGRADED" not in shadow_text
    scoped_live_pilot_root = (
        Path(str(results.get("run_root")))
        if str(results.get("run_root") or "").strip()
        else results_path.parent
        if results_path.name == "execution_results.json"
        else None
    )
    dynamic_sections = render_dynamic_email_sections(
        _REPO_ROOT,
        trade_date,
        live_pilot_run_root=scoped_live_pilot_root,
    )

    # ------------------------------------------------------------------ #
    # Assemble body
    # ------------------------------------------------------------------ #
    action_items: list[str] = []
    if status_display in {"HALTED", "PARTIAL"}:
        halt_text = str(halt_reason or "").lower()
        if "deploy" in halt_text or "working_tree" in halt_text:
            action_items.append(
                "Validate and attest the exact VM deployment before the next live run; do not bypass the deployment guard."
            )
        else:
            action_items.append("Review execution halt before next run.")
    elif status_display == "SUBMITTED_UNFILLED":
        action_items.append(
            "Orders are live at the broker and not yet fully filled; monitor for "
            "fill/expiration before the next scheduled run (not a failure)."
        )
    if not recon_healthy:
        action_items.append("Review reconciliation drift before next run.")
    suppressed_buy_count = _to_int(
        results.get("remaining_blocked_or_suppressed_buy_count")
    )
    if suppressed_buy_count > 0:
        action_items.append(
            f"Review {suppressed_buy_count} blocked/suppressed buy(s): "
            f"{results.get('blocked_or_suppressed_buy_reason') or 'reason unavailable'}."
        )
    if not shadow_healthy:
        action_items.append("Review shadow artifact generation or data coverage.")
    if not action_items:
        action_items.append("None")
    operator_text = "--- Operator Action Required ---\n" + "\n".join(f"- {item}" for item in action_items) + "\n"
    operator_html = (
        "<h3>Operator Action Required</h3><ul>"
        + "".join(f"<li>{html_escape(item)}</li>" for item in action_items)
        + "</ul>"
    )

    body_text = (
        f"LANE: {lane} | STATUS: {status_display} | {trade_date}\n\n"
        f"{execution_text}\n"
        f"{raw_diag_text}{chr(10) if raw_diag_text else ''}"
        f"{live_pilot_lifecycle_text}{chr(10) if live_pilot_lifecycle_text else ''}"
        f"{reporting_artifacts_text}{chr(10) if reporting_artifacts_text else ''}"
        f"{recon_text}\n"
        f"{perf_text}"
        f"{shadow_text}"
        f"{dynamic_sections['text']}"
        f"\n{operator_text}"
    )
    body_html = (
        "<html><body>"
        f"<h2>[{html_escape(lane)}] Trading Confirmation {html_escape(trade_date)} [{html_escape(status_display)}]</h2>"
        f"{execution_html}"
        f"{('<hr>' + raw_diag_html) if raw_diag_html else ''}"
        f"{('<hr>' + live_pilot_lifecycle_html) if live_pilot_lifecycle_html else ''}"
        f"{('<hr>' + reporting_artifacts_html) if reporting_artifacts_html else ''}"
        f"<hr>"
        f"{recon_html}"
        f"<hr>"
        f"{perf_html}"
        f"<hr>"
        f"{shadow_html}"
        f"<hr>"
        f"{dynamic_sections['html']}"
        f"<hr>"
        f"{operator_html}"
        "</body></html>"
    )
    return subject, body_text, body_html


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    trade_date = _resolve_trade_date()
    results, results_path = _load_results(trade_date)
    results, results_path = refresh_live_pilot_confirmation_status(results, results_path)

    # Load run context for operator summary updates
    explicit_run_root = _explicit_run_root()
    latest = None if explicit_run_root is not None else _resolve_execution_pointer_optional(trade_date)
    run_root = explicit_run_root or (Path(str(latest.get("run_root") or "").strip()) if latest else None)
    reconciliation_data = _load_reconciliation_data(trade_date, results_path)
    operator_summary_fields = {
        "run_id": str(results.get("run_id") or ""),
        "trade_date": str(results.get("trade_date") or ""),
        "mode": str(results.get("mode") or ""),
        "terminal_status": str(
            results.get("terminal_status")
            or results.get("terminal_outcome")
            or results.get("status")
            or "UNKNOWN"
        ),
        "submitted_count": _to_int(results.get("submitted_count")),
        "accepted_count": _to_int(results.get("accepted_count")),
        "rejected_count": _to_int(results.get("rejected_count")),
        "post_execution_recon_status": (
            "DRY_RUN_NO_SUBMISSION"
            if str(results.get("terminal_status") or results.get("status") or "").upper() == "DRY_RUN"
            else reconciliation_data.get("status")
        ),
        "broker_authoritative_state": bool(
            results_path.name == "execution_results.json"
            and not results.get("execution_payload_fallback")
            and not results.get("broker_snapshot_fallback")
        ),
    }

    event = EmailEvent(
        event_type="trading_confirmation",
        subject="",
        body_text="",
        payload=results,
    )
    if not event.should_send():
        logger.info("[TRADING_CONFIRMATION] governance suppressed")
        if run_root:
            _record_confirmation_delivery(
                run_root,
                operator_summary_fields=operator_summary_fields,
                confirmation_email_sent=False,
            )
        return

    if str(os.getenv("EMAIL_DRY_RUN", "")).strip().lower() in {"1", "true", "yes", "y", "on"}:
        logger.info("[TRADING_CONFIRMATION] dry-run enabled; skipping send")
        if run_root:
            _record_confirmation_delivery(
                run_root,
                operator_summary_fields=operator_summary_fields,
                confirmation_email_sent=False,
            )
        return

    subject, body_text, body_html = _build_confirmation_email(results, results_path)
    send_email(subject=subject, body_text=body_text, body_html=body_html)
    logger.info("[TRADING_CONFIRMATION] sent from execution results: %s", results_path)

    # Update operator summary with confirmation email sent
    if run_root:
        _record_confirmation_delivery(
            run_root,
            operator_summary_fields=operator_summary_fields,
            confirmation_email_sent=True,
        )

        # Log final operator summary
        op_summary = load_operator_summary(run_root)
        if op_summary:
            print(format_operator_summary_log(op_summary))


if __name__ == "__main__":
    main()

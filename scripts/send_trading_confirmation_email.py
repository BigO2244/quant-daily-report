from __future__ import annotations

import json
import logging
import os
import sys
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
from core.execution_payload import STATUS_EXECUTED, STATUS_HALTED, STATUS_SKIPPED_DUPLICATE
from core.operator_summary import write_operator_summary, load_operator_summary, format_operator_summary_log
from core.quant_report import send_email
from core.run_pointer import read_trade_stage_pointer
from core.shadow_scoreboard import build_shadow_scoreboard
from core.timing_policy import current_et

logger = logging.getLogger(__name__)


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
    accepted_count = max(submitted_count - rejected_count, 0)

    if submitted_count <= 0 and len(fills) <= 0:
        raise RuntimeError(f"Broker snapshot for {trade_date} contains no report-date orders or fills")

    operator_execution_status = "executed"
    halt_reason = None
    status = STATUS_EXECUTED

    terminal_statuses = {status for status in statuses if status}
    if terminal_statuses and terminal_statuses.issubset({"rejected", "canceled", "expired"}):
        operator_execution_status = "halted"
        halt_reason = "broker_snapshot_no_executed_orders"
        status = STATUS_HALTED
    elif any(status in {"rejected", "canceled", "expired"} for status in terminal_statuses):
        operator_execution_status = "partial"
        halt_reason = "broker_snapshot_partial_execution"

    return {
        "trade_date": trade_date,
        "run_id": str((pointer or {}).get("run_id") or f"broker_snapshot:{trade_date}"),
        "mode": str(os.getenv("TRADING_MODE") or os.getenv("MODE") or "paper").upper(),
        "status": status,
        "submitted_count": submitted_count,
        "accepted_count": accepted_count,
        "rejected_count": int(rejected_count),
        "halt_reason": halt_reason,
        "operator_execution_status": operator_execution_status,
        "broker_snapshot_fallback": True,
        "broker_fill_count": int(counts.get("fills_report_date") or len(fills) or 0),
    }


def _allow_confirmation_broker_fetch() -> bool:
    return str(os.getenv("ALLOW_CONFIRMATION_BROKER_FETCH", "")).strip().lower() in {"1", "true", "yes", "y", "on"}


def _fetch_live_broker_snapshot_inputs(*, report_date: str, order_limit: int):
    from scripts.export_alpaca_broker_snapshot import fetch_snapshot_inputs

    return fetch_snapshot_inputs(report_date=report_date, order_limit=order_limit)


def _build_live_broker_snapshot_payload(**kwargs) -> dict:
    from scripts.export_alpaca_broker_snapshot import build_snapshot_payload

    return build_snapshot_payload(**kwargs)


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
        ("Prior unfilled attempts", results.get("prior_unfilled_attempts", "unavailable")),
        ("Escalation reason", results.get("escalation_reason", "none")),
        ("Remaining blocked/suppressed buys", blocked_count),
        ("Blocked/suppressed reason", results.get("blocked_or_suppressed_buy_reason") or results.get("halt_reason") or "none"),
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
    status = str(results.get("status") or "UNKNOWN")
    mode = str(results.get("mode") or "UNKNOWN")
    submitted = int(results.get("submitted_count") or 0)
    accepted = int(results.get("accepted_count") or 0)
    rejected = int(results.get("rejected_count") or 0)
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
    if status == STATUS_SKIPPED_DUPLICATE or (submitted > 0 and halt_reason and "duplicate" in halt_reason.lower()):
        status_display = "SKIPPED_DUPLICATE"
        status_emoji = "⏭️"
    elif reconciled_to_target_state or operator_execution_status == "reconciled_success":
        # Raw broker-abort PARTIAL that post-trade reconciliation confirmed
        # reached the expected post-execution state. Surface the final status;
        # the raw status/reason are preserved in the diagnostic section below.
        status_display = "RECONCILED_SUCCESS"
        status_emoji = "✅"
    elif operator_execution_status == "partial":
        status_display = "PARTIAL"
        status_emoji = "⚠️"
    elif status == STATUS_HALTED or halt_reason:
        status_display = "HALTED"
        status_emoji = "🛑"
    elif submitted > 0:
        status_display = "EXECUTED"
        status_emoji = "✅"
    else:
        status_display = "NO_ACTION"
        status_emoji = "—"

    subject = f"Trading Confirmation {trade_date} [{status_display}]"

    # Build reason line
    if status_display == "SKIPPED_DUPLICATE":
        reason_line = f"Skip reason: Duplicate execution detected for run_id {run_id}"
    elif status_display == "RECONCILED_SUCCESS":
        reason_line = f"Final reason: {final_execution_reason or 'raw_partial_reconciled_to_target_state'}"
    elif halt_reason:
        reason_line = f"Halt reason: {halt_reason}"
    else:
        reason_line = "Halt reason: none"

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
    execution_text = (
        "--- Execution Status ---\n"
        f"Run ID: {run_id}\n"
        f"Trade date: {trade_date}\n"
        f"Mode: {mode}\n"
        f"Status: {status_display}\n"
        f"Submitted: {submitted}\n"
        f"Accepted: {accepted}\n"
        f"Rejected: {rejected}\n"
        f"Filled: {filled}\n"
        f"{reason_line}\n"
        f"{artifact_ref}\n"
    )
    execution_html = (
        f"<h3>{status_emoji} Execution Status</h3>"
        f"<p><b>Run ID:</b> {html_escape(run_id)}</p>"
        f"<p><b>Trade Date:</b> {html_escape(trade_date)}</p>"
        f"<p><b>Mode:</b> {html_escape(mode)}</p>"
        f"<p><b>Status:</b> {html_escape(status_display)}</p>"
        f"<p><b>Submitted:</b> {submitted} | <b>Accepted:</b> {accepted} | "
        f"<b>Rejected:</b> {rejected} | <b>Filled:</b> {filled}</p>"
        f"<p><b>Halt/skip reason:</b> {html_escape(halt_reason or 'none')}</p>"
        f"<p style='font-size: 0.9em; color: #666;'>{html_escape(artifact_ref)}</p>"
    )

    recon_text, recon_html, recon_healthy = _format_reconciliation_section(
        _load_reconciliation_data(trade_date, results_path)
    )
    live_pilot_lifecycle_text, live_pilot_lifecycle_html = _format_live_pilot_buy_lifecycle(results)

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
    dynamic_sections = render_dynamic_email_sections(_REPO_ROOT, trade_date)

    # ------------------------------------------------------------------ #
    # Assemble body
    # ------------------------------------------------------------------ #
    action_items: list[str] = []
    if status_display in {"HALTED", "PARTIAL"}:
        action_items.append("Review execution halt before next run.")
    if not recon_healthy:
        action_items.append("Review reconciliation drift before next run.")
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
        f"{execution_text}\n"
        f"{raw_diag_text}{chr(10) if raw_diag_text else ''}"
        f"{live_pilot_lifecycle_text}{chr(10) if live_pilot_lifecycle_text else ''}"
        f"{recon_text}\n"
        f"{perf_text}"
        f"{shadow_text}"
        f"{dynamic_sections['text']}"
        f"\n{operator_text}"
    )
    body_html = (
        "<html><body>"
        f"<h2>Trading Confirmation {html_escape(trade_date)} [{html_escape(status_display)}]</h2>"
        f"{execution_html}"
        f"{('<hr>' + raw_diag_html) if raw_diag_html else ''}"
        f"{('<hr>' + live_pilot_lifecycle_html) if live_pilot_lifecycle_html else ''}"
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

    # Load run context for operator summary updates
    explicit_run_root = _explicit_run_root()
    latest = None if explicit_run_root is not None else _resolve_execution_pointer_optional(trade_date)
    run_root = explicit_run_root or (Path(str(latest.get("run_root") or "").strip()) if latest else None)

    event = EmailEvent(
        event_type="trading_confirmation",
        subject="",
        body_text="",
        payload=results,
    )
    if not event.should_send():
        logger.info("[TRADING_CONFIRMATION] governance suppressed")
        if run_root:
            write_operator_summary(
                run_root,
                run_id=str(results.get("run_id") or ""),
                trade_date=str(results.get("trade_date") or ""),
                mode=str(results.get("mode") or ""),
                confirmation_email_sent=False,
            )
        return

    if str(os.getenv("EMAIL_DRY_RUN", "")).strip().lower() in {"1", "true", "yes", "y", "on"}:
        logger.info("[TRADING_CONFIRMATION] dry-run enabled; skipping send")
        if run_root:
            write_operator_summary(
                run_root,
                run_id=str(results.get("run_id") or ""),
                trade_date=str(results.get("trade_date") or ""),
                mode=str(results.get("mode") or ""),
                confirmation_email_sent=False,
            )
        return

    subject, body_text, body_html = _build_confirmation_email(results, results_path)
    send_email(subject=subject, body_text=body_text, body_html=body_html)
    logger.info("[TRADING_CONFIRMATION] sent from execution results: %s", results_path)

    # Update operator summary with confirmation email sent
    if run_root:
        write_operator_summary(
            run_root,
            run_id=str(results.get("run_id") or ""),
            trade_date=str(results.get("trade_date") or ""),
            mode=str(results.get("mode") or ""),
            confirmation_email_sent=True,
        )

        # Log final operator summary
        op_summary = load_operator_summary(run_root)
        if op_summary:
            print(format_operator_summary_log(op_summary))


if __name__ == "__main__":
    main()

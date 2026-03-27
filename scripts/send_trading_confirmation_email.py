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
from core.execution_payload import STATUS_EXECUTED, STATUS_HALTED, STATUS_SKIPPED_DUPLICATE
from core.operator_summary import write_operator_summary, load_operator_summary, format_operator_summary_log
from core.quant_report import send_email
from core.run_pointer import read_trade_stage_pointer

logger = logging.getLogger(__name__)


def _resolve_trade_date() -> str:
    override = str(os.getenv("REPORT_DATE", "")).strip()
    if override:
        return override
    from datetime import date

    return date.today().isoformat()


def _resolve_execution_pointer(trade_date: str) -> dict:
    pointer = read_trade_stage_pointer(trade_date, "execution")
    if not pointer or not isinstance(pointer, dict):
        raise RuntimeError(
            f"Missing execution workflow pointer for {trade_date}; cannot resolve execution_results.json"
        )
    return pointer


def _load_results(trade_date: str) -> tuple[dict, Path]:
    latest = _resolve_execution_pointer(trade_date)
    run_root = Path(str(latest.get("run_root") or "").strip())
    if not run_root:
        raise RuntimeError("execution workflow pointer missing run_root")
    results_path = run_root / "execution_results.json"
    if not results_path.exists():
        raise RuntimeError(f"Missing execution results artifact: {results_path}")
    with results_path.open("r", encoding="utf-8") as f:
        return json.load(f), results_path


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


def _fmt_pct(val: float) -> str:
    sign = "+" if val >= 0 else ""
    return f"{sign}{val * 100:.2f}%"


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
    halt_reason = results.get("halt_reason")
    operator_execution_status = str(results.get("operator_execution_status") or "").strip().lower()

    # Determine status display
    if status == STATUS_SKIPPED_DUPLICATE or (submitted > 0 and halt_reason and "duplicate" in halt_reason.lower()):
        status_display = "SKIPPED_DUPLICATE"
        status_emoji = "⏭️"
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
    elif halt_reason:
        reason_line = f"Halt reason: {halt_reason}"
    else:
        reason_line = "Halt reason: none"

    artifact_ref = f"Results artifact: {results_path}"

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

    # ------------------------------------------------------------------ #
    # Assemble body
    # ------------------------------------------------------------------ #
    body_text = (
        f"Run ID: {run_id}\n"
        f"Trade date: {trade_date}\n"
        f"Mode: {mode}\n"
        f"Status: {status_display}\n"
        f"\n"
        f"Submitted: {submitted}\n"
        f"Accepted: {accepted}\n"
        f"Rejected: {rejected}\n"
        f"\n"
        f"{reason_line}\n"
        f"{artifact_ref}\n"
        f"{perf_text}"
    )
    body_html = (
        "<html><body>"
        f"<h3>{status_emoji} Trading Confirmation {trade_date}</h3>"
        f"<p><b>Run ID:</b> {run_id}</p>"
        f"<p><b>Trade Date:</b> {trade_date}</p>"
        f"<p><b>Mode:</b> {mode}</p>"
        f"<p><b>Status:</b> {status_display}</p>"
        f"<hr>"
        f"<p><b>Submitted:</b> {submitted} | <b>Accepted:</b> {accepted} | <b>Rejected:</b> {rejected}</p>"
        f"<p><b>Halt/skip reason:</b> {halt_reason or 'none'}</p>"
        f"<hr>"
        f"{perf_html}"
        f"<hr>"
        f"<p style='font-size: 0.9em; color: #666;'>{artifact_ref}</p>"
        "</body></html>"
    )
    return subject, body_text, body_html


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    trade_date = _resolve_trade_date()
    results, results_path = _load_results(trade_date)

    # Load run context for operator summary updates
    latest = _resolve_execution_pointer(trade_date)
    run_root = Path(str(latest.get("run_root") or "").strip()) if latest else None

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

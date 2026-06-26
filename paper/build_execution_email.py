from __future__ import annotations

import datetime as dt
from html import escape
from pathlib import Path
from typing import Any

from core.dynamic_daily_email import render_dynamic_email_sections
from core.email_reporting_sections import (
    construction_provenance_rows,
    execution_reliability_rows,
    fr105_research_status_rows,
    target_attainment_rows,
    text_table_section,
)
from paper.email_styles import wrap_email_html
from paper.html_tables import render_card, render_html_table


def _fmt_price(value: Any) -> str:
    try:
        return f"${float(value):,.2f}"
    except Exception:
        return "n/a"


def _fmt_shares(value: Any) -> str:
    try:
        return str(int(value))
    except Exception:
        return "n/a"


def _fmt_est_notional(value: Any) -> str:
    try:
        return f"~${float(value):,.2f}"
    except Exception:
        return "n/a"


def _dynamic_sections(payload: dict[str, Any], trade_date: str) -> dict[str, str]:
    if payload.get("include_dynamic_sleeve_sections") is False:
        return {"text": "", "html": ""}
    repo_root = Path(str(payload.get("repo_root") or Path(__file__).resolve().parents[1]))
    return render_dynamic_email_sections(repo_root, trade_date)




def _no_order_reasons(payload: dict[str, Any], limit: int = 3) -> list[str]:
    reasons: list[str] = []
    if payload.get("execution_status") == "HALTED" and payload.get("halt_reason"):
        reasons.append(f"execution halted: {payload.get('halt_reason')}")
    if payload.get("market_status") and str(payload.get("market_status")).upper() != "OPEN":
        reason = payload.get("market_reason") or payload.get("market_status")
        reasons.append(f"market status: {reason}")
    if payload.get("no_trades_reason"):
        reasons.append(str(payload.get("no_trades_reason")))

    risk_meta = payload.get("risk_meta", {}) or {}
    if bool(risk_meta.get("turnover_scaled")):
        turnover_scale = risk_meta.get("turnover_scale")
        scale_text = "unavailable"
        try:
            if turnover_scale is not None:
                scale_text = f"{float(turnover_scale):.4f}"
        except Exception:
            scale_text = "unavailable"
        reasons.append(
            "turnover scaling applied"
            f" (scale={scale_text})"
        )

    filter_stats = payload.get("filter_stats") if isinstance(payload.get("filter_stats"), dict) else {}
    dropped_zero = filter_stats.get("dropped_zero_shares")
    if dropped_zero not in (None, 0):
        reasons.append(f"dropped zero-share orders: {int(dropped_zero)}")

    dropped_min_notional = filter_stats.get("dropped_min_notional")
    if dropped_min_notional not in (None, 0):
        reasons.append(f"dropped below min-notional orders: {int(dropped_min_notional)}")

    if not reasons:
        reasons.append("no executable trades after constraints and filters")
    return reasons[:limit]



def _fmt_diag_count(value: Any) -> str:
    if value is None:
        return "unavailable"
    try:
        return str(int(value))
    except Exception:
        return "unavailable"


def _fmt_diag_money(value: Any) -> str:
    if value is None:
        return "unavailable"
    try:
        return f"${float(value):,.2f}"
    except Exception:
        return "unavailable"



def _diag_from_payload(payload: dict[str, Any], key: str) -> Any:
    if key == "proposed_intent_count":
        for candidate in (
            "planner_intended_trades_count",
            "proposed_trades_intent_count",
            "proposed_trades_intent",
            "proposed_intent_count",
        ):
            if payload.get(candidate) is not None:
                return payload.get(candidate)
        return None

    if key == "execution_eligible_count":
        for candidate in ("execution_eligible_trades_count", "executable_trades_count"):
            if payload.get(candidate) is not None:
                return payload.get(candidate)
        return None

    if key == "dropped_zero":
        filter_stats = payload.get("filter_stats") if isinstance(payload.get("filter_stats"), dict) else {}
        for candidate in ("dropped_zero_shares", "dropped_zero"):
            if filter_stats.get(candidate) is not None:
                return filter_stats.get(candidate)
        if payload.get("dropped_zero") is not None:
            return payload.get("dropped_zero")
        return None

    if key == "dropped_min_notional":
        filter_stats = payload.get("filter_stats") if isinstance(payload.get("filter_stats"), dict) else {}
        if filter_stats.get("dropped_min_notional") is not None:
            return filter_stats.get("dropped_min_notional")
        if payload.get("dropped_min_notional") is not None:
            return payload.get("dropped_min_notional")
        return None

    return payload.get(key)


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _candidate_lifecycle_reason_detail(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidate_trade_lifecycle")
    candidates = candidates if isinstance(candidates, list) else []
    parts: list[str] = []
    for raw in candidates:
        row = raw if isinstance(raw, dict) else {}
        ticker = str(row.get("ticker") or "").strip().upper()
        side = str(row.get("side") or "").strip().upper()
        reason = str(row.get("decision_reason") or row.get("suppression_or_clipping_reason") or "").strip()
        if ticker and side and reason:
            parts.append(f"{ticker} {side}:{reason}")
    return "|".join(parts)


def _candidate_lifecycle_rows(payload: dict[str, Any]) -> list[list[Any]]:
    summary = payload.get("candidate_trade_lifecycle_summary")
    summary = summary if isinstance(summary, dict) else {}
    artifact = payload.get("candidate_trade_lifecycle_artifact") or summary.get("artifact_path")
    reason_detail = _candidate_lifecycle_reason_detail(payload)
    rows = [
        ["Planned Payload Trades", _first_present(payload.get("planned_payload_trade_count"), summary.get("precompute_candidates"))],
        ["Executable Filter Passed", _first_present(payload.get("executable_filter_passed_count"), summary.get("passed_executable_filter"))],
        ["Intended Orders", _first_present(payload.get("intended_orders_count"), summary.get("intended_orders"))],
        ["Final Executable Trades", _first_present(payload.get("final_executable_trades_count"), payload.get("execution_eligible_trades_count"), payload.get("executable_trades_count"))],
        ["Orders Submitted", _first_present(payload.get("submitted_count"), payload.get("orders_submitted_count"), summary.get("submitted"))],
        ["Orders Accepted", _first_present(payload.get("accepted_count"), summary.get("accepted"))],
        ["Orders Filled", _first_present(payload.get("orders_filled_count"), summary.get("filled"))],
        ["Orders Rejected", _first_present(payload.get("rejected_count"), summary.get("rejected"))],
        ["Clipped Candidates", summary.get("clipped")],
        ["Suppressed Candidates", summary.get("suppressed")],
        ["Candidate Reasons", reason_detail],
    ]
    if artifact:
        rows.append(["Lifecycle Artifact", artifact])
    return [row for row in rows if row[1] not in (None, "")]


def _candidate_lifecycle_text(payload: dict[str, Any]) -> list[str]:
    rows = _candidate_lifecycle_rows(payload)
    if not rows:
        return []
    return [
        "",
        "EXECUTION LIFECYCLE",
        "Metric | Value",
        "------ | -----",
        *[f"{label} | {value}" for label, value in rows],
    ]


def _audit_reporting_text(payload: dict[str, Any]) -> list[str]:
    repo_root = Path(str(payload.get("repo_root") or Path(__file__).resolve().parents[1]))
    sections = [
        text_table_section(
            "Execution Reliability",
            execution_reliability_rows(payload, repo_root),
        ),
        text_table_section(
            "Target Attainment",
            target_attainment_rows(payload, repo_root),
        ),
        text_table_section(
            "Construction Provenance",
            construction_provenance_rows(payload, repo_root),
        ),
        text_table_section(
            "FR-105 Research Status",
            fr105_research_status_rows(payload, repo_root),
        ),
    ]
    return [section for section in sections if section]


def _fmt_planned_for(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    raw = raw.replace("T", " ")
    if raw.endswith("-05:00") or raw.endswith("-04:00"):
        raw = raw[:-6]
    try:
        parsed = dt.datetime.fromisoformat(str(value))
        return parsed.strftime("%Y-%m-%d %H:%M ET")
    except Exception:
        pass
    try:
        parsed = dt.datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S")
        return parsed.strftime("%Y-%m-%d %H:%M ET")
    except Exception:
        return raw


def build_execution_email_text(payload: dict[str, Any]) -> tuple[str, str]:
    trade_date = str(payload.get("trade_date", dt.date.today().isoformat()))
    mode = str(payload.get("mode", "PAPER")).upper()
    status = str(payload.get("execution_status", "READY")).upper()
    reason = payload.get("halt_reason")
    planned_for = payload.get("planned_for")
    plan_only = bool(payload.get("plan_only", False))
    market_status = str(payload.get("market_status", "")).upper()
    planning_disclaimer = payload.get("planning_disclaimer")
    pricing_source = str(payload.get("pricing_source", "OPEN")).upper()
    pricing_asof = str(payload.get("pricing_asof", "") or "")
    pricing_disclaimer = payload.get("pricing_disclaimer")
    turnover_note = payload.get("turnover_note")
    status_label = payload.get("status_label")
    status_reason = payload.get("status_reason")
    operator_execution_status = str(payload.get("operator_execution_status", "")).strip().lower()
    timing_status = str(payload.get("timing_status", "")).strip().lower()
    recon_failure = bool(payload.get("recon_failure", False))
    auto_bootstrap_triggered = bool(payload.get("auto_bootstrap_triggered", False))
    broker_snapshot_fallback = bool(payload.get("broker_snapshot_fallback", False))

    subject = f"TRADE EXECUTION — {trade_date} ({mode})"
    if bool(payload.get("pdt_constrained")):
        subject += " [PDT WARN]"
    if bool(payload.get("capital_constrained_no_trades")):
        subject += " [CAPITAL CONSTRAINED]"

    lines: list[str] = [
        f"Mode: {mode}",
        f"Trade Date: {trade_date}",
    ]
    if operator_execution_status:
        lines.append(f"Execution Outcome: {operator_execution_status.upper()}")
    if timing_status:
        lines.append(f"Timing Status: {timing_status}")

    if status == "HALTED":
        suffix = f" — {reason}" if reason else ""
        lines.append(f"Execution Status: HALTED{suffix}")
        
        # If this is a recon failure with auto-bootstrap, show detailed info
        if recon_failure:
            lines.extend([
                "",
                "=" * 70,
                "⚠️  NO TRADES SENT — PRETRADE RECONCILIATION FAILED  ⚠️",
                "=" * 70,
                "",
            ])
            
            if auto_bootstrap_triggered:
                lines.extend([
                    "AUTO-RECOVERY ACTION TAKEN:",
                    "✓ Canonical model snapshot auto-refreshed from broker positions",
                    "✓ Next scheduled run should pass reconciliation",
                    "✓ Normal trading will resume once positions sync",
                    "",
                ])
            
            recon_verdict = payload.get("recon_verdict", "FAIL")
            recon_diffs = payload.get("recon_diffs", {}) or {}
            
            lines.extend([
                "RECONCILIATION DETAILS:",
                f"• Verdict: {recon_verdict}",
            ])
            
            missing_in_broker = recon_diffs.get("missing_in_broker", []) or []
            missing_in_model = recon_diffs.get("missing_in_model", []) or []
            qty_mismatches = recon_diffs.get("qty_mismatches", []) or []
            
            if missing_in_broker:
                lines.extend([
                    "",
                    "Missing in Broker (model expected these positions):",
                ])
                for ticker in missing_in_broker:
                    lines.append(f"  - {ticker}")
            
            if missing_in_model:
                lines.extend([
                    "",
                    "Missing in Model (broker has these positions):",
                ])
                for ticker in missing_in_model:
                    lines.append(f"  - {ticker}")
            
            if qty_mismatches:
                lines.extend([
                    "",
                    "Quantity Mismatches:",
                    "  Ticker | Broker Qty | Model Qty | Diff",
                    "  ------ | ---------- | --------- | ----",
                ])
                for mismatch in qty_mismatches:
                    if isinstance(mismatch, dict):
                        ticker = mismatch.get("symbol", "?")
                        broker_qty = mismatch.get("broker_qty", 0)
                        model_qty = mismatch.get("model_qty", 0)
                        diff = mismatch.get("diff", 0)
                        lines.append(f"  {ticker} | {broker_qty} | {model_qty} | {diff:+.2f}")
            
            if status_reason:
                lines.extend([
                    "",
                    f"Status Reason: {status_reason}",
                ])
            
            execution_notes = payload.get("execution_notes", []) or []
            if execution_notes:
                lines.extend([
                    "",
                    "NOTES:",
                ])
                for note in execution_notes:
                    lines.append(f"• {note}")
        
        dynamic = _dynamic_sections(payload, trade_date)
        lines.extend(_audit_reporting_text(payload))
        if dynamic["text"]:
            lines.append(dynamic["text"].rstrip())
        return subject, "\n".join(lines)

    lines.append(f"Pricing Source: {pricing_source}")
    if pricing_asof:
        lines.append(f"Pricing As-Of: {pricing_asof}")

    if broker_snapshot_fallback and operator_execution_status == "executed":
        lines.append("Execution Status: EXECUTED — BROKER SNAPSHOT FALLBACK")
        if pricing_disclaimer:
            lines.append(str(pricing_disclaimer))
    elif status == "PLANNED":
        if plan_only and market_status == "OPEN":
            lines.append("Execution Status: PLANNED — PLAN ONLY")
        else:
            lines.append("Execution Status: PLANNED — MARKET CLOSED (NEXT OPEN)")
        if planned_for:
            lines.append(f"Planned For: {_fmt_planned_for(planned_for)}")
        lines.append(str(planning_disclaimer or "Planning email only — no orders were sent."))
        if pricing_disclaimer:
            lines.append(str(pricing_disclaimer))
    else:
        lines.append("Execution Status: READY")
        if plan_only and planned_for:
            lines.append(f"Planned For: {_fmt_planned_for(planned_for)}")
        if plan_only:
            lines.append("Planning email only — no orders were sent.")
    if status_label:
        lines.append(f"Execution Detail: {str(status_label)}")
    if status_reason:
        lines.append(f"Execution Reason: {str(status_reason)}")

    if turnover_note:
        lines.append(f"Risk Note: {turnover_note}")

    lifecycle_text = _candidate_lifecycle_text(payload)
    if lifecycle_text:
        lines.extend(lifecycle_text)
    lines.extend(_audit_reporting_text(payload))

    risk_summary = payload.get("risk_summary", {}) or {}
    if risk_summary:
        lines.extend(["", "PORTFOLIO RISK SUMMARY"])
        for metric, value in risk_summary.items():
            lines.append(f"- {metric}: {value}")

    raw_blocked_tickers = payload.get("blocked_tickers", {}) or {}
    blocked_ticker_lines: list[str] = []
    blocked_ticker_summary_items: list[str] = []
    blocked_tickers_map: dict[str, list[str]] = {}
    if isinstance(raw_blocked_tickers, dict):
        blocked_tickers_map = {
            str(ticker): [str(reason) for reason in (reasons or [])]
            for ticker, reasons in raw_blocked_tickers.items()
        }
    elif isinstance(raw_blocked_tickers, list):
        blocked_ticker_summary_items = [str(item) for item in raw_blocked_tickers]

    if blocked_tickers_map:
        blocked_ticker_lines.extend(["", "BLOCKED TICKERS (VALIDATION)"])
        for ticker in sorted(blocked_tickers_map.keys()):
            reasons = blocked_tickers_map.get(ticker, []) or []
            reason_txt = ", ".join(str(r) for r in reasons) if reasons else "validation_failed"
            blocked_ticker_lines.append(f"- {ticker}: {reason_txt}")
            blocked_ticker_summary_items.append(f"{ticker} ({reason_txt})")

    trades = payload.get("trades", []) or []
    notes_lines = [
        "- Entry (X) is the model’s expected execution price.",
        "- Stop-Loss (Y) and Take Profit (Z) are the model’s current exit levels.",
        "- CASH is not the same thing as SGOV: CASH is uninvested cash, SGOV is a Treasury ETF position.",
        "- If Execution Status is HALTED, do not place trades.",
        "- Refer to the Daily Quant / Trade Rundown for diagnostics and attribution.",
    ]

    shadow_action_lines = []
    if not trades:
        recommended_action = payload.get("recommended_action")
        confidence_level = payload.get("confidence_level")
        human_override_required = payload.get("human_override_required")
        rationale_items = payload.get("rationale", []) or []
        recommended_trades = payload.get("recommended_trades", []) or []
        blocked_by_constraints = payload.get("blocked_by_constraints", []) or []
        next_checkpoint = payload.get("next_checkpoint")
        signals_status = payload.get("signals_status")
        constraints_status = payload.get("constraints_status")
        execution_payload_status = payload.get("execution_payload_status")

        if any(
            value is not None
            for value in [
                recommended_action,
                confidence_level,
                human_override_required,
                next_checkpoint,
                signals_status,
                constraints_status,
                execution_payload_status,
            ]
        ) or rationale_items or blocked_by_constraints or recommended_trades:
            shadow_action_lines.extend(
                [
                    "",
                    "RECOMMENDED ACTION",
                    f"• Execute Trades Today: {str(recommended_action or 'NO').upper()}",
                    f"• Confidence Level: {str(confidence_level or 'HIGH').upper()}",
                    f"• Human Override Required: {str(human_override_required or 'NO').upper()}",
                    "",
                    "RATIONALE (1–2 MIN READ)",
                ]
            )
            if rationale_items:
                shadow_action_lines.extend([f"• {item}" for item in rationale_items])
            else:
                shadow_action_lines.append("• No additional rationale provided")

            shadow_action_lines.extend(["", "RECOMMENDED TRADES (IF OVERRIDDEN)"])
            if recommended_trades:
                shadow_action_lines.extend([f"• {item}" for item in recommended_trades])
            else:
                shadow_action_lines.append("None")

            shadow_action_lines.extend(["", "TRADES BLOCKED BY CONSTRAINTS"])
            if blocked_by_constraints:
                shadow_action_lines.extend([f"• {item}" for item in blocked_by_constraints])
            else:
                shadow_action_lines.append("None")

            shadow_action_lines.extend(
                [
                    "",
                    "NEXT CHECKPOINT",
                    f"• {next_checkpoint or 'Re-evaluate at next rebalance window or upon signal state change'}",
                    "",
                    "SYSTEM STATUS",
                    f"• Signals: {str(signals_status or 'VALID').upper()}",
                    f"• Constraints: {str(constraints_status or 'ENFORCED').upper()}",
                    f"• Execution Payload: {str(execution_payload_status or f'GENERATED ({len(trades)} executable trades)').upper()}",
                ]
            )

    if not trades:
        reasons = _no_order_reasons(payload)
        lines.extend(
            [
                "",
                "========================",
                "1) TODAY’S ACTION — EXECUTE THESE ORDERS",
                "========================",
                "",
                "========================",
                "NO TRADES TODAY",
                "========================",
                "",
                "WHY NO ORDERS?",
            ]
        )
        lines.extend([f"- {reason}" for reason in reasons])
        lines.extend(
            [
                "",
                "DIAGNOSTICS",
                "Metric | Value",
                "------ | -----",
                f"Proposed Trades (Intent) | {_fmt_diag_count(_diag_from_payload(payload, 'proposed_intent_count'))}",
                f"Executable Trades | {_fmt_diag_count(_diag_from_payload(payload, 'execution_eligible_count'))}",
                f"Dropped Zero Shares | {_fmt_diag_count(_diag_from_payload(payload, 'dropped_zero'))}",
                f"Dropped Min Notional | {_fmt_diag_count(_diag_from_payload(payload, 'dropped_min_notional'))}",
                f"Min Trade Dollars | {_fmt_diag_money(payload.get('min_trade_dollars'))}",
                "",
                "========================",
                "3) EXECUTION NOTES",
                "========================",
                "",
            ]
        )
        if shadow_action_lines:
            lines.extend(shadow_action_lines)
            lines.append("")
        if blocked_ticker_lines:
            lines.extend(blocked_ticker_lines)
            lines.append("")
        if blocked_ticker_summary_items:
            lines.extend(["", f"Blocked tickers: {', '.join(blocked_ticker_summary_items)}"])
        lines.extend(notes_lines)
        dynamic = _dynamic_sections(payload, trade_date)
        if dynamic["text"]:
            lines.append(dynamic["text"].rstrip())
        return subject, "\n".join(lines)

    buys = sorted(
        [t for t in trades if str(t.get("side", "")).upper() == "BUY"],
        key=lambda t: str(t.get("ticker", "")),
    )
    sells = sorted(
        [t for t in trades if str(t.get("side", "")).upper() in {"SELL", "CLOSE", "REDUCE"}],
        key=lambda t: str(t.get("ticker", "")),
    )

    lines.extend(
        [
            "",
            "========================",
            "1) TODAY’S ACTION — EXECUTE THESE ORDERS",
            "========================",
            "",
            "BUY ORDERS",
            "Ticker | Side | Shares | Entry (X) | Stop-Loss (Y) | Take Profit (Z) | Est. Notional",
            "------ | ---- | ---------- | --------- | ------------- | --------------- | ------------",
        ]
    )
    if buys:
        for tr in buys:
            lines.append(
                "{ticker} | BUY | {shares} | {entry} | {stop} | {take} | {notional}".format(
                    ticker=tr.get("ticker", ""),
                    shares=_fmt_shares(tr.get("shares")),
                    entry=_fmt_price(tr.get("entry_price")),
                    stop=_fmt_price(tr.get("stop_loss")),
                    take=_fmt_price(tr.get("take_profit")),
                    notional=_fmt_est_notional(tr.get("notional")),
                )
            )
    else:
        lines.append("(none)")

    lines.extend(
        [
            "",
            "SELL / CLOSE ORDERS",
            "Ticker | Side | Shares | Entry (X) | Stop-Loss (Y) | Take Profit (Z) | Notes",
            "------ | ---- | ------ | --------- | ------------- | --------------- | -----",
        ]
    )
    if sells:
        for tr in sells:
            lines.append(
                "{ticker} | {side} | {shares} | {entry} | {stop} | {take} | {notes}".format(
                    ticker=tr.get("ticker", ""),
                    side=str(tr.get("side", "SELL")).upper(),
                    shares=_fmt_shares(tr.get("shares")),
                    entry=_fmt_price(tr.get("entry_price")),
                    stop=_fmt_price(tr.get("stop_loss")),
                    take=_fmt_price(tr.get("take_profit")),
                    notes=tr.get("notes") or tr.get("reason") or "Rebalance",
                )
            )
    else:
        lines.append("(none)")

    run_id = str(payload.get("run_id", ""))
    order_ids = payload.get("order_ids", []) or []
    order_ids = sorted(
        order_ids,
        key=lambda oid: tuple((str(oid).split(":"))[-2:]) if ":" in str(oid) else ("", str(oid)),
    )

    if blocked_ticker_lines:
        lines.extend(blocked_ticker_lines)

    lines.extend(
        [
            "",
            "========================",
            "2) ORDER META (FOR TRACKING / IDEMPOTENCY)",
            "========================",
            "",
            "Run ID:",
            run_id or "n/a",
            "",
            "Deterministic Order IDs:",
        ]
    )
    for oid in order_ids:
        lines.append(f"- {oid}")

    lines.extend(
        [
            "",
            "========================",
            "3) EXECUTION NOTES",
            "========================",
            "",
        ]
    )
    lines.extend(notes_lines)
    dynamic = _dynamic_sections(payload, trade_date)
    if dynamic["text"]:
        lines.append(dynamic["text"].rstrip())

    return subject, "\n".join(lines)


def build_execution_email_html(payload: dict[str, Any]) -> tuple[str, str]:
    subject, _ = build_execution_email_text(payload)

    status = str(payload.get("execution_status", "READY")).upper()
    mode = str(payload.get("mode", "PAPER")).upper()
    trade_date = str(payload.get("trade_date", dt.date.today().isoformat()))
    status_label = payload.get("status_label")
    status_reason = payload.get("status_reason")
    recon_failure = bool(payload.get("recon_failure", False))
    auto_bootstrap_triggered = bool(payload.get("auto_bootstrap_triggered", False))
    operator_execution_status = str(payload.get("operator_execution_status", "")).strip().lower()
    broker_snapshot_fallback = bool(payload.get("broker_snapshot_fallback", False))

    status_display = status
    if broker_snapshot_fallback and operator_execution_status == "executed":
        status_display = "EXECUTED — BROKER SNAPSHOT FALLBACK"

    header_items = [
        f"<li><b>Mode:</b> {escape(mode)}</li>",
        f"<li><b>Trade Date:</b> {escape(trade_date)}</li>",
        f"<li><b>Execution Status:</b> {escape(status_display)}</li>",
    ]
    if operator_execution_status:
        header_items.append(f"<li><b>Execution Outcome:</b> {escape(operator_execution_status.upper())}</li>")
    if status_label:
        header_items.append(f"<li><b>Execution Detail:</b> {escape(str(status_label))}</li>")
    if status_reason:
        header_items.append(f"<li><b>Execution Reason:</b> {escape(str(status_reason))}</li>")
    if payload.get("turnover_note"):
        header_items.append(f"<li><b>Risk Note:</b> {escape(str(payload.get('turnover_note')))}</li>")

    cards = []
    
    # Add prominent recon failure banner if applicable
    if recon_failure:
        recon_verdict = payload.get("recon_verdict", "FAIL")
        recon_diffs = payload.get("recon_diffs", {}) or {}
        missing_in_broker = recon_diffs.get("missing_in_broker", []) or []
        missing_in_model = recon_diffs.get("missing_in_model", []) or []
        qty_mismatches = recon_diffs.get("qty_mismatches", []) or []
        
        banner_html = '<div style="background-color: #fff3cd; border: 3px solid #ffc107; padding: 20px; margin-bottom: 20px; border-radius: 8px;">'
        banner_html += '<h2 style="color: #856404; margin-top: 0;">⚠️ NO TRADES SENT — PRETRADE RECONCILIATION FAILED</h2>'
        
        if auto_bootstrap_triggered:
            banner_html += '<div style="background-color: #d4edda; border-left: 4px solid #28a745; padding: 12px; margin: 15px 0;">'
            banner_html += '<h3 style="color: #155724; margin: 0 0 8px 0;">Auto-Recovery Action Taken:</h3>'
            banner_html += '<ul style="margin: 0; padding-left: 20px; color: #155724;">'
            banner_html += '<li>✓ Canonical model snapshot auto-refreshed from broker positions</li>'
            banner_html += '<li>✓ Next scheduled run should pass reconciliation</li>'
            banner_html += '<li>✓ Normal trading will resume once positions sync</li>'
            banner_html += '</ul>'
            banner_html += '</div>'
        
        banner_html += f'<p><b>Reconciliation Verdict:</b> <span style="color: #dc3545;">{escape(recon_verdict)}</span></p>'
        
        if missing_in_broker:
            banner_html += '<p><b>Missing in Broker</b> (model expected these positions):</p><ul>'
            for ticker in missing_in_broker:
                banner_html += f'<li>{escape(str(ticker))}</li>'
            banner_html += '</ul>'
        
        if missing_in_model:
            banner_html += '<p><b>Missing in Model</b> (broker has these positions):</p><ul>'
            for ticker in missing_in_model:
                banner_html += f'<li>{escape(str(ticker))}</li>'
            banner_html += '</ul>'
        
        if qty_mismatches:
            banner_html += '<p><b>Quantity Mismatches:</b></p>'
            banner_html += '<table style="border-collapse: collapse; width: 100%; margin-top: 8px;"><thead><tr style="background-color: #f8f9fa;">'
            banner_html += '<th style="border: 1px solid #dee2e6; padding: 8px; text-align: left;">Ticker</th>'
            banner_html += '<th style="border: 1px solid #dee2e6; padding: 8px; text-align: right;">Broker Qty</th>'
            banner_html += '<th style="border: 1px solid #dee2e6; padding: 8px; text-align: right;">Model Qty</th>'
            banner_html += '<th style="border: 1px solid #dee2e6; padding: 8px; text-align: right;">Diff</th>'
            banner_html += '</tr></thead><tbody>'
            for mismatch in qty_mismatches:
                if isinstance(mismatch, dict):
                    ticker = escape(str(mismatch.get("symbol", "?")))
                    broker_qty = mismatch.get("broker_qty", 0)
                    model_qty = mismatch.get("model_qty", 0)
                    diff = mismatch.get("diff", 0)
                    diff_color = "#dc3545" if abs(diff) > 0 else "#6c757d"
                    banner_html += f'<tr><td style="border: 1px solid #dee2e6; padding: 8px;">{ticker}</td>'
                    banner_html += f'<td style="border: 1px solid #dee2e6; padding: 8px; text-align: right;">{broker_qty}</td>'
                    banner_html += f'<td style="border: 1px solid #dee2e6; padding: 8px; text-align: right;">{model_qty}</td>'
                    banner_html += f'<td style="border: 1px solid #dee2e6; padding: 8px; text-align: right; color: {diff_color}; font-weight: bold;">{diff:+.2f}</td></tr>'
            banner_html += '</tbody></table>'
        
        execution_notes = payload.get("execution_notes", []) or []
        if execution_notes:
            banner_html += '<div style="margin-top: 15px; padding: 10px; background-color: #f8f9fa; border-radius: 4px;">'
            banner_html += '<p style="margin: 0 0 8px 0; font-weight: bold;">Notes:</p><ul style="margin: 0;">'
            for note in execution_notes:
                banner_html += f'<li>{escape(str(note))}</li>'
            banner_html += '</ul></div>'
        
        banner_html += '</div>'
        
        cards.append(banner_html)
    
    cards.append(render_card("Run Context", f"<ul class='kvs'>{''.join(header_items)}</ul>"))

    lifecycle_rows = _candidate_lifecycle_rows(payload)
    if lifecycle_rows:
        cards.append(
            render_card(
                "Execution Lifecycle",
                render_html_table(["Metric", "Value"], lifecycle_rows, numeric_cols=set()),
            )
        )
    repo_root = Path(str(payload.get("repo_root") or Path(__file__).resolve().parents[1]))
    reliability_rows = execution_reliability_rows(payload, repo_root)
    if reliability_rows:
        cards.append(
            render_card(
                "Execution Reliability",
                render_html_table(["Metric", "Value"], reliability_rows, numeric_cols=set()),
            )
        )
    attainment_rows = target_attainment_rows(payload, repo_root)
    if attainment_rows:
        cards.append(
            render_card(
                "Target Attainment",
                render_html_table(["Metric", "Value"], attainment_rows, numeric_cols=set()),
            )
        )
    provenance_rows = construction_provenance_rows(payload, repo_root)
    if provenance_rows:
        cards.append(
            render_card(
                "Construction Provenance",
                render_html_table(["Metric", "Value"], provenance_rows, numeric_cols=set()),
            )
        )
    fr105_rows = fr105_research_status_rows(payload, repo_root)
    if fr105_rows:
        cards.append(
            render_card(
                "FR-105 Research Status",
                render_html_table(["Metric", "Value"], fr105_rows, numeric_cols=set()),
            )
        )

    trades = payload.get("trades", []) or []
    if not trades:
        why_rows = [["Reason", r] for r in _no_order_reasons(payload)]
        why_rows.extend(
            [
                ["Proposed Trades (Intent)", _fmt_diag_count(_diag_from_payload(payload, "proposed_intent_count"))],
                ["Executable Trades", _fmt_diag_count(_diag_from_payload(payload, "execution_eligible_count"))],
                ["Dropped Zero Shares", _fmt_diag_count(_diag_from_payload(payload, "dropped_zero"))],
                ["Dropped Min Notional", _fmt_diag_count(_diag_from_payload(payload, "dropped_min_notional"))],
                ["Min Trade Dollars", _fmt_diag_money(payload.get("min_trade_dollars"))],
            ]
        )
        cards.append(
            render_card(
                "Why no orders?",
                render_html_table(["Type", "Detail"], why_rows, numeric_cols=set()),
            )
        )
    buys = sorted(
        [t for t in trades if str(t.get("side", "")).upper() == "BUY"],
        key=lambda t: str(t.get("ticker", "")),
    )
    sells = sorted(
        [t for t in trades if str(t.get("side", "")).upper() in {"SELL", "CLOSE", "REDUCE"}],
        key=lambda t: str(t.get("ticker", "")),
    )

    buy_headers = ["Ticker", "Side", "Shares", "Entry (X)", "Stop (Y)", "Target (Z)", "Est. Notional"]
    buy_rows = [
        [
            tr.get("ticker", ""),
            "BUY",
            _fmt_shares(tr.get("shares")),
            _fmt_price(tr.get("entry_price")),
            _fmt_price(tr.get("stop_loss")),
            _fmt_price(tr.get("take_profit")),
            _fmt_est_notional(tr.get("notional")),
        ]
        for tr in buys
    ]
    cards.append(
        render_card(
            "Buy Orders",
            render_html_table(buy_headers, buy_rows, numeric_cols={2, 3, 4, 5, 6}),
        )
    )

    sell_headers = ["Ticker", "Side", "Shares", "Entry (X)", "Stop (Y)", "Target (Z)", "Notes / Reason"]
    sell_rows = [
        [
            tr.get("ticker", ""),
            str(tr.get("side", "SELL")).upper(),
            _fmt_shares(tr.get("shares")),
            _fmt_price(tr.get("entry_price")),
            _fmt_price(tr.get("stop_loss")),
            _fmt_price(tr.get("take_profit")),
            tr.get("notes") or tr.get("reason") or "Rebalance",
        ]
        for tr in sells
    ]
    cards.append(
        render_card(
            "Sell / Close Orders",
            render_html_table(sell_headers, sell_rows, numeric_cols={2, 3, 4, 5}),
        )
    )

    risk_summary = payload.get("risk_summary", {}) or {}
    if risk_summary:
        risk_rows = [[k, v] for k, v in risk_summary.items()]
        cards.append(
            render_card(
                "Portfolio Risk Summary",
                render_html_table(["Metric", "Value"], risk_rows, numeric_cols=set()),
            )
        )

    dynamic = _dynamic_sections(payload, trade_date)
    if dynamic["html"]:
        cards.append(dynamic["html"])

    html = wrap_email_html("TRADE EXECUTION", "".join(cards))
    return subject, html

from __future__ import annotations

import datetime as dt
from html import escape
from typing import Any

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
        reasons.append(
            "turnover scaling applied"
            f" (scale={float(risk_meta.get('turnover_scale', 1.0)):.4f})"
        )

    dropped_zero = risk_meta.get("dropped_zero")
    if dropped_zero not in (None, 0):
        reasons.append(f"dropped zero-share orders: {int(dropped_zero)}")

    dropped_min_notional = risk_meta.get("dropped_min_notional")
    if dropped_min_notional not in (None, 0):
        reasons.append(f"dropped below min-notional orders: {int(dropped_min_notional)}")

    if not reasons:
        reasons.append("no executable trades after constraints and filters")
    return reasons[:limit]

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
    mode = str(payload.get("mode", "SHADOW")).upper()
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

    subject = f"TRADE EXECUTION — {trade_date} ({mode})"

    lines: list[str] = [
        f"Mode: {mode}",
        f"Trade Date: {trade_date}",
    ]

    if status == "HALTED":
        suffix = f" — {reason}" if reason else ""
        lines.append(f"Execution Status: HALTED{suffix}")
        return subject, "\n".join(lines)

    lines.append(f"Pricing Source: {pricing_source}")
    if pricing_asof:
        lines.append(f"Pricing As-Of: {pricing_asof}")

    if status == "PLANNED":
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

    if turnover_note:
        lines.append(f"Risk Note: {turnover_note}")

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
                f"- Proposed Trades (Intent): {int(payload.get('proposed_trades_intent', len(payload.get('trades', []) or [])))}",
                f"- Executable Trades: {int(payload.get('executable_trades_count', len(payload.get('trades', []) or [])))}",
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

    return subject, "\n".join(lines)


def build_execution_email_html(payload: dict[str, Any]) -> tuple[str, str]:
    subject, _ = build_execution_email_text(payload)

    status = str(payload.get("execution_status", "READY")).upper()
    mode = str(payload.get("mode", "SHADOW")).upper()
    trade_date = str(payload.get("trade_date", dt.date.today().isoformat()))

    header_items = [
        f"<li><b>Mode:</b> {escape(mode)}</li>",
        f"<li><b>Trade Date:</b> {escape(trade_date)}</li>",
        f"<li><b>Execution Status:</b> {escape(status)}</li>",
    ]
    if payload.get("turnover_note"):
        header_items.append(f"<li><b>Risk Note:</b> {escape(str(payload.get('turnover_note')))}</li>")

    cards = [render_card("Run Context", f"<ul class='kvs'>{''.join(header_items)}</ul>")]

    trades = payload.get("trades", []) or []
    if not trades:
        why_rows = [["Reason", r] for r in _no_order_reasons(payload)]
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

    html = wrap_email_html("TRADE EXECUTION", "".join(cards))
    return subject, html

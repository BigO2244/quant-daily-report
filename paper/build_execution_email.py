from __future__ import annotations

import datetime as dt
from typing import Any


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


def build_execution_email_text(payload: dict[str, Any]) -> tuple[str, str]:
    trade_date = str(payload.get("trade_date", dt.date.today().isoformat()))
    mode = str(payload.get("mode", "SHADOW")).upper()
    status = str(payload.get("execution_status", "READY")).upper()
    reason = payload.get("halt_reason")

    subject = f"TRADE EXECUTION — {trade_date} ({mode})"

    lines: list[str] = [
        f"Mode: {mode}",
        f"Trade Date: {trade_date}",
    ]

    if status == "HALTED":
        suffix = f" — {reason}" if reason else ""
        lines.append(f"Execution Status: HALTED{suffix}")
        return subject, "\n".join(lines)

    lines.append("Execution Status: READY")

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
                    f"• Execution Payload: {str(execution_payload_status or 'NOT GENERATED (EXPECTED IN SHADOW)').upper()}",
                ]
            )

    if not trades:
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
                "Reason:",
                "- Market OPEN",
                "- Signals evaluated",
                "- No assets met entry criteria",
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

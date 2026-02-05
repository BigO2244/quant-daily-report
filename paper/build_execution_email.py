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
        return f"{float(value):.2f}"
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
    if not trades:
        lines.extend(
            [
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
                "- Entry (X) is the model’s expected execution price.",
                "- Stop-Loss (Y) and Take Profit (Z) are the model’s current exit levels.",
                "- If Execution Status is HALTED, do not place trades.",
                "- Refer to the Daily Quant / Trade Rundown for diagnostics and attribution.",
            ]
        )
        return subject, "\n".join(lines)

    buys = [t for t in trades if str(t.get("side", "")).upper() == "BUY"]
    sells = [t for t in trades if str(t.get("side", "")).upper() in {"SELL", "CLOSE"}]

    lines.extend(
        [
            "",
            "========================",
            "1) TODAY’S ACTION — EXECUTE THESE ORDERS",
            "========================",
            "",
            "BUY ORDERS",
            "Ticker | Side | Est. Shares | Entry (X) | Stop-Loss (Y) | Take Profit (Z) | Est. Notional",
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
            "Ticker | Side | Shares | Notes",
            "------ | ---- | ------ | -----",
        ]
    )
    if sells:
        for tr in sells:
            lines.append(
                "{ticker} | {side} | {shares} | {notes}".format(
                    ticker=tr.get("ticker", ""),
                    side=str(tr.get("side", "SELL")).upper(),
                    shares=_fmt_shares(tr.get("shares")),
                    notes=tr.get("notes") or tr.get("reason") or "Rebalance",
                )
            )
    else:
        lines.append("(none)")

    run_id = str(payload.get("run_id", ""))
    order_ids = payload.get("order_ids", []) or []

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
            "- Entry (X) is the model’s expected execution price.",
            "- Stop-Loss (Y) and Take Profit (Z) are the model’s current exit levels.",
            "- If Execution Status is HALTED, do not place trades.",
            "- Refer to the Daily Quant / Trade Rundown for diagnostics and attribution.",
        ]
    )

    return subject, "\n".join(lines)


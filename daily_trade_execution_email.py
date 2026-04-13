from __future__ import annotations

import json
import logging
import os
import argparse
from pathlib import Path

from core.email_governance import should_email_pre_trade_status
from core.run_pointer import read_latest_run_pointer, read_trade_stage_pointer
from core.timing_policy import current_et
from core.trading_mode import canonical_trading_mode, canonical_trading_mode_label
from paper.build_execution_email import build_execution_email_html, build_execution_email_text
from paper.paper_broker import load_config, reset_orders_sent_ledger_for_date
from paper.send_execution_email import send_execution_email

logger = logging.getLogger(__name__)


def _resolve_trade_date() -> str:
    override = os.getenv("REPORT_DATE", "").strip()
    if override:
        return override
    return current_et().strftime("%Y-%m-%d")


def _load_payload(path: Path, trade_date: str, mode: str) -> dict:
    if not path.exists():
        broker_fallback = _load_broker_snapshot_payload(trade_date=trade_date, mode=mode)
        if broker_fallback is not None:
            logger.warning(
                "[EXECUTION_EMAIL] payload missing, using broker snapshot fallback for %s: %s",
                trade_date,
                path,
            )
            return broker_fallback
        logger.warning("[EXECUTION_EMAIL] payload missing, writing HALTED artifact: %s", path)
        return {
            "trade_date": trade_date,
            "mode": mode.upper(),
            "execution_status": "HALTED",
            "halt_reason": "MISSING EXECUTION PAYLOAD",
            "trades": [],
            "run_id": "",
            "order_ids": [],
        }
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _to_float(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _load_broker_snapshot(trade_date: str) -> dict | None:
    snapshot_path = Path("outputs") / "broker_snapshot" / f"broker_snapshot_{trade_date}.json"
    if snapshot_path.exists():
        with snapshot_path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        return payload if isinstance(payload, dict) else None

    try:
        from scripts.export_alpaca_broker_snapshot import build_snapshot_payload, fetch_snapshot_inputs

        account, positions, orders_all, orders_closed, fills, _source_mode = fetch_snapshot_inputs(
            report_date=trade_date,
            order_limit=200,
        )
        snapshot = build_snapshot_payload(
            report_date=trade_date,
            workflow_run_id="",
            git_sha="",
            account=account,
            positions=positions,
            orders_all=orders_all,
            orders_closed=orders_closed,
            fills=fills,
        )
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return snapshot
    except Exception as exc:
        logger.warning("[EXECUTION_EMAIL] broker snapshot unavailable for %s: %s", trade_date, exc)
        return None


def _build_broker_snapshot_payload(snapshot: dict, trade_date: str, mode: str) -> dict | None:
    counts = snapshot.get("counts") if isinstance(snapshot.get("counts"), dict) else {}
    raw_orders = snapshot.get("orders_report_date")
    orders = raw_orders if isinstance(raw_orders, list) else []
    fills = snapshot.get("fills_report_date") if isinstance(snapshot.get("fills_report_date"), list) else []
    if not orders and not fills:
        return None

    trades: list[dict] = []
    rejected_count = 0
    filled_order_count = 0
    for order in orders:
        if not isinstance(order, dict):
            continue
        status = str(order.get("status") or "").strip().lower()
        if status == "rejected":
            rejected_count += 1
        if status == "filled":
            filled_order_count += 1

        shares = _to_float(order.get("filled_qty"))
        if shares is None:
            shares = _to_float(order.get("qty"))
        price = _to_float(order.get("filled_avg_price"))
        if price is None:
            price = _to_float(order.get("limit_price"))
        notional = _to_float(order.get("notional"))
        if notional is None and shares is not None and price is not None:
            notional = shares * price

        trades.append(
            {
                "ticker": str(order.get("symbol") or "").strip().upper(),
                "side": str(order.get("side") or "").strip().upper(),
                "shares": int(shares) if shares is not None else None,
                "entry_price": price,
                "stop_loss": None,
                "take_profit": None,
                "notional": notional,
                "reason": "broker_snapshot_fallback",
                "notes": "Executed at broker; canonical execution payload unavailable",
                "order_id": order.get("id") or order.get("client_order_id"),
            }
        )

    submitted_count = int(counts.get("orders_report_date") or len(orders) or 0)
    fill_count = int(counts.get("fills_report_date") or len(fills) or 0)
    run_id = (
        str((((snapshot.get("meta") or {}) if isinstance(snapshot.get("meta"), dict) else {}).get("workflow_run_id")) or "")
        or f"broker_snapshot:{trade_date}"
    )

    return {
        "trade_date": trade_date,
        "mode": mode.upper(),
        "execution_status": "READY",
        "halt_reason": None,
        "status_label": "BROKER_SNAPSHOT_FALLBACK",
        "status_reason": "Rendered from Alpaca broker snapshot because canonical execution payload was unavailable.",
        "operator_execution_status": "executed",
        "timing_status": "post_execution",
        "pricing_source": "BROKER_SNAPSHOT",
        "pricing_asof": trade_date,
        "pricing_disclaimer": "Derived from executed broker fills; model entry and exit levels were unavailable.",
        "trades": [trade for trade in trades if trade.get("ticker")],
        "run_id": run_id,
        "order_ids": [trade["order_id"] for trade in trades if trade.get("order_id")],
        "planner_intended_trades_count": submitted_count,
        "execution_eligible_trades_count": submitted_count,
        "orders_submitted_count": submitted_count,
        "orders_filled_count": fill_count,
        "submitted_count": submitted_count,
        "accepted_count": max(submitted_count - rejected_count, 0),
        "rejected_count": rejected_count,
        "broker_snapshot_fallback": True,
    }


def _load_broker_snapshot_payload(trade_date: str, mode: str) -> dict | None:
    snapshot = _load_broker_snapshot(trade_date)
    if snapshot is None:
        return None
    return _build_broker_snapshot_payload(snapshot, trade_date=trade_date, mode=mode)


def _resolve_payload_path(trade_date: str) -> Path:
    try:
        execution_pointer = read_trade_stage_pointer(trade_date, "execution")
    except json.JSONDecodeError as exc:
        logger.warning(
            "[EXECUTION_EMAIL] malformed execution workflow pointer for %s: %s",
            trade_date,
            exc,
        )
        execution_pointer = None
    if isinstance(execution_pointer, dict):
        run_root = str(execution_pointer.get("run_root") or "").strip()
        if run_root:
            candidate = Path(run_root) / "execution_payload.json"
            if candidate.exists():
                logger.info("[EXECUTION_EMAIL] using execution phase pointer: %s", candidate)
            else:
                logger.warning(
                    "[EXECUTION_EMAIL] execution phase pointer payload missing, refusing legacy fallback: %s",
                    candidate,
                )
            return candidate

    try:
        latest = read_latest_run_pointer()
    except json.JSONDecodeError as exc:
        logger.warning("[EXECUTION_EMAIL] malformed latest_run pointer: %s", exc)
        latest = None
    latest_stage = str((latest or {}).get("workflow_stage") or "").strip().lower()
    if (
        isinstance(latest, dict)
        and str(latest.get("trade_date") or "") == trade_date
        and latest_stage == "execution"
    ):
        run_root = str(latest.get("run_root") or "").strip()
        if run_root:
            candidate = Path(run_root) / "execution_payload.json"
            if candidate.exists():
                logger.info("[EXECUTION_EMAIL] using canonical payload: %s", candidate)
                return candidate

    legacy = Path("outputs") / "execution_email" / f"{trade_date}.json"
    logger.info("[EXECUTION_EMAIL] using legacy payload fallback: %s", legacy)
    return legacy


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build and optionally send pre-trade execution-status email artifact")
    parser.add_argument("--dry-run", action="store_true", help="write artifact only; skip SMTP send")
    parser.add_argument("--reset-ledger-date", default=None, help="Delete execution idempotency ledger rows matching YYYY-MM-DD before execution")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_args(argv)

    cfg = load_config("paper/config_paper.json")
    mode = canonical_trading_mode(cfg.trading_mode, field_name="TRADING_MODE")
    if mode == "live":
        raise RuntimeError("TRADING_MODE=live is blocked for execution email.")

    trade_date = _resolve_trade_date()
    if args.reset_ledger_date:
        reset_orders_sent_ledger_for_date("outputs/orders_sent/orders_sent.csv", args.reset_ledger_date)
    payload_path = _resolve_payload_path(trade_date)
    payload = _load_payload(payload_path, trade_date=trade_date, mode=mode)

    payload["mode"] = canonical_trading_mode_label(payload.get("mode") or mode)
    if payload["mode"] == "LIVE":
        payload["execution_status"] = "HALTED"
        payload["halt_reason"] = "LIVE MODE BLOCKED"

    subject, body_text = build_execution_email_text(payload)
    _, body_html = build_execution_email_html(payload)

    out_txt = Path("outputs") / "daily" / f"trade_execution_{trade_date}.txt"
    out_txt.parent.mkdir(parents=True, exist_ok=True)
    out_txt.write_text(body_text.rstrip() + "\n", encoding="utf-8")
    logger.info("[EXECUTION_EMAIL] wrote artifact: %s", out_txt)

    # --- Email Governance: This script is the operator-facing pre-trade status email ---
    execution_status = payload.get("execution_status", "UNKNOWN")
    halt_reason = payload.get("halt_reason")

    # Check if pre-trade analysis email is enabled in configuration.
    # The normalized status (READY / HALTED / NO_ACTION) is the operator message.
    if not should_email_pre_trade_status(execution_status, halt_reason):
        logger.info("[EXECUTION_EMAIL] email governance suppressed: event_type=pre_trade_analysis")
        return

    if args.dry_run:
        logger.info("[EXECUTION_EMAIL] dry_run=1 — skipping send")
        return

    send_execution_email(subject=subject, body_text=body_text, body_html=body_html, payload=payload)


if __name__ == "__main__":
    main()

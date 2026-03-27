from __future__ import annotations

import datetime as dt
import json
import logging
import os
import argparse
from pathlib import Path

from core.email_governance import should_email_pre_trade_status
from core.run_pointer import read_latest_run_pointer, resolve_trade_stage_pointer
from core.trading_mode import canonical_trading_mode, canonical_trading_mode_label
from paper.build_execution_email import build_execution_email_html, build_execution_email_text
from paper.paper_broker import load_config, reset_orders_sent_ledger_for_date
from paper.send_execution_email import send_execution_email

logger = logging.getLogger(__name__)


def _resolve_trade_date() -> str:
    override = os.getenv("REPORT_DATE", "").strip()
    if override:
        return override
    return dt.date.today().strftime("%Y-%m-%d")


def _load_payload(path: Path, trade_date: str, mode: str) -> dict:
    if not path.exists():
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


def _resolve_payload_path(trade_date: str) -> Path:
    execution_pointer = resolve_trade_stage_pointer(trade_date, "execution")
    if isinstance(execution_pointer, dict):
        run_root = str(execution_pointer.get("run_root") or "").strip()
        if run_root:
            candidate = Path(run_root) / "execution_payload.json"
            if candidate.exists():
                logger.info("[EXECUTION_EMAIL] using execution phase pointer: %s", candidate)
                return candidate

    latest = read_latest_run_pointer()
    if isinstance(latest, dict):
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

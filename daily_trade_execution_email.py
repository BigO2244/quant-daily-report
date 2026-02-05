from __future__ import annotations

import datetime as dt
import json
import logging
import os
from pathlib import Path

from paper.build_execution_email import build_execution_email_text
from paper.paper_broker import load_config
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


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    cfg = load_config("paper/config_paper.json")
    mode = cfg.trading_mode.lower()
    if mode == "live":
        raise RuntimeError("TRADING_MODE=live is blocked for execution email.")
    if mode not in {"paper", "shadow"}:
        raise RuntimeError(f"Unsupported TRADING_MODE={mode}")

    trade_date = _resolve_trade_date()
    payload_path = Path("outputs") / "execution_email" / f"{trade_date}.json"
    payload = _load_payload(payload_path, trade_date=trade_date, mode=mode)

    payload["mode"] = str(payload.get("mode") or mode).upper()
    if payload["mode"] == "LIVE":
        payload["execution_status"] = "HALTED"
        payload["halt_reason"] = "LIVE MODE BLOCKED"

    subject, body_text = build_execution_email_text(payload)

    out_txt = Path("outputs") / "daily" / f"trade_execution_{trade_date}.txt"
    out_txt.parent.mkdir(parents=True, exist_ok=True)
    out_txt.write_text(body_text, encoding="utf-8")
    logger.info("[EXECUTION_EMAIL] wrote artifact: %s", out_txt)

    send_execution_email(subject=subject, body_text=body_text, payload=payload)


if __name__ == "__main__":
    main()

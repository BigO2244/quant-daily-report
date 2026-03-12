from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple

from brokers.alpaca_broker import AlpacaBroker
from paper.run_manager import ensure_dir, safe_write_text

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def fetch_pretrade_snapshot() -> Dict[str, Any]:
    """
    Best-effort Alpaca snapshot captured before run_paper_day().

    This is observability-only in Phase 1: failures are recorded in the payload
    but must not alter execution behavior.
    """
    captured_at = _utc_now_iso()
    base_payload: Dict[str, Any] = {
        "broker": "alpaca",
        "captured_at": captured_at,
        "base_url": None,
        "paper": None,
        "account": {},
        "positions": [],
        "account_error": None,
        "positions_error": None,
        "ok": False,
    }

    try:
        broker = AlpacaBroker.from_env()
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        logger.warning("[BROKER][PRETRADE] broker init failed (non-blocking): %s", err)
        return {
            **base_payload,
            "account_error": err,
            "positions_error": err,
        }

    base_payload["base_url"] = broker.base_url
    base_payload["paper"] = bool(broker.paper)

    try:
        base_payload["account"] = broker.get_account() or {}
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        logger.warning("[BROKER][PRETRADE] account snapshot failed (non-blocking): %s", err)
        base_payload["account_error"] = err

    try:
        base_payload["positions"] = broker.get_positions() or []
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        logger.warning("[BROKER][PRETRADE] positions snapshot failed (non-blocking): %s", err)
        base_payload["positions_error"] = err

    base_payload["ok"] = (
        base_payload["account_error"] is None
        and base_payload["positions_error"] is None
    )
    return base_payload


def write_pretrade_snapshot_artifacts(
    *,
    run_root: Path,
    run_id: str,
    trade_date: str,
    snapshot: Dict[str, Any],
    allow_overwrite: bool = True,
) -> Tuple[Path, Path]:
    broker_dir = Path(run_root) / "broker"
    ensure_dir(broker_dir)

    account_payload = {
        "run_id": str(run_id),
        "trade_date": str(trade_date),
        "captured_at": str(snapshot.get("captured_at") or _utc_now_iso()),
        "broker": str(snapshot.get("broker") or "alpaca"),
        "base_url": snapshot.get("base_url"),
        "paper": snapshot.get("paper"),
        "ok": snapshot.get("account_error") is None,
        "error": snapshot.get("account_error"),
        "account": snapshot.get("account") or {},
    }
    positions: List[Dict[str, Any]] = list(snapshot.get("positions") or [])
    positions_payload = {
        "run_id": str(run_id),
        "trade_date": str(trade_date),
        "captured_at": str(snapshot.get("captured_at") or _utc_now_iso()),
        "broker": str(snapshot.get("broker") or "alpaca"),
        "base_url": snapshot.get("base_url"),
        "paper": snapshot.get("paper"),
        "ok": snapshot.get("positions_error") is None,
        "error": snapshot.get("positions_error"),
        "positions_count": int(len(positions)),
        "positions": positions,
    }

    account_path = broker_dir / "pretrade_account_snapshot.json"
    positions_path = broker_dir / "pretrade_positions.json"
    safe_write_text(
        account_path,
        json.dumps(account_payload, indent=2, sort_keys=True, default=str) + "\n",
        allow_overwrite=allow_overwrite,
    )
    safe_write_text(
        positions_path,
        json.dumps(positions_payload, indent=2, sort_keys=True, default=str) + "\n",
        allow_overwrite=allow_overwrite,
    )
    return account_path, positions_path

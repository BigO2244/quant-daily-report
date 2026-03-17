from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from paper.run_manager import safe_write_text

PRECOMPUTE_ROOT = Path("outputs/precompute")
PRECOMPUTE_SCHEMA_VERSION = 1
PRECOMPUTE_ARTIFACT_TYPE = "alpaca_precompute_bundle"
PRECOMPUTE_STATUS_COMPLETE = "complete"
PRECOMPUTE_STATUS_INCOMPLETE = "incomplete"
PRECOMPUTE_STATUS_INVALID = "invalid"

REASON_PRECOMPUTE_MISSING = "precompute_missing"
REASON_PRECOMPUTE_WRONG_TRADE_DATE = "precompute_wrong_trade_date"
REASON_PRECOMPUTE_INCOMPLETE = "precompute_incomplete"
REASON_PRECOMPUTE_INVALID = "precompute_invalid"
REASON_PRECOMPUTE_VALIDATION_FAILED = "precompute_validation_failed"


def precompute_bundle_dir(trade_date: str) -> Path:
    return PRECOMPUTE_ROOT / str(trade_date)


def precompute_contract_path(trade_date: str) -> Path:
    return precompute_bundle_dir(trade_date) / "contract.json"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return None


def build_precompute_contract(
    *,
    trade_date: str,
    run_id: str,
    mode: str,
    daily_snapshot: dict[str, Any],
    execution_payload: dict[str, Any],
    signals_filename: str = "signals.json",
    snapshot_filename: str = "daily_snapshot.json",
    payload_filename: str = "planned_execution_payload.json",
) -> dict[str, Any]:
    executable_count = int(
        (execution_payload or {}).get("execution_eligible_trades_count")
        or (execution_payload or {}).get("executable_trades_count")
        or len((execution_payload or {}).get("trades") or [])
        or 0
    )
    planner_intended = int(
        (execution_payload or {}).get("planner_intended_trades_count")
        or (execution_payload or {}).get("proposed_trades_intent_count")
        or (execution_payload or {}).get("proposed_trades_intent")
        or len((daily_snapshot or {}).get("proposed_trades") or [])
        or 0
    )
    return {
        "artifact_type": PRECOMPUTE_ARTIFACT_TYPE,
        "schema_version": PRECOMPUTE_SCHEMA_VERSION,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "trade_date": str(trade_date),
        "mode": str(mode).upper(),
        "source_run_id": str(run_id),
        "status": PRECOMPUTE_STATUS_COMPLETE,
        "validation_reason": None,
        "validated_for_execution": True,
        "workflow_stage": "precompute",
        "compatibility": {
            "trading_mode": str(mode).upper(),
            "execution_flow": "precompute_before_open_v1",
        },
        "files": {
            "daily_snapshot": snapshot_filename,
            "signals": signals_filename,
            "planned_execution_payload": payload_filename,
        },
        "summary": {
            "execution_status": str((execution_payload or {}).get("execution_status") or ""),
            "market_status": str((execution_payload or {}).get("market_status") or ""),
            "planner_intended_trades_count": planner_intended,
            "execution_eligible_trades_count": executable_count,
            "trade_plan_count": len((execution_payload or {}).get("trades") or []),
        },
    }


def write_precompute_bundle(
    *,
    trade_date: str,
    run_id: str,
    mode: str,
    daily_snapshot: dict[str, Any],
    signals_payload: dict[str, Any],
    execution_payload: dict[str, Any],
    allow_overwrite: bool = True,
) -> Path:
    bundle_dir = precompute_bundle_dir(trade_date)
    bundle_dir.mkdir(parents=True, exist_ok=True)

    snapshot_path = bundle_dir / "daily_snapshot.json"
    signals_path = bundle_dir / "signals.json"
    payload_path = bundle_dir / "planned_execution_payload.json"
    contract_path = bundle_dir / "contract.json"

    safe_write_text(
        snapshot_path,
        json.dumps(daily_snapshot, indent=2, default=str) + "\n",
        allow_overwrite=allow_overwrite,
    )
    safe_write_text(
        signals_path,
        json.dumps(signals_payload, indent=2, default=str) + "\n",
        allow_overwrite=allow_overwrite,
    )
    safe_write_text(
        payload_path,
        json.dumps(execution_payload, indent=2, default=str) + "\n",
        allow_overwrite=allow_overwrite,
    )

    contract = build_precompute_contract(
        trade_date=trade_date,
        run_id=run_id,
        mode=mode,
        daily_snapshot=daily_snapshot,
        execution_payload=execution_payload,
    )
    safe_write_text(
        contract_path,
        json.dumps(contract, indent=2, default=str) + "\n",
        allow_overwrite=allow_overwrite,
    )
    return contract_path


def load_precompute_contract(trade_date: str) -> dict[str, Any] | None:
    return _read_json(precompute_contract_path(trade_date))


def validate_precompute_contract(
    contract: dict[str, Any] | None,
    *,
    expected_trade_date: str,
    expected_mode: str = "ALPACA",
) -> tuple[bool, str | None]:
    if not contract:
        return False, REASON_PRECOMPUTE_MISSING
    if str(contract.get("artifact_type") or "") != PRECOMPUTE_ARTIFACT_TYPE:
        return False, REASON_PRECOMPUTE_INVALID
    if str(contract.get("trade_date") or "") != str(expected_trade_date):
        return False, REASON_PRECOMPUTE_WRONG_TRADE_DATE
    if str(contract.get("mode") or "").upper() != str(expected_mode).upper():
        return False, REASON_PRECOMPUTE_INVALID
    if str(contract.get("status") or "") != PRECOMPUTE_STATUS_COMPLETE:
        reason = str(contract.get("validation_reason") or "").strip()
        return False, reason or REASON_PRECOMPUTE_INCOMPLETE
    if not bool(contract.get("validated_for_execution")):
        reason = str(contract.get("validation_reason") or "").strip()
        return False, reason or REASON_PRECOMPUTE_VALIDATION_FAILED
    files = contract.get("files") or {}
    if not all(files.get(key) for key in ("daily_snapshot", "signals", "planned_execution_payload")):
        return False, REASON_PRECOMPUTE_INCOMPLETE
    bundle_dir = precompute_bundle_dir(expected_trade_date)
    for rel_name in files.values():
        if not (bundle_dir / str(rel_name)).exists():
            return False, REASON_PRECOMPUTE_INCOMPLETE
    return True, None


def load_precompute_inputs(
    *,
    trade_date: str,
    expected_mode: str = "ALPACA",
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None, str | None]:
    contract = load_precompute_contract(trade_date)
    valid, reason = validate_precompute_contract(
        contract,
        expected_trade_date=trade_date,
        expected_mode=expected_mode,
    )
    if not valid or not contract:
        return None, None, contract, reason

    files = contract.get("files") or {}
    bundle_dir = precompute_bundle_dir(trade_date)
    snapshot = _read_json(bundle_dir / str(files.get("daily_snapshot") or ""))
    signals = _read_json(bundle_dir / str(files.get("signals") or ""))
    payload = _read_json(bundle_dir / str(files.get("planned_execution_payload") or ""))
    if snapshot is None or signals is None or payload is None:
        return None, None, contract, REASON_PRECOMPUTE_INCOMPLETE
    snapshot["signals_snapshot_path"] = str(bundle_dir / str(files.get("signals")))
    return snapshot, payload, contract, None

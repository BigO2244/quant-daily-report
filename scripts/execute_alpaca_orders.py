from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

# Ensure repo root is on sys.path when invoked as `python scripts/execute_alpaca_orders.py`
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from brokers.alpaca_broker import AlpacaBroker, alpaca_client_order_id
from core.execution_payload import (
    STATUS_READY,
    STATUS_HALTED,
    STATUS_NO_ACTION,
    STATUS_EXECUTED,
    STATUS_SKIPPED_DUPLICATE,
    STATUS_IDEMPOTENT_REPLAY,
)
from core.execution_audit import write_early_halt_audit, write_executor_audit
from core.operator_summary import write_operator_summary, format_operator_summary_log
from core.run_pointer import LATEST_RUN_POINTER, read_latest_run_pointer
from core.step_summary import append_step_summary

logger = logging.getLogger(__name__)


def _write_execution_step_summary(
    *,
    run_root: Path,
    run_id: str,
    trade_date: str,
    mode: str,
    pretrade_status: str,
    proposed_count: int,
    executable_count: int,
    execution_status: str,
    submitted_count: int,
    accepted_count: int,
    rejected_count: int,
    duplicate_skip_reason: str | None = None,
) -> None:
    append_step_summary(
        [
            "### Execution Summary",
            f"- run_id: `{run_id}`",
            f"- trade_date: `{trade_date}`",
            f"- mode: `{mode}`",
            f"- pretrade_status: `{pretrade_status}`",
            f"- proposed_trades_count: `{proposed_count}`",
            f"- executable_trades_count: `{executable_count}`",
            f"- execution_status: `{execution_status}`",
            f"- submitted_count: `{submitted_count}`",
            f"- accepted_count: `{accepted_count}`",
            f"- rejected_count: `{rejected_count}`",
            f"- duplicate_skip_reason: `{duplicate_skip_reason or ''}`",
            f"- operator_summary_path: `{run_root / 'operator_summary.json'}`",
            f"- execution_results_path: `{run_root / 'execution_results.json'}`",
            "",
        ]
    )


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _json_default(obj: Any) -> Any:
    """Deterministic JSON serializer for non-native types returned by Alpaca SDK."""
    import uuid
    import decimal
    from datetime import date, datetime
    from enum import Enum

    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value if hasattr(obj, "value") else str(obj)
    if isinstance(obj, decimal.Decimal):
        return str(obj)
    if isinstance(obj, Path):
        return str(obj)
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    return str(obj)


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=_json_default) + "\n", encoding="utf-8")


def _results_path(run_root: Path) -> Path:
    return run_root / "execution_results.json"


def _new_results(pointer: Dict[str, Any], payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    payload = payload or {}
    return {
        "run_id": str(payload.get("run_id") or pointer.get("run_id") or ""),
        "trade_date": str(payload.get("trade_date") or pointer.get("trade_date") or ""),
        "mode": str(payload.get("mode") or pointer.get("mode") or "UNKNOWN"),
        "submitted_count": 0,
        "accepted_count": 0,
        "rejected_count": 0,
        "duplicate_count": 0,
        "order_ids": [],
        "status": STATUS_HALTED,
        "halt_reason": None,
        "broker_responses": [],
        "rejected_reasons": [],
    }


def _validate_payload(payload: Dict[str, Any]) -> Tuple[bool, str | None]:
    """Validate execution payload has required fields."""
    required = ["run_id", "trade_date", "mode", "status", "trades"]
    for key in required:
        if key not in payload:
            return False, f"missing_required_field:{key}"
    
    # Also accept execution_status for backward compatibility
    if "status" not in payload and "execution_status" in payload:
        payload["status"] = payload["execution_status"]
    
    if not isinstance(payload.get("trades"), list):
        return False, "invalid_trades_type"
    return True, None


def _validate_order(trade: Dict[str, Any], idx: int) -> Tuple[bool, str | None]:
    """
    Validate individual order before submission.
    
    Guardrails:
    - Must have ticker, side, shares/qty
    - Ticker must be non-empty string
    - Side must be BUY or SELL
    - Quantity must be positive number
    
    Returns (valid: bool, reason: str | None)
    """
    ticker = str(trade.get("ticker") or "").strip().upper()
    if not ticker:
        return False, f"order_{idx}_missing_ticker"
    
    side_raw = str(trade.get("side") or "").strip().upper()
    if side_raw not in {"BUY", "SELL", "CLOSE", "REDUCE"}:
        return False, f"order_{idx}_invalid_side:{side_raw}"
    
    qty = trade.get("shares") or trade.get("qty") or 0
    try:
        qty = float(qty)
    except (ValueError, TypeError):
        return False, f"order_{idx}_invalid_qty_type:{type(qty).__name__}"
    
    if qty <= 0:
        return False, f"order_{idx}_non_positive_qty:{qty}"
    
    return True, None


def _is_submit_mode(mode: str) -> bool:
    """Check if mode allows broker submission."""
    mode_norm = str(mode or "").strip().upper()
    return mode_norm in {"ALPACA", "PAPER", "ALPACA-PAPER"}


def _normalize_side(side: str) -> str:
    """Normalize order side to BUY or SELL."""
    side_norm = str(side or "").strip().upper()
    if side_norm in {"SELL", "CLOSE", "REDUCE"}:
        return "SELL"
    return "BUY"


def _submit_orders(payload: Dict[str, Any], broker: AlpacaBroker, run_root: Path) -> Dict[str, Any]:
    """
    Submit orders to Alpaca broker with per-order validation.
    
    Returns summary with:
    - submitted_count: orders passed validation and were submitted
    - accepted_count: broker accepted order
    - rejected_count: broker rejected or validation failed
    - order_ids: list of accepted Alpaca order IDs
    - broker_responses: detailed per-order results
    - rejected_reasons: reasons for each rejection
    """
    responses: List[Dict[str, Any]] = []
    rejected_reasons: List[str] = []
    order_ids: List[str] = []
    accepted = 0
    rejected = 0
    duplicates = 0
    submitted = 0

    broker_endpoint = os.getenv("APCA_API_BASE_URL", "unknown")
    logger.info(f"[EXECUTION] Alpaca endpoint: {broker_endpoint}")
    logger.info(f"[EXECUTION] Mode: {payload.get('mode')} | Validate orders before submission")

    for idx, trade in enumerate(payload.get("trades", [])):
        # Pre-submission validation
        valid, reason = _validate_order(trade, idx)
        if not valid:
            reason = reason or "unknown_validation_failure"
            logger.warning(f"[EXECUTION] Order {idx} validation failed: {reason}")
            rejected_reasons.append(reason)
            responses.append(
                {
                    "ticker": str(trade.get("ticker") or ""),
                    "side": str(trade.get("side") or ""),
                    "shares": trade.get("shares") or trade.get("qty") or 0,
                    "status": "VALIDATION_FAILED",
                    "reason": reason,
                }
            )
            rejected += 1
            continue

        symbol = str(trade.get("ticker") or "").upper().strip()
        side = _normalize_side(str(trade.get("side") or ""))
        qty = float(trade.get("shares") or trade.get("qty") or 0)
        internal_order_id = str(trade.get("order_id") or f"{payload.get('run_id','')}_{symbol}_{side}_{qty}")

        client_order_id = alpaca_client_order_id(internal_order_id)
        existing = broker.find_order_by_client_id(client_order_id)
        if existing:
            logger.info(
                "[EXECUTION] duplicate client_order_id treated as idempotent replay: %s",
                client_order_id,
            )
            responses.append(
                {
                    "ticker": symbol,
                    "side": side,
                    "shares": qty,
                    "client_order_id": client_order_id,
                    "status": "IDEMPOTENT_REPLAY",
                    "order": existing,
                }
            )
            duplicates += 1
            continue

        submitted += 1
        try:
            order = broker.submit_market_order(
                symbol=symbol,
                qty=qty,
                side=side,
                client_order_id=client_order_id,
                tif="day",
            )
            alpaca_order_id = str(order.get("id") or "")
            if alpaca_order_id:
                order_ids.append(alpaca_order_id)
            responses.append(
                {
                    "ticker": symbol,
                    "side": side,
                    "shares": qty,
                    "client_order_id": client_order_id,
                    "status": "ACCEPTED",
                    "order": order,
                }
            )
            accepted += 1
            logger.info(f"[EXECUTION] Order {idx} ACCEPTED: {symbol} {side} {qty}")
        except Exception as exc:
            reason = f"broker_error:{str(exc)}"
            logger.error(f"[EXECUTION] Order {idx} REJECTED: {symbol} {side} {qty} - {exc}")
            rejected_reasons.append(reason)
            responses.append(
                {
                    "ticker": symbol,
                    "side": side,
                    "shares": qty,
                    "client_order_id": client_order_id,
                    "status": "REJECTED",
                    "error": str(exc),
                }
            )
            rejected += 1

    return {
        "submitted_count": submitted,
        "accepted_count": accepted,
        "rejected_count": rejected,
        "duplicate_count": duplicates,
        "order_ids": order_ids,
        "broker_responses": responses,
        "rejected_reasons": rejected_reasons,
    }


def run_execution(force_resubmit: bool = False) -> Dict[str, Any]:
    """
    Execute canonical payload orders to Alpaca broker.
    
    Idempotency: skips if execution_results.json already exists for this run_id.
    
    Guardrails:
    - Validates every order before submission
    - Logs broker endpoint and mode
    - Fails closed on invalid orders
    - Records rejected reasons
    
    Returns execution results dict with status, counts, and responses.
    """
    _pointer_path = Path(LATEST_RUN_POINTER)
    logger.info(
        "[EXEC_POINTER] checking canonical pointer: exists=%s path=%s",
        _pointer_path.exists(), _pointer_path,
    )
    latest = read_latest_run_pointer()
    if not latest or not isinstance(latest, dict):
        trade_date = os.environ.get("REPORT_DATE", "UNKNOWN")
        halt_reason = (
            "MISSING_POINTER — outputs/latest_run.json not found and no fallback resolved. "
            "engine_run did not write the pointer (planner may have failed before "
            "_init_run_context, or canonical_positions.json was missing). "
            "Trigger workflow_dispatch with bootstrap_model_ledger_from_broker=true first."
        )
        logger.error("[EXEC_POINTER] %s", halt_reason)
        fallback_run_id = f"MISSING_POINTER_{trade_date}"
        fallback_root = Path(f"outputs/runs/{fallback_run_id}")
        fallback_root.mkdir(parents=True, exist_ok=True)
        out: Dict[str, Any] = {
            "run_id": fallback_run_id,
            "trade_date": trade_date,
            "mode": "UNKNOWN",
            "submitted_count": 0,
            "accepted_count": 0,
            "rejected_count": 0,
            "order_ids": [],
            "status": STATUS_HALTED,
            "halt_reason": halt_reason,
            "broker_responses": [],
            "rejected_reasons": [],
        }
        _write_json(_results_path(fallback_root), out)
        write_early_halt_audit(
            run_id=fallback_run_id,
            trade_date=trade_date,
            mode="UNKNOWN",
            halt_reason=halt_reason,
            stage="missing_pointer",
        )
        print(
            f"[EXECUTION_SUMMARY] run_id={fallback_run_id} submitted=0 accepted=0 rejected=0 "
            f"status={STATUS_HALTED}"
        )
        sys.exit(1)

    _source = latest.get("_source", "latest_run_json")
    run_root = Path(str(latest.get("run_root") or "").strip())
    logger.info(
        "[EXEC_POINTER] resolved pointer source=%s run_id=%s trade_date=%s run_root=%s status=%s",
        _source, latest.get("run_id"), latest.get("trade_date"), run_root, latest.get("status"),
    )
    if not run_root:
        raise RuntimeError("latest_run.json missing run_root")
    # Guard: if the directory doesn't exist the planner produced no artifacts.
    # upload-artifact@v4 silently drops empty directories, so this happens whenever
    # engine_run fails before _init_run_context writes any files (e.g. missing
    # canonical_positions.json).  Write a HALTED result so the email job can still
    # send a proper confirmation rather than silently skipping it.
    if not run_root.exists() or (run_root.exists() and not any(run_root.iterdir())):
        run_root.mkdir(parents=True, exist_ok=True)
        out = _new_results(latest)
        out["status"] = STATUS_HALTED
        out["halt_reason"] = (
            f"ENGINE_RUN_NO_ARTIFACTS — run directory missing or empty: {run_root}. "
            "engine_run likely failed before _init_run_context wrote any outputs. "
            "Most common cause: canonical_positions.json missing from cache. "
            "Fix: trigger workflow_dispatch with bootstrap_model_ledger_from_broker=true."
        )
        _write_json(_results_path(run_root), out)
        logger.error(
            "[EXEC_POINTER] HALTED — engine_run produced no artifacts at %s. "
            "Check engine_run job logs. "
            "If canonical_positions.json is missing, bootstrap first via workflow_dispatch.",
            run_root,
        )
        print(
            "[EXECUTION_SUMMARY] "
            f"run_id={out.get('run_id','')} submitted=0 accepted=0 rejected=0 status={STATUS_HALTED}"
        )
        write_operator_summary(
            run_root,
            run_id=str(out.get("run_id") or ""),
            trade_date=str(out.get("trade_date") or ""),
            mode=str(out.get("mode") or ""),
            pretrade_halt_reason=out["halt_reason"],
            executor_completed=True,
        )
        write_early_halt_audit(
            run_id=str(out.get("run_id") or latest.get("run_id") or "UNKNOWN"),
            trade_date=str(out.get("trade_date") or latest.get("trade_date") or "UNKNOWN"),
            mode=str(out.get("mode") or latest.get("mode") or "UNKNOWN"),
            halt_reason=out["halt_reason"],
            stage="engine_run_no_artifacts",
        )
        return out

    results_path = _results_path(run_root)
    if results_path.exists() and not force_resubmit:
        existing = _load_json(results_path)
        if int(existing.get("submitted_count") or 0) > 0:
            logger.info("[EXECUTION] idempotency guard skip run_id=%s", existing.get("run_id"))
            print(
                "[EXECUTION_SUMMARY] "
                f"run_id={existing.get('run_id','')} submitted={existing.get('submitted_count',0)} "
                f"accepted={existing.get('accepted_count',0)} rejected={existing.get('rejected_count',0)} "
                f"status={STATUS_SKIPPED_DUPLICATE}"
            )
            # Update operator summary
            write_operator_summary(
                run_root,
                run_id=str(existing.get("run_id") or ""),
                trade_date=str(existing.get("trade_date") or ""),
                mode=str(existing.get("mode") or ""),
                skipped_duplicate=True,
                executor_completed=True,
            )
            _write_execution_step_summary(
                run_root=run_root,
                run_id=str(existing.get("run_id") or ""),
                trade_date=str(existing.get("trade_date") or ""),
                mode=str(existing.get("mode") or ""),
                pretrade_status=str(existing.get("status") or STATUS_READY),
                proposed_count=0,
                executable_count=0,
                execution_status=STATUS_SKIPPED_DUPLICATE,
                submitted_count=int(existing.get("submitted_count") or 0),
                accepted_count=int(existing.get("accepted_count") or 0),
                rejected_count=int(existing.get("rejected_count") or 0),
                duplicate_skip_reason="existing execution_results.json with submitted_count > 0",
            )
            return existing

    payload_path = run_root / "execution_payload.json"
    if not payload_path.exists():
        out = _new_results(latest)
        out["status"] = STATUS_HALTED
        out["halt_reason"] = f"MISSING_EXECUTION_PAYLOAD:{payload_path}"
        _write_json(results_path, out)
        print(
            "[EXECUTION_SUMMARY] "
            f"run_id={out.get('run_id','')} submitted=0 accepted=0 rejected=0 status={STATUS_HALTED}"
        )
        logger.error(
            "[EXEC_DECISION] HALTED — execution_payload.json not found at %s. "
            "This means engine_run either failed before writing the payload, or the artifact "
            "was not restored into the expected path. Check engine_run job logs.",
            payload_path,
        )
        # Update operator summary
        write_operator_summary(
            run_root,
            run_id=str(out.get("run_id") or ""),
            trade_date=str(out.get("trade_date") or ""),
            mode=str(out.get("mode") or ""),
            pretrade_halt_reason=out["halt_reason"],
            executor_completed=True,
        )
        _write_execution_step_summary(
            run_root=run_root,
            run_id=str(out.get("run_id") or ""),
            trade_date=str(out.get("trade_date") or ""),
            mode=str(out.get("mode") or ""),
            pretrade_status=STATUS_HALTED,
            proposed_count=0,
            executable_count=0,
            execution_status=STATUS_HALTED,
            submitted_count=0,
            accepted_count=0,
            rejected_count=0,
            duplicate_skip_reason=None,
        )
        write_early_halt_audit(
            run_id=str(out.get("run_id") or latest.get("run_id") or "UNKNOWN"),
            trade_date=str(out.get("trade_date") or latest.get("trade_date") or "UNKNOWN"),
            mode=str(out.get("mode") or latest.get("mode") or "UNKNOWN"),
            halt_reason=out["halt_reason"],
            stage="missing_execution_payload",
        )
        return out

    payload = _load_json(payload_path)
    out = _new_results(latest, payload)

    valid, reason = _validate_payload(payload)
    if not valid:
        out["status"] = STATUS_HALTED
        out["halt_reason"] = reason
        _write_json(results_path, out)
        print(
            "[EXECUTION_SUMMARY] "
            f"run_id={out.get('run_id','')} submitted=0 accepted=0 rejected=0 status={STATUS_HALTED}"
        )
        # Update operator summary
        write_operator_summary(
            run_root,
            run_id=str(out.get("run_id") or ""),
            trade_date=str(out.get("trade_date") or ""),
            mode=str(out.get("mode") or ""),
            pretrade_halt_reason=reason,
            executor_completed=True,
        )
        _write_execution_step_summary(
            run_root=run_root,
            run_id=str(out.get("run_id") or ""),
            trade_date=str(out.get("trade_date") or ""),
            mode=str(out.get("mode") or ""),
            pretrade_status=STATUS_HALTED,
            proposed_count=0,
            executable_count=0,
            execution_status=STATUS_HALTED,
            submitted_count=0,
            accepted_count=0,
            rejected_count=0,
            duplicate_skip_reason=None,
        )
        return out

    # Accept both 'status' (canonical) and 'execution_status' (legacy)
    execution_status = str(payload.get("status") or payload.get("execution_status") or "").upper()
    if execution_status != STATUS_READY.upper():
        out["status"] = STATUS_HALTED
        out["halt_reason"] = str(payload.get("halt_reason") or f"status_not_ready:{execution_status}")
        _write_json(results_path, out)
        print(
            "[EXECUTION_SUMMARY] "
            f"run_id={out.get('run_id','')} submitted=0 accepted=0 rejected=0 status={STATUS_HALTED}"
        )
        logger.error(
            "[EXEC_DECISION] HALTED — payload status is %s (expected READY). "
            "halt_reason=%s trades_in_payload=%d. "
            "This is the gate that blocks order submission when the planner marked the run as non-executable. "
            "Check engine_run logs for [EXEC_DECISION] to see which gate caused this.",
            execution_status,
            out["halt_reason"],
            len(payload.get("trades") or []),
        )
        # Update operator summary
        write_operator_summary(
            run_root,
            run_id=str(out.get("run_id") or ""),
            trade_date=str(out.get("trade_date") or ""),
            mode=str(out.get("mode") or ""),
            pretrade_halt_reason=out["halt_reason"],
            executor_completed=True,
        )
        _write_execution_step_summary(
            run_root=run_root,
            run_id=str(out.get("run_id") or ""),
            trade_date=str(out.get("trade_date") or ""),
            mode=str(out.get("mode") or ""),
            pretrade_status=execution_status,
            proposed_count=len(payload.get("trades", [])),
            executable_count=int(payload.get("executable_trades_count") or len(payload.get("trades", []))),
            execution_status=STATUS_HALTED,
            submitted_count=0,
            accepted_count=0,
            rejected_count=0,
            duplicate_skip_reason=None,
        )
        write_early_halt_audit(
            run_id=str(out.get("run_id") or "UNKNOWN"),
            trade_date=str(out.get("trade_date") or "UNKNOWN"),
            mode=str(out.get("mode") or "UNKNOWN"),
            halt_reason=out["halt_reason"],
            stage=f"status_not_ready:{execution_status}",
        )
        return out

    mode = str(payload.get("mode") or "").upper()
    if not _is_submit_mode(mode):
        out["status"] = STATUS_HALTED
        out["halt_reason"] = f"mode_not_submit_enabled:{mode}"
        _write_json(results_path, out)
        print(
            "[EXECUTION_SUMMARY] "
            f"run_id={out.get('run_id','')} submitted=0 accepted=0 rejected=0 status={STATUS_HALTED}"
        )
        # Update operator summary
        write_operator_summary(
            run_root,
            run_id=str(out.get("run_id") or ""),
            trade_date=str(out.get("trade_date") or ""),
            mode=mode,
            pretrade_halt_reason=out["halt_reason"],
            executor_completed=True,
        )
        _write_execution_step_summary(
            run_root=run_root,
            run_id=str(out.get("run_id") or ""),
            trade_date=str(out.get("trade_date") or ""),
            mode=mode,
            pretrade_status=execution_status,
            proposed_count=len(payload.get("trades", [])),
            executable_count=int(payload.get("executable_trades_count") or len(payload.get("trades", []))),
            execution_status=STATUS_HALTED,
            submitted_count=0,
            accepted_count=0,
            rejected_count=0,
            duplicate_skip_reason=None,
        )
        return out

    broker = AlpacaBroker.from_env()
    summary = _submit_orders(payload, broker, run_root)
    out.update(summary)
    _n_submitted = int(out.get("submitted_count") or 0)
    _n_duplicates = int(out.get("duplicate_count") or 0)
    _n_rejected = int(out.get("rejected_count") or 0)
    if _n_submitted > 0:
        out["status"] = STATUS_EXECUTED
    elif _n_duplicates > 0:
        # All work was already submitted in a prior attempt; no new submissions this run.
        out["status"] = STATUS_IDEMPOTENT_REPLAY
    else:
        out["status"] = STATUS_NO_ACTION
    _write_json(results_path, out)

    print(
        "[EXECUTION_SUMMARY] "
        f"run_id={out.get('run_id','')} submitted={out.get('submitted_count',0)} "
        f"accepted={out.get('accepted_count',0)} rejected={out.get('rejected_count',0)} "
        f"status={out.get('status','UNKNOWN')}"
    )
    
    # Update operator summary
    write_operator_summary(
        run_root,
        run_id=str(out.get("run_id") or ""),
        trade_date=str(out.get("trade_date") or ""),
        mode=mode,
        submitted_count=int(out.get("submitted_count", 0)),
        accepted_count=int(out.get("accepted_count", 0)),
        rejected_count=int(out.get("rejected_count", 0)),
        executor_completed=True,
    )
    
    # Write operator summary log
    op_summary = {
        "run_id": out.get("run_id"),
        "pretrade_status": execution_status,
        "executable_trades_count": len(payload.get("trades", [])),
        "submitted_count": out.get("submitted_count", 0),
        "accepted_count": out.get("accepted_count", 0),
        "rejected_count": out.get("rejected_count", 0),
        "skipped_duplicate": False,
        "confirmation_email_sent": False,
    }
    print(format_operator_summary_log(op_summary))
    _write_execution_step_summary(
        run_root=run_root,
        run_id=str(out.get("run_id") or ""),
        trade_date=str(out.get("trade_date") or ""),
        mode=mode,
        pretrade_status=execution_status,
        proposed_count=len(payload.get("trades", [])),
        executable_count=int(payload.get("executable_trades_count") or len(payload.get("trades", []))),
        execution_status=str(out.get("status") or STATUS_NO_ACTION),
        submitted_count=int(out.get("submitted_count") or 0),
        accepted_count=int(out.get("accepted_count") or 0),
        rejected_count=int(out.get("rejected_count") or 0),
        duplicate_skip_reason=None,
    )

    # Fetch post-execution broker state for audit
    _broker_cash_after = None
    _broker_positions_after = None
    try:
        account = broker.get_account()
        _broker_cash_after = float(account.get("cash") or account.get("buying_power") or 0) or None
        _broker_positions_after = broker.get_positions()
    except Exception:
        pass

    write_executor_audit(
        run_id=str(out.get("run_id") or "UNKNOWN"),
        pretrade_status=execution_status,
        halt_reason=out.get("halt_reason"),
        submitted_count=int(out.get("submitted_count") or 0),
        accepted_count=int(out.get("accepted_count") or 0),
        rejected_count=int(out.get("rejected_count") or 0),
        duplicate_count=int(out.get("duplicate_count") or 0),
        broker_responses=out.get("broker_responses"),
        broker_cash_after=_broker_cash_after,
        broker_positions_after=_broker_positions_after,
        execution_status=str(out.get("status") or STATUS_NO_ACTION),
    )

    return out


def _parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Submit canonical execution payload orders to Alpaca")
    parser.add_argument(
        "--force-resubmit",
        action="store_true",
        help="Bypass run-level idempotency guard and allow resubmission for the same run_id",
    )
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_args(argv)
    run_execution(force_resubmit=bool(args.force_resubmit))


if __name__ == "__main__":
    main()

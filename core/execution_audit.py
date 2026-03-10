"""
Execution audit artifact writer.

Writes a single deterministic JSON artifact per run that lets an operator
answer within 60 seconds:
  - Did we have trades to place?
  - If not, why not?
  - If yes, were they blocked or submitted?
  - Why is cash still high?

Written non-blocking (never raises). Designed to be called from both
daily_quant_report.py (planner layer) and execute_alpaca_orders.py
(executor layer) so the artifact accumulates facts across the two-job
boundary.

Output path: outputs/execution_audit/{run_id}.json
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_AUDIT_DIR = Path("outputs/execution_audit")


def safe_artifact_name(value: str) -> str:
    """
    Return a filesystem-safe, artifact-safe filename component.

    Keeps alphanumerics, dots, dashes, and underscores.
    Replaces all other characters (including colons, slashes, angle brackets,
    pipes, asterisks, question marks, backslashes) with underscores.
    Collapses consecutive underscores into one.
    Strips leading/trailing underscores.
    Returns 'unnamed' if the result is empty.

    The raw original value is NOT modified — this function is only used for
    path/file naming. Original identifiers are preserved inside JSON payloads.
    """
    safe = re.sub(r"[^\w.\-]", "_", str(value))   # keep alnum, _, ., -
    safe = re.sub(r"_+", "_", safe)                # collapse repeated underscores
    safe = safe.strip("_")
    return safe if safe else "unnamed"


def _audit_path(run_id: str) -> Path:
    return _AUDIT_DIR / f"{safe_artifact_name(run_id)}.json"


def _load_existing(run_id: str) -> dict:
    path = _audit_path(run_id)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _write(run_id: str, data: dict) -> Path:
    _AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    path = _audit_path(run_id)
    path.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def write_planner_audit(
    *,
    run_id: str,
    trade_date: str,
    mode: str,
    # Gate evaluations
    today_et: str,
    is_planning_run: bool,
    market_is_open: bool,
    should_execute: bool,
    market_guard: dict | None = None,
    # Broker state before execution
    broker_cash_before: float | None = None,
    broker_positions_before: list | None = None,
    canonical_positions_before: list | None = None,
    # Signal/target state
    signals_generated: int | None = None,
    target_positions: list | None = None,
    proposed_trades: list | None = None,
    blocked_reasons: list | None = None,
    # Execution payload
    execution_payload: dict | None = None,
    # Additional context
    extra: dict | None = None,
) -> Path | None:
    """
    Write (or update) the planner-layer section of the execution audit.

    Call this from daily_quant_report.py after should_execute is determined
    and after execution_payload is built.
    """
    try:
        payload = execution_payload or {}
        trades = payload.get("trades") or []
        buy_orders = [t for t in trades if str(t.get("side", "")).upper() == "BUY"]
        sell_orders = [t for t in trades if str(t.get("side", "")).upper() in {"SELL", "CLOSE", "REDUCE"}]

        gate_results = {
            "today_et": today_et,
            "trade_date": trade_date,
            "is_planning_run": is_planning_run,
            "is_planning_run_reason": (
                f"trade_date={trade_date} != today_et={today_et}" if is_planning_run else "trade_date matches today"
            ),
            "market_is_open": market_is_open,
            "market_guard_status": (market_guard or {}).get("status", "UNKNOWN"),
            "market_guard_reason": (market_guard or {}).get("reason", "UNKNOWN"),
            "should_execute": should_execute,
            "should_execute_verdict": "PASS" if should_execute else "FAIL",
        }

        cash_analysis = None
        if broker_cash_before is not None and payload.get("equity"):
            equity = float(payload.get("equity") or 0)
            if equity > 0:
                cash_pct = broker_cash_before / equity * 100
                target_pct = (payload.get("cash_target_weight") or 0) * 100
                cash_analysis = {
                    "broker_cash_before": broker_cash_before,
                    "broker_cash_pct": round(cash_pct, 2),
                    "target_cash_pct": round(target_pct, 2),
                    "cash_above_target_pct": round(cash_pct - target_pct, 2),
                    "deployable_dollars": round(max(0, broker_cash_before - equity * (target_pct / 100)), 2),
                }

        audit: dict[str, Any] = {
            "run_id": run_id,
            "trade_date": trade_date,
            "mode": mode,
            "written_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "planner": {
                "gates": gate_results,
                "execution_status": payload.get("execution_status", "UNKNOWN"),
                "halt_reason": payload.get("halt_reason"),
                "validation_reason": payload.get("validation_reason"),
                "signals_generated": signals_generated,
                "proposed_trades_count": len(proposed_trades) if proposed_trades is not None else None,
                "executable_trades_count": int(payload.get("executable_trades_count") or 0),
                "buy_orders_count": len(buy_orders),
                "sell_orders_count": len(sell_orders),
                "buy_orders": [
                    {"ticker": t.get("ticker"), "shares": t.get("shares"), "notional": t.get("notional")}
                    for t in buy_orders
                ],
                "sell_orders": [
                    {"ticker": t.get("ticker"), "shares": t.get("shares"), "notional": t.get("notional")}
                    for t in sell_orders
                ],
                "blocked_reasons": blocked_reasons or [],
                "target_positions": target_positions,
                "cash_analysis": cash_analysis,
            },
            "broker_state_before": {
                "cash": broker_cash_before,
                "positions": broker_positions_before,
            },
            "canonical_state_before": {
                "positions": canonical_positions_before,
            },
            "executor": None,  # filled by write_executor_audit
        }

        if extra:
            audit["extra"] = extra

        path = _write(run_id, audit)
        logger.info("[EXEC_AUDIT] planner audit written: %s", path)
        return path

    except Exception as exc:
        logger.warning("[EXEC_AUDIT] failed to write planner audit: %s", exc)
        return None


def write_executor_audit(
    *,
    run_id: str,
    pretrade_status: str,
    halt_reason: str | None,
    submitted_count: int,
    accepted_count: int,
    rejected_count: int,
    duplicate_count: int = 0,
    broker_responses: list | None = None,
    broker_cash_after: float | None = None,
    broker_positions_after: list | None = None,
    execution_status: str = "UNKNOWN",
) -> Path | None:
    """
    Write (or update) the executor-layer section of the execution audit.

    Call this from execute_alpaca_orders.py after orders are submitted
    (or after a halt is determined). Merges with existing planner audit if present.
    """
    try:
        existing = _load_existing(run_id)

        executor_block: dict[str, Any] = {
            "written_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "pretrade_status": pretrade_status,
            "halt_reason": halt_reason,
            "execution_status": execution_status,
            "submitted_count": submitted_count,
            "accepted_count": accepted_count,
            "rejected_count": rejected_count,
            "duplicate_count": duplicate_count,
            "broker_responses_summary": [
                {
                    "ticker": r.get("ticker"),
                    "side": r.get("side"),
                    "shares": r.get("shares"),
                    "status": r.get("status"),
                    "reason": r.get("reason") or r.get("error"),
                }
                for r in (broker_responses or [])
            ],
            "broker_state_after": {
                "cash": broker_cash_after,
                "positions": broker_positions_after,
            },
        }

        existing["executor"] = executor_block
        existing.setdefault("run_id", run_id)

        # Top-level verdict for operator 60-second read
        existing["verdict"] = _compute_verdict(existing)

        path = _write(run_id, existing)
        logger.info("[EXEC_AUDIT] executor audit written: %s", path)
        return path

    except Exception as exc:
        logger.warning("[EXEC_AUDIT] failed to write executor audit: %s", exc)
        return None


def write_early_halt_audit(
    *,
    run_id: str,
    trade_date: str,
    mode: str,
    halt_reason: str,
    stage: str,
) -> Path | None:
    """
    Write a minimal audit record when execution halts before the planner completes
    (e.g., missing execution_payload.json, failed payload validation).

    Call this from execute_alpaca_orders.py when halting before any submission attempt.
    """
    try:
        existing = _load_existing(run_id)
        existing.setdefault("run_id", run_id)
        existing.setdefault("trade_date", trade_date)
        existing.setdefault("mode", mode)
        existing["executor"] = {
            "written_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "pretrade_status": "HALTED",
            "halt_reason": halt_reason,
            "halt_stage": stage,
            "execution_status": "HALTED",
            "submitted_count": 0,
            "accepted_count": 0,
            "rejected_count": 0,
        }
        existing["verdict"] = _compute_verdict(existing)
        path = _write(run_id, existing)
        logger.info("[EXEC_AUDIT] early halt audit written: %s", path)
        return path
    except Exception as exc:
        logger.warning("[EXEC_AUDIT] failed to write early halt audit: %s", exc)
        return None


def _compute_verdict(audit: dict) -> dict:
    """
    Compute a top-level operator-readable verdict.

    Answers:
      - trades_proposed: yes/no/unknown
      - execution_attempted: yes/no
      - orders_submitted: N
      - cash_explanation: why cash was not deployed (if applicable)
    """
    planner = audit.get("planner") or {}
    executor = audit.get("executor") or {}

    trades_proposed = int(planner.get("executable_trades_count") or 0) > 0
    gates = planner.get("gates") or {}
    submitted = int(executor.get("submitted_count") or 0)
    accepted = int(executor.get("accepted_count") or 0)

    executor_status = executor.get("execution_status", "UNKNOWN")
    is_idempotent_replay = executor_status == "IDEMPOTENT_REPLAY"
    _duplicate_count = int(executor.get("duplicate_count") or 0)
    # Idempotent replay means work was attempted and orders existed from a prior run.
    execution_attempted = executor_status not in {None, "HALTED", "NO_ACTION"} or is_idempotent_replay

    # When this is an idempotent replay, the duplicate_count is reliable evidence
    # that trades existed and were previously submitted, even if the planner block
    # is absent (executor-only retry job has no planner section).
    if is_idempotent_replay and not trades_proposed and _duplicate_count > 0:
        trades_proposed = True

    cash_not_deployed_reason = None
    # Executor halt takes precedence — if we never got to submission, say why.
    # Idempotent replay is checked before the generic no-trades fallback so
    # it does not inherit "no_executable_trades_after_constraints".
    if executor.get("halt_reason"):
        cash_not_deployed_reason = f"executor_halt: {executor['halt_reason']}"
    elif "should_execute" in gates and not gates["should_execute"]:
        if gates.get("is_planning_run"):
            cash_not_deployed_reason = f"planning_run: trade_date {audit.get('trade_date')} is not today ({gates.get('today_et')})"
        elif not gates.get("market_is_open"):
            cash_not_deployed_reason = f"market_not_open: {gates.get('market_guard_reason', 'UNKNOWN')}"
        else:
            cash_not_deployed_reason = "should_execute=False: unknown gate"
    elif is_idempotent_replay:
        cash_not_deployed_reason = "orders_already_submitted_prior_run"
    elif not trades_proposed:
        cash_not_deployed_reason = "no_executable_trades_after_constraints"
    elif submitted == 0 and trades_proposed:
        cash_not_deployed_reason = "trades_proposed_but_not_submitted: check executor stage"

    # proposed_count: prefer planner data; fall back to duplicate_count for replay
    # runs where the planner block is absent.
    _planner_proposed = int(planner.get("executable_trades_count") or 0)
    proposed_count = _planner_proposed if _planner_proposed > 0 else (
        _duplicate_count if is_idempotent_replay else 0
    )

    return {
        "trades_proposed": trades_proposed,
        "proposed_count": proposed_count,
        "execution_attempted": execution_attempted,
        "orders_submitted": submitted,
        "orders_accepted": accepted,
        "cash_not_deployed_reason": cash_not_deployed_reason,
        "planner_status": planner.get("execution_status", "UNKNOWN"),
        "executor_status": executor_status,
        "is_idempotent_replay": is_idempotent_replay,
        "executor_halt_reason": executor.get("halt_reason"),
        "executor_halt_stage": executor.get("halt_stage"),
        "buy_orders": planner.get("buy_orders_count", 0),
        "sell_orders": planner.get("sell_orders_count", 0),
        "blocked_reasons": planner.get("blocked_reasons") or [],
    }

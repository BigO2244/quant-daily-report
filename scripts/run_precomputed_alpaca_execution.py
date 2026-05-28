from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Load .env explicitly so Alpaca credentials are available regardless of how
# this module is invoked (cron, SSH one-liner, direct python call).
_env_path = _REPO_ROOT / ".env"
if _env_path.exists():
    with open(_env_path, "r", encoding="utf-8") as _env_file:
        for _line in _env_file:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _key, _, _value = _line.partition("=")
            _value = _value.strip().strip('"').strip("'")
            os.environ.setdefault(_key.strip(), _value)

import pandas as pd

from brokers.alpaca_broker import (
    CASH_REBALANCE_INCOMPLETE,
    EXECUTION_OUTCOME_PARTIAL_BROKER_ABORT,
    EXECUTION_OUTCOME_POST_SUBMIT_ARTIFACT_FAILURE,
)
from brokers.alpaca_snapshot import (
    fetch_pretrade_snapshot,
    summarize_pretrade_broker_policy,
    write_pretrade_snapshot_artifacts,
)
from core.execution_audit import write_executor_audit, write_planner_audit
from core.execution_integrity import write_execution_integrity_audit
from core.execution_lifecycle_timeline import write_execution_lifecycle_timeline
from core.execution_payload import normalize_status, write_canonical_execution_payload
from core.execution_summary import write_execution_artifacts
from core.live_retry_policy import evaluate_live_retry
from core.operator_summary import (
    format_execution_health_banner,
    format_operator_summary_log,
    load_operator_summary,
    write_operator_summary,
)
from core.precompute_contract import load_precompute_inputs
from core.run_pointer import write_latest_run_pointer, write_trade_stage_pointer
from core.timing_policy import classify_timing, current_et
from core.trading_day_summary import write_trading_day_summary
from core.trading_mode import canonical_trading_mode_label
from paper.build_execution_email import build_execution_email_text
from paper.paper_broker import run_paper_day
from paper.run_manager import ensure_dir, get_run_dir, get_run_id, safe_write_text
from paper.state_paths import ensure_paper_state_files
from reconciliation import ensure_sent_ledger_exists, pre_trade_reconcile_and_classify

import daily_quant_report as dqr


logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Execute Alpaca orders from precomputed daily bundle")
    parser.add_argument("--retry-attempt", type=int, default=0)
    parser.add_argument(
        "--continuation-mode",
        choices=("none", "buy_only"),
        default="none",
        help="Allow buy-only continuation after a non-trading partial failure.",
    )
    parser.add_argument(
        "--continuation-intended-orders-path",
        default="",
        help=(
            "Optional broker/intended_orders_<DATE>.json source for explicit "
            "buy-only continuation recovery. Only BUY rows are hydrated."
        ),
    )
    return parser.parse_args(argv)


def _trade_date() -> str:
    report_date = str(os.getenv("REPORT_DATE", "")).strip()
    if report_date:
        return report_date
    return current_et().strftime("%Y-%m-%d")


def _execution_mode_label() -> str:
    mode_value = str(os.getenv("TRADING_MODE", "")).strip() or str(os.getenv("MODE", "")).strip() or "paper"
    return canonical_trading_mode_label(mode_value, field_name="TRADING_MODE")


def _workflow_start_utc() -> dt.datetime | None:
    raw = str(os.getenv("WORKFLOW_STARTED_AT_UTC", "")).strip()
    if not raw:
        return None
    try:
        return dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _operator_summary_timing_kwargs(timing: dict[str, object]) -> dict[str, str]:
    return {
        "timing_status": str(timing.get("timing_status") or ""),
        "preferred_target_et": str(timing.get("preferred_target_et") or ""),
        "degraded_auto_trade_deadline_et": str(timing.get("degraded_auto_trade_deadline_et") or ""),
        "actual_workflow_start_et": str(timing.get("actual_workflow_start_et") or ""),
        "actual_execution_start_et": str(timing.get("actual_execution_start_et") or ""),
        "first_submit_et": str(timing.get("first_submit_et") or ""),
    }


def _coerce_float(value: object) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except Exception:
        return None


def _env_truthy(name: str) -> bool:
    return str(os.getenv(name, "")).strip().lower() in {"1", "true", "yes", "y", "on"}


def _read_last_ledger_equity(ledger_path: str) -> float | None:
    try:
        ledger = pd.read_csv(ledger_path)
    except Exception:
        return None
    if ledger.empty or "total_equity" not in ledger.columns:
        return None
    values = pd.to_numeric(ledger["total_equity"], errors="coerce").dropna()
    if values.empty:
        return None
    return float(values.iloc[-1])


def _load_signals_payload(path: str) -> dict[str, object]:
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Signals payload must be a JSON object: {path}")
    return payload


def _write_risk_adjusted_signals(
    *,
    run_root: Path,
    trade_date: str,
    original_payload: dict[str, object],
    adjusted_weights: pd.DataFrame,
    cash_target_weight: float,
) -> Path:
    payload = dict(original_payload)
    meta = dict(payload.get("meta") or {})
    meta["trade_date"] = trade_date
    meta["risk_controls_applied"] = True
    meta["risk_controls_generated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    payload["meta"] = meta
    payload["snapshot_date"] = trade_date
    payload["cash_target_weight"] = float(cash_target_weight)
    payload["signals"] = (
        adjusted_weights[["ticker", "sleeve", "target_weight"]]
        .assign(
            ticker=lambda df: df["ticker"].astype(str).str.upper(),
            sleeve=lambda df: df["sleeve"].astype(str),
            target_weight=lambda df: df["target_weight"].astype(float),
        )
        .to_dict(orient="records")
    )

    out_path = run_root / "snapshots" / f"risk_adjusted_{trade_date}.json"
    safe_write_text(
        out_path,
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        allow_overwrite=True,
    )
    return out_path


def _apply_pre_execution_risk_controls(
    *,
    run_root: Path,
    trade_date: str,
    snapshot: dict[str, object],
    pretrade_policy: dict[str, object],
    ledger_path: str,
) -> tuple[str, float]:
    from core.risk_controls import (
        RiskControls,
        load_sector_map,
        peak_equity_path,
        update_peak_equity_state,
    )
    from paper.paper_broker import load_targets

    signals_path = str(snapshot.get("signals_snapshot_path") or "").strip()
    if not signals_path:
        raise ValueError("daily snapshot missing signals_snapshot_path")

    cash_target_weight_default = float(snapshot.get("target_cash_weight") or 0.0)
    original_payload = _load_signals_payload(signals_path)
    targets, cash_target_weight, _, _ = load_targets(
        signals_path,
        cash_target_weight_default=cash_target_weight_default,
    )

    current_equity = (
        _coerce_float(pretrade_policy.get("broker_preflight_equity"))
        or _read_last_ledger_equity(ledger_path)
        or 0.0
    )
    equity_source = (
        "broker_preflight_equity"
        if _coerce_float(pretrade_policy.get("broker_preflight_equity")) is not None
        else "ledger"
    )
    peak_state = update_peak_equity_state(
        current_equity=current_equity,
        trade_date=trade_date,
        source=equity_source,
        path=peak_equity_path(),
    )

    controls = RiskControls()
    result = controls.apply_to_targets(
        targets,
        sector_map=load_sector_map(),
        cash_target_weight=cash_target_weight,
        current_equity=current_equity,
        peak_equity=_coerce_float(peak_state.get("peak_equity")),
    )

    adjusted_signals_path = _write_risk_adjusted_signals(
        run_root=run_root,
        trade_date=trade_date,
        original_payload=original_payload,
        adjusted_weights=result.weights,
        cash_target_weight=result.cash_target_weight,
    )

    artifact = {
        "trade_date": trade_date,
        "original_signals_path": signals_path,
        "adjusted_signals_path": str(adjusted_signals_path),
        "peak_equity_path": str(peak_equity_path()),
        "peak_equity_state": peak_state,
        "result": result.to_artifact(),
    }
    safe_write_text(
        run_root / "audit" / "risk_controls.json",
        json.dumps(artifact, indent=2, sort_keys=True) + "\n",
        allow_overwrite=True,
    )

    if result.actions:
        logger.warning(
            "[RISK_CONTROLS] adjusted targets before execution: actions=%d cash_target_weight=%.2f",
            len(result.actions),
            result.cash_target_weight,
        )
    else:
        logger.info(
            "[RISK_CONTROLS] no target adjustments required; cash_target_weight=%.2f",
            result.cash_target_weight,
        )

    snapshot["signals_snapshot_path"] = str(adjusted_signals_path)
    snapshot["target_cash_weight"] = float(result.cash_target_weight)
    snapshot["risk_controls"] = artifact["result"]
    return str(adjusted_signals_path), float(result.cash_target_weight)


def _write_execution_run_pointers(
    *,
    run_id: str,
    trade_date: str,
    run_root: Path,
    status: str,
    substatus: str | None = None,
    status_message: str | None = None,
) -> None:
    mode_label = _execution_mode_label()
    latest_pointer_kwargs = {
        "run_id": run_id,
        "trade_date": trade_date,
        "mode": mode_label,
        "run_root": str(run_root),
        "status": status,
        "substatus": substatus,
        "status_message": status_message,
        "workflow_stage": "execution",
    }
    write_latest_run_pointer(**latest_pointer_kwargs)
    write_trade_stage_pointer(
        stage="execution",
        run_id=run_id,
        trade_date=trade_date,
        mode=mode_label,
        run_root=str(run_root),
        status=status,
        substatus=substatus,
        status_message=status_message,
    )


def _init_run_root(run_root: Path, trade_date: str, run_id: str) -> None:
    mode_label = _execution_mode_label()
    for subdir in ("logs", "reports", "broker", "ledger", "snapshots", "audit"):
        ensure_dir(run_root / subdir)
    _write_execution_run_pointers(
        run_id=run_id,
        trade_date=trade_date,
        run_root=run_root,
        status="running",
    )
    safe_write_text(
        run_root / "meta.json",
        json.dumps(
            {
                "run_id": run_id,
                "trade_date": trade_date,
                "mode": mode_label,
                "run_root": str(run_root),
                "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            },
            indent=2,
        )
        + "\n",
        allow_overwrite=True,
    )


def _first_submit_et(paper_summary: dict[str, object]) -> dt.datetime | None:
    submissions = list((paper_summary or {}).get("alpaca_submissions") or [])
    for item in submissions:
        raw = str((item or {}).get("submitted_at") or "").strip()
        if not raw:
            continue
        try:
            return dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(ET)
        except Exception:
            continue
    return None


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value or 0)
    except Exception:
        return int(default)


def _safe_float(value: object, default: float | None = None) -> float | None:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _expected_planned_trade_count(planned_payload: dict[str, object]) -> int | None:
    for key in (
        "trades_count",
        "trade_count",
        "executable_trades_count",
        "execution_eligible_trades_count",
    ):
        if planned_payload.get(key) is None:
            continue
        return _safe_int(planned_payload.get(key), default=-1)
    return None


def _validate_exact_planned_payload(
    planned_payload: dict[str, object] | None,
    *,
    trade_date: str,
) -> list[dict[str, object]]:
    if not isinstance(planned_payload, dict):
        raise RuntimeError("planned_execution_payload_missing_or_malformed")

    payload_trade_date = str(planned_payload.get("trade_date") or "").strip()
    if payload_trade_date != str(trade_date):
        raise RuntimeError(
            f"planned_execution_payload_trade_date_mismatch payload_trade_date={payload_trade_date or 'missing'} trade_date={trade_date}"
        )

    payload_status = str(
        planned_payload.get("execution_status") or planned_payload.get("status") or ""
    ).strip().upper()
    if payload_status != "PLANNED":
        raise RuntimeError(
            f"planned_execution_payload_status_not_planned status={payload_status or 'missing'}"
        )

    trades = planned_payload.get("trades")
    if not isinstance(trades, list):
        raise RuntimeError("planned_execution_payload_trades_missing_or_malformed")

    expected_count = _expected_planned_trade_count(planned_payload)
    if expected_count is not None and expected_count != len(trades):
        raise RuntimeError(
            f"planned_execution_payload_trades_count_mismatch expected={expected_count} actual={len(trades)}"
        )

    normalized: list[dict[str, object]] = []
    for idx, trade in enumerate(trades):
        if not isinstance(trade, dict):
            raise RuntimeError(f"planned_execution_payload_trade_malformed index={idx}")
        ticker = str(trade.get("ticker") or "").strip().upper()
        side = str(trade.get("side") or "").strip().upper()
        shares = _safe_float(trade.get("shares", trade.get("quantity")), 0.0) or 0.0
        price = _safe_float(trade.get("price", trade.get("entry_price")), 0.0) or 0.0
        notional = _safe_float(trade.get("notional"), 0.0) or 0.0
        if not ticker or side not in {"BUY", "SELL", "CLOSE", "REDUCE"} or shares <= 0.0:
            raise RuntimeError(f"planned_execution_payload_trade_invalid index={idx} ticker={ticker or 'missing'} side={side or 'missing'} shares={shares}")
        if price <= 0.0 and notional <= 0.0:
            raise RuntimeError(f"planned_execution_payload_trade_missing_price index={idx} ticker={ticker}")
        normalized.append(dict(trade))
    return normalized


def _planned_payload_provenance(
    *,
    planned_payload: dict[str, object] | None,
    exact_precomputed_execution: bool,
) -> dict[str, object]:
    if exact_precomputed_execution:
        planning_price_basis = str((planned_payload or {}).get("pricing_source") or "PREV_CLOSE").strip() or "PREV_CLOSE"
        return {
            "execution_source": "planned_payload_exact",
            "planning_price_basis": planning_price_basis,
            "pricing_asof": (planned_payload or {}).get("pricing_asof"),
            "execution_price_requirement": "PRECOMPUTE_VALIDATED",
            "price_freshness_scope": "precompute_bundle",
        }
    return {
        "execution_source": "rebuilt_from_signals",
        "planning_price_basis": None,
        "pricing_asof": None,
        "execution_price_requirement": "SAME_DAY_OPEN_FRESHNESS",
        "price_freshness_scope": "open_market_fetch",
    }


def _pending_buy_orders_from_summary(paper_summary: dict[str, object]) -> list[dict[str, object]]:
    pending: list[dict[str, object]] = []
    for order in list((paper_summary or {}).get("budget_skipped_orders") or []):
        if not isinstance(order, dict):
            continue
        if str(order.get("side") or "").upper() != "BUY":
            continue
        pending.append(dict(order))
    return pending


def _submitted_side_counts(paper_summary: dict[str, object]) -> dict[str, int]:
    counts = {"BUY": 0, "SELL": 0}
    for item in list((paper_summary or {}).get("alpaca_submissions") or []):
        if not isinstance(item, dict):
            continue
        side = str(item.get("side") or "").upper()
        if side in {"BUY", "SELL"}:
            counts[side] += 1
    return counts


def _capital_allows_pending_buys(
    paper_summary: dict[str, object],
    pending_buy_orders: list[dict[str, object]],
) -> bool:
    capital_budget = dict((paper_summary or {}).get("capital_budget") or {})
    if bool(capital_budget.get("capital_constraint_triggered")):
        return False
    if _safe_int(capital_budget.get("clipped_or_deferred_buys_count")) > 0:
        return False

    requested = _safe_float(capital_budget.get("requested_buy_notional"))
    allowed = _safe_float(capital_budget.get("allowed_buy_notional"))
    if requested is None or requested <= 0.0:
        requested = sum(
            abs(_safe_float(order.get("notional"), 0.0) or 0.0)
            for order in pending_buy_orders
        )
    if allowed is None or allowed <= 0.0:
        # Exact precomputed execution can preserve planned buys without mutating
        # a planning-time capital budget. In that case, absence of a constraint
        # is enough to avoid misclassifying the pending buy leg as budget-clipped.
        return True
    return float(requested or 0.0) <= float(allowed) + 1e-6


def _pending_buy_block_reason(paper_summary: dict[str, object]) -> str:
    submission_summary = dict((paper_summary or {}).get("alpaca_submission_summary") or {})
    for key in ("buy_phase_block_reason", "execution_reason"):
        value = str(submission_summary.get(key) or (paper_summary or {}).get(key) or "").strip()
        if value:
            return value
    sell_phase_status = str((paper_summary or {}).get("sell_phase_status") or "").strip()
    if sell_phase_status:
        return f"sell_phase_{sell_phase_status.lower()}"
    return "pending_buy_leg_not_submitted"


def _pending_buy_leg_state(paper_summary: dict[str, object]) -> dict[str, object]:
    pending_buy_orders = _pending_buy_orders_from_summary(paper_summary)
    submission_summary = dict((paper_summary or {}).get("alpaca_submission_summary") or {})
    buy_phase_submitted = _safe_int(submission_summary.get("buy_phase_submitted"))
    capital_allows = _capital_allows_pending_buys(paper_summary, pending_buy_orders)
    reason = _pending_buy_block_reason(paper_summary)
    reason_lower = reason.lower()
    has_broker_reject = bool(
        str((paper_summary or {}).get("broker_reject_status") or "").strip()
        or str(submission_summary.get("broker_reject_status") or "").strip()
        or "broker_reject" in reason_lower
        or "pdt" in reason_lower
    )
    requires_partial = bool(
        pending_buy_orders
        and buy_phase_submitted == 0
        and capital_allows
        and not has_broker_reject
    )
    return {
        "pending_buy_orders": pending_buy_orders,
        "pending_buy_count": int(len(pending_buy_orders)),
        "buy_phase_submitted": buy_phase_submitted,
        "buy_phase_planned": _safe_int(submission_summary.get("buy_phase_planned")),
        "buy_phase_block_reason": reason,
        "capital_allows_pending_buys": bool(capital_allows),
        "has_broker_reject": bool(has_broker_reject),
        "requires_partial": bool(requires_partial),
    }


def _apply_pending_buy_leg_guard(paper_summary: dict[str, object]) -> dict[str, object]:
    state = _pending_buy_leg_state(paper_summary)
    if not bool(state.get("requires_partial")):
        return state

    reason = str(state.get("buy_phase_block_reason") or "pending_buy_leg_not_submitted")
    paper_summary["execution_outcome"] = (
        str((paper_summary or {}).get("execution_outcome") or "").strip()
        or EXECUTION_OUTCOME_PARTIAL_BROKER_ABORT
    )
    paper_summary["execution_reason"] = (
        str((paper_summary or {}).get("execution_reason") or "").strip()
        or reason
    )
    paper_summary["cash_rebalance_status"] = (
        str((paper_summary or {}).get("cash_rebalance_status") or "").strip()
        or CASH_REBALANCE_INCOMPLETE
    )
    paper_summary["halt_reason"] = (
        str((paper_summary or {}).get("halt_reason") or "").strip()
        or f"{paper_summary['execution_outcome']}:{paper_summary['execution_reason']}:{CASH_REBALANCE_INCOMPLETE}"
    )

    submission_summary = dict((paper_summary or {}).get("alpaca_submission_summary") or {})
    submission_summary.setdefault("execution_outcome", paper_summary["execution_outcome"])
    submission_summary.setdefault("execution_reason", paper_summary["execution_reason"])
    submission_summary.setdefault("cash_rebalance_status", paper_summary["cash_rebalance_status"])
    submission_summary.setdefault("halt_remaining_buys", True)
    submission_summary.setdefault("buy_phase_block_reason", reason)
    paper_summary["alpaca_submission_summary"] = submission_summary

    blocked_reasons = list((paper_summary or {}).get("blocked_reasons") or [])
    halt_reason = str(paper_summary.get("halt_reason") or "")
    if halt_reason and halt_reason not in blocked_reasons:
        blocked_reasons.append(halt_reason)
    paper_summary["blocked_reasons"] = blocked_reasons
    return state


def _pending_buy_continuation_eligible(
    *,
    paper_summary: dict[str, object],
    pending_buy_state: dict[str, object],
    submitted_count: int,
    timing: dict[str, object],
) -> bool:
    if int(submitted_count or 0) <= 0:
        return False
    if int(pending_buy_state.get("pending_buy_count") or 0) <= 0:
        return False
    if str((timing or {}).get("timing_status") or "") == "after_deadline":
        return False
    if bool(pending_buy_state.get("has_broker_reject")):
        return False

    outcome = str((paper_summary or {}).get("execution_outcome") or "").strip()
    if outcome == EXECUTION_OUTCOME_POST_SUBMIT_ARTIFACT_FAILURE:
        return True
    if outcome != EXECUTION_OUTCOME_PARTIAL_BROKER_ABORT:
        return False
    if not bool(pending_buy_state.get("capital_allows_pending_buys")):
        return False

    reason = str(pending_buy_state.get("buy_phase_block_reason") or "").lower()
    continuation_reason_allowed = (
        reason.startswith("sell_phase_")
        or reason == "pending_buy_leg_not_submitted"
    )
    return bool(pending_buy_state.get("requires_partial")) and continuation_reason_allowed


def _load_buy_continuation_plan_from_intended_orders(path: str | Path) -> list[dict[str, object]]:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"continuation intended orders artifact not found: {source}")
    payload = json.loads(source.read_text(encoding="utf-8"))
    rows = list((payload or {}).get("orders_intended") or [])
    buys: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("side") or "").upper() != "BUY":
            continue
        ticker = str(row.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        shares = _safe_float(row.get("shares", row.get("quantity")), 0.0) or 0.0
        notional = abs(_safe_float(row.get("notional"), 0.0) or 0.0)
        price = _safe_float(row.get("price"))
        if (price is None or price <= 0.0) and shares > 0.0 and notional > 0.0:
            price = notional / abs(shares)
        buys.append(
            {
                "ticker": ticker,
                "side": "BUY",
                "shares": abs(float(shares)),
                "price": float(price or 0.0),
                "notional": float(notional),
                "reason": row.get("reason") or "buy_only_continuation",
                "order_id": row.get("order_id"),
            }
        )
    if not buys:
        raise RuntimeError(f"continuation intended orders artifact has no BUY rows: {source}")
    return buys


def _operator_execution_status(execution_payload: dict[str, object]) -> str:
    submitted = int(
        execution_payload.get("submitted_count")
        or execution_payload.get("orders_submitted_count")
        or 0
    )
    outcome = str(execution_payload.get("execution_outcome") or "").strip()
    status = str(execution_payload.get("execution_status") or "").upper()
    if submitted > 0 and outcome in {
        EXECUTION_OUTCOME_PARTIAL_BROKER_ABORT,
        EXECUTION_OUTCOME_POST_SUBMIT_ARTIFACT_FAILURE,
    }:
        return "partial"
    if submitted > 0:
        return "executed"
    if status in {"NO_ACTION", "PLANNED"}:
        return "skipped"
    if status == "HALTED":
        return "failed"
    return "skipped"


def _write_execution_email_payload(trade_date: str, payload: dict[str, object]) -> Path:
    out_dir = Path("outputs/execution_email")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{trade_date}.json"
    safe_write_text(
        out_path,
        json.dumps(payload, indent=2, default=str) + "\n",
        allow_overwrite=True,
    )
    return out_path


def _write_execution_results(run_root: Path, payload: dict[str, object], paper_summary: dict[str, object]) -> Path:
    submission_summary = dict((paper_summary or {}).get("alpaca_submission_summary") or {})
    operator_status = str(payload.get("operator_execution_status") or "").strip().lower()
    if operator_status == "partial":
        results_status = "PARTIAL"
    elif operator_status == "failed":
        results_status = "HALTED"
    elif operator_status == "executed":
        results_status = "EXECUTED"
    else:
        results_status = str(payload.get("execution_status") or "NO_ACTION")
    results = {
        "run_id": str(payload.get("run_id") or get_run_id()),
        "trade_date": str(payload.get("trade_date") or _trade_date()),
        "mode": canonical_trading_mode_label(payload.get("mode") or "paper"),
        "status": results_status,
        "halt_reason": payload.get("halt_reason"),
        "submitted_count": int(payload.get("submitted_count") or 0),
        "accepted_count": int(payload.get("accepted_count") or 0),
        "rejected_count": int(payload.get("rejected_count") or 0),
        "duplicate_count": int(submission_summary.get("remote_existing_orders") or 0),
        "broker_responses": list((paper_summary or {}).get("alpaca_submissions") or []),
        "execution_outcome": payload.get("execution_outcome"),
        "execution_reason": payload.get("execution_reason"),
        "cash_rebalance_status": payload.get("cash_rebalance_status"),
        "broker_reject_status": payload.get("broker_reject_status"),
        "broker_reject_message": payload.get("broker_reject_message"),
        "timing_status": payload.get("timing_status"),
        "operator_execution_status": payload.get("operator_execution_status"),
    }
    out_path = run_root / "execution_results.json"
    safe_write_text(out_path, json.dumps(results, indent=2, default=str) + "\n", allow_overwrite=True)
    return out_path


def _write_execution_timeline_artifacts(*, run_root: Path, trade_date: str, run_id: str) -> None:
    try:
        write_execution_lifecycle_timeline(
            run_root=run_root,
            trade_date=trade_date,
            run_id=run_id,
        )
    except Exception as exc:
        logger.warning("[EXECUTION_TIMELINE] timeline artifacts skipped: %s", exc)


def _failure_payload(*, trade_date: str, run_id: str, reason: str, timing: dict[str, object]) -> dict[str, object]:
    workflow_context = _workflow_context()
    payload = {
        "run_id": run_id,
        "trade_date": trade_date,
        "mode": _execution_mode_label(),
        "execution_status": "HALTED",
        "halt_reason": reason,
        "trades": [],
        "execution_outcome": None,
        "execution_reason": reason,
        "cash_rebalance_status": None,
        "submitted_count": 0,
        "accepted_count": 0,
        "rejected_count": 0,
        "orders_submitted_count": 0,
        "orders_filled_count": 0,
        "operator_execution_status": "failed",
        **timing,
        **workflow_context,
    }
    return payload


def _precompute_reconciliation_halt_reason(recon_result: dict[str, object] | None) -> str | None:
    decision = str((recon_result or {}).get("reconciliation_decision") or "PASS").strip().upper()
    if decision in {"PASS", "WARN"}:
        return None
    if decision == "BLOCK":
        return str((recon_result or {}).get("block_reason") or "pretrade_blocked_reconciliation")
    if decision == "SELF_HEAL":
        return "precompute_reconciliation_self_heal"
    return f"precompute_reconciliation_{decision.lower()}"


def _run_pretrade_reconciliation(*, trade_date: str, paper_ledger_path: str) -> dict[str, object]:
    kwargs = {
        "run_date": trade_date,
        "trading_mode": "paper",
        "ledger_path": paper_ledger_path,
        "sent_ledger_path": "outputs/orders_sent/orders_sent.csv",
    }
    initial = pre_trade_reconcile_and_classify(**kwargs) or {}
    initial_decision = str(initial.get("reconciliation_decision") or "PASS").strip().upper()
    if initial_decision != "SELF_HEAL":
        return initial

    logger.warning(
        "[LIVE_EXECUTION] pretrade reconciliation SELF_HEAL -> re-running reconciliation against refreshed canonical state"
    )
    followup = dict(pre_trade_reconcile_and_classify(**kwargs) or {})
    followup["reconciliation_rechecked_after_self_heal"] = True
    followup["initial_reconciliation_decision"] = initial_decision
    followup["initial_reconciliation_report_path"] = str(initial.get("report_path") or "")
    return followup


def _workflow_context() -> dict[str, object]:
    return {
        "workflow_kind": str(os.getenv("WORKFLOW_KIND", "")).strip() or None,
        "event_freshness_status": str(os.getenv("EVENT_FRESHNESS_STATUS", "")).strip() or None,
        "bundle_status": str(os.getenv("BUNDLE_STATUS", "")).strip() or None,
        "bundle_source": str(os.getenv("BUNDLE_SOURCE", "")).strip() or None,
        "precompute_bundle_required": str(os.getenv("PRECOMPUTE_BUNDLE_REQUIRED", "")).strip().lower() == "true",
        "precompute_bundle_found": str(os.getenv("PRECOMPUTE_BUNDLE_FOUND", "")).strip().lower() == "true",
        "bundle_report_date": str(os.getenv("BUNDLE_REPORT_DATE", "")).strip() or None,
        "execution_window_status": str(os.getenv("EXECUTION_WINDOW_STATUS", "")).strip() or None,
    }


def _acquire_execution_lock(trade_date: str, *, allow_existing: bool = False) -> Path | None:
    """Atomic single-flight guard: only one execution process per trade date.

    Uses O_CREAT|O_EXCL (open with 'x' mode) so the file-create is atomic on
    POSIX filesystems.  If the lock file already exists, raises SystemExit so
    the caller can log and terminate without proceeding to order submission.
    """
    lock_dir = _REPO_ROOT / "outputs" / "execution_locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{trade_date}.lock"
    try:
        with open(lock_path, "x") as fh:
            fh.write(f"pid={os.getpid()} started_at={dt.datetime.now(ZoneInfo('UTC')).isoformat()}\n")
    except FileExistsError:
        if allow_existing:
            logger.warning(
                "[LOCK][CONTINUATION] Existing primary execution lock found for %s (%s); "
                "continuing in explicit buy-only continuation mode.",
                trade_date,
                lock_path,
            )
            return None
        logger.error(
            "[LOCK] Execution lock already held for %s (%s) — "
            "a second process attempted to run. Aborting to prevent duplicate orders.",
            trade_date,
            lock_path,
        )
        raise SystemExit(75)  # EX_TEMPFAIL — safe exit code, cron will not retry
    logger.info("[LOCK] Acquired execution lock: %s", lock_path)
    return lock_path


def _release_execution_lock(lock_path: Path | None) -> None:
    if lock_path is None:
        return
    try:
        if lock_path.exists():
            lock_path.unlink()
            logger.info("[LOCK] Released execution lock: %s", lock_path)
    except Exception as exc:
        logger.warning("[LOCK] Failed to release execution lock %s: %s", lock_path, exc)


def _safe_to_release_lock_after_exception(exc: Exception) -> bool:
    reason = str(exc or "").strip()
    if reason.startswith("[HALT]"):
        return True
    return "no targets remain" in reason.lower()


def _persist_terminal_exception_state(
    *,
    exc: Exception,
    run_id: str,
    trade_date: str,
    run_root: Path,
    timing: dict[str, object],
    pretrade_policy: dict[str, object] | None,
    retry_attempt: int,
    planner_completed: bool,
) -> None:
    reason = str(exc).strip() or exc.__class__.__name__
    payload = _failure_payload(
        trade_date=trade_date,
        run_id=run_id,
        reason=reason,
        timing=timing,
    )
    payload["exception_type"] = exc.__class__.__name__
    payload["exception_message"] = reason
    write_canonical_execution_payload(payload, trade_date, run_root=run_root, allow_overwrite=True)
    _write_execution_email_payload(trade_date, payload)

    policy = dict(pretrade_policy or {})
    write_operator_summary(
        run_root,
        run_id=run_id,
        trade_date=trade_date,
        mode="PAPER",
        pretrade_status="HALTED",
        pretrade_halt_reason=reason,
        planner_completed=planner_completed,
        execution_payload_written=True,
        execution_stage_reached=False,
        terminal_status="failed_pre_execution",
        exception_type=exc.__class__.__name__,
        exception_message=reason,
        execution_reason=reason,
        operator_execution_status="failed",
        broker_preflight_status=policy.get("broker_preflight_status"),
        broker_preflight_account_status=policy.get("broker_preflight_account_status"),
        broker_preflight_cash=policy.get("broker_preflight_cash"),
        broker_preflight_equity=policy.get("broker_preflight_equity"),
        broker_preflight_buying_power=policy.get("broker_preflight_buying_power"),
        broker_preflight_restriction_flags=policy.get("broker_preflight_restriction_flags"),
        broker_preflight_warning_flags=policy.get("broker_preflight_warning_flags"),
        broker_pdt_risk_status=policy.get("broker_pdt_risk_status"),
        broker_pdt_daytrade_count=policy.get("broker_pdt_daytrade_count"),
        broker_pdt_daytrading_buying_power=policy.get("broker_pdt_daytrading_buying_power"),
        broker_pdt_flags=policy.get("broker_pdt_flags"),
        broker_pdt_warning_message=policy.get("broker_pdt_warning_message"),
        pdt_constrained=bool(policy.get("pdt_constrained")),
        retry_attempt_count=retry_attempt,
        retry_eligible=False,
        retry_reason="",
        workflow_kind=payload.get("workflow_kind"),
        event_freshness_status=payload.get("event_freshness_status"),
        bundle_status=payload.get("bundle_status"),
        execution_window_status=payload.get("execution_window_status"),
        **_operator_summary_timing_kwargs(timing),
    )
    _write_execution_run_pointers(
        run_id=run_id,
        trade_date=trade_date,
        run_root=run_root,
        status="failed_pre_execution",
        substatus=reason,
        status_message=reason,
    )
    write_trading_day_summary(run_root=run_root, run_id=run_id, trade_date=trade_date)
    _write_execution_timeline_artifacts(run_root=run_root, run_id=run_id, trade_date=trade_date)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parse_args(argv)
    trade_date = _trade_date()

    # --- Atomic single-flight guard (prevents TOCTOU duplicate submissions) ---
    lock_path = _acquire_execution_lock(
        trade_date,
        allow_existing=args.continuation_mode == "buy_only",
    )
    release_lock_on_exit = False
    pretrade_policy: dict[str, object] | None = None
    timing: dict[str, object] | None = None
    run_id: str | None = None
    run_root: Path | None = None
    planner_completed = False

    try:
        run_id = get_run_id()
        run_root = get_run_dir(run_id)
        _init_run_root(run_root, trade_date, run_id)

        workflow_start_utc = _workflow_start_utc()
        workflow_start_et = workflow_start_utc.astimezone(ET) if workflow_start_utc else None
        execution_start_et = current_et()
        retry_attempt = int(args.retry_attempt or 0)
        timing = classify_timing(
            now=execution_start_et,
            workflow_start_et=workflow_start_et,
            execution_start_et=execution_start_et,
            retry_attempt=retry_attempt > 0,
        )

        snapshot, planned_payload, _contract, precompute_reason = load_precompute_inputs(
            trade_date=trade_date,
            expected_mode="PAPER",
        )
        if snapshot is None or planned_payload is None:
            payload = _failure_payload(
                trade_date=trade_date,
                run_id=run_id,
                reason=str(precompute_reason or "precompute_missing"),
                timing=timing,
            )
            write_canonical_execution_payload(payload, trade_date, run_root=run_root, allow_overwrite=True)
            _write_execution_email_payload(trade_date, payload)
            retry = evaluate_live_retry(
                submitted_count=0,
                retry_attempt_count=retry_attempt,
                reason=precompute_reason,
                now=execution_start_et,
            )
            write_operator_summary(
                run_root,
                run_id=run_id,
                trade_date=trade_date,
                mode="PAPER",
                pretrade_status="HALTED",
                pretrade_halt_reason=str(precompute_reason),
                planner_completed=False,
                execution_payload_written=True,
                execution_stage_reached=False,
                terminal_status="failed_pre_execution",
                execution_reason=str(precompute_reason),
                operator_execution_status="failed",
                retry_attempt_count=retry_attempt,
                retry_eligible=bool(retry.get("retry_allowed")),
                retry_reason=str(retry.get("retry_reason") or ""),
                workflow_kind=payload.get("workflow_kind"),
                event_freshness_status=payload.get("event_freshness_status"),
                bundle_status=payload.get("bundle_status"),
                execution_window_status=payload.get("execution_window_status"),
                **_operator_summary_timing_kwargs(timing),
            )
            _write_execution_run_pointers(
                run_id=run_id,
                trade_date=trade_date,
                run_root=run_root,
                status="failed_pre_execution",
                substatus=str(precompute_reason or ""),
                status_message=str(precompute_reason or ""),
            )
            write_trading_day_summary(run_root=run_root, run_id=run_id, trade_date=trade_date)
            _write_execution_timeline_artifacts(run_root=run_root, run_id=run_id, trade_date=trade_date)
            logger.error("[LIVE_EXECUTION] precompute unavailable reason=%s", precompute_reason)
            release_lock_on_exit = True
            return 1
        planner_completed = True
        continuation_intended_orders_path = str(args.continuation_intended_orders_path or "").strip()
        if continuation_intended_orders_path:
            if args.continuation_mode != "buy_only":
                raise RuntimeError(
                    "--continuation-intended-orders-path requires --continuation-mode buy_only"
                )
            buy_continuation_plan = _load_buy_continuation_plan_from_intended_orders(
                continuation_intended_orders_path
            )
            planned_payload = dict(planned_payload or {})
            planned_payload["trades"] = buy_continuation_plan
            planned_payload["continuation_intended_orders_path"] = continuation_intended_orders_path
            logger.warning(
                "[CONTINUATION] hydrated buy-only plan from intended orders path=%s buys=%d",
                continuation_intended_orders_path,
                int(len(buy_continuation_plan)),
            )

        pretrade_snapshot = fetch_pretrade_snapshot()
        write_pretrade_snapshot_artifacts(
            run_root=run_root,
            run_id=run_id,
            trade_date=trade_date,
            snapshot=pretrade_snapshot,
            allow_overwrite=True,
        )
        pretrade_policy = summarize_pretrade_broker_policy(pretrade_snapshot)
        logger.info("%s", dqr.format_broker_preflight_banner(pretrade_policy))

        ensure_sent_ledger_exists("outputs/orders_sent/orders_sent.csv")
        paper_ledger_path, paper_trades_path = ensure_paper_state_files()
        recon_result = _run_pretrade_reconciliation(
            trade_date=trade_date,
            paper_ledger_path=paper_ledger_path,
        )
        recon_halt_reason = _precompute_reconciliation_halt_reason(recon_result)
        if recon_halt_reason is not None:
            reason = str(recon_halt_reason)
            payload = _failure_payload(trade_date=trade_date, run_id=run_id, reason=reason, timing=timing)
            payload["pretrade_reconciliation_decision"] = str(
                (recon_result or {}).get("reconciliation_decision") or ""
            )
            payload["pretrade_reconciliation_report_path"] = str(
                (recon_result or {}).get("report_path") or ""
            )
            write_canonical_execution_payload(payload, trade_date, run_root=run_root, allow_overwrite=True)
            _write_execution_email_payload(trade_date, payload)
            retry = evaluate_live_retry(
                submitted_count=0,
                retry_attempt_count=retry_attempt,
                reason=reason,
                now=execution_start_et,
            )
            write_operator_summary(
                run_root,
                run_id=run_id,
                trade_date=trade_date,
                mode="PAPER",
                pretrade_status="HALTED",
                pretrade_halt_reason=reason,
                planner_completed=True,
                execution_payload_written=True,
                execution_stage_reached=False,
                terminal_status="failed_pre_execution",
                execution_reason=reason,
                operator_execution_status="failed",
                broker_preflight_status=pretrade_policy.get("broker_preflight_status"),
                broker_preflight_account_status=pretrade_policy.get("broker_preflight_account_status"),
                broker_preflight_cash=pretrade_policy.get("broker_preflight_cash"),
                broker_preflight_equity=pretrade_policy.get("broker_preflight_equity"),
                broker_preflight_buying_power=pretrade_policy.get("broker_preflight_buying_power"),
                broker_preflight_restriction_flags=pretrade_policy.get("broker_preflight_restriction_flags"),
                broker_preflight_warning_flags=pretrade_policy.get("broker_preflight_warning_flags"),
                broker_pdt_risk_status=pretrade_policy.get("broker_pdt_risk_status"),
                broker_pdt_daytrade_count=pretrade_policy.get("broker_pdt_daytrade_count"),
                broker_pdt_daytrading_buying_power=pretrade_policy.get("broker_pdt_daytrading_buying_power"),
                broker_pdt_flags=pretrade_policy.get("broker_pdt_flags"),
                broker_pdt_warning_message=pretrade_policy.get("broker_pdt_warning_message"),
                pdt_constrained=bool(pretrade_policy.get("pdt_constrained")),
                retry_attempt_count=retry_attempt,
                retry_eligible=bool(retry.get("retry_allowed")),
                retry_reason=str(retry.get("retry_reason") or ""),
                workflow_kind=str(os.getenv("WORKFLOW_KIND", "")).strip() or "live",
                event_freshness_status=str(os.getenv("EVENT_FRESHNESS_STATUS", "")).strip() or None,
                bundle_status=str(os.getenv("BUNDLE_STATUS", "")).strip() or None,
                execution_window_status=str(os.getenv("EXECUTION_WINDOW_STATUS", "")).strip() or None,
                **_operator_summary_timing_kwargs(timing),
            )
            _write_execution_run_pointers(
                run_id=run_id,
                trade_date=trade_date,
                run_root=run_root,
                status="failed_pre_execution",
                substatus=reason,
                status_message=reason,
            )
            write_trading_day_summary(run_root=run_root, run_id=run_id, trade_date=trade_date)
            _write_execution_timeline_artifacts(run_root=run_root, run_id=run_id, trade_date=trade_date)
            logger.error(
                "[LIVE_EXECUTION] blocked by pretrade reconciliation decision=%s reason=%s report=%s",
                str((recon_result or {}).get("reconciliation_decision") or "UNKNOWN"),
                reason,
                str((recon_result or {}).get("report_path") or ""),
            )
            release_lock_on_exit = True
            return 1

        if args.continuation_mode == "buy_only":
            os.environ["ALLOW_PARTIAL_BUY_CONTINUATION"] = "1"

        exact_precomputed_execution = _env_truthy("PRECOMPUTE_EXECUTE_EXACT_PLAN")
        provenance = _planned_payload_provenance(
            planned_payload=planned_payload,
            exact_precomputed_execution=exact_precomputed_execution,
        )
        planned_execution_trades = None
        if exact_precomputed_execution:
            planned_execution_trades = _validate_exact_planned_payload(
                planned_payload,
                trade_date=trade_date,
            )
        elif args.continuation_mode == "buy_only":
            planned_execution_trades = list((planned_payload or {}).get("trades") or [])
        if args.continuation_mode == "buy_only" and planned_execution_trades is not None:
            planned_execution_trades = [
                dict(trade)
                for trade in planned_execution_trades
                if str((trade or {}).get("side") or "").upper() == "BUY"
            ]
        logger.info(
            "EXECUTION_SOURCE=%s PRICE_BASIS=%s PRICING_ASOF=%s FRESHNESS_SCOPE=%s",
            provenance["execution_source"],
            provenance.get("planning_price_basis") or "SAME_DAY_OPEN",
            provenance.get("pricing_asof") or "n/a",
            provenance["execution_price_requirement"],
        )
        adjusted_signals_path, adjusted_cash_target_weight = _apply_pre_execution_risk_controls(
            run_root=run_root,
            trade_date=trade_date,
            snapshot=snapshot,
            pretrade_policy=pretrade_policy,
            ledger_path=paper_ledger_path,
        )

        prior_run_output_root = os.environ.get("RUN_OUTPUT_ROOT")
        os.environ["RUN_OUTPUT_ROOT"] = str(run_root)
        try:
            paper_summary = run_paper_day(
                run_date=trade_date,
                signals_path=adjusted_signals_path,
                ledger_path=paper_ledger_path,
                trades_path=paper_trades_path,
                config_path="paper/config_paper.json",
                force=args.continuation_mode == "buy_only",
                plan_only=False,
                constraints={
                    "cash_target_weight": adjusted_cash_target_weight,
                },
                precomputed_trade_plan=planned_execution_trades,
            )
        finally:
            if prior_run_output_root is None:
                os.environ.pop("RUN_OUTPUT_ROOT", None)
            else:
                os.environ["RUN_OUTPUT_ROOT"] = prior_run_output_root
        paper_summary["run_id"] = run_id
        paper_summary["precomputed_execution_mode"] = (
            "buy_only_continuation"
            if args.continuation_mode == "buy_only"
            else ("exact_payload" if exact_precomputed_execution else "rebuilt_from_signals")
        )
        paper_summary.update(provenance)
        paper_summary["planned_payload_trade_count"] = int(len((planned_payload or {}).get("trades") or []))
        first_submit_et = _first_submit_et(paper_summary)
        timing = classify_timing(
            now=current_et(),
            workflow_start_et=workflow_start_et,
            execution_start_et=execution_start_et,
            first_submit_et=first_submit_et,
            retry_attempt=retry_attempt > 0,
        )
        pending_buy_state = _apply_pending_buy_leg_guard(paper_summary)

        execution_payload = dqr.build_execution_email_payload(
            trade_date=trade_date,
            daily_snapshot=snapshot,
            paper_summary=paper_summary,
        )
        execution_payload["run_id"] = run_id
        execution_payload.update(timing)
        execution_payload.update(_workflow_context())
        execution_payload.update(provenance)
        execution_payload["retry_attempt_count"] = retry_attempt

        submission_summary = dict((paper_summary or {}).get("alpaca_submission_summary") or {})
        submitted_count = int(submission_summary.get("submit_success") or 0)
        rejected_count = int(submission_summary.get("submit_failed") or 0)
        execution_payload["submitted_count"] = submitted_count
        execution_payload["accepted_count"] = submitted_count
        execution_payload["rejected_count"] = rejected_count
        execution_payload["orders_submitted_count"] = submitted_count

        pending_buy_count = int(pending_buy_state.get("pending_buy_count") or 0)
        continuation_eligible = _pending_buy_continuation_eligible(
            paper_summary=paper_summary,
            pending_buy_state=pending_buy_state,
            submitted_count=submitted_count,
            timing=timing,
        )
        submitted_side_counts = _submitted_side_counts(paper_summary)
        execution_payload["pending_buy_count"] = pending_buy_count
        execution_payload["pending_buy_orders"] = list(pending_buy_state.get("pending_buy_orders") or [])
        execution_payload["continuation_eligible"] = continuation_eligible
        execution_payload["continuation_reason"] = (
            str(
                (paper_summary or {}).get("execution_reason")
                or pending_buy_state.get("buy_phase_block_reason")
                or "post_submit_artifact_failure"
            )
            if continuation_eligible
            else None
        )
        execution_payload["buy_phase_planned"] = int(pending_buy_state.get("buy_phase_planned") or 0)
        execution_payload["buy_phase_submitted"] = int(pending_buy_state.get("buy_phase_submitted") or 0)
        execution_payload["buy_phase_block_reason"] = (
            str(pending_buy_state.get("buy_phase_block_reason") or "") or None
        )
        execution_payload["sell_phase_status"] = (paper_summary or {}).get("sell_phase_status")
        execution_payload["sell_phase_completion_reason"] = (paper_summary or {}).get("sell_phase_completion_reason")
        execution_payload["submitted_buy_count"] = int(submitted_side_counts.get("BUY") or 0)
        execution_payload["submitted_sell_count"] = int(submitted_side_counts.get("SELL") or 0)
        execution_payload["capital_allows_pending_buys"] = bool(
            pending_buy_state.get("capital_allows_pending_buys")
        )
        if str(args.continuation_intended_orders_path or "").strip():
            execution_payload["continuation_intended_orders_path"] = str(
                args.continuation_intended_orders_path
            ).strip()
            execution_payload["continuation_source"] = "intended_orders"
        if args.continuation_mode != "none":
            execution_payload["continuation_mode"] = str(args.continuation_mode)
        execution_payload["operator_execution_status"] = _operator_execution_status(execution_payload)

        # Extract capital budget metadata for operator summary and retry decisions.
        _capital_budget = dict((paper_summary or {}).get("capital_budget") or {})
        capital_constrained_no_trades = bool(_capital_budget.get("capital_constrained_no_trades"))

        retry = evaluate_live_retry(
            submitted_count=submitted_count,
            retry_attempt_count=retry_attempt,
            reason=execution_payload.get("execution_reason") or execution_payload.get("halt_reason"),
            now=current_et(),
        )
        # Override: when capital constraint clipped all orders to zero and no orders
        # were submitted, signal retry so the next window can try with refreshed cash.
        if (
            capital_constrained_no_trades
            and submitted_count == 0
            and retry_attempt == 0
            and not bool(retry.get("retry_allowed"))
        ):
            retry["retry_allowed"] = True
            retry["retry_reason"] = "capital_constrained_no_trades"
            logger.warning("[CAPITAL_BUDGET] overriding retry_eligible=true — all buys clipped to zero")

        execution_payload["retry_eligible"] = bool(retry.get("retry_allowed"))
        execution_payload["retry_reason"] = str(retry.get("retry_reason") or "")
        execution_payload["pdt_constrained"] = bool(pretrade_policy.get("pdt_constrained"))
        execution_payload["capital_constrained_no_trades"] = capital_constrained_no_trades

        write_planner_audit(
            run_id=run_id,
            trade_date=trade_date,
            mode="PAPER",
            today_et=trade_date,
            is_planning_run=False,
            market_is_open=str((paper_summary or {}).get("market_status") or "").upper() == "OPEN",
            should_execute=True,
            market_guard=(paper_summary or {}).get("market_guard"),
            broker_cash_before=None,
            broker_positions_before=(pretrade_snapshot or {}).get("positions"),
            canonical_positions_before=None,
            signals_generated=len((snapshot or {}).get("proposed_trades") or []),
            proposed_trades=list((snapshot or {}).get("proposed_trades") or []),
            blocked_reasons=list((paper_summary or {}).get("blocked_reasons") or []),
            execution_payload=execution_payload,
            extra={"workflow_stage": "live_execution_from_precompute"},
        )

        write_canonical_execution_payload(
            execution_payload,
            trade_date,
            run_root=run_root,
            allow_overwrite=True,
        )
        _write_execution_email_payload(trade_date, execution_payload)
        _write_execution_results(run_root, execution_payload, paper_summary)

        write_operator_summary(
            run_root,
            run_id=run_id,
            trade_date=trade_date,
            mode="PAPER",
            pretrade_status=normalize_status(
                execution_status=execution_payload.get("execution_status"),
                halt_reason=execution_payload.get("halt_reason"),
                executable_trades_count=int(execution_payload.get("executable_trades_count") or 0),
            ),
            pretrade_halt_reason=execution_payload.get("halt_reason"),
            proposed_trades_count=int(execution_payload.get("planner_intended_trades_count") or 0),
            executable_trades_count=int(execution_payload.get("execution_eligible_trades_count") or 0),
            submitted_count=submitted_count,
            accepted_count=submitted_count,
            rejected_count=rejected_count,
            model_proposed_trades_count=int(execution_payload.get("model_proposed_trades_count") or 0),
            planner_intended_trades_count=int(execution_payload.get("planner_intended_trades_count") or 0),
            execution_eligible_trades_count=int(execution_payload.get("execution_eligible_trades_count") or 0),
            orders_submitted_count=submitted_count,
            planner_completed=True,
            executor_completed=True,
            execution_payload_written=True,
            execution_stage_reached=True,
            terminal_status=(
                "failed_pre_execution"
                if execution_payload["operator_execution_status"] in {"failed", "partial"}
                else ("no_action" if execution_payload["operator_execution_status"] == "skipped" else "success")
            ),
            exception_type=str((paper_summary or {}).get("artifact_failure_stage") or "") or None,
            exception_message=str((paper_summary or {}).get("artifact_failure_message") or "") or None,
            broker_pretrade_snapshot_ok=bool((pretrade_snapshot or {}).get("ok")),
            broker_posttrade_snapshot_ok=bool((paper_summary or {}).get("posttrade_account_snapshot_path") and (paper_summary or {}).get("posttrade_positions_snapshot_path")),
            broker_authoritative_state=bool((paper_summary or {}).get("posttrade_positions_snapshot_path")),
            post_execution_recon_status=(paper_summary or {}).get("posttrade_recon_status"),
            post_execution_recon_path=(paper_summary or {}).get("posttrade_recon_path"),
            affected_symbols=list((paper_summary or {}).get("execution_submitted_symbols") or []),
            repair_suggestions=list((paper_summary or {}).get("posttrade_repair_suggestions") or []),
            duplicate_fill_suspicions_count=int((paper_summary or {}).get("posttrade_duplicate_fill_suspicions_count") or 0),
            broker_reject_status=(paper_summary or {}).get("broker_reject_status"),
            broker_reject_message=(paper_summary or {}).get("broker_reject_message"),
            execution_outcome=(paper_summary or {}).get("execution_outcome"),
            execution_reason=(paper_summary or {}).get("execution_reason"),
            cash_rebalance_status=(paper_summary or {}).get("cash_rebalance_status"),
            broker_preflight_status=pretrade_policy.get("broker_preflight_status"),
            broker_preflight_account_status=pretrade_policy.get("broker_preflight_account_status"),
            broker_preflight_cash=pretrade_policy.get("broker_preflight_cash"),
            broker_preflight_equity=pretrade_policy.get("broker_preflight_equity"),
            broker_preflight_buying_power=pretrade_policy.get("broker_preflight_buying_power"),
            broker_preflight_restriction_flags=pretrade_policy.get("broker_preflight_restriction_flags"),
            broker_preflight_warning_flags=pretrade_policy.get("broker_preflight_warning_flags"),
            broker_pdt_risk_status=pretrade_policy.get("broker_pdt_risk_status"),
            broker_pdt_daytrade_count=pretrade_policy.get("broker_pdt_daytrade_count"),
            broker_pdt_daytrading_buying_power=pretrade_policy.get("broker_pdt_daytrading_buying_power"),
            broker_pdt_flags=pretrade_policy.get("broker_pdt_flags"),
            broker_pdt_warning_message=pretrade_policy.get("broker_pdt_warning_message"),
            pdt_constrained=bool(pretrade_policy.get("pdt_constrained")),
            broker_cash_at_planning=_capital_budget.get("broker_cash_at_planning"),
            broker_equity_at_planning=_capital_budget.get("broker_equity_at_planning"),
            broker_buying_power_at_planning=_capital_budget.get("broker_buying_power_at_planning"),
            reserve_cash_policy=_capital_budget.get("reserve_cash_policy"),
            expected_sell_proceeds=_capital_budget.get("expected_sell_proceeds"),
            expected_sell_proceeds_conservative=_capital_budget.get("expected_sell_proceeds_conservative"),
            requested_buy_notional=_capital_budget.get("requested_buy_notional"),
            allowed_buy_notional=_capital_budget.get("allowed_buy_notional"),
            capital_constraint_triggered=bool(_capital_budget.get("capital_constraint_triggered")),
            capital_constrained_no_trades=capital_constrained_no_trades,
            clipped_or_deferred_buys_count=int(_capital_budget.get("clipped_or_deferred_buys_count") or 0),
            timing_status=str(timing.get("timing_status") or ""),
            preferred_target_et=str(timing.get("preferred_target_et") or ""),
            degraded_auto_trade_deadline_et=str(timing.get("degraded_auto_trade_deadline_et") or ""),
            actual_workflow_start_et=str(timing.get("actual_workflow_start_et") or ""),
            actual_execution_start_et=str(timing.get("actual_execution_start_et") or ""),
            first_submit_et=str(timing.get("first_submit_et") or ""),
            operator_execution_status=str(execution_payload.get("operator_execution_status") or ""),
            retry_attempt_count=retry_attempt,
            retry_eligible=bool(retry.get("retry_allowed")),
            retry_reason=str(retry.get("retry_reason") or ""),
            continuation_eligible=continuation_eligible,
            continuation_reason=str(execution_payload.get("continuation_reason") or ""),
            pending_buy_count=pending_buy_count,
            pending_buy_orders=list(pending_buy_state.get("pending_buy_orders") or []),
            buy_phase_planned=int(execution_payload.get("buy_phase_planned") or 0),
            buy_phase_submitted=int(execution_payload.get("buy_phase_submitted") or 0),
            buy_phase_block_reason=execution_payload.get("buy_phase_block_reason"),
            sell_phase_status=execution_payload.get("sell_phase_status"),
            sell_phase_completion_reason=execution_payload.get("sell_phase_completion_reason"),
            submitted_buy_count=int(execution_payload.get("submitted_buy_count") or 0),
            submitted_sell_count=int(execution_payload.get("submitted_sell_count") or 0),
            capital_allows_pending_buys=bool(execution_payload.get("capital_allows_pending_buys")),
            continuation_intended_orders_path=execution_payload.get("continuation_intended_orders_path"),
            continuation_source=execution_payload.get("continuation_source"),
            continuation_mode=execution_payload.get("continuation_mode"),
            workflow_kind=execution_payload.get("workflow_kind"),
            event_freshness_status=execution_payload.get("event_freshness_status"),
            bundle_status=execution_payload.get("bundle_status"),
            execution_window_status=execution_payload.get("execution_window_status"),
        )

        try:
            integrity_path = write_execution_integrity_audit(
                run_root=run_root,
                trade_date=trade_date,
                run_id=run_id,
                intended_orders_path=execution_payload.get("continuation_intended_orders_path"),
            )
            integrity_payload = json.loads(integrity_path.read_text(encoding="utf-8"))
            findings = list(integrity_payload.get("findings") or [])
            write_operator_summary(
                run_root,
                execution_integrity_status=integrity_payload.get("status"),
                execution_integrity_findings=[
                    str((finding or {}).get("code") or "")
                    for finding in findings[:5]
                    if str((finding or {}).get("code") or "").strip()
                ],
                execution_integrity_artifact=str(integrity_path),
            )
        except Exception as exc:
            logger.warning("[EXECUTION_INTEGRITY] audit skipped: %s", exc)

        print(format_operator_summary_log(load_operator_summary(run_root) or {}))
        print(format_execution_health_banner(load_operator_summary(run_root) or {}))

        write_executor_audit(
            run_id=run_id,
            pretrade_status=str(execution_payload.get("execution_status") or "UNKNOWN"),
            halt_reason=str(execution_payload.get("halt_reason") or "") or None,
            submitted_count=submitted_count,
            accepted_count=submitted_count,
            rejected_count=rejected_count,
            duplicate_count=int(submission_summary.get("remote_existing_orders") or 0),
            broker_responses=list((paper_summary or {}).get("alpaca_submissions") or []),
            broker_cash_after=(paper_summary or {}).get("cash"),
            broker_positions_after=(paper_summary or {}).get("alpaca_positions_snapshot"),
            execution_status=str(execution_payload.get("operator_execution_status") or execution_payload.get("execution_status") or "UNKNOWN").upper(),
        )

        try:
            write_execution_artifacts(
                run_id=run_id,
                trade_date=trade_date,
                run_dir=run_root,
                execution_payload=execution_payload,
                paper_summary=paper_summary,
                nav_snapshot={
                    "position_count": len([h for h in (snapshot.get("holdings") or []) if str(h.get("ticker", "")).upper() != "CASH"]),
                    "equity": ((snapshot.get("performance_diagnostics") or {}).get("current_equity")),
                    "cash": (paper_summary or {}).get("cash"),
                    "turnover_pct": execution_payload.get("turnover_pct"),
                    "holdings": (snapshot.get("holdings") or []),
                },
            )
        except Exception as exc:
            logger.warning("[LIVE_EXECUTION] execution summary artifacts skipped: %s", exc)

        write_trading_day_summary(run_root=run_root, run_id=run_id, trade_date=trade_date)
        _write_execution_timeline_artifacts(run_root=run_root, run_id=run_id, trade_date=trade_date)

        try:
            from core.benchmark_tracking import update_benchmark_vs_spy
            update_benchmark_vs_spy(trade_date=trade_date, run_root=run_root)
        except Exception as _bench_exc:
            logger.warning("[BENCHMARK] benchmark update skipped: %s", _bench_exc)

        exec_subject, exec_body = build_execution_email_text(execution_payload)
        safe_write_text(
            run_root / "reports" / f"trade_execution_{trade_date}.txt",
            exec_body.rstrip() + "\n",
            allow_overwrite=True,
        )
        logger.info("[LIVE_EXECUTION] email_subject=%s", exec_subject)

        terminal_status = (
            "failed_pre_execution"
            if execution_payload["operator_execution_status"] in {"failed", "partial"}
            else ("no_action" if execution_payload["operator_execution_status"] == "skipped" else "success")
        )
        _write_execution_run_pointers(
            run_id=run_id,
            trade_date=trade_date,
            run_root=run_root,
            status=terminal_status,
            substatus=str((paper_summary or {}).get("execution_reason") or ""),
            status_message=str(execution_payload.get("halt_reason") or ""),
        )

        release_lock_on_exit = submitted_count == 0
        if execution_payload["operator_execution_status"] in {"failed", "partial"}:
            return 1
        return 0
    except Exception as exc:
        logger.exception("[LIVE_EXECUTION] unhandled exception")
        if run_id is not None and run_root is not None:
            try:
                _persist_terminal_exception_state(
                    exc=exc,
                    run_id=run_id,
                    trade_date=trade_date,
                    run_root=run_root,
                    timing=dict(timing or {}),
                    pretrade_policy=pretrade_policy,
                    retry_attempt=int(args.retry_attempt or 0),
                    planner_completed=planner_completed,
                )
            except Exception as artifact_exc:
                logger.error(
                    "[LIVE_EXECUTION] failed to persist terminal exception state: %s",
                    artifact_exc,
                )
        release_lock_on_exit = _safe_to_release_lock_after_exception(exc)
        return 1
    finally:
        if release_lock_on_exit:
            _release_execution_lock(lock_path)


if __name__ == "__main__":
    sys.exit(main())

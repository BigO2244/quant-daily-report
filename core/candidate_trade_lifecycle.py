"""Candidate trade lifecycle artifact builder.

This module is intentionally audit-only.  It reconstructs where each planned
candidate landed by joining already-written execution artifacts; it does not
participate in sizing, filtering, order construction, or broker submission.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping


LIFECYCLE_SCHEMA_VERSION = "candidate_trade_lifecycle.v1"

STAGES = [
    "precompute_payload",
    "executable_filter",
    "intended_orders",
    "post_sell_rebudget",
    "broker_submission",
    "broker_response",
    "posttrade_reconciliation",
]

CODE_PATHS: dict[str, dict[str, str]] = {
    "precompute_payload": {
        "file": "scripts/run_precomputed_alpaca_execution.py",
        "function": "main",
        "lines": "1330-1573",
    },
    "executable_filter": {
        "file": "paper/paper_broker.py",
        "function": "_normalize_and_filter_executable_trades",
        "lines": "1254-1315",
    },
    "intended_orders": {
        "file": "paper/paper_broker.py",
        "function": "_write_intended_orders_artifact",
        "lines": "1646-1688",
    },
    "post_sell_rebudget": {
        "file": "paper/paper_broker.py",
        "function": "_rebuild_post_sell_buy_trades",
        "lines": "3432-3612",
    },
    "broker_submission": {
        "file": "paper/paper_broker.py",
        "function": "_submit_alpaca_orders",
        "lines": "3735-3955",
    },
    "broker_response": {
        "file": "paper/paper_broker.py",
        "function": "_submit_alpaca_orders",
        "lines": "3917-3945",
    },
    "posttrade_reconciliation": {
        "file": "paper/paper_broker.py",
        "function": "_capture_alpaca_posttrade_state",
        "lines": "3958-4084",
    },
}

ACCEPTED_STATUSES = {
    "ACCEPTED",
    "ACCEPTED_FOR_BIDDING",
    "CALCULATED",
    "DONE_FOR_DAY",
    "EXISTING_REMOTE",
    "FILLED",
    "FILLED_ESTIMATE",
    "NEW",
    "PARTIALLY_FILLED",
    "PENDING_CANCEL",
    "PENDING_NEW",
    "PENDING_REPLACE",
    "SUBMITTED",
}

REJECTED_STATUSES = {
    "CANCELED",
    "CANCELLED",
    "EXPIRED",
    "OPEN_DUPLICATE_BLOCKED",
    "REJECTED",
    "REPLACED",
    "STOPPED",
    "SUSPENDED",
}

FILLED_STATUSES = {"FILLED", "FILLED_ESTIMATE"}

PROVENANCE_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "sleeve_id": ("sleeve_id", "sleeve", "sleeve_name"),
    "strategy_id": ("strategy_id", "strategy", "strategy_name"),
    "source_model": ("source_model", "model", "model_id", "model_name", "signal_model"),
    "candidate_rank": ("candidate_rank", "rank", "model_rank"),
    "alpha_rank": ("alpha_rank", "alpha_model_rank", "momentum_rank", "quality_rank"),
    "conviction_score": ("conviction_score", "alpha_score", "score", "signal_strength"),
    "target_weight": ("target_weight", "target_weight_pct"),
    "current_weight": ("current_weight", "current_weight_pct"),
    "target_notional": ("target_notional", "target_notional_dollars"),
    "current_notional": ("current_notional", "current_notional_dollars"),
    "delta_notional": ("delta_notional", "delta_notional_dollars"),
    "capital_rank": ("capital_rank", "capital_priority_rank", "buy_priority_rank"),
}

PROVENANCE_INT_FIELDS = {"candidate_rank", "alpha_rank", "capital_rank"}
PROVENANCE_FLOAT_FIELDS = {
    "conviction_score",
    "target_weight",
    "current_weight",
    "target_notional",
    "current_notional",
    "delta_notional",
}


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _float(value: Any, default: float | None = None) -> float | None:
    try:
        if value in ("", None):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _text(value: Any) -> str:
    return str(value or "").strip()


def _side(value: Any) -> str:
    raw = _text(value).upper()
    if raw in {"SELL", "CLOSE", "REDUCE"}:
        return "SELL"
    if raw == "BUY":
        return "BUY"
    return raw


def _ticker(value: Any) -> str:
    return _text(value).upper()


def _key(ticker: Any, side: Any) -> tuple[str, str]:
    return (_ticker(ticker), _side(side))


def _status(row: Mapping[str, Any]) -> str:
    return _text(row.get("latest_status") or row.get("status")).upper()


def _quantity(row: Mapping[str, Any]) -> float | None:
    for key in ("quantity", "qty", "shares"):
        value = _float(row.get(key), None)
        if value is not None:
            return abs(value)
    return None


def _notional(row: Mapping[str, Any]) -> float | None:
    value = _float(row.get("notional"), None)
    if value is not None:
        return abs(value)
    qty = _quantity(row)
    price = _price(row)
    if qty is not None and price is not None:
        return abs(qty * price)
    return None


def _price(row: Mapping[str, Any]) -> float | None:
    for key in ("price", "entry_price", "limit_price"):
        value = _float(row.get(key), None)
        if value is not None and value > 0:
            return value
    qty = _quantity(row)
    notional = _float(row.get("notional"), None)
    if qty is not None and qty > 0 and notional is not None and notional > 0:
        return abs(notional) / abs(qty)
    return None


def _filled_qty(row: Mapping[str, Any]) -> float:
    value = _float(row.get("filled_qty"), None)
    if value is not None:
        return abs(value)
    if _status(row) in FILLED_STATUSES:
        return float(_quantity(row) or 0.0)
    return 0.0


def _is_submitted(row: Mapping[str, Any]) -> bool:
    return bool(_ticker(row.get("ticker") or row.get("symbol")) and _side(row.get("side")))


def _is_accepted(row: Mapping[str, Any]) -> bool:
    status = _status(row)
    if status in REJECTED_STATUSES:
        return False
    return status in ACCEPTED_STATUSES or bool(row.get("alpaca_order_id"))


def _is_filled(row: Mapping[str, Any]) -> bool:
    return _status(row) in FILLED_STATUSES or _filled_qty(row) > 1e-12


def _read_json(path: Path) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return None


def _first_by_key(rows: list[Any], *, default_side: str | None = None) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in rows:
        row = _as_dict(raw)
        key = _key(row.get("ticker") or row.get("symbol"), row.get("side") or default_side)
        if key[0] and key[1] and key not in out:
            if not row.get("side") and default_side:
                row["side"] = default_side
            out[key] = row
    return out


def _rows_by_key(rows: list[Any]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    out: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for raw in rows:
        row = _as_dict(raw)
        key = _key(row.get("ticker") or row.get("symbol"), row.get("side"))
        if key[0] and key[1]:
            out.setdefault(key, []).append(row)
    return out


def _summarize_broker_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "submitted": False,
            "accepted": False,
            "rejected": False,
            "filled": False,
            "submitted_shares": None,
            "submitted_notional": None,
            "filled_shares": 0.0,
            "statuses": [],
            "order_ids": [],
            "broker_order_ids": [],
        }
    return {
        "submitted": any(_is_submitted(row) for row in rows),
        "accepted": any(_is_accepted(row) for row in rows),
        "rejected": any(_status(row) in REJECTED_STATUSES for row in rows),
        "filled": any(_is_filled(row) for row in rows),
        "submitted_shares": sum(float(_quantity(row) or 0.0) for row in rows),
        "submitted_notional": sum(float(_notional(row) or 0.0) for row in rows),
        "filled_shares": sum(float(_filled_qty(row) or 0.0) for row in rows),
        "statuses": sorted({_status(row) for row in rows if _status(row)}),
        "order_ids": sorted({_text(row.get("order_id")) for row in rows if _text(row.get("order_id"))}),
        "broker_order_ids": sorted(
            {_text(row.get("alpaca_order_id")) for row in rows if _text(row.get("alpaca_order_id"))}
        ),
    }


def _normalized_precompute_rows(planned_payload: Mapping[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in _as_list(planned_payload.get("trades")):
        row = _as_dict(raw)
        ticker = _ticker(row.get("ticker"))
        side = _side(row.get("side"))
        if not ticker or not side:
            continue
        shares = _quantity(row) or 0.0
        price = _price(row) or 0.0
        notional = _notional(row)
        if notional is None:
            notional = abs(shares * price)
        key = (ticker, side)
        rows[key] = {
            "ticker": ticker,
            "side": side,
            "shares": float(shares),
            "price": float(price),
            "notional": float(abs(notional)),
            "reason": _text(row.get("reason") or row.get("notes") or "precomputed_execution_payload"),
            "raw": row,
        }
    return rows


def _executable_filter_stage(
    precompute_row: Mapping[str, Any] | None,
    *,
    min_trade_dollars: float,
    allow_fractional: bool,
) -> dict[str, Any]:
    if not precompute_row:
        return {
            "normalized_shares": None,
            "normalized_price": None,
            "normalized_notional": None,
            "passed_min_notional": None,
            "suppression_reason": None,
        }
    shares = abs(float(precompute_row.get("shares") or 0.0))
    price = float(precompute_row.get("price") or 0.0)
    normalized_shares = shares if allow_fractional else float(math.floor(shares))
    normalized_notional = abs(normalized_shares * price)
    if normalized_shares <= 1e-12 or (not allow_fractional and normalized_shares < 1.0):
        reason = "zero_shares_after_execution_normalization"
        passed = False
    elif normalized_notional + 1e-9 < float(min_trade_dollars):
        reason = "min_notional"
        passed = False
    else:
        reason = None
        passed = True
    return {
        "normalized_shares": float(normalized_shares),
        "normalized_price": float(price),
        "normalized_notional": float(normalized_notional),
        "passed_min_notional": bool(passed),
        "suppression_reason": reason,
        "min_trade_dollars": float(min_trade_dollars),
        "allow_fractional": bool(allow_fractional),
    }


def _load_intended_orders(run_root: Path, trade_date: str) -> tuple[dict[str, Any], dict[tuple[str, str], dict[str, Any]], Path]:
    path = run_root / "broker" / f"intended_orders_{trade_date}.json"
    payload = _as_dict(_read_json(path))
    rows = _first_by_key(_as_list(payload.get("orders_intended")))
    return payload, rows, path


def _load_post_sell_rebudget(
    run_root: Path,
    trade_date: str,
    paper_summary: Mapping[str, Any],
    execution_payload: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], Path]:
    payload = _as_dict(
        paper_summary.get("post_sell_rebudget")
        or (execution_payload or {}).get("post_sell_rebudget")
        or _as_dict(paper_summary.get("cash_gate_diagnostics")).get("post_sell_rebudget")
        or _as_dict((execution_payload or {}).get("cash_gate_diagnostics")).get("post_sell_rebudget")
    )
    path_text = _text(
        paper_summary.get("post_sell_rebudget_artifact_path")
        or (execution_payload or {}).get("post_sell_rebudget_artifact_path")
        or payload.get("artifact_path")
    )
    path = Path(path_text) if path_text else run_root / "broker" / f"post_sell_rebudget_{trade_date}.json"
    if not payload:
        loaded = _read_json(path)
        payload = _as_dict(loaded)
    return payload, path


def _load_reconciliation(
    run_root: Path,
    trade_date: str,
    paper_summary: Mapping[str, Any],
    execution_payload: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], Path | None]:
    path_text = _text(
        paper_summary.get("posttrade_recon_path") or (execution_payload or {}).get("posttrade_recon_path")
    )
    path = Path(path_text) if path_text else None
    if path is None:
        candidates = sorted((run_root / "broker").glob(f"recon_*{trade_date}*.json"))
        path = candidates[-1] if candidates else None
    payload = _as_dict(_read_json(path)) if path is not None else {}
    return payload, path


def _rebudget_indexes(payload: Mapping[str, Any]) -> tuple[
    dict[tuple[str, str], dict[str, Any]],
    dict[tuple[str, str], dict[str, Any]],
]:
    final_rows = _first_by_key(_as_list(payload.get("final_buy_orders_submitted")), default_side="BUY")
    skipped_rows = _first_by_key(_as_list(payload.get("skipped_buy_orders")), default_side="BUY")
    return final_rows, skipped_rows


def _broker_indexes(
    paper_summary: Mapping[str, Any],
    execution_payload: Mapping[str, Any] | None = None,
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    rows: list[Any] = []
    for source in (
        paper_summary.get("alpaca_submissions"),
        paper_summary.get("order_lifecycle"),
        paper_summary.get("posttrade_resolved_orders"),
        paper_summary.get("observed_buy_orders"),
        (execution_payload or {}).get("order_lifecycle"),
    ):
        rows.extend(_as_list(source))
    return _rows_by_key(rows)


def _candidate_sources(
    precompute_rows: dict[tuple[str, str], dict[str, Any]],
    intended_rows: dict[tuple[str, str], dict[str, Any]],
    rebudget_final_rows: dict[tuple[str, str], dict[str, Any]],
    rebudget_skipped_rows: dict[tuple[str, str], dict[str, Any]],
    broker_rows: dict[tuple[str, str], list[dict[str, Any]]],
) -> list[tuple[str, str]]:
    keys = set(precompute_rows)
    keys.update(intended_rows)
    keys.update(rebudget_final_rows)
    keys.update(rebudget_skipped_rows)
    keys.update(broker_rows)
    return sorted(keys, key=lambda item: (item[0], item[1]))


def _reason_from_rebudget(row: Mapping[str, Any]) -> str | None:
    return _text(row.get("block_reason") or row.get("reason")) or None


def _coerce_provenance_value(field: str, value: Any) -> Any:
    if value in (None, ""):
        return None
    if field in PROVENANCE_INT_FIELDS:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    if field in PROVENANCE_FLOAT_FIELDS:
        return _float(value, None)
    text = _text(value)
    return text or None


def _provenance_sources(*rows: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    sources: list[Mapping[str, Any]] = []
    for row in rows:
        if not row:
            continue
        raw = row.get("raw") if isinstance(row, Mapping) else None
        if isinstance(raw, Mapping):
            sources.append(raw)
        sources.append(row)
    return sources


def _provenance_value(field: str, sources: list[Mapping[str, Any]]) -> Any:
    aliases = PROVENANCE_FIELD_ALIASES[field]
    for source in sources:
        for alias in aliases:
            if alias in source and source.get(alias) not in (None, ""):
                return _coerce_provenance_value(field, source.get(alias))
    return None


def _provenance_fields(
    *,
    precompute_row: Mapping[str, Any] | None,
    intended_row: Mapping[str, Any] | None,
    rebudget_final_row: Mapping[str, Any] | None,
    rebudget_skipped_row: Mapping[str, Any] | None,
) -> dict[str, Any]:
    sources = _provenance_sources(
        precompute_row,
        intended_row,
        rebudget_final_row,
        rebudget_skipped_row,
    )
    return {
        field: _provenance_value(field, sources)
        for field in PROVENANCE_FIELD_ALIASES
    }


def _estimated_unexecuted_notional(
    *,
    submitted: bool,
    clipped: bool,
    intended_notional: float | None,
    normalized_notional: float | None,
    precompute_notional: float | None,
    submitted_notional: float | None,
    executable: Mapping[str, Any],
) -> tuple[float | None, str | None]:
    if clipped and intended_notional is not None and submitted_notional is not None:
        return max(0.0, float(intended_notional) - float(submitted_notional)), "intended_minus_submitted_notional"
    if submitted:
        return None, None
    if intended_notional is not None:
        return float(intended_notional), "intended_order_notional"
    if normalized_notional is not None:
        reason = _text(executable.get("suppression_reason")) or "not_submitted"
        return float(normalized_notional), f"normalized_notional:{reason}"
    if precompute_notional is not None:
        return float(precompute_notional), "precompute_notional"
    return None, None


def _candidate_reason(
    *,
    side: str,
    executable: Mapping[str, Any],
    reached_intended_orders: bool,
    intended_payload: Mapping[str, Any],
    rebudget_enabled: bool,
    rebudget_status: str,
    rebudget_final_row: Mapping[str, Any] | None,
    rebudget_skipped_row: Mapping[str, Any] | None,
    submitted: bool,
    accepted: bool,
    rejected: bool,
    filled: bool,
) -> tuple[str | None, str | None]:
    if executable.get("passed_min_notional") is False:
        return str(executable.get("suppression_reason") or "executable_filter_drop"), "executable_filter"
    if not reached_intended_orders:
        if bool(intended_payload.get("execution_blocked")):
            return "execution_gate_blocked_before_submission", "intended_orders"
        return "not_in_intended_orders_after_executable_filter", "intended_orders"
    if side == "BUY" and rebudget_enabled:
        if rebudget_skipped_row:
            return _reason_from_rebudget(rebudget_skipped_row) or "post_sell_rebudget_suppressed", "post_sell_rebudget"
        if rebudget_final_row:
            reason = _reason_from_rebudget(rebudget_final_row)
            if reason and "clip" in reason:
                return reason, "post_sell_rebudget"
            return None, None
        if not submitted:
            status = rebudget_status or "UNKNOWN"
            return f"post_sell_rebudget_no_candidate_recorded:{status}", "post_sell_rebudget"
    if not submitted:
        return "not_in_broker_submission_payload", "broker_submission"
    if rejected:
        return "broker_rejected", "broker_response"
    if submitted and not accepted:
        return "broker_response_not_accepted", "broker_response"
    if accepted and not filled:
        return "accepted_not_filled_at_posttrade_reconciliation", "posttrade_reconciliation"
    return None, None


def _row_stage_summary(
    *,
    precompute_row: Mapping[str, Any] | None,
    executable: Mapping[str, Any],
    intended_row: Mapping[str, Any] | None,
    rebudget_row: Mapping[str, Any] | None,
    rebudget_skipped_row: Mapping[str, Any] | None,
    broker_summary: Mapping[str, Any],
    recon_status: str | None,
) -> dict[str, Any]:
    return {
        "precompute_payload": {
            "present": bool(precompute_row),
            "code_path": CODE_PATHS["precompute_payload"],
        },
        "executable_filter": {
            "present": precompute_row is not None,
            "passed_min_notional": executable.get("passed_min_notional"),
            "suppression_reason": executable.get("suppression_reason"),
            "code_path": CODE_PATHS["executable_filter"],
        },
        "intended_orders": {
            "present": bool(intended_row),
            "code_path": CODE_PATHS["intended_orders"],
        },
        "post_sell_rebudget": {
            "present": bool(rebudget_row or rebudget_skipped_row),
            "status": "submitted" if rebudget_row else ("skipped" if rebudget_skipped_row else None),
            "reason": _reason_from_rebudget(rebudget_row or rebudget_skipped_row or {}),
            "code_path": CODE_PATHS["post_sell_rebudget"],
        },
        "broker_submission": {
            "submitted": bool(broker_summary.get("submitted")),
            "code_path": CODE_PATHS["broker_submission"],
        },
        "broker_response": {
            "accepted": bool(broker_summary.get("accepted")),
            "rejected": bool(broker_summary.get("rejected")),
            "statuses": list(broker_summary.get("statuses") or []),
            "code_path": CODE_PATHS["broker_response"],
        },
        "posttrade_reconciliation": {
            "filled": bool(broker_summary.get("filled")),
            "status": recon_status,
            "code_path": CODE_PATHS["posttrade_reconciliation"],
        },
    }


def build_candidate_trade_lifecycle(
    *,
    run_id: str,
    trade_date: str,
    run_root: str | Path,
    planned_payload: Mapping[str, Any] | None,
    paper_summary: Mapping[str, Any] | None,
    execution_payload: Mapping[str, Any] | None = None,
    min_trade_dollars: float | None = None,
    allow_fractional: bool | None = None,
) -> dict[str, Any]:
    run_root = Path(run_root)
    planned_payload = _as_dict(planned_payload)
    paper_summary = _as_dict(paper_summary)
    execution_payload = _as_dict(execution_payload)
    min_trade = float(
        min_trade_dollars
        if min_trade_dollars is not None
        else paper_summary.get("min_trade_dollars") or execution_payload.get("min_trade_dollars") or 100.0
    )
    fractional = bool(
        allow_fractional
        if allow_fractional is not None
        else paper_summary.get("allow_fractional")
        if paper_summary.get("allow_fractional") is not None
        else execution_payload.get("allow_fractional")
        if execution_payload.get("allow_fractional") is not None
        else True
    )

    precompute_rows = _normalized_precompute_rows(planned_payload)
    intended_payload, intended_rows, intended_path = _load_intended_orders(run_root, trade_date)
    rebudget_payload, rebudget_path = _load_post_sell_rebudget(
        run_root,
        trade_date,
        paper_summary,
        execution_payload,
    )
    recon_payload, recon_path = _load_reconciliation(run_root, trade_date, paper_summary, execution_payload)
    rebudget_final_rows, rebudget_skipped_rows = _rebudget_indexes(rebudget_payload)
    broker_rows = _broker_indexes(paper_summary, execution_payload)
    rebudget_enabled = bool(rebudget_payload.get("enabled"))
    rebudget_status = _text(rebudget_payload.get("status")).upper()
    recon_status = _text(
        paper_summary.get("posttrade_recon_status")
        or execution_payload.get("posttrade_recon_status")
        or recon_payload.get("status")
    ) or None

    candidates: list[dict[str, Any]] = []
    for key in _candidate_sources(
        precompute_rows,
        intended_rows,
        rebudget_final_rows,
        rebudget_skipped_rows,
        broker_rows,
    ):
        ticker, side = key
        precompute_row = precompute_rows.get(key)
        intended_row = intended_rows.get(key)
        rebudget_final_row = rebudget_final_rows.get(key)
        rebudget_skipped_row = rebudget_skipped_rows.get(key)
        broker_summary = _summarize_broker_rows(broker_rows.get(key, []))
        executable = _executable_filter_stage(
            precompute_row,
            min_trade_dollars=min_trade,
            allow_fractional=fractional,
        )
        reached_intended_orders = bool(intended_row)
        submitted = bool(broker_summary.get("submitted"))
        accepted = bool(broker_summary.get("accepted"))
        rejected = bool(broker_summary.get("rejected"))
        filled = bool(broker_summary.get("filled"))
        reason, decision_stage = _candidate_reason(
            side=side,
            executable=executable,
            reached_intended_orders=reached_intended_orders,
            intended_payload=intended_payload,
            rebudget_enabled=rebudget_enabled,
            rebudget_status=rebudget_status,
            rebudget_final_row=rebudget_final_row,
            rebudget_skipped_row=rebudget_skipped_row,
            submitted=submitted,
            accepted=accepted,
            rejected=rejected,
            filled=filled,
        )
        code_path = CODE_PATHS.get(decision_stage or "", {}) if decision_stage else None
        candidate_source = "precompute_payload" if precompute_row else (
            "post_sell_rebudget_only" if (rebudget_final_row or rebudget_skipped_row) else "broker_or_intended_only"
        )
        normalized_notional = executable.get("normalized_notional")
        intended_notional = _notional(intended_row or {}) if intended_row else None
        submitted_shares = broker_summary.get("submitted_shares")
        clipped = bool(
            rebudget_final_row
            and reason
            and "clip" in str(reason).lower()
        )
        if rebudget_final_row and not clipped and intended_notional is not None:
            final_notional = _notional(rebudget_final_row)
            clipped = final_notional is not None and final_notional + 1e-9 < float(intended_notional)
            if clipped and not reason:
                reason = "post_sell_rebudget_capital_clipped"
                decision_stage = "post_sell_rebudget"
                code_path = CODE_PATHS["post_sell_rebudget"]
        provenance = _provenance_fields(
            precompute_row=precompute_row,
            intended_row=intended_row,
            rebudget_final_row=rebudget_final_row,
            rebudget_skipped_row=rebudget_skipped_row,
        )
        estimated_unexecuted_notional, opportunity_cost_basis = _estimated_unexecuted_notional(
            submitted=submitted,
            clipped=clipped,
            intended_notional=intended_notional,
            normalized_notional=_float(normalized_notional, None),
            precompute_notional=_float(precompute_row.get("notional"), None) if precompute_row else None,
            submitted_notional=(
                _notional(rebudget_final_row)
                if rebudget_final_row
                else _float(broker_summary.get("submitted_notional"), None)
            ),
            executable=executable,
        )

        candidates.append(
            {
                "ticker": ticker,
                "side": side,
                "candidate_source": candidate_source,
                "precompute_shares": precompute_row.get("shares") if precompute_row else None,
                "precompute_price": precompute_row.get("price") if precompute_row else None,
                "precompute_notional": precompute_row.get("notional") if precompute_row else None,
                "precompute_reason": precompute_row.get("reason") if precompute_row else None,
                "normalized_executable_shares": executable.get("normalized_shares"),
                "normalized_executable_price": executable.get("normalized_price"),
                "normalized_executable_notional": normalized_notional,
                "passed_min_notional": executable.get("passed_min_notional"),
                "reached_intended_orders": reached_intended_orders,
                "intended_shares": _quantity(intended_row or {}) if intended_row else None,
                "intended_price": _price(intended_row or {}) if intended_row else None,
                "intended_notional": intended_notional,
                "post_sell_rebudget_status": (
                    "submitted"
                    if rebudget_final_row
                    else "skipped"
                    if rebudget_skipped_row
                    else rebudget_status
                    if side == "BUY" and rebudget_enabled
                    else "not_applicable"
                ),
                "post_sell_rebudget_reason": _reason_from_rebudget(
                    rebudget_final_row or rebudget_skipped_row or {}
                ),
                "submitted": submitted,
                "accepted": accepted,
                "filled": filled,
                "rejected": rejected,
                "final_submitted_shares": submitted_shares,
                "final_filled_shares": broker_summary.get("filled_shares"),
                "broker_statuses": broker_summary.get("statuses"),
                "order_ids": broker_summary.get("order_ids"),
                "broker_order_ids": broker_summary.get("broker_order_ids"),
                "clipped": clipped,
                "suppression_or_clipping_reason": reason,
                "decision_stage": decision_stage,
                "decision_reason": reason,
                "code_stage_responsible": (
                    f"{code_path.get('file')}:{code_path.get('function')}"
                    if isinstance(code_path, Mapping) and code_path
                    else None
                ),
                "code_path_responsible": code_path,
                "responsible_code_path": code_path,
                "sleeve_id": provenance["sleeve_id"],
                "strategy_id": provenance["strategy_id"],
                "source_model": provenance["source_model"],
                "candidate_rank": provenance["candidate_rank"],
                "alpha_rank": provenance["alpha_rank"],
                "conviction_score": provenance["conviction_score"],
                "target_weight": provenance["target_weight"],
                "current_weight": provenance["current_weight"],
                "target_notional": provenance["target_notional"],
                "current_notional": provenance["current_notional"],
                "delta_notional": provenance["delta_notional"],
                "capital_rank": provenance["capital_rank"],
                "estimated_unexecuted_notional": estimated_unexecuted_notional,
                "opportunity_cost_basis": opportunity_cost_basis,
                "stages": _row_stage_summary(
                    precompute_row=precompute_row,
                    executable=executable,
                    intended_row=intended_row,
                    rebudget_row=rebudget_final_row,
                    rebudget_skipped_row=rebudget_skipped_row,
                    broker_summary=broker_summary,
                    recon_status=recon_status,
                ),
            }
        )

    source_artifacts = {
        "precompute_payload": str(Path("outputs") / "precompute" / trade_date / "planned_execution_payload.json"),
        "intended_orders": str(intended_path),
        "post_sell_rebudget": str(rebudget_path),
        "posttrade_reconciliation": str(recon_path) if recon_path is not None else None,
    }
    counts = {
        "precompute_candidates": len(precompute_rows),
        "candidate_rows": len(candidates),
        "passed_executable_filter": sum(1 for row in candidates if row.get("passed_min_notional") is True),
        "filtered_executable": sum(1 for row in candidates if row.get("passed_min_notional") is False),
        "intended_orders": sum(1 for row in candidates if row.get("reached_intended_orders")),
        "submitted": sum(1 for row in candidates if row.get("submitted")),
        "accepted": sum(1 for row in candidates if row.get("accepted")),
        "filled": sum(1 for row in candidates if row.get("filled")),
        "rejected": sum(1 for row in candidates if row.get("rejected")),
        "clipped": sum(1 for row in candidates if row.get("clipped")),
        "suppressed": sum(
            1
            for row in candidates
            if not row.get("submitted") and row.get("suppression_or_clipping_reason")
        ),
        "suppression_reason_counts": {
            reason: sum(
                1
                for row in candidates
                if not row.get("submitted") and row.get("suppression_or_clipping_reason") == reason
            )
            for reason in sorted({
                str(row.get("suppression_or_clipping_reason"))
                for row in candidates
                if not row.get("submitted") and row.get("suppression_or_clipping_reason")
            })
        },
        "clipping_reason_counts": {
            reason: sum(
                1
                for row in candidates
                if row.get("clipped") and row.get("suppression_or_clipping_reason") == reason
            )
            for reason in sorted({
                str(row.get("suppression_or_clipping_reason"))
                for row in candidates
                if row.get("clipped") and row.get("suppression_or_clipping_reason")
            })
        },
    }
    return {
        "schema_version": LIFECYCLE_SCHEMA_VERSION,
        "trade_date": str(trade_date),
        "run_id": str(run_id),
        "stages": list(STAGES),
        "provenance_fields": sorted(PROVENANCE_FIELD_ALIASES),
        "source_artifacts": source_artifacts,
        "code_paths": CODE_PATHS,
        "execution_config": {
            "min_trade_dollars": min_trade,
            "allow_fractional": fractional,
        },
        "counts": counts,
        "candidates": candidates,
    }


def write_candidate_trade_lifecycle(
    *,
    run_id: str,
    trade_date: str,
    run_root: str | Path,
    planned_payload: Mapping[str, Any] | None,
    paper_summary: Mapping[str, Any] | None,
    execution_payload: Mapping[str, Any] | None = None,
    min_trade_dollars: float | None = None,
    allow_fractional: bool | None = None,
) -> tuple[Path, dict[str, Any]]:
    payload = build_candidate_trade_lifecycle(
        run_id=run_id,
        trade_date=trade_date,
        run_root=run_root,
        planned_payload=planned_payload,
        paper_summary=paper_summary,
        execution_payload=execution_payload,
        min_trade_dollars=min_trade_dollars,
        allow_fractional=allow_fractional,
    )
    out_dir = Path(run_root) / "audit"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"candidate_trade_lifecycle_{trade_date}.json"
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return out_path, payload

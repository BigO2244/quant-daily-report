"""Reporting-only portfolio construction provenance artifact.

This module joins already-written target, holdings, planned-trade, and
candidate lifecycle artifacts. It does not participate in optimization, sizing,
broker submission, or execution decisions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = "construction_provenance.v1"
UNAVAILABLE = "unavailable"

TARGET_SCORE_FALLBACK_SOURCES = {
    "",
    "allocation_weight",
    "allocation_weights",
    "final_allocation_weight",
    "target_weight",
    "target_allocation_weight",
    "weight",
    "final_target_weight",
    "fallback_target_weight",
    "post_cap_target_weight",
    "pre_cap_target_weight",
    "inferred_from_target_weight",
    "inferred_from_weight",
    "inferred_from_allocation_weight",
}


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _ticker(value: Any) -> str:
    return _text(value).upper()


def _float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _read_json(path: Path | None) -> Any:
    if path is None:
        return None
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return None


def _resolve_path(value: str | Path | None, repo_root: str | Path | None = None) -> Path | None:
    raw = _text(value)
    if not raw:
        return None
    path = Path(raw)
    if path.is_absolute():
        return path
    return (Path(repo_root) if repo_root is not None else Path.cwd()) / path


def _source_status(path: Path | None, payload: Mapping[str, Any] | None) -> str:
    if payload:
        return "PROVIDED"
    if path is None:
        return "NOT_PROVIDED"
    return "FOUND" if path.exists() else "MISSING"


def _rows_from_payload(payload: Mapping[str, Any] | None, *keys: str) -> list[dict[str, Any]]:
    data = _as_dict(payload)
    for key in keys:
        rows = data.get(key)
        if isinstance(rows, list):
            return [_as_dict(row) for row in rows if isinstance(row, Mapping)]
    return []


def _target_rows(payload: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    rows = _rows_from_payload(payload, "signals", "targets", "target_weights", "positions")
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        ticker = _ticker(row.get("ticker") or row.get("symbol"))
        if not ticker or ticker == "CASH":
            continue
        out[ticker] = row
    return out


def _current_rows(payload: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    rows = _rows_from_payload(payload, "positions", "holdings", "current_positions")
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        ticker = _ticker(row.get("ticker") or row.get("symbol"))
        if not ticker or ticker == "CASH":
            continue
        out[ticker] = row
    return out


def _planned_rows(payload: Mapping[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    rows = _rows_from_payload(payload, "trades", "proposed_trades_intent", "planned_trades")
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        ticker = _ticker(row.get("ticker") or row.get("symbol"))
        if ticker and ticker != "CASH":
            out.setdefault(ticker, []).append(row)
    return out


def _lifecycle_rows(payload: Mapping[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    rows = _rows_from_payload(payload, "candidates", "candidate_trade_lifecycle")
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        ticker = _ticker(row.get("ticker") or row.get("symbol"))
        if ticker and ticker != "CASH":
            out.setdefault(ticker, []).append(row)
    return out


def _first_lifecycle_value(rows: list[dict[str, Any]], *keys: str) -> Any:
    for row in rows:
        for key in keys:
            value = row.get(key)
            if value not in (None, ""):
                return value
    return None


def _first_with_source(*values: tuple[Any, str]) -> tuple[Any, str]:
    for value, source in values:
        if value not in (None, ""):
            return value, source
    return None, UNAVAILABLE


def _first_lifecycle_value_with_source(rows: list[dict[str, Any]], *keys: str) -> tuple[Any, str]:
    for row in rows:
        for key in keys:
            value = row.get(key)
            if value not in (None, ""):
                return value, f"candidate_trade_lifecycle.{key}"
    return None, UNAVAILABLE


def _score_from_sources(
    target_row: Mapping[str, Any],
    lifecycle_rows: list[dict[str, Any]],
) -> tuple[float | None, str]:
    lifecycle_score = _float(_first_lifecycle_value(lifecycle_rows, "conviction_score"))
    if lifecycle_score is not None:
        return lifecycle_score, "candidate_trade_lifecycle.conviction_score"
    for row in lifecycle_rows:
        raw_score = _float(row.get("raw_score") or row.get("score"))
        raw_source = _text(row.get("raw_score_source") or row.get("score_source"))
        if raw_score is not None and raw_source.lower() not in TARGET_SCORE_FALLBACK_SOURCES:
            return raw_score, raw_source

    raw_score = _float(target_row.get("raw_score"))
    raw_source = _text(target_row.get("raw_score_source") or target_row.get("score_source"))
    if raw_score is not None and raw_source.lower() not in TARGET_SCORE_FALLBACK_SOURCES:
        return raw_score, raw_source

    return None, UNAVAILABLE


def _sleeves(target_row: Mapping[str, Any], lifecycle_rows: list[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for raw in (
        target_row.get("sleeve_source"),
        target_row.get("sleeve_sources"),
        target_row.get("sleeve"),
        target_row.get("sleeve_name"),
        _first_lifecycle_value(lifecycle_rows, "sleeve_id", "sleeve", "sleeve_name"),
    ):
        if isinstance(raw, list):
            values.extend(str(item).strip() for item in raw if str(item).strip())
        elif raw not in (None, ""):
            values.extend(part.strip() for part in str(raw).split(",") if part.strip())
    deduped: list[str] = []
    for value in values:
        if value and value not in deduped:
            deduped.append(value)
    return deduped


def _active_constraints(
    *,
    planned_payload: Mapping[str, Any],
    lifecycle_rows: list[dict[str, Any]],
    planned_rows: list[dict[str, Any]],
) -> list[str]:
    constraints: list[str] = []
    risk_meta = _as_dict(planned_payload.get("risk_meta"))
    if risk_meta.get("turnover_scaled") is True:
        scale = risk_meta.get("turnover_scale")
        constraints.append(f"turnover_scaled:{scale}" if scale not in (None, "") else "turnover_scaled")
    if planned_payload.get("min_trade_dollars") not in (None, ""):
        constraints.append(f"min_trade_dollars:{planned_payload.get('min_trade_dollars')}")
    if planned_payload.get("cash_target_weight") not in (None, ""):
        constraints.append(f"cash_target_weight:{planned_payload.get('cash_target_weight')}")
    for row in lifecycle_rows:
        reason = _text(row.get("suppression_or_clipping_reason") or row.get("decision_reason"))
        stage = _text(row.get("decision_stage"))
        if reason:
            constraints.append(f"{stage}:{reason}" if stage else reason)
        if row.get("clipped") is True and "clipped" not in constraints:
            constraints.append("clipped")
    for row in planned_rows:
        reason = _text(row.get("block_reason") or row.get("reason"))
        if reason and reason not in {"rebalance_to_target", "removed_from_targets"}:
            constraints.append(reason)
    deduped: list[str] = []
    for value in constraints:
        if value and value not in deduped:
            deduped.append(value)
    return deduped or [UNAVAILABLE]


def _trade_delta_notional(rows: list[dict[str, Any]], lifecycle_rows: list[dict[str, Any]]) -> float | None:
    value = _float(_first_lifecycle_value(lifecycle_rows, "delta_notional"))
    if value is not None:
        return value
    total = 0.0
    seen = False
    for row in rows:
        notional = _float(row.get("notional"))
        if notional is None:
            continue
        side = _text(row.get("side")).upper()
        total += -abs(notional) if side in {"SELL", "CLOSE", "REDUCE"} else abs(notional)
        seen = True
    return total if seen else None


def _construction_action(
    *,
    current_weight: float | None,
    final_target_weight: float | None,
    lifecycle_rows: list[dict[str, Any]],
    planned_rows: list[dict[str, Any]],
) -> str:
    if any(
        row.get("submitted") is False and row.get("suppression_or_clipping_reason")
        for row in lifecycle_rows
    ):
        return "skipped"
    sides = {_text(row.get("side")).upper() for row in planned_rows if _text(row.get("side"))}
    if final_target_weight is not None and final_target_weight <= 1e-12 and current_weight and current_weight > 1e-12:
        return "removed"
    if "SELL" in sides or "CLOSE" in sides or "REDUCE" in sides:
        return "removed" if final_target_weight in (None, 0.0) else "reduced"
    if current_weight is not None and current_weight <= 1e-12 and final_target_weight and final_target_weight > 1e-12:
        return "added"
    if current_weight is not None and final_target_weight is not None:
        if final_target_weight + 1e-12 < current_weight:
            return "reduced"
        return "retained"
    return UNAVAILABLE


def _field_sources(
    row: Mapping[str, Any],
    *,
    sleeve_sources_source: str,
    sleeve_local_rank_source: str,
    global_rank_source: str,
    pre_cap_target_weight_source: str,
    post_cap_target_weight_source: str,
    current_weight_source: str,
    final_target_weight_source: str,
    trade_delta_source: str,
    suppression_block_reason_source: str,
) -> dict[str, str]:
    sources = {
        "sleeve_sources": sleeve_sources_source,
        "sleeve_local_rank": sleeve_local_rank_source,
        "global_rank": global_rank_source,
        "raw_score": row.get("score_source") if row.get("raw_score") != UNAVAILABLE else UNAVAILABLE,
        "pre_cap_target_weight": pre_cap_target_weight_source,
        "post_cap_target_weight": post_cap_target_weight_source,
        "current_weight": current_weight_source,
        "final_target_weight": final_target_weight_source,
        "trade_delta": trade_delta_source,
        "suppression_block_reason": suppression_block_reason_source,
        "active_constraints": "planned_payload_and_candidate_trade_lifecycle",
        "construction_action": "derived_from_artifact_weights_and_lifecycle",
    }
    return {key: str(value or UNAVAILABLE) for key, value in sources.items()}


def build_construction_provenance(
    *,
    trade_date: str,
    run_id: str | None = None,
    signals_payload: Mapping[str, Any] | None = None,
    planned_payload: Mapping[str, Any] | None = None,
    candidate_lifecycle_payload: Mapping[str, Any] | None = None,
    current_positions_payload: Mapping[str, Any] | None = None,
    source_artifact_paths: Mapping[str, str | None] | None = None,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    paths = dict(source_artifact_paths or {})
    resolved_paths = {
        key: _resolve_path(value, repo_root)
        for key, value in paths.items()
    }
    signals = _as_dict(signals_payload or _read_json(resolved_paths.get("signals")))
    planned = _as_dict(planned_payload or _read_json(resolved_paths.get("planned_payload")))
    lifecycle = _as_dict(candidate_lifecycle_payload or _read_json(resolved_paths.get("candidate_trade_lifecycle")))
    current = _as_dict(current_positions_payload or _read_json(resolved_paths.get("current_positions")))

    target_by_ticker = _target_rows(signals)
    current_by_ticker = _current_rows(current)
    planned_by_ticker = _planned_rows(planned)
    lifecycle_by_ticker = _lifecycle_rows(lifecycle)
    target_book_present = any(isinstance(signals.get(key), list) for key in ("signals", "targets", "target_weights", "positions"))
    current_book_present = current.get("ok") is not False and any(
        isinstance(current.get(key), list)
        for key in ("positions", "holdings", "current_positions")
    )
    tickers = sorted(
        set(target_by_ticker)
        | set(current_by_ticker)
        | set(planned_by_ticker)
        | set(lifecycle_by_ticker)
    )

    rows: list[dict[str, Any]] = []
    for ticker in tickers:
        target_row = target_by_ticker.get(ticker, {})
        current_row = current_by_ticker.get(ticker, {})
        planned_rows = planned_by_ticker.get(ticker, [])
        lifecycle_rows = lifecycle_by_ticker.get(ticker, [])

        lifecycle_target_weight, lifecycle_target_weight_source = _first_lifecycle_value_with_source(
            lifecycle_rows,
            "target_weight",
        )
        final_weight_raw, final_weight_source = _first_with_source(
            (target_row.get("final_target_weight"), "signals.final_target_weight"),
            (target_row.get("target_weight"), "signals.target_weight"),
            (lifecycle_target_weight, lifecycle_target_weight_source),
        )
        final_weight = _float(final_weight_raw)
        if final_weight is None and target_book_present:
            final_weight = 0.0
            final_weight_source = "signals.absence_implies_zero"

        lifecycle_pre_cap_weight, lifecycle_pre_cap_weight_source = _first_lifecycle_value_with_source(
            lifecycle_rows,
            "pre_cap_target_weight",
            "uncapped_target_weight",
        )
        pre_cap_weight_raw, pre_cap_weight_source = _first_with_source(
            (target_row.get("pre_cap_target_weight"), "signals.pre_cap_target_weight"),
            (target_row.get("uncapped_target_weight"), "signals.uncapped_target_weight"),
            (target_row.get("raw_target_weight"), "signals.raw_target_weight"),
            (lifecycle_pre_cap_weight, lifecycle_pre_cap_weight_source),
        )
        pre_cap_weight = _float(pre_cap_weight_raw)

        post_cap_weight_raw, post_cap_weight_source = _first_with_source(
            (target_row.get("post_cap_target_weight"), "signals.post_cap_target_weight"),
            (target_row.get("target_weight"), "signals.target_weight"),
            (final_weight, final_weight_source),
        )
        post_cap_weight = _float(post_cap_weight_raw)

        lifecycle_current_weight, lifecycle_current_weight_source = _first_lifecycle_value_with_source(
            lifecycle_rows,
            "current_weight",
        )
        current_weight_raw, current_weight_source = _first_with_source(
            (current_row.get("current_weight"), "current_positions.current_weight"),
            (current_row.get("weight"), "current_positions.weight"),
            (lifecycle_current_weight, lifecycle_current_weight_source),
        )
        current_weight = _float(current_weight_raw)
        if current_weight is None and current_book_present:
            current_weight = 0.0
            current_weight_source = "current_positions.absence_implies_zero"
        score, score_source = _score_from_sources(target_row, lifecycle_rows)
        trade_delta_weight = (
            float(final_weight) - float(current_weight)
            if final_weight is not None and current_weight is not None
            else None
        )
        suppression_reason, suppression_reason_source = _first_lifecycle_value_with_source(
            lifecycle_rows,
            "suppression_or_clipping_reason",
            "decision_reason",
            "post_sell_rebudget_reason",
        )
        trade_delta_notional = _trade_delta_notional(planned_rows, lifecycle_rows)
        trade_delta_source = (
            "final_target_weight_minus_current_weight"
            if trade_delta_weight is not None
            else "planned_payload_or_lifecycle"
            if trade_delta_notional is not None
            else UNAVAILABLE
        )

        sleeve_sources = _sleeves(target_row, lifecycle_rows)
        sleeve_sources_source = (
            "signals_or_candidate_trade_lifecycle.sleeve"
            if sleeve_sources
            else UNAVAILABLE
        )
        sleeve_local_rank = _int(_first_lifecycle_value(lifecycle_rows, "candidate_rank", "alpha_rank"))
        sleeve_local_rank_source = (
            "candidate_trade_lifecycle.candidate_rank_or_alpha_rank"
            if sleeve_local_rank is not None
            else UNAVAILABLE
        )
        lifecycle_global_rank, lifecycle_global_rank_source = _first_lifecycle_value_with_source(
            lifecycle_rows,
            "global_rank",
            "capital_rank",
        )
        global_rank_raw, global_rank_source = _first_with_source(
            (target_row.get("global_rank"), "signals.global_rank"),
            (lifecycle_global_rank, lifecycle_global_rank_source),
        )
        global_rank = _int(global_rank_raw)
        row = {
            "ticker": ticker,
            "sleeve_sources": sleeve_sources or [UNAVAILABLE],
            "sleeve_local_rank": sleeve_local_rank if sleeve_local_rank is not None else UNAVAILABLE,
            "global_rank": global_rank if global_rank is not None else UNAVAILABLE,
            "raw_score": score if score is not None else UNAVAILABLE,
            "score_source": score_source,
            "pre_cap_target_weight": pre_cap_weight if pre_cap_weight is not None else UNAVAILABLE,
            "post_cap_target_weight": post_cap_weight if post_cap_weight is not None else UNAVAILABLE,
            "current_weight": current_weight if current_weight is not None else UNAVAILABLE,
            "final_target_weight": final_weight if final_weight is not None else UNAVAILABLE,
            "trade_delta_weight": trade_delta_weight if trade_delta_weight is not None else UNAVAILABLE,
            "trade_delta_notional": trade_delta_notional if trade_delta_notional is not None else UNAVAILABLE,
            "trade_sides": sorted({_text(row.get("side")).upper() for row in planned_rows if _text(row.get("side"))}) or [UNAVAILABLE],
            "suppression_block_reason": _text(suppression_reason) or UNAVAILABLE,
            "active_constraints": _active_constraints(
                planned_payload=planned,
                lifecycle_rows=lifecycle_rows,
                planned_rows=planned_rows,
            ),
            "construction_action": _construction_action(
                current_weight=current_weight,
                final_target_weight=final_weight,
                lifecycle_rows=lifecycle_rows,
                planned_rows=planned_rows,
            ),
        }
        row["field_sources"] = _field_sources(
            row,
            sleeve_sources_source=sleeve_sources_source,
            sleeve_local_rank_source=sleeve_local_rank_source,
            global_rank_source=global_rank_source if global_rank is not None else UNAVAILABLE,
            pre_cap_target_weight_source=pre_cap_weight_source if pre_cap_weight is not None else UNAVAILABLE,
            post_cap_target_weight_source=post_cap_weight_source if post_cap_weight is not None else UNAVAILABLE,
            current_weight_source=current_weight_source if current_weight is not None else UNAVAILABLE,
            final_target_weight_source=final_weight_source if final_weight is not None else UNAVAILABLE,
            trade_delta_source=trade_delta_source,
            suppression_block_reason_source=suppression_reason_source if suppression_reason else UNAVAILABLE,
        )
        row["unavailable_fields"] = sorted(
            key for key, value in row.items()
            if value == UNAVAILABLE or value == [UNAVAILABLE]
        )
        rows.append(row)

    action_counts = {
        action: sum(1 for row in rows if row.get("construction_action") == action)
        for action in sorted({str(row.get("construction_action")) for row in rows})
    }
    constraint_counts: dict[str, int] = {}
    for row in rows:
        for constraint in row.get("active_constraints") or []:
            constraint_counts[str(constraint)] = constraint_counts.get(str(constraint), 0) + 1

    return {
        "schema_version": SCHEMA_VERSION,
        "trade_date": trade_date,
        "run_id": run_id,
        "mode": "REPORTING_ONLY",
        "trading_behavior_changed": False,
        "source_artifacts": {
            key: {
                "path": paths.get(key),
                "status": _source_status(resolved_paths.get(key), {
                    "signals": signals,
                    "planned_payload": planned,
                    "candidate_trade_lifecycle": lifecycle,
                    "current_positions": current,
                }.get(key)),
            }
            for key in ("signals", "planned_payload", "candidate_trade_lifecycle", "current_positions")
        },
        "summary": {
            "row_count": len(rows),
            "status": "OK" if rows else "NO_ROWS",
            "action_counts": action_counts,
            "constraint_counts": constraint_counts,
            "score_backed_count": sum(1 for row in rows if row.get("raw_score") != UNAVAILABLE),
            "unavailable_score_count": sum(1 for row in rows if row.get("raw_score") == UNAVAILABLE),
        },
        "rows": rows,
    }


def write_construction_provenance(
    *,
    run_root: str | Path,
    trade_date: str,
    run_id: str | None = None,
    signals_payload: Mapping[str, Any] | None = None,
    planned_payload: Mapping[str, Any] | None = None,
    candidate_lifecycle_payload: Mapping[str, Any] | None = None,
    current_positions_payload: Mapping[str, Any] | None = None,
    source_artifact_paths: Mapping[str, str | None] | None = None,
    repo_root: str | Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    payload = build_construction_provenance(
        trade_date=trade_date,
        run_id=run_id,
        signals_payload=signals_payload,
        planned_payload=planned_payload,
        candidate_lifecycle_payload=candidate_lifecycle_payload,
        current_positions_payload=current_positions_payload,
        source_artifact_paths=source_artifact_paths,
        repo_root=repo_root,
    )
    out_dir = Path(run_root) / "audit"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"construction_provenance_{trade_date}.json"
    out_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    return out_path, payload

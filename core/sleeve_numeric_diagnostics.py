from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


REASON_SLEEVE_TERMINAL_EQUITY_NAN = "sleeve_terminal_equity_nan"
REASON_INPUT_PRICE_NAN = "input_price_nan"
REASON_MTM_EQUITY_NON_FINITE = "mtm_equity_non_finite"
REASON_TRADE_PNL_NON_FINITE = "trade_pnl_non_finite"
REASON_SLEEVE_STRENGTH_NON_FINITE = "sleeve_strength_non_finite"
REASON_NAN_TRACE_MISSING = "nan_trace_missing"


def is_finite_number(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def json_safe_value(value: Any) -> Any:
    if value is pd.NA:
        return None
    if pd.isna(value):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    try:
        if not math.isfinite(float(value)):
            return str(value)
    except (TypeError, ValueError):
        pass
    return value


def diagnostic_event(
    *,
    sleeve_id: str,
    calculation_stage: str,
    reason_code: str,
    field: str | None = None,
    value: Any = None,
    ticker: str | None = None,
    date: Any = None,
    index: Any = None,
    source_artifact: str | None = None,
    downstream_effect: str | None = None,
) -> dict[str, Any]:
    return {
        "sleeve_id": sleeve_id,
        "calculation_stage": calculation_stage,
        "reason_code": reason_code,
        "ticker": ticker,
        "date": json_safe_value(date),
        "index": json_safe_value(index),
        "field": field,
        "value": json_safe_value(value),
        "source_artifact": source_artifact,
        "downstream_effect": downstream_effect,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }


def first_non_finite_in_frame(
    frame: pd.DataFrame,
    *,
    sleeve_id: str,
    fields: list[str],
    calculation_stage: str,
    reason_code: str,
    source_artifact: str | None = None,
    downstream_effect: str | None = None,
) -> dict[str, Any] | None:
    if frame is None or frame.empty:
        return None
    for field in fields:
        if field not in frame.columns:
            continue
        values = pd.to_numeric(frame[field], errors="coerce")
        bad_mask = ~values.apply(is_finite_number)
        if not bool(bad_mask.any()):
            continue
        row = frame.loc[bad_mask].iloc[0]
        return diagnostic_event(
            sleeve_id=sleeve_id,
            calculation_stage=calculation_stage,
            reason_code=reason_code,
            field=field,
            value=row.get(field),
            ticker=str(row.get("ticker")) if "ticker" in row and pd.notna(row.get("ticker")) else None,
            date=row.get("date") if "date" in row else None,
            index=row.name,
            source_artifact=source_artifact,
            downstream_effect=downstream_effect,
        )
    return None


def build_trace_payload(
    *,
    sleeve_id: str,
    trade_date: str | None,
    reason_code: str,
    invalid_reason: str,
    events: list[dict[str, Any]] | None = None,
    source_artifact: str | None = None,
    downstream_effect: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    observed_events = [dict(event) for event in (events or []) if isinstance(event, dict)]
    if not observed_events:
        observed_events = [
            diagnostic_event(
                sleeve_id=sleeve_id,
                calculation_stage="sleeve_validation",
                reason_code=REASON_NAN_TRACE_MISSING,
                source_artifact=source_artifact,
                downstream_effect=downstream_effect,
            )
        ]
    return {
        "schema_version": 1,
        "artifact_type": "sleeve_numeric_trace",
        "sleeve_id": sleeve_id,
        "trade_date": trade_date,
        "run_id": run_id,
        "status": "BLOCKING",
        "reason_code": reason_code,
        "invalid_reason": invalid_reason,
        "source_artifact": source_artifact,
        "downstream_effect": downstream_effect,
        "first_event": observed_events[0],
        "events": observed_events,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def trace_artifact_name(sleeve_id: str, trade_date: str | None) -> str:
    clean_sleeve = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in sleeve_id)
    clean_date = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in str(trade_date or "unknown"))
    return f"sleeve_numeric_trace_{clean_sleeve}_{clean_date}.json"


def relative_path(path: str | Path | None) -> str | None:
    if path is None:
        return None
    try:
        return str(Path(path))
    except TypeError:
        return str(path)

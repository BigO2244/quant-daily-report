# paper/signals_io.py
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def normalize_weights(
    df: pd.DataFrame, weight_col: str = "target_weight"
) -> pd.DataFrame:
    out = df.copy()
    out[weight_col] = out[weight_col].astype(float)
    s = float(out[weight_col].sum())
    if s <= 0:
        raise ValueError(f"Sum of {weight_col} is <= 0; cannot normalize.")
    out[weight_col] = out[weight_col] / s
    return out


def _coerce_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    try:
        return float(value)
    except Exception:
        return None


def _infer_raw_score(row: pd.Series, fallback_col: str) -> float:
    candidate_cols = (
        "raw_score",
        "signal_strength",
        "score",
        "final_signal",
        "composite_score",
        "trend_score",
        "quality_score",
        fallback_col,
    )
    for col in candidate_cols:
        if col not in row.index:
            continue
        value = _coerce_float(row.get(col))
        if value is not None:
            return float(value)
    return 0.0


def write_signals_snapshot(
    df_targets: pd.DataFrame,
    run_date: str,
    asof_date: str | None = None,
    out_dir: str = "signals",
    cash_target_weight: Optional[float] = None,
    sleeve_col: Optional[str] = "sleeve",
    ticker_col: str = "ticker",
    weight_col: str = "target_weight",
    model_version: str | None = None,
    extra: dict | None = None,
) -> str:
    """
    Writes signals/YYYY-MM-DD.json

    Expected df_targets columns:
      - ticker
      - target_weight
      - optional sleeve

    Returns the filepath written.

    Hardening (2026-04):
      - When cash_target_weight is None we log a WARNING and record
        "cash_target_weight_missing": true in the payload so audits can
        detect cash-less snapshots. In strict mode (env
        SIGNALS_SNAPSHOT_STRICT=1) this raises instead of warning.
      - When cash_target_weight > 0 we emit an explicit CASH row in the
        signals list so the snapshot reflects the full allocator output
        rather than just the normalized equity book. This means the sum
        of `target_weight` across the signals list is 1.0 and the cash
        line is visible to downstream consumers.
      - The output file is fsync'd and its existence + non-empty size
        is verified before returning.
    """
    required = {ticker_col, weight_col}
    missing = [c for c in required if c not in df_targets.columns]
    if missing:
        raise ValueError(
            f"df_targets missing required columns: {missing}. Have: {list(df_targets.columns)}"
        )

    strict_mode = str(os.environ.get("SIGNALS_SNAPSHOT_STRICT", "")).strip() in {
        "1",
        "true",
        "yes",
        "on",
    }
    cash_weight_missing = cash_target_weight is None
    if cash_weight_missing:
        msg = (
            "[SIGNAL_SNAPSHOT] cash_target_weight was not supplied; "
            "snapshot will not include a CASH row. Production callers "
            "should always pass cash_target_weight explicitly."
        )
        if strict_mode:
            raise ValueError(msg + " (SIGNALS_SNAPSHOT_STRICT=1)")
        logger.warning(msg)
        cash_weight = 0.0
    else:
        try:
            cash_weight = float(cash_target_weight)
        except Exception as exc:
            raise ValueError(
                f"cash_target_weight must be a number in [0, 1]; got {cash_target_weight!r}"
            ) from exc
        if cash_weight < 0.0 or cash_weight > 1.0 + 1e-9:
            raise ValueError(
                f"cash_target_weight out of range [0, 1]: {cash_weight}"
            )
        cash_weight = max(0.0, min(1.0, cash_weight))

    df = df_targets[
        [
            c
            for c in [ticker_col, weight_col, sleeve_col]
            if c and c in df_targets.columns
        ]
    ].copy()

    if sleeve_col is None or sleeve_col not in df.columns:
        df["sleeve"] = "core"
        sleeve_col = "sleeve"

    # Clean
    df[ticker_col] = df[ticker_col].astype(str).str.upper().str.strip()
    df = df[df[ticker_col].str.len() > 0].copy()

    # If all equity target weights are zero/non-positive (e.g., breaker
    # lock), treat cash as 100% regardless of what the caller passed.
    weight_sum = float(pd.to_numeric(df.get(weight_col), errors="coerce").fillna(0.0).sum())
    if weight_sum <= 0.0:
        cash_row = {ticker_col: "CASH", weight_col: 1.0}
        if sleeve_col:
            cash_row[sleeve_col] = "core"
        df = pd.DataFrame([cash_row])
        cash_weight = 1.0
    else:
        # Scale equity targets to (1 - cash_weight), then emit an
        # explicit CASH row for the remainder. This makes cash visible
        # in the signals payload instead of being hidden by normalization.
        equity_target = max(0.0, 1.0 - cash_weight)
        if equity_target > 0.0 and weight_sum > 0.0:
            df[weight_col] = df[weight_col].astype(float) * (equity_target / weight_sum)
        else:
            df[weight_col] = 0.0
        if cash_weight > 0.0:
            # Drop pre-existing CASH rows from df_targets to avoid duplication
            df = df[df[ticker_col].str.upper() != "CASH"].copy()
            cash_row = {ticker_col: "CASH", weight_col: float(cash_weight)}
            if sleeve_col:
                cash_row[sleeve_col] = "core"
            df = pd.concat([df, pd.DataFrame([cash_row])], ignore_index=True)

    # Normalize weights to sum to 1 (defensive — any rounding residual)
    df = normalize_weights(df, weight_col=weight_col)

    # Ensure directory + write
    ensure_dir(out_dir)
    out_dir_path = os.path.abspath(out_dir)
    out_path = os.path.join(out_dir_path, f"{run_date}.json")
    if os.path.exists(out_path):
        logger.warning("[SIGNAL_SNAPSHOT] WARN re-run detected for %s", run_date)

    signals_df = df.rename(
        columns={
            ticker_col: "ticker",
            weight_col: "target_weight",
            sleeve_col: "sleeve",
        }
    )
    if "sleeve" not in signals_df.columns:
        signals_df["sleeve"] = "core"
    signals_df["raw_score"] = signals_df.apply(
        lambda row: _infer_raw_score(row, weight_col),
        axis=1,
    )
    cash_mask = signals_df["ticker"].astype(str).str.upper().eq("CASH")
    if cash_mask.any():
        signals_df.loc[cash_mask, "raw_score"] = 0.0
    signals = signals_df[["ticker", "target_weight", "sleeve", "raw_score"]].to_dict(
        orient="records"
    )

    payload = {
        "snapshot_date": run_date,
        "meta": {
            "trade_date": run_date,
            "asof_date": asof_date or run_date,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "signals": signals,
    }
    if model_version:
        payload["meta"]["model_version"] = model_version
    # Always record the cash line in the payload header as well as in the
    # signals list, so consumers that only look at the meta block can still
    # see what fraction of the book was held in cash.
    payload["cash_target_weight"] = float(cash_weight)
    if cash_weight_missing:
        payload["cash_target_weight_missing"] = True
    if extra:
        payload.update(extra)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=out_dir_path,
            prefix=f".{run_date}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp_path = handle.name
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, out_path)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    # Hard verification: the writer is called from the daily pipeline and
    # until now a silent miss (file not written, file empty, or content
    # missing cash_target_weight) has left us blind. Catch it here.
    if not os.path.exists(out_path):
        raise RuntimeError(
            f"[SIGNAL_SNAPSHOT] write_signals_snapshot finished but file is missing: {out_path}"
        )
    try:
        stat = os.stat(out_path)
    except OSError as exc:
        raise RuntimeError(
            f"[SIGNAL_SNAPSHOT] failed to stat written snapshot {out_path}: {exc}"
        ) from exc
    if stat.st_size <= 0:
        raise RuntimeError(
            f"[SIGNAL_SNAPSHOT] written snapshot is empty: {out_path}"
        )

    return out_path

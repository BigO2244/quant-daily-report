from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import pandas as pd


@dataclass(frozen=True)
class EventStudySummary:
    cohort: str
    horizon_days: int
    sample_size: int
    avg_forward_return: float | None
    median_forward_return: float | None
    hit_rate: float | None
    avg_max_drawdown: float | None


def attach_forward_returns(signals: pd.DataFrame, horizons: Iterable[int] = (1, 3, 5, 10)) -> pd.DataFrame:
    if signals.empty:
        return signals.copy()
    out = signals.copy().sort_values(["ticker", "date"]).reset_index(drop=True)
    grouped = out.groupby("ticker", group_keys=False)
    for horizon in horizons:
        out[f"fwd_{horizon}d"] = grouped["close"].shift(-horizon) / out["close"] - 1.0
        future_paths = [grouped["close"].shift(-step) / out["close"] - 1.0 for step in range(1, horizon + 1)]
        if future_paths:
            future_frame = pd.concat(future_paths, axis=1)
            out[f"max_drawdown_next_{horizon}d"] = future_frame.min(axis=1)
        else:
            out[f"max_drawdown_next_{horizon}d"] = pd.NA
    return out


def build_event_study(signals: pd.DataFrame, horizons: Iterable[int] = (1, 3, 5, 10)) -> tuple[pd.DataFrame, list[dict]]:
    event_rows = signals.copy()
    summaries: list[dict] = []
    cohorts = {
        "unconditional": event_rows["signal_ready"],
        "momentum_only": event_rows["momentum_only"],
        "flow_active": event_rows["flow_active"],
        "flow_active_v1_1": event_rows["flow_active_v1_1"],
    }
    for cohort_name, mask in cohorts.items():
        cohort = event_rows.loc[mask].copy()
        for horizon in horizons:
            ret_col = f"fwd_{horizon}d"
            dd_col = f"max_drawdown_next_{horizon}d"
            valid = cohort[[ret_col, dd_col]].dropna(subset=[ret_col])
            summary = EventStudySummary(
                cohort=cohort_name,
                horizon_days=int(horizon),
                sample_size=int(len(valid)),
                avg_forward_return=_round_or_none(valid[ret_col].mean()),
                median_forward_return=_round_or_none(valid[ret_col].median()),
                hit_rate=_round_or_none((valid[ret_col] > 0).mean()),
                avg_max_drawdown=_round_or_none(valid[dd_col].mean()) if dd_col in valid else None,
            )
            summaries.append(summary.__dict__)
    return event_rows, summaries


def _round_or_none(value: float | None, digits: int = 6) -> float | None:
    if value is None or pd.isna(value):
        return None
    return round(float(value), digits)

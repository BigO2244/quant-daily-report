from __future__ import annotations

from typing import Iterable

import pandas as pd

from .analysis import attach_forward_returns


def build_event_study_v2(signals: pd.DataFrame, horizons: Iterable[int] = (1, 3, 5, 10)) -> tuple[pd.DataFrame, list[dict]]:
    rows = attach_forward_returns(signals.copy(), horizons=horizons).sort_values(["date", "ticker"]).reset_index(drop=True)
    summaries: list[dict] = []
    cohorts = {
        "unconditional": rows["signal_ready_v2"],
        "momentum_only": rows["momentum_only"],
        "slower_participation": rows["participation_entry_signal"],
        "extended_momentum": rows["extended_momentum"],
        "extended_plus_exhaustion": rows["exhaustion_flow"],
    }
    for cohort_name, mask in cohorts.items():
        cohort = rows.loc[mask].copy()
        for horizon in horizons:
            ret_col = f"fwd_{horizon}d"
            dd_col = f"max_drawdown_next_{horizon}d"
            valid = cohort[[ret_col, dd_col, "trend_state", "vol_bucket", "sector"]].dropna(subset=[ret_col])
            summaries.append(_summarize_block(valid, cohort_name=cohort_name, horizon=horizon, split="all"))
            for trend in ("strong_up", "weak_up", "neutral"):
                block = valid[valid["trend_state"] == trend]
                if len(block) >= 50:
                    summaries.append(_summarize_block(block, cohort_name=cohort_name, horizon=horizon, split=f"trend:{trend}"))
            for vol in ("normal", "high_vol"):
                block = valid[valid["vol_bucket"] == vol]
                if len(block) >= 50:
                    summaries.append(_summarize_block(block, cohort_name=cohort_name, horizon=horizon, split=f"vol:{vol}"))
            if "sector" in valid.columns:
                sector_counts = valid["sector"].value_counts()
                for sector, count in sector_counts.items():
                    if int(count) < 200:
                        continue
                    block = valid[valid["sector"] == sector]
                    summaries.append(_summarize_block(block, cohort_name=cohort_name, horizon=horizon, split=f"sector:{sector}"))
    return rows, summaries


def _summarize_block(block: pd.DataFrame, *, cohort_name: str, horizon: int, split: str) -> dict:
    ret_col = f"fwd_{horizon}d"
    dd_col = f"max_drawdown_next_{horizon}d"
    return {
        "cohort": cohort_name,
        "split": split,
        "horizon_days": int(horizon),
        "sample_size": int(len(block)),
        "avg_forward_return": _round(block[ret_col].mean()),
        "median_forward_return": _round(block[ret_col].median()),
        "hit_rate": _round((block[ret_col] > 0).mean()),
        "avg_max_drawdown": _round(block[dd_col].mean()),
    }


def _round(value: float | None, digits: int = 6) -> float | None:
    if value is None or pd.isna(value):
        return None
    return round(float(value), digits)

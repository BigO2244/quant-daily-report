"""
core/ic_throttle.py
-------------------
IC-gated auto-throttle for sleeve strengths.

Created 2026-04-11 as part of the backtest-vs-live reconciliation effort.

Background
==========
The quality sleeve has historically run at a fixed strength of 1.0 regardless
of whether its forecast skill (Information Coefficient) is positive or negative
over the recent past. In the 2026-Q1 live window, quality contributed the worst
attribution while its rolling IC was non-positive — the sleeve was paying full
weight while the edge was negative. This module computes a multiplier in
[floor, 1.0] that shrinks the sleeve strength when rolling IC is non-positive,
and restores full strength as IC recovers.

Design
======
- Read rolling IC from outputs/ic_monitor/ic_rolling_60d.csv (written by
  research/ic_monitor.py). We prefer the 60-day window as a noise-tolerant
  proxy for "is this sleeve still working?".
- Pick the longest available horizon (falling back as needed) so we're
  throttling against a meaningful forecast skill measure rather than 1-day
  noise.
- If the IC data file, sleeve, or all rows are missing/null (e.g. because
  the signals snapshot pipeline has not yet populated enough history),
  we return the neutral multiplier 1.0 and surface a reason. We never
  fail closed in a way that would cash out the sleeve on a data miss —
  the deadband and allocator cash rules handle that layer.
- When IC >= 0: multiplier is 1.0 (no throttle).
- When IC <  0: multiplier linearly interpolates from 1.0 at IC=0 down to
  `floor` at IC=`floor_ic`. Further-negative IC stays pinned at floor.

Outputs
=======
`compute_sleeve_ic_throttle` returns an `IcThrottleResult` with:
- multiplier: final sleeve strength multiplier in [floor, 1.0]
- rolling_ic: the IC value used (or None when unavailable)
- window, horizon, asof_date: diagnostic metadata
- reason: human-readable explanation used in the daily report.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import logging

import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_IC_ROLLING_PATH = Path("outputs/ic_monitor/ic_rolling_60d.csv")
DEFAULT_WINDOW = 60
DEFAULT_HORIZON_CANDIDATES: tuple[int, ...] = (21, 10, 5, 1)
DEFAULT_FLOOR = 0.2
DEFAULT_FLOOR_IC = -0.05  # IC at which we pin at the floor


@dataclass
class IcThrottleResult:
    sleeve: str
    multiplier: float
    rolling_ic: Optional[float]
    window: Optional[int]
    horizon: Optional[int]
    asof_date: Optional[str]
    reason: str

    def to_dict(self) -> dict:
        return {
            "sleeve": self.sleeve,
            "multiplier": float(self.multiplier),
            "rolling_ic": None if self.rolling_ic is None else float(self.rolling_ic),
            "window": self.window,
            "horizon": self.horizon,
            "asof_date": self.asof_date,
            "reason": self.reason,
        }


def _load_rolling_ic(path: Path) -> Optional[pd.DataFrame]:
    if not path.exists():
        logger.info("[IC_THROTTLE] %s not found — returning None", path)
        return None
    try:
        df = pd.read_csv(path)
    except Exception as exc:
        logger.warning("[IC_THROTTLE] failed to read %s: %s", path, exc)
        return None
    if df.empty:
        return None
    required = {"date", "sleeve", "horizon", "window", "rolling_ic"}
    missing = required - set(df.columns)
    if missing:
        logger.warning(
            "[IC_THROTTLE] %s missing columns %s — available %s",
            path,
            sorted(missing),
            list(df.columns),
        )
        return None
    return df


def _latest_ic_for_horizon(
    rolling: pd.DataFrame,
    sleeve: str,
    window: int,
    horizon: int,
) -> tuple[Optional[float], Optional[str]]:
    """Return (latest_non_null_ic, date_str) for this sleeve/window/horizon."""
    mask = (
        (rolling["sleeve"].astype(str) == sleeve)
        & (pd.to_numeric(rolling["window"], errors="coerce") == window)
        & (pd.to_numeric(rolling["horizon"], errors="coerce") == horizon)
    )
    sub = rolling.loc[mask, ["date", "rolling_ic"]].copy()
    if sub.empty:
        return None, None
    sub["rolling_ic"] = pd.to_numeric(sub["rolling_ic"], errors="coerce")
    sub = sub.dropna(subset=["rolling_ic"])
    if sub.empty:
        return None, None
    sub["date"] = pd.to_datetime(sub["date"], errors="coerce")
    sub = sub.dropna(subset=["date"]).sort_values("date")
    if sub.empty:
        return None, None
    last = sub.iloc[-1]
    return float(last["rolling_ic"]), last["date"].strftime("%Y-%m-%d")


def _multiplier_from_ic(
    rolling_ic: float,
    *,
    floor: float,
    floor_ic: float,
) -> float:
    """Linearly interpolate multiplier from 1.0 at IC=0 to `floor` at IC<=floor_ic.

    Positive IC -> 1.0
    IC in (floor_ic, 0) -> between floor and 1.0
    IC <= floor_ic -> floor
    """
    if rolling_ic >= 0.0:
        return 1.0
    if rolling_ic <= floor_ic:
        return float(floor)
    # linear ramp
    frac = (rolling_ic - floor_ic) / (0.0 - floor_ic)  # in (0, 1)
    return float(floor + (1.0 - floor) * frac)


def compute_sleeve_ic_throttle(
    sleeve: str = "sleeve_quality",
    *,
    ic_rolling_path: Path = DEFAULT_IC_ROLLING_PATH,
    window: int = DEFAULT_WINDOW,
    horizon_candidates: Iterable[int] = DEFAULT_HORIZON_CANDIDATES,
    floor: float = DEFAULT_FLOOR,
    floor_ic: float = DEFAULT_FLOOR_IC,
) -> IcThrottleResult:
    """Compute an IC-gated strength multiplier for a sleeve.

    Returns a multiplier in [floor, 1.0]. Neutral (1.0) when:
      - the IC file is missing
      - no rolling IC rows exist yet for this sleeve/window
      - the latest rolling IC is non-negative

    Throttled below 1.0 only when the latest rolling IC is strictly negative.
    """
    rolling = _load_rolling_ic(Path(ic_rolling_path))
    if rolling is None:
        return IcThrottleResult(
            sleeve=sleeve,
            multiplier=1.0,
            rolling_ic=None,
            window=window,
            horizon=None,
            asof_date=None,
            reason=f"ic_rolling file missing at {ic_rolling_path}",
        )

    chosen_ic: Optional[float] = None
    chosen_horizon: Optional[int] = None
    chosen_date: Optional[str] = None
    for horizon in horizon_candidates:
        ic_val, date_str = _latest_ic_for_horizon(rolling, sleeve, window, horizon)
        if ic_val is not None:
            chosen_ic = ic_val
            chosen_horizon = horizon
            chosen_date = date_str
            break

    if chosen_ic is None:
        return IcThrottleResult(
            sleeve=sleeve,
            multiplier=1.0,
            rolling_ic=None,
            window=window,
            horizon=None,
            asof_date=None,
            reason=(
                f"no non-null rolling_ic rows for sleeve={sleeve} "
                f"window={window} horizons={list(horizon_candidates)} — "
                "neutral multiplier 1.0 applied"
            ),
        )

    multiplier = _multiplier_from_ic(chosen_ic, floor=floor, floor_ic=floor_ic)
    if chosen_ic >= 0.0:
        reason = (
            f"rolling_ic={chosen_ic:.4f} >= 0 — sleeve at full strength"
        )
    elif chosen_ic <= floor_ic:
        reason = (
            f"rolling_ic={chosen_ic:.4f} <= floor_ic={floor_ic} — "
            f"pinned at floor multiplier={floor:.2f}"
        )
    else:
        reason = (
            f"rolling_ic={chosen_ic:.4f} in ({floor_ic}, 0) — "
            f"interpolated multiplier={multiplier:.3f}"
        )

    logger.info(
        "[IC_THROTTLE] sleeve=%s window=%d horizon=%s ic=%.4f mult=%.3f asof=%s",
        sleeve,
        window,
        chosen_horizon,
        chosen_ic,
        multiplier,
        chosen_date,
    )

    return IcThrottleResult(
        sleeve=sleeve,
        multiplier=multiplier,
        rolling_ic=chosen_ic,
        window=window,
        horizon=chosen_horizon,
        asof_date=chosen_date,
        reason=reason,
    )
